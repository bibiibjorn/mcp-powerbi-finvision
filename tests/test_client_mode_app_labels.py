"""``00_Client_Mode status`` lists the app labels the front end observed (phase 3b.0, item 4), so a
lab run can fill ``adapters.appinfo.TESTED_APPS``. Labels and counts only. Synthetic data."""

from __future__ import annotations

from server.handlers import client_mode_handler as h


def _gateway(monkeypatch, frontend_apps):
    monkeypatch.setattr(h.gw, "health", lambda *a, **k: {"version": "t"})
    monkeypatch.setattr(
        h.gw, "status", lambda *a, **k: {"default_mode": "off", "frontend_apps": frontend_apps}
    )
    return h.handle_client_mode({"operation": "status"})


def test_status_lists_the_observed_labels_per_adapter(monkeypatch):
    out = _gateway(
        monkeypatch,
        {
            "copilot": {"seen": {"vscode/1": 4, "copilot-chat/0.36": 4}, "untested": ["vscode/1"]},
            "chatgpt_web": {"seen": {"chrome/141": 2}, "untested": []},
        },
    )
    assert out["apps"] == {
        "copilot": {"seen": {"vscode/1": 4, "copilot-chat/0.36": 4}, "untested": ["vscode/1"]},
        "chatgpt_web": {"seen": {"chrome/141": 2}, "untested": []},
    }


def test_status_without_observed_apps_has_no_apps_key(monkeypatch):
    assert "apps" not in _gateway(monkeypatch, {})
    assert "apps" not in _gateway(monkeypatch, None)


def test_only_labels_and_counts_are_passed_on(monkeypatch):
    out = _gateway(
        monkeypatch,
        {"copilot": {"seen": {"vscode/1": 1, "bad": "secret"}, "untested": [], "header": "x"}},
    )
    assert out["apps"] == {"copilot": {"seen": {"vscode/1": 1}, "untested": []}}


def test_the_gateway_state_reports_what_the_front_end_noted(tmp_path):
    from tests.test_frontend_addon import make_state

    state = make_state(tmp_path)
    state.frontend_note_app("copilot", ("vscode/1", "copilot-chat/0.36"))
    state.frontend_note_app("chatgpt_web", ("chrome/141",))
    apps = state.status()["frontend_apps"]
    assert apps["copilot"]["seen"] == {"copilot-chat/0.36": 1, "vscode/1": 1}
    assert apps["chatgpt_web"]["seen"] == {"chrome/141": 1}
