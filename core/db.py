"""SQLite storage for questions (plain sqlite3)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from pathlib import Path

from core.schema import Question, Status, Verification
from core.timeutil import from_db, to_db

# Never shown to users: flagged (failed verification or reported) and retired questions.
HIDDEN_STATUSES = (Status.FLAGGED.value, Status.RETIRED.value)
_HIDDEN_SQL = "(" + ", ".join(f"'{s}'" for s in HIDDEN_STATUSES) + ")"

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id            TEXT PRIMARY KEY,
    topic         TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    difficulty    INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    format        TEXT NOT NULL,
    prompt_md     TEXT NOT NULL,
    answer        TEXT NOT NULL,
    answer_type   TEXT NOT NULL,
    solution_md   TEXT NOT NULL,
    verification  TEXT NOT NULL,   -- JSON
    source        TEXT NOT NULL,
    source_kind   TEXT NOT NULL CHECK (source_kind IN ('curated', 'generated', 'paper')),
    status        TEXT NOT NULL CHECK (status IN ('fresh', 'trusted', 'flagged', 'retired')),
    created_at    TEXT NOT NULL,   -- ISO 8601, UTC
    solve_stats   TEXT NOT NULL,   -- JSON
    reference_hash TEXT            -- fingerprint of the reference the status was earned against
);
CREATE INDEX IF NOT EXISTS idx_questions_pool ON questions (source_kind, status);
CREATE INDEX IF NOT EXISTS idx_questions_node ON questions (node_id);

-- Per-user, per-question spaced-repetition state. user_id is part of the key so multi-user
-- support needs no migration; today there is one local user. All datetimes are UTC (core.timeutil).
CREATE TABLE IF NOT EXISTS review_state (
    user_id           TEXT NOT NULL,
    question_id       TEXT NOT NULL REFERENCES questions (id),
    ease              REAL NOT NULL CHECK (ease >= 1.3),
    interval_days     INTEGER NOT NULL CHECK (interval_days >= 0),
    repetitions       INTEGER NOT NULL CHECK (repetitions >= 0),
    lapses            INTEGER NOT NULL CHECK (lapses >= 0),
    due_at            TEXT NOT NULL,
    last_reviewed_at  TEXT,
    PRIMARY KEY (user_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_review_due ON review_state (user_id, due_at);
"""

# On re-load, YAML owns question content and its reference. The database owns lifecycle data:
# status, verification, created_at and solve_stats. If the prompt, answer, answer type or the
# reference changes, any earlier review no longer applies (trust was earned against the old
# evidence), so status goes back to 'fresh' and verification is cleared (CLAUDE.md rules 2 and 3).
# A reference counts as changed only when both hashes are known and differ: NULL means "not
# recorded yet" (rows from before references lived in YAML, or a caller that passes no hashes)
# and is adopted without a reset.
_CHANGED = """(questions.prompt_md != excluded.prompt_md
          OR questions.answer != excluded.answer
          OR questions.answer_type != excluded.answer_type
          OR (excluded.reference_hash IS NOT NULL
              AND questions.reference_hash IS NOT NULL
              AND questions.reference_hash != excluded.reference_hash))"""
UPSERT_SQL = f"""
INSERT INTO questions (id, topic, node_id, difficulty, format, prompt_md, answer,
                       answer_type, solution_md, verification, source, source_kind, status,
                       created_at, solve_stats, reference_hash)
VALUES (:id, :topic, :node_id, :difficulty, :format, :prompt_md, :answer, :answer_type,
        :solution_md, :verification, :source, :source_kind, :status, :created_at, :solve_stats,
        :reference_hash)
ON CONFLICT(id) DO UPDATE SET
    topic = excluded.topic,
    node_id = excluded.node_id,
    difficulty = excluded.difficulty,
    format = excluded.format,
    solution_md = excluded.solution_md,
    source = excluded.source,
    source_kind = excluded.source_kind,
    status = CASE WHEN {_CHANGED} THEN 'fresh' ELSE questions.status END,
    verification = CASE WHEN {_CHANGED} THEN excluded.verification ELSE questions.verification END,
    prompt_md = excluded.prompt_md,
    answer = excluded.answer,
    answer_type = excluded.answer_type,
    reference_hash = COALESCE(excluded.reference_hash, questions.reference_hash)
"""


