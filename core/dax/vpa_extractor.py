"""VertiPaq Analyzer extractor — DMV + DAX statistics over the live model.

Runs the exact DMV set used by ``Dax.Model.Extractor`` (sql-bi/VertiPaq-Analyzer),
optionally augments it with DAX statistics for *true* cardinality, and hands the
raw rows to :mod:`pbi_perf_core.vpa` which owns every formula (sizes, %DB,
selectivity, encoding, one-to-many ratio, findings).

Nothing here computes a metric: this module is transport + batching only. If a
number looks wrong, the fix belongs in ``pbi_perf_core.vpa``.

Two cost tiers:

* ``include_stats=False`` — DMVs only. Cheap (a handful of round-trips), but
  cardinality falls back to the ``H$`` attribute-hierarchy row count.
* ``include_stats=True`` (default) — adds ``COUNTROWS`` / ``DISTINCTCOUNT``
  batches and relationship RI probes. Accurate, but it scans the model, so on a
  large fact table it is the expensive part of the call.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.dax.dax_names import column_ref, quote_table

logger = logging.getLogger(__name__)

# Batch sizes are Dax.Model.Extractor's own defaults (StatExtractor.cs):
# 50 scalar stats per UNION, 10 relationship probes per query.
STATS_BATCH_SIZE = 50
RELATIONSHIP_BATCH_SIZE = 10

VALID_VIEWS = ("summary", "tables", "columns", "relationships", "partitions", "segments", "all")

# DMV queries, verbatim from DmvExtractor.cs (see research doc §4.1). The
# `RIGHT(LEFT(TABLE_ID,2),1) <> '$'` predicate excludes the H$/U$/R$ internal
# structures, which are fetched separately because they carry different sizes.
DMV_STORAGE_TABLES = (
    "SELECT DIMENSION_NAME AS TABLE_NAME, TABLE_ID AS TABLE_ID, "
    "ROWS_COUNT AS ROWS_IN_TABLE, RIVIOLATION_COUNT AS RI_VIOLATION_COUNT "
    "FROM $SYSTEM.DISCOVER_STORAGE_TABLES "
    "WHERE RIGHT ( LEFT ( TABLE_ID, 2 ), 1 ) <> '$'"
)
DMV_STORAGE_COLUMNS = (
    "SELECT DIMENSION_NAME AS TABLE_NAME, COLUMN_ID AS COLUMN_ID, "
    "ATTRIBUTE_NAME AS COLUMN_NAME, DATATYPE AS [Data Type], "
    "DICTIONARY_SIZE AS DICTIONARY_SIZE_BYTES, COLUMN_ENCODING AS COLUMN_ENCODING_INT "
    "FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMNS WHERE COLUMN_TYPE = 'BASIC_DATA'"
)
DMV_COLUMN_CARDINALITY = (
    "SELECT DIMENSION_NAME AS TABLE_NAME, TABLE_ID AS COLUMN_HIERARCHY_ID, "
    "ROWS_COUNT - 3 AS COLUMN_CARDINALITY "
    "FROM $SYSTEM.DISCOVER_STORAGE_TABLES WHERE LEFT ( TABLE_ID, 2 ) = 'H$'"
)
DMV_SEGMENTS = (
    "SELECT DIMENSION_NAME AS TABLE_NAME, PARTITION_NAME, COLUMN_ID AS COLUMN_NAME, "
    "SEGMENT_NUMBER, TABLE_PARTITION_NUMBER, RECORDS_COUNT AS SEGMENT_ROWS, USED_SIZE, "
    "COMPRESSION_TYPE, BITS_COUNT, BOOKMARK_BITS_COUNT, VERTIPAQ_STATE "
    "FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS "
    "WHERE RIGHT ( LEFT ( TABLE_ID, 2 ), 1 ) <> '$'"
)
DMV_COLUMN_HIERARCHIES = (
    "SELECT DIMENSION_NAME AS TABLE_NAME, COLUMN_ID AS STRUCTURE_NAME, SEGMENT_NUMBER, "
    "TABLE_PARTITION_NUMBER, USED_SIZE, TABLE_ID AS COLUMN_HIERARCHY_ID "
    "FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS "
    "WHERE LEFT ( TABLE_ID, 2 ) = 'H$' AND SEGMENT_NUMBER = 0"
)
DMV_USER_HIERARCHIES = (
    "SELECT DIMENSION_NAME AS TABLE_NAME, COLUMN_ID AS STRUCTURE_NAME, USED_SIZE, "
    "TABLE_ID AS HIERARCHY_ID "
    "FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS WHERE LEFT ( TABLE_ID, 2 ) = 'U$'"
)
DMV_RELATIONSHIP_SIZES = (
    "SELECT DIMENSION_NAME AS TABLE_NAME, TABLE_ID AS RELATIONSHIP_ID, USED_SIZE "
    "FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS WHERE LEFT ( TABLE_ID, 2 ) = 'R$'"
)
DMV_RELATIONSHIPS = (
    "SELECT [ID] AS [RelationshipID], [FromTableID], [FromColumnID], [FromCardinality], "
    "[ToTableID], [ToColumnID], [ToCardinality], [IsActive], [CrossFilteringBehavior], "
    "[JoinOnDateBehavior], [RelyOnReferentialIntegrity], [SecurityFilteringBehavior], [State] "
    "FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS"
)
DMV_PARTITIONS = (
    "SELECT [ID] AS PARTITION_ID, [TableID] AS TABLE_ID, [Name] AS PARTITION_NAME, "
    "[Description], [State], [Type], [Mode], [RefreshedTime] "
    "FROM $SYSTEM.TMSCHEMA_PARTITIONS ORDER BY [TableID]"
)
DMV_TABLES = "SELECT * FROM $SYSTEM.TMSCHEMA_TABLES"
DMV_COLUMNS = "SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS"
# P-17: only ``len(measure_rows)`` is used (``measure_count``); skip every other field
# (Expression text included) instead of converting ~18 cells per measure.
DMV_MEASURES = "SELECT [ID] FROM $SYSTEM.TMSCHEMA_MEASURES"


# --------------------------------------------------------------------------
# executor plumbing
# --------------------------------------------------------------------------


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the ``[bracket]`` wrappers ADOMD puts on DMV column names."""
    out: Dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(key, str) and key.startswith("[") and key.endswith("]"):
            out[key[1:-1]] = value
        else:
            out[key] = value
    return out


