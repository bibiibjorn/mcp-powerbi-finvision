"""04_Run_DAX export_path: stream the full result set to disk.

When export_path is set, the rows are streamed by the executor straight to CSV
or JSON on disk (B-15: no 10k reader cap, honest ``truncated``/``total_rows``,
atomic write, absolute path, CSV as UTF-8 with BOM) and the handler returns a
compact {export_path, row_count, columns, sample} envelope instead of the
(middleware-truncated) rows.

The executor is the REAL OptimizedQueryExecutor over a fake ADOMD command —
the seam that used to truncate is exercised, not mocked away.
"""

import csv
import json
import os
from unittest.mock import MagicMock


import server.handlers.query_handler as qh
from core.infrastructure import query_executor as qx


class _FakeReader:
    def __init__(self, columns, rows):
        self._columns = columns
        self._rows = rows
        self._i = -1
        self.closed = 0

    @property
    def FieldCount(self):
        return len(self._columns)

    def GetName(self, i):
        return self._columns[i]

    def Read(self):
        self._i += 1
        return self._i < len(self._rows)

    def GetValue(self, i):
        return self._rows[self._i][i]

    def Close(self):
        self.closed += 1


def _install_fake_adomd(monkeypatch, columns, rows):
    created = []

    class FakeCommand:
        def __init__(self, text, conn):
            self.text = text
            self.CommandTimeout = 0
            created.append(self)

        def ExecuteReader(self):
            return _FakeReader(columns, rows)

    monkeypatch.setattr(qx, "AdomdCommand", FakeCommand)
    monkeypatch.setattr(qx, "ADOMD_AVAILABLE", True)
    return created


def _patch_connected(monkeypatch, n_rows):
    columns = ["[Col]", "[Name]"]
    rows = [[i, f"r{i}"] for i in range(n_rows)]
    _install_fake_adomd(monkeypatch, columns, rows)
    executor = qx.OptimizedQueryExecutor(object())
    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.query_executor = executor
    cs.get_connection_identity.return_value = {"port": 1, "model_generation": 0}
    policy = MagicMock()
    policy.safe_run_dax.return_value = {
        "success": True,
        "rows": [{"[Col]": 0}],
        "columns": ["[Col]"],
        "row_count": 1,
    }
    cs.agent_policy = policy
    monkeypatch.setattr(qh, "connection_state", cs)
    monkeypatch.setattr(qh, "_read_only_mode", lambda: False)
    return policy


def test_export_csv_writes_all_rows_and_returns_summary(tmp_path, monkeypatch):
    # 12,500 rows: more than the old 10k SAFETY_MAX_ROWS reader cap.
    _patch_connected(monkeypatch, 12_500)
    out = tmp_path / "result.csv"

    res = qh.handle_run_dax({"query": "EVALUATE T", "export_path": str(out)})

    assert res["success"] is True
    assert res["exported"] is True
    assert res["row_count"] == 12_500
    assert res["truncated"] is False
    assert res["export_path"] == os.path.abspath(str(out))
    assert "rows" not in res
    assert len(res["sample"]) <= 10
    assert res["cache"]["hit"] is False

    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV must be UTF-8 with BOM for Excel"
    with open(out, newline="", encoding="utf-8-sig") as fh:
        reader = list(csv.DictReader(fh))
    assert len(reader) == 12_500


def test_export_json_writes_all_rows(tmp_path, monkeypatch):
    _patch_connected(monkeypatch, 40)
    out = tmp_path / "result.json"

    res = qh.handle_run_dax({"query": "EVALUATE T", "export_path": str(out)})
    assert res["success"] is True
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 40
    assert data[3] == {"[Col]": 3, "[Name]": "r3"}


def test_export_cap_reports_truncation_and_total(tmp_path, monkeypatch):
    _patch_connected(monkeypatch, 30)
    out = tmp_path / "capped.csv"
    res = qh.handle_run_dax({"query": "EVALUATE T", "export_path": str(out), "top_n": 10})
    assert res["success"] is True
    assert res["row_count"] == 10
    assert res["total_rows"] == 30
    assert res["truncated"] is True
    assert "warning" in res


def test_export_refused_in_read_only_mode(tmp_path, monkeypatch):
    _patch_connected(monkeypatch, 3)
    monkeypatch.setattr(qh, "_read_only_mode", lambda: True)
    out = tmp_path / "x.csv"
    res = qh.handle_run_dax({"query": "EVALUATE T", "export_path": str(out)})
    assert res["success"] is False
    assert res["error_type"] == "read_only_mode"
    assert not out.exists()


def test_no_export_path_returns_rows_as_before(monkeypatch):
    policy = _patch_connected(monkeypatch, 5)
    res = qh.handle_run_dax({"query": "EVALUATE T"})
    assert res["rows"] == policy.safe_run_dax.return_value["rows"]
    assert "exported" not in res
    assert res["cache"] == {"hit": False, "age_s": None}
