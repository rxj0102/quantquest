"""Run Python source in a restricted subprocess, and check a reference solution against tests.

WARNING: DO NOT USE THIS FOR USER-SUBMITTED CODE ON A PUBLIC DEPLOYMENT.
It is meant for code produced by our own pipeline (an LLM-written reference solution checked
against generated tests), run on a machine we control. It is a best-effort guard against
*accidents* (infinite loops, memory hogs, stray network calls), not a security boundary against
a determined attacker. Running strangers' code needs a real sandbox (a container or microVM
with seccomp, no shared filesystem, and per-run resource accounting), not this module.

What each run gets
------------------
* a fresh temporary working directory, removed afterwards; source is copied in as ``main.py``
* ``python -I -S -B`` (isolated mode, no site-packages, no ``PYTHON*`` env vars)
* a scrubbed environment (no API keys, tokens or ``HOME``)
* a wall-clock timeout; the whole process group is killed, also on the way out
* ``RLIMIT_CPU``, ``RLIMIT_AS`` (memory), ``RLIMIT_FSIZE`` (max file size), ``RLIMIT_NOFILE``,
  ``RLIMIT_NPROC`` (set after dropping privileges), ``RLIMIT_CORE=0``
* no network: the process runs in a fresh network namespace via ``unshare --net`` (or
  ``unshare --net --map-root-user`` where unprivileged user namespaces are allowed). If neither
  works the run is refused (``SandboxUnavailable``) unless the caller passes
  ``allow_no_netns=True``, which only blocks ``import socket`` and is trivially bypassable
* if the parent is real root, the child drops to uid/gid 65534 (nobody) before running the code;
  if that fails the run is aborted
* stdout/stderr go to files capped in size, so a chatty program cannot exhaust memory

What it does NOT protect against
--------------------------------
* reading files: the code sees the same filesystem as the parent, limited only by file
  permissions (world-readable files such as ``/etc`` and the source tree are readable)
* a hostile solution forging results: in ``check_code`` the solution runs in the same process as
  the harness that prints the results
* syscall-level attacks: there is no seccomp filter, no PID or mount namespace, no chroot
* disk exhaustion beyond ``RLIMIT_FSIZE`` per file (many files in the temp dir are possible)
* CPU or memory use below the limits, noisy-neighbour effects, and kernel or interpreter bugs
* ``RLIMIT_NPROC`` counts processes per user, not per run
* if the parent is not root and user namespaces are unavailable, running at all (it refuses)
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from verify.result import CheckResult, errored, passed_if

NOBODY = 65534
MAX_FILE_BYTES = 1 << 20
MAX_OPEN_FILES = 64
MAX_PROCESSES = 64
_RESULT_MARK = "__QQ_RESULT__"


class SandboxUnavailable(RuntimeError):
    """The network sandbox cannot be set up, so code will not be run."""


class RunResult(BaseModel):
    """What happened when a program ran."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    duration: float
    degraded: bool = False


_BOOTSTRAP = r"""
import os, resource, sys, runpy
wd, cpu, mem, fsize, nofile, nproc, degraded = sys.argv[1:8]
cpu, mem, fsize, nofile, nproc = int(cpu), int(mem), int(fsize), int(nofile), int(nproc)
def lim(which, soft, hard=None):
    resource.setrlimit(which, (soft, soft if hard is None else hard))
lim(resource.RLIMIT_CPU, cpu, cpu + 1)
lim(resource.RLIMIT_AS, mem)
lim(resource.RLIMIT_FSIZE, fsize)
lim(resource.RLIMIT_NOFILE, nofile)
lim(resource.RLIMIT_CORE, 0)
def in_init_userns():
    try:
        return open("/proc/self/uid_map").read().split() == ["0", "0", "4294967295"]
    except OSError:
        return False
if os.getuid() == 0 and in_init_userns():
    try:
        os.setgroups([]); os.setgid(@NOBODY@); os.setuid(@NOBODY@)
    except OSError as exc:
        sys.stderr.write("sandbox: cannot drop privileges: %s\n" % exc); os._exit(97)
    if os.getuid() == 0:
        sys.stderr.write("sandbox: still root after setuid\n"); os._exit(97)
lim(resource.RLIMIT_NPROC, nproc)
if degraded == "1":
    sys.modules["socket"] = None; sys.modules["_socket"] = None
os.chdir(wd)
sys.argv = ["main.py"]
runpy.run_path("main.py", run_name="__main__")
""".replace("@NOBODY@", str(NOBODY))

_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}


