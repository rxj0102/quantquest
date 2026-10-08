# quantquest

Game-style learning app for quant interview prep. See `CLAUDE.md` for rules and `PLAN.md` for milestones.

## Setup
```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ruff check . && pytest -q
```

## Verification (M2)
```
python -m verify.promote --db quantquest.db     # verify curated questions, promote fresh -> trusted
python -m verify.code_runner --selfcheck        # confirm the code sandbox isolates the network
python -m scripts.check_answers                 # independent fixture checks used by the tests
```
`verify.promote` prints a failure report and exits 1 if any question was flagged. Flagged and
retired questions are excluded from every default query in `core.db`.
`verify.code_runner` is for our own pipeline only; see its docstring before reusing it.
