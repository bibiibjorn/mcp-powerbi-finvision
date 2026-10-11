"""F-26: single-call long ops (screenshot_all, Word generate) report a start/finish bracket.

Per-page / per-visual progress for these needs the core loops (desktop_bridge, debug_operations),
which belong to other work packages; the handler-level bracket is what can be done here.
"""

from __future__ import annotations

from types import SimpleNamespace

from server.handlers import desktop_bridge_handler as bridge
from server.handlers import documentation_handler as docs


def test_screenshot_all_brackets_the_cli_call(monkeypatch):
    seen = []
    monkeypatch.setattr(bridge, "_progress", lambda done, total, msg: seen.append((done, total)))
    monkeypatch.setattr(
        bridge.desktop_bridge,
        "op_screenshot_all",
        lambda **k: seen.append("cli") or {"success": True, "screenshots": []},
    )
    out = bridge.handle_desktop_bridge({"operation": "screenshot_all", "output_dir": "C:/x"})
    assert out["success"] is True
    assert seen == [(0, 1), "cli", (1, 1)]


def test_word_generate_brackets_the_orchestrator_call(monkeypatch):
    seen = []
    monkeypatch.setattr(docs, "emit_progress", lambda done, total, msg: seen.append((done, total)))
    orch = SimpleNamespace(
        generate_word_documentation=lambda cs, out, include_hidden=True: seen.append("gen")
        or {"success": True, "path": "d.docx"}
    )
    state = SimpleNamespace(
        is_connected=lambda: True, agent_policy=SimpleNamespace(documentation_orch=orch)
    )
    monkeypatch.setattr(docs, "connection_state", state)
    out = docs.handle_documentation_word({"mode": "generate"})
    assert out["success"] is True
    assert seen == [(0, 1), "gen", (1, 1)]
