"""Review G-04 / A-16 / G-13 / G-14: 02_TMDL_Operations forwards and gates its new args."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.tmdl as tmdl_pkg
from server.handlers import tmdl_handler
from server.tool_schemas import TOOL_SCHEMAS


class _Rec:
    calls: list = []

    def __init__(self, *a, **k):
        pass

    def bulk_rename(self, **kw):
        _Rec.calls.append(("bulk_rename", kw))
        return SimpleNamespace(to_dict=lambda: {"success": True})

    def replace_in_measures(self, **kw):
        _Rec.calls.append(("replace", kw))
        return SimpleNamespace(to_dict=lambda: {"success": True})


@pytest.fixture
def rec(monkeypatch, tmp_path):
    _Rec.calls = []
    monkeypatch.setattr(tmdl_pkg, "TmdlBulkEditor", _Rec)
    monkeypatch.setattr("server.roots.list_root_paths", lambda: [str(tmp_path)])
    (tmp_path / "definition").mkdir()
    return tmp_path


def _rename_args(tmp_path, **extra):
    return {
        "operation": "bulk_rename",
        "tmdl_path": str(tmp_path / "definition"),
        "renames": [{"object_type": "measure", "old_name": "A", "new_name": "B"}],
        "dry_run": False,
        **extra,
    }


@pytest.mark.parametrize("value", [True, "PATH"])
def test_cascade_is_catastrophic_for_true_and_paths(rec, value):
    cascade = str(rec) if value == "PATH" else True
    out = tmdl_handler.handle_tmdl_operations(_rename_args(rec, cascade_to_reports=cascade))
    assert out.get("success") is False
    assert _Rec.calls == []


def test_cascade_is_forwarded_when_confirmed(rec):
    out = tmdl_handler.handle_tmdl_operations(
        _rename_args(rec, cascade_to_reports=str(rec), confirm_destructive=True)
    )
    assert out["success"] is True
    ((name, kw),) = _Rec.calls
    assert kw["cascade_to_reports"] == str(rec.resolve())


def test_no_cascade_is_not_catastrophic_and_forwards_none(rec):
    out = tmdl_handler.handle_tmdl_operations(_rename_args(rec, cascade_to_reports="false"))
    assert out["success"] is True
    assert _Rec.calls[0][1]["cascade_to_reports"] is None


def test_find_replace_forwards_scope(rec):
    tmdl_handler.handle_tmdl_operations(
        {
            "operation": "find_replace",
            "tmdl_path": str(rec / "definition"),
            "pattern": "a",
            "replacement": "b",
            "scope": "object",
            "include_strings_and_comments": True,
        }
    )
    kw = _Rec.calls[0][1]
    assert kw["scope"] == "object" and kw["include_strings_and_comments"] is True
    assert kw["target"] == "all"


def test_migrate_measures_paths_are_guarded():
    out = tmdl_handler.handle_tmdl_operations(
        {
            "operation": "migrate_measures",
            "source_path": "C:/Windows/System32/drivers/etc/hosts",
            "target_path": "C:/Windows/x.tmdl",
            "confirm_destructive": True,
        }
    )
    assert out["success"] is False


def test_schema_declares_the_args_the_handler_reads():
    props = TOOL_SCHEMAS["tmdl_operations"]["properties"]
    for key in ("cascade_to_reports", "scope", "include_strings_and_comments"):
        assert key in props
    assert "all" in props["target"]["enum"]
