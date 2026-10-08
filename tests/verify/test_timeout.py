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
