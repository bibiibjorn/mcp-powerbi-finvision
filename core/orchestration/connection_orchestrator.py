"""Connection management orchestration."""

import logging
from typing import Any, Dict, Optional
from .base_orchestrator import BaseOrchestrator

logger = logging.getLogger(__name__)


class ConnectionOrchestrator(BaseOrchestrator):
    """Handles connection and health check operations."""

    def ensure_connected(
        self, connection_manager, connection_state, preferred_index: Optional[int] = None
    ) -> Dict[str, Any]:
        """Ensure the server is connected to a Power BI Desktop instance."""
        if connection_state.is_connected():
            info = connection_manager.get_instance_info() or {}
            return {
                "success": True,
                "already_connected": True,
                "port": info.get("port") if info else None,
            }

        instances = connection_manager.detect_instances()
        if not instances:
            return {
                "success": False,
                "error": "No Power BI Desktop instances detected. Open a .pbix in Power BI Desktop and try again.",
                "error_type": "no_instances",
                "suggestions": [
                    "Open Power BI Desktop with a .pbix file",
                    "Wait 10–15 seconds for the model to load",
                    "Then run detection again",
                ],
            }

        index = preferred_index if preferred_index is not None else 0
        connect_result = connection_manager.connect(index)
        if not connect_result.get("success"):
            return connect_result

        connection_state.set_connection_manager(connection_manager)
        connection_state.initialize_managers()

        return {
            "success": True,
            "connected": True,
            "instance_count": len(instances),
            "selected_index": index,
            "selected_port": instances[index].get("port") if index < len(instances) else None,
        }
