"""Exact checks: numeric (with a stated tolerance) and symbolic (SymPy equivalence).

Every call runs under a hard timeout in a child process (see ``verify.timeout``), because
``simplify``/``doit`` cannot be interrupted safely in-process. A timeout, an unparsable input
or an undecidable comparison is an ``error`` result, never a pass and never a fail.
"""

from __future__ import annotations

from typing import Any

import sympy as sp

from verify.parsing import ParseRejected, safe_parse
from verify.result import CheckResult, errored, passed_if
from verify.specs import ExactSpec
from verify.timeout import CallFailed, CallTimeout, call_with_timeout

DEFAULT_TIMEOUT = 10.0
DEFAULT_REL_TOL = 1e-9
DEFAULT_ABS_TOL = 1e-12
_SAMPLE_POINTS = (2, 3, 5, 7, 11, 13)
_UNDECIDED_EPS = sp.Float("1e-15")


def _plain(expr: sp.Expr) -> sp.Expr:
    """Replace symbols by plain symbols of the same name (assumptions must not affect equality)."""
    return expr.xreplace({s: sp.Symbol(s.name) for s in expr.free_symbols})


def _evaluate_reference(spec: dict[str, Any]) -> sp.Expr:
    expr = safe_parse(spec["expr"], reference=True, symbols=spec["symbols"])
    if hasattr(expr, "doit"):
        expr = expr.doit()
    if not isinstance(expr, sp.Expr):
        raise ParseRejected("reference did not evaluate to a scalar expression")
    return expr


def _numeric_job(
    claimed: str, spec: dict[str, Any], rel_tol: float, abs_tol: float
) -> dict[str, Any]:
    c = safe_parse(claimed)
    r = _evaluate_reference(spec)
    if c.free_symbols or r.free_symbols:
        return {"outcome": "error", "details": "numeric check needs numbers, found free symbols"}
    cv, rv = sp.N(c, 50), sp.N(r, 50)
    if not (cv.is_real and rv.is_real):
        return {"outcome": "error", "details": "non-real value in numeric check"}
    diff = abs(cv - rv)
    limit = max(abs_tol, rel_tol * abs(rv))
    return {
        "outcome": "pass" if diff <= limit else "fail",
        "claimed": float(cv),
        "reference": float(rv),
        "abs_diff": float(diff),
    }


def _sample_diff(diff: sp.Expr) -> sp.Expr | None:
    """Largest |diff| over a few fixed sample points, or None if it could not be evaluated."""
    syms = sorted(diff.free_symbols, key=lambda s: s.name)
    worst = None
    for shift in range(len(_SAMPLE_POINTS) - len(syms) + 1 or 1):
        point = {
            s: sp.Rational(_SAMPLE_POINTS[(i + shift) % len(_SAMPLE_POINTS)], 7)
            for i, s in enumerate(syms)
        }
        try:
            v = abs(sp.N(diff.subs(point), 30))
        except (ZeroDivisionError, TypeError):
            continue
        if not v.is_number or v.has(sp.nan, sp.zoo, sp.oo):
            continue
        worst = v if worst is None or v > worst else worst
    return worst


def _symbolic_job(claimed: str, spec: dict[str, Any]) -> dict[str, Any]:
    c = _plain(safe_parse(claimed))
    r = _plain(_evaluate_reference(spec))
    raw = c - r
    diff = sp.simplify(raw)
    if diff == 0:
        return {"outcome": "pass"}
    worst = _sample_diff(raw)
    if worst is not None and worst > _UNDECIDED_EPS:
        return {"outcome": "fail", "residual": str(diff)[:120]}
    # simplify could not prove equality but the sampled difference is ~0: do not guess.
    return {"outcome": "error", "details": "equivalence undecided (simplify did not reduce to 0)"}


def _run(
    job: Any, args: tuple[Any, ...], timeout: float, method: str
) -> dict[str, Any] | CheckResult:
    try:
        return call_with_timeout(job, *args, timeout=timeout)
    except CallTimeout:
        return errored(method, f"timeout after {timeout:g}s (killed)")
    except CallFailed as exc:
        msg = str(exc)
        kind = "rejected input" if "ParseRejected" in msg else "check failed"
        return errored(method, f"{kind}: {msg}")


def check_numeric(
    claimed: str,
    exact: ExactSpec,
    *,
    rel_tol: float = DEFAULT_REL_TOL,
    abs_tol: float = DEFAULT_ABS_TOL,
    timeout: float = DEFAULT_TIMEOUT,
) -> CheckResult:
    """Compare a claimed number to an independent exact computation within a stated tolerance."""
    method = f"sympy_numeric(rel_tol={rel_tol:g},abs_tol={abs_tol:g})"
    out = _run(_numeric_job, (claimed, exact.model_dump(), rel_tol, abs_tol), timeout, method)
    if isinstance(out, CheckResult):
        return out
    if out["outcome"] == "error":
        return errored(method, out["details"])
    return passed_if(
        out["outcome"] == "pass",
        method,
        f"claimed={out['claimed']:.12g} reference={out['reference']:.12g} "
        f"abs_diff={out['abs_diff']:.3e} rel_tol={rel_tol:g}",
        rel_tol=rel_tol,
        abs_tol=abs_tol,
        abs_diff=out["abs_diff"],
    )


def check_symbolic(
    claimed: str, exact: ExactSpec, *, timeout: float = DEFAULT_TIMEOUT
) -> CheckResult:
    """Check that a claimed expression equals the reference (simplify of the difference is 0)."""
    method = "sympy_symbolic(simplify)"
    out = _run(_symbolic_job, (claimed, exact.model_dump()), timeout, method)
    if isinstance(out, CheckResult):
        return out
    if out["outcome"] == "error":
        return errored(method, out["details"])
    if out["outcome"] == "pass":
        return passed_if(True, method, "simplify(claimed - reference) == 0")
    return passed_if(False, method, f"not equivalent; residual {out['residual']}")


def _float_job(claimed: str) -> float:
    value = safe_parse(claimed)
    if value.free_symbols:
        raise ParseRejected("expected a number")
    return float(sp.N(value, 30))


def claimed_to_float(claimed: str, *, timeout: float = DEFAULT_TIMEOUT) -> tuple[float, str | None]:
    """Parse a claimed number under the hard timeout: ``(value, None)`` or ``(nan, error)``."""
    try:
        return call_with_timeout(_float_job, claimed, timeout=timeout), None
    except CallTimeout:
        return float("nan"), f"timeout after {timeout:g}s parsing the claimed answer"
    except CallFailed as exc:
        return float("nan"), f"claimed answer rejected: {exc}"
