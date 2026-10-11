"""
Base class for unified operation handlers
Provides common patterns for operation routing and validation
"""

from typing import Dict, Any, List, Callable
import logging

logger = logging.getLogger(__name__)


class BaseOperationsHandler:
    """Base class for unified operation handlers"""

    def __init__(self, operation_name: str):
        self.operation_name = operation_name
        self._operations: Dict[str, Callable] = {}

    def register_operation(self, operation: str, handler: Callable):
        """Register an operation handler"""
        self._operations[operation] = handler
        logger.debug(f"{self.operation_name}: Registered operation '{operation}'")

    def execute(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an operation based on args"""
        operation = args.get("operation")

        if not operation:
            return {
                "success": False,
                "error": "operation parameter is required",
                "available_operations": list(self._operations.keys()),
            }

        if operation not in self._operations:
            return {
                "success": False,
                "error": f"Unknown operation: {operation}",
                "available_operations": list(self._operations.keys()),
            }

        handler = self._operations[operation]

        try:
            logger.info(f"{self.operation_name}: Executing operation '{operation}'")
            result = handler(args)
            logger.info(f"{self.operation_name}: Operation '{operation}' completed successfully")
            return result
        except Exception as e:
            logger.error(
                f"{self.operation_name}: Operation '{operation}' failed: {e}", exc_info=True
            )
            return {
                "success": False,
                "error": f"Operation failed: {str(e)}",
                "operation": operation,
            }

    def get_available_operations(self) -> List[str]:
        """Get list of available operations"""
        return list(self._operations.keys())
