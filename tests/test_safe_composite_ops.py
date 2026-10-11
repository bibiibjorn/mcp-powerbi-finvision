"""Composite safe_delete / safe_rename ops on 02_Model_Operations.

These wrap the raw delete/rename with a mandatory in-process blast-radius check
and FAIL CLOSED: they only proceed when the dependency graph affirmatively
proves the change safe. Unlike the raw ops (which merely gate), a safe_* op that
can't prove safety refuses rather than asking for confirm_destructive.

Contract:
  safe_delete  target safe    -> runs real delete (confirm_destructive injected)
               target unsafe  -> {success:False, error_type:"unsafe_delete", blast_radius}
               no evidence    -> {success:False, error_type:"evidence_unavailable"}
  safe_rename  no collision   -> runs real rename
               collision      -> {success:False, error_type:"rename_collision"}
               no evidence    -> {success:False, error_type:"evidence_unavailable"}

The raw delete/rename dispatch is monkeypatched, so no CLR / live model.
"""

from __future__ import annotations

import pytest

import server.handlers.model_operations_handler as mod
from core.autonomous import evidence as ev
from core.autonomous import gating


@pytest.fixture(autouse=True)
def _light(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    gating.invalidate_policy_cache()
    yield
    gating.invalidate_policy_cache()


def _proven(br, pbip_path=None):
    """In-process evidence whose proof conditions held (C-02/C-03 contract)."""
    return {
        "blast_radius": br,
        "proven": True,
        "unproven_reasons": [],
        "provenance": {"source": "in_process", "pbip_path": pbip_path},
    }


def _capture_dispatch(monkeypatch):
    seen = {}

    def _fake(a):
        seen["args"] = a
        return {"success": True, "op": a.get("operation")}

    monkeypatch.setattr(mod, "_original_handle_model_operations", _fake)
    return seen


# ---- safe_delete ----------------------------------------------------------


def test_safe_delete_table_runs_when_safe(monkeypatch):
    seen = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(
        ev,
        "compute_delete_evidence",
        lambda refs, pbip_path=None: _proven({"all_safe": True, "unsafe_nodes": [], "impacts": {}}),
    )
    out = mod.handle_model_operations(
        {"object_type": "table", "operation": "safe_delete", "table_name": "Junk"}
    )
    assert out["success"] is True
    # The real op ran as a delete, with confirm injected (evidence proved safe).
    assert seen["args"]["operation"] == "delete"
    assert seen["args"]["confirm_destructive"] is True


def test_safe_delete_refuses_when_unsafe(monkeypatch):
    seen = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(
        ev,
        "compute_delete_evidence",
        lambda refs, pbip_path=None: {
            "blast_radius": {
                "all_safe": False,
                "unsafe_nodes": ["table:Junk"],
                "impacts": {"table:Junk": {"breaks": ["measure:Sales[X]"]}},
            }
        },
    )
    out = mod.handle_model_operations(
        {"object_type": "table", "operation": "safe_delete", "table_name": "Junk"}
    )
    assert out["success"] is False
    assert out["error_type"] == "unsafe_delete"
    assert "blast_radius" in out
    assert "args" not in seen  # never dispatched the delete


def test_safe_delete_refuses_when_evidence_unavailable(monkeypatch):
    seen = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(ev, "compute_delete_evidence", lambda refs, pbip_path=None: None)
    out = mod.handle_model_operations(
        {"object_type": "measure", "operation": "safe_delete", "table_name": "Sales", "name": "M1"}
    )
    assert out["success"] is False
    assert out["error_type"] == "evidence_unavailable"
    assert "args" not in seen


def test_safe_delete_measure_builds_measure_ref(monkeypatch):
    captured = {}

    def _fake(refs, pbip_path=None):
        captured["refs"] = refs
        return {"blast_radius": {"all_safe": True, "unsafe_nodes": []}}

    monkeypatch.setattr(ev, "compute_delete_evidence", _fake)
    _capture_dispatch(monkeypatch)
    mod.handle_model_operations(
        {"object_type": "measure", "operation": "safe_delete", "table_name": "Sales", "name": "M1"}
    )
    assert captured["refs"] == ["measure:Sales[M1]"]


# ---- safe_rename ----------------------------------------------------------


def test_safe_rename_runs_when_no_collision(monkeypatch):
    seen = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(
        ev,
        "compute_rename_evidence",
        lambda ref, new, pbip_path=None: _proven({"all_safe": True, "unsafe_nodes": []}),
    )
    out = mod.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "safe_rename",
            "table_name": "Sales",
            "name": "Old",
            "new_name": "New",
        }
    )
    assert out["success"] is True
    assert seen["args"]["operation"] == "rename"
    assert seen["args"]["new_name"] == "New"


def test_safe_rename_refuses_on_collision(monkeypatch):
    seen = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(
        ev,
        "compute_rename_evidence",
        lambda ref, new, pbip_path=None: {
            "blast_radius": {
                "all_safe": False,
                "unsafe_nodes": [ref],
                "impacts": {ref: {"collision": True, "collides_with": "measure:Sales[New]"}},
            }
        },
    )
    out = mod.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "safe_rename",
            "table_name": "Sales",
            "name": "Old",
            "new_name": "New",
        }
    )
    assert out["success"] is False
    assert out["error_type"] == "rename_collision"
    assert "args" not in seen


def test_safe_rename_requires_new_name(monkeypatch):
    _capture_dispatch(monkeypatch)
    out = mod.handle_model_operations(
        {"object_type": "measure", "operation": "safe_rename", "table_name": "Sales", "name": "Old"}
    )
    assert out["success"] is False
    assert out["error_type"] == "invalid_args"
