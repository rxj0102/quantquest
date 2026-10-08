import pytest

from core.schema import AnswerType, Question
from scripts.check_answers import EXACT as SCRIPT_EXACT
from verify.references import ExactSpec
from verify.sympy_check import check_numeric, check_symbolic

# --- known good: the scripts/ computations are the fixtures ---------------------------------


def test_numeric_known_good_against_script_fixtures(curated: list[Question]) -> None:
    ran = 0
    for q in curated:
        if q.answer_type is not AnswerType.NUMERIC:
            continue
        reference = ExactSpec(expr=str(SCRIPT_EXACT[q.id]()))
        res = check_numeric(q.answer, reference)
        assert res.passed, (q.id, res.details)
        ran += 1
    assert ran >= 14


def test_symbolic_known_good_against_script_fixtures(curated: list[Question]) -> None:
    ran = 0
    for q in curated:
        if q.answer_type is not AnswerType.SYMBOLIC:
            continue
        reference = ExactSpec(expr=str(SCRIPT_EXACT[q.id]()), symbols={})
        res = check_symbolic(q.answer, reference)
        assert res.passed, (q.id, res.details)
        ran += 1
    assert ran >= 4  # linalg-005, stat-003, linalg-009, linalg-012: none skipped


def test_symbolic_accepts_equivalent_forms() -> None:
    ref = ExactSpec(expr="(a*d - b*c)**2")
    assert check_symbolic("a**2*d**2 - 2*a*b*c*d + b**2*c**2", ref).passed
    assert check_symbolic("(b*c - a*d)**2", ref).passed


# --- known bad: wrong and near-miss answers must be rejected --------------------------------


@pytest.mark.parametrize(
    "claimed,ref",
    [
        ("0.1111", "1/9"),  # truncated decimal
        ("0.111111", "1/9"),
        ("1/9 + 1/10**6", "1/9"),
        ("1/9 + 1/10**8", "1/9"),
        ("5/14 + 1/1000", "5/14"),
        ("0.3571", "5/14"),
        ("6", "5"),
        ("4.999", "5"),
        ("-1", "1"),
        ("2/3 + 1/10**5", "2/3"),
        ("0", "1"),
        ("17.0000001", "17"),
    ],
)
def test_numeric_rejects_near_misses(claimed: str, ref: str) -> None:
    res = check_numeric(claimed, ExactSpec(expr=ref))
    assert not res.passed and res.outcome == "fail"


def test_numeric_tolerance_is_explicit_and_recorded() -> None:
    ref = ExactSpec(expr="1/9")
    strict = check_numeric("0.1111111", ref, rel_tol=1e-9)
    loose = check_numeric("0.1111111", ref, rel_tol=1e-6)
    assert not strict.passed and loose.passed
    assert loose.data["rel_tol"] == 1e-6
    assert "rel_tol" in loose.method or "rel_tol" in loose.details


@pytest.mark.parametrize(
    "claimed,ref",
    [
        ("(a*d + b*c)**2", "(a*d - b*c)**2"),
        ("a*d - b*c", "(a*d - b*c)**2"),
        ("(a*d - b*c)**2 + 1/10**9", "(a*d - b*c)**2"),
        ("1/lam**2", "2/lam**2"),
        ("2/lam**2 + 1/10**9", "2/lam**2"),
        ("2/lam", "2/lam**2"),
        ("-2/lam**2", "2/lam**2"),
        ("2/mu**2", "2/lam**2"),  # different variable is a different answer
    ],
)
def test_symbolic_rejects_wrong_and_near_miss(claimed: str, ref: str) -> None:
    res = check_symbolic(claimed, ExactSpec(expr=ref))
    assert not res.passed and res.outcome == "fail"


def test_numeric_claim_with_free_symbols_is_an_error_not_a_pass() -> None:
    res = check_numeric("2/lam**2", ExactSpec(expr="1/9"))
    assert not res.passed


# --- parsing safety flows through -----------------------------------------------------------


@pytest.mark.parametrize("claimed", ["__import__('os').system('true')", "9**9**9", "open('x')"])
def test_malicious_claimed_answers_are_errors(claimed: str) -> None:
    for fn in (check_numeric, check_symbolic):
        res = fn(claimed, ExactSpec(expr="1"))
        assert res.outcome == "error" and not res.passed


def test_malicious_reference_is_an_error() -> None:
    res = check_numeric("1", ExactSpec(expr="__import__('os').system('true')"))
    assert res.outcome == "error"


def test_reference_must_evaluate_to_a_scalar() -> None:
    res = check_numeric("1", ExactSpec(expr="Matrix([[1, 2]])"))
    assert res.outcome == "error"


# --- hard timeout ---------------------------------------------------------------------------

# An integral SymPy cannot finish quickly: used to prove the timeout kills the work.
HANG = "Integral(sin(x)**x*exp(-x**3)*log(x + sin(x**2)), (x, 0, oo))"


def test_symbolic_check_has_a_hard_timeout() -> None:
    import time

    t0 = time.monotonic()
    res = check_symbolic("1", ExactSpec(expr=HANG), timeout=2.0)
    assert time.monotonic() - t0 < 10
    assert res.outcome == "error" and "timeout" in res.details.lower()
    assert not res.passed


def test_numeric_check_has_a_hard_timeout() -> None:
    res = check_numeric("1", ExactSpec(expr=HANG), timeout=2.0)
    assert res.outcome == "error" and "timeout" in res.details.lower()
