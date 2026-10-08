# Survivors of the first run on parsing, timeout and pricing

Run of 2026-10-08 on `claude/trust-evidence` with `parsing.sh`, `timeout.sh` and `pricing.sh`.
97 mutants: 51 caught, 46 survived, 0 invalid. Of the survivors, 33 are test gaps and 13 are
equivalent (or very likely equivalent). No tests have been written for any of them yet.

How each survivor was classified: a **test gap** means an input exists where the original and the
mutant behave differently (found by running both on a probe input; shown in the last column) and
no current test uses it. **Equivalent** means every probe gave the same outcome and the reason is
in the code. "Likely" means reasoned from the code but not probed.

Tests run per list: parsing = `test_parsing.py`, `test_sympy_check.py`, `test_answers.py`;
timeout = `test_timeout.py`, `test_sympy_check.py`; pricing = `test_pricing.py`.

## verify/parsing.py: 35 mutants, 12 caught, 23 survived (17 gaps, 6 equivalent)

| mutant | verdict | evidence |
|---|---|---|
| `MAX_REFERENCE_LEN` 600 to 6000 | gap | 701-character reference text: rejected, accepted by the mutant |
| `MAX_LITERAL` 10**18 to 10**30 | gap | `100000000000000000000`: rejected, accepted |
| `MAX_DIGITS_ESTIMATE` 2000 to 200000 | gap | `99999**1000`: rejected, mutant crashes with an uncaught `ValueError` (Python's int-to-str limit) |
| `len(text) > limit` to `>=` | gap | text of exactly 200 characters: accepted, rejected by the mutant (boundary) |
| `abs(v) >= MAX_LITERAL` to `>` | gap | `1000000000000000000` (10**18): rejected, accepted (boundary) |
| name check removed (underscore, `__`, length) | gap | `_x`, `a__b` and a 30-character name: rejected, accepted as symbols |
| `startswith("_")` dropped | gap | `_x` |
| `"__" in node.id` dropped | gap | `a__b` |
| name length limit 24 to 2400 | gap | 30-character name |
| call check removed | gap | `Integer(5)`: rejected, accepted (the code comment relies on this check) |
| list and tuple allowed in answer mode | gap | `(1, 2)` and `[1, 2]`: rejected, accepted |
| index must be a constant integer (reference mode) | gap | `[1, 2][1+0]`: rejected, accepted |
| reference names always in the namespace | gap | `oo + 1`: parsed as a symbol, parsed as infinity by the mutant |
| unknown assumption name accepted | gap | `symbols={"x": "weird"}`: rejected, accepted (the spec layer also validates it) |
| numeric-power digit check removed | gap | `99999**1000`: rejected, mutant crashes with `ValueError` |
| division by zero guard in the constant evaluator | gap | `2**(1/0)`: accepted as `nan`, mutant raises an uncaught `ZeroDivisionError` |
| unary minus ignored in the constant evaluator | gap | `2**(5 - -1001)`: rejected (exponent 1006), accepted |
| empty-expression check removed | equivalent | `ast.parse("")` raises `SyntaxError`, which becomes `ParseRejected` anyway; only the message differs |
| binary-operator check removed | equivalent | `ast.walk` also visits the operator node (`Mod`, `FloorDiv`, ...), which the final `else` rejects; probes `%`, `//`, `<<`, `|` all rejected |
| unary-operator check removed | equivalent | same reason; `~5` and `not 5` rejected |
| `or node.keywords` removed | equivalent | `ast.walk` visits the `keyword` node and rejects it; ten keyword probes all rejected |
| index allowed in answer mode | equivalent | no allowed function returns something indexable; `x[0]`, `(1)[0]`, `sqrt(2)[0]`, `Max(1,2)[0]` all rejected. Would become a gap if such a function were added |
| `except ParseRejected` removed | equivalent | a `ParseRejected` raised inside would be re-wrapped as `ParseRejected` by the next handler (likely) |

## verify/timeout.py: 28 mutants, 12 caught (two of them by hanging), 16 survived (10 gaps, 6 equivalent)

| mutant | verdict | evidence |
|---|---|---|
| `MAX_WORKERS` 4 to 1 | gap | six parallel 1 s jobs: 2.3 s with four workers, 6.0 s with one |
| `_slots` semaphore 1000 | gap | the same jobs start six workers instead of four |
| `RECYCLE_AFTER` default 10**9 | gap | the only recycling test sets the constant itself; the default of 200 is never exercised |
| expired-deadline `raise CallTimeout` removed | gap | `timeout=0`: `CallTimeout`, mutant raises `ValueError` from `select` and later reads stale output |
| `remaining <= 0` to `< -1e9` | gap | same input |
| EOF check (`if not chunk`) removed | gap | worker dies mid-call: `CallFailed` after 0.3 s, mutant `CallTimeout` after the full 4 s |
| `data += chunk` to `data = chunk` | gap | a 1 MB result: returned intact, mutant times out |
| startup handshake check removed | gap (not probed) | would need a fake worker that sends a wrong first byte (for example by patching `_BOOT`) |
| `-I` (isolated mode) dropped | gap | worker reports `sys.flags.isolated` 1 versus 0 |
| `atexit.register(shutdown)` removed | gap | parent exits during a job: the worker is gone with the hook, still running without it |
| `stderr=DEVNULL` to inherited | equivalent | only changes where the worker's error text goes; nothing in the API can observe it |
| `close_fds=False` | equivalent | probe: the worker has 5 open descriptors either way (Python opens descriptors non-inheritable, PEP 446) |
| `self.kill()` removed in the worker-died branch (line 122) | likely equivalent | the process is already dead and `_release` reaps it with `poll()` |
| `_release` condition reduced to the job count | likely equivalent | `_acquire` re-checks liveness before reusing a pooled worker |
| `_release(healthy=True)` | likely equivalent | `_release` checks `poll()` itself |
| `with _lock` removed | likely equivalent | the set operations it guards are atomic under the GIL; no deterministic test can tell |

## verify/pricing.py: 34 mutants, 27 caught, 7 survived (6 gaps, 1 equivalent)

| mutant | verdict | evidence |
|---|---|---|
| `Field(gt=0)` to `ge=0` | gap | `PricingSpec(S=0)` is rejected, accepted by the mutant; `check_price` still returns an error because `_validate` rejects it |
| `allow_inf_nan` True | gap | `PricingSpec(r=nan)` and `S=inf` rejected, accepted; `check_price` still errors on "must be finite" |
| risk-neutral probability range check removed | gap | `binomial_price(r=0.5, sigma=0.01, n_steps=10)`: `ValueError`, mutant returns 3.2e10; `check_price` still errors, via the cross-check |
| `cross_tol` 0.02 to 0.5 | gap | a tree 0.1 away from Black-Scholes: error, mutant passes |
| `except ValueError` to `KeyError` | gap | `check_price(n_steps=0)`: error result, mutant lets a `ValueError` escape |
| `diff <= abs_tol` to `<` | gap | exact claim with `abs_tol=0`: pass, mutant fails (boundary) |
| `values[0]` to `values[-1]` | equivalent | after the backward induction the array has length 1 |
