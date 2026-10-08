#!/usr/bin/env bash
# Apply ONE mutation to ONE source file and see whether the named tests notice.
#
#   scripts/mutation/mutate.sh FILE 'SED-EXPRESSION' PYTEST-ARGS...
#   e.g. scripts/mutation/mutate.sh verify/answers.py 's/if False:/if True:/' tests/test_answers.py
#
# Prints one line: CAUGHT (tests failed), SURVIVED (tests still pass: a gap or an equivalent
# mutant), INVALID (pytest did not run the tests, e.g. an import error), or NO-OP (the sed
# expression changed nothing). Exit codes: 0 caught, 1 survived, 2 invalid or no-op,
# 3 another run holds the lock, 4 the tests already fail without the mutation.
# The source file is always restored, even on Ctrl-C.
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT" || exit 2
PY=${PYTHON:-$ROOT/.venv/bin/python}
[ -x "$PY" ] || PY=python
export PYTHONDONTWRITEBYTECODE=1  # a stale .pyc made early mutation verdicts unreliable

f=$1; expr=$2; shift 2

exec 9>"${TMPDIR:-/tmp}/quantquest-mutation.lock"
flock -n 9 || { echo "LOCKED   another mutation run is active; never run two at once"; exit 3; }

clean() { find . -name __pycache__ -not -path "./.venv/*" -prune -exec rm -rf {} + 2>/dev/null; }

if [ "${MUTATION_BASELINE_DONE:-0}" != "1" ]; then
  if ! "$PY" -m pytest -q -x -p no:cacheprovider "$@" >/dev/null 2>&1; then
    echo "BASELINE-FAILS  the tests fail without any mutation: $*"; exit 4
  fi
fi

cp -p "$f" "$f.orig"
restore() { [ -f "$f.orig" ] && mv -f "$f.orig" "$f"; }
trap restore EXIT INT TERM
sed -i "$expr" "$f"
if cmp -s "$f" "$f.orig"; then echo "NO-OP    $f :: $expr"; exit 2; fi
clean
out=$("$PY" -m pytest -q -x -p no:cacheprovider "$@" 2>&1); code=$?
restore; trap - EXIT INT TERM
clean
case $code in
  1) echo "CAUGHT   $f :: $expr"; exit 0 ;;
  0) echo "SURVIVED $f :: $expr"; echo "$out" | tail -3; exit 1 ;;
  *) echo "INVALID  $f :: $expr (pytest exit code $code)"; echo "$out" | tail -3; exit 2 ;;
esac
