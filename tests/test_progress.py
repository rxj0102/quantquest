"""XP and streaks. Days are America/New_York days from the configured zone; clocks are injected."""

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core import progress
from core.progress import (
    INTERVIEW_XP_PER_DIFFICULTY,
    insert_xp_event,
    local_day,
    streak,
    total_xp,
    xp_for_interview,
    xp_for_review,
    xp_on_day,
)
from core.scheduler import Rating

NY = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")
USER = "local"


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def conn(trusted_conn):
    return trusted_conn


def log(conn, at: datetime, xp: int = 3, user: str = USER, qid: str = "prob-001") -> None:
    insert_xp_event(conn, user, qid, "review", xp, at)
    conn.commit()


# --- the midnight boundary --------------------------------------------------------------------------


def test_winter_midnight_boundary_is_05_00_utc() -> None:
    assert local_day(utc(2026, 1, 15, 4, 59, 59), NY) == date(2026, 1, 14)
    assert local_day(utc(2026, 1, 15, 5, 0, 0), NY) == date(2026, 1, 15)
    assert local_day(utc(2026, 1, 15, 5, 0, 0, 1), NY) == date(2026, 1, 15)


def test_summer_midnight_boundary_is_04_00_utc() -> None:
    assert local_day(utc(2026, 7, 15, 3, 59, 59), NY) == date(2026, 7, 14)
    assert local_day(utc(2026, 7, 15, 4, 0, 0), NY) == date(2026, 7, 15)


def test_utc_midnight_is_not_the_day_boundary() -> None:
    assert local_day(utc(2026, 1, 15, 0, 0, 0), NY) == date(2026, 1, 14)
    assert local_day(utc(2026, 1, 14, 23, 59, 59), NY) == date(2026, 1, 14)


def test_the_zone_comes_from_the_argument_so_config_changes_the_days() -> None:
    at = utc(2026, 1, 15, 20, 0, 0)  # 15:00 in New York, 05:00 the next day in Tokyo
    assert local_day(at, NY) == date(2026, 1, 15) and local_day(at, TOKYO) == date(2026, 1, 16)


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        local_day(datetime(2026, 1, 15, 12, 0), NY)


def test_one_second_either_side_of_midnight_is_two_streak_days(conn) -> None:
    log(conn, utc(2026, 1, 15, 4, 59, 59))  # 23:59:59 on the 14th
    log(conn, utc(2026, 1, 15, 5, 0, 0))  # 00:00:00 on the 15th
    s = streak(conn, USER, utc(2026, 1, 15, 5, 0, 1), NY)
    assert (s.current, s.longest, s.active_today) == (2, 2, True)


def test_two_reviews_in_the_same_local_evening_are_one_day(conn) -> None:
    log(conn, utc(2026, 1, 15, 1, 0, 0))  # 20:00 on the 14th
    log(conn, utc(2026, 1, 15, 4, 59, 59))  # 23:59:59 on the 14th
    assert streak(conn, USER, utc(2026, 1, 15, 4, 59, 59), NY).current == 1


def test_xp_on_day_respects_the_boundary(conn) -> None:
    log(conn, utc(2026, 1, 15, 4, 59, 59), xp=5)
    log(conn, utc(2026, 1, 15, 5, 0, 0), xp=7)
    assert xp_on_day(conn, USER, date(2026, 1, 14), NY) == 5
    assert xp_on_day(conn, USER, date(2026, 1, 15), NY) == 7
    assert total_xp(conn, USER) == 12


# --- DST ---------------------------------------------------------------------------------------------


def test_spring_forward_day_has_23_hours_and_does_not_break_a_streak(conn) -> None:
    for at in (utc(2026, 3, 7, 17), utc(2026, 3, 8, 17), utc(2026, 3, 9, 16)):  # noon locally
        log(conn, at)
    assert streak(conn, USER, utc(2026, 3, 9, 17), NY).current == 3
    assert local_day(utc(2026, 3, 8, 4, 59, 59), NY) == date(2026, 3, 7)
    assert local_day(utc(2026, 3, 8, 5, 0, 0), NY) == date(2026, 3, 8)
    assert local_day(utc(2026, 3, 9, 3, 59, 59), NY) == date(2026, 3, 8)  # 23 hours later
    assert local_day(utc(2026, 3, 9, 4, 0, 0), NY) == date(2026, 3, 9)


def test_fall_back_day_has_25_hours_and_does_not_break_a_streak(conn) -> None:
    log(conn, utc(2026, 11, 1, 3, 30))  # 23:30 EDT on Oct 31
    log(conn, utc(2026, 11, 1, 4, 30))  # 00:30 EDT on Nov 1
    log(conn, utc(2026, 11, 1, 6, 30))  # 01:30 EST on Nov 1 (the repeated hour): same day
    log(conn, utc(2026, 11, 2, 5, 30))  # 00:30 EST on Nov 2
    s = streak(conn, USER, utc(2026, 11, 2, 12), NY)
    assert s.current == 3 and s.longest == 3
    assert local_day(utc(2026, 11, 2, 4, 59, 59), NY) == date(2026, 11, 1)  # 25 hours long
    assert local_day(utc(2026, 11, 2, 5, 0, 0), NY) == date(2026, 11, 2)


# --- streak rules ------------------------------------------------------------------------------------


def test_no_activity_means_no_streak(conn) -> None:
    s = streak(conn, USER, utc(2026, 1, 15, 15), NY)
    assert (s.current, s.longest, s.active_today, s.last_day) == (0, 0, False, None)


