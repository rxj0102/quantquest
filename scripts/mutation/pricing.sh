#!/usr/bin/env bash
# Mutation run for verify/pricing.py (Black-Scholes, the CRR tree, and check_price).
# Tests run: the module's own test file only (tests/verify/test_dispatch.py also exercises it
# through the dispatcher but runs the slow Monte Carlo checks, so it is not included).
# Run it alone: it edits source files temporarily. See scripts/mutation/README.md.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
F=verify/pricing.py
T=tests/verify/test_pricing.py
baseline "$T"
P() { M "$F" "$1" "$T"; }
# --- input validation
P 's|        if not math.isfinite(value):|        if False:|'
P 's|S <= 0 or K <= 0 or sigma <= 0 or T <= 0|S < 0 or K < 0 or sigma < 0 or T < 0|'
P 's|    if kind not in ("call", "put"):|    if False:|'
P 's|Field(gt=0, allow_inf_nan=False)|Field(ge=0, allow_inf_nan=False)|'
P 's|allow_inf_nan=False|allow_inf_nan=True|'
# --- Black-Scholes
P 's|    return 0.5 \* math.erfc(-x / math.sqrt(2.0))|    return 0.5 * math.erfc(x / math.sqrt(2.0))|'
P 's|    vol = sigma \* math.sqrt(T)|    vol = sigma * T|'
P 's|(r - q + 0.5 \* sigma \* sigma) \* T|(r - q - 0.5 * sigma * sigma) * T|'
P 's|    d2 = d1 - vol|    d2 = d1 + vol|'
P 's|fwd, disc = S \* math.exp(-q \* T), K \* math.exp(-r \* T)|fwd, disc = S * math.exp(q * T), K * math.exp(-r * T)|'
P 's|K \* math.exp(-r \* T)$|K * math.exp(r * T)|'
P 's|    if kind == "call":|    if kind == "put":|'
P 's|        return fwd \* _cdf(d1) - disc \* _cdf(d2)|        return fwd * _cdf(d1) + disc * _cdf(d2)|'
P 's|    return disc \* _cdf(-d2) - fwd \* _cdf(-d1)|    return disc * _cdf(d2) - fwd * _cdf(d1)|'
# --- the binomial tree
P 's|^MAX_STEPS = 20_000|MAX_STEPS = 20_000_000|'
P 's|    if not 1 <= n_steps <= MAX_STEPS:|    if False:|'
P 's|    u = math.exp(sigma \* math.sqrt(dt))|    u = math.exp(sigma * dt)|'
P 's|    d = 1.0 / u|    d = 0.5 / u|'
P 's|    p = (math.exp((r - q) \* dt) - d) / (u - d)|    p = (math.exp(r * dt) - d) / (u - d)|'
P 's|    if not 0.0 < p < 1.0:|    if False:|'
P 's|    spot = S \* np.exp(sigma \* math.sqrt(dt) \* (2 \* j - n_steps))|    spot = S * np.exp(sigma * math.sqrt(dt) * (j - n_steps))|'
P 's|np.maximum(spot - K, 0.0) if kind == "call" else|np.maximum(spot - K, 0.0) if kind == "put" else|'
P 's|    disc = math.exp(-r \* dt)|    disc = 1.0|'
P 's|        values = disc \* (p \* values\[1:\] + (1.0 - p) \* values\[:-1\])|        values = disc * (p * values[:-1] + (1.0 - p) * values[1:])|'
P 's|    return float(values\[0\])|    return float(values[-1])|'
# --- check_price
P 's|abs_tol: float = 5e-4,|abs_tol: float = 5e-2,|'
P 's|cross_tol: float = 0.02,|cross_tol: float = 0.5,|'
P 's|n_steps: int = 2000,|n_steps: int = 20,|'
P 's|    value, err = claimed_to_float(claimed, timeout=timeout)|    value, err = float(claimed), None|'
P 's|    if err is not None:|    if False:|'
P 's|    except ValueError as exc:|    except KeyError as exc:|'
P 's|    if abs(bs - tree) > cross_tol:|    if False:|'
P 's|    diff = abs(value - bs)|    diff = abs(value - tree)|'
P 's|        diff <= abs_tol,|        diff < abs_tol,|'
summary
