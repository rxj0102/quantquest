"""Timed interview mode: fixed set, server-side deadline, results. Clocks are injected."""

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core import db, interview, progress, review
from core.interview import (
    AlreadyAnswered,
    DeadlinePassed,
    InterviewUnavailable,
    NotInRun,
    finish_run,
    get_active_run,
    remaining_seconds,
    select_questions,
    start_run,
    submit_answer,
    summarize,
)
from core.schema import AnswerType, Question, Status, Verification
from core.skill_tree import load_tree
from tests.conftest import add_question

T0 = datetime(2026, 1, 15, 14, 0, 0, tzinfo=UTC)
USER = "local"
TOL = 1e-3


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def tree():
    return load_tree()


@pytest.fixture
def conn(trusted_conn):
    return trusted_conn


def start(conn, tree, now=T0, **kw):
    kw.setdefault("n_questions", 5)
    kw.setdefault("minutes", 20)
    kw.setdefault("seed", 1)
    return start_run(conn, tree, USER, now, **kw)


def right_answer(conn, qid: str) -> str:
    return db.get_question(conn, qid).answer


# --- selecting the fixed set ----------------------------------------------------------------------------


def test_selection_is_deterministic_for_a_seed_and_varies_across_seeds(conn, tree) -> None:
    a = [q.id for q in select_questions(conn, tree, USER, 4, seed=7)]
    assert a == [q.id for q in select_questions(conn, tree, USER, 4, seed=7)]
    assert (
        len({tuple(q.id for q in select_questions(conn, tree, USER, 4, seed=s)) for s in range(30)})
        > 1
    )


def test_selection_only_uses_trusted_curated_auto_gradable_questions_from_open_nodes(
    conn, tree
) -> None:
    add_question(conn, "cod-001", answer_type="code", answer="def f(): pass")
    add_question(conn, "gen-001", source="generated")
    # a text question can never be trusted (core.schema), so the strongest case is a fresh one
    add_question(
        conn, "txt-001", answer_type="text", answer="words", fmt="derivation", status="fresh"
    )
    for status in ("fresh", "flagged", "retired"):
        add_question(conn, f"zz{status[0]}-001", status=status)
    chosen = select_questions(conn, tree, USER, 50, seed=1)
    assert {q.id for q in chosen} == {
        "prob-001", "prob-004", "prob-005", "linalg-001", "linalg-003", "linalg-005",
    }  # fmt: skip
    assert all(q.answer_type in (AnswerType.NUMERIC, AnswerType.SYMBOLIC) for q in chosen)
    assert all(q.status is Status.TRUSTED and q.source == "curated" for q in chosen)


def test_selection_has_no_duplicates_and_respects_n(conn, tree) -> None:
    for n in (1, 3, 6):
        got = [q.id for q in select_questions(conn, tree, USER, n, seed=3)]
        assert len(got) == n == len(set(got))
    assert len(select_questions(conn, tree, USER, 99, seed=3)) == 6  # only 6 are eligible


def test_selection_mixes_nodes_when_it_can(conn, tree) -> None:
    nodes = {q.node_id for q in select_questions(conn, tree, USER, 2, seed=5)}
    assert nodes == {"prob.counting", "la.determinants"}


def test_locked_nodes_are_not_used(conn, tree) -> None:
    assert not {q.node_id for q in select_questions(conn, tree, USER, 99, seed=1)} & {
        "stat.moments"
    }


def test_nothing_eligible_is_an_error_not_an_empty_run(tree) -> None:
    empty = db.connect(":memory:")
    assert select_questions(empty, tree, USER, 5, seed=1) == []
    with pytest.raises(InterviewUnavailable):
        start_run(empty, tree, USER, T0, n_questions=5, minutes=20, seed=1)


# --- the run and its countdown --------------------------------------------------------------------------


def test_a_run_stores_a_fixed_set_and_a_deadline(conn, tree) -> None:
    run = start(conn, tree)
    assert run.status == "active" and run.started_at == T0
    assert run.deadline_at == T0 + timedelta(minutes=20) and run.duration_seconds == 1200
    assert len(run.question_ids) == 5 == len(set(run.question_ids))
    assert remaining_seconds(run, T0) == 1200
    assert remaining_seconds(run, at(300)) == 900
    assert remaining_seconds(run, at(1200)) == 0 and remaining_seconds(run, at(5000)) == 0


