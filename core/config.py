"""Application settings, read from environment variables with safe defaults.

The "day" for streaks and daily XP is a calendar day in ``timezone`` (default America/New_York).
``ui_numeric_rel_tol`` is the relative tolerance for numeric answers *typed in the UI*; it is
not used by verification or promotion, which keep their own strict tolerance (see
``verify.sympy_check``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    """A setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Validated settings. Construct directly in tests, or use ``load_settings``."""

    timezone: str = "America/New_York"
    db_path: str = "quantquest.db"
    data_dir: str = ""  # curated YAML directory; empty = the repository default
    user_id: str = "local"
    ui_numeric_rel_tol: float = 1e-3
    session_max_cards: int = 20
    session_max_new: int = 10
    interview_questions: int = 5
    interview_minutes: int = 20
    answer_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
            raise ConfigError(f"unknown timezone {self.timezone!r}") from exc
        if not self.user_id or len(self.user_id) > 64:
            raise ConfigError("user_id must be 1 to 64 characters")
        if not self.db_path:
            raise ConfigError("db_path must not be empty")
        if not 0 <= self.ui_numeric_rel_tol < 1:
            raise ConfigError("ui_numeric_rel_tol must be in [0, 1)")
        if self.session_max_cards < 1 or self.session_max_new < 0:
            raise ConfigError("session_max_cards must be >= 1 and session_max_new >= 0")
        if self.interview_questions < 1 or self.interview_minutes < 1:
            raise ConfigError("interview_questions and interview_minutes must be >= 1")
        if self.answer_timeout_seconds <= 0:
            raise ConfigError("answer_timeout_seconds must be > 0")

    @property
    def tz(self) -> ZoneInfo:
        """The configured timezone."""
        return ZoneInfo(self.timezone)


def _number(env: Mapping[str, str], key: str, cast: type) -> object:
    raw = env[key]
    try:
        return cast(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}={raw!r} is not a valid {cast.__name__}") from exc


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build ``Settings`` from ``QQ_*`` environment variables (``os.environ`` by default)."""
    env = os.environ if env is None else env
    kwargs: dict[str, object] = {}
    for key, field, cast in (
        ("QQ_TIMEZONE", "timezone", str),
        ("QQ_DB_PATH", "db_path", str),
        ("QQ_DATA_DIR", "data_dir", str),
        ("QQ_USER_ID", "user_id", str),
        ("QQ_UI_REL_TOL", "ui_numeric_rel_tol", float),
        ("QQ_SESSION_MAX_CARDS", "session_max_cards", int),
        ("QQ_SESSION_MAX_NEW", "session_max_new", int),
        ("QQ_INTERVIEW_QUESTIONS", "interview_questions", int),
        ("QQ_INTERVIEW_MINUTES", "interview_minutes", int),
        ("QQ_ANSWER_TIMEOUT", "answer_timeout_seconds", float),
    ):
        if key in env:
            kwargs[field] = _number(env, key, cast)
    return Settings(**kwargs)  # type: ignore[arg-type]
