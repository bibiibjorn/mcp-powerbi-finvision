"""F-20a: ``04_Run_DAX mode=build`` - measures + group_by + filters -> SUMMARIZECOLUMNS."""

from __future__ import annotations

import pytest

from core.debug.dax_query_builder import BuildError, build_query
from server.handlers import query_handler as qh


def test_minimal_build():
    out = build_query(measures=["Total Sales"], group_by=["Date[Year]"])
    assert out["query"] == (
        "EVALUATE\nSUMMARIZECOLUMNS(\n    'Date'[Year],\n    \"Total Sales\", [Total Sales]\n)"
    )
    assert out["group_by"] == ["'Date'[Year]"] and out["measures"] == ["Total Sales"]


def test_measure_forms_and_aliases():
    out = build_query(
        measures=["[Sales]", {"name": "Margin %", "expression": "DIVIDE([Margin], [Sales])"}],
        group_by=["'Product Line'[Name]"],
    )
    assert '"Sales", [Sales]' in out["query"]
    assert '"Margin %", DIVIDE([Margin], [Sales])' in out["query"]
    assert "'Product Line'[Name]" in out["query"]
    with pytest.raises(BuildError, match="duplicate"):
        build_query(measures=["A", "[A]"])


def test_filters_dict_and_string_forms():
    q = build_query(
        measures=["Sales"],
        group_by=["Date[Year]"],
        filters=[
            {"column": "Date[Year]", "operator": "=", "values": [2024, 2025]},
            'Product[Color] in {"Red", "Blue"}',
            'Product[Size] <> "XL"',
            {"column": "Sales[Amount]", "operator": "between", "values": [10, 20.5]},
            {"column": "Sales[Qty]", "operator": ">=", "value": 5},
            {"column": "Date[Day]", "operator": "=", "values": [{"dax": "DATE(2025,1,1)"}]},
            {"column": "Customer[Active]", "values": [True]},
            "KEEPFILTERS(TREATAS({1}, 'X'[Y]))",
        ],
    )["query"]
    assert "TREATAS({2024, 2025}, 'Date'[Year])" in q
    assert 'TREATAS({"Red", "Blue"}, \'Product\'[Color])' in q
    assert "FILTER(ALL('Product'[Size]), 'Product'[Size] <> \"XL\")" in q
    assert "FILTER(ALL('Sales'[Amount]), 'Sales'[Amount] >= 10 && 'Sales'[Amount] <= 20.5)" in q
    assert "FILTER(ALL('Sales'[Qty]), 'Sales'[Qty] >= 5)" in q
    assert "TREATAS({DATE(2025,1,1)}, 'Date'[Day])" in q
    assert "TREATAS({TRUE()}, 'Customer'[Active])" in q
    assert q.index("TREATAS({2024") < q.index(
        '"Sales", [Sales]'
    )  # filters come before the measures


def test_not_in_and_string_escaping():
    q = build_query(
        measures=["S"], filters=[{"column": "P[N]", "operator": "not in", "values": ['a"b', "c"]}]
    )["query"]
    assert 'FILTER(ALL(\'P\'[N]), NOT(\'P\'[N] IN {"a""b", "c"}))' in q


def test_order_by():
    q = build_query(
        measures=["Total Sales"],
        group_by=["Date[Year]"],
        order_by=[
            "Date[Year] desc",
            "[Total Sales] ASC",
            {"by": "Total Sales", "direction": "desc"},
        ],
    )["query"]
    assert q.endswith("ORDER BY 'Date'[Year] DESC, [Total Sales] ASC, [Total Sales] DESC")


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({}, "at least one"),
        ({"measures": "Sales"}, "must be a list"),
        ({"measures": [""]}, "empty measure"),
        ({"measures": [{"name": "x"}]}, "name and expression"),
        ({"group_by": ["Year"]}, r"must look like Table"),
        ({"measures": ["S"], "filters": ["just words"]}, "neither"),
        (
            {"measures": ["S"], "filters": [{"column": "A[B]", "operator": "like", "values": [1]}]},
            "operator",
        ),
        (
            {
                "measures": ["S"],
                "filters": [{"column": "A[B]", "operator": "between", "values": [1]}],
            },
            "two values",
        ),
        (
            {"measures": ["S"], "filters": [{"column": "A[B]", "operator": ">", "values": [1, 2]}]},
            "exactly one",
        ),
        ({"measures": ["S"], "filters": [5]}, "string or an object"),
    ],
)
def test_bad_specs(kwargs, needle):
    with pytest.raises(BuildError, match=needle):
        build_query(**kwargs)


# ---------------------------------------------------------------------------
# 04_Run_DAX mode=build
# ---------------------------------------------------------------------------
def test_mode_build_returns_the_query_without_a_query_argument_or_connection(monkeypatch):
    called = []
    monkeypatch.setattr(qh, "_run_evaluate_and_log", lambda *a, **k: called.append(1))
    out = qh.execute_dax({"mode": "build", "measures": ["Total Sales"], "group_by": ["Date[Year]"]})
    assert out["success"] and out["mode"] == "build" and out["executed"] is False
    assert out["query"].startswith("EVALUATE\nSUMMARIZECOLUMNS(") and not called


def test_mode_build_reports_a_bad_spec_as_invalid_args():
    out = qh.execute_dax({"mode": "build", "measures": "x"})
    assert out["success"] is False and out["error_type"] == "invalid_args"


def test_mode_build_execute_runs_the_built_query_with_the_remaining_args(monkeypatch):
    seen = {}
    real = qh.execute_dax

    def spy(args, *, source="04_Run_DAX"):
        if args.get("mode") == "build":
            return real(args, source=source)
        seen["args"] = args
        return {"success": True, "rows": [{"x": 1}]}

    monkeypatch.setattr(qh, "execute_dax", spy)
    out = qh.execute_dax(
        {"mode": "build", "measures": ["S"], "execute": True, "top_n": 5, "bypass_cache": True}
    )
    assert out["success"] and out["built_query"].startswith("EVALUATE")
    assert out["built_from"]["measures"] == ["S"]
    assert (
        seen["args"]["mode"] == "auto"
        and seen["args"]["top_n"] == 5
        and seen["args"]["bypass_cache"] is True
    )
    assert "measures" not in seen["args"] and seen["args"]["query"] == out["built_query"]


def test_build_is_declared_and_read_classified():
    from server.op_classification import classify
    from server.registry import get_registry
    from server.handlers import register_all_handlers

    reg = get_registry()
    if reg.total_tools() == 0:
        register_all_handlers(reg)
    schema = reg.get_full_schema("04_Run_DAX")["properties"]
    assert "build" in schema["mode"]["enum"]
    for arg in ("measures", "group_by", "filters", "order_by", "execute"):
        assert arg in schema
    assert not reg.get_tool_def("04_Run_DAX").input_schema.get("required")
    assert classify("04_Run_DAX", {"mode": "build"}) == "read"
