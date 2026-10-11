"""The masking layer wired into the MCP protocol surface (``call_tool``, ``read_resource``, log
forwarding), driven over the SDK's in-memory transport with a fake gateway.

Every return path of ``call_tool`` is masked when the layer is on; when it is off nothing changes
and the gateway is never called.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from server import masking_layer
from server.response_pipeline import MAX_RESULT_SIZE_META
from tests._masking_fakes import PAIRS, FakeGateway, forbid_gateway, install
from tests._mcp_harness import make_app, make_dispatcher, run_session, tool

REAL = "Zorblax Holdings NV"
TOKEN = PAIRS[REAL]


def _dispatcher():
    seen = []

    def echo(args):
        seen.append(dict(args))
        return {"success": True, "client": REAL, "args": args}

    d = make_dispatcher(
        tool(
            "04_Run_DAX",
            echo,
            properties={"query": {"type": "string"}},
            required=["query"],
        ),
        tool(
            "13_Query_Plan",
            lambda a: {"success": True},
            properties={"operation": {"type": "string"}, "query": {"type": "string"}},
            required=["operation"],
        ),
        tool("00_Client_Mode", lambda a: {"success": True, "who": REAL}),
    )
    d.seen = seen  # type: ignore[attr-defined]
    return d


def _call(app, name, args):
    async def body(client):
        return await client.call_tool(name, args)

    return run_session(app, body)


def _masked(res) -> dict:
    """The result is masked: marker first, no real value anywhere."""
    text = res.content[0].text
    data = json.loads(text)
    assert list(data)[0] == "_fv" and data["_fv"].startswith("fvpbi-")
    assert REAL not in text
    return data


@pytest.fixture
def gw(monkeypatch) -> FakeGateway:
    return install(monkeypatch)


# ------------------------------------------------------------------ every return path
def test_success_path_masked_and_arguments_unmasked(gw):
    d = _dispatcher()
    app, _ = make_app(d)
    res = _call(app, "04_Run_DAX", {"query": f'EVALUATE ROW("c", "{TOKEN}")'})
    data = _masked(res)
    assert data["client"] == TOKEN
    assert d.seen[-1]["query"] == f'EVALUATE ROW("c", "{REAL}")'  # the tool got the real value
    assert res.isError is False
    assert res.content[0].meta == {"anthropic/maxResultSizeChars": MAX_RESULT_SIZE_META}


def test_hidden_by_profile_path_masked(gw, monkeypatch):
    from server import tool_profiles

    monkeypatch.setattr(tool_profiles, "is_exposed", lambda name: False)
    monkeypatch.setattr(tool_profiles, "active_profile", lambda: f"profile-{REAL}")
    app, _ = make_app(_dispatcher())
    res = _call(app, "04_Run_DAX", {"query": "EVALUATE {1}"})
    data = _masked(res)
    assert data["error_type"] == "tool_hidden_by_profile" and res.isError is True
    assert TOKEN in data["error"]  # the profile text went through the gateway like any string


def test_argument_conflict_path_masked(gw):
    app, _ = make_app(_dispatcher())
    res = _call(app, "13_Query_Plan", {"operation": "capture", "dax_query": REAL, "query": "other"})
    data = _masked(res)
    assert data["error_type"] == "conflicting_arguments" and res.isError is True


def test_validation_error_path_masked(gw):
    app, _ = make_app(_dispatcher())
    res = _call(app, "04_Run_DAX", {"query": 5, "extra": REAL})
    data = _masked(res)
    assert data["error_type"] == "invalid_arguments" and res.isError is True


def test_rate_limit_path_masked(gw):
    class Limiter:
        enabled = True

        def allow_request(self, tool_name):
            return False

        def get_retry_after(self, tool_name):
            return 3

    app, _ = make_app(_dispatcher(), rate_limiter=Limiter())
    res = _call(app, "04_Run_DAX", {"query": "EVALUATE {1}"})
    data = _masked(res)
    assert data["error_type"] == "rate_limit" and res.isError is True


def test_unexpected_exception_path_masked(gw, monkeypatch):
    d = _dispatcher()

    async def boom(tool_name, args):
        raise RuntimeError(f"failed for {REAL}")

    monkeypatch.setattr(d, "dispatch_and_render_async", boom)
    app, _ = make_app(d)
    res = _call(app, "04_Run_DAX", {"query": "EVALUATE {1}"})
    _masked(res)  # whatever the envelope says about the failure, the real name is gone
    assert res.isError is True


def test_exempt_tool_carries_the_marker_but_is_not_masked(gw):
    app, _ = make_app(_dispatcher())
    res = _call(app, "00_Client_Mode", {})
    data = json.loads(res.content[0].text)
    assert list(data)[0] == "_fv" and data["who"] == REAL
    assert gw.calls == 0


# ------------------------------------------------------------------ fail closed
def test_gateway_down_returns_the_envelope_and_no_data(gw):
    gw.reachable = False
    d = _dispatcher()
    app, _ = make_app(d)
    res = _call(app, "04_Run_DAX", {"query": "EVALUATE {1}"})
    data = json.loads(res.content[0].text)
    assert res.isError is True and data["error_type"] == "masking_unavailable"
    assert REAL not in res.content[0].text and "client" not in data
    assert data["error"] == masking_layer.UNAVAILABLE_TEXT


def test_token_in_arguments_with_the_gateway_down_never_reaches_the_tool(gw):
    gw.reachable = False
    d = _dispatcher()
    app, _ = make_app(d)
    res = _call(app, "04_Run_DAX", {"query": f'EVALUATE {{"{TOKEN}"}}'})
    data = json.loads(res.content[0].text)
    assert res.isError is True and data["error_type"] == "masking_unavailable"
    assert d.seen == []  # the tool was not executed with a pseudonym as a name


# ------------------------------------------------------------------ disabled = no change
def test_disabled_path_is_unchanged_and_never_calls_the_gateway(monkeypatch):
    monkeypatch.delenv("FINVISION_MCP_MASKING", raising=False)
    counter = forbid_gateway(monkeypatch)

    def _never(*_a, **_k):
        raise AssertionError("layer must not run while disabled")

    monkeypatch.setattr(masking_layer, "mask_call_result", _never)
    monkeypatch.setattr(masking_layer, "unmask_arguments", _never)
    d = _dispatcher()
    app, _ = make_app(d)
    res = _call(app, "04_Run_DAX", {"query": f'EVALUATE {{"{TOKEN}"}}'})
    data = json.loads(res.content[0].text)
    assert "_fv" not in data and data["client"] == REAL
    assert d.seen[-1]["query"] == f'EVALUATE {{"{TOKEN}"}}'  # arguments untouched
    assert counter["calls"] == 0


# ------------------------------------------------------------------ resources
def _register_resource(rm, uri="powerbi://test/client"):
    rm.register_dynamic_resource(
        uri=uri,
        name="t",
        description="t",
        provider=lambda: json.dumps({"c": REAL}),
        mime_type="application/json",
    )
    return uri


def test_resource_read_is_masked(gw):
    app, rm = make_app(_dispatcher())
    uri = _register_resource(rm)

    async def body(client):
        return await client.read_resource(uri)

    res = run_session(app, body)
    text = res.contents[0].text
    assert REAL not in text and json.loads(text)["c"] == TOKEN
    assert list(json.loads(text))[0] == "_fv"


def test_resource_read_fails_without_data_when_masking_is_unavailable(gw):
    gw.reachable = False
    app, rm = make_app(_dispatcher())
    uri = _register_resource(rm)

    async def body(client):
        try:
            await client.read_resource(uri)
        except Exception as exc:  # noqa: BLE001 - McpError
            return str(exc)
        return "no error"

    msg = run_session(app, body)
    assert "Failed to read resource" in msg and REAL not in msg and "no error" != msg


def test_resource_read_unchanged_when_disabled(monkeypatch):
    monkeypatch.delenv("FINVISION_MCP_MASKING", raising=False)
    counter = forbid_gateway(monkeypatch)
    app, rm = make_app(_dispatcher())
    uri = _register_resource(rm)

    async def body(client):
        return await client.read_resource(uri)

    res = run_session(app, body)
    assert json.loads(res.contents[0].text) == {"c": REAL}
    assert counter["calls"] == 0


# ------------------------------------------------------------------ completions, listings
def _complete(app, values, monkeypatch, **extra):
    from mcp import types

    from server import mcp_app

    monkeypatch.setattr(
        mcp_app,
        "_complete_argument",
        lambda ref, argument, context: types.Completion(values=values, total=len(values), **extra),
    )

    async def body(client):
        return await client.complete(
            types.PromptReference(type="ref/prompt", name="p"), {"name": "table", "value": ""}
        )

    return run_session(app, body)


def test_completion_values_are_masked_in_one_call(gw, monkeypatch):
    app, _ = make_app(_dispatcher())
    res = _complete(app, [REAL, "Sales", "Quentin Testermans"], monkeypatch, hasMore=True)
    assert res.completion.values == [TOKEN, "Sales", PAIRS["Quentin Testermans"]]
    assert res.completion.hasMore is True and res.completion.total == 3
    assert len(gw.mask_calls) == 1


def test_completion_is_empty_when_masking_is_unavailable(gw, monkeypatch):
    gw.reachable = False
    app, _ = make_app(_dispatcher())
    res = _complete(app, [REAL, "Sales"], monkeypatch)
    assert res.completion.values == []


def test_completion_is_empty_when_the_gateway_call_raises(gw, monkeypatch):
    gw.raise_on_mask = True
    app, _ = make_app(_dispatcher())
    res = _complete(app, [REAL], monkeypatch)
    assert res.completion.values == []


def test_completion_unchanged_and_no_gateway_call_when_disabled(monkeypatch):
    monkeypatch.delenv("FINVISION_MCP_MASKING", raising=False)
    counter = forbid_gateway(monkeypatch)
    app, _ = make_app(_dispatcher())
    res = _complete(app, [REAL], monkeypatch)
    assert res.completion.values == [REAL] and counter["calls"] == 0


def _register_listing(rm):
    rm.register_dynamic_resource(
        uri=f"powerbi://client/{REAL.split()[0]}",
        name=f"{REAL} model",
        description=f"All data of {REAL}",
        provider=lambda: "x",
        mime_type="text/plain",
    )
    rm.register_template(
        uri_template="powerbi://client/{table}/rows",
        name=f"{REAL} rows",
        description=f"Rows for {REAL}",
        resolver=lambda **kw: "x",
        mime_type="text/plain",
    )


def test_resource_and_template_listings_are_masked(gw):
    app, rm = make_app(_dispatcher())
    _register_listing(rm)

    async def body(client):
        return await client.list_resources(), await client.list_resource_templates()

    resources, templates = run_session(app, body)
    mine = [r for r in resources.resources if "client" in str(r.uri)]
    assert mine and all(REAL not in (r.name or "") + (r.description or "") for r in mine)
    assert mine[0].name == f"{TOKEN} model" and mine[0].description == f"All data of {TOKEN}"
    assert str(mine[0].uri) == f"powerbi://client/{REAL.split()[0]}"  # the uri is not masked
    tmpl = [t for t in templates.resourceTemplates if "client" in t.uriTemplate]
    assert tmpl[0].name == f"{TOKEN} rows" and tmpl[0].description == f"Rows for {TOKEN}"
    assert tmpl[0].uriTemplate == "powerbi://client/{table}/rows"
    assert tmpl[0].mimeType == "text/plain"


def test_listings_are_empty_when_masking_is_unavailable(gw):
    gw.reachable = False
    app, rm = make_app(_dispatcher())
    _register_listing(rm)

    async def body(client):
        return await client.list_resources(), await client.list_resource_templates()

    resources, templates = run_session(app, body)
    assert resources.resources == [] and templates.resourceTemplates == []


def test_listings_unchanged_and_no_gateway_call_when_disabled(monkeypatch):
    monkeypatch.delenv("FINVISION_MCP_MASKING", raising=False)
    counter = forbid_gateway(monkeypatch)
    app, rm = make_app(_dispatcher())
    _register_listing(rm)

    async def body(client):
        return await client.list_resources(), await client.list_resource_templates()

    resources, templates = run_session(app, body)
    assert any(r.name == f"{REAL} model" for r in resources.resources)
    assert any(t.name == f"{REAL} rows" for t in templates.resourceTemplates)
    assert counter["calls"] == 0


# ------------------------------------------------------------------ log forwarding
class _Session:
    def __init__(self):
        self.sent = []

    async def send_log_message(self, level, data, logger=None):
        self.sent.append(data)


def _emit(monkeypatch, message: str):
    """Emit one WARNING record through the forwarder and run the scheduled send to completion."""
    from server import log_forward

    session = _Session()
    factories = []
    monkeypatch.setattr(log_forward, "_get_session", lambda: session)
    monkeypatch.setattr(log_forward, "fire_on_server_loop", lambda f: factories.append(f) or True)
    handler = log_forward.McpNotificationHandler()
    handler.setLevel(logging.WARNING)
    record = logging.LogRecord("t", logging.WARNING, __file__, 1, message, None, None)
    handler.emit(record)
    assert len(factories) == 1
    asyncio.run(factories[0]())
    return session


def test_log_message_masked_before_it_is_sent(gw, monkeypatch):
    session = _emit(monkeypatch, f"failed for {REAL}")
    assert session.sent == [f"failed for {TOKEN}"]


def test_log_message_dropped_when_masking_fails(gw, monkeypatch):
    gw.reachable = False
    session = _emit(monkeypatch, f"failed for {REAL}")
    assert session.sent == []


def test_log_message_sent_as_is_and_no_gateway_call_when_disabled(monkeypatch):
    monkeypatch.delenv("FINVISION_MCP_MASKING", raising=False)
    counter = forbid_gateway(monkeypatch)
    session = _emit(monkeypatch, f"failed for {REAL}")
    assert session.sent == [f"failed for {REAL}"]
    assert counter["calls"] == 0