@functools.lru_cache(maxsize=1)
def _probe_netns() -> tuple[str, ...] | None:
    for prefix in (("unshare", "--net", "--"), ("unshare", "--net", "--map-root-user", "--")):
        try:
            done = subprocess.run([*prefix, "true"], capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode == 0:
            return prefix
    return None


def _netns_prefix() -> list[str] | None:
    """Command prefix that runs a program in a new network namespace, or None if unavailable."""
    found = _probe_netns()
    return list(found) if found else None


def netns_available() -> bool:
    """True if programs can be run in an isolated network namespace here."""
    return _netns_prefix() is not None


def _read_capped(f: Any, cap: int) -> str:
    f.seek(0)
    return f.read(cap).decode("utf-8", errors="replace")


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_code(
    source: str,
    *,
    stdin: str = "",
    timeout: float = 5.0,
    cpu_seconds: int | None = None,
    memory_mb: int = 256,
    max_output_bytes: int = 65_536,
    allow_no_netns: bool = False,
    extra_files: dict[str, str] | None = None,
) -> RunResult:
    """Run ``source`` as ``main.py`` in the sandbox described in the module docstring.

    Raises ``SandboxUnavailable`` if the network cannot be isolated (and ``allow_no_netns`` is
    false). ``extra_files`` maps file names to text written next to ``main.py``.
    """
    prefix = _netns_prefix()
    degraded = prefix is None
    if degraded and not allow_no_netns:
        raise SandboxUnavailable(
            "cannot create a network namespace (unshare --net); refusing to run code. "
            "Enable unprivileged user namespaces or run as root."
        )
    cpu = cpu_seconds if cpu_seconds is not None else math.ceil(timeout) + 1
    workdir = tempfile.mkdtemp(prefix="qq-run-")
    out = tempfile.TemporaryFile()
    err = tempfile.TemporaryFile()
    proc: subprocess.Popen[bytes] | None = None
    start = time.monotonic()
    timed_out = False
    try:
        files = {"main.py": source, **(extra_files or {})}
        for name, text in files.items():
            path = os.path.join(workdir, os.path.basename(name))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(path, 0o644)
        os.chmod(workdir, 0o755)
        if os.getuid() == 0:
            for entry in [workdir, *(os.path.join(workdir, f) for f in os.listdir(workdir))]:
                os.chown(entry, NOBODY, NOBODY)
        cmd = [
            *(prefix or []),
            getattr(sys, "_base_executable", sys.executable),
            "-I", "-S", "-B", "-c", _BOOTSTRAP,
            workdir, str(cpu), str(memory_mb * 1024 * 1024), str(MAX_FILE_BYTES),
            str(MAX_OPEN_FILES), str(MAX_PROCESSES), "1" if degraded else "0",
        ]  # fmt: skip
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
            env=_ENV,
            cwd="/",
            start_new_session=True,
            close_fds=True,
        )
        try:
            proc.communicate(input=stdin.encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc.pid)
            proc.communicate()
        except BrokenPipeError:
            proc.wait()
        return RunResult(
            stdout=_read_capped(out, max_output_bytes),
            stderr=_read_capped(err, max_output_bytes),
            returncode=proc.returncode,
            timed_out=timed_out,
            duration=time.monotonic() - start,
            degraded=degraded,
        )
    finally:
        if proc is not None:
            _kill_group(proc.pid)
        out.close()
        err.close()
        shutil.rmtree(workdir, ignore_errors=True)


# -- checking a reference solution against tests ---------------------------------------------


class CodeSpec(BaseModel):
    """Tests for a reference solution: call ``entrypoint(*args)`` and compare to ``expected``."""

    model_config = ConfigDict(extra="forbid")

    entrypoint: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    cases: list[tuple[list[Any], Any]] = Field(min_length=1, max_length=200)
    rel_tol: float = Field(default=1e-9, ge=0, allow_inf_nan=False)
    timeout: float = Field(default=10.0, gt=0, le=60)
    memory_mb: int = Field(default=256, ge=32, le=2048)

    @field_validator("cases")
    @classmethod
    def _json_only(cls, cases: list[tuple[list[Any], Any]]) -> list[tuple[list[Any], Any]]:
        json.dumps(cases)  # raises TypeError/ValueError if not JSON-serializable
        return cases


_HARNESS = """
import json, sys
sys.path.insert(0, ".")
spec = json.load(open("spec.json"))
results = []
try:
    import solution
    fn = getattr(solution, spec["entrypoint"])
except BaseException as exc:
    print(@MARK@ + json.dumps({"fatal": type(exc).__name__ + ": " + str(exc)[:200]}))
    raise SystemExit(0)
for args in spec["args"]:
    try:
        value = fn(*args)
        json.dumps(value)
        results.append({"ok": True, "value": value})
    except BaseException as exc:
        results.append({"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:200]})
print(@MARK@ + json.dumps({"results": results}))
""".replace("@MARK@", repr(_RESULT_MARK))


