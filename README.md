# quantquest

Game-style learning app for quant interview prep. See `CLAUDE.md` for rules and `PLAN.md` for milestones.

## What the trust boundary protects
- **How an answer becomes trusted.** A curated question starts `fresh`. `verify.promote` (and the
  app's first page load) checks its answer against the independent `reference:` in its YAML entry:
  exact SymPy (relative tolerance 1e-9, or symbolic equivalence), plus Monte Carlo (fixed seed, at
  least 1e6 trials, within 3 standard errors) for probability questions, or Black-Scholes against
  a binomial tree for prices. Only if every check passes is it `trusted`. Editing a question's
  prompt, answer, answer type or reference resets it to `fresh`.
- **What can never be trusted.** Text and case answers have no code check, so promotion leaves
  them `fresh`, and the schema refuses `trusted` on a text answer. Only the curated pool is promoted.
- **How failures are handled.** A failed check makes the question `flagged`: hidden from the app
  and every default query, and printed with the evidence (`verify.promote` exits 1). A check that
  cannot run (bad reference, timeout) leaves it `fresh`.
- **How it is tested.** Known-bad cases for each verifier, plus near-miss answers for the numeric,
  symbolic, Monte Carlo and pricing checks; every curated numeric or symbolic answer is perturbed
  and must fail; every YAML reference must match an independent value in `scripts/`. Mutation
  testing (`scripts/mutation/`) currently covers the `dice_event` simulator and the app logic; the
  core verifiers are being added.
- **Known limit.** Monte Carlo cannot separate answers within about 3 standard errors, so the exact
  reference is the real gate.

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
evidence used to verify it) and restart the app, or run `python -m verify.promote`. The YAML
reference is the only source of references. Changing a question's prompt, answer or reference
sends it back to `fresh` until it is re-verified. A text-answer question can never be `trusted`.
When you add or remove a curated question on purpose, update the expected ids in
`tests/test_curated_ids.py` (and the expected symbolic ids in `tests/verify/test_sympy_check.py`
if it is symbolic) in the same commit, so an accidental deletion fails a test.

## Seeding new questions
```
python -m scripts.draft_check data/drafts/<file>.yaml     # review sheet for a batch of drafts
python -m core.skill_tree --db quantquest.db               # trusted questions per node
```
- Drafts live in `data/drafts/` and are never loaded by the app. Each draft carries its
  `reference:`; `draft_check` runs the real verifier, an independent third check
  (`scripts/draft_checks/`), the prompt-similarity check (`core/similarity.py`) and the draft rules
  (answer format stated, symbolic answers ask for a single expression, display math that renders,
  difficulty 1 to 4, no near-duplicates).
- A draft moves to `data/curated/` only after the owner's review. Its third check then stays as the
  question's answer-check fixture (`scripts/check_answers.py`).
- Plans for the next work are in `docs/plans/` (finance nodes for batch 2, and an explicit
  unverified-practice status for questions that cannot be auto-verified).

## Mutation testing
`scripts/mutation/` holds the harness used on the trust-boundary code. See its README for how to
run it, what the verdicts mean, and the latest results. Run it on its own: it edits source files
temporarily.
