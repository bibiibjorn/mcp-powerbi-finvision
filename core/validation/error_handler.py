"""
Error handling utilities for PBIXRay MCP Server
"""

import functools
import logging
from typing import Any, Dict, Callable
from core.validation.error_response import ErrorResponse

logger = logging.getLogger(__name__)


def safe_tool_execution(fallback_error: str = "Tool not available"):
    """
    Decorator to handle common tool execution patterns with proper error handling.

    Args:
        fallback_error: Default error message when tool is not available
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            try:
                result = func(*args, **kwargs)

                # Ensure result is properly formatted
                if not isinstance(result, dict):
                    return {"success": True, "data": result}

                return result

            except ConnectionError as e:
                logger.error(f"Connection error in {func.__name__}: {e}")
                return ErrorResponse(
                    error=f"Connection error: {str(e)}",
                    error_type="connection_error",
                    suggestions=[
                        "Check Power BI Desktop connection",
                        "Verify instance is still running",
                    ],
                ).to_dict()
            except ValueError as e:
                logger.error(f"Value error in {func.__name__}: {e}")
                return ErrorResponse(
                    error=f"Invalid parameter: {str(e)}", error_type="parameter_error"
                ).to_dict()
            except Exception as e:
                logger.error(f"Unexpected error in {func.__name__}: {e}", exc_info=True)
                return ErrorResponse(
                    error=f"Unexpected error: {str(e)}",
                    error_type="unexpected_error",
                    context={"tool_name": func.__name__},
                ).to_dict()

        return wrapper

    return decorator


def require_connection(func: Callable) -> Callable:
    """
    Decorator to ensure a Power BI connection is active before executing a tool.
    """

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if not hasattr(self, "connection_manager") or not self.connection_manager.is_connected():
            return ErrorResponse(
                error="Not connected to Power BI Desktop",
                error_type="not_connected",
                suggestions=[
                    "Run 01_Connection operation='ensure' to connect (or reconnect)",
                    "Run 01_Connection operation='detect' to list running instances",
                ],
            ).to_dict()

        return func(self, *args, **kwargs)

    return wrapper


def requires_connection(fn: Callable) -> Callable:
    """Gate a method on connection_state.is_connected().

    Finds connection_state by scanning positional args for an object that has
    an ``is_connected`` attribute (duck-typed). Returns
    ``ErrorHandler.handle_not_connected()`` when absent or not connected.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        conn = next((a for a in args if hasattr(a, "is_connected")), None)
        if conn is None or not conn.is_connected():
            return ErrorHandler.handle_not_connected()
        return fn(*args, **kwargs)

    return wrapper


