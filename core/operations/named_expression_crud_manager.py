"""Named Expression CRUD Manager — manage Power Query parameters/expressions via TOM.

Acquires the live model through the shared ``amo_helpers.get_server_db_model``
pattern (server + db + model, caller disconnects in ``finally``) — the same
convention every other CRUD manager uses. A prior version read a nonexistent
``connection_state.model`` attribute and raised ``AttributeError`` on every call.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

from core.operations.write_lane import commit as _lane_commit  # noqa: E402
from core.operations.amo_mixins import AmoModelMixin  # noqa: E402


class NamedExpressionCrudManager(AmoModelMixin):
    """CRUD operations for model named expressions (Power Query parameters)."""

    def __init__(self):
        self._last_amo_error = None

    def _connection_error_msg(self) -> str:
        cause = self._last_amo_error or "unknown cause"
        return f"Could not connect to model: {cause}"

    def list_expressions(self) -> Dict[str, Any]:
        """List all named expressions in the model."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            expressions = []
            for expr in model.Expressions:
                expressions.append(
                    {
                        "name": expr.Name,
                        "kind": str(expr.Kind) if hasattr(expr, "Kind") else "M",
                        "description": expr.Description or "",
                    }
                )
            return {"success": True, "expressions": expressions, "count": len(expressions)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def describe_expression(self, expression_name: str) -> Dict[str, Any]:
        """Get detailed info about a named expression."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            expr = model.Expressions.Find(expression_name)
            if not expr:
                return {"success": False, "error": f"Expression '{expression_name}' not found"}
            return {
                "success": True,
                "expression": {
                    "name": expr.Name,
                    "kind": str(expr.Kind) if hasattr(expr, "Kind") else "M",
                    "expression": expr.Expression,
                    "description": expr.Description or "",
                },
            }
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def create_expression(self, expression_name: str, expression: str, **kwargs) -> Dict[str, Any]:
        """Create a new named expression."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            import Microsoft.AnalysisServices.Tabular as TOM

            ne = TOM.NamedExpression()
            ne.Name = expression_name
            ne.Expression = expression
            ne.Kind = TOM.ExpressionKind.M
            if kwargs.get("description"):
                ne.Description = kwargs["description"]
            model.Expressions.Add(ne)
            _lane_commit(model, label="named_expression")
            return {"success": True, "message": f"Expression '{expression_name}' created"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def update_expression(
        self, expression_name: str, expression: str = None, **kwargs
    ) -> Dict[str, Any]:
        """Update a named expression."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            expr = model.Expressions.Find(expression_name)
            if not expr:
                return {"success": False, "error": f"Expression '{expression_name}' not found"}
            if expression is not None:
                expr.Expression = expression
            if kwargs.get("description") is not None:
                expr.Description = kwargs["description"]
            _lane_commit(model, label="named_expression")
            return {"success": True, "message": f"Expression '{expression_name}' updated"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def delete_expression(self, expression_name: str) -> Dict[str, Any]:
        """Delete a named expression."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            expr = model.Expressions.Find(expression_name)
            if not expr:
                return {"success": False, "error": f"Expression '{expression_name}' not found"}
            model.Expressions.Remove(expr)
            _lane_commit(model, label="named_expression")
            return {"success": True, "message": f"Expression '{expression_name}' deleted"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)
