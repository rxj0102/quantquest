"""Timed interview mode: a fixed set of questions against a countdown that cannot be reset.

The deadline is stored in SQLite when the run starts, and all time arithmetic uses the ``now``
the caller passes in (the server's clock, never the browser's). Consequently:

* refreshing the page, or opening a second tab, finds the same active run with the same deadline
  and the same questions: ``start_run`` returns the existing run instead of creating a new one;
* at most one run per user can be active at a time (enforced by a partial unique index, and by a
  ``BEGIN IMMEDIATE`` transaction so simultaneous starts agree);
* an answer submitted at or after the deadline is rejected and not stored;
* a run discovered to be overdue (say after a refresh) is marked expired, with the time used
  capped at the limit.

Interviews are separate from spaced repetition: they never touch ``review_state`` or
``solve_stats`` (so repeated mock runs cannot inflate mastery). They award XP, and count as
activity for streaks. Only trusted, curated, auto-gradable (numeric or symbolic) questions from
unlocked nodes are used.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from core import db, progress
from core.schema import AnswerType, Question
from core.skill_tree import SkillTree, node_states
from core.timeutil import from_db, to_db, to_utc
from verify.answers import AnswerResult, check_typed_answer


class InterviewError(Exception):
    """Base class for interview errors."""


class InterviewUnavailable(InterviewError):
    """There are no eligible questions to build a run from."""


class DeadlinePassed(InterviewError):
    """The run is over (deadline reached or already finished)."""


class AlreadyAnswered(InterviewError):
    """That question already has a stored answer."""


class NotInRun(InterviewError):
    """The question is not part of this run, or is no longer available."""


@dataclass(frozen=True)
class Run:
    """A stored interview run."""

    id: str
    user_id: str
    started_at: datetime
    deadline_at: datetime
    duration_seconds: int
    question_ids: tuple[str, ...]
    status: Literal["active", "finished", "expired"]
    finished_at: datetime | None


def _run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        user_id=row["user_id"],
        started_at=from_db(row["started_at"]),
        deadline_at=from_db(row["deadline_at"]),
        duration_seconds=row["duration_seconds"],
        question_ids=tuple(json.loads(row["question_ids"])),
        status=row["status"],
        finished_at=from_db(row["finished_at"]) if row["finished_at"] else None,
    )


def get_run(conn: sqlite3.Connection, run_id: str) -> Run | None:
    """A run by id, or None."""
    with closing(conn.execute("SELECT * FROM interview_run WHERE id = ?", (run_id,))) as cur:
        row = cur.fetchone()
    return _run(row) if row else None


def latest_run(conn: sqlite3.Connection, user_id: str) -> Run | None:
    """The user's most recently started run (active, finished or expired), or None."""
    with closing(
        conn.execute(
            "SELECT * FROM interview_run WHERE user_id = ? "
            "ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (user_id,),
        )
    ) as cur:
        row = cur.fetchone()
    return _run(row) if row else None


def remaining_seconds(run: Run, now: datetime) -> int:
    """Whole seconds left (rounded up), never negative. Based only on the stored deadline."""
    return max(0, math.ceil((run.deadline_at - to_utc(now)).total_seconds()))


def select_questions(
    conn: sqlite3.Connection, tree: SkillTree, user_id: str, n: int, seed: int
) -> list[Question]:
    """A deterministic (for ``seed``) mix of up to ``n`` eligible questions, spread over nodes."""
    playable = db.list_playable(conn)
    states = node_states(tree, playable)
    by_node: dict[str, list[Question]] = {}
    for q in sorted(playable, key=lambda q: q.id):
        if (
            q.answer_type in (AnswerType.NUMERIC, AnswerType.SYMBOLIC)
            and q.node_id in states
            and states[q.node_id].status != "locked"
        ):
            by_node.setdefault(q.node_id, []).append(q)
    rng = random.Random(seed)
    nodes = [nid for nid in tree.order if nid in by_node]
    rng.shuffle(nodes)
    for qs in by_node.values():
        rng.shuffle(qs)
    chosen: list[Question] = []
    while len(chosen) < n and any(by_node[nid] for nid in nodes):
        for nid in nodes:
            if by_node[nid] and len(chosen) < n:
                chosen.append(by_node[nid].pop())
    return chosen


