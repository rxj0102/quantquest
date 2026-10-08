"""Typed question schema (CLAUDE.md "Question schema")."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = re.compile(r"^[a-z]{2,8}-\d{3,}$")
SOURCE_PATTERN = re.compile(r"^(curated|generated|paper:https?://\S+)$")


class Format(StrEnum):
    MENTAL_MATH = "mental_math"
    PROBABILITY = "probability"
    DERIVATION = "derivation"
    CODING = "coding"
    CASE = "case"


class AnswerType(StrEnum):
    NUMERIC = "numeric"
    SYMBOLIC = "symbolic"
    TEXT = "text"
    CODE = "code"


class Status(StrEnum):
    FRESH = "fresh"
    TRUSTED = "trusted"
    FLAGGED = "flagged"
    RETIRED = "retired"


class Verification(BaseModel):
    """Outcome of code verification. ``unverified`` means no code check has run."""

    model_config = ConfigDict(extra="forbid")

    method: str = "none"
    result: Literal["pass", "fail", "unverified"] = "unverified"
    details: str = ""


class SolveStats(BaseModel):
    """Aggregate user solve statistics for a question."""

    model_config = ConfigDict(extra="forbid")

    attempts: int = Field(default=0, ge=0)
    correct: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _correct_le_attempts(self) -> SolveStats:
        if self.correct > self.attempts:
            raise ValueError("correct cannot exceed attempts")
        return self


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Question(BaseModel):
    """One interview question. ``answer`` is always stored as a string."""

    model_config = ConfigDict(extra="forbid")

    id: str
    topic: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    difficulty: Annotated[int, Field(ge=1, le=5)]
    format: Format
    prompt_md: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    answer_type: AnswerType
    solution_md: str = Field(min_length=1)
    verification: Verification = Field(default_factory=Verification)
    source: str
    status: Status = Status.FRESH
    created_at: datetime = Field(default_factory=_utcnow)
    solve_stats: SolveStats = Field(default_factory=SolveStats)
    # Relative tolerance for numeric answers typed in the UI. None = the app default (exact for
    # integer answers). Not evidence: verification and promotion never read it.
    answer_tolerance: float | None = Field(default=None, ge=0, le=1)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not ID_PATTERN.match(v):
            raise ValueError(
                f"id {v!r} must look like 'prob-001' (lowercase prefix, dash, 3+ digits)"
            )
        return v

    @field_validator("source")
    @classmethod
    def _check_source(cls, v: str) -> str:
        if not SOURCE_PATTERN.match(v):
            raise ValueError(f"source {v!r} must be 'curated', 'generated' or 'paper:<URL>'")
        return v

    @field_validator("created_at")
    @classmethod
    def _require_tz(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @model_validator(mode="after")
    def _trusted_needs_verification(self) -> Question:
        """CLAUDE.md rules 2 and 3: only code-verified answers can be trusted.

        Text answers have no code verifier, so they can never be trusted, whatever their
        verification record says. Numeric, symbolic and code answers need a passing check.
        """
        if self.status is not Status.TRUSTED:
            return self
        if self.answer_type is AnswerType.TEXT:
            raise ValueError("a text-answer question can never be trusted (it cannot be verified)")
        if self.verification.result != "pass":
            raise ValueError(
                "a trusted numeric/symbolic/code question needs verification.result == 'pass'"
            )
        return self

    @property
    def source_kind(self) -> Literal["curated", "generated", "paper"]:
        """Which pool the question belongs to (curated core vs AI feed vs ingested papers)."""
        return self.source.split(":", 1)[0]  # type: ignore[return-value]
