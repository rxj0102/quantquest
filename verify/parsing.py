"""Safe parsing of untrusted expression text into SymPy objects.

This is part of the trust boundary. Claimed answers (and, later, LLM-written reference
expressions) are text from an untrusted source, so they are never passed to ``sympify`` or
``eval``. Instead ``safe_parse``:

1. enforces a length limit,
2. parses the text with Python's ``ast`` and walks the tree against a strict allowlist
   (numbers, names, ``+ - * / **``, calls to a fixed set of functions; nothing else:
   no attribute access, strings, lambdas, comprehensions, keyword arguments, ...),
3. rejects expression bombs (power towers, huge exponents or literals, too many nodes),
4. only then calls ``parse_expr`` with a restricted namespace whose ``__builtins__`` is ``{}``
   (Python would otherwise silently inject the real builtins).

Anything that could still be slow (``simplify``, ``doit``) runs under a hard timeout; see
``verify.timeout``. The remaining risk is a legal expression that is expensive to evaluate,
which the timeout and memory cap contain.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from fractions import Fraction
from typing import Any

import sympy as sp
from sympy.parsing.sympy_parser import parse_expr, rationalize, standard_transformations

MAX_LEN = 200
MAX_REFERENCE_LEN = 600
MAX_NODES = 250
MAX_EXPONENT = 1000
MAX_LITERAL = 10**18
MAX_DIGITS_ESTIMATE = 2000

ASSUMPTIONS = {"plain", "positive", "real", "integer", "nonnegative"}


class ParseRejected(ValueError):
    """The text is not an acceptable expression."""


# -- helpers available to reference expressions (trusted code, not LLM text) -----------------


def _det(m: sp.MatrixBase) -> sp.Expr:
    return m.det()


def _rank(m: sp.MatrixBase) -> sp.Expr:
    return sp.Integer(m.rank())


def _trace(m: sp.MatrixBase) -> sp.Expr:
    return m.trace()


def _max_eigenvalue(m: sp.MatrixBase) -> sp.Expr:
    vals = list(m.eigenvals())
    if any(not sp.im(sp.N(v)) == 0 for v in vals):
        raise ValueError("complex eigenvalues")
    return max(vals, key=lambda v: sp.N(v))


def _gamblers_ruin(start: int, lower: int, upper: int) -> sp.Rational:
    """P(fair +-1 walk from ``start`` hits ``upper`` before ``lower``), via a linear system."""
    if not (lower < start < upper) or upper - lower > 200:
        raise ValueError("bad walk bounds")
    n = upper - lower
    a, b = sp.zeros(n + 1, n + 1), sp.zeros(n + 1, 1)
    a[0, 0], a[n, n], b[n] = 1, 1, 1
    for i in range(1, n):
        a[i, i], a[i, i - 1], a[i, i + 1] = 1, -sp.Rational(1, 2), -sp.Rational(1, 2)
    return a.LUsolve(b)[start - lower]


_ANSWER_FUNCS: dict[str, Any] = {
    n: getattr(sp, n)
    for n in (
        "sqrt exp log sin cos tan asin acos atan sinh cosh tanh Abs Rational binomial "
        "factorial Max Min floor ceiling"
    ).split()
}
_ANSWER_FUNCS["ln"] = sp.log
_CONSTANTS: dict[str, Any] = {"pi": sp.pi, "E": sp.E}
_REFERENCE_FUNCS: dict[str, Any] = {
    **{
        n: getattr(sp, n)
        for n in (
            "Sum Integral Matrix KroneckerDelta subfactorial solve diff expand simplify"
        ).split()
    },
    "det": _det,
    "rank": _rank,
    "trace": _trace,
    "max_eigenvalue": _max_eigenvalue,
    "gamblers_ruin": _gamblers_ruin,
}
_REFERENCE_CONSTANTS: dict[str, Any] = {"oo": sp.oo}

_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_UNARY_OPS = (ast.USub, ast.UAdd)


def namespace(*, reference: bool) -> dict[str, Any]:
    """The only names an expression may use, with builtins pinned to empty."""
    # Integer/Float/Symbol are emitted by parse_expr's own transformations. They are not in the
    # AST allowlist, so user text that calls them is rejected before parse_expr ever runs.
    ns: dict[str, Any] = {
        **_ANSWER_FUNCS,
        **_CONSTANTS,
        "Integer": sp.Integer,
        "Float": sp.Float,
        "Symbol": sp.Symbol,
    }
    if reference:
        ns.update(_REFERENCE_FUNCS)
        ns.update(_REFERENCE_CONSTANTS)
    ns["__builtins__"] = {}
    return ns


def _const_value(node: ast.AST) -> Fraction | None:
    """Exact value of a purely numeric subtree, or None if it contains names/calls.

    Raises ParseRejected for numeric subtrees that would be huge, before computing them.
    """
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        if isinstance(node.value, float) and not math.isfinite(node.value):
            raise ParseRejected("numeric literal out of range")
        return Fraction(node.value)
    if isinstance(node, ast.UnaryOp):
        v = _const_value(node.operand)
        return None if v is None else (-v if isinstance(node.op, ast.USub) else v)
    if isinstance(node, ast.BinOp):
        left, right = _const_value(node.left), _const_value(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return None if right == 0 else left / right
        if isinstance(node.op, ast.Pow):
            if right.denominator != 1 or abs(right) > MAX_EXPONENT:
                if abs(right) > MAX_EXPONENT:
                    raise ParseRejected(f"exponent too large (limit {MAX_EXPONENT})")
                return None
            if left == 0:
                return None if right < 0 else Fraction(0)
            digits = abs(math.log10(abs(left))) * abs(int(right))
            if digits > MAX_DIGITS_ESTIMATE:
                raise ParseRejected("numeric power too large")
            return left ** int(right)
    return None


def _validate(tree: ast.Expression, *, reference: bool) -> None:
    funcs = set(_ANSWER_FUNCS) | (set(_REFERENCE_FUNCS) if reference else set())
    expr_nodes = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.expr):
            expr_nodes += 1
            if expr_nodes > MAX_NODES:
                raise ParseRejected("expression too complex")
        if isinstance(node, (ast.Expression, ast.Load)):
            continue
        if isinstance(node, ast.Constant):
            v = node.value
            if type(v) not in (int, float):
                raise ParseRejected("only numbers are allowed as literals")
            if not math.isfinite(v) or abs(v) >= MAX_LITERAL:
                raise ParseRejected("numeric literal out of range")
        elif isinstance(node, ast.Name):
            if node.id.startswith("_") or "__" in node.id or len(node.id) > 24:
                raise ParseRejected(f"bad name {node.id!r}")
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, _BIN_OPS):
                raise ParseRejected(f"operator {type(node.op).__name__} not allowed")
            if isinstance(node.op, ast.Pow):
                if any(
                    isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow)
                    for n in ast.walk(node.right)
                ):
                    raise ParseRejected("power in an exponent is not allowed")
                _const_value(node)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _UNARY_OPS):
                raise ParseRejected(f"operator {type(node.op).__name__} not allowed")
        elif isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in funcs) or node.keywords:
                raise ParseRejected("call to a function that is not allowed")
        elif isinstance(node, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)):
            continue
        elif reference and isinstance(node, (ast.List, ast.Tuple)):
            continue
        elif reference and isinstance(node, ast.Subscript):
            if not (isinstance(node.slice, ast.Constant) and type(node.slice.value) is int):
                raise ParseRejected("only constant integer indexes are allowed")
        else:
            raise ParseRejected(f"disallowed syntax: {type(node).__name__}")


def _local_dict(symbols: Mapping[str, str] | None) -> dict[str, sp.Symbol]:
    out: dict[str, sp.Symbol] = {}
    for name, assumption in (symbols or {}).items():
        if assumption not in ASSUMPTIONS:
            raise ParseRejected(f"unknown assumption {assumption!r}")
        out[name] = (
            sp.Symbol(name) if assumption == "plain" else sp.Symbol(name, **{assumption: True})
        )
    return out


def safe_parse(
    text: str,
    *,
    reference: bool = False,
    symbols: Mapping[str, str] | None = None,
    max_len: int | None = None,
) -> Any:
    """Parse untrusted ``text`` into a SymPy object, or raise ``ParseRejected``.

    ``reference=True`` additionally allows ``Sum``, ``Integral``, ``Matrix``, ``det`` and a few
    other names used by independent reference computations, plus lists, tuples and constant
    indexing. ``symbols`` maps variable names to assumptions (``positive``, ``real``, ...).
    """
    if not isinstance(text, str):
        raise ParseRejected("expression must be a string")
    limit = max_len if max_len is not None else (MAX_REFERENCE_LEN if reference else MAX_LEN)
    if len(text) > limit:
        raise ParseRejected(f"length {len(text)} exceeds limit {limit}")
    src = text.strip().replace("^", "**")
    if not src:
        raise ParseRejected("empty expression")
    try:
        tree = ast.parse(src, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise ParseRejected(f"not a valid expression: {type(exc).__name__}") from exc
    _validate(tree, reference=reference)
    try:
        return parse_expr(
            src,
            local_dict=_local_dict(symbols),
            global_dict=namespace(reference=reference),
            transformations=standard_transformations + (rationalize,),
        )
    except ParseRejected:
        raise
    except Exception as exc:  # noqa: BLE001 - any SymPy failure means "cannot use this text"
        raise ParseRejected(
            f"could not evaluate expression: {type(exc).__name__}: {exc}"[:200]
        ) from exc
