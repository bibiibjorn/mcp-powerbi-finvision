"""
DAX Injector for PBIXRay MCP Server

Provides live DAX measure injection and modification capabilities.
"""

import logging
from typing import Dict, Any, Optional, Tuple

from core.operations.write_lane import commit


def _valid_amo_name(name: str) -> bool:
    """Validate a name for AMO/TMSL operations (table or measure name).

    Unlike DAX identifiers, AMO object names can contain spaces and special
    characters — they are passed as string properties, not as unquoted DAX tokens.
    """
    return bool(name) and len(name.strip()) > 0 and len(name) <= 128 and "\0" not in name


logger = logging.getLogger(__name__)


def _prepare_amo_server(server):
    """Authenticate an AMO Server for the read-only XMLA target (no-op on Desktop)."""
    from core.infrastructure.xmla_target import prepare_amo_server

    return prepare_amo_server(server)


# Try to load AMO
AMO_AVAILABLE = False
AMOServer = None
Measure = None

try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    load_amo_assemblies()
    from Microsoft.AnalysisServices.Tabular import Server as AMOServer, Measure

    AMO_AVAILABLE = True
    logger.info("AMO available for DAX injection")
except Exception as e:
    logger.warning(f"AMO not available for DAX injection: {e}")


# Try to load ADOMD for database queries
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


def _set_measure_format_string_expression(measure, expression: str) -> bool:
    """Set a dynamic DAX format string on a measure via FormatStringDefinition.

    Mirrors the same logic used for calculation items in calculation_group_manager.py.
    Tries the modern TOM FormatStringDefinition path first, then falls back to
    FormatStringExpression for older TOM versions.
    """
    try:
        import Microsoft.AnalysisServices.Tabular as Tabular  # type: ignore

        FSDef = getattr(Tabular, "FormatStringDefinition", None)
        if FSDef is not None:
            fsd = FSDef()
            fsd.Expression = expression
            measure.FormatStringDefinition = fsd
            return True
    except Exception:
        pass
    try:
        if hasattr(measure, "FormatStringExpression"):
            measure.FormatStringExpression = expression
            return True
    except Exception:
        pass
    return False


def _norm_expr(expr: Any) -> str:
    return str(expr if expr is not None else "").replace("\r\n", "\n").strip()


def _norm_name(name: Any) -> str:
    return " ".join(str(name or "").lower().split())


def _measure_error(m: Any) -> str:
    """Error text TOM attaches to a measure after SaveChanges ('' when valid)."""
    try:
        msg = getattr(m, "ErrorMessage", None)
        if msg:
            return str(msg)
        state = str(getattr(m, "State", "") or "")
        if "error" in state.lower():
            return state
    except Exception:  # noqa: BLE001 - CLR attribute access
        pass
    return ""


def snapshot_measure(m: Any) -> Dict[str, Any]:
    """Everything needed to recreate a deleted measure (K-04: rollback used to
    recreate from the expression only, losing format string / folder / ...)."""

    def g(obj: Any, *attrs: str) -> Any:
        cur = obj
        for a in attrs:
            if cur is None:
                return None
            try:
                cur = getattr(cur, a, None)
            except Exception:  # noqa: BLE001
                return None
        return cur

    def s(v: Any) -> Optional[str]:
        return None if v is None or str(v) == "" else str(v)

    hidden = g(m, "IsHidden")
    return {
        "expression": s(g(m, "Expression")),
        "format_string": s(g(m, "FormatString")),
        "format_string_expression": s(g(m, "FormatStringDefinition", "Expression")),
        "display_folder": s(g(m, "DisplayFolder")),
        "description": s(g(m, "Description")),
        "data_category": s(g(m, "DataCategory")),
        "is_hidden": bool(hidden) if hidden is not None else None,
        "detail_rows_expression": s(g(m, "DetailRowsDefinition", "Expression")),
        "has_kpi": g(m, "KPI") is not None,
    }


