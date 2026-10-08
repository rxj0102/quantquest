"""Helpers for driving Streamlit pages with AppTest."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from streamlit.testing.v1 import AppTest

T0 = datetime(2026, 1, 15, 14, 0, 0, tzinfo=UTC)
USER = "local"
_ATTRS = ("value", "label", "body", "text", "help", "placeholder")


def at_time(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _walk(block) -> Iterator[object]:
    for child in block.children.values():
        yield child
        if hasattr(child, "children"):
            yield from _walk(child)


def all_text(at: AppTest) -> str:
    """Every piece of text a user could see on the page, including the sidebar."""
    parts: list[str] = []
    for root in (at.main, at.sidebar):
        for el in _walk(root):
            for attr in _ATTRS:
                v = getattr(el, attr, None)
                if isinstance(v, str):
                    parts.append(v)
            options = getattr(el, "options", None)
            if options:
                parts.extend(str(o) for o in options)
    return "\n".join(parts)


def button(at: AppTest, label: str):
    for b in at.button:
        if b.label == label:
            return b
    raise AssertionError(f"no button {label!r}; have {[b.label for b in at.button]}")


def has_button(at: AppTest, label: str) -> bool:
    return any(b.label == label for b in at.button)


def run_page(name: str, *, timeout: int = 90) -> AppTest:
    """A fresh session (like a new browser tab) showing one page."""
    at = AppTest.from_string(f"from app.views.{name} import page\npage()", default_timeout=timeout)
    return at.run()


def configure(monkeypatch, db_path, now: datetime = T0, **env: str) -> None:
    """Point the app at a database and freeze its clock."""
    from app import runtime

    monkeypatch.setenv("QQ_DB_PATH", str(db_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(runtime, "utc_now", lambda: now)


def set_clock(monkeypatch, now: datetime) -> None:
    from app import runtime

    monkeypatch.setattr(runtime, "utc_now", lambda: now)


def env_has(key: str) -> bool:
    return key in os.environ
