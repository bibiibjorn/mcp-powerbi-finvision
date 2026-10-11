"""
Autonomous subsystem — trimmed to the live-iteration + gating surface.

v7.8 removed the close/reopen/save/snapshot lifecycle and the
12_Autonomous_Workflow tool. What remains:

- ``desktop_bridge`` — wrapper around the MS ``powerbi-desktop`` CLI (run as
  ``node <cli script>``, never via the .cmd shim): in-place reload +
  screenshot of a RUNNING Power BI Desktop. Reload refuses on unsaved work, save
  refuses while disk edits are pending. Backs ``17_Desktop_Bridge`` + the debug loop.
- ``edit_ledger`` — pending live-TOM writes / disk edits per project (fed by the
  dispatcher and every ``pbip_utils`` write) that those refusals are based on.
- ``gating`` — policy-driven destructive-op gate (confirm_destructive
  break-glass). Imported by every write handler + CRUD manager.
- ``reload_hints`` — attaches reload_required hints pointing at the bridge.
- ``clr_errors`` — CLR exception-chain extraction for refresh errors.
- ``loops.debug_loop`` — closed-loop debug → fix → bridge-reload → verify.
"""

from core.autonomous import desktop_bridge
from core.autonomous.clr_errors import (
    extract_clr_exception_chain,
    format_refresh_error,
)

__all__ = [
    "desktop_bridge",
    "extract_clr_exception_chain",
    "format_refresh_error",
]
