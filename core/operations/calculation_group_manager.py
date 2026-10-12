"""
Calculation Group Manager — direct AMO layer (P2-24 two-layer split).

Two-layer convention for model operations:
  *_manager.py    (this layer) — direct AMO/TOM access. Stateless helpers
                                 that take an AMO connection/model and return
                                 raw dicts. No handler dispatch, no arg validation
                                 beyond what TOM enforces.
  *_operations.py (handler dispatch layer) — BaseOperationsHandler subclass.
                                 Validates args, resolves managers via
                                 get_manager_or_error, calls *_manager.py,
                                 formats results.

Rule: cross-layer logic belongs in amo_helpers or *_operations.py, NOT
duplicated in both files.
"""

import logging
import re
from typing import Dict, Any, List, Optional

from core.autonomous.clr_errors import format_clr_error
from core.operations.amo_mixins import AmoModelMixin

logger = logging.getLogger(__name__)

AMO_AVAILABLE = True  # Determined lazily per-connection


def _read_calc_item_format_string(item):
    """Read the format string expression across TOM versions."""
    try:
        fsd = getattr(item, "FormatStringDefinition", None)
        if fsd is not None:
            expr = getattr(fsd, "Expression", None)
            if expr:
                return str(expr)
    except Exception:
        pass
    try:
        if hasattr(item, "FormatStringExpression"):
            v = item.FormatStringExpression
            return str(v) if v else None
    except Exception:
        pass
    return None


def _set_calc_item_format_string(item, expression: str, Tabular) -> bool:
    """Set the calculation item's format string expression across TOM versions.

    Newer TOM exposes FormatStringDefinition (a complex object with .Expression);
    older TOM exposes FormatStringExpression as a flat string. Try the modern
    path first, then fall back. Returns True on success, False if neither works.
    """
    try:
        FSDef = getattr(Tabular, "FormatStringDefinition", None)
        if FSDef is not None:
            fsd = FSDef()
            fsd.Expression = expression
            item.FormatStringDefinition = fsd
            return True
    except Exception:
        pass
    try:
        if hasattr(item, "FormatStringExpression"):
            item.FormatStringExpression = expression
            return True
    except Exception:
        pass
    return False


#: F-20c: compatibility level each selection feature needs (measured against the loaded TOM, which
#: enforces them at assignment time with a CompatibilityViolationException).
SELECTION_EXPRESSION_MIN_CL = 1605
SELECTION_BEHAVIOR_MIN_CL = 1609
SELECTION_BEHAVIORS = {"automatic": "Automatic", "visual": "Visual", "nonvisual": "NonVisual"}

_MIN_CL_RE = re.compile(r"minimal compat\w* level of (\d+)", re.IGNORECASE)


def _compat_violation(exc: Exception) -> Optional[int]:
    """The required compatibility level when ``exc`` is TOM's CompatibilityViolationException."""
    if "CompatibilityViolation" not in type(exc).__name__ and "compatib" not in str(exc).lower():
        return None
    m = _MIN_CL_RE.search(str(exc))
    return int(m.group(1)) if m else 0


def _selection_expression_text(cg, attr: str) -> Optional[str]:
    try:
        holder = getattr(cg, attr, None)
        expr = getattr(holder, "Expression", None) if holder is not None else None
        return str(expr) if expr else None
    except Exception:
        return None


