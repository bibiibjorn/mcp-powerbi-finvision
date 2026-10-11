"""Hierarchy CRUD Manager — manage table hierarchies via TOM.

Acquires the live model through the shared ``amo_helpers.get_server_db_model``
pattern (server + db + model, caller disconnects in ``finally``) — the same
convention every other CRUD manager uses. A prior version read a nonexistent
``connection_state.model`` attribute and raised ``AttributeError`` on every call.
"""

import logging
from typing import Any, Dict
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

from core.operations.write_lane import commit as _lane_commit  # noqa: E402
from core.operations.write_lane import safe_undo  # noqa: E402
from core.operations.amo_mixins import AmoModelMixin  # noqa: E402


class HierarchyCrudManager(AmoModelMixin):
    """CRUD operations for table hierarchies."""

    def __init__(self):
        self._last_amo_error = None

    def _connection_error_msg(self) -> str:
        cause = self._last_amo_error or "unknown cause"
        return f"Could not connect to model: {cause}"

    def list_hierarchies(self, table_name: str = None) -> Dict[str, Any]:
        """List hierarchies, optionally filtered by table."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            hierarchies = []
            tables = [model.Tables.Find(table_name)] if table_name else model.Tables
            for table in tables:
                if table is None:
                    return {"success": False, "error": f"Table '{table_name}' not found"}
                for hier in table.Hierarchies:
                    levels = []
                    for level in hier.Levels:
                        levels.append(
                            {
                                "name": level.Name,
                                "ordinal": level.Ordinal,
                                "column": level.Column.Name if level.Column else None,
                            }
                        )
                    hierarchies.append(
                        {
                            "table": table.Name,
                            "name": hier.Name,
                            "description": hier.Description or "",
                            "is_hidden": hier.IsHidden,
                            "levels": levels,
                        }
                    )
            return {"success": True, "hierarchies": hierarchies, "count": len(hierarchies)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def describe_hierarchy(self, table_name: str, hierarchy_name: str) -> Dict[str, Any]:
        """Get detailed info about a hierarchy."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            table = model.Tables.Find(table_name)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            hier = table.Hierarchies.Find(hierarchy_name)
            if not hier:
                return {
                    "success": False,
                    "error": f"Hierarchy '{hierarchy_name}' not found in '{table_name}'",
                }
            levels = []
            for level in hier.Levels:
                levels.append(
                    {
                        "name": level.Name,
                        "ordinal": level.Ordinal,
                        "column": level.Column.Name if level.Column else None,
                    }
                )
            return {
                "success": True,
                "hierarchy": {
                    "table": table.Name,
                    "name": hier.Name,
                    "description": hier.Description or "",
                    "is_hidden": hier.IsHidden,
                    "display_folder": hier.DisplayFolder or "",
                    "levels": levels,
                },
            }
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def create_hierarchy(
        self, table_name: str, hierarchy_name: str, levels: list, **kwargs
    ) -> Dict[str, Any]:
        """Create a new hierarchy with specified levels."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            table = model.Tables.Find(table_name)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            import Microsoft.AnalysisServices.Tabular as TOM

            hier = TOM.Hierarchy()
            hier.Name = hierarchy_name
            if kwargs.get("description"):
                hier.Description = kwargs["description"]
            if kwargs.get("display_folder"):
                hier.DisplayFolder = kwargs["display_folder"]
            if kwargs.get("hidden") is not None:
                hier.IsHidden = kwargs["hidden"]
            for i, level_info in enumerate(levels):
                level = TOM.Level()
                level.Name = level_info.get("name", level_info.get("column"))
                level.Ordinal = i
                col = table.Columns.Find(level_info["column"])
                if not col:
                    return {
                        "success": False,
                        "error": f"Column '{level_info['column']}' not found in '{table_name}'",
                    }
                level.Column = col
                hier.Levels.Add(level)
            table.Hierarchies.Add(hier)
            _lane_commit(model, label="hierarchy")
            return {
                "success": True,
                "message": f"Hierarchy '{hierarchy_name}' created with {len(levels)} levels",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def update_hierarchy(
        self,
        table_name: str,
        hierarchy_name: str,
        *,
        new_name: str = None,
        levels: list = None,
        description: str = None,
        display_folder: str = None,
        hidden: bool = None,
    ) -> Dict[str, Any]:
        """Rename / re-describe a hierarchy and reorder its levels (C-42), ONE save.

        ``levels`` is the new level order: level names (or ``{"name": ...}``
        dicts); it must be a permutation of the existing levels. Report visuals
        bind hierarchies by name, so a rename returns a ``pbir_followup`` note.
        """
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            table = model.Tables.Find(table_name)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            hier = table.Hierarchies.Find(hierarchy_name)
            if not hier:
                return error_envelope(
                    "hierarchy_not_found",
                    f"Hierarchy '{hierarchy_name}' not found in '{table_name}'",
                )
            by_name = {str(lv.Name): lv for lv in hier.Levels}
            order = None
            if levels is not None:
                order = [
                    str(lv.get("name") if isinstance(lv, dict) else lv) for lv in (levels or [])
                ]
                if sorted(order) != sorted(by_name) or len(set(order)) != len(order):
                    return {
                        "success": False,
                        "error": "levels must list every existing level exactly once",
                        "error_type": "invalid_parameters",
                        "existing_levels": sorted(by_name),
                    }
            renaming = bool(new_name) and new_name != hier.Name
            if renaming and any(
                str(h.Name).lower() == str(new_name).lower()
                for h in table.Hierarchies
                if str(h.Name) != str(hierarchy_name)  # pythonnet: `is` never matches
            ):
                return error_envelope(
                    "name_conflict",
                    f"Table '{table_name}' already has a hierarchy named '{new_name}'",
                )
            changed = []
            if renaming:
                hier.Name = new_name
                changed.append("name")
            if description is not None:
                hier.Description = description
                changed.append("description")
            if display_folder is not None:
                hier.DisplayFolder = display_folder
                changed.append("display_folder")
            if hidden is not None:
                hier.IsHidden = bool(hidden)
                changed.append("hidden")
            if order is not None and order != [
                str(lv.Name) for lv in sorted(hier.Levels, key=lambda x: x.Ordinal)
            ]:
                for i, name in enumerate(order):
                    by_name[name].Ordinal = i
                changed.append("levels")
            if not changed:
                return {"success": True, "action": "noop", "hierarchy": hier.Name}
            info = _lane_commit(model, label="hierarchy/update")
            out = {
                "success": True,
                "action": "updated",
                "table": table_name,
                "hierarchy": hier.Name,
                "updated": changed,
                **info,
            }
            if renaming:
                out["pbir_followup"] = (
                    "Report visuals bind hierarchies by name — rebind them "
                    f"('{hierarchy_name}' -> '{new_name}') in the PBIR report."
                )
            return out
        except Exception as e:
            try:
                safe_undo(model)
            except Exception:  # noqa: BLE001
                pass
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    # ---- level operations (MP-01) ----------------------------------------

    def _find_hierarchy(self, model, table_name: str, hierarchy_name: str):
        """(table, hierarchy, error-dict)."""
        table = model.Tables.Find(table_name)
        if not table:
            return (
                None,
                None,
                error_envelope("table_not_found", f"Table '{table_name}' not found"),
            )
        hier = table.Hierarchies.Find(hierarchy_name)
        if not hier:
            return (
                None,
                None,
                error_envelope(
                    "hierarchy_not_found",
                    f"Hierarchy '{hierarchy_name}' not found in '{table_name}'",
                ),
            )
        return table, hier, None

    @staticmethod
    def _levels_in_order(hier) -> list:
        return sorted(list(hier.Levels), key=lambda lv: lv.Ordinal)

    def add_level(
        self,
        table_name: str,
        hierarchy_name: str,
        level_name: str = None,
        column_name: str = None,
        ordinal: int = None,
    ) -> Dict[str, Any]:
        """Add one level to an existing hierarchy in ONE save.

        ``column_name`` is the source column (required); ``level_name`` defaults to it;
        ``ordinal`` is the 0-based position (default: append at the end, others shift).
        """
        if not column_name:
            return error_envelope("missing_argument", "column_name is required for add_level")
        if ordinal is not None:
            try:
                ordinal = int(ordinal)
            except (TypeError, ValueError):
                return error_envelope(
                    "invalid_parameters", f"ordinal must be an integer, got {ordinal!r}"
                )
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            table, hier, err = self._find_hierarchy(model, table_name, hierarchy_name)
            if err:
                return err
            col = table.Columns.Find(column_name)
            if not col:
                return error_envelope(
                    "column_not_found", f"Column '{column_name}' not found in '{table_name}'"
                )
            name = level_name or column_name
            existing = self._levels_in_order(hier)
            if any(str(lv.Name).lower() == str(name).lower() for lv in existing):
                return error_envelope(
                    "name_conflict",
                    f"Hierarchy '{hierarchy_name}' already has a level named '{name}'",
                )
            position = len(existing) if ordinal is None else ordinal
            if position < 0 or position > len(existing):
                return {
                    "success": False,
                    "error": f"ordinal must be between 0 and {len(existing)}, got {position}",
                    "error_type": "invalid_parameters",
                }
            import Microsoft.AnalysisServices.Tabular as TOM

            level = TOM.Level()
            level.Name = name
            level.Column = col
            # Shift the levels at or after the insert position, then place the new one.
            for lv in existing:
                if lv.Ordinal >= position:
                    lv.Ordinal = lv.Ordinal + 1
            level.Ordinal = position
            hier.Levels.Add(level)
            info = _lane_commit(model, label="hierarchy/add_level")
            return {
                "success": True,
                "action": "level_added",
                "table": table_name,
                "hierarchy": hierarchy_name,
                "level": name,
                "column": column_name,
                "ordinal": position,
                "level_count": len(existing) + 1,
                **info,
            }
        except Exception as e:
            try:
                safe_undo(model)
            except Exception:  # noqa: BLE001
                pass
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def remove_level(
        self, table_name: str, hierarchy_name: str, level_name: str = None
    ) -> Dict[str, Any]:
        """Remove one level (never the last one - delete the hierarchy instead)."""
        if not level_name:
            return error_envelope("missing_argument", "level_name is required for remove_level")
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            table, hier, err = self._find_hierarchy(model, table_name, hierarchy_name)
            if err:
                return err
            existing = self._levels_in_order(hier)
            target = next((lv for lv in existing if str(lv.Name) == str(level_name)), None)
            if target is None:
                return {
                    "success": False,
                    "error": f"Level '{level_name}' not found in hierarchy '{hierarchy_name}'",
                    "error_type": "level_not_found",
                    "existing_levels": [str(lv.Name) for lv in existing],
                }
            if len(existing) == 1:
                return error_envelope(
                    "last_level",
                    f"'{level_name}' is the last level of '{hierarchy_name}'; delete the "
                    "hierarchy instead (operation=delete).",
                )
            hier.Levels.Remove(target)
            remaining = [lv for lv in existing if lv is not target]
            for i, lv in enumerate(remaining):  # keep ordinals 0..n-1
                if lv.Ordinal != i:
                    lv.Ordinal = i
            info = _lane_commit(model, label="hierarchy/remove_level")
            return {
                "success": True,
                "action": "level_removed",
                "table": table_name,
                "hierarchy": hierarchy_name,
                "level": level_name,
                "level_count": len(remaining),
                **info,
            }
        except Exception as e:
            try:
                safe_undo(model)
            except Exception:  # noqa: BLE001
                pass
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def rename_level(
        self, table_name: str, hierarchy_name: str, level_name: str = None, new_name: str = None
    ) -> Dict[str, Any]:
        """Rename one level. Report visuals bind hierarchy levels by name (pbir_followup)."""
        if not level_name or not new_name:
            return error_envelope(
                "missing_argument", "level_name and new_name are required for rename_level"
            )
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            table, hier, err = self._find_hierarchy(model, table_name, hierarchy_name)
            if err:
                return err
            existing = self._levels_in_order(hier)
            target = next((lv for lv in existing if str(lv.Name) == str(level_name)), None)
            if target is None:
                return {
                    "success": False,
                    "error": f"Level '{level_name}' not found in hierarchy '{hierarchy_name}'",
                    "error_type": "level_not_found",
                    "existing_levels": [str(lv.Name) for lv in existing],
                }
            if str(new_name) == str(level_name):
                return {"success": True, "action": "noop", "level": level_name}
            if any(
                str(lv.Name).lower() == str(new_name).lower() for lv in existing if lv is not target
            ):
                return error_envelope(
                    "name_conflict",
                    f"Hierarchy '{hierarchy_name}' already has a level named '{new_name}'",
                )
            target.Name = new_name
            info = _lane_commit(model, label="hierarchy/rename_level")
            return {
                "success": True,
                "action": "level_renamed",
                "table": table_name,
                "hierarchy": hierarchy_name,
                "level": new_name,
                "original_name": level_name,
                "pbir_followup": (
                    "Report visuals bind hierarchy levels by name - rebind them "
                    f"('{level_name}' -> '{new_name}') in the PBIR report."
                ),
                **info,
            }
        except Exception as e:
            try:
                safe_undo(model)
            except Exception:  # noqa: BLE001
                pass
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)

    def delete_hierarchy(self, table_name: str, hierarchy_name: str) -> Dict[str, Any]:
        """Delete a hierarchy."""
        server, db, model = self._get_server_db_model()
        if not model:
            return {"success": False, "error": self._connection_error_msg()}
        try:
            table = model.Tables.Find(table_name)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found"}
            hier = table.Hierarchies.Find(hierarchy_name)
            if not hier:
                return {"success": False, "error": f"Hierarchy '{hierarchy_name}' not found"}
            table.Hierarchies.Remove(hier)
            _lane_commit(model, label="hierarchy")
            return {"success": True, "message": f"Hierarchy '{hierarchy_name}' deleted"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if server is not None:
                from core.operations.amo_helpers import safe_disconnect

                safe_disconnect(server)
