"""
Table CRUD Manager for MCP-PowerBi-Finvision
Provides comprehensive table management: create, update, delete, rename, refresh
"""

import logging
from typing import Dict, Any, Optional

from core.dax.dax_rename import rename_table_refs
from core.operations import model_readback, rename_flow
from core.operations.rename_cascade import cascade_table_rename, is_calculated_partition
from core.operations.write_lane import commit, safe_undo
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

AMO_AVAILABLE = False
AMOServer = None
Table = None
Partition = None

try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    load_amo_assemblies()
    from Microsoft.AnalysisServices.Tabular import (  # noqa: F401
        Server as AMOServer,
        Table,
        Partition,
        PartitionSourceType,
    )

    AMO_AVAILABLE = True
    logger.info("AMO available for table CRUD operations")
except Exception as e:
    logger.warning(f"AMO not available for table CRUD: {e}")


# Try to load ADOMD
AdomdConnection = None
AdomdCommand = None

try:
    from core.infrastructure.dll_paths import load_adomd_assembly

    if load_adomd_assembly():
        from Microsoft.AnalysisServices.AdomdClient import (  # noqa: F401
            AdomdConnection,
            AdomdCommand,
        )
except Exception:
    pass
from core.operations.amo_mixins import AmoModelMixin  # noqa: E402


