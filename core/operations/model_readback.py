"""Read-back of live model state over ADOMD for verify-after-write (C-10, C-40).

AMO is the *write* client; this module is the independent *read* path used to
decide what actually happened after a ``SaveChanges``:

* :func:`expression_errors` — every object whose DAX currently fails to
  evaluate (measures, calculated columns, calc items, format-string / detail-rows
  definitions, calculated partitions, RLS filters), keyed by ``kind:ID``. A
  snapshot before a rename and one after give the rename's NEW errors.
* :func:`find_measure` / :func:`table_exists` / :func:`column_exists` —
  targeted "did the change land?" checks for :func:`write_lane.commit`'s
  ``verify`` callback.
* :func:`udf_rows` — DAX UDFs (AMO ``Model.Functions`` is empty on Desktop).

Every read goes through the shared query executor: DMVs via
``execute_dmv_query`` (command-gated, uncached) with an ``INFO.*`` fallback via
``validate_and_execute_dax(..., bypass_cache=True)``. Everything is best-effort:
``None`` means "could not read" (not connected, DMV missing) — callers must
treat that as *unverified*, never as "no errors".
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "column_exists",
    "diff_errors",
    "dmv_rows",
    "expression_errors",
    "find_measure",
    "measure_rows",
    "table_exists",
    "table_names_by_id",
    "udf_rows",
]

# kind -> (DMV, INFO function, name column, parent id column)
_ERROR_SOURCES = {
    "measure": ("TMSCHEMA_MEASURES", "INFO.MEASURES()", "Name", "TableID"),
    "column": ("TMSCHEMA_COLUMNS", "INFO.COLUMNS()", "ExplicitName", "TableID"),
    "calc_item": (
        "TMSCHEMA_CALCULATION_ITEMS",
        "INFO.CALCULATIONITEMS()",
        "Name",
        "CalculationGroupID",
    ),
    "format_string": (
        "TMSCHEMA_FORMAT_STRING_DEFINITIONS",
        "INFO.FORMATSTRINGDEFINITIONS()",
        "ObjectID",
        "ObjectID",
    ),
    "detail_rows": (
        "TMSCHEMA_DETAIL_ROWS_DEFINITIONS",
        "INFO.DETAILROWSDEFINITIONS()",
        "ObjectID",
        "ObjectID",
    ),
    "partition": ("TMSCHEMA_PARTITIONS", "INFO.PARTITIONS()", "Name", "TableID"),
    "rls": ("TMSCHEMA_TABLE_PERMISSIONS", "INFO.TABLEPERMISSIONS()", "TableID", "RoleID"),
}


def _executor():
    try:
        from core.infrastructure.connection_state import connection_state

        if not connection_state.is_connected():
            return None
        return connection_state.query_executor
    except Exception:  # noqa: BLE001
        return None


def _norm_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (row or {}).items():
        key = k[1:-1] if isinstance(k, str) and k.startswith("[") and k.endswith("]") else k
        out[key] = v
    return out


def dmv_rows(dmv: str, info_fn: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """Rows of ``$SYSTEM.<dmv>`` (keys unbracketed); INFO fallback; None on failure."""
    qe = _executor()
    if qe is None:
        return None
    try:
        res = qe.execute_dmv_query(f"SELECT * FROM $SYSTEM.{dmv}")
        if isinstance(res, dict) and res.get("success"):
            rows = res.get("rows")
            if rows is None:
                rows = res.get("data") or []
            return [_norm_row(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.debug("DMV %s failed: %s", dmv, e)
    if not info_fn:
        return None
    try:
        res = qe.validate_and_execute_dax(f"EVALUATE {info_fn}", 0, bypass_cache=True)
        if isinstance(res, dict) and res.get("success"):
            return [_norm_row(r) for r in res.get("rows") or []]
    except Exception as e:  # noqa: BLE001
        logger.debug("INFO fallback %s failed: %s", info_fn, e)
    return None


def table_names_by_id() -> Optional[Dict[Any, str]]:
    rows = dmv_rows("TMSCHEMA_TABLES", "INFO.TABLES()")
    if rows is None:
        return None
    return {r.get("ID"): str(r.get("Name") or "") for r in rows}


def measure_rows() -> Optional[List[Dict[str, Any]]]:
    """``[{id, table, name, expression, error, state}]`` for every measure."""
    rows = dmv_rows("TMSCHEMA_MEASURES", "INFO.MEASURES()")
    if rows is None:
        return None
    tables = table_names_by_id() or {}
    out = []
    for r in rows:
        out.append(
            {
                "id": r.get("ID"),
                "table": tables.get(r.get("TableID"), ""),
                "name": str(r.get("Name") or ""),
                "expression": r.get("Expression") or "",
                "error": r.get("ErrorMessage") or "",
                "state": r.get("State"),
                "display_folder": r.get("DisplayFolder") or "",
                "format_string": r.get("FormatString") or "",
            }
        )
    return out


def find_measure(table: str, name: str) -> Optional[Dict[str, Any]]:
    """The measure row, ``{}`` when it provably does not exist, None when unreadable."""
    rows = measure_rows()
    if rows is None:
        return None
    for r in rows:
        if r["name"] == name and (not table or r["table"] == table):
            return r
    return {}


def table_exists(name: str) -> Optional[bool]:
    tables = table_names_by_id()
    if tables is None:
        return None
    return name in tables.values()


def column_exists(table: str, column: str) -> Optional[bool]:
    tables = table_names_by_id()
    rows = dmv_rows("TMSCHEMA_COLUMNS", "INFO.COLUMNS()")
    if tables is None or rows is None:
        return None
    for r in rows:
        if tables.get(r.get("TableID")) != table:
            continue
        if str(r.get("ExplicitName") or r.get("InferredName") or "") == column:
            return True
    return False


def udf_rows() -> Optional[List[Dict[str, Any]]]:
    """UDFs via the executor's ``list_udfs`` (DMV/INFO); None when unreadable."""
    qe = _executor()
    if qe is None or not hasattr(qe, "list_udfs"):
        return None
    try:
        res = qe.list_udfs()
    except Exception as e:  # noqa: BLE001
        logger.debug("list_udfs failed: %s", e)
        return None
    if not isinstance(res, dict) or not res.get("success"):
        return None
    return [_norm_row(r) for r in res.get("rows") or []]