def _apply_metadata(m: Any, spec: Dict[str, Any]) -> None:
    if spec.get("format_string") is not None:
        m.FormatString = spec["format_string"]
    if spec.get("format_string_expression") is not None:
        _set_measure_format_string_expression(m, spec["format_string_expression"])
    if spec.get("display_folder") is not None:
        m.DisplayFolder = spec["display_folder"]
    if spec.get("description") is not None:
        m.Description = spec["description"]
    if spec.get("data_category") is not None:
        m.DataCategory = spec["data_category"]
    if spec.get("is_hidden") is not None:
        m.IsHidden = bool(spec["is_hidden"])
    if spec.get("detail_rows_expression") is not None:
        try:
            import Microsoft.AnalysisServices.Tabular as Tabular  # type: ignore

            drd = Tabular.DetailRowsDefinition()
            drd.Expression = spec["detail_rows_expression"]
            m.DetailRowsDefinition = drd
        except Exception:  # noqa: BLE001
            pass


def apply_changes_to_model(
    model: Any,
    upserts: list,
    deletes: Optional[list] = None,
    *,
    measure_factory: Any = None,
) -> Dict[str, Any]:
    """Apply a whole codemod plan to an AMO/TOM ``model`` with ONE ``SaveChanges``.

    Review E-14/E-06/E-02 (pure on the PascalCase TOM surface so tests use fakes):

    - ``upserts``: ``{table_name, measure_name, dax_expression, create_only,
      expected_expression, <metadata>}``. ``create_only`` refuses an existing
      measure (model-scoped name); ``expected_expression`` (not None) must equal
      the current TOM body, else the whole plan aborts as a CONFLICT.
    - ``deletes``: ``{table_name, measure_name, guard_refs, expected_expression}``.
      ``guard_refs`` rescans every DAX-bearing object of the in-memory model
      (after the upserts) and REFUSES the delete while a reference remains; a
      refused delete does not abort the rest of the plan. The deleted measure's
      full snapshot is returned (journal / rollback).
    - any conflict / missing table / exception -> ``UndoLocalChanges`` and
      nothing is committed; after ``SaveChanges`` a measure that now carries an
      error it did not have before reverts the whole plan (``invalid``).
    """
    from core.codemods.model_refs import find_measure_references

    deletes = deletes or []
    factory = measure_factory if measure_factory is not None else Measure
    tables = list(getattr(model, "Tables", []) or [])

    def find_table(name: str):
        exact = next((t for t in tables if t.Name == name), None)
        return exact or next((t for t in tables if _norm_name(t.Name) == _norm_name(name)), None)

    def find_measure(table: Any, name: str):
        ms = list(table.Measures)
        exact = next((m for m in ms if m.Name == name), None)
        return exact or next((m for m in ms if _norm_name(m.Name) == _norm_name(name)), None)

    def measure_anywhere(name: str):
        for t in tables:
            for m in t.Measures:
                if _norm_name(m.Name) == _norm_name(name):
                    return t, m
        return None, None

    conflicts: list = []
    errors: list = []
    undo: list = []  # (kind, table, obj_or_measure, prior)
    written: list = []
    prior_error: Dict[str, str] = {}

    def revert_in_memory() -> None:
        for kind, table, obj, prior in reversed(undo):
            try:
                if kind == "set":
                    obj.Expression = prior
                elif kind == "remove":
                    table.Measures.Remove(obj)
                elif kind == "readd":
                    new_m = factory()
                    new_m.Name = obj["name"]
                    new_m.Expression = obj["snapshot"].get("expression") or ""
                    _apply_metadata(new_m, obj["snapshot"])
                    table.Measures.Add(new_m)
            except Exception as e:  # noqa: BLE001
                logger.warning("in-memory revert step failed: %s", e)

    def abort(error_type: str, error: str) -> Dict[str, Any]:
        revert_in_memory()
        try:
            model.UndoLocalChanges()
        except Exception:  # noqa: BLE001 - not every TOM build exposes it
            pass
        return {
            "success": False,
            "committed": False,
            "atomic": True,
            "error_type": error_type,
            "error": error,
            "conflicts": conflicts,
            "errors": errors,
            "written": [],
            "deleted": [],
            "delete_refused": [],
        }

    try:
        for u in upserts:
            tname, mname = u.get("table_name"), u.get("measure_name")
            key = f"{tname}[{mname}]"
            if not _valid_amo_name(tname or "") or not _valid_amo_name(mname or ""):
                errors.append({"target": key, "error": "invalid name"})
                continue
            table = find_table(tname)
            if table is None:
                errors.append({"target": key, "error": f"table '{tname}' not found"})
                continue
            m = find_measure(table, mname)
            if u.get("create_only"):
                _t, other = measure_anywhere(mname)
                if other is not None:
                    conflicts.append(
                        {"target": key, "error": f"a measure named '{mname}' already exists"}
                    )
                    continue
                if not u.get("dax_expression"):
                    errors.append({"target": key, "error": "empty expression for a new measure"})
                    continue
                new_m = factory()
                new_m.Name = mname
                new_m.Expression = u["dax_expression"]
                _apply_metadata(new_m, u)
                table.Measures.Add(new_m)
                undo.append(("remove", table, new_m, None))
                written.append((key, new_m))
                prior_error[key] = ""
                continue
            if m is None:
                errors.append({"target": key, "error": f"measure '{mname}' not found in '{tname}'"})
                continue
            exp = u.get("expected_expression")
            if exp is not None and _norm_expr(m.Expression) != _norm_expr(exp):
                conflicts.append(
                    {"target": key, "error": "measure changed since the plan was computed"}
                )
                continue
            if u.get("dax_expression") in (None, ""):
                errors.append({"target": key, "error": "empty expression"})
                continue
            prior_error[key] = _measure_error(m)
            undo.append(("set", table, m, m.Expression))
            m.Expression = u["dax_expression"]
            written.append((key, m))

        if conflicts:
            return abort("plan_stale", "a measure changed after the plan was computed")
        if errors:
            return abort("persist_failed", "; ".join(e["error"] for e in errors))

        deleted: list = []
        refused: list = []
        for d in deletes:
            tname, mname = d.get("table_name"), d.get("measure_name")
            key = f"{tname}[{mname}]"
            table = find_table(tname) if tname else None
            m = find_measure(table, mname) if table is not None else None
            if m is None:
                refused.append({"table": tname, "measure": mname, "reason": "not found"})
                continue
            exp = d.get("expected_expression")
            if exp is not None and _norm_expr(m.Expression) != _norm_expr(exp):
                conflicts.append({"target": key, "error": "measure changed since the plan"})
                continue
            if d.get("guard_refs"):
                hits = find_measure_references(
                    model, table.Name, m.Name, exclude_owners={f"{table.Name}[{m.Name}]"}
                )
                if hits:
                    refused.append(
                        {
                            "table": tname,
                            "measure": mname,
                            "reason": "still referenced after the rewrite",
                            "referenced_by": hits[:50],
                        }
                    )
                    continue
            snap = snapshot_measure(m)
            table.Measures.Remove(m)
            undo.append(("readd", table, {"name": m.Name, "snapshot": snap}, None))
            deleted.append({"table": table.Name, "measure": m.Name, "snapshot": snap})
        if conflicts:
            return abort("plan_stale", "a measure changed after the plan was computed")

        if not written and not deleted:
            return {
                "success": True,
                "committed": False,
                "atomic": True,
                "written": [],
                "deleted": [],
                "delete_refused": refused,
                "conflicts": [],
                "errors": [],
            }

        try:
            commit(model, label="dax_injector/apply_measure_changes")
        except Exception as save_e:  # noqa: BLE001
            logger.error("apply_measure_changes SaveChanges failed: %s", save_e)
            return abort("save_failed", f"Commit failed: {save_e}")

        invalid = []
        for key, m in written:
            err = _measure_error(m)
            if err and not prior_error.get(key):
                invalid.append({"target": key, "error": err})
        if invalid:
            # The engine accepted the save but the new DAX does not compile —
            # put everything back and save again (nothing half-applied).
            revert_in_memory()
            reverted = True
            try:
                commit(model, label="dax_injector/apply_measure_changes:revert")
            except Exception as e:  # noqa: BLE001
                reverted = False
                logger.error("revert SaveChanges failed: %s", e)
            return {
                "success": False,
                "committed": False,
                "atomic": True,
                "reverted": reverted,
                "error_type": "invalid_dax",
                "error": "the rewritten DAX does not compile — the plan was reverted",
                "invalid": invalid,
                "written": [],
                "deleted": [],
                "delete_refused": refused,
                "conflicts": [],
                "errors": [],
            }
        return {
            "success": True,
            "committed": True,
            "atomic": True,
            "written": [k for k, _m in written],
            "deleted": deleted,
            "delete_refused": refused,
            "conflicts": [],
            "errors": [],
        }
    except Exception as e:  # noqa: BLE001
        logger.error("apply_measure_changes failed: %s", e, exc_info=True)
        return abort("persist_failed", str(e))


