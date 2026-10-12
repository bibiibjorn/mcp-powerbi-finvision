"""
Enhanced VertiPaq storage reporting with segment-level analysis.

Provides detailed storage metrics for Power BI models including:
- Column-level storage info (dictionary size, data size, encoding)
- Segment-level data for detailed analysis
- Table-level storage summaries
- Optimization recommendations based on storage patterns
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Column-name signals that a string column is acting as a surrogate/business key.
_KEY_NAME_RE = re.compile(r"(key|id|code|guid|uuid|number|nr|no)$", re.IGNORECASE)

# Encoding type constants from DMV
ENCODING_HASH = "HASH"
ENCODING_VALUE = "VALUE"
ENCODING_RUN_LENGTH = "RUN_LENGTH"

# Thresholds for recommendations
HIGH_CARDINALITY_THRESHOLD = 1_000_000
POOR_COMPRESSION_RATIO = 0.5
MANY_SEGMENTS_THRESHOLD = 8
LARGE_DICTIONARY_MB = 50
HIGH_CARDINALITY_COLUMN_THRESHOLD = 100_000


@dataclass
class ColumnStorage:
    """Storage metrics for a single column."""

    table_name: str
    column_name: str
    data_type: str
    cardinality: int
    dictionary_size: int  # bytes
    data_size: int  # bytes
    total_size: int  # bytes
    encoding: str  # "HASH", "VALUE", "RUN_LENGTH"
    segments: int
    partitions: int
    bits_per_value: int = 0
    compression_ratio: float = 0.0


def prescriptive_advice(col: "ColumnStorage") -> str:
    """Return a specific, data-type-aware cardinality-reduction action.

    Turns a high-cardinality column into a concrete remediation instead of the
    generic "remove/aggregate/split". Pure — keyed on data_type + column-name
    signal — so it is unit-testable.
    """
    dtype = (col.data_type or "").strip().lower()
    name = col.column_name or ""

    if "datetime" in dtype or dtype == "date" and "time" in name.lower():
        return (
            "Split this DateTime into a Date column (Int64 YYYYMMDD key to a Date "
            "dimension) and a separate Time column. Storing date and time together "
            "explodes cardinality."
        )

    if dtype in ("double", "decimal") or "single" in dtype or "currency" in dtype:
        return (
            "Reduce numeric precision: convert to Fixed Decimal (Currency) or round "
            "to the needed scale. High-precision Double/Decimal values rarely "
            "compress and inflate the dictionary."
        )

    if "string" in dtype or dtype in ("text",):
        if _KEY_NAME_RE.search(name):
            return (
                "Replace this string surrogate key with an Int64 integer key in "
                "Power Query / the source. Integer keys hash far smaller and speed "
                "up relationships."
            )
        return (
            "Move this high-cardinality text out of the fact into a dimension "
            "table (and relate by an integer key), or reduce its distinct values."
        )

    if "binary" in dtype:
        return (
            "Binary/blob columns do not compress; remove it from the model or store "
            "a reference (path/URL) instead."
        )

    return (
        "Reduce distinct values: remove the column if unused, aggregate to a coarser "
        "grain, or split it into a separate table."
    )


@dataclass
class TableStorage:
    """Storage metrics for a single table."""

    table_name: str
    row_count: int
    total_size: int  # bytes
    dictionary_size: int
    data_size: int
    columns: List[ColumnStorage] = field(default_factory=list)
    relationships_size: int = 0


@dataclass
class StorageReport:
    """Complete storage report for the model."""

    tables: List[TableStorage]
    total_model_size: int
    total_rows: int
    recommendations: List[Dict[str, str]]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON response."""
        table_dicts = []
        for t in self.tables:
            col_dicts = []
            for c in t.columns:
                col_dicts.append(
                    {
                        "table": c.table_name,
                        "column": c.column_name,
                        "data_type": c.data_type,
                        "cardinality": c.cardinality,
                        "dictionary_size_bytes": c.dictionary_size,
                        "data_size_bytes": c.data_size,
                        "total_size_bytes": c.total_size,
                        "encoding": c.encoding,
                        "segments": c.segments,
                        "partitions": c.partitions,
                        "bits_per_value": c.bits_per_value,
                        "compression_ratio": round(c.compression_ratio, 3),
                    }
                )
            table_dicts.append(
                {
                    "table_name": t.table_name,
                    "row_count": t.row_count,
                    "total_size_bytes": t.total_size,
                    "total_size_mb": round(t.total_size / (1024 * 1024), 2),
                    "dictionary_size_bytes": t.dictionary_size,
                    "data_size_bytes": t.data_size,
                    "relationships_size_bytes": t.relationships_size,
                    "column_count": len(t.columns),
                    "columns": col_dicts,
                }
            )

        # Sort tables by total_size descending
        table_dicts.sort(key=lambda x: x["total_size_bytes"], reverse=True)

        total_mb = round(self.total_model_size / (1024 * 1024), 2)

        return {
            "success": True,
            "total_model_size_bytes": self.total_model_size,
            "total_model_size_mb": total_mb,
            "total_rows": self.total_rows,
            "table_count": len(self.tables),
            "tables": table_dicts,
            "recommendations": self.recommendations,
            "top_tables_by_size": [
                {
                    "table": t["table_name"],
                    "size_mb": t["total_size_mb"],
                    "row_count": t["row_count"],
                    "columns": t["column_count"],
                }
                for t in table_dicts[:10]
            ],
        }