def expression_errors() -> Optional[Dict[str, str]]:
    """``{"kind:ID": "<label>: <ErrorMessage>"}`` for every object in error.

    None when nothing could be read at all (the caller cannot verify). Sources
    that are unavailable on this engine are skipped individually.
    """
    if _executor() is None:
        return None
    tables = table_names_by_id() or {}
    errors: Dict[str, str] = {}
    read_any = False
    for kind, (dmv, info_fn, name_col, parent_col) in _ERROR_SOURCES.items():
        rows = dmv_rows(dmv, info_fn)
        if rows is None:
            continue
        read_any = True
        for r in rows:
            msg = r.get("ErrorMessage")
            if not msg:
                continue
            if kind == "partition" and str(r.get("SourceType") or r.get("Type") or "") not in (
                "",
                "2",
                "Calculated",
            ):
                # M/query partitions carry REFRESH errors — not DAX-reference errors.
                continue
            parent = tables.get(r.get(parent_col), r.get(parent_col))
            label = f"{kind} {parent}[{r.get(name_col)}]"
            errors[f"{kind}:{r.get('ID')}"] = f"{label}: {msg}"
    return errors if read_any else None


def diff_errors(
    before: Optional[Dict[str, str]], after: Optional[Dict[str, str]]
) -> Optional[List[str]]:
    """Errors present in *after* but not *before*; None when either is unknown."""
    if before is None or after is None:
        return None
    return sorted(msg for key, msg in after.items() if key not in before)
