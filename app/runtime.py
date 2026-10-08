"""Process-wide helpers for the pages: settings, the clock, and database access.

Pages call ``runtime.utc_now()`` (the server's clock, never the browser's) and
``runtime.open_db(...)``. Tests replace ``utc_now`` to control time.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from core import db
from core.config import Settings, load_settings
from core.loader import DEFAULT_DIR


def get_settings() -> Settings:
    """Settings from the environment (cheap, so it is re-read on every script run)."""
    return load_settings()


def data_dir(settings: Settings) -> Path:
    """The curated YAML directory to load."""
    return Path(settings.data_dir) if settings.data_dir else DEFAULT_DIR


def utc_now() -> datetime:
    """The current time, timezone-aware UTC. The only place pages read the clock."""
    return datetime.now(UTC)


@contextmanager
def open_db(settings: Settings) -> Iterator[sqlite3.Connection]:
    """A connection for one script run, closed afterwards."""
    conn = db.connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()
