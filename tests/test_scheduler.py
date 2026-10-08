"""SM-2 scheduler: rating-to-quality mapping, interval growth, lapses, due-date ordering."""

from datetime import UTC, datetime, timedelta

import pytest

from core.scheduler import QUALITY, SM2, Rating, ReviewState, Scheduler

T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def run(ratings: list[Rating], sched: SM2 | None = None, start: datetime = T0) -> list[ReviewState]:
    """Review a fresh card repeatedly, each time exactly when it falls due."""
    sched = sched or SM2()
    state, now, out = sched.initial("u", "prob-001", start), start, []
    for rating in ratings:
        state = sched.review(state, rating, now)
        out.append(state)
        now = state.due_at
    return out


G, E, H, A = Rating.GOOD, Rating.EASY, Rating.HARD, Rating.AGAIN


# --- rating -> SM-2 quality mapping ---------------------------------------------------------


def test_quality_mapping_is_the_documented_one() -> None:
    """Four ratings map onto SM-2's 0-5 grade. Below 3 is a lapse; 3, 4, 5 are passes."""
    assert QUALITY == {Rating.AGAIN: 1, Rating.HARD: 3, Rating.GOOD: 4, Rating.EASY: 5}
    assert all(q < 3 for r, q in QUALITY.items() if r is Rating.AGAIN)
    assert all(q >= 3 for r, q in QUALITY.items() if r is not Rating.AGAIN)


def test_mapping_is_documented_in_the_module() -> None:
    import core.scheduler as mod

    doc = mod.__doc__ or ""
    for needle in ("AGAIN", "HARD", "GOOD", "EASY", "quality", "lapse", "0.2", "1.3"):
        assert needle in doc, needle


@pytest.mark.parametrize(
    "rating,delta", [(E, +0.10), (G, 0.0), (H, -0.14)]
)  # SM-2: EF' = EF + 0.1 - (5-q)(0.08 + (5-q)0.02)
def test_ease_change_per_rating_matches_the_sm2_formula(rating: Rating, delta: float) -> None:
    (s,) = run([rating])
    assert s.ease == pytest.approx(2.5 + delta, abs=1e-9)


def test_again_lowers_ease_by_the_lapse_penalty_not_the_raw_formula() -> None:
    """Canonical SM-2 leaves EF unchanged on a failure and the raw formula gives -0.54 for q=1;
    we use a fixed -0.2 (Anki-style). Documented deviation."""
    (s,) = run([A])
    assert s.ease == pytest.approx(2.3)


# --- interval growth ------------------------------------------------------------------------


def test_good_reviews_grow_1_6_15_38_95_238() -> None:
    states = run([G] * 6)
    assert [s.interval_days for s in states] == [1, 6, 15, 38, 95, 238]
    assert all(s.ease == pytest.approx(2.5) for s in states)
    assert [s.repetitions for s in states] == [1, 2, 3, 4, 5, 6]


def test_easy_reviews_grow_faster_and_raise_ease() -> None:
    states = run([E] * 4)
    assert [s.interval_days for s in states] == [1, 6, 16, 45]
    assert [round(s.ease, 2) for s in states] == [2.6, 2.7, 2.8, 2.9]


def test_hard_reviews_grow_slowly_and_lower_ease() -> None:
    states = run([H] * 4)
    assert [s.interval_days for s in states] == [1, 6, 13, 27]
    assert [round(s.ease, 2) for s in states] == [2.36, 2.22, 2.08, 1.94]


def test_rating_ordering_of_intervals() -> None:
    hard, good, easy = (run([r] * 5)[-1].interval_days for r in (H, G, E))
    assert hard < good < easy


def test_intervals_never_shrink_during_a_success_streak() -> None:
    for rating in (H, G, E):
        iv = [s.interval_days for s in run([rating] * 12)]
        assert iv == sorted(iv)


def test_ease_never_drops_below_the_floor() -> None:
    states = run([H] * 30)
    assert min(s.ease for s in states) == pytest.approx(1.3)
    again = run([A] * 20)
    assert min(s.ease for s in again) == pytest.approx(1.3)


def test_interval_is_capped() -> None:
    states = run([E] * 20, SM2(max_interval=365))
    assert max(s.interval_days for s in states) == 365


def test_due_date_is_now_plus_interval_in_utc() -> None:
    sched = SM2()
    s = sched.review(sched.initial("u", "prob-001", T0), G, T0)
    assert s.due_at == T0 + timedelta(days=1) and s.last_reviewed_at == T0
    assert s.due_at.tzinfo is UTC


