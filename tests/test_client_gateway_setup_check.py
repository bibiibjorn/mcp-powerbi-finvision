"""``python -m client_gateway setup --check`` and the new lines of ``setup --status``.

``--check`` is read-only and starts nothing: it makes one mask and one unmask round trip against a
RUNNING gateway with a fixed harmless test text and says PASS / DEGRADED / FAIL with the reason code
and the concrete fix (exit 0, 0, 1); a gateway that is not running is reported as such (exit 0).
The gateway is an in-process one (same pattern as ``test_client_gateway_authority.py``), the
control token is the one in its masking home. Synthetic data only.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from client_gateway import check_cmd, cli, control_client, setup_cmd
from client_gateway.log import GatewayLog
from client_gateway.server import Gateway
from client_gateway.setup_cmd import EXIT_OK, EXIT_REFUSED
from client_gateway.state import GatewayState
from tests._masking_fakes import pin_fail_mode
from tests.test_setup_cmd import Env

NAME = "Zorvex Holding"


@pytest.fixture()
def running(tmp_path, monkeypatch):
    """A gateway on a free port; the CLI is pointed at it and at its masking home."""
    state = GatewayState(home=tmp_path, default_mode="off", images_policy="pass")
    state.smap.add_values("CUS", [NAME], "test")
    gateway = Gateway(
        state,
        port=0,
        upstream="http://127.0.0.1:9",
        log=GatewayLog(None),
        client=httpx.Client(http2=False, timeout=httpx.Timeout(10.0, read=None)),
    )
    gateway.start()
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", str(gateway.port))
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path))
    from client_gateway import server as server_mod

    monkeypatch.setattr(server_mod, "degrade_wait_s", lambda: 0.05)
    monkeypatch.setattr(server_mod, "authority_wait_s", lambda: 0.05)
    yield state
    gateway.shutdown()


def _run(capsys) -> tuple[int, str]:
    code = check_cmd.run_check()
    return code, capsys.readouterr().out


def test_a_working_gateway_is_a_pass(running, capsys):
    code, out = _run(capsys)
    assert code == EXIT_OK
    assert "mask:          ok" in out and "unmask:        ok" in out
    assert "dictionary:    ready" in out
    assert out.strip().splitlines()[-1].startswith("VERDICT: PASS")
    assert NAME not in out


def test_a_dictionary_that_is_not_ready_is_degraded_with_the_reason_and_the_fix(running, capsys):
    running.set_ready(
        False, "dictionary_incomplete", {"total": 1, "not_ready": 1, "classes": ["query_failed"]}
    )
    code, out = _run(capsys)
    assert code == EXIT_OK  # degraded masking still works
    last = out.strip().splitlines()[-1]
    assert last.startswith("VERDICT: DEGRADED (dictionary_incomplete)")
    assert "update Power BI Desktop" in last
    assert "NOT READY (dictionary_incomplete; classes: query_failed)" in out
    assert "mask:          ok (degraded: dictionary_incomplete)" in out


def test_closed_mode_with_a_dictionary_that_is_not_ready_fails(running, capsys, monkeypatch):
    pin_fail_mode(monkeypatch, "closed")
    running.set_ready(False, "dictionary_empty")
    code, out = _run(capsys)
    assert code == 1
    last = out.strip().splitlines()[-1]
    assert last.startswith("VERDICT: FAIL (not_ready)") and "fail_mode" in last
    assert "mask:          FAILED (not_ready)" in out


def test_a_stale_control_token_fails_with_the_fix(running, capsys):
    (running.home / "gateway.token").write_text("x" * 43, encoding="ascii")
    code, out = _run(capsys)
    assert code == 1
    last = out.strip().splitlines()[-1]
    assert last.startswith("VERDICT: FAIL (token_required)")
    assert "rerun python -m client_gateway setup --yes" in last


def test_a_gateway_that_is_not_running_is_reported_and_nothing_is_started(
    free_tcp_port, monkeypatch, capsys
):
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", str(free_tcp_port))
    started: list[str] = []
    monkeypatch.setattr(control_client, "spawn_gateway", lambda: started.append("spawn"))
    monkeypatch.setattr(control_client, "start_gateway", lambda *a, **k: started.append("start"))
    code, out = _run(capsys)
    assert code == EXIT_OK and started == []
    assert "not running" in out and "nothing was started" in out
    assert out.strip().splitlines()[-1].startswith("VERDICT: NOT RUNNING")


# ------------------------------------------------------------------ a fake gateway
def _fake(replies: dict[str, Any]):
    """``call`` double: path -> reply (a tuple, None or a callable)."""
    seen: list[tuple[str, dict[str, Any] | None]] = []

    def call(port, method, path, body=None, headers=None, timeout=3.0):
        seen.append((path, body))
        reply = replies.get(path)
        return reply(body) if callable(reply) else reply

    call.seen = seen  # type: ignore[attr-defined]
    return call


HEALTH = (200, {"ok": True, "version": "9.9"})


def _lines(call, **kw) -> tuple[int, list[str]]:
    out: list[str] = []
    code = check_cmd.run_check(7, out.append, call, token="t" * 43)
    return code, out


def test_a_gateway_that_answers_health_but_not_the_mask_call_fails_as_unreachable():
    code, out = _lines(_fake({"/__gateway/health": HEALTH, "/__gateway/mask": None}))
    assert code == 1 and "VERDICT: FAIL (unreachable)" in out[-1]
    assert "gateway did not answer" in out[-1]


@pytest.mark.parametrize(
    "reply, code_word",
    [
        ((503, {"ok": False, "error": "mask_failed"}), "mask_failed"),
        ((500, {}), "bad_reply"),
        ((200, {"ok": True, "texts": []}), "bad_reply"),
    ],
)
def test_other_failed_mask_calls_name_their_reason(reply, code_word):
    code, out = _lines(_fake({"/__gateway/health": HEALTH, "/__gateway/mask": reply}))
    assert code == 1 and f"VERDICT: FAIL ({code_word})" in out[-1]


def test_a_failing_unmask_is_a_fail_too():
    fake = _fake(
        {
            "/__gateway/health": HEALTH,
            "/__gateway/mask": lambda body: (200, {"ok": True, "texts": body["texts"]}),
            "/__gateway/unmask": (503, {"ok": False, "error": "unmask_failed"}),
        }
    )
    code, out = _lines(fake)
    assert code == 1 and "  unmask:        FAILED (mask_failed)" in out
    assert [p for p, _ in fake.seen][-2:] == ["/__gateway/mask", "/__gateway/unmask"]


def test_the_test_text_is_fixed_and_harmless_and_the_output_holds_no_value():
    fake = _fake(
        {
            "/__gateway/health": HEALTH,
            "/__gateway/status": (200, {"ready": True, "dictionary": {"values": 12}}),
            "/__gateway/mask": lambda body: (200, {"ok": True, "texts": body["texts"]}),
            "/__gateway/unmask": lambda body: (200, {"ok": True, "texts": body["texts"]}),
        }
    )
    code, out = _lines(fake)
    assert code == 0 and out[-1].startswith("VERDICT: PASS")
    sent = [b for p, b in fake.seen if p in ("/__gateway/mask", "/__gateway/unmask")]
    assert all(b["texts"] == [check_cmd.TEST_TEXT] for b in sent)
    assert "12 values" in "\n".join(out)


def test_every_code_has_a_concrete_fix():
    for code in ("unreachable", "timeout", "token_required", "not_ready", "mask_failed",
                 "bad_reply", "dictionary_empty", "connect_failed", "query_failed",
                 "column_unreadable", "new_instance", "instance_listing_failed"):  # fmt: skip
        assert len(check_cmd._fix_for(code, [])) > 20
    assert "update Power BI Desktop" in check_cmd._fix_for("x", ["query_failed"])
    assert "detect_timeout_s" in check_cmd._fix_for("x", ["timeout"])


# ------------------------------------------------------------------ the command line
def test_the_command_is_wired_and_takes_no_other_option(monkeypatch, capsys):
    monkeypatch.setattr(check_cmd, "run_check", lambda *a, **k: 0)
    assert cli.main(["setup", "--check"]) == 0
    for extra in ("--yes", "--status", "--remove", "--register", "--desktop-apps"):
        assert cli.main(["setup", "--check", extra]) == EXIT_REFUSED
        assert "read-only" in capsys.readouterr().out


def test_the_check_module_changes_nothing_on_the_system():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(check_cmd.__file__).read_text(encoding="utf-8"))
    imported = {
        n.module if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    }
    assert not any(m and m.split(".")[0] in ("subprocess", "winreg", "ctypes") for m in imported)


# ------------------------------------------------------------------ setup --status
def test_status_shows_the_dictionary_and_the_fail_mode(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    gateway = {
        "version": "9.9",
        "ready": False,
        "not_ready": {"reason": "dictionary_incomplete", "classes": ["query_failed"]},
        "dictionary": {"values": 0},
    }
    assert env.run(status=True, gateway=gateway) == EXIT_OK
    assert "dictionary:    NOT READY (dictionary_incomplete; classes: query_failed), 0 values" in (
        env.text
    )
    assert "fail mode:     degrade" in env.text
    env.out.clear()
    env.run(status=True, gateway={"version": "9.9", "ready": True, "dictionary": {"values": 41}})
    assert "dictionary:    ready, 41 values" in env.text
    env.out.clear()
    env.run(status=True, gateway=None)
    assert "dictionary:    gateway not running" in env.text
    env.out.clear()
    env.run(status=True, gateway={"ok": True})  # a status that could not be read
    assert "dictionary:    unknown" in env.text


def test_status_names_a_closed_fail_mode(tmp_path, monkeypatch):
    pin_fail_mode(monkeypatch, "closed")
    env = Env(tmp_path, monkeypatch)
    env.run(status=True)
    assert "fail mode:     closed" in env.text


def test_status_is_still_read_only(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    before = env.tree()
    env.run(status=True, gateway={"ready": True})
    assert env.tree() == before and setup_cmd is not None