def _rows_of(result: Any) -> List[Dict[str, Any]]:
    """Pull rows out of an executor envelope, tolerating rows/data key drift."""
    if not isinstance(result, dict):
        return []
    if not result.get("success", True):
        return []
    rows = result.get("rows")
    if rows is None:
        rows = result.get("data")
    if not rows:
        return []
    return [_normalize_row(r) for r in rows if isinstance(r, dict)]


def _run_dmv(qe: Any, query: str) -> List[Dict[str, Any]]:
    """Run one DMV. A failing DMV degrades that view, never the whole call."""
    try:
        return _rows_of(qe.execute_dmv_query(query))
    except Exception as exc:  # noqa: BLE001 - DMV availability varies per engine build
        logger.warning("VPA DMV failed (%s): %s", query.split("FROM")[-1].strip()[:60], exc)
        return []


def _run_dax(qe: Any, query: str) -> Tuple[List[Dict[str, Any]], bool]:
    """Run one DAX statistics batch.

    Returns ``(rows, failed)``. The flag matters: a batch that *errored* is worth
    retrying item-by-item (one unqueryable column poisons the whole UNION), a
    batch that merely returned nothing is not.
    """
    try:
        result = qe.validate_and_execute_dax(query, 0)
    except Exception as exc:  # noqa: BLE001 - a single bad column must not kill the sweep
        logger.warning("VPA DAX stats batch failed: %s", exc)
        return [], True
    failed = isinstance(result, dict) and not result.get("success", True)
    if failed:
        logger.warning("VPA DAX stats batch error: %s", result.get("error"))
    return _rows_of(result), failed


# --------------------------------------------------------------------------
# DAX statistics
# --------------------------------------------------------------------------


# C-15: the DAX quoting rules live in core.dax.dax_names (same output as the old local copies).
_quote_table = quote_table
_quote_column = column_ref


def _label(text: Any) -> str:
    """A ROW() label / id as a DAX string literal (review E-20: a '"' in a table
    or column name used to break the whole batch and the stat went missing)."""
    return '"' + str(text).replace('"', '""') + '"'


