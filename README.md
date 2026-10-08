# quantquest

Game-style learning app for quant interview prep. See `CLAUDE.md` for rules and `PLAN.md` for milestones.

## Setup
```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ruff check . && pytest -q
```
