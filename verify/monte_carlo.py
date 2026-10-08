"""Monte Carlo check: pass if the claim is within 3 standard errors of a simulation.

Fixed seed (deterministic and reproducible), at least 1e6 trials (CLAUDE.md). The standard
error is estimated from the samples (``std(ddof=1)/sqrt(n)``) and reported in the result.

Limitation worth knowing: at 1e6 trials the standard error is around 5e-4, so Monte Carlo cannot
tell apart answers closer together than roughly 1.5e-3. It is a sanity check on the exact
computation, never a replacement for it; ``dispatch`` requires both whenever both exist.
"""

from __future__ import annotations

import math

import numpy as np

from verify.result import CheckResult, errored, passed_if
from verify.simulators import Simulate

MIN_TRIALS = 1_000_000
SEED = 12345
Z_MAX = 3.0


def monte_carlo_check(
    claimed: float,
    simulate: Simulate,
    *,
    trials: int = MIN_TRIALS,
    seed: int = SEED,
    z_max: float = Z_MAX,
) -> CheckResult:
    """Compare ``claimed`` to the mean of ``simulate(rng, trials)``.

    ``simulate`` returns per-trial samples (possibly fewer than ``trials`` for conditional
    quantities). Raises ValueError if ``trials`` is below ``MIN_TRIALS``.
    """
    if trials < MIN_TRIALS:
        raise ValueError(f"trials must be >= {MIN_TRIALS}, got {trials}")
    method = f"monte_carlo(trials={trials},seed={seed},z_max={z_max:g})"
    if not math.isfinite(claimed):
        return errored(method, "claimed value is not finite")
    rng = np.random.default_rng(seed)
    try:
        samples = np.asarray(simulate(rng, trials), dtype=float).ravel()
    except Exception as exc:  # noqa: BLE001 - a broken simulator certifies nothing
        return errored(method, f"simulator failed: {type(exc).__name__}: {exc}")
    if samples.size < 2:
        return errored(method, f"simulator returned {samples.size} samples")
    if not np.isfinite(samples).all():
        return errored(method, "simulator returned non-finite samples")
    n = int(samples.size)
    estimate = float(samples.mean())
    stderr = float(samples.std(ddof=1) / math.sqrt(n))
    gap = abs(claimed - estimate)
    z = gap / stderr if stderr > 0 else (0.0 if gap <= 1e-12 else math.inf)
    return passed_if(
        z <= z_max,
        method,
        f"claimed={claimed:.6g} estimate={estimate:.6g} stderr={stderr:.3g} z={z:.2f} "
        f"trials={trials} n_effective={n} seed={seed}",
        claimed=claimed,
        estimate=estimate,
        stderr=stderr,
        z=z,
        trials=trials,
        n_effective=n,
        seed=seed,
    )
