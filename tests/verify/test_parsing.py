"""safe_parse is the only way untrusted text becomes a SymPy object."""

import pytest
import sympy as sp

from verify.parsing import MAX_LEN, ParseRejected, safe_parse


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1/9", sp.Rational(1, 9)),
        ("3.92", sp.Rational(392, 100)),
        ("2^3", sp.Integer(8)),
        ("-5", sp.Integer(-5)),
        ("2/lam**2", 2 / sp.Symbol("lam") ** 2),
        (
            "(a*d - b*c)**2",
            (sp.Symbol("a") * sp.Symbol("d") - sp.Symbol("b") * sp.Symbol("c")) ** 2,
        ),
        ("sqrt(2)", sp.sqrt(2)),
        ("exp(-x)", sp.exp(-sp.Symbol("x"))),
        ("pi/4", sp.pi / 4),
        ("binomial(5, 2)", sp.Integer(10)),
    ],
)
def test_parses_ordinary_answers(text: str, expected: sp.Expr) -> None:
    assert sp.simplify(safe_parse(text) - expected) == 0


def test_decimals_are_exact_rationals() -> None:
    assert safe_parse("0.1") == sp.Rational(1, 10)


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('true')",
        "().__class__",
        "x.__class__",
        "(1).real",
        "open('/etc/passwd')",
        "eval('1')",
        "exec('1')",
        "compile('1', 'a', 'eval')",
        "getattr(x, 'y')",
        "lambda: 1",
        "[c for c in range(3)]",
        "1 if x else 2",
        "'abc'",
        "x; y",
        "import os",
        "x = 1",
        "x[0]",
        "f(x)",  # undefined function: only whitelisted functions may be called
        "os.system('true')",
        "{1: 2}",
        "x if",
        "lambda",  # Python keyword, cannot be a variable name
        "",
        "   ",
    ],
)
def test_rejects_code_like_input(text: str) -> None:
    with pytest.raises(ParseRejected):
        safe_parse(text)


@pytest.mark.parametrize(
    "text",
    ["9**9**9", "10**100000", "2**(999*999*999)", "x**(2**20)", "x**(9**9)", "2**1001"],
)
def test_rejects_expression_bombs(text: str) -> None:
    with pytest.raises(ParseRejected):
        safe_parse(text)


def test_length_limit() -> None:
    safe_parse("1+" * ((MAX_LEN - 1) // 2) + "1")  # within the limit
    with pytest.raises(ParseRejected, match="length"):
        safe_parse("1" * (MAX_LEN + 1))
    with pytest.raises(ParseRejected, match="length"):
        safe_parse("1+" * 5000 + "1")


def test_node_count_limit() -> None:
    with pytest.raises(ParseRejected, match="complex"):
        safe_parse("+".join(["x"] * 150), max_len=1000)


def test_huge_literal_rejected() -> None:
    with pytest.raises(ParseRejected):
        safe_parse("1" + "0" * 40)


def test_non_str_rejected() -> None:
    with pytest.raises(ParseRejected):
        safe_parse(123)  # type: ignore[arg-type]


def test_builtins_are_not_reachable_through_the_namespace() -> None:
    """parse_expr's eval would inject real builtins if __builtins__ were not pinned to {}."""
    from verify.parsing import namespace

    ns = namespace(reference=False)
    assert ns["__builtins__"] == {}
    assert "open" not in ns and "__import__" not in ns


def test_reference_only_names_not_available_to_answers() -> None:
    with pytest.raises(ParseRejected):
        safe_parse("Integral(x, (x, 0, 1))")
    with pytest.raises(ParseRejected):
        safe_parse("Matrix([[1]])")
    with pytest.raises(ParseRejected):
        safe_parse("x[0]")


def test_reference_mode_evaluates_independent_computations() -> None:
    e = safe_parse("det(Matrix([[2, 1], [5, 3]]))", reference=True, max_len=600)
    assert e == 1
    assert safe_parse("Sum(k, (k, 1, 4))", reference=True).doit() == 10
    assert safe_parse("solve(diff(10*log(l) - 5*l, l), l)[0]", reference=True) == 2


def test_reference_mode_still_rejects_code() -> None:
    for bad in ["__import__('os')", "().__class__", "open('x')", "lambda: 1", "9**9**9"]:
        with pytest.raises(ParseRejected):
            safe_parse(bad, reference=True)


def test_declared_symbol_assumptions() -> None:
    e = safe_parse(
        "Integral(x**2*lam*exp(-lam*x), (x, 0, oo))",
        reference=True,
        symbols={"x": "positive", "lam": "positive"},
    )
    assert sp.simplify(e.doit() - 2 / sp.Symbol("lam", positive=True) ** 2) == 0


@pytest.mark.parametrize("text", ["2**1e999", "1e999**2", "2**(1e999)", "(1e999+1)**2"])
def test_non_finite_float_literal_in_a_power_is_a_clean_rejection(text: str) -> None:
    """safe_parse may only raise ParseRejected. A float literal that overflows to inf used to
    escape from the exact-value pre-check as an OverflowError."""
    with pytest.raises(ParseRejected):
        safe_parse(text)