def _batched(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _stat_value(row: Dict[str, Any], *names: str) -> Any:
    """Read a ROW() column regardless of the [bracket]/case form ADOMD returns."""
    for name in names:
        for candidate in (name, f"[{name}]", name.upper(), name.lower()):
            if candidate in row:
                return row[candidate]
    return None


def collect_table_stats(qe: Any, tables: Sequence[str]) -> Dict[str, int]:
    """``COUNTROWS`` per table, batched into UNION'd ROW() constructors."""
    stats: Dict[str, int] = {}
    for batch in _batched(list(tables), STATS_BATCH_SIZE):
        rows_dax = ", ".join(
            f'ROW("VpaTable", {_label(t)}, "VpaCardinality", COUNTROWS({_quote_table(t)}))'
            for t in batch
        )
        query = f"EVALUATE UNION({rows_dax})" if len(batch) > 1 else f"EVALUATE {rows_dax}"
        rows, _failed = _run_dax(qe, query)
        for row in rows:
            name = _stat_value(row, "VpaTable")
            value = _stat_value(row, "VpaCardinality")
            if name is None or value is None:
                continue
            try:
                stats[str(name)] = int(value)
            except (TypeError, ValueError):
                continue
    return stats


def collect_column_stats(
    qe: Any,
    columns: Sequence[Tuple[str, str]],
    group_by_columns: Optional[set] = None,
) -> Dict[str, int]:
    """``DISTINCTCOUNT`` per column, keyed ``Table[Column]``.

    Field-parameter columns (those with GroupByColumns) reject DISTINCTCOUNT, so
    they use ``COUNTROWS(ALLNOBLANKROW(col))`` exactly as StatExtractor does.
    """
    group_by_columns = group_by_columns or set()
    stats: Dict[str, int] = {}
    for batch in _batched(list(columns), STATS_BATCH_SIZE):
        parts = []
        for table, column in batch:
            ref = _quote_column(table, column)
            expr = (
                f"COUNTROWS(ALLNOBLANKROW({ref}))"
                if (table, column) in group_by_columns
                else f"DISTINCTCOUNT({ref})"
            )
            parts.append(
                f'ROW("VpaTable", {_label(table)}, "VpaColumn", {_label(column)}, '
                f'"VpaCardinality", {expr})'
            )
        query = f"EVALUATE UNION({', '.join(parts)})" if len(parts) > 1 else f"EVALUATE {parts[0]}"
        rows, failed = _run_dax(qe, query)
        if failed and len(batch) > 1:
            # One unqueryable column poisons the whole UNION — retry singly so the
            # rest of the batch still yields numbers.
            for pair in batch:
                stats.update(collect_column_stats(qe, [pair], group_by_columns))
            continue
        for row in rows:
            table = _stat_value(row, "VpaTable")
            column = _stat_value(row, "VpaColumn")
            value = _stat_value(row, "VpaCardinality")
            if table is None or column is None or value is None:
                continue
            try:
                stats[f"{table}[{column}]"] = int(value)
            except (TypeError, ValueError):
                continue
    return stats


def collect_relationship_stats(
    qe: Any, relationships: Sequence[Dict[str, Any]]
) -> Dict[str, Dict[str, int]]:
    """Referential-integrity probes, batched 10 at a time.

    Each probe counts the fact keys that land on the dimension's blank row —
    ``MissingKeys`` distinct keys over ``InvalidRows`` fact rows.
    """
    stats: Dict[str, Dict[str, int]] = {}
    probes = [
        r
        for r in relationships
        if r.get("from_table") and r.get("from_column") and r.get("to_table") and r.get("to_column")
    ]
    for batch in _batched(probes, RELATIONSHIP_BATCH_SIZE):
        parts = []
        for rel in batch:
            fk = _quote_column(rel["from_table"], rel["from_column"])
            pk = _quote_column(rel["to_table"], rel["to_column"])
            parts.append(
                "CALCULATETABLE("
                f'ROW("VpaRelationshipId", {_label(rel.get("id", ""))}, '
                f'"MissingKeys", DISTINCTCOUNT({fk}), '
                f'"InvalidRows", COUNTROWS({_quote_table(rel["from_table"])})), '
                f"ISBLANK({pk}), USERELATIONSHIP({fk}, {pk}))"
            )
        query = f"EVALUATE UNION({', '.join(parts)})" if len(parts) > 1 else f"EVALUATE {parts[0]}"
        rows, failed = _run_dax(qe, query)
        if failed and len(batch) > 1:
            for rel in batch:
                stats.update(collect_relationship_stats(qe, [rel]))
            continue
        for row in rows:
            rel_id = _stat_value(row, "VpaRelationshipId")
            if rel_id is None:
                continue
            try:
                stats[str(rel_id)] = {
                    "missing_keys": int(_stat_value(row, "MissingKeys") or 0),
                    "invalid_rows": int(_stat_value(row, "InvalidRows") or 0),
                }
            except (TypeError, ValueError):
                continue
    return stats


# --------------------------------------------------------------------------
# schema resolution
# --------------------------------------------------------------------------


#: ``System (645)`` -> ``System``. The storage DMVs tag every object with its
#: internal id; the schema DMVs do not, so one side has to be normalised before
#: the two can be joined.
_OBJECT_ID_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")


def _strip_object_id(name: str) -> str:
    """Drop the trailing ``(id)`` the storage DMVs append to object names."""
    return _OBJECT_ID_SUFFIX_RE.sub("", name or "").strip()


def _build_id_maps(
    tmschema_tables: Sequence[Dict[str, Any]], tmschema_columns: Sequence[Dict[str, Any]]
) -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]], set]:
    """Map TMSCHEMA ids to names — TMSCHEMA_RELATIONSHIPS speaks ids only."""
    table_by_id: Dict[str, str] = {}
    for row in tmschema_tables:
        table_id = row.get("ID") or row.get("Id")
        name = row.get("Name")
        if table_id is not None and name:
            table_by_id[str(table_id)] = str(name)

    column_by_id: Dict[str, Tuple[str, str]] = {}
    group_by_columns: set = set()
    for row in tmschema_columns:
        column_id = row.get("ID") or row.get("Id")
        name = row.get("ExplicitName") or row.get("InferredName") or row.get("Name")
        table_name = table_by_id.get(str(row.get("TableID") or row.get("TableId") or ""))
        if column_id is None or not name or not table_name:
            continue
        column_by_id[str(column_id)] = (table_name, str(name))
        if row.get("GroupByColumns"):
            group_by_columns.add((table_name, str(name)))
    return table_by_id, column_by_id, group_by_columns