class CalculationGroupManager(AmoModelMixin):
    """Manage calculation groups and items."""

    def __init__(self, connection):
        """Initialize with ADOMD connection."""
        self.connection = connection

    def _connect_amo_server_db(self):
        """Open a TOM Server and return (server, db, TabularModule) or (None, None, None).

        P2-37: delegates to `core.operations.amo_helpers.get_server_db_model` so
        database discovery uses the DMV DBSCHEMA_CATALOGS lookup rather than
        blindly picking `server.Databases[0]`. The previous local implementation
        silently returned the wrong database on multi-model msmdsrv instances.
        """
        try:
            server, db, _model = self._get_server_db_model()
            err = self._last_amo_error
            if err or server is None or db is None:
                logger.warning(f"AMO not available for calc groups: {err}")
                return None, None, None
            import Microsoft.AnalysisServices.Tabular as Tabular  # type: ignore

            return server, db, Tabular
        except Exception as _e:
            logger.warning(f"AMO not available for calc groups: {_e}")
            return None, None, None

    def _list_calculation_groups_via_dax(self) -> Dict[str, Any]:
        """List calculation groups from the TMSCHEMA DMVs (no AMO session).

        C-30: the previous DAX path filtered ``INFO.TABLES()`` on a
        ``[TABLE_TYPE]`` column that does not exist, so it ALWAYS failed and
        every list paid an extra round trip before the AMO fallback; it also
        read item names from a column named like the table. The DMVs give the
        full picture — precedence, item expressions, ordinals and format-string
        expressions — over the gated query executor.
        """
        from core.operations import model_readback

        groups = model_readback.dmv_rows("TMSCHEMA_CALCULATION_GROUPS", "INFO.CALCULATIONGROUPS()")
        if groups is None:
            return {"success": False, "error": "calculation-group DMVs unavailable"}
        items = (
            model_readback.dmv_rows("TMSCHEMA_CALCULATION_ITEMS", "INFO.CALCULATIONITEMS()") or []
        )
        fsds = (
            model_readback.dmv_rows(
                "TMSCHEMA_FORMAT_STRING_DEFINITIONS", "INFO.FORMATSTRINGDEFINITIONS()"
            )
            or []
        )
        tables = model_readback.table_names_by_id() or {}
        fsd_by_id = {r.get("ID"): r.get("Expression") for r in fsds}
        calc_groups = []
        for g in groups:
            table_name = tables.get(g.get("TableID"), str(g.get("TableID")))
            g_items = [
                {
                    "name": it.get("Name"),
                    "expression": it.get("Expression"),
                    "ordinal": it.get("Ordinal"),
                    "format_string_expression": fsd_by_id.get(it.get("FormatStringDefinitionID")),
                }
                for it in items
                if it.get("CalculationGroupID") == g.get("ID")
            ]
            g_items.sort(key=lambda i: (i["ordinal"] if isinstance(i["ordinal"], int) else 0))
            calc_groups.append(
                {
                    "name": table_name,
                    "table": table_name,
                    "precedence": g.get("Precedence", 0),
                    "description": g.get("Description"),
                    "items": g_items,
                    "item_count": len(g_items),
                }
            )
        return {
            "success": True,
            "calculation_groups": calc_groups,
            "total_groups": len(calc_groups),
            "method": "dmv",
        }

    def list_calculation_groups(self, prefer_amo: bool = False) -> Dict[str, Any]:
        """List all calculation groups and their items.

        P1-29 fix: the DAX-first path cannot populate `expression`,
        `precedence`, or `format_string_expression` for calc items, so
        callers asking for those fields always got None. Added a
        `prefer_amo=True` opt-in so richer data is reachable without
        forcing DAX failure. Callers that need expressions should pass
        prefer_amo=True; callers that just need names / ordinals keep the
        faster DAX path.
        """
        if not prefer_amo:
            dax_result = self._list_calculation_groups_via_dax()
            if dax_result.get("success"):
                return dax_result
            dax_error = dax_result.get("error")
        else:
            dax_error = None

        # Fall back to AMO if DAX approach failed or caller requested richer data.
        server, db, Tabular = self._connect_amo_server_db()
        if not server or not db or not Tabular:
            # Both methods failed / requested AMO and AMO unavailable
            return {
                "success": False,
                "error": "Unable to list calculation groups. AMO not available and DAX query failed.",
                "dax_error": dax_error,
            }
        try:
            model = db.Model

            calc_groups = []

            for table in model.Tables:
                if hasattr(table, "CalculationGroup") and table.CalculationGroup is not None:
                    calc_group = table.CalculationGroup

                    items = []
                    for item in calc_group.CalculationItems:
                        items.append(
                            {
                                "name": item.Name,
                                "expression": item.Expression,
                                "ordinal": item.Ordinal if hasattr(item, "Ordinal") else None,
                                "format_string_expression": _read_calc_item_format_string(item),
                            }
                        )

                    calc_groups.append(
                        {
                            "name": calc_group.Name if hasattr(calc_group, "Name") else table.Name,
                            "table": table.Name,
                            "precedence": (
                                calc_group.Precedence if hasattr(calc_group, "Precedence") else 0
                            ),
                            "description": (
                                calc_group.Description
                                if hasattr(calc_group, "Description")
                                else None
                            ),
                            "multiple_or_empty_selection_expression": _selection_expression_text(
                                calc_group, "MultipleOrEmptySelectionExpression"
                            ),
                            "no_selection_expression": _selection_expression_text(
                                calc_group, "NoSelectionExpression"
                            ),
                            "items": items,
                            "item_count": len(items),
                        }
                    )

            return {
                "success": True,
                "calculation_groups": calc_groups,
                "total_groups": len(calc_groups),
            }

        except Exception as e:
            logger.error(f"Error listing calculation groups: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def create_calculation_group(
        self,
        name: str,
        items: List[Dict[str, Any]],
        description: Optional[str] = None,
        precedence: int = 0,
        column_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a calculation group with items.

        Args:
            name: Name of the calculation group
            items: List of calculation items with 'name' and 'expression'
            description: Optional description
            precedence: Precedence level (default 0)

        Returns:
            Result dictionary
        """
        server, db, Tabular = self._connect_amo_server_db()
        if not server or not db or not Tabular:
            return {"success": False, "error": "AMO not available for calculation groups"}

        if not items:
            return {"success": False, "error": "At least one calculation item is required"}

        try:
            model = db.Model

            # Check if table already exists
            existing_table = next((t for t in model.Tables if t.Name == name), None)
            if existing_table:
                return {
                    "success": False,
                    "error": f"Table '{name}' already exists. Use a different name.",
                }

            # Create table for calculation group
            table = Tabular.Table()
            table.Name = name

            # Mark table as CalculationGroup type if supported
            try:
                if hasattr(Tabular, "TableType") and hasattr(table, "TableType"):
                    table.TableType = Tabular.TableType.CalculationGroup
            except Exception:
                pass

            # Create calculation group
            calc_group = Tabular.CalculationGroup()
            if description:
                calc_group.Description = description
            if hasattr(calc_group, "Precedence"):
                calc_group.Precedence = precedence

            # C-30: the calc-group columns are DataColumns bound to the
            # engine-provided source columns "Name" and "Ordinal" (what Desktop
            # and Tabular Editor create). The old code tried a non-existent
            # CalculationGroupColumn class and fell back to an UNBOUND column.
            try:
                data_col = Tabular.DataColumn()
                data_col.Name = column_name or name
                data_col.SourceColumn = "Name"
                data_col.DataType = Tabular.DataType.String
                table.Columns.Add(data_col)
                ord_col = Tabular.DataColumn()
                ord_col.Name = "Ordinal"
                ord_col.SourceColumn = "Ordinal"
                ord_col.DataType = Tabular.DataType.Int64
                ord_col.IsHidden = True
                table.Columns.Add(ord_col)
                try:
                    data_col.SortByColumn = ord_col
                except Exception as sb_err:  # noqa: BLE001
                    logger.debug("calc group sort-by Ordinal not set: %s", sb_err)
            except Exception as inner_e:
                return {
                    "success": False,
                    "error": "Failed to create calculation group columns",
                    "details": str(inner_e),
                }

            # Calculation groups require implicit measures to be discouraged
            # (Desktop flips this switch when the first calc group is added).
            discouraged_now = False
            try:
                if not bool(getattr(model, "DiscourageImplicitMeasures", True)):
                    model.DiscourageImplicitMeasures = True
                    discouraged_now = True
            except Exception as dim_err:  # noqa: BLE001
                logger.debug("DiscourageImplicitMeasures not set: %s", dim_err)

            # Add calculation items
            for idx, item_data in enumerate(items):
                item = Tabular.CalculationItem()
                item.Name = item_data.get("name")
                item.Expression = item_data.get("expression")

                if "ordinal" in item_data and hasattr(item, "Ordinal"):
                    item.Ordinal = item_data["ordinal"]
                elif hasattr(item, "Ordinal"):
                    item.Ordinal = idx

                if (
                    "format_string_expression" in item_data
                    and item_data["format_string_expression"]
                ):
                    _set_calc_item_format_string(
                        item, item_data["format_string_expression"], Tabular
                    )

                calc_group.CalculationItems.Add(item)

            table.CalculationGroup = calc_group

            # CRITICAL: TOM requires calculation group tables to have a partition with CalculationGroup source
            # This partition must exist before SaveChanges() validation
            try:
                part = Tabular.Partition()
                part.Name = f"{name}_Partition"

                # Calculation groups require a CalculationGroupSource (not M or Query source)
                try:
                    cg_source = Tabular.CalculationGroupSource()
                    part.Source = cg_source
                    logger.info(f"Created CalculationGroupSource partition for '{name}'")
                except Exception as cg_error:
                    # Fallback for older TOM: try creating without explicit source type
                    logger.warning(f"CalculationGroupSource not available: {cg_error}")
                    # Some TOM versions auto-create partition source for calc groups
                    pass

                # Set DataView to Full if available (required for some TOM versions)
                if hasattr(part, "DataView"):
                    try:
                        if hasattr(Tabular, "DataViewType"):
                            part.DataView = Tabular.DataViewType.Full
                        else:
                            part.DataView = 0  # numeric: 0 = Full
                    except Exception as dv_error:
                        logger.debug(f"DataView setting failed: {dv_error}")

                # Add the partition to the table BEFORE adding table to model
                table.Partitions.Add(part)
                logger.info(f"Added CalculationGroup partition to table '{name}'")

            except Exception as part_error:
                logger.warning(f"Failed to create partition for calculation group: {part_error}")
                # Continue - SaveChanges will provide specific error if partition is critical

            # Add table to model
            model.Tables.Add(table)

            # Save changes - this will validate the complete structure
            try:
                from core.operations.write_lane import commit

                commit_info = commit(model, label="calculation_group/create")
                logger.info(f"Created calculation group '{name}' with {len(items)} items")
            except Exception as save_error:
                # Provide helpful error message about common issues
                error_msg = str(save_error)
                suggestions = [
                    "Verify calculation item expressions are valid DAX",
                    "Ensure calculation group name is unique",
                    "Check that model compatibility level supports calculation groups (1470+)",
                ]

                if "partition" in error_msg.lower() or "dataview" in error_msg.lower():
                    suggestions.insert(
                        0,
                        "Partition validation failed - this TOM version may have specific DataView requirements",
                    )

                return {"success": False, "error": error_msg, "suggestions": suggestions}

            result = {
                "success": True,
                "action": "created",
                "calculation_group": name,
                "items_count": len(items),
                "message": f"Successfully created calculation group '{name}' with {len(items)} items",
                **commit_info,
            }
            if discouraged_now:
                result["model_change"] = (
                    "discourageImplicitMeasures set to true (required by calculation groups)"
                )
            return result

        except Exception as e:
            logger.error(f"Error creating calculation group: {e}", exc_info=True)
            return format_clr_error(
                e,
                error_type="calc_group_create_error",
                calculation_group=name,
                suggestions=[
                    "Verify calculation item expressions are valid DAX",
                    "Ensure calculation group name is unique",
                    "Check that model compatibility level supports calculation groups (1470+)",
                    "Verify TOM libraries are up to date",
                ],
            )
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def delete_calculation_group(self, name: str) -> Dict[str, Any]:
        """Delete a calculation group."""
        server, db, Tabular = self._connect_amo_server_db()
        if not server or not db or not Tabular:
            return {"success": False, "error": "AMO not available"}
        try:
            model = db.Model

            # Find table with calculation group
            table = next(
                (
                    t
                    for t in model.Tables
                    if t.Name == name
                    and hasattr(t, "CalculationGroup")
                    and t.CalculationGroup is not None
                ),
                None,
            )

            if not table:
                return {"success": False, "error": f"Calculation group '{name}' not found"}

            # Remove table (which contains calculation group)
            model.Tables.Remove(table)
            from core.operations.write_lane import commit

            commit(model, label="calculation_group/delete")

            logger.info(f"Deleted calculation group '{name}'")

            return {
                "success": True,
                "action": "deleted",
                "calculation_group": name,
                "message": f"Successfully deleted calculation group '{name}'",
            }

        except Exception as e:
            logger.error(f"Error deleting calculation group: {e}", exc_info=True)
            return format_clr_error(e, error_type="calc_group_delete_error", calculation_group=name)
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _find_calc_group_table(self, model, group_name: str):
        """Find a table hosting a calculation group by table name."""
        for t in model.Tables:
            try:
                if t.Name == group_name and getattr(t, "CalculationGroup", None) is not None:
                    return t
            except Exception:
                continue
        return None

    def set_precedence(self, group_name: str, precedence: int) -> Dict[str, Any]:
        """Update Precedence on an existing calculation group.

        Precedence determines the order in which multiple calculation groups
        apply when their items intersect. Higher Precedence wins.
        """
        server, db, Tabular = self._connect_amo_server_db()
        if not server or not db or not Tabular:
            return {
                "success": False,
                "error": "AMO not available",
                "error_type": "amo_unavailable",
            }
        try:
            model = db.Model
            table = self._find_calc_group_table(model, group_name)
            if table is None:
                return {
                    "success": False,
                    "error": f"Calculation group '{group_name}' not found",
                    "error_type": "calc_group_not_found",
                }
            cg = table.CalculationGroup
            if not hasattr(cg, "Precedence"):
                return {
                    "success": False,
                    "error": "TOM does not expose Precedence on this CalculationGroup",
                    "error_type": "tom_unsupported",
                }
            cg.Precedence = int(precedence)
            from core.operations.write_lane import commit

            commit(model, label="calculation_group/set_precedence")
            return {
                "success": True,
                "action": "updated",
                "group": group_name,
                "precedence": int(precedence),
            }
        except Exception as e:
            logger.error(f"set_precedence failed: {e}", exc_info=True)
            return format_clr_error(
                e,
                error_type="calc_group_precedence_error",
                calculation_group=group_name,
            )
        finally:
            try:
                if server:
                    server.Disconnect()
            except Exception:
                pass

    def update_calculation_group(
        self,
        group_name: str,
        description: Optional[str] = None,
        precedence: Optional[int] = None,
        hidden: Optional[bool] = None,
        multiple_or_empty_selection_expression: Optional[str] = None,
        no_selection_expression: Optional[str] = None,
        selection_expression_behavior: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a calculation group's description / precedence / hidden flag in ONE save (QG-01).

        F-20c: ``multiple_or_empty_selection_expression`` / ``no_selection_expression`` set what
        SELECTEDMEASURE-style calculations return when several or no items are selected (empty
        string clears it; compatibility level 1605+); ``selection_expression_behavior``
        (automatic | visual | nonvisual) sets Model.SelectionExpressionBehavior, which is MODEL-wide
        (CL 1609+). A level below the requirement is refused before anything is written.

        ``precedence`` must not collide with another group's (the engine rejects duplicates and
        an ambiguous order is never what the caller meant); ``hidden`` is the hosting table's
        IsHidden. Rename goes through ``calculation_group rename`` (a table rename).
        """
        sel_exprs = {
            "MultipleOrEmptySelectionExpression": multiple_or_empty_selection_expression,
            "NoSelectionExpression": no_selection_expression,
        }
        behavior = None
        if selection_expression_behavior is not None:
            behavior = SELECTION_BEHAVIORS.get(str(selection_expression_behavior).strip().lower())
            if behavior is None:
                return {
                    "success": False,
                    "error": (
                        f"selection_expression_behavior must be one of "
                        f"{list(SELECTION_BEHAVIORS.values())}, got {selection_expression_behavior!r}"
                    ),
                    "error_type": "invalid_parameters",
                }
        has_selection = behavior is not None or any(v is not None for v in sel_exprs.values())
        if description is None and precedence is None and hidden is None and not has_selection:
            return {
                "success": False,
                "error": (
                    "Nothing to update: pass description, precedence, hidden and/or a selection "
                    "expression (multiple_or_empty_selection_expression, no_selection_expression, "
                    "selection_expression_behavior)."
                ),
                "error_type": "invalid_parameters",
            }
        prec_int = None
        if precedence is not None:
            try:
                prec_int = int(precedence)
            except (TypeError, ValueError):
                return {
                    "success": False,
                    "error": f"precedence must be an integer (got {precedence!r})",
                    "error_type": "invalid_parameters",
                }
        server, db, Tabular = self._connect_amo_server_db()
        if not server or not db or not Tabular:
            return {"success": False, "error": "AMO not available", "error_type": "amo_unavailable"}
        try:
            model = db.Model
            table = self._find_calc_group_table(model, group_name)
            if table is None:
                return {
                    "success": False,
                    "error": f"Calculation group '{group_name}' not found",
                    "error_type": "calc_group_not_found",
                }
            cg = table.CalculationGroup
            if has_selection:
                level = getattr(db, "CompatibilityLevel", None)
                needs = []
                if any(v is not None for v in sel_exprs.values()):
                    needs.append(("selection expressions", SELECTION_EXPRESSION_MIN_CL))
                if behavior is not None:
                    needs.append(("selection_expression_behavior", SELECTION_BEHAVIOR_MIN_CL))
                for what, minimum in needs:
                    if level is not None and int(level) < minimum:
                        return {
                            "success": False,
                            "error": (
                                f"{what} need compatibility level {minimum}+; this model is at "
                                f"{int(level)}. Raise the compatibility level first "
                                "(model set_properties, irreversible)."
                            ),
                            "error_type": "compatibility_level_too_low",
                            "required_compatibility_level": minimum,
                            "compatibility_level": int(level),
                        }
                if behavior is not None and not hasattr(model, "SelectionExpressionBehavior"):
                    return {
                        "success": False,
                        "error": "The loaded AMO/TOM assemblies do not expose Model.SelectionExpressionBehavior.",
                        "error_type": "feature_unsupported_by_client",
                    }
            if prec_int is not None:
                for other in model.Tables:
                    ocg = getattr(other, "CalculationGroup", None)
                    if (
                        ocg is not None
                        and other.Name != group_name
                        and getattr(ocg, "Precedence", None) == prec_int
                    ):
                        return {
                            "success": False,
                            "error": (
                                f"Precedence {prec_int} is already used by calculation group "
                                f"'{other.Name}'"
                            ),
                            "error_type": "precedence_taken",
                        }
            updated = []
            if description is not None:
                cg.Description = description
                updated.append("description")
            if prec_int is not None:
                cg.Precedence = prec_int
                updated.append("precedence")
            if hidden is not None:
                table.IsHidden = bool(hidden)
                updated.append("hidden")
            for attr, text in sel_exprs.items():
                if text is None:
                    continue
                if str(text).strip() == "":
                    setattr(cg, attr, None)  # an empty string clears the expression
                else:
                    holder = Tabular.CalculationGroupExpression()
                    holder.Expression = str(text)
                    setattr(cg, attr, holder)
                updated.append(
                    "multiple_or_empty_selection_expression"
                    if attr.startswith("Multiple")
                    else "no_selection_expression"
                )
            previous_behavior = None
            if behavior is not None:
                previous_behavior = str(getattr(model, "SelectionExpressionBehavior", "") or "")
                model.SelectionExpressionBehavior = getattr(
                    Tabular.SelectionExpressionBehaviorType, behavior
                )
                updated.append("selection_expression_behavior")
            from core.operations.write_lane import commit

            info = commit(model, label="calculation_group/update")
            extra: Dict[str, Any] = {}
            if behavior is not None:
                extra["selection_expression_behavior"] = behavior
                extra["previous_selection_expression_behavior"] = previous_behavior
                extra["note"] = "selection_expression_behavior is a MODEL-wide setting."
            return {
                "success": True,
                "action": "updated",
                "group": group_name,
                "updated": updated,
                **extra,
                **info,
            }
        except Exception as e:
            try:
                from core.operations.write_lane import safe_undo

                safe_undo(db.Model)
            except Exception:
                pass
            required = _compat_violation(e)
            if required is not None:
                return {
                    "success": False,
                    "error": f"compatibility level too low: {e}",
                    "error_type": "compatibility_level_too_low",
                    "required_compatibility_level": required or None,
                }
            logger.error(f"update_calculation_group failed: {e}", exc_info=True)
            return format_clr_error(
                e, error_type="calc_group_update_error", calculation_group=group_name
            )
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def add_calculation_item(
        self,
        group_name: str,
        item_name: str,
        expression: str,
        ordinal: Optional[int] = None,
        format_string_expression: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a new item to an existing calculation group."""
        if not item_name or expression is None:
            return {"success": False, "error": "item_name and expression are required"}
        server, db, Tabular = self._connect_amo_server_db()
        if not server or not db or not Tabular:
            return {"success": False, "error": "AMO not available"}
        try:
            model = db.Model
            table = self._find_calc_group_table(model, group_name)
            if table is None:
                return {"success": False, "error": f"Calculation group '{group_name}' not found"}
            cg = table.CalculationGroup
            existing = next((i for i in cg.CalculationItems if i.Name == item_name), None)
            if existing is not None:
                return {
                    "success": False,
                    "error": f"Item '{item_name}' already exists in '{group_name}'",
                }
            item = Tabular.CalculationItem()
            item.Name = item_name
            item.Expression = expression
            if ordinal is not None and hasattr(item, "Ordinal"):
                item.Ordinal = ordinal
            if format_string_expression is not None:
                if not _set_calc_item_format_string(item, format_string_expression, Tabular):
                    return {
                        "success": False,
                        "error": "TOM does not expose FormatStringExpression / FormatStringDefinition on this CalculationItem; cannot set format_string_expression",
                    }
            if description:
                item.Description = description
            cg.CalculationItems.Add(item)
            from core.operations.write_lane import commit

            commit(model, label="calculation_group/add_item")
            return {
                "success": True,
                "message": f"Added item '{item_name}' to '{group_name}'",
                "group": group_name,
                "item": item_name,
            }
        except Exception as e:
            logger.error(f"add_calculation_item failed: {e}", exc_info=True)
            return format_clr_error(
                e,
                error_type="calc_item_add_error",
                calculation_group=group_name,
                item=item_name,
            )
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def update_calculation_item(
        self,
        group_name: str,
        item_name: str,
        new_name: Optional[str] = None,
        expression: Optional[str] = None,
        ordinal: Optional[int] = None,
        format_string_expression: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing calculation item. Any field can be renamed or edited."""
        server, db, Tabular = self._connect_amo_server_db()
        if not server or not db or not Tabular:
            return {"success": False, "error": "AMO not available"}
        try:
            model = db.Model
            table = self._find_calc_group_table(model, group_name)
            if table is None:
                return {"success": False, "error": f"Calculation group '{group_name}' not found"}
            cg = table.CalculationGroup
            item = next((i for i in cg.CalculationItems if i.Name == item_name), None)
            if item is None:
                return {
                    "success": False,
                    "error": f"Item '{item_name}' not found in '{group_name}'",
                }
            updates = []
            if new_name and new_name != item_name:
                try:
                    item.RequestRename(new_name)
                except Exception:
                    item.Name = new_name
                updates.append(f"renamed to '{new_name}'")
            if expression is not None:
                item.Expression = expression
                updates.append("expression")
            if ordinal is not None and hasattr(item, "Ordinal"):
                item.Ordinal = ordinal
                updates.append(f"ordinal={ordinal}")
            if format_string_expression is not None:
                if _set_calc_item_format_string(item, format_string_expression, Tabular):
                    updates.append("format_string_expression")
                else:
                    return {
                        "success": False,
                        "error": "TOM does not expose FormatStringExpression / FormatStringDefinition on this CalculationItem; cannot set format_string_expression",
                    }
            if description is not None:
                item.Description = description
                updates.append("description")
            from core.operations.write_lane import commit

            commit(model, label="calculation_group/update_item")
            return {
                "success": True,
                "message": f"Updated item '{item_name}' in '{group_name}': {', '.join(updates) or 'no changes'}",
                "group": group_name,
                "item": new_name or item_name,
            }
        except Exception as e:
            logger.error(f"update_calculation_item failed: {e}", exc_info=True)
            return format_clr_error(
                e,
                error_type="calc_item_update_error",
                calculation_group=group_name,
                item=item_name,
            )
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def delete_calculation_item(self, group_name: str, item_name: str) -> Dict[str, Any]:
        """Delete a calculation item from a calculation group."""
        server, db, Tabular = self._connect_amo_server_db()
        if not server or not db or not Tabular:
            return {"success": False, "error": "AMO not available"}
        try:
            model = db.Model
            table = self._find_calc_group_table(model, group_name)
            if table is None:
                return {"success": False, "error": f"Calculation group '{group_name}' not found"}
            cg = table.CalculationGroup
            item = next((i for i in cg.CalculationItems if i.Name == item_name), None)
            if item is None:
                return {
                    "success": False,
                    "error": f"Item '{item_name}' not found in '{group_name}'",
                }
            cg.CalculationItems.Remove(item)
            from core.operations.write_lane import commit

            commit(model, label="calculation_group/delete_item")
            return {
                "success": True,
                "message": f"Deleted item '{item_name}' from '{group_name}'",
            }
        except Exception as e:
            logger.error(f"delete_calculation_item failed: {e}", exc_info=True)
            return format_clr_error(
                e,
                error_type="calc_item_delete_error",
                calculation_group=group_name,
                item=item_name,
            )
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass
