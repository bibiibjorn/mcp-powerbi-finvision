"""RLS Role CRUD Manager — create/update/delete security roles, table filters, and members via TOM."""

import logging
from typing import Any, Dict, List, Optional

from core.operations.amo_helpers import get_server_db_model, safe_disconnect

logger = logging.getLogger(__name__)

from core.operations.write_lane import commit as _lane_commit  # noqa: E402

try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    if load_amo_assemblies():
        from Microsoft.AnalysisServices.Tabular import (
            ModelRole,
            ModelPermission,
            TablePermission,
            ModelRoleMember,
            ExternalModelRoleMember,
            WindowsModelRoleMember,
        )
    else:
        ModelRole = None
        ModelPermission = None
        TablePermission = None
        ModelRoleMember = None
        ExternalModelRoleMember = None
        WindowsModelRoleMember = None
except Exception as e:
    logger.debug(f"AMO not loaded: {e}")
    ModelRole = None
    ModelPermission = None
    TablePermission = None
    ModelRoleMember = None
    ExternalModelRoleMember = None
    WindowsModelRoleMember = None


_PERMISSION_MAP = {
    "none": "None",
    "read": "Read",
    "readrefresh": "ReadRefresh",
    "refresh": "Refresh",
    "administrator": "Administrator",
}


def _resolve_permission(value: str):
    """Parse permission string to ModelPermission enum (case-insensitive).

    C-25: an unknown value (e.g. "admin") used to fall back to Read silently;
    it now raises ValueError, which create/update report as invalid_parameters.
    """
    if ModelPermission is None:
        return None
    key = (value or "read").strip().lower()
    name = _PERMISSION_MAP.get(key)
    if name is None:
        raise ValueError(
            f"Invalid model permission {value!r}. Valid: None, Read, ReadRefresh, Refresh, Administrator"
        )
    return getattr(ModelPermission, name)


