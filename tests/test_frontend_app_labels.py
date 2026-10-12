"""App labels of the front end (phase 3a.2, Task D, rule R9): the app and its major version are
recorded per chat space; a version the wire fixtures were not recorded with shows in ``status`` as
``untested_app_version`` while masking keeps running. Labels only: no header value is stored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mitmproxy import http
from mitmproxy.test import tflow

from client_gateway.adapters import appinfo
from client_gateway.adapters.appinfo import app_labels, browser_label, is_tested, label_of
from client_gateway.frontend.addon import FrontendAddon
from tests._wire_support import WIRE
from tests.test_frontend_copilot_identity import chat_body, make_state, tool_result
from tests.test_frontend_r2 import CHAT_URL

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)
EDGE_UA = CHROME_UA + " Edg/141.0.3537.57"
VSCODE_UA = "GitHubCopilotChat/0.32.4"


def test_a_product_becomes_an_app_and_major_version_label():
    assert label_of("vscode/1.105.0") == "vscode/1"
    assert label_of("copilot-chat/0.32.4") == "copilot-chat/0.32"  # a 0.x line keeps its minor
    assert label_of("copilot-chat/0") == "copilot-chat/0.0"
    assert label_of("GitHubCopilotChat/12.1") == "githubcopilotchat/12"
    for bad in ("", "vscode", "/1.0", "1.0/x", "vscode 1.105", None):
        assert label_of(bad) is None  # type: ignore[arg-type]


def test_a_browser_user_agent_gives_the_browser_and_its_major_version():
    assert browser_label(CHROME_UA) == "chrome/141"
    assert browser_label(EDGE_UA) == "edge/141"
    assert browser_label("Mozilla/5.0 (X11) Gecko/20100101 Firefox/133.0") == "firefox/133"
    assert browser_label("curl/8.4.0") is None and browser_label("") is None


def test_labels_per_chat_space():
    copilot = {"editor-plugin-version": "copilot-chat/0.32.4", "editor-version": "vscode/1.105.1"}
    assert app_labels("copilot", copilot) == ("copilot-chat/0.32", "vscode/1")
    assert app_labels("copilot", {"user-agent": VSCODE_UA}) == ("githubcopilotchat/0.32",)
    assert app_labels("copilot", {}) == ()
    assert app_labels("chatgpt_web", {"user-agent": CHROME_UA}) == ("chrome/141",)
    assert app_labels("chatgpt_web", {"editor-version": "vscode/1.1"}) == ()
    same = {"editor-plugin-version": "vscode/1.2", "editor-version": "vscode/1.3"}
    assert app_labels("copilot", same) == ("vscode/1",)  # no duplicate labels


def test_the_tested_table_is_pinned_to_the_fixtures():
    """A fixture that records the label of the app it came from must be in ``TESTED_APPS``: the
    table IS the fixture set. (The captures of 2026-10-07 recorded no version header, so the table
    starts empty and every version is reported until a re-capture fills it in.)"""
    recorded: dict[str, set[str]] = {}
    for path in sorted(WIRE.glob("*.json")):
        request = json.loads(path.read_text(encoding="utf-8"))["request"]
        for label in request.get("client", []):
            host = request["host"]
            space = "copilot" if host.endswith("githubcopilot.com") else "chatgpt_web"
            recorded.setdefault(space, set()).add(label)
    for space, labels in recorded.items():
        assert labels <= appinfo.TESTED_APPS[space], (space, labels)
    for space, labels in appinfo.TESTED_APPS.items():
        assert all(is_tested(space, label) for label in labels)
    assert not is_tested("copilot", "vscode/999") and not is_tested("nope", "x/1")


@pytest.fixture()
def state(tmp_path: Path):
    return make_state(tmp_path)


def flow_with(headers: dict[str, str], body: bytes) -> Any:
    req = http.Request.make("POST", CHAT_URL, b"", {"content-type": "application/json", **headers})
    req.raw_content = body
    req.headers["content-length"] = str(len(body))
    return tflow.tflow(req=req)


def test_the_front_end_records_labels_and_reports_an_untested_version(state):
    addon = FrontendAddon(state)
    headers = {"editor-plugin-version": "copilot-chat/0.32.4", "editor-version": "vscode/1.105.1"}
    addon.process_request(flow_with(headers, chat_body("hello")))
    addon.process_request(flow_with(headers, chat_body("hello again")))
    apps = state.status()["frontend_apps"]["copilot"]
    assert apps["seen"] == {"copilot-chat/0.32": 2, "vscode/1": 2}
    assert apps["untested"] == ["copilot-chat/0.32", "vscode/1"]
    assert "untested_app_version:copilot" in state.status()["warnings"]


def test_a_tested_version_reports_nothing(state, monkeypatch):
    monkeypatch.setitem(
        appinfo.TESTED_APPS, "copilot", frozenset({"copilot-chat/0.32", "vscode/1"})
    )
    addon = FrontendAddon(state)
    headers = {"editor-plugin-version": "copilot-chat/0.32.9", "editor-version": "vscode/1.106.0"}
    addon.process_request(flow_with(headers, chat_body("hello")))
    assert state.status()["frontend_apps"]["copilot"]["untested"] == []
    assert not any(w.startswith("untested_app_version") for w in state.status()["warnings"])


def test_an_untested_version_keeps_masking_running_and_no_header_value_is_kept(state):
    addon = FrontendAddon(state)
    secret_ua = "Mozilla/5.0 AccountSpecificUserAgentValue Chrome/150.0.0.0"
    flow = flow_with({"user-agent": secret_ua}, chat_body("a chat", tail=[tool_result()]))
    addon.process_request(flow)
    assert flow.metadata["fv_frontend"].mode == "on"  # masked although the version is unknown
    assert state.stats()["frontend_masked"] == 1
    dumped = json.dumps(state.status())
    assert "AccountSpecificUserAgentValue" not in dumped and secret_ua not in dumped


def test_the_label_table_is_bounded(state):
    for i in range(100):
        state.frontend_note_app("copilot", (f"app{i}/1",))
    assert len(state.frontend_apps()["copilot"]["seen"]) <= 32
