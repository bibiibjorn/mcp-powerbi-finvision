"""Partition CRUD Manager — manage table partitions via TOM."""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

from core.operations.write_lane import commit as _lane_commit  # noqa: E402

try:
    from core.infrastructure.dll_paths import load_amo_assemblies, load_adomd_assembly

    if load_amo_assemblies():
        from Microsoft.AnalysisServices.Tabular import Server as AMOServer
    else:
        AMOServer = None
    if load_adomd_assembly():
        from Microsoft.AnalysisServices.AdomdClient import AdomdCommand
    else:
        AdomdCommand = None
except Exception as e:
    logger.debug(f"AMO/ADOMD assemblies not loaded: {e}")
    AMOServer = None
    AdomdCommand = None
from core.operations.amo_mixins import AmoSessionMixin  # noqa: E402


class PartitionCrudManager(AmoSessionMixin):
    """CRUD operations for table partitions."""

    def __init__(self):
        self._connection_state = None

    @property
    def connection_state(self):
        if self._connection_state is None:
            from core.infrastructure.connection_state import connection_state

            self._connection_state = connection_state
        return self._connection_state

    def list_partitions(self, table_name: str) -> Dict[str, Any]:
        """List all partitions for a table."""
        server, db, model, err = self._get_server_db_model()
        if err:
            return {"success": False, "error": err}
        try:
            table = model.Tables.Find(table_name)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            partitions = []
            for partition in table.Partitions:
                partitions.append(
                    {
                        "name": partition.Name,
                        "source_type": str(partition.SourceType),
                        "mode": str(partition.Mode) if hasattr(partition, "Mode") else None,
                        "state": str(partition.State) if hasattr(partition, "State") else None,
                        "refreshed_time": (
                            str(partition.RefreshedTime)
                            if hasattr(partition, "RefreshedTime")
                            else None
                        ),
                    }
                )
            return {
                "success": True,
                "table": table_name,
                "partitions": partitions,
                "count": len(partitions),
            }
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def describe_partition(self, table_name: str, partition_name: str) -> Dict[str, Any]:
        """Get detailed info about a partition."""
        server, db, model, err = self._get_server_db_model()
        if err:
            return {"success": False, "error": err}
        try:
            table = model.Tables.Find(table_name)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            partition = table.Partitions.Find(partition_name)
            if not partition:
                return {
                    "success": False,
                    "error": f"Partition '{partition_name}' not found in '{table_name}'",
                }
            info = {
                "name": partition.Name,
                "source_type": str(partition.SourceType),
                "mode": str(partition.Mode) if hasattr(partition, "Mode") else None,
                "state": str(partition.State) if hasattr(partition, "State") else None,
                "refreshed_time": (
                    str(partition.RefreshedTime) if hasattr(partition, "RefreshedTime") else None
                ),
            }
            try:
                if hasattr(partition, "Source") and hasattr(partition.Source, "Expression"):
                    info["expression"] = partition.Source.Expression
            except Exception:
                pass
            return {"success": True, "partition": info}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def update_partition_expression(
        self,
        table_name: str,
        partition_name: str,
        expression: str,
        refresh_after: bool = False,
    ) -> Dict[str, Any]:
        """Update the M expression of a partition. Optionally refresh afterwards
        so data reflects the new M code (metadata-only save leaves cached rows stale)."""
        server, db, model, err = self._get_server_db_model()
        if err:
            return {"success": False, "error": err}
        try:
            table = model.Tables.Find(table_name)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            partition = table.Partitions.Find(partition_name)
            if not partition:
                return {"success": False, "error": f"Partition '{partition_name}' not found"}
            try:
                partition.Source.Expression = expression
                if refresh_after:
                    from Microsoft.AnalysisServices.Tabular import RefreshType

                    partition.RequestRefresh(RefreshType.Full)
                _lane_commit(model, label="partition")
                return {
                    "success": True,
                    "message": f"Updated expression for partition '{partition_name}'"
                    + (" and refreshed" if refresh_after else ""),
                    "refreshed": bool(refresh_after),
                }
            except Exception as e:
                if refresh_after:
                    from core.autonomous.clr_errors import format_refresh_error

                    return format_refresh_error(
                        e,
                        table=table_name,
                        partition=partition_name,
                        last_query=expression,
                    )
                return {"success": False, "error": f"Update failed: {e}"}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def create_partition(
        self,
        table_name: str,
        partition_name: str,
        expression: str,
        mode: str = "Import",
    ) -> Dict[str, Any]:
        """Create a new M partition on a table.

        mode: Import | DirectQuery | Dual.
        """
        if AMOServer is None:
            return {"success": False, "error": "AMO assemblies not available"}
        if not partition_name or not expression:
            return {"success": False, "error": "partition_name and expression are required"}
        server, db, model, err = self._get_server_db_model()
        if err:
            return {"success": False, "error": err}
        try:
            from Microsoft.AnalysisServices.Tabular import Partition, MPartitionSource, ModeType

            table = model.Tables.Find(table_name)
            if table is None:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            if table.Partitions.Find(partition_name) is not None:
                return {
                    "success": False,
                    "error": f"Partition '{partition_name}' already exists on '{table_name}'",
                }
            part = Partition()
            part.Name = partition_name
            src = MPartitionSource()
            src.Expression = expression
            part.Source = src
            mode_key = (mode or "Import").strip().lower()
            mode_map = {"import": "Import", "directquery": "DirectQuery", "dual": "Dual"}
            mode_name = mode_map.get(mode_key)
            if mode_name is None:
                return {
                    "success": False,
                    "error": f"Invalid mode '{mode}'. Valid: Import, DirectQuery, Dual",
                }
            try:
                part.Mode = getattr(ModeType, mode_name)
            except Exception:
                # Older TOM exposes it differently
                pass
            table.Partitions.Add(part)
            _lane_commit(model, label="partition")
            return {
                "success": True,
                "message": f"Created partition '{partition_name}' on '{table_name}'",
                "partition": partition_name,
                "mode": mode_name,
            }
        except Exception as e:
            return {"success": False, "error": f"create_partition failed: {e}"}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def delete_partition(self, table_name: str, partition_name: str) -> Dict[str, Any]:
        """Delete a partition from a table. Refuses if it's the last partition."""
        server, db, model, err = self._get_server_db_model()
        if err:
            return {"success": False, "error": err}
        try:
            table = model.Tables.Find(table_name)
            if table is None:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            if table.Partitions.Count <= 1:
                return {
                    "success": False,
                    "error": f"Cannot delete the only partition on '{table_name}'. Delete the table instead.",
                }
            part = table.Partitions.Find(partition_name)
            if part is None:
                return {
                    "success": False,
                    "error": f"Partition '{partition_name}' not found on '{table_name}'",
                }
            table.Partitions.Remove(part)
            _lane_commit(model, label="partition")
            return {
                "success": True,
                "message": f"Deleted partition '{partition_name}' from '{table_name}'",
            }
        except Exception as e:
            return {"success": False, "error": f"delete_partition failed: {e}"}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def set_partition_mode(self, table_name: str, partition_name: str, mode: str) -> Dict[str, Any]:
        """Change a partition's mode (Import | DirectQuery | Dual)."""
        server, db, model, err = self._get_server_db_model()
        if err:
            return {"success": False, "error": err}
        try:
            from Microsoft.AnalysisServices.Tabular import ModeType

            table = model.Tables.Find(table_name)
            if table is None:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            part = table.Partitions.Find(partition_name)
            if part is None:
                return {
                    "success": False,
                    "error": f"Partition '{partition_name}' not found on '{table_name}'",
                }
            mode_key = (mode or "").strip().lower()
            mode_map = {"import": "Import", "directquery": "DirectQuery", "dual": "Dual"}
            mode_name = mode_map.get(mode_key)
            if mode_name is None:
                return {
                    "success": False,
                    "error": f"Invalid mode '{mode}'. Valid: Import, DirectQuery, Dual",
                }
            part.Mode = getattr(ModeType, mode_name)
            _lane_commit(model, label="partition")
            return {
                "success": True,
                "message": f"Set partition '{partition_name}' mode to {mode_name}",
                "mode": mode_name,
            }
        except Exception as e:
            return {"success": False, "error": f"set_partition_mode failed: {e}"}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def refresh_partition(self, table_name: str, partition_name: str) -> Dict[str, Any]:
        """Refresh a specific partition."""
        server, db, model, err = self._get_server_db_model()
        if err:
            return {"success": False, "error": err}
        try:
            table = model.Tables.Find(table_name)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            partition = table.Partitions.Find(partition_name)
            if not partition:
                return {"success": False, "error": f"Partition '{partition_name}' not found"}
            try:
                from Microsoft.AnalysisServices.Tabular import RefreshType

                partition.RequestRefresh(RefreshType.Automatic)
                _lane_commit(model, label="partition")
                return {
                    "success": True,
                    "message": f"Refresh requested for partition '{partition_name}'",
                    "table": table_name,
                    "partition": partition_name,
                }
            except Exception as e:
                from core.autonomous.clr_errors import format_refresh_error

                last_query = getattr(partition, "Source", None)
                q_expr = getattr(last_query, "Expression", None) if last_query else None
                return format_refresh_error(
                    e,
                    table=table_name,
                    partition=partition_name,
                    last_query=q_expr if isinstance(q_expr, str) else None,
                )
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass
