"""Regression: swap_table_reference quotes table names with spaces (P1).

_sub always emitted f"{new}[{col}]" unquoted, so swapping to a table named
"Fact Sales" produced "Fact Sales[Amount]" — invalid DAX. A name with spaces
or special chars must be single-quoted.
"""

from types import SimpleNamespace

from core.codemods.transforms.swap_table_reference import SwapTableReference


def _model(expr):
    m = SimpleNamespace(name="M", expression=expr)
    tbl = SimpleNamespace(name="T", measures=[m])
    return SimpleNamespace(tables=[tbl])


def test_new_table_with_space_is_quoted():
    model = _model("SUM(Sales[Amount])")
    out = SwapTableReference().preview(model, {"from_table": "Sales", "to_table": "Fact Sales"})
    assert len(out.edits) == 1
    after = out.edits[0].after
    assert "'Fact Sales'[Amount]" in after, f"table name not quoted: {after}"
    assert "Fact Sales[Amount]" not in after.replace("'Fact Sales'", ""), after


def test_quoted_old_to_spaced_new():
    model = _model("SUM('Sales'[Amount])")
    out = SwapTableReference().preview(model, {"from_table": "Sales", "to_table": "Fact Sales"})
    assert len(out.edits) == 1
    assert "'Fact Sales'[Amount]" in out.edits[0].after


def test_simple_name_stays_unquoted():
    model = _model("SUM(Sales[Amount])")
    out = SwapTableReference().preview(model, {"from_table": "Sales", "to_table": "FactSales"})
    assert len(out.edits) == 1
    assert "FactSales[Amount]" in out.edits[0].after
    assert "'FactSales'" not in out.edits[0].after
