"""References: the independent evidence a question is verified against.

A ``Reference`` is plain, JSON-serializable data (``model_dump_json``), so a generated question
can carry its own later. It never contains executable code from an untrusted source: the exact
part is a restricted expression (``verify.parsing``), the simulator is a *name* from a fixed
library plus parameters, pricing is a parameter set, and code tests are inputs/outputs.

``CURATED_REFERENCES`` holds the references for the hand-written core pool. They are written
independently of the solutions (enumeration, sums, integrals, linear systems) and are
cross-checked against ``scripts/answer_checks`` in the tests.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from verify.code_runner import CodeSpec
from verify.pricing import PricingSpec
from verify.specs import ExactSpec, SimulatorSpec

__all__ = [
    "CURATED_REFERENCES",
    "CodeSpec",
    "ExactSpec",
    "PricingSpec",
    "Reference",
    "SimulatorSpec",
]


class Reference(BaseModel):
    """Everything needed to verify one question. Fields are independent and all optional."""

    model_config = ConfigDict(extra="forbid")

    exact: ExactSpec | None = None
    simulator: SimulatorSpec | None = None
    price: PricingSpec | None = None
    code: CodeSpec | None = None
    rel_tol: float = Field(default=1e-9, ge=0, allow_inf_nan=False)


def _ref(
    expr: str, symbols: dict[str, str] | None = None, sim: SimulatorSpec | None = None
) -> Reference:
    return Reference(exact=ExactSpec(expr=expr, symbols=symbols or {}), simulator=sim)  # type: ignore[arg-type]


def _sim(name: str, **params: object) -> SimulatorSpec:
    return SimulatorSpec(name=name, params=params)


CURATED_REFERENCES: dict[str, Reference] = {
    "prob-001": _ref(
        "Sum(Sum(KroneckerDelta(a + b, 9), (a, 1, 6)), (b, 1, 6))/36",
        {"a": "integer", "b": "integer"},
        _sim("dice_sum_equals", n_dice=2, sides=6, target=9),
    ),
    "prob-002": _ref(
        "binomial(5, 2)/binomial(8, 2)",
        sim=_sim("draw_all_of_color", counts={"red": 5, "blue": 3}, k=2, color="red"),
    ),
    "prob-003": _ref("Sum(Rational(5, 6)**(k - 1), (k, 1, oo))", {"k": "integer"}),
    "prob-004": _ref(
        "Sum(binomial(4, j), (j, 2, 4))/2**4",
        {"j": "integer"},
        _sim("coin_heads_at_least", n=4, k=2),
    ),
    "prob-005": _ref("subfactorial(4)/factorial(4)", sim=_sim("derangement", n=4)),
    "prob-006": _ref(
        "(100*Rational(95, 100))/(100*Rational(95, 100) + 9900*Rational(10, 100))",
        sim=_sim("screening_posterior", prevalence=0.01, sensitivity=0.95, false_positive=0.10),
    ),
    "prob-007": _ref(
        "Integral(Integral(u, (v, 0, u)), (u, 0, 1)) + Integral(Integral(v, (u, 0, v)), (v, 0, 1))",
        {"u": "positive", "v": "positive"},
        _sim("max_of_uniforms", k=2),
    ),
    "prob-008": _ref(
        "gamblers_ruin(3, 0, 10)", sim=_sim("walk_hits_upper", start=3, lower=0, upper=10)
    ),
    "stat-001": _ref("25*Rational(1, 25)**2*16"),
    "stat-002": _ref("det(Matrix([[1, 1]])*Matrix([[4, 2], [2, 9]])*Matrix([[1], [1]]))"),
    "stat-003": _ref(
        "Integral(x**2*lam*exp(-lam*x), (x, 0, oo))", {"x": "positive", "lam": "positive"}
    ),
    "stat-004": _ref("solve(diff(10*log(lam) - 5*lam, lam), lam)[0]", {"lam": "positive"}),
    "stat-006": _ref("2*Rational(196, 100)*10/sqrt(100)"),
    "linalg-001": _ref("det(Matrix([[2, 1], [5, 3]]))"),
    "linalg-002": _ref("max_eigenvalue(Matrix([[4, 1], [2, 3]]))"),
    "linalg-003": _ref("rank(Matrix([[1, 2, 3], [2, 4, 6], [1, 0, 1]]))"),
    "linalg-004": _ref("trace(Matrix([[1, 2], [0, 3]])**2)"),
    "linalg-005": _ref("det(Matrix([[a, b], [c, d]])**2)"),
    "linalg-006": _ref("(2*2 + 2*2) - (2*2 - 2*1)**2/(1*1 + 2*2)"),
}
