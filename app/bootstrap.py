"""Prepare the database: load the curated YAML, then verify and promote what can be.

Safe to run from several processes at once (SQLite serialises the writes and every step is
idempotent), so two app starts, or an app start during ``python -m verify.promote``, cannot
corrupt anything. The app calls it once per process through ``st.cache_resource``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from core import db
from core.loader import DEFAULT_DIR, LoadError
from verify.curated import hashes_by_id, load_curated, references_by_id
from verify.promote import promote_curated

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapReport:
    """What bootstrap did. ``error`` is set if the curated files could not be loaded."""

    promoted: int = 0
    flagged: int = 0
    unverified: int = 0
    error: str | None = None


def bootstrap(db_path: str, data_dir: Path = DEFAULT_DIR) -> BootstrapReport:
    """Load, upsert and promote. Never shows flagged questions: they are only logged here."""
    try:
        entries = load_curated(data_dir)
    except LoadError as exc:
        log.error("cannot load curated questions: %s", exc)
        return BootstrapReport(error=str(exc))
    conn = db.connect(db_path)
    try:
        db.upsert_questions(conn, [e.question for e in entries], hashes_by_id(entries))
        report = promote_curated(conn, references_by_id(entries))
    finally:
        conn.close()
    for item in report.flagged:
        log.warning(
            "question %s failed verification and is hidden: %s", item.question_id, item.details
        )
    return BootstrapReport(
        promoted=len(report.promoted),
        flagged=len(report.flagged),
        unverified=len(report.unverified),
    )
