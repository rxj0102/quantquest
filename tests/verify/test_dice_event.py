"""dice_event: conditional (or unconditional) dice experiments, cross-checked against exact counts.

The reference implementation below (``exact``) is deliberately independent of the simulator: it
enumerates every outcome with itertools and evaluates predicates with plain Python.
"""

import itertools
from fractions import Fraction

import numpy as np
import pytest

from verify.monte_carlo import MIN_TRIALS, monte_carlo_check
from verify.simulators import SIMULATORS, get_simulator


def sim(dice, given, event):
    return get_simulator("dice_event", {"dice": dice, "given": given, "event": event})


def P(stat, cmp, value, **extra):
    return {"stat": stat, "cmp": cmp, "value": value, **extra}


# --- independent exact enumeration -------------------------------------------------------------------

_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "ge": lambda a, b: a >= b,
    "gt": lambda a, b: a > b,
    "le": lambda a, b: a <= b,
    "lt": lambda a, b: a < b,
}


def _stat(name: str, roll: tuple[int, ...], face: int | None) -> int:
    if name == "sum":
        return sum(roll)
    if name == "max":
        return max(roll)
    if name == "min":
        return min(roll)
    if name == "first":
        return roll[0]
    if name == "last":
        return roll[-1]
    if name == "num_distinct":
        return len(set(roll))
    if name == "count_even":
        return sum(1 for r in roll if r % 2 == 0)
    if name == "count_eq":
        return sum(1 for r in roll if r == face)
    raise AssertionError(name)


def _holds(preds, roll) -> bool:
    for p in preds:
        v = _stat(p["stat"], roll, p.get("face"))
        if "mod" in p:
            v %= p["mod"]
        if not _OPS[p["cmp"]](v, p["value"]):
            return False
    return True


def exact(dice, given, event) -> Fraction:
    rolls = list(itertools.product(*[range(1, s + 1) for s in dice]))
    conditioned = [r for r in rolls if _holds(given, r)]
    return Fraction(sum(1 for r in conditioned if _holds(event, r)), len(conditioned))


# --- closed forms worked by hand --------------------------------------------------------------------------

CLOSED_FORMS = [
    # (dice, given, event, answer, why)
    (
        [10],
        [P("first", "eq", 0, mod=3)],
        [P("first", "gt", 5)],
        Fraction(2, 3),
        "{3,6,9}: 6 and 9 exceed 5",
    ),
    (
        [6, 6],
        [P("num_distinct", "eq", 2)],
        [P("sum", "eq", 8)],
        Fraction(2, 15),
        "4 of 30 ordered pairs",
    ),
    (
        [6, 6, 6, 6],
        [P("max", "eq", 5)],
        [P("count_eq", "eq", 2, face=5)],
        Fraction(32, 123),
        "6*4^2 / (5^4 - 4^4) = 96/369",
    ),
    ([6, 6], [], [P("sum", "eq", 9)], Fraction(1, 9), "4 of 36"),
    ([6, 12], [], [P("sum", "ge", 17)], Fraction(1, 24), "(5,12), (6,11), (6,12) of 72"),
    (
        [6, 6],
        [P("sum", "ge", 10)],
        [P("count_eq", "ge", 1, face=6)],
        Fraction(5, 6),
        "5 of the 6 outcomes",
    ),
    ([6, 6, 6], [], [P("count_even", "ge", 2)], Fraction(1, 2), "symmetry between even and odd"),
    (
        [5],
        [],
        [P("count_even", "eq", 1)],
        Fraction(2, 5),
        "faces 2 and 4 of 1..5 (odd count differs)",
    ),
    (
        [5, 5],
        [P("count_even", "ge", 1)],
        [P("count_even", "eq", 2)],
        Fraction(1, 4),
        "4 of the 16 outcomes with an even face",
    ),
]


@pytest.mark.parametrize(
    "dice,given,event,answer,why", CLOSED_FORMS, ids=[c[4][:30] for c in CLOSED_FORMS]
)
def test_the_hand_worked_closed_forms_agree_with_the_independent_enumeration(
    dice, given, event, answer, why
) -> None:
    assert exact(dice, given, event) == answer, why


