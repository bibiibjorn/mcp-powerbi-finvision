"""F-14: equivalence over a context grid (core/validation/equivalence_grid.py) with a fake engine."""

from __future__ import annotations

import re
from collections import defaultdict

import pytest

from core.validation import equivalence_grid as eg

DATA = [  # (region, year, amount)
    ("BE", 2024, 10.0),
    ("BE", 2025, 20.0),
    ("NL", 2024, 5.0),
    ("NL", 2025, 7.0),
]
REGION = "'Sales'[Region]"
YEAR = "'Sales'[Year]"


class FakeEngine:
    """Evaluates the grid queries over DATA. Expressions are looked up by their DAX text."""

    def __init__(self, exprs, fail_on=()):
        self.exprs = exprs  # dax text -> f(rows) -> number | None
        self.fail_on = fail_on
        self.queries = []

    @staticmethod
    def _defined(q):
        return {
            m.group(1): m.group(2).strip()
            for m in re.finditer(
                r"MEASURE '[^']+'\[(__eq[AB])\] =\n\s*(.*?)\n\n?(?=\s*(?:MEASURE|EVALUATE))",
                q,
                re.S,
            )
        }

    def __call__(self, q):
        self.queries.append(q)
        defs = self._defined(q)
        sides = [s for s in ("A", "B") if f"__eq{s}" in defs]
        for marker in self.fail_on:
            if marker in defs.get("__eqB", "") and "B" in [s for s in sides]:
                return {"success": False, "error": f"boom: {marker}"}

        def vals(rows):
            out = {}
            for s in sides:
                out[s.lower()] = self.exprs[defs[f"__eq{s}"]](rows)
            return out

        if "ADDCOLUMNS(" in q:  # year grain
            by = defaultdict(list)
            for r in DATA:
                by[r[1]].append(r)
            return {
                "success": True,
                "rows": [
                    {"[__p]": y, **{f"[{k}]": v for k, v in vals(rs).items()}}
                    for y, rs in sorted(by.items())
                ],
            }
        if "ROLLUPADDISSUBTOTAL" in q:
            cols = [c for c in (REGION, YEAR) if c in q.split("EVALUATE", 1)[1]]
            idx = {REGION: 0, YEAR: 1}
            groups = defaultdict(list)
            for r in DATA:
                groups[tuple(r[idx[c]] for c in cols)].append(r)
            rows = []
            for key, rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
                row = {c.replace("'", "").replace("Sales[", "Sales["): k for c, k in zip(cols, key)}
                row.update({"[__tot0]": False, **{f"[{k}]": v for k, v in vals(rs).items()}})
                rows.append(row)
            rows.append({"[__tot0]": True, **{f"[{k}]": v for k, v in vals(DATA).items()}})
            return {"success": True, "rows": rows}
        m = re.search(r"CALCULATE\(\[__eqA\], 'Sales'\[Region\] = \"(\w+)\"\)", q)
        if m:
            rs = [r for r in DATA if r[0] == m.group(1)]
            return {"success": True, "rows": [{f"[{k}]": v for k, v in vals(rs).items()}]}
        return {"success": True, "rows": [{f"[{k}]": v for k, v in vals(DATA).items()}]}


SUM = "SUM('Sales'[Amount])"
SUM_X = "SUMX('Sales', 'Sales'[Amount])"
BUGGY = "SUM('Sales'[Amount]) + IF(HASONEVALUE('Sales'[Region]), 0, 1)"


def _exprs():
    total = lambda rs: sum(r[2] for r in rs)  # noqa: E731
    return {
        SUM: total,
        SUM_X: total,
        # differs from SUM only in the total row of a column grouping (one extra unit)
        BUGGY: lambda rs: total(rs) + (1 if len({r[0] for r in rs}) > 1 else 0),
        "[Revenue]": total,
    }


def _run(expr_a, expr_b, **kw):
    eng = FakeEngine(_exprs())
    res = eg.run_equivalence(
        eng, expression_a=expr_a, expression_b=expr_b, host_table="Sales", **kw
    )
    return res, eng


