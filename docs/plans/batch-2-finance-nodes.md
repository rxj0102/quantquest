# Plan: seeding batch 2 (finance nodes)

Status: **approved, not started.** Written 2026-10-08. Nothing in this plan has been built.
Batch 1 is independent of this plan and partly reviewed: prob.conditional (except prob-012) and
la.eigen are in `data/curated/` and promoted; stat.estimation, la.projections and prob-012 are
still drafts under review.

Scope: only **verifiable numeric and symbolic** questions. Text, rubric and behavioral questions
are deferred out of batch 2 entirely; they come back with the unverified-practice status
(`unverified-practice-status.md`).

## Findings that shape the plan

- `stat.moments` has three questions (variance of a sample mean, Var(X+Y), E[X^2] of an
  exponential) and nothing about the normal distribution. stat-006 and stat-010 use z = 1.96
  only as a given constant. So a small prerequisite node, `prob.normal`, is added. No existing
  node's prerequisites change (that would re-lock progress).
- `check_price` handles European call/put with dividend yield only, with an absolute tolerance of
  5e-4. A "round to 2 decimals" answer (10.45 against an exact 10.4506) fails by 6e-4.
- A price reference returns early in `verify.dispatch`, so there is no Monte Carlo cross-check;
  the closed form and the binomial tree are the two independent implementations.
- Reference expressions cannot evaluate the normal CDF or quantile: `erf` and `erfinv` are not in
  the reference namespace.
- Monte Carlo checks a mean within 3 standard errors. A quantile (VaR) is not a mean.

## Nodes

Track weights are in the order behavioral quant, quant research, risk quant. They are proposals.
A node is added to `data/skill_tree.yaml` **together with its first approved questions**, never
empty (see "No empty nodes").

| node id | title | prerequisites | weights | first questions |
|---|---|---|---|---|
| `prob.normal` | Normal distribution and z-scores | prob.expectation | 0.2, 0.8, 0.7 | 6: standardising, interval probabilities, sums of independent normals, lognormal mean |
| `fin.derivatives` | Derivatives pricing | prob.normal, prob.expectation | 0.3, 0.9, 0.7 | 8: Black-Scholes and binomial prices, put-call parity, one-step hedge and risk-neutral probability |
| `fin.risk` | Risk: VaR and expected shortfall | prob.normal, prob.conditional | 0.2, 0.6, 1.0 | 8: normal and historical VaR and ES, horizon scaling, portfolio variance |
| `ts.basics` | Time series | stat.estimation | 0.1, 0.9, 0.7 | 8: AR(1) stationary variance, autocorrelations, MA(1), AR(2) stationarity, forecasts |
| `beh.finance` | Behavioral finance (quantitative) | prob.conditional, prob.expectation | 0.8, 0.4, 0.5 | 6 to 8: expected-utility certainty equivalent, prospect-theory value, Kelly fraction, base-rate Bayes |

## Verification per node

- **prob.normal**: exact references with `erf`/`erfinv`; probability-format questions use the new
  `tail_event` simulator as the Monte Carlo cross-check.
- **fin.derivatives**: `reference.price` (Black-Scholes closed form plus binomial tree) for price
  questions; exact `Rational` expressions for put-call parity, one-step binomial hedge ratios and
  risk-neutral probabilities. American options, Greeks and implied volatility are deferred to a
  later batch (they need new verifiers).
- **fin.risk**: exact references (`erfinv`, `exp`, `sqrt`, `pi`) for normal VaR and ES;
  `Sum`/`Rational` for historical VaR and ES from a stated dataset; matrix helpers that already
  exist for portfolio variance; `tail_event` for the Monte Carlo cross-check (exceedance
  probability for VaR, conditional mean for ES).
- **ts.basics**: exact references (AR(1) variance sigma^2/(1-phi^2), autocorrelation phi^k, MA(1)
  autocorrelation theta/(1+theta^2), `solve` for AR(2) stationarity). A NumPy long-path
  simulation is used as a **draft-time third check only**; no `ar_process` simulator is added.
- **beh.finance**: exact references for utility, prospect-theory value, Kelly fraction and Bayes;
  existing conditional simulators where a probability question applies.

## Infrastructure (each its own commit, with tests)

1. **`erf`, `erfc`, `erfinv` whitelisted in reference mode only.** No new code (they are SymPy
   functions). Tests: they evaluate in references, are rejected in answers, wrong values are
   caught; mutation-test the whitelist.
2. **Per-reference `abs_tol` on `PricingSpec`**, default unchanged at 5e-4.
   - Rule: the tolerance must be at most half a unit of the last decimal place the prompt asks
     for (2 decimals means at most 0.005).
   - **Required test:** an answer exactly one unit off in the last decimal place is **rejected**
     (for example 10.46 and 10.44 against an exact 10.4506 at 2 decimals), alongside the correct
     rounded answer being accepted, at each tolerance in the test matrix.
   - Changing `abs_tol` changes the reference hash, so affected questions re-verify.
3. **`tail_event` simulator (normal distribution first).** Justified because two nodes need it:
   `prob.normal` (interval and tail probabilities) and `fin.risk` (VaR exceedance, ES as a
   conditional mean). Needs a closed-form cross-check test against `erf` and M2-style mutation
   testing.
4. **Not added**
   - `ar_process`: only `ts.basics` would use it.
   - `gbm_payoff`: only `fin.derivatives` would use it, and price references already have two
     independent implementations. Dropped.

## Conventions lint

`scripts/draft_check` gets a per-node checklist of required convention items, with tests. A prompt
missing any required item is an error, like an unstated answer format.

- **Risk**: loss is a positive number (loss versus return), confidence level, horizon, and how
  horizon scaling is done.
- **Derivatives**: European exercise, continuous compounding, annualised volatility and rate,
  maturity T in years, dividend assumption.
- **Time series**: stationary, mean (zero or stated), white-noise variance, population versus
  sample autocorrelation.
- **Behavioral**: the functional form and its parameters (reference point, loss-aversion lambda,
  utility form), Kelly odds convention.
- **prob.normal**: parameterisation (variance versus standard deviation) and one- versus
  two-sided.

## No empty nodes

`draft_check` currently rejects a node that is not in the real tree. It will also read pending
nodes from `data/drafts/skill_tree_additions.yaml`. A node enters `data/skill_tree.yaml` in the
same commit that moves its approved drafts into `data/curated/`, so the app never shows a node
without questions.

## Milestones (one branch each, stop for review after each)

1. `erf` whitelist, the `draft_check` tree overlay and the conventions lint.
2. `tail_event` simulator, then `prob.normal` drafts with the node block.
3. Price `abs_tol` (with the one-unit-off test), then `fin.derivatives` drafts.
4. `fin.risk` drafts (reuses `tail_event`).
5. `ts.basics` drafts.
6. `beh.finance` drafts.

Every draft keeps the batch 1 checks: independent third check, similarity report, stated answer
format, stated conventions, rendered preview through the real practice page. Nothing moves to
`data/curated/` without the owner's review.

## Open items

- Node weights are proposals.
- Questions that cannot be auto-verified are out of scope here; see the unverified-practice plan.
