"""Shared ``_get_server_db_model`` for the live-TOM CRUD managers.

Twelve managers each carried an identical private wrapper around
:func:`core.operations.amo_helpers.get_server_db_model`. The wrapper is now
inherited from one of the two mixins below (deep review C-16). Both keep the
lazy import so merely importing a manager does not load the AMO assemblies, and
both resolve ``amo_helpers.get_server_db_model`` at call time, so the offline
lane / XMLA target dispatch inside it and test monkeypatches of it still apply.
"""

from typing import Any, Optional, Tuple


class AmoModelMixin:
    """``_get_server_db_model() -> (server, db, model)``; the AMO error goes to
    ``self._last_amo_error`` so the connection-error response can surface it."""

    _last_amo_error: Optional[str] = None

    def _get_server_db_model(self) -> Tuple[Any, Any, Any]:
        from core.infrastructure.connection_state import connection_state
        from core.operations import amo_helpers

        server, db, model, err = amo_helpers.get_server_db_model(connection_state)
        self._last_amo_error = err
        return server, db, model


class AmoSessionMixin:
    """``_get_server_db_model() -> (server, db, model, error)`` from
    ``self.connection_state`` (managers constructed with an explicit state)."""

    def _get_server_db_model(self) -> Tuple[Any, Any, Any, Optional[str]]:
        from core.operations import amo_helpers

        return amo_helpers.get_server_db_model(self.connection_state)
