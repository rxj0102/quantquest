"""Independent computations for the curated probability answers.

Each exact check uses enumeration or SymPy, never the formula in the solution text.
Each Monte Carlo check (fixed seed, 1e6 trials) must land within 3 standard errors.
"""

from __future__ import annotations

import itertools
from fractions import Fraction

import numpy as np
import sympy as sp

TRIALS = 1_000_000
SEED = 12345


def prob_001() -> Fraction:
    rolls = list(itertools.product(range(1, 7), repeat=2))
    return Fraction(sum(a + b == 9 for a, b in rolls), len(rolls))


def prob_002() -> Fraction:
    socks = ["r"] * 5 + ["b"] * 3
    pairs = list(itertools.permutations(range(8), 2))
    return Fraction(sum(socks[i] == socks[j] == "r" for i, j in pairs), len(pairs))


def prob_003() -> sp.Expr:
    # E[N] = sum_{k>=1} P(N >= k) = sum (5/6)^(k-1)
    k = sp.symbols("k", integer=True, positive=True)
    return sp.summation(sp.Rational(5, 6) ** (k - 1), (k, 1, sp.oo))


def prob_004() -> Fraction:
    flips = list(itertools.product((0, 1), repeat=4))
    return Fraction(sum(sum(f) >= 2 for f in flips), len(flips))


def prob_005() -> Fraction:
    perms = list(itertools.permutations(range(4)))
    return Fraction(sum(all(p[i] != i for i in range(4)) for p in perms), len(perms))


def prob_006() -> Fraction:
    # Enumerate a population of 10_000 people: 100 sick, 9_900 healthy.
    sick, healthy = 100, 9_900
    true_pos = sick * 95 // 100
    false_pos = healthy * 10 // 100
    return Fraction(true_pos, true_pos + false_pos)


def prob_007() -> sp.Expr:
    u, v = sp.symbols("u v", positive=True)
    # E[max] = int int max(u, v) du dv over the unit square, split on u > v.
    return sp.integrate(sp.integrate(u, (v, 0, u)), (u, 0, 1)) + sp.integrate(
        sp.integrate(v, (u, 0, v)), (v, 0, 1)
    )


def prob_008() -> sp.Rational:
    # Solve the absorbing-chain linear system h(i) = (h(i-1)+h(i+1))/2, h(0)=0, h(10)=1.
    n = 10
    a = sp.zeros(n + 1, n + 1)
    b = sp.zeros(n + 1, 1)
    a[0, 0], a[n, n], b[n] = 1, 1, 1
    for i in range(1, n):
        a[i, i], a[i, i - 1], a[i, i + 1] = 1, -sp.Rational(1, 2), -sp.Rational(1, 2)
    return a.LUsolve(b)[3]


# Monte Carlo: id -> (estimate, standard error)
def _mc(indicator: np.ndarray) -> tuple[float, float]:
    p = float(indicator.mean())
    return p, float(np.sqrt(p * (1 - p) / indicator.size))


def mc_prob_001() -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    d = rng.integers(1, 7, size=(TRIALS, 2))
    return _mc(d.sum(axis=1) == 9)


def mc_prob_004() -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    return _mc(rng.integers(0, 2, size=(TRIALS, 4)).sum(axis=1) >= 2)


def mc_prob_005() -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    perms = rng.permuted(np.tile(np.arange(4), (TRIALS, 1)), axis=1)
    return _mc((perms != np.arange(4)).all(axis=1))


def mc_prob_006() -> tuple[float, float]:
    """Estimate P(sick | flagged) from the flagged subpopulation."""
    rng = np.random.default_rng(SEED)
    sick = rng.random(TRIALS) < 0.01
    flagged = np.where(sick, rng.random(TRIALS) < 0.95, rng.random(TRIALS) < 0.10)
    p = float(sick[flagged].mean())
    return p, float(np.sqrt(p * (1 - p) / flagged.sum()))


def mc_prob_007() -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    m = rng.random((TRIALS, 2)).max(axis=1)
    return float(m.mean()), float(m.std(ddof=1) / np.sqrt(TRIALS))


def mc_prob_008() -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    pos = np.full(200_000, 3)
    alive = np.ones(pos.size, dtype=bool)
    while alive.any():
        step = rng.integers(0, 2, size=pos.size) * 2 - 1
        pos = np.where(alive, pos + step, pos)
        alive &= (pos > 0) & (pos < 10)
    return _mc(pos == 10)


EXACT = {f"prob-{i:03d}": globals()[f"prob_{i:03d}"] for i in range(1, 9)}
MONTE_CARLO = {
    "prob-001": mc_prob_001,
    "prob-004": mc_prob_004,
    "prob-005": mc_prob_005,
    "prob-006": mc_prob_006,
    "prob-007": mc_prob_007,
    "prob-008": mc_prob_008,
}
