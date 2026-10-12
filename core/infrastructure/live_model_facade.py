"""Live-model facade — builds a TmdlModel-shaped object from DMV results.

The builder/detector/codemod code accepts duck-typed objects with:
    model.tables       -> list of objects with .name, .columns, .measures
    table.columns      -> list of objects with .name, .table_name
    table.measures     -> list of objects with .name, .expression, .table_name
    model.relationships -> list (may be empty)

Live PBI Desktop has no in-memory `model` on connection_state — data flows
through DMV INFO.* queries via query_executor.execute_info_query. This facade
runs those queries once, materializes lightweight dataclasses, and hands them
back to whatever code wants a model-shaped object.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class _Column:
    name: str
    table_name: str


@dataclass
class _Measure:
    name: str
    expression: str
    table_name: str


@dataclass
class _Table:
    name: str
    columns: List[_Column] = field(default_factory=list)
    measures: List[_Measure] = field(default_factory=list)
    # "import" / "directquery" / "dual" / "directlake" / "" (unknown or mixed partitions);
    # read by 15_DAX_Refactor use_divide, which must not rewrite DirectQuery-only measures.
    storage_mode: str = ""


@dataclass
class _Relationship:
    from_table: str = ""
    from_column: str = ""
    to_table: str = ""
    to_column: str = ""
    is_active: bool = True


@dataclass
class _LiveModel:
    tables: List[_Table] = field(default_factory=list)
    relationships: List[_Relationship] = field(default_factory=list)


def _rows(result: Any) -> List[dict]:
    """Extract row list from execute_info_query result, tolerating shape variants."""
    if not isinstance(result, dict):
        return []
    for key in ("rows", "data", "records", "results"):
        v = result.get(key)
        if isinstance(v, list):
            return v
    return []


def _row_get(row: dict, *names: str, default: Any = None) -> Any:
    """First non-None value among candidate column names (case-insensitive)."""
    if not isinstance(row, dict):
        return default
    norm = {str(k).lower(): v for k, v in row.items()}
    for n in names:
        v = norm.get(n.lower())
        if v is not None:
            return v
    return default


def _info(qe: Any, view: str, bypass_cache: bool) -> Any:
    if not bypass_cache:
        return qe.execute_info_query(view)
    try:
        return qe.execute_info_query(view, bypass_cache=True)
    except TypeError:  # executor without the kwarg (fakes / older builds)
        return qe.execute_info_query(view)


def build_live_model(bypass_cache: bool = False) -> Optional[_LiveModel]:
    """Build a live-model facade by querying TABLES, COLUMNS, MEASURES, RELATIONSHIPS.

    ``bypass_cache=True`` reads every INFO.* view fresh — required wherever the
    result feeds a staleness check or a write (15_DAX_Refactor plan_hash, review
    E-02; cross-WP edit by WP6). Returns None if not connected or if DMV
    queries fail.
    """
    try:
        from core.infrastructure.connection_state import connection_state
    except Exception as e:
        logger.warning("connection_state import failed: %s", e)
        return None

    if not connection_state.is_connected():
        return None

    qe = getattr(connection_state, "query_executor", None) or getattr(
        connection_state, "dax_query_executor", None
    )
    if qe is None:
        try:
            qe = connection_state.get_manager("query_executor")
        except Exception:
            qe = None
    if qe is None:
        logger.warning("query_executor not available on connection_state")
        return None

    model = _LiveModel()
    by_name: dict[str, _Table] = {}
    name_by_id: dict[str, str] = {}

    try:
        tbl_res = _info(qe, "TABLES", bypass_cache)
        for row in _rows(tbl_res):
            tname = _row_get(row, "Name")
            if not tname:
                continue
            t = _Table(name=str(tname))
            model.tables.append(t)
            by_name[str(tname)] = t
            tid = _row_get(row, "ID")
            if tid is not None:
                name_by_id[str(tid)] = str(tname)
    except Exception as e:
        logger.warning("TABLES query failed: %s", e)

    def _table_of(row: dict) -> Optional[str]:
        """Table name of an INFO.COLUMNS/MEASURES row (J-03a: resolve TableID)."""
        tname = _row_get(row, "Table", "TableName")
        tid = _row_get(row, "TableID")
        if tid is not None and (tname is None or str(tname) == str(tid)):
            tname = name_by_id.get(str(tid), tname)
        return str(tname) if tname else None

    try:
        col_res = _info(qe, "COLUMNS", bypass_cache)
        for row in _rows(col_res):
            # J-03(a): INFO.COLUMNS has ExplicitName/InferredName, no Name.
            cname = _row_get(row, "Name", "ExplicitName", "InferredName")
            tname = _table_of(row)
            ctype = _row_get(row, "Type")
            if str(ctype) == "3":  # RowNumber column — internal, never referenced
                continue
            if not cname or not tname:
                continue
            t = by_name.get(str(tname))
            if t is None:
                t = _Table(name=str(tname))
                model.tables.append(t)
                by_name[str(tname)] = t
            t.columns.append(_Column(name=str(cname), table_name=str(tname)))
    except Exception as e:
        logger.warning("COLUMNS query failed: %s", e)

    try:
        meas_res = _info(qe, "MEASURES", bypass_cache)
        for row in _rows(meas_res):
            mname = _row_get(row, "Name")
            tname = _table_of(row)
            expr = _row_get(row, "Expression", default="") or ""
            if not mname or not tname:
                continue
            t = by_name.get(str(tname))
            if t is None:
                t = _Table(name=str(tname))
                model.tables.append(t)
                by_name[str(tname)] = t
            t.measures.append(
                _Measure(name=str(mname), expression=str(expr), table_name=str(tname))
            )
    except Exception as e:
        logger.warning("MEASURES query failed: %s", e)

    try:
        from core.dax.analyzer.models import _norm_storage_mode

        modes: dict[str, set] = {}
        for row in _rows(_info(qe, "PARTITIONS", bypass_cache)):
            tname = _table_of(row)
            mode = _norm_storage_mode(_row_get(row, "Mode"))
            if tname:
                modes.setdefault(tname, set()).add(mode)
        for t in model.tables:
            found = modes.get(t.name, set())
            t.storage_mode = next(iter(found)) if len(found) == 1 else ""
    except Exception as e:
        logger.debug("PARTITIONS query failed (non-fatal, storage modes unknown): %s", e)

    try:
        rel_res = _info(qe, "RELATIONSHIPS", bypass_cache)
        for row in _rows(rel_res):
            model.relationships.append(
                _Relationship(
                    from_table=str(_row_get(row, "FromTable", default="") or ""),
                    from_column=str(_row_get(row, "FromColumn", default="") or ""),
                    to_table=str(_row_get(row, "ToTable", default="") or ""),
                    to_column=str(_row_get(row, "ToColumn", default="") or ""),
                    is_active=bool(_row_get(row, "IsActive", default=True)),
                )
            )
    except Exception as e:
        logger.debug("RELATIONSHIPS query failed (non-fatal): %s", e)

    return model
