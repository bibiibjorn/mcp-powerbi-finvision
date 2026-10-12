"""
RLS Manager for PBIXRay MCP Server
Manages Row-Level Security roles and testing
"""

import logging
from typing import Dict, Any, Optional
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

# Try to load AMO
AMO_AVAILABLE = False
AMOServer = None

try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    load_amo_assemblies()
    from Microsoft.AnalysisServices.Tabular import Server as AMOServer  # noqa: F401

    AMO_AVAILABLE = True
except Exception as e:
    logger.warning(f"AMO not available: {e}")


class RLSManager:
    """Manage Row-Level Security roles."""

    def __init__(self, connection, query_executor):
        """Initialize with connection and query executor."""
        self.connection = connection
        self.executor = query_executor

    def list_roles(self) -> Dict[str, Any]:
        """List all security roles and their filters.

        P2-38: database acquisition now delegates to
        `core.operations.amo_helpers.get_server_db_model`, which resolves the
        catalog name via DBSCHEMA_CATALOGS. The previous `server.Databases[0]`
        lookup picked the wrong database on multi-model msmdsrv instances.
        """
        if not AMO_AVAILABLE:
            return {"success": False, "error": "AMO not available for role management"}

        from core.operations.amo_helpers import get_server_db_model, safe_disconnect
        from core.infrastructure.connection_state import connection_state

        server, db, model, err = get_server_db_model(connection_state)
        if err:
            return {"success": False, "error": err}
        try:
            roles = []

            if hasattr(model, "Roles"):
                for role in model.Roles:
                    role_data = {
                        "name": role.Name,
                        "description": role.Description if hasattr(role, "Description") else None,
                        "model_permission": (
                            str(role.ModelPermission)
                            if hasattr(role, "ModelPermission")
                            else "Read"
                        ),
                        "table_permissions": [],
                    }

                    # Get table permissions (filters)
                    if hasattr(role, "TablePermissions"):
                        for perm in role.TablePermissions:
                            perm_data = {"table": perm.Table.Name if perm.Table else "Unknown"}

                            if hasattr(perm, "FilterExpression") and perm.FilterExpression:
                                perm_data["filter_expression"] = perm.FilterExpression

                            role_data["table_permissions"].append(perm_data)

                    # Get members count
                    if hasattr(role, "Members"):
                        role_data["members_count"] = role.Members.Count
                    else:
                        role_data["members_count"] = 0

                    roles.append(role_data)

            return {"success": True, "roles": roles, "total_roles": len(roles)}

        except Exception as e:
            logger.error(f"Error listing roles: {e}")
            return {"success": False, "error": str(e)}
        finally:
            safe_disconnect(server)

    @staticmethod
    def _rows_signature(result: Dict[str, Any]):
        rows = result.get("rows") or []
        return [tuple(sorted((str(k), repr(v)) for k, v in r.items())) for r in rows]

    def test_role_filter(
        self, role_name: str, test_query: str, test_table: Optional[str] = None
    ) -> Dict[str, Any]:
        """Test RLS: run the query unfiltered and AS the role, then compare VALUES.

        C-29: the old version compared ROW COUNTS of an auto-generated
        ROW("RowCount", COUNTROWS(T)) query (always 1 row, so filter_active
        was always False), tested each table filter alone (RLS applies all of
        a role's filters together, plus propagation) and broke DEFINE queries
        with a blanket replace('EVALUATE ', '').

        Primary path: the engine itself enforces the role - the query runs on
        an ADOMD connection with Roles=<role> (run_as_role). Fallback
        (impersonation unavailable): a simulation that applies ALL of the
        role's table filters at once to an EVALUATE query.
        """
        try:
            roles_result = self.list_roles()
            if not roles_result.get("success"):
                return roles_result

            role = next((r for r in roles_result["roles"] if r["name"] == role_name), None)
            if not role:
                available = [r["name"] for r in roles_result.get("roles", [])]
                return {
                    "success": False,
                    "error": f"Role '{role_name}' not found",
                    "available_roles": available,
                }

            filters = [p for p in role.get("table_permissions", []) if p.get("filter_expression")]
            if not filters:
                return {
                    "success": True,
                    "role": role_name,
                    "message": f"Role '{role_name}' has no table filters defined",
                    "role_filters": [],
                }

            from core.dax.dax_names import quote_table

            if test_table and not test_query:
                test_query = f'EVALUATE ROW("RowCount", COUNTROWS({quote_table(test_table)}))'

            if not test_query:
                return {
                    "success": False,
                    "error": "Either test_query or test_table parameter required",
                }

            result_unfiltered = self.executor.validate_and_execute_dax(
                test_query, 0, bypass_cache=True
            )

            method = "impersonation"
            impersonation_error = None
            try:
                from core.infrastructure.role_impersonation import run_as_role

                result_filtered = run_as_role(dax_query=test_query, roles=[role_name], top_n=10000)
            except Exception as imp_err:  # noqa: BLE001
                result_filtered = {"success": False, "error": str(imp_err)}
            simulated_query = None
            if not result_filtered.get("success"):
                method = "simulation"
                impersonation_error = result_filtered.get("error")
                import re as _re

                m = _re.match(r"^\s*EVALUATE\s+", test_query, _re.IGNORECASE)
                if not m:
                    return error_envelope(
                        "simulation_unsupported",
                        "Role impersonation failed and the query is not a plain EVALUATE "
                        f"query that can be simulated ({impersonation_error}).",
                    )
                filter_args = ",\n  ".join(
                    f"FILTER({quote_table(p['table'])}, {p['filter_expression']})" for p in filters
                )
                simulated_query = f"EVALUATE CALCULATETABLE(\n  {test_query[m.end():].strip()},\n  {filter_args}\n)"
                result_filtered = self.executor.validate_and_execute_dax(
                    simulated_query, 0, bypass_cache=True
                )

            if not (result_unfiltered.get("success") and result_filtered.get("success")):
                return {
                    "success": False,
                    "error": result_unfiltered.get("error") or result_filtered.get("error"),
                    "role": role_name,
                    "method": method,
                    "result_unfiltered": result_unfiltered,
                    "result_filtered": result_filtered,
                }

            unfiltered_rows = len(result_unfiltered.get("rows", []))
            filtered_rows = len(result_filtered.get("rows", []))
            values_differ = self._rows_signature(result_unfiltered) != self._rows_signature(
                result_filtered
            )
            combined = {
                "tables": [p["table"] for p in filters],
                "filter_expressions": {p["table"]: p["filter_expression"] for p in filters},
                "unfiltered_row_count": unfiltered_rows,
                "filtered_row_count": filtered_rows,
                "rows_removed": unfiltered_rows - filtered_rows,
                "values_differ": values_differ,
                "filter_active": values_differ,
                "filtered_result": result_filtered,
            }
            if simulated_query:
                combined["filtered_query"] = simulated_query
            out = {
                "success": True,
                "role": role_name,
                "method": method,
                "test_query": test_query,
                "result_unfiltered": result_unfiltered,
                "filter_tests": [combined],
                "summary": {
                    "total_filters": len(filters),
                    "filters_tested": len(filters),
                    "filters_active": len(filters) if values_differ else 0,
                    "role_changes_result": values_differ,
                },
            }
            if impersonation_error:
                out["impersonation_error"] = impersonation_error
                out["note"] = (
                    "Simulated: all role filters applied together via CALCULATETABLE; "
                    "relationship propagation of RLS is approximated."
                )
            return out

        except Exception as e:
            logger.error(f"Error testing role: {e}")
            return {"success": False, "error": str(e)}
