"""Hard timeouts for work that cannot be interrupted in-process (SymPy simplify/integrate).

A signal or thread timeout cannot stop a long C-level or pure-Python computation reliably, so the
call runs in a separate worker process that is killed when the deadline passes. Workers are small
long-lived subprocesses (``verify._worker``) that have already imported SymPy, so a call costs
milliseconds, not the half second an import would. A pool of up to ``MAX_WORKERS`` serves
concurrent callers (several Streamlit sessions). A worker that times out, dies or runs out of
memory is discarded and a fresh one starts on demand; workers are also recycled periodically.

Why not ``multiprocessing``? Its children re-execute the parent's ``__main__``. Under Streamlit
``__main__`` is the page script, so each check would re-run the whole app in the child. Workers
here are started with ``python -I -c`` and never touch the parent's ``__main__``.

Limitation: ``fn`` must be a module-level function (looked up by module and name in the worker),
and arguments and results must be picklable.
"""

from __future__ import annotations

import atexit
import os
import pickle
import queue
import select
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

DEFAULT_MEMORY_MB = 1024
MAX_WORKERS = 4
RECYCLE_AFTER = 200  # jobs per worker
STARTUP_TIMEOUT = 60.0

_BOOT = "import sys; sys.path[:0] = {paths!r}; from verify._worker import serve; serve()"


class CallTimeout(Exception):
    """The worker did not answer before the deadline and was killed."""


class CallFailed(Exception):
    """The job raised, the worker ran out of memory, or the worker died without a result."""


def _read_exact(fd: int, n: int, deadline: float) -> bytes:
    data = b""
    while len(data) < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CallTimeout
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            raise CallTimeout
        chunk = os.read(fd, n - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


class _Worker:
    """One worker subprocess."""

    def __init__(self) -> None:
        paths = [p for p in sys.path if p and os.path.isdir(p)]
        self.jobs = 0
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _BOOT.format(paths=paths)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            close_fds=True,
        )
        assert self.proc.stdout is not None
        try:
            ready = _read_exact(self.proc.stdout.fileno(), 1, time.monotonic() + STARTUP_TIMEOUT)
        except (CallTimeout, EOFError) as exc:
            self.kill()
            raise CallFailed("worker did not start") from exc
        if ready != b"R":
            self.kill()
            raise CallFailed("worker sent a bad handshake")

    def kill(self) -> None:
        """Kill the process and reap it."""
        try:
            self.proc.kill()
        except OSError:
            pass
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            pass
        for pipe in (self.proc.stdin, self.proc.stdout):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass

    def call(self, fn: Callable[..., Any], args: tuple[Any, ...], timeout: float, memory_mb: int):
        """Run one job. Raises CallTimeout (worker killed) or CallFailed (worker unusable)."""
        assert self.proc.stdin is not None and self.proc.stdout is not None
        if fn.__module__ == "__main__":
            raise ValueError("fn must be defined in an importable module, not __main__")
        blob = pickle.dumps((fn.__module__, fn.__qualname__, args, memory_mb))
        deadline = time.monotonic() + timeout
        try:
            self.proc.stdin.write(struct.pack(">Q", len(blob)) + blob)
            fd = self.proc.stdout.fileno()
            size = struct.unpack(">Q", _read_exact(fd, 8, deadline))[0]
            kind, payload = pickle.loads(_read_exact(fd, size, deadline))
        except CallTimeout:
            self.kill()
            raise CallTimeout(f"timed out after {timeout:g}s") from None
        except (EOFError, BrokenPipeError, OSError, struct.error) as exc:
            self.kill()
            raise CallFailed("worker died without a result (out of memory or killed)") from exc
        self.jobs += 1
        if kind == "err":
            raise CallFailed(payload)
        return payload


_idle: queue.LifoQueue[_Worker] = queue.LifoQueue()
_slots = threading.BoundedSemaphore(MAX_WORKERS)
_all: set[_Worker] = set()
_lock = threading.Lock()


def _acquire() -> _Worker:
    _slots.acquire()
    try:
        while True:
            try:
                worker = _idle.get_nowait()
            except queue.Empty:
                break
            if worker.proc.poll() is None:
                return worker
            worker.kill()
            with _lock:
                _all.discard(worker)
        worker = _Worker()
        with _lock:
            _all.add(worker)
        return worker
    except BaseException:
        _slots.release()
        raise


def _release(worker: _Worker, *, healthy: bool) -> None:
    try:
        if healthy and worker.jobs < RECYCLE_AFTER and worker.proc.poll() is None:
            _idle.put(worker)
        else:
            worker.kill()
            with _lock:
                _all.discard(worker)
    finally:
        _slots.release()


def shutdown() -> None:
    """Kill every worker (registered with atexit; also handy in tests)."""
    with _lock:
        workers = list(_all)
        _all.clear()
    while True:
        try:
            _idle.get_nowait()
        except queue.Empty:
            break
    for worker in workers:
        worker.kill()


atexit.register(shutdown)


def call_with_timeout(
    fn: Callable[..., Any],
    *args: Any,
    timeout: float,
    memory_mb: int = DEFAULT_MEMORY_MB,
) -> Any:
    """Run ``fn(*args)`` in a worker process and return its (picklable) result.

    Raises ``CallTimeout`` after ``timeout`` seconds (the worker is killed) and ``CallFailed`` if
    the job raised, ran out of memory, or the worker died.
    """
    worker = _acquire()
    try:
        return worker.call(fn, args, timeout, memory_mb)
    finally:
        # a worker that timed out or died was killed and is not reused; one that merely ran a
        # job that raised is fine
        _release(worker, healthy=worker.proc.poll() is None)
