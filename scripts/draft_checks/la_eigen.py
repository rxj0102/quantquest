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
    """Eigendecompose M = I + u v^T numerically; drop the two eigenvalues equal to 1."""
    u, v = np.array([2.0, 1.0, 3.0]), np.array([1.0, 2.0, 1.0])
    vals = np.linalg.eigvals(np.eye(3) + np.outer(u, v)).real
    ones = [x for x in vals if abs(x - 1) < 1e-6]
    assert len(ones) == 2, "the eigenvalue 1 should appear exactly twice"
    (third,) = [x for x in vals if abs(x - 1) >= 1e-6]
    return float(third)


def linalg_009() -> sp.Expr:
    """Characteristic polynomial from SymPy: det(tI - A) equals det(A - tI) for a 2x2 matrix."""
    t = sp.Symbol("t")
    return sp.Matrix([[1, 2], [3, 4]]).charpoly(t).as_expr()


def linalg_010() -> float:
    """Apply the polynomial to A's eigenvalues instead of forming B."""
    vals = np.linalg.eigvals(np.array([[1, 2], [3, 0]], dtype=float)).real
    return float(max(v**2 - 2 * v + 1 for v in vals))


def linalg_011() -> float:
    """Eigendecompose A and return the eigenvalue whose eigenvector is parallel to v.

    Deliberately not the A v computation used by the solution and the reference.
    """
    a = np.array([[2, 0, 1], [1, 1, 1], [2, 0, 3]], dtype=float)
    v = np.array([1, 1, 2], dtype=float)
    vals, vecs = np.linalg.eig(a)
    cos = np.abs(vecs.T @ v) / (np.linalg.norm(vecs, axis=0) * np.linalg.norm(v))
    best = int(np.argmax(cos))
    assert cos[best] > 1 - 1e-9, "no eigenvector is parallel to v"
    assert sum(abs(vals - vals[best]) < 1e-6) == 1, "eigenvalue is repeated: ambiguous"
    return float(vals[best].real)


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
