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

## Skill tree and review scheduling (M3)
```
python -m core.skill_tree --db quantquest.db    # trusted questions per node, content gaps, status
```
- `data/skill_tree.yaml` defines the nodes; add a block to extend it. A node needs at least 3
  trusted questions (configurable) before it can be mastered, and only trusted questions count.
- `core/scheduler.py` is SM-2 behind a small `Scheduler` interface (rating-to-quality mapping is
  documented in its docstring). `core/review.py` stores per-user review state and answers
  `next_due(conn, user_id, now)`; the clock is always passed in. All stored datetimes are UTC.
