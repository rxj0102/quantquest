# Survivors on parsing, timeout and pricing

Two runs on `claude/trust-evidence`, both with `parsing.sh`, `timeout.sh` and `pricing.sh`.

| | mutants | caught | survived | invalid |
|---|---|---|---|---|
| first run (before any new test) | 97 | 51 | 46 (33 test gaps, 13 equivalent or likely) | 0 |
| **second run (after the fixes and tests)** | **99** | **86** | **13** | **0** |

The second run has two more mutants than the first: `parsing.sh` gained two that revert the
`safe_parse` overflow fixes (the non-finite float check and the digit estimate). No mutant in the
second run was stopped by the harness time limit; the two that used to be caught only by hanging
for 300 seconds (`if not ready:` removed and the `_slots.release()` removal in `_release`) now fail
in about a second, and so does a third that was also caught only by hanging (`MAX_STEPS` raised).
All 33 test gaps are killed. Every one of the 13 remaining survivors is one the first run had
already classed as equivalent or very likely equivalent.

Per module: parsing 37 mutants, 31 caught, 6 survived. timeout 28, 22 caught, 6 survived.
pricing 34, 33 caught, 1 survived.

## How to read the labels

- **Equivalent** (8 below): a probe found no input where the original and the mutant differ, and
  the code gives the reason. This is evidence, not a proof.
- **Likely equivalent, unproven** (5 below): reasoned from the code only. No probe or test
  separates them and none was shown to be impossible to separate. They stay labelled unproven
  until someone either finds a separating input (then they are gaps) or proves them equivalent.

Tests run per list: parsing = `test_parsing.py`, `test_sympy_check.py`, `test_answers.py`;
timeout = `test_timeout_pool.py` (first, so the harness stops there), `test_timeout.py`,
`test_sympy_check.py`; pricing = `test_pricing.py`.

## verify/parsing.py: 6 survivors

| mutant | label | reason |
|---|---|---|
| empty-expression check (`if not src:`) removed | equivalent | `ast.parse("")` raises `SyntaxError`, which becomes `ParseRejected` anyway; only the message differs |
| binary-operator check removed | equivalent | `ast.walk` also visits the operator node (`Mod`, `FloorDiv`, ...), which the final `else` rejects; probes `%`, `//`, `<<`, `\|` all rejected |
| unary-operator check removed | equivalent | same reason; `~5` and `not 5` rejected |
| `or node.keywords` removed | equivalent | `ast.walk` visits the `keyword` node and rejects it; ten keyword probes all rejected |
| index allowed in answer mode (`elif reference and isinstance(node, ast.Subscript)`) | equivalent | no allowed function returns something indexable; `x[0]`, `(1)[0]`, `sqrt(2)[0]`, `Max(1,2)[0]` all rejected. Becomes a gap if such a function is ever added |
| `except ParseRejected: raise` replaced by `except KeyError` | likely equivalent, unproven | a `ParseRejected` raised inside `parse_expr` would be re-wrapped as `ParseRejected` by the next handler (only the message would change). Not probed |

## verify/timeout.py: 6 survivors

| mutant | label | reason |
|---|---|---|
| `stderr=subprocess.DEVNULL` to inherited | equivalent | only changes where the worker's error text goes; nothing in the API can observe it |
| `close_fds=False` | equivalent | probe: the worker has 5 open descriptors either way (Python opens descriptors non-inheritable, PEP 446) |
| `self.kill()` removed in the worker-died branch of `call` (line 122) | likely equivalent, unproven | the process is already dead and `_release` reaps it with `poll()`. Not probed |
| `_release` condition reduced to the job count | likely equivalent, unproven | `_acquire` re-checks liveness before reusing a pooled worker. Not probed |
| `_release(worker, healthy=True)` | likely equivalent, unproven | `_release` checks `poll()` itself. Not probed |
| `with _lock` removed | likely equivalent, unproven | the set operations it guards are atomic under the GIL; no deterministic test can tell. A race that exists only without the GIL is not ruled out |

## verify/pricing.py: 1 survivor

| mutant | label | reason |
|---|---|---|
| `values[0]` to `values[-1]` | equivalent | after the backward induction the array has length 1 |

## What was fixed and tested since the first run

- Three real `safe_parse` contract violations (it raised something other than `ParseRejected`):
  a non-finite float literal in a power (`2**1e999`, `OverflowError`), a huge integer base
  (`(10**1000)**2`, `OverflowError`), and a tiny rational base (`(1/10**400)**2`, `ValueError`).
  Each has its own commit, with the test shown failing first.
- The three inputs the first survivor table named (`2**(1/0)`, `99999**1000`,
  `2**(5 - -1001)`) were not violations on the original code: the table described what the
  *mutants* did. They are pinned by a regression test, which now also asserts the right rejection.
- Two tests in the first batch moved a mutation with them (they imported the constant under
  test) or probed a limit so far past it that the mutant hung instead of failing. Both were found
  by the rerun and fixed; the limits are now literals and the probe is one past the limit.
