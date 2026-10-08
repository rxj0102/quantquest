"""Independent third checks for the la.eigen drafts.

Numeric ones use NumPy's floating-point eigensolver (a different engine from SymPy's exact one);
symbolic ones use SymPy's characteristic polynomial or eigenvalue solver rather than the
determinant/trace identities used in the references and solutions.
"""

from __future__ import annotations

import numpy as np
import sympy as sp


def linalg_007() -> float:
    a = np.array([[6, 1, 4], [0, 2, 9], [0, 0, 5]], dtype=float)
    return float(np.linalg.eigvals(a).real.max())


def linalg_008() -> float:
    """Symmetric matrix: use the symmetric eigensolver and take the smaller eigenvalue."""
    vals = np.linalg.eigvalsh(np.array([[1, 3], [3, 1]], dtype=float))
    assert abs(vals[1] - 4.0) < 1e-12  # the stated eigenvalue 4 is really there
    return float(vals[0])


def linalg_009() -> sp.Expr:
    """Characteristic polynomial from SymPy: det(tI - A) equals det(A - tI) for a 2x2 matrix."""
    t = sp.Symbol("t")
    return sp.Matrix([[1, 2], [3, 4]]).charpoly(t).as_expr()


def linalg_010() -> float:
    """Apply the polynomial to A's eigenvalues instead of forming B."""
    vals = np.linalg.eigvals(np.array([[1, 2], [3, 0]], dtype=float)).real
    return float(max(v**2 - 2 * v + 1 for v in vals))


def linalg_011() -> float:
    """Divide A v by v componentwise; equal ratios also prove that v is an eigenvector."""
    a = np.array([[2, 0, 1], [1, 1, 1], [2, 0, 3]], dtype=float)
    v = np.array([1, 1, 2], dtype=float)
    ratios = (a @ v) / v
    assert np.allclose(ratios, ratios[0]), "v is not an eigenvector"
    return float(ratios[0])


def linalg_012() -> sp.Expr:
    """Let SymPy solve the eigenproblem and return the eigenvalue that is not 1."""
    a, b = sp.symbols("a b")
    vals = sp.Matrix([[1 - a, a], [b, 1 - b]]).eigenvals()
    (other,) = [v for v in vals if sp.simplify(v - 1) != 0]
    return other


CHECKS = {
    "linalg-007": linalg_007,
    "linalg-008": linalg_008,
    "linalg-009": linalg_009,
    "linalg-010": linalg_010,
    "linalg-011": linalg_011,
    "linalg-012": linalg_012,
}
