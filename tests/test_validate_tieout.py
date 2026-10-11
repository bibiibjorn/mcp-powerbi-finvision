"""F-09: tie-out / additivity audit (core/validation/tieout.py) against fake rows and a fake builder."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.validation import tieout as to

COUNTRY, CITY = "'Geo'[Country]", "'Geo'[City]"
LEVELS = [[COUNTRY], [CITY]]
FLAGS = ["IsGrandTotalRowTotal", "IsDM1Total"]


def _row(g, dm1, country, city, **vals):
    row = {
        "[IsGrandTotalRowTotal]": g,
        "[IsDM1Total]": dm1,
        "Geo[Country]": country,
        "Geo[City]": city,
    }
    row.update({f"[{k}]": v for k, v in vals.items()})
    return row


def _matrix(nl_subtotal=10, grand=30, ratio=None):
    ratio = ratio or {}
    return [
        _row(True, True, None, None, Sales=grand, Margin=ratio.get("grand", 0.5)),
        _row(False, True, "BE", None, Sales=20, Margin=ratio.get("be", 0.5)),
        _row(False, False, "BE", "Brussels", Sales=12, Margin=0.4),
        _row(False, False, "BE", "Antwerp", Sales=8, Margin=0.6),
        _row(False, True, "NL", None, Sales=nl_subtotal, Margin=0.3),
        _row(False, False, "NL", "Amsterdam", Sales=10, Margin=0.3),
    ]


def _check(rows, **kw):
    return to.check_hierarchy(
        rows,
        levels=LEVELS,
        flags=FLAGS,
        measures={"Sales": "Sales", "Margin": "Margin"},
        non_additive=["Margin"],
        **kw,
    )


def test_matrix_ties_and_non_additive_is_skipped():
    res = _check(_matrix())
    assert res["ties"] is True and res["break_count"] == 0
    assert res["skipped_non_additive"] == ["Margin"] and res["measures_checked"] == ["Sales"]
    assert [lv["level"] for lv in res["levels_checked"]] == [0, 1]


def test_first_break_names_level_and_measure():
    res = _check(_matrix(nl_subtotal=11, grand=31))
    assert res["ties"] is False
    fb = res["first_break"]
    # the grand total (31) equals its children (20 + 11), the NL subtotal (11) does not equal its detail (10)
    assert fb["level"] == 1 and fb["measure"] == "Sales" and fb["level_name"] == COUNTRY
    assert fb["parent_value"] == 11 and fb["children_sum"] == 10 and fb["difference"] == 1
    assert fb["parent_key"] == {COUNTRY: "NL"}
    assert res["per_measure"]["Sales"]["first_break_level"] == 1


def test_grand_total_break_reported_at_level_zero():
    res = _check(_matrix(grand=99))
    assert res["first_break"]["level"] == 0 and res["first_break"]["level_name"] == "<grand total>"


def test_ratio_measure_breaks_when_not_listed_non_additive():
    res = to.check_hierarchy(
        _matrix(ratio={"grand": 0.9}),
        levels=LEVELS,
        flags=FLAGS,
        measures={"Margin": "Margin"},
        expressions={"Margin": "DIVIDE([Profit], [Sales])"},
    )
    assert res["ties"] is False
    assert "ratio" in res["per_measure"]["Margin"]["cause_hint"]


def test_tolerance_absolute_and_relative():
    rows = _matrix(grand=30.004)
    assert to.check_hierarchy(
        rows, levels=LEVELS, flags=FLAGS, measures={"Sales": "Sales"}, tolerance=0.01
    )["ties"]
    assert not to.check_hierarchy(
        rows, levels=LEVELS, flags=FLAGS, measures={"Sales": "Sales"}, tolerance=0.001
    )["ties"]


def test_flat_table_grand_total_vs_detail():
    rows = [
        {"[IsGrandTotalRowTotal]": True, "T[a]": None, "[S]": 15},
        {"[IsGrandTotalRowTotal]": False, "T[a]": "x", "[S]": 5},
        {"[IsGrandTotalRowTotal]": False, "T[a]": "y", "[S]": 10},
    ]
    res = to.check_hierarchy(
        rows, levels=[["'T'[a]"]], flags=["IsGrandTotalRowTotal"], measures={"S": "S"}
    )
    assert res["ties"] is True
    rows[0]["[S]"] = 16
    assert (
        to.check_hierarchy(
            rows, levels=[["'T'[a]"]], flags=["IsGrandTotalRowTotal"], measures={"S": "S"}
        )["ties"]
        is False
    )


def test_matrix_column_field_is_part_of_the_parent_key():
    rows = []
    for yr in (2024, 2025):
        rows.append({"[IsGrandTotalRowTotal]": True, "D[Year]": yr, "Geo[Country]": None, "[S]": 3})
        rows.append(
            {"[IsGrandTotalRowTotal]": False, "D[Year]": yr, "Geo[Country]": "BE", "[S]": 1}
        )
        rows.append(
            {"[IsGrandTotalRowTotal]": False, "D[Year]": yr, "Geo[Country]": "NL", "[S]": 2}
        )
    kw = dict(
        levels=[[COUNTRY]],
        flags=["IsGrandTotalRowTotal"],
        measures={"S": "S"},
        fixed_cols=["'D'[Year]"],
    )
    assert to.check_hierarchy(rows, **kw)["ties"] is True
    rows[3]["[S]"] = 4  # the 2025 grand total (rows[3]) no longer equals 1 + 2
    res = to.check_hierarchy(rows, **kw)
    assert res["ties"] is False and res["break_count"] == 1
    assert res["first_break"]["parent_key"] == {"'D'[Year]": 2025}


def test_no_subtotal_rows_is_inconclusive_not_green():
    rows = [{"[IsGrandTotalRowTotal]": False, "Geo[Country]": "BE", "[S]": 1}]
    res = to.check_hierarchy(
        rows, levels=[[COUNTRY]], flags=["IsGrandTotalRowTotal"], measures={"S": "S"}
    )
    assert res["ties"] is None and "inconclusive" in res
    ok, msgs = to.tieout_passes({"success": True, **res})
    assert not ok and "inconclusive" in msgs[0]


def test_derive_levels_matrix_per_level_and_table_flat():
    kw = {
        "grouping_columns": [COUNTRY, CITY, "'D'[Year]"],
        "grouping_levels": [[COUNTRY], [CITY], ["'D'[Year]"]],
        "row_columns": [COUNTRY, CITY],
        "row_subtotals": None,
    }
    levels, flags, fixed = to.derive_levels(kw, "matrix")
    assert (
        levels == [[COUNTRY], [CITY]]
        and flags == ["IsGrandTotalRowTotal", "IsDM1Total"]
        and fixed == ["'D'[Year]"]
    )
    levels, flags, fixed = to.derive_levels({"grouping_columns": [COUNTRY, CITY]}, "table")
    assert levels == [[COUNTRY, CITY]] and flags == ["IsGrandTotalRowTotal"] and fixed == []
    levels, flags, _ = to.derive_levels({**kw, "row_subtotals": False}, "matrix")
    assert flags == ["IsGrandTotalRowTotal"] and levels == [[COUNTRY, CITY]]


def test_lift_row_limit_rewrites_the_topn_window():
    q = "DEFINE\n VAR __DS0PrimaryWindowed =\n TOPN(501, __DS0Core, [IsGrandTotalRowTotal], 0, 'T'[a], 1)\nEVALUATE x"
    lifted, original = to.lift_row_limit(q, 5000)
    assert original == 501 and "TOPN(5000, __DS0Core" in lifted
    assert to.lift_row_limit("EVALUATE ROW(1,1)", 5000) == ("EVALUATE ROW(1,1)", None)


# --------------------------------------------------------------------------- visual glue
class _FakeBuilder:
    def __init__(self, vtype="matrix", rows=None):
        self.vtype = vtype
        self.queries = []

    def build_visual_query(
        self, page, visual_id=None, visual_name=None, measure_name=None, measures=None, **k
    ):
        vi = SimpleNamespace(visual_type=self.vtype, measures=["Sales"])
        return SimpleNamespace(
            visual_info=vi,
            dax_query="DEFINE VAR __DS0PrimaryWindowed = TOPN(501, __DS0Core, [IsGrandTotalRowTotal], 0)\nEVALUATE __DS0PrimaryWindowed",
            build_kwargs={
                "grouping_columns": [COUNTRY, CITY],
                "grouping_levels": LEVELS,
                "row_columns": [COUNTRY, CITY],
            },
            fidelity="partial",
            fidelity_notes=["a slicer filter was skipped"],
            measure_definitions=[SimpleNamespace(name="Sales", expression="SUM(Fact[Amount])")],
            value_column=lambda ref: "Sales",
        )


def test_run_visual_tieout_end_to_end_with_fake_executor():
    seen = []

    def run(q):
        seen.append(q)
        return {"success": True, "rows": _matrix(nl_subtotal=11, grand=31)}

    res = to.run_visual_tieout(_FakeBuilder(), run, page_name="P", visual_id="v1")
    assert res["success"] and res["ties"] is False and res["fidelity"] == "partial"
    assert res["first_break"]["measure"] == "Sales" and res["row_limit"] == {
        "visual_default": 501,
        "used": 5000,
    }
    assert "TOPN(5000, __DS0Core" in seen[0]


def test_run_visual_tieout_refuses_a_chart():
    res = to.run_visual_tieout(
        _FakeBuilder("lineChart"), lambda q: {"success": True, "rows": []}, page_name="P"
    )
    assert res["success"] is False and res["error_type"] == "unsupported_visual"


def test_row_cap_makes_the_result_inconclusive():
    rows = _matrix()
    res = to.run_visual_tieout(
        _FakeBuilder(), lambda q: {"success": True, "rows": rows}, page_name="P", max_rows=len(rows)
    )
    assert res["truncated"] is True and res["ties"] is None


# --------------------------------------------------------------------------- A vs B / page vs page
def test_a_vs_b_grand_total():
    qs = []

    def run(q):
        qs.append(q)
        return {"success": True, "rows": [{"[a]": 266015.0, "[b]": 278000.0}]}

    res = to.run_ab_tieout(run, measure_a="CF EBITDA", measure_b="PL EBITDA")
    assert qs[0] == 'EVALUATE ROW("a", [CF EBITDA], "b", [PL EBITDA])'
    assert res["ties"] is False and res["differences"][0]["difference"] == pytest.approx(-11985.0)
    ok, msgs = to.tieout_passes(res)
    assert not ok and "grand total" in msgs[0]


def test_a_vs_b_by_grain_and_filters():
    qs = []

    def run(q):
        qs.append(q)
        return {
            "success": True,
            "rows": [
                {"Date[Year]": 2024, "[a]": 1.0, "[b]": 1.0},
                {"Date[Year]": 2025, "[a]": 2.0, "[b]": 2.5},
            ],
        }

    res = to.run_ab_tieout(
        run, measure_a="[A]", measure_b="B", by=["'Date'[Year]"], filters=["'Co'[Id] = 1"]
    )
    assert "CALCULATETABLE(SUMMARIZECOLUMNS('Date'[Year]" in qs[0] and "'Co'[Id] = 1" in qs[0]
    assert res["compared"] == 2 and res["difference_count"] == 1
    assert res["differences"][0]["label"] == {"'Date'[Year]": 2025}


def test_a_vs_b_query_error_is_reported():
    res = to.run_ab_tieout(
        lambda q: {"success": False, "error": "no measure"}, measure_a="A", measure_b="B"
    )
    assert res["success"] is False and res["error_type"] == "query_failed"


class _PageBuilder:
    def __init__(self, data):
        self.data = data  # page -> [(id, title, value)]

    def list_visuals(self, page):
        return [
            {"id": i, "title": t, "measures": ["Sales"], "is_slicer": False, "is_data_visual": True}
            for i, t, _v in self.data[page]
        ]

    def build_visual_query(self, page, vid, measure_name=None, **k):
        return SimpleNamespace(page=page, vid=vid, fidelity="full")

    def build_total_query(self, vq, measure):
        return f'EVALUATE ROW("Value", {vq.page}/{vq.vid})', "Value"


def _page_runner(builder):
    def run(q):
        page, vid = q.split(", ")[1].rstrip(")").split("/")
        val = next(v for i, _t, v in builder.data[page] if i == vid)
        return {"success": True, "rows": [{"[Value]": val}]}

    return run


def test_page_vs_page_single_visual_each():
    b = _PageBuilder({"A": [("a1", "Revenue", 100.0)], "B": [("b1", "Rev", 100.5)]})
    res = to.run_page_vs_page(b, _page_runner(b), page_a="A", page_b="B", measure="Sales")
    assert res["ties"] is False and res["differences"][0]["difference"] == pytest.approx(-0.5)
    b2 = _PageBuilder({"A": [("a1", "Revenue", 100.0)], "B": [("b1", "Rev", 100.0)]})
    assert (
        to.run_page_vs_page(b2, _page_runner(b2), page_a="A", page_b="B", measure="Sales")["ties"]
        is True
    )


def test_page_vs_page_pairs_by_title_when_several_visuals():
    b = _PageBuilder(
        {
            "A": [("a1", "Total", 1.0), ("a2", "Only A", 2.0)],
            "B": [("b1", "total", 1.0), ("b2", "Only B", 3.0)],
        }
    )
    res = to.run_page_vs_page(b, _page_runner(b), page_a="A", page_b="B", measure="Sales")
    assert res["ties"] is True and res["compared"] == 1 and len(res["unmatched_visuals"]) == 2


def test_page_vs_page_missing_measure():
    b = _PageBuilder({"A": [("a1", "t", 1.0)], "B": []})
    res = to.run_page_vs_page(b, _page_runner(b), page_a="A", page_b="B", measure="Sales")
    assert res["success"] is False and res["error_type"] == "not_found"


# --------------------------------------------------------------------------- dispatcher
def test_run_tieout_dispatch_and_validation():
    run = lambda q: {"success": True, "rows": [{"[a]": 1, "[b]": 1}]}  # noqa: E731
    assert to.run_tieout(
        {"mode": "A_vs_B", "measure_a": "A", "measure_b": "B"}, builder=None, run_query=run
    )["ties"]
    assert (
        to.run_tieout({"measure_a": "A", "measure_b": "B"}, builder=None, run_query=run)["mode"]
        == "A_vs_B"
    )
    assert (
        to.run_tieout({"mode": "x"}, builder=None, run_query=run)["error_type"]
        == "invalid_parameters"
    )
    assert (
        to.run_tieout({"page_name": "P"}, builder=None, run_query=run)["error_type"]
        == "pbip_path_required"
    )
    assert (
        to.run_tieout({"mode": "A_vs_B"}, builder=None, run_query=run)["error_type"]
        == "missing_argument"
    )
    assert (
        to.run_tieout({"mode": "page_vs_page"}, builder=object(), run_query=run)["error_type"]
        == "missing_argument"
    )
    assert to.run_tieout({}, builder=object(), run_query=run)["error_type"] == "missing_argument"


def test_tieout_crash_becomes_a_failed_result():
    class Boom:
        def build_visual_query(self, *a, **k):
            raise RuntimeError("bad pbip")

    res = to.run_tieout(
        {"page_name": "P", "visual_id": "v"}, builder=Boom(), run_query=lambda q: {}
    )
    assert res["success"] is False and "bad pbip" in res["error"]
