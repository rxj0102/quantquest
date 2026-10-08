"""code_runner sandbox. Isolation tests FAIL (not skip) when the sandbox is unavailable."""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from verify import code_runner
from verify.code_runner import (
    CodeSpec,
    SandboxUnavailable,
    check_code,
    netns_available,
    run_code,
)


def test_network_namespace_is_available() -> None:
    """Fail loudly: without it the sandbox refuses to run (see docs in code_runner)."""
    assert netns_available(), "cannot create a network namespace; see CI sysctl step"


def test_runs_code_and_captures_output() -> None:
    r = run_code("print(6 * 7)")
    assert r.returncode == 0 and r.stdout.strip() == "42" and not r.timed_out


def test_stdin_is_passed() -> None:
    r = run_code("print(input().upper())", stdin="abc\n")
    assert r.stdout.strip() == "ABC"


def test_runs_in_a_fresh_temp_dir_that_is_removed() -> None:
    r = run_code("import os\nopen('scratch.txt', 'w').write('x')\nprint(os.getcwd())")
    cwd = Path(r.stdout.strip())
    assert str(cwd).startswith(tempfile.gettempdir())
    assert not cwd.exists()
    assert not (Path.cwd() / "scratch.txt").exists()


def test_environment_is_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    monkeypatch.setenv("SOME_TOKEN", "t")
    r = run_code("import os\nprint(sorted(os.environ))")
    assert (
        "sk-test-secret" not in r.stdout and "ANTHROPIC" not in r.stdout and "TOKEN" not in r.stdout
    )


def test_does_not_run_as_root_when_parent_is_root() -> None:
    r = run_code("import os\nprint(os.getuid())")
    if os.getuid() == 0:
        assert r.stdout.strip() != "0"


def test_wall_clock_timeout_kills_a_sleeper() -> None:
    t0 = time.monotonic()
    r = run_code("import time\ntime.sleep(60)", timeout=1.0)
    assert r.timed_out and time.monotonic() - t0 < 6


def test_wall_clock_timeout_kills_a_busy_loop() -> None:
    t0 = time.monotonic()
    r = run_code("while True:\n    pass", timeout=1.0, cpu_seconds=30)
    assert r.timed_out and time.monotonic() - t0 < 6


def test_cpu_limit_kills_before_wall_clock() -> None:
    r = run_code("while True:\n    pass", timeout=20.0, cpu_seconds=1)
    assert not r.timed_out
    assert r.returncode == -signal.SIGXCPU or r.returncode == -signal.SIGKILL


def test_memory_limit() -> None:
    r = run_code("x = bytearray(2 * 10**9)\nprint('allocated')", memory_mb=256)
    # Must be refused by RLIMIT_AS right away, not merely killed later by the wall clock.
    assert not r.timed_out and r.duration < 3
    assert r.returncode != 0 and "allocated" not in r.stdout
    assert "MemoryError" in r.stderr


def test_output_is_capped() -> None:
    code = "import sys\nwhile True:\n    sys.stdout.write('a' * 10000)"
    r = run_code(code, timeout=10.0, max_output_bytes=4096)
    assert len(r.stdout) <= 4096
    # Stopped by the file-size limit (SIGXFSZ), not by waiting for the wall clock.
    assert not r.timed_out and r.returncode != 0 and r.duration < 8


def test_file_size_limit() -> None:
    r = run_code("open('big.bin', 'wb').write(b'x' * 5_000_000)\nprint('written')", timeout=10.0)
    assert not r.timed_out and "written" not in r.stdout and r.returncode != 0


def test_fork_bomb_is_contained() -> None:
    t0 = time.monotonic()
    code = "import os\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        pass"
    r = run_code(code, timeout=2.0)
    assert time.monotonic() - t0 < 15
    assert r.timed_out or r.returncode != 0
    time.sleep(0.5)
    out = subprocess.run(["pgrep", "-f", "runpy"], capture_output=True, text=True).stdout
    assert out.strip() == ""