def get_live_injector(connection_state: Any = None) -> "DAXInjector":
    """The ADOMD-bound injector managed by ``connection_state`` (review E-01).

    ``DAXInjector(connection_state)`` — what the codemod transaction, the
    optimizer pipeline and ``MeasureApplier`` used to build — binds the injector
    to the STATE object, which has neither ``.State`` nor ``.ConnectionString``,
    so every write failed with ``connection_lost``. The state's ``_MANAGER_SPECS``
    builds the right one (``DAXInjector(conn)`` + ``link_connection_manager``).
    Raises ``RuntimeError`` when no injector is available (not connected).
    """
    cs = connection_state
    if cs is None:
        from core.infrastructure.connection_state import connection_state as cs
    inj = getattr(cs, "dax_injector", None)
    if inj is None and hasattr(cs, "get_manager"):
        inj = cs.get_manager("dax_injector")
    if inj is None and hasattr(cs, "initialize_managers"):
        try:
            if not hasattr(cs, "is_connected") or cs.is_connected():
                cs.initialize_managers()
                inj = getattr(cs, "dax_injector", None)
        except Exception as e:  # noqa: BLE001
            logger.debug("initialize_managers failed: %s", e)
    if inj is None:
        raise RuntimeError("DAX injector unavailable — connect with 01_Connection first")
    return inj


