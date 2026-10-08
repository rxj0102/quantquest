"""Persist review state and decide what is due.

Only ``trusted`` questions are ever scheduled. ``record_review`` is atomic: the review state and
the question's ``solve_stats`` are updated in one transaction, so a failure part-way saves
nothing. Every function that depends on the time takes ``now`` as a parameter; nothing here
reads the system clock.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime

from core.db import question_from_row
from core.scheduler import SM2, Rating, ReviewState, Scheduler
from core.schema import Question, SolveStats, Status
from core.timeutil import from_db, to_db, to_utc

DEFAULT_USER_ID = "local"  # single local user for now; the column exists for multi-user later


def _check_user(user_id: str) -> None:
    if not isinstance(user_id, str) or not 1 <= len(user_id) <= 64:
        raise ValueError("user_id must be a string of 1 to 64 characters")


def _row_to_state(row: sqlite3.Row) -> ReviewState:
    return ReviewState(
        user_id=row["user_id"],
        question_id=row["question_id"],
        ease=row["ease"],
        interval_days=row["interval_days"],
        repetitions=row["repetitions"],
        lapses=row["lapses"],
        due_at=from_db(row["due_at"]),
        last_reviewed_at=from_db(row["last_reviewed_at"]) if row["last_reviewed_at"] else None,
    )


def get_state(conn: sqlite3.Connection, user_id: str, question_id: str) -> ReviewState | None:
    """The stored review state for this user and question, or None if never reviewed."""
    with closing(
        conn.execute(
            "SELECT * FROM review_state WHERE user_id = ? AND question_id = ?",
            (user_id, question_id),
        )
    ) as cur:
        row = cur.fetchone()
    return _row_to_state(row) if row else None


def _upsert_state(conn: sqlite3.Connection, state: ReviewState) -> None:
    """Write a review state. Does not commit."""
    conn.execute(
        """
        INSERT INTO review_state (user_id, question_id, ease, interval_days, repetitions,
                                  lapses, due_at, last_reviewed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, question_id) DO UPDATE SET
            ease = excluded.ease,
            interval_days = excluded.interval_days,
            repetitions = excluded.repetitions,
            lapses = excluded.lapses,
            due_at = excluded.due_at,
            last_reviewed_at = excluded.last_reviewed_at
        """,
        (
            state.user_id,
            state.question_id,
            state.ease,
            state.interval_days,
            state.repetitions,
            state.lapses,
            to_db(state.due_at),
            to_db(state.last_reviewed_at) if state.last_reviewed_at else None,
        ),
    )


def _bump_solve_stats(conn: sqlite3.Connection, question_id: str, *, correct: bool) -> None:
    """Count one attempt on the question. Does not commit."""
    row = conn.execute("SELECT solve_stats FROM questions WHERE id = ?", (question_id,)).fetchone()
    stats = SolveStats.model_validate_json(row["solve_stats"])
    bumped = stats.model_copy(
        update={"attempts": stats.attempts + 1, "correct": stats.correct + int(correct)}
    )
    conn.execute(
        "UPDATE questions SET solve_stats = ? WHERE id = ?",
        (bumped.model_dump_json(), question_id),
    )


def record_review(
    conn: sqlite3.Connection,
    user_id: str,
    question_id: str,
    rating: Rating,
    now: datetime,
    scheduler: Scheduler | None = None,
    extra_writes: Callable[[sqlite3.Connection], None] | None = None,
) -> ReviewState:
    """Record one answer: schedule the next review and count the attempt, atomically.

    Everything happens in a single ``BEGIN IMMEDIATE`` transaction, which also stops two writers
    from losing each other's updates. Any exception rolls the whole thing back. Raises
    ``KeyError`` for an unknown question, ``ValueError`` if the question is not ``trusted``, the
    clock is naive or ``user_id`` is invalid, and ``RuntimeError`` if the connection already has
    an open transaction (the caller's work would be committed or rolled back with ours).
    ``extra_writes`` runs inside the transaction (it must not commit), for writes that have to
    succeed or fail together with the review, such as the XP event.
    """
    _check_user(user_id)
    now = to_utc(now)
    rating = Rating(rating)
    scheduler = scheduler or SM2()
    if conn.in_transaction:
        raise RuntimeError("record_review needs its own transaction; commit or roll back first")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT status FROM questions WHERE id = ?", (question_id,)).fetchone()
        if row is None:
            raise KeyError(question_id)
        if row["status"] != Status.TRUSTED.value:
            raise ValueError(
                f"question {question_id} is {row['status']}; only trusted questions can be reviewed"
            )
        state = get_state(conn, user_id, question_id) or scheduler.initial(
            user_id, question_id, now
        )
        new_state = scheduler.review(state, rating, now)
        _upsert_state(conn, new_state)
        _bump_solve_stats(conn, question_id, correct=rating is not Rating.AGAIN)
        if extra_writes is not None:
            extra_writes(conn)  # same transaction: if it raises, everything above is rolled back
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return new_state


def next_due(
    conn: sqlite3.Connection, user_id: str, now: datetime, limit: int | None = None
) -> list[Question]:
    """Trusted questions this user has reviewed before whose due time is ``<= now``.

    Ordered by due time (most overdue first), ties broken by question id. A question that has
    stopped being trusted (flagged, retired or back to fresh) is never returned. Questions the
    user has never seen are not included; see ``unseen_trusted``.
    """
    _check_user(user_id)
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")
    sql = (
        "SELECT q.* FROM review_state r JOIN questions q ON q.id = r.question_id "
        "WHERE r.user_id = ? AND q.status = 'trusted' AND r.due_at <= ? "
        "ORDER BY r.due_at, q.id"
    )
    params: list[object] = [user_id, to_db(now)]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with closing(conn.execute(sql, params)) as cur:
        return [question_from_row(r) for r in cur.fetchall()]


def unseen_trusted(conn: sqlite3.Connection, user_id: str) -> list[Question]:
    """Trusted questions this user has never reviewed, ordered by id."""
    _check_user(user_id)
    sql = (
        "SELECT q.* FROM questions q WHERE q.status = 'trusted' AND NOT EXISTS "
        "(SELECT 1 FROM review_state r WHERE r.user_id = ? AND r.question_id = q.id) "
        "ORDER BY q.id"
    )
    with closing(conn.execute(sql, (user_id,))) as cur:
        return [question_from_row(r) for r in cur.fetchall()]
