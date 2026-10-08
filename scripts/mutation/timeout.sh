#!/usr/bin/env bash
# Mutation run for verify/timeout.py (the hard-timeout worker pool).
# Tests run: the module's own tests and the exact/symbolic checks that depend on it.
# Run it alone: it edits source files temporarily. See scripts/mutation/README.md.
# Mutants that remove a kill or a slot release can hang the tests; the harness times them out.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
F=verify/timeout.py
T="tests/verify/test_timeout.py tests/verify/test_sympy_check.py"
# shellcheck disable=SC2086
baseline $T
# shellcheck disable=SC2086
P() { M "$F" "$1" $T; }
# --- constants
P 's|^MAX_WORKERS = 4|MAX_WORKERS = 1|'
P 's|^RECYCLE_AFTER = 200|RECYCLE_AFTER = 10**9|'
P 's|^STARTUP_TIMEOUT = 60.0|STARTUP_TIMEOUT = 0.0001|'
P 's|^_slots = threading.BoundedSemaphore(MAX_WORKERS)|_slots = threading.BoundedSemaphore(1000)|'
# --- reading the worker's answer
P '54s|            raise CallTimeout|            pass|'
P 's|        if remaining <= 0:|        if remaining < -1e9:|'
P 's|        if not ready:|        if False:|'
P 's|        if not chunk:|        if False:|'
P 's|        data += chunk|        data = chunk|'
# --- starting a worker
P 's|        if ready != b"R":|        if False:|'
P 's|\[sys.executable, "-I", "-c"|[sys.executable, "-c"|'
P 's|            stderr=subprocess.DEVNULL,|            stderr=None,|'
P 's|close_fds=True|close_fds=False|'
# --- killing
P 's|            self.proc.kill()|            pass|'
P 's|            self.proc.wait(5)|            pass|'
P '119s|self.kill()|pass|'
P '122s|self.kill()|pass|'
# --- running a job
P 's|        if fn.__module__ == "__main__":|        if False:|'
P 's|        self.jobs += 1|        pass|'
P 's|        if kind == "err":|        if False:|'
P 's|^        return payload|        return None|'
# --- the pool
P 's|            if worker.proc.poll() is None:|            if True:|'
P 's|        if healthy and worker.jobs < RECYCLE_AFTER and worker.proc.poll() is None:|        if worker.jobs < RECYCLE_AFTER:|'
P 's|        _release(worker, healthy=worker.proc.poll() is None)|        _release(worker, healthy=True)|'
P '167s|_slots.release()|pass|'
P 's|    with _lock:|    if True:|'
P 's|    while True:|    while False:|'
P 's|^atexit.register(shutdown)|pass|'
summary
