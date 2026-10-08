"""Check an answer typed in the UI against a question's stored answer.

Typed text is untrusted: it goes through ``safe_parse`` and the timeout-wrapped verifiers
(``check_numeric`` / ``check_symbolic``), so a hostile or pathological input can at worst be
reported as unreadable. This is *not* promotion: the tolerance here (``ui_rel_tol``) is looser
than the verifier's strict default and is applied only to what a person types.

Tolerance rules for numeric answers:
* the question's own ``answer_tolerance``, if set, always wins (0 means exact);
* otherwise an integer-valued answer must match exactly;
* otherwise ``ui_rel_tol`` (relative).
Symbolic answers are compared by SymPy equivalence, with no tolerance. Text answers are
self-graded and labelled unverified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.schema import AnswerType, Question
from verify.parsing import ParseRejected, safe_parse
from verify.specs import ExactSpec
from verify.sympy_check import check_numeric, check_symbolic

Outcome = Literal["correct", "incorrect", "unreadable", "self_graded"]
DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class AnswerResult:
    """What checking a typed answer found."""

    outcome: Outcome
    verified: bool  # True only when code checked it (numeric/symbolic); False for self-graded
    message: str
    tolerance: float | None = None  # relative tolerance applied, for numeric answers

    @property
    def correct(self) -> bool:
        """True if the answer was checked and is right."""
        return self.outcome == "correct"


def is_integer_answer(answer: str) -> bool:
    """True if the stored answer's value is an integer (such answers need exact matches)."""
    try:
        value = safe_parse(answer, reference=True)
    except ParseRejected:
        return False
    return bool(getattr(value, "is_Integer", False))


def effective_tolerance(question: Question, ui_rel_tol: float) -> float:
    """Relative tolerance for typed numeric answers to this question."""
    if question.answer_tolerance is not None:
        return question.answer_tolerance
    return 0.0 if is_integer_answer(question.answer) else ui_rel_tol


def check_typed_answer(
    question: Question,
    typed: str,
    *,
    ui_rel_tol: float = 1e-3,
    timeout: float = DEFAULT_TIMEOUT,
) -> AnswerResult:
    """Check ``typed`` against ``question``. Raises ValueError for ``code`` questions."""
    kind = question.answer_type
    if kind is AnswerType.CODE:
        raise ValueError("code questions are not supported in the practice UI yet")
    if kind is AnswerType.TEXT:
        return AnswerResult(
            "self_graded",
            False,
            "Unverified: this answer is not checked by code. Compare with the model answer and "
            "grade yourself.",
        )
    reference = ExactSpec(expr=question.answer)
    if kind is AnswerType.SYMBOLIC:
        res = check_symbolic(typed, reference, timeout=timeout)
        tolerance = None
    else:
        tolerance = effective_tolerance(question, ui_rel_tol)
        res = check_numeric(
            typed,
            reference,
            rel_tol=tolerance,
            abs_tol=0.0 if tolerance == 0.0 else 1e-12,
            timeout=timeout,
        )
    if res.outcome == "error":
        hint = "a number" if kind is AnswerType.NUMERIC else "an expression"
        return AnswerResult(
            "unreadable", False, f"Couldn't read that as {hint}: {res.details}", tolerance
        )
    if res.passed:
        return AnswerResult("correct", True, "Correct.", tolerance)
    return AnswerResult("incorrect", True, "Not quite.", tolerance)
