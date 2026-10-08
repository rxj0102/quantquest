"""Curated questions together with their references.

A reference lives in the question's own YAML entry (``reference:``), so adding a question is a
single-file edit. The YAML is the only source: there is no separate registry to keep in step.

``reference_hash`` fingerprints the *parsed* reference (key order, whitespace and explicit
defaults do not matter). The database stores it next to each question, and a change to it
invalidates the question's trust: see ``core.db.upsert_questions``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from core.loader import DEFAULT_DIR, Entry, LoadError, load_entries, load_file_entries
from core.schema import Question
from verify.references import Reference

NO_REFERENCE_HASH = "none"  # stored for questions that have no reference at all


def reference_hash(reference: Reference | None) -> str:
    """Stable fingerprint of a reference, or ``"none"`` when there is none."""
    if reference is None:
        return NO_REFERENCE_HASH
    canonical = json.dumps(
        reference.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class CuratedEntry:
    """A curated question, its reference, whether it has one, and the reference's hash."""

    question: Question
    reference: Reference | None
    source: str  # "yaml" (has a reference) | "none"
    reference_hash: str


def _convert(entry: Entry) -> CuratedEntry:
    """Turn a raw YAML entry into a ``CuratedEntry``."""
    reference: Reference | None = None
    if entry.reference is not None:
        try:
            reference = Reference.model_validate(entry.reference)
        except ValidationError as exc:
            raise LoadError(
                f"{entry.path}: question {entry.question.id}: invalid reference: {exc}"
            ) from exc
    return CuratedEntry(
        entry.question, reference, "yaml" if reference else "none", reference_hash(reference)
    )


def load_curated(directory: Path = DEFAULT_DIR) -> list[CuratedEntry]:
    """Load curated entries, each with the reference from its YAML block.

    Raises ``LoadError`` (naming the file and question) for an invalid reference.
    """
    return [_convert(entry) for entry in load_entries(directory)]


def load_curated_file(path: Path) -> list[CuratedEntry]:
    """Load one YAML file the same way (used for drafts)."""
    return [_convert(entry) for entry in load_file_entries(path)]


def references_by_id(entries: list[CuratedEntry]) -> dict[str, Reference]:
    """The ``{question_id: Reference}`` mapping ``promote_curated`` expects."""
    return {e.question.id: e.reference for e in entries if e.reference is not None}


def hashes_by_id(entries: list[CuratedEntry]) -> dict[str, str]:
    """The ``{question_id: reference_hash}`` mapping ``upsert_questions`` expects."""
    return {e.question.id: e.reference_hash for e in entries}
