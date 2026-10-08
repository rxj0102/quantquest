"""Module-level jobs for the worker-pool tests (a worker imports the function by module name)."""

import os
import sys
import time
from pathlib import Path


def pid() -> int:
    return os.getpid()


def add(a: int, b: int) -> int:
    return a + b


def isolated() -> int:
    return sys.flags.isolated


def die() -> None:
    os._exit(3)


def big(n: int) -> bytes:
    return b"x" * n


def hold(directory: str) -> int:
    """Record that this worker started, then wait until the test creates ``go``."""
    d = Path(directory)
    (d / f"started-{os.getpid()}").touch()
    while not (d / "go").exists():
        time.sleep(0.01)
    return os.getpid()


def signal_then_spin(path: str) -> None:
    """Tell the test the job is running inside the worker, then never return."""
    Path(path).touch()
    while True:
        pass
