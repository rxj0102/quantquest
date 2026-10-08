from pathlib import Path

import pytest

from core.loader import LoadError, load_file, load_questions
from core.schema import AnswerType, Status
from tests.conftest import make_question
from tests.pool import N_CURATED


def test_loads_every_curated_question(curated) -> None:
    assert len(curated) == N_CURATED


def test_ids_unique_and_topics_covered(curated) -> None:
    assert len({q.id for q in curated}) == N_CURATED
    counts = {t: sum(q.topic == t for q in curated) for t in {q.topic for q in curated}}
    assert set(counts) == {"probability", "statistics", "linear_algebra"}
    assert all(n >= 6 for n in counts.values()) and sum(counts.values()) == N_CURATED


def test_curated_are_fresh_until_m2(curated) -> None:
    assert all(q.status is Status.FRESH for q in curated)
    assert all(q.verification.result == "unverified" for q in curated)
    assert all(q.source == "curated" for q in curated)


def test_sample_is_varied(curated) -> None:
    assert {q.answer_type for q in curated} == {
        AnswerType.NUMERIC,
        AnswerType.SYMBOLIC,
        AnswerType.TEXT,
    }
    assert len({q.difficulty for q in curated}) >= 4


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_invalid_question_error_names_file_and_id(tmp_path: Path) -> None:
    import yaml

    bad = make_question(difficulty=9)
    f = _write(tmp_path / "bad.yaml", yaml.safe_dump([bad]))
    with pytest.raises(LoadError, match=r"bad\.yaml.*prob-999"):
        load_file(f)


def test_duplicate_ids_across_files_rejected(tmp_path: Path) -> None:
    import yaml

    text = yaml.safe_dump([make_question()])
    _write(tmp_path / "a.yaml", text)
    _write(tmp_path / "b.yaml", text)
    with pytest.raises(LoadError, match="duplicate id prob-999"):
        load_questions(tmp_path)


def test_non_list_and_bad_yaml_rejected(tmp_path: Path) -> None:
    with pytest.raises(LoadError, match="list"):
        load_file(_write(tmp_path / "x.yaml", "a: 1\n"))
    with pytest.raises(LoadError, match="invalid YAML"):
        load_file(_write(tmp_path / "y.yaml", "- [unclosed\n"))


def test_generated_source_rejected_in_curated_dir(tmp_path: Path) -> None:
    import yaml

    _write(tmp_path / "g.yaml", yaml.safe_dump([make_question(source="generated")]))
    with pytest.raises(LoadError, match="curated"):
        load_questions(tmp_path)
