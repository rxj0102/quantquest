"""Pick and run the right verifiers for a question and return a ``Verification`` record.

Routing (CLAUDE.md "Verification rules"):

* ``text`` answers and ``case`` format: never auto-verified, labelled ``unverified``
* ``code`` answers: reference solution run against the reference tests in the sandbox
* pricing reference: Black-Scholes closed form cross-checked by the binomial tree
* ``numeric`` / ``symbolic``: exact SymPy check against the independent exact reference; for
  ``format: probability`` a Monte Carlo check (>= 1e6 trials, fixed seed, 3 standard errors) is
  required as well. A probability question with no simulator reference cannot be certified.

Result mapping: any definite failure -> ``fail``; otherwise any check that could not complete
(bad input, timeout, missing reference, sandbox unavailable) -> ``unverified``; otherwise ``pass``.
"""

from __future__ import annotations

from core.schema import AnswerType, Format, Question, Verification
from verify.code_runner import check_code
from verify.monte_carlo import monte_carlo_check
from verify.pricing import check_price
from verify.references import Reference
from verify.result import CheckResult, errored
from verify.simulators import get_simulator
from verify.sympy_check import check_numeric, check_symbolic, claimed_to_float


def _monte_carlo(question: Question, reference: Reference) -> CheckResult:
    method = "monte_carlo"
    if question.answer_type is not AnswerType.NUMERIC:
        return errored(method, "Monte Carlo needs a numeric answer")
    assert reference.simulator is not None
    try:
        simulate = get_simulator(reference.simulator.name, reference.simulator.params)
    except (KeyError, ValueError) as exc:
        return errored(method, f"invalid simulator reference: {exc}")
    value, err = claimed_to_float(question.answer)
    if err is not None:
        return errored(method, err)
    return monte_carlo_check(value, simulate)


def _checks(question: Question, reference: Reference) -> list[CheckResult]:
    if question.answer_type is AnswerType.CODE:
        if reference.code is None:
            return [errored("code_runner", "code question has no code reference")]
        return [check_code(question.answer, reference.code)]
    if reference.price is not None:
        return [check_price(question.answer, reference.price)]
    if reference.exact is None:
        return [errored("sympy", "no exact reference")]
    if question.answer_type is AnswerType.SYMBOLIC:
        results = [check_symbolic(question.answer, reference.exact)]
    else:
        results = [check_numeric(question.answer, reference.exact, rel_tol=reference.rel_tol)]
    needs_mc = question.format is Format.PROBABILITY
    if reference.simulator is None:
        if needs_mc:
            results.append(
                errored("monte_carlo", "probability question has no simulator reference")
            )
    elif not results[0].passed:
        pass  # exact check already decided; do not spend 1e6 trials on a wrong answer
    else:
        results.append(_monte_carlo(question, reference))
    return results


def _combine(results: list[CheckResult]) -> Verification:
    if any(r.outcome == "fail" for r in results):
        outcome = "fail"
    elif any(r.outcome == "error" for r in results):
        outcome = "unverified"
    else:
        outcome = "pass"
    method = " + ".join(r.method for r in results)
    details = " | ".join(f"{r.method.split('(')[0]}: {r.outcome}: {r.details}" for r in results)
    return Verification(method=method, result=outcome, details=details)


def verify_question(question: Question, reference: Reference | None) -> Verification:
    """Run the verifiers that apply to ``question`` and return the resulting record."""
    if question.answer_type is AnswerType.TEXT or question.format is Format.CASE:
        return Verification(
            method="none",
            result="unverified",
            details="text and case answers are not auto-verified; grade with a rubric",
        )
    if reference is None:
        return Verification(method="none", result="unverified", details="no reference supplied")
    return _combine(_checks(question, reference))
