"""Evidence-aware gate (review C-02 / C-03).

Under LIGHT a CATASTROPHIC op auto-downgrades to an advisory ONLY when the
handler passes evidence computed in-process whose proof conditions held
(``proven`` + ``provenance.source == "in_process"``) and whose blast radius is
zero (plus no baseline regression). Caller-supplied ``args["evidence"]`` is
never consulted — the old gate auto-cleared on any caller dict, which these
tests pin as fixed. STRICT is never relaxed.
"""

import pytest

from core.autonomous.gating import Severity, require_autonomous_mode, invalidate_policy_cache


def _proven(blast_radius, **extra):
    return {
        "blast_radius": blast_radius,
        "proven": True,
        "unproven_reasons": [],
        "provenance": {"source": "in_process", "refs": ["table:Junk"]},
        **extra,
    }


@pytest.fixture(autouse=True)
def _light(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    invalidate_policy_cache()
    yield
    invalidate_policy_cache()


def test_catastrophic_blocks_without_evidence():
    gate = require_autonomous_mode("02_Model_Operations:table/delete", {}, Severity.CATASTROPHIC)
    assert gate is not None
    assert gate.get("success") is False


def test_proven_safe_evidence_downgrades_to_advisory():
    gate = require_autonomous_mode(
        "02_Model_Operations:table/delete",
        {},
        Severity.CATASTROPHIC,
        evidence=_proven({"all_safe": True, "unsafe_nodes": []}),
    )
    assert gate is not None and gate.get("success") is not False
    assert gate.get("evidence_cleared") is True


def test_caller_supplied_evidence_in_args_is_ignored():
    """C-02 regression: a hand-written `evidence` arg no longer clears the gate."""
    args = {"evidence": _proven({"all_safe": True, "unsafe_nodes": []})}
    gate = require_autonomous_mode(
        "03_Batch_Operations:columns/delete", args, Severity.CATASTROPHIC
    )
    assert gate is not None and gate.get("success") is False


def test_unproven_evidence_does_not_downgrade():
    """C-03: a safe-looking blast radius from a partial/stale graph is not proof."""
    ev = _proven({"all_safe": True, "unsafe_nodes": []})
    ev["proven"] = False
    ev["unproven_reasons"] = ["graph is stale (built for a different model state)"]
    gate = require_autonomous_mode(
        "02_Model_Operations:table/delete", {}, Severity.CATASTROPHIC, evidence=ev
    )
    assert gate is not None and gate.get("success") is False


def test_evidence_without_in_process_provenance_does_not_downgrade():
    ev = _proven({"all_safe": True})
    ev["provenance"] = {"source": "caller"}
    gate = require_autonomous_mode(
        "02_Model_Operations:table/delete", {}, Severity.CATASTROPHIC, evidence=ev
    )
    assert gate is not None and gate.get("success") is False


def test_catastrophic_unsafe_evidence_still_blocks():
    gate = require_autonomous_mode(
        "02_Model_Operations:table/delete",
        {},
        Severity.CATASTROPHIC,
        evidence=_proven({"all_safe": False, "unsafe_nodes": ["measure:Sales[X]"]}),
    )
    assert gate is not None and gate.get("success") is False


def test_baseline_regression_blocks_even_with_safe_blast_radius():
    ev = _proven({"all_safe": True}, baseline_diff={"changed": ["Total Sales"]})
    gate = require_autonomous_mode(
        "02_Model_Operations:model/refresh", {}, Severity.CATASTROPHIC, evidence=ev
    )
    assert gate is not None and gate.get("success") is False


def test_single_safe_impact_shape_also_downgrades():
    gate = require_autonomous_mode(
        "15_DAX_Refactor:apply:inline_measure",
        {},
        Severity.CATASTROPHIC,
        evidence=_proven({"safe": True, "breaks": []}),
    )
    assert gate is not None and gate.get("success") is not False


def test_confirm_destructive_must_be_a_real_bool():
    """C-26: a truthy string never confirms; the legacy `confirmed` alias does."""
    blocked = require_autonomous_mode(
        "02_Model_Operations:table/delete", {"confirm_destructive": "false"}, Severity.CATASTROPHIC
    )
    assert blocked is not None and blocked.get("success") is False
    assert (
        require_autonomous_mode(
            "02_Model_Operations:table/delete", {"confirmed": True}, Severity.CATASTROPHIC
        )
        is None
    )


def test_strict_ignores_evidence(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    invalidate_policy_cache()
    gate = require_autonomous_mode(
        "02_Model_Operations:table/delete",
        {},
        Severity.CATASTROPHIC,
        evidence=_proven({"all_safe": True}),
    )
    # STRICT is the unsupervised-safe tier: evidence must NOT relax it.
    assert gate is not None and gate.get("success") is False
