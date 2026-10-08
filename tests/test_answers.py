"""Typed-answer checking in the UI: safe_parse + the timeout-wrapped verifiers."""

import pytest

from core.schema import Question
from tests.conftest import make_question
from verify.answers import check_typed_answer, effective_tolerance, is_integer_answer

UI = 1e-3


@pytest.fixture
def by_id(curated: list[Question]) -> dict[str, Question]:
    return {q.id: q for q in curated}


def check(q: Question, typed: str, **kw):
    return check_typed_answer(q, typed, ui_rel_tol=kw.pop("ui_rel_tol", UI), **kw)


# --- integer detection and tolerance selection ------------------------------------------------------


@pytest.mark.parametrize(
    "answer,expected",
    [("17", True), ("1", True), ("6/3", True), ("-4", True), ("36/5", False), ("3.92", False),
     ("2/lam**2", False), ("(a*d - b*c)**2", False), ("not an expression )(", False)],
)  # fmt: skip
def test_is_integer_answer(answer: str, expected: bool) -> None:
    assert is_integer_answer(answer) is expected


def test_effective_tolerance_rules(by_id: dict[str, Question]) -> None:
    assert effective_tolerance(by_id["prob-001"], UI) == UI  # 1/9: not an integer
    assert effective_tolerance(by_id["stat-002"], UI) == 0.0  # 17: integer -> exact
    over = by_id["stat-002"].model_copy(update={"answer_tolerance": 0.05})
    assert effective_tolerance(over, UI) == 0.05  # a per-question override beats the integer rule
    over2 = by_id["prob-001"].model_copy(update={"answer_tolerance": 0.0})
    assert effective_tolerance(over2, UI) == 0.0


# --- numeric ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["1/9", "0.1111", "0.11111", "  1/9 ", "2/18", "0.1112"])
def test_numeric_accepts_exact_forms_and_close_decimals(by_id, typed: str) -> None:
    r = check(by_id["prob-001"], typed)
    assert r.outcome == "correct" and r.verified is True, typed


@pytest.mark.parametrize("typed", ["0.11", "0.1115", "1/8", "0.12", "0", "-1/9", "1/10"])
def test_numeric_rejects_wrong_answers_and_coarse_decimals(by_id, typed: str) -> None:
    r = check(by_id["prob-001"], typed)
    assert r.outcome == "incorrect" and r.verified is True, typed


def test_the_ui_tolerance_is_a_parameter_not_a_constant(by_id) -> None:
    q = by_id["prob-001"]
    assert check(q, "0.1111", ui_rel_tol=0.0).outcome == "incorrect"  # strict
    assert check(q, "0.11", ui_rel_tol=0.05).outcome == "correct"  # loose (1% off)
    assert check(q, "0.1111", ui_rel_tol=UI).tolerance == UI


def test_per_question_tolerance_override(by_id) -> None:
    q = by_id["prob-001"].model_copy(update={"answer_tolerance": 0.05})
    assert check(q, "0.11").outcome == "correct" and check(q, "0.11").tolerance == 0.05
    assert check(q, "0.2").outcome == "incorrect"


# --- integer answers need exact matches ------------------------------------------------------------


@pytest.mark.parametrize("typed", ["17", "17.0", "34/2", "17.000000"])
def test_integer_answers_accept_exact_values(by_id, typed: str) -> None:
    assert check(by_id["stat-002"], typed).outcome == "correct", typed


@pytest.mark.parametrize("typed", ["17.01", "16.99", "17.0001", "18", "16", "17.000001", "0"])
def test_integer_answers_reject_anything_not_exact_even_within_the_ui_tolerance(
    by_id, typed
) -> None:
    r = check(by_id["stat-002"], typed)  # 17.01 is only 5.9e-4 off: inside the 1e-3 UI tolerance
    assert r.outcome == "incorrect" and r.tolerance == 0.0, typed


