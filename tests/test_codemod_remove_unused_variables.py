"""remove_unused_variables transform: drop VAR declarations never referenced.

Conservative: only removes a VAR whose name does not appear anywhere after its
declaration (case-insensitive identifier match). Preserves all referenced VARs
and the RETURN. Skips expressions with no VAR/RETURN structure.
"""

from types import SimpleNamespace

from core.codemods.transforms.remove_unused_variables import RemoveUnusedVariables


def _model(expr):
    m = SimpleNamespace(name="M", expression=expr)
    t = SimpleNamespace(name="T", measures=[m])
    return SimpleNamespace(tables=[t])


def _after(expr):
    out = RemoveUnusedVariables().preview(_model(expr), {})
    return out.edits[0].after if out.edits else None


def test_unused_var_removed():
    expr = "VAR x = 1\nVAR y = 2\nRETURN y"
    after = _after(expr)
    assert after is not None
    assert "VAR x" not in after
    assert "VAR y = 2" in after
    assert "RETURN y" in after


def test_all_vars_used_not_touched():
    assert _after("VAR x = 1\nVAR y = 2\nRETURN x + y") is None


def test_no_var_structure_not_touched():
    assert _after("SUM('T'[A])") is None


def test_var_used_only_in_another_var():
    # x is used by y; both kept.
    expr = "VAR x = 1\nVAR y = x + 2\nRETURN y"
    assert _after(expr) is None


def test_var_name_substring_not_false_positive():
    # 'tax' contains 'ax' but VAR ax is genuinely unused — must be removed,
    # and 'tax' (a different identifier) must not count as a use of 'ax'.
    expr = "VAR ax = 1\nVAR tax = 5\nRETURN tax"
    after = _after(expr)
    assert after is not None
    assert "VAR ax" not in after
    assert "VAR tax = 5" in after


def test_var_referenced_inside_string_not_counted_as_use():
    # The only occurrence of `unused` after its decl is inside a string literal,
    # which is not a real reference — so it should be removed.
    expr = 'VAR unused = 1\nVAR used = 2\nRETURN used & "unused text"'
    after = _after(expr)
    assert after is not None
    assert "VAR unused" not in after
