"""XP and streaks, stored in SQLite.

XP is an append-only log (``xp_event``). Totals and streaks are derived from it at read time.
A "day" is a calendar day in the configured timezone (America/New_York by default); it is computed
from the stored UTC timestamp, so changing the zone regroups history instead of corrupting it, and
daylight-saving days (23 or 25 hours) are handled by the zone rules, not by adding 24 hours.

Streak rules: a day counts if it has any XP event. The current streak is the run of consecutive
days ending today, or ending yesterday if nothing has been done yet today (it only breaks once a
whole day has passed with no activity). ``longest`` is the best run ever. Every function takes the
clock as ``now``; nothing reads the system time.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

from core.scheduler import Rating
from core.timeutil import from_db, to_db, to_utc

XP_MULTIPLIER = {Rating.AGAIN: 1, Rating.HARD: 2, Rating.GOOD: 3, Rating.EASY: 4}
INTERVIEW_XP_PER_DIFFICULTY = 3


def xp_for_review(difficulty: int, rating: Rating) -> int:
    """XP for one review: question difficulty times a multiplier for how well it went."""
    return difficulty * XP_MULTIPLIER[Rating(rating)]


def xp_for_interview(difficulty: int, correct: bool) -> int:
    """XP for one interview answer: only correct answers earn any."""
    return difficulty * INTERVIEW_XP_PER_DIFFICULTY if correct else 0


def local_day(at: datetime, tz: tzinfo) -> date:
    """The calendar day of ``at`` in ``tz``. Raises ValueError for a naive datetime."""
    return to_utc(at).astimezone(tz).date()


def insert_xp_event(
    conn: sqlite3.Connection, user_id: str, question_id: str, kind: str, xp: int, at: datetime
) -> None:
    """Append an XP event. Does not commit, so callers can include it in their own transaction."""
    conn.execute(
        "INSERT INTO xp_event (user_id, question_id, kind, xp, at) VALUES (?, ?, ?, ?, ?)",
        (user_id, question_id, kind, xp, to_db(at)),
    )


def total_xp(conn: sqlite3.Connection, user_id: str) -> int:
    """All XP this user has earned."""
    row = conn.execute(
        "SELECT COALESCE(SUM(xp), 0) FROM xp_event WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(row[0])


def _events(conn: sqlite3.Connection, user_id: str) -> list[tuple[datetime, int]]:
    with closing(
        conn.execute("SELECT at, xp FROM xp_event WHERE user_id = ? ORDER BY at", (user_id,))
    ) as cur:
        return [(from_db(r["at"]), r["xp"]) for r in cur.fetchall()]


def xp_on_day(conn: sqlite3.Connection, user_id: str, day: date, tz: tzinfo) -> int:
    """XP earned on a given local day."""
    return sum(xp for at, xp in _events(conn, user_id) if local_day(at, tz) == day)


def active_days(conn: sqlite3.Connection, user_id: str, tz: tzinfo) -> list[date]:
    """Sorted distinct local days with any activity."""
    return sorted({local_day(at, tz) for at, _ in _events(conn, user_id)})


@dataclass(frozen=True)
class Streak:
    """Streak summary as of a moment in time."""

    current: int
    longest: int
    active_today: bool
    last_day: date | None


def streak(conn: sqlite3.Connection, user_id: str, now: datetime, tz: tzinfo) -> Streak:
    """Current and longest streak of consecutive active local days, as of ``now``."""
    days = active_days(conn, user_id, tz)
    if not days:
        return Streak(0, 0, False, None)
    today = local_day(now, tz)
    longest = run = 1
    for prev, cur in zip(days, days[1:], strict=False):
        run = run + 1 if cur - prev == timedelta(days=1) else 1
        longest = max(longest, run)
    last = days[-1]
    if last >= today - timedelta(days=1):  # active today, or yesterday and still alive
        current = 1
        for prev, cur in zip(reversed(days[:-1]), reversed(days), strict=False):
            if cur - prev != timedelta(days=1):
                break
            current += 1
    else:
        current = 0
    return Streak(current, longest, last == today, last)
