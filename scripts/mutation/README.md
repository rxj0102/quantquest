# Mutation testing harness

A mutant is a copy of the code with one small deliberate bug. If the tests still pass, they would
not have noticed that bug. We use this to check that the tests around the trust boundary
(`verify/`, answer checking, visibility rules, scoring) really fail when the code is wrong.

## Run it

From the repository root, with the project virtualenv in place (`.venv`, or set `PYTHON`):

```
scripts/mutation/dice_event.sh     # the dice_event simulator (28 mutants, about a minute)
scripts/mutation/m4.sh             # the M4 logic: answers, XP and streaks, practice, interview,
                                   # visibility, reference hash, worker pool (28 mutants)
scripts/mutation/parsing.sh        # verify/parsing.py, the expression parser (37 mutants)
scripts/mutation/timeout.sh        # verify/timeout.py, the worker pool (28 mutants; a mutant that hangs
                                   # is stopped by MUTATION_TIMEOUT)
scripts/mutation/pricing.sh        # verify/pricing.py, Black-Scholes and the tree (34 mutants)
scripts/mutation/mutate.sh FILE 'SED-EXPRESSION' PYTEST-ARGS...    # one mutant by hand
```

Example of one mutant by hand:

```
scripts/mutation/mutate.sh verify/answers.py \
  's/abs_tol=0.0 if tolerance == 0.0 else 1e-12/abs_tol=1e-12/' tests/test_answers.py
```

## What the verdicts mean

| line starts with | meaning |
|---|---|
| `CAUGHT` | the tests failed with the bug in: good. A mutant that makes the tests hang is stopped after `MUTATION_TIMEOUT` seconds (default 300) and also counted as caught, with `(the tests hung ...)` in the line, because a hung test run would fail CI too. Keep the limit well above the normal run time, or a slow but passing run would be miscounted as caught |
| `SURVIVED` | the tests still passed: either a gap in the tests, or an equivalent mutant (the change does not alter behaviour). Read the mutant and decide; do not assume either |
| `INVALID` | pytest did not run the tests properly (an import error, a syntax error in the mutated file). Not a verdict |
| `NO-OP` | the sed expression changed nothing (the code it matched has moved). Fix the expression |
| `BASELINE-FAILS` | the tests already fail with no mutation, so no verdict would mean anything. Fix them first |
| `LOCKED` | another mutation run is active |

Each driver script ends with a line such as `mutants: 28  caught: 27  survived: 1  invalid or
no-op: 0` and exits non-zero if anything survived or was invalid.

## Rules

- **Run one at a time, and run nothing else in the repository meanwhile.** The harness edits the
  real source file for the length of each mutant and restores it afterwards (also on Ctrl-C). A
  lock in `/tmp` stops two harness runs overlapping, but it cannot stop an unrelated pytest run or
  a commit from seeing a mutated file. Do not commit while a run is going.
- It disables Python bytecode caching and clears `__pycache__` around each mutant. An early run
  without this reported wrong verdicts because a stale `.pyc` hid the edit.
- The baseline check runs the named tests once before any mutation. An earlier version of this
  harness had no baseline and no virtualenv, so an import error was reported as `SURVIVED`.

## Adding a mutant

Add a line to a driver script: `M FILE 'sed-expression' tests...` (in `dice_event.sh` the file and
tests are fixed, so the line is `D 'sed-expression'`). Prefer mutants that flip one comparison,
constant, boundary or branch. A mutation that needs a multi-line edit cannot be written as a
single-line `sed` expression and will be reported as `NO-OP`.

## Latest run (2026-10-08, branch `claude/seed-batch-1`)

`dice_event.sh`: 28 mutants, 27 caught, 1 survived, 0 invalid.
- Survived: the chunking mutant (`m = m - 1` inserted after the rolls are drawn). `m` is not used
  again in that loop body, so the mutant does not change behaviour: an equivalent mutant, found by
  reading `_dice_event`, not by a test.

`m4.sh`: 28 mutants, 26 caught, 1 survived, 1 no-op.
- Survived: `RECYCLE_AFTER = 200` changed to `10**9` in `verify/timeout.py`. The only test of
  recycling sets `RECYCLE_AFTER` itself with `monkeypatch`, so the default is never exercised. A
  gap in the tests, with no effect on correctness (workers are recycled for hygiene only).
- No-op: the `core/interview.py` mutant that needs a two-line edit (the single-line `sed` cannot
  apply it). It needs rewriting or removing; it is not a verdict.
- The `abs_tol=1e-12` mutant in `verify/answers.py` (exact-integer answers must not get an absolute
  tolerance) is caught, by `test_exact_means_exact_even_at_floating_point_dust`.

`parsing.sh`, `timeout.sh`, `pricing.sh` (second run, 2026-10-08, after the fixes and new tests):
99 mutants, 86 caught, 13 survived, 0 invalid (parsing 31 of 37, timeout 22 of 28, pricing 33 of
34). The first run had 97 mutants (35 + 28 + 34) and 51 caught; the second adds two parsing
mutants that revert the `safe_parse` overflow fixes. All 33 test gaps from the first run are
killed. The 13 survivors are equivalent (8, each probed) or likely equivalent and unproven (5);
every one is listed with its reason in [SURVIVORS.md](SURVIVORS.md).

Not covered by these scripts: the M2 `sympy_check`, `monte_carlo` and `code_runner` modules and
the M3 core. Those were mutation tested during development, but the mutant lists and results were
not kept, so they cannot be re-run from here yet.
