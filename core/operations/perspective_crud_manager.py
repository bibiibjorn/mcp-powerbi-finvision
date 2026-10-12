"""Perspective CRUD Manager — manage model perspectives via TOM."""

import logging
from contextlib import contextmanager
from typing import Any, Dict

logger = logging.getLogger(__name__)

from core.operations.write_lane import commit as _lane_commit  # noqa: E402
from core.operations.write_lane import safe_undo  # noqa: E402


class PerspectiveCrudManager:
    """CRUD operations for model perspectives."""

    def __init__(self):
        self._connection_state = None

    @property
    def connection_state(self):
        if self._connection_state is None:
            from core.infrastructure.connection_state import connection_state

            self._connection_state = connection_state
        return self._connection_state

    @contextmanager
    def _amo_session(self):
        """Yield (server, model, err) for one AMO transaction; always disconnect."""
        from core.operations.amo_helpers import get_server_db_model

        server, _db, model, err = get_server_db_model(self.connection_state)
        try:
            yield server, model, err
        finally:
            if server is not None:
                try:
                    server.Disconnect()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _find_or_add_persp_table(self, persp, table_name: str):
        """Return (PerspectiveTable, created_flag) for `table_name`.

        Caller is responsible for SaveChanges() — this helper only mutates the
        in-memory model.
        """
        for pt in persp.PerspectiveTables:
            if pt.Name == table_name:
                return pt, False
        import Microsoft.AnalysisServices.Tabular as TOM

        pt = TOM.PerspectiveTable()
        pt.Name = table_name
        persp.PerspectiveTables.Add(pt)
        return pt, True

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_perspectives(self) -> Dict[str, Any]:
        """List all perspectives in the model."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            perspectives = []
            for persp in model.Perspectives:
                table_count = sum(1 for _ in persp.PerspectiveTables)
                perspectives.append(
                    {
                        "name": persp.Name,
                        "description": persp.Description or "",
                        "table_count": table_count,
                    }
                )
            return {"success": True, "perspectives": perspectives, "count": len(perspectives)}

    def describe_perspective(self, perspective_name: str) -> Dict[str, Any]:
        """Get detailed info about a perspective."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            tables = []
            for pt in persp.PerspectiveTables:
                columns = [pc.Name for pc in pt.PerspectiveColumns]
                measures = [pm.Name for pm in pt.PerspectiveMeasures]
                hierarchies = [ph.Name for ph in pt.PerspectiveHierarchies]
                tables.append(
                    {
                        "table": pt.Name,
                        "columns": columns,
                        "measures": measures,
                        "hierarchies": hierarchies,
                    }
                )
            return {
                "success": True,
                "perspective": {
                    "name": persp.Name,
                    "description": persp.Description or "",
                    "tables": tables,
                },
            }

    def create_perspective(self, perspective_name: str, **kwargs) -> Dict[str, Any]:
        """Create a new perspective."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            try:
                import Microsoft.AnalysisServices.Tabular as TOM

                persp = TOM.Perspective()
                persp.Name = perspective_name
                if kwargs.get("description"):
                    persp.Description = kwargs["description"]
                model.Perspectives.Add(persp)
                _lane_commit(model, label="perspective")
                return {"success": True, "message": f"Perspective '{perspective_name}' created"}
            except Exception as e:
                return {"success": False, "error": str(e)}

    def delete_perspective(self, perspective_name: str) -> Dict[str, Any]:
        """Delete a perspective."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            try:
                model.Perspectives.Remove(persp)
                _lane_commit(model, label="perspective")
                return {"success": True, "message": f"Perspective '{perspective_name}' deleted"}
            except Exception as e:
                return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    def add_table(self, perspective_name: str, table_name: str) -> Dict[str, Any]:
        """Add a table to a perspective."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            if not any(t.Name == table_name for t in model.Tables):
                return {"success": False, "error": f"Table '{table_name}' not found in model"}
            if any(pt.Name == table_name for pt in persp.PerspectiveTables):
                return {
                    "success": True,
                    "action": "noop",
                    "table": table_name,
                    "message": f"Table '{table_name}' already in perspective",
                }
            try:
                import Microsoft.AnalysisServices.Tabular as TOM

                pt = TOM.PerspectiveTable()
                pt.Name = table_name
                persp.PerspectiveTables.Add(pt)
                _lane_commit(model, label="perspective")
                return {"success": True, "action": "added", "table": table_name}
            except Exception as e:
                return {"success": False, "error": str(e)}

    def add_measure(
        self, perspective_name: str, table_name: str, measure_name: str
    ) -> Dict[str, Any]:
        """Add a measure to a perspective, implicitly adding the table if missing."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            table = next((t for t in model.Tables if t.Name == table_name), None)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found in model"}
            if not any(m.Name == measure_name for m in table.Measures):
                return {
                    "success": False,
                    "error": f"Measure '{measure_name}' not found in table '{table_name}'",
                }
            pt, was_created = self._find_or_add_persp_table(persp, table_name)
            try:
                if any(pm.Name == measure_name for pm in pt.PerspectiveMeasures):
                    if was_created:
                        persp.PerspectiveTables.Remove(pt)
                    return {
                        "success": True,
                        "action": "noop",
                        "table": table_name,
                        "measure": measure_name,
                    }
                import Microsoft.AnalysisServices.Tabular as TOM

                pm = TOM.PerspectiveMeasure()
                pm.Name = measure_name
                pt.PerspectiveMeasures.Add(pm)
                _lane_commit(model, label="perspective")
                return {
                    "success": True,
                    "action": "added",
                    "table": table_name,
                    "measure": measure_name,
                }
            except Exception as e:
                if was_created:
                    try:
                        persp.PerspectiveTables.Remove(pt)
                    except Exception:
                        pass
                return {"success": False, "error": str(e)}

    def add_column(
        self, perspective_name: str, table_name: str, column_name: str
    ) -> Dict[str, Any]:
        """Add a column to a perspective, implicitly adding the table if missing."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            table = next((t for t in model.Tables if t.Name == table_name), None)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found in model"}
            if not any(c.Name == column_name for c in table.Columns):
                return {
                    "success": False,
                    "error": f"Column '{column_name}' not found in table '{table_name}'",
                }
            pt, was_created = self._find_or_add_persp_table(persp, table_name)
            try:
                if any(pc.Name == column_name for pc in pt.PerspectiveColumns):
                    if was_created:
                        persp.PerspectiveTables.Remove(pt)
                    return {
                        "success": True,
                        "action": "noop",
                        "table": table_name,
                        "column": column_name,
                    }
                import Microsoft.AnalysisServices.Tabular as TOM

                pc = TOM.PerspectiveColumn()
                pc.Name = column_name
                pt.PerspectiveColumns.Add(pc)
                _lane_commit(model, label="perspective")
                return {
                    "success": True,
                    "action": "added",
                    "table": table_name,
                    "column": column_name,
                }
            except Exception as e:
                if was_created:
                    try:
                        persp.PerspectiveTables.Remove(pt)
                    except Exception:
                        pass
                return {"success": False, "error": str(e)}

    def add_hierarchy(
        self, perspective_name: str, table_name: str, hierarchy_name: str
    ) -> Dict[str, Any]:
        """Add a hierarchy to a perspective, implicitly adding the table if missing."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            table = next((t for t in model.Tables if t.Name == table_name), None)
            if not table:
                return {"success": False, "error": f"Table '{table_name}' not found in model"}
            if not any(h.Name == hierarchy_name for h in table.Hierarchies):
                return {
                    "success": False,
                    "error": f"Hierarchy '{hierarchy_name}' not found in table '{table_name}'",
                }
            pt, was_created = self._find_or_add_persp_table(persp, table_name)
            try:
                if any(ph.Name == hierarchy_name for ph in pt.PerspectiveHierarchies):
                    if was_created:
                        persp.PerspectiveTables.Remove(pt)
                    return {
                        "success": True,
                        "action": "noop",
                        "table": table_name,
                        "hierarchy": hierarchy_name,
                    }
                import Microsoft.AnalysisServices.Tabular as TOM

                ph = TOM.PerspectiveHierarchy()
                ph.Name = hierarchy_name
                pt.PerspectiveHierarchies.Add(ph)
                _lane_commit(model, label="perspective")
                return {
                    "success": True,
                    "action": "added",
                    "table": table_name,
                    "hierarchy": hierarchy_name,
                }
            except Exception as e:
                if was_created:
                    try:
                        persp.PerspectiveTables.Remove(pt)
                    except Exception:
                        pass
                return {"success": False, "error": str(e)}

    def remove_table(self, perspective_name: str, table_name: str) -> Dict[str, Any]:
        """Remove a table (and all its members) from a perspective."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            pt = next((pt for pt in persp.PerspectiveTables if pt.Name == table_name), None)
            if not pt:
                return {"success": True, "action": "noop", "table": table_name}
            try:
                persp.PerspectiveTables.Remove(pt)
                _lane_commit(model, label="perspective")
                return {"success": True, "action": "removed", "table": table_name}
            except Exception as e:
                return {"success": False, "error": str(e)}

    def remove_measure(
        self, perspective_name: str, table_name: str, measure_name: str
    ) -> Dict[str, Any]:
        """Remove a measure from a perspective table."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            pt = next((pt for pt in persp.PerspectiveTables if pt.Name == table_name), None)
            if not pt:
                return {
                    "success": True,
                    "action": "noop",
                    "table": table_name,
                    "measure": measure_name,
                }
            pm = next((pm for pm in pt.PerspectiveMeasures if pm.Name == measure_name), None)
            if not pm:
                return {
                    "success": True,
                    "action": "noop",
                    "table": table_name,
                    "measure": measure_name,
                }
            try:
                pt.PerspectiveMeasures.Remove(pm)
                _lane_commit(model, label="perspective")
                return {
                    "success": True,
                    "action": "removed",
                    "table": table_name,
                    "measure": measure_name,
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

    def remove_column(
        self, perspective_name: str, table_name: str, column_name: str
    ) -> Dict[str, Any]:
        """Remove a column from a perspective table."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            pt = next((pt for pt in persp.PerspectiveTables if pt.Name == table_name), None)
            if not pt:
                return {
                    "success": True,
                    "action": "noop",
                    "table": table_name,
                    "column": column_name,
                }
            pc = next((pc for pc in pt.PerspectiveColumns if pc.Name == column_name), None)
            if not pc:
                return {
                    "success": True,
                    "action": "noop",
                    "table": table_name,
                    "column": column_name,
                }
            try:
                pt.PerspectiveColumns.Remove(pc)
                _lane_commit(model, label="perspective")
                return {
                    "success": True,
                    "action": "removed",
                    "table": table_name,
                    "column": column_name,
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

    def remove_hierarchy(
        self, perspective_name: str, table_name: str, hierarchy_name: str
    ) -> Dict[str, Any]:
        """Remove a hierarchy from a perspective table."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            pt = next((pt for pt in persp.PerspectiveTables if pt.Name == table_name), None)
            if not pt:
                return {
                    "success": True,
                    "action": "noop",
                    "table": table_name,
                    "hierarchy": hierarchy_name,
                }
            ph = next((ph for ph in pt.PerspectiveHierarchies if ph.Name == hierarchy_name), None)
            if not ph:
                return {
                    "success": True,
                    "action": "noop",
                    "table": table_name,
                    "hierarchy": hierarchy_name,
                }
            try:
                pt.PerspectiveHierarchies.Remove(ph)
                _lane_commit(model, label="perspective")
                return {
                    "success": True,
                    "action": "removed",
                    "table": table_name,
                    "hierarchy": hierarchy_name,
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Bulk membership (C-42): many members, ONE SaveChanges
    # ------------------------------------------------------------------

    _MEMBER_KINDS = (
        ("measure_name", "Measures", "PerspectiveMeasures", "PerspectiveMeasure"),
        ("column_name", "Columns", "PerspectiveColumns", "PerspectiveColumn"),
        ("hierarchy_name", "Hierarchies", "PerspectiveHierarchies", "PerspectiveHierarchy"),
    )

    def set_members(self, perspective_name: str, members, mode: str = "add") -> Dict[str, Any]:
        """Add or remove many perspective members in one transaction.

        ``members``: ``[{table_name, measure_name | column_name | hierarchy_name}]``;
        an item with only ``table_name`` is the whole table. ``mode``: ``add``
        (default) or ``remove``. Every item is validated before anything is
        mutated (all-or-nothing); existing / absent members are ``noop``.
        """
        mode = str(mode or "add").strip().lower()
        if mode not in ("add", "remove"):
            return {
                "success": False,
                "error": f"mode must be 'add' or 'remove', got {mode!r}",
                "error_type": "invalid_parameters",
            }
        if not isinstance(members, list) or not members:
            return {
                "success": False,
                "error": "members must be a non-empty list of {table_name, measure_name|column_name|hierarchy_name}",
                "error_type": "invalid_parameters",
            }
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            persp = model.Perspectives.Find(perspective_name)
            if not persp:
                return {"success": False, "error": f"Perspective '{perspective_name}' not found"}
            plan = []
            errors = []
            for idx, m in enumerate(members):
                if not isinstance(m, dict) or not (m.get("table_name") or m.get("table")):
                    errors.append({"index": idx, "error": "table_name is required"})
                    continue
                tname = m.get("table_name") or m.get("table")
                table = next((t for t in model.Tables if t.Name == tname), None)
                if table is None:
                    errors.append({"index": idx, "error": f"Table '{tname}' not found"})
                    continue
                kinds = [k for k in self._MEMBER_KINDS if m.get(k[0])]
                if len(kinds) > 1:
                    errors.append({"index": idx, "error": "one member kind per item"})
                    continue
                if kinds:
                    key, model_coll, _pcoll, _cls = kinds[0]
                    if mode == "add" and not any(
                        o.Name == m[key] for o in getattr(table, model_coll)
                    ):
                        errors.append(
                            {"index": idx, "error": f"'{m[key]}' not found in table '{tname}'"}
                        )
                        continue
                    plan.append((tname, kinds[0], m[key]))
                else:
                    plan.append((tname, None, None))
            if errors:
                return {
                    "success": False,
                    "error_type": "invalid_members",
                    "error": f"{len(errors)} member(s) invalid — nothing was changed",
                    "validation_errors": errors,
                }
            import Microsoft.AnalysisServices.Tabular as TOM

            results = []
            try:
                for tname, kind, name in plan:
                    pt = next((p for p in persp.PerspectiveTables if p.Name == tname), None)
                    if mode == "add":
                        if pt is None:
                            pt = TOM.PerspectiveTable()
                            pt.Name = tname
                            persp.PerspectiveTables.Add(pt)
                        if kind is None:
                            results.append({"table": tname, "action": "added"})
                            continue
                        _key, _mc, pcoll, cls = kind
                        coll = getattr(pt, pcoll)
                        if any(o.Name == name for o in coll):
                            results.append({"table": tname, "member": name, "action": "noop"})
                            continue
                        obj = getattr(TOM, cls)()
                        obj.Name = name
                        coll.Add(obj)
                        results.append({"table": tname, "member": name, "action": "added"})
                    else:
                        if pt is None:
                            results.append({"table": tname, "member": name, "action": "noop"})
                            continue
                        if kind is None:
                            persp.PerspectiveTables.Remove(pt)
                            results.append({"table": tname, "action": "removed"})
                            continue
                        _key, _mc, pcoll, _cls = kind
                        coll = getattr(pt, pcoll)
                        hit = next((o for o in coll if o.Name == name), None)
                        if hit is None:
                            results.append({"table": tname, "member": name, "action": "noop"})
                            continue
                        coll.Remove(hit)
                        results.append({"table": tname, "member": name, "action": "removed"})
                changed = [r for r in results if r["action"] != "noop"]
                info = _lane_commit(model, label="perspective/set_members") if changed else {}
            except Exception as e:
                try:
                    safe_undo(model)
                except Exception:  # noqa: BLE001
                    pass
                return {"success": False, "error": str(e), "rolled_back": True}
            return {
                "success": True,
                "perspective": perspective_name,
                "mode": mode,
                "changed": len(changed),
                "results": results,
                **info,
            }
