"""A-02 / L-01: progress, elicitation, roots, sampling and log forwarding must
work from a HANDLER THREAD, driven through the real dispatcher.

The old dispatcher ran handlers via ``loop.run_in_executor(None, ...)``, which
does not copy contextvars (``request_ctx`` invisible in the worker) and the
bridges used ``asyncio.get_event_loop()``, which raises in a worker thread on
Python 3.12+. The previous test suite only passed because it monkeypatched
exactly those two links. These tests patch NEITHER: a real MCP client/server
pair runs over the SDK in-memory transport, a real handler runs on the
dispatcher's worker pool, and the client's callbacks observe what arrives.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from mcp import types

from tests._mcp_harness import make_app, make_dispatcher, run_session, tool


def _body_text(result) -> dict:
    return json.loads(result.content[0].text)


def test_progress_notification_reaches_the_client_from_a_worker_thread():
    seen_threads = []

    def handler(args):
        from server.progress import emit_progress

        seen_threads.append(threading.current_thread().name)
        emit_progress(1, 2, "half way")
        emit_progress(2, 2, "done")
        time.sleep(0.05)  # let the notifications flush before the response
        return {"success": True}

    d = make_dispatcher(tool("04_Run_DAX", handler))
    app, _ = make_app(d)
    received = []

    async def body(client):
        async def on_progress(progress, total, message):
            received.append((progress, total, message))

        res = await client.call_tool("04_Run_DAX", {}, progress_callback=on_progress)
        await asyncio.sleep(0.05)
        return res

    res = run_session(app, body)
    assert res.isError is False
    assert seen_threads and seen_threads[0].startswith("mcp-")  # ran on the dispatcher pool
    assert (1.0, 2.0, "half way") in received
    assert (2.0, 2.0, "done") in received


def test_elicitation_prompt_reaches_the_client_and_answer_returns():
    answers = []

    def handler(args):
        from server.elicitation import elicit_confirmation

        answers.append(
            elicit_confirmation(
                message="really?", op_name="02:x", severity="catastrophic", timeout=5
            )
        )
        return {"success": True, "answer": answers[-1]}

    d = make_dispatcher(tool("02_Model_Operations", handler, read_only=False, destructive=True))
    app, _ = make_app(d)
    prompts = []

    async def on_elicit(context, params):
        prompts.append(params.message)
        return types.ElicitResult(action="accept", content={"confirm": True})

    async def body(client):
        return await client.call_tool("02_Model_Operations", {"operation": "create"})

    res = run_session(app, body, elicitation_callback=on_elicit)
    assert res.isError is False
    assert answers == [True]
    assert prompts and "really?" in prompts[0]


def test_roots_are_listed_from_a_worker_and_percent_decoded():
    got = []

    def handler(args):
        from server.roots import list_root_paths

        got.append(list_root_paths())
        return {"success": True}

    d = make_dispatcher(tool("09_Document", handler))
    app, _ = make_app(d)

    async def on_roots(context):
        return types.ListRootsResult(
            roots=[types.Root(uri="file:///C:/My%20Repo/Sales.Report", name="repo")]
        )

    async def body(client):
        return await client.call_tool("09_Document", {"operation": "page"})

    res = run_session(app, body, list_roots_callback=on_roots)
    assert res.isError is False
    assert got == [["C:/My Repo/Sales.Report"]]


def test_warning_logged_in_a_handler_is_forwarded_to_the_client():
    from server.log_forward import install

    forwarder = install(level=logging.WARNING)

    def handler(args):
        logging.getLogger("wp2.test.handler").warning("CLR hiccup while reading")
        time.sleep(0.05)
        return {"success": True}

    d = make_dispatcher(tool("04_Run_DAX", handler))
    app, _ = make_app(d)
    messages = []

    async def on_log(params):
        messages.append(params)

    async def body(client):
        res = await client.call_tool("04_Run_DAX", {})
        await asyncio.sleep(0.05)
        return res

    try:
        run_session(app, body, logging_callback=on_log)
    finally:
        logging.getLogger().removeHandler(forwarder)
    assert any("CLR hiccup" in str(m.data) for m in messages), messages
    assert all(m.level == "warning" for m in messages if "CLR hiccup" in str(m.data))


def test_sampling_request_reaches_the_client():
    out = []

    def handler(args):
        from server.sampling import sample_text

        out.append(sample_text("summarize", timeout=5))
        return {"success": True}

    d = make_dispatcher(tool("09_Document", handler))
    app, _ = make_app(d)

    async def on_sample(context, params):
        return types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(type="text", text="a summary"),
            model="fake",
        )

    async def body(client):
        return await client.call_tool("09_Document", {"operation": "page"})

    run_session(app, body, sampling_callback=on_sample)
    assert out == ["a summary"]


def test_roots_resource_does_not_freeze_the_event_loop():
    """The powerbi://state/roots provider runs ON the loop: it must await
    roots/list instead of blocking the loop on run_coroutine_threadsafe
    (the old path froze the loop for ~2.5 s and returned [])."""
    from server.mcp_app import seed_resources
    from server.resources import ResourceManager

    rm = ResourceManager()
    seed_resources(rm)
    d = make_dispatcher(tool("04_Run_DAX", lambda a: {"success": True}))
    app, _ = make_app(d, resource_manager=rm)

    async def on_roots(context):
        return types.ListRootsResult(roots=[types.Root(uri="file:///C:/work/Proj")])

    async def body(client):
        t0 = time.monotonic()
        res = await client.read_resource("powerbi://state/roots")
        return res, time.monotonic() - t0

    res, elapsed = run_session(app, body, list_roots_callback=on_roots)
    assert json.loads(res.contents[0].text) == {"roots": ["C:/work/Proj"]}
    assert res.contents[0].mimeType == "application/json"
    assert elapsed < 1.5


def test_sync_roots_bridge_on_the_loop_thread_returns_immediately():
    """Defensive: a sync caller on the loop thread gets [] at once, no deadlock."""
    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext

    from server import request_context
    from server.roots import list_root_paths

    class _Session:
        async def list_roots(self):  # pragma: no cover - must not be reached
            raise AssertionError("should not be awaited from the loop thread")

    async def main():
        request_context.set_server_loop(asyncio.get_running_loop())
        tok = request_ctx.set(
            RequestContext(request_id=1, meta=None, session=_Session(), lifespan_context=None)
        )
        try:
            t0 = time.monotonic()
            paths = list_root_paths(timeout=2.0)
            return paths, time.monotonic() - t0
        finally:
            request_ctx.reset(tok)

    paths, elapsed = asyncio.run(main())
    assert paths == []
    assert elapsed < 0.5