@pytest.mark.parametrize(
    "dice,given,event,answer,why", CLOSED_FORMS, ids=[c[4][:30] for c in CLOSED_FORMS]
)
def test_the_simulator_matches_each_closed_form_within_three_standard_errors(
    dice, given, event, answer, why
) -> None:
    res = monte_carlo_check(float(answer), sim(dice, given, event))
    assert res.passed, (why, res.data)
    assert res.data["trials"] >= MIN_TRIALS


@pytest.mark.parametrize(
    "dice,given,event,answer,why", CLOSED_FORMS, ids=[c[4][:30] for c in CLOSED_FORMS]
)
def test_the_simulator_rejects_answers_that_are_a_few_percent_off(
    dice, given, event, answer, why
) -> None:
    for wrong in (float(answer) * 1.05, float(answer) * 0.95, float(answer) + 0.02):
        if 0.0 < wrong < 1.0:
            assert not monte_carlo_check(wrong, sim(dice, given, event)).passed, (why, wrong)


# --- every stat, comparison and the modulus, against enumeration ------------------------------------------

COMBOS = [
    ([6, 6, 6], [P("min", "ge", 2)], [P("sum", "le", 9)]),
    ([6, 6, 6], [P("first", "lt", 4)], [P("last", "gt", 3), P("max", "ne", 6)]),
    ([8, 8], [P("sum", "ge", 9)], [P("num_distinct", "eq", 1)]),
    ([4, 4, 4, 4], [], [P("count_eq", "eq", 2, face=3)]),
    ([5, 7], [P("sum", "ne", 6)], [P("sum", "eq", 0, mod=4)]),
    ([6, 6, 6, 6], [P("count_even", "ge", 1)], [P("count_even", "eq", 4)]),
    ([9], [P("first", "gt", 2)], [P("first", "le", 6, mod=7)]),
    ([6, 6, 6], [P("max", "gt", 4), P("min", "lt", 3)], [P("sum", "ge", 10)]),
    ([5, 5, 5], [P("count_even", "ge", 1)], [P("count_even", "eq", 3)]),  # odd-sided: not symmetric
    ([7, 3], [], [P("count_even", "eq", 0)]),
]


@pytest.mark.parametrize("dice,given,event", COMBOS)
def test_simulator_agrees_with_enumeration_for_every_stat_and_comparison(
    dice, given, event
) -> None:
    truth = exact(dice, given, event)
    res = monte_carlo_check(float(truth), sim(dice, given, event))
    assert res.passed, (truth, res.data)


def test_conditional_runs_use_fewer_effective_samples_than_trials() -> None:
    res = monte_carlo_check(1 / 6, sim([6], [P("first", "ge", 3)], [P("first", "eq", 3)]))
    assert res.data["n_effective"] < res.data["trials"] and res.data["n_effective"] > 500_000
    unconditional = monte_carlo_check(1 / 6, sim([6], [], [P("first", "eq", 3)]))
    assert unconditional.data["n_effective"] == unconditional.data["trials"]


def test_unconditional_dice_event_matches_the_older_dice_sum_equals() -> None:
    a = get_simulator("dice_event", {"dice": [6, 6], "given": [], "event": [P("sum", "eq", 9)]})
    b = get_simulator("dice_sum_equals", {"n_dice": 2, "sides": 6, "target": 9})
    ra, rb = monte_carlo_check(1 / 9, a), monte_carlo_check(1 / 9, b)
    assert ra.passed and rb.passed
    assert abs(ra.data["estimate"] - rb.data["estimate"]) < 4 * ra.data["stderr"]


def test_the_simulation_is_deterministic() -> None:
    args = ([6, 6], [P("sum", "ge", 7)], [P("first", "eq", 3)])
    assert monte_carlo_check(0.2, sim(*args)).data == monte_carlo_check(0.2, sim(*args)).data


def test_samples_are_zero_one_indicators() -> None:
    rng = np.random.default_rng(1)
    s = sim([6, 6], [P("sum", "ge", 7)], [P("first", "eq", 3)])(rng, 50_000)
    assert set(np.unique(s)) <= {0.0, 1.0} and 0 < s.mean() < 1


def test_an_impossible_condition_is_an_error_not_a_pass() -> None:
    res = monte_carlo_check(0.5, sim([6], [P("first", "gt", 6)], [P("first", "eq", 1)]))
    assert res.outcome == "error"


