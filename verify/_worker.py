"""Worker process for ``verify.timeout``: runs jobs sent by the parent, one at a time.

Started as ``python -I -c "...; from verify._worker import serve; serve()"``. It never imports or
re-executes the parent's ``__main__`` (which is what ``multiprocessing`` does, and which is wrong
when the parent is a Streamlit page script).

Protocol on the pipes: every message is an 8-byte big-endian length followed by a pickle.
Worker -> parent: the single byte ``b"R"`` once ready, then one reply per request,
``("ok", result)`` or ``("err", "ExceptionName: message")``.
Parent -> worker: ``(module, qualname, args, memory_mb)``.
"""

from __future__ import annotations

import functools
import importlib
import os
import pickle
import struct
import sys


def _read_exact(stream, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            return b""
        data += chunk
    return data


def serve() -> None:
    """Serve jobs until the parent closes the pipe."""
    import resource

    out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)  # anything a job prints goes to stderr, never into the protocol stream
    inp = sys.stdin.buffer
    import sympy  # noqa: F401  # warm start: the first real job should not pay for the import

    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    out.write(b"R")
    while True:
        header = _read_exact(inp, 8)
        if not header:
            return
        blob = _read_exact(inp, struct.unpack(">Q", header)[0])
        try:
            module, qualname, args, memory_mb = pickle.loads(blob)
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
            fn = functools.reduce(getattr, qualname.split("."), importlib.import_module(module))
            reply: tuple[str, object] = ("ok", fn(*args))
        except BaseException as exc:  # noqa: BLE001 - report everything to the parent
            reply = ("err", f"{type(exc).__name__}: {exc}"[:500])
        finally:
            try:
                resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
            except (ValueError, OSError):
                pass
        try:
            data = pickle.dumps(reply)
        except Exception:  # noqa: BLE001
            data = pickle.dumps(("err", "result could not be pickled"))
        out.write(struct.pack(">Q", len(data)) + data)
