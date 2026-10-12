"""Review slice J — dependency graph (J-03 b/c, J-04, J-11, J-18, J-19, J-20,
J-22, J-27, J-28).

The model side is fed by a fake query executor that answers ``EVALUATE INFO.*()``
with PascalCase, BRACKETED keys exactly like Desktop (LIVE-CHECKS.md: INFO.COLUMNS
has ExplicitName/InferredName and TableID, no Name/table name; INFO.RELATIONSHIPS
carries column IDs). The rows mirror the synthetic golden model
``tests/fixtures/pbir_golden/GoldenCo/MDL-Core.SemanticModel``; the report side is
the golden ``RPT-Sales`` PBIR report, read from disk.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from core.graph import queries
from core.graph.builder import build_graph, emit_dax_refs, ModelIndex
from core.graph.cache import GraphCache, get_cache
from core.graph.model_source import ModelSnapshot, load_live_snapshot
from core.graph.models import Edge, Graph, Node

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"

_FP = (
    "{\n    (\"Amount\", NAMEOF('Sales'[Total Amount]), 0),\n"
    "    (\"Net \u0394 Value\", NAMEOF('m Metrics'[Net \u0394 Value]), 1)\n}"
)
_INFO = {
    "TABLES": [
        {"[ID]": 1, "[Name]": "Sales"},
        {"[ID]": 2, "[Name]": "Product Line"},
        {"[ID]": 3, "[Name]": "Owner's Book"},
        {"[ID]": 4, "[Name]": "Time Calc", "[CalculationGroupID]": 70},
        {"[ID]": 5, "[Name]": "fp Metric"},
        {"[ID]": 6, "[Name]": "m Metrics"},
    ],
    "COLUMNS": [
        {
            "[ID]": 100,
            "[TableID]": 1,
            "[ExplicitName]": None,
            "[InferredName]": "RowNumber-1",
            "[Type]": 3,
        },
        {"[ID]": 101, "[TableID]": 1, "[ExplicitName]": "Amount", "[Type]": 1},
        {"[ID]": 102, "[TableID]": 1, "[ExplicitName]": "Product Key", "[Type]": 1},
        {"[ID]": 103, "[TableID]": 1, "[ExplicitName]": "Owner Key", "[Type]": 1},
        {"[ID]": 104, "[TableID]": 1, "[ExplicitName]": "Order Date", "[Type]": 1},
        {"[ID]": 201, "[TableID]": 2, "[ExplicitName]": "Product Key", "[Type]": 1},
        {"[ID]": 202, "[TableID]": 2, "[ExplicitName]": "Category", "[Type]": 1},
        {"[ID]": 203, "[TableID]": 2, "[ExplicitName]": "Region", "[Type]": 1},
        {"[ID]": 204, "[TableID]": 2, "[ExplicitName]": "Unused Code", "[Type]": 1},
        {"[ID]": 301, "[TableID]": 3, "[ExplicitName]": "Owner Key", "[Type]": 1},
        {"[ID]": 302, "[TableID]": 3, "[ExplicitName]": "Owner", "[Type]": 1},
        {
            "[ID]": 401,
            "[TableID]": 4,
            "[ExplicitName]": "Name",
            "[Type]": 1,
            "[SortByColumnID]": 402,
        },
        {"[ID]": 402, "[TableID]": 4, "[ExplicitName]": "Ordinal", "[Type]": 1},
        {
            "[ID]": 501,
            "[TableID]": 5,
            "[ExplicitName]": "fp Metric",
            "[Type]": 4,
            "[SortByColumnID]": 503,
        },
        {
            "[ID]": 502,
            "[TableID]": 5,
            "[ExplicitName]": "fp Metric Fields",
            "[Type]": 4,
            "[SortByColumnID]": 503,
        },
        {"[ID]": 503, "[TableID]": 5, "[ExplicitName]": "fp Metric Order", "[Type]": 4},
        {"[ID]": 601, "[TableID]": 6, "[InferredName]": "Value", "[Type]": 4},
        # A calculated column whose bare [Amount] is a row-context column of its table.
        {
            "[ID]": 105,
            "[TableID]": 1,
            "[ExplicitName]": "Amount x2",
            "[Type]": 2,
            "[Expression]": "[Amount] * 2 // not [Orphan Metric]",
        },
    ],
    "MEASURES": [
        {
            "[ID]": 1001,
            "[TableID]": 1,
            "[Name]": "Total Amount",
            "[Expression]": "SUM(Sales[Amount])",
        },
        {
            "[ID]": 1002,
            "[TableID]": 1,
            "[Name]": "Amount Guarded",
            "[Expression]": "VAR _base = [Total Amount]\nRETURN IF(_base > 0, _base, BLANK())",
        },
        {
            "[ID]": 1003,
            "[TableID]": 1,
            "[Name]": "Amount Dynamic",
            "[Expression]": "[Total Amount]",
            "[FormatStringDefinitionID]": 9001,
        },
        {
            "[ID]": 1004,
            "[TableID]": 6,
            "[Name]": "Show In Slot",
            "[Expression]": "IF(HASONEVALUE('Product Line'[Category]), 1, 0)",
        },
        {
            "[ID]": 1005,
            "[TableID]": 6,
            "[Name]": "Net \u0394 Value",
            "[Expression]": "VAR _cur = [total amount]\nVAR _prev = CALCULATE([Total Amount], "
            "'Time Calc'[Name] = \"PY\")\nRETURN _cur - _prev",
            "[FormatStringDefinitionID]": 9002,
        },
        {"[ID]": 1006, "[TableID]": 6, "[Name]": "Orphan Metric", "[Expression]": "42"},
    ],
    "RELATIONSHIPS": [
        {"[ID]": 1, "[FromColumnID]": 102, "[ToColumnID]": 201, "[IsActive]": True},
        {"[ID]": 2, "[FromColumnID]": 103, "[ToColumnID]": 301, "[IsActive]": True},
    ],
    "PARTITIONS": [
        {"[TableID]": 1, "[Type]": 4, "[QueryDefinition]": "let Source = 1 in Source"},
        {"[TableID]": 5, "[Type]": 2, "[QueryDefinition]": _FP},
        {"[TableID]": 6, "[Type]": 2, "[QueryDefinition]": 'Row("Value", BLANK())'},
    ],
    "CALCULATIONGROUPS": [{"[ID]": 70, "[TableID]": 4}],
    "CALCULATIONITEMS": [
        {"[CalculationGroupID]": 70, "[Name]": "Current", "[Expression]": "SELECTEDMEASURE()"},
        {
            "[CalculationGroupID]": 70,
            "[Name]": "YTD",
            "[Expression]": "CALCULATE(SELECTEDMEASURE(), DATESYTD(Sales[Order Date]))",
        },
        {
            "[CalculationGroupID]": 70,
            "[Name]": "PY",
            "[Expression]": "// prior year\nCALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR(Sales[Order Date]))",
            "[FormatStringDefinitionID]": 9003,
        },
    ],
    "ROLES": [{"[ID]": 80, "[Name]": "Region Reader"}],
    "TABLEPERMISSIONS": [
        {
            "[RoleID]": 80,
            "[TableID]": 2,
            "[FilterExpression]": "VAR __upn = USERPRINCIPALNAME()\nRETURN 'Product Line'[Region] "
            'IN { "EU", "O\'Brien Isles" }',
        },
    ],
    "HIERARCHIES": [{"[ID]": 90, "[TableID]": 2, "[Name]": "Geo"}],
    "LEVELS": [
        {"[HierarchyID]": 90, "[Ordinal]": 0, "[Name]": "Region", "[ColumnID]": 203},
        {"[HierarchyID]": 90, "[Ordinal]": 1, "[Name]": "Category", "[ColumnID]": 202},
    ],
    "FORMATSTRINGDEFINITIONS": [
        {"[ID]": 9001, "[Expression]": 'ScaleFormat ("K")'},
        {"[ID]": 9002, "[Expression]": 'ScaleFormat ("M")'},
        {"[ID]": 9003, "[Expression]": "SELECTEDMEASUREFORMATSTRING()"},
    ],
    "DETAILROWSDEFINITIONS": [],
}


class FakeQE:
    def __init__(self, info=None, fail=()):
        self.info = info or _INFO
        self.fail = set(fail)
        self.queries = []

    def validate_and_execute_dax(self, q, top_n=0, bypass_cache=False, **kw):
        self.queries.append((q, bypass_cache))
        view = q.replace("EVALUATE INFO.", "").replace("()", "").strip()
        if view in self.fail or view not in self.info:
            return {"success": False, "error": "unknown function"}
        return {"success": True, "rows": [dict(r) for r in self.info[view]]}

    def list_udfs(self):
        return {
            "success": True,
            "rows": [
                {
                    "Name": "ScaleFormat",
                    "Expression": '(unit: string) => SWITCH(unit, "K", "#,0,.0 K", "#,0")',
                }
            ],
        }


@pytest.fixture
def snap() -> ModelSnapshot:
    return load_live_snapshot(FakeQE())


@pytest.fixture
def full(snap) -> Graph:
    return build_graph(snap, str(GOLDEN / "RPT-Sales.pbip"), scope="both")


# ---------------------------------------------------------------------------
# J-03 (b/c) — every DAX-bearing object + the report side
# ---------------------------------------------------------------------------
def test_j03_columns_from_real_info_columns_shape(snap):
    names = {(c["table"], c["name"]) for c in snap.columns}
    assert ("Sales", "Amount") in names and ("m Metrics", "Value") in names
    assert not any(n.startswith("RowNumber") for _t, n in names)


def test_j03_snapshot_queries_bypass_the_result_cache():
    qe = FakeQE()
    load_live_snapshot(qe)
    assert qe.queries and all(bypass for _q, bypass in qe.queries)


def test_j03_report_side_is_built_from_pbir(full):
    assert full.meta["report_side_available"] is True
    kinds = {n.kind for n in full.nodes.values()}
    assert {"report", "page", "visual", "bookmark"} <= kinds
    card = "visual:RPT-Sales/aa01overview00000001/vc01cardvisual000001"
    assert any(
        e.dst == "measure:Sales[Total Amount]" and e.kind == "binds" for e in full.outgoing(card)
    )


def test_j03_visual_level_measure_filter_is_a_consumer(full):
    # G-09 guard: 'Show In Slot' is used only as a visual-level filter.
    inc = full.incoming("measure:m Metrics[Show In Slot]")
    assert any(e.kind == "filters" and e.src.startswith("visual:") for e in inc)


def test_j03_table_delete_breaks_external_consumers(full):
    imp = queries.get_impact(full, "table:Sales", change="delete")
    assert imp["safe"] is False
    breaks = set(imp["breaks"])
    assert "measure:m Metrics[Net \u0394 Value]" in breaks  # [Total Amount] across tables
    assert "table:fp Metric" in breaks  # field parameter NAMEOF('Sales'[Total Amount])
    assert any(b.startswith("relationship:") for b in breaks)
    assert any(b.startswith("visual:RPT-Sales/") for b in breaks)
    assert "calc_group_item:Time Calc[YTD]" in breaks  # DATESYTD(Sales[Order Date])
    assert "measure:Sales[Total Amount]" not in breaks  # owned, deleted with it


def test_j03_model_objects_and_edges(full):
    assert full.nodes["table:fp Metric"].attrs.get("field_parameter") is True
    out = {(e.dst, e.kind) for e in full.outgoing("measure:Sales[Amount Dynamic]")}
    assert ("function:ScaleFormat", "calls") in out  # formatStringDefinition = UDF call
    assert ("column:Time Calc[Ordinal]", "sort_by") in {
        (e.dst, e.kind) for e in full.outgoing("column:Time Calc[Name]")
    }
    role_out = {(e.dst, e.kind) for e in full.outgoing("rls_role:Region Reader")}
    assert ("table:Product Line", "filters") in role_out
    assert ("column:Product Line[Region]", "references") in role_out
    h = {e.dst for e in full.outgoing("hierarchy:Product Line[Geo]") if e.kind == "hierarchy_level"}
    assert h == {"column:Product Line[Region]", "column:Product Line[Category]"}
    calc = {e.dst for e in full.outgoing("column:Sales[Amount x2]") if e.kind == "references"}
    assert calc == {"column:Sales[Amount]"}  # comment text never parsed


def test_j03_function_delete_breaks_its_callers(full):
    imp = queries.get_impact(full, "function:ScaleFormat", change="delete")
    assert "measure:Sales[Amount Dynamic]" in imp["breaks"]
    assert "measure:m Metrics[Net \u0394 Value]" in imp["breaks"]


def test_j03_missing_core_info_view_marks_model_incomplete():
    snap = load_live_snapshot(FakeQE(fail={"COLUMNS"}))
    assert snap.complete is False and "COLUMNS" in snap.missing
    g = build_graph(snap, None, scope="model")
    assert g.meta["model_side_complete"] is False


# ---------------------------------------------------------------------------
# J-04 — unknown refs are never safe; bare / case-variant refs resolve
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ref",
    [
        "Sales[Total Amount]",
        "'Sales'[Total Amount]",
        "measure:sales[total amount]",
        "[TOTAL AMOUNT]",
    ],
)
def test_j04_bare_and_case_variant_refs_resolve(full, ref):
    imp = queries.get_impact(full, ref, change="delete")
    assert imp["node"] == "measure:Sales[Total Amount]"
    assert imp["unknown_nodes"] == []
    assert imp["safe"] is False  # bound in visuals + referenced


def test_j04_unknown_node_is_unsafe_and_listed(full):
    imp = queries.get_impact(full, "Sales[Nope]", change="delete")
    assert imp["safe"] is False and imp["unknown_nodes"] == ["Sales[Nope]"]


def test_j04_batch_impact_with_unknown_is_not_all_safe(monkeypatch, full):
    import server.handlers.dependency_graph_handler as dh

    monkeypatch.setattr(dh, "_get_graph", lambda gid: (full, None))
    out = dh.handle_dependency_graph(
        {
            "operation": "batch_impact",
            "graph_id": "x",
            "node_refs": ["measure:m Metrics[Orphan Metric]", "measure:Nope[Nope]"],
        }
    )
    assert out["impacts"]["measure:m Metrics[Orphan Metric]"]["safe"] is True
    assert out["all_safe"] is False
    assert out["unknown_nodes"] == ["measure:Nope[Nope]"]


def test_j04_impact_results_carry_the_gate_fields(monkeypatch, full):
    import server.handlers.dependency_graph_handler as dh

    monkeypatch.setattr(dh, "_get_graph", lambda gid: (full, None))
    for args in (
        {"operation": "get_impact", "graph_id": "x", "node_ref": "Sales[Amount]"},
        {"operation": "batch_impact", "graph_id": "x", "node_refs": ["Sales[Amount]"]},
    ):
        out = dh.handle_dependency_graph(args)
        for key in (
            "unknown_nodes",
            "report_side_available",
            "model_etag",
            "built_at",
            "graph_scope",
        ):
            assert key in out, (args["operation"], key)
        assert isinstance(out["unknown_nodes"], list)
        assert out["report_side_available"] is True
        assert out["graph_scope"] == "both"
        assert isinstance(out["model_etag"], str) and "T" in out["built_at"]


def test_j04_model_only_graph_warns_about_missing_report_side(snap):
    g = build_graph(snap, None, scope="model")
    imp = queries.get_impact(g, "measure:m Metrics[Show In Slot]", change="delete")
    assert imp["report_side_available"] is False and "report side unavailable" in imp["warning"]


# ---------------------------------------------------------------------------
# J-18 — find_unused = no consumers; refuses without the report side
# ---------------------------------------------------------------------------
def test_j18_find_unused_counts_every_consumer(full):
    unused = {u["node"] for u in queries.find_unused(full, kinds=["measure", "column"])["unused"]}
    assert "measure:m Metrics[Orphan Metric]" in unused
    assert "column:Product Line[Unused Code]" in unused
    assert (
        "measure:Sales[Amount Guarded]" in unused
    )  # references [Total Amount], consumed by nobody
    for used in (
        "measure:m Metrics[Show In Slot]",  # visual filter only
        "measure:Sales[Total Amount]",
        "column:Sales[Product Key]",  # relationship
        "column:Time Calc[Ordinal]",  # sort-by
        "column:Time Calc[Name]",  # CALCULATE filter in a measure
    ):
        assert used not in unused, used


def test_j18_handler_refuses_find_unused_without_report_side(monkeypatch, snap):
    import server.handlers.dependency_graph_handler as dh

    g = build_graph(snap, None, scope="model")
    monkeypatch.setattr(dh, "_get_graph", lambda gid: (g, None))
    out = dh.handle_dependency_graph({"operation": "find_unused", "graph_id": "x"})
    assert out["success"] is False and out["error_type"] == "report_side_unavailable"
    ok = dh.handle_dependency_graph(
        {"operation": "find_unused", "graph_id": "x", "allow_model_only": True}
    )
    assert ok["success"] is True and "warning" in ok


# ---------------------------------------------------------------------------
# J-20 — lexer-based references
# ---------------------------------------------------------------------------
def _mini_index():
    s = ModelSnapshot()
    s.tables = {"Sales": {}, "Bob's Table": {}, "Other": {}}
    s.columns = [
        {"table": "Sales", "name": "Amount"},
        {"table": "Sales", "name": "Amount]USD"},
        {"table": "Bob's Table", "name": "Amount"},
    ]
    s.measures = [
        {"table": "Other", "name": "Total Sales", "expression": ""},
        {"table": "Other", "name": "Old Measure", "expression": ""},
    ]
    return ModelIndex(s)


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("VAR x = 1 RETURN Sales[Amount]", {"column:Sales[Amount]"}),
        ("SUM('Bob''s Table'[Amount])", {"column:Bob's Table[Amount]"}),
        ("SUM(Sales[Amount]]USD])", {"column:Sales[Amount]USD]"}),
        ("1 // was [Old Measure]\n+ 2", set()),
        ('"[Old Measure]"', set()),
        ("[total sales] * 2", {"measure:Other[Total Sales]"}),
    ],
)
def test_j20_reference_extraction(expr, expected):
    g = Graph()
    emit_dax_refs(g, "measure:X[Y]", expr, _mini_index())
    assert {e.dst for e in g.edges.values() if e.kind == "references"} == expected


# ---------------------------------------------------------------------------
# J-19 — cache key includes scope / path / etag / value signature
# ---------------------------------------------------------------------------
@pytest.fixture
def svc(monkeypatch, snap):
    from core.graph import service

    get_cache().clear()
    monkeypatch.setattr(service, "load_model_snapshot", lambda force=False: (snap, None))
    return service


def test_j19_scope_is_part_of_the_cache_key(svc):
    a = svc.get_or_build_graph(scope="model", pbip_path=str(GOLDEN / "RPT-Sales.pbip"))
    b = svc.get_or_build_graph(scope="both", pbip_path=str(GOLDEN / "RPT-Sales.pbip"))
    assert b["cached"] is False and a["graph_id"] != b["graph_id"]
    assert b["graph"].has_report_side() and not a["graph"].has_report_side()
    c = svc.get_or_build_graph(scope="both", pbip_path=str(GOLDEN / "RPT-Sales.pbip"))
    assert c["cached"] is True and c["graph_id"] == b["graph_id"]


def test_j19_deleted_visual_and_mtime_preserving_copy_invalidate(svc, tmp_path):
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    pbip = str(root / "RPT-Sales.pbip")
    svc.get_or_build_graph(scope="both", pbip_path=pbip)
    vdir = root / "RPT-Sales.Report" / "definition" / "pages" / "ab02details000000002" / "visuals"
    shutil.rmtree(vdir / "vt01tableinverted001")
    second = svc.get_or_build_graph(scope="both", pbip_path=pbip)
    assert second["cached"] is False
    pages = root / "RPT-Sales.Report" / "definition" / "pages"
    shutil.copytree(pages / "ac03tooltip000000003", pages / "zz99copy000000000009")  # copy2 mtimes
    third = svc.get_or_build_graph(scope="both", pbip_path=pbip)
    assert third["cached"] is False


def test_j19_falls_back_to_the_connected_pbip_path(svc, monkeypatch):
    from core.infrastructure import connection_state as cs_mod

    monkeypatch.setattr(
        cs_mod.connection_state, "get_pbip_folder_path", lambda: str(GOLDEN / "RPT-Sales.pbip")
    )
    res = svc.get_or_build_graph(scope="both")
    assert res["success"] is True and res["graph"].has_report_side()


def test_j19_stale_flag_after_model_change(svc, monkeypatch):
    from core.infrastructure import connection_state as cs_mod

    res = svc.get_or_build_graph(scope="both", pbip_path=str(GOLDEN / "RPT-Sales.pbip"))
    g = res["graph"]
    g.meta["model_generation"] = 1
    monkeypatch.setattr(type(cs_mod.connection_state), "model_generation", property(lambda self: 2))
    assert svc.freshness(g)["stale"] is True


# ---------------------------------------------------------------------------
# J-11 — adjacency indexes: queries are O(V+E)
# ---------------------------------------------------------------------------
def _big_graph(n_measures=5000):
    g = Graph()
    g.add_node(Node(kind="table", scoped_name="T"))
    for i in range(60):
        g.add_node(Node(kind="column", scoped_name=f"T[c{i}]"))
        g.add_edge(Edge(src=f"column:T[c{i}]", dst="table:T", kind="belongs_to"))
    for i in range(n_measures):
        mid = f"measure:T[m{i}]"
        g.add_node(Node(kind="measure", scoped_name=f"T[m{i}]"))
        g.add_edge(Edge(src=mid, dst="table:T", kind="belongs_to"))
        g.add_edge(Edge(src=mid, dst=f"column:T[c{i % 60}]", kind="references"))
        if i:
            g.add_edge(Edge(src=mid, dst=f"measure:T[m{i - 1}]", kind="references"))
    g.meta["report_side_available"] = True
    return g


def test_j11_find_unused_on_5k_nodes_is_fast():
    g = _big_graph()
    t0 = time.perf_counter()
    res = queries.find_unused(g)
    dt = time.perf_counter() - t0
    assert dt < 0.2, dt
    assert [u["node"] for u in res["unused"]] == ["measure:T[m4999]"]


def test_j11_find_cycles_and_batch_impact_are_fast():
    g = _big_graph()
    g.add_edge(Edge(src="measure:T[m0]", dst="measure:T[m4999]", kind="references"))
    t0 = time.perf_counter()
    cyc = queries.find_cycles(g)
    for i in range(15):
        queries.get_impact(g, f"measure:T[m{i * 300}]")
    assert time.perf_counter() - t0 < 2.0
    assert cyc["cycles"]  # the 5000-long chain closes into a cycle, no RecursionError


# ---------------------------------------------------------------------------
# J-22 — rename collisions are case-insensitive and model-wide for measures
# ---------------------------------------------------------------------------
def test_j22_rename_collisions(full):
    m = queries.get_impact(
        full,
        "measure:m Metrics[Orphan Metric]",
        change="rename",
        new_name="total amount",
        cascade=True,
    )
    assert m["collision"] is True and m["collides_with"] == "measure:Sales[Total Amount]"
    c = queries.get_impact(
        full, "measure:m Metrics[Orphan Metric]", change="rename", new_name="VALUE", cascade=True
    )
    assert c["collides_with"] == "column:m Metrics[Value]"  # measure vs column, same table
    ok = queries.get_impact(
        full, "measure:m Metrics[Orphan Metric]", change="rename", new_name="Brand New"
    )
    assert ok["safe"] is True


# ---------------------------------------------------------------------------
# J-27 / J-28 — cache singleton, one-step lookup, DOT escaping, export guard
# ---------------------------------------------------------------------------
def test_j27_expired_entries_leave_no_key_orphans():
    c = GraphCache(max_entries=4, ttl_seconds=0)
    c.put(("k",), Graph())
    time.sleep(0.01)
    assert c.get(("k",)) == (None, None)
    assert c._key_to_id == {}


def test_j27_singleton_exists_at_import():
    from core.graph import cache

    assert cache._singleton is cache.get_cache()


def test_j28_dot_escapes_quotes_and_backslashes():
    from core.graph.export import to_dot

    g = Graph()
    g.add_node(Node(kind="measure", scoped_name='T[a "quoted" \\ name]'))
    dot = to_dot(g)
    assert '"measure:T[a \\"quoted\\" \\\\ name]"' in dot


def test_j28_export_output_path_is_guarded(monkeypatch, full):
    import server.handlers.dependency_graph_handler as dh

    monkeypatch.setattr(dh, "_get_graph", lambda gid: (full, None))
    monkeypatch.setattr(
        "core.validation.path_guard.guard_or_error",
        lambda p, name, **k: (
            None,
            {"success": False, "error_type": "path_traversal_blocked", "error": "blocked"},
        ),
    )
    out = dh.handle_dependency_graph(
        {"operation": "export", "graph_id": "x", "output_path": "C:/Windows/evil.json"}
    )
    assert out["error_type"] == "path_traversal_blocked"
