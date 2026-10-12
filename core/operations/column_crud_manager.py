"""
Column CRUD Manager for MCP-PowerBi-Finvision
Provides comprehensive column management: get, create, update, delete, rename
"""

import logging
from typing import Dict, Any, Optional

from core.dax.dax_rename import rename_column_refs
from core.operations import model_readback, rename_flow
from core.operations.rename_cascade import cascade_column_rename
from core.operations.write_lane import commit
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

AMO_AVAILABLE = False
AMOServer = None
Column = None
DataColumn = None
CalculatedColumn = None

try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    load_amo_assemblies()
    from Microsoft.AnalysisServices.Tabular import (  # noqa: F401
        Server as AMOServer,
        Column,
        DataColumn,
        CalculatedColumn,
        DataType,
    )

    AMO_AVAILABLE = True
    logger.info("AMO available for column CRUD operations")
except Exception as e:
    logger.warning(f"AMO not available for column CRUD: {e}")


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


class ColumnCRUDManager(AmoModelMixin):
    """Manage column CRUD operations using TOM."""

    def __init__(self, connection):
        """Initialize with ADOMD connection."""
        self.connection = connection

    def _valid_identifier(self, s: Optional[str]) -> bool:
        """Validate identifier (column name, etc.)."""
        return bool(s) and len(str(s).strip()) > 0 and len(str(s)) <= 128 and "\0" not in str(s)

    def _connection_error_msg(self) -> str:
        """P1-32: build a connection error message that includes the real
        AMO cause captured in `_last_amo_error`, rather than the generic
        "Could not connect to model"."""
        cause = getattr(self, "_last_amo_error", None) or "unknown cause"
        return f"Could not connect to model: {cause}"

    _DATA_TYPE_NAMES = (
        "String",
        "Int64",
        "Double",
        "Decimal",
        "Boolean",
        "DateTime",
        "Binary",
        "Variant",
        "Unknown",
    )

    def _parse_data_type(self, data_type_str: str):
        """Parse a data type string to the TOM DataType enum (case-insensitive).

        C-25: an unknown name ("Integer", "Date") used to fall back to String
        silently — on UPDATE that changed an existing column's type. It now
        raises ValueError, which the callers turn into ``invalid_parameters``.
        """
        from Microsoft.AnalysisServices.Tabular import DataType  # noqa: F811

        key = str(data_type_str or "").strip().lower()
        for name in self._DATA_TYPE_NAMES:
            if name.lower() == key:
                return getattr(DataType, name)
        raise ValueError(
            f"Invalid data_type {data_type_str!r}. Valid: {', '.join(self._DATA_TYPE_NAMES[:-1])}"
        )

    def get_column(self, table_name: str, column_name: str) -> Dict[str, Any]:
        """
        Get detailed information about a specific column.

        Args:
            table_name: Name of the table
            column_name: Name of the column

        Returns:
            Result dictionary with column details
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")

        if not self._valid_identifier(table_name) or not self._valid_identifier(column_name):
            return error_envelope(
                "invalid_parameters", "Table and column names must be non-empty and <=128 chars"
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

            # Find column
            column = next((c for c in table.Columns if c.Name == column_name), None)
            if not column:
                return error_envelope(
                    "column_not_found", f"Column '{column_name}' not found in table '{table_name}'"
                )

            # Build column info
            column_info = {
                "name": column.Name,
                "data_type": str(column.DataType),
                "description": column.Description if hasattr(column, "Description") else None,
                "is_hidden": column.IsHidden if hasattr(column, "IsHidden") else False,
                "display_folder": (
                    column.DisplayFolder if hasattr(column, "DisplayFolder") else None
                ),
                "format_string": column.FormatString if hasattr(column, "FormatString") else None,
                "is_key": column.IsKey if hasattr(column, "IsKey") else False,
                "sort_by_column": (
                    column.SortByColumn.Name
                    if hasattr(column, "SortByColumn") and column.SortByColumn
                    else None
                ),
            }

            # Sep 2026 indexing behaviour (only when the loaded TOM exposes it) - read-back
            # for column update string_indexing_behavior / full_text_indexing_behavior.
            for key, attr in (
                ("string_indexing_behavior", "StringIndexingBehavior"),
                ("full_text_indexing_behavior", "FullTextIndexingBehavior"),
            ):
                if hasattr(column, attr):
                    column_info[key] = str(getattr(column, attr)).split(".")[-1].lower()

            # Check if calculated column
            if hasattr(column, "Expression") and column.Expression:
                column_info["type"] = "calculated"
                column_info["expression"] = column.Expression
            else:
                column_info["type"] = "data"
                column_info["source_column"] = (
                    column.SourceColumn if hasattr(column, "SourceColumn") else None
                )

            return {"success": True, "table": table_name, "column": column_info}

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error getting column: {error_msg}", exc_info=True)
            return error_envelope("retrieval_error", error_msg)

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def create_column(
        self,
        table_name: str,
        column_name: str,
        data_type: Optional[str] = None,
        expression: Optional[str] = None,
        description: Optional[str] = None,
        hidden: bool = False,
        display_folder: Optional[str] = None,
        format_string: Optional[str] = None,
        source_column: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new column (data or calculated column).

        ``data_type`` is optional: a data column defaults to String, a
        calculated column keeps the engine-inferred type unless one is given.

        Args:
            table_name: Name of the table
            column_name: Name of the column to create
            data_type: Data type (String, Int64, Double, Decimal, Boolean, DateTime, etc.)
            expression: DAX expression for calculated column (optional)
            description: Column description (optional)
            hidden: Whether to hide the column (default: False)
            display_folder: Display folder for organization (optional)
            format_string: Format string (optional)
            source_column: Source column name for data columns (optional)

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available - cannot create columns")

        if not self._valid_identifier(table_name) or not self._valid_identifier(column_name):
            return error_envelope(
                "invalid_parameters", "Table and column names must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            res = self._apply_create_column(
                model,
                table_name=table_name,
                column_name=column_name,
                data_type=data_type,
                expression=expression,
                description=description,
                hidden=hidden,
                display_folder=display_folder,
                format_string=format_string,
                source_column=source_column,
            )
            if res.get("success"):
                res.update(
                    commit(
                        model,
                        label="column/create",
                        verify=lambda: model_readback.column_exists(table_name, column_name),
                    )
                )
                logger.info(
                    f"Created {res.get('column_type')} column '{column_name}' "
                    f"in table '{table_name}'"
                )
            return res

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error creating column: {error_msg}", exc_info=True)
            return error_envelope("creation_error", error_msg)

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_create_column(
        self,
        model,
        *,
        table_name,
        column_name,
        data_type=None,
        expression=None,
        description=None,
        hidden=False,
        display_folder=None,
        format_string=None,
        source_column=None,
    ) -> Dict[str, Any]:
        """Mutate `model` to add a column. Does NOT SaveChanges or Disconnect."""
        if data_type is not None:
            try:
                parsed_type = self._parse_data_type(data_type)
            except ValueError as e:
                return error_envelope("invalid_parameters", str(e))
        elif expression:
            parsed_type = None  # calculated column: let the engine infer the type
        else:
            parsed_type = self._parse_data_type("String")
        # Find table
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if not table:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")

        # Check if column already exists
        existing_column = next((c for c in table.Columns if c.Name == column_name), None)
        if existing_column:
            return error_envelope(
                "column_exists", f"Column '{column_name}' already exists in table '{table_name}'"
            )

        # Determine column type and create
        if expression:
            # Create calculated column
            from Microsoft.AnalysisServices.Tabular import CalculatedColumn  # noqa: F811

            column = CalculatedColumn()
            column.Name = column_name
            column.Expression = expression
            column_type = "calculated"
        else:
            # Create data column
            from Microsoft.AnalysisServices.Tabular import DataColumn  # noqa: F811

            column = DataColumn()
            column.Name = column_name
            if source_column:
                column.SourceColumn = source_column
            column_type = "data"

        # Set common properties
        if parsed_type is not None:
            column.DataType = parsed_type

        if description:
            column.Description = description
        if hidden:
            column.IsHidden = hidden
        if display_folder:
            column.DisplayFolder = display_folder
        if format_string:
            column.FormatString = format_string

        # Add column to table
        table.Columns.Add(column)

        return {
            "success": True,
            "action": "created",
            "table": table_name,
            "column": column_name,
            "column_type": column_type,
            "data_type": (
                data_type if data_type is not None else ("String" if not expression else None)
            ),
            "expression": expression,
            "message": f"Successfully created {column_type} column '{column_name}'",
        }

    def update_column(
        self,
        table_name: str,
        column_name: str,
        expression: Optional[str] = None,
        description: Optional[str] = None,
        hidden: Optional[bool] = None,
        display_folder: Optional[str] = None,
        format_string: Optional[str] = None,
        new_name: Optional[str] = None,
        data_type: Optional[str] = None,
        sort_by_column: Optional[str] = None,
        summarize_by: Optional[str] = None,
        data_category: Optional[str] = None,
        is_key: Optional[bool] = None,
        is_nullable: Optional[bool] = None,
        clear_sort_by_column: bool = False,
        preview: bool = False,
        preview_hash: Optional[str] = None,
        string_indexing_behavior: Optional[str] = None,
        full_text_indexing_behavior: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing column.

        A rename (``new_name``) cascades every DAX reference, is verified after
        SaveChanges and rolled back on new expression errors (C-01/C-40);
        ``preview`` / ``preview_hash`` as in ``TableCRUDManager.update_table``.

        Args:
            table_name: Name of the table
            column_name: Current name of the column
            expression: New DAX expression for calculated column (optional)
            description: New description (optional)
            hidden: Set hidden state (optional)
            display_folder: New display folder (optional)
            format_string: New format string (optional)
            new_name: New column name (optional)

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")

        if not self._valid_identifier(table_name) or not self._valid_identifier(column_name):
            return error_envelope(
                "invalid_parameters", "Table and column names must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            renaming = bool(new_name) and new_name != column_name
            if renaming and (preview or preview_hash):
                pv = self._preview_rename(model, table_name, column_name, new_name)
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
            res = self._apply_update_column(
                model,
                table_name=table_name,
                column_name=column_name,
                expression=expression,
                description=description,
                hidden=hidden,
                display_folder=display_folder,
                format_string=format_string,
                new_name=new_name,
                data_type=data_type,
                sort_by_column=sort_by_column,
                summarize_by=summarize_by,
                data_category=data_category,
                is_key=is_key,
                is_nullable=is_nullable,
                clear_sort_by_column=clear_sort_by_column,
                record=record,
                string_indexing_behavior=string_indexing_behavior,
                full_text_indexing_behavior=full_text_indexing_behavior,
            )
            if res.get("success"):
                if renaming:
                    outcome = rename_flow.finish(
                        model,
                        label="column/rename",
                        baseline=baseline,
                        record=record,
                        verify_landed=lambda: model_readback.column_exists(table_name, new_name),
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
                        lambda e: rename_column_refs(e, table_name, column_name, new_name)
                    )
                    if untouched:
                        res["untouched"] = untouched
                else:
                    res.update(commit(model, label="column/update"))
                logger.info(
                    f"Updated column '{column_name}' in table '{table_name}': "
                    f"{', '.join(res.get('updates', []))}"
                )
            return res

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error updating column: {error_msg}", exc_info=True)
            return error_envelope("update_error", error_msg)

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_update_column(
        self,
        model,
        *,
        table_name,
        column_name,
        expression=None,
        description=None,
        hidden=None,
        display_folder=None,
        format_string=None,
        new_name=None,
        data_type=None,
        sort_by_column=None,
        summarize_by=None,
        data_category=None,
        is_key=None,
        is_nullable=None,
        clear_sort_by_column=False,
        record=None,
        string_indexing_behavior=None,
        full_text_indexing_behavior=None,
    ) -> Dict[str, Any]:
        """Mutate `model` to update a column. Does NOT SaveChanges or Disconnect.

        ``record`` (a list) receives every renamed site so the caller can
        verify and roll the rename back exactly.
        """
        # Find table
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if not table:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")

        # Find column
        column = next((c for c in table.Columns if c.Name == column_name), None)
        if not column:
            return error_envelope(
                "column_not_found", f"Column '{column_name}' not found in table '{table_name}'"
            )

        updates = []

        # Update expression (calculated columns only)
        if expression is not None:
            if hasattr(column, "Expression"):
                column.Expression = expression
                updates.append("expression")
            else:
                return error_envelope(
                    "invalid_operation", "Column is not a calculated column - cannot set expression"
                )

        # Update description
        if description is not None:
            column.Description = description
            updates.append("description")

        # Update hidden state
        if hidden is not None:
            column.IsHidden = hidden
            updates.append("hidden")

        # Update display folder
        if display_folder is not None:
            column.DisplayFolder = display_folder
            updates.append("display_folder")

        # Update format string
        if format_string is not None:
            column.FormatString = format_string
            updates.append("format_string")

        # Update data type
        if data_type is not None:
            try:
                parsed = self._parse_data_type(data_type)
            except ValueError as dt_err:
                return error_envelope("invalid_parameters", str(dt_err))
            try:
                column.DataType = parsed
                updates.append(f"data_type={data_type}")
            except Exception as dt_err:
                return error_envelope(
                    "invalid_data_type", f"Failed to set data_type '{data_type}': {dt_err}"
                )

        # Update SummarizeBy (None|Sum|Min|Max|Count|Average|DistinctCount)
        if summarize_by is not None:
            try:
                from Microsoft.AnalysisServices.Tabular import AggregateFunction

                sb_map = {
                    "default": AggregateFunction.Default,
                    # C-15: pythonnet 3 exposes the member only as "None".
                    "none": getattr(AggregateFunction, "None"),
                    "sum": AggregateFunction.Sum,
                    "min": AggregateFunction.Min,
                    "max": AggregateFunction.Max,
                    "count": AggregateFunction.Count,
                    "average": AggregateFunction.Average,
                    "distinctcount": AggregateFunction.DistinctCount,
                }
                key = str(summarize_by).strip().lower()
                if key not in sb_map:
                    return {
                        "success": False,
                        "error": f"Invalid summarize_by '{summarize_by}'. Valid: {', '.join(sb_map.keys())}",
                        "error_type": "invalid_parameters",
                    }
                column.SummarizeBy = sb_map[key]
                updates.append(f"summarize_by={summarize_by}")
            except Exception as sb_err:
                return error_envelope("update_error", f"Failed to set summarize_by: {sb_err}")

        # Update DataCategory (free-form string, e.g. 'WebUrl', 'ImageUrl', 'Address', ...)
        if data_category is not None:
            try:
                column.DataCategory = data_category
                updates.append(f"data_category={data_category}")
            except Exception as dc_err:
                return error_envelope("update_error", f"Failed to set data_category: {dc_err}")

        # Update IsKey
        if is_key is not None:
            try:
                column.IsKey = bool(is_key)
                updates.append(f"is_key={is_key}")
            except Exception as ik_err:
                return error_envelope("update_error", f"Failed to set is_key: {ik_err}")

        # Update IsNullable
        if is_nullable is not None:
            try:
                column.IsNullable = bool(is_nullable)
                updates.append(f"is_nullable={is_nullable}")
            except Exception as in_err:
                return error_envelope("update_error", f"Failed to set is_nullable: {in_err}")

        # String / full-text indexing behaviour (Sep 2026; never upgrades the compat level)
        if string_indexing_behavior is not None or full_text_indexing_behavior is not None:
            from core.operations.column_indexing import apply_indexing_behavior

            idx_updates, idx_error = apply_indexing_behavior(
                model,
                column,
                string_indexing_behavior=string_indexing_behavior,
                full_text_indexing_behavior=full_text_indexing_behavior,
            )
            if idx_error:
                return idx_error
            updates.extend(idx_updates)

        # Update SortByColumn
        if clear_sort_by_column:
            try:
                column.SortByColumn = None
                updates.append("sort_by_column=<cleared>")
            except Exception as sc_err:
                return error_envelope("update_error", f"Failed to clear sort_by_column: {sc_err}")
        elif sort_by_column is not None:
            target = next((c for c in table.Columns if c.Name == sort_by_column), None)
            if target is None:
                return error_envelope(
                    "column_not_found",
                    f"sort_by_column target '{sort_by_column}' not found in table '{table_name}'",
                )
            if target.Name == column.Name:
                return error_envelope(
                    "invalid_parameters", "sort_by_column cannot reference the column itself"
                )
            try:
                column.SortByColumn = target
                updates.append(f"sort_by_column={sort_by_column}")
            except Exception as sc_err:
                return error_envelope("update_error", f"Failed to set sort_by_column: {sc_err}")

        # Update name (with DAX cascade — TOM does NOT auto-cascade DAX references)
        cascaded = []
        if new_name and self._valid_identifier(new_name) and new_name != column.Name:
            # Check if new name already exists (columns and measures share the
            # table namespace; DAX names are case-insensitive)
            lowered = str(new_name).lower()
            if any(str(c.Name).lower() == lowered for c in table.Columns if c != column) or any(
                str(getattr(m, "Name", "")).lower() == lowered
                for m in getattr(table, "Measures", []) or []
            ):
                return error_envelope(
                    "name_conflict", f"Column '{new_name}' already exists in table '{table_name}'"
                )
            old_col_name = column.Name
            column.Name = new_name
            updates.append("name")
            if record is not None:
                record.append(
                    rename_flow.name_record(
                        column, old_col_name, new_name, f"column '{table_name}'[{old_col_name}]"
                    )
                )

            # Cascade DAX references: 'Table'[OldCol] -> 'Table'[NewCol]
            # Note: relationships, sort-by-column, hierarchy levels, perspectives
            # are object references in TOM and survive renames automatically.
            cascaded = cascade_column_rename(
                model, table_name, old_col_name, new_name, record=record
            )

        result = {
            "success": True,
            "action": "updated",
            "table": table_name,
            "column": new_name if new_name else column_name,
            "original_name": column_name if new_name else None,
            "updates": updates,
            "message": f"Successfully updated column '{column_name}'",
        }
        if cascaded:
            result["cascaded_references"] = len(cascaded)
            result["updated_expressions"] = cascaded
        return result

    def delete_column(self, table_name: str, column_name: str) -> Dict[str, Any]:
        """
        Delete a column.

        Args:
            table_name: Name of the table
            column_name: Name of the column to delete

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")

        if not self._valid_identifier(table_name) or not self._valid_identifier(column_name):
            return error_envelope(
                "invalid_parameters", "Table and column names must be non-empty and <=128 chars"
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            res = self._apply_delete_column(model, table_name=table_name, column_name=column_name)
            if res.get("success"):

                def _gone():
                    exists = model_readback.column_exists(table_name, column_name)
                    return None if exists is None else not exists

                res.update(commit(model, label="column/delete", verify=_gone))
                logger.info(f"Deleted column '{column_name}' from table '{table_name}'")
            return res

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error deleting column: {error_msg}", exc_info=True)
            return {
                "success": False,
                "error": error_msg,
                "error_type": "deletion_error",
                "suggestions": [
                    "Check if column is used in relationships",
                    "Verify column is not referenced by measures or calculated columns",
                    "Ensure column is not used as a sort-by column",
                ],
            }

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _apply_delete_column(self, model, *, table_name, column_name) -> Dict[str, Any]:
        """Mutate `model` to remove a column. Does NOT SaveChanges or Disconnect."""
        # Find table
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if not table:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")

        # Find column
        column = next((c for c in table.Columns if c.Name == column_name), None)
        if not column:
            return error_envelope(
                "column_not_found", f"Column '{column_name}' not found in table '{table_name}'"
            )

        # Remove column
        table.Columns.Remove(column)

        return {
            "success": True,
            "action": "deleted",
            "table": table_name,
            "column": column_name,
            "message": f"Successfully deleted column '{column_name}'",
        }

    def _apply_rename_column(
        self, model, *, table_name, column_name, new_name, record=None
    ) -> Dict[str, Any]:
        """Mutate `model` to rename a column (DAX cascade included). Does NOT
        SaveChanges or Disconnect. Mirrors the rename path of _apply_update_column.
        Note: the public `rename_column` keeps delegating to `update_column` (so the
        PBIR cascade path is preserved); this helper exists for the single-
        connection atomic batch path."""
        return self._apply_update_column(
            model, table_name=table_name, column_name=column_name, new_name=new_name, record=record
        )

    def _preview_rename(self, model, table_name, column_name, new_name) -> Dict[str, Any]:
        """Dry-run the column rename cascade on `model` (no mutation)."""
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if table is None:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")
        if not any(c.Name == column_name for c in table.Columns):
            return error_envelope(
                "column_not_found", f"Column '{column_name}' not found in table '{table_name}'"
            )
        record: list = []
        cascade_column_rename(model, table_name, column_name, new_name, record=record, apply=False)
        return rename_flow.preview_payload(
            "column", f"{table_name}[{column_name}]", new_name, record
        )

    def preview_rename(self, table_name: str, column_name: str, new_name: str) -> Dict[str, Any]:
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
            return self._preview_rename(model, table_name, column_name, new_name)
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def rename_column(
        self,
        table_name: str,
        column_name: str,
        new_name: str,
        pbip_path: Optional[str] = None,
        preview: bool = False,
        preview_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Rename a column.

        Args:
            table_name: Name of the table
            column_name: Current column name
            new_name: New column name
            pbip_path: Optional path to the .pbip file/folder. When provided,
                the rename is cascaded across the bound PBIR report after
                the live model rename succeeds. PBI Desktop must be closed.

        Returns:
            Result dictionary with success status
        """
        result = self.update_column(
            table_name=table_name,
            column_name=column_name,
            new_name=new_name,
            preview=preview,
            preview_hash=preview_hash,
        )
        if result.get("success") and pbip_path:
            from core.operations.rename_cascade_pbir_bridge import apply_pbir_cascade

            pbir_result = apply_pbir_cascade(
                pbip_path=pbip_path,
                kind="column",
                old_name=column_name,
                new_name=new_name,
                table_name=table_name,
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