def _equal(got: Any, want: Any, rel_tol: float) -> bool:
    if isinstance(got, bool) or isinstance(want, bool):
        return type(got) is type(want) and got == want
    if isinstance(got, (int, float)) and isinstance(want, (int, float)):
        return math.isclose(got, want, rel_tol=rel_tol, abs_tol=1e-12)
    if isinstance(got, list) and isinstance(want, list):
        return len(got) == len(want) and all(
            _equal(g, w, rel_tol) for g, w in zip(got, want, strict=True)
        )
    if isinstance(got, dict) and isinstance(want, dict):
        return got.keys() == want.keys() and all(_equal(got[k], want[k], rel_tol) for k in want)
    return bool(got == want)


def check_code(solution: str, spec: CodeSpec, *, timeout: float | None = None) -> CheckResult:
    """Run the reference ``solution`` against ``spec.cases`` in the sandbox.

    The expected answers never enter the sandbox: only the arguments do, and results are compared
    here. Wrong results, exceptions, missing entrypoints and timeouts are ``fail``; an
    unavailable sandbox is ``error``.
    """
    method = f"code_runner(cases={len(spec.cases)},timeout={timeout or spec.timeout:g}s)"
    payload = json.dumps({"entrypoint": spec.entrypoint, "args": [c[0] for c in spec.cases]})
    try:
        run = run_code(
            _HARNESS,
            timeout=timeout or spec.timeout,
            memory_mb=spec.memory_mb,
            extra_files={"solution.py": solution, "spec.json": payload},
        )
    except SandboxUnavailable as exc:
        return errored(method, str(exc))
    n = len(spec.cases)
    if run.timed_out:
        return passed_if(
            False, method, f"timed out after {timeout or spec.timeout:g}s", cases=n, failed=n
        )
    line = next(
        (ln for ln in reversed(run.stdout.splitlines()) if ln.startswith(_RESULT_MARK)), None
    )
    if line is None:
        tail = run.stderr.strip().splitlines()[-1:] or [f"exit code {run.returncode}"]
        return passed_if(False, method, f"solution did not run: {tail[0][:200]}", cases=n, failed=n)
    data = json.loads(line[len(_RESULT_MARK) :])
    if "fatal" in data:
        return passed_if(
            False, method, f"could not load {spec.entrypoint}: {data['fatal']}", cases=n, failed=n
        )
    failures = []
    for (args, want), res in zip(spec.cases, data["results"], strict=True):
        if not res["ok"]:
            failures.append(f"{spec.entrypoint}({args}) raised {res['error']}")
        elif not _equal(res["value"], want, spec.rel_tol):
            failures.append(f"{spec.entrypoint}({args}) -> {res['value']!r}, expected {want!r}")
    shown = "; ".join(failures[:3])
    return passed_if(
        not failures,
        method,
        f"{n - len(failures)}/{n} cases passed"
        + (f"; first failures: {shown}" if failures else ""),
        cases=n,
        failed=len(failures),
    )


# -- self check (used by CI) --------------------------------------------------------------------


def selfcheck() -> str:
    """Verify the sandbox really isolates the network. Raises ``SandboxUnavailable`` if not."""
    prefix = _netns_prefix()
    if prefix is None:
        raise SandboxUnavailable("cannot create a network namespace (unshare --net failed)")
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        probe = (
            "import socket\ns = socket.socket(); s.settimeout(2)\n"
            f"try:\n    s.connect(('127.0.0.1', {port})); print('CONNECTED')\n"
            "except OSError:\n    print('blocked')\n"
        )
        run = run_code(probe, timeout=15.0)
    if run.stdout.strip() != "blocked":
        raise SandboxUnavailable(
            f"network is NOT isolated: stdout={run.stdout!r} stderr={run.stderr[-200:]!r}"
        )
    return " ".join(prefix)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m verify.code_runner --selfcheck`` exits non-zero if isolation fails."""
    parser = argparse.ArgumentParser(description="code_runner sandbox utilities")
    parser.add_argument("--selfcheck", action="store_true", help="verify network isolation works")
    args = parser.parse_args(argv)
    if not args.selfcheck:
        parser.print_help()
        return 2
    try:
        how = selfcheck()
    except SandboxUnavailable as exc:
        print(f"SANDBOX SELFCHECK FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"sandbox selfcheck OK (network isolated via: {how})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