_REL_ID_SUFFIX = re.compile(r"\((\d+)\)\s*$")

#: ``H$Sales (12)$Amount (34)`` — the real attribute-hierarchy structure id.
_H_ID_FULL = re.compile(r"^H\$(?P<table>.+?) \((?P<tid>\d+)\)\$(?P<col>.+?) \((?P<cid>\d+)\)\s*$")
#: ``H$Amount (1)`` — the short form older fixtures / builds use.
_H_ID_SHORT = re.compile(r"^H\$(?P<col>.+?) \((?P<cid>\d+)\)\s*$")
#: Internal hierarchy structures reported as the segment "column" of an H$ row.
_H_STRUCTURES = {"POS_TO_ID", "ID_TO_POS"}


def parse_hierarchy_id(hierarchy_id: str, table_hint: str = "") -> Tuple[str, str]:
    """``H$Table (id)$Column (id)`` -> ``(Table, Column)`` (review E-18).

    The short ``H$Column (id)`` form yields ``(table_hint, Column)``; anything
    else ``("", "")``. Needs live check: confirm the id format with one
    ``DMV_COLUMN_HIERARCHIES`` / ``DMV_COLUMN_CARDINALITY`` run on Desktop.
    """
    hid = str(hierarchy_id or "").strip()
    m = _H_ID_FULL.match(hid)
    if m:
        return m.group("table"), m.group("col")
    m = _H_ID_SHORT.match(hid)
    if m and "$" not in m.group("col"):
        return table_hint, m.group("col")
    return "", ""


