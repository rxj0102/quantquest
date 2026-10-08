"""Reference is data (JSON-serializable); the curated registry must match the scripts/ fixtures."""

import pytest

from core.schema import AnswerType, Question
from scripts.check_answers import EXACT as SCRIPT_EXACT
from scripts.check_answers import exact_agrees
from verify.code_runner import CodeSpec
from verify.pricing import PricingSpec
from verify.references import CURATED_REFERENCES, ExactSpec, Reference, SimulatorSpec
from verify.sympy_check import check_numeric, check_symbolic


def test_registry_covers_every_non_text_curated_question(curated: list[Question]) -> None:
    ids = {q.id for q in curated if q.answer_type is not AnswerType.TEXT}
    assert set(CURATED_REFERENCES) == ids
    assert "stat-005" not in CURATED_REFERENCES


def test_every_reference_round_trips_through_json() -> None:
    for qid, ref in CURATED_REFERENCES.items():
        again = Reference.model_validate_json(ref.model_dump_json())
        assert again == ref, qid


def test_reference_with_every_field_round_trips() -> None:
    ref = Reference(
        exact=ExactSpec(expr="Integral(x, (x, 0, 1))", symbols={"x": "positive"}),
        simulator=SimulatorSpec(name="coin_heads_at_least", params={"n": 4, "k": 2}),
        price=PricingSpec(S=100, K=100, r=0.05, sigma=0.2, T=1, kind="call"),
        code=CodeSpec(entrypoint="f", cases=[([1, 2], 3)]),
        rel_tol=1e-6,
    )
    assert Reference.model_validate_json(ref.model_dump_json()) == ref
    assert Reference.model_validate(ref.model_dump(mode="json")) == ref


def test_reference_rejects_unknown_fields_and_bad_values() -> None:
    with pytest.raises(ValueError):
        Reference.model_validate({"exact": {"expr": "1"}, "evil": 1})
    with pytest.raises(ValueError):
        ExactSpec(expr="1", symbols={"x": "weird"})
    with pytest.raises(ValueError):
        Reference(rel_tol=-1)


def test_registry_values_equal_the_independent_script_fixtures(curated: list[Question]) -> None:
    """Two separately written computations of every answer: the registry and scripts/."""
    for q in curated:
        if q.answer_type is AnswerType.TEXT:
            continue
        ref = CURATED_REFERENCES[q.id]
        assert ref.exact is not None, q.id
        assert exact_agrees(q.answer, q.answer_type, SCRIPT_EXACT[q.id]()), q.id  # fixture sanity
        checker = check_symbolic if q.answer_type is AnswerType.SYMBOLIC else check_numeric
        res = checker(q.answer, ref.exact)
        assert res.passed, (q.id, res.details)
        # The registry's own value must also match the script's value, not just the YAML answer.
        script_value = ExactSpec(expr=str(SCRIPT_EXACT[q.id]()))
        assert checker(str(SCRIPT_EXACT[q.id]()), ref.exact).passed, q.id
        assert script_value.expr


def test_probability_format_questions_all_have_simulators(curated: list[Question]) -> None:
    for q in curated:
        if q.format.value == "probability":
            assert CURATED_REFERENCES[q.id].simulator is not None, q.id
