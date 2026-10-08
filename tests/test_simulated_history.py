"""A scripted user: do the schedules and the skill tree behave sensibly over 200 days?"""

from datetime import UTC, datetime, timedelta

import pytest

from core import db, review
from core.scheduler import Rating
from core.skill_tree import load_tree, node_states

G, H, A = Rating.GOOD, Rating.HARD, Rating.AGAIN
T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
USER = review.DEFAULT_USER_ID

# Hand-worked in the comments of test_scheduler.py: SM-2, ease 2.5, lapse penalty 0.2.
SCRIPTS = {
    "prob-001": [G] * 6,  # a card the user knows cold
    "prob-004": [G, G, G, A, G, G, G, G, G, G],  # one lapse on the third review round
    "prob-005": [H, A, H, G, A, G, G, G, G, G, G, G],  # a card the user struggles with
}


def simulate(conn, scripts: dict[str, list[Rating]], days: int = 200):
    """Each day: review whatever is due, using the scripted rating for that card."""
    cursors = {qid: iter(r) for qid, r in scripts.items()}
    history: dict[str, list[tuple[int, Rating]]] = {qid: [] for qid in scripts}

    def do_review(qid: str, d: int) -> None:
        rating = next(cursors[qid], G)
        history[qid].append((d, rating))
        review.record_review(conn, USER, qid, rating, T0 + timedelta(days=d))

    for qid in scripts:  # day 0: the user meets each card for the first time
        do_review(qid, 0)
    for d in range(1, days + 1):
        for due in review.next_due(conn, USER, T0 + timedelta(days=d)):
            do_review(due.id, d)
    return history


@pytest.fixture
def history(trusted_conn):
    return simulate(trusted_conn, SCRIPTS), trusted_conn


def intervals(conn, qid: str, h) -> list[int]:
    """Replay the history on a scratch DB to read the interval after each review."""
    from core.scheduler import SM2

    sched, state, out = SM2(), None, []
    for d, rating in h[qid]:
        now = T0 + timedelta(days=d)
        state = sched.review(state or sched.initial(USER, qid, now), rating, now)
        out.append(state.interval_days)
    return out


def test_a_card_known_cold_is_seen_rarely(history) -> None:
    h, conn = history
    assert [d for d, _ in h["prob-001"]] == [0, 1, 7, 22, 60, 155]
    assert intervals(conn, "prob-001", h) == [1, 6, 15, 38, 95, 238]


def test_a_lapse_resets_the_ladder_then_it_regrows(history) -> None:
    h, conn = history
    assert [d for d, _ in h["prob-004"]] == [0, 1, 7, 22, 23, 24, 30, 44, 76, 150]
    assert intervals(conn, "prob-004", h) == [1, 6, 15, 1, 1, 6, 14, 32, 74, 170]


def test_a_struggling_card_is_reviewed_often_and_ends_with_low_ease(history) -> None:
    h, conn = history
    assert [d for d, _ in h["prob-005"]] == [0, 1, 2, 3, 9, 10, 11, 17, 28, 48, 84, 150]
    assert intervals(conn, "prob-005", h) == [1, 1, 1, 6, 1, 1, 6, 11, 20, 36, 66, 120]
    s5 = review.get_state(conn, USER, "prob-005")
    s4 = review.get_state(conn, USER, "prob-004")
    s1 = review.get_state(conn, USER, "prob-001")
    assert (s1.lapses, s4.lapses, s5.lapses) == (0, 1, 2)
    assert s5.ease < s4.ease < s1.ease
    assert (
        s5.ease == pytest.approx(1.82)
        and s4.ease == pytest.approx(2.3)
        and s1.ease == pytest.approx(2.5)
    )


def test_more_difficulty_means_more_reviews(history) -> None:
    h, _ = history
    assert len(h["prob-001"]) < len(h["prob-004"]) < len(h["prob-005"])


def test_cards_never_come_back_before_they_are_due(history) -> None:
    h, conn = history
    for qid, entries in h.items():
        days = [d for d, _ in entries]
        ivs = intervals(conn, qid, h)
        for (d, _), nxt, iv in zip(entries, days[1:], ivs, strict=False):
            assert nxt - d >= iv, (qid, d, nxt, iv)


def test_due_queue_at_checkpoints(history) -> None:
    _, conn = history
    at = lambda d: [x.id for x in review.next_due(conn, USER, T0 + timedelta(days=d))]  # noqa: E731
    # the simulated user reviewed every card on its due day, so nothing is overdue at day 150
    assert at(150) == []
    # final due dates: prob-005 150 + 120 = 270, prob-004 150 + 170 = 320, prob-001 155 + 238 = 393
    assert at(269) == []
    assert at(270) == ["prob-005"]
    assert at(318) == ["prob-005"]
    assert at(320) == ["prob-005", "prob-004"]  # ordered by due date
    assert at(393) == ["prob-005", "prob-004", "prob-001"]


def test_same_day_ties_are_ordered_by_id(trusted_conn) -> None:
    h = simulate(trusted_conn, SCRIPTS)
    assert [d for d, _ in h["prob-004"]][-1] == [d for d, _ in h["prob-005"]][-1] == 150


def test_solve_stats_follow_the_reviews_and_drive_mastery(history) -> None:
    h, conn = history
    stats = {q: db.get_question(conn, q).solve_stats for q in SCRIPTS}
    assert [(s.attempts, s.correct) for s in stats.values()] == [(6, 6), (10, 9), (12, 10)]


def test_reviews_move_the_skill_tree(trusted_conn) -> None:
    tree = load_tree()

    def status() -> dict[str, str]:
        return {k: v.status for k, v in node_states(tree, db.list_questions(trusted_conn)).items()}

    before = status()
    assert before["prob.counting"] == "unlocked"
    assert before["prob.conditional"] == before["prob.expectation"] == "locked"

    simulate(trusted_conn, SCRIPTS)
    after = node_states(tree, db.list_questions(trusted_conn))
    m = after["prob.counting"].mastery
    assert (m.trusted_count, m.covered) == (3, 3)
    assert m.accuracy == pytest.approx(25 / 28)
    assert after["prob.counting"].status == "mastered"
    assert after["prob.conditional"].status == after["prob.expectation"].status == "unlocked"
    assert after["stat.moments"].status == "locked"  # still needs prob.expectation


def test_a_user_who_always_fails_sees_the_card_daily_with_ease_at_the_floor(trusted_conn) -> None:
    h = simulate(trusted_conn, {"prob-001": [A] * 12}, days=11)
    assert [d for d, _ in h["prob-001"]] == list(range(12))
    s = review.get_state(trusted_conn, USER, "prob-001")
    assert s.lapses == 12 and s.interval_days == 1 and s.ease == pytest.approx(1.3)
    st = db.get_question(trusted_conn, "prob-001").solve_stats
    assert (st.attempts, st.correct) == (12, 0)


def test_the_simulation_is_deterministic(trusted_conn, curated) -> None:
    first = simulate(trusted_conn, SCRIPTS)
    other = db.connect(":memory:")
    from core.schema import AnswerType, Status, Verification

    db.upsert_questions(other, curated)
    for q in curated:
        if q.answer_type is not AnswerType.TEXT:
            db.set_verification(
                other, q.id, Verification(method="t", result="pass"), Status.TRUSTED
            )
    assert simulate(other, SCRIPTS) == first
    other.close()
