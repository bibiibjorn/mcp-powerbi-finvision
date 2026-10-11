"""Regression: VAR extraction must not mangle table-qualified column refs (P1).

_insert_var_and_replace replaced a bare [Name] ref with the VAR name using a
single-char negative lookbehind, which does NOT exclude a table qualifier
ending in a quote (e.g. 'Sales'[Amount]). So extracting [Amount] would rewrite
'Sales'[Amount] -> 'Sales'_Amount, producing invalid DAX.
"""

from core.dax.optimizer.strategies.variable_extraction import VariableExtractionStrategy


def _balanced_brackets(s: str) -> bool:
    return s.count("[") == s.count("]")


def test_qualified_ref_after_decl_not_partially_replaced():
    # No RETURN: the VAR decl is prepended, so the whole expression body is the
    # after-decl region. A qualified column ref 'Sales'[Amount] there must NOT
    # become 'Sales'_Amount (invalid DAX).
    dax = "'Sales'[Amount] * 2"
    out = VariableExtractionStrategy._insert_var_and_replace(dax, "[Amount]", "_Amount")
    # E-07: token-based — there is no LONE [Amount] here, so nothing is hoisted.
    if out is None:
        return
    assert "'Sales'_Amount" not in out, f"qualified column ref mangled: {out}"
    assert "'Sales'[Amount]" in out, f"qualified ref lost: {out}"
    assert _balanced_brackets(out), f"unbalanced brackets: {out}"


def test_qualified_ref_after_return_not_replaced():
    dax = "SUMX(T, [Amount]) RETURN 'Sales'[Amount]"
    out = VariableExtractionStrategy._insert_var_and_replace(dax, "[Amount]", "_Amount")
    assert out is not None
    assert "'Sales'_Amount" not in out, f"qualified column ref mangled: {out}"
    assert _balanced_brackets(out), f"unbalanced brackets: {out}"


def test_bare_ref_still_replaced():
    # A genuine bare [Amount] after the decl SHOULD be replaced.
    dax = "[Amount] * 2"
    out = VariableExtractionStrategy._insert_var_and_replace(dax, "[Amount]", "_Amount")
    assert out is not None
    # The body's [Amount] becomes _Amount; the decl keeps [Amount].
    assert out.count("_Amount * 2") == 1, f"bare ref not replaced: {out}"