class RlsRoleCrudManager:
    """CRUD for model roles, table permissions (filters), and role members."""

    def __init__(self):
        self._connection_state = None

    @property
    def connection_state(self):
        if self._connection_state is None:
            from core.infrastructure.connection_state import connection_state

            self._connection_state = connection_state
        return self._connection_state

    def create_role(
        self,
        role_name: str,
        model_permission: str = "Read",
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new role."""
        if ModelRole is None:
            return {"success": False, "error": "AMO not available"}
        if not role_name:
            return {"success": False, "error": "role_name is required"}
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            if model.Roles.Find(role_name) is not None:
                return {"success": False, "error": f"Role '{role_name}' already exists"}
            role = ModelRole()
            role.Name = role_name
            role.ModelPermission = _resolve_permission(model_permission)
            if description:
                role.Description = description
            model.Roles.Add(role)
            _lane_commit(model, label="role")
            return {
                "success": True,
                "message": f"Created role '{role_name}'",
                "role": role_name,
                "model_permission": model_permission,
            }
        except ValueError as e:
            return {"success": False, "error": str(e), "error_type": "invalid_parameters"}
        except Exception as e:
            return {"success": False, "error": f"create_role failed: {e}"}
        finally:
            safe_disconnect(server)

    def update_role(
        self,
        role_name: str,
        new_name: Optional[str] = None,
        model_permission: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Rename and/or update permission/description."""
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            role = model.Roles.Find(role_name)
            if role is None:
                return {"success": False, "error": f"Role '{role_name}' not found"}
            updates = []
            perm = _resolve_permission(model_permission) if model_permission is not None else None
            if new_name and new_name != role_name:
                role.RequestRename(new_name)
                updates.append(f"renamed to '{new_name}'")
            if model_permission is not None:
                role.ModelPermission = perm
                updates.append(f"permission={model_permission}")
            if description is not None:
                role.Description = description
                updates.append("description")
            _lane_commit(model, label="role")
            return {
                "success": True,
                "message": f"Updated role '{role_name}': {', '.join(updates) or 'no changes'}",
                "role": new_name or role_name,
            }
        except ValueError as e:
            return {"success": False, "error": str(e), "error_type": "invalid_parameters"}
        except Exception as e:
            return {"success": False, "error": f"update_role failed: {e}"}
        finally:
            safe_disconnect(server)

    def delete_role(self, role_name: str) -> Dict[str, Any]:
        """Delete a role and all its table permissions + members."""
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            role = model.Roles.Find(role_name)
            if role is None:
                return {"success": False, "error": f"Role '{role_name}' not found"}
            model.Roles.Remove(role)
            _lane_commit(model, label="role")
            return {"success": True, "message": f"Deleted role '{role_name}'"}
        except Exception as e:
            return {"success": False, "error": f"delete_role failed: {e}"}
        finally:
            safe_disconnect(server)

    def set_table_filter(
        self,
        role_name: str,
        table_name: str,
        filter_expression: str,
    ) -> Dict[str, Any]:
        """Set or update a table's RLS filter expression on a role.

        Passing an empty filter_expression is not allowed — use clear_table_filter instead.
        """
        if TablePermission is None:
            return {"success": False, "error": "AMO not available"}
        if not filter_expression:
            return {
                "success": False,
                "error": "filter_expression is required; use clear_table_filter to remove",
            }
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            role = model.Roles.Find(role_name)
            if role is None:
                return {"success": False, "error": f"Role '{role_name}' not found"}
            table = model.Tables.Find(table_name)
            if table is None:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            perm = role.TablePermissions.Find(table_name)
            created = False
            if perm is None:
                perm = TablePermission()
                perm.Table = table
                role.TablePermissions.Add(perm)
                created = True
            perm.FilterExpression = filter_expression
            _lane_commit(model, label="role")
            return {
                "success": True,
                "message": f"{'Created' if created else 'Updated'} filter on '{table_name}' for role '{role_name}'",
                "role": role_name,
                "table": table_name,
                "filter_expression": filter_expression,
            }
        except Exception as e:
            return {"success": False, "error": f"set_table_filter failed: {e}"}
        finally:
            safe_disconnect(server)

    def clear_table_filter(self, role_name: str, table_name: str) -> Dict[str, Any]:
        """Remove a role's RLS filter on a table (drops the TablePermission)."""
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            role = model.Roles.Find(role_name)
            if role is None:
                return {"success": False, "error": f"Role '{role_name}' not found"}
            perm = role.TablePermissions.Find(table_name)
            if perm is None:
                return {
                    "success": False,
                    "error": f"No permission for '{table_name}' on role '{role_name}'",
                }
            role.TablePermissions.Remove(perm)
            _lane_commit(model, label="role")
            return {
                "success": True,
                "message": f"Cleared filter on '{table_name}' for role '{role_name}'",
            }
        except Exception as e:
            return {"success": False, "error": f"clear_table_filter failed: {e}"}
        finally:
            safe_disconnect(server)

    def add_member(
        self,
        role_name: str,
        member_identifier: str,
        member_type: str = "external",
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a member to a role.

        member_type: 'external' (default — UPN or AAD object ID) or 'windows' (domain\\user).
        """
        if ModelRoleMember is None:
            return {"success": False, "error": "AMO not available"}
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            role = model.Roles.Find(role_name)
            if role is None:
                return {"success": False, "error": f"Role '{role_name}' not found"}
            mtype = (member_type or "external").strip().lower()
            if mtype == "windows":
                member = WindowsModelRoleMember()
            else:
                member = ExternalModelRoleMember()
                if tenant_id:
                    member.IdentityProvider = "AzureAD"
                    member.TenantId = tenant_id
            member.MemberName = member_identifier
            role.Members.Add(member)
            _lane_commit(model, label="role")
            return {
                "success": True,
                "message": f"Added {mtype} member '{member_identifier}' to '{role_name}'",
            }
        except Exception as e:
            return {"success": False, "error": f"add_member failed: {e}"}
        finally:
            safe_disconnect(server)

    def remove_member(self, role_name: str, member_identifier: str) -> Dict[str, Any]:
        """Remove a member from a role by MemberName match."""
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            role = model.Roles.Find(role_name)
            if role is None:
                return {"success": False, "error": f"Role '{role_name}' not found"}
            target = None
            for m in role.Members:
                if str(m.MemberName) == member_identifier:
                    target = m
                    break
            if target is None:
                return {
                    "success": False,
                    "error": f"Member '{member_identifier}' not found on role '{role_name}'",
                }
            role.Members.Remove(target)
            _lane_commit(model, label="role")
            return {
                "success": True,
                "message": f"Removed member '{member_identifier}' from '{role_name}'",
            }
        except Exception as e:
            return {"success": False, "error": f"remove_member failed: {e}"}
        finally:
            safe_disconnect(server)

    def list_members(self, role_name: str) -> Dict[str, Any]:
        """List all members on a role."""
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            role = model.Roles.Find(role_name)
            if role is None:
                return {"success": False, "error": f"Role '{role_name}' not found"}
            members: List[Dict[str, Any]] = []
            for m in role.Members:
                entry = {
                    "name": str(m.MemberName),
                    "member_type": m.GetType().Name,
                }
                try:
                    if hasattr(m, "IdentityProvider"):
                        entry["identity_provider"] = (
                            str(m.IdentityProvider) if m.IdentityProvider else None
                        )
                except Exception:
                    pass
                members.append(entry)
            return {
                "success": True,
                "role": role_name,
                "members": members,
                "count": len(members),
            }
        except Exception as e:
            return {"success": False, "error": f"list_members failed: {e}"}
        finally:
            safe_disconnect(server)

    def get_effective_permissions(
        self, role_name: Optional[str] = None, table_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Read-only: what each role can see - model permission, per-table RLS filter and
        object-level security (MP-01; parity with Microsoft's ``GetEffectivePermissions``).

        Per role: ``model_permission``; ``tables[]`` for every table the role has a
        TablePermission on (``filter_expression`` = the RLS filter, ``table_ols`` = the
        table-level metadata permission, ``columns[]`` = column-level OLS entries that are
        not Default); ``unrestricted_tables`` = the model's other tables (no permission
        entry: every row and column is visible when the role can read the model).
        ``table_name`` narrows ``tables[]`` to one table.
        """
        server, db, model, err = get_server_db_model(self.connection_state)
        if err:
            return {"success": False, "error": err, "error_type": "connection_error"}
        try:
            if role_name:
                found = model.Roles.Find(role_name)
                if found is None:
                    return {
                        "success": False,
                        "error": f"Role '{role_name}' not found",
                        "error_type": "role_not_found",
                    }
                roles = [found]
            else:
                roles = list(model.Roles)
            all_tables = [str(t.Name) for t in model.Tables]
            out_roles: List[Dict[str, Any]] = []
            for role in roles:
                tables: List[Dict[str, Any]] = []
                covered = set()
                for tp in role.TablePermissions:
                    tname = _tp_table_name(tp)
                    covered.add(tname)
                    if table_name and tname != table_name:
                        continue
                    flt = getattr(tp, "FilterExpression", None)
                    entry: Dict[str, Any] = {
                        "table": tname,
                        "filter_expression": str(flt) if flt else None,
                        "table_ols": _perm_name(getattr(tp, "MetadataPermission", None)),
                        "columns": [],
                    }
                    for cp in getattr(tp, "ColumnPermissions", None) or []:
                        perm = _perm_name(getattr(cp, "MetadataPermission", None))
                        if perm != "Default":
                            entry["columns"].append({"column": str(cp.Name), "permission": perm})
                    tables.append(entry)
                unrestricted = [t for t in all_tables if t not in covered]
                if table_name:
                    unrestricted = [t for t in unrestricted if t == table_name]
                out_roles.append(
                    {
                        "role": str(role.Name),
                        "model_permission": _perm_name(getattr(role, "ModelPermission", None)),
                        "member_count": len(list(getattr(role, "Members", None) or [])),
                        "tables": tables,
                        "filtered_tables": [t["table"] for t in tables if t["filter_expression"]],
                        "unrestricted_tables": unrestricted,
                    }
                )
            return {
                "success": True,
                "roles": out_roles,
                "count": len(out_roles),
                "note": (
                    "Static read of the role definitions. Members with several roles get the UNION "
                    "of their roles' rows. To see actual rows, use 04_Query_Operations test_rls / "
                    "04_Run_DAX roles=[...]."
                ),
            }
        except Exception as e:
            return {"success": False, "error": f"get_effective_permissions failed: {e}"}
        finally:
            safe_disconnect(server)


def _tp_table_name(tp: Any) -> str:
    table = getattr(tp, "Table", None)
    name = getattr(table, "Name", None) if table is not None else None
    return str(name if name is not None else getattr(tp, "Name", ""))


def _perm_name(value: Any) -> Optional[str]:
    """Enum member name of a CLR permission value (``Default`` when unset)."""
    if value is None:
        return "Default"
    text = str(value).split(".")[-1].strip()
    return text or "Default"
