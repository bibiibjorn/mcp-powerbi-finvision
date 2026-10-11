"""VertiPaq Analyzer parity - model size, cardinality and encoding metrics.

Pure functions over rows already fetched from the DMVs, so the same maths serves
a local Desktop model and an online XMLA endpoint.  The caller supplies:

* ``DISCOVER_STORAGE_TABLES``
* ``DISCOVER_STORAGE_TABLE_COLUMNS`` (``COLUMN_TYPE = 'BASIC_DATA'``)
* ``DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS``
* ``TMSCHEMA_RELATIONSHIPS`` / ``TMSCHEMA_PARTITIONS`` (optional)

Size formulas follow Dax.Model.Extractor:

* ``Column.TotalSize = DataSize + DictionarySize + HierarchiesSize``
* ``Table.TableSize  = ColumnsTotalSize + UserHierarchiesSize + RelationshipsSize``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

__all__ = [
    "ColumnMetrics",
    "TableMetrics",
    "ModelMetrics",
    "build_model_metrics",
    "analyse_model",
    "ENCODING_NAMES",
]

ENCODING_NAMES = {1: "HASH", 2: "VALUE", 0: "UNKNOWN"}

#: Segment-table name prefixes: what a segment row is actually measuring.
_HIERARCHY_PREFIX = "H$"
_USER_HIERARCHY_PREFIX = "U$"
_RELATIONSHIP_PREFIX = "R$"

#: Real attribute-hierarchy structure id: ``H$<Table> (id)$<Column> (id)``
#: (review E-18). The segment row's own column is POS_TO_ID / ID_TO_POS.
_H_ID_FULL = re.compile(r"^(?P<table>.+?) \((?P<tid>\d+)\)\$(?P<col>.+?) \((?P<cid>\d+)\)\s*$")
_H_STRUCTURES = {"POS_TO_ID", "ID_TO_POS"}


def _get(row: Mapping, *names, default=None):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
        upper = name.upper()
        if upper in row and row[upper] is not None:
            return row[upper]
    return default


_NUMERIC_TYPES = {
    "int64",
    "double",
    "decimal",
    "currency",
    "dbtype_i8",
    "dbtype_i4",
    "dbtype_i2",
    "dbtype_r8",
    "dbtype_r4",
    "dbtype_cy",
    "dbtype_decimal",
    "dbtype_numeric",
    "dbtype_ui8",
    "dbtype_ui4",
}
_DICTIONARY_BASELINE_BYTES = 2 * 1024 * 1024


def _is_numeric_type(data_type: str) -> bool:
    """True for engine numeric types, both TOM names and DMV DBTYPE_* codes."""
    return str(data_type or "").strip().lower() in _NUMERIC_TYPES


def _num(value, default=0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class ColumnMetrics:
    table_name: str
    column_name: str
    data_type: str = ""
    encoding: str = "UNKNOWN"
    # None = unknown (no DAX stats and no attribute hierarchy to read it from);
    # 0 was indistinguishable from an empty column (live check 2026-09-23).
    cardinality: int | None = None
    data_size: int = 0
    dictionary_size: int = 0
    hierarchies_size: int = 0
    segment_count: int = 0
    bits_count: int = 0
    compression_type: str = ""
    is_resident: bool | None = None
    temperature: float | None = None

    @property
    def total_size(self) -> int:
        return self.data_size + self.dictionary_size + self.hierarchies_size

    def to_dict(self, model_size: int = 0, table_size: int = 0) -> dict[str, Any]:
        out = {
            "table": self.table_name,
            "column": self.column_name,
            "data_type": self.data_type,
            "encoding": self.encoding,
            "cardinality": self.cardinality,
            "data_size": self.data_size,
            "dictionary_size": self.dictionary_size,
            "hierarchies_size": self.hierarchies_size,
            "total_size": self.total_size,
            "segments": self.segment_count,
            "bits": self.bits_count,
        }
        if model_size:
            out["pct_db"] = round(100.0 * self.total_size / model_size, 2)
        if table_size:
            out["pct_table"] = round(100.0 * self.total_size / table_size, 2)
        if self.is_resident is not None:
            out["is_resident"] = self.is_resident
        if self.temperature is not None:
            out["temperature"] = self.temperature
        return out


@dataclass
class TableMetrics:
    table_name: str
    rows_count: int = 0
    columns: list[ColumnMetrics] = field(default_factory=list)
    user_hierarchies_size: int = 0
    relationships_size: int = 0
    partitions_count: int = 0
    segments_count: int = 0
    ri_violation_count: int = 0

    @property
    def columns_total_size(self) -> int:
        return sum(c.total_size for c in self.columns)

    @property
    def table_size(self) -> int:
        return self.columns_total_size + self.user_hierarchies_size + self.relationships_size

    def to_dict(self, model_size: int = 0) -> dict[str, Any]:
        out = {
            "table": self.table_name,
            "rows": self.rows_count,
            "columns": len(self.columns),
            "columns_size": self.columns_total_size,
            "user_hierarchies_size": self.user_hierarchies_size,
            "relationships_size": self.relationships_size,
            "table_size": self.table_size,
            "partitions": self.partitions_count,
            "segments": self.segments_count,
        }
        if self.ri_violation_count:
            out["ri_violations"] = self.ri_violation_count
        if model_size:
            out["pct_db"] = round(100.0 * self.table_size / model_size, 2)
        return out


@dataclass
class RelationshipMetrics:
    name: str = ""
    from_table: str = ""
    from_column: str = ""
    to_table: str = ""
    to_column: str = ""
    is_active: bool = True
    cross_filter: str = ""
    from_cardinality: int = 0
    to_cardinality: int = 0
    size: int = 0
    missing_keys: int = 0
    invalid_rows: int = 0

    @property
    def one_to_many_ratio(self) -> float:
        if not self.from_cardinality:
            return 0.0
        return round(self.to_cardinality / self.from_cardinality, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "from": f"{self.from_table}[{self.from_column}]",
            "to": f"{self.to_table}[{self.to_column}]",
            "active": self.is_active,
            "cross_filter": self.cross_filter,
            "from_cardinality": self.from_cardinality,
            "to_cardinality": self.to_cardinality,
            "one_to_many_ratio": self.one_to_many_ratio,
            "size": self.size,
            "missing_keys": self.missing_keys,
            "invalid_rows": self.invalid_rows,
        }


@dataclass
class ModelMetrics:
    tables: list[TableMetrics] = field(default_factory=list)
    relationships: list[RelationshipMetrics] = field(default_factory=list)

    @property
    def model_size(self) -> int:
        return sum(t.table_size for t in self.tables)

    @property
    def total_rows(self) -> int:
        return sum(t.rows_count for t in self.tables)

    def all_columns(self) -> list[ColumnMetrics]:
        return [c for t in self.tables for c in t.columns]

    def summary(self) -> dict[str, Any]:
        size = self.model_size
        columns = self.all_columns()
        return {
            "model_size_bytes": size,
            "model_size_mb": round(size / 1024 / 1024, 2),
            "tables": len(self.tables),
            "columns": len(columns),
            "relationships": len(self.relationships),
            "total_rows": self.total_rows,
            "largest_tables": [
                t.to_dict(size) for t in sorted(self.tables, key=lambda t: -t.table_size)[:10]
            ],
            "largest_columns": [
                c.to_dict(
                    size,
                    next((t.table_size for t in self.tables if t.table_name == c.table_name), 0),
                )
                for c in sorted(columns, key=lambda c: -c.total_size)[:15]
            ],
        }


def build_model_metrics(
    storage_tables: Iterable[Mapping] | None = None,
    storage_columns: Iterable[Mapping] | None = None,
    segments: Iterable[Mapping] | None = None,
    relationships: Iterable[Mapping] | None = None,
    partitions: Iterable[Mapping] | None = None,
    dax_stats: Mapping[str, Any] | None = None,
) -> ModelMetrics:
    """Assemble the metrics from raw DMV rows."""
    model = ModelMetrics()
    tables: dict[str, TableMetrics] = {}

    # --- tables -----------------------------------------------------------
    for row in storage_tables or []:
        name = str(_get(row, "TABLE_ID", "DIMENSION_NAME", "Name", default="") or "")
        if not name or name.startswith(("H$", "U$", "R$")):
            continue
        table = tables.setdefault(name, TableMetrics(table_name=name))
        table.rows_count = _num(_get(row, "ROWS_COUNT", "RowsCount"))
        table.ri_violation_count = _num(_get(row, "RIVIOLATION_COUNT", "RiViolationCount"))

    # --- columns ----------------------------------------------------------
    columns: dict[tuple[str, str], ColumnMetrics] = {}
    for row in storage_columns or []:
        col_type = str(_get(row, "COLUMN_TYPE", default="BASIC_DATA") or "")
        if col_type and col_type != "BASIC_DATA":
            continue
        table_name = str(_get(row, "TABLE_ID", "DIMENSION_NAME", default="") or "")
        column_name = str(_get(row, "ATTRIBUTE_NAME", "COLUMN_ID", default="") or "")
        if not table_name or not column_name:
            continue
        if table_name.startswith(("H$", "U$", "R$")):
            continue
        table = tables.setdefault(table_name, TableMetrics(table_name=table_name))
        column = ColumnMetrics(
            table_name=table_name,
            column_name=column_name,
            data_type=str(_get(row, "DATATYPE", "DataType", default="") or ""),
            encoding=ENCODING_NAMES.get(
                _num(_get(row, "COLUMN_ENCODING", "ColumnEncoding")), "UNKNOWN"
            ),
            dictionary_size=_num(_get(row, "DICTIONARY_SIZE", "DictionarySize")),
        )
        columns[(table_name, column_name)] = column
        table.columns.append(column)

    # --- segments ---------------------------------------------------------
    for row in segments or []:
        table_name = str(_get(row, "TABLE_ID", "DIMENSION_NAME", default="") or "")
        column_name = str(_get(row, "COLUMN_ID", "ATTRIBUTE_NAME", default="") or "")
        used_size = _num(_get(row, "USED_SIZE", "UsedSize"))
        if not table_name:
            continue

        # Segment rows for hierarchies/relationships are prefixed tables.
        if table_name.startswith(_HIERARCHY_PREFIX):
            base = table_name[len(_HIERARCHY_PREFIX) :]
            full = _H_ID_FULL.match(base)
            if full:
                # Raw DMV rows: TABLE_ID = "H$Sales (12)$Amount (34)".
                key = (full.group("table"), full.group("col"))
            elif column_name.upper() in _H_STRUCTURES:
                continue  # cannot attribute a structure row without the full id
            else:
                key = (base, column_name)
            if key in columns:
                columns[key].hierarchies_size += used_size
            continue
        if table_name.startswith(_USER_HIERARCHY_PREFIX):
            base = table_name[len(_USER_HIERARCHY_PREFIX) :]
            if base in tables:
                tables[base].user_hierarchies_size += used_size
            continue
        if table_name.startswith(_RELATIONSHIP_PREFIX):
            base = table_name[len(_RELATIONSHIP_PREFIX) :]
            if base in tables:
                tables[base].relationships_size += used_size
            continue

        key = (table_name, column_name)
        column = columns.get(key)
        if column is None:
            continue
        column.data_size += used_size
        column.segment_count += 1
        column.bits_count = max(column.bits_count, _num(_get(row, "BITS_COUNT")))
        compression = _get(row, "COMPRESSION_TYPE")
        if compression:
            column.compression_type = str(compression)
        resident = _get(row, "ISRESIDENT", "IsResident")
        if resident is not None:
            column.is_resident = bool(resident)
        temperature = _get(row, "TEMPERATURE", "Temperature")
        if temperature is not None:
            try:
                column.temperature = float(temperature)
            except (TypeError, ValueError):
                pass
        if table_name in tables:
            tables[table_name].segments_count += 1

    # --- partitions -------------------------------------------------------
    for row in partitions or []:
        table_name = str(_get(row, "TableName", "TABLE_ID", default="") or "")
        if table_name in tables:
            tables[table_name].partitions_count += 1

    # --- cardinality from DAX stats --------------------------------------
    stats = dax_stats or {}
    for (table_name, column_name), column in columns.items():
        value = stats.get(f"{table_name}[{column_name}]")
        if value is not None:
            column.cardinality = _num(value)
    for table_name, table in tables.items():
        value = stats.get(table_name)
        if value is not None:
            table.rows_count = _num(value, table.rows_count)

    # --- relationships ----------------------------------------------------
    for row in relationships or []:
        rel = RelationshipMetrics(
            name=str(_get(row, "Name", "ID", default="") or ""),
            from_table=str(_get(row, "FromTable", "FromTableName", default="") or ""),
            from_column=str(_get(row, "FromColumn", "FromColumnName", default="") or ""),
            to_table=str(_get(row, "ToTable", "ToTableName", default="") or ""),
            to_column=str(_get(row, "ToColumn", "ToColumnName", default="") or ""),
            is_active=bool(_get(row, "IsActive", default=True)),
            cross_filter=str(_get(row, "CrossFilteringBehavior", default="") or ""),
            size=_num(_get(row, "UsedSize", "USED_SIZE")),
            missing_keys=_num(_get(row, "MissingKeys")),
            invalid_rows=_num(_get(row, "InvalidRows")),
        )
        from_col = columns.get((rel.from_table, rel.from_column))
        to_col = columns.get((rel.to_table, rel.to_column))
        if from_col:
            rel.from_cardinality = from_col.cardinality or 0
        if to_col:
            rel.to_cardinality = to_col.cardinality or 0
        model.relationships.append(rel)

    model.tables = list(tables.values())
    return model


def analyse_model(model: ModelMetrics, top_n: int = 15) -> dict[str, Any]:
    """Findings a modeller can act on, using VertiPaq Analyzer thresholds."""
    findings: list[dict[str, Any]] = []
    size = model.model_size
    columns = model.all_columns()

    for column in sorted(columns, key=lambda c: -c.total_size)[:top_n]:
        if size and column.total_size / size > 0.10:
            findings.append(
                {
                    "severity": "high",
                    "code": "DOMINANT_COLUMN",
                    "object": f"{column.table_name}[{column.column_name}]",
                    "detail": (
                        f"{round(100.0 * column.total_size / size, 1)}% of the model "
                        f"({round(column.total_size / 1024 / 1024, 1)} MB). Reduce "
                        "cardinality, split, or remove it."
                    ),
                }
            )

    for column in columns:
        if column.encoding == "HASH" and _is_numeric_type(column.data_type):
            if column.dictionary_size > 1024 * 1024:
                findings.append(
                    {
                        "severity": "medium",
                        "code": "HASH_ENCODED_NUMERIC",
                        "object": f"{column.table_name}[{column.column_name}]",
                        "detail": (
                            "Numeric column is HASH encoded with a "
                            f"{round(column.dictionary_size / 1024 / 1024, 1)} MB "
                            "dictionary - VALUE encoding would drop it."
                        ),
                    }
                )
        if column.dictionary_size and column.data_size and not _is_numeric_type(column.data_type):
            # Every text column carries a ~1 MB baseline dictionary even with a handful
            # of values (live: 1,065,xxx bytes on 4-row tables) - only flag above it.
            if (
                column.dictionary_size > 4 * column.data_size
                and column.dictionary_size > _DICTIONARY_BASELINE_BYTES
            ):
                findings.append(
                    {
                        "severity": "medium",
                        "code": "DICTIONARY_HEAVY",
                        "object": f"{column.table_name}[{column.column_name}]",
                        "detail": (
                            "Dictionary is more than 4x the data size - high "
                            "cardinality text is dominating this column."
                        ),
                    }
                )

    for table in model.tables:
        if table.ri_violation_count:
            findings.append(
                {
                    "severity": "high",
                    "code": "RI_VIOLATION",
                    "object": table.table_name,
                    "detail": (
                        f"{table.ri_violation_count} referential integrity violations - "
                        "a blank row was added to the related dimension."
                    ),
                }
            )

    for rel in model.relationships:
        ratio = rel.one_to_many_ratio
        if ratio and ratio < 0.1 and rel.to_cardinality:
            findings.append(
                {
                    "severity": "low",
                    "code": "LOW_ONE_TO_MANY_RATIO",
                    "object": rel.name or f"{rel.from_table}->{rel.to_table}",
                    "detail": (
                        f"One-to-many ratio {ratio} - the dimension is far larger than "
                        "the values actually used in the fact."
                    ),
                }
            )
        if rel.missing_keys:
            findings.append(
                {
                    "severity": "high",
                    "code": "MISSING_KEYS",
                    "object": rel.name or f"{rel.from_table}->{rel.to_table}",
                    "detail": f"{rel.missing_keys} keys in the fact have no dimension row.",
                }
            )

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    return {"summary": model.summary(), "findings": findings}
