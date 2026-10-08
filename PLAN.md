# PLAN.md: QuantQuest milestones

Work through these in order. Use plan mode at the start of each. Do not start a milestone until the previous one has passing tests.

## M0: Repo setup (1 hour)
- Init repo, venv, ruff, pytest, CI workflow that runs tests
- Add CLAUDE.md and this file
- Done when: pytest runs green on an empty test

## M1: Schema and storage (half day)
- Question schema, SQLite tables, load curated questions from data/ files
- Done when: 20 sample questions load and round-trip in tests

## M2: Verifier library, test first (1 weekend)
- sympy_check, monte_carlo, pricing (Black-Scholes and binomial), code_runner with sandbox and timeout
- Tests include deliberately wrong answers that must be rejected
- Done when: known-good questions pass and known-bad ones fail

## M3: Skill tree and spaced repetition (1 weekend)
- Topic nodes with prerequisites, unlock logic
- SM-2 or FSRS scheduler, with unit tests on interval growth and lapses
- Done when: a simulated user history produces sensible review schedules

## M4: Streamlit MVP (1 weekend)
- Skill tree view, question page with LaTeX, XP and streaks, timed interview mode
- Seed 150 to 200 original, hand-curated questions across 6 to 8 topics
- Done when: you can use it daily for a week

## M5: Code challenges (1 weekend)
- Python problems with auto-graded test cases in the sandbox
- Done when: a failing and a passing submission both grade correctly

## M6: Daily generator (1 weekend)
- Generator, independent solver, comparison, verifier routing, dedupe, fresh queue
- GitHub Actions cron plus a daily spend cap
- Done when: 20 questions per day arrive, with a logged reject rate

## M7: Track mocks and analytics
- Mock interviews per track (behavioral quant, quant research, risk quant), weakness heatmap
- Feed weakness data into tomorrow's generation prompt

## M8: Paper and news ingestion
- arXiv q-fin and stat.ML plus selected feeds, summary cards, grounded questions with source links
- Human review queue before anything is promoted

## Later
- Adaptive difficulty from real solve rates, report-a-problem flow, optional multi-user mode
