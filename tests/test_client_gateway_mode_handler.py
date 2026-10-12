"""00_Client_Mode gateway operations and the control client.

A fake loopback gateway stands in for the real one; the masking home and every settings file are
temp copies. Synthetic data only.
"""

from __future__ import annotations

import json
import threading

import pytest

from client_gateway import control_client as cc
from client_gateway import folders as gw_folders
from server.handlers import client_mode_handler as h
from server.op_classification import classify
from server.tool_schemas import TOOL_SCHEMAS
from tests.test_client_gateway_hook import FakeGateway

SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def gw(tmp_path, monkeypatch):
    gateway = FakeGateway().start()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(home))
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", str(gateway.port))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    cfg = tmp_path / "claude-config"  # never the user's real ~/.claude/settings.json
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    project = tmp_path / "project"  # the chat's project folder (never the repo the tests run in)
    project.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    gateway.home = home
    gateway.cfg = cfg
    gateway.project = project
    yield gateway
    gateway.stop()


@pytest.fixture()
def routed(gw, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{gw.port}")
    return gw


def _stopped_port(monkeypatch) -> None:
    """A port nobody listens on."""
    probe = FakeGateway()
    port = probe.port
    probe.server.server_close()
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", str(port))


# ------------------------------------------------------------------------------ surface
def test_schema_enum_matches_the_handler_and_has_folder_path():
    schema = TOOL_SCHEMAS["client_mode"]
    assert tuple(schema["properties"]["operation"]["enum"]) == h._VALID_OPS
    assert schema["properties"]["folder_path"]["type"] == "string"
    assert schema["required"] == ["operation"]


def test_op_levels():
    levels = {op: classify("00_Client_Mode", {"operation": op}) for op in h._VALID_OPS}
    assert levels["gateway_status"] == "read" and levels["status"] == "read"
    for op in (
        "gateway_start",
        "gateway_stop",
        "route_folder",
        "unroute_folder",
        "route_all",
        "unroute_all",
        "on",
        "off",
    ):
        assert levels[op] == "write", op


def test_unknown_operation_lists_the_new_ones():
    out = h.handle_client_mode({"operation": "bogus"})
    assert out["error_type"] == "invalid_operation"
    assert {
        "gateway_status",
        "gateway_start",
        "gateway_stop",
        "route_folder",
        "unroute_folder",
        "route_all",
        "unroute_all",
    } <= set(out["valid_values"])
    assert "open" not in out["valid_values"]


# ------------------------------------------------------------------------------ status
def test_status_without_gateway(gw, monkeypatch):
    _stopped_port(monkeypatch)
    out = h.handle_client_mode({"operation": "status"})
    assert out["success"] is True and out["running"] is False
    assert out["routed_session"] is False and "not routed" in out["note"]


def test_status_in_a_routed_session_reports_gateway_facts(routed):
    out = h.handle_client_mode({"operation": "status"})
    assert out["running"] is True and out["version"] == "t" and out["routed_session"] is True
    assert out["session_mode"] == "on" and out["every_chat_routed"] is False
    assert out["dictionary"] == {"total": 3, "by_prefix": {"CUS": 2, "ORG": 1}}
    routed.modes[SID] = "off"
    assert h.handle_client_mode({"operation": "status"})["session_mode"] == "off"


def test_status_says_when_a_routed_session_has_no_gateway(routed, monkeypatch):
    _stopped_port(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{cc.gateway_port()}")
    out = h.handle_client_mode({"operation": "status"})
    assert out["running"] is False and "not running" in out["note"]


# ------------------------------------------------------------------------------ on / off / refresh
def test_on_is_the_typed_masking_on_it_switches_on_and_routes_the_project(gw):
    gw.modes[SID] = "off"
    out = h.handle_client_mode({"operation": "on"})
    assert out["success"] is True and out["masking"] == "on"
    assert out["folder"] == str(gw.project)
    post = [c for c in gw.calls if c[0] == "POST"][0]
    assert post[2] == {"session": SID, "mode": "on"}
    assert gw.modes[SID] == "on"
    env = json.loads((gw.project / ".claude" / "settings.local.json").read_text("utf-8"))["env"]
    assert env["ANTHROPIC_BASE_URL"] == f"http://127.0.0.1:{gw.port}"


def test_on_when_the_gateway_cannot_start_is_an_error(gw, monkeypatch):
    monkeypatch.setattr(cc, "start_gateway", lambda wait_s=0: {"running": False, "error": "x"})
    out = h.handle_client_mode({"operation": "on"})
    assert out["error_type"] == "masking_on_failed" and "masking on" in out["suggestion"]
    assert not (gw.project / ".claude" / "settings.local.json").exists()


@pytest.fixture()
def copilot(gw, monkeypatch):
    """A Power BI MCP without a Claude Code chat (VS Code Copilot): no session id, no project."""
    from server import masking_layer

    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    monkeypatch.delenv(masking_layer.ENV_FLAG, raising=False)  # restored after the test
    monkeypatch.setattr(masking_layer, "configured", lambda: True)
    monkeypatch.setattr(cc, "routing_wanted", lambda: False)
    return gw


def test_on_without_a_claude_code_chat_masks_the_power_bi_results(copilot, monkeypatch):
    from client_gateway import keep

    monkeypatch.setattr(cc, "spawn_gateway", lambda: pytest.fail("the gateway runs already"))
    out = h.handle_client_mode({"operation": "on"})
    assert out["success"] is True and out["masking"] == "on"
    assert "masked" in out["power_bi_results"]
    assert out["mcp_masking_effective"] == {"effective": True, "basis": "gateway_running"}
    assert (
        out["chat_text"].startswith("not masked")
        and "--desktop-apps" in out["to_mask_the_chat_too"]
    )
    assert keep.registered(copilot.home)  # the gateway keeps running while this MCP runs
    assert not [c for c in copilot.calls if c[0] == "POST"]  # no session switch, no global switch
    assert not (copilot.project / ".claude").exists()  # no folder is routed
    keep.unregister(copilot.home)


def test_on_without_a_claude_code_chat_and_routed_desktop_apps(copilot, monkeypatch):
    from client_gateway import keep

    monkeypatch.setattr(cc, "routing_wanted", lambda: True)
    out = h.handle_client_mode({"operation": "on"})
    assert out["success"] is True and out["chat_text"].startswith("masked")
    assert "to_mask_the_chat_too" not in out
    keep.unregister(copilot.home)


def test_on_without_a_claude_code_chat_sets_the_flag_for_this_process(copilot, monkeypatch):
    from client_gateway import keep
    from server import masking_layer

    monkeypatch.setattr(masking_layer, "configured", lambda: False)
    out = h.handle_client_mode({"operation": "on"})
    assert out["success"] is True
    assert masking_layer.os.environ.get(masking_layer.ENV_FLAG) == "1"
    keep.unregister(copilot.home)


def test_on_without_a_claude_code_chat_when_the_gateway_cannot_start(copilot, monkeypatch):
    from client_gateway import keep

    monkeypatch.setattr(cc, "start_gateway", lambda wait_s=0: {"running": False, "error": "x"})
    out = h.handle_client_mode({"operation": "on"})
    assert out["error_type"] == "gateway_start_failed" and "setup --check" in out["suggestion"]
    assert not keep.registered(copilot.home)


def test_off_without_a_claude_code_chat_tells_the_user_the_terminal_command(copilot):
    out = h.handle_client_mode({"operation": "off"})
    assert out["error_type"] == "user_action_required"
    assert "client_gateway stop" in out["user_action"] and copilot.calls == []


def test_off_without_a_claude_code_chat_with_routed_desktop_apps(copilot, monkeypatch):
    monkeypatch.setattr(cc, "routing_wanted", lambda: True)
    out = h.handle_client_mode({"operation": "off"})
    assert "setup --remove --yes" in out["user_action"]


def test_off_in_a_routed_session_is_never_sent_to_the_gateway(routed):
    out = h.handle_client_mode({"operation": "off"})
    assert out["success"] is False and out["error_type"] == "user_action_required"
    assert out["user_action"] == "type: masking off in the chat"
    assert routed.calls == [] and routed.modes == {}


def test_off_is_always_the_users(gw):
    out = h.handle_client_mode({"operation": "off"})
    assert out["error_type"] == "user_action_required" and gw.calls == []


def test_on_without_a_project_folder_masks_like_a_client_without_a_chat(gw, monkeypatch):
    from client_gateway import keep
    from server import masking_layer

    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    monkeypatch.delenv(masking_layer.ENV_FLAG, raising=False)
    out = h.handle_client_mode({"operation": "on"})
    assert out["success"] is True and "power_bi_results" in out
    assert not [c for c in gw.calls if c[0] == "POST"]
    keep.unregister(gw.home)


def test_refresh_in_a_routed_session_asks_the_gateway(routed):
    out = h.handle_client_mode({"operation": "refresh"})
    assert out["success"] is True and out["dictionary"]["total"] == 3
    assert ("POST", "/__gateway/refresh", {}, None) in routed.calls


# ------------------------------------------------------------------------------ gateway ops
def test_gateway_status_lists_routed_folders(gw, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    gw_folders.route_folder(folder)
    out = h.handle_client_mode({"operation": "gateway_status"})
    assert out["success"] is True and out["running"] is True
    assert out["routed_folders"] == [str(folder)]


def test_gateway_start_when_running_does_not_spawn(gw, monkeypatch):
    monkeypatch.setattr(cc, "spawn_gateway", lambda: pytest.fail("must not spawn"))
    out = h.handle_client_mode({"operation": "gateway_start"})
    assert out == {"success": True, "running": True, "started": False, "url": cc.gateway_url()}


def test_gateway_start_failure_is_an_error(gw, monkeypatch):
    monkeypatch.setattr(
        cc, "start_gateway", lambda: {"running": False, "error": "no_health_answer"}
    )
    out = h.handle_client_mode({"operation": "gateway_start"})
    assert out["error_type"] == "gateway_start_failed" and "no_health_answer" in out["error"]


def test_gateway_stop_reports_the_consequence(gw, monkeypatch):
    monkeypatch.setattr(cc, "stop_gateway", lambda: {"stopped": True, "exit_code": 0})
    out = h.handle_client_mode({"operation": "gateway_stop"})
    assert out["success"] is True and "fail until the gateway runs again" in out["note"]
    assert "warning" not in out
    monkeypatch.setattr(cc, "stop_gateway", lambda: {"stopped": False})
    assert h.handle_client_mode({"operation": "gateway_stop"})["error_type"] == (
        "gateway_stop_failed"
    )


# ------------------------------------------------------------------------------ route / unroute
def test_route_and_unroute_folder_through_the_tool(gw, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    out = h.handle_client_mode({"operation": "route_folder", "folder_path": str(folder)})
    assert out["success"] is True and out["base_url"] == cc.gateway_url()
    assert "says nothing about the data" in out["note"]
    settings = json.loads((folder / ".claude" / "settings.local.json").read_text("utf-8"))
    assert settings["env"]["ANTHROPIC_BASE_URL"] == cc.gateway_url()
    out = h.handle_client_mode({"operation": "unroute_folder", "folder_path": str(folder)})
    assert out["success"] is True and out["unrouted"] is True
    assert gw_folders.list_folders() == []


def test_route_folder_argument_errors(gw, tmp_path):
    assert h.handle_client_mode({"operation": "route_folder"})["error_type"] == "missing_argument"
    out = h.handle_client_mode({"operation": "route_folder", "folder_path": str(tmp_path / "no")})
    assert out["error_type"] == "invalid_argument"
    bad = tmp_path / "bad"
    (bad / ".claude").mkdir(parents=True)
    (bad / ".claude" / "settings.local.json").write_text("{x", encoding="utf-8")
    out = h.handle_client_mode({"operation": "route_folder", "folder_path": str(bad)})
    assert out["error_type"] == "invalid_argument" and "valid JSON" in out["error"]


def test_other_base_urls_do_not_count_as_the_gateway(gw, monkeypatch):
    for url in ("https://api.anthropic.com", f"http://127.0.0.1:{gw.port + 1}", "", "nonsense"):
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        assert cc.env_routed() is False, url


# ------------------------------------------------------------------------------ control client
def test_session_mode_is_none_when_the_gateway_is_down(gw, monkeypatch):
    _stopped_port(monkeypatch)
    assert cc.health() is None and cc.session_mode(SID) is None and cc.status() is None
    assert cc.request("GET", "/__gateway/health") is None


def test_start_gateway_waits_for_health_then_gives_up(gw, monkeypatch):
    _stopped_port(monkeypatch)
    spawned = []
    monkeypatch.setattr(cc, "spawn_gateway", lambda: spawned.append(1))
    out = cc.start_gateway(wait_s=0.3)
    assert out["running"] is False and out["started"] is True and spawned == [1]

    def boom():
        raise OSError("x")

    monkeypatch.setattr(cc, "spawn_gateway", boom)
    assert cc.start_gateway(wait_s=0.3) == {"running": False, "started": False, "error": "OSError"}


def test_ensure_gateway_background(gw, tmp_path, monkeypatch):
    # under pytest the autostart is skipped on purpose
    assert cc.ensure_gateway_background() is None
    monkeypatch.setattr(cc, "_skip_autostart", lambda: False)
    assert cc.ensure_gateway_background() is None  # no routed folder: nothing starts
    folder = tmp_path / "proj"
    folder.mkdir()
    gw_folders.route_folder(folder)
    # masking on demand (2026-10-08): a routed folder is not a reason for the MCP to start the
    # gateway (the typed "masking on" does that); only routing the desktop apps is
    assert cc.ensure_gateway_background() is None
    monkeypatch.setattr(cc, "routing_wanted", lambda: True)
    monkeypatch.setattr(cc, "attach_pid", lambda pid=None: False)
    started = threading.Event()
    monkeypatch.setattr(cc, "start_gateway", lambda: started.set() or {"running": True})
    thread = cc.ensure_gateway_background()
    assert thread is not None and thread.daemon is True
    assert started.wait(5)


def test_ensure_gateway_background_never_raises(monkeypatch):
    monkeypatch.setattr(cc, "_skip_autostart", lambda: False)
    monkeypatch.setattr(gw_folders, "list_folders", lambda *a, **k: 1 / 0)
    assert cc.ensure_gateway_background() is None


def test_counts_summary_takes_numbers_only():
    assert cc.counts_summary(
        {"dictionary": {"total": 5, "by_prefix": {"CUS": 3, "ORG": 2}, "note": "x"}}
    ) == {"total": 5, "by_prefix": {"CUS": 3, "ORG": 2}}
    assert cc.counts_summary({"dictionary": {"CUS": 3, "ORG": True}}) == {
        "total": 3,
        "by_prefix": {"CUS": 3},
    }
    assert cc.counts_summary({"dictionary": {"values": 3, "by_prefix": {"CUS": 2, "ORG": 1}}}) == {
        "total": 3,
        "by_prefix": {"CUS": 2, "ORG": 1},
    }  # the gateway's own status shape
    assert cc.counts_summary({}) == {} and cc.counts_summary(None) == {}


def test_gateway_port_order_env_then_config_then_default(monkeypatch):
    monkeypatch.delenv("FINVISION_GATEWAY_PORT", raising=False)
    monkeypatch.setattr(
        "core.config.config_manager.config.get", lambda key, default=None: 9123, raising=False
    )
    assert cc.gateway_port() == 9123 and cc.gateway_url() == "http://127.0.0.1:9123"
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", "9456")
    assert cc.gateway_port() == 9456
    monkeypatch.delenv("FINVISION_GATEWAY_PORT")
    monkeypatch.setattr(
        "core.config.config_manager.config.get", lambda key, default=None: 0, raising=False
    )
    assert cc.gateway_port() == 8890


def test_hook_counts_text_reads_the_gateway_status_shape():
    from tests.test_client_gateway_hook import hook

    status = {"dictionary": {"values": 3, "by_prefix": {"CUS": 2, "ORG": 1}}}
    assert hook._counts_text(status) == "Dictionary: 3 values (CUS 2, ORG 1)."


def test_server_start_hook_is_wired_and_guarded():
    from pathlib import Path

    src = Path(cc.__file__).resolve().parent.parent / "src" / "pbixray_server_enhanced.py"
    text = src.read_text(encoding="utf-8")
    assert "ensure_gateway_background" in text and "Client gateway not started" in text


# ------------------------------------------------------------------------------ routed: user only
def test_gateway_stop_is_refused_in_a_routed_session(routed, monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(cc, "stop_gateway", lambda: called.append(1) or {"stopped": True})
    out = h.handle_client_mode({"operation": "gateway_stop"})
    assert out["error_type"] == "user_action_required" and called == []
    assert "terminal" in out["user_action"]


def test_unroute_folder_is_refused_in_a_routed_session_and_changes_nothing(routed, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    assert h.handle_client_mode({"operation": "route_folder", "folder_path": str(folder)})[
        "success"
    ]
    out = h.handle_client_mode({"operation": "unroute_folder", "folder_path": str(folder)})
    assert out["error_type"] == "user_action_required"
    settings = json.loads((folder / ".claude" / "settings.local.json").read_text("utf-8"))
    assert settings["env"]["ANTHROPIC_BASE_URL"] == cc.gateway_url()
    assert len(gw_folders.list_folders()) == 1


def test_gateway_stop_and_unroute_work_outside_a_routed_session(gw, monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "stop_gateway", lambda: {"stopped": True, "exit_code": 0})
    assert h.handle_client_mode({"operation": "gateway_stop"})["success"] is True
    folder = tmp_path / "proj"
    folder.mkdir()
    h.handle_client_mode({"operation": "route_folder", "folder_path": str(folder)})
    out = h.handle_client_mode({"operation": "unroute_folder", "folder_path": str(folder)})
    assert out["success"] is True and out["unrouted"] is True


def test_route_note_names_the_channels_that_ignore_the_base_url(gw, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    out = h.handle_client_mode({"operation": "route_folder", "folder_path": str(folder)})
    assert "WebFetch" in out["note"] and "telemetry" in out["note"]
    assert out["privacy"]["settings"] == ["skipWebFetchPreflight"]


def test_route_all_and_unroute_all_through_the_tool(gw, monkeypatch):
    monkeypatch.setattr(cc, "start_gateway", lambda *a, **k: {"running": True})
    out = h.handle_client_mode({"operation": "route_all"})
    assert out["success"] is True and out["scope"] == "every chat" and out["gateway_running"]
    assert "Remote Control" in out["note"]
    data = json.loads((gw.cfg / "settings.json").read_text("utf-8"))
    assert data["env"]["ANTHROPIC_BASE_URL"] == cc.gateway_url()
    assert h.handle_client_mode({"operation": "status"})["every_chat_routed"] is True
    out = h.handle_client_mode({"operation": "unroute_all"})
    assert out["success"] is True and out["unrouted"] is True


def test_unroute_all_is_refused_in_a_routed_session(routed, monkeypatch):
    monkeypatch.setattr(cc, "start_gateway", lambda *a, **k: {"running": True})
    h.handle_client_mode({"operation": "route_all"})
    out = h.handle_client_mode({"operation": "unroute_all"})
    assert out["error_type"] == "user_action_required"
    data = json.loads((routed.cfg / "settings.json").read_text("utf-8"))
    assert data["env"]["ANTHROPIC_BASE_URL"] == cc.gateway_url()
