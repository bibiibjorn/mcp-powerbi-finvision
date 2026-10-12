"""
Manager decorators for connection and manager validation.

These decorators eliminate duplicate connection/manager check patterns across operations.
Reduces ~200 lines of duplicated code.
"""

import functools
import logging
from typing import Any, Callable, Dict, Optional, Union

from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler

logger = logging.getLogger(__name__)


def require_connection(func: Callable) -> Callable:
    """Decorator to ensure a Power BI connection is active before executing an operation."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Dict[str, Any]:
        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()
        return func(*args, **kwargs)

    return wrapper


def require_manager(manager_attr: str, manager_display_name: Optional[str] = None) -> Callable:
    """Decorator to check connection, get manager from connection_state, and inject it as 'manager' kwarg."""
    display_name = manager_display_name or manager_attr

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            # Check connection first
            if not connection_state.is_connected():
                return ErrorHandler.handle_not_connected()

            # Get manager from connection state
            manager = getattr(connection_state, manager_attr, None)
            if not manager:
                return ErrorHandler.handle_manager_unavailable(display_name)

            # Inject manager as keyword argument
            kwargs["manager"] = manager
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_managers(*manager_specs: Union[str, tuple]) -> Callable:
    """Decorator to check connection and inject multiple managers. Spec is str or (attr, injection_name) tuple."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            # Check connection first
            if not connection_state.is_connected():
                return ErrorHandler.handle_not_connected()

            # Check each manager and inject
            for spec in manager_specs:
                if isinstance(spec, tuple):
                    manager_attr, injection_name = spec
                else:
                    manager_attr = spec
                    injection_name = spec

                manager = getattr(connection_state, manager_attr, None)
                if not manager:
                    return ErrorHandler.handle_manager_unavailable(manager_attr)

                kwargs[injection_name] = manager

            return func(*args, **kwargs)

        return wrapper

    return decorator


class ManagerContext:
    """Context manager for accessing connection state managers with validation. Returns None on failure; check error_result."""

    def __init__(self, manager_attr: str, manager_display_name: Optional[str] = None):
        self.manager_attr = manager_attr
        self.manager_display_name = manager_display_name or manager_attr
        self.manager = None
        self.error_result = None

    def __enter__(self):
        if not connection_state.is_connected():
            self.error_result = ErrorHandler.handle_not_connected()
            return None

        self.manager = getattr(connection_state, self.manager_attr, None)
        if not self.manager:
            self.error_result = ErrorHandler.handle_manager_unavailable(self.manager_display_name)
            return None

        return self.manager

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.manager = None
        return False  # Don't suppress exceptions

    @staticmethod
    def get(
        manager_attr: str, manager_display_name: Optional[str] = None
    ) -> Union[Any, Dict[str, Any]]:
        """Get a manager with validation; returns error dict if not connected or manager unavailable."""
        display_name = manager_display_name or manager_attr

        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()

        manager = getattr(connection_state, manager_attr, None)
        if not manager:
            return ErrorHandler.handle_manager_unavailable(display_name)

        return manager


def get_manager_or_error(
    manager_attr: str, manager_display_name: Optional[str] = None
) -> Union[Any, Dict[str, Any]]:
    """Helper function to get a manager with connection validation. Returns manager or error dict."""
    return ManagerContext.get(manager_attr, manager_display_name)


def check_connection_and_manager(
    manager_attr: str, manager_display_name: Optional[str] = None
) -> tuple[bool, Optional[Any], Optional[Dict[str, Any]]]:
    """Check connection and manager; returns (True, manager, None) on success or (False, None, error_dict) on failure."""
    display_name = manager_display_name or manager_attr

    if not connection_state.is_connected():
        return (False, None, ErrorHandler.handle_not_connected())

    manager = getattr(connection_state, manager_attr, None)
    if not manager:
        return (False, None, ErrorHandler.handle_manager_unavailable(display_name))

    return (True, manager, None)
