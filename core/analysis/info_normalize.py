"""INFO.* row normalisation (2026-09-22 review I-15 / I-16).

``INFO.COLUMNS()`` / ``INFO.RELATIONSHIPS()`` / ``INFO.ROLES()`` return TOM
enums as integers (``[CrossFilteringBehavior] = 2``) and have no ``Name`` /
``Cardinality`` / ``ColumnCount`` columns. Every consumer used to compare with
strings (``== "BothDirections"``) or read keys that do not exist, so
bidirectional relationships, calculated columns and RLS permissions were never
reported. These helpers map the enums once, in one place.

Enum values are the TOM ones (Microsoft.AnalysisServices.Tabular).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

DATA_TYPE = {
    1: "Automatic",
    2: "String",
    6: "Int64",
    8: "Double",
    9: "DateTime",
    10: "Decimal",
    11: "Boolean",
    17: "Binary",
    19: "Unknown",
    20: "Variant",
}
COLUMN_TYPE = {1: "Data", 2: "Calculated", 3: "RowNumber", 4: "CalculatedTableColumn"}
CROSS_FILTER = {1: "OneDirection", 2: "BothDirections", 3: "Automatic"}
SECURITY_FILTER = {1: "OneDirection", 2: "BothDirections", 3: "None"}
CARDINALITY = {0: "None", 1: "One", 2: "Many"}
MODEL_PERMISSION = {1: "None", 2: "Read", 3: "ReadRefresh", 4: "Refresh", 5: "Administrator"}
METADATA_PERMISSION = {1: "Default", 2: "None", 3: "Read"}


def pick(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    """First non-empty value among *keys* (plain or ``[bracketed]``)."""
    for key in keys:
        for k in (key, f"[{key}]"):
            if k in row and row[k] not in (None, ""):
                return row[k]
    return default


def enum_name(
    mapping: Mapping[int, str], value: Any, default: Optional[str] = None
) -> Optional[str]:
    """Map a TOM enum (int, numeric string or already a name) to its name.

    Names are normalised to the canonical casing (``"bothDirections"`` ->
    ``"BothDirections"``); unknown values pass through as strings.
    """
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return mapping.get(int(value), str(value))
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return mapping.get(int(text), text)
    lowered = {v.lower(): v for v in mapping.values()}
    return lowered.get(text.lower(), text)


def column_name(row: Mapping[str, Any]) -> str:
    return str(pick(row, "Name", "ExplicitName", "InferredName", "ColumnName", default="") or "")


def column_data_type(row: Mapping[str, Any]) -> str:
    raw = pick(row, "ExplicitDataType", "DataType", "InferredDataType")
    return enum_name(DATA_TYPE, raw, "Unknown") or "Unknown"


def column_kind(row: Mapping[str, Any]) -> str:
    return enum_name(COLUMN_TYPE, pick(row, "Type", "ColumnType"), "Data") or "Data"


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("true", "1", "yes")


def normalize_relationship(row: Mapping[str, Any]) -> Dict[str, Any]:
    """INFO.RELATIONSHIPS row -> ``{fromTable, fromColumn, toTable, toColumn,
    fromCardinality, toCardinality, crossFilteringBehavior, isActive, ...}``."""
    return {
        "name": str(pick(row, "Name", default="") or ""),
        "fromTable": str(pick(row, "FromTable", "FromTableName", default="") or ""),
        "fromColumn": str(pick(row, "FromColumn", "FromColumnName", default="") or ""),
        "toTable": str(pick(row, "ToTable", "ToTableName", default="") or ""),
        "toColumn": str(pick(row, "ToColumn", "ToColumnName", default="") or ""),
        "fromCardinality": enum_name(CARDINALITY, pick(row, "FromCardinality"), "Many"),
        "toCardinality": enum_name(CARDINALITY, pick(row, "ToCardinality"), "One"),
        "crossFilteringBehavior": enum_name(
            CROSS_FILTER, pick(row, "CrossFilteringBehavior"), "OneDirection"
        ),
        "securityFilteringBehavior": enum_name(
            SECURITY_FILTER, pick(row, "SecurityFilteringBehavior"), "OneDirection"
        ),
        "isActive": to_bool(pick(row, "IsActive", default=True)),
    }


def counts_by_table(
    rows: Iterable[Mapping[str, Any]], *, skip_row_number: bool = False
) -> Dict[str, int]:
    """Row count per ``Table`` (execute_info_query maps TableID -> Table)."""
    out: Dict[str, int] = {}
    for r in rows:
        if skip_row_number and column_kind(r) == "RowNumber":
            continue
        t = str(pick(r, "Table", "TableName", "TableID", default="") or "")
        out[t] = out.get(t, 0) + 1
    return out


def relationships_from_tmsl(model: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """TMSL relationships with TOM defaults filled in (fromCardinality=Many …)."""
    out: List[Dict[str, Any]] = []
    for r in model.get("relationships") or []:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "name": str(r.get("name") or ""),
                "fromTable": str(r.get("fromTable") or ""),
                "fromColumn": str(r.get("fromColumn") or ""),
                "toTable": str(r.get("toTable") or ""),
                "toColumn": str(r.get("toColumn") or ""),
                "fromCardinality": enum_name(CARDINALITY, r.get("fromCardinality"), "Many"),
                "toCardinality": enum_name(CARDINALITY, r.get("toCardinality"), "One"),
                "crossFilteringBehavior": enum_name(
                    CROSS_FILTER, r.get("crossFilteringBehavior"), "OneDirection"
                ),
                "securityFilteringBehavior": enum_name(
                    SECURITY_FILTER, r.get("securityFilteringBehavior"), "OneDirection"
                ),
                "isActive": r.get("isActive", True) is not False,
            }
        )
    return out


def _is_field_parameter(table: Mapping[str, Any]) -> bool:
    for c in table.get("columns") or []:
        if not isinstance(c, dict):
            continue
        for p in c.get("extendedProperties") or []:
            if isinstance(p, dict) and p.get("name") == "ParameterMetadata":
                return True
    for p in table.get("partitions") or []:
        src = p.get("source") if isinstance(p, dict) else None
        expr = src.get("expression") if isinstance(src, dict) else None
        text = "\n".join(expr) if isinstance(expr, list) else str(expr or "")
        if "NAMEOF(" in text.upper().replace(" ", ""):
            return True
    return False


def _is_what_if_parameter(table: Mapping[str, Any]) -> bool:
    for p in table.get("partitions") or []:
        src = p.get("source") if isinstance(p, dict) else None
        if not isinstance(src, dict) or str(src.get("type") or "").lower() != "calculated":
            continue
        expr = src.get("expression")
        text = "\n".join(expr) if isinstance(expr, list) else str(expr or "")
        if "GENERATESERIES(" in text.upper().replace(" ", ""):
            return True
    return False


def dax_referenced_tables(model: Mapping[str, Any]) -> List[str]:
    """Names of the tables any DAX expression in a TMSL model reads: measures, calculated
    columns, calculated-table sources, calculation items, format-string / detail-rows
    expressions, role filters and UDF bodies. A table that only DAX reads (a slicer, parameter
    or helper table used through SELECTEDVALUE / TREATAS) is disconnected by design.

    A reference counts only when the expression lives OUTSIDE the table it reads: a table's own
    measures, calculated columns, calculated partition or role filter reading that table say
    nothing about whether anything else connects to it, so they do not silence the
    disconnected-table finding. UDF bodies belong to no table and always count."""
    from core.dax.dax_reference_parser import scan_references

    tables = [t for t in model.get("tables") or [] if isinstance(t, dict)]
    known = {str(t.get("name") or "").lower(): str(t.get("name") or "") for t in tables}
    known_keys = set(k for k in known if k)
    exprs: List[Tuple[str, str]] = []  # (host table name lower-cased, DAX text)

    def _text(v: Any) -> str:
        return "\n".join(v) if isinstance(v, list) else (v if isinstance(v, str) else "")

    for t in tables:
        host = str(t.get("name") or "").lower()
        for m in t.get("measures") or []:
            if isinstance(m, dict):
                exprs.append((host, _text(m.get("expression"))))
                for sub in ("formatStringDefinition", "detailRowsDefinition"):
                    if isinstance(m.get(sub), dict):
                        exprs.append((host, _text(m[sub].get("expression"))))
        for c in t.get("columns") or []:
            if isinstance(c, dict) and c.get("expression"):
                exprs.append((host, _text(c.get("expression"))))
        for p_ in t.get("partitions") or []:
            src = p_.get("source") if isinstance(p_, dict) else None
            if isinstance(src, dict) and str(src.get("type") or "").lower() == "calculated":
                exprs.append((host, _text(src.get("expression"))))
        cg = t.get("calculationGroup")
        if isinstance(cg, dict):
            for item in cg.get("calculationItems") or []:
                if isinstance(item, dict):
                    exprs.append((host, _text(item.get("expression"))))
    for role in model.get("roles") or []:
        for tp in (role.get("tablePermissions") or []) if isinstance(role, dict) else []:
            if isinstance(tp, dict):
                exprs.append((str(tp.get("name") or "").lower(), _text(tp.get("filterExpression"))))
    for fn in model.get("functions") or []:
        if isinstance(fn, dict):
            exprs.append(("", _text(fn.get("expression"))))

    found = set()
    for host, e in exprs:
        if not e.strip():
            continue
        try:
            refs = scan_references(e, known_tables=known_keys)
        except Exception:  # noqa: BLE001 - a malformed expression only loses its references
            continue
        for r in refs:
            name = str(getattr(r, "table", "") or "").lower()
            if name in known and name != host:
                found.add(known[name])
    return sorted(found)


def tables_from_tmsl(model: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Topology-validator table dicts (with the by-design disconnected flags)."""
    out: List[Dict[str, Any]] = []
    for t in model.get("tables") or []:
        if not isinstance(t, dict):
            continue
        parts = t.get("partitions") or []
        src = (parts[0] or {}).get("source") if parts and isinstance(parts[0], dict) else {}
        is_calc = isinstance(src, dict) and str(src.get("type") or "").lower() == "calculated"
        out.append(
            {
                "name": str(t.get("name") or ""),
                "dataCategory": t.get("dataCategory"),
                "isHidden": bool(t.get("isHidden")),
                "isCalculationGroup": isinstance(t.get("calculationGroup"), dict),
                "isFieldParameter": _is_field_parameter(t),
                "isParameter": _is_what_if_parameter(t),
                "isCalculatedTable": is_calc,
                "isPrivate": bool(t.get("isPrivate")),
                "measureCount": len(t.get("measures") or []),
                "columnCount": sum(
                    1
                    for c in t.get("columns") or []
                    if isinstance(c, dict) and str(c.get("type") or "").lower() != "rownumber"
                ),
            }
        )
    return out
