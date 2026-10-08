"""Promote verified curated questions from fresh to trusted.

``python -m verify.promote --db quantquest.db`` loads ``data/curated`` into the database, verifies
every curated question that is still ``fresh``, and then:

* ``pass``         -> ``trusted`` (verification record stored)
* ``fail``         -> ``flagged`` (record stored, hidden from every user-facing query)
* ``unverified``   -> stays ``fresh`` (no reference, text/case answer, or the check could not run)

A failure report is printed, and the exit code is 1 if anything was flagged.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from core import db
from core.loader import DEFAULT_DIR
from core.schema import Status
from verify.curated import hashes_by_id, load_curated, references_by_id
from verify.dispatch import verify_question
from verify.references import Reference


@dataclass(frozen=True)
class Outcome:
    """What happened to one question."""

    question_id: str
    answer: str
    method: str
    details: str


@dataclass
class PromotionReport:
    """Result of a promotion run."""

    promoted: list[Outcome] = field(default_factory=list)
    flagged: list[Outcome] = field(default_factory=list)
    unverified: list[Outcome] = field(default_factory=list)

    def format(self) -> str:
        """Human-readable report; flagged questions are listed with the failing evidence."""
        lines = [
            f"Promotion report: promoted {len(self.promoted)}, flagged {len(self.flagged)}, "
            f"left fresh/unverified {len(self.unverified)}"
        ]
        if self.unverified:
            lines.append("UNVERIFIED (left fresh):")
            lines += [f"  {o.question_id}: {o.details}" for o in self.unverified]
        if self.flagged:
            lines.append("FLAGGED (failed verification; hidden from users):")
            for o in self.flagged:
                lines += [
                    f"  {o.question_id}  claimed answer: {o.answer}",
                    f"    method:  {o.method}",
                    f"    details: {o.details}",
                ]
        return "\n".join(lines)


def promote_curated(
    conn: sqlite3.Connection, references: Mapping[str, Reference]
) -> PromotionReport:
    """Verify every fresh curated question and update its status. Idempotent."""
    report = PromotionReport()
    for q in db.list_questions(conn, source_kind="curated", status=Status.FRESH.value):
        verification = verify_question(q, references.get(q.id))
        outcome = Outcome(q.id, q.answer, verification.method, verification.details)
        if verification.result == "pass":
            db.set_verification(conn, q.id, verification, Status.TRUSTED)
            report.promoted.append(outcome)
        elif verification.result == "fail":
            db.set_verification(conn, q.id, verification, Status.FLAGGED)
            report.flagged.append(outcome)
        else:
            db.set_verification(conn, q.id, verification, Status.FRESH)
            report.unverified.append(outcome)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 1 if any question was flagged, else 0."""
    parser = argparse.ArgumentParser(description="Verify and promote curated questions")
    parser.add_argument("--db", default="quantquest.db", help="SQLite database path")
    parser.add_argument("--data", default=str(DEFAULT_DIR), help="directory of curated YAML files")
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    try:
        entries = load_curated(Path(args.data))
        db.upsert_questions(conn, [e.question for e in entries], hashes_by_id(entries))
        report = promote_curated(conn, references_by_id(entries))
    finally:
        conn.close()
    print(report.format())
    return 1 if report.flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
