"""verify.timeout: pool size, recycling, deadlines and pipe handling, with no timing assertions.

Written for the survivors of scripts/mutation/timeout.sh. Where a broken implementation would
hang (a read that waits forever, a leaked slot), the test is arranged so that it fails at once
instead: non-blocking pipes, and counting free slots rather than waiting for a blocked caller.
Run this file first in the mutation list (the harness stops at the first failure).
"""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.verify import timeout_jobs as jobs
from verify import timeout as mod
from verify.timeout import CallFailed, CallTimeout, call_with_timeout

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_WORKERS = 4  # the documented pool size; deliberately not read from the module under test


@pytest.fixture(autouse=True)
def _clean_pool():
    mod.shutdown()
    yield
    mod.shutdown()


@pytest.fixture
def pipe():
    """A non-blocking pipe: a read that should not happen fails with BlockingIOError, not a hang."""
    r, w = os.pipe()
    os.set_blocking(r, False)
    try:
        yield r, w
    finally:
        for fd in (r, w):
            try:
                os.close(fd)
            except OSError:
                pass


def _free_slots() -> int:
    """How many worker slots are free right now (takes and gives them back)."""
    got = 0
    while mod._slots.acquire(blocking=False):
        got += 1
    for _ in range(got):
        mod._slots.release()
    return got


# --- _read_exact ---------------------------------------------------------------------------------


def test_read_exact_times_out_when_nothing_arrives(pipe) -> None:
    r, _ = pipe
    with pytest.raises(CallTimeout):
        mod._read_exact(r, 1, time.monotonic() + 0.05)


def test_read_exact_times_out_with_partial_data(pipe) -> None:
    r, w = pipe
    os.write(w, b"ab")
    with pytest.raises(CallTimeout):
        mod._read_exact(r, 3, time.monotonic() + 0.05)


@pytest.mark.parametrize("past", [0.0, 1.0, 1e10])
def test_read_exact_with_an_expired_deadline_is_a_timeout_and_reads_nothing(pipe, past) -> None:
    r, w = pipe
    os.write(w, b"x")
    with pytest.raises(CallTimeout):
        mod._read_exact(r, 1, time.monotonic() - past)
    assert os.read(r, 1) == b"x"


def test_read_exact_reports_eof(pipe) -> None:
    r, w = pipe
    os.write(w, b"ab")
    os.close(w)
    with pytest.raises(EOFError):
        mod._read_exact(r, 5, time.monotonic() + 0.2)


def test_read_exact_joins_chunks(pipe, monkeypatch) -> None:
    r, w = pipe
    real_read = os.read
    one_byte = SimpleNamespace(read=lambda fd, n: real_read(fd, 1))
    os.write(w, b"abcd")
    monkeypatch.setattr(mod, "os", one_byte)
    assert mod._read_exact(r, 4, time.monotonic() + 0.2) == b"abcd"


# --- slots (kept ahead of the other call-based tests: a leaked slot would hang the later ones) ---


def test_every_way_a_call_can_end_gives_its_slot_back() -> None:
    before = _free_slots()
    assert before > 0
    assert call_with_timeout(jobs.add, 1, 2, timeout=60) == 3
    assert _free_slots() == before
    with pytest.raises(CallTimeout):
        call_with_timeout(jobs.add, 1, 2, timeout=0)
    assert _free_slots() == before
    with pytest.raises(CallFailed):
        call_with_timeout(jobs.die, timeout=60)
    assert _free_slots() == before


# --- deadlines and dying workers through the public call ----------------------------------------


@pytest.mark.parametrize("timeout", [0, -1])
def test_a_deadline_that_has_already_passed_is_a_timeout(timeout) -> None:
    with pytest.raises(CallTimeout):
        call_with_timeout(jobs.add, 1, 2, timeout=timeout)
    assert call_with_timeout(jobs.add, 1, 2, timeout=60) == 3