def _after_write(reason: str) -> None:
    """A TOM write changed the model: invalidate cached DAX results (E-02)."""
    try:
        from core.infrastructure.connection_state import connection_state

        connection_state.bump_model_generation(reason)
    except Exception:  # noqa: BLE001
        pass


class DAXInjector:
    """
    DAX measure injection and modification service.

    Allows creating and updating DAX measures in live Power BI Desktop models.
    """

    def __init__(self, connection):
        """
        Initialize DAX injector.

        Args:
            connection: Active ADOMD connection
        """
        self.connection = connection
        self._connection_manager = None  # Optional reference for reconnection support

    def set_connection_manager(self, connection_manager):
        """
        Set reference to connection manager for reconnection support.

        Args:
            connection_manager: ConnectionManager instance
        """
        self._connection_manager = connection_manager
        logger.debug("DAX injector linked to connection manager for reconnection support")

    def _verify_connection(self) -> Dict[str, Any]:
        """
        Verify connection is alive, attempt reconnection if needed.

        Returns:
            Dict with 'success' (bool) and optional 'error' (str)
        """
        try:
            # Check if ADOMD connection is open
            if self.connection and hasattr(self.connection, "State"):
                state = self.connection.State.ToString()
                if state == "Open":
                    return {"success": True}

            # Connection not open - try reconnection if manager available
            if self._connection_manager:
                logger.warning("DAX Injector: Connection not open, attempting reconnection...")
                result = self._connection_manager.ensure_connected(max_retries=3)
                if result.get("connected"):
                    # Update our connection reference
                    self.connection = self._connection_manager.get_connection()
                    logger.info("DAX Injector: Reconnection successful")
                    return {"success": True, "reconnected": True}
                else:
                    return {
                        "success": False,
                        "error": result.get("error", "Connection lost and reconnection failed"),
                    }

            return {
                "success": False,
                "error": "Connection not open and no connection manager for reconnection",
            }

        except Exception as e:
            logger.error(f"Error verifying connection: {e}")
            return {"success": False, "error": str(e)}

    def _get_database_name(self, server) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Get database name via DMV query with AMO fallback.

        Tries a DMV catalog query first, then falls back to the
        first database exposed by AMO (typical for PBI Desktop).

        Args:
            server: Connected AMO Server instance

        Returns:
            Tuple of (db_name, error_dict). On success,
            error_dict is None. On failure, db_name is empty
            and error_dict contains the error response.
        """
        db_name = None
        try:
            db_query = "SELECT [CATALOG_NAME] " "FROM $SYSTEM.DBSCHEMA_CATALOGS"
            from core.infrastructure.connection_state import connection_state as _cs

            # B-01 (WP1): serialised on the shared connection's command gate.
            with _cs.command_gate(purpose="catalog_name"):
                cmd = AdomdCommand(db_query, self.connection)
                reader = cmd.ExecuteReader()
                try:
                    if reader.Read():
                        db_name = str(reader.GetValue(0))
                finally:
                    # Always close the reader — a raise inside Read()/GetValue()
                    # would otherwise leak an ADOMD reader handle per failure.
                    try:
                        reader.Close()
                    except Exception:
                        pass
        except Exception:
            db_name = None

        if not db_name:
            try:
                if server.Databases.Count > 0:
                    db_name = server.Databases[0].Name
            except Exception:
                pass

        if not db_name:
            return "", {
                "success": False,
                "error": "Could not determine database name",
                "error_type": "database_error",
            }

        return db_name, None

    def upsert_measure(
        self,
        table_name: str,
        measure_name: str,
        dax_expression: str,
        display_folder: Optional[str] = None,
        description: Optional[str] = None,
        format_string: Optional[str] = None,
        format_string_expression: Optional[str] = None,
        data_category: Optional[str] = None,
        create_if_missing: bool = True,
    ) -> Dict[str, Any]:
        """
        Create or update a DAX measure.

        Args:
            table_name: Name of the table to add measure to
            measure_name: Name of the measure
            dax_expression: DAX expression for the measure
            display_folder: Optional display folder for organization
            description: Optional measure description
            format_string: Optional static format string (e.g., "#,##0.00")
            format_string_expression: Optional dynamic DAX format string expression (sets FormatStringDefinition)
            data_category: Optional data category (e.g., "ImageUrl" for SVG visuals)
            create_if_missing: If False (P2-29), return `measure_not_found` when
                the measure doesn't exist rather than creating it. Used by the
                `update` operation so typos surface instead of silently creating
                a new measure.

        Returns:
            Result dictionary with success status and details
        """
        if not AMO_AVAILABLE:
            return {
                "success": False,
                "error": "AMO not available - cannot inject measures",
                "error_type": "amo_unavailable",
            }

        # Verify connection before AMO operations
        conn_check = self._verify_connection()
        if not conn_check.get("success"):
            return {
                "success": False,
                "error": conn_check.get("error", "Connection lost"),
                "error_type": "connection_lost",
                "suggestions": [
                    "The connection to Power BI Desktop was lost",
                    "Ensure Power BI Desktop is still running",
                    "Try reconnecting using 01_Connection",
                ],
            }

        # Validate identifiers (AMO names allow spaces/special chars)
        if not _valid_amo_name(table_name):
            return {
                "success": False,
                "error": f"Invalid table name: '{table_name}'",
                "error_type": "invalid_parameters",
            }
        if not _valid_amo_name(measure_name):
            return {
                "success": False,
                "error": f"Invalid measure name: '{measure_name}'",
                "error_type": "invalid_parameters",
            }

        server = AMOServer()
        _prepare_amo_server(server)

        try:
            # Connect to server
            server.Connect(self.connection.ConnectionString)

            db_name, db_error = self._get_database_name(server)
            if db_error:
                return db_error

            # Get database and model
            db = server.Databases.GetByName(db_name)
            model = db.Model

            # Find table
            table = next((t for t in model.Tables if t.Name == table_name), None)

            if not table:
                available_tables = [t.Name for t in model.Tables]
                return {
                    "success": False,
                    "error": f"Table '{table_name}' not found",
                    "error_type": "table_not_found",
                    "available_tables": available_tables[:10],  # Show first 10
                }

            # Find or create measure
            measure = next((m for m in table.Measures if m.Name == measure_name), None)

            if measure:
                # Update existing measure
                if dax_expression:
                    measure.Expression = dax_expression

                if display_folder is not None:
                    measure.DisplayFolder = display_folder

                if description is not None:
                    measure.Description = description

                if format_string is not None:
                    measure.FormatString = format_string

                if format_string_expression is not None:
                    _set_measure_format_string_expression(measure, format_string_expression)

                if data_category is not None:
                    measure.DataCategory = data_category

                action = "updated"
                logger.info(f"Updated measure '{measure_name}' in table '{table_name}'")

            else:
                # P2-29: respect create_if_missing so update-typos don't silently create
                if not create_if_missing:
                    available = [m.Name for m in table.Measures]
                    return {
                        "success": False,
                        "error": f"Measure '{measure_name}' not found in table '{table_name}'",
                        "error_type": "measure_not_found",
                        "available_measures": available[:10],
                    }
                # Create new measure (requires an expression)
                if not dax_expression:
                    return {
                        "success": False,
                        "error": "Expression is required when creating a new measure",
                        "error_type": "invalid_parameters",
                    }
                measure = Measure()
                measure.Name = measure_name
                measure.Expression = dax_expression

                if display_folder:
                    measure.DisplayFolder = display_folder

                if description:
                    measure.Description = description

                if format_string:
                    measure.FormatString = format_string

                if format_string_expression:
                    _set_measure_format_string_expression(measure, format_string_expression)

                if data_category:
                    measure.DataCategory = data_category

                table.Measures.Add(measure)
                action = "created"
                logger.info(f"Created measure '{measure_name}' in table '{table_name}'")

            # Save changes
            commit(model, label=f"dax_injector/upsert_measure:{action}")
            _after_write(f"DAXInjector.upsert_measure {action}")

            return {
                "success": True,
                "action": action,
                "table": table_name,
                "measure": measure_name,
                "expression": dax_expression,
                "display_folder": display_folder,
                "description": description,
                "format_string": format_string,
                "data_category": data_category,
                "message": f"Successfully {action} measure '{measure_name}' in table '{table_name}'",
            }

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error upserting measure: {error_msg}")

            # Provide helpful suggestions based on error
            suggestions = []

            if "syntax" in error_msg.lower() or "expression" in error_msg.lower():
                suggestions.extend(
                    [
                        "Check DAX syntax - ensure expression is valid",
                        "Verify all referenced columns and measures exist",
                        "Test the DAX expression separately first",
                    ]
                )

            if "table" in error_msg.lower():
                suggestions.append(f"Verify table '{table_name}' exists in the model")

            if "duplicate" in error_msg.lower() or "already exists" in error_msg.lower():
                suggestions.append("Measure name may conflict with existing object")

            if not suggestions:
                suggestions.extend(
                    [
                        "Verify all table and column references exist",
                        "Check DAX expression syntax",
                        "Ensure model is in a valid state",
                    ]
                )

            return {
                "success": False,
                "error": error_msg,
                "error_type": "injection_error",
                "suggestions": suggestions,
            }

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def apply_measure_changes(
        self, upserts: list, deletes: Optional[list] = None
    ) -> Dict[str, Any]:
        """Commit a whole codemod plan in ONE AMO session and ONE ``SaveChanges``.

        See :func:`apply_changes_to_model` for the contract (conflict check,
        reference guard for deletes, post-save compile check, snapshots).
        """
        if not AMO_AVAILABLE:
            return {
                "success": False,
                "committed": False,
                "error": "AMO not available",
                "error_type": "amo_unavailable",
            }
        conn_check = self._verify_connection()
        if not conn_check.get("success"):
            return {
                "success": False,
                "committed": False,
                "error": conn_check.get("error", "Connection lost"),
                "error_type": "connection_lost",
            }
        server = AMOServer()
        _prepare_amo_server(server)
        try:
            server.Connect(self.connection.ConnectionString)
            db_name, db_error = self._get_database_name(server)
            if db_error:
                return {**db_error, "committed": False}
            model = server.Databases.GetByName(db_name).Model
            result = apply_changes_to_model(model, upserts, deletes, measure_factory=Measure)
            result["database"] = db_name
            if result.get("committed") or result.get("reverted"):
                _after_write("DAXInjector.apply_measure_changes")
            return result
        except Exception as e:  # noqa: BLE001
            logger.error("apply_measure_changes failed: %s", e, exc_info=True)
            return {
                "success": False,
                "committed": False,
                "error": str(e),
                "error_type": "persist_failed",
            }
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def delete_measure(self, table_name: str, measure_name: str) -> Dict[str, Any]:
        """
        Delete a DAX measure.

        Args:
            table_name: Name of the table containing the measure
            measure_name: Name of the measure to delete

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return {"success": False, "error": "AMO not available", "error_type": "amo_unavailable"}

        # Verify connection before AMO operations
        conn_check = self._verify_connection()
        if not conn_check.get("success"):
            return {
                "success": False,
                "error": conn_check.get("error", "Connection lost"),
                "error_type": "connection_lost",
            }

        # Validate identifiers early (AMO names allow spaces/special chars)
        if not _valid_amo_name(table_name):
            return {
                "success": False,
                "error": f"Invalid table name: '{table_name}'",
                "error_type": "invalid_parameters",
            }
        if not _valid_amo_name(measure_name):
            return {
                "success": False,
                "error": f"Invalid measure name: '{measure_name}'",
                "error_type": "invalid_parameters",
            }

        server = AMOServer()
        _prepare_amo_server(server)

        try:
            server.Connect(self.connection.ConnectionString)

            db_name, db_error = self._get_database_name(server)
            if db_error:
                return db_error

            # Get database and model
            db = server.Databases.GetByName(db_name)
            model = db.Model

            # Find table
            table = next((t for t in model.Tables if t.Name == table_name), None)

            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}

            # Find measure
            measure = next((m for m in table.Measures if m.Name == measure_name), None)

            if not measure:
                return {
                    "success": False,
                    "error": f"Measure '{measure_name}' not found in table '{table_name}'",
                }

            # Remove measure
            table.Measures.Remove(measure)
            commit(model, label="dax_injector/delete_measure")
            _after_write("DAXInjector.delete_measure")

            logger.info(f"Deleted measure '{measure_name}' from table '{table_name}'")

            return {
                "success": True,
                "action": "deleted",
                "table": table_name,
                "measure": measure_name,
                "message": f"Successfully deleted measure '{measure_name}'",
            }

        except Exception as e:
            logger.error(f"Error deleting measure: {e}")
            return {"success": False, "error": str(e), "error_type": "deletion_error"}

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def rename_measure(self, table_name: str, measure_name: str, new_name: str) -> Dict[str, Any]:
        """
        Rename a DAX measure.

        Args:
            table_name: Name of the table containing the measure
            measure_name: Current name of the measure
            new_name: New name for the measure

        Returns:
            Result dictionary with success status
        """
        if not AMO_AVAILABLE:
            return {"success": False, "error": "AMO not available", "error_type": "amo_unavailable"}

        # Verify connection before AMO operations
        conn_check = self._verify_connection()
        if not conn_check.get("success"):
            return {
                "success": False,
                "error": conn_check.get("error", "Connection lost"),
                "error_type": "connection_lost",
            }

        for name_label, name_val in [
            ("table", table_name),
            ("measure", measure_name),
            ("new measure", new_name),
        ]:
            if not _valid_amo_name(name_val):
                return {
                    "success": False,
                    "error": f"Invalid {name_label} name: " f"'{name_val}'",
                    "error_type": "invalid_parameters",
                }

        server = AMOServer()
        _prepare_amo_server(server)

        try:
            server.Connect(self.connection.ConnectionString)

            db_name, db_error = self._get_database_name(server)
            if db_error:
                return db_error

            # Get database and model
            db = server.Databases.GetByName(db_name)
            model = db.Model

            # Find table
            table = next(
                (t for t in model.Tables if t.Name == table_name),
                None,
            )
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}

            # Find measure
            measure = next((m for m in table.Measures if m.Name == measure_name), None)
            if not measure:
                return {
                    "success": False,
                    "error": f"Measure '{measure_name}' not found in table '{table_name}'",
                }

            # Check if new name already exists in the table
            if any(m.Name == new_name for m in table.Measures if m != measure):
                return {
                    "success": False,
                    "error": f"Measure '{new_name}' already exists in table '{table_name}'",
                    "error_type": "name_conflict",
                }

            # Rename measure and cascade DAX references
            from core.operations.rename_cascade import cascade_measure_rename

            old_name = measure.Name
            measure.Name = new_name
            cascaded = cascade_measure_rename(model, old_name, new_name, table_name=table_name)
            commit(model, label="dax_injector/rename_measure")
            _after_write("DAXInjector.rename_measure")

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
            }
            if cascaded:
                result["cascaded_references"] = len(cascaded)
                result["updated_expressions"] = cascaded
            return result

        except Exception as e:
            logger.error(f"Error renaming measure: {e}")
            return {"success": False, "error": str(e), "error_type": "rename_error"}

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def move_measure(
        self, source_table: str, measure_name: str, target_table: str
    ) -> Dict[str, Any]:
        """
        Move a DAX measure from one table to another.

        P1-19 / DEPRECATED: this implementation previously dropped
        IsHidden, annotations, DetailRowsDefinition, FormatStringDefinition,
        and KPI properties. The canonical move path is
        `MeasureCRUDManager.move_measure`, which preserves all of them.
        Delegating here keeps backwards compatibility for existing callers
        while ensuring property-preservation parity.

        Args:
            source_table: Name of the table containing the measure
            measure_name: Name of the measure to move
            target_table: Name of the destination table

        Returns:
            Result dictionary with success status
        """
        try:
            from core.operations.measure_crud_manager import MeasureCRUDManager

            # MeasureCRUDManager uses connection_state internally (via
            # amo_helpers.get_server_db_model), so the `connection` ctor
            # arg is primarily for compatibility; pass our live connection.
            crud = MeasureCRUDManager(self.connection)
            return crud.move_measure(
                source_table=source_table,
                measure_name=measure_name,
                target_table=target_table,
            )
        except ImportError:
            # Fall through to the legacy path below if MeasureCRUDManager
            # can't be imported for some reason — better than failing hard.
            logger.warning(
                "MeasureCRUDManager unavailable; falling back to legacy "
                "DAXInjector.move_measure (drops KPI/FormatStringDefinition/annotations)"
            )

        if not AMO_AVAILABLE:
            return {"success": False, "error": "AMO not available", "error_type": "amo_unavailable"}

        # Verify connection before AMO operations
        conn_check = self._verify_connection()
        if not conn_check.get("success"):
            return {
                "success": False,
                "error": conn_check.get("error", "Connection lost"),
                "error_type": "connection_lost",
            }

        for name_label, name_val in [
            ("source table", source_table),
            ("measure", measure_name),
            ("target table", target_table),
        ]:
            if not _valid_amo_name(name_val):
                return {
                    "success": False,
                    "error": f"Invalid {name_label} name: " f"'{name_val}'",
                    "error_type": "invalid_parameters",
                }

        if source_table == target_table:
            return {
                "success": False,
                "error": "Source and target tables must be different",
                "error_type": "invalid_parameters",
            }

        server = AMOServer()
        _prepare_amo_server(server)

        try:
            server.Connect(self.connection.ConnectionString)

            db_name, db_error = self._get_database_name(server)
            if db_error:
                return db_error

            # Get database and model
            db = server.Databases.GetByName(db_name)
            model = db.Model

            # Find source table
            src_table = next((t for t in model.Tables if t.Name == source_table), None)
            if not src_table:
                return {
                    "success": False,
                    "error": f"Source table '{source_table}' not found",
                    "error_type": "table_not_found",
                }

            # Find target table
            tgt_table = next((t for t in model.Tables if t.Name == target_table), None)
            if not tgt_table:
                return {
                    "success": False,
                    "error": f"Target table '{target_table}' not found",
                    "error_type": "table_not_found",
                }

            # Find measure
            measure = next((m for m in src_table.Measures if m.Name == measure_name), None)
            if not measure:
                return {
                    "success": False,
                    "error": f"Measure '{measure_name}' not found in table '{source_table}'",
                    "error_type": "measure_not_found",
                }

            # Check if measure with same name exists in target table
            if any(m.Name == measure_name for m in tgt_table.Measures):
                return {
                    "success": False,
                    "error": f"Measure '{measure_name}' already exists in target table '{target_table}'",
                    "error_type": "name_conflict",
                }

            # Move measure (remove from source, add to target)
            # Note: We need to create a new measure object with same properties
            new_measure = Measure()
            new_measure.Name = measure.Name
            new_measure.Expression = measure.Expression

            # Copy optional properties
            if hasattr(measure, "Description") and measure.Description:
                new_measure.Description = measure.Description
            if hasattr(measure, "DisplayFolder") and measure.DisplayFolder:
                new_measure.DisplayFolder = measure.DisplayFolder
            if hasattr(measure, "FormatString") and measure.FormatString:
                new_measure.FormatString = measure.FormatString
            if hasattr(measure, "DataCategory") and measure.DataCategory:
                new_measure.DataCategory = measure.DataCategory

            # Remove from source and add to target
            src_table.Measures.Remove(measure)
            tgt_table.Measures.Add(new_measure)
            commit(model, label="dax_injector/move_measure")
            _after_write("DAXInjector.move_measure")

            logger.info(
                f"Moved measure '{measure_name}' from table '{source_table}' to '{target_table}'"
            )

            return {
                "success": True,
                "action": "moved",
                "measure": measure_name,
                "source_table": source_table,
                "target_table": target_table,
                "message": f"Successfully moved measure '{measure_name}' from '{source_table}' to '{target_table}'",
            }

        except Exception as e:
            logger.error(f"Error moving measure: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_type": "move_error",
                "suggestions": [
                    "Verify both source and target tables exist",
                    "Check that measure references are valid in target table context",
                    "Ensure no name conflicts in target table",
                ],
            }

        finally:
            try:
                server.Disconnect()
            except Exception:
                pass
