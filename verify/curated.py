"""Curated questions together with their references.

A reference normally lives in the question's own YAML entry (``reference:``), so adding a question
is a single-file edit. ``CURATED_REFERENCES`` in ``verify.references`` remains as a fallback for
entries without one; if both exist they must agree, so the two can never silently diverge.

``reference_hash`` fingerprints the *parsed* reference (key order, whitespace and explicit
defaults do not matter). The database stores it next to each question, and a change to it
invalidates the question's trust: see ``core.db.upsert_questions``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from core.loader import DEFAULT_DIR, Entry, LoadError, load_entries, load_file_entries
from core.schema import Question
from verify.references import CURATED_REFERENCES, Reference

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
    """A curated question, its reference, where the reference came from, and its hash."""

    question: Question
    reference: Reference | None
    source: str  # "yaml" | "registry" | "none"
    reference_hash: str


def _convert(entry: Entry, registry: Mapping[str, Reference]) -> CuratedEntry:
    """Turn a raw YAML entry into a ``CuratedEntry`` (YAML reference first, registry fallback)."""
    qid = entry.question.id
    reference: Reference | None = None
    source = "none"
    if entry.reference is not None:
        try:
            reference = Reference.model_validate(entry.reference)
        except ValidationError as exc:
            raise LoadError(f"{entry.path}: question {qid}: invalid reference: {exc}") from exc
        source = "yaml"
        fallback = registry.get(qid)
        if fallback is not None and reference_hash(fallback) != reference_hash(reference):
            raise LoadError(
                f"{entry.path}: question {qid}: reference in YAML differs from the registry "
                "entry in verify.references; remove one or make them equal"
            )
    elif qid in registry:
        reference, source = registry[qid], "registry"
    return CuratedEntry(entry.question, reference, source, reference_hash(reference))


def load_curated(
    directory: Path = DEFAULT_DIR,
    registry: Mapping[str, Reference] | None = CURATED_REFERENCES,
) -> list[CuratedEntry]:
    """Load curated entries, taking each reference from its YAML block, else from ``registry``.

    Raises ``LoadError`` (naming the file and question) for an invalid reference, or when the YAML
    and the registry both define a reference for a question and they differ.
    """
    registry = registry or {}
    return [_convert(entry, registry) for entry in load_entries(directory)]


def load_curated_file(
    path: Path, registry: Mapping[str, Reference] | None = None
) -> list[CuratedEntry]:
    """Load one YAML file the same way (used for drafts; no registry fallback by default)."""
    return [_convert(entry, registry or {}) for entry in load_file_entries(path)]


def references_by_id(entries: list[CuratedEntry]) -> dict[str, Reference]:
    """The ``{question_id: Reference}`` mapping ``promote_curated`` expects."""
    return {e.question.id: e.reference for e in entries if e.reference is not None}


def hashes_by_id(entries: list[CuratedEntry]) -> dict[str, str]:
    """The ``{question_id: reference_hash}`` mapping ``upsert_questions`` expects."""
    return {e.question.id: e.reference_hash for e in entries}
