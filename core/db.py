"""SQLite storage for questions (plain sqlite3)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime
from pathlib import Path

from core.schema import Question

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
    solve_stats   TEXT NOT NULL    -- JSON
);
CREATE INDEX IF NOT EXISTS idx_questions_pool ON questions (source_kind, status);
CREATE INDEX IF NOT EXISTS idx_questions_node ON questions (node_id);
"""

# On re-load, YAML owns question content only. The database owns lifecycle data: status,
# verification, created_at and solve_stats. If the prompt, answer or answer type changes, any
# earlier review no longer applies, so status goes back to 'fresh' and verification is cleared
# (CLAUDE.md rules 2 and 3).
_CHANGED = """(questions.prompt_md != excluded.prompt_md
          OR questions.answer != excluded.answer
          OR questions.answer_type != excluded.answer_type)"""
UPSERT_SQL = f"""
INSERT INTO questions (id, topic, node_id, difficulty, format, prompt_md, answer,
                       answer_type, solution_md, verification, source, source_kind, status,
                       created_at, solve_stats)
VALUES (:id, :topic, :node_id, :difficulty, :format, :prompt_md, :answer, :answer_type,
        :solution_md, :verification, :source, :source_kind, :status, :created_at, :solve_stats)
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
    answer_type = excluded.answer_type
"""


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a connection with row access by name and create the tables if missing."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _to_row(q: Question) -> dict[str, object]:
    return {
        **q.model_dump(mode="python", exclude={"verification", "solve_stats", "created_at"}),
        "format": q.format.value,
        "answer_type": q.answer_type.value,
        "status": q.status.value,
        "verification": q.verification.model_dump_json(),
        "solve_stats": q.solve_stats.model_dump_json(),
        "source_kind": q.source_kind,
        "created_at": q.created_at.isoformat(),
    }


def _from_row(row: sqlite3.Row) -> Question:
    data = dict(row)
    data.pop("source_kind")
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    return Question.model_validate(
        {
            **data,
            "verification": json.loads(data["verification"]),
            "solve_stats": json.loads(data["solve_stats"]),
        }
    )


def upsert_questions(conn: sqlite3.Connection, questions: Iterable[Question]) -> None:
    """Insert new questions; update existing ones but keep solve_stats and created_at."""
    with conn:
        conn.executemany(UPSERT_SQL, [_to_row(q) for q in questions])


def get_question(conn: sqlite3.Connection, question_id: str) -> Question | None:
    """Fetch one question by id, or None."""
    with closing(conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,))) as cur:
        row = cur.fetchone()
    return _from_row(row) if row else None


def list_questions(
    conn: sqlite3.Connection,
    *,
    source_kind: str | None = None,
    status: str | None = None,
) -> list[Question]:
    """List questions, optionally restricted to one pool (curated/generated/paper) and/or status."""
    clauses, params = [], []
    if source_kind is not None:
        clauses.append("source_kind = ?")
        params.append(source_kind)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with closing(conn.execute(f"SELECT * FROM questions{where} ORDER BY id", params)) as cur:
        return [_from_row(r) for r in cur.fetchall()]


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
