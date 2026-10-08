"""Hard timeouts for work that cannot be interrupted in-process (SymPy simplify/integrate).

A signal or thread timeout cannot stop a long C-level or pure-Python computation reliably, so
the call runs in a child process that is killed when the deadline passes. The child also gets
a memory cap. ``forkserver`` is used so that the parent's threads (NumPy/BLAS) are never forked.
"""

from __future__ import annotations

import multiprocessing as mp
import resource
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any

DEFAULT_MEMORY_MB = 1024


class CallTimeout(Exception):
    """The child did not finish before the deadline and was killed."""


class CallFailed(Exception):
    """The child raised, ran out of memory, or died without a result."""


def _child(conn: Connection, fn: Callable[..., Any], args: tuple[Any, ...], memory_mb: int) -> None:
    try:
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        conn.send(("ok", fn(*args)))
    except BaseException as exc:  # noqa: BLE001 - report everything to the parent
        try:
            conn.send(("err", f"{type(exc).__name__}: {exc}"[:500]))
        except Exception:  # noqa: BLE001
            pass
    finally:
        conn.close()


def call_with_timeout(
    fn: Callable[..., Any],
    *args: Any,
    timeout: float,
    memory_mb: int = DEFAULT_MEMORY_MB,
) -> Any:
    """Run ``fn(*args)`` in a child process and return its (picklable) result.

    ``fn`` must be a module-level function and ``args``/result must be picklable.
    Raises ``CallTimeout`` after ``timeout`` seconds (the child is killed) and ``CallFailed`` if
    the child raised or died.
    """
    ctx = mp.get_context("forkserver")
    # Children are forked from a server that has already imported SymPy (saves ~0.4s per call).
    ctx.set_forkserver_preload(["sympy", "verify.parsing", "verify.sympy_check"])
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child, fn, args, memory_mb), daemon=True)
    proc.start()
    child.close()
    try:
        if not parent.poll(timeout):
            raise CallTimeout(f"timed out after {timeout:g}s")
        try:
            kind, payload = parent.recv()
        except EOFError as exc:
            raise CallFailed("child died without a result (out of memory or killed)") from exc
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        parent.close()
    if kind == "err":
        raise CallFailed(payload)
    return payload