def test_a_streak_stays_alive_until_the_end_of_the_next_day(conn) -> None:
    log(conn, utc(2026, 1, 13, 15))
    log(conn, utc(2026, 1, 14, 15))
    # nothing yet on the 15th: the streak of 2 is intact but not extended
    s = streak(conn, USER, utc(2026, 1, 15, 4, 59, 59 + 0), NY)  # still the 14th locally
    assert s.current == 2 and s.active_today is True
    s = streak(conn, USER, utc(2026, 1, 15, 20), NY)  # the 15th, no review yet
    assert s.current == 2 and s.active_today is False and s.last_day == date(2026, 1, 14)
    s = streak(conn, USER, utc(2026, 1, 16, 4, 59, 59), NY)  # still the 15th
    assert s.current == 2
    s = streak(conn, USER, utc(2026, 1, 16, 5, 0, 0), NY)  # the 16th: the 14th is two days ago
    assert s.current == 0 and s.longest == 2


def test_a_gap_breaks_the_streak_but_the_longest_is_kept(conn) -> None:
    for day in (1, 2, 3, 4, 7, 8):
        log(conn, utc(2026, 1, day, 15))
    s = streak(conn, USER, utc(2026, 1, 8, 20), NY)
    assert s.current == 2 and s.longest == 4


def test_longest_streak_can_be_the_current_one(conn) -> None:
    for day in (1, 2, 5, 6, 7):
        log(conn, utc(2026, 1, day, 15))
    s = streak(conn, USER, utc(2026, 1, 7, 20), NY)
    assert s.current == 3 and s.longest == 3


def test_streaks_cross_month_and_year_ends(conn) -> None:
    for at in (utc(2025, 12, 30, 15), utc(2025, 12, 31, 15), utc(2026, 1, 1, 15)):
        log(conn, at)
    assert streak(conn, USER, utc(2026, 1, 1, 20), NY).current == 3


def test_changing_the_zone_regroups_the_same_history(conn) -> None:
    log(conn, utc(2026, 1, 15, 2))  # 21:00 on the 14th in New York, 11:00 on the 15th in Tokyo
    log(conn, utc(2026, 1, 15, 20))  # 15:00 on the 15th in New York, 05:00 on the 16th in Tokyo
    assert streak(conn, USER, utc(2026, 1, 15, 21), NY).longest == 2  # the 14th and the 15th
    assert streak(conn, USER, utc(2026, 1, 15, 21), TOKYO).longest == 2  # the 15th and the 16th
    assert [str(d) for d in progress.active_days(conn, USER, NY)] == ["2026-01-14", "2026-01-15"]
    assert [str(d) for d in progress.active_days(conn, USER, TOKYO)] == ["2026-01-15", "2026-01-16"]


def test_users_are_isolated(conn) -> None:
    log(conn, utc(2026, 1, 15, 15), xp=4, user="alice")
    assert total_xp(conn, "alice") == 4 and total_xp(conn, USER) == 0
    assert streak(conn, "bob", utc(2026, 1, 15, 20), NY).current == 0


# --- XP rules ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rating,mult", [(Rating.AGAIN, 1), (Rating.HARD, 2), (Rating.GOOD, 3), (Rating.EASY, 4)]
)
def test_review_xp_is_difficulty_times_a_rating_multiplier(rating: Rating, mult: int) -> None:
    for difficulty in range(1, 6):
        assert xp_for_review(difficulty, rating) == difficulty * mult


def test_interview_xp_only_for_correct_answers() -> None:
    assert xp_for_interview(3, True) == 3 * INTERVIEW_XP_PER_DIFFICULTY
    assert xp_for_interview(3, False) == 0


def test_xp_events_are_stored_as_utc_fixed_width(conn) -> None:
    log(conn, datetime(2026, 1, 15, 10, 0, tzinfo=NY))
    raw = conn.execute("SELECT at FROM xp_event").fetchone()[0]
    assert raw == "2026-01-15T15:00:00.000000Z" and re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", raw)


def test_xp_events_need_an_aware_time_and_a_known_question(conn) -> None:
    with pytest.raises(ValueError, match="timezone"):
        insert_xp_event(conn, USER, "prob-001", "review", 3, datetime(2026, 1, 15, 10))
    with pytest.raises(Exception):  # noqa: B017 - foreign key
        insert_xp_event(conn, USER, "prob-404", "review", 3, utc(2026, 1, 15, 10))
    with pytest.raises(Exception):  # noqa: B017 - check constraint
        insert_xp_event(conn, USER, "prob-001", "review", -1, utc(2026, 1, 15, 10))
    with pytest.raises(Exception):  # noqa: B017 - check constraint
        insert_xp_event(conn, USER, "prob-001", "bonus", 1, utc(2026, 1, 15, 10))


def test_activity_from_either_kind_counts_for_the_streak(conn) -> None:
    insert_xp_event(conn, USER, "prob-001", "review", 3, utc(2026, 1, 14, 15))
    insert_xp_event(conn, USER, "prob-001", "interview", 9, utc(2026, 1, 15, 15))
    conn.commit()
    assert streak(conn, USER, utc(2026, 1, 15, 20), NY).current == 2


def test_progress_reads_do_not_depend_on_the_system_clock(conn, monkeypatch) -> None:
    """An injected `now` far in the future gives the future's answer."""
    log(conn, utc(2026, 1, 15, 15))
    assert streak(conn, USER, utc(2031, 6, 1, 12), NY).current == 0
    assert streak(conn, USER, utc(2026, 1, 15, 15) + timedelta(hours=1), NY).current == 1