def test_a_refresh_cannot_reset_the_countdown(conn, tree) -> None:
    run = start(conn, tree)
    again = start(conn, tree, now=at(600), minutes=20, seed=999)  # "refresh" 10 minutes later
    assert again.id == run.id and again.deadline_at == run.deadline_at
    assert again.question_ids == run.question_ids  # nor does it reshuffle the questions
    assert remaining_seconds(again, at(600)) == 600
    assert conn.execute("SELECT COUNT(*) FROM interview_run").fetchone()[0] == 1


def test_asking_for_a_longer_run_cannot_extend_an_active_one(conn, tree) -> None:
    run = start(conn, tree, minutes=20)
    longer = start(conn, tree, now=at(1000), minutes=120)
    assert longer.deadline_at == run.deadline_at


def test_a_second_tab_sees_the_same_run_and_deadline(tree, tmp_path: Path, curated) -> None:
    path = tmp_path / "qq.db"
    first = db.connect(path)
    db.upsert_questions(first, curated)
    for q in curated:
        if q.answer_type is not AnswerType.TEXT:
            db.set_verification(
                first, q.id, Verification(method="t", result="pass"), Status.TRUSTED
            )
    run = start(first, tree)
    second = db.connect(path)  # a second tab is a second connection (and session)
    seen = get_active_run(second, USER, at(120))
    assert seen is not None and seen.id == run.id and seen.deadline_at == run.deadline_at
    assert start(second, tree, now=at(120)).id == run.id
    assert remaining_seconds(seen, at(120)) == 1080
    first.close()
    second.close()


def test_simultaneous_starts_create_exactly_one_run(tree, tmp_path: Path, curated) -> None:
    path = tmp_path / "qq.db"
    seed = db.connect(path)
    db.upsert_questions(seed, curated)
    for q in curated:
        if q.answer_type is not AnswerType.TEXT:
            db.set_verification(seed, q.id, Verification(method="t", result="pass"), Status.TRUSTED)
    seed.close()
    barrier = threading.Barrier(6)
    results: list = []
    errors: list = []

    def tab(i: int) -> None:
        c = db.connect(path)
        try:
            barrier.wait()
            results.append(start(c, tree, now=at(i), seed=i))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=tab, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    assert len({r.id for r in results}) == 1 and len({r.deadline_at for r in results}) == 1
    check = db.connect(path)
    assert check.execute("SELECT COUNT(*) FROM interview_run").fetchone()[0] == 1
    check.close()


def test_the_database_itself_allows_one_active_run_per_user(conn, tree) -> None:
    start(conn, tree)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO interview_run (id, user_id, started_at, deadline_at, duration_seconds, "
            "question_ids, status) VALUES ('x', 'local', '2026-01-15T14:00:00.000000Z', "
            "'2026-01-15T14:20:00.000000Z', 1200, '[]', 'active')"
        )


def test_different_users_can_each_have_a_run(conn, tree) -> None:
    a = start_run(conn, tree, "alice", T0, n_questions=3, minutes=10, seed=1)
    b = start_run(conn, tree, "bob", at(30), n_questions=3, minutes=10, seed=1)
    assert a.id != b.id and a.deadline_at != b.deadline_at


def test_after_the_deadline_the_run_is_expired_and_a_new_one_may_start(conn, tree) -> None:
    run = start(conn, tree)
    assert get_active_run(conn, USER, at(1199)).id == run.id
    assert get_active_run(conn, USER, at(1200)) is None  # exactly at the deadline: over
    assert interview.get_run(conn, run.id).status == "expired"
    fresh = start(conn, tree, now=at(1500))
    assert fresh.id != run.id and fresh.deadline_at == at(1500) + timedelta(minutes=20)


def test_the_countdown_is_computed_from_the_stored_deadline_not_from_the_caller(conn, tree) -> None:
    run = start(conn, tree)
    stored = interview.get_run(conn, run.id)
    assert remaining_seconds(stored, at(10)) == remaining_seconds(run, at(10)) == 1190


# --- answering ---------------------------------------------------------------------------------------------


def test_a_correct_answer_is_stored_and_earns_interview_xp(conn, tree) -> None:
    run = start(conn, tree)
    qid = run.question_ids[0]
    sub = submit_answer(conn, run.id, qid, right_answer(conn, qid), at(30), ui_rel_tol=TOL)
    assert sub.result.outcome == "correct" and sub.stored is True
    difficulty = db.get_question(conn, qid).difficulty
    assert sub.xp == difficulty * progress.INTERVIEW_XP_PER_DIFFICULTY
    assert progress.total_xp(conn, USER) == sub.xp
    kinds = {r[0] for r in conn.execute("SELECT kind FROM xp_event")}
    assert kinds == {"interview"}


