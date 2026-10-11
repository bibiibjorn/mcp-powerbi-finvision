"""A-01 / A-14 / A-15 / A-18 / A-20 / A-24 / A-27 / A-33 / L-13 / L-14: the MCP
protocol surface driven end-to-end over the SDK in-memory transport.

Before: every resources/read failed (AnyUrl vs str keys), serverInfo.version was
the SDK's, the logging capability was never declared, undeclared args vanished
silently, pagination claimed completeness it didn't have, and none of
src/pbixray_server_enhanced.py was tested.
"""

from __future__ import annotations

import json

import pytest

from tests._mcp_harness import make_app, make_dispatcher, run_session, tool


def _payload(res) -> dict:
    return json.loads(res.content[0].text)


def _dispatcher():
    return make_dispatcher(
        tool(
            "04_Run_DAX",
            lambda a: {"success": True, "rows": [{"n": i} for i in range(60)], "echo": a},
            properties={"query": {"type": "string"}},
            required=["query"],
        ),
        tool(
            "07_Visual_Operations",
            lambda a: {"success": True, "pages": [{"p": i} for i in range(7)], "count": 7},
            properties={"operation": {"type": "string", "enum": ["list", "get"]}},
            read_only=False,
            destructive=True,
            category="pbip",
        ),
        tool(
            "09_Document",
            lambda a: {"ok": False, "err": "Unknown operation: bogus"},
            properties={"operation": {"type": "string"}},
        ),
    )


def test_server_info_version_and_capabilities():
    app, _ = make_app(_dispatcher())

    async def body(client):
        return await client.list_tools()

    tools = run_session(app, body)
    opts = app.create_initialization_options()
    assert opts.server_version == "9.9.9-test"  # A-24: not the mcp SDK version
    assert opts.capabilities.logging is not None  # A-27: logging capability declared
    assert "04_Run_DAX" in opts.instructions
    assert {t.name for t in tools.tools} == {"04_Run_DAX", "07_Visual_Operations", "09_Document"}


def test_tool_list_is_prebuilt_once():
    d = _dispatcher()
    app, _ = make_app(d)
    first = d.registry.get_all_tools_as_mcp()
    second = d.registry.get_all_tools_as_mcp()
    assert [id(t) for t in first] == [id(t) for t in second]  # A-33: cached objects
    assert d.registry.get_input_validator("04_Run_DAX") is d.registry.get_input_validator(
        "04_Run_DAX"
    )


def test_call_success_and_uncompacted_envelope():
    app, _ = make_app(_dispatcher())

    async def body(client):
        return await client.call_tool("04_Run_DAX", {"query": "EVALUATE {1}"})

    res = run_session(app, body)
    body_ = _payload(res)
    assert res.isError is False
    assert body_["success"] is True and "ok" not in body_
    assert res.content[0].meta == {"anthropic/maxResultSizeChars": 500000}


def test_invalid_arguments_return_canonical_envelope_with_valid_values():
    app, _ = make_app(_dispatcher())

    async def body(client):
        missing = await client.call_tool("04_Run_DAX", {})
        bad_enum = await client.call_tool("07_Visual_Operations", {"operation": "nope"})
        return missing, bad_enum

    missing, bad_enum = run_session(app, body)
    assert missing.isError and _payload(missing)["error_type"] == "invalid_arguments"
    assert "'query' is a required property" in _payload(missing)["error"]
    assert bad_enum.isError
    assert _payload(bad_enum)["valid_values"] == ["list", "get"]


def test_undeclared_args_reach_the_handler_and_are_reported():
    """A-15 mechanism: never silently dropped — passed through + `unknown_args`."""
    app, _ = make_app(_dispatcher())

    async def body(client):
        return await client.call_tool("04_Run_DAX", {"query": "EVALUATE {1}", "candidat": 1})

    out = _payload(run_session(app, body))
    assert out["echo"]["candidat"] == 1
    assert out["unknown_args"] == ["candidat"]


def test_legacy_error_envelope_is_normalized_and_typed():
    """L-13: `{ok:false, err}` from a handler reaches the client as the canonical
    envelope with an inferred error_type (not 'unspecified')."""
    app, _ = make_app(_dispatcher())

    async def body(client):
        return await client.call_tool("09_Document", {"operation": "bogus"})

    res = run_session(app, body)
    out = _payload(res)
    assert res.isError is True
    assert out["success"] is False
    assert out["error"] == "Unknown operation: bogus"
    assert out["error_type"] == "invalid_operation"
    assert "ok" not in out and "err" not in out


def test_unknown_tool_is_an_error_with_inventory():
    app, _ = make_app(_dispatcher())

    async def body(client):
        return await client.call_tool("12_Autonomous_Workflow", {})

    res = run_session(app, body)
    out = _payload(res)
    assert res.isError and out["error_type"] == "unknown_tool"
    assert "04_Run_DAX" in out["available_tools"]


# ---------------------------------------------------------------------------
# Pagination through the protocol (A-18)
# ---------------------------------------------------------------------------


def test_pagination_falls_back_to_largest_list_and_concise_honours_page_size():
    app, _ = make_app(_dispatcher())

    async def body(client):
        pages = await client.call_tool(
            "07_Visual_Operations", {"operation": "list", "page_size": 3}
        )
        concise = await client.call_tool(
            "04_Run_DAX", {"query": "q", "page_size": 40, "response_format": "concise"}
        )
        stale = await client.call_tool(
            "04_Run_DAX", {"query": "q", "page_size": 5, "next_token": "visuals:5"}
        )
        return pages, concise, stale

    pages, concise, stale = run_session(app, body)
    p = _payload(pages)
    assert len(p["pages"]) == 3 and p["paginated_key"] == "pages" and p["has_more"] is True
    c = _payload(concise)
    assert len(c["rows"]) == 40  # not capped at 25 behind next_token
    assert c["next_token"] == "rows:40"
    s = _payload(stale)
    assert stale.isError and s["success"] is False and s["error_type"] == "stale_pagination_token"


