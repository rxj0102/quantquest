import itertools
import math

import pytest

from verify.pricing import PricingSpec, binomial_price, bs_price, check_price

BASE = {"S": 100.0, "K": 100.0, "r": 0.05, "sigma": 0.2, "T": 1.0}


def test_black_scholes_textbook_values() -> None:
    assert bs_price(**BASE, kind="call") == pytest.approx(10.450583572185565, abs=1e-9)
    assert bs_price(**BASE, kind="put") == pytest.approx(5.573526022256971, abs=1e-9)


def test_black_scholes_with_dividend_yield() -> None:
    # Merton: S -> S e^{-qT}. Check against the same call priced with the shifted spot.
    q = 0.03
    shifted = {**BASE, "S": BASE["S"] * math.exp(-q * BASE["T"])}
    assert bs_price(**BASE, q=q, kind="call") == pytest.approx(
        bs_price(**shifted, kind="call"), abs=1e-9
    )


GRID = list(
    itertools.product(
        [80.0, 100.0, 120.0],  # S
        [90.0, 100.0, 110.0],  # K
        [0.01, 0.05],  # r
        [0.15, 0.4],  # sigma
        [0.25, 1.0, 2.0],  # T
        [0.0, 0.02],  # q
    )
)


@pytest.mark.parametrize("kind", ["call", "put"])
def test_binomial_agrees_with_black_scholes_on_a_grid(kind: str) -> None:
    worst = 0.0
    for S, K, r, sigma, T, q in GRID:
        args = {"S": S, "K": K, "r": r, "sigma": sigma, "T": T, "q": q, "kind": kind}
        bs, tree = bs_price(**args), binomial_price(**args, n_steps=2000)
        worst = max(worst, abs(bs - tree))
    assert worst < 0.01, worst


def test_binomial_converges_as_steps_increase() -> None:
    exact = bs_price(**BASE, kind="call")
    e_small = abs(binomial_price(**BASE, kind="call", n_steps=50) - exact)
    e_big = abs(binomial_price(**BASE, kind="call", n_steps=3200) - exact)
    assert e_big < e_small / 4
    assert e_big < 2e-3


def test_put_call_parity_in_both_models() -> None:
    q = 0.02
    rhs = BASE["S"] * math.exp(-q * BASE["T"]) - BASE["K"] * math.exp(-BASE["r"] * BASE["T"])
    for price in (bs_price, lambda **kw: binomial_price(**kw, n_steps=1000)):
        c, p = price(**BASE, q=q, kind="call"), price(**BASE, q=q, kind="put")
        assert c - p == pytest.approx(rhs, abs=5e-3)
    assert bs_price(**BASE, q=q, kind="call") - bs_price(**BASE, q=q, kind="put") == pytest.approx(
        rhs, abs=1e-9
    )


def test_basic_no_arbitrage_bounds() -> None:
    for S, K, r, sigma, T, q in GRID:
        c = bs_price(S=S, K=K, r=r, sigma=sigma, T=T, q=q, kind="call")
        p = bs_price(S=S, K=K, r=r, sigma=sigma, T=T, q=q, kind="put")
        assert c >= max(S * math.exp(-q * T) - K * math.exp(-r * T), 0) - 1e-12
        assert p >= max(K * math.exp(-r * T) - S * math.exp(-q * T), 0) - 1e-12


@pytest.mark.parametrize(
    "kw",
    [
        {"sigma": 0.0},
        {"sigma": -0.2},
        {"T": 0.0},
        {"T": -1.0},
        {"S": 0.0},
        {"K": -5.0},
        {"S": float("nan")},
        {"r": float("inf")},
    ],
)
def test_invalid_inputs_raise(kw: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        bs_price(**{**BASE, **kw}, kind="call")
    with pytest.raises(ValueError):
        binomial_price(**{**BASE, **kw}, kind="call", n_steps=100)


def test_bad_kind_and_steps() -> None:
    with pytest.raises(ValueError):
        bs_price(**BASE, kind="straddle")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        binomial_price(**BASE, kind="call", n_steps=0)
    with pytest.raises(ValueError):
        binomial_price(**BASE, kind="call", n_steps=10**7)


# --- check_price: known good / known bad ----------------------------------------------------

CALL = PricingSpec(**BASE, kind="call")
PUT = PricingSpec(**BASE, kind="put")


@pytest.mark.parametrize("claimed", ["10.4506", "10.45058", "10.450583572"])
def test_check_price_accepts_correct_call(claimed: str) -> None:
    res = check_price(claimed, CALL)
    assert res.passed, res.details
    assert res.data["bs"] == pytest.approx(10.450583572, abs=1e-8)
    assert res.data["tree"] == pytest.approx(res.data["bs"], abs=0.01)


def test_check_price_accepts_correct_put() -> None:
    assert check_price("5.5735", PUT).passed


@pytest.mark.parametrize(
    "claimed",
    [
        "10.46",  # about a cent off
        "10.4556",
        "10.45",
        "10.4",
        "5.5735",  # the put price offered for a call
        "10.5",
        "0",
        "-10.4506",
        "104.506",  # decimal slip
    ],
)
def test_check_price_rejects_wrong_and_near_miss_calls(claimed: str) -> None:
    res = check_price(claimed, CALL)
    assert not res.passed and res.outcome == "fail"


def test_check_price_rejects_call_price_for_put_and_vice_versa() -> None:
    assert not check_price("10.4506", PUT).passed
    assert not check_price("5.5735", CALL).passed


def test_check_price_rejects_malicious_claim() -> None:
    assert check_price("__import__('os')", CALL).outcome == "error"


def test_disagreeing_references_fail_closed() -> None:
    """If the tree and closed form disagree beyond cross_tol, the check cannot certify anything."""
    res = check_price("10.4506", CALL, n_steps=10, cross_tol=1e-4)
    assert res.outcome == "error" and "disagree" in res.details


def test_spec_round_trips_through_json() -> None:
    assert PricingSpec.model_validate_json(CALL.model_dump_json()) == CALL
