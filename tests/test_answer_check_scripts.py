"""The scripts/ answer checks must agree with the curated YAML, and must reject wrong answers."""

import pytest

from core.schema import AnswerType, Question
from scripts.check_answers import EXACT, MONTE_CARLO, exact_agrees, mc_agrees


def test_every_non_text_question_has_a_check(curated: list[Question]) -> None:
    missing = [q.id for q in curated if q.answer_type is not AnswerType.TEXT and q.id not in EXACT]
    assert missing == []


def test_curated_answers_match_independent_computation(curated: list[Question]) -> None:
    for q in curated:
        if q.answer_type is not AnswerType.TEXT:
            assert exact_agrees(q.answer, q.answer_type, EXACT[q.id]()), q.id


def test_probability_answers_match_monte_carlo(curated: list[Question]) -> None:
    by_id = {q.id: q for q in curated}
    for qid, run in MONTE_CARLO.items():
        est, se = run()
        assert mc_agrees(by_id[qid].answer, est, se), qid


@pytest.mark.parametrize(
    "qid,wrong,kind",
    [
        ("prob-001", "1/8", AnswerType.NUMERIC),
        ("prob-006", "0.95", AnswerType.NUMERIC),
        ("stat-003", "1/lam**2", AnswerType.SYMBOLIC),
        ("linalg-005", "a*d + b*c", AnswerType.SYMBOLIC),
        ("linalg-006", "7", AnswerType.NUMERIC),
    ],
)
def test_wrong_answers_are_rejected(qid: str, wrong: str, kind: AnswerType) -> None:
    assert not exact_agrees(wrong, kind, EXACT[qid]())


def test_monte_carlo_rejects_wrong_answer() -> None:
    est, se = MONTE_CARLO["prob-001"]()
    assert not mc_agrees("1/8", est, se)
