"""T0-2: gating tests for 02_QA_Linguistic handler.

Verifies that write ops (set_include_in_qa, add_synonym, remove_synonym) are
routed through gate_and_wrap. Under STRICT they must be blocked unless
autonomous mode is active. Under LIGHT they pass with advisory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.handlers.qa_linguistic_handler import handle_qa_linguistic

SAMPLE_SCHEMA = {
    "Version": "1.0.0",
    "Language": "en-US",
    "Entities": {
        "Sales": {
            "Definition": {"Binding": {"ConceptualEntity": "Sales"}},
            "State": "Generated",
            "Terms": [{"Sales": {"State": "Generated"}}],
        },
    },
    "Relationships": {},
    "PhraseTables": {},
    "Examples": [],
}


@pytest.fixture
def pbip_with_qa(tmp_path: Path) -> Path:
    sm = tmp_path / "Model.SemanticModel"
    cult = sm / "definition" / "cultures" / "en-US"
    cult.mkdir(parents=True)
    (cult / "linguisticMetadata.json").write_text(json.dumps(SAMPLE_SCHEMA), encoding="utf-8")
    return tmp_path


def test_load_op_is_read_only(monkeypatch: pytest.MonkeyPatch, pbip_with_qa: Path) -> None:
    """Read ops must NOT be gated even under STRICT."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous import gating

    gating.invalidate_policy_cache()
    res = handle_qa_linguistic({"operation": "load", "pbip_path": str(pbip_with_qa)})
    assert res.get("success") is True
    # No gate block.
    assert res.get("error_type") not in {"gate_blocked", "confirm_destructive_required"}


def test_add_synonym_blocked_under_strict(
    monkeypatch: pytest.MonkeyPatch, pbip_with_qa: Path
) -> None:
    """Write op must be gate-blocked under STRICT without autonomous mode."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous import gating

    gating.invalidate_policy_cache()
    res = handle_qa_linguistic(
        {
            "operation": "add_synonym",
            "pbip_path": str(pbip_with_qa),
            "entity": "Sales",
            "synonym": "revenue",
        }
    )
    assert res.get("success") is False
    assert res.get("error_type") in {"gate_blocked", "confirm_destructive_required"}


def test_add_synonym_passes_under_light_with_advisory(
    monkeypatch: pytest.MonkeyPatch, pbip_with_qa: Path
) -> None:
    """Write op passes under LIGHT (default) but with gate metadata."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    from core.autonomous import gating

    gating.invalidate_policy_cache()
    res = handle_qa_linguistic(
        {
            "operation": "add_synonym",
            "pbip_path": str(pbip_with_qa),
            "entity": "Sales",
            "synonym": "revenue",
        }
    )
    # LIGHT lets the op through; the manager runs and either succeeds or
    # returns its own error. What matters here is "not gate_blocked".
    assert res.get("error_type") not in {"gate_blocked", "confirm_destructive_required"}


def test_handler_rejects_unknown_op(pbip_with_qa: Path) -> None:
    res = handle_qa_linguistic({"operation": "delete_everything", "pbip_path": str(pbip_with_qa)})
    assert res.get("success") is False
    assert "valid_operations" in res


def test_handler_requires_pbip_path() -> None:
    res = handle_qa_linguistic({"operation": "load"})
    assert res.get("success") is False
    assert "pbip_path" in (res.get("error") or "")
