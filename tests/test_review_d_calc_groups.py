"""2026-09-22 deep review, slice D — calculation-group analyzer (D-21).

TMSCHEMA_CALCULATION_GROUPS has no [Name]; the name is the table's (TableID),
and items point at their group by CalculationGroupID. Detection must follow how
DAX applies items: a filter on the group's column.
"""

from unittest.mock import MagicMock

from core.dax.calculation_group_analyzer import CalculationGroupAnalyzer

DMV = {
    "CALCULATION_GROUPS": [{"[ID]": 7, "[TableID]": 70, "[Precedence]": 10, "[Description]": None}],
    "TABLES": [{"ID": 70, "Name": "Time Int'l"}, {"ID": 71, "Name": "Sales"}],
    "CALCULATION_ITEMS": [
        {"CalculationGroupID": 7, "Name": "YTD", "Ordinal": 1},
        {"CalculationGroupID": 7, "Name": "PY", "Ordinal": 0},
    ],
    "COLUMNS": [
        {"TableID": 70, "ExplicitName": "RowNumber-2662979B", "Type": 3},
        {"TableID": 70, "ExplicitName": "Period", "Type": 1},
    ],
}


def _analyzer():
    cs = MagicMock()
    cs.is_connected.return_value = True
    queries = []

    def dmv(q):
        queries.append(q)
        for key, rows in DMV.items():
            if f"TMSCHEMA_{key}" in q:
                return {"success": True, "data": rows}
        return {"success": False, "data": []}

    cs.query_executor.execute_dmv_query.side_effect = dmv
    return CalculationGroupAnalyzer(cs), queries


def test_loads_groups_by_joining_dmvs_without_interpolating_names():
    a, queries = _analyzer()
    assert a.load_calculation_groups() is True
    g = a._calc_groups["Time Int'l"]
    assert g.items == ["PY", "YTD"] and g.precedence == 10 and g.column_name == "Period"
    assert all("Time Int" not in q for q in queries)
    assert not any("[CalculationGroupName]" in q for q in queries)


def test_detects_group_filtered_by_column_and_items_by_string():
    a, _ = _analyzer()
    res = a.analyze_dax_with_calc_groups("CALCULATE([Sales], 'Time Int''l'[Period] = \"YTD\")")
    assert res["groups_detected"] == ["Time Int'l"]
    assert res["items_referenced"] == {"Time Int'l": ["YTD"]}


def test_group_name_in_string_or_comment_is_not_usage():
    a, _ = _analyzer()
    res = a.analyze_dax_with_calc_groups("[Sales] & \"Time Int'l\" // 'Time Int''l'[Period]")
    assert res["groups_detected"] == []


def test_iterator_issue_only_when_reference_is_inside_iterator():
    a, _ = _analyzer()
    outside = a.analyze_dax_with_calc_groups(
        "SUMX(Sales, Sales[a]) + CALCULATE([S], 'Time Int''l'[Period] = \"PY\")"
    )
    assert not any(i["type"] == "calc_group_in_iterator" for i in outside["issues"])
    inside = a.analyze_dax_with_calc_groups(
        "SUMX(Sales, CALCULATE([S], 'Time Int''l'[Period] = \"PY\"))"
    )
    assert any(i["type"] == "calc_group_in_iterator" for i in inside["issues"])


def test_no_groups_means_not_loaded():
    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.query_executor.execute_dmv_query.return_value = {"success": True, "data": []}
    assert CalculationGroupAnalyzer(cs).load_calculation_groups() is False