def test_a_wrong_answer_is_stored_with_no_xp(conn, tree) -> None:
    run = start(conn, tree)
    sub = submit_answer(conn, run.id, run.question_ids[0], "123456", at(30), ui_rel_tol=TOL)
    assert sub.result.outcome == "incorrect" and sub.stored and sub.xp == 0
    assert progress.total_xp(conn, USER) == 0


def test_an_unreadable_answer_is_not_stored_and_can_be_retried(conn, tree) -> None:
    run = start(conn, tree)
    qid = run.question_ids[0]
    sub = submit_answer(conn, run.id, qid, "__import__('os')", at(30), ui_rel_tol=TOL)
    assert sub.result.outcome == "unreadable" and sub.stored is False
    assert submit_answer(conn, run.id, qid, right_answer(conn, qid), at(40), ui_rel_tol=TOL).stored


def test_an_answer_cannot_be_changed_or_submitted_twice(conn, tree) -> None:
    run = start(conn, tree)
    qid = run.question_ids[0]
    submit_answer(conn, run.id, qid, "123456", at(30), ui_rel_tol=TOL)
    with pytest.raises(AlreadyAnswered):
        submit_answer(conn, run.id, qid, right_answer(conn, qid), at(40), ui_rel_tol=TOL)
    assert summarize(conn, run.id).correct == 0


def test_questions_outside_the_fixed_set_are_refused(conn, tree) -> None:
    run = start(conn, tree, n_questions=1)
    other = next(
        q.id for q in select_questions(conn, tree, USER, 99, seed=1) if q.id not in run.question_ids
    )
    with pytest.raises(NotInRun):
        submit_answer(conn, run.id, other, right_answer(conn, other), at(30), ui_rel_tol=TOL)
    with pytest.raises(NotInRun):
        submit_answer(conn, run.id, "nope-001", "1", at(30), ui_rel_tol=TOL)


def test_answers_after_the_deadline_are_rejected_and_not_stored(conn, tree) -> None:
    run = start(conn, tree)
    qid = run.question_ids[0]
    with pytest.raises(DeadlinePassed):
        submit_answer(conn, run.id, qid, right_answer(conn, qid), at(1200), ui_rel_tol=TOL)
    with pytest.raises(DeadlinePassed):
        submit_answer(conn, run.id, qid, right_answer(conn, qid), at(1201), ui_rel_tol=TOL)
    assert conn.execute("SELECT COUNT(*) FROM interview_answer").fetchone()[0] == 0
    assert progress.total_xp(conn, USER) == 0
    assert interview.get_run(conn, run.id).status == "expired"


def test_the_last_second_before_the_deadline_still_counts(conn, tree) -> None:
    run = start(conn, tree)
    qid = run.question_ids[0]
    sub = submit_answer(conn, run.id, qid, right_answer(conn, qid), at(1199.9), ui_rel_tol=TOL)
    assert sub.stored and sub.result.correct


def test_a_finished_run_takes_no_more_answers(conn, tree) -> None:
    run = start(conn, tree)
    finish_run(conn, run.id, at(60))
    with pytest.raises(DeadlinePassed):
        submit_answer(conn, run.id, run.question_ids[0], "1", at(70), ui_rel_tol=TOL)


def test_integer_answers_need_exact_matches_in_interviews_too(conn, tree) -> None:
    run = start(conn, tree, n_questions=6)
    qid = "linalg-003"  # answer 2
    assert qid in run.question_ids
    assert (
        submit_answer(conn, run.id, qid, "2.001", at(30), ui_rel_tol=TOL).result.outcome
        == "incorrect"
    )


def test_a_failure_between_the_answer_and_its_xp_saves_neither(conn, tree, monkeypatch) -> None:
    run = start(conn, tree)
    qid = run.question_ids[0]

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(progress, "insert_xp_event", boom)
    with pytest.raises(RuntimeError, match="disk on fire"):
        submit_answer(conn, run.id, qid, right_answer(conn, qid), at(30), ui_rel_tol=TOL)
    assert conn.execute("SELECT COUNT(*) FROM interview_answer").fetchone()[0] == 0
    assert not conn.in_transaction
    monkeypatch.undo()
    assert submit_answer(conn, run.id, qid, right_answer(conn, qid), at(31), ui_rel_tol=TOL).stored


# --- results -----------------------------------------------------------------------------------------------


