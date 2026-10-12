"""Bridge to the live DAX injector for applying optimized DAX.

Review E-01: this used to build ``DAXInjector(connection_state)`` — an injector
bound to the state object (no ``.State`` / ``.ConnectionString``), so every
write returned ``connection_lost`` and ``09_Debug_Operations debug_loop``
(live_tom) never applied anything. It now uses the ADOMD-bound injector the
connection state manages (``dax_injector.get_live_injector``) and passes the
destructive-op gate first (``MCP_AUTONOMOUS_GATE_POLICY``; STRICT blocks unless
``gate_args`` carries ``confirm_destructive=true``).
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class MeasureApplier:
    """Applies optimized DAX to Power BI models via existing infrastructure."""

    def __init__(self, connection_state: Any = None):
        self._connection_state = connection_state

    def apply(
        self,
        measure_name: str,
        table_name: str,
        new_dax: str,
        *,
        gate_args: Optional[Dict[str, Any]] = None,
        expected_expression: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Replace the expression of an existing measure (never creates one).

        ``expected_expression``: when given, the write happens only if the
        measure still has that body (one TOM session, conflict -> nothing
        written).
        """
        if not self._connection_state:
            return {"success": False, "error": "No connection to Power BI model"}
        from core.autonomous.gating import Severity, merge_gate_info, require_autonomous_mode

        gate = require_autonomous_mode(
            op_name="optimizer:apply_measure",
            args=dict(gate_args or {}),
            severity=Severity.DESTRUCTIVE,
        )
        if gate is not None and not gate.get("success", True):
            return gate
        try:
            from core.dax.dax_injector import get_live_injector

            injector = get_live_injector(self._connection_state)
            if expected_expression is not None and hasattr(injector, "apply_measure_changes"):
                res = injector.apply_measure_changes(
                    upserts=[
                        {
                            "table_name": table_name,
                            "measure_name": measure_name,
                            "dax_expression": new_dax,
                            "expected_expression": expected_expression,
                        }
                    ]
                )
                if res.get("committed"):
                    res["success"] = True
            else:
                res = injector.upsert_measure(
                    measure_name=measure_name,
                    table_name=table_name,
                    dax_expression=new_dax,
                    create_if_missing=False,
                )
            return merge_gate_info(res, gate)
        except Exception as e:
            logger.error(f"Failed to apply measure: {e}")
            return {"success": False, "error": str(e)}

    def is_connected(self) -> bool:
        """Check if we have an active connection."""
        return (
            self._connection_state is not None
            and hasattr(self._connection_state, "is_connected")
            and self._connection_state.is_connected()
        )
