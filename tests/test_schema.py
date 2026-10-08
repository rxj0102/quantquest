import pytest
from pydantic import ValidationError

from core.schema import Question, SolveStats, Status
from tests.conftest import make_question


def test_valid_question_defaults() -> None:
    q = Question.model_validate(make_question())
    assert q.status is Status.FRESH
    assert q.verification.result == "unverified"
    assert q.solve_stats == SolveStats(attempts=0, correct=0)
    assert q.created_at.tzinfo is not None
    assert q.source_kind == "curated"


@pytest.mark.parametrize("good", ["prob-001", "linalg-012", "stat-1000", "ab-123"])
def test_id_format_accepts(good: str) -> None:
    assert Question.model_validate(make_question(id=good)).id == good


@pytest.mark.parametrize(
    "bad", ["prob001", "prob-1", "Prob-001", "prob_001", "prob-001a", "1-001", "-001", "", "p-001"]
)
def test_id_format_rejects(bad: str) -> None:
    with pytest.raises(ValidationError, match="id"):
        Question.model_validate(make_question(id=bad))


@pytest.mark.parametrize("d", [0, 6, -1])
def test_difficulty_out_of_range(d: int) -> None:
    with pytest.raises(ValidationError):
        Question.model_validate(make_question(difficulty=d))


@pytest.mark.parametrize(
    "field,value", [("format", "riddle"), ("answer_type", "vibes"), ("status", "ok")]
)
def test_enums_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Question.model_validate(make_question(**{field: value}))


@pytest.mark.parametrize("src", ["curated", "generated", "paper:https://arxiv.org/abs/1234.5678"])
def test_source_accepts(src: str) -> None:
    assert Question.model_validate(make_question(source=src)).source == src


@pytest.mark.parametrize("src", ["", "paper:", "paper:not-a-url", "ai", "Curated"])
def test_source_rejects(src: str) -> None:
    with pytest.raises(ValidationError, match="source"):
        Question.model_validate(make_question(source=src))


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        Question.model_validate(make_question(bogus=1))


def test_solve_stats_correct_cannot_exceed_attempts() -> None:
    with pytest.raises(ValidationError):
        SolveStats(attempts=1, correct=2)


def test_trusted_numeric_requires_passing_verification() -> None:
    with pytest.raises(ValidationError, match="trusted"):
        Question.model_validate(make_question(status="trusted"))
    with pytest.raises(ValidationError, match="trusted"):
        Question.model_validate(
            make_question(status="trusted", verification={"method": "mc", "result": "fail"})
        )
    ok = Question.model_validate(
        make_question(status="trusted", verification={"method": "mc", "result": "pass"})
    )
    assert ok.status is Status.TRUSTED


def test_trusted_text_does_not_need_code_verification() -> None:
    q = Question.model_validate(make_question(status="trusted", answer_type="text", format="case"))
    assert q.status is Status.TRUSTED
