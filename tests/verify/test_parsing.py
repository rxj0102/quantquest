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


@pytest.mark.parametrize(
    "text",
    ["(10**1000)**2", "(10**500)**2", "(10**309)**1", "((10**1000)**1)**1", "(10**1000)**3"],
)
def test_huge_numeric_base_only_ever_raises_parse_rejected(text: str) -> None:
    """math.log10 of a Fraction past the float range raised OverflowError out of safe_parse."""
    try:
        safe_parse(text)
    except ParseRejected:
        pass


def test_huge_numeric_base_to_a_large_power_is_rejected() -> None:
    with pytest.raises(ParseRejected):
        safe_parse("(10**1000)**3")


@pytest.mark.parametrize(
    "text",
    [
        "(1/10**400)**2",
        "(10**-400)**2",
        "(1/10**324)**2",
        "(1e-300*1e-300)**2",
        "(0.1**400)**2",
        "((1/3)**1000)**2",
        "(3**1000/7**1000)**2",
    ],
)
def test_tiny_numeric_base_only_ever_raises_parse_rejected(text: str) -> None:
    """A base below the float range converted to 0.0 and math.log10 raised ValueError."""
    try:
        safe_parse(text)
    except ParseRejected:
        pass


def test_tiny_numeric_base_to_a_large_power_is_rejected() -> None:
    with pytest.raises(ParseRejected):
        safe_parse("(1/10**1000)**3")


# --- gap-killing tests found by scripts/mutation/parsing.sh (see scripts/mutation/SURVIVORS.md)


def test_length_limits_are_exact_for_answers_and_references() -> None:
    from verify.parsing import MAX_REFERENCE_LEN

    # Padding with spaces keeps the expression itself trivial, so only the length check can fire.
    assert safe_parse("1" + " " * (MAX_LEN - 1)) == 1
    with pytest.raises(ParseRejected, match="length"):
        safe_parse("1" + " " * MAX_LEN)
    assert safe_parse("1" + " " * (MAX_REFERENCE_LEN - 1), reference=True) == 1
    with pytest.raises(ParseRejected, match="length"):
        safe_parse("1" + " " * MAX_REFERENCE_LEN, reference=True)


def test_literal_limit_is_exact() -> None:
    assert safe_parse(str(10**18 - 1)) == 10**18 - 1
    for text in (str(10**18), str(10**20)):
        with pytest.raises(ParseRejected, match="out of range"):
            safe_parse(text)


@pytest.mark.parametrize("text", ["99999**1000", "(10**4)**1000", "(1/10**4)**1000"])
def test_numeric_power_that_would_be_huge_is_rejected_before_it_is_computed(text: str) -> None:
    with pytest.raises(ParseRejected, match="too large"):
        safe_parse(text)


def test_named_regressions_from_the_survivor_list() -> None:
    """Inputs named in the first mutation run. 2**(1/0) is not a contract violation on the
    current code (it parses to nan); the point is that it never raises anything but ParseRejected.
    The other two are rejected on the exponent limit, which the mutants skipped."""
    try:
        safe_parse("2**(1/0)")
    except ParseRejected:
        pass
    with pytest.raises(ParseRejected, match="exponent too large"):
        safe_parse("2**(5 - -1001)")
    with pytest.raises(ParseRejected, match="too large"):
        safe_parse("99999**1000")


@pytest.mark.parametrize("name", ["_x", "x_", "__x", "a__b", "x" * 25, "x" * 30])
def test_bad_names_are_rejected(name: str) -> None:
    if name == "x_":  # a single trailing underscore is an ordinary name
        assert safe_parse(name) == sp.Symbol(name)
        return
    with pytest.raises(ParseRejected, match="bad name"):
        safe_parse(name)


def test_name_length_limit_is_exact() -> None:
    assert safe_parse("x" * 24) == sp.Symbol("x" * 24)


@pytest.mark.parametrize("text", ["Integer(5)", "Float(1.5)", "Symbol('x')"])
def test_parser_internal_constructors_are_not_callable_by_users(text: str) -> None:
    with pytest.raises(ParseRejected, match="not allowed"):
        safe_parse(text)


@pytest.mark.parametrize("text", ["(1, 2)", "[1, 2]", "(1,)", "[]"])
def test_sequences_are_rejected_in_answers_but_allowed_in_references(text: str) -> None:
    with pytest.raises(ParseRejected, match="disallowed syntax"):
        safe_parse(text)
    safe_parse(text, reference=True)


def test_reference_index_must_be_a_constant_integer() -> None:
    assert safe_parse("[5, 7][1]", reference=True) == 7
    for text in ("[5, 7][1+0]", "[5, 7][0.0]", "[5, 7][x]"):
        with pytest.raises(ParseRejected, match="constant integer"):
            safe_parse(text, reference=True)


def test_reference_names_are_not_visible_to_answers() -> None:
    answer = safe_parse("oo + 1")
    assert answer != sp.oo
    assert sp.Symbol("oo") in answer.free_symbols
    assert safe_parse("oo + 1", reference=True) == sp.oo


def test_unknown_symbol_assumption_is_rejected() -> None:
    with pytest.raises(ParseRejected, match="unknown assumption"):
        safe_parse("x + 1", symbols={"x": "weird"})
