"""``00_Client_Mode status`` / ``gateway_status``: the fields that make a masking problem diagnosable
(owner decision 2026-10-12): what the MCP masking layer decides for THIS process and why, the fail
mode, the dictionary readiness (reason, classes, values) and the degraded-event counters. Counts and
fixed labels only. The gateway is faked, no settings file is read.
"""

from __future__ import annotations

import pytest

from client_gateway import control_client as cc
from server import masking_layer
from server.handlers import client_mode_handler as h
from tests._masking_fakes import pin_fail_mode

FULL_STATUS = {
    "ok": True,
    "ready": False,
    "not_ready": {
        "reason": "dictionary_incomplete",
        "total": 1,
        "not_ready": 1,
        "classes": ["query_failed"],
    },
    "dictionary": {"values": 0, "by_prefix": {}},
    "fail_mode": "degrade",
    "stats": {"authority_degraded": 4, "not_ready_bypassed": 2, "requests": 9},
}


@pytest.fixture()
def fake(monkeypatch):
    state = {"routed": False, "sid": "", "status": dict(FULL_STATUS), "running": True}
    monkeypatch.setattr(cc, "env_routed", lambda environ=None: state["routed"])
    monkeypatch.setattr(cc, "current_session_id", lambda: state["sid"])
    monkeypatch.setattr(
        cc, "health", lambda timeout=1.0: {"ok": True} if state["running"] else None
    )
    monkeypatch.setattr(cc, "status", lambda timeout=3.0: state["status"])
    monkeypatch.setattr(cc, "session_mode", lambda sid, timeout=1.0: "on")
    monkeypatch.setattr(h.gw_folders, "list_folders", lambda: [])
    monkeypatch.setattr(h.gw_folders, "routes_all", lambda: False)
    monkeypatch.setattr(masking_layer, "_active_cache", {"at": -1e9, "value": False})
    monkeypatch.setenv("FINVISION_MCP_MASKING", "1")
    return state


def _status(op: str = "status") -> dict:
    return h.handle_client_mode({"operation": op})


def test_status_says_why_the_dictionary_is_not_ready(fake):
    out = _status()
    assert out["success"] is True
    assert out["ready"] is False
    assert out["not_ready_reason"] == "dictionary_incomplete"
    assert out["not_ready_classes"] == ["query_failed"]
    assert out["values"] == 0
    assert out["gateway_fail_mode"] == "degrade" and out["fail_mode"] == "degrade"
    assert out["degraded_events"] == {
        "authority_degraded": 4,
        "authority_retried": 0,
        "authority_failures": 0,
        "not_ready_bypassed": 2,
        "ner_auto_degraded": 0,
    }
    assert "requests" not in out["degraded_events"]


def test_gateway_status_is_the_same_view(fake):
    assert _status("gateway_status")["not_ready_reason"] == "dictionary_incomplete"


def test_a_ready_dictionary_has_no_reason_fields(fake):
    fake["status"] = {**FULL_STATUS, "ready": True, "not_ready": {}, "dictionary": {"values": 7}}
    out = _status()
    assert out["ready"] is True and out["values"] == 7
    assert "not_ready_reason" not in out and "not_ready_classes" not in out


def test_a_sessionless_client_like_copilot_is_told_the_mcp_layer_masks(fake):
    out = _status()  # no CLAUDE_CODE_SESSION_ID, a gateway runs, the flag is on
    assert out["mcp_masking_effective"] == {"effective": True, "basis": "gateway_running"}
    assert "MCP masking layer masks every Power BI result" in out["note"]
    assert "nothing masks it" not in out["note"]


def test_a_claude_code_session_reports_the_session_basis(fake):
    fake["sid"] = "s-1"
    assert _status()["mcp_masking_effective"] == {"effective": True, "basis": "session"}


def test_the_flag_off_is_reported_as_such_and_the_note_says_nothing_masks(fake, monkeypatch):
    monkeypatch.delenv("FINVISION_MCP_MASKING", raising=False)
    monkeypatch.setattr(masking_layer.config, "get", lambda key, default=None: default)
    out = _status()
    assert out["mcp_masking_effective"] == {"effective": False, "basis": "flag_off"}
    assert "nothing masks it" in out["note"]


def test_without_a_gateway_the_effective_value_is_false_and_the_status_still_answers(fake):
    fake["running"] = False
    out = _status()
    assert out["running"] is False and out["mcp_masking_effective"]["effective"] is False
    assert out["fail_mode"] == "degrade" and "ready" not in out


def test_the_fail_mode_of_this_process_is_reported(fake, monkeypatch):
    pin_fail_mode(monkeypatch, "closed")
    assert _status()["fail_mode"] == "closed"


def test_a_status_without_the_new_fields_still_works(fake):
    fake["status"] = {"ok": True, "dictionary": {"values": 3}}  # an older gateway
    out = _status()
    assert out["success"] is True and out["values"] == 3 and "ready" not in out
