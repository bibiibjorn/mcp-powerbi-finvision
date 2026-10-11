"""Query Group CRUD Manager - Power Query folders (``Model.QueryGroups``) via TOM (QG-01).

A query group is a folder in the Power Query editor that organises named expressions
(parameters, shared queries) and table partitions (the query behind an import table).
Its name IS its folder path (``Sources\\Staging``); there is no separate name property, so
renaming a group means changing the folder path. Pure hygiene: no effect on results or
performance.

Acquires the live model through ``amo_helpers.get_server_db_model`` and saves once through
``write_lane.commit`` like every other CRUD manager.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from core.operations.write_lane import commit as _lane_commit  # noqa: E402
from core.operations.write_lane import safe_undo  # noqa: E402
from core.operations.amo_mixins import AmoModelMixin  # noqa: E402


def _err(message: str, error_type: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error": message, "error_type": error_type, **extra}


def _group_name(group: Any) -> str:
    return str(getattr(group, "Name", None) or getattr(group, "Folder", "") or "")


class QueryGroupCrudManager(AmoModelMixin):
    """list / create / update / delete query groups and (un)assign their members."""

    def __init__(self):
        self._last_amo_error = None

    def _connection_error(self) -> Dict[str, Any]:
        return _err(
            f"Could not connect to model: {self._last_amo_error or 'unknown cause'}",
            "connection_error",
        )

    @staticmethod
    def _disconnect(server) -> None:
        if server is not None:
            from core.operations.amo_helpers import safe_disconnect

            safe_disconnect(server)

    @staticmethod
    def _supported(model) -> bool:
        try:
            return hasattr(model, "QueryGroups")
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _unsupported() -> Dict[str, Any]:
        return _err(
            "The loaded AMO/TOM assemblies do not expose Model.QueryGroups.",
            "feature_unsupported_by_client",
        )

    @staticmethod
    def _clean(name: Any) -> Optional[str]:
        text = str(name or "").strip().strip("\\")
        return text or None

    @staticmethod
    def _members(model, group_name: str) -> Dict[str, List[Any]]:
        """Named expressions and partitions currently assigned to ``group_name``."""
        expressions = [
            str(e.Name)
            for e in model.Expressions
            if getattr(e, "QueryGroup", None) is not None
            and _group_name(e.QueryGroup) == group_name
        ]
        partitions: List[Dict[str, str]] = []
        for t in model.Tables:
            for p in t.Partitions:
                qg = getattr(p, "QueryGroup", None)
                if qg is not None and _group_name(qg) == group_name:
                    partitions.append({"table": str(t.Name), "partition": str(p.Name)})
        return {"named_expressions": expressions, "partitions": partitions}

    # ---- reads -----------------------------------------------------------

    def list_query_groups(self) -> Dict[str, Any]:
        server, db, model = self._get_server_db_model()
        if not model:
            return self._connection_error()
        try:
            if not self._supported(model):
                return self._unsupported()
            groups = []
            for g in model.QueryGroups:
                name = _group_name(g)
                members = self._members(model, name)
                groups.append(
                    {
                        "query_group": name,
                        "description": getattr(g, "Description", None) or "",
                        "named_expressions": members["named_expressions"],
                        "partitions": members["partitions"],
                        "member_count": len(members["named_expressions"])
                        + len(members["partitions"]),
                    }
                )
            return {"success": True, "query_groups": groups, "count": len(groups)}
        finally:
            self._disconnect(server)

    # ---- writes ----------------------------------------------------------

    def create_query_group(
        self, query_group: Optional[str], description: Optional[str] = None
    ) -> Dict[str, Any]:
        name = self._clean(query_group)
        if not name:
            return _err("query_group (the folder path) is required", "missing_argument")
        server, db, model = self._get_server_db_model()
        if not model:
            return self._connection_error()
        try:
            if not self._supported(model):
                return self._unsupported()
            if model.QueryGroups.Find(name) is not None:
                return _err(f"Query group '{name}' already exists", "already_exists")
            import Microsoft.AnalysisServices.Tabular as TOM

            group = TOM.QueryGroup()
            group.Folder = name  # the name is derived from the folder
            if description:
                group.Description = description
            model.QueryGroups.Add(group)
            info = _lane_commit(model, label="query_group/create")
            return {"success": True, "action": "created", "query_group": name, **info}
        except Exception as e:  # noqa: BLE001
            self._undo(model)
            return _err(str(e), "tom_error")
        finally:
            self._disconnect(server)

    def update_query_group(
        self,
        query_group: Optional[str],
        description: Optional[str] = None,
        new_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        name = self._clean(query_group)
        if not name:
            return _err("query_group (the folder path) is required", "missing_argument")
        target_name = self._clean(new_name)
        if description is None and not target_name:
            return _err("Nothing to update: pass description and/or new_name", "invalid_parameters")
        server, db, model = self._get_server_db_model()
        if not model:
            return self._connection_error()
        try:
            if not self._supported(model):
                return self._unsupported()
            group = model.QueryGroups.Find(name)
            if group is None:
                return _err(f"Query group '{name}' not found", "query_group_not_found")
            changed = []
            if target_name and target_name != name:
                if model.QueryGroups.Find(target_name) is not None:
                    return _err(f"Query group '{target_name}' already exists", "name_conflict")
                group.Folder = target_name
                changed.append("name")
            if description is not None:
                group.Description = description
                changed.append("description")
            if not changed:
                return {"success": True, "action": "noop", "query_group": name}
            info = _lane_commit(model, label="query_group/update")
            return {
                "success": True,
                "action": "updated",
                "query_group": target_name or name,
                "updated": changed,
                **info,
            }
        except Exception as e:  # noqa: BLE001
            self._undo(model)
            return _err(str(e), "tom_error")
        finally:
            self._disconnect(server)

    def delete_query_group(self, query_group: Optional[str], force: bool = False) -> Dict[str, Any]:
        """Delete a group. Refuses while it has members unless ``force`` (members are unassigned)."""
        name = self._clean(query_group)
        if not name:
            return _err("query_group (the folder path) is required", "missing_argument")
        server, db, model = self._get_server_db_model()
        if not model:
            return self._connection_error()
        try:
            if not self._supported(model):
                return self._unsupported()
            group = model.QueryGroups.Find(name)
            if group is None:
                return _err(f"Query group '{name}' not found", "query_group_not_found")
            members = self._members(model, name)
            count = len(members["named_expressions"]) + len(members["partitions"])
            if count and not force:
                return _err(
                    f"Query group '{name}' still has {count} member(s); unassign them or pass "
                    "force=true to unassign and delete.",
                    "query_group_in_use",
                    **members,
                )
            for e in model.Expressions:
                qg = getattr(e, "QueryGroup", None)
                if qg is not None and _group_name(qg) == name:
                    e.QueryGroup = None
            for t in model.Tables:
                for p in t.Partitions:
                    qg = getattr(p, "QueryGroup", None)
                    if qg is not None and _group_name(qg) == name:
                        p.QueryGroup = None
            model.QueryGroups.Remove(group)
            info = _lane_commit(model, label="query_group/delete")
            return {
                "success": True,
                "action": "deleted",
                "query_group": name,
                "unassigned": members,
                **info,
            }
        except Exception as e:  # noqa: BLE001
            self._undo(model)
            return _err(str(e), "tom_error")
        finally:
            self._disconnect(server)

    def assign(
        self,
        query_group: Optional[str],
        expression_name: Optional[str] = None,
        table_name: Optional[str] = None,
        partition_name: Optional[str] = None,
        unassign: bool = False,
    ) -> Dict[str, Any]:
        """(Un)assign ONE named expression (``expression_name``) or table partition
        (``table_name`` + optional ``partition_name`` - default: the table's only partition)."""
        name = self._clean(query_group)
        if not unassign and not name:
            return _err("query_group (the folder path) is required", "missing_argument")
        if bool(expression_name) == bool(table_name):
            return _err(
                "Pass exactly one target: expression_name (a named expression) OR "
                "table_name [+ partition_name] (a table partition).",
                "invalid_parameters",
            )
        server, db, model = self._get_server_db_model()
        if not model:
            return self._connection_error()
        try:
            if not self._supported(model):
                return self._unsupported()
            group = None
            if not unassign:
                group = model.QueryGroups.Find(name)
                if group is None:
                    return _err(
                        f"Query group '{name}' not found (create it first)", "query_group_not_found"
                    )
            target, label, err = self._resolve_target(
                model, expression_name, table_name, partition_name
            )
            if err:
                return err
            previous = (
                _group_name(target.QueryGroup) if getattr(target, "QueryGroup", None) else None
            )
            target.QueryGroup = None if unassign else group
            verb = "unassign" if unassign else "assign"
            info = _lane_commit(model, label=f"query_group/{verb}")
            return {
                "success": True,
                "action": "unassigned" if unassign else "assigned",
                "target": label,
                "query_group": None if unassign else name,
                "previous_query_group": previous,
                **info,
            }
        except Exception as e:  # noqa: BLE001
            self._undo(model)
            return _err(str(e), "tom_error")
        finally:
            self._disconnect(server)

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _undo(model) -> None:
        try:
            safe_undo(model)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _resolve_target(
        model, expression_name, table_name, partition_name
    ) -> Tuple[Any, str, Optional[Dict[str, Any]]]:
        if expression_name:
            expr = model.Expressions.Find(expression_name)
            if expr is None:
                return (
                    None,
                    "",
                    _err(f"Named expression '{expression_name}' not found", "expression_not_found"),
                )
            return expr, f"named_expression:{expression_name}", None
        table = model.Tables.Find(table_name)
        if table is None:
            return None, "", _err(f"Table '{table_name}' not found", "table_not_found")
        partitions = list(table.Partitions)
        if partition_name:
            part = table.Partitions.Find(partition_name)
            if part is None:
                return (
                    None,
                    "",
                    _err(
                        f"Partition '{partition_name}' not found in '{table_name}'",
                        "partition_not_found",
                    ),
                )
        elif len(partitions) == 1:
            part = partitions[0]
        else:
            return (
                None,
                "",
                _err(
                    f"Table '{table_name}' has {len(partitions)} partitions; pass partition_name.",
                    "ambiguous_partition",
                    partitions=[str(p.Name) for p in partitions],
                ),
            )
        return part, f"partition:{table_name}/{part.Name}", None