class ErrorHandler:
    """
    Centralized error handling for common patterns.
    """

    @staticmethod
    def handle_manager_unavailable(manager_name: str) -> Dict[str, Any]:
        """Standard response when a manager is not available."""
        # Map manager names to user-friendly descriptions and recovery steps
        manager_details = {
            "connection_manager": {
                "description": "Connection Manager handles Power BI Desktop connections",
                "likely_cause": "Server initialization incomplete or connection not established",
                "recovery_steps": [
                    "1. Run 01_Connection operation='reconnect'",
                    "2. Restart the MCP server",
                    "3. Check if Power BI Desktop is still running",
                ],
            },
            "query_executor": {
                "description": "Query Executor runs DAX queries and DMV queries",
                "likely_cause": "Connection to Power BI not established or connection lost",
                "recovery_steps": [
                    "1. Run 01_Connection operation='ensure' to establish the connection",
                    "2. Verify Power BI Desktop is responsive",
                    "3. Check if the model is still loaded",
                ],
            },
            "agent_policy": {
                "description": "Agent Policy manages tool execution policies and limits",
                "likely_cause": "Server not fully initialized",
                "recovery_steps": [
                    "1. Reconnect to Power BI Desktop",
                    "2. Restart the MCP server if issue persists",
                ],
            },
        }

        details = manager_details.get(
            manager_name,
            {
                "description": f"{manager_name} is required for this operation",
                "likely_cause": "Component not initialized",
                "recovery_steps": ["Ensure connection is established"],
            },
        )

        try:
            from core.infrastructure import offline_model

            offline = offline_model.is_active()
        except Exception:  # noqa: BLE001 - the generic message still applies
            offline = False
        if offline and manager_name == "query_executor":
            return ErrorResponse(
                error=(
                    "This operation needs the Analysis Services engine (DAX / DMV), and the "
                    "offline PBIP lane (01_Connection connect_folder) has none."
                ),
                error_type="offline_no_engine",
                suggestions=[
                    "Reads that come from TOM (model get_properties) and model writes work offline.",
                    "For lists, DAX and traces, open the project in Desktop and run 01_Connection ensure.",
                ],
                context={"required_manager": manager_name, "target": "offline_folder"},
            ).to_dict()

        return ErrorResponse(
            error=f"{manager_name} not available",
            error_type="manager_unavailable",
            suggestions=[
                "Ensure connection to Power BI Desktop is established",
                "Check if all required services are initialized",
            ],
            context={
                "required_manager": manager_name,
                "description": details["description"],
                "likely_cause": details["likely_cause"],
                "recovery_steps": details["recovery_steps"],
            },
        ).to_dict()

    @staticmethod
    def handle_not_connected() -> Dict[str, Any]:
        """Standard response when there is no active connection.

        A-19: names the REGISTERED tool (01_Connection) — the v6 names
        detect_powerbi_desktop / connect_to_powerbi no longer exist.
        """
        return ErrorResponse(
            error="Not connected to Power BI Desktop",
            error_type="not_connected",
            suggestions=[
                "Run 01_Connection operation='ensure' (idempotent: connects, or reconnects)",
                "Run 01_Connection operation='detect' to list running instances first",
            ],
            context={
                "connection_workflow": {
                    "step1": {
                        "tool": "01_Connection",
                        "args": {"operation": "detect"},
                        "description": "Optional: list running Power BI Desktop instances",
                        "required": False,
                    },
                    "step2": {
                        "tool": "01_Connection",
                        "args": {"operation": "ensure"},
                        "description": (
                            "Connect (auto-detects; pass model_index / file_path to pick "
                            "one of several instances)"
                        ),
                        "required": True,
                    },
                },
                "common_causes": [
                    "No Power BI Desktop instances are running",
                    "Connection was lost or timed out",
                    "Never connected since server started",
                ],
                "troubleshooting": {
                    "if_no_instances": "Open a .pbix / .pbip file in Power BI Desktop",
                    "if_connection_lost": "Run 01_Connection operation='ensure'",
                    "if_still_fails": "Check Power BI Desktop is responsive and the model is loaded",
                },
            },
        ).to_dict()

    @staticmethod
    def handle_connection_error(error: Exception) -> Dict[str, Any]:
        """Standard response for connection errors."""
        error_str = str(error).lower()

        # Provide context-specific guidance based on error message
        context = {"error_details": str(error), "recovery_steps": []}

        if "timeout" in error_str:
            context["likely_cause"] = "Power BI Desktop is not responding or is busy"
            context["recovery_steps"] = [
                "1. Check if Power BI Desktop is frozen or busy",
                "2. Wait for any running operations to complete",
                "3. Try reconnecting after a few seconds",
                "4. If issue persists, restart Power BI Desktop",
            ]
        elif "refused" in error_str or "could not connect" in error_str:
            context["likely_cause"] = "Power BI Desktop instance is not available"
            context["recovery_steps"] = [
                "1. Verify Power BI Desktop is still running",
                "2. Check that the model is still open",
                "3. Run 01_Connection operation='detect' to find current instances",
                "4. Reconnect with 01_Connection operation='ensure'",
            ]
        elif "authentication" in error_str or "unauthorized" in error_str:
            context["likely_cause"] = "Authentication or permission issue"
            context["recovery_steps"] = [
                "1. Check Windows permissions",
                "2. Verify you have access to the model",
                "3. Try running as administrator if needed",
            ]
        else:
            context["likely_cause"] = "Unknown connection issue"
            context["recovery_steps"] = [
                "1. Verify Power BI Desktop is running",
                "2. Check network connectivity",
                "3. Try reconnecting to the instance",
                "4. Check server logs for more details",
            ]

        return ErrorResponse(
            error=f"Connection error: {str(error)}",
            error_type="connection_error",
            suggestions=[
                "Verify Power BI Desktop is running",
                "Check network connectivity",
                "Try reconnecting to the instance",
            ],
            context=context,
        ).to_dict()

    @staticmethod
    def handle_validation_error(error: Exception) -> Dict[str, Any]:
        """Standard response for validation errors."""
        return ErrorResponse(
            error=f"Validation error: {str(error)}", error_type="validation_error"
        ).to_dict()

    @staticmethod
    def handle_unexpected_error(tool_name: str, error: Exception) -> Dict[str, Any]:
        """Standard response for unexpected errors with tool context."""

        error_str = str(error).lower()
        error_type_name = type(error).__name__

        # Provide helpful context based on error type
        context = {
            "tool_name": tool_name,
            "error_type": error_type_name,
            "error_message": str(error),
        }

        # Add specific guidance for common error patterns
        if "keyerror" in error_type_name.lower():
            context["likely_cause"] = "Missing required data field or parameter"
            context["suggestion"] = "This may indicate a data structure mismatch or API change"
        elif "attributeerror" in error_type_name.lower():
            context["likely_cause"] = "Object method or property not available"
            context["suggestion"] = "This may indicate an API version mismatch"
        elif "typeerror" in error_type_name.lower():
            context["likely_cause"] = "Incorrect data type or parameter format"
            context["suggestion"] = "Check parameter types match expected values"
        elif "valueerror" in error_type_name.lower():
            context["likely_cause"] = "Invalid parameter value"
            context["suggestion"] = "Check parameter values are within valid ranges"
        elif "memory" in error_str or "out of memory" in error_str:
            context["likely_cause"] = "Insufficient memory"
            context["suggestion"] = "Try with smaller data sets or use pagination"
            context["recovery_steps"] = [
                "1. Use limit or page_size parameters to reduce data",
                "2. Close other applications to free memory",
                "3. Consider exporting to file instead of returning large results",
            ]
        else:
            context["likely_cause"] = "Unexpected internal error"
            context["suggestion"] = "This is likely a bug - please report with details"

        # Add troubleshooting info
        context["troubleshooting"] = {
            "check_logs": "Review server logs for detailed stack trace",
            "retry": (
                "Reads can be retried. For a write, VERIFY the current model/report "
                "state first - the operation may have partially applied"
            ),
            "report": "If issue persists, report as a bug with error details",
        }

        # Unwrap a CLR exception chain (pythonnet TargetInvocationException /
        # AggregateException) so the REAL root cause surfaces instead of the
        # opaque wrapper message ("Exception has been thrown by the target of
        # an invocation").
        error_message = str(error)
        try:
            from core.autonomous.clr_errors import extract_clr_exception_chain

            chain = extract_clr_exception_chain(error)
            if chain and len(chain) > 1:
                context["clr_chain"] = chain
                root = chain[-1]
                root_msg = str(root.get("message", "")).strip()
                if root_msg:
                    context["clr_root_cause"] = root_msg
                    context["clr_root_type"] = root.get("type")
                    error_message = f"{root.get('type', 'error')}: {root_msg}"
        except Exception:  # noqa: BLE001 - unwrapping must never mask the error
            pass

        return ErrorResponse(
            error=f"Unexpected error in {tool_name}: {error_message}",
            error_type="unexpected_error",
            suggestions=[
                "Check server logs for detailed error information",
                "Verify state before retrying a write (it may have partially applied)",
                "Report persistent issues as bugs",
            ],
            context=context,
        ).to_dict()

    @staticmethod
    def wrap_result(data: Any, success: bool = True) -> Dict[str, Any]:
        """Wrap raw data in standard response format."""
        if isinstance(data, dict) and "success" in data:
            return data

        return {"success": success, "data": data}