def test_a_worker_that_dies_mid_call_is_a_failure_not_a_timeout() -> None:
    first = call_with_timeout(jobs.pid, timeout=60)
    with pytest.raises(CallFailed, match="died"):
        call_with_timeout(jobs.die, timeout=60)
    assert call_with_timeout(jobs.pid, timeout=60) != first


def test_a_large_result_arrives_intact() -> None:
    assert len(call_with_timeout(jobs.big, 1_000_000, timeout=60)) == 1_000_000


# --- starting a worker -------------------------------------------------------------------------


def test_a_bad_first_byte_from_the_worker_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "_BOOT",
        "import sys; sys.stdout.buffer.write(b'X'); sys.stdout.buffer.flush(); input()",
    )
    with pytest.raises(CallFailed, match="handshake"):
        call_with_timeout(jobs.add, 1, 2, timeout=5)


def test_a_worker_that_exits_at_startup_is_a_failure(monkeypatch) -> None:
    monkeypatch.setattr(mod, "_BOOT", "pass")
    monkeypatch.setattr(mod, "STARTUP_TIMEOUT", 10.0)
    with pytest.raises(CallFailed, match="did not start"):
        call_with_timeout(jobs.add, 1, 2, timeout=5)


def test_a_failed_start_gives_its_slot_back(monkeypatch) -> None:
    before = _free_slots()
    monkeypatch.setattr(mod, "_BOOT", "pass")
    monkeypatch.setattr(mod, "STARTUP_TIMEOUT", 10.0)
    for _ in range(3):
        with pytest.raises(CallFailed):
            call_with_timeout(jobs.add, 1, 2, timeout=5)
        assert _free_slots() == before


def test_workers_run_in_isolated_mode() -> None:
    assert call_with_timeout(jobs.isolated, timeout=60) == 1


# --- pool size and recycling --------------------------------------------------------------------


def test_only_four_workers_start_for_concurrent_jobs(tmp_path) -> None:
    results: list[int] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(call_with_timeout(jobs.hold, str(tmp_path), timeout=120))
        except BaseException as exc:  # noqa: BLE001 - reported by the assertions below
            errors.append(exc)

    threads = [threading.Thread(target=run, daemon=True) for _ in range(EXPECTED_WORKERS + 2)]
    for t in threads:
        t.start()
    try:
        guard = time.monotonic() + 30  # a hang guard, not a measurement
        while len(list(tmp_path.glob("started-*"))) < EXPECTED_WORKERS and not errors:
            assert time.monotonic() < guard, "fewer workers started than the pool should allow"
            time.sleep(0.02)
        # all four are busy; the other two callers are queued on the semaphore, not starting
        assert not errors
        assert _free_slots() == 0
        assert len(mod._all) == EXPECTED_WORKERS
    finally:
        (tmp_path / "go").touch()
        for t in threads:
            t.join(60)
    assert not errors
    assert len(results) == EXPECTED_WORKERS + 2
    assert len(set(results)) <= EXPECTED_WORKERS


def test_the_default_recycling_threshold_replaces_a_worker_after_200_jobs() -> None:
    pids = [call_with_timeout(jobs.pid, timeout=60) for _ in range(201)]
    assert len(set(pids[:200])) == 1
    assert pids[200] != pids[0]


# --- shutdown at interpreter exit --------------------------------------------------------------

_EXIT_SCRIPT = """
import sys, threading, time
from pathlib import Path
from tests.verify import timeout_jobs as jobs
from verify import timeout as mod

flag = sys.argv[1]
t = threading.Thread(
    target=lambda: mod.call_with_timeout(jobs.signal_then_spin, flag, timeout=600), daemon=True
)
t.start()
while not Path(flag).exists():
    time.sleep(0.01)
print(next(iter(mod._all)).proc.pid, flush=True)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_workers_are_killed_when_the_parent_exits_mid_job(tmp_path) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run(
        [sys.executable, "-c", _EXIT_SCRIPT, str(tmp_path / "running")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    pid = int(done.stdout.split()[-1])
    try:
        assert not _alive(pid), "the worker outlived its parent"
    finally:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)
