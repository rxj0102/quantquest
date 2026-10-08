import math

import numpy as np
import pytest

from scripts.check_answers import EXACT as SCRIPT_EXACT
from verify.monte_carlo import MIN_TRIALS, SEED, monte_carlo_check
from verify.simulators import SIMULATORS, get_simulator


def dice9(rng: np.random.Generator, n: int) -> np.ndarray:
    return (rng.integers(1, 7, size=(n, 2)).sum(axis=1) == 9).astype(float)


def test_defaults_match_claude_md() -> None:
    assert MIN_TRIALS == 1_000_000
    assert isinstance(SEED, int)


def test_rejects_fewer_than_a_million_trials() -> None:
    with pytest.raises(ValueError, match="trials"):
        monte_carlo_check(1 / 9, dice9, trials=999_999)


def test_known_good_reports_standard_error() -> None:
    res = monte_carlo_check(1 / 9, dice9)
    assert res.passed
    d = res.data
    assert d["trials"] == MIN_TRIALS and d["seed"] == SEED
    assert 0 < d["stderr"] < 1e-3
    assert abs(d["estimate"] - 1 / 9) <= 3 * d["stderr"]
    assert d["stderr"] == pytest.approx(
        math.sqrt(d["estimate"] * (1 - d["estimate"]) / MIN_TRIALS), rel=1e-3
    )
    assert "stderr" in res.details


def test_is_deterministic_with_fixed_seed() -> None:
    a, b = monte_carlo_check(1 / 9, dice9), monte_carlo_check(1 / 9, dice9)
    assert a.data == b.data


def test_wrong_answer_rejected() -> None:
    res = monte_carlo_check(1 / 8, dice9)
    assert not res.passed and res.outcome == "fail"
    assert res.data["z"] > 3


def test_three_standard_error_boundary() -> None:
    base = monte_carlo_check(1 / 9, dice9).data
    est, se = base["estimate"], base["stderr"]
    assert monte_carlo_check(est + 2.9 * se, dice9).passed
    assert monte_carlo_check(est - 2.9 * se, dice9).passed
    assert not monte_carlo_check(est + 3.1 * se, dice9).passed
    assert not monte_carlo_check(est - 3.1 * se, dice9).passed


def test_near_miss_beyond_three_se_rejected_but_tiny_error_is_below_resolution() -> None:
    """MC cannot resolve differences far below 3 SE, so dispatch also needs an exact check."""
    se = monte_carlo_check(1 / 9, dice9).data["stderr"]
    assert not monte_carlo_check(1 / 9 + 6 * se, dice9).passed
    assert monte_carlo_check(1 / 9 + se / 10, dice9).passed  # documented limitation


def test_zero_variance_does_not_pass_for_free() -> None:
    ones = lambda rng, n: np.ones(n)  # noqa: E731
    assert monte_carlo_check(1.0, ones).passed
    bad = monte_carlo_check(0.999, ones)
    assert not bad.passed and bad.data["stderr"] == 0.0
    assert not monte_carlo_check(0.0, ones).passed


def test_conditional_estimates_use_effective_sample_size() -> None:
    sim = get_simulator(
        "screening_posterior", {"prevalence": 0.01, "sensitivity": 0.95, "false_positive": 0.10}
    )
    res = monte_carlo_check(19 / 217, sim)
    assert res.passed
    assert res.data["n_effective"] < res.data["trials"]
    assert res.data["stderr"] > 0.0005  # wider than an unconditional 1e6-trial SE


def test_simulator_returning_nothing_is_an_error() -> None:
    res = monte_carlo_check(0.5, lambda rng, n: np.array([]))
    assert res.outcome == "error"


def test_non_finite_samples_are_an_error() -> None:
    res = monte_carlo_check(0.5, lambda rng, n: np.full(n, np.nan))
    assert res.outcome == "error"


# --- the named simulator library --------------------------------------------------------------

CASES = {
    "prob-001": ("dice_sum_equals", {"n_dice": 2, "sides": 6, "target": 9}),
    "prob-002": ("draw_all_of_color", {"counts": {"red": 5, "blue": 3}, "k": 2, "color": "red"}),
    "prob-004": ("coin_heads_at_least", {"n": 4, "k": 2}),
    "prob-005": ("derangement", {"n": 4}),
    "prob-006": (
        "screening_posterior",
        {"prevalence": 0.01, "sensitivity": 0.95, "false_positive": 0.10},
    ),
    "prob-007": ("max_of_uniforms", {"k": 2}),
    "prob-008": ("walk_hits_upper", {"start": 3, "lower": 0, "upper": 10}),
}


@pytest.mark.parametrize("qid", sorted(CASES))
def test_library_simulators_agree_with_script_fixtures(qid: str) -> None:
    name, params = CASES[qid]
    claimed = float(SCRIPT_EXACT[qid]())
    assert name in SIMULATORS
    res = monte_carlo_check(claimed, get_simulator(name, params))
    assert res.passed, (qid, res.data)
    assert res.data["trials"] >= 1_000_000


@pytest.mark.parametrize(
    "qid,wrong", [("prob-001", 1 / 8), ("prob-004", 0.7), ("prob-007", 0.5), ("prob-008", 0.35)]
)
def test_library_simulators_reject_wrong_answers(qid: str, wrong: float) -> None:
    name, params = CASES[qid]
    assert not monte_carlo_check(wrong, get_simulator(name, params)).passed


def test_unknown_simulator_and_bad_params() -> None:
    with pytest.raises(KeyError):
        get_simulator("rm_rf", {})
    with pytest.raises(ValueError):
        get_simulator("dice_sum_equals", {"n_dice": 2})  # missing params
    with pytest.raises(ValueError):
        get_simulator("dice_sum_equals", {"n_dice": 10**6, "sides": 6, "target": 9})  # too big
    with pytest.raises(ValueError):
        get_simulator("coin_heads_at_least", {"n": 4, "k": 2, "evil": 1})  # unknown param
