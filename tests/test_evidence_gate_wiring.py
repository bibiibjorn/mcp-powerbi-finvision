"""The destructive handlers auto-populate blast-radius evidence into the gate.

Under LIGHT, a CATASTROPHIC table-delete or mass measure-delete should be
auto-cleared to an advisory (not blocked) WHEN the handler's own in-process
blast-radius computation proves the target is safe — without an agent supplying
`evidence` manually. When the blast radius is unsafe, the op stays blocked.

We patch the evidence computer (so no CLR) and the underlying manager (so no
live model), and assert the gate outcome via the handler's returned dict.
"""

from __future__ import annotations

import pytest

from core.autonomous import gating


def _PROVEN_SAFE(refs):
    """In-process evidence whose proof conditions held (C-02/C-03 contract)."""
    return {
        "blast_radius": {"all_safe": True, "unsafe_nodes": [], "impacts": {}},
        "proven": True,
        "unproven_reasons": [],
        "provenance": {"source": "in_process", "refs": list(refs)},
    }


@pytest.fixture(autouse=True)
def _light_policy(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    gating.invalidate_policy_cache()
    yield
    gating.invalidate_policy_cache()


# ---- 02_Model_Operations table/delete -------------------------------------


def test_table_delete_autocleared_when_safe(monkeypatch):
    import server.handlers.model_operations_handler as mod
    from core.autonomous import evidence as ev

    # Prove the table is safe to delete.
    monkeypatch.setattr(
        ev,
        "compute_delete_evidence",
        lambda refs, pbip_path=None: _PROVEN_SAFE(refs),
    )
    # The actual delete never touches a live model in the test.
    monkeypatch.setattr(
        mod, "_original_handle_model_operations", lambda a: {"success": True, "deleted": True}
    )

    out = mod.handle_model_operations(
        {"object_type": "table", "operation": "delete", "table_name": "Junk"}
    )
    assert out["success"] is True
    assert out.get("evidence_cleared") is True


def test_table_delete_blocked_when_unsafe(monkeypatch):
    import server.handlers.model_operations_handler as mod
    from core.autonomous import evidence as ev

    monkeypatch.setattr(
        ev,
        "compute_delete_evidence",
        lambda refs, pbip_path=None: {
            "blast_radius": {
                "all_safe": False,
                "unsafe_nodes": ["table:Junk"],
                "impacts": {},
            }
        },
    )
    called = {"deleted": False}

    def _should_not_run(a):
        called["deleted"] = True
        return {"success": True}

    monkeypatch.setattr(mod, "_original_handle_model_operations", _should_not_run)

    out = mod.handle_model_operations(
        {"object_type": "table", "operation": "delete", "table_name": "Junk"}
    )
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"
    assert called["deleted"] is False  # blocked before the delete ran


def test_table_delete_blocked_when_evidence_unavailable(monkeypatch):
    import server.handlers.model_operations_handler as mod
    from core.autonomous import evidence as ev

    # Graph unavailable -> None -> no downgrade -> normal catastrophic block.
    monkeypatch.setattr(ev, "compute_delete_evidence", lambda refs, pbip_path=None: None)
    monkeypatch.setattr(
        mod, "_original_handle_model_operations", lambda a: {"success": True, "deleted": True}
    )

    out = mod.handle_model_operations(
        {"object_type": "table", "operation": "delete", "table_name": "Junk"}
    )
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"


def test_table_delete_confirm_destructive_still_bypasses(monkeypatch):
    import server.handlers.model_operations_handler as mod
    from core.autonomous import evidence as ev

    # If the caller already confirmed, we must NOT waste a graph build.
    def _boom(refs, pbip_path=None):
        raise AssertionError("evidence should not be computed when confirmed")

    monkeypatch.setattr(ev, "compute_delete_evidence", _boom)
    monkeypatch.setattr(
        mod, "_original_handle_model_operations", lambda a: {"success": True, "deleted": True}
    )

    out = mod.handle_model_operations(
        {
            "object_type": "table",
            "operation": "delete",
            "table_name": "Junk",
            "confirm_destructive": True,
        }
    )
    assert out["success"] is True


def test_explicit_evidence_arg_is_ignored(monkeypatch):
    """C-02: a caller-supplied `evidence` never clears the gate — the handler
    recomputes it from the op's own target (here: unavailable -> block)."""
    import server.handlers.model_operations_handler as mod
    from core.autonomous import evidence as ev

    seen = []
    monkeypatch.setattr(
        ev, "compute_delete_evidence", lambda refs, pbip_path=None: seen.append(refs) or None
    )
    monkeypatch.setattr(
        mod, "_original_handle_model_operations", lambda a: {"success": True, "deleted": True}
    )

    out = mod.handle_model_operations(
        {
            "object_type": "table",
            "operation": "delete",
            "table_name": "Junk",
            "evidence": {"blast_radius": {"all_safe": True, "unsafe_nodes": []}},
        }
    )
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"
    assert seen == [["table:Junk"]]  # recomputed for the real target
    assert any("evidence" in d for d in out.get("deprecated_args", []))


# ---- 03_Batch_Operations mass measure delete ------------------------------


def test_batch_mass_delete_autocleared_when_safe(monkeypatch):
    import server.handlers.batch_operations_handler as bmod
    from core.autonomous import evidence as ev

    monkeypatch.setattr(
        ev,
        "compute_delete_evidence",
        lambda refs, pbip_path=None: _PROVEN_SAFE(refs),
    )
    monkeypatch.setattr(
        bmod._batch_ops_handler, "execute", lambda a: {"success": True, "deleted": len(a["items"])}
    )

    items = [{"table_name": "Sales", "name": f"M{i}"} for i in range(15)]  # >10 -> CATASTROPHIC
    out = bmod.handle_batch_operations(
        {"object_type": "measures", "batch_operation": "delete", "items": items}
    )
    assert out["success"] is True
    assert out.get("evidence_cleared") is True


def test_batch_mass_delete_blocked_when_unsafe(monkeypatch):
    import server.handlers.batch_operations_handler as bmod
    from core.autonomous import evidence as ev

    monkeypatch.setattr(
        ev,
        "compute_delete_evidence",
        lambda refs, pbip_path=None: {
            "blast_radius": {
                "all_safe": False,
                "unsafe_nodes": ["measure:Sales[M0]"],
                "impacts": {},
            }
        },
    )
    monkeypatch.setattr(
        bmod._batch_ops_handler, "execute", lambda a: {"success": True, "deleted": 0}
    )

    items = [{"table_name": "Sales", "name": f"M{i}"} for i in range(15)]
    out = bmod.handle_batch_operations(
        {"object_type": "measures", "batch_operation": "delete", "items": items}
    )
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"


# ---- 03_Batch_Operations apply_changeset with >10 deletes -----------------


def test_changeset_apply_autocleared_when_safe(monkeypatch):
    import server.handlers.batch_operations_handler as bmod
    from core.autonomous import evidence as ev

    monkeypatch.setattr(
        ev,
        "compute_delete_evidence",
        lambda refs, pbip_path=None: _PROVEN_SAFE(refs),
    )
    monkeypatch.setattr(
        bmod._batch_ops_handler, "apply_changeset", lambda a: {"success": True, "applied": True}
    )

    changeset = {
        "columns": {"delete": [{"table_name": "Sales", "name": f"C{i}"} for i in range(11)]},
    }
    out = bmod.handle_batch_operations({"changeset": changeset, "plan_hash": "abc"})
    assert out["success"] is True
    assert out.get("evidence_cleared") is True


# ---- FIX 3: partial-coverage guard (never vouch for a subset) --------------


def test_batch_delete_evidence_none_on_partial_coverage(monkeypatch):
    # 15 items but one is un-reffable (missing table) -> refs cover only 14.
    # The autopopulate helper must return None rather than produce evidence that
    # only covers a subset (which could clear a delete of items it never saw).
    import server.handlers.batch_operations_handler as bmod

    items = [{"table_name": "Sales", "name": f"M{i}"} for i in range(14)]
    items.append({"name": "NoTable"})  # can't build a ref
    out = bmod._autopopulate_batch_delete_evidence(
        "measures", "delete", gating.Severity.CATASTROPHIC, items, {}
    )
    assert out is None


def test_changeset_delete_evidence_none_on_partial_coverage(monkeypatch):
    import server.handlers.batch_operations_handler as bmod

    doc = {
        "columns": {
            "delete": [{"table_name": "Sales", "name": f"C{i}"} for i in range(10)]
            + [{"name": "NoTable"}]  # un-reffable
        }
    }
    out = bmod._autopopulate_changeset_delete_evidence(gating.Severity.CATASTROPHIC, doc, {})
    assert out is None


# ---- FIX 5: policy-blind computation — helpers no-op unless LIGHT ----------


def test_table_delete_autopopulate_skips_under_strict(monkeypatch):
    import server.handlers.model_operations_handler as mod
    from core.autonomous import evidence as ev

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    gating.invalidate_policy_cache()

    def _boom(refs, pbip_path=None):
        raise AssertionError("evidence must not be computed outside LIGHT")

    monkeypatch.setattr(ev, "compute_delete_evidence", _boom)
    out = mod._autopopulate_evidence(
        "table", "delete", {"table_name": "Junk"}, gating.Severity.CATASTROPHIC
    )
    assert out is None


def test_batch_delete_autopopulate_skips_under_off(monkeypatch):
    import server.handlers.batch_operations_handler as bmod
    from core.autonomous import evidence as ev

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    gating.invalidate_policy_cache()

    def _boom(refs, pbip_path=None):
        raise AssertionError("evidence must not be computed outside LIGHT")

    monkeypatch.setattr(ev, "compute_delete_evidence", _boom)
    items = [{"table_name": "Sales", "name": f"M{i}"} for i in range(15)]
    out = bmod._autopopulate_batch_delete_evidence(
        "measures", "delete", gating.Severity.CATASTROPHIC, items, {}
    )
    assert out is None


def test_changeset_autopopulate_skips_under_warn(monkeypatch):
    import server.handlers.batch_operations_handler as bmod
    from core.autonomous import evidence as ev

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "WARN")
    gating.invalidate_policy_cache()

    def _boom(refs, pbip_path=None):
        raise AssertionError("evidence must not be computed outside LIGHT")

    monkeypatch.setattr(ev, "compute_delete_evidence", _boom)
    doc = {"columns": {"delete": [{"table_name": "Sales", "name": f"C{i}"} for i in range(11)]}}
    out = bmod._autopopulate_changeset_delete_evidence(gating.Severity.CATASTROPHIC, doc, {})
    assert out is None