def test_equivalent_expressions_over_the_grid():
    res, eng = _run(
        SUM,
        SUM_X,
        grid_columns=[REGION, YEAR],
        date_column="'Date'[Date]",
        grains=["year"],
        filters=["'Sales'[Region] = \"BE\""],
    )
    assert res["success"] and res["equivalent"] is True and res["identical_hash"] is True
    assert res["model_written"] is False and res["proof"] == "sampled_grid"
    kinds = [g["kind"] for g in res["coverage"]["groups"]]
    assert kinds == ["total", "filter", "column", "column", "pair", "grain"]
    assert res["cells_compared"] >= 1 + 1 + 3 + 3 + 5 + 2
    # nothing but DEFINE MEASURE ... EVALUATE: no model write statement
    assert all(q.lstrip().startswith("DEFINE") for q in eng.queries)
    assert all("__eqA" in q and "__eqB" in q for q in eng.queries)


def test_difference_is_localised_to_the_total_rows():
    res, _ = _run(SUM, BUGGY, grid_columns=[REGION, YEAR])
    assert res["equivalent"] is False and res["identical_hash"] is False
    assert res["cells_differing"] >= 3
    groups = {d["group"] for d in res["differing_cells"]}
    assert "total" in groups and f"col:{REGION}" in groups
    assert res["max_abs_difference"] == pytest.approx(1.0)
    # in the region grouping only the total row differs (a single region never trips the bug)
    region_diffs = [d for d in res["differing_cells"] if d["group"] == f"col:{REGION}"]
    assert region_diffs and all(d["cell"]["[__tot0]"] is True for d in region_diffs)


def test_measure_versus_candidate_body():
    args = {"measure_name": "Revenue", "candidate_expression": SUM}
    a, b, err = eg.resolve_expressions(args)
    assert (a, b, err) == ("[Revenue]", SUM, None)
    res, _ = _run(a, b, grid_columns=[REGION])
    assert res["equivalent"] is True
    assert eg.resolve_expressions({"expression_a": "1"})[2]


def test_cell_cap_is_split_across_groups_and_reported():
    groups = eg.build_groups(grid_columns=["a", "b", "c"], max_cells=100)
    caps = {g["id"]: g["cap"] for g in groups}
    assert caps["total"] == 1 and all(v >= 5 for k, v in caps.items() if k != "total")
    assert sum(caps.values()) <= 100 + 5 * len(caps)
    assert eg.build_groups(grid_columns=["a"], max_cells=10**9)[1]["cap"] <= eg.HARD_MAX_CELLS
    # pairs only among the first three columns
    pair_groups = [g for g in eg.build_groups(grid_columns=list("abcd")) if g["kind"] == "pair"]
    assert len(pair_groups) == 3


def test_group_queries_are_deterministic():
    g1 = eg.build_groups(
        grid_columns=[REGION], date_column="'Date'[Date]", grains=["month"], filters=["x"]
    )
    g2 = eg.build_groups(
        grid_columns=[REGION], date_column="'Date'[Date]", grains=["month"], filters=["x"]
    )
    q = lambda gs: [eg.compile_group(g, "Sales", "1", "2") for g in gs]  # noqa: E731
    assert q(g1) == q(g2)
    month = [g for g in g1 if g["kind"] == "grain"][0]
    dax = eg.compile_group(month, "Sales", "1", "2")
    assert "YEAR('Date'[Date]) * 100 + MONTH('Date'[Date])" in dax and "TOPN(" in dax


def test_error_on_one_side_is_a_difference_not_a_pass():
    eng = FakeEngine(_exprs(), fail_on=[BUGGY])
    res = eg.run_equivalence(
        eng, expression_a=SUM, expression_b=BUGGY, host_table="Sales", grid_columns=[REGION]
    )
    assert res["equivalent"] is False
    assert res["errors"] and all(e["errors"].get("b") for e in res["errors"])
    assert {d["error_side"] for d in res["differing_cells"] if "error_side" in d} == {"b"}


def test_inputs_validated():
    run = lambda q: {"success": True, "rows": []}  # noqa: E731
    assert (
        eg.run_equivalence(run, expression_a="", expression_b="1", host_table="T")["error_type"]
        == "missing_argument"
    )
    assert (
        eg.run_equivalence(run, expression_a="1", expression_b="1", host_table="")["error_type"]
        == "missing_argument"
    )
    res = eg.run_equivalence(run, expression_a="1", expression_b="1", host_table="T")
    assert res["equivalent"] is None  # nothing came back: inconclusive, never green


def test_values_equal_distinguishes_blank_and_zero():
    assert eg.values_equal(1.0, 1.0000001, 1e-6)
    assert not eg.values_equal(None, 0, 1e-6)
    assert eg.values_equal(None, None, 1e-6)
    assert eg.values_equal("x", "x", 1e-6) and not eg.values_equal("x", "y", 1e-6)
