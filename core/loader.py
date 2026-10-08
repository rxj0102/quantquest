"""Load curated questions from YAML files under data/."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from core.schema import Question

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "curated"


class LoadError(ValueError):
    """A curated question file is malformed. The message names the file and question."""


def load_file(path: Path) -> list[Question]:
    """Parse one YAML file containing a list of questions."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LoadError(f"{path}: invalid YAML: {exc}") from exc
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LoadError(f"{path}: top level must be a list of questions")
    questions = []
    for i, item in enumerate(raw):
        label = item.get("id", f"#{i}") if isinstance(item, dict) else f"#{i}"
        try:
            questions.append(Question.model_validate(item))
        except ValidationError as exc:
            raise LoadError(f"{path}: question {label}: {exc}") from exc
    return questions


def load_questions(directory: Path = DEFAULT_DIR) -> list[Question]:
    """Load every ``*.yaml`` under ``directory``. Raises on duplicate ids or non-curated source."""
    questions: list[Question] = []
    seen: dict[str, Path] = {}
    for path in sorted(directory.rglob("*.yaml")):
        for q in load_file(path):
            if q.source_kind != "curated":
                raise LoadError(
                    f"{path}: question {q.id}: source must be 'curated' in data/curated"
                )
            if q.id in seen:
                raise LoadError(f"{path}: duplicate id {q.id} (also in {seen[q.id]})")
            seen[q.id] = path
            questions.append(q)
    return questions