def test_network_is_blocked_from_inside_the_sandbox() -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        code = (
            "import socket\n"
            f"for host, p in [('127.0.0.1', {port}), ('1.1.1.1', 53), ('8.8.8.8', 443)]:\n"
            "    s = socket.socket(); s.settimeout(2)\n"
            "    try:\n"
            "        s.connect((host, p)); print('CONNECTED', host)\n"
            "    except OSError:\n"
            "        print('blocked', host)\n"
        )
        r = run_code(code, timeout=15.0)
    finally:
        srv.close()
    assert "CONNECTED" not in r.stdout, r.stdout
    assert r.stdout.count("blocked") == 3


def test_refuses_to_run_without_network_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_runner, "_netns_prefix", lambda: None)
    with pytest.raises(SandboxUnavailable):
        run_code("print(1)")
    res = check_code("def f(x):\n    return x", CodeSpec(entrypoint="f", cases=[([1], 1)]))
    assert res.outcome == "error" and not res.passed


def test_degraded_mode_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_runner, "_netns_prefix", lambda: None)
    r = run_code("print(1)", allow_no_netns=True)
    assert r.stdout.strip() == "1" and r.degraded


def test_selfcheck_cli_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = subprocess.run(
        [sys.executable, "-m", "verify.code_runner", "--selfcheck"], capture_output=True, text=True
    )
    assert ok.returncode == 0, ok.stderr
    assert code_runner.main(["--selfcheck"]) == 0
    monkeypatch.setattr(code_runner, "_netns_prefix", lambda: None)
    assert code_runner.main(["--selfcheck"]) == 1


# --- check_code: reference solution vs generated tests ------------------------------------------

SOLUTION = (
    "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n"
)
SPEC = CodeSpec(entrypoint="fib", cases=[([0], 0), ([1], 1), ([2], 1), ([10], 55), ([30], 832040)])


def test_check_code_known_good() -> None:
    res = check_code(SOLUTION, SPEC)
    assert res.passed, res.details
    assert res.data["cases"] == 5 and res.data["failed"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        SOLUTION.replace("range(n)", "range(n - 1)"),  # off by one
        SOLUTION.replace("a, b = 0, 1", "a, b = 1, 1"),  # wrong seed values
        "def fib(n):\n    return n\n",  # plausible but wrong
        "def fib(n):\n    return 55 if n == 10 else 0\n",  # hard-coded
        "def fib(n):\n    raise RuntimeError('x')\n",
        "def fib(n):\n    return 'a'\n",
        "def other(n):\n    return n\n",  # missing entrypoint
        "def fib(n:\n",  # syntax error
    ],
)
def test_check_code_known_bad(bad: str) -> None:
    res = check_code(bad, SPEC)
    assert not res.passed and res.outcome == "fail"


def test_failing_case_is_reported() -> None:
    res = check_code(SOLUTION.replace("range(n)", "range(n - 1)"), SPEC)
    assert res.data["failed"] >= 1 and "fib" in res.details


def test_infinite_loop_solution_fails_by_timeout() -> None:
    res = check_code("def fib(n):\n    while True:\n        pass\n", SPEC, timeout=1.0)
    assert res.outcome == "fail" and "timed out" in res.details


def test_solution_cannot_see_expected_answers() -> None:
    peek = "import os\ndef fib(n):\n    return sorted(os.listdir('.'))\n"
    res = check_code(peek, CodeSpec(entrypoint="fib", cases=[([1], 1)]))
    assert not res.passed


def test_float_results_use_tolerance() -> None:
    spec = CodeSpec(entrypoint="f", cases=[([1], 0.1 + 0.2)], rel_tol=1e-9)
    assert check_code("def f(x):\n    return 0.3\n", spec).passed
    assert not check_code("def f(x):\n    return 0.31\n", spec).passed


def test_spec_validation_and_json_round_trip() -> None:
    assert CodeSpec.model_validate_json(SPEC.model_dump_json()) == SPEC
    with pytest.raises(ValueError):
        CodeSpec(entrypoint="not an identifier!", cases=[([1], 1)])
    with pytest.raises(ValueError):
        CodeSpec(entrypoint="f", cases=[])
