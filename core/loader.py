"""Load curated questions from YAML files under data/.

An entry may carry an optional ``reference:`` block (the evidence used to verify it). This module
only separates it from the question fields and returns it as a raw mapping; ``verify.curated``
turns it into a ``Reference``, so ``core`` does not depend on ``verify``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from core.schema import Question

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "curated"


class LoadError(ValueError):
    """A curated question file is malformed. The message names the file and question."""


@dataclass(frozen=True)
class Entry:
    """One YAML entry: the question, its raw ``reference`` mapping (or None), and its file."""

    question: Question
    reference: dict[str, Any] | None
    path: Path


def load_file_entries(path: Path) -> list[Entry]:
    """Parse one YAML file containing a list of questions."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LoadError(f"{path}: invalid YAML: {exc}") from exc
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LoadError(f"{path}: top level must be a list of questions")
    entries = []
    for i, item in enumerate(raw):
        label = item.get("id", f"#{i}") if isinstance(item, dict) else f"#{i}"
        reference = None
        if isinstance(item, dict) and "reference" in item:
            item = dict(item)
            reference = item.pop("reference")
            if reference is not None and not isinstance(reference, dict):
                raise LoadError(f"{path}: question {label}: reference must be a mapping")
        try:
            entries.append(Entry(Question.model_validate(item), reference, path))
        except ValidationError as exc:
            raise LoadError(f"{path}: question {label}: {exc}") from exc
    return entries


def load_file(path: Path) -> list[Question]:
    """Parse one YAML file and return just its questions."""
    return [e.question for e in load_file_entries(path)]


def load_entries(directory: Path = DEFAULT_DIR) -> list[Entry]:
    """Load every ``*.yaml`` under ``directory``. Raises on duplicate ids or non-curated source."""
    entries: list[Entry] = []
    seen: dict[str, Path] = {}
    for path in sorted(directory.rglob("*.yaml")):
        for entry in load_file_entries(path):
            q = entry.question
            if q.source_kind != "curated":
                raise LoadError(
                    f"{path}: question {q.id}: source must be 'curated' in data/curated"
                )
            if q.id in seen:
                raise LoadError(f"{path}: duplicate id {q.id} (also in {seen[q.id]})")
            seen[q.id] = path
            entries.append(entry)
    return entries


def load_questions(directory: Path = DEFAULT_DIR) -> list[Question]:
    """Load every curated question (references are ignored here)."""
    return [e.question for e in load_entries(directory)]