def test_summary_counts_scores_and_times(conn, tree) -> None:
    run = start(conn, tree)
    q = run.question_ids
    submit_answer(conn, run.id, q[0], right_answer(conn, q[0]), at(30), ui_rel_tol=TOL)
    submit_answer(conn, run.id, q[1], right_answer(conn, q[1]), at(95), ui_rel_tol=TOL)
    submit_answer(conn, run.id, q[2], "123456", at(140), ui_rel_tol=TOL)  # wrong
    s = finish_run(conn, run.id, at(200))  # q[3] and q[4] left unanswered
    assert (s.total, s.correct, s.incorrect, s.unanswered) == (5, 2, 1, 2)
    assert s.score == pytest.approx(2 / 5) and s.status == "finished"
    assert s.time_used_seconds == 200
    assert s.xp_earned == progress.total_xp(conn, USER)
    outcomes = [i.outcome for i in s.items]
    assert outcomes == ["correct", "correct", "incorrect", "unanswered", "unanswered"]
    assert [i.question.id for i in s.items if i.question] == list(q)
    assert sum(c for c, _ in s.by_node.values()) == 2
    assert sum(t for _, t in s.by_node.values()) == 5


def test_summary_shows_solutions_and_the_typed_answers(conn, tree) -> None:
    run = start(conn, tree, n_questions=2)
    qid = run.question_ids[0]
    submit_answer(conn, run.id, qid, "123456", at(30), ui_rel_tol=TOL)
    s = finish_run(conn, run.id, at(60))
    first = s.items[0]
    assert first.typed == "123456" and first.question.solution_md and first.question.answer
    assert s.items[1].typed is None


def test_finishing_is_idempotent_and_keeps_the_first_finish_time(conn, tree) -> None:
    run = start(conn, tree)
    a = finish_run(conn, run.id, at(100))
    b = finish_run(conn, run.id, at(900))
    assert a == b and b.time_used_seconds == 100
    assert interview.get_run(conn, run.id).status == "finished"


def test_an_expired_run_has_a_summary_capped_at_the_time_limit(conn, tree) -> None:
    run = start(conn, tree)
    submit_answer(
        conn,
        run.id,
        run.question_ids[0],
        right_answer(conn, run.question_ids[0]),
        at(60),
        ui_rel_tol=TOL,
    )
    assert get_active_run(conn, USER, at(5000)) is None  # discovered late, e.g. after a refresh
    s = summarize(conn, run.id)
    assert s.status == "expired" and s.time_used_seconds == 1200
    assert (s.correct, s.unanswered) == (1, 4)
    assert (
        finish_run(conn, run.id, at(6000)).status == "expired"
    )  # cannot be turned into 'finished'


def test_withdrawn_questions_are_not_shown_in_a_summary(conn, tree) -> None:
    run = start(conn, tree)
    gone = run.question_ids[2]
    conn.execute("UPDATE questions SET status = 'flagged' WHERE id = ?", (gone,))
    conn.commit()
    s = finish_run(conn, run.id, at(60))
    item = s.items[2]
    assert item.outcome == "withdrawn" and item.question is None and item.typed is None
    assert s.total == 4 and gone not in {i.question.id for i in s.items if i.question}


def test_interviews_do_not_touch_the_review_schedule_or_mastery(conn, tree) -> None:
    before = (
        [tuple(r) for r in conn.execute("SELECT * FROM review_state")],
        [tuple(r) for r in conn.execute("SELECT id, solve_stats FROM questions ORDER BY id")],
    )
    run = start(conn, tree)
    for qid in run.question_ids:
        submit_answer(conn, run.id, qid, right_answer(conn, qid), at(30), ui_rel_tol=TOL)
    finish_run(conn, run.id, at(100))
    after = (
        [tuple(r) for r in conn.execute("SELECT * FROM review_state")],
        [tuple(r) for r in conn.execute("SELECT id, solve_stats FROM questions ORDER BY id")],
    )
    assert after == before
    assert review.next_due(conn, USER, at(10**7)) == []


def test_interview_activity_counts_toward_the_streak(conn, tree) -> None:
    from zoneinfo import ZoneInfo

    run = start(conn, tree)
    qid = run.question_ids[0]
    submit_answer(conn, run.id, qid, right_answer(conn, qid), at(30), ui_rel_tol=TOL)
    s = progress.streak(conn, USER, at(60), ZoneInfo("America/New_York"))
    assert s.current == 1 and s.active_today


def test_summary_of_an_unknown_run_is_an_error(conn) -> None:
    with pytest.raises(KeyError):
        summarize(conn, "nope")
    assert interview.get_run(conn, "nope") is None


def test_run_dataclass_is_immutable(conn, tree) -> None:
    run = start(conn, tree)
    with pytest.raises(Exception):  # noqa: B017
        run.deadline_at = T0  # type: ignore[misc]
    assert isinstance(run.question_ids, tuple) and isinstance(conn, sqlite3.Connection)
    assert all(isinstance(db.get_question(conn, q), Question) for q in run.question_ids)
