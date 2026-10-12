"""Shared helpers for the protocol-layer tests (not a test module).

Builds a small HandlerRegistry of fake tools, a ToolDispatcher bound to it,
and runs a REAL MCP client/server pair over the SDK's in-memory transport, so
the tests exercise the actual request_ctx / event-loop / worker-thread seams.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional

from server.dispatch import ToolDispatcher
from server.registry import HandlerRegistry, ToolDefinition


def tool(
    name: str,
    handler: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    properties: Optional[Dict[str, Any]] = None,
    required: Optional[list] = None,
    read_only: bool = True,
    destructive: bool = False,
    category: str = "query",
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"test tool {name} " + "x" * 10,
        handler=handler,
        input_schema={
            "type": "object",
            "properties": dict(properties or {}),
            "required": list(required or []),
        },
        category=category,
        annotations={
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": read_only,
            "openWorldHint": False,
        },
    )


def make_dispatcher(*tool_defs: ToolDefinition) -> ToolDispatcher:
    reg = HandlerRegistry()
    for td in tool_defs:
        reg.register(td)
    reg.prebuild()  # as the server does at startup (keeps first-call latency out of timeouts)
    d = ToolDispatcher()
    d.registry = reg
    return d


def make_app(dispatcher: ToolDispatcher, **kwargs):
    from server.mcp_app import create_server
    from server.resources import ResourceManager

    rm = kwargs.pop("resource_manager", None) or ResourceManager()
    return (
        create_server(
            dispatcher.registry, dispatcher, version="9.9.9-test", resource_manager=rm, **kwargs
        ),
        rm,
    )


def run_session(app, body, **callbacks):
    """Run ``await body(client_session)`` against ``app`` over memory streams."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async def _main():
        async with create_connected_server_and_client_session(app, **callbacks) as client:
            return await body(client)

    return asyncio.run(_main())
