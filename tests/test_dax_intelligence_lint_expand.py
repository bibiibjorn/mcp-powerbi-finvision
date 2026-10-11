"""05_DAX_Intelligence lint_model + expand_measure (Phase 5 quick wins).

lint_model: run the unified analyzer over every measure in one call, returning
findings grouped by rule with an auto_fixable flag (maps to a 15_DAX_Refactor
transform). Eliminates the N-serial-call pattern.

expand_measure: emit a self-contained DEFINE MEASURE ... EVALUATE query inlining
a measure plus its transitive measure dependencies (DAX Studio parity).
"""

from unittest.mock import MagicMock

import server.handlers.dax_context_handler as dh


def _patch_measures(monkeypatch, rows):
    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()
    qe.enumerate_measures_tom.return_value = {"success": True, "rows": rows, "row_count": len(rows)}
    cs.query_executor = qe
    monkeypatch.setattr(dh, "connection_state", cs)
    return cs


# --- lint_model --------------------------------------------------------------


def test_lint_model_returns_findings_per_measure(monkeypatch):
    rows = [
        {"Name": "Bad", "Table": "F", "Expression": "IF([X] > 0, TRUE, FALSE)"},
        {"Name": "Clean", "Table": "F", "Expression": "SUM('F'[Amount])"},
    ]
    _patch_measures(monkeypatch, rows)

    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    assert res["success"] is True
    assert res["measures_analyzed"] == 2
    # Findings keyed/grouped so the worst measures are triageable.
    assert res["total_findings"] >= 1
    # The redundant IF measure must be flagged.
    flagged = {f["measure"] for f in res["findings"]}
    assert "Bad" in flagged


def test_lint_model_not_connected(monkeypatch):
    cs = MagicMock()
    cs.is_connected.return_value = False
    monkeypatch.setattr(dh, "connection_state", cs)
    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    assert res["success"] is False


# --- expand_measure ----------------------------------------------------------


def test_expand_measure_inlines_transitive_deps(monkeypatch):
    rows = [
        {"Name": "Total", "Table": "F", "Expression": "[Base] * 2"},
        {"Name": "Base", "Table": "F", "Expression": "SUM('F'[Amount])"},
    ]
    _patch_measures(monkeypatch, rows)

    res = dh.handle_dax_intelligence({"operation": "expand_measure", "expression": "Total"})
    assert res["success"] is True
    q = res["query"]
    # Both the target and its dependency must be defined, then EVALUATE'd.
    assert "DEFINE" in q
    assert "MEASURE 'F'[Total]" in q or "MEASURE F[Total]" in q
    assert "MEASURE 'F'[Base]" in q or "MEASURE F[Base]" in q
    assert "EVALUATE" in q


def test_expand_measure_unknown_measure_errors(monkeypatch):
    _patch_measures(monkeypatch, [{"Name": "A", "Table": "F", "Expression": "1"}])
    res = dh.handle_dax_intelligence({"operation": "expand_measure", "expression": "DoesNotExist"})
    assert res["success"] is False
