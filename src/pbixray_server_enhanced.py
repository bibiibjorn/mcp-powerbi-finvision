#!/usr/bin/env python3
"""
MCP-PowerBi-Finvision Server - Clean Modular Edition
Uses handler registry for all tool routing.
Version is the single source of truth in src/__version__.py (do not hardcode here).

Bootstrap only: logging, managers, handler registration and the stdio run loop.
The MCP protocol surface (tools/call pipeline, resources, prompts, logging
capability) lives in ``server/mcp_app.py``; the response pipeline in
``server/response_pipeline.py``; dispatch, timeouts and write serialisation in
``server/dispatch.py``.
"""

import asyncio
import logging
import sys
import os
import time

# Add parent directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from mcp.server.stdio import stdio_server  # noqa: E402

from __version__ import __version__  # noqa: E402
from core.config.config_manager import config  # noqa: E402
from server.logging_setup import configure_logging  # noqa: E402

# A-27 / K-07: stderr honours server.log_level; the rotating file log keeps INFO.
LOG_PATH = configure_logging(
    os.path.join(parent_dir, "logs"), config.get("server.log_level", "WARNING")
)
logger = logging.getLogger("mcp_powerbi_finvision")
if LOG_PATH:
    logger.info("File logging enabled: %s", LOG_PATH)

from core.infrastructure.connection_manager import ConnectionManager  # noqa: E402
from core.config.tool_timeouts import ToolTimeoutManager  # noqa: E402
from core.infrastructure.rate_limiter import RateLimiter  # noqa: E402
from core.infrastructure.limits_manager import init_limits_manager  # noqa: E402
from core.orchestration.agent_policy import AgentPolicy  # noqa: E402
from core.infrastructure.connection_state import connection_state  # noqa: E402

from server.registry import get_registry  # noqa: E402
from server.dispatch import ToolDispatcher  # noqa: E402
from server.handlers import register_all_handlers  # noqa: E402
from server.mcp_app import create_server, seed_resources  # noqa: E402
from server.resources import get_resource_manager  # noqa: E402

# Track server start time
start_time = time.time()

# Initialize managers
connection_manager = ConnectionManager()
timeout_manager = ToolTimeoutManager(config.get("tool_timeouts", {}))
for _unknown in timeout_manager.unknown_keys():
    logger.warning(
        "config tool_timeouts key %r is not a registered tool name and is ignored", _unknown
    )
rate_limiter = RateLimiter(config.get("rate_limiting", {}))
limits_manager = init_limits_manager(config.get_all())

connection_state.set_connection_manager(connection_manager)

# Initialize handler registry and dispatcher
registry = get_registry()
register_all_handlers(registry)
dispatcher = ToolDispatcher()

agent_policy = AgentPolicy(
    config,
    timeout_manager=timeout_manager,
    rate_limiter=rate_limiter,
    limits_manager=limits_manager,
)

# Set agent_policy in connection_state so handlers can access it
connection_state.agent_policy = agent_policy

try:
    seed_resources(get_resource_manager(), dispatcher)
except Exception as _seed_err:  # noqa: BLE001
    logger.warning("Static resource seeding skipped: %s", _seed_err)

# Forward WARN+ log records to the MCP client as LoggingMessageNotification.
try:
    from server.log_forward import install as _install_log_forwarder

    _install_log_forwarder(level=logging.WARNING)
except Exception as _log_err:  # noqa: BLE001
    logger.warning("MCP log forwarder not installed: %s", _log_err)

# Client gateway: when any folder (or every chat) is routed through it, make sure it runs
# (background thread; never blocks the server start, never raises). Nothing happens otherwise.
try:
    from client_gateway.control_client import ensure_gateway_background as _ensure_gateway

    _ensure_gateway()
except Exception as _gw_err:  # noqa: BLE001
    logger.warning("Client gateway not started: %s", _gw_err)

# A-24: serverInfo.version is OUR version (was the mcp SDK's); instructions are
# generated from the registry (A-34).
app = create_server(
    registry,
    dispatcher,
    version=__version__,
    rate_limiter=rate_limiter,
)


async def main():
    """Main entry point"""
    logger.info("=" * 80)
    logger.info(f"MCP-PowerBi-Finvision Server v{__version__} - Clean Modular Edition")
    logger.info("=" * 80)
    logger.info(f"Registered {registry.total_tools()} tools")

    from server.request_context import set_server_loop

    set_server_loop(asyncio.get_running_loop())
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def cli() -> None:
    """Sync console-script entry point."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.critical(f"Server crashed: {e}", exc_info=True)
        # Flush all log handlers to ensure error is written
        for handler in logging.getLogger().handlers:
            handler.flush()
        raise


if __name__ == "__main__":
    cli()
