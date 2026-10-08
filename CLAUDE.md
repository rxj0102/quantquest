# CLAUDE.md

## Project
QuantQuest: a game-style learning app for quant interview prep. It has a skill tree, a question engine, spaced repetition, and a daily AI-generated question feed that is verified by code before users see it.

Target tracks: behavioral quant, quant research, risk quant.

## Core rules (never break these)
1. Correctness over features. A wrong question is worse than no question.
2. Every question with a numeric or symbolic answer must be verified by code (SymPy, Monte Carlo, or a reference pricing implementation). An LLM opinion alone is never enough.
3. Generated questions enter as status=fresh. Only verified, reviewed questions become status=trusted.
4. Keep the hand-curated core pool and the AI feed separate in the data model and in the UI.
5. Never commit API keys. Read them from environment variables.
6. Keep milestones small. One feature per branch. Explain what you changed and why after each milestone.

## Stack
- Python 3.11+, Streamlit for the UI, SQLite for storage (SQLModel or plain sqlite3)
- pytest for tests, ruff for linting
- SymPy, NumPy, SciPy for verification
- Anthropic API for generation (structured JSON output)
- GitHub Actions cron for the daily job

## Repo layout
```
quantquest/
  app/            Streamlit pages
  core/           schema, skill tree, scheduler (SM-2 or FSRS), scoring
  verify/         verifiers: sympy_check, monte_carlo, pricing, code_runner
  generate/       prompts, generator, independent solver, dedupe
  ingest/         arXiv and news fetchers (late milestone)
  data/           curated question files (YAML or JSON)
  tests/
CLAUDE.md
PLAN.md
```

## Question schema
Each question has: id, topic, node_id, difficulty (1-5), format (mental_math, probability, derivation, coding, case), prompt_md (with LaTeX), answer, answer_type (numeric, symbolic, text, code), solution_md, verification (method and result), source (curated, generated, paper:URL), status (fresh, trusted, flagged, retired), created_at, solve_stats.

## Verification rules
- numeric: compare to an independent computation with a stated tolerance
- symbolic: check equivalence with SymPy simplify
- probability: Monte Carlo with at least 1e6 trials and a fixed seed, within 3 standard errors of the claimed answer
- pricing: compare to a Black-Scholes closed form or a binomial tree
- coding: run the reference solution against the generated tests in a sandboxed subprocess with a timeout
- case or behavioral: no auto-verification; grade with a rubric and label as unverified

## Generation pipeline
generate -> independent solve (without seeing the first answer) -> compare -> run code verifier -> dedupe by embedding similarity -> store as fresh. Discard on any disagreement. Cap daily API spend with a hard limit and fail closed.

## Commands
- Run app: streamlit run app/main.py
- Tests: pytest -q
- Lint: ruff check .

## Conventions
- Type hints everywhere, small pure functions, docstrings on public functions
- Write tests before the verifier code
- No new dependency without saying why
- Read the diff of any verifier change carefully; this is the trust boundary of the app
