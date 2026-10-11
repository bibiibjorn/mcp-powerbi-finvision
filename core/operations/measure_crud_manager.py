"""
Measure CRUD Manager for MCP-PowerBi-Finvision
Provides measure rename and move operations
"""

import logging
from typing import Dict, Any, Optional, List

from core.autonomous.clr_errors import format_clr_error
from core.dax.dax_rename import rename_measure_refs
from core.operations import model_readback, rename_flow
from core.operations.rename_cascade import cascade_measure_rename
from core.operations.write_lane import commit
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

AMO_AVAILABLE = False
AMOServer = None
Measure = None

try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    load_amo_assemblies()
    from Microsoft.AnalysisServices.Tabular import Server as AMOServer, Measure  # noqa: F401

    AMO_AVAILABLE = True
    logger.info("AMO available for measure CRUD operations")
except Exception as e:
    logger.warning(f"AMO not available for measure CRUD: {e}")


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


def _measure_landed(
    table_name: str, measure_name: str, expression: Optional[str] = None
) -> Optional[bool]:
    """Read-back for write_lane.commit: does the measure exist (with *expression*)?

    None when the model cannot be read (the write stays unverified).
    """
    row = model_readback.find_measure(table_name, measure_name)
    if row is None:
        return None
    if not row:
        return False
    if expression is not None:
        return (row.get("expression") or "").strip() == str(expression).strip()
    return True


def _expression_round_trip(
    table_name: str, measure_name: str, expression: Optional[str]
) -> Optional[Dict[str, Any]]:
    """S-21: after a SUCCESSFUL save, compare the stored expression with the one sent.

    ``None`` when they match (or cannot be read); otherwise an ``expression_changed_on_store``
    payload (sha of both + the first differing code point) - e.g. a non-ASCII literal the
    engine / transport normalised. Line endings are ignored.
    """
    if not expression:
        return None
    try:
        row = model_readback.find_measure(table_name, measure_name)
    except Exception:  # noqa: BLE001 - diagnostic only
        return None
    if not row:
        return None
    sent = str(expression).replace("\r\n", "\n").strip()
    stored = str(row.get("expression") or "").replace("\r\n", "\n").strip()
    if sent == stored:
        return None
    import hashlib

    idx = next(
        (i for i, (a, b) in enumerate(zip(sent, stored)) if a != b), min(len(sent), len(stored))
    )
    sent_cp = f"U+{ord(sent[idx]):04X}" if idx < len(sent) else None
    stored_cp = f"U+{ord(stored[idx]):04X}" if idx < len(stored) else None
    return {
        "sent_sha": hashlib.sha1(sent.encode("utf-8", "replace")).hexdigest()[:12],
        "stored_sha": hashlib.sha1(stored.encode("utf-8", "replace")).hexdigest()[:12],
        "first_diff": {"index": idx, "sent_codepoint": sent_cp, "stored_codepoint": stored_cp},
        "message": (
            "The stored expression differs from the one sent (e.g. a non-ASCII character was "
            "normalised). Build such literals with UNICHAR() and re-read the measure."
        ),
    }


def _measure_gone(table_name: str, measure_name: str) -> Optional[bool]:
    row = model_readback.find_measure(table_name, measure_name)
    if row is None:
        return None
    return not row


def _set_format_string_definition(measure, expression: Optional[str]) -> None:
    """Set (or clear, with an empty value) a measure's FormatStringDefinition."""
    if not expression:
        measure.FormatStringDefinition = None
        return
    from Microsoft.AnalysisServices.Tabular import FormatStringDefinition

    fsd = FormatStringDefinition()
    fsd.Expression = expression
    measure.FormatStringDefinition = fsd


def _same_clr_object(a, b) -> bool:
    if a is None or b is None:
        return False
    if a is b:
        return True
    try:
        return bool(a == b)
    except Exception:  # noqa: BLE001
        return False


def _find_measure_anywhere(model, name: str):
    """(table, measure) of a measure named *name* (case-insensitive) or (None, None)."""
    lowered = str(name).lower()
    for t in model.Tables:
        for m in t.Measures:
            if str(m.Name).lower() == lowered:
                return t, m
    return None, None


