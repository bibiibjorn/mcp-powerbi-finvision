"""Review slice C — evidence-aware destructive gate, end to end (C-02, C-03, K-03).

Nothing here mocks the evidence computer or the graph: the handler computes the
blast radius in-process through ``core.graph.service.get_or_build_graph`` (model
side = a fake query executor answering ``EVALUATE INFO.*()`` with Desktop-shaped
PascalCase rows; report side = the synthetic golden PBIR reports read from disk),
``get_impact`` decides, and the gate auto-clears ONLY a delete the graph proves
unused. Only the final TOM write is stubbed.

The model rows extend the synthetic GoldenCo model used by the WP11 graph tests
with three synthetic tables:

* ``Scratch``  — a column + ten orphan measures, consumed by nothing;
* ``Secured``  — consumed only by an RLS role filter;
* ``Fmt``      — its measure is consumed only by another measure's
  dynamic format string.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from core.autonomous import evidence as ev
from core.autonomous import gating
from core.graph import service
from core.graph.cache import get_cache
from tests.test_review_j_graph import _INFO as _J_INFO
from tests.test_review_j_graph import FakeQE

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
PBIP = str(GOLDEN / "RPT-Sales.pbip")


def _info():
    info = copy.deepcopy(_J_INFO)
    info["TABLES"] += [
        {"[ID]": 7, "[Name]": "Scratch"},
        {"[ID]": 8, "[Name]": "Secured"},
        {"[ID]": 9, "[Name]": "Fmt"},
    ]
    info["COLUMNS"] += [
        {"[ID]": 701, "[TableID]": 7, "[ExplicitName]": "Note", "[Type]": 1},
        {"[ID]": 801, "[TableID]": 8, "[ExplicitName]": "Tenant", "[Type]": 1},
        {"[ID]": 901, "[TableID]": 9, "[ExplicitName]": "Unit", "[Type]": 1},
    ]
    info["MEASURES"] += [
        {"[ID]": 1100 + i, "[TableID]": 7, "[Name]": f"Orphan {i}", "[Expression]": str(i)}
        for i in range(10)
    ]
    info["MEASURES"] += [
        {"[ID]": 1201, "[TableID]": 9, "[Name]": "Unit Label", "[Expression]": '"K"'},
        {
            "[ID]": 1202,
            "[TableID]": 6,
            "[Name]": "Scaled",
            "[Expression]": "42",
            "[FormatStringDefinitionID]": 9101,
        },
    ]
    info["FORMATSTRINGDEFINITIONS"] += [
        {"[ID]": 9101, "[Expression]": '"#,0 " & [Unit Label]'},
    ]
    info["ROLES"] += [{"[ID]": 81, "[Name]": "Tenant Reader"}]
    info["TABLEPERMISSIONS"] += [
        {
            "[RoleID]": 81,
            "[TableID]": 8,
            "[FilterExpression]": "'Secured'[Tenant] = USERPRINCIPALNAME()",
        }
    ]
    return info


@pytest.fixture
def live(monkeypatch):
    """A 'connected' fake Desktop: INFO.* answers + the golden PBIP on disk."""
    import core.infrastructure.connection_state as cs_mod

    cs = cs_mod.connection_state
    qe = FakeQE(info=_info())
    monkeypatch.setattr(cs, "is_connected", lambda: True)
    monkeypatch.setattr(cs, "query_executor", qe, raising=False)
    monkeypatch.setattr(cs, "get_pbip_folder_path", lambda: PBIP)
    monkeypatch.setattr(
        cs,
        "get_connection_identity",
        lambda: {
            "port": 1,
            "database": "golden",
            "connection_generation": 0,
            "model_generation": cs.model_generation,
        },
    )
    get_cache().clear()
    service.invalidate_snapshot_cache()
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    gating.invalidate_policy_cache()
    yield qe
    get_cache().clear()
    service.invalidate_snapshot_cache()
    gating.invalidate_policy_cache()


def _strict(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    gating.invalidate_policy_cache()


@pytest.fixture
def model_writes(monkeypatch):
    """Stub ONLY the final TOM write of 02 / 03; record what reached it."""
    import server.handlers.batch_operations_handler as bmod
    import server.handlers.model_operations_handler as mod

    calls = []
    monkeypatch.setattr(
        mod,
        "_original_handle_model_operations",
        lambda a: calls.append(("02", dict(a))) or {"success": True, "deleted": True},
    )
    monkeypatch.setattr(
        bmod._batch_ops_handler,
        "execute",
        lambda a: calls.append(("03", dict(a))) or {"success": True, "deleted": len(a["items"])},
    )
    return calls


def _drop_table(name, **extra):
    import server.handlers.model_operations_handler as mod

    return mod.handle_model_operations(
        {"object_type": "table", "operation": "delete", "table_name": name, **extra}
    )


def _drop_measures(items, **extra):
    import server.handlers.batch_operations_handler as bmod

    return bmod.handle_batch_operations(
        {"object_type": "measures", "batch_operation": "delete", "items": items, **extra}
    )


_ORPHANS = [{"table_name": "Scratch", "name": f"Orphan {i}"} for i in range(10)]


# ---------------------------------------------------------------------------
# The real graph proves / refuses
# ---------------------------------------------------------------------------
def test_unused_table_delete_is_auto_cleared_under_light(live, model_writes):
    out = _drop_table("Scratch")
    assert out["success"] is True, out
    assert out.get("evidence_cleared") is True
    assert model_writes and model_writes[0][0] == "02"


def test_evidence_for_unused_table_is_proven_with_full_provenance(live):
    out = ev.compute_delete_evidence(["table:Scratch"])
    assert out is not None
    assert out["proven"] is True, out["unproven_reasons"]
    prov = out["provenance"]
    assert prov["source"] == "in_process"
    assert prov["graph_scope"] == "both" and prov["report_side_available"] is True
    assert prov["model_side_complete"] is True
    assert prov["model_etag"] and prov["model_etag"] == prov["current_model_etag"]
    # Every report bound to the model was scanned, not only the connected one.
    assert set(prov["reports"]) >= {"RPT-Sales", "RPT-Finance"}
    assert prov["other_bound_reports"] == []


def test_mass_delete_of_unused_measures_is_auto_cleared(live, model_writes):
    items = _ORPHANS + [{"table_name": "m Metrics", "name": "Orphan Metric"}]  # 11 -> CATASTROPHIC
    out = _drop_measures(items)
    assert out["success"] is True, out
    assert out.get("evidence_cleared") is True
    assert model_writes and model_writes[0][0] == "03"


def test_delete_of_measure_used_by_a_visual_is_not_cleared(live, model_writes):
    # 'Show In Slot' is consumed ONLY as a visual-level filter in RPT-Sales.
    items = _ORPHANS + [{"table_name": "m Metrics", "name": "Show In Slot"}]
    out = _drop_measures(items)
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"
    assert out.get("unsafe_nodes") == ["measure:m Metrics[Show In Slot]"]
    assert model_writes == []


def test_delete_of_table_used_by_an_rls_filter_is_not_cleared(live, model_writes):
    out = _drop_table("Secured")
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"
    assert out.get("unsafe_nodes") == ["table:Secured"]
    breaks = out["blast_radius"]["impacts"]["table:Secured"]["breaks"]
    assert "rls_role:Tenant Reader" in breaks
    assert model_writes == []


def test_delete_of_table_used_by_a_format_string_is_not_cleared(live, model_writes):
    out = _drop_table("Fmt")
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"
    breaks = out["blast_radius"]["impacts"]["table:Fmt"]["breaks"]
    assert "measure:m Metrics[Scaled]" in breaks
    assert model_writes == []


def test_strict_never_relaxes_even_for_a_proven_unused_delete(live, model_writes, monkeypatch):
    assert ev.compute_delete_evidence(["table:Scratch"])["proven"] is True
    _strict(monkeypatch)
    out = _drop_table("Scratch")
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"
    assert out.get("policy") == "STRICT"
    out = _drop_measures(_ORPHANS + [{"table_name": "m Metrics", "name": "Orphan Metric"}])
    assert out["success"] is False and out.get("policy") == "STRICT"
    assert model_writes == []


def test_caller_supplied_evidence_never_clears_a_used_target(live, model_writes):
    forged = {
        "blast_radius": {"all_safe": True, "unsafe_nodes": [], "impacts": {}},
        "proven": True,
        "unproven_reasons": [],
        "provenance": {"source": "in_process"},
    }
    out = _drop_table("Secured", evidence=forged)
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"
    assert model_writes == []


def test_confirm_destructive_is_the_break_glass(live, model_writes):
    out = _drop_table("Secured", confirm_destructive=True)
    assert out["success"] is True
    assert model_writes


# ---------------------------------------------------------------------------
# Proof conditions on the graph itself (fail closed)
# ---------------------------------------------------------------------------
def test_incomplete_model_side_is_not_proof(monkeypatch, live, model_writes):
    live.fail = {"COLUMNS"}  # a core INFO view fails -> model_side_complete False
    out = ev.compute_delete_evidence(["table:Scratch"])
    assert out is not None and out["proven"] is False
    assert any("model side incomplete" in r for r in out["unproven_reasons"])
    blocked = _drop_table("Scratch")
    assert blocked["success"] is False
    assert blocked.get("evidence_unproven_reasons")
    assert model_writes == []


def test_etag_mismatch_is_not_proof(monkeypatch, live):
    monkeypatch.setattr(ev, "_current_model_etag", lambda: "a-different-model-state")
    out = ev.compute_delete_evidence(["table:Scratch"])
    assert out["proven"] is False
    assert any("stale" in r for r in out["unproven_reasons"])


def test_unknown_current_etag_is_not_proof(monkeypatch, live):
    monkeypatch.setattr(ev, "_current_model_etag", lambda: None)
    out = ev.compute_delete_evidence(["table:Scratch"])
    assert out["proven"] is False
    assert any("etag unknown" in r for r in out["unproven_reasons"])


def test_unscanned_other_bound_reports_are_not_proof(monkeypatch, live):
    graph = ev._build_graph_for_evidence()
    graph.meta = dict(graph.meta, other_bound_reports=["C:/elsewhere/RPT-Other.Report"])
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: graph)
    out = ev.compute_delete_evidence(["table:Scratch"])
    assert out["proven"] is False
    assert any("other bound report" in r for r in out["unproven_reasons"])


def test_stale_graph_is_not_proof(monkeypatch, live):
    graph = ev._build_graph_for_evidence()
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: graph)
    import core.infrastructure.connection_state as cs_mod

    cs_mod.connection_state.bump_model_generation("test: model changed after the build")
    out = ev.compute_delete_evidence(["table:Scratch"])
    assert out["proven"] is False
    assert any("stale" in r for r in out["unproven_reasons"])


def test_stale_cached_graph_is_rebuilt_before_use(live):
    first = ev._build_graph_for_evidence()
    assert first is not None
    import core.infrastructure.connection_state as cs_mod

    cs_mod.connection_state.bump_model_generation("test: unrelated write")
    second = ev._build_graph_for_evidence()
    assert second is not None and second is not first
    assert service.freshness(second)["stale"] is False
    assert ev.compute_delete_evidence(["table:Scratch"])["proven"] is True


def test_graph_unavailable_without_pbip_fails_closed(monkeypatch, live, model_writes):
    import core.infrastructure.connection_state as cs_mod

    monkeypatch.setattr(cs_mod.connection_state, "get_pbip_folder_path", lambda: None)
    monkeypatch.setattr(cs_mod.connection_state, "get_pbip_info", lambda: {}, raising=False)
    assert ev.compute_delete_evidence(["table:Scratch"]) is None
    out = _drop_table("Scratch")
    assert out["success"] is False and model_writes == []


def test_not_connected_fails_closed(monkeypatch, live):
    import core.infrastructure.connection_state as cs_mod

    monkeypatch.setattr(cs_mod.connection_state, "is_connected", lambda: False)
    assert ev.compute_delete_evidence(["table:Scratch"]) is None


def test_unknown_target_is_never_vouched_for(live):
    assert ev.compute_delete_evidence(["table:Scratch", "table:DoesNotExist"]) is None


# ---------------------------------------------------------------------------
# K-03 — read-only ops are never gated; the op is validated before the gate
# ---------------------------------------------------------------------------
def test_read_only_ops_are_never_gated_even_under_strict(monkeypatch):
    import server.handlers.model_operations_handler as mod

    _strict(monkeypatch)
    try:
        seen = []
        monkeypatch.setattr(
            mod,
            "_original_handle_model_operations",
            lambda a: seen.append(a) or {"success": True, "items": []},
        )
        out = mod.handle_model_operations({"object_type": "measure", "operation": "list"})
        assert out["success"] is True and seen
        assert "gate_notice" not in out and "gate_warning" not in out
    finally:
        gating.invalidate_policy_cache()
