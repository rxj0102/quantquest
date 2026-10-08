"""Reference is data (JSON-serializable); every YAML reference must match the scripts/ fixtures."""

import pytest

from core.schema import AnswerType, Question
from scripts.check_answers import EXACT as SCRIPT_EXACT
from scripts.check_answers import exact_agrees
from tests.pool import YAML_REFERENCES
from verify.code_runner import CodeSpec
from verify.pricing import PricingSpec
from verify.references import ExactSpec, Reference, SimulatorSpec
from verify.sympy_check import check_numeric, check_symbolic


def test_every_curated_numeric_or_symbolic_question_has_a_yaml_reference(
    curated: list[Question],
) -> None:
    """The YAML entry is the only source of references, so none may be missing or extra."""
    ids = {q.id for q in curated if q.answer_type is not AnswerType.TEXT}
    assert set(YAML_REFERENCES) == ids
    assert all(ref.exact is not None for ref in YAML_REFERENCES.values())
    assert "stat-005" not in YAML_REFERENCES  # text answers have no reference


def test_every_reference_round_trips_through_json() -> None:
    for qid, ref in YAML_REFERENCES.items():
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


def test_each_yaml_reference_matches_its_independent_script_value(curated: list[Question]) -> None:
    """Two separately written computations of every answer: the YAML reference and scripts/."""
    for q in curated:
        if q.answer_type is AnswerType.TEXT:
            continue
        ref = YAML_REFERENCES[q.id]
        assert ref.exact is not None, q.id
        script_value = SCRIPT_EXACT[q.id]()
        assert exact_agrees(q.answer, q.answer_type, script_value), q.id  # fixture sanity
        checker = check_symbolic if q.answer_type is AnswerType.SYMBOLIC else check_numeric
        assert checker(q.answer, ref.exact).passed, q.id
        # the script's value, not just the stated answer, must match the YAML reference
        res = checker(str(script_value), ref.exact)
        assert res.passed, (q.id, res.details)


def test_probability_format_questions_all_have_simulators(curated: list[Question]) -> None:
    for q in curated:
        if q.format.value == "probability":
            assert YAML_REFERENCES[q.id].simulator is not None, q.id
