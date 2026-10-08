"""A library of named, parameterised simulators for Monte Carlo checks.

A ``SimulatorSpec`` (name + JSON params) names an entry here, so a reference stays plain data
that can be stored with a question. Generated questions can pick from this library but cannot
supply simulator *code*. Every simulator takes ``(rng, n_trials)`` and returns a 1-D array of
per-trial samples (0/1 indicators for probabilities, values for expectations). A simulator may
return fewer than ``n_trials`` samples when it estimates a conditional quantity.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

Simulate = Callable[[np.random.Generator, int], np.ndarray]
CHUNK = 100_000


@dataclass(frozen=True)
class _Param:
    kind: type
    low: float
    high: float


def _chunks(n: int) -> list[int]:
    return [CHUNK] * (n // CHUNK) + ([n % CHUNK] if n % CHUNK else [])


def _dice_sum_equals(p: Mapping[str, Any]) -> Simulate:
    n_dice, sides, target = p["n_dice"], p["sides"], p["target"]

    def sim(rng: np.random.Generator, n: int) -> np.ndarray:
        rolls = rng.integers(1, sides + 1, size=(n, n_dice))
        return (rolls.sum(axis=1) == target).astype(float)

    return sim


def _draw_all_of_color(p: Mapping[str, Any]) -> Simulate:
    counts, k, color = p["counts"], p["k"], p["color"]
    if (
        not isinstance(counts, dict)
        or not 1 <= len(counts) <= 10
        or color not in counts
        or not all(isinstance(v, int) and 0 <= v <= 1000 for v in counts.values())
    ):
        raise ValueError("counts must map up to 10 colors to ints in [0, 1000] and include color")
    if not isinstance(k, int) or not 1 <= k <= sum(counts.values()):
        raise ValueError("k must be between 1 and the number of items")
    colors = np.array(list(counts.values()))
    idx = list(counts).index(color)

    def sim(rng: np.random.Generator, n: int) -> np.ndarray:
        drawn = rng.multivariate_hypergeometric(colors, k, size=n)
        return (drawn[:, idx] == k).astype(float)

    return sim


def _coin_heads_at_least(p: Mapping[str, Any]) -> Simulate:
    flips, k = p["n"], p["k"]
    if k > flips:
        raise ValueError("k cannot exceed n")

    def sim(rng: np.random.Generator, n: int) -> np.ndarray:
        out = [(rng.integers(0, 2, size=(m, flips)).sum(axis=1) >= k) for m in _chunks(n)]
        return np.concatenate(out).astype(float)

    return sim


def _derangement(p: Mapping[str, Any]) -> Simulate:
    size = p["n"]
    identity = np.arange(size)

    def sim(rng: np.random.Generator, n: int) -> np.ndarray:
        out = []
        for m in _chunks(n):
            perms = rng.permuted(np.tile(identity, (m, 1)), axis=1)
            out.append((perms != identity).all(axis=1))
        return np.concatenate(out).astype(float)

    return sim


def _screening_posterior(p: Mapping[str, Any]) -> Simulate:
    prev, sens, fpr = p["prevalence"], p["sensitivity"], p["false_positive"]

    def sim(rng: np.random.Generator, n: int) -> np.ndarray:
        sick = rng.random(n) < prev
        flagged = np.where(sick, rng.random(n) < sens, rng.random(n) < fpr)
        return sick[flagged].astype(float)  # conditional on being flagged

    return sim


def _max_of_uniforms(p: Mapping[str, Any]) -> Simulate:
    k = p["k"]

    def sim(rng: np.random.Generator, n: int) -> np.ndarray:
        return rng.random((n, k)).max(axis=1)

    return sim


def _walk_hits_upper(p: Mapping[str, Any]) -> Simulate:
    start, lower, upper = p["start"], p["lower"], p["upper"]
    if not lower < start < upper or upper - lower > 100:
        raise ValueError("need lower < start < upper and upper - lower <= 100")

    def sim(rng: np.random.Generator, n: int) -> np.ndarray:
        pos = np.full(n, start, dtype=np.int64)
        alive = np.arange(n)
        hit = np.zeros(n)
        for _ in range(1_000_000):
            if alive.size == 0:
                break
            pos[alive] += rng.integers(0, 2, size=alive.size) * 2 - 1
            p_alive = pos[alive]
            hit[alive[p_alive == upper]] = 1.0
            alive = alive[(p_alive > lower) & (p_alive < upper)]
        else:
            raise RuntimeError("walk did not absorb")
        return hit

    return sim


_DICE_STATS = ("sum", "max", "min", "first", "last", "num_distinct", "count_even", "count_eq")
_COMPARISONS = {
    "eq": np.equal,
    "ne": np.not_equal,
    "ge": np.greater_equal,
    "gt": np.greater,
    "le": np.less_equal,
    "lt": np.less,
}
_MAX_DICE, _MAX_PREDICATES, _MAX_VALUE = 8, 4, 10_000


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _check_predicates(where: str, preds: Any, *, minimum: int) -> list[dict[str, Any]]:
    if not isinstance(preds, list) or not minimum <= len(preds) <= _MAX_PREDICATES:
        raise ValueError(
            f"dice_event.{where} must be a list of {minimum} to {_MAX_PREDICATES} predicates"
        )
    for pred in preds:
        if not isinstance(pred, dict):
            raise ValueError(f"dice_event.{where}: each predicate must be a mapping")
        allowed = {"stat", "cmp", "value"} | ({"face"} if pred.get("stat") == "count_eq" else set())
        allowed |= {"mod"} if "mod" in pred else set()
        if set(pred) != allowed or not {"stat", "cmp", "value"} <= set(pred):
            raise ValueError(f"dice_event.{where}: bad predicate keys {sorted(pred)}")
        if pred["stat"] not in _DICE_STATS or pred["cmp"] not in _COMPARISONS:
            raise ValueError(f"dice_event.{where}: unknown stat or comparison in {pred}")
        if not _is_int(pred["value"]) or abs(pred["value"]) > _MAX_VALUE:
            raise ValueError(f"dice_event.{where}: value must be an int within +-{_MAX_VALUE}")
        if "face" in pred and (not _is_int(pred["face"]) or not 1 <= pred["face"] <= 100):
            raise ValueError(f"dice_event.{where}: face must be an int from 1 to 100")
        if "mod" in pred and (not _is_int(pred["mod"]) or not 2 <= pred["mod"] <= 1000):
            raise ValueError(f"dice_event.{where}: mod must be an int from 2 to 1000")
    return preds


def _dice_stat(name: str, rolls: np.ndarray, face: int | None) -> np.ndarray:
    if name == "sum":
        return rolls.sum(axis=1)
    if name == "max":
        return rolls.max(axis=1)
    if name == "min":
        return rolls.min(axis=1)
    if name == "first":
        return rolls[:, 0]
    if name == "last":
        return rolls[:, -1]
    if name == "num_distinct":
        return (np.diff(np.sort(rolls, axis=1), axis=1) != 0).sum(axis=1) + 1
    if name == "count_even":
        return (rolls % 2 == 0).sum(axis=1)
    return (rolls == face).sum(axis=1)  # count_eq


def _holds(preds: list[dict[str, Any]], rolls: np.ndarray) -> np.ndarray:
    ok = np.ones(rolls.shape[0], dtype=bool)
    for pred in preds:
        value = _dice_stat(pred["stat"], rolls, pred.get("face"))
        if "mod" in pred:
            value = value % pred["mod"]
        ok &= _COMPARISONS[pred["cmp"]](value, pred["value"])
    return ok


def _dice_event(p: Mapping[str, Any]) -> Simulate:
    """P(event | given) for one roll of some dice; ``given`` may be empty (unconditional).

    ``dice`` lists the number of sides of each die (1 to 8 dice, 2 to 100 sides each; dice may
    differ). A predicate is ``{"stat", "cmp", "value"}`` plus optional ``"mod"`` (compare the stat
    modulo m) and, for ``count_eq`` only, ``"face"``. Predicates in a list must all hold. Stats:
    sum, max, min, first, last, num_distinct, count_even, count_eq (dice showing ``face``).
    The samples are the 0/1 outcomes of ``event`` among the trials where ``given`` held.
    """
    dice, given, event = p["dice"], p["given"], p["event"]
    if (
        not isinstance(dice, list)
        or not 1 <= len(dice) <= _MAX_DICE
        or not all(_is_int(s) and 2 <= s <= 100 for s in dice)
    ):
        raise ValueError(
            f"dice_event.dice must list 1 to {_MAX_DICE} side counts between 2 and 100"
        )
    given = _check_predicates("given", given, minimum=0)
    event = _check_predicates("event", event, minimum=1)
    highs = np.array(dice) + 1

    def sim(rng: np.random.Generator, n: int) -> np.ndarray:
        out = []
        for m in _chunks(n):
            rolls = rng.integers(1, highs, size=(m, len(dice)))
            out.append(_holds(event, rolls)[_holds(given, rolls)])
        return np.concatenate(out).astype(float)

    return sim


def _int(low: int, high: int) -> _Param:
    return _Param(int, low, high)


def _prob() -> _Param:
    return _Param(float, 1e-9, 1 - 1e-9)


# name -> (parameter schema, factory)
SIMULATORS: dict[str, tuple[dict[str, _Param | None], Callable[[Mapping[str, Any]], Simulate]]] = {
    "dice_sum_equals": (
        {"n_dice": _int(1, 20), "sides": _int(2, 100), "target": _int(0, 2000)},
        _dice_sum_equals,
    ),
    "draw_all_of_color": ({"counts": None, "k": None, "color": None}, _draw_all_of_color),
    "coin_heads_at_least": ({"n": _int(1, 200), "k": _int(0, 200)}, _coin_heads_at_least),
    "derangement": ({"n": _int(2, 12)}, _derangement),
    "screening_posterior": (
        {"prevalence": _prob(), "sensitivity": _prob(), "false_positive": _prob()},
        _screening_posterior,
    ),
    "max_of_uniforms": ({"k": _int(2, 10)}, _max_of_uniforms),
    "dice_event": ({"dice": None, "given": None, "event": None}, _dice_event),
    "walk_hits_upper": (
        {"start": _int(-100, 100), "lower": _int(-100, 100), "upper": _int(-100, 100)},
        _walk_hits_upper,
    ),
}


def get_simulator(name: str, params: Mapping[str, Any]) -> Simulate:
    """Build a simulator by name. Raises KeyError for unknown names, ValueError for bad params."""
    schema, factory = SIMULATORS[name]
    if set(params) != set(schema):
        raise ValueError(f"{name}: params must be exactly {sorted(schema)}, got {sorted(params)}")
    for key, spec in schema.items():
        if spec is None:
            continue
        value = params[key]
        ok_type = isinstance(value, (int, float)) and not isinstance(value, bool)
        if spec.kind is int:
            ok_type = isinstance(value, int) and not isinstance(value, bool)
        if not ok_type or not spec.low <= value <= spec.high:
            raise ValueError(f"{name}.{key}={value!r} outside [{spec.low}, {spec.high}]")
    return factory(params)
