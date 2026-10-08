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

## Running the app (M4)
```
pip install -r requirements-dev.txt
streamlit run app/main.py
```
The first start loads `data/curated`, verifies every question with code and promotes the ones that
pass (a few seconds, once per server process). Only `trusted` questions ever appear in the app.

Pages: **Skill tree** (locked / unlocked / mastered, trusted-question count per node), **Practice**
(due reviews first, then new questions from unlocked nodes; rate Again / Hard / Good / Easy), and
**Interview** (a fixed set of questions against a countdown that is stored in the database, so a
refresh or a second tab cannot reset it). XP and streaks count days in `America/New_York`.

Settings are environment variables: `QQ_DB_PATH`, `QQ_TIMEZONE`, `QQ_UI_REL_TOL` (tolerance for
typed numeric answers, default `0.001`; integer answers always need exact matches),
`QQ_SESSION_MAX_CARDS`, `QQ_SESSION_MAX_NEW`, `QQ_INTERVIEW_QUESTIONS`, `QQ_INTERVIEW_MINUTES`,
`QQ_DATA_DIR`. A question can override the typed-answer tolerance with `answer_tolerance:` in its
YAML entry. That tolerance is for typing only: verification and promotion always use the strict one.

### Adding a question
Add one entry to a file in `data/curated/` (including its `reference:` block, the independent
evidence used to verify it) and restart the app, or run `python -m verify.promote`. Changing a
question's prompt, answer or reference sends it back to `fresh` until it is re-verified.