def test_reviewing_early_or_late_counts_from_the_review_time() -> None:
    sched = SM2()
    s = sched.review(sched.initial("u", "prob-001", T0), G, T0)
    late = sched.review(s, G, T0 + timedelta(days=10))
    assert late.interval_days == 6 and late.due_at == T0 + timedelta(days=16)


# --- lapses ---------------------------------------------------------------------------------


def test_lapse_resets_the_interval_and_streak() -> None:
    before = run([G, G, G])[-1]
    assert before.interval_days == 15 and before.repetitions == 3
    after = run([G, G, G, A])[-1]
    assert after.interval_days == 1 and after.repetitions == 0 and after.lapses == 1
    assert after.ease == pytest.approx(2.3)
    assert after.due_at == before.due_at + timedelta(days=1)


def test_recovery_after_a_lapse_restarts_the_ladder() -> None:
    states = run([G, G, G, A, G, G, G])
    assert [s.interval_days for s in states] == [1, 6, 15, 1, 1, 6, 14]  # 6 * 2.3 = 13.8 -> 14


def test_lapses_accumulate_and_do_not_reset() -> None:
    assert run([G, A, G, A, A, G])[-1].lapses == 3


def test_a_lapse_on_a_new_card_is_counted() -> None:
    (s,) = run([A])
    assert s.lapses == 1 and s.interval_days == 1 and s.repetitions == 0


# --- due-date ordering ----------------------------------------------------------------------


def test_cards_sort_by_due_date_after_different_histories() -> None:
    cards = {
        "lapsed": run([G, G, G, A])[-1],
        "hard": run([H, H, H])[-1],
        "good": run([G, G, G])[-1],
        "easy": run([E, E, E])[-1],
    }
    order = sorted(cards, key=lambda k: cards[k].due_at)
    # all four were reviewed on their own ladders; compare by days from each card's own review
    assert (
        cards["lapsed"].interval_days
        == 1
        < cards["hard"].interval_days
        < cards["good"].interval_days
    )
    assert cards["good"].interval_days < cards["easy"].interval_days
    assert order[-1] == "easy"


def test_same_review_time_orders_by_interval() -> None:
    sched = SM2()
    base = sched.review(sched.initial("u", "q-1", T0), G, T0)
    base = sched.review(base, G, T0 + timedelta(days=1))
    weak = sched.review(base, A, T0 + timedelta(days=7))
    strong = sched.review(base, E, T0 + timedelta(days=7))
    assert weak.due_at < strong.due_at


# --- purity, validation, interface ----------------------------------------------------------


def test_review_does_not_mutate_its_input() -> None:
    sched = SM2()
    s0 = sched.initial("u", "prob-001", T0)
    snapshot = s0.model_copy()
    sched.review(s0, G, T0)
    assert s0 == snapshot
    with pytest.raises(Exception):  # noqa: B017 - frozen model
        s0.ease = 9.0  # type: ignore[misc]


def test_naive_clock_is_rejected() -> None:
    sched = SM2()
    with pytest.raises(ValueError, match="timezone"):
        sched.initial("u", "prob-001", datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="timezone"):
        sched.review(sched.initial("u", "prob-001", T0), G, datetime(2026, 1, 1))


def test_non_utc_clock_is_normalised_to_utc() -> None:
    from datetime import timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    sched = SM2()
    s = sched.review(sched.initial("u", "q-1", T0), G, datetime(2026, 1, 1, 14, 30, tzinfo=ist))
    assert s.last_reviewed_at == T0 and s.last_reviewed_at.utcoffset() == timedelta(0)
    assert s.due_at.utcoffset() == timedelta(0)


def test_sm2_satisfies_the_scheduler_protocol_and_so_can_a_stub() -> None:
    class Fixed:
        """A stand-in for FSRS: always one week, no ease."""

        def initial(self, user_id: str, question_id: str, now: datetime) -> ReviewState:
            return ReviewState(user_id=user_id, question_id=question_id, due_at=now)

        def review(self, state: ReviewState, rating: Rating, now: datetime) -> ReviewState:
            return state.model_copy(update={"interval_days": 7, "due_at": now + timedelta(days=7)})

    assert isinstance(SM2(), Scheduler) and isinstance(Fixed(), Scheduler)
    s = Fixed().review(Fixed().initial("u", "q-1", T0), G, T0)
    assert s.interval_days == 7


def test_review_state_validates_fields() -> None:
    with pytest.raises(ValueError):
        ReviewState(user_id="", question_id="prob-001", due_at=T0)
    with pytest.raises(ValueError):
        ReviewState(user_id="u", question_id="prob-001", due_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        ReviewState(user_id="u", question_id="prob-001", due_at=T0, ease=1.0)
    with pytest.raises(ValueError):
        ReviewState(user_id="u", question_id="prob-001", due_at=T0, interval_days=-1)
