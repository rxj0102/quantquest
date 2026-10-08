"""Independent third checks for the prob.conditional drafts.

Each uses a method different from both the stated solution and the YAML reference: explicit
enumeration of the sample space with exact fractions, or a different algebraic route.
"""

from __future__ import annotations

import itertools
from fractions import Fraction as F

import sympy as sp


def prob_009() -> F:
    """Condition by listing the faces that satisfy the given, then count."""
    given = [face for face in range(1, 11) if face % 3 == 0]
    return F(sum(1 for face in given if face > 5), len(given))


def prob_010() -> F:
    """Enumerate ordered (red, blue) pairs with different faces."""
    pairs = [(r, b) for r in range(1, 7) for b in range(1, 7) if r != b]
    return F(sum(1 for r, b in pairs if r + b == 8), len(pairs))


def prob_011() -> F:
    """Enumerate the die face, then every resistor in the opened box, each with its weight."""
    box_a = [1] * 3 + [0] * 9  # 1 = faulty
    box_b = [1] * 7 + [0] * 13
    total = F(0)
    for face in range(1, 7):
        box = box_a if face <= 2 else box_b
        for resistor in box:
            total += F(1, 6) * F(1, len(box)) * resistor
    return total


def prob_012() -> F:
    """Update the posterior one inspection at a time (sequential Bayes)."""
    prior = F(2, 100)
    sens, fpr = F(90, 100), F(5, 100)
    for _ in range(2):
        prior = prior * sens / (prior * sens + (1 - prior) * fpr)
    return prior


def prob_013() -> sp.Expr:
    """Solve for P(B) from P(not A and not B) = (1 - P(A))(1 - P(B)) = 1 - q."""
    q = sp.Symbol("q")
    return 1 - (1 - q) / (1 - sp.Rational(2, 5))


def prob_014() -> F:
    """Enumerate all 6**4 outcomes, condition on max == 5, count exactly two fives."""
    given = [r for r in itertools.product(range(1, 7), repeat=4) if max(r) == 5]
    return F(sum(1 for r in given if r.count(5) == 2), len(given))


CHECKS = {
    "prob-009": prob_009,
    "prob-010": prob_010,
    "prob-011": prob_011,
    "prob-012": prob_012,
    "prob-013": prob_013,
    "prob-014": prob_014,
}