def _expire_overdue(conn: sqlite3.Connection, user_id: str, now: datetime) -> None:
    """Mark the user's active run expired if its deadline has been reached. Does not commit."""
    conn.execute(
        "UPDATE interview_run SET status = 'expired', finished_at = deadline_at "
        "WHERE user_id = ? AND status = 'active' AND deadline_at <= ?",
        (user_id, to_db(now)),
    )


def get_active_run(conn: sqlite3.Connection, user_id: str, now: datetime) -> Run | None:
    """The user's run if it is still running at ``now``; an overdue run is expired and ignored."""
    now = to_utc(now)
    with conn:
        _expire_overdue(conn, user_id, now)
    with closing(
        conn.execute(
            "SELECT * FROM interview_run WHERE user_id = ? AND status = 'active'", (user_id,)
        )
    ) as cur:
        row = cur.fetchone()
    return _run(row) if row else None


def start_run(
    conn: sqlite3.Connection,
    tree: SkillTree,
    user_id: str,
    now: datetime,
    *,
    n_questions: int,
    minutes: int,
    seed: int | None = None,
) -> Run:
    """Start a run, or return the user's active one unchanged.

    Calling this again (a refresh, a second tab, a double click) never resets the deadline, the
    questions, or the length: the already-active run is returned as it is.
    """
    if n_questions < 1 or minutes < 1:
        raise ValueError("n_questions and minutes must be >= 1")
    now = to_utc(now)
    if conn.in_transaction:
        raise RuntimeError("start_run needs its own transaction; commit or roll back first")
    conn.execute("BEGIN IMMEDIATE")
    try:
        _expire_overdue(conn, user_id, now)
        row = conn.execute(
            "SELECT * FROM interview_run WHERE user_id = ? AND status = 'active'", (user_id,)
        ).fetchone()
        if row is not None:
            conn.commit()
            return _run(row)
        chosen = select_questions(
            conn, tree, user_id, n_questions, int(now.timestamp() * 1000) if seed is None else seed
        )
        if not chosen:
            raise InterviewUnavailable("no eligible questions: master a node or add content")
        run_id = uuid.uuid4().hex
        deadline = now + timedelta(minutes=minutes)
        conn.execute(
            "INSERT INTO interview_run (id, user_id, started_at, deadline_at, duration_seconds, "
            "question_ids, status) VALUES (?, ?, ?, ?, ?, ?, 'active')",
            (
                run_id,
                user_id,
                to_db(now),
                to_db(deadline),
                minutes * 60,
                json.dumps([q.id for q in chosen]),
            ),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    run = get_run(conn, run_id)
    assert run is not None
    return run


@dataclass(frozen=True)
class Submission:
    """The outcome of submitting one answer."""

    result: AnswerResult
    xp: int
    stored: bool


def submit_answer(
    conn: sqlite3.Connection,
    run_id: str,
    question_id: str,
    typed: str,
    now: datetime,
    *,
    ui_rel_tol: float,
    timeout: float = 10.0,
) -> Submission:
    """Check and store one answer. Unreadable input is not stored (the user can retry).

    Raises ``DeadlinePassed`` at or after the deadline (or once finished), ``NotInRun`` for a
    question outside the fixed set, ``AlreadyAnswered`` for a second answer.
    """
    now = to_utc(now)
    run = get_run(conn, run_id)
    if run is None:
        raise KeyError(run_id)
    if run.status != "active" or now >= run.deadline_at:
        if run.status == "active":
            with conn:
                _expire_overdue(conn, run.user_id, now)
        raise DeadlinePassed("this interview is over")
    if question_id not in run.question_ids:
        raise NotInRun(question_id)
    if _answered(conn, run_id, question_id):
        raise AlreadyAnswered(question_id)
    question = db.get_playable(conn, question_id)
    if question is None:
        raise NotInRun(f"{question_id} is no longer available")
    result = check_typed_answer(question, typed, ui_rel_tol=ui_rel_tol, timeout=timeout)
    if result.outcome == "unreadable":
        return Submission(result, 0, False)
    if result.outcome not in ("correct", "incorrect"):
        raise ValueError("interview questions must be auto-gradable")
    xp = progress.xp_for_interview(question.difficulty, result.correct)
    conn.execute("BEGIN IMMEDIATE")
    try:
        status = conn.execute("SELECT status FROM interview_run WHERE id = ?", (run_id,)).fetchone()
        if status is None or status[0] != "active":
            raise DeadlinePassed("this interview is over")
        if _answered(conn, run_id, question_id):
            raise AlreadyAnswered(question_id)
        conn.execute(
            "INSERT INTO interview_answer (run_id, question_id, typed, outcome, answered_at, xp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, question_id, typed, result.outcome, to_db(now), xp),
        )
        progress.insert_xp_event(conn, run.user_id, question_id, "interview", xp, now)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return Submission(result, xp, True)


def _answered(conn: sqlite3.Connection, run_id: str, question_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM interview_answer WHERE run_id = ? AND question_id = ?", (run_id, question_id)
    ).fetchone()
    return row is not None


@dataclass(frozen=True)
class ItemResult:
    """One question in a results summary."""

    question: Question | None  # None if the question was withdrawn after the run started
    typed: str | None
    outcome: Literal["correct", "incorrect", "unanswered", "withdrawn"]


@dataclass(frozen=True)
class RunSummary:
    """Results of a finished or expired run."""

    run_id: str
    status: str
    total: int
    correct: int
    incorrect: int
    unanswered: int
    score: float
    time_used_seconds: int
    xp_earned: int
    items: tuple[ItemResult, ...]
    by_node: dict[str, tuple[int, int]]  # node_id -> (correct, total)


def summarize(conn: sqlite3.Connection, run_id: str, now: datetime | None = None) -> RunSummary:
    """Results so far. Withdrawn (no longer trusted) questions are listed without any content."""
    run = get_run(conn, run_id)
    if run is None:
        raise KeyError(run_id)
    answers = {
        r["question_id"]: r
        for r in conn.execute("SELECT * FROM interview_answer WHERE run_id = ?", (run_id,))
    }
    items: list[ItemResult] = []
    by_node: dict[str, list[int]] = {}
    for qid in run.question_ids:
        question = db.get_playable(conn, qid)
        if question is None:
            items.append(ItemResult(None, None, "withdrawn"))
            continue
        ans = answers.get(qid)
        outcome = ans["outcome"] if ans else "unanswered"
        items.append(ItemResult(question, ans["typed"] if ans else None, outcome))
        tally = by_node.setdefault(question.node_id, [0, 0])
        tally[0] += outcome == "correct"
        tally[1] += 1
    shown = [i for i in items if i.outcome != "withdrawn"]
    correct = sum(i.outcome == "correct" for i in shown)
    incorrect = sum(i.outcome == "incorrect" for i in shown)
    if run.finished_at is not None:
        end = run.finished_at
    elif now is not None:
        end = min(to_utc(now), run.deadline_at)
    else:
        end = run.started_at
    return RunSummary(
        run_id=run_id,
        status=run.status,
        total=len(shown),
        correct=correct,
        incorrect=incorrect,
        unanswered=len(shown) - correct - incorrect,
        score=correct / len(shown) if shown else 0.0,
        time_used_seconds=max(0, round((end - run.started_at).total_seconds())),
        xp_earned=sum(a["xp"] for a in answers.values()),
        items=tuple(items),
        by_node={k: (v[0], v[1]) for k, v in by_node.items()},
    )


def finish_run(conn: sqlite3.Connection, run_id: str, now: datetime) -> RunSummary:
    """End the run and return its summary. Idempotent: a second call changes nothing."""
    now = to_utc(now)
    run = get_run(conn, run_id)
    if run is None:
        raise KeyError(run_id)
    if run.status == "active":
        overdue = now >= run.deadline_at
        with conn:
            conn.execute(
                "UPDATE interview_run SET status = ?, finished_at = ? "
                "WHERE id = ? AND status = 'active'",
                (
                    "expired" if overdue else "finished",
                    to_db(run.deadline_at if overdue else now),
                    run_id,
                ),
            )
    return summarize(conn, run_id)