class VertiPaqStorageReport:
    """Enhanced VertiPaq storage reporting using DMV queries."""

    def __init__(self, query_executor):
        self._qe = query_executor

    def generate_report(self) -> StorageReport:
        """Storage report for ``06_Analysis_Operations`` storage.

        Review E-17/E-19: the DMV readers of this module used columns the
        rowsets do not have (ATTRIBUTE_COUNT / ATTRIBUTE_SIZE, ATTRIBUTE_NAME on
        segments), a dead ``result["data"]`` INFO fallback and an encoding map
        opposite to 04 vertipaq_analyzer. The numbers now come from THE
        extractor (``core.dax.vpa_extractor`` + ``pbi_perf_core.vpa``, DMV-only):
        Col Size = Data + Dictionary + Hierarchies, 1 = HASH / 2 = VALUE, H$
        attribute-hierarchy cardinality.
        """
        from core.dax.vpa_extractor import extract_model_metrics

        model = extract_model_metrics(self._qe, include_stats=False)["model"]
        tables: List[TableStorage] = []
        for t in model.tables:
            ts = TableStorage(
                table_name=t.table_name,
                row_count=int(t.rows_count or 0),
                total_size=int(t.table_size),
                dictionary_size=sum(c.dictionary_size for c in t.columns),
                data_size=sum(c.data_size for c in t.columns),
                columns=[],
                relationships_size=int(t.relationships_size),
            )
            for c in t.columns:
                raw = (t.rows_count or 0) * 8
                ts.columns.append(
                    ColumnStorage(
                        table_name=c.table_name,
                        column_name=c.column_name,
                        data_type=c.data_type,
                        cardinality=int(c.cardinality or 0),
                        dictionary_size=int(c.dictionary_size),
                        data_size=int(c.data_size),
                        total_size=int(c.total_size),
                        encoding=_normalize_encoding(c.encoding),
                        segments=int(c.segment_count),
                        partitions=int(t.partitions_count),
                        bits_per_value=int(c.bits_count),
                        # data bytes vs a naive 8 bytes per row
                        compression_ratio=(c.data_size / raw) if raw and c.data_size else 0.0,
                    )
                )
            ts.columns.sort(key=lambda c: c.total_size, reverse=True)
            tables.append(ts)
        tables.sort(key=lambda t: t.total_size, reverse=True)

        recommendations = self._generate_recommendations(tables)
        if not tables:
            logger.warning("Storage report empty (VertiPaq DMVs unavailable?)")
            recommendations.append(
                {
                    "severity": "high",
                    "category": "diagnostics",
                    "message": "No storage data retrieved — VertiPaq DMVs unavailable?",
                }
            )
        return StorageReport(
            tables=tables,
            total_model_size=int(model.model_size),
            total_rows=sum(t.row_count for t in tables),
            recommendations=recommendations,
        )

    def _generate_recommendations(self, tables: List[TableStorage]) -> List[Dict[str, str]]:
        """Generate optimization recommendations."""
        recommendations: List[Dict[str, str]] = []

        all_columns: List[ColumnStorage] = []
        for t in tables:
            all_columns.extend(t.columns)

        # 1. High-cardinality columns
        high_card = [c for c in all_columns if c.cardinality > HIGH_CARDINALITY_THRESHOLD]
        for col in high_card:
            size_mb = round(col.total_size / (1024 * 1024), 2)
            recommendations.append(
                {
                    "severity": "high",
                    "category": "cardinality",
                    "column": (f"{col.table_name}" f"[{col.column_name}]"),
                    "message": (
                        f"High cardinality "
                        f"({col.cardinality:,} unique "
                        f"values, {size_mb} MB). "
                        f"{prescriptive_advice(col)}"
                    ),
                }
            )

        # 2. Poor compression ratio columns
        poor_compress = [
            c
            for c in all_columns
            if c.compression_ratio > POOR_COMPRESSION_RATIO
            and c.compression_ratio > 0
            and c.total_size > 1024 * 1024  # >1MB
        ]
        for col in poor_compress:
            ratio_pct = round(col.compression_ratio * 100, 1)
            recommendations.append(
                {
                    "severity": "medium",
                    "category": "compression",
                    "column": (f"{col.table_name}" f"[{col.column_name}]"),
                    "message": (
                        f"Poor compression ratio "
                        f"({ratio_pct}%). Column data "
                        f"is not compressing well. "
                        f"Consider data type changes or "
                        f"reducing precision."
                    ),
                }
            )

        # 3. HASH encoding on low-cardinality NUMERIC columns (text columns
        # are always HASH — VALUE is not an option for them).
        hash_low_card = [
            c
            for c in all_columns
            if c.encoding == ENCODING_HASH
            and (c.data_type or "").lower() in ("int64", "double", "decimal", "currency")
            and c.cardinality < 1000
            and c.total_size > 100 * 1024  # >100KB
        ]
        for col in hash_low_card:
            recommendations.append(
                {
                    "severity": "low",
                    "category": "encoding",
                    "column": (f"{col.table_name}" f"[{col.column_name}]"),
                    "message": (
                        f"HASH encoding on low-cardinality "
                        f"column ({col.cardinality} unique "
                        f"values). VALUE encoding might "
                        f"be more efficient."
                    ),
                }
            )

        # 4. Tables with many segments
        for t in tables:
            max_segs = max(
                (c.segments for c in t.columns),
                default=0,
            )
            if max_segs > MANY_SEGMENTS_THRESHOLD:
                recommendations.append(
                    {
                        "severity": "medium",
                        "category": "segments",
                        "table": t.table_name,
                        "message": (
                            f"Table has columns with up to "
                            f"{max_segs} segments. Many "
                            f"segments can increase memory "
                            f"and slow scans. Consider "
                            f"partition management."
                        ),
                    }
                )

        # 5. Large dictionary sizes
        large_dict = [
            c for c in all_columns if c.dictionary_size > LARGE_DICTIONARY_MB * 1024 * 1024
        ]
        for col in large_dict:
            dict_mb = round(col.dictionary_size / (1024 * 1024), 2)
            recommendations.append(
                {
                    "severity": "high",
                    "category": "dictionary",
                    "column": (f"{col.table_name}" f"[{col.column_name}]"),
                    "message": (
                        f"Very large dictionary "
                        f"({dict_mb} MB). This column "
                        f"stores many unique values. "
                        f"Consider removing if unused "
                        f"or reducing granularity."
                    ),
                }
            )

        # 6. Overall model size warning
        total_mb = sum(t.total_size for t in tables) / (1024 * 1024)
        if total_mb > 1000:
            recommendations.append(
                {
                    "severity": "high",
                    "category": "model_size",
                    "message": (
                        f"Total model storage is "
                        f"{round(total_mb, 1)} MB. "
                        f"Consider incremental refresh, "
                        f"aggregation tables, or removing "
                        f"unused columns to reduce size."
                    ),
                }
            )

        # Sort by severity: high > medium > low
        severity_order = {
            "high": 0,
            "medium": 1,
            "low": 2,
        }
        recommendations.sort(key=lambda r: severity_order.get(r.get("severity", "low"), 3))

        return recommendations


def _normalize_encoding(raw: Any) -> str:
    """Normalize an encoding code/name to this module's labels.

    Numeric COLUMN_ENCODING codes decode via the one map (``pbi_perf_core.vpa``,
    re-exported by ``vertipaq_analyzer.encoding_label``): 1 = HASH, 2 = VALUE
    (LIVE-CHECKS.md, review E-17). Names pass through in upper case.
    """
    if raw is None or str(raw).strip() == "":
        return "unknown"
    enc = str(raw).upper().strip()
    if enc.isdigit():
        from core.dax.vertipaq_analyzer import encoding_label

        return {"value": ENCODING_VALUE, "hash": ENCODING_HASH}.get(encoding_label(enc), enc)
    name_map = {
        "HASH": ENCODING_HASH,
        "VALUE": ENCODING_VALUE,
        "RLE": ENCODING_RUN_LENGTH,
        "RUN_LENGTH": ENCODING_RUN_LENGTH,
        "RUNLENGTH": ENCODING_RUN_LENGTH,
    }
    return name_map.get(enc, enc)
