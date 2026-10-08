"""Persistence of review state, next_due, atomic record_review, UTC storage."""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import db, review
from core.loader import load_questions
from core.scheduler import SM2, Rating, ReviewState
from core.schema import Question, Status, Verification
from tests.conftest import make_question

T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
USER = review.DEFAULT_USER_ID
G, E, H, A = Rating.GOOD, Rating.EASY, Rating.HARD, Rating.AGAIN


def day(n: float) -> datetime:
    return T0 + timedelta(days=n)


# --- schema ---------------------------------------------------------------------------------


def test_review_state_table_has_a_user_id_and_a_composite_key(
    trusted_conn: sqlite3.Connection,
) -> None:
    cols = {r["name"]: r for r in trusted_conn.execute("PRAGMA table_info(review_state)")}
    assert {"user_id", "question_id", "ease", "interval_days", "repetitions", "lapses", "due_at",
            "last_reviewed_at"} <= set(cols)  # fmt: skip
    pk = sorted((r["pk"], n) for n, r in cols.items() if r["pk"])
    assert [n for _, n in pk] == ["user_id", "question_id"]
    idx = {r["name"] for r in trusted_conn.execute("PRAGMA index_list(review_state)")}
    assert "idx_review_due" in idx


def test_single_local_user_by_default() -> None:
    assert review.DEFAULT_USER_ID == "local"


def test_foreign_keys_are_enforced(trusted_conn: sqlite3.Connection) -> None:
    assert trusted_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        trusted_conn.execute(
            "INSERT INTO review_state VALUES "
            "('local', 'prob-404', 2.5, 0, 0, 0, '2026-01-01T00:00:00.000000Z', NULL)"
        )


# --- record_review --------------------------------------------------------------------------


def test_first_review_creates_state(trusted_conn: sqlite3.Connection) -> None:
    s = review.record_review(trusted_conn, USER, "prob-001", G, T0)
    assert s.interval_days == 1 and s.due_at == day(1) and s.repetitions == 1
    assert review.get_state(trusted_conn, USER, "prob-001") == s


def test_state_round_trips_through_sqlite(trusted_conn: sqlite3.Connection) -> None:
    for i, r in enumerate([G, G, A, H]):
        s = review.record_review(trusted_conn, USER, "prob-001", r, day(i))
    got = review.get_state(trusted_conn, USER, "prob-001")
    assert got == s and got.lapses == 1 and got.due_at.tzinfo is UTC


def test_repeat_reviews_update_the_same_row(trusted_conn: sqlite3.Connection) -> None:
    review.record_review(trusted_conn, USER, "prob-001", G, T0)
    review.record_review(trusted_conn, USER, "prob-001", G, day(1))
    n = trusted_conn.execute("SELECT COUNT(*) FROM review_state").fetchone()[0]
    assert n == 1 and review.get_state(trusted_conn, USER, "prob-001").interval_days == 6


def test_users_are_isolated(trusted_conn: sqlite3.Connection) -> None:
    review.record_review(trusted_conn, "alice", "prob-001", G, T0)
    review.record_review(trusted_conn, "alice", "prob-001", G, day(1))
    review.record_review(trusted_conn, "bob", "prob-001", A, T0)
    assert review.get_state(trusted_conn, "alice", "prob-001").interval_days == 6
    assert review.get_state(trusted_conn, "bob", "prob-001").lapses == 1
    assert review.get_state(trusted_conn, "carol", "prob-001") is None
    assert [x.id for x in review.next_due(trusted_conn, "bob", day(1))] == ["prob-001"]
    assert review.next_due(trusted_conn, "carol", day(30)) == []


def test_review_updates_solve_stats(trusted_conn: sqlite3.Connection) -> None:
    for i, r in enumerate([G, E, A, H]):
        review.record_review(trusted_conn, USER, "prob-001", r, day(i))
    stats = db.get_question(trusted_conn, "prob-001").solve_stats
    assert (stats.attempts, stats.correct) == (4, 3)  # only AGAIN counts as incorrect