class TableCRUDManager(AmoModelMixin):
    """Manage table CRUD operations using TOM."""

    def __init__(self, connection):
        """Initialize with ADOMD connection."""
        self.connection = connection

    def _valid_identifier(self, s: Optional[str]) -> bool:
        """Validate identifier (table name, etc.)."""
        return bool(s) and len(str(s).strip()) > 0 and len(str(s)) <= 128 and "\0" not in str(s)

    def _connection_error_msg(self) -> str:
        """P1-32: build a connection error message that includes the real
        AMO cause captured in `_last_amo_error`, rather than the generic
        "Could not connect to model"."""
        cause = getattr(self, "_last_amo_error", None) or "unknown cause"
        return f"Could not connect to model: {cause}"

    def create_table(
        self,
        table_name: str,
        description: Optional[str] = None,
        expression: Optional[str] = None,
        hidden: bool = False,
        m_expression: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new table: calculated (``expression`` = DAX) or import
        (``m_expression`` = Power Query M). One of the two is required (C-24:
        an import table with an empty M source cannot refresh).

        Args:
            table_name: Name of the table to create
            description: Optional table description
            expression: DAX expression for calculated table (required for calculated tables)
            hidden: Whether to hide the table (default: False)

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available - cannot create tables")

        if not self._valid_identifier(table_name):
            return error_envelope(
                "invalid_parameters", "Table name must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            res = self._apply_create_table(
                model,
                table_name=table_name,
                description=description,
                expression=expression,
                hidden=hidden,
                m_expression=m_expression,
            )
            if res.get("success"):
                res.update(
                    commit(
                        model,
                        label="table/create",
                        verify=lambda: model_readback.table_exists(table_name),
                    )
                )
                logger.info(f"Created table '{table_name}'")
            return res

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error creating table: {error_msg}", exc_info=True)
            return error_envelope("creation_error", error_msg)

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_create_table(
        self,
        model,
        *,
        table_name,
        description=None,
        expression=None,
        hidden=False,
        m_expression=None,
    ) -> Dict[str, Any]:
        """Mutate `model` to add a table. Does NOT SaveChanges or Disconnect."""
        # Check if table already exists
        existing_table = next((t for t in model.Tables if t.Name == table_name), None)
        if existing_table:
            return error_envelope("table_exists", f"Table '{table_name}' already exists")
        if not expression and not (m_expression and str(m_expression).strip()):
            return error_envelope(
                "invalid_parameters",
                "Pass `expression` (DAX) for a calculated table or `m_expression` "
                "(Power Query M) for an import table — a table with an empty M "
                "source cannot be refreshed.",
            )

        # Create new table
        table = Table()
        table.Name = table_name

        if description:
            table.Description = description

        if hidden:
            table.IsHidden = hidden

        # Add partition (required for all tables)
        # Import necessary type
        from Microsoft.AnalysisServices.Tabular import (  # noqa: F401, F811
            Partition,
            PartitionSourceType,
        )

        partition = Partition()
        partition.Name = table_name

        if expression:
            # Set as calculated table
            from Microsoft.AnalysisServices.Tabular import CalculatedPartitionSource

            calc_source = CalculatedPartitionSource()
            calc_source.Expression = expression
            partition.Source = calc_source
        else:
            # Import table: M partition with the caller's Power Query source.
            from Microsoft.AnalysisServices.Tabular import MPartitionSource

            m_source = MPartitionSource()
            m_source.Expression = str(m_expression)
            partition.Source = m_source

        table.Partitions.Add(partition)

        model.Tables.Add(table)

        created: Dict[str, Any] = {
            "success": True,
            "action": "created",
            "table": table_name,
            "description": description,
            "expression": expression,
            "hidden": hidden,
            "message": f"Successfully created table '{table_name}'",
        }
        if expression:
            # S-13: a calculated table holds no rows until it is calculated (a refresh of
            # THIS table with refresh_type=calculate - not the whole model).
            created["needs_refresh"] = True
            created["refresh_with"] = {
                "tool": "02_Model_Operations",
                "args": {
                    "object_type": "model",
                    "operation": "refresh",
                    "tables": [table_name],
                    "refresh_type": "calculate",
                },
            }
            created["refresh_note"] = (
                f"Calculated table '{table_name}' is EMPTY until it is refreshed "
                "(refresh_with = calculate refresh of just this table)."
            )
        return created

    def update_table(
        self,
        table_name: str,
        description: Optional[str] = None,
        expression: Optional[str] = None,
        hidden: Optional[bool] = None,
        new_name: Optional[str] = None,
        preview: bool = False,
        preview_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing table.

        A rename (``new_name``) cascades every DAX reference, is verified after
        SaveChanges and rolled back when it introduced new expression errors
        (C-01/C-40). ``preview=True`` returns the would-be diff + a
        ``preview_hash`` without writing; passing that hash back on the real
        call refuses to apply when the model changed in between (C-41).

        Args:
            table_name: Current name of the table
            description: New description (optional)
            expression: New DAX expression for calculated table (optional)
            hidden: Set hidden state (optional)
            new_name: New table name (optional)

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")

        if not self._valid_identifier(table_name):
            return error_envelope(
                "invalid_parameters", "Table name must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            renaming = bool(new_name) and new_name != table_name
            if renaming and (preview or preview_hash):
                pv = self._preview_rename(model, table_name, new_name)
                if preview or not pv.get("success"):
                    return pv
                if pv.get("preview_hash") != preview_hash:
                    return {
                        "success": False,
                        "error_type": "stale_preview",
                        "error": "The model changed since the rename preview; re-run preview=true.",
                        "expected": preview_hash,
                        "current": pv.get("preview_hash"),
                    }
            baseline = rename_flow.snapshot_errors() if renaming else None
            record: list = []
            res = self._apply_update_table(
                model,
                table_name=table_name,
                description=description,
                expression=expression,
                hidden=hidden,
                new_name=new_name,
                record=record,
            )
            if res.get("success"):
                if renaming:
                    outcome = rename_flow.finish(
                        model,
                        label="table/rename",
                        baseline=baseline,
                        record=record,
                        verify_landed=lambda: model_readback.table_exists(new_name),
                    )
                    if outcome.get("success") is False:
                        return outcome
                    res.update(outcome)
                    untouched = [
                        r
                        for r in rename_flow.public_records(record)
                        if r.get("kind") == "untouched"
                    ]
                    untouched += rename_flow.udf_reference_report(
                        lambda e: rename_table_refs(e, table_name, new_name)
                    )
                    if untouched:
                        res["untouched"] = untouched
                else:
                    res.update(commit(model, label="table/update"))
                logger.info(f"Updated table '{table_name}': {', '.join(res.get('updates', []))}")
            return res

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error updating table: {error_msg}", exc_info=True)
            return error_envelope("update_error", error_msg)

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_update_table(
        self,
        model,
        *,
        table_name,
        description=None,
        expression=None,
        hidden=None,
        new_name=None,
        record=None,
    ) -> Dict[str, Any]:
        """Mutate `model` to update a table. Does NOT SaveChanges or Disconnect.

        ``record`` (a list) receives every renamed site (see rename_cascade) so
        the caller can verify and roll back the rename exactly.
        """
        # Find table
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if not table:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")

        updates = []

        # Update description
        if description is not None:
            table.Description = description
            updates.append("description")

        # Update hidden state
        if hidden is not None:
            table.IsHidden = hidden
            updates.append("hidden")

        # Update expression (for calculated tables)
        if expression is not None:
            # C-24: only a CALCULATED partition takes a DAX expression — an M
            # partition also has `.Expression` (its Power Query), which must
            # never be overwritten with DAX.
            partition = next(iter(table.Partitions), None)
            if partition is not None and is_calculated_partition(partition):
                partition.Source.Expression = expression
                updates.append("expression")
            else:
                return error_envelope(
                    "invalid_operation",
                    "Table is not a calculated table — its partition is not a DAX "
                    "(calculated) source; edit Power Query via object_type=partition.",
                )

        # Update name (with DAX cascade — TOM does NOT auto-cascade DAX references)
        cascaded = []
        if new_name and self._valid_identifier(new_name) and new_name != table.Name:
            # Check if new name already exists (case-insensitive, like DAX names)
            if any(
                str(t.Name).lower() == str(new_name).lower() for t in model.Tables if t != table
            ):
                return error_envelope("name_conflict", f"Table '{new_name}' already exists")
            old_tbl_name = table.Name
            table.Name = new_name
            updates.append("name")
            if record is not None:
                record.append(
                    rename_flow.name_record(
                        table, old_tbl_name, new_name, f"table '{old_tbl_name}'"
                    )
                )

            # Cascade DAX references (bare + qualified; strings/comments untouched).
            # Relationships are object references in TOM and survive automatically.
            cascaded = cascade_table_rename(model, old_tbl_name, new_name, record=record)

        result = {
            "success": True,
            "action": "updated",
            "table": new_name if new_name else table_name,
            "original_name": table_name if new_name else None,
            "updates": updates,
            "message": f"Successfully updated table '{table_name}'",
        }
        if cascaded:
            result["cascaded_references"] = len(cascaded)
            result["updated_expressions"] = cascaded
        return result

    def delete_table(self, table_name: str) -> Dict[str, Any]:
        """
        Delete a table.

        Args:
            table_name: Name of the table to delete

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")

        if not self._valid_identifier(table_name):
            return error_envelope(
                "invalid_parameters", "Table name must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            res = self._apply_delete_table(model, table_name=table_name)
            if res.get("success"):

                def _gone():
                    exists = model_readback.table_exists(table_name)
                    return None if exists is None else not exists

                res.update(commit(model, label="table/delete", verify=_gone))
                logger.info(f"Deleted table '{table_name}'")
            return res

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error deleting table: {error_msg}", exc_info=True)
            return {
                "success": False,
                "error": error_msg,
                "error_type": "deletion_error",
                "suggestions": [
                    "Check if table is referenced by relationships",
                    "Verify no measures or columns are being used elsewhere",
                    "Ensure table is not a required system table",
                ],
            }

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_delete_table(self, model, *, table_name) -> Dict[str, Any]:
        """Mutate `model` to remove a table. Does NOT SaveChanges or Disconnect."""
        # Find table
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if not table:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")

        # Remove table
        model.Tables.Remove(table)

        return {
            "success": True,
            "action": "deleted",
            "table": table_name,
            "message": f"Successfully deleted table '{table_name}'",
        }

    # ------------------------------------------------------------------
    # Refresh policy (incremental refresh) — table.RefreshPolicy CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _refresh_policy_to_dict(policy) -> Optional[Dict[str, Any]]:
        """Serialize a TOM BasicRefreshPolicy to a plain dict (None if absent)."""
        if policy is None:
            return None
        return {
            "rolling_window_granularity": str(getattr(policy, "RollingWindowGranularity", "")),
            "rolling_window_periods": getattr(policy, "RollingWindowPeriods", None),
            "incremental_granularity": str(getattr(policy, "IncrementalGranularity", "")),
            "incremental_periods": getattr(policy, "IncrementalPeriods", None),
            "source_expression": getattr(policy, "SourceExpression", None),
        }

    def get_refresh_policy(self, table_name: str) -> Dict[str, Any]:
        """Read a table's RefreshPolicy (read-only — never saves)."""
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")
        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }
        try:
            table = next((t for t in model.Tables if t.Name == table_name), None)
            if table is None:
                return error_envelope("table_not_found", f"Table '{table_name}' not found")
            return {
                "success": True,
                "table": table_name,
                "refresh_policy": self._refresh_policy_to_dict(
                    getattr(table, "RefreshPolicy", None)
                ),
            }
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_set_refresh_policy(
        self,
        model,
        *,
        table_name: str,
        rolling_window_granularity: str,
        rolling_window_periods: int,
        incremental_granularity: str,
        incremental_periods: int,
        source_expression: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mutate `model` to set a table's BasicRefreshPolicy. No save/disconnect."""
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if table is None:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")
        try:
            from Microsoft.AnalysisServices.Tabular import (
                BasicRefreshPolicy,
                RefreshGranularityType,
            )

            policy = BasicRefreshPolicy()
            policy.RollingWindowGranularity = getattr(
                RefreshGranularityType, rolling_window_granularity
            )
            policy.RollingWindowPeriods = int(rolling_window_periods)
            policy.IncrementalGranularity = getattr(RefreshGranularityType, incremental_granularity)
            policy.IncrementalPeriods = int(incremental_periods)
            if source_expression:
                policy.SourceExpression = source_expression
            table.RefreshPolicy = policy
        except Exception as e:  # noqa: BLE001 — CLR interop
            return error_envelope("set_policy_error", str(e))
        return {"success": True, "table": table_name}

    def set_refresh_policy(
        self,
        table_name: str,
        rolling_window_granularity: str,
        rolling_window_periods: int,
        incremental_granularity: str,
        incremental_periods: int,
        source_expression: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set a table's incremental-refresh policy (live TOM write)."""
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")
        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }
        try:
            res = self._apply_set_refresh_policy(
                model,
                table_name=table_name,
                rolling_window_granularity=rolling_window_granularity,
                rolling_window_periods=rolling_window_periods,
                incremental_granularity=incremental_granularity,
                incremental_periods=incremental_periods,
                source_expression=source_expression,
            )
            if res.get("success"):
                res.update(commit(model, label="table/set_refresh_policy"))
            return res
        except Exception as e:  # noqa: BLE001
            return error_envelope("set_policy_error", str(e))
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_clear_refresh_policy(self, model, *, table_name: str) -> Dict[str, Any]:
        """Mutate `model` to clear a table's RefreshPolicy. No save/disconnect."""
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if table is None:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")
        table.RefreshPolicy = None
        return {"success": True, "table": table_name}

    def clear_refresh_policy(self, table_name: str) -> Dict[str, Any]:
        """Remove a table's incremental-refresh policy (live TOM write)."""
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")
        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }
        try:
            res = self._apply_clear_refresh_policy(model, table_name=table_name)
            if res.get("success"):
                res.update(commit(model, label="table/clear_refresh_policy"))
            return res
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_rename_table(self, model, *, table_name, new_name, record=None) -> Dict[str, Any]:
        """Mutate `model` to rename a table (DAX cascade included). Does NOT
        SaveChanges or Disconnect. Mirrors the rename path of _apply_update_table.
        Note: the public `rename_table` keeps delegating to `update_table` (so the
        PBIR cascade path is preserved); this helper exists for the single-
        connection atomic batch path."""
        return self._apply_update_table(
            model, table_name=table_name, new_name=new_name, record=record
        )

    def _preview_rename(self, model, table_name: str, new_name: str) -> Dict[str, Any]:
        """Dry-run the rename cascade on `model` (no mutation)."""
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if table is None:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")
        if any(str(t.Name).lower() == str(new_name).lower() for t in model.Tables if t != table):
            return error_envelope("name_conflict", f"Table '{new_name}' already exists")
        record: list = []
        cascade_table_rename(model, table_name, new_name, record=record, apply=False)
        return rename_flow.preview_payload("table", table_name, new_name, record)

    def preview_rename(self, table_name: str, new_name: str) -> Dict[str, Any]:
        """Public dry-run: the would-be cascade diff + preview_hash (read-only)."""
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")
        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }
        try:
            return self._preview_rename(model, table_name, new_name)
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def rename_table(
        self,
        table_name: str,
        new_name: str,
        pbip_path: Optional[str] = None,
        preview: bool = False,
        preview_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Rename a table.

        Args:
            table_name: Current table name
            new_name: New table name
            pbip_path: Optional path to the .pbip file/folder. When provided,
                the rename is cascaded across the bound PBIR report after
                the live model rename succeeds. PBI Desktop must be closed.

        Returns:
            Result dictionary with success status
        """
        result = self.update_table(
            table_name=table_name, new_name=new_name, preview=preview, preview_hash=preview_hash
        )
        if result.get("success") and pbip_path:
            from core.operations.rename_cascade_pbir_bridge import apply_pbir_cascade

            pbir_result = apply_pbir_cascade(
                pbip_path=pbip_path,
                kind="table",
                old_name=table_name,
                new_name=new_name,
                dry_run=bool(preview),
            )
            result["pbir_cascade"] = {
                "success": pbir_result.get("success"),
                "files_modified": pbir_result.get("files_modified", 0),
                "replacements": pbir_result.get("replacements", 0),
                "errors": pbir_result.get("errors", []),
                "report_path": pbir_result.get("report_path"),
                "report_paths": pbir_result.get("report_paths"),
                "reports": pbir_result.get("reports"),
            }
            if pbir_result.get("reload_required"):
                result["reload_required"] = True
                result["reload_op_chain"] = pbir_result.get("reload_op_chain")
        return result

    def refresh_table(self, table_name: str, refresh_type: str = "full") -> Dict[str, Any]:
        """
        Refresh a table's data.

        Args:
            table_name: Name of the table to refresh
            refresh_type: full|automatic|dataOnly|calculate|clearValues|defragment.
                Use 'calculate' for calculated/field-parameter tables.

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")

        if not self._valid_identifier(table_name):
            return error_envelope(
                "invalid_parameters", "Table name must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            # Find table
            table = next((t for t in model.Tables if t.Name == table_name), None)
            if not table:
                return error_envelope("table_not_found", f"Table '{table_name}' not found")

            # Request refresh
            from core.operations.model_refresh_manager import _resolve_refresh_type

            rtype, err = _resolve_refresh_type(refresh_type)
            if err:
                return error_envelope("invalid_parameters", err)
            if str(refresh_type or "").strip().lower() == "indexes":
                from core.operations.model_refresh_manager import indexes_refresh_refusal

                refusal = indexes_refresh_refusal(db)
                if refusal:
                    return refusal
            table.RequestRefresh(rtype)
            commit_info = commit(model, label="table/refresh")

            logger.info(f"Requested {refresh_type} refresh for table '{table_name}'")

            return {
                "success": True,
                "action": "refresh_requested",
                "table": table_name,
                "refresh_type": refresh_type,
                "message": f"Successfully requested {refresh_type} refresh for table '{table_name}'",
                **commit_info,
            }

        except Exception as e:
            from core.autonomous.clr_errors import format_refresh_error

            detail = format_refresh_error(e, table=table_name)
            logger.error(
                "Error refreshing table '%s': %s (root: %s)",
                table_name,
                detail.get("error"),
                detail.get("clr_root_cause"),
            )
            return detail

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    # ---- mark as date table (MP-01) --------------------------------------

    @staticmethod
    def _stat(row: Dict[str, Any], key: str) -> Any:
        return row.get(key, row.get(f"[{key}]"))

    def check_date_column(self, table_name: str, column_name: str) -> Dict[str, Any]:
        """Data checks a date table must pass: no blanks, unique, one row per whole day,
        contiguous. Runs ONE DAX query; returns ``{success, ok, problems[], stats{}}``."""
        from core.dax.dax_names import column_ref, quote_table
        from core.infrastructure.connection_state import connection_state

        qe = getattr(connection_state, "query_executor", None)
        if qe is None:
            from core.infrastructure import offline_model

            if offline_model.is_active():
                # N-31: the offline PBIP lane has no engine to run the date checks.
                from core.validation.error_handler import ErrorHandler

                return ErrorHandler.handle_manager_unavailable("query_executor")
            return error_envelope("not_connected", "query executor unavailable (not connected)")
        col = column_ref(table_name, column_name)
        tbl = quote_table(table_name)
        query = (
            "EVALUATE ROW("
            f'"rows", COUNTROWS({tbl}), '
            f'"distinct_values", COUNTROWS(DISTINCT({col})), '
            f'"blanks", COUNTBLANK({col}), '
            f'"min_day", INT(MIN({col})), '
            f'"max_day", INT(MAX({col})), '
            f'"with_time", COUNTROWS(FILTER(DISTINCT({col}), {col} <> INT({col}))), '
            f'"distinct_days", COUNTROWS(DISTINCT(SELECTCOLUMNS({tbl}, "d", INT({col})))))'
        )
        res = qe.validate_and_execute_dax(query, bypass_cache=True)
        if not isinstance(res, dict) or not res.get("success") or not res.get("rows"):
            return {
                "success": False,
                "error": (res or {}).get("error", "date validation query returned no rows"),
                "error_type": "date_validation_failed",
                "hint": (
                    "The table must be loaded/calculated to validate its dates; refresh it "
                    "first (operation=refresh)."
                ),
            }
        row = res["rows"][0]
        stats = {
            k: self._stat(row, k)
            for k in (
                "rows",
                "distinct_values",
                "blanks",
                "min_day",
                "max_day",
                "with_time",
                "distinct_days",
            )
        }
        problems = []
        try:
            n_rows = int(stats["rows"] or 0)
            distinct = int(stats["distinct_values"] or 0)
            blanks = int(stats["blanks"] or 0)
            with_time = int(stats["with_time"] or 0)
            distinct_days = int(stats["distinct_days"] or 0)
            span = int(float(stats["max_day"]) - float(stats["min_day"])) + 1 if n_rows else 0
        except (TypeError, ValueError):
            return error_envelope(
                "date_validation_failed", f"unexpected date validation result: {stats}"
            )
        if n_rows == 0:
            problems.append("the table has no rows")
        if blanks:
            problems.append(f"{blanks} blank date(s)")
        if n_rows != distinct:
            problems.append(f"dates are not unique ({n_rows} rows, {distinct} distinct values)")
        if with_time:
            problems.append(
                f"{with_time} value(s) carry a time of day (one row per whole day needed)"
            )
        if n_rows and distinct_days != span:
            problems.append(
                f"dates are not contiguous ({distinct_days} distinct days over a "
                f"{span}-day span; {span - distinct_days} day(s) missing)"
            )
        return {"success": True, "ok": not problems, "problems": problems, "stats": stats}

    def mark_as_date_table(
        self, table_name: str, date_column: Optional[str] = None
    ) -> Dict[str, Any]:
        """Mark ``table_name`` as the model's date table on ``date_column`` (TOM equivalent
        of "Mark as date table": ``Table.DataCategory = 'Time'`` + ``Column.IsKey``).

        Validated BEFORE anything is written: the column must exist and be DateTime; its
        data must be unique, blank-free, whole days and contiguous (one DAX query); no other
        column of the table may already be the key. A failing check writes nothing.
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")
        if not self._valid_identifier(table_name):
            return error_envelope(
                "invalid_parameters", "Table name must be non-empty and <=128 chars"
            )
        if not date_column:
            return error_envelope(
                "missing_argument",
                "date_column is required (the DateTime column with one row per day)",
            )
        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }
        try:
            table = next((t for t in model.Tables if t.Name == table_name), None)
            if not table:
                return error_envelope("table_not_found", f"Table '{table_name}' not found")
            column = next((c for c in table.Columns if c.Name == date_column), None)
            if not column:
                return error_envelope(
                    "column_not_found", f"Column '{date_column}' not found in table '{table_name}'"
                )
            dtype = str(getattr(column, "DataType", "")).split(".")[-1]
            if dtype.lower() != "datetime":
                return error_envelope(
                    "invalid_date_column",
                    f"Column '{date_column}' is {dtype}; a date table needs a DateTime column",
                )
            other_keys = [
                str(c.Name)
                for c in table.Columns
                if c.Name != date_column and bool(getattr(c, "IsKey", False))
            ]
            if other_keys:
                return error_envelope(
                    "another_key_column",
                    f"Column(s) {other_keys} are already marked as key in '{table_name}'; "
                    "clear is_key on them first (02 column update is_key=false).",
                )
            check = self.check_date_column(table_name, date_column)
            if not check.get("success"):
                return check
            if not check["ok"]:
                return {
                    "success": False,
                    "error": (
                        f"'{table_name}'[{date_column}] cannot be a date table: "
                        + "; ".join(check["problems"])
                    ),
                    "error_type": "invalid_date_column",
                    "problems": check["problems"],
                    "stats": check["stats"],
                }
            previous_category = getattr(table, "DataCategory", None)
            table.DataCategory = "Time"
            column.IsKey = True
            info = commit(model, label="table/mark_as_date_table")
            return {
                "success": True,
                "action": "marked_as_date_table",
                "table": table_name,
                "date_column": date_column,
                "previous_data_category": previous_category,
                "validated": check["stats"],
                **info,
            }
        except Exception as e:
            try:
                safe_undo(model)
            except Exception:  # noqa: BLE001
                pass
            logger.error("mark_as_date_table failed: %s", e)
            return error_envelope("tom_error", str(e))
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass
