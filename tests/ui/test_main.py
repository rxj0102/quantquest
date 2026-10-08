"""The entry point: once-per-process bootstrap, sidebar, and error handling."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import bootstrap as boot
from core import db, progress
from tests.ui.helpers import all_text, configure

MAIN = str(Path(__file__).resolve().parents[2] / "app" / "main.py")


def run_main(timeout: int = 120) -> AppTest:
    return AppTest.from_file(MAIN, default_timeout=timeout).run()


@pytest.fixture
def spy(monkeypatch):
    """Count (and keep the real behaviour of) bootstrap calls."""
    calls: list[tuple] = []
    real = boot.bootstrap

    def counting(db_path, data_dir=boot.DEFAULT_DIR):
        calls.append((db_path, str(data_dir)))
        return real(db_path, data_dir)

    monkeypatch.setattr(boot, "bootstrap", counting)
    return calls


def test_bootstrap_runs_once_per_process_however_many_sessions_and_reruns(
    tmp_path, monkeypatch, spy
) -> None:
    configure(monkeypatch, tmp_path / "qq.db")
    first = run_main()
    assert not first.exception and len(spy) == 1
    first.run()  # a rerun in the same session
    second = run_main()  # a second session (another tab)
    third = run_main()
    assert not second.exception and not third.exception
    assert len(spy) == 1


def test_a_different_database_is_bootstrapped_separately(tmp_path, monkeypatch, spy) -> None:
    configure(monkeypatch, tmp_path / "a.db")
    run_main()
    monkeypatch.setenv("QQ_DB_PATH", str(tmp_path / "b.db"))
    run_main()
    assert len(spy) == 2


def test_first_start_verifies_and_promotes_the_curated_questions(tmp_path, monkeypatch) -> None:
    path = tmp_path / "qq.db"
    configure(monkeypatch, path)
    at = run_main()
    assert not at.exception
    conn = db.connect(path)
    assert len(db.list_playable(conn)) == 19
    conn.close()
    assert "Skill tree" in all_text(at)  # the default page


def test_a_broken_question_file_shows_an_error_instead_of_crashing(tmp_path, monkeypatch) -> None:
    bad = tmp_path / "data"
    bad.mkdir()
    (bad / "q.yaml").write_text("- id: nope\n")
    configure(monkeypatch, tmp_path / "qq.db", QQ_DATA_DIR=str(bad))
    at = run_main()
    assert not at.exception
    assert any("Could not load the question files" in e.value for e in at.error)


def test_a_failed_load_is_not_cached_forever(tmp_path, monkeypatch) -> None:
    """Fixing the files and reloading must work without restarting the process."""
    bad = tmp_path / "data"
    bad.mkdir()
    (bad / "q.yaml").write_text("- id: nope\n")
    configure(monkeypatch, tmp_path / "qq.db", QQ_DATA_DIR=str(bad))
    assert any("Could not load" in e.value for e in run_main().error)
    monkeypatch.setenv("QQ_DATA_DIR", "")  # back to the real data
    assert not run_main().error


def test_sidebar_shows_xp_and_a_new_york_day_streak(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime

    path = tmp_path / "qq.db"
    configure(monkeypatch, path, now=datetime(2026, 1, 15, 5, 0, 1, tzinfo=UTC))  # 00:00:01 NY
    run_main()  # creates and promotes the database
    conn = db.connect(path)
    progress.insert_xp_event(
        conn, "local", "prob-001", "review", 3, datetime(2026, 1, 15, 4, 59, 59, tzinfo=UTC)
    )
    conn.commit()
    conn.close()
    at = run_main()
    metrics = {m.label: m.value for m in at.sidebar.metric}
    assert metrics["XP"] == "3" and metrics["Streak"] == "1 day"  # yesterday's review, still alive
    assert any("America/New_York" in c.value for c in at.sidebar.caption)


def test_the_streak_resets_after_a_full_idle_day(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime

    path = tmp_path / "qq.db"
    configure(monkeypatch, path, now=datetime(2026, 1, 17, 5, 0, 1, tzinfo=UTC))
    run_main()
    conn = db.connect(path)
    progress.insert_xp_event(
        conn, "local", "prob-001", "review", 3, datetime(2026, 1, 15, 4, 59, 59, tzinfo=UTC)
    )
    conn.commit()
    conn.close()
    metrics = {m.label: m.value for m in run_main().sidebar.metric}
    assert metrics["Streak"] == "0 days"


def test_the_script_runs_the_way_streamlit_runs_it(tmp_path) -> None:
    """`streamlit run app/main.py` has app/ (not the repo root) on sys.path and no PYTHONPATH."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(QQ_DB_PATH=str(tmp_path / "qq.db"), QQ_DATA_DIR=str(tmp_path))  # empty data dir
    done = subprocess.run(
        [sys.executable, MAIN], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert "ModuleNotFoundError" not in done.stderr and "ImportError" not in done.stderr, (
        done.stderr
    )
    assert done.returncode == 0, done.stderr
