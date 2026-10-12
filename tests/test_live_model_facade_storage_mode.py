"""The live-model facade reports each table's storage mode (TOM ModeType from INFO.PARTITIONS),
so 15_DAX_Refactor use_divide can refuse DirectQuery-only measures (D-05 hand-off)."""

from unittest.mock import MagicMock

from core.infrastructure import live_model_facade as lmf
from core.infrastructure.connection_state import connection_state


def _executor(partitions):
    def info(view, **_kw):
        rows = {
            "TABLES": [
                {"ID": 1, "Name": "Sales"},
                {"ID": 2, "Name": "Rates"},
                {"ID": 3, "Name": "Mixed"},
                {"ID": 4, "Name": "Lake"},
            ],
            "PARTITIONS": partitions,
        }.get(view, [])
        return {"success": True, "rows": rows}

    qe = MagicMock()
    qe.execute_info_query.side_effect = info
    return qe


def test_storage_mode_per_table_from_partitions(monkeypatch):
    qe = _executor(
        [
            {"TableID": 1, "Mode": 0},  # Import
            {"TableID": 2, "Mode": 1},  # DirectQuery
            {"TableID": 3, "Mode": 1},  # mixed partitions -> unknown
            {"TableID": 3, "Mode": 0},
            {"TableID": 4, "Mode": 5},  # DirectLake
        ]
    )
    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(connection_state, "query_executor", qe, raising=False)
    model = lmf.build_live_model()
    modes = {t.name: t.storage_mode for t in model.tables}
    assert modes == {"Sales": "import", "Rates": "directquery", "Mixed": "", "Lake": "directlake"}


def test_partitions_failure_leaves_modes_unknown(monkeypatch):
    qe = _executor([])

    def info(view, **_kw):
        if view == "PARTITIONS":
            raise RuntimeError("no INFO.PARTITIONS")
        return {"success": True, "rows": [{"ID": 1, "Name": "Sales"}] if view == "TABLES" else []}

    qe.execute_info_query.side_effect = info
    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(connection_state, "query_executor", qe, raising=False)
    model = lmf.build_live_model()
    assert [t.storage_mode for t in model.tables] == [""]
