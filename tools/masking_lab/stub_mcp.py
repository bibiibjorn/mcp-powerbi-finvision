"""Lab-only stub MCP server: one tool with SYNTHETIC client data, sent through the real masking layer.

Used to see how an AI app carries MCP tool results (does the PBI marker reach the gateway?) without
touching Power BI: it has no connection tool and no model access. Run it from a test client's MCP
config with the lab gateway's environment, e.g.

    command: <venv python>   args: [<repo>/tools/masking_lab/stub_mcp.py]
    env: FINVISION_MCP_MASKING=1, FINVISION_GATEWAY_PORT=18890,
         FINVISION_MASKING_HOME=<lab dir>/home, FINVISION_GATEWAY_AUTOSTART=0
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from mcp import types  # noqa: E402
from mcp.server.lowlevel import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402

from server import masking_layer  # noqa: E402

TOOL = "lab_customer_revenue"
ROWS = {
    "Zorblax Holdings NV": 1200,
    "Quillfeather Logistics BV": 800,
    "Brambleton Fakeworks SA": 450,
}

app: Server = Server("masking-lab-stub")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=TOOL,
            description=(
                "Synthetic lab data: revenue per customer. Pass a customer name to get one "
                "customer, or no argument for all customers."
            ),
            inputSchema={"type": "object", "properties": {"customer": {"type": "string"}}},
        )
    ]


@app.call_tool(validate_input=False)
async def call_tool(name: str, arguments: Any) -> types.CallToolResult:
    args = dict(arguments) if isinstance(arguments, dict) else {}
    try:
        args = await asyncio.to_thread(masking_layer.unmask_arguments, args)
    except masking_layer.MaskingUnavailable:
        return masking_layer.unavailable_result(name)
    wanted = str(args.get("customer") or "").strip().lower()
    rows = [
        {"Customer": c, "Revenue": r} for c, r in ROWS.items() if not wanted or wanted in c.lower()
    ]
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps({"success": True, "rows": rows}))],
        isError=False,
    )
    if not masking_layer.enabled():
        return result
    return await asyncio.to_thread(masking_layer.mask_call_result, name, result)


async def _main() -> None:
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
