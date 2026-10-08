#!/usr/bin/env bash
# Mutation run for the dice_event simulator (verify/simulators.py) against its test file.
# Run it alone: it edits source files temporarily. See scripts/mutation/README.md.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
F=verify/simulators.py; T=tests/verify/test_dice_event.py
baseline "$T"
D() { M "$F" "$1" "$T"; }
D 's/    "ge": np.greater_equal,/    "ge": np.greater,/'
D 's/    "le": np.less_equal,/    "le": np.less,/'
D 's/    "eq": np.equal,/    "eq": np.not_equal,/'
D 's/    "ne": np.not_equal,/    "ne": np.equal,/'
D 's/    "lt": np.less,/    "lt": np.less_equal,/'
D 's/    "gt": np.greater,/    "gt": np.greater_equal,/'
D 's/        return rolls.sum(axis=1)/        return rolls.max(axis=1)/'
D 's/        return rolls.max(axis=1)/        return rolls.min(axis=1)/'
D 's/        return rolls.min(axis=1)/        return rolls.max(axis=1)/'
D 's/        return rolls\[:, 0\]/        return rolls[:, -1]/'
D 's/        return rolls\[:, -1\]/        return rolls[:, 0]/'
D 's/ != 0).sum(axis=1) + 1/ != 0).sum(axis=1)/'
D 's/        return (rolls % 2 == 0).sum(axis=1)/        return (rolls % 2 == 1).sum(axis=1)/'
D 's/    return (rolls == face).sum(axis=1)  # count_eq/    return (rolls == 1).sum(axis=1)  # count_eq/'
D 's/            value = value % pred\["mod"\]/            value = value/'
D 's/            out.append(_holds(event, rolls)\[_holds(given, rolls)\])/            out.append(_holds(event, rolls))/'
D 's/            out.append(_holds(event, rolls)\[_holds(given, rolls)\])/            out.append(_holds(given, rolls)[_holds(event, rolls)])/'
D 's/            rolls = rng.integers(1, highs, size=(m, len(dice)))/            rolls = rng.integers(1, highs[0], size=(m, len(dice)))/'
D 's/        for m in _chunks(n):\n            rolls = rng.integers(1, highs/XX/;s/            rolls = rng.integers(1, highs, size=(m, len(dice)))/            rolls = rng.integers(1, highs, size=(m, len(dice)))\n            m = m - 1/'
D 's/        ok \&= _COMPARISONS\[pred\["cmp"\]\](value, pred\["value"\])/        ok |= _COMPARISONS[pred["cmp"]](value, pred["value"])/'
D 's/        if set(pred) != allowed or not {"stat", "cmp", "value"} <= set(pred):/        if False:/'
D 's/        if pred\["stat"\] not in _DICE_STATS or pred\["cmp"\] not in _COMPARISONS:/        if False:/'
D 's/        if not _is_int(pred\["value"\]) or abs(pred\["value"\]) > _MAX_VALUE:/        if False:/'
D 's/    event = _check_predicates("event", event, minimum=1)/    event = _check_predicates("event", event, minimum=0)/'
D 's/        or not all(_is_int(s) and 2 <= s <= 100 for s in dice)/        or not all(_is_int(s) for s in dice)/'
D 's/    return isinstance(x, int) and not isinstance(x, bool)/    return isinstance(x, int)/'
D 's/        if "face" in pred and (not _is_int(pred\["face"\]) or not 1 <= pred\["face"\] <= 100):/        if False:/'
D 's/        if "mod" in pred and (not _is_int(pred\["mod"\]) or not 2 <= pred\["mod"\] <= 1000):/        if False:/'
summary
