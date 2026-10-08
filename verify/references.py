"""References: the independent evidence a question is verified against.

A ``Reference`` is plain, JSON-serializable data (``model_dump_json``), so a generated question
can carry its own later. It never contains executable code from an untrusted source: the exact
part is a restricted expression (``verify.parsing``), the simulator is a *name* from a fixed
library plus parameters, pricing is a parameter set, and code tests are inputs/outputs.

References for the hand-written core pool live in each question's YAML entry (see
``verify.curated``). They are written independently of the solutions (enumeration, sums,
integrals, linear systems) and are cross-checked against ``scripts/answer_checks`` in the tests.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from verify.code_runner import CodeSpec
from verify.pricing import PricingSpec
from verify.specs import ExactSpec, SimulatorSpec

__all__ = [
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
