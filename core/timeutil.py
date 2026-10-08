"""UTC handling for everything stored in the database.

Every datetime that is persisted is timezone-aware UTC and written in one fixed-width format
(``YYYY-MM-DDTHH:MM:SS.ffffffZ``), so SQL string comparison and ordering equal time order.
Naive datetimes are rejected rather than guessed at. Code never reads the system clock here:
callers pass ``now`` in, which keeps tests independent of real time.
"""

from __future__ import annotations

from datetime import UTC, datetime

DB_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def to_utc(dt: datetime) -> datetime:
    """Return ``dt`` converted to UTC. Raises ValueError for naive datetimes."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware (got a naive datetime)")
    return dt.astimezone(UTC)


def to_db(dt: datetime) -> str:
    """Canonical storage string for ``dt`` (UTC, fixed width, sortable)."""
    return to_utc(dt).strftime(DB_FORMAT)


def from_db(text: str) -> datetime:
    """Parse a stored string (new ``...Z`` format or legacy ``+00:00``) into aware UTC."""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"stored datetime {text!r} has no timezone")
    return parsed.astimezone(UTC)
