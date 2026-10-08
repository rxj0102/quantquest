"""bootstrap(): idempotent, and safe when several processes start at once."""

import multiprocessing as mp
import sqlite3
import threading
from pathlib import Path

import pytest

from app.bootstrap import BootstrapReport, bootstrap
from core import db
from core.loader import DEFAULT_DIR
from tests.pool import N_CURATED, N_TRUSTED


def counts(path: Path) -> dict:
    conn = db.connect(path)
    try:
        return {
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "questions": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            "trusted": conn.execute(
                "SELECT COUNT(*) FROM questions WHERE status='trusted'"
            ).fetchone()[0],
            "flagged": conn.execute(
                "SELECT COUNT(*) FROM questions WHERE status='flagged'"
            ).fetchone()[0],
            "no_hash": conn.execute(
                "SELECT COUNT(*) FROM questions WHERE reference_hash IS NULL"
            ).fetchone()[0],
        }
    finally:
        conn.close()


def test_bootstrap_loads_verifies_and_promotes(tmp_path: Path) -> None:
    report = bootstrap(str(tmp_path / "qq.db"))
    assert report == BootstrapReport(promoted=N_TRUSTED, flagged=0, unverified=1)
    assert counts(tmp_path / "qq.db") == {
        "integrity": "ok", "questions": N_CURATED, "trusted": N_TRUSTED, "flagged": 0, "no_hash": 0,
    }  # fmt: skip


def test_a_second_bootstrap_changes_nothing(tmp_path: Path) -> None:
    path = str(tmp_path / "qq.db")
    bootstrap(path)
    again = bootstrap(path)
    assert (again.promoted, again.flagged, again.error) == (0, 0, None)
    assert counts(Path(path))["trusted"] == N_TRUSTED


def test_an_unreadable_data_directory_is_reported_not_raised(tmp_path: Path) -> None:
    bad = tmp_path / "data"
    bad.mkdir()
    (bad / "q.yaml").write_text("- not: valid\n")
    report = bootstrap(str(tmp_path / "qq.db"), bad)
    assert report.error and "q.yaml" in report.error and report.promoted == 0


def test_bootstrap_resumes_after_a_crash_between_loading_and_promoting(tmp_path: Path) -> None:
    from verify.curated import hashes_by_id, load_curated

    path = tmp_path / "qq.db"
    conn = db.connect(path)
    entries = load_curated(DEFAULT_DIR)
    db.upsert_questions(conn, [e.question for e in entries], hashes_by_id(entries))
    conn.close()  # "crashed" before promote_curated
    assert counts(path)["trusted"] == 0
    assert bootstrap(str(path)).promoted == N_TRUSTED


def test_bootstrap_flags_but_never_exposes_a_failing_question(tmp_path: Path) -> None:
    import shutil

    data = tmp_path / "curated"
    shutil.copytree(DEFAULT_DIR, data)
    f = data / "statistics.yaml"
    f.write_text(f.read_text().replace('answer: "17"', 'answer: "18"'))
    path = tmp_path / "qq.db"
    report = bootstrap(str(path), data)
    assert report.flagged == 1 and report.promoted == N_TRUSTED - 1
    conn = db.connect(path)
    assert db.get_playable(conn, "stat-002") is None
    assert len(db.list_playable(conn)) == N_TRUSTED - 1
    conn.close()


# --- several starters at once -------------------------------------------------------------------------------


def _start(path: str, barrier, out) -> None:
    try:
        barrier.wait(60)
        report = bootstrap(path)
        out.put(("ok", report.promoted, report.flagged, report.error))
    except Exception as exc:  # noqa: BLE001
        out.put(("error", type(exc).__name__, str(exc)))


def test_concurrent_process_starts_do_not_corrupt_the_database(tmp_path: Path) -> None:
    ctx = mp.get_context("spawn")
    path = str(tmp_path / "qq.db")
    n = 4
    barrier, out = ctx.Barrier(n), ctx.Queue()
    procs = [ctx.Process(target=_start, args=(path, barrier, out)) for _ in range(n)]
    [p.start() for p in procs]
    results = [out.get(timeout=300) for _ in procs]
    [p.join(60) for p in procs]
    assert all(r[0] == "ok" for r in results), results
    assert all(r[2] == 0 and r[3] is None for r in results), results  # nothing flagged, no errors
    assert sum(r[1] for r in results) >= N_TRUSTED  # between them every question got promoted
    assert counts(Path(path)) == {
        "integrity": "ok", "questions": N_CURATED, "trusted": N_TRUSTED, "flagged": 0, "no_hash": 0,
    }  # fmt: skip
    check = db.connect(path)
    ids = [r[0] for r in check.execute("SELECT id FROM questions")]
    check.close()
    assert len(ids) == len(set(ids)) == N_CURATED


def test_concurrent_thread_starts_in_one_process(tmp_path: Path) -> None:
    path = str(tmp_path / "qq.db")
    barrier = threading.Barrier(4)
    results, errors = [], []

    def run() -> None:
        try:
            barrier.wait(60)
            results.append(bootstrap(path))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(300) for t in threads]
    assert not errors, errors
    assert all(r.error is None and r.flagged == 0 for r in results)
    assert counts(Path(path))["trusted"] == N_TRUSTED


def _migrate(path: str, barrier, out) -> None:
    try:
        barrier.wait(60)
        conn = db.connect(path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
        conn.close()
        out.put(("ok", sorted(cols)))
    except Exception as exc:  # noqa: BLE001
        out.put(("error", type(exc).__name__, str(exc)))


def test_concurrent_migration_of_an_old_database(tmp_path: Path) -> None:
    """A database from before M4 (no reference_hash / answer_tolerance), opened by 8 processes."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE questions (
            id TEXT PRIMARY KEY, topic TEXT NOT NULL, node_id TEXT NOT NULL,
            difficulty INTEGER NOT NULL, format TEXT NOT NULL, prompt_md TEXT NOT NULL,
            answer TEXT NOT NULL, answer_type TEXT NOT NULL, solution_md TEXT NOT NULL,
            verification TEXT NOT NULL, source TEXT NOT NULL, source_kind TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, solve_stats TEXT NOT NULL);
        INSERT INTO questions VALUES ('prob-001','probability','prob.counting',1,'probability',
            'p','1/9','numeric','s','{"method":"none","result":"unverified","details":""}',
            'curated','curated','fresh','2026-01-01T00:00:00+00:00','{"attempts":3,"correct":2}');
        """
    )
    old.commit()
    old.close()
    ctx = mp.get_context("spawn")
    barrier, out = ctx.Barrier(8), ctx.Queue()
    procs = [ctx.Process(target=_migrate, args=(str(path), barrier, out)) for _ in range(8)]
    [p.start() for p in procs]
    results = [out.get(timeout=120) for _ in procs]
    [p.join(30) for p in procs]
    assert all(r[0] == "ok" for r in results), results
    assert all({"reference_hash", "answer_tolerance"} <= set(r[1]) for r in results)
    conn = db.connect(path)
    row = conn.execute("SELECT solve_stats, created_at FROM questions").fetchone()
    conn.close()
    assert row["solve_stats"] == '{"attempts":3,"correct":2}'  # data survived the migration
    assert pytest is not None
