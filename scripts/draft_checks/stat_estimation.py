"""Independent third checks for the stat.estimation drafts.

Each recomputes the answer from the raw data or the model by a different route than the stated
solution and the YAML reference (NumPy, SciPy optimisation or quadrature, exact enumeration over a
matrix identity, or the score-variance form of the Fisher information).
"""

from __future__ import annotations

from fractions import Fraction as F

import numpy as np
import sympy as sp
from scipy import integrate, optimize


def stat_007() -> float:
    """NumPy's sample variance with ddof=1."""
    return float(np.var([1, 3, 5, 7, 9], ddof=1))


def stat_008() -> float:
    """Solve for the maximum from the raw data: the root of the log-likelihood's slope.

    The slope is a central difference of the log-likelihood, so no formula is derived by hand.
    Maximising the likelihood directly is only accurate to about 1e-8 because it is flat at the
    top, which is not enough for the strict 1e-9 comparison used against the references.
    """
    data = np.array([0.5, 1.5, 2.0, 4.0])
    h = 1e-6

    def loglik(lam: float) -> float:
        return len(data) * np.log(lam) - lam * data.sum()

    return float(
        optimize.brentq(
            lambda lam: (loglik(lam + h) - loglik(lam - h)) / (2 * h), 0.01, 10.0, xtol=1e-14
        )
    )


def stat_009() -> sp.Expr:
    """E[sum of squares] = sigma^2 * trace of the centring matrix I - J/n, built explicitly."""
    n, sigma2 = 5, 10
    centring = sp.eye(n) - sp.ones(n, n) / n
    return sp.Rational(1, n) * sigma2 * centring.trace() - sigma2


def stat_010() -> int:
    """Smallest n with 1.96*5/sqrt(n) <= 0.5, tested exactly on squares."""
    z, sigma, half = F(196, 100), F(5), F(1, 2)
    n = 1
    while (z * sigma) ** 2 > half**2 * n:
        n += 1
    return n


def stat_011() -> float:
    """Numerical quadrature of the unnormalised posterior (prior times likelihood)."""
    num, _ = integrate.quad(lambda p: p * p * (1 - p) ** 2 * p**7 * (1 - p) ** 3, 0, 1)
    den, _ = integrate.quad(lambda p: p * (1 - p) ** 2 * p**7 * (1 - p) ** 3, 0, 1)
    return float(num / den)


def stat_012() -> sp.Expr:
    """Fisher information as the variance of the score, not minus the second derivative."""
    lam, x, n = sp.symbols("lam x n", positive=True)
    density = lam * sp.exp(-lam * x)
    score = sp.diff(sp.log(density), lam)
    info = sp.integrate(sp.simplify(score**2 * density), (x, 0, sp.oo))
    return 1 / (n * info)


CHECKS = {
    "stat-007": stat_007,
    "stat-008": stat_008,
    "stat-009": stat_009,
    "stat-010": stat_010,
    "stat-011": stat_011,
    "stat-012": stat_012,
}
