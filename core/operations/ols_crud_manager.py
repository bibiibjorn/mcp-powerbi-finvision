"""OLS CRUD Manager — manage Object-Level Security rules via TOM.

Acquires the live model through the shared ``amo_helpers.get_server_db_model``
pattern (server + db + model, caller disconnects in ``finally``) — the same
convention every other CRUD manager uses. A prior version read a nonexistent
``connection_state.model`` attribute and raised ``AttributeError`` on every call.
"""

import logging
from typing import Any, Dict
from core.operations.amo_mixins import AmoModelMixin
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


class OlsCrudManager(AmoModelMixin):
    """CRUD operations for Object-Level Security rules."""

    def __init__(self):
        self._last_amo_error = None

    def _connection_error_msg(self) -> str:
        cause = self._last_amo_error or "unknown cause"
        return f"Could not connect to model: {cause}"

    def list_ols_rules(self, role_name: str = None) -> Dict[str, Any]:
        """List OLS rules, optionally filtered by role."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            rules = []
            roles = [model.Roles.Find(role_name)] if role_name else model.Roles
            for role in roles:
                if role is None:
                    return {"success": False, "error": f"Role '{role_name}' not found"}
                for tp in role.TablePermissions:
                    table_perm = str(getattr(tp, "MetadataPermission", "Default"))
                    if table_perm.split(".")[-1] not in ("Default", ""):
                        rules.append(
                            {
                                "role": role.Name,
                                "table": tp.Name,
                                "column": None,
                                "scope": "table",
                                "metadata_permission": table_perm,
                            }
                        )
                    for cp in tp.ColumnPermissions:
                        rules.append(
                            {
                                "role": role.Name,
                                "table": tp.Name,
                                "column": cp.Name,
                                "metadata_permission": str(cp.MetadataPermission),
                            }
                        )
            return {"success": True, "ols_rules": rules, "count": len(rules)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    _PERMISSIONS = ("None", "Read", "Default")

    @staticmethod
    def _resolve_permission(TOM, permission: str):
        """MetadataPermission enum member (case-insensitive) or None.

        C-15: pythonnet 3 exposes the ``None`` member ONLY via
        ``getattr(MetadataPermission, "None")`` — ``MetadataPermission.None_``
        raised AttributeError, so every ``set`` call failed.
        """
        key = str(permission or "").strip().lower()
        for name in OlsCrudManager._PERMISSIONS:
            if name.lower() == key:
                return getattr(TOM.MetadataPermission, name)
        return None

    def set_ols_rule(
        self, role_name: str, table_name: str, column_name: str = None, permission: str = "None"
    ) -> Dict[str, Any]:
        """Set OLS permission. permission: 'None' (block), 'Read', 'Default'.

        With ``column_name`` the permission applies to that column; without it
        it is TABLE-level OLS (``TablePermission.MetadataPermission``).
        """
        if str(permission or "").strip().lower() not in {p.lower() for p in self._PERMISSIONS}:
            return error_envelope(
                "invalid_parameters", f"Invalid permission: {permission}. Use: None, Read, Default"
            )
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            import Microsoft.AnalysisServices.Tabular as TOM

            perm = self._resolve_permission(TOM, permission)
            if perm is None:
                return error_envelope(
                    "invalid_parameters",
                    f"Invalid permission: {permission}. Use: None, Read, Default",
                )
            role = model.Roles.Find(role_name)
            if not role:
                return {"success": False, "error": f"Role '{role_name}' not found"}
            table = model.Tables.Find(table_name)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            col = None
            if column_name:
                col = table.Columns.Find(column_name)
                if not col:
                    return {
                        "success": False,
                        "error": f"Column '{column_name}' not found in '{table_name}'",
                    }

            tp = role.TablePermissions.Find(table_name)
            if not tp:
                tp = TOM.TablePermission()
                tp.Table = table
                role.TablePermissions.Add(tp)
            if col is None:
                tp.MetadataPermission = perm
                scope = "table"
            else:
                cp = tp.ColumnPermissions.Find(column_name)
                if not cp:
                    cp = TOM.ColumnPermission()
                    cp.Column = col
                    tp.ColumnPermissions.Add(cp)
                cp.MetadataPermission = perm
                scope = "column"
            from core.operations.write_lane import commit

            info = commit(model, label="ols_rule/set")
            target = f"{table_name}/{column_name}" if column_name else table_name
            return {
                "success": True,
                "scope": scope,
                "message": f"OLS set: {role_name}/{target} = {permission}",
                **info,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def remove_ols_rule(self, role_name: str, table_name: str, column_name: str) -> Dict[str, Any]:
        """Remove OLS permission from a column."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            role = model.Roles.Find(role_name)
            if not role:
                return {"success": False, "error": f"Role '{role_name}' not found"}
            tp = role.TablePermissions.Find(table_name)
            if not tp:
                return {
                    "success": False,
                    "error": f"No table permission for '{table_name}' in role '{role_name}'",
                }
            cp = tp.ColumnPermissions.Find(column_name)
            if not cp:
                return {
                    "success": False,
                    "error": f"No OLS rule for '{column_name}' in '{table_name}'",
                }
            tp.ColumnPermissions.Remove(cp)
            from core.operations.write_lane import commit

            commit(model, label="ols_rule/remove")
            return {
                "success": True,
                "message": f"OLS removed: {role_name}/{table_name}/{column_name}",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)