def test_integer_override_allows_a_tolerance(by_id) -> None:
    q = by_id["stat-002"].model_copy(update={"answer_tolerance": 0.01})
    assert check(q, "17.1").outcome == "correct"  # 0.59% off, inside the override
    assert check(q, "18").outcome == "incorrect"  # 5.9% off


def test_zero_valued_answers_are_matched_exactly() -> None:
    q = Question.model_validate(make_question(answer="0"))
    assert check(q, "0").outcome == "correct" and check(q, "0.0001").outcome == "incorrect"


# --- symbolic ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["2/lam**2", "2*lam**(-2)", "2/(lam*lam)", "(4/2)/lam^2"])
def test_symbolic_accepts_equivalent_forms(by_id, typed: str) -> None:
    assert check(by_id["stat-003"], typed).outcome == "correct", typed


@pytest.mark.parametrize(
    "typed", ["1/lam**2", "2/lam**2 + 1/10**9", "2/lam", "-2/lam**2", "2/mu**2"]
)
def test_symbolic_rejects_wrong_forms(by_id, typed: str) -> None:
    assert check(by_id["stat-003"], typed).outcome == "incorrect", typed


def test_symbolic_polynomial_equivalence(by_id) -> None:
    q = by_id["linalg-005"]
    assert check(q, "(a*d - b*c)**2").outcome == "correct"
    assert check(q, "a**2*d**2 - 2*a*b*c*d + b**2*c**2").outcome == "correct"
    assert check(q, "(a*d + b*c)**2").outcome == "incorrect"


def test_symbolic_ignores_the_numeric_tolerance(by_id) -> None:
    assert check(by_id["stat-003"], "2/lam**2 + 1/10**9", ui_rel_tol=0.5).outcome == "incorrect"


# --- unreadable input is not a wrong answer ---------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    ["", "   ", "abc def", "__import__('os').system('true')", "open('/etc/passwd')", "9**9**9",
     "lambda: 1", "1" * 500, "x.__class__", "1/", "((("],
)  # fmt: skip
def test_unreadable_input_is_flagged_not_marked_wrong(by_id, typed: str) -> None:
    for qid in ("prob-001", "stat-003"):
        r = check(by_id[qid], typed)
        assert r.outcome == "unreadable" and r.verified is False and r.message, (qid, typed)


def test_a_symbol_in_a_numeric_answer_is_unreadable(by_id) -> None:
    r = check(by_id["prob-001"], "x/9")
    assert r.outcome == "unreadable" and "number" in r.message.lower()


def test_slow_input_times_out_as_unreadable(by_id) -> None:
    r = check(by_id["prob-001"], "factorial(10**15)", timeout=2.0)
    assert r.outcome == "unreadable"


# --- other answer types ------------------------------------------------------------------------------


def test_text_answers_are_self_graded_and_labelled_unverified(by_id) -> None:
    r = check(by_id["stat-005"], "because of degrees of freedom")
    assert r.outcome == "self_graded" and r.verified is False
    assert "unverified" in r.message.lower() and "self" in r.message.lower()


def test_code_answers_are_not_supported_yet() -> None:
    q = Question.model_validate(make_question(answer_type="code", answer="def f(): pass"))
    with pytest.raises(ValueError, match="code"):
        check(q, "anything")


def test_the_result_says_what_was_verified(by_id) -> None:
    ok = check(by_id["prob-001"], "1/9")
    assert ok.verified and ok.correct and ok.tolerance == UI
    bad = check(by_id["prob-001"], "1/8")
    assert bad.verified and not bad.correct


def test_exact_means_exact_even_at_floating_point_dust(by_id) -> None:
    """Integer answers allow no absolute slack either (the verifier's 1e-12 is not used here)."""
    assert check(by_id["stat-002"], "17.0000000000001").outcome == "incorrect"
    assert check(by_id["stat-002"], "16.9999999999999").outcome == "incorrect"
    q = Question.model_validate(make_question(answer="0"))
    assert check(q, "0.0000000000001").outcome == "incorrect"
    assert check(q, "-0.0000000000001").outcome == "incorrect"
