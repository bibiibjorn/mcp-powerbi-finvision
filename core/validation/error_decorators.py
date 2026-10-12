"""
Error handling decorators for consistent error handling.

These decorators consolidate duplicate try/except patterns.
Reduces ~90 lines of duplicated code.
"""

import functools
import logging
from typing import Any, Callable, Dict, Optional

from core.validation.error_handler import ErrorHandler

logger = logging.getLogger(__name__)


def handle_operation_errors(operation_name: str) -> Callable:
    """Decorator to handle common error patterns with logging.

    Wraps the function in a try/except, logs errors, and returns proper error responses.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {operation_name}: {e}", exc_info=True)
                return ErrorHandler.handle_unexpected_error(operation_name, e)

        return wrapper

    return decorator


def handle_query_errors(query_type: str) -> Callable:
    """Decorator for query execution with detailed error handling (ConnectionError, ValueError, Exception)."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            try:
                return func(*args, **kwargs)
            except ConnectionError as e:
                logger.error(f"Connection error during {query_type} query: {e}")
                return ErrorHandler.handle_connection_error(e)
            except ValueError as e:
                logger.error(f"Value error during {query_type} query: {e}")
                return ErrorHandler.handle_validation_error(e)
            except Exception as e:
                logger.error(f"Error executing {query_type} query: {e}", exc_info=True)
                return ErrorHandler.handle_unexpected_error(f"{query_type}_query", e)

        return wrapper

    return decorator


def log_operation(operation_name: str, log_args: bool = False) -> Callable:
    """Decorator to log operation entry/exit. Set log_args=True to include call args (avoid with sensitive data)."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            if log_args:
                # Try to get args from kwargs or positional args
                call_args = kwargs.get("args", args[1] if len(args) > 1 else {})
                logger.info(f"[{operation_name}] Starting with args: {call_args}")
            else:
                logger.info(f"[{operation_name}] Starting")

            try:
                result = func(*args, **kwargs)
                success = isinstance(result, dict) and result.get("success", True)
                if success:
                    logger.info(f"[{operation_name}] Completed successfully")
                else:
                    error = (
                        result.get("error", "Unknown error")
                        if isinstance(result, dict)
                        else "Unknown error"
                    )
                    logger.warning(f"[{operation_name}] Completed with error: {error}")
                return result
            except Exception as e:
                logger.error(f"[{operation_name}] Failed with exception: {e}", exc_info=True)
                raise

        return wrapper

    return decorator


def with_error_context(context: Dict[str, Any]) -> Callable:
    """Decorator to inject additional context fields into error responses."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            try:
                result = func(*args, **kwargs)

                # If result is an error, add context
                if isinstance(result, dict) and not result.get("success", True):
                    if "context" not in result:
                        result["context"] = {}
                    result["context"].update(context)

                return result
            except Exception as e:
                error_result = ErrorHandler.handle_unexpected_error(func.__name__, e)
                if "context" not in error_result:
                    error_result["context"] = {}
                error_result["context"].update(context)
                return error_result

        return wrapper

    return decorator


def retry_on_connection_error(max_retries: int = 1, delay_seconds: float = 0.5) -> Callable:
    """Decorator to retry operations on ConnectionError up to max_retries times."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            import time

            last_error = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except ConnectionError as e:
                    last_error = e
                    if attempt < max_retries:
                        logger.warning(
                            f"Connection error in {func.__name__}, retrying ({attempt + 1}/{max_retries})..."
                        )
                        time.sleep(delay_seconds)
                    else:
                        logger.error(
                            f"Connection error in {func.__name__} after {max_retries} retries: {e}"
                        )

            return ErrorHandler.handle_connection_error(last_error)

        return wrapper

    return decorator


def wrap_result(include_success: bool = True) -> Callable:
    """Decorator to ensure result is always in standard dict format with 'success' key."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            result = func(*args, **kwargs)

            if isinstance(result, dict) and "success" in result:
                return result

            if include_success:
                return {"success": True, "data": result}
            else:
                return result if isinstance(result, dict) else {"data": result}

        return wrapper

    return decorator


class OperationErrorHandler:
    """Context manager for operation error handling. Suppresses exceptions and stores them as error_response."""

    def __init__(self, operation_name: str):
        self.operation_name = operation_name
        self.error_response: Optional[Dict[str, Any]] = None

    def __enter__(self):
        logger.debug(f"Starting operation: {self.operation_name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            logger.error(f"Error in {self.operation_name}: {exc_val}", exc_info=True)
            self.error_response = ErrorHandler.handle_unexpected_error(self.operation_name, exc_val)
            return True  # Suppress the exception, error_response is set
        return False


def combine_decorators(*decorators: Callable) -> Callable:
    """Utility to combine multiple decorators into one (applied bottom-up)."""

    def decorator(func: Callable) -> Callable:
        for dec in reversed(decorators):
            func = dec(func)
        return func

    return decorator
