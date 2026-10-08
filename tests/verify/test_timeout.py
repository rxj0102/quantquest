"""call_with_timeout: hard (process-kill) timeouts for anything that runs SymPy."""

import os
import time

import pytest

from verify.timeout import CallFailed, CallTimeout, call_with_timeout


def _add(a: int, b: int) -> int:
    return a + b


def _spin() -> None:
    while True:
        pass


def _boom() -> None:
    raise ValueError("nope")


def _hog() -> int:
    return len(bytearray(8 * 10**9))


def _pid() -> int:
    return os.getpid()


def test_returns_value() -> None:
    assert call_with_timeout(_add, 2, 3, timeout=10) == 5


def test_runs_in_a_child_process() -> None:
    assert call_with_timeout(_pid, timeout=10) != os.getpid()


def test_hard_timeout_kills_a_cpu_spin() -> None:
    t0 = time.monotonic()
    with pytest.raises(CallTimeout):
        call_with_timeout(_spin, timeout=1.0)
    assert time.monotonic() - t0 < 5


def test_exception_in_child_is_reported() -> None:
    with pytest.raises(CallFailed, match="ValueError"):
        call_with_timeout(_boom, timeout=10)


def test_memory_limit_is_enforced() -> None:
    with pytest.raises(CallFailed):
        call_with_timeout(_hog, timeout=20, memory_mb=512)


def test_parent_unaffected_after_timeouts() -> None:
    with pytest.raises(CallTimeout):
        call_with_timeout(_spin, timeout=0.5)
    assert call_with_timeout(_add, 1, 1, timeout=10) == 2


# --- regression: the parent's __main__ must never be re-executed ----------------------------------


def test_the_parents_main_script_is_not_re_executed_by_checks(tmp_path) -> None:
    """multiprocessing children re-run __main__; under Streamlit that is the whole app script."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    marker = tmp_path / "marker.txt"
    script = tmp_path / "main_script.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(root)!r})\n"
        f"open({str(marker)!r}, 'a').write('x')\n"  # top level, deliberately not guarded
        "from verify.specs import ExactSpec\n"
        "from verify.sympy_check import check_numeric, check_symbolic\n"
        "for _ in range(3):\n"
        "    assert check_numeric('1/9', ExactSpec(expr='1/9')).passed\n"
        "    assert check_symbolic('2/x**2', ExactSpec(expr='2/x**2')).passed\n"
    )
    done = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert marker.read_text() == "x"  # executed exactly once, by the parent


def test_a_function_from_main_is_refused() -> None:
    fn = _add
    fn.__module__ = "__main__"
    try:
        with pytest.raises(ValueError, match="__main__"):
            call_with_timeout(fn, 1, 2, timeout=5)
    finally:
        fn.__module__ = __name__


# --- the worker pool -----------------------------------------------------------------------------------


def test_many_threads_share_the_pool_without_interference() -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(lambda i: call_with_timeout(_add, i, 1, timeout=60), range(48)))
    assert results == [i + 1 for i in range(48)]


def test_a_timeout_in_one_caller_does_not_disturb_the_others() -> None:
    from concurrent.futures import ThreadPoolExecutor

    def slow() -> str:
        try:
            call_with_timeout(_spin, timeout=1.0)
        except CallTimeout:
            return "timeout"
        return "no"

    with ThreadPoolExecutor(max_workers=6) as ex:
        spin = ex.submit(slow)
        adds = [ex.submit(call_with_timeout, _add, i, i, timeout=60) for i in range(10)]
        assert [f.result() for f in adds] == [2 * i for i in range(10)]
        assert spin.result() == "timeout"


def test_workers_are_recycled(monkeypatch) -> None:
    from verify import timeout as mod

    mod.shutdown()
    monkeypatch.setattr(mod, "RECYCLE_AFTER", 2)
    pids = [call_with_timeout(_pid, timeout=30) for _ in range(6)]
    assert len(set(pids)) >= 3 and all(p != os.getpid() for p in pids)
    mod.shutdown()


def test_a_worker_that_was_killed_from_outside_is_replaced() -> None:
    import signal

    pid = call_with_timeout(_pid, timeout=30)
    os.kill(pid, signal.SIGKILL)
    time.sleep(0.2)
    again = call_with_timeout(_pid, timeout=30)
    assert again != pid


def test_a_job_that_raises_does_not_poison_the_worker() -> None:
    pids = set()
    for _ in range(3):
        pids.add(call_with_timeout(_pid, timeout=30))
        with pytest.raises(CallFailed):
            call_with_timeout(_boom, timeout=30)
    assert len(pids) <= 2  # the same warm worker kept serving
