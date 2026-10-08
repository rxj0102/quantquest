"""Check every curated answer against an independent computation.

Run:  python -m scripts.check_answers
Exit code is non-zero if any answer disagrees. Seed for the M2 verifier tests; it is not
the app's verifier and is never imported by app code.
"""

from __future__ import annotations

import sys

import sympy as sp

from core.loader import load_questions
from core.schema import AnswerType
from scripts.answer_checks import linear_algebra, probability, statistics
from verify.parsing import safe_parse

EXACT = {**probability.EXACT, **statistics.EXACT, **linear_algebra.EXACT}
MONTE_CARLO = probability.MONTE_CARLO
TOL = sp.Rational(1, 10**9)


def parse(answer: str) -> sp.Expr:
    """Parse an answer string into a SymPy expression."""
    return safe_parse(answer)


def exact_agrees(answer: str, answer_type: AnswerType, computed: object) -> bool:
    """Compare the claimed answer to the computed one (numeric: tolerance; symbolic: simplify)."""
    claimed = parse(answer)
    got = (
        sp.nsimplify(computed, rational=True)
        if isinstance(computed, float)
        else sp.sympify(computed)  # trusted: output of our own check functions
    )
    # Compare symbols by name only (checks may declare assumptions such as positive=True).
    got = got.subs({sym: sp.Symbol(sym.name) for sym in got.free_symbols})
    diff = sp.simplify(claimed - got)
    if answer_type is AnswerType.SYMBOLIC:
        return diff == 0
    return abs(sp.N(diff, 30)) <= TOL


def mc_agrees(answer: str, estimate: float, stderr: float) -> bool:
    """Monte Carlo estimate must be within 3 standard errors of the claimed answer."""
    return abs(float(parse(answer)) - estimate) <= 3 * stderr


def main() -> int:
    failures = 0
    for q in load_questions():
        if q.answer_type is AnswerType.TEXT:
            print(f"SKIP  {q.id}  (text answer, not auto-verifiable)")
            continue
        if q.id not in EXACT:
            print(f"MISSING  {q.id}: no check registered")
            failures += 1
            continue
        ok = exact_agrees(q.answer, q.answer_type, EXACT[q.id]())
        line = f"{'OK   ' if ok else 'FAIL '} {q.id}  claimed={q.answer}  exact-check"
        if q.id in MONTE_CARLO:
            est, se = MONTE_CARLO[q.id]()
            mc_ok = mc_agrees(q.answer, est, se)
            ok = ok and mc_ok
            line += f"  mc={est:.5f}±{se:.5f} {'ok' if mc_ok else 'FAIL'}"
        print(line)
        failures += not ok
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
