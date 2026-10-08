"""Independent computations for the curated statistics answers."""

from __future__ import annotations

import numpy as np
import sympy as sp

SEED = 12345


def stat_001() -> sp.Rational:
    rng = np.random.default_rng(SEED)
    means = rng.normal(0.0, 4.0, size=(400_000, 25)).mean(axis=1)
    est = float(means.var())
    assert abs(est - 0.64) < 0.01, est  # sanity: simulation agrees with the exact value below
    return sp.Rational(16, 25)


def stat_002() -> sp.Rational:
    cov = sp.Matrix([[4, 2], [2, 9]])
    w = sp.Matrix([1, 1])
    return (w.T * cov * w)[0]


def stat_003() -> sp.Expr:
    x, lam = sp.symbols("x lam", positive=True)
    return sp.integrate(x**2 * lam * sp.exp(-lam * x), (x, 0, sp.oo))


def stat_004() -> sp.Rational:
    lam = sp.symbols("lam", positive=True)
    data = [1, 0, 3, 2, 4]
    loglik = sum(xi * sp.log(lam) - lam for xi in data)
    return sp.solve(sp.diff(loglik, lam), lam)[0]


def stat_006() -> float:
    from scipy import stats

    z = stats.norm.ppf(0.975)
    assert abs(z - 1.96) < 0.001, z
    return 2 * 1.96 * 10 / np.sqrt(100)


EXACT = {
    "stat-001": stat_001,
    "stat-002": stat_002,
    "stat-003": stat_003,
    "stat-004": stat_004,
    "stat-006": stat_006,
}
# stat-005 is a text answer: no automatic check (CLAUDE.md: text is graded, not verified).
