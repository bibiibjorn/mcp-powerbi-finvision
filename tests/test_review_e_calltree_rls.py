"""2026-09-22 review E-15 (call tree is linear) and E-16 (RLS simulation wrapping)."""

from __future__ import annotations

import time

from core.dax.call_tree_builder import CallTreeBuilder, NodeType
from core.dax.rls_rewriter import build_as_user_query, rewrite_filter_expression


def _count(n):
    return 1 + sum(_count(c) for c in n.children)


def test_e15_nested_if_chain_is_linear():
    depth = 18
    dax = "IF(TRUE(), " * depth + "SUM(Sales[Amount])" + ", 0)" * depth
    t = time.perf_counter()
    root = CallTreeBuilder().build_call_tree(dax)
    assert time.perf_counter() - t < 1.0
    # root + (IF + TRUE) per level + the SUM  (was 1,048,574 nodes)
    assert _count(root) == 2 * depth + 2


def test_e15_nested_calls_appear_once_and_strings_are_ignored():
    root = CallTreeBuilder().build_call_tree(
        'VAR a = CALCULATE([M], T[c] = "x( [NotARef]") RETURN a + [N]'
    )
    var = root.children[0]
    assert var.node_type == NodeType.VARIABLE
    calc = var.children[0]
    assert calc.function_name == "CALCULATE" and calc.expression.startswith("CALCULATE([M]")
    assert [c.function_name for c in calc.children] == ["[M]"]
    assert [c.function_name for c in root.children[1:]] == ["[N]"]


def test_e15_nested_var_block_not_duplicated():
    root = CallTreeBuilder().build_call_tree("VAR a = CALCULATE(VAR b = 1 RETURN b) RETURN a")
    names = [c.function_name for c in root.children]
    assert names == ["VAR a"]


def test_e16_order_by_stays_outside_the_wrapper():
    q = build_as_user_query(
        "EVALUATE Sales ORDER BY Sales[Amount] DESC",
        [{"table": "Sales", "filter_expression": "Sales[u] = USERPRINCIPALNAME()"}],
        "v@x.c",
    )
    assert q.rstrip().endswith("ORDER BY Sales[Amount] DESC")
    assert "Sales ORDER BY" not in q


def test_e16_define_string_with_evaluate_does_not_split():
    q = build_as_user_query(
        'DEFINE MEASURE Sales[x] = "EVALUATE" EVALUATE ROW("v", [x])',
        [{"table": "Sales", "filter_expression": "Sales[u] = USERPRINCIPALNAME()"}],
        "v@x.c",
    )
    assert q.startswith('DEFINE MEASURE Sales[x] = "EVALUATE" ')
    assert q.count("CALCULATETABLE(") == 1


def test_e16_each_evaluate_wrapped_separately_and_comment_safe():
    q = build_as_user_query(
        "EVALUATE A // first\nEVALUATE B",
        [{"table": "T", "filter_expression": "T[u] = USERNAME()"}],
        "v@x.c",
    )
    assert q.count("EVALUATE\nCALCULATETABLE(") == 2
    # the separator comma is on its own line, after the body's line comment
    assert "A // first\n  ,\n" in q


def test_e16_userobjectid_and_customdata_are_reported():
    notes = []
    out = rewrite_filter_expression(
        "USEROBJECTID() = [o] && CUSTOMDATA() = [c]", "u@x", notes=notes
    )
    assert out == '"u@x" = [o] && "" = [c]'
    assert len(notes) == 2
    out = rewrite_filter_expression(
        "USEROBJECTID() = [o] && CUSTOMDATA() = [c] // USERNAME()",
        "u@x",
        object_id="oid-1",
        custom_data="EU",
    )
    assert out == '"oid-1" = [o] && "EU" = [c] // USERNAME()'
