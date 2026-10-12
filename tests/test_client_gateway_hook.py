"""Hook events session / prompt / pretool for ROUTED sessions (synthetic data only).

A fake gateway (loopback HTTP, the control contract /__gateway/health|status|session|mode) stands
in for the real one. The hook is also run the way Claude Code runs it: by file path, in a
subprocess, from another folder (the v1 prompt switch crashed on import that way).
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "client_masking_hook.py"
SID = "sess-0001"
TOKEN = "tok-secret-123"


def _load():
    spec = importlib.util.spec_from_file_location("client_masking_hook_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load()


class FakeGateway:
    """The gateway's control contract, recording what it was asked."""

    def __init__(self) -> None:
        self.modes: dict[str, str] = {}
        self.default = "on"
        self.stats: dict[str, int] = {}
        self.token = TOKEN
        self.calls: list[tuple[str, str, dict, str | None]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _send(self, code: int, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802
                url = urlparse(self.path)
                outer.calls.append(("GET", self.path, {}, None))
                if url.path == "/__gateway/health":
                    self._send(200, {"ok": True, "version": "t"})
                elif url.path == "/__gateway/status":
                    self._send(
                        200,
                        {
                            "ok": True,
                            "dictionary": {"values": 3, "by_prefix": {"CUS": 2, "ORG": 1}},
                            "stats": outer.stats,
                        },
                    )
                elif url.path == "/__gateway/session":
                    sid = parse_qs(url.query).get("id", [""])[0]
                    self._send(
                        200,
                        {"ok": True, "session": sid, "mode": outer.modes.get(sid, outer.default)},
                    )
                else:
                    self._send(404, {"ok": False})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                sent = self.headers.get("X-Gateway-Token")
                outer.calls.append(("POST", self.path, body, sent))
                if self.path == "/__gateway/refresh":
                    return self._send(200, {"ok": True})
                if self.path != "/__gateway/mode":
                    return self._send(404, {"ok": False})
                if body.get("mode") == "off" and sent != outer.token:
                    return self._send(403, {"ok": False, "error": "token"})
                outer.modes[body["session"]] = body["mode"]
                self._send(200, {"ok": True, "mode": body["mode"]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "FakeGateway":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def gw(tmp_path, monkeypatch):
    gateway = FakeGateway().start()
    home = tmp_path / "home"
    home.mkdir()
    (home / "gateway.token").write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(home))
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", str(gateway.port))
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)  # never the repo the tests run in
    claude = tmp_path / "claude-config"  # never the developer's own ~/.claude/settings.json
    claude.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    gateway.home = home
    yield gateway
    gateway.stop()


def _route(folder: Path, port: int) -> Path:
    (folder / ".claude").mkdir(parents=True, exist_ok=True)
    (folder / ".claude" / "settings.local.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}"}}), encoding="utf-8"
    )
    return folder


@pytest.fixture()
def proj(tmp_path, gw):
    return _route(tmp_path / "proj", gw.port)


def _run(monkeypatch, capsys, event: str, payload: dict):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code = hook.main(["hook", event])
    out = capsys.readouterr()
    return code, out.out, out.err


def _prompt(monkeypatch, capsys, proj: Path, text: str, sid: str = SID):
    code, out, err = _run(
        monkeypatch, capsys, "prompt", {"session_id": sid, "cwd": str(proj), "prompt": text}
    )
    return code, (json.loads(out) if out else None)


def _pre(monkeypatch, capsys, proj: Path, tool: str, tool_input: dict, sid: str = SID):
    return _run(
        monkeypatch,
        capsys,
        "pretool",
        {"session_id": sid, "cwd": str(proj), "tool_name": tool, "tool_input": tool_input},
    )


# ------------------------------------------------------------------------------ routed lookup
def test_routed_base_reads_the_project_settings_file(proj, gw):
    assert hook.routed_base(str(proj)) == f"http://127.0.0.1:{gw.port}"


def test_routed_base_finds_the_file_from_a_subfolder_up_to_the_repo_root(proj, gw):
    (proj / ".git").mkdir()
    sub = proj / "a" / "b"
    sub.mkdir(parents=True)
    assert hook.routed_base(str(sub)) == f"http://127.0.0.1:{gw.port}"
    # a routed folder ABOVE a repository root is not this repository's routing
    outer = proj.parent / "outer"
    inner = outer / "repo"
    (inner / ".git").mkdir(parents=True)
    _route(outer, gw.port)
    assert hook.routed_base(str(inner)) is None


def test_routed_base_ignores_other_ports_garbage_and_missing_files(tmp_path, gw):
    other = _route(tmp_path / "other", gw.port + 1)
    assert hook.routed_base(str(other)) is None
    bad = tmp_path / "bad"
    (bad / ".claude").mkdir(parents=True)
    (bad / ".claude" / "settings.local.json").write_text("{nope", encoding="utf-8")
    assert hook.routed_base(str(bad)) is None
    assert hook.routed_base(str(tmp_path / "missing")) is None