class MeasureCRUDManager(AmoModelMixin):
    """Manage measure CRUD operations using TOM."""

    def __init__(self, connection):
        """Initialize with ADOMD connection."""
        self.connection = connection

    def _valid_identifier(self, s: Optional[str]) -> bool:
        """Validate identifier (measure name, etc.)."""
        return bool(s) and len(str(s).strip()) > 0 and len(str(s)) <= 128 and "\0" not in str(s)

    def _connection_error_msg(self) -> str:
        """P1-32: build a connection error message that includes the real
        AMO cause captured in `_last_amo_error`, rather than the generic
        "Could not connect to model"."""
        cause = getattr(self, "_last_amo_error", None) or "unknown cause"
        return f"Could not connect to model: {cause}"

    def rename_measure(
        self,
        table_name: str,
        measure_name: str,
        new_name: str,
        pbip_path: Optional[str] = None,
        preview: bool = False,
        preview_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Rename a measure.

        Args:
            table_name: Table containing the measure
            measure_name: Current measure name
            new_name: New measure name
            pbip_path: Optional path to the .pbip file/folder. When provided,
                the rename is cascaded across the bound PBIR report after
                the live model rename succeeds. PBI Desktop must be closed.
            preview: dry-run — return the cascade diff + ``preview_hash``.
            preview_hash: refuse to apply when the diff changed since preview.

        The cascade is verified after SaveChanges and rolled back when it
        introduced new expression errors (C-01/C-40).

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available - cannot rename measures")

        if (
            not self._valid_identifier(table_name)
            or not self._valid_identifier(measure_name)
            or not self._valid_identifier(new_name)
        ):
            return error_envelope(
                "invalid_parameters",
                "Table name, measure name, and new name must be non-empty and <=128 chars",
            )

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        try:
            if preview or preview_hash:
                pv = self._preview_rename(model, table_name, measure_name, new_name)
                if preview or not pv.get("success"):
                    if preview and pv.get("success") and pbip_path:
                        from core.operations.rename_cascade_pbir_bridge import apply_pbir_cascade

                        pv["pbir_preview"] = apply_pbir_cascade(
                            pbip_path=pbip_path,
                            kind="measure",
                            old_name=measure_name,
                            new_name=new_name,
                            table_name=table_name,
                            dry_run=True,
                        )
                    return pv
                if pv.get("preview_hash") != preview_hash:
                    return {
                        "success": False,
                        "error_type": "stale_preview",
                        "error": "The model changed since the rename preview; re-run preview=true.",
                        "expected": preview_hash,
                        "current": pv.get("preview_hash"),
                    }

            baseline = rename_flow.snapshot_errors()
            record: List[Dict[str, Any]] = []
            res = self._apply_rename_measure(
                model,
                table_name=table_name,
                measure_name=measure_name,
                new_name=new_name,
                record=record,
            )
            if not res.get("success"):
                return res
            old_name = measure_name
            cascaded = res.get("updated_expressions") or []

            outcome = rename_flow.finish(
                model,
                label="measure/rename",
                baseline=baseline,
                record=record,
                verify_landed=lambda: _measure_landed(table_name, new_name),
            )
            if outcome.get("success") is False:
                return outcome

            logger.info(
                f"Renamed measure '{old_name}' to '{new_name}' in table '{table_name}', cascaded {len(cascaded)} references"
            )

            result = {
                "success": True,
                "action": "renamed",
                "table": table_name,
                "old_name": old_name,
                "new_name": new_name,
                "message": f"Successfully renamed measure from '{old_name}' to '{new_name}'",
                **outcome,
            }
            if cascaded:
                result["cascaded_references"] = len(cascaded)
                result["updated_expressions"] = cascaded
            untouched = [
                r for r in rename_flow.public_records(record) if r.get("kind") == "untouched"
            ]
            untouched += rename_flow.udf_reference_report(
                lambda e: rename_measure_refs(e, old_name, new_name, home_table=table_name)
            )
            if untouched:
                result["untouched"] = untouched

            if pbip_path:
                from core.operations.rename_cascade_pbir_bridge import apply_pbir_cascade

                pbir_result = apply_pbir_cascade(
                    pbip_path=pbip_path,
                    kind="measure",
                    old_name=old_name,
                    new_name=new_name,
                    table_name=table_name,
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

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error renaming measure: {error_msg}", exc_info=True)
            return error_envelope("rename_error", error_msg)

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def set_format_string_expression(
        self,
        table_name: str,
        measure_name: str,
        expression: Optional[str],
    ) -> Dict[str, Any]:
        """Set or clear the dynamic format string expression on a measure.

        Targets ``Measure.FormatStringDefinition.Expression`` (compatibility
        level 1606+). Passing ``None`` or an empty string clears the
        FormatStringDefinition (sets it to ``None``).
        """
        if not (self._valid_identifier(table_name) and self._valid_identifier(measure_name)):
            return error_envelope(
                "invalid_parameters",
                "Invalid table_name or measure_name (empty, too long, or contains NUL)",
            )

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
            if not table:
                return error_envelope("table_not_found", f"Table '{table_name}' not found")

            measure = next((m for m in table.Measures if m.Name == measure_name), None)
            if not measure:
                return error_envelope(
                    "measure_not_found",
                    f"Measure '{measure_name}' not found in table '{table_name}'",
                )

            cleared = False
            if not expression:
                measure.FormatStringDefinition = None
                cleared = True
            else:
                from Microsoft.AnalysisServices.Tabular import FormatStringDefinition

                fsd = FormatStringDefinition()
                fsd.Expression = expression
                measure.FormatStringDefinition = fsd

            commit(model, label="measure/set_format_string_expression")

            return {
                "success": True,
                "action": "cleared" if cleared else "set",
                "table": table_name,
                "measure": measure_name,
                "cleared": cleared,
            }

        except Exception as e:
            logger.error(
                f"Error setting format string expression on '{measure_name}': {e}",
                exc_info=True,
            )
            return format_clr_error(
                e,
                error_type="format_string_error",
                table=table_name,
                measure=measure_name,
            )

        finally:
            try:
                if server:
                    server.Disconnect()
            except Exception:
                pass

    def set_detail_rows_expression(
        self,
        table_name: str,
        measure_name: str,
        expression: Optional[str],
    ) -> Dict[str, Any]:
        """Set or clear the DetailRowsDefinition expression on a measure.

        Targets ``Measure.DetailRowsDefinition.Expression`` (CL 1400+). The
        expression is a DAX query (typically ``SELECTCOLUMNS(...)``) that
        powers ``DETAILROWS()`` and Excel's "Show details" drill. Passing
        ``None`` or an empty string clears the DetailRowsDefinition.
        """
        if not (self._valid_identifier(table_name) and self._valid_identifier(measure_name)):
            return error_envelope(
                "invalid_parameters",
                "Invalid table_name or measure_name (empty, too long, or contains NUL)",
            )

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
            if not table:
                return error_envelope("table_not_found", f"Table '{table_name}' not found")

            measure = next((m for m in table.Measures if m.Name == measure_name), None)
            if not measure:
                return error_envelope(
                    "measure_not_found",
                    f"Measure '{measure_name}' not found in table '{table_name}'",
                )

            cleared = False
            if not expression:
                measure.DetailRowsDefinition = None
                cleared = True
            else:
                from Microsoft.AnalysisServices.Tabular import DetailRowsDefinition

                drd = DetailRowsDefinition()
                drd.Expression = expression
                measure.DetailRowsDefinition = drd

            commit(model, label="measure/set_detail_rows_expression")

            return {
                "success": True,
                "action": "cleared" if cleared else "set",
                "table": table_name,
                "measure": measure_name,
                "cleared": cleared,
            }

        except Exception as e:
            logger.error(
                f"Error setting detail rows expression on '{measure_name}': {e}",
                exc_info=True,
            )
            return format_clr_error(
                e,
                error_type="detail_rows_error",
                table=table_name,
                measure=measure_name,
            )

        finally:
            try:
                if server:
                    server.Disconnect()
            except Exception:
                pass

    def set_kpi(
        self,
        table_name: str,
        measure_name: str,
        target_expression: Optional[str] = None,
        status_expression: Optional[str] = None,
        trend_expression: Optional[str] = None,
        status_graphic: Optional[str] = None,
        trend_graphic: Optional[str] = None,
        target_description: Optional[str] = None,
        status_description: Optional[str] = None,
        trend_description: Optional[str] = None,
        target_format_string: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update the KPI on a measure.

        Requires at least one of ``target_expression``, ``status_expression``,
        ``trend_expression`` to be a non-empty string. Args left as ``None``
        are not touched on an existing KPI; on a fresh KPI they remain unset.
        """
        if not (self._valid_identifier(table_name) and self._valid_identifier(measure_name)):
            return error_envelope(
                "invalid_parameters",
                "Invalid table_name or measure_name (empty, too long, or contains NUL)",
            )

        if not (target_expression or status_expression or trend_expression):
            return error_envelope(
                "invalid_parameters",
                "set_kpi requires at least one of target_expression, "
                "status_expression, trend_expression",
            )

        if not AMO_AVAILABLE:
            return error_envelope("amo_unavailable", "AMO not available")

        server, db, model = self._get_server_db_model()
        if not model:
            return {
                "success": False,
                "error": self._connection_error_msg(),
                "error_type": "connection_error",
            }

        # Property name <-> arg map. Lets the loop drive both fresh-create
        # and partial-update paths from the same source.
        kpi_props = {
            "TargetExpression": target_expression,
            "StatusExpression": status_expression,
            "TrendExpression": trend_expression,
            "StatusGraphic": status_graphic,
            "TrendGraphic": trend_graphic,
            "TargetDescription": target_description,
            "StatusDescription": status_description,
            "TrendDescription": trend_description,
            "TargetFormatString": target_format_string,
        }

        try:
            table = next((t for t in model.Tables if t.Name == table_name), None)
            if not table:
                return error_envelope("table_not_found", f"Table '{table_name}' not found")

            measure = next((m for m in table.Measures if m.Name == measure_name), None)
            if not measure:
                return error_envelope(
                    "measure_not_found",
                    f"Measure '{measure_name}' not found in table '{table_name}'",
                )

            existing_kpi = getattr(measure, "KPI", None)
            if existing_kpi is None:
                from Microsoft.AnalysisServices.Tabular import KPI as _KPI

                kpi = _KPI()
                # Status/Trend graphic must be set or AMO will reject save.
                # Default to "Three Symbols UnCircled Colored" if a status
                # expression is provided but no graphic — AMO requires it.
                if status_expression and not status_graphic:
                    kpi_props["StatusGraphic"] = "Three Symbols UnCircled Colored"
                if trend_expression and not trend_graphic:
                    kpi_props["TrendGraphic"] = "Three Arrows Colored"
                action = "created"
            else:
                kpi = existing_kpi
                action = "updated"

            properties_set: List[str] = []
            for prop_name, value in kpi_props.items():
                if value is None:
                    continue
                try:
                    setattr(kpi, prop_name, value)
                    properties_set.append(prop_name)
                except Exception as set_err:  # noqa: BLE001
                    logger.debug(f"Could not set KPI.{prop_name}: {set_err}")

            if existing_kpi is None:
                measure.KPI = kpi

            commit(model, label="measure/set_kpi")

            return {
                "success": True,
                "action": action,
                "table": table_name,
                "measure": measure_name,
                "properties_set": properties_set,
            }

        except Exception as e:
            logger.error(
                f"Error setting KPI on '{measure_name}': {e}",
                exc_info=True,
            )
            return format_clr_error(
                e,
                error_type="kpi_error",
                table=table_name,
                measure=measure_name,
            )

        finally:
            try:
                if server:
                    server.Disconnect()
            except Exception:
                pass

    def clear_kpi(self, table_name: str, measure_name: str) -> Dict[str, Any]:
        """Clear (remove) the KPI on a measure. Idempotent."""
        if not (self._valid_identifier(table_name) and self._valid_identifier(measure_name)):
            return error_envelope(
                "invalid_parameters",
                "Invalid table_name or measure_name (empty, too long, or contains NUL)",
            )

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
            if not table:
                return error_envelope("table_not_found", f"Table '{table_name}' not found")

            measure = next((m for m in table.Measures if m.Name == measure_name), None)
            if not measure:
                return error_envelope(
                    "measure_not_found",
                    f"Measure '{measure_name}' not found in table '{table_name}'",
                )

            was_present = getattr(measure, "KPI", None) is not None
            measure.KPI = None
            commit(model, label="measure/clear_kpi")

            return {
                "success": True,
                "action": "cleared",
                "table": table_name,
                "measure": measure_name,
                "was_present": was_present,
            }

        except Exception as e:
            logger.error(
                f"Error clearing KPI on '{measure_name}': {e}",
                exc_info=True,
            )
            return format_clr_error(
                e,
                error_type="kpi_error",
                table=table_name,
                measure=measure_name,
            )

        finally:
            try:
                if server:
                    server.Disconnect()
            except Exception:
                pass

    def get_kpi(self, table_name: str, measure_name: str) -> Dict[str, Any]:
        """Read KPI properties on a measure. Read-only — never calls SaveChanges."""
        if not (self._valid_identifier(table_name) and self._valid_identifier(measure_name)):
            return error_envelope(
                "invalid_parameters",
                "Invalid table_name or measure_name (empty, too long, or contains NUL)",
            )

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
            if not table:
                return error_envelope("table_not_found", f"Table '{table_name}' not found")

            measure = next((m for m in table.Measures if m.Name == measure_name), None)
            if not measure:
                return error_envelope(
                    "measure_not_found",
                    f"Measure '{measure_name}' not found in table '{table_name}'",
                )

            kpi = getattr(measure, "KPI", None)
            if kpi is None:
                return {
                    "success": True,
                    "table": table_name,
                    "measure": measure_name,
                    "present": False,
                }

            attrs = {
                "target_expression": "TargetExpression",
                "status_expression": "StatusExpression",
                "trend_expression": "TrendExpression",
                "status_graphic": "StatusGraphic",
                "trend_graphic": "TrendGraphic",
                "target_description": "TargetDescription",
                "status_description": "StatusDescription",
                "trend_description": "TrendDescription",
                "target_format_string": "TargetFormatString",
            }
            kpi_dict = {}
            for snake, camel in attrs.items():
                # Don't use `val if val else None` — that silently drops
                # `0`, `""`, and other falsy-but-valid values (project gotcha).
                kpi_dict[snake] = getattr(kpi, camel, None)

            return {
                "success": True,
                "table": table_name,
                "measure": measure_name,
                "present": True,
                "kpi": kpi_dict,
            }

        except Exception as e:
            logger.error(
                f"Error reading KPI on '{measure_name}': {e}",
                exc_info=True,
            )
            return format_clr_error(
                e,
                error_type="kpi_error",
                table=table_name,
                measure=measure_name,
            )

        finally:
            try:
                if server:
                    server.Disconnect()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # No-connect primitives (the caller owns connect + commit). Used by the
    # single ops below AND by 03_Batch_Operations' one-SaveChanges batches
    # (C-11) so every measure write shares one code path.
    # ------------------------------------------------------------------

    def _apply_upsert_measure(
        self,
        model,
        *,
        table_name: Optional[str],
        measure_name: Optional[str],
        expression: Optional[str] = None,
        description: Optional[str] = None,
        format_string: Optional[str] = None,
        format_string_expression: Optional[str] = None,
        display_folder: Optional[str] = None,
        data_category: Optional[str] = None,
        hidden: Optional[bool] = None,
        mode: str = "upsert",
    ) -> Dict[str, Any]:
        """Create / update a measure on `model` (no save).

        ``mode``: ``create`` fails with ``already_exists`` when a measure of
        that name exists ANYWHERE in the model (C-12 — measure names are
        model-wide); ``update`` fails with ``measure_not_found`` when it does
        not exist (C-11: a batch update never creates); ``upsert`` does either.
        """
        if not self._valid_identifier(table_name) or not self._valid_identifier(measure_name):
            return {
                "success": False,
                "error": "table_name and measure_name are required (non-empty, <=128 chars)",
                "error_type": "invalid_parameters",
                "table": table_name,
                "measure": measure_name,
            }
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if table is None:
            return {
                "success": False,
                "error": f"Table '{table_name}' not found",
                "error_type": "table_not_found",
                "table": table_name,
                "measure": measure_name,
            }
        existing = next((m for m in table.Measures if m.Name == measure_name), None)
        if existing is None:
            other_table, other = _find_measure_anywhere(model, measure_name)
            if other is not None:
                return {
                    "success": False,
                    "error": (
                        f"Measure '{measure_name}' already exists in table "
                        f"'{other_table.Name}' (measure names are model-wide)"
                    ),
                    "error_type": "already_exists",
                    "existing_table": other_table.Name,
                    "table": table_name,
                    "measure": measure_name,
                }
        if existing is not None and mode == "create":
            return {
                "success": False,
                "error": (
                    f"Measure '{measure_name}' already exists in table '{table_name}'. "
                    "Use operation='update', or if_exists='update' / operation='upsert'."
                ),
                "error_type": "already_exists",
                "table": table_name,
                "measure": measure_name,
            }
        if existing is None and mode == "update":
            return {
                "success": False,
                "error": f"Measure '{measure_name}' not found in table '{table_name}'",
                "error_type": "measure_not_found",
                "table": table_name,
                "measure": measure_name,
                "available_measures": [m.Name for m in table.Measures][:10],
            }

        if existing is not None:
            measure = existing
            if expression:
                measure.Expression = expression
            action = "updated"
        else:
            if not expression:
                return {
                    "success": False,
                    "error": "expression is required when creating a new measure",
                    "error_type": "invalid_parameters",
                    "table": table_name,
                    "measure": measure_name,
                }
            measure = Measure()
            measure.Name = measure_name
            measure.Expression = expression
            action = "created"

        if description is not None:
            measure.Description = description
        if format_string is not None:
            measure.FormatString = format_string
        if display_folder is not None:
            measure.DisplayFolder = display_folder
        if data_category is not None:
            measure.DataCategory = data_category
        if hidden is not None:
            measure.IsHidden = bool(hidden)
        if format_string_expression is not None:
            _set_format_string_definition(measure, format_string_expression)
        if existing is None:
            table.Measures.Add(measure)
        return {
            "success": True,
            "action": action,
            "table": table_name,
            "measure": measure_name,
        }

    def _apply_delete_measure(self, model, *, table_name, measure_name) -> Dict[str, Any]:
        """Remove a measure from `model` (no save)."""
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if table is None:
            return {
                "success": False,
                "error": f"Table '{table_name}' not found",
                "error_type": "table_not_found",
                "table": table_name,
                "measure": measure_name,
            }
        measure = next((m for m in table.Measures if m.Name == measure_name), None)
        if measure is None:
            return {
                "success": False,
                "error": f"Measure '{measure_name}' not found in table '{table_name}'",
                "error_type": "measure_not_found",
                "table": table_name,
                "measure": measure_name,
            }
        table.Measures.Remove(measure)
        return {"success": True, "action": "deleted", "table": table_name, "measure": measure_name}

    def _apply_rename_measure(
        self, model, *, table_name, measure_name, new_name, record=None
    ) -> Dict[str, Any]:
        """Rename a measure + cascade on `model` (no save). Model-wide,
        case-insensitive conflict check (C-32: measure names are model-wide)."""
        if not all(self._valid_identifier(x) for x in (table_name, measure_name, new_name)):
            return error_envelope(
                "invalid_parameters", "table_name, measure_name and new_name are required"
            )
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if table is None:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")
        measure = next((m for m in table.Measures if m.Name == measure_name), None)
        if measure is None:
            return error_envelope(
                "measure_not_found", f"Measure '{measure_name}' not found in table '{table_name}'"
            )
        if str(new_name).lower() != str(measure_name).lower():
            other_table, other = _find_measure_anywhere(model, new_name)
            if other is not None:
                return error_envelope(
                    "name_conflict",
                    f"Measure '{new_name}' already exists in table '{other_table.Name}' "
                    "(measure names are model-wide)",
                )
            if any(str(c.Name).lower() == str(new_name).lower() for c in table.Columns):
                return error_envelope(
                    "name_conflict", f"Table '{table_name}' already has a column named '{new_name}'"
                )
        old_name = measure.Name
        measure.Name = new_name
        if record is not None:
            record.append(
                rename_flow.name_record(
                    measure, old_name, new_name, f"measure '{table_name}'[{old_name}]"
                )
            )
        cascaded = cascade_measure_rename(
            model, old_name, new_name, table_name=table_name, record=record
        )
        return {
            "success": True,
            "action": "renamed",
            "table": table_name,
            "old_name": old_name,
            "new_name": new_name,
            "cascaded_references": len(cascaded),
            "updated_expressions": cascaded,
        }

    def _preview_rename(self, model, table_name, measure_name, new_name) -> Dict[str, Any]:
        """Dry-run the measure rename cascade (no mutation)."""
        table = next((t for t in model.Tables if t.Name == table_name), None)
        if table is None:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")
        if not any(m.Name == measure_name for m in table.Measures):
            return error_envelope(
                "measure_not_found", f"Measure '{measure_name}' not found in table '{table_name}'"
            )
        record: List[Dict[str, Any]] = []
        cascade_measure_rename(
            model, measure_name, new_name, table_name=table_name, record=record, apply=False
        )
        return rename_flow.preview_payload(
            "measure", f"{table_name}[{measure_name}]", new_name, record
        )

    def preview_rename(self, table_name: str, measure_name: str, new_name: str) -> Dict[str, Any]:
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
            return self._preview_rename(model, table_name, measure_name, new_name)
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Single-object ops built on the primitives
    # ------------------------------------------------------------------

    def _single_write(self, label: str, apply_fn, verify=None, roundtrip=None) -> Dict[str, Any]:
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
            res = apply_fn(model)
            if res.get("success"):
                res.update(commit(model, label=label, verify=verify))
                if roundtrip is not None:
                    changed = roundtrip()
                    if changed:
                        res["expression_changed_on_store"] = changed
            return res
        except Exception as e:  # noqa: BLE001 - surfaced with the CLR root cause
            logger.error("%s failed: %s", label, e, exc_info=True)
            return format_clr_error(e, error_type=label.replace("/", "_") + "_error")
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def create_measure(self, table_name: str, measure_name: str, **props) -> Dict[str, Any]:
        """Create a measure; ``already_exists`` when the name is taken (C-12)."""
        mode = props.pop("mode", "create")
        return self._single_write(
            f"measure/{'upsert' if mode == 'upsert' else 'create'}",
            lambda model: self._apply_upsert_measure(
                model, table_name=table_name, measure_name=measure_name, mode=mode, **props
            ),
            verify=lambda: _measure_landed(table_name, measure_name, props.get("expression")),
            roundtrip=lambda: _expression_round_trip(
                table_name, measure_name, props.get("expression")
            ),
        )

    def update_measure(self, table_name: str, measure_name: str, **props) -> Dict[str, Any]:
        """Update an existing measure; ``measure_not_found`` otherwise."""
        return self._single_write(
            "measure/update",
            lambda model: self._apply_upsert_measure(
                model, table_name=table_name, measure_name=measure_name, mode="update", **props
            ),
            verify=lambda: _measure_landed(
                table_name, measure_name, props.get("expression") or None
            ),
            roundtrip=lambda: _expression_round_trip(
                table_name, measure_name, props.get("expression") or None
            ),
        )

    def delete_measure(self, table_name: str, measure_name: str) -> Dict[str, Any]:
        return self._single_write(
            "measure/delete",
            lambda model: self._apply_delete_measure(
                model, table_name=table_name, measure_name=measure_name
            ),
            verify=lambda: _measure_gone(table_name, measure_name),
        )

    def batch_rename_measures(
        self, items: List[Dict[str, Any]], options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Rename many measures on one connection with ONE SaveChanges.

        C-32: conflicts are checked model-wide and case-insensitively; the
        whole rename graph is validated up front (no chains ``A→B, B→C``, no
        two items targeting one name); all-or-nothing unless
        ``options.continue_on_error`` (with ``use_transaction=false``). The
        result is verified and rolled back on new expression errors.
        """
        options = dict(options or {})
        norm = []
        seen_old, seen_new = {}, {}
        graph_errors = []
        for idx, item in enumerate(items or []):
            t = item.get("table_name") or item.get("table")
            m = item.get("measure_name") or item.get("measure") or item.get("name")
            n = item.get("new_name")
            if not (t and m and n):
                graph_errors.append({"index": idx, "error": "Missing table, measure, or new_name"})
                continue
            key_old, key_new = str(m).lower(), str(n).lower()
            if key_new in seen_new:
                graph_errors.append({"index": idx, "error": f"two items rename to '{n}'"})
            if key_new in seen_old or key_old in seen_new:
                graph_errors.append(
                    {
                        "index": idx,
                        "error": f"chained rename involving '{m}'/'{n}' — split the batch",
                    }
                )
            seen_old[key_old] = idx
            seen_new[key_new] = idx
            norm.append({"table_name": t, "measure_name": m, "new_name": n})
        if graph_errors:
            return {
                "success": False,
                "error_type": "invalid_rename_batch",
                "error": "The rename batch is not a valid one-step rename set.",
                "validation_errors": graph_errors,
            }

        from core.operations.batch_operations import run_atomic_batch

        options["_operation"] = "rename"
        return run_atomic_batch(
            norm,
            options,
            lambda model, item, record: self._apply_rename_measure(
                model,
                table_name=item["table_name"],
                measure_name=item["measure_name"],
                new_name=item["new_name"],
                record=record,
            ),
            label="measures/rename",
            verify_rename=True,
        )

    def _apply_set_display_folder(
        self, model, *, table_name: str, measure_name: str, display_folder: str
    ) -> Dict[str, Any]:
        """No-connect, no-save mutate: set a measure's DisplayFolder (P1).

        Keyword-only mutate helper so a batch can acquire the AMO model ONCE
        and apply many display-folder moves under a single SaveChanges (mirrors
        the table/column _apply_* helpers). The caller owns connect + save.
        """
        tbl = next((t for t in model.Tables if t.Name == table_name), None)
        if tbl is None:
            return error_envelope("table_not_found", f"Table '{table_name}' not found")
        meas = next((m for m in tbl.Measures if m.Name == measure_name), None)
        if meas is None:
            return error_envelope(
                "measure_not_found", f"Measure '{measure_name}' not found in '{table_name}'"
            )
        meas.DisplayFolder = display_folder or ""
        return {
            "success": True,
            "table": table_name,
            "measure": measure_name,
            "display_folder": display_folder or "",
        }

    # ------------------------------------------------------------------ move

    def _apply_move_measure(
        self, model, *, source_table: str, measure_name: str, target_table: str
    ) -> Dict[str, Any]:
        """Move a measure between tables on `model` (no save).

        TOM has no re-parent, so the move is remove + re-create. C-31: the new
        object keeps LineageTag / SourceLineageTag, extended properties,
        annotations, KPI, detail rows, dynamic format string, culture
        translations and perspective membership — everything that would
        otherwise silently disappear with the removed object.
        """
        if not all(self._valid_identifier(x) for x in (source_table, measure_name, target_table)):
            return error_envelope(
                "invalid_parameters",
                "Source table, measure name, and target table must be non-empty and <=128 chars",
            )
        src_table = next((t for t in model.Tables if t.Name == source_table), None)
        if not src_table:
            return error_envelope("table_not_found", f"Source table '{source_table}' not found")
        tgt_table = next((t for t in model.Tables if t.Name == target_table), None)
        if not tgt_table:
            return error_envelope("table_not_found", f"Target table '{target_table}' not found")
        measure = next((m for m in src_table.Measures if m.Name == measure_name), None)
        if not measure:
            return error_envelope(
                "measure_not_found", f"Measure '{measure_name}' not found in table '{source_table}'"
            )
        if source_table == target_table:
            return {"success": True, "action": "noop", "measure": measure_name}
        if any(str(m.Name).lower() == str(measure_name).lower() for m in tgt_table.Measures) or any(
            str(c.Name).lower() == str(measure_name).lower() for c in tgt_table.Columns
        ):
            return error_envelope(
                "name_conflict", f"'{measure_name}' already exists in target table '{target_table}'"
            )

        props = {
            "Expression": measure.Expression,
            "Description": getattr(measure, "Description", None) or "",
            "FormatString": getattr(measure, "FormatString", None) or "",
            "DisplayFolder": getattr(measure, "DisplayFolder", None) or "",
            "IsHidden": bool(getattr(measure, "IsHidden", False)),
            "DataCategory": getattr(measure, "DataCategory", None) or "",
            "LineageTag": getattr(measure, "LineageTag", None) or "",
            "SourceLineageTag": getattr(measure, "SourceLineageTag", None) or "",
        }
        annotations = []
        for ann in list(getattr(measure, "Annotations", None) or []):
            annotations.append((ann.Name, ann.Value))
        ext_props = []
        for ep in list(getattr(measure, "ExtendedProperties", None) or []):
            ext_props.append((type(ep).__name__, ep.Name, getattr(ep, "Value", None)))
        kpi_props: Dict[str, Any] = {}
        kpi = getattr(measure, "KPI", None)
        if kpi is not None:
            for attr in (
                "TargetExpression",
                "StatusExpression",
                "TrendExpression",
                "TargetDescription",
                "StatusDescription",
                "TrendDescription",
                "TargetFormatString",
                "StatusGraphic",
                "TrendGraphic",
            ):
                kpi_props[attr] = getattr(kpi, attr, None)
        drd = getattr(measure, "DetailRowsDefinition", None)
        detail_rows_expr = getattr(drd, "Expression", None) if drd is not None else None
        fsd = getattr(measure, "FormatStringDefinition", None)
        fsd_expr = getattr(fsd, "Expression", None) if fsd is not None else None

        # Translations + perspective membership reference the object: detach
        # them BEFORE removing it, re-attach to the new object afterwards.
        translations = []
        for culture in list(getattr(model, "Cultures", None) or []):
            coll = getattr(culture, "ObjectTranslations", None)
            for ot in list(coll or []):
                # pythonnet hands out a fresh proxy per access: `is` never
                # matches a CLR object — compare with Equals (verified offline
                # against the real TOM assembly).
                if _same_clr_object(getattr(ot, "Object", None), measure):
                    translations.append((culture, getattr(ot, "Property", None), ot.Value))
                    coll.Remove(ot)
        perspectives = []
        for persp in list(getattr(model, "Perspectives", None) or []):
            for pt in list(getattr(persp, "PerspectiveTables", None) or []):
                if getattr(pt, "Name", None) != source_table:
                    continue
                for pm in list(getattr(pt, "PerspectiveMeasures", None) or []):
                    if getattr(pm, "Name", None) == measure_name:
                        perspectives.append(persp)
                        pt.PerspectiveMeasures.Remove(pm)

        src_table.Measures.Remove(measure)

        new_measure = Measure()
        new_measure.Name = measure_name
        for attr, value in props.items():
            if attr in ("LineageTag", "SourceLineageTag", "DataCategory") and not value:
                continue
            try:
                setattr(new_measure, attr, value)
            except Exception as e:  # noqa: BLE001
                logger.debug("move: could not restore %s: %s", attr, e)
        tgt_table.Measures.Add(new_measure)

        import Microsoft.AnalysisServices.Tabular as TOM  # type: ignore

        restored: List[str] = []
        for name, value in annotations:
            try:
                ann = TOM.Annotation()
                ann.Name = name
                ann.Value = value
                new_measure.Annotations.Add(ann)
            except Exception as e:  # noqa: BLE001
                logger.debug("move: annotation %s not restored: %s", name, e)
        if annotations:
            restored.append("annotations")
        for type_name, name, value in ext_props:
            try:
                ctor = getattr(TOM, type_name, None) or TOM.StringExtendedProperty
                ep = ctor()
                ep.Name = name
                ep.Value = value
                new_measure.ExtendedProperties.Add(ep)
            except Exception as e:  # noqa: BLE001
                logger.debug("move: extended property %s not restored: %s", name, e)
        if ext_props:
            restored.append("extended_properties")
        if detail_rows_expr:
            d = TOM.DetailRowsDefinition()
            d.Expression = detail_rows_expr
            new_measure.DetailRowsDefinition = d
            restored.append("detail_rows")
        if fsd_expr:
            f = TOM.FormatStringDefinition()
            f.Expression = fsd_expr
            new_measure.FormatStringDefinition = f
            restored.append("format_string_definition")
        if kpi is not None:
            k = TOM.KPI()
            for attr, val in kpi_props.items():
                if val is None:
                    continue
                try:
                    setattr(k, attr, val)
                except Exception as e:  # noqa: BLE001
                    logger.debug("move: KPI.%s not restored: %s", attr, e)
            new_measure.KPI = k
            restored.append("kpi")
        for culture, prop, value in translations:
            culture.ObjectTranslations.SetTranslation(new_measure, prop, value)
        if translations:
            restored.append(f"translations({len(translations)})")
        for persp in perspectives:
            pt = next(
                (p for p in persp.PerspectiveTables if getattr(p, "Name", None) == target_table),
                None,
            )
            if pt is None:
                pt = TOM.PerspectiveTable()
                pt.Table = tgt_table
                persp.PerspectiveTables.Add(pt)
            pm = TOM.PerspectiveMeasure()
            pm.Measure = new_measure
            pt.PerspectiveMeasures.Add(pm)
        if perspectives:
            restored.append(f"perspectives({len(perspectives)})")

        return {
            "success": True,
            "action": "moved",
            "measure": measure_name,
            "source_table": source_table,
            "target_table": target_table,
            "preserved": restored + ["lineage_tag"] * bool(props["LineageTag"]),
            "message": f"Successfully moved measure '{measure_name}' from '{source_table}' to '{target_table}'",
        }

    def move_measure(
        self,
        source_table: str,
        measure_name: str,
        target_table: str,
        pbip_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Move a measure to a different table (see ``_apply_move_measure``).

        Visuals bind a measure as ``Entity=<home table>`` — after a move the
        bound PBIR visuals still name the old table; the result carries a
        ``pbir_followup`` hint (the PBIR bridge has no home-table rebind kind).
        """
        res = self._single_write(
            "measure/move",
            lambda model: self._apply_move_measure(
                model,
                source_table=source_table,
                measure_name=measure_name,
                target_table=target_table,
            ),
            verify=lambda: _measure_landed(target_table, measure_name),
        )
        if res.get("success") and res.get("action") == "moved":
            res["pbir_followup"] = (
                f"Report visuals that bind '{measure_name}' still reference Entity "
                f"'{source_table}'. Rebind them with 07_Report_Operations operation=rebind "
                f"(old '{source_table}'[{measure_name}] -> new '{target_table}'[{measure_name}])"
                + (f" in {pbip_path}" if pbip_path else "")
                + ", then reload."
            )
        return res