def test_page_size_without_any_list_does_not_claim_completeness():
    d = make_dispatcher(tool("10_Show_User_Guide", lambda a: {"success": True, "guide": "text"}))
    out = d.dispatch("10_Show_User_Guide", {"page_size": 10})
    assert out["paginated_key"] is None
    assert "has_more" not in out


# ---------------------------------------------------------------------------
# Resources (A-01 / L-14)
# ---------------------------------------------------------------------------


def test_every_seeded_resource_reads_with_its_declared_mime_type():
    from server.mcp_app import seed_resources
    from server.resources import ResourceManager

    rm = ResourceManager()
    d = _dispatcher()
    seed_resources(rm, d)
    app, _ = make_app(d, resource_manager=rm)

    async def body(client):
        listed = await client.list_resources()
        out = {}
        for r in listed.resources:
            if str(r.uri) in ("powerbi://state/connection", "powerbi://docs/user-guide"):
                continue  # need the live connection_state / full registry
            res = await client.read_resource(r.uri)
            out[str(r.uri)] = (r.mimeType, res.contents[0].mimeType, res.contents[0].text)
        templates = await client.list_resource_templates()
        tool_ref = await client.read_resource("powerbi://docs/tools/04_Run_DAX")
        catalog = await client.read_resource("powerbi://docs/visual-catalog/cardVisual")
        return out, templates, tool_ref, catalog

    out, templates, tool_ref, catalog = run_session(app, body)
    assert "powerbi://docs/claude-md" not in out  # developer rules no longer served
    assert "powerbi://metadata/bpa-rules" in out
    for uri, (declared, served, text) in out.items():
        assert declared == served, uri
        assert text, uri
    json.loads(out["powerbi://state/dispatcher"][2])
    assert {t.uriTemplate for t in templates.resourceTemplates} >= {
        "powerbi://docs/tools/{tool}",
        "powerbi://docs/visual-catalog/{type}",
        "powerbi://docs/design-principles/{topic}",
    }
    assert "`04_Run_DAX`" in tool_ref.contents[0].text
    assert tool_ref.contents[0].mimeType == "text/markdown"
    assert catalog.contents[0].text


def test_resource_template_rejects_path_traversal():
    from server.mcp_app import seed_resources
    from server.resources import ResourceManager

    rm = ResourceManager()
    seed_resources(rm)
    with pytest.raises(ValueError):
        rm.read_resource("powerbi://docs/visual-catalog/..%2F..%2FCLAUDE")


def test_read_resource_accepts_anyurl_objects():
    from pydantic import AnyUrl

    from server.resources import ResourceManager

    rm = ResourceManager()
    rm.register_dynamic_resource("powerbi://x/y", "n", "d", lambda: "hello", mime_type="text/plain")
    assert rm.read_resource(AnyUrl("powerbi://x/y")) == "hello"


def test_spilled_results_become_readable_export_resources(monkeypatch, tmp_path):
    import server.dispatch as dispatch_mod
    import server.response_pipeline as rp

    monkeypatch.setattr(rp, "spill_dir", lambda: tmp_path)
    monkeypatch.setattr(rp, "INLINE_MAX_CHARS", 2000)
    # F-34: the soft inline budget (first page of the list) would cut this result before the hard
    # spill; switch it off to exercise the spill + export-resource path this test is about.
    monkeypatch.setattr(dispatch_mod, "soft_inline_budget", lambda: 0)
    d = make_dispatcher(
        tool("04_Run_DAX", lambda a: {"success": True, "rows": [{"v": "x" * 50}] * 200})
    )
    monkeypatch.setattr(d, "_inline_cap", lambda name: 2000)
    app, rm = make_app(d)

    async def body(client):
        res = await client.call_tool("04_Run_DAX", {})
        latest = await client.read_resource("powerbi://export/latest")
        return res, latest

    res, latest = run_session(app, body)
    summary = _payload(res)
    assert summary["_spilled"] is True
    assert len(json.loads(latest.contents[0].text)["rows"]) == 200
    assert latest.contents[0].mimeType == "application/json"


def test_set_logging_level_changes_the_forwarding_threshold():
    import logging

    from server import log_forward

    handler = log_forward.install(level=logging.WARNING)
    app, _ = make_app(_dispatcher())

    async def body(client):
        await client.set_logging_level("error")

    try:
        run_session(app, body)
        assert handler.level == logging.ERROR
    finally:
        handler.setLevel(logging.WARNING)
        logging.getLogger().removeHandler(handler)


def test_bridge_screenshot_can_return_inline_image_content(tmp_path):
    """A-31b / L-15: opt-in ImageContent for 17_Desktop_Bridge screenshots."""
    png = tmp_path / "page.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    d = make_dispatcher(
        tool(
            "17_Desktop_Bridge",
            lambda a: {"success": True, "output": str(png), "page_id": "p1"},
            properties={"operation": {"type": "string"}},
            read_only=False,
            destructive=True,
            category="debug",
        )
    )
    app, _ = make_app(d)

    async def body(client):
        plain = await client.call_tool("17_Desktop_Bridge", {"operation": "screenshot"})
        inline = await client.call_tool(
            "17_Desktop_Bridge", {"operation": "screenshot", "inline_image": True}
        )
        return plain, inline

    plain, inline = run_session(app, body)
    assert [c.type for c in plain.content] == ["text"]
    assert [c.type for c in inline.content] == ["text", "image"]
    assert inline.content[1].mimeType == "image/png"
    assert "unknown_args" not in _payload(inline)  # declared by the registry