def test_routed_base_from_the_environment(tmp_path, gw, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{gw.port}/")
    assert hook.routed_base(str(tmp_path)) == f"http://127.0.0.1:{gw.port}"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    assert hook.routed_base(str(tmp_path)) is None


# ------------------------------------------------------------------------------ typed commands
@pytest.mark.parametrize(
    "text",
    [
        "client mode off",
        "Client Mode OFF",
        "/client mode off",
        "masking off",
        "Masking OFF.",
        "/masking off",
        "client mode off.",
        "  client mode off!  ",
        "client  mode off",
    ],
)
def test_client_mode_off_switches_the_session_with_the_token(monkeypatch, capsys, proj, gw, text):
    code, reply = _prompt(monkeypatch, capsys, proj, text)
    assert code == 0 and reply["decision"] == "block"
    assert reply["reason"].startswith("Client masking OFF in this folder")
    assert "stops by itself" in reply["reason"]
    assert gw.modes[SID] == "off"
    post = [c for c in gw.calls if c[0] == "POST"][0]
    assert post[2] == {"session": SID, "mode": "off"} and post[3] == TOKEN
    # masking on demand: the folder goes straight to Anthropic again (the key is written, never
    # deleted: a running session keeps a removed key)
    env = _settings_env(proj)
    assert env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"


def test_client_mode_on_switches_on_and_routes_the_folder(monkeypatch, capsys, tmp_path, gw):
    plain = tmp_path / "plain"  # not routed yet: masking on demand
    plain.mkdir()
    gw.modes[SID] = "off"
    code, reply = _prompt(monkeypatch, capsys, plain, "client mode on")
    assert reply["decision"] == "block" and reply["reason"].startswith("Client masking ON")
    assert gw.modes[SID] == "on"
    assert _settings_env(plain)["ANTHROPIC_BASE_URL"] == f"http://127.0.0.1:{gw.port}"


def test_client_mode_status_reports_mode_and_gateway(monkeypatch, capsys, proj, gw):
    _, reply = _prompt(monkeypatch, capsys, proj, "client mode status")
    assert reply["decision"] == "block"
    assert reply["reason"] == hook.SWITCH_STATUS.format(mode="ON", running="running")
    gw.modes[SID] = "off"
    _, reply = _prompt(monkeypatch, capsys, proj, "client mode status?")
    assert reply["reason"].startswith("Client masking is OFF for this chat.")


def test_masking_status_shows_what_went_less_well(monkeypatch, capsys, proj, gw):
    gw.stats.update(masked_requests=4, ner_partial=1, system_values_passed=0)
    _, reply = _prompt(monkeypatch, capsys, proj, "masking status")
    assert "Since the gateway started: 4 request(s) masked; 1 message(s) went on" in reply["reason"]
    assert "identity lines" not in reply["reason"]  # zero counters are left out


def test_off_without_a_token_file_still_sends_the_folder_direct(monkeypatch, capsys, proj, gw):
    # the typed command is the user's: without the token the gateway keeps its mode, but the
    # folder no longer sends anything through it
    (gw.home / "gateway.token").unlink()
    _, reply = _prompt(monkeypatch, capsys, proj, "client mode off")
    assert reply["reason"].startswith("Client masking OFF in this folder (0 chat(s)")
    assert _settings_env(proj)["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"


def test_off_with_a_wrong_token_leaves_the_gateway_mode_but_unroutes(monkeypatch, capsys, proj, gw):
    (gw.home / "gateway.token").write_text("stale", encoding="utf-8")
    _, reply = _prompt(monkeypatch, capsys, proj, "client mode off")
    assert reply["reason"].startswith("Client masking OFF in this folder (0 chat(s)")
    assert SID not in gw.modes
    assert _settings_env(proj)["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"


def test_gateway_down_blocks_with_the_manual_start_command(monkeypatch, capsys, tmp_path, gw):
    proj = _route(tmp_path / "down", gw.port)
    gw.stop()
    monkeypatch.setattr(hook, "_spawn_gateway", lambda: None)
    monkeypatch.setattr(hook, "START_WAIT_S", 0.2)
    _, reply = _prompt(monkeypatch, capsys, proj, "please check the report")
    assert reply == {"decision": "block", "reason": hook.NOT_STARTED.format(repo=hook._REPO_ROOT)}
    text = reply["reason"]
    assert "python -m client_gateway start" in text and "gateway_start" not in text
    # the typed off never needs the gateway: the folder goes direct again
    _, reply = _prompt(monkeypatch, capsys, proj, "masking off")
    assert reply["reason"].startswith("Client masking OFF in this folder")
    assert _settings_env(proj)["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert "could not" not in hook.NOT_RUNNING or "terminal" in hook.NOT_RUNNING


def test_every_routed_prompt_restarts_a_dead_gateway(monkeypatch, capsys, tmp_path, gw):
    proj = _route(tmp_path / "down", gw.port)
    gw.stop()
    started = []

    def spawn():
        started.append(1)
        revived = ThreadingHTTPServer(("127.0.0.1", gw.port), gw.server.RequestHandlerClass)
        threading.Thread(target=revived.serve_forever, daemon=True).start()
        gw.server = revived

    monkeypatch.setattr(hook, "_spawn_gateway", spawn)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{gw.port}")  # a switched process
    code, out, err = _run(
        monkeypatch,
        capsys,
        "prompt",
        {"session_id": SID, "cwd": str(proj), "prompt": "just a question"},
    )
    assert (code, out, err) == (0, "", "")  # the prompt goes on, to a gateway that is up again
    assert started == [1]


def test_slow_health_probe_does_not_spawn_a_duplicate_gateway(monkeypatch, gw):
    checks = iter((False, True))
    monkeypatch.setattr(hook, "_healthy", lambda base: next(checks))
    monkeypatch.setattr(hook, "_gateway_port", lambda: gw.port)
    monkeypatch.setattr(hook, "_spawn_gateway", lambda: pytest.fail("duplicate gateway spawned"))
    assert hook._ensure_gateway(f"http://127.0.0.1:{gw.port}", wait_s=1)


def test_an_ordinary_prompt_to_a_dead_gateway_that_will_not_start_is_blocked_clearly(
    monkeypatch, capsys, tmp_path, gw
):
    proj = _route(tmp_path / "down", gw.port)
    gw.stop()
    monkeypatch.setattr(hook, "_spawn_gateway", lambda: None)
    monkeypatch.setattr(hook, "START_WAIT_S", 0.2)
    _, reply = _prompt(monkeypatch, capsys, proj, "what is the weather")
    assert reply["decision"] == "block" and "python -m client_gateway start" in reply["reason"]


def test_other_prompts_pass_untouched_and_cost_only_a_health_probe(monkeypatch, capsys, proj, gw):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{gw.port}")  # a switched process
    for text in (
        "please client mode off now",
        "client mode",
        "what is CUS_48213?",
        "client mode offline",
    ):
        code, out, err = _run(
            monkeypatch, capsys, "prompt", {"session_id": SID, "cwd": str(proj), "prompt": text}
        )
        assert (code, out, err) == (0, "", "")
    assert {c[1] for c in gw.calls} == {"/__gateway/health"}  # never a mode change or a token
    assert not [c for c in gw.calls if c[0] == "POST"]


def test_command_without_a_session_id_is_blocked_not_sent(monkeypatch, capsys, proj, gw):
    code, out, _ = _run(
        monkeypatch, capsys, "prompt", {"cwd": str(proj), "prompt": "client mode off"}
    )
    assert json.loads(out)["decision"] == "block" and gw.modes == {}


def test_not_routed_sessions_pass_every_other_prompt_untouched(monkeypatch, capsys, tmp_path, gw):
    plain = tmp_path / "plain"
    plain.mkdir()
    code, out, err = _run(
        monkeypatch,
        capsys,
        "prompt",
        {"session_id": SID, "cwd": str(plain), "prompt": "please check the report"},
    )
    assert (code, out, err) == (0, "", "")
    assert gw.calls == []
    # and a typed off there changes nothing but is still answered locally
    code, out, err = _run(
        monkeypatch,
        capsys,
        "prompt",
        {"session_id": SID, "cwd": str(plain), "prompt": "client mode off"},
    )
    assert json.loads(out)["decision"] == "block"


# ------------------------------------------------------------------------------ pretool
def test_claude_ai_connectors_are_blocked_while_mode_is_on(monkeypatch, capsys, proj, gw):
    code, out, err = _pre(monkeypatch, capsys, proj, "mcp__claude_ai_Claude_Docs__read", {})
    assert code == 2 and out == ""
    assert "claude.ai connector" in err and "mcp__claude_ai_Claude_Docs__read" in err


def test_claude_ai_connectors_pass_when_the_session_mode_is_off(monkeypatch, capsys, proj, gw):
    gw.modes[SID] = "off"
    code, out, err = _pre(monkeypatch, capsys, proj, "mcp__claude_ai_Claude_Docs__read", {})
    assert (code, err) == (0, "")


def test_claude_ai_connectors_are_blocked_when_the_gateway_is_unreachable(
    monkeypatch, capsys, tmp_path, gw
):
    proj = _route(tmp_path / "down", gw.port)
    gw.stop()
    code, _, err = _pre(monkeypatch, capsys, proj, "mcp__claude_ai_Microsoft_Learn__search", {})
    assert code == 2 and "claude.ai connector" in err


def test_in_process_side_door_blocks_are_dropped_for_routed_sessions(monkeypatch, capsys, proj):
    # the rendered canvas and image reads come from open Desktop models / go through the gateway's
    # image redaction; Fabric data queries are NOT among these (see the uncovered-source tests)
    for tool, tool_input in (
        ("mcp__pbi-webview2__pbi_screenshot", {}),
        ("Read", {"file_path": str(proj / "shot.png")}),
        ("Bash", {"command": "ls exports"}),
    ):
        code, out, err = _pre(monkeypatch, capsys, proj, tool, tool_input)
        assert (code, err) == (0, ""), tool


@pytest.mark.parametrize(
    "command",
    [
        "python -m client_gateway mode off",
        "venv/Scripts/python.exe -m client_gateway mode --session abc off",
        "python -m client_gateway stop",
        "client_gateway stop",
        'taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq client_gateway"',
        "Stop-Process -Name client_gateway",
        'curl -X POST http://127.0.0.1:{port}/__gateway/mode -d \'{{"mode":"off"}}\'',
        "Invoke-RestMethod -Method Post -Uri http://localhost:{port}/__gateway/mode",
        "python -c \"import urllib.request as u; u.urlopen('http://127.0.0.1:{port}/__gateway/mode')\"",
        "curl http://127.0.0.1:{port}/v1/messages",
        "cat gateway.token",
        "type C:\\x\\home\\gateway.token",
        "cat ~/gateway.t*",
        "Get-Content $env:LOCALAPPDATA\\Some\\gateway.tok?n",
    ],
)
def test_commands_that_switch_off_or_stop_the_gateway_are_blocked(
    monkeypatch, capsys, proj, gw, command
):
    gw.modes[SID] = "off"  # also (especially) while the mode is off
    code, out, err = _pre(
        monkeypatch, capsys, proj, "Bash", {"command": command.format(port=gw.port)}
    )
    assert code == 2, command
    assert "Only the user can do that" in err or "Only the user" in err


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "python -m client_gateway status",
        "python -m client_gateway start",
        "python -m pytest tests/test_client_gateway_hook.py -k stop",
        "curl https://example.org/api",
        "git fetch origin",
        "echo client mode off",
        # mentions in code work are not actions (the guard used to fire on these)
        "wc -l hooks/client_masking_hook.py client_gateway/images.py",
        'grep -n "health\\|start\\|stop" hooks/client_masking_hook.py',
        "echo BASE=${ANTHROPIC_BASE_URL:-unset}",
        "python -m client_gateway --help",
        "grep -rn client_gateway src/ server/",
        'python -c "from client_gateway.images import redact_image_block"',
        "grep -rn FinvisionMasking core/ docs/",
        "python -c \"import json; print(json.load(open('.claude/settings.local.json'))['env'])\"",
        # a doc text naming the package and, far later, "stop" / "mode ... off" (newlines collapse)
        "cat > doc.md <<'EOF'\nFiles: `client_gateway/` (control client, folders, server); schema "
        "TOOL_SCHEMAS.\n| `gateway_stop` | Stops the gateway; routed chats fail until it runs again. |\n"
        "| `off` | refused: the user types `masking off` in the chat |\nEOF",
    ],
)
def test_ordinary_commands_pass_in_routed_sessions(monkeypatch, capsys, proj, command):
    code, out, err = _pre(monkeypatch, capsys, proj, "Bash", {"command": command})
    assert (code, err) == (0, ""), command


def test_the_token_file_is_blocked_for_every_file_tool(monkeypatch, capsys, proj, gw):
    token = str(gw.home / "gateway.token")
    for tool, tool_input in (
        ("Read", {"file_path": token}),
        ("Edit", {"file_path": token, "old_string": "a", "new_string": "b"}),
        ("Write", {"file_path": token, "content": "x"}),
        ("Grep", {"pattern": "tok", "path": token}),
        ("Glob", {"pattern": "gateway.t*", "path": str(gw.home)}),
        ("Grep", {"pattern": ".", "path": str(gw.home)}),
        ("Glob", {"pattern": "*", "path": str(gw.home)}),
    ):
        code, _, err = _pre(monkeypatch, capsys, proj, tool, tool_input)
        assert code == 2, (tool, tool_input)


def test_other_files_in_the_home_and_elsewhere_stay_readable(monkeypatch, capsys, proj, gw):
    for tool, tool_input in (
        ("Read", {"file_path": str(gw.home / "gateway.log")}),
        ("Read", {"file_path": str(proj / "notes.md")}),
        ("Grep", {"pattern": "x", "path": str(proj)}),
    ):
        code, _, err = _pre(monkeypatch, capsys, proj, tool, tool_input)
        assert (code, err) == (0, ""), tool


# ------------------------------------------------------------------------------ session start
def test_session_start_on_gives_the_model_context_and_the_user_message(
    monkeypatch, capsys, proj, gw
):
    code, out, _ = _run(monkeypatch, capsys, "session", {"session_id": SID, "cwd": str(proj)})
    data = json.loads(out)
    assert code == 0
    assert data["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert "Client masking is ON in this session" in ctx
    assert "CUS_48213" in ctx and "PCC_20311" in ctx and "ORG_10442" in ctx
    assert "never try to switch masking off" in ctx
    assert data["systemMessage"] == (
        "Client masking ON for this session (type: masking off for dev data)."
    )


def test_session_start_off_tells_only_the_user(monkeypatch, capsys, proj, gw):
    gw.modes[SID] = "off"
    _, out, _ = _run(monkeypatch, capsys, "session", {"session_id": SID, "cwd": str(proj)})
    data = json.loads(out)
    assert "hookSpecificOutput" not in data
    assert data["systemMessage"].startswith("Client masking off")


def test_session_start_outside_routed_folders_is_silent(monkeypatch, capsys, tmp_path, gw):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _run(monkeypatch, capsys, "session", {"session_id": SID, "cwd": str(plain)}) == (
        0,
        "",
        "",
    )
    assert gw.calls == []


def test_session_start_starts_a_stopped_gateway(monkeypatch, capsys, tmp_path, gw):
    proj = _route(tmp_path / "p", gw.port)
    gw.stop()
    started = []

    def spawn():
        started.append(1)
        revived = FakeGateway()
        revived.server = ThreadingHTTPServer(("127.0.0.1", gw.port), gw.server.RequestHandlerClass)
        threading.Thread(target=revived.server.serve_forever, daemon=True).start()
        gw.server = revived.server

    monkeypatch.setattr(hook, "_spawn_gateway", spawn)
    _, out, _ = _run(monkeypatch, capsys, "session", {"session_id": SID, "cwd": str(proj)})
    assert started == [1]
    assert "additionalContext" in json.loads(out)["hookSpecificOutput"]


def test_session_start_reports_a_gateway_that_does_not_start(monkeypatch, capsys, tmp_path, gw):
    proj = _route(tmp_path / "p", gw.port)
    gw.stop()
    monkeypatch.setattr(hook, "_spawn_gateway", lambda: None)
    monkeypatch.setattr(hook, "START_WAIT_S", 0.3)
    _, out, _ = _run(monkeypatch, capsys, "session", {"session_id": SID, "cwd": str(proj)})
    msg = json.loads(out)["systemMessage"]
    assert msg == hook.NOT_STARTED.format(repo=hook._REPO_ROOT)
    assert msg.startswith("Client gateway did not start: requests in this folder will fail.")
    assert "python -m client_gateway start" in msg


def test_session_start_survives_a_spawn_that_raises(monkeypatch, capsys, tmp_path, gw):
    proj = _route(tmp_path / "p", gw.port)
    gw.stop()

    def boom():
        raise OSError("no exe")

    monkeypatch.setattr(hook, "_spawn_gateway", boom)
    _, out, _ = _run(monkeypatch, capsys, "session", {"session_id": SID, "cwd": str(proj)})
    assert json.loads(out) == {"systemMessage": hook.NOT_STARTED.format(repo=hook._REPO_ROOT)}


# ------------------------------------------------------------------------------ by path
def _by_path(event: str, payload: dict, cwd: Path, env_extra: dict):
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.pop("ANTHROPIC_BASE_URL", None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HOOK), event],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=60,
        env=env,
        check=False,
    )


def test_typed_switch_works_when_run_by_path_from_another_folder(tmp_path, gw, proj):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {"FINVISION_MASKING_HOME": str(gw.home), "FINVISION_GATEWAY_PORT": str(gw.port)}
    proc = _by_path(
        "pretool",
        {
            "session_id": SID,
            "cwd": str(proj),
            "tool_name": "Bash",
            "tool_input": {"command": "python -m client_gateway stop"},
        },
        elsewhere,
        env,
    )
    assert proc.returncode == 2 and "Only the user" in proc.stderr  # a routed chat: guarded
    proc = _by_path(
        "prompt", {"session_id": SID, "cwd": str(proj), "prompt": "client mode off"}, elsewhere, env
    )
    assert proc.returncode == 0 and proc.stderr == ""
    assert json.loads(proc.stdout)["reason"].startswith("Client masking OFF in this folder")
    assert gw.modes[SID] == "off"
    proc = _by_path("session", {"session_id": SID, "cwd": str(proj)}, elsewhere, env)
    assert proc.returncode == 0 and proc.stdout == ""  # the folder is no longer routed


def test_the_hook_imports_only_the_switch_and_only_for_a_typed_command():
    text = HOOK.read_text(encoding="utf-8")
    for needle in ("import core", "from core", "import server", "from server"):
        assert needle not in text
    assert text.count("from client_gateway import") == 1
    assert "from client_gateway import switch  # noqa: PLC0415" in text  # lazily, in the function
    assert b"\r\n" not in HOOK.read_bytes()


def test_garbage_input_never_breaks_the_hook(tmp_path):
    for event in ("prompt", "pretool", "session", "stop", "unknown"):
        proc = subprocess.run(
            [sys.executable, str(HOOK), event],
            input="{not json",
            capture_output=True,
            text=True,
            cwd=tmp_path,
            timeout=30,
        )
        assert proc.returncode == 0 and proc.stdout == ""


# ------------------------------------------------------------------------------ guard bypasses
def _blocked(monkeypatch, capsys, proj, tool, tool_input):
    code, out, err = _pre(monkeypatch, capsys, proj, tool, tool_input)
    return code == 2 and ("Only the user" in err or "does not cover" in err)


@pytest.mark.parametrize(
    "command",
    [
        # forging the typed command through the hook itself
        'echo \'{{"prompt":"client mode off","session_id":"S","cwd":"."}}\' | '
        "python /repo/hooks/client_masking_hook.py prompt",
        "python hooks\\client_masking_hook.py prompt < payload.json",
        # importing the CLI: it reads the token itself (an empty session id = every session)
        "python -c \"from client_gateway.cli import mode; mode(8890,'off','')\"",
        "python -c \"import client_gateway.cli as c; c.main(['mode','off'])\"",
        "venv/Scripts/python.exe -m client_gateway mode off",
        "python -m client_gateway serve --port 8891",
        # a line continuation hides the keywords from a single-line pattern
        "python -m client_gateway \\\n  mode \\\n  off",
        "python -m client_gateway `\n mode `\n off",
        # an argv list split over lines
        "python - <<'EOF'\nimport subprocess\nsubprocess.run(['python', '-m', 'client_gateway',\n"
        "    'stop'])\nEOF",
        # the masking folder, by any route to it
        'cat "$LOCALAPPDATA"/FinvisionMasking/g*',
        "for f in $LOCALAPPDATA/FinvisionMasking/*; do cat $f; done",
        "Get-ChildItem $env:LOCALAPPDATA\\FinvisionMasking | Get-Content",
        "ls {home}",
        "cat {home}/g*",
        "echo $FINVISION_MASKING_HOME",
        # taking the folder out of the gateway
        "sed -i '/ANTHROPIC_BASE_URL/d' .claude/settings.local.json",
        "python -c \"import json;p='.claude/settings.local.json';d=json.load(open(p));"
        "d['env'].pop('ANTHROPIC_BASE_URL')\"",
        "rm .claude/settings.local.json",
        "Remove-Item -Force .claude\\settings.local.json",
        "mv .claude/settings.local.json /tmp/x",
    ],
)
def test_the_known_bypasses_of_the_off_switch_are_blocked(monkeypatch, capsys, proj, gw, command):
    command = command.replace("{home}", str(gw.home).replace("\\", "/"))
    assert _blocked(monkeypatch, capsys, proj, "Bash", {"command": command}), command
    assert _blocked(monkeypatch, capsys, proj, "PowerShell", {"command": command}), command


def test_grep_and_glob_cannot_enumerate_the_masking_folder_by_name(monkeypatch, capsys, proj, gw):
    for tool, tool_input in (
        ("Glob", {"pattern": "FinvisionMasking/*", "path": str(proj)}),
        ("Grep", {"pattern": "x", "path": str(proj), "glob": "**/FinvisionMasking/*"}),
        ("Glob", {"pattern": "**/finvisionmasking/g*"}),
    ):
        assert _blocked(monkeypatch, capsys, proj, tool, tool_input), (tool, tool_input)


def test_gateway_stop_and_unroute_are_refused_for_the_ai_but_status_and_on_are_not(
    monkeypatch, capsys, proj, gw
):
    for op in ("gateway_stop", "unroute_folder", "unroute_all", "GATEWAY_STOP"):
        for mode in ("on", "off"):
            gw.modes[SID] = mode
            assert _blocked(
                monkeypatch,
                capsys,
                proj,
                "mcp__powerbi-finvision__00_Client_Mode",
                {"operation": op, "folder_path": str(proj)},
            ), (op, mode)
    for op in ("status", "on", "gateway_status", "gateway_start", "refresh", "route_folder"):
        code, _, err = _pre(
            monkeypatch, capsys, proj, "mcp__powerbi-finvision__00_Client_Mode", {"operation": op}
        )
        assert (code, err) == (0, ""), op


def test_the_routing_key_cannot_be_edited_away_with_the_file_tools(monkeypatch, capsys, proj, gw):
    settings = str(proj / ".claude" / "settings.local.json")
    for tool, tool_input in (
        (
            "Edit",
            {"file_path": settings, "old_string": '"ANTHROPIC_BASE_URL": "x"', "new_string": ""},
        ),
        (
            "MultiEdit",
            {
                "file_path": settings,
                "edits": [{"old_string": "ANTHROPIC_BASE_URL", "new_string": "OTHER"}],
            },
        ),
        ("Write", {"file_path": settings, "content": "{}"}),  # drops the key that is there now
        (
            "Write",
            {
                "file_path": str(proj / ".claude" / "settings.json"),
                "content": '{"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}}',
            },
        ),
    ):
        assert _blocked(monkeypatch, capsys, proj, tool, tool_input), (tool, tool_input)
    for tool, tool_input in (
        ("Edit", {"file_path": settings, "old_string": "allow", "new_string": "deny"}),
        ("Write", {"file_path": str(proj / "notes.md"), "content": "ANTHROPIC_BASE_URL in docs"}),
        ("Write", {"file_path": str(proj / ".claude" / "settings.json"), "content": "{}"}),
    ):
        code, _, err = _pre(monkeypatch, capsys, proj, tool, tool_input)
        assert (code, err) == (0, ""), (tool, tool_input)


def test_writing_settings_that_hold_no_routing_key_is_allowed(monkeypatch, capsys, tmp_path, gw):
    other = tmp_path / "other"
    (other / ".claude").mkdir(parents=True)
    _route(tmp_path / "proj", gw.port)
    code, _, err = _pre(
        monkeypatch,
        capsys,
        tmp_path / "proj",
        "Write",
        {
            "file_path": str(other / ".claude" / "settings.local.json"),
            "content": '{"permissions": {}}',
        },
    )
    assert (code, err) == (0, "")


# ------------------------------------------------------------------------------ uncovered sources
def test_fabric_data_queries_and_xmla_are_blocked_while_masking_is_on(
    monkeypatch, capsys, proj, gw
):
    calls = (
        ("mcp__ms-fabric-mcp__06_run_query", {}),
        ("mcp__ms-fabric-mcp__08_query_semantic_model", {"dataset": "x"}),
        ("mcp__powerbi-finvision__01_Connection", {"operation": "connect_xmla"}),
    )
    for tool, tool_input in calls:
        code, out, err = _pre(monkeypatch, capsys, proj, tool, tool_input)
        assert code == 2 and "does not cover" in err and "masking off" in err, tool
    # other operations of the same tools are fine, and so is everything once the user said off
    for tool, tool_input in (
        ("mcp__powerbi-finvision__01_Connection", {"operation": "ensure"}),
        ("mcp__powerbi-finvision__04_Run_DAX", {"query": "EVALUATE ROW(1,1)"}),
        ("mcp__ms-fabric-mcp__01_list_workspaces", {}),
    ):
        assert _pre(monkeypatch, capsys, proj, tool, tool_input)[0] == 0, tool
    gw.modes[SID] = "off"
    for tool, tool_input in calls:
        assert _pre(monkeypatch, capsys, proj, tool, tool_input)[0] == 0, tool


# ------------------------------------------------------------------------------ unrouted registry
def test_session_start_warns_when_a_registered_folder_is_no_longer_routed(
    monkeypatch, capsys, tmp_path, gw
):
    folder = tmp_path / "was-routed"
    folder.mkdir()
    (gw.home / "gateway_folders.json").write_text(
        json.dumps({"version": 1, "folders": {"k": {"path": str(folder)}}}), encoding="utf-8"
    )
    sub = folder / "sub"
    sub.mkdir()
    for cwd in (folder, sub):
        _, out, _ = _run(monkeypatch, capsys, "session", {"session_id": SID, "cwd": str(cwd)})
        assert json.loads(out) == {"systemMessage": hook.UNROUTED_REGISTERED}
    assert "NOT masked" in hook.UNROUTED_REGISTERED
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _run(monkeypatch, capsys, "session", {"session_id": SID, "cwd": str(elsewhere)}) == (
        0,
        "",
        "",
    )


def test_session_start_is_silent_for_a_registered_folder_that_is_still_routed(
    monkeypatch, capsys, proj, gw
):
    (gw.home / "gateway_folders.json").write_text(
        json.dumps({"version": 1, "folders": {"k": {"path": str(proj)}}}), encoding="utf-8"
    )
    _, out, _ = _run(monkeypatch, capsys, "session", {"session_id": SID, "cwd": str(proj)})
    assert "UNROUTED" not in out and "NOT masked" not in out


@pytest.mark.parametrize("tool", ["Artifact", "ArtifactComments", "ArtifactData"])
def test_artifacts_are_blocked_in_a_masked_chat_and_pass_otherwise(
    monkeypatch, capsys, proj, gw, tool
):
    """An artifact is published on Anthropic's side, outside the gateway, with the REAL values
    the gateway restored into the tool input: never in a masked chat."""
    code, _, err = _pre(monkeypatch, capsys, proj, tool, {"content": "x"})
    assert code == 2 and tool in err
    gw.modes[SID] = "off"
    code, _, err = _pre(monkeypatch, capsys, proj, tool, {"content": "x"})
    assert (code, err) == (0, "")


# ------------------------------------------------------------------------------ settings-file rule
@pytest.mark.parametrize(
    "command",
    [
        # the false positive: the index only, the working file (and its key) stays
        "git rm --cached .claude/settings.local.json",
        "git rm -r --cached .claude/settings.local.json && git status",
        "cd /repo && git rm --cached .claude/settings.local.json",
        # a tracked settings file that holds no routing key, rewritten or removed
        "python - <<'EOF'\nimport json\np='.claude/settings.json'\nd=json.load(open(p))\n"
        "d.pop('defaultMode', None)\nd['permissions']['allow'].remove('Read(C:/Users/x/**)')\n"
        "json.dump(d, open(p, 'w'))\nEOF",
        "sed -i '/defaultMode/d' .claude/settings.json",
        "rm .claude/settings.json",
        "mv .claude/settings.json .claude/settings.bak",
        # removing a file that is not there is not an unroute
        "rm other/.claude/settings.local.json",
    ],
)
def test_settings_commands_that_do_not_touch_the_routing_key_pass(
    monkeypatch, capsys, proj, gw, command
):
    (proj / ".claude" / "settings.json").write_text(
        '{"defaultMode": "plan", "permissions": {"allow": ["Read(C:/Users/x/**)"]}}',
        encoding="utf-8",
    )
    code, _, err = _pre(monkeypatch, capsys, proj, "Bash", {"command": command})
    assert (code, err) == (0, ""), command


@pytest.mark.parametrize(
    "command",
    [
        # a real unroute: the file that carries the key is deleted, moved or emptied
        "rm .claude/settings.local.json",
        "rm -f ./.claude/settings.local.json",
        "git rm .claude/settings.local.json",
        "git mv .claude/settings.local.json elsewhere.json",
        "mv .claude/settings.local.json /tmp/x",
        "Remove-Item -Force .claude\\settings.local.json",
        'del "{proj}\\.claude\\settings.local.json"',
        "rm '{proj}/.claude/settings.local.json'",
        # anything ambiguous about which file it is
        "rm $PROJECT/.claude/settings.local.json",
        "rm .claude/settings*.json",
        "cd /somewhere && rm .claude/settings.local.json",
        # the key itself removed
        "sed -i '/ANTHROPIC_BASE_URL/d' .claude/settings.local.json",
    ],
)
def test_a_real_unroute_through_the_shell_still_blocks(monkeypatch, capsys, proj, gw, command):
    command = command.replace("{proj}", str(proj))
    assert _blocked(monkeypatch, capsys, proj, "Bash", {"command": command}), command
    assert _blocked(monkeypatch, capsys, proj, "PowerShell", {"command": command}), command


def test_a_settings_file_that_holds_the_routing_key_is_protected_wherever_it_is(
    monkeypatch, capsys, tmp_path, gw
):
    """The user-level file of a route-all setup is not in the session's folder: an absolute path to
    it is read and recognised, so removing it blocks (and a different file does not)."""
    proj = _route(tmp_path / "proj", gw.port)
    user = tmp_path / "userhome" / ".claude"
    user.mkdir(parents=True)
    (user / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{gw.port}"}}),
        encoding="utf-8",
    )
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "settings.json").write_text("{}", encoding="utf-8")
    keyed = str(user / "settings.json").replace(chr(92), "/")
    other = str(plain / "settings.json").replace(chr(92), "/")
    assert _blocked(monkeypatch, capsys, proj, "Bash", {"command": f"rm {keyed}"})
    code, _, err = _pre(monkeypatch, capsys, proj, "Bash", {"command": f"rm {other}"})
    assert (code, err) == (0, "")