def _relationship_size_by_id(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """``R$Table (37)`` → the size booked against relationship id 37."""
    sizes: Dict[str, int] = {}
    for row in rows:
        table_id = str(row.get("RELATIONSHIP_ID") or row.get("TABLE_ID") or "")
        match = _REL_ID_SUFFIX.search(table_id)
        if not match:
            continue
        try:
            sizes[match.group(1)] = sizes.get(match.group(1), 0) + int(row.get("USED_SIZE") or 0)
        except (TypeError, ValueError):
            continue
    return sizes


def _resolve_relationships(
    dmv_rows: Sequence[Dict[str, Any]],
    table_by_id: Dict[str, str],
    column_by_id: Dict[str, Tuple[str, str]],
    sizes: Dict[str, int],
) -> List[Dict[str, Any]]:
    """Turn TMSCHEMA_RELATIONSHIPS id rows into the name-shaped rows vpa wants."""
    out: List[Dict[str, Any]] = []
    for row in dmv_rows:
        rel_id = str(row.get("RelationshipID") or row.get("ID") or "")
        from_table = table_by_id.get(str(row.get("FromTableID") or ""), "")
        to_table = table_by_id.get(str(row.get("ToTableID") or ""), "")
        from_column = column_by_id.get(str(row.get("FromColumnID") or ""), ("", ""))[1]
        to_column = column_by_id.get(str(row.get("ToColumnID") or ""), ("", ""))[1]
        out.append(
            {
                "id": rel_id,
                "Name": f"{from_table}[{from_column}] -> {to_table}[{to_column}]",
                "from_table": from_table,
                "from_column": from_column,
                "to_table": to_table,
                "to_column": to_column,
                "FromTable": from_table,
                "FromColumn": from_column,
                "ToTable": to_table,
                "ToColumn": to_column,
                # State 1 == Ready; TMSCHEMA reports IsActive as 0/1.
                "IsActive": bool(row.get("IsActive", True)),
                "CrossFilteringBehavior": _CROSS_FILTER.get(
                    row.get("CrossFilteringBehavior"), str(row.get("CrossFilteringBehavior") or "")
                ),
                "UsedSize": sizes.get(rel_id, 0),
            }
        )
    return out


_CROSS_FILTER = {1: "OneDirection", 2: "BothDirections", 3: "Automatic"}


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------


def extract_model_metrics(qe: Any, include_stats: bool = True) -> Dict[str, Any]:
    """Fetch every DMV (+ optional DAX stats) and build the pbi_perf_core model.

    Returns ``{"model": ModelMetrics, "partitions": [...], "segments": [...],
    "measure_count": int, "stats_queries": int}``. Raises nothing: an
    unavailable DMV yields an empty section.
    """
    storage_tables = _run_dmv(qe, DMV_STORAGE_TABLES)
    storage_columns = _run_dmv(qe, DMV_STORAGE_COLUMNS)
    segment_rows = _run_dmv(qe, DMV_SEGMENTS)
    hierarchy_rows = _run_dmv(qe, DMV_COLUMN_HIERARCHIES)
    user_hierarchy_rows = _run_dmv(qe, DMV_USER_HIERARCHIES)
    rel_size_rows = _run_dmv(qe, DMV_RELATIONSHIP_SIZES)
    rel_rows = _run_dmv(qe, DMV_RELATIONSHIPS)
    partition_rows = _run_dmv(qe, DMV_PARTITIONS)
    tmschema_tables = _run_dmv(qe, DMV_TABLES)
    tmschema_columns = _run_dmv(qe, DMV_COLUMNS)
    measure_rows = _run_dmv(qe, DMV_MEASURES)
    dmv_cardinality = _run_dmv(qe, DMV_COLUMN_CARDINALITY)

    table_by_id, column_by_id, group_by_columns = _build_id_maps(tmschema_tables, tmschema_columns)

    # E-18: segments carry the storage COLUMN_ID ("Amount (123)"); join them to
    # DISCOVER_STORAGE_TABLE_COLUMNS by that id (as DmvExtractor does) and only
    # fall back to the "(id)"-stripped name when the id is unknown.
    name_by_storage_id: Dict[Tuple[str, str], str] = {}
    for row in storage_columns:
        sid = row.get("COLUMN_ID")
        cname = row.get("COLUMN_NAME") or row.get("ATTRIBUTE_NAME")
        tname = row.get("TABLE_NAME")
        if sid is not None and cname and tname:
            name_by_storage_id[(str(tname), str(sid))] = str(cname)

    # vpa.build_model_metrics reads all size-bearing segment rows from one
    # sequence and routes them by the H$/U$/R$ prefix on TABLE_ID.
    all_segments: List[Dict[str, Any]] = []
    for row in segment_rows:
        row = dict(row)
        table_name = row.get("TABLE_NAME") or _strip_object_id(str(row.get("TABLE_ID", "")))
        raw_col = str(row.get("COLUMN_NAME") or row.get("COLUMN_ID", ""))
        row["TABLE_ID"] = table_name
        row["COLUMN_ID"] = name_by_storage_id.get(
            (str(table_name), raw_col), _strip_object_id(raw_col)
        )
        all_segments.append(row)
    for row in hierarchy_rows:
        # The segment "column" of an H$ row is the structure (POS_TO_ID /
        # ID_TO_POS); the owning column lives in the H$ id itself.
        table_name = str(row.get("TABLE_NAME", "") or "")
        h_table, h_col = parse_hierarchy_id(row.get("COLUMN_HIERARCHY_ID", ""), table_name)
        if not h_col:
            structure = str(row.get("STRUCTURE_NAME", "") or "")
            h_col = "" if structure.upper() in _H_STRUCTURES else _strip_object_id(structure)
        if not h_col:
            continue
        all_segments.append(
            {
                "TABLE_ID": f"H${table_name or h_table}",
                "COLUMN_ID": h_col,
                "USED_SIZE": row.get("USED_SIZE", 0),
            }
        )
    for row in user_hierarchy_rows:
        all_segments.append(
            {
                "TABLE_ID": f"U${row.get('TABLE_NAME', '')}",
                "COLUMN_ID": row.get("STRUCTURE_NAME", ""),
                "USED_SIZE": row.get("USED_SIZE", 0),
            }
        )
    for row in rel_size_rows:
        # DIMENSION_NAME on an R$ row carries the relationship id suffix
        # ("Sales (37)"); vpa.py keys table lookups on the bare table name.
        all_segments.append(
            {
                "TABLE_ID": f"R${_REL_ID_SUFFIX.sub('', str(row.get('TABLE_NAME', ''))).strip()}",
                "COLUMN_ID": "",
                "USED_SIZE": row.get("USED_SIZE", 0),
            }
        )

    storage_tables_norm = [
        {
            "TABLE_ID": row.get("TABLE_NAME") or row.get("TABLE_ID"),
            "ROWS_COUNT": row.get("ROWS_IN_TABLE", 0),
            "RIVIOLATION_COUNT": row.get("RI_VIOLATION_COUNT", 0),
        }
        for row in storage_tables
    ]
    storage_columns_norm = [
        {
            "TABLE_ID": row.get("TABLE_NAME"),
            "ATTRIBUTE_NAME": row.get("COLUMN_NAME") or row.get("ATTRIBUTE_NAME"),
            "DATATYPE": row.get("Data Type") or row.get("DATATYPE"),
            "DICTIONARY_SIZE": row.get("DICTIONARY_SIZE_BYTES") or row.get("DICTIONARY_SIZE", 0),
            "COLUMN_ENCODING": row.get("COLUMN_ENCODING_INT") or row.get("COLUMN_ENCODING"),
            "COLUMN_TYPE": "BASIC_DATA",
        }
        for row in storage_columns
    ]
    partitions_norm = [
        {
            "TableName": table_by_id.get(str(row.get("TABLE_ID") or ""), ""),
            "PartitionName": row.get("PARTITION_NAME", ""),
            "Mode": row.get("Mode"),
            "Type": row.get("Type"),
            "State": row.get("State"),
            "RefreshedTime": row.get("RefreshedTime"),
        }
        for row in partition_rows
    ]

    table_names = [str(r["TABLE_ID"]) for r in storage_tables_norm if r.get("TABLE_ID")]
    column_pairs = [
        (str(r["TABLE_ID"]), str(r["ATTRIBUTE_NAME"]))
        for r in storage_columns_norm
        if r.get("TABLE_ID") and r.get("ATTRIBUTE_NAME")
    ]

    relationships = _resolve_relationships(
        rel_rows, table_by_id, column_by_id, _relationship_size_by_id(rel_size_rows)
    )

    # DMV cardinality (H$ ROWS_COUNT-3) is the cheap fallback; DAX stats override it.
    dax_stats: Dict[str, Any] = {}
    for row in dmv_cardinality:
        table_name = str(row.get("TABLE_NAME") or "")
        h_table, column_name = parse_hierarchy_id(
            str(row.get("COLUMN_HIERARCHY_ID") or ""), table_name
        )
        table_name = table_name or h_table
        if table_name and column_name:
            dax_stats[f"{table_name}[{column_name}]"] = row.get("COLUMN_CARDINALITY", 0)

    stats_queries = 0
    if include_stats:
        table_stats = collect_table_stats(qe, table_names)
        column_stats = collect_column_stats(qe, column_pairs, group_by_columns)
        rel_stats = collect_relationship_stats(qe, relationships)
        dax_stats.update(table_stats)
        dax_stats.update(column_stats)
        for rel in relationships:
            probe = rel_stats.get(str(rel.get("id", "")))
            if probe:
                rel["MissingKeys"] = probe["missing_keys"]
                rel["InvalidRows"] = probe["invalid_rows"]
        stats_queries = (
            -(-len(table_names) // STATS_BATCH_SIZE)
            + -(-len(column_pairs) // STATS_BATCH_SIZE)
            + -(-len(relationships) // RELATIONSHIP_BATCH_SIZE)
        )

    from pbi_perf_core.vpa import build_model_metrics

    model = build_model_metrics(
        storage_tables=storage_tables_norm,
        storage_columns=storage_columns_norm,
        segments=all_segments,
        relationships=relationships,
        partitions=partitions_norm,
        dax_stats=dax_stats,
    )

    return {
        "model": model,
        "partitions": partitions_norm,
        "segments": segment_rows,
        "measure_count": len(measure_rows),
        "stats_queries": stats_queries,
        "stats_included": bool(include_stats),
    }


# --------------------------------------------------------------------------
# findings — thresholds from dax-tooling.md §2
# --------------------------------------------------------------------------

#: DefaultSegmentRowCount on Desktop / shared-capacity Import (dax-tooling.md §2).
DESKTOP_SEGMENT_ROWS = 1_000_000
#: "% DB > 5% = red flag" on the Columns view.
PCT_DB_RED_FLAG = 5.0
#: "Cardinality > 1M on numeric = split" on the Columns view.
HIGH_CARDINALITY = 1_000_000
#: "Cols > 50 on a fact = column-pruning candidate" on the Tables view.
WIDE_TABLE_COLUMNS = 50


def _core_count() -> int:
    import os

    return os.cpu_count() or 4


def build_findings(
    model: Any, top_n: int = 15, core_count: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Extend pbi_perf_core's findings with the local dax-tooling.md thresholds.

    pbi_perf_core covers dominant columns, HASH-encoded numerics, dictionary-heavy
    columns, RI violations, low one-to-many ratio and missing keys. Added here:
    per-column %DB, high-cardinality numerics, segment count vs cores, and wide
    tables — the thresholds documented for the DAX Studio Tables/Columns views.
    """
    from pbi_perf_core.vpa import analyse_model
    from pbi_perf_core.vpa import _is_numeric_type as _vpa_is_numeric

    findings: List[Dict[str, Any]] = list(analyse_model(model, top_n=top_n).get("findings", []))
    cores = core_count if core_count is not None else _core_count()
    model_size = model.model_size

    for column in model.all_columns():
        ref = f"{column.table_name}[{column.column_name}]"
        if model_size:
            pct_db = 100.0 * column.total_size / model_size
            # DOMINANT_COLUMN already fires above 10%; this catches the 5-10% band.
            if PCT_DB_RED_FLAG <= pct_db <= 10.0:
                findings.append(
                    {
                        "severity": "medium",
                        "code": "LARGE_COLUMN_PCT_DB",
                        "object": ref,
                        "detail": (
                            f"{ref} is {round(pct_db, 1)}% of the model "
                            f"({round(column.total_size / 1024 / 1024, 1)} MB) - above the "
                            f"{PCT_DB_RED_FLAG}% red-flag line. Sort the Columns view by Col Size "
                            "and attack the top 5-10."
                        ),
                    }
                )
        # cardinality may be None (unknown without stats); DMV types are DBTYPE_* codes.
        if (column.cardinality or 0) > HIGH_CARDINALITY and _vpa_is_numeric(column.data_type):
            findings.append(
                {
                    "severity": "medium",
                    "code": "HIGH_CARDINALITY_NUMERIC",
                    "object": ref,
                    "detail": (
                        f"{ref} has {column.cardinality:,} distinct values. Split it "
                        "(date+time, integer+decimal) or round it - cardinality drives both "
                        "dictionary and data size."
                    ),
                }
            )

    for table in model.tables:
        if table.rows_count > DESKTOP_SEGMENT_ROWS and 0 < table.segments_count < cores:
            findings.append(
                {
                    "severity": "low",
                    "code": "SEGMENTS_BELOW_CORES",
                    "object": table.table_name,
                    "detail": (
                        f"{table.table_name} has {table.segments_count} segment(s) across "
                        f"{table.rows_count:,} rows but the machine has {cores} cores - scans "
                        "cannot use every core. More segments = more parallel scan potential."
                    ),
                }
            )
        if len(table.columns) > WIDE_TABLE_COLUMNS:
            findings.append(
                {
                    "severity": "low",
                    "code": "WIDE_TABLE",
                    "object": table.table_name,
                    "detail": (
                        f"{table.table_name} has {len(table.columns)} columns (>{WIDE_TABLE_COLUMNS}) - "
                        "a column-pruning candidate; unused columns still cost RAM and refresh time."
                    ),
                }
            )

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: order.get(f.get("severity"), 9))
    return findings


# --------------------------------------------------------------------------
# views + export
# --------------------------------------------------------------------------


def _tables_view(model: Any, top_n: int) -> List[Dict[str, Any]]:
    size = model.model_size
    rows = [t.to_dict(size) for t in sorted(model.tables, key=lambda t: -t.table_size)]
    return rows[:top_n]


def _columns_view(model: Any, top_n: int) -> List[Dict[str, Any]]:
    size = model.model_size
    table_size = {t.table_name: t.table_size for t in model.tables}
    rows = [
        c.to_dict(size, table_size.get(c.table_name, 0))
        for c in sorted(model.all_columns(), key=lambda c: -c.total_size)
    ]
    return rows[:top_n]


def _relationships_view(model: Any, top_n: int) -> List[Dict[str, Any]]:
    rows = [r.to_dict() for r in sorted(model.relationships, key=lambda r: -r.size)]
    return rows[:top_n]


def _segments_view(segments: Sequence[Dict[str, Any]], top_n: int) -> List[Dict[str, Any]]:
    rows = sorted(segments, key=lambda r: -(r.get("USED_SIZE") or 0))
    return [
        {
            "table": r.get("TABLE_NAME", ""),
            "column": r.get("COLUMN_NAME", ""),
            "partition": r.get("PARTITION_NAME", ""),
            "segment": r.get("SEGMENT_NUMBER", 0),
            "rows": r.get("SEGMENT_ROWS", 0),
            "used_size": r.get("USED_SIZE", 0),
            "compression": r.get("COMPRESSION_TYPE", ""),
            "bits": r.get("BITS_COUNT", 0),
        }
        for r in rows[:top_n]
    ]


def _flatten_for_csv(payload: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Pick the richest table-shaped section for a CSV export."""
    for key in ("columns", "tables", "relationships", "partitions", "segments", "findings"):
        rows = payload.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            headers: List[str] = []
            for row in rows:
                for name in row:
                    if name not in headers:
                        headers.append(name)
            return headers, rows
    return [], []


def export_metrics(payload: Dict[str, Any], export_path: str) -> Dict[str, Any]:
    """Write the payload to .json or .csv; returns the export descriptor."""
    path = Path(export_path)
    suffix = path.suffix.lower()
    if suffix not in (".json", ".csv"):
        return {
            "success": False,
            "error": f"export_path must end in .json or .csv (got '{path.suffix}')",
            "error_type": "invalid_args",
        }
    path.parent.mkdir(parents=True, exist_ok=True)

    if suffix == ".json":
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return {"success": True, "export_path": str(path), "format": "json"}

    headers, rows = _flatten_for_csv(payload)
    if not headers:
        return {
            "success": False,
            "error": "Nothing table-shaped to write as CSV (use view=columns/tables or .json)",
            "error_type": "invalid_args",
        }
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return {"success": True, "export_path": str(path), "format": "csv", "rows_written": len(rows)}


def vertipaq_analyzer(
    include_stats: bool = True,
    top_n: int = 50,
    view: str = "summary",
    export_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Handler-level entry: full VertiPaq Analyzer metrics for the live model."""
    if view not in VALID_VIEWS:
        return {
            "success": False,
            "error": f"Unknown view '{view}'. Valid: {list(VALID_VIEWS)}",
            "error_type": "invalid_args",
        }

    from core.infrastructure.connection_state import connection_state

    if not connection_state.is_connected():
        return {
            "success": False,
            "error": "Not connected to Power BI Desktop. Use 01_Connection first.",
            "error_type": "not_connected",
        }
    qe = getattr(connection_state, "query_executor", None)
    if qe is None:
        return {
            "success": False,
            "error": "query_executor not available",
            "error_type": "not_connected",
        }

    extracted = extract_model_metrics(qe, include_stats=bool(include_stats))
    model = extracted["model"]
    if not model.tables:
        return {
            "success": False,
            "error": (
                "DISCOVER_STORAGE_TABLES returned no data - VertiPaq DMVs may be "
                "unavailable on this connection"
            ),
            "error_type": "dmv_unavailable",
        }

    top_n = max(1, int(top_n))
    summary = model.summary()
    summary["measures"] = extracted["measure_count"]
    summary["partitions"] = len(extracted["partitions"])
    summary["segments"] = sum(t.segments_count for t in model.tables)
    summary["stats_included"] = extracted["stats_included"]

    payload: Dict[str, Any] = {
        "success": True,
        "view": view,
        "summary": summary,
        "findings": build_findings(model, top_n=top_n),
        "stats_included": extracted["stats_included"],
        "stats_queries": extracted["stats_queries"],
    }
    if view in ("tables", "all"):
        payload["tables"] = _tables_view(model, top_n)
    if view in ("columns", "all"):
        payload["columns"] = _columns_view(model, top_n)
    if view in ("relationships", "all"):
        payload["relationships"] = _relationships_view(model, top_n)
    if view in ("partitions", "all"):
        payload["partitions"] = extracted["partitions"][:top_n]
    if view in ("segments", "all"):
        payload["segments"] = _segments_view(extracted["segments"], top_n)

    if export_path:
        export = export_metrics(payload, export_path)
        if not export.get("success"):
            return export
        payload["export"] = export

    return payload
