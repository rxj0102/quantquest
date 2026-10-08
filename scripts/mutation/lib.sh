# Shared by the driver scripts: M FILE 'SED' TESTS... runs one mutant and tallies the verdicts;
# baseline TESTS... checks once that the tests pass unmutated; summary prints the totals.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
cd "$ROOT" || exit 2
PY=${PYTHON:-$ROOT/.venv/bin/python}
[ -x "$PY" ] || PY=python
CAUGHT=0; SURVIVED=0; INVALID=0

baseline() {
  echo "baseline: $*"
  local out
  if ! out=$(PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q -x -p no:cacheprovider "$@" 2>&1); then
    echo "$out" | tail -5; echo "the tests fail before any mutation; fix them first"; exit 4
  fi
  echo "$out" | tail -1
  export MUTATION_BASELINE_DONE=1
}

M() {
  local line
  line=$("$HERE/mutate.sh" "$@"); local code=$?
  echo "$line"
  case $code in
    0) CAUGHT=$((CAUGHT + 1)) ;;
    1) SURVIVED=$((SURVIVED + 1)) ;;
    3|4) echo "stopping: $line"; exit "$code" ;;
    *) INVALID=$((INVALID + 1)) ;;
  esac
}

summary() {
  echo "mutants: $((CAUGHT + SURVIVED + INVALID))  caught: $CAUGHT  survived: $SURVIVED  invalid or no-op: $INVALID"
  [ "$SURVIVED" -eq 0 ] && [ "$INVALID" -eq 0 ]
}
