"""Deep review 2026-09-22, slice H — write safety of the PBIR authoring gate.

H-01  parallel writes to one visual.json must not lose updates (reviewer race
      probe: 2 threads x 60 set_raw_property on one visual, 120/120 keys).
H-29  a failed write surfaces (``write_failed``) and a PARTIAL write keeps the
      reload hint + ``files_modified``.
H-33  a folder holding several ``.Report`` folders is ``ambiguous_report``.
H-40  a cached Desktop status showing unsaved changes is surfaced and, under
      STRICT, blocks the disk write unless ``confirm_destructive``.
A-06  ``dry_run=true`` is honoured by the gate for ANY write op and the op
      classifies as a READ.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"


def _mini_report(tmp_path: Path) -> Path:
    defn = tmp_path / "M.Report" / "definition"
    (defn / "pages" / "p1" / "visuals" / "v1").mkdir(parents=True)
    (defn / "pages" / "p1" / "page.json").write_text(
        json.dumps({"name": "p1", "displayName": "P1"}), encoding="utf-8"
    )
    (defn / "pages" / "pages.json").write_text(
        json.dumps({"pageOrder": ["p1"], "activePageName": "p1"}), encoding="utf-8"
    )
    (defn / "pages" / "p1" / "visuals" / "v1" / "visual.json").write_text(
        json.dumps(
            {
                "name": "v1",
                "position": {"x": 0, "y": 0},
                "visual": {"visualType": "card", "objects": {}},
            }
        ),
        encoding="utf-8",
    )
    return defn


def _objects(defn: Path) -> dict:
    return json.loads(
        (defn / "pages" / "p1" / "visuals" / "v1" / "visual.json").read_text(encoding="utf-8")
    )["visual"]["objects"]


# ---------------------------------------------------------------------------
# H-01
# ---------------------------------------------------------------------------


def _race(call, n=60):
    errors: list = []
    fails = [0]

    def worker(tag: str) -> None:
        for i in range(n):
            try:
                r = call(tag, i)
                if not r.get("success"):
                    fails[0] += 1
            except Exception as e:  # pragma: no cover - reported below
                errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors, fails[0]


def test_h01_parallel_set_raw_property_loses_no_update(tmp_path):
    from server.handlers.visual_config_handler import _op_set_raw_property

    defn = _mini_report(tmp_path)

    def call(tag, i):
        return _op_set_raw_property(
            {
                "visual_name": "v1",
                "page_name": "P1",
                "json_path": f"visual.objects.{tag}{i}",
                "value": {"k": i},
            },
            defn,
        )

    errors, fails = _race(call)
    assert errors == [] and fails == 0
    objs = _objects(defn)
    assert len(objs) == 120, f"lost updates: {120 - len(objs)}"
    assert sorted(p.name for p in (defn / "pages" / "p1" / "visuals" / "v1").iterdir()) == [
        "visual.json"
    ]  # no leaked temp files


def test_h01_parallel_writes_through_the_tool_handler(tmp_path):
    from server.handlers.visual_operations_handler import handle_visual_operations

    defn = _mini_report(tmp_path)
    report = str(defn.parent)

    def call(tag, i):
        return handle_visual_operations(
            {
                "operation": "set_raw_property",
                "pbip_path": report,
                "visual_name": "v1",
                "page_name": "p1",
                "json_path": f"visual.objects.{tag}{i}",
                "value": [i],
            }
        )

    errors, fails = _race(call, n=30)
    assert errors == [] and fails == 0
    assert len(_objects(defn)) == 60


# ---------------------------------------------------------------------------
# H-29
# ---------------------------------------------------------------------------


def _gate(args, dispatch, path, **kw):
    from server.handlers._authoring_utils import gate_and_wrap

    return gate_and_wrap(
        args,
        args["operation"],
        tool_name="07_Visual_Operations",
        dispatch_fn=dispatch,
        pbip_path_or_defn=path,
        read_only_ops={"list"},
        catastrophic_ops=set(),
        **kw,
    )


def test_h29_partial_write_keeps_reload_hint(tmp_path):
    from core.pbip.authoring import safe_io

    defn = _mini_report(tmp_path)
    vj = defn / "pages" / "p1" / "visuals" / "v1" / "visual.json"

    def dispatch(args, path):
        safe_io.update_json(vj, lambda d: d.__setitem__("isHidden", True))
        return {"success": False, "error": "second file failed"}

    res = _gate({"operation": "update_position"}, dispatch, str(defn))
    assert res["success"] is False
    assert res["partial_write"] is True
    assert res["files_modified"] == 1
    assert res["reload_required"] is True


def test_h29_write_error_becomes_an_envelope(tmp_path):
    from core.pbip.authoring import safe_io

    defn = _mini_report(tmp_path)

    def dispatch(args, path):
        raise safe_io.WriteError("locked by another process", defn / "x.json")

    res = _gate({"operation": "update_position"}, dispatch, str(defn))
    assert res["success"] is False
    assert res["error_type"] == "write_failed"
    assert "reload_required" not in res


def test_h29_write_outside_the_report_is_refused(tmp_path):
    from core.pbip.authoring import safe_io

    defn = _mini_report(tmp_path)
    outside = tmp_path / "elsewhere.json"

    def dispatch(args, path):
        safe_io.write_json(outside, {"x": 1})
        return {"success": True}

    res = _gate({"operation": "update_position"}, dispatch, str(defn))
    assert res["error_type"] == "write_failed"
    assert not outside.exists()


# ---------------------------------------------------------------------------
# H-33
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler_path,op",
    [
        ("server.handlers.visual_operations_handler:handle_visual_operations", "update_position"),
        ("server.handlers.page_operations_handler:handle_page_operations", "resize"),
        ("server.handlers.theme_operations_handler:handle_theme_operations", "set_colors"),
        ("server.handlers.bookmark_operations_handler:handle_bookmark_operations", "create"),
        ("server.handlers.authoring_handler:handle_pbip_authoring", "create_page"),
    ],
)
def test_h33_multi_report_folder_is_ambiguous(tmp_path, handler_path, op):
    import importlib

    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    mod, fn = handler_path.split(":")
    handler = getattr(importlib.import_module(mod), fn)
    res = handler(
        {
            "operation": op,
            "pbip_path": str(root),
            "page_name": "Overview",
            "visual_name": "vc01cardvisual000001",
            "x": 1,
            "width": 100,
            "height": 100,
            "display_name": "B",
            "colors": {"background": "#FFFFFF"},
            "confirm_destructive": True,
        }
    )
    assert res["success"] is False
    assert res["error_type"] == "ambiguous_report"
    assert len(res["candidates"]) == 3


# ---------------------------------------------------------------------------
# H-40
# ---------------------------------------------------------------------------


@pytest.fixture
def cached_unsaved_status(tmp_path, monkeypatch):
    from core.autonomous import desktop_bridge as db

    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    status = {
        "instances": [
            {
                "pid": 4242,
                "currentFilePath": str(root / "RPT-Sales.pbip"),
                "hasUnsavedChanges": True,
            }
        ]
    }
    monkeypatch.setitem(db._STATUS_CACHE, "result", status)
    monkeypatch.setitem(db._STATUS_CACHE, "t", time.monotonic())
    return root


def _resize(root: Path, **extra):
    from server.handlers.page_operations_handler import handle_page_operations

    return handle_page_operations(
        {
            "operation": "resize",
            "pbip_path": str(root / "RPT-Sales.Report"),
            "page_name": "Overview",
            "width": 1300,
            "height": 720,
            **extra,
        }
    )


def test_h40_desktop_state_is_reported(cached_unsaved_status, monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    res = _resize(cached_unsaved_status)
    assert res["success"] is True
    assert res["desktop_state"]["has_unsaved_changes"] is True
    assert res["desktop_state"]["pid"] == 4242


def test_h40_strict_blocks_write_over_unsaved_desktop(cached_unsaved_status, monkeypatch):
    from core.autonomous.gating import invalidate_policy_cache

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    invalidate_policy_cache()
    page_json = (
        cached_unsaved_status / "RPT-Sales.Report/definition/pages/aa01overview00000001/page.json"
    )
    before = page_json.read_bytes()
    # confirm_destructive clears the destructive gate but a STRICT unsaved
    # Desktop still needs it explicitly — pass it only on the second call.
    blocked = _resize(cached_unsaved_status, dry_run=False)
    assert blocked["success"] is False
    assert blocked["error_type"] in ("desktop_unsaved_changes", "confirm_destructive_required")
    assert page_json.read_bytes() == before
    ok = _resize(cached_unsaved_status, confirm_destructive=True)
    assert ok["success"] is True
    assert json.loads(page_json.read_text(encoding="utf-8"))["width"] == 1300


def test_h40_strict_unsaved_block_is_its_own_error(cached_unsaved_status, monkeypatch):
    """With the destructive gate passed, the unsaved-Desktop check still blocks."""
    from core.autonomous.gating import invalidate_policy_cache
    from server.handlers import _authoring_utils as au

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    invalidate_policy_cache()
    monkeypatch.setattr(au, "require_autonomous_mode", lambda *a, **k: None)
    res = _resize(cached_unsaved_status)
    assert res["error_type"] == "desktop_unsaved_changes"
    assert res["desktop_state"]["open"] is True


# ---------------------------------------------------------------------------
# A-06
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    [
        "07_Bookmark_Operations",
        "07_Page_Operations",
        "07_Report_Operations",
        "07_Theme_Operations",
        "07_Visual_Operations",
        "11_PBIP_Authoring",
    ],
)
def test_a06_every_write_op_dry_run_classifies_read(tool):
    from server.op_classification import _OP_TABLES, classify

    for op, level in _OP_TABLES[tool].items():
        assert classify(tool, {"operation": op, "dry_run": True}) == "read", op
        if level != "read" and op not in ("manage_visual_calcs", "manage_extension_measures"):
            assert classify(tool, {"operation": op}) == level


def test_a06_gate_dry_run_writes_nothing_and_reports_the_plan(tmp_path):
    from core.pbip.authoring import safe_io

    defn = _mini_report(tmp_path)
    vj = defn / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    before = vj.read_bytes()

    def dispatch(args, path):
        safe_io.update_json(vj, lambda d: d.__setitem__("isHidden", True))
        safe_io.remove_path(defn / "pages" / "p1" / "page.json")
        return {"success": True}

    res = _gate({"operation": "delete", "dry_run": True}, dispatch, str(defn))
    assert res["dry_run"] is True
    assert res["files_modified"] == 0
    assert "reload_required" not in res
    assert {c["action"] for c in res["would_change"]} == {"write", "delete"}
    assert vj.read_bytes() == before
    assert (defn / "pages" / "p1" / "page.json").exists()


def test_h01_tree_move_retries_a_transient_lock(tmp_path, monkeypatch):
    """A folder move right after a copy can hit WinError 5/32 (AV / indexer):
    safe_io retries with backoff instead of failing the clone."""
    import os as _os

    from core.pbip.authoring import safe_io
    from core.utilities import pbip_utils

    monkeypatch.setattr(pbip_utils, "_REPLACE_BACKOFF_S", (0.0, 0.0, 0.0))
    src = tmp_path / "a"
    src.mkdir()
    (src / "f.json").write_text("{}", encoding="utf-8")
    real = _os.replace
    calls = {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "locked")
        return real(a, b)

    monkeypatch.setattr(_os, "replace", flaky)
    safe_io.move_path(src, tmp_path / "b")
    assert (tmp_path / "b" / "f.json").exists() and calls["n"] == 3
