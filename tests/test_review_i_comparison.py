"""2026-09-22 deep review, slice I — live model compare (I-07, I-08, I-18, I-21).

Round-trips TMSL-shaped synthetic models through the real normaliser ->
ModelDiffer -> ModelDiffReportV2, and drives the orchestrator / helper with a
fake multi-instance manager (no ADOMD/AMO).
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

import core.comparison.model_comparison_orchestrator as orch_mod
import server.handlers._internal.comparison_helpers as helpers
from core.comparison.model_diff_engine import ModelDiffer
from core.comparison.model_diff_report import ModelDiffReportV2
from core.comparison.tmsl_normalizer import normalize_tmsl


def _base():
    return {
        "name": "Model",
        "tables": [
            {
                "name": "Sales",
                "lineageTag": "t-sales",
                "columns": [
                    {
                        "name": "Amount",
                        "dataType": "decimal",
                        "sourceColumn": "Amount",
                        "lineageTag": "c-amt",
                    },
                    {"name": "Key", "dataType": "int64", "sourceColumn": "Key", "isHidden": True},
                    {
                        "name": "Band",
                        "type": "calculated",
                        "dataType": "string",
                        "expression": 'IF ( [Amount] > 1, "hi", "lo" )',
                    },
                ],
                "measures": [
                    {
                        "name": "Total",
                        "expression": "SUM ( Sales[Amount] )",
                        "lineageTag": "m-total",
                    },
                    {
                        "name": "Avg",
                        "expression": "AVERAGE ( Sales[Amount] )",
                        "lineageTag": "m-avg",
                    },
                ],
                "partitions": [
                    {
                        "name": "Sales",
                        "source": {"type": "m", "expression": ["let", "  S = Source", "in S"]},
                    }
                ],
            },
            {
                "name": "Customer",
                "lineageTag": "t-cust",
                "columns": [{"name": "Key", "dataType": "int64", "sourceColumn": "Key"}],
            },
            {
                "name": "Time Intel",
                "calculationGroup": {
                    "calculationItems": [
                        {"name": "CY", "expression": "SELECTEDMEASURE()", "ordinal": 0}
                    ]
                },
                "columns": [{"name": "Name", "dataType": "string"}],
            },
        ],
        "relationships": [
            {
                "name": "r1",
                "fromTable": "Sales",
                "fromColumn": "Key",
                "toTable": "Customer",
                "toColumn": "Key",
            }
        ],
        "roles": [
            {
                "name": "Region",
                "tablePermissions": [{"name": "Customer", "filterExpression": "[Key] = 1"}],
            }
        ],
    }


def _diff(before, after):
    return ModelDiffer(normalize_tmsl(before), normalize_tmsl(after)).compare()


def _mut(fn):
    m = copy.deepcopy(_base())
    fn(m)
    return m


def test_identical_models_have_no_changes():
    d = _diff(_base(), _base())
    assert d["summary"]["total_changes"] == 0


@pytest.mark.parametrize(
    "mutate,field",
    [
        (
            lambda m: m["relationships"][0].__setitem__("crossFilteringBehavior", "bothDirections"),
            "cross_filtering_behavior",
        ),
        (lambda m: m["relationships"][0].__setitem__("toCardinality", "many"), "to_cardinality"),
        (lambda m: m["relationships"][0].__setitem__("isActive", False), "is_active"),
        (
            lambda m: m["relationships"][0].__setitem__(
                "securityFilteringBehavior", "bothDirections"
            ),
            "security_filtering_behavior",
        ),
    ],
)
def test_relationship_property_changes_are_detected(mutate, field):
    d = _diff(_base(), _mut(mutate))
    mods = d["relationships"]["modified"]
    assert len(mods) == 1 and field in mods[0]["changes"]
    assert d["summary"]["total_changes"] == 1


def test_default_valued_keys_compare_equal_to_absent_keys():
    after = _mut(
        lambda m: m["relationships"][0].update(
            {"fromCardinality": "many", "crossFilteringBehavior": "oneDirection", "isActive": True}
        )
    )
    assert _diff(_base(), after)["summary"]["total_changes"] == 0


def test_rls_filter_change_is_detected_and_rendered(tmp_path):
    after = _mut(
        lambda m: m["roles"][0]["tablePermissions"][0].__setitem__("filterExpression", "[Key] = 2")
    )
    d = _diff(_base(), after)
    role = d["roles"]["modified"][0]
    assert role["changes"]["filter_expressions"]["changed_tables"] == ["Customer"]
    out = ModelDiffReportV2(d).generate_html(str(tmp_path / "r.html"))
    assert "[Key] = 2" in open(out, encoding="utf-8").read()


def test_calculated_column_partition_and_calc_item_changes():
    def mutate(m):
        m["tables"][0]["columns"][2]["expression"] = 'IF ( [Amount] > 10, "hi", "lo" )'
        m["tables"][0]["partitions"][0]["source"]["expression"] = "let S = Other in S"
        m["tables"][2]["calculationGroup"]["calculationItems"][0][
            "expression"
        ] = "SELECTEDMEASURE() * 2"

    d = _diff(_base(), _mut(mutate))
    cats = d["summary"]["changes_by_category"]
    assert cats["columns_modified"] == 1 and cats["partitions_changed"] == 1
    assert cats["calculation_items_changed"] == 1
    assert d["summary"]["total_changes"] == 3


@pytest.mark.parametrize("kind", ["roles", "perspectives"])
def test_role_or_perspective_added_and_removed_renders(tmp_path, kind):
    before = _base()
    after = copy.deepcopy(before)
    if kind == "roles":
        after["roles"].append({"name": "NewRole"})
        before["roles"].append({"name": "OldRole"})
    else:
        after["perspectives"] = [{"name": "Sales view", "tables": [{"name": "Sales"}]}]
        before["perspectives"] = [{"name": "Old view", "tables": []}]
    d = _diff(before, after)
    assert d[kind]["added"][0]["name"] in ("NewRole", "Sales view")
    html = open(
        ModelDiffReportV2(d).generate_html(str(tmp_path / "x.html")), encoding="utf-8"
    ).read()
    assert "NewRole" in html or "Sales view" in html


def test_legacy_string_role_lists_still_render(tmp_path):
    d = _diff(_base(), _base())
    d["roles"] = {"added": ["A"], "removed": ["B"], "modified": []}
    html = ModelDiffReportV2(d)._build_roles_section()
    assert "A" in html and "B" in html


@pytest.mark.parametrize(
    "new_expr",
    [
        "SUM(Sales[Amount])",
        "SUM ( Sales[Amount] )\r\n",
        "sum ( sales[amount] ) // comment",
        "SUM (\n    Sales[Amount]\n)",
    ],
)
def test_formatting_only_dax_change_is_cosmetic(new_expr):
    d = _diff(
        _base(), _mut(lambda m: m["tables"][0]["measures"][0].__setitem__("expression", new_expr))
    )
    assert d["summary"]["total_changes"] == 0
    assert d["summary"]["cosmetic_changes"] == 1


def test_measure_rename_by_lineage_tag():
    d = _diff(
        _base(), _mut(lambda m: m["tables"][0]["measures"][0].__setitem__("name", "Total Sales"))
    )
    assert d["measures"]["added"] == [] and d["measures"]["removed"] == []
    assert (
        d["measures"]["renamed"][0]["from"] == "Total"
        and d["measures"]["renamed"][0]["to"] == "Total Sales"
    )
    assert d["summary"]["total_changes"] == 1


def test_measure_rename_without_lineage_tag_matches_by_expression():
    def strip(m):
        for mm in m["tables"][0]["measures"]:
            mm.pop("lineageTag")

    before = _mut(strip)
    after = _mut(lambda m: (strip(m), m["tables"][0]["measures"][1].__setitem__("name", "Average")))
    d = _diff(before, after)
    assert [(r["from"], r["to"]) for r in d["measures"]["renamed"]] == [("Avg", "Average")]


def test_measure_moved_to_another_table():
    def mutate(m):
        moved = m["tables"][0]["measures"].pop(1)
        m["tables"][1]["measures"] = [moved]

    d = _diff(_base(), _mut(mutate))
    assert d["measures"]["moved"][0]["from_table"] == "Sales"
    assert d["measures"]["moved"][0]["to_table"] == "Customer"
    assert d["measures"]["added"] == [] and d["measures"]["removed"] == []


def test_table_rename_is_counted_and_rendered(tmp_path):
    d = _diff(_base(), _mut(lambda m: m["tables"][1].__setitem__("name", "Client")))
    assert d["tables"]["renamed"][0]["from"] == "Customer"
    assert d["summary"]["changes_by_category"]["tables_renamed"] == 1
    assert d["summary"]["total_changes"] >= 1
    html = open(
        ModelDiffReportV2(d).generate_html(str(tmp_path / "t.html")), encoding="utf-8"
    ).read()
    assert "Customer → Client" in html


def test_added_table_measures_appear_in_top_level_measures():
    d = _diff(
        _base(),
        _mut(
            lambda m: m["tables"].append(
                {"name": "Budget", "measures": [{"name": "B", "expression": "1"}]}
            )
        ),
    )
    assert [(m["table"], m["name"]) for m in d["measures"]["added"]] == [("Budget", "B")]


def test_modified_measure_counts_once():
    d = _diff(
        _base(),
        _mut(
            lambda m: m["tables"][0]["measures"][0].__setitem__(
                "expression", "SUM ( Sales[Amount] ) * 2"
            )
        ),
    )
    assert d["summary"]["total_changes"] == 1
    assert d["summary"]["changes_by_category"]["tables_modified"] == 1  # container, not counted


def test_report_escapes_model_names(tmp_path):
    d = _diff(_base(), _base())
    d["summary"]["model1_name"] = "<script>alert(1)</script>"
    html = open(
        ModelDiffReportV2(d, normalize_tmsl(_base()), normalize_tmsl(_base())).generate_html(
            str(tmp_path / "e.html")
        ),
        encoding="utf-8",
    ).read()
    assert "<script>alert(1)</script>" not in html


# ── orchestrator / helper ────────────────────────────────────────────────────


class _FakeMIM:
    def __init__(self, fail_port=None):
        self.fail_port = fail_port
        self.connected = []
        self.released = []

    def connect_to_instance(self, port):
        if port == self.fail_port:
            return {"success": False, "error": "boom"}
        self.connected.append(port)
        return {
            "success": True,
            "status": "connected",
            "database_name": "0f3a1c2e-1111-2222-3333-444455556666",
            "instance_info": {"display_name": f"model{port}.pbix"},
        }

    def get_instance(self, port):
        return SimpleNamespace(get_connection=lambda: port)

    def disconnect_instance(self, port):
        self.released.append(port)
        return {"success": True}


def _exporter(models):
    return lambda conn: SimpleNamespace(
        export_tmsl=lambda include_full_model=True: {
            "success": True,
            "model": models[conn],
            "database_name": "guid",
            "statistics": {},
        }
    )


def test_orchestrator_compares_tmsl_and_releases_its_ports(monkeypatch, tmp_path):
    mim = _FakeMIM()
    monkeypatch.setattr(orch_mod, "multi_instance_manager", mim)
    after = _mut(
        lambda m: m["relationships"][0].__setitem__("crossFilteringBehavior", "bothDirections")
    )
    o = orch_mod.ModelComparisonOrchestrator(exporter_factory=_exporter({1: _base(), 2: after}))
    res = o.compare_models(1, 2, output_path=str(tmp_path / "d.html"), generate_json=True)
    assert res["success"], res
    assert res["summary"]["total_changes"] == 1
    assert res["summary"]["model1_name"] == "model1.pbix"
    assert "tmdl_exports" not in res  # no temp files -> no dangling paths
    assert sorted(mim.released) == [1, 2]


def test_orchestrator_releases_only_what_it_acquired(monkeypatch):
    mim = _FakeMIM(fail_port=2)
    monkeypatch.setattr(orch_mod, "multi_instance_manager", mim)
    res = orch_mod.ModelComparisonOrchestrator(exporter_factory=_exporter({})).compare_models(1, 2)
    assert res["success"] is False and mim.released == [1]


def test_default_report_dir_is_repo_exports():
    assert orch_mod.DEFAULT_REPORT_DIR.parts[-2:] == ("exports", "model_diffs")
    assert (orch_mod.DEFAULT_REPORT_DIR.parent.parent / "core").is_dir()


def _helper_state(instances):
    return SimpleNamespace(
        is_connected=lambda: True,
        connection_manager=SimpleNamespace(detect_instances=lambda: instances),
    )


_TWO = [
    {"port": 2002, "started_at": "2026-09-22T10:00:00", "display_name": "new.pbix"},
    {"port": 1001, "started_at": "2026-09-22T09:00:00", "display_name": "old.pbix"},
]


def test_helper_auto_picks_with_exactly_two_instances(monkeypatch):
    seen = {}

    class _Orch:
        def compare_models(self, a, b, **kw):
            seen.update(a=a, b=b, **kw)
            return {"success": True, "reports": {"html": "x"}}

    monkeypatch.setattr(helpers, "connection_state", _helper_state(_TWO))
    monkeypatch.setattr(orch_mod, "ModelComparisonOrchestrator", _Orch)
    res = helpers.handle_compare_pbi_models({})
    assert res["success"] and res["ports"] == {"old": 1001, "new": 2002, "auto_selected": True}
    assert seen["model1_label"] == "old.pbix"
    assert res["workflow"]["next_recommendations"][1]["tool"] == "08_Documentation_Word"


def test_helper_single_port_is_an_error(monkeypatch):
    monkeypatch.setattr(helpers, "connection_state", _helper_state(_TWO))
    res = helpers.handle_compare_pbi_models({"old_port": 1001})
    assert res["success"] is False and res["error_type"] == "invalid_input"


def test_helper_three_instances_needs_input(monkeypatch):
    three = _TWO + [{"port": 3003, "started_at": "2026-09-22T11:00:00"}]
    monkeypatch.setattr(helpers, "connection_state", _helper_state(three))
    res = helpers.handle_compare_pbi_models({})
    assert res["requires_input"] is True and len(res["instances"]) == 3
