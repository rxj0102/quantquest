"""Independent computations for the curated linear algebra answers."""

from __future__ import annotations

import numpy as np
import sympy as sp


def linalg_001() -> sp.Expr:
    return sp.Matrix([[2, 1], [5, 3]]).det()


def linalg_002() -> float:
    return float(max(np.linalg.eigvals(np.array([[4, 1], [2, 3]], dtype=float))).real)


def linalg_003() -> int:
    return sp.Matrix([[1, 2, 3], [2, 4, 6], [1, 0, 1]]).rank()


def linalg_004() -> sp.Expr:
    a = sp.Matrix([[1, 2], [0, 3]])
    return (a * a).trace()


def linalg_005() -> sp.Expr:
    a, b, c, d = sp.symbols("a b c d")
    m = sp.Matrix([[a, b], [c, d]])
    return sp.expand((m * m).det())


def linalg_006() -> sp.Rational:
    v, u = sp.Matrix([2, 2]), sp.Matrix([1, 2])
    p = (v.dot(u) / u.dot(u)) * u
    return p.dot(p)


EXACT = {f"linalg-{i:03d}": globals()[f"linalg_{i:03d}"] for i in range(1, 7)}
