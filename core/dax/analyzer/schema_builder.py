"""Build a ModelSchema from the live model (Phase 2.1 population path).

Used by the 05_DAX_Intelligence handler to enrich AnalysisContext so
schema-aware rules can resolve measure-vs-column ambiguity and reason about
relationship topology / RLS. Fetches via the query_executor's INFO DMV queries
and never raises — on any failure it returns an empty (but valid) schema so DAX
analysis degrades to static-only.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from core.dax.analyzer.models import ModelSchema

logger = logging.getLogger(__name__)


def _rows(executor, view: str) -> List[Dict[str, Any]]:
    try:
        res = executor.execute_info_query(view)
    except Exception:
        return []
    if not isinstance(res, dict) or not res.get("success"):
        return []
    return res.get("rows", []) or []


def _first(row: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        for variant in (k, f"[{k}]"):
            if variant in row and row[variant]:
                return str(row[variant])
    return ""


def build_model_schema(executor) -> ModelSchema:
    """Assemble a ModelSchema from live INFO DMV queries.

    Always returns a ModelSchema (possibly empty); never raises.
    """
    if executor is None:
        return ModelSchema()

    try:
        table_rows = _rows(executor, "TABLES")
        name_by_id = {
            _first(r, "ID", "TableID"): _first(r, "Name", "TableName", "ExplicitName")
            for r in table_rows
            if _first(r, "ID", "TableID")
        }
        # storage mode per table (INFO.PARTITIONS: TOM ModeType 0 Import / 1 DirectQuery /
        # 2 Default / 3 Push / 4 Dual / 5 DirectLake); a table whose partitions disagree stays
        # unknown (D-05).
        modes_by_table: Dict[str, set] = {}
        for pr in _rows(executor, "PARTITIONS"):
            tname = name_by_id.get(_first(pr, "TableID")) or _first(pr, "Table", "TableName")
            mode = _first(pr, "Mode")
            if tname and mode != "":
                modes_by_table.setdefault(tname, set()).add(str(mode))
        tables = [
            {
                "name": _first(r, "Name", "TableName", "ExplicitName"),
                "mode": (
                    next(iter(modes_by_table[_first(r, "Name", "TableName", "ExplicitName")]))
                    if len(modes_by_table.get(_first(r, "Name", "TableName", "ExplicitName"), ()))
                    == 1
                    else ""
                ),
            }
            for r in table_rows
        ]
        measures = [
            {
                "name": _first(r, "Name", "MeasureName", "ExplicitName"),
                "table": _first(r, "Table", "TableName"),
            }
            for r in _rows(executor, "MEASURES")
        ]
        columns = [
            {
                "name": _first(r, "Name", "ColumnName", "ExplicitName"),
                "table": _first(r, "Table", "TableName"),
            }
            for r in _rows(executor, "COLUMNS")
        ]
        # INFO.RELATIONSHIPS carries table IDs only: add the names so the topology-aware rules
        # (PY_ALL_VS_ALLCROSSFILTERED / PY_RLS_BIDIRECTIONAL_INTERACTION) can resolve tables (D-09).
        relationships = []
        for r in _rows(executor, "RELATIONSHIPS"):
            row = dict(r)
            frm = name_by_id.get(_first(r, "FromTableID"))
            to = name_by_id.get(_first(r, "ToTableID"))
            if frm and "FromTable" not in row:
                row["FromTable"] = frm
            if to and "ToTable" not in row:
                row["ToTable"] = to
            relationships.append(row)
        roles = _rows(executor, "ROLES")

        return ModelSchema.from_model(
            {
                "tables": [t for t in tables if t["name"]],
                "measures": [m for m in measures if m["name"]],
                "columns": [c for c in columns if c["name"]],
                "relationships": relationships,
                "roles": roles,
            }
        )
    except Exception as e:  # never break DAX analysis on schema-build failure
        logger.warning("build_model_schema failed: %s", e)
        return ModelSchema()