def test_only_trusted_questions_can_be_reviewed(trusted_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="trusted"):
        review.record_review(trusted_conn, USER, "stat-005", G, T0)  # fresh (text answer)
    trusted_conn.execute("UPDATE questions SET status = 'flagged' WHERE id = 'prob-002'")
    trusted_conn.commit()
    with pytest.raises(ValueError, match="trusted"):
        review.record_review(trusted_conn, USER, "prob-002", G, T0)
    with pytest.raises(KeyError):
        review.record_review(trusted_conn, USER, "prob-404", G, T0)
    assert trusted_conn.execute("SELECT COUNT(*) FROM review_state").fetchone()[0] == 0
    assert db.get_question(trusted_conn, "stat-005").solve_stats.attempts == 0


def test_bad_arguments_are_rejected(trusted_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="timezone"):
        review.record_review(trusted_conn, USER, "prob-001", G, datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        review.record_review(trusted_conn, "", "prob-001", G, T0)
    assert trusted_conn.execute("SELECT COUNT(*) FROM review_state").fetchone()[0] == 0


def test_non_utc_clock_is_stored_as_utc(trusted_conn: sqlite3.Connection) -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    review.record_review(
        trusted_conn, USER, "prob-001", G, datetime(2026, 1, 1, 14, 30, tzinfo=ist)
    )
    raw = trusted_conn.execute("SELECT due_at, last_reviewed_at FROM review_state").fetchone()
    assert tuple(raw) == ("2026-01-02T09:00:00.000000Z", "2026-01-01T09:00:00.000000Z")


def test_a_custom_scheduler_can_be_injected(trusted_conn: sqlite3.Connection) -> None:
    class Weekly:
        def initial(self, user_id: str, question_id: str, now: datetime) -> ReviewState:
            return ReviewState(user_id=user_id, question_id=question_id, due_at=now)

        def review(self, state: ReviewState, rating: Rating, now: datetime) -> ReviewState:
            return state.model_copy(
                update={
                    "interval_days": 7,
                    "due_at": now + timedelta(days=7),
                    "last_reviewed_at": now,
                }
            )

    s = review.record_review(trusted_conn, USER, "prob-001", G, T0, scheduler=Weekly())
    assert s.due_at == day(7)


# --- atomicity ------------------------------------------------------------------------------


def snapshot(conn: sqlite3.Connection) -> tuple:
    states = [
        tuple(r) for r in conn.execute("SELECT * FROM review_state ORDER BY user_id, question_id")
    ]
    stats = [tuple(r) for r in conn.execute("SELECT id, solve_stats FROM questions ORDER BY id")]
    return states, stats


@pytest.mark.parametrize("failing_step", ["_upsert_state", "_bump_solve_stats"])
def test_failure_midway_saves_nothing_for_a_first_review(
    trusted_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, failing_step: str
) -> None:
    before = snapshot(trusted_conn)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(review, failing_step, boom)
    with pytest.raises(RuntimeError, match="disk on fire"):
        review.record_review(trusted_conn, USER, "prob-001", G, T0)
    assert snapshot(trusted_conn) == before
    assert not trusted_conn.in_transaction


@pytest.mark.parametrize("failing_step", ["_upsert_state", "_bump_solve_stats"])
def test_failure_midway_leaves_an_existing_review_untouched(
    trusted_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, failing_step: str
) -> None:
    review.record_review(trusted_conn, USER, "prob-001", G, T0)
    before = snapshot(trusted_conn)
    real = getattr(review, failing_step)
    calls = {"n": 0}

    def fail_after_the_other_step_ran(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(review, failing_step, fail_after_the_other_step_ran)
    with pytest.raises(RuntimeError):
        review.record_review(trusted_conn, USER, "prob-001", A, day(1))
    assert calls["n"] == 1 and real is not fail_after_the_other_step_ran
    assert snapshot(trusted_conn) == before
    monkeypatch.undo()
    # the connection is still usable and the review now succeeds from the untouched state
    s = review.record_review(trusted_conn, USER, "prob-001", G, day(1))
    assert s.interval_days == 6 and s.lapses == 0


def test_failing_scheduler_saves_nothing(trusted_conn: sqlite3.Connection) -> None:
    class Broken:
        def initial(self, user_id: str, question_id: str, now: datetime) -> ReviewState:
            return SM2().initial(user_id, question_id, now)

        def review(self, state: ReviewState, rating: Rating, now: datetime) -> ReviewState:
            raise ValueError("scheduler bug")

    before = snapshot(trusted_conn)
    with pytest.raises(ValueError, match="scheduler bug"):
        review.record_review(trusted_conn, USER, "prob-001", G, T0, scheduler=Broken())
    assert snapshot(trusted_conn) == before


def test_record_review_refuses_to_join_a_callers_open_transaction(
    trusted_conn: sqlite3.Connection,
) -> None:
    trusted_conn.execute("BEGIN")
    trusted_conn.execute("UPDATE questions SET topic = 'x' WHERE id = 'prob-001'")
    with pytest.raises(RuntimeError, match="transaction"):
        review.record_review(trusted_conn, USER, "prob-001", G, T0)
    trusted_conn.rollback()


def test_two_connections_do_not_lose_updates(tmp_path: Path, curated: list[Question]) -> None:
    path = tmp_path / "qq.db"
    a = db.connect(path)
    db.upsert_questions(a, curated)
    db.set_verification(a, "prob-001", Verification(method="t", result="pass"), Status.TRUSTED)
    b = db.connect(path)
    review.record_review(a, USER, "prob-001", G, T0)
    review.record_review(b, USER, "prob-001", G, day(1))
    review.record_review(a, USER, "prob-001", G, day(7))
    assert db.get_question(b, "prob-001").solve_stats.attempts == 3
    assert review.get_state(b, USER, "prob-001").interval_days == 15
    a.close()
    b.close()


# --- next_due -------------------------------------------------------------------------------


def test_next_due_returns_only_due_cards(trusted_conn: sqlite3.Connection) -> None:
    review.record_review(trusted_conn, USER, "prob-001", G, T0)  # due day 1
    review.record_review(trusted_conn, USER, "prob-002", G, T0)
    review.record_review(trusted_conn, USER, "prob-002", G, day(1))  # due day 7
    assert review.next_due(trusted_conn, USER, day(0.5)) == []
    assert [x.id for x in review.next_due(trusted_conn, USER, day(2))] == ["prob-001"]
    assert [x.id for x in review.next_due(trusted_conn, USER, day(8))] == ["prob-001", "prob-002"]


def test_a_card_due_exactly_now_is_included(trusted_conn: sqlite3.Connection) -> None:
    review.record_review(trusted_conn, USER, "prob-001", G, T0)
    assert [x.id for x in review.next_due(trusted_conn, USER, day(1))] == ["prob-001"]
    assert review.next_due(trusted_conn, USER, day(1) - timedelta(microseconds=1)) == []


def test_next_due_orders_by_due_date_then_id(trusted_conn: sqlite3.Connection) -> None:
    review.record_review(trusted_conn, USER, "prob-004", H, T0)  # due day 1
    review.record_review(trusted_conn, USER, "prob-002", G, T0 - timedelta(days=5))  # due day -4
    review.record_review(trusted_conn, USER, "prob-001", G, T0)  # due day 1
    review.record_review(trusted_conn, USER, "prob-003", G, T0 - timedelta(days=2))  # due day -1
    got = [x.id for x in review.next_due(trusted_conn, USER, day(10))]
    assert got == ["prob-002", "prob-003", "prob-001", "prob-004"]  # ties broken by id


def test_next_due_honours_limit(trusted_conn: sqlite3.Connection) -> None:
    for qid in ("prob-001", "prob-002", "prob-003"):
        review.record_review(trusted_conn, USER, qid, G, T0)
    assert len(review.next_due(trusted_conn, USER, day(5), limit=2)) == 2
    assert review.next_due(trusted_conn, USER, day(5), limit=0) == []


def test_next_due_drops_questions_that_stop_being_trusted(trusted_conn: sqlite3.Connection) -> None:
    for qid in ("prob-001", "prob-002", "prob-003"):
        review.record_review(trusted_conn, USER, qid, G, T0)
    trusted_conn.execute("UPDATE questions SET status = 'flagged' WHERE id = 'prob-001'")
    trusted_conn.execute("UPDATE questions SET status = 'retired' WHERE id = 'prob-002'")
    trusted_conn.execute("UPDATE questions SET status = 'fresh' WHERE id = 'prob-003'")
    assert review.next_due(trusted_conn, USER, day(5)) == []


def test_next_due_never_returns_fresh_or_generated_questions(
    trusted_conn: sqlite3.Connection,
) -> None:
    gen = Question.model_validate(make_question(id="gen-001", source="generated"))
    db.upsert_questions(trusted_conn, [gen])
    trusted_conn.execute(
        "INSERT INTO review_state VALUES "
        "('local', 'gen-001', 2.5, 1, 1, 0, '2025-01-01T00:00:00.000000Z', NULL)"
    )
    assert review.next_due(trusted_conn, USER, day(5)) == []


def test_next_due_requires_an_explicit_aware_clock(trusted_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="timezone"):
        review.next_due(trusted_conn, USER, datetime(2026, 1, 5))
    with pytest.raises(TypeError):
        review.next_due(trusted_conn, USER)  # type: ignore[call-arg]


def test_next_due_compares_in_utc_whatever_the_callers_offset(
    trusted_conn: sqlite3.Connection,
) -> None:
    review.record_review(trusted_conn, USER, "prob-001", G, T0)  # due 2026-01-02 09:00Z
    plus8 = timezone(timedelta(hours=8))
    assert review.next_due(trusted_conn, USER, datetime(2026, 1, 2, 16, 59, tzinfo=plus8)) == []
    assert len(review.next_due(trusted_conn, USER, datetime(2026, 1, 2, 17, 0, tzinfo=plus8))) == 1


def test_unseen_trusted_lists_questions_without_state(trusted_conn: sqlite3.Connection) -> None:
    assert len(review.unseen_trusted(trusted_conn, USER)) == 19
    review.record_review(trusted_conn, USER, "prob-001", G, T0)
    ids = [x.id for x in review.unseen_trusted(trusted_conn, USER)]
    assert "prob-001" not in ids and len(ids) == 18 and "stat-005" not in ids
    assert len(review.unseen_trusted(trusted_conn, "bob")) == 19


# --- reload safety --------------------------------------------------------------------------


def test_reloading_yaml_keeps_review_state_and_stats(trusted_conn: sqlite3.Connection) -> None:
    review.record_review(trusted_conn, USER, "prob-001", G, T0)
    review.record_review(trusted_conn, USER, "prob-001", G, day(1))
    before = snapshot(trusted_conn)
    db.upsert_questions(trusted_conn, load_questions())
    assert snapshot(trusted_conn) == before
    assert db.get_question(trusted_conn, "prob-001").status is Status.TRUSTED


def test_review_runs_as_one_immediate_transaction(trusted_conn: sqlite3.Connection) -> None:
    """Both writes sit between BEGIN IMMEDIATE and a single COMMIT, so no other writer can
    slip in between the read of the old state and the write of the new one."""
    seen: list[str] = []
    trusted_conn.set_trace_callback(seen.append)
    review.record_review(trusted_conn, USER, "prob-001", G, T0)
    trusted_conn.set_trace_callback(None)
    verbs = [s.split()[0].upper() for s in seen if s.strip()]
    assert seen[0].strip().upper() == "BEGIN IMMEDIATE"
    assert verbs.count("BEGIN") == 1 and verbs.count("COMMIT") == 1 and verbs[-1] == "COMMIT"
    writes = [i for i, v in enumerate(verbs) if v in {"INSERT", "UPDATE"}]
    assert len(writes) == 2 and verbs.index("COMMIT") > max(writes)


def test_a_second_writer_is_turned_away_while_a_review_holds_the_lock(
    tmp_path: Path, curated: list[Question]
) -> None:
    path = tmp_path / "qq.db"
    a = db.connect(path)
    db.upsert_questions(a, curated)
    db.set_verification(a, "prob-001", Verification(method="t", result="pass"), Status.TRUSTED)
    b = db.connect(path)
    b.execute("PRAGMA busy_timeout = 0")
    a.execute("BEGIN IMMEDIATE")  # another writer is mid-transaction
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        review.record_review(b, USER, "prob-001", G, T0)
    assert not b.in_transaction  # nothing half-open on the loser
    a.rollback()
    assert review.record_review(b, USER, "prob-001", G, T0).interval_days == 1
    a.close()
    b.close()