def _settings_env(folder: Path) -> dict:
    data = json.loads((folder / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    return data.get("env") or {}


# ------------------------------------------------------------------------------ pending switch
def test_a_masked_chat_whose_process_has_not_switched_yet_holds_the_message(
    monkeypatch, capsys, proj, gw
):
    # 2026-10-08: Claude Code re-reads its settings seconds after "masking on"; a message sent
    # before that would go straight to Anthropic, unmasked
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    gw.modes[SID] = "on"
    _, reply = _prompt(monkeypatch, capsys, proj, "check the report")
    assert reply == {"decision": "block", "reason": hook.MASKING_PENDING}
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{gw.port}")  # it switched
    code, out, err = _run(
        monkeypatch,
        capsys,
        "prompt",
        {"session_id": SID, "cwd": str(proj), "prompt": "check the report"},
    )
    assert (code, out, err) == (0, "", "")


def test_an_unmasked_chat_in_a_routed_folder_is_never_held(monkeypatch, capsys, proj, gw):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    gw.modes[SID] = "off"
    code, out, err = _run(
        monkeypatch,
        capsys,
        "prompt",
        {"session_id": SID, "cwd": str(proj), "prompt": "check the report"},
    )
    assert (code, out, err) == (0, "", "")


def test_masking_on_waits_for_claude_code_to_reread_its_settings(monkeypatch, capsys, tmp_path, gw):
    plain = tmp_path / "plain"
    plain.mkdir()
    slept = []
    monkeypatch.setattr(hook.time, "sleep", lambda s: slept.append(s))
    _, reply = _prompt(monkeypatch, capsys, plain, "masking on")
    assert reply["reason"].startswith("Client masking ON") and slept == [hook.SETTLE_S]


def test_masking_off_names_an_old_every_chat_route(monkeypatch, capsys, proj, gw):
    from client_gateway import folders

    folders.route_all()  # the user settings of an older setup (CLAUDE_CONFIG_DIR is a temp dir)
    _, reply = _prompt(monkeypatch, capsys, proj, "masking off")
    assert reply["reason"].startswith("Client masking OFF in this folder")
    assert hook.SWITCH_OFF_EVERY_CHAT in reply["reason"]
