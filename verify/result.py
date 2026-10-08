"""Common result type for every verifier."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CheckResult(BaseModel):
    """Outcome of one check.

    ``fail`` means the claimed answer is wrong. ``error`` means the check could not be completed
    (bad input, timeout, sandbox unavailable): it certifies nothing and never counts as a pass.
    """

    model_config = ConfigDict(frozen=True)

    outcome: Literal["pass", "fail", "error"]
    method: str
    details: str = ""
    data: dict[str, float | int | str] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """True only for a definite pass."""
        return self.outcome == "pass"


def passed_if(ok: bool, method: str, details: str, **data: float | int | str) -> CheckResult:
    """Build a pass/fail result."""
    return CheckResult(outcome="pass" if ok else "fail", method=method, details=details, data=data)


def errored(method: str, details: str, **data: float | int | str) -> CheckResult:
    """Build an error result (certifies nothing)."""
    return CheckResult(outcome="error", method=method, details=details, data=data)
