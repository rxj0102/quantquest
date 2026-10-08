"""Independent third checks for the la.projections drafts.

The references use the normal-equation / projection-matrix formula and the solutions use dot
products or summary statistics, so these use other routes: NumPy's least-squares solver, a SciPy
null-space basis, or a one-variable minimisation.
"""

from __future__ import annotations

import numpy as np
import sympy as sp
from scipy.linalg import null_space


def linalg_013() -> float:
    """The projection is zero iff v is orthogonal to u: v must lie in u's null-space complement."""
    (direction,) = null_space(np.array([[2.0, -1.0]])).T  # unit vector perpendicular to u
    return float(6.0 * direction[1] / direction[0])  # scale so the first entry is 6


def linalg_014() -> sp.Expr:
    """Minimise |v - c u|^2 over c with calculus, then square the length of c u."""
    a, b, c = sp.symbols("a b c", real=True)
    dist2 = (a - 3 * c) ** 2 + (b - 4 * c) ** 2
    (c_star,) = sp.solve(sp.diff(dist2, c), c)
    return sp.expand(c_star**2 * 25)


def linalg_015() -> float:
    """Least-squares fit of b by the columns a1, a2 (the projection onto the plane)."""
    cols = np.array([[1, 1], [1, -1], [0, 1]], dtype=float)
    b = np.array([2.0, 4.0, 3.0])
    coef = np.linalg.lstsq(cols, b, rcond=None)[0]
    return float((cols @ coef)[0])


def linalg_016() -> float:
    """Residual of the point after projecting onto a null-space basis of the plane's equation."""
    basis = null_space(np.array([[2.0, 1.0, -2.0]]))  # orthonormal basis of the plane
    p = np.array([4.0, 2.0, -1.0])
    residual = p - basis @ (basis.T @ p)
    return float(np.linalg.norm(residual))


def linalg_017() -> float:
    x, y = np.array([0.0, 1.0, 2.0, 3.0]), np.array([1.0, 3.0, 4.0, 8.0])
    return float(np.polyfit(x, y, 1)[1])  # [slope, intercept]


def linalg_018() -> float:
    """Trace of Q Q^T from a QR factorisation: the orthonormal basis route, not the formula."""
    a = np.array([[1, 0], [1, 1], [1, 2], [1, 3]], dtype=float)
    q, _ = np.linalg.qr(a)
    return float(np.trace(q @ q.T))


def linalg_019() -> float:
    """Project with least squares onto the two spanning vectors, then reflect."""
    cols = np.array([[1, 0], [0, 1], [1, 1]], dtype=float)
    b = np.array([3.0, 0.0, 1.0])
    p = cols @ np.linalg.lstsq(cols, b, rcond=None)[0]
    return float((2 * p - b)[2])


CHECKS = {
    "linalg-013": linalg_013,
    "linalg-014": linalg_014,
    "linalg-015": linalg_015,
    "linalg-016": linalg_016,
    "linalg-017": linalg_017,
    "linalg-018": linalg_018,
    "linalg-019": linalg_019,
}
