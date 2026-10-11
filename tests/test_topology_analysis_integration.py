"""Integration test: topology validator wired into comprehensive_analysis.

2026-09-22 (I-06): the old version mocked ``list_relationships_simple`` with a
``relationships`` key that the real method never returns (it returns ``data``),
so the test passed while production flagged EVERY table as disconnected. These
tests drive the real code over a fake executor that answers with INFO-shaped
rows (integer enums, FromTable/ToTable already mapped by execute_info_query),
and over a TMSL snapshot.
"""

from core.orchestration.analysis_orchestrator import AnalysisOrchestrator


class _FakeExecutor:
    def __init__(self, table_rows, rel_rows):
        self._table_rows = table_rows
        self._rel_rows = rel_rows

    def execute_info_query(self, view, **_kw):
        if view == "TABLES":
            return {"success": True, "rows": self._table_rows}
        if view == "RELATIONSHIPS":
            return {"success": True, "rows": self._rel_rows}
        return {"success": False, "rows": []}


def _rel(from_table, to_table, from_card=2, to_card=1, active=True, cross=1):
    # INFO.RELATIONSHIPS shape after execute_info_query's ID->name mapping.
    return {
        "ID": 1,
        "FromTable": from_table,
        "FromColumn": "Key",
        "ToTable": to_table,
        "ToColumn": "Key",
        "FromCardinality": from_card,
        "ToCardinality": to_card,
        "IsActive": active,
        "CrossFilteringBehavior": cross,
    }


def test_topology_analysis_flags_orphan_and_missing_date():
    tables = [{"Name": "Sales"}, {"Name": "Product"}, {"Name": "Orphan"}]
    orch = AnalysisOrchestrator(config={})
    result = orch._run_topology_analysis(_FakeExecutor(tables, [_rel("Sales", "Product")]))

    assert result["success"] is True
    got = {(f["finding_id"], f["object_name"]) for f in result["findings"]}
    assert ("DISCONNECTED_TABLE", "Orphan") in got
    assert ("DISCONNECTED_TABLE", "Sales") not in got
    assert ("MISSING_DATE_TABLE", "(model)") in got
    assert result["finding_count"] == len(result["findings"])


def test_topology_analysis_clean_star_has_no_findings():
    tables = [{"Name": "Sales"}, {"Name": "Date"}, {"Name": "Product"}]
    rels = [_rel("Sales", "Date"), _rel("Sales", "Product")]
    orch = AnalysisOrchestrator(config={})
    result = orch._run_topology_analysis(_FakeExecutor(tables, rels))
    assert result["success"] is True
    assert result["finding_count"] == 0, result["findings"]


def test_topology_analysis_handles_executor_failure_gracefully():
    orch = AnalysisOrchestrator(config={})

    class _Boom:
        def execute_info_query(self, view, **_kw):
            raise RuntimeError("dmv exploded")

    result = orch._run_topology_analysis(_Boom())
    assert result["success"] is False
    assert "error" in result


def _tmsl():
    return {
        "tables": [
            {"name": "Sales", "columns": [{"name": "Key"}]},
            {"name": "Date", "dataCategory": "Time", "columns": [{"name": "Key"}]},
            {"name": "Returns", "columns": [{"name": "Key"}]},
            {
                "name": "Metric Selector",
                "columns": [
                    {
                        "name": "Metric",
                        "extendedProperties": [
                            {
                                "type": "json",
                                "name": "ParameterMetadata",
                                "value": {"version": 3, "kind": 2},
                            }
                        ],
                    }
                ],
                "partitions": [
                    {
                        "name": "p",
                        "source": {
                            "type": "calculated",
                            "expression": "{(\"A\", NAMEOF('Sales'[Key]), 0)}",
                        },
                    }
                ],
            },
            {
                "name": "Discount",
                "columns": [{"name": "Discount"}],
                "partitions": [
                    {
                        "name": "p",
                        "source": {
                            "type": "calculated",
                            "expression": "GENERATESERIES(0, 1, 0.05)",
                        },
                    }
                ],
            },
            {
                "name": "Time Intel",
                "calculationGroup": {"calculationItems": []},
                "columns": [{"name": "Name"}],
            },
            {
                "name": "Measures",
                "columns": [{"name": "Column1", "isHidden": True}],
                "measures": [{"name": "m", "expression": "1"}],
            },
            {
                "name": "LocalDateTable_1",
                "isPrivate": True,
                "isHidden": True,
                "columns": [{"name": "Date"}],
            },
        ],
        "relationships": [
            # TMSL omits defaults: fromCardinality=many, toCardinality=one, active.
            {
                "name": "r1",
                "fromTable": "Sales",
                "fromColumn": "Key",
                "toTable": "Date",
                "toColumn": "Key",
            },
            {
                "name": "r2",
                "fromTable": "Returns",
                "fromColumn": "Key",
                "toTable": "Date",
                "toColumn": "Key",
            },
        ],
    }


def test_topology_from_tmsl_exempts_parameters_calc_groups_measure_and_auto_date_tables():
    orch = AnalysisOrchestrator(config={})
    result = orch._run_topology_analysis(None, tmsl_model=_tmsl())
    assert result["success"] is True and result["source"] == "tmsl"
    assert result["findings"] == []


def test_topology_from_tmsl_detects_fact_to_fact():
    m = _tmsl()
    m["tables"].append({"name": "Budget", "columns": [{"name": "Key"}]})
    m["relationships"].append(
        {
            "name": "f2f",
            "fromTable": "Budget",
            "fromColumn": "Key",
            "toTable": "Sales",
            "toColumn": "Key",
            "toCardinality": "many",
        }
    )
    m["relationships"].append(
        {
            "name": "r3",
            "fromTable": "Budget",
            "fromColumn": "Key",
            "toTable": "Date",
            "toColumn": "Key",
        }
    )
    result = AnalysisOrchestrator(config={})._run_topology_analysis(None, tmsl_model=m)
    ids = {f["finding_id"] for f in result["findings"]}
    assert "FACT_TO_FACT" in ids
