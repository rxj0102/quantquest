"""Independent third checks for drafted questions, keyed by question id.

Each function recomputes the answer a different way from both the stated solution and the
reference expression (enumeration, exact fractions, NumPy) and returns a value that
``scripts.draft_check`` compares with the draft's answer. Not imported by app code.
"""

from __future__ import annotations

from collections.abc import Callable

from scripts.draft_checks import la_eigen, prob_conditional, stat_estimation  # noqa: E402

THIRD_CHECKS: dict[str, Callable[[], object]] = {
    **prob_conditional.CHECKS,
    **la_eigen.CHECKS,
    **stat_estimation.CHECKS,
}
