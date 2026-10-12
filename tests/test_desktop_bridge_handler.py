"""Dispatch-shell tests for 17_Desktop_Bridge. Mocks the desktop_bridge core
ops at the module boundary; never runs the real CLI. Verifies op routing,
required-arg validation, and that the handler is ungated."""

from server.handlers import desktop_bridge_handler as h


def test_unknown_op_returns_error():
    out = h.handle_desktop_bridge({"operation": "nope"})
    assert out["success"] is False
    assert out["error_type"] == "invalid_operation"


def test_status_routes_to_core(monkeypatch):
    monkeypatch.setattr(
        h.desktop_bridge, "op_status", lambda **k: {"success": True, "instances": []}
    )
    out = h.handle_desktop_bridge({"operation": "status"})
    assert out["success"] is True


def test_screenshot_requires_page_and_output():
    out = h.handle_desktop_bridge({"operation": "screenshot"})
    assert out["success"] is False
    assert out["error_type"] == "missing_argument"


def test_reload_passes_pid(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge,
        "op_reload",
        lambda **k: seen.update(k) or {"success": True, "pid": k.get("pid")},
    )
    out = h.handle_desktop_bridge({"operation": "reload", "pid": 7})
    assert out["success"] is True
    assert seen["pid"] == 7


def test_handler_is_ungated(monkeypatch):
    # Calling reload without any enter_mode / gating must not be blocked.
    monkeypatch.setattr(h.desktop_bridge, "op_reload", lambda **k: {"success": True})
    out = h.handle_desktop_bridge({"operation": "reload", "pid": 1})
    assert "autonomous_mode_required" not in str(out)


def test_screenshot_all_requires_output_dir():
    out = h.handle_desktop_bridge({"operation": "screenshot_all"})
    assert out["success"] is False
    assert out["error_type"] == "missing_argument"


def test_save_routes_to_core(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge,
        "op_save",
        lambda **k: seen.update(k) or {"success": True, "pid": k.get("pid")},
    )
    out = h.handle_desktop_bridge({"operation": "save", "pid": 42})
    assert out["success"] is True
    assert seen["pid"] == 42


def test_save_passes_save_timeout(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge, "op_save", lambda **k: seen.update(k) or {"success": True}
    )
    h.handle_desktop_bridge({"operation": "save", "pid": 1, "save_timeout": 12})
    assert seen["save_timeout"] == 12.0


def test_save_is_a_valid_op():
    assert "save" in h._VALID_OPS


def test_reload_and_screenshot_is_valid_op_and_routes(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge,
        "op_reload_and_screenshot",
        lambda **k: seen.update(k) or {"success": True},
    )
    out = h.handle_desktop_bridge(
        {
            "operation": "reload_and_screenshot",
            "pid": 7,
            "page": "P",
            "output": "o.png",
            "save_first": True,
        }
    )
    assert out["success"] is True
    assert seen["page"] == "P"
    assert seen["output"] == "o.png"
    assert seen["save_first"] is True


def test_reload_and_screenshot_requires_page_and_output():
    out = h.handle_desktop_bridge({"operation": "reload_and_screenshot", "pid": 7})
    assert out["success"] is False
    assert out["error_type"] == "missing_argument"


def test_reload_passes_save_first(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge, "op_reload", lambda **k: seen.update(k) or {"success": True}
    )
    h.handle_desktop_bridge({"operation": "reload", "pid": 7, "save_first": True})
    assert seen["save_first"] is True


def test_boolean_args_coerce_string_false(monkeypatch):
    # MCP clients sometimes send booleans as strings; "false" must not
    # coerce to True.
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge, "op_screenshot", lambda **k: seen.update(k) or {"success": True}
    )
    h.handle_desktop_bridge(
        {"operation": "screenshot", "page": "P", "output": "o.png", "optimize": "false"}
    )
    assert seen["optimize"] is False


def test_registration_adds_tool():
    from server.registry import HandlerRegistry

    reg = HandlerRegistry()
    h.register_desktop_bridge_handler(reg)
    assert "17_Desktop_Bridge" in reg._handlers
    td = reg._handlers["17_Desktop_Bridge"]
    # WP2 (A-10/L-08): reload discards unsaved in-memory edits and save
    # overwrites on-disk PBIR edits -> destructive (was pinned False, the bug).
    assert td.annotations["readOnlyHint"] is False
    assert td.annotations["destructiveHint"] is True
