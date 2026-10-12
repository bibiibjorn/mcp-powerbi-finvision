"""Dispatch-shell tests for 05_Column_Usage_Mapping (column_usage_handler).

No live AMO/CLR: the analyzer is faked via the module-level `_get_analyzer`
seam; connection_state is faked for the connection gate; the offline PBIP path
is faked via `_analyze_pbip_multi_report`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import server.handlers.column_usage_handler as mod


@pytest.fixture(autouse=True)
def _reset_analyzer_cache(monkeypatch):
    # The module caches a singleton analyzer; make sure our fake wins.
    monkeypatch.setattr(mod, "_analyzer_instance", None, raising=False)
    yield


def _connected(monkeypatch, connected=True):
    ns = SimpleNamespace(is_connected=lambda: connected, query_executor=MagicMock())
    monkeypatch.setattr(mod, "connection_state", ns)


def test_unused_pbip_missing_path_is_pure_error():
    out = mod.handle_column_usage_mapping({"operation": "get_unused_columns_pbip"})
    assert out["success"] is False
    assert "pbip_path is required" in out["error"]


def test_not_connected_gate(monkeypatch):
    _connected(monkeypatch, connected=False)
    out = mod.handle_column_usage_mapping({"operation": "get_full_mapping"})
    assert out["success"] is False
    assert out["error_type"] == "not_connected"


def test_get_measures_for_tables_missing_tables(monkeypatch):
    _connected(monkeypatch)
    monkeypatch.setattr(mod, "_get_analyzer", lambda: MagicMock())
    out = mod.handle_column_usage_mapping({"operation": "get_measures_for_tables"})
    assert out["success"] is False
    assert "tables parameter is required" in out["error"]


def test_get_measures_for_tables_routes_to_analyzer(monkeypatch):
    _connected(monkeypatch)
    analyzer = MagicMock()
    analyzer.get_measures_using_tables.return_value = {"success": True, "measures": ["M"]}
    monkeypatch.setattr(mod, "_get_analyzer", lambda: analyzer)
    out = mod.handle_column_usage_mapping(
        {"operation": "get_measures_for_tables", "tables": ["Sales"]}
    )
    assert out["success"] is True
    analyzer.get_measures_using_tables.assert_called_once()


def test_get_full_mapping_routes(monkeypatch):
    _connected(monkeypatch)
    analyzer = MagicMock()
    analyzer.get_full_mapping.return_value = {"success": True, "mapping": {}}
    monkeypatch.setattr(mod, "_get_analyzer", lambda: analyzer)
    out = mod.handle_column_usage_mapping({"operation": "get_full_mapping"})
    assert out["success"] is True
    analyzer.get_full_mapping.assert_called_once()


def test_unknown_operation(monkeypatch):
    _connected(monkeypatch)
    monkeypatch.setattr(mod, "_get_analyzer", lambda: MagicMock())
    out = mod.handle_column_usage_mapping({"operation": "does_not_exist"})
    assert out["success"] is False
    assert "Unknown operation" in out["error"]
    assert "valid_operations" in out


def test_unused_pbip_routes_to_multi_report(monkeypatch):
    monkeypatch.setattr(
        mod,
        "_analyze_pbip_multi_report",
        lambda *a, **k: {
            # unused_columns are Table[Column] strings (the handler splits them).
            "unused_columns": ["Sales[X]", "Sales[Y]"],
            "unused_measures": ["OldMeasure"],
            "reports_analyzed": ["r1"],
            "reports_failed": [],
            "total_reports": 1,
            "total_columns": 5,
            "total_measures": 3,
        },
    )
    out = mod.handle_column_usage_mapping(
        {"operation": "get_unused_columns_pbip", "pbip_path": "C:/whatever.pbip"}
    )
    assert out["success"] is True
    assert out["unused_columns_count"] == 2
    assert out["unused_by_table"] == {"Sales": ["X", "Y"]}
