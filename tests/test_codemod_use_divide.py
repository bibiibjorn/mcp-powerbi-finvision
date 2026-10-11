"""use_divide transform: rewrite safe binary `a / b` to DIVIDE(a, b).

Conservative by design (a refactor must NEVER emit wrong DAX): only rewrites
when an expression — or a parenthesized sub-expression — is a single top-level
binary division whose operands are balanced. Ambiguous precedence cases
(e.g. a + b / c) are left unchanged with a warning.
"""

from types import SimpleNamespace

from core.codemods.transforms.use_divide import UseDivide


def _model(expr):
    m = SimpleNamespace(name="M", expression=expr)
    t = SimpleNamespace(name="T", measures=[m])
    return SimpleNamespace(tables=[t])


def _after(expr):
    out = UseDivide().preview(_model(expr), {})
    return out.edits[0].after if out.edits else None


def test_simple_division_rewritten():
    assert _after("[Sales] / [Count]") == "DIVIDE([Sales], [Count])"


def test_qualified_operands_rewritten():
    assert _after("'F'[Amt] / 'F'[Qty]") == "DIVIDE('F'[Amt], 'F'[Qty])"


def test_parenthesized_division_rewritten():
    # The whole expr is a parenthesized single division.
    assert _after("([Sales] / [Count])") == "DIVIDE([Sales], [Count])"


def test_already_divide_not_touched():
    assert _after("DIVIDE([Sales], [Count])") is None


def test_no_division_not_touched():
    assert _after("[Sales] + [Count]") is None


def test_ambiguous_precedence_left_alone():
    # a + b / c : the division operands are b and c, NOT the whole expression.
    # Conservative transform must NOT rewrite this (would change semantics).
    assert _after("[A] + [B] / [C]") is None


def test_division_inside_function_not_naively_rewritten():
    # Whole expr is a function call, not a top-level binary division.
    assert _after("SUM([A]) / SUM([B])") == "DIVIDE(SUM([A]), SUM([B]))"


def test_string_literal_with_slash_not_rewritten():
    assert _after('"a/b"') is None