BUSY_TIMEOUT_SECONDS = 30.0


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a database created by an older version up to date (idempotent, race-safe)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
    if "reference_hash" not in cols:
        try:
            conn.execute("ALTER TABLE questions ADD COLUMN reference_hash TEXT")
            conn.commit()
        except sqlite3.OperationalError as exc:  # another process added it first
            if "duplicate column" not in str(exc).lower():
                raise


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a connection with row access by name and create or migrate the tables.

    Safe to call from several processes at once: SQLite serialises schema changes, writers wait up
    to ``BUSY_TIMEOUT_SECONDS`` instead of failing, and file databases use WAL so readers are not
    blocked by a writer.
    """
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _to_row(q: Question, reference_hash: str | None = None) -> dict[str, object]:
    return {
        **q.model_dump(mode="python", exclude={"verification", "solve_stats", "created_at"}),
        "format": q.format.value,
        "answer_type": q.answer_type.value,
        "status": q.status.value,
        "verification": q.verification.model_dump_json(),
        "solve_stats": q.solve_stats.model_dump_json(),
        "source_kind": q.source_kind,
        "created_at": to_db(q.created_at),
        "reference_hash": reference_hash,
    }


def question_from_row(row: sqlite3.Row) -> Question:
    """Build a ``Question`` from a row that has every column of the ``questions`` table."""
    data = dict(row)
    data.pop("source_kind")
    data.pop("reference_hash", None)
    data["created_at"] = from_db(data["created_at"])
    return Question.model_validate(
        {
            **data,
            "verification": json.loads(data["verification"]),
            "solve_stats": json.loads(data["solve_stats"]),
        }
    )


def upsert_questions(
    conn: sqlite3.Connection,
    questions: Iterable[Question],
    reference_hashes: Mapping[str, str] | None = None,
) -> None:
    """Insert new questions; update existing ones but keep solve_stats and created_at.

    ``reference_hashes`` maps question id to the fingerprint of its current reference (see
    ``verify.curated.reference_hash``). A different stored fingerprint resets the question to
    ``fresh``; without a hash the stored one is left alone. See the rule above ``UPSERT_SQL``.
    """
    hashes = reference_hashes or {}
    with conn:
        conn.executemany(UPSERT_SQL, [_to_row(q, hashes.get(q.id)) for q in questions])


def get_question(
    conn: sqlite3.Connection, question_id: str, *, include_hidden: bool = False
) -> Question | None:
    """Fetch one question by id, or None.

    Flagged and retired questions are hidden (returned as None) unless ``include_hidden`` is
    set, which only admin and verification code should do.
    """
    sql = "SELECT * FROM questions WHERE id = ?"
    if not include_hidden:
        sql += f" AND status NOT IN {_HIDDEN_SQL}"
    with closing(conn.execute(sql, (question_id,))) as cur:
        row = cur.fetchone()
    return question_from_row(row) if row else None


def list_questions(
    conn: sqlite3.Connection,
    *,
    source_kind: str | None = None,
    status: str | None = None,
    include_hidden: bool = False,
) -> list[Question]:
    """List questions, optionally restricted to one pool (curated/generated/paper) and/or status.

    Flagged and retired questions are excluded unless ``include_hidden`` is set; asking for a
    hidden ``status`` without it is an error rather than a silently empty result.
    """
    if status in HIDDEN_STATUSES and not include_hidden:
        raise ValueError(f"status {status!r} is hidden; pass include_hidden=True (admin use only)")
    clauses, params = [], []
    if not include_hidden:
        clauses.append(f"status NOT IN {_HIDDEN_SQL}")
    if source_kind is not None:
        clauses.append("source_kind = ?")
        params.append(source_kind)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with closing(conn.execute(f"SELECT * FROM questions{where} ORDER BY id", params)) as cur:
        return [question_from_row(r) for r in cur.fetchall()]


def record_attempt(conn: sqlite3.Connection, question_id: str, *, correct: bool) -> None:
    """Increment solve_stats for a question."""
    q = get_question(conn, question_id)
    if q is None:
        raise KeyError(question_id)
    stats = q.solve_stats.model_copy(
        update={
            "attempts": q.solve_stats.attempts + 1,
            "correct": q.solve_stats.correct + int(correct),
        }
    )
    with conn:
        conn.execute(
            "UPDATE questions SET solve_stats = ? WHERE id = ?",
            (stats.model_dump_json(), question_id),
        )


def set_verification(
    conn: sqlite3.Connection,
    question_id: str,
    verification: Verification,
    status: Status,
) -> None:
    """Store a verification record and move the question to ``status``.

    The pair is validated through the ``Question`` model first, so a numeric/symbolic/code
    question cannot become ``trusted`` without a passing record (CLAUDE.md rules 2 and 3).
    solve_stats and created_at are untouched. Works on hidden questions too.
    """
    q = get_question(conn, question_id, include_hidden=True)
    if q is None:
        raise KeyError(question_id)
    checked = Question.model_validate(
        {**q.model_dump(), "verification": verification.model_dump(), "status": status}
    )
    with conn:
        conn.execute(
            "UPDATE questions SET status = ?, verification = ? WHERE id = ?",
            (checked.status.value, checked.verification.model_dump_json(), question_id),
        )