def test_a_certain_event_estimates_one_with_zero_variance_handled() -> None:
    res = monte_carlo_check(1.0, sim([6, 6], [], [P("sum", "ge", 2)]))
    assert res.passed and res.data["stderr"] == 0.0
    assert not monte_carlo_check(0.99, sim([6, 6], [], [P("sum", "ge", 2)])).passed


# --- validation --------------------------------------------------------------------------------------------


def bad(params: dict) -> None:
    with pytest.raises(ValueError):
        get_simulator("dice_event", params)


def test_the_simulator_is_registered_with_exact_parameters() -> None:
    assert "dice_event" in SIMULATORS
    bad({"dice": [6], "given": []})  # event missing
    bad({"dice": [6], "given": [], "event": [P("sum", "eq", 3)], "evil": 1})


@pytest.mark.parametrize(
    "dice",
    [[], [1], [101], [6] * 9, "66", [6.5], [True], [0], [-6]],
)
def test_bad_dice_are_rejected(dice) -> None:
    bad({"dice": dice, "given": [], "event": [P("sum", "eq", 3)]})


@pytest.mark.parametrize(
    "pred",
    [
        {"stat": "sum", "cmp": "eq"},  # no value
        {"stat": "sum", "value": 3},  # no cmp
        {"cmp": "eq", "value": 3},  # no stat
        {"stat": "median", "cmp": "eq", "value": 3},  # unknown stat
        {"stat": "sum", "cmp": "approx", "value": 3},  # unknown comparison
        {"stat": "sum", "cmp": "eq", "value": "3"},  # not an int
        {"stat": "sum", "cmp": "eq", "value": 3.5},
        {"stat": "sum", "cmp": "eq", "value": True},
        {"stat": "sum", "cmp": "eq", "value": 10**6},  # out of range
        {"stat": "count_eq", "cmp": "eq", "value": 1},  # face missing
        {"stat": "sum", "cmp": "eq", "value": 1, "face": 3},  # face only for count_eq
        {"stat": "count_eq", "cmp": "eq", "value": 1, "face": 0},
        {"stat": "sum", "cmp": "eq", "value": 1, "mod": 1},  # mod must be >= 2
        {"stat": "sum", "cmp": "eq", "value": 1, "mod": "2"},
        {"stat": "sum", "cmp": "eq", "value": 1, "evil": 2},  # unknown key
        "sum == 3",  # not a mapping
        ["sum", "eq", 3],
    ],
)
def test_bad_predicates_are_rejected_in_both_places(pred) -> None:
    bad({"dice": [6, 6], "given": [pred], "event": [P("sum", "eq", 3)]})
    bad({"dice": [6, 6], "given": [], "event": [pred]})


def test_the_event_cannot_be_empty_and_lists_are_bounded() -> None:
    bad({"dice": [6], "given": [], "event": []})
    bad({"dice": [6], "given": [P("sum", "ge", 1)] * 5, "event": [P("sum", "eq", 3)]})
    bad({"dice": [6], "given": [], "event": [P("sum", "ge", 1)] * 5})
    bad({"dice": [6], "given": "none", "event": [P("sum", "eq", 3)]})


def test_no_code_can_be_smuggled_in_through_predicates() -> None:
    for payload in ("__import__('os').system('true')", "lambda x: 1", "sum; drop"):
        bad({"dice": [6], "given": [], "event": [{"stat": payload, "cmp": "eq", "value": 1}]})
        bad({"dice": [6], "given": [], "event": [{"stat": "sum", "cmp": payload, "value": 1}]})


def test_a_spec_round_trips_through_json_and_the_reference_model() -> None:
    from verify.references import Reference, SimulatorSpec

    spec = SimulatorSpec(
        name="dice_event",
        params={
            "dice": [6, 6],
            "given": [P("num_distinct", "eq", 2)],
            "event": [P("sum", "eq", 8)],
        },
    )
    ref = Reference.model_validate_json(Reference(simulator=spec).model_dump_json())
    assert ref.simulator == spec
    assert monte_carlo_check(2 / 15, get_simulator(ref.simulator.name, ref.simulator.params)).passed


def test_the_number_of_samples_is_not_silently_truncated() -> None:
    rng = np.random.default_rng(5)
    for n in (1, 99_999, 100_000, 250_001, 1_000_000):
        assert len(sim([6], [], [P("first", "ge", 1)])(rng, n)) == n
