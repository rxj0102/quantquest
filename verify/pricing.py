"""Reference option pricing: Black-Scholes closed form and a CRR binomial tree.

The two are independent implementations of the same model. ``check_price`` requires them to
agree with each other before it will certify anything, then compares the claimed price to the
closed form. European exercise only (American options are not supported).
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from verify.result import CheckResult, errored, passed_if
from verify.sympy_check import claimed_to_float

Kind = Literal["call", "put"]
MAX_STEPS = 20_000


class PricingSpec(BaseModel):
    """Serializable inputs of a European option pricing question."""

    model_config = ConfigDict(extra="forbid")

    S: float = Field(gt=0, allow_inf_nan=False)
    K: float = Field(gt=0, allow_inf_nan=False)
    r: float = Field(allow_inf_nan=False)
    sigma: float = Field(gt=0, allow_inf_nan=False)
    T: float = Field(gt=0, allow_inf_nan=False)
    q: float = Field(default=0.0, allow_inf_nan=False)
    kind: Kind


def _validate(S: float, K: float, r: float, sigma: float, T: float, q: float, kind: str) -> None:
    for name, value in (("S", S), ("K", K), ("r", r), ("sigma", sigma), ("T", T), ("q", q)):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if S <= 0 or K <= 0 or sigma <= 0 or T <= 0:
        raise ValueError("S, K, sigma and T must be positive")
    if kind not in ("call", "put"):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")


def _cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def bs_price(
    *, S: float, K: float, r: float, sigma: float, T: float, kind: Kind, q: float = 0.0
) -> float:
    """Black-Scholes-Merton price of a European option with continuous dividend yield ``q``."""
    _validate(S, K, r, sigma, T, q, kind)
    vol = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol
    d2 = d1 - vol
    fwd, disc = S * math.exp(-q * T), K * math.exp(-r * T)
    if kind == "call":
        return fwd * _cdf(d1) - disc * _cdf(d2)
    return disc * _cdf(-d2) - fwd * _cdf(-d1)


def binomial_price(
    *,
    S: float,
    K: float,
    r: float,
    sigma: float,
    T: float,
    kind: Kind,
    q: float = 0.0,
    n_steps: int = 2000,
) -> float:
    """Cox-Ross-Rubinstein binomial tree price of a European option (backward induction)."""
    _validate(S, K, r, sigma, T, q, kind)
    if not 1 <= n_steps <= MAX_STEPS:
        raise ValueError(f"n_steps must be between 1 and {MAX_STEPS}")
    dt = T / n_steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    if not 0.0 < p < 1.0:
        raise ValueError("risk-neutral probability outside (0, 1); use more steps")
    j = np.arange(n_steps + 1)
    spot = S * np.exp(sigma * math.sqrt(dt) * (2 * j - n_steps))
    values = np.maximum(spot - K, 0.0) if kind == "call" else np.maximum(K - spot, 0.0)
    disc = math.exp(-r * dt)
    for _ in range(n_steps):
        values = disc * (p * values[1:] + (1.0 - p) * values[:-1])
    return float(values[0])


def check_price(
    claimed: str,
    spec: PricingSpec,
    *,
    abs_tol: float = 5e-4,
    n_steps: int = 2000,
    cross_tol: float = 0.02,
    timeout: float = 10.0,
) -> CheckResult:
    """Check a claimed option price against Black-Scholes, with the binomial tree as a cross-check.

    Passes if ``|claimed - bs| <= abs_tol``. If the tree and the closed form disagree by more
    than ``cross_tol`` the result is an ``error``: neither reference can be trusted then.
    """
    method = f"pricing(black_scholes+binomial_tree, abs_tol={abs_tol:g})"
    value, err = claimed_to_float(claimed, timeout=timeout)
    if err is not None:
        return errored(method, err)
    try:
        inputs = spec.model_dump()
        bs = bs_price(**inputs)
        tree = binomial_price(**inputs, n_steps=n_steps)
    except ValueError as exc:
        return errored(method, f"pricing inputs rejected: {exc}")
    if abs(bs - tree) > cross_tol:
        return errored(
            method,
            f"references disagree: black_scholes={bs:.6f} binomial({n_steps})={tree:.6f}",
            bs=bs,
            tree=tree,
        )
    diff = abs(value - bs)
    return passed_if(
        diff <= abs_tol,
        method,
        f"claimed={value:.6f} black_scholes={bs:.6f} binomial({n_steps})={tree:.6f} "
        f"abs_diff={diff:.2e} abs_tol={abs_tol:g}",
        claimed=value,
        bs=bs,
        tree=tree,
        abs_tol=abs_tol,
    )
