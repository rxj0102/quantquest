#!/usr/bin/env bash
# Mutation run for verify/parsing.py (safe_parse: the allowlist, the limits, the empty builtins).
# Tests run: the module's own tests, the exact/symbolic checks that call it, and typed answers.
# Run it alone: it edits source files temporarily. See scripts/mutation/README.md.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
F=verify/parsing.py
T="tests/verify/test_parsing.py tests/verify/test_sympy_check.py tests/test_answers.py"
# shellcheck disable=SC2086
baseline $T
# shellcheck disable=SC2086
P() { M "$F" "$1" $T; }
# --- limits
P 's|^MAX_LEN = 200|MAX_LEN = 2000|'
P 's|^MAX_REFERENCE_LEN = 600|MAX_REFERENCE_LEN = 6000|'
P 's|^MAX_NODES = 250|MAX_NODES = 2500|'
P 's|^MAX_EXPONENT = 1000|MAX_EXPONENT = 10000|'
P 's|^MAX_LITERAL = 10\*\*18|MAX_LITERAL = 10**30|'
P 's|^MAX_DIGITS_ESTIMATE = 2000|MAX_DIGITS_ESTIMATE = 200000|'
P 's|    if len(text) > limit:|    if len(text) >= limit:|'
P 's|            if expr_nodes > MAX_NODES:|            if False:|'
# --- input checks in safe_parse
P 's|    if not isinstance(text, str):|    if False:|'
P 's|\.replace("^", "\*\*")||'
P 's|    if not src:|    if False:|'
# --- names
P 's|            if node.id.startswith("_") or "__" in node.id or len(node.id) > 24:|            if False:|'
P 's|node.id.startswith("_") or ||'
P 's| or "__" in node.id||'
P 's|len(node.id) > 24|len(node.id) > 2400|'
# --- literals
P 's|            if type(v) not in (int, float):|            if False:|'
P 's|            if not math.isfinite(v) or abs(v) >= MAX_LITERAL:|            if False:|'
P 's|abs(v) >= MAX_LITERAL|abs(v) > MAX_LITERAL|'
# --- operators and calls
P 's|            if not isinstance(node.op, _BIN_OPS):|            if False:|'
P 's|            if not isinstance(node.op, _UNARY_OPS):|            if False:|'
P 's|raise ParseRejected("power in an exponent is not allowed")|pass|'
P 's|^                _const_value(node)$|                pass|'
P 's|            if not (isinstance(node.func, ast.Name) and node.func.id in funcs) or node.keywords:|            if False:|'
P 's| or node.keywords:|:|'
# --- answer mode versus reference mode
P 's|        elif reference and isinstance(node, (ast.List, ast.Tuple)):|        elif isinstance(node, (ast.List, ast.Tuple)):|'
P 's|        elif reference and isinstance(node, ast.Subscript):|        elif isinstance(node, ast.Subscript):|'
P 's|            if not (isinstance(node.slice, ast.Constant) and type(node.slice.value) is int):|            if False:|'
P 's|^    if reference:$|    if True:|'
P 's|    ns\["__builtins__"\] = {}|    pass|'
P 's|if assumption not in ASSUMPTIONS:|if False:|'
# --- the constant evaluator that catches numeric bombs
P 's|abs(right) > MAX_EXPONENT:|abs(right) > MAX_EXPONENT * 1000:|'
P 's|            if digits > MAX_DIGITS_ESTIMATE:|            if False:|'
P 's|            return None if right == 0 else left / right|            return left / right|'
P 's|        return None if v is None else (-v if isinstance(node.op, ast.USub) else v)|        return None if v is None else v|'
# --- error wrapping
P 's|    except ParseRejected:|    except KeyError:|'
summary
