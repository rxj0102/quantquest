"""Serializable spec models shared by the verifiers (leaf module: imports nothing from verify)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Assumption = Literal["plain", "positive", "real", "integer", "nonnegative"]


class ExactSpec(BaseModel):
    """An independent exact computation, written as a restricted SymPy expression.

    ``expr`` is parsed by ``verify.parsing.safe_parse(reference=True)`` and evaluated with
    ``doit()``. ``symbols`` declares assumptions for variables (e.g. ``{"x": "positive"}``).
    """

    model_config = ConfigDict(extra="forbid")

    expr: str = Field(min_length=1, max_length=600)
    symbols: dict[str, Assumption] = Field(default_factory=dict)


class SimulatorSpec(BaseModel):
    """A named Monte Carlo simulator from ``verify.simulators.SIMULATORS`` plus its parameters."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
