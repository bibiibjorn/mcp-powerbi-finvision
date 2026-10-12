"""Wrapper around INFO.VIEW.* DAX functions with DMV fallback for older models.

INFO.VIEW.MEASURES / TABLES / COLUMNS / RELATIONSHIPS arrived in CL 1604+
(Power BI Desktop >= April 2024). On models below that compatibility level
the call fails, so we fall back to the TMSCHEMA DMVs and tag the result with
``source: "DMV_FALLBACK"``.

Review E-26:
- results are read with ``bypass_cache=True`` (``info_view measures`` was up to
  600 s stale after an edit);
- the fallback rows are NORMALISED to the INFO.VIEW column names (``Name``,
  ``Table``, ``FromTable`` ... with ids resolved to names) instead of returning
  MDSCHEMA ``MEASURE_NAME`` / TMSCHEMA id rows under the same view name;
- only a "function not recognised" error flips the per-connection capability
  flag; an empty error, a timeout or a transient exception no longer disables
  INFO.VIEW for the whole session.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_VALID_VIEWS = ("measures", "tables", "columns", "relationships")
_DMV = {
    "measures": "SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES",
    "tables": "SELECT * FROM $SYSTEM.TMSCHEMA_TABLES",
    "columns": "SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS",
    "relationships": "SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS",
}
# Kept for callers that imported the old name.
_DMV_FALLBACKS = _DMV

_CROSS_FILTER = {1: "OneDirection", 2: "BothDirections", 3: "Automatic"}
_CARDINALITY = {1: "One", 2: "Many"}


def _is_unsupported_error(msg: str) -> bool:
    """True only for "INFO.VIEW.X is not a known function"-style errors."""
    m = (msg or "").lower()
    if not m:
        return False
    if "info.view" in m and any(
        k in m
        for k in (
            "not recognized",
            "unrecognized",
            "cannot find",
            "does not exist",
            "unknown function",
            "failed to resolve",
            "is not a valid",
        )
    ):
        return True
    return "function" in m and ("not recognized" in m or "does not exist" in m) and "info" in m


def info_view(view: str, top_n: int = 1000) -> Dict[str, Any]:
    """Return rows of INFO.VIEW.<view>() with a normalised DMV fallback.

    Returns
    -------
    {
        "success": True,
        "view": <view>,
        "source": "INFO.VIEW" | "DMV_FALLBACK",
        "rows": [...],
        "columns": [...],
        "row_count": N,
        "truncated": bool,
    }
    """
    if view not in _VALID_VIEWS:
        return {
            "success": False,
            "error": f"Unknown view '{view}'. Valid: {list(_VALID_VIEWS)}",
            "error_type": "invalid_args",
        }

    from core.infrastructure.connection_state import connection_state

    if not connection_state.is_connected():
        return {
            "success": False,
            "error": "Not connected to Power BI Desktop",
            "error_type": "not_connected",
        }

    qe = getattr(connection_state, "query_executor", None)
    if qe is None:
        return {
            "success": False,
            "error": "DAX executor unavailable",
            "error_type": "not_connected",
        }

    top_n_clamped = max(1, int(top_n))

    supported = getattr(connection_state, "_info_view_supported", None)
    if supported is False:
        return _dmv_fallback(qe, view, top_n_clamped)

    dax = f"EVALUATE TOPN({top_n_clamped}, INFO.VIEW.{view.upper()}())"
    try:
        result = qe.validate_and_execute_dax(dax, top_n_clamped, bypass_cache=True)
    except Exception as e:  # noqa: BLE001 - transient: fall back for THIS call only
        logger.info("INFO.VIEW.%s raised — DMV fallback for this call (%s)", view, e)
        return _dmv_fallback(qe, view, top_n_clamped)

    if not isinstance(result, dict) or result.get("success") is False:
        err_msg = (result.get("error") or "") if isinstance(result, dict) else ""
        if _is_unsupported_error(str(err_msg)):
            connection_state._info_view_supported = False
            return _dmv_fallback(qe, view, top_n_clamped)
        return (
            result
            if isinstance(result, dict)
            else {
                "success": False,
                "error": "executor returned no result",
                "error_type": "query_error",
            }
        )

    connection_state._info_view_supported = True
    rows = [_strip_brackets(r) for r in (result.get("rows", []) or []) if isinstance(r, dict)]
    columns = [
        c[1:-1] if isinstance(c, str) and c.startswith("[") and c.endswith("]") else c
        for c in (result.get("columns", []) or [])
    ]
    return {
        "success": True,
        "view": view,
        "source": "INFO.VIEW",
        "rows": rows,
        "columns": columns,
        "row_count": len(rows),
        "truncated": len(rows) >= top_n_clamped,
    }


def _strip_brackets(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        (k[1:-1] if isinstance(k, str) and k.startswith("[") and k.endswith("]") else k): v
        for k, v in row.items()
    }


def _dmv_rows(qe, sql: str) -> List[Dict[str, Any]]:
    result = qe.execute_dmv_query(sql)
    if not isinstance(result, dict) or result.get("success") is False:
        raise RuntimeError(
            result.get("error", "DMV query failed") if isinstance(result, dict) else "no result"
        )
    return [_strip_brackets(r) for r in (result.get("rows") or result.get("data") or [])]


def _g(row: Dict[str, Any], *names: str) -> Any:
    for n in names:
        if row.get(n) is not None:
            return row[n]
    return None


def _normalise(view: str, rows: List[Dict[str, Any]], qe) -> List[Dict[str, Any]]:
    """Map TMSCHEMA rows onto the INFO.VIEW.<view> column names."""
    tables: Dict[str, str] = {}
    columns: Dict[str, tuple] = {}
    if view in ("measures", "columns", "relationships"):
        for t in _dmv_rows(qe, _DMV["tables"]):
            if _g(t, "ID") is not None:
                tables[str(t["ID"])] = str(_g(t, "Name") or "")
    if view == "relationships":
        for c in _dmv_rows(qe, _DMV["columns"]):
            if _g(c, "ID") is not None:
                columns[str(c["ID"])] = (
                    tables.get(str(_g(c, "TableID")), ""),
                    str(_g(c, "ExplicitName", "InferredName", "Name") or ""),
                )
    out: List[Dict[str, Any]] = []
    for r in rows:
        if view == "measures":
            out.append(
                {
                    "ID": _g(r, "ID"),
                    "Name": _g(r, "Name"),
                    "Table": tables.get(str(_g(r, "TableID")), _g(r, "TableID")),
                    "Expression": _g(r, "Expression"),
                    "Description": _g(r, "Description"),
                    "DisplayFolder": _g(r, "DisplayFolder"),
                    "FormatString": _g(r, "FormatString"),
                    "DataType": _g(r, "DataType"),
                    "IsHidden": _g(r, "IsHidden"),
                    "State": _g(r, "State"),
                }
            )
        elif view == "tables":
            out.append(
                {
                    "ID": _g(r, "ID"),
                    "Name": _g(r, "Name"),
                    "Description": _g(r, "Description"),
                    "IsHidden": _g(r, "IsHidden"),
                    "DataCategory": _g(r, "DataCategory"),
                }
            )
        elif view == "columns":
            if str(_g(r, "Type")) == "3":  # RowNumber — never user-visible
                continue
            out.append(
                {
                    "ID": _g(r, "ID"),
                    "Name": _g(r, "ExplicitName", "InferredName", "Name"),
                    "Table": tables.get(str(_g(r, "TableID")), _g(r, "TableID")),
                    "DataType": _g(r, "ExplicitDataType", "InferredDataType", "DataType"),
                    "Expression": _g(r, "Expression"),
                    "DisplayFolder": _g(r, "DisplayFolder"),
                    "IsHidden": _g(r, "IsHidden"),
                    "IsKey": _g(r, "IsKey"),
                    "Description": _g(r, "Description"),
                }
            )
        else:  # relationships
            ft, fc = columns.get(str(_g(r, "FromColumnID")), ("", ""))
            tt, tc = columns.get(str(_g(r, "ToColumnID")), ("", ""))
            out.append(
                {
                    "ID": _g(r, "ID"),
                    "Name": _g(r, "Name"),
                    "FromTable": ft or tables.get(str(_g(r, "FromTableID")), ""),
                    "FromColumn": fc,
                    "FromCardinality": _CARDINALITY.get(
                        _g(r, "FromCardinality"), _g(r, "FromCardinality")
                    ),
                    "ToTable": tt or tables.get(str(_g(r, "ToTableID")), ""),
                    "ToColumn": tc,
                    "ToCardinality": _CARDINALITY.get(
                        _g(r, "ToCardinality"), _g(r, "ToCardinality")
                    ),
                    "IsActive": _g(r, "IsActive"),
                    "CrossFilteringBehavior": _CROSS_FILTER.get(
                        _g(r, "CrossFilteringBehavior"), _g(r, "CrossFilteringBehavior")
                    ),
                }
            )
    return out


def _dmv_fallback(qe, view: str, top_n: int) -> Dict[str, Any]:
    """TMSCHEMA equivalent, normalised to the INFO.VIEW column names."""
    try:
        rows = _normalise(view, _dmv_rows(qe, _DMV[view]), qe)
    except Exception as e:  # noqa: BLE001
        return {
            "success": False,
            "error": f"INFO.VIEW.{view} unsupported and DMV fallback failed: {e}",
            "error_type": "info_view_unsupported",
        }
    truncated = False
    if top_n and len(rows) > top_n:
        rows = rows[:top_n]
        truncated = True
    return {
        "success": True,
        "view": view,
        "source": "DMV_FALLBACK",
        "rows": rows,
        "columns": list(rows[0].keys()) if rows else [],
        "row_count": len(rows),
        "truncated": truncated,
        "normalized_to_info_view": True,
    }
