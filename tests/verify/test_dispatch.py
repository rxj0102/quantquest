import pytest

from core.schema import AnswerType, Question, Status
from tests.conftest import make_question
from tests.pool import YAML_REFERENCES
from verify.code_runner import CodeSpec
from verify.dispatch import verify_question
from verify.pricing import PricingSpec
from verify.references import ExactSpec, Reference, SimulatorSpec

DICE = SimulatorSpec(name="dice_sum_equals", params={"n_dice": 2, "sides": 6, "target": 9})


def q(**kw: object) -> Question:
    return Question.model_validate(make_question(**kw))


@pytest.fixture(scope="module")
def curated_results(curated_module: list[Question]):
    return {x.id: verify_question(x, YAML_REFERENCES.get(x.id)) for x in curated_module}


@pytest.fixture(scope="module")
def curated_module() -> list[Question]:
    from core.loader import load_questions

    return load_questions()


def test_every_curated_question_with_a_reference_passes(curated_module, curated_results) -> None:
    for x in curated_module:
        v = curated_results[x.id]
        if x.answer_type is AnswerType.TEXT:
            assert v.result == "unverified", x.id
        else:
            assert v.result == "pass", (x.id, v.details)


def test_probability_questions_use_both_exact_and_monte_carlo(
    curated_module, curated_results
) -> None:
    for x in curated_module:
        v = curated_results[x.id]
        if x.format.value == "probability":
            assert "monte_carlo" in v.method and "sympy" in v.method, x.id
            assert "stderr" in v.details and "seed" in v.details, x.id
            assert "1000000" in v.details.replace(",", "").replace("_", "") or "1e6" in v.details


def test_non_probability_questions_use_exact_only(curated_results) -> None:
    assert "monte_carlo" not in curated_results["prob-003"].method
    assert "sympy" in curated_results["linalg-001"].method


def test_text_answers_are_labelled_unverified(curated_results) -> None:
    v = curated_results["stat-005"]
    assert v.result == "unverified" and v.method == "none"


def test_no_reference_means_unverified() -> None:
    v = verify_question(q(), None)
    assert v.result == "unverified" and "reference" in v.details


def test_case_format_is_never_auto_verified() -> None:
    v = verify_question(
        q(format="case", answer_type="text", answer="rubric"), Reference(exact=ExactSpec(expr="1"))
    )
    assert v.result == "unverified"


# --- wrong answers must fail ----------------------------------------------------------------


def _mutations(x: Question) -> list[str]:
    if x.answer_type is AnswerType.SYMBOLIC:
        return [f"({x.answer}) + 1", f"({x.answer}) * (1 + 1/1000)", f"-({x.answer})"]
    return [f"({x.answer}) + 1", f"({x.answer}) * (1 + 1/1000)", f"({x.answer}) * (1 - 1/10**6)"]


def test_every_curated_answer_fails_when_perturbed(curated_module) -> None:
    n = 0
    for x in curated_module:
        if x.answer_type is AnswerType.TEXT:
            continue
        for wrong in _mutations(x):
            v = verify_question(x.model_copy(update={"answer": wrong}), YAML_REFERENCES[x.id])
            assert v.result == "fail", (x.id, wrong, v.details)
            n += 1
    assert n >= 54


@pytest.mark.parametrize("wrong", ["0.1111", "1/8", "0.111112", "1/9 + 1/10**7"])
def test_near_miss_probability_answers_fail(wrong: str) -> None:
    ref = Reference(exact=ExactSpec(expr="1/9"), simulator=DICE)
    v = verify_question(q(answer=wrong), ref)
    assert v.result == "fail"
    assert "monte_carlo" not in v.method  # no 1e6-trial run once the exact check has failed


def test_monte_carlo_can_fail_when_exact_passes() -> None:
    wrong_sim = SimulatorSpec(name="dice_sum_equals", params={"n_dice": 2, "sides": 6, "target": 8})
    v = verify_question(
        q(answer="1/9"), Reference(exact=ExactSpec(expr="1/9"), simulator=wrong_sim)
    )
    assert v.result == "fail" and "monte_carlo" in v.method


def test_probability_without_simulator_cannot_be_certified() -> None:
    v = verify_question(q(answer="1/9"), Reference(exact=ExactSpec(expr="1/9")))
    assert v.result == "unverified" and "simulator" in v.details


def test_numeric_without_exact_reference_is_unverified() -> None:
    v = verify_question(q(format="mental_math"), Reference(simulator=DICE))
    assert v.result == "unverified"


def test_errors_are_unverified_not_fail() -> None:
    bad_ref = Reference(exact=ExactSpec(expr="__import__('os')"))
    v = verify_question(q(format="mental_math"), bad_ref)
    assert v.result == "unverified"
    bad_claim = q(format="mental_math", answer="__import__('os')")
    assert verify_question(bad_claim, Reference(exact=ExactSpec(expr="1"))).result == "unverified"


def test_fail_beats_error_when_both_occur() -> None:
    ref = Reference(exact=ExactSpec(expr="1/9"), simulator=SimulatorSpec(name="nope", params={}))
    v = verify_question(q(answer="1/8"), ref)
    assert v.result == "fail"


# --- pricing and code -----------------------------------------------------------------------

CALL = PricingSpec(S=100, K=100, r=0.05, sigma=0.2, T=1, kind="call")


def test_pricing_route() -> None:
    ok = verify_question(q(format="mental_math", answer="10.4506"), Reference(price=CALL))
    assert ok.result == "pass" and "pricing" in ok.method
    bad = verify_question(q(format="mental_math", answer="10.46"), Reference(price=CALL))
    assert bad.result == "fail"


SOL = "def add(a, b):\n    return a + b\n"
CODE = CodeSpec(entrypoint="add", cases=[([1, 2], 3), ([-1, 1], 0), ([10, 5], 15)])


def test_code_route() -> None:
    ok = verify_question(q(format="coding", answer_type="code", answer=SOL), Reference(code=CODE))
    assert ok.result == "pass" and "code_runner" in ok.method
    bad = verify_question(
        q(format="coding", answer_type="code", answer=SOL.replace("+", "-")), Reference(code=CODE)
    )
    assert bad.result == "fail"
    assert (
        verify_question(q(format="coding", answer_type="code", answer=SOL), None).result
        == "unverified"
    )


def test_verification_record_is_schema_valid_for_trust() -> None:
    v = verify_question(q(format="mental_math", answer="10.4506"), Reference(price=CALL))
    trusted = q(
        format="mental_math", answer="10.4506", status="trusted", verification=v.model_dump()
    )
    assert trusted.status is Status.TRUSTED
