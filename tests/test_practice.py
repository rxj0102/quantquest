"""Practice queue, rating rules, and the atomic rating write."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from core import db, practice, progress, review
from core.practice import Card, allowed_ratings, build_queue, rate
from core.scheduler import Rating
from core.skill_tree import load_tree
from tests.conftest import add_question
from verify.answers import AnswerResult

T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
USER = "local"
G, E, H, A = Rating.GOOD, Rating.EASY, Rating.HARD, Rating.AGAIN


def day(n: float) -> datetime:
    return T0 + timedelta(days=n)


@pytest.fixture
def conn(trusted_conn):
    return trusted_conn


@pytest.fixture(scope="module")
def tree():
    return load_tree()


def ids(cards: list[Card]) -> list[str]:
    return [c.question.id for c in cards]


# --- the queue ----------------------------------------------------------------------------------------


def test_new_cards_come_only_from_unlocked_nodes_in_tree_order(conn, tree) -> None:
    cards = build_queue(conn, tree, USER, day(0), max_cards=50, max_new=50)
    assert all(c.kind == "new" for c in cards)
    nodes = {c.question.node_id for c in cards}
    assert nodes == {"prob.counting", "la.determinants"}  # only the two root nodes are unlocked
    order = [tree.order.index(c.question.node_id) for c in cards]
    assert order == sorted(order)
    within = [(c.question.node_id, c.question.difficulty, c.question.id) for c in cards]
    assert within == sorted(within, key=lambda t: (tree.order.index(t[0]), t[1], t[2]))
    assert len(cards) == 6  # 3 + 3 trusted questions in the two root nodes


def test_due_cards_come_first_most_overdue_first(conn, tree) -> None:
    review.record_review(conn, USER, "prob-004", G, day(-3))  # due day -2
    review.record_review(conn, USER, "prob-001", G, day(-5))  # due day -4
    review.record_review(conn, USER, "linalg-001", G, day(-1))  # due day 0
    cards = build_queue(conn, tree, USER, day(1), max_cards=50, max_new=50)
    assert ids(cards)[:3] == ["prob-001", "prob-004", "linalg-001"]
    assert [c.kind for c in cards[:3]] == ["due"] * 3
    assert all(c.kind == "new" for c in cards[3:])


def test_a_card_is_never_listed_twice(conn, tree) -> None:
    review.record_review(conn, USER, "prob-001", G, day(-5))
    got = ids(build_queue(conn, tree, USER, day(1), max_cards=50, max_new=50))
    assert len(got) == len(set(got)) and got.count("prob-001") == 1


def test_reviewed_cards_that_are_not_yet_due_are_not_offered_again(conn, tree) -> None:
    review.record_review(conn, USER, "prob-001", G, day(0))  # due day 1
    got = ids(build_queue(conn, tree, USER, day(0.5), max_cards=50, max_new=50))
    assert "prob-001" not in got


def test_caps_apply_to_new_cards_and_to_the_whole_session(conn, tree) -> None:
    for qid in ("prob-001", "prob-004", "prob-005"):
        review.record_review(conn, USER, qid, G, day(-5))
    assert len(build_queue(conn, tree, USER, day(1), max_cards=50, max_new=2)) == 3 + 2
    assert len(build_queue(conn, tree, USER, day(1), max_cards=4, max_new=50)) == 4
    assert ids(build_queue(conn, tree, USER, day(1), max_cards=2, max_new=50)) == [
        "prob-001",
        "prob-004",
    ]
    assert build_queue(conn, tree, USER, day(1), max_cards=0, max_new=5) == []
    assert all(
        c.kind == "due" for c in build_queue(conn, tree, USER, day(1), max_cards=50, max_new=0)
    )


def test_mastering_a_node_unlocks_its_children_for_new_cards(conn, tree) -> None:
    before = {
        c.question.node_id for c in build_queue(conn, tree, USER, day(0), max_cards=50, max_new=50)
    }
    assert "prob.conditional" not in before
    for qid in ("prob-001", "prob-004", "prob-005"):  # all of prob.counting, answered correctly
        review.record_review(conn, USER, qid, G, day(0))
    after = {
        c.question.node_id for c in build_queue(conn, tree, USER, day(0), max_cards=50, max_new=50)
    }
    assert {"prob.conditional", "prob.expectation"} <= after


def test_users_have_separate_queues(conn, tree) -> None:
    review.record_review(conn, "alice", "prob-001", G, day(-5))
    assert ids(build_queue(conn, tree, "alice", day(1), max_cards=50, max_new=0)) == ["prob-001"]
    assert build_queue(conn, tree, "bob", day(1), max_cards=50, max_new=0) == []


def test_the_queue_depends_only_on_the_injected_clock(conn, tree) -> None:
    review.record_review(conn, USER, "prob-001", G, day(0))
    assert "prob-001" not in ids(build_queue(conn, tree, USER, day(0.9), max_cards=50, max_new=0))
    assert "prob-001" in ids(build_queue(conn, tree, USER, day(1.0), max_cards=50, max_new=0))


def test_code_and_generated_questions_are_not_in_the_practice_pool(conn, tree) -> None:
    add_question(conn, "cod-001", answer_type="code", answer="def f(): pass")
    add_question(conn, "gen-001", source="generated")
    got = ids(build_queue(conn, tree, USER, day(0), max_cards=50, max_new=50))
    assert "cod-001" not in got and "gen-001" not in got
    review.record_review(conn, USER, "gen-001", G, day(-5))
    assert "gen-001" not in ids(build_queue(conn, tree, USER, day(1), max_cards=50, max_new=50))


@pytest.mark.parametrize("status", ["fresh", "flagged", "retired"])
def test_non_trusted_questions_are_never_queued(conn, tree, status) -> None:
    add_question(conn, "zzz-001", status=status)
    got = ids(build_queue(conn, tree, USER, day(0), max_cards=100, max_new=100))
    assert "zzz-001" not in got


def test_a_question_flagged_after_review_leaves_the_due_list(conn, tree) -> None:
    review.record_review(conn, USER, "prob-001", G, day(-5))
    conn.execute("UPDATE questions SET status = 'flagged' WHERE id = 'prob-001'")
    conn.commit()
    assert "prob-001" not in ids(build_queue(conn, tree, USER, day(1), max_cards=50, max_new=50))


# --- which ratings may be chosen ---------------------------------------------------------------------


def result(outcome: str, verified: bool = True) -> AnswerResult:
    return AnswerResult(outcome=outcome, verified=verified, message="")


def test_allowed_ratings() -> None:
    assert allowed_ratings(result("correct")) == [A, H, G, E]
    assert allowed_ratings(result("incorrect")) == [A]  # a wrong answer cannot be rated Good
    assert allowed_ratings(result("self_graded", verified=False)) == [A, H, G, E]
    assert allowed_ratings(result("unreadable", verified=False)) == []  # retry instead


# --- rate(): review + XP in one transaction ----------------------------------------------------------


def snapshot(conn: sqlite3.Connection) -> tuple:
    return (
        [tuple(r) for r in conn.execute("SELECT * FROM review_state ORDER BY 1, 2")],
        [tuple(r) for r in conn.execute("SELECT id, solve_stats FROM questions ORDER BY id")],
        [tuple(r) for r in conn.execute("SELECT user_id, question_id, kind, xp FROM xp_event")],
    )


def test_rate_records_the_review_the_attempt_and_the_xp(conn) -> None:
    out = rate(conn, USER, "prob-001", G, T0, result("correct"))  # difficulty 1
    assert out.xp == 1 * 3 and out.state.interval_days == 1
    assert review.get_state(conn, USER, "prob-001") == out.state
    stats = db.get_question(conn, "prob-001").solve_stats
    assert (stats.attempts, stats.correct) == (1, 1)
    assert progress.total_xp(conn, USER) == 3


@pytest.mark.parametrize("rating,mult", [(A, 1), (H, 2), (G, 3), (E, 4)])
def test_xp_depends_on_difficulty_and_rating(conn, rating, mult) -> None:
    out = rate(conn, USER, "prob-008", rating, T0, result("self_graded", False))  # difficulty 4
    assert out.xp == 4 * mult


def test_a_rating_not_allowed_for_the_answer_is_refused_and_writes_nothing(conn) -> None:
    before = snapshot(conn)
    with pytest.raises(ValueError, match="not allowed"):
        rate(conn, USER, "prob-001", G, T0, result("incorrect"))
    with pytest.raises(ValueError, match="not allowed"):
        rate(conn, USER, "prob-001", A, T0, result("unreadable", False))
    assert snapshot(conn) == before


def test_rating_a_non_trusted_question_is_refused(conn) -> None:
    with pytest.raises(ValueError, match="trusted|playable"):
        rate(conn, USER, "stat-005", G, T0, result("self_graded", False))
    add_question(conn, "gen-001", source="generated")
    with pytest.raises(ValueError):
        rate(conn, USER, "gen-001", G, T0, result("correct"))
    assert progress.total_xp(conn, USER) == 0


@pytest.mark.parametrize("failing", ["_upsert_state", "_bump_solve_stats", "insert_xp_event"])
def test_a_failure_midway_saves_no_review_and_no_xp(conn, monkeypatch, failing) -> None:
    before = snapshot(conn)

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("disk on fire")

    target = progress if failing == "insert_xp_event" else review
    monkeypatch.setattr(target, failing, boom)
    # practice.rate must call progress.insert_xp_event through the module so the patch applies
    with pytest.raises(RuntimeError, match="disk on fire"):
        rate(conn, USER, "prob-001", G, T0, result("correct"))
    assert snapshot(conn) == before
    assert not conn.in_transaction
    monkeypatch.undo()
    assert rate(conn, USER, "prob-001", G, T0, result("correct")).xp == 3  # still usable


def test_the_extra_writes_hook_runs_inside_the_review_transaction(conn) -> None:
    seen = {}

    def hook(c: sqlite3.Connection) -> None:
        seen["in_tx"] = c.in_transaction
        c.execute(
            "INSERT INTO xp_event (user_id, question_id, kind, xp, at) "
            "VALUES ('local', 'prob-001', 'review', 7, '2026-01-01T09:00:00.000000Z')"
        )

    review.record_review(conn, USER, "prob-001", G, T0, extra_writes=hook)
    assert seen["in_tx"] is True and progress.total_xp(conn, USER) == 7


def test_a_failing_extra_writes_hook_rolls_the_review_back(conn) -> None:
    before = snapshot(conn)

    def hook(c: sqlite3.Connection) -> None:
        raise RuntimeError("hook failed")

    with pytest.raises(RuntimeError, match="hook failed"):
        review.record_review(conn, USER, "prob-001", G, T0, extra_writes=hook)
    assert snapshot(conn) == before


def test_practice_module_exposes_the_helpers_the_pages_need() -> None:
    for name in ("Card", "build_queue", "allowed_ratings", "rate", "RatingOutcome"):
        assert hasattr(practice, name)
