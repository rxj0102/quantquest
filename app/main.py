"""QuantQuest entry point: ``streamlit run app/main.py``."""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run app/main.py` puts app/ on sys.path, not the repository root, so make the root
# importable before anything from app/, core/ or verify/ is imported.
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st  # noqa: E402

from app import bootstrap as boot  # noqa: E402
from app import runtime  # noqa: E402
from app.views import interview, practice, skill_tree  # noqa: E402
from core import progress  # noqa: E402


@st.cache_resource(show_spinner="Verifying questions (first start only)...")
def ensure_ready(db_path: str, data_dir: str) -> boot.BootstrapReport:
    """Load and verify the curated questions once per process, not once per page view."""
    return boot.bootstrap(db_path, Path(data_dir))


def sidebar(settings) -> None:
    """XP and streak, shown on every page."""
    now = runtime.utc_now()
    with runtime.open_db(settings) as conn:
        total = progress.total_xp(conn, settings.user_id)
        today = progress.xp_on_day(
            conn, settings.user_id, progress.local_day(now, settings.tz), settings.tz
        )
        s = progress.streak(conn, settings.user_id, now, settings.tz)
    st.sidebar.metric("XP", total, delta=f"+{today} today" if today else None)
    st.sidebar.metric("Streak", f"{s.current} day{'s' if s.current != 1 else ''}")
    st.sidebar.caption(
        f"Longest streak: {s.longest} day{'s' if s.longest != 1 else ''}. "
        f"Days are counted in {settings.timezone}."
    )


def main() -> None:
    """Set up the page, prepare the database once, and run the selected page."""
    st.set_page_config(page_title="QuantQuest", page_icon="🎯", layout="wide")
    settings = runtime.get_settings()
    report = ensure_ready(settings.db_path, str(runtime.data_dir(settings)))
    if report.error:
        st.error(f"Could not load the question files: {report.error}")
        st.stop()
    sidebar(settings)
    pages = [
        st.Page(
            skill_tree.page, title="Skill tree", icon="🌳", url_path="skill-tree", default=True
        ),
        st.Page(practice.page, title="Practice", icon="📝", url_path="practice"),
        st.Page(interview.page, title="Interview", icon="⏱️", url_path="interview"),
    ]
    st.navigation(pages).run()


main()
