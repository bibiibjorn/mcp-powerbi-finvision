"""VertiPaq size reporter — top columns by RAM cost + verdict (04 vertipaq_report).

Review E-17/E-19: this module used to read its own DMV set with fields that do
not exist (COLUMN_CARDINALITY / COLUMN_HIERARCHIES_SIZE on the columns rowset,
sizes from DISCOVER_STORAGE_TABLE_DICTIONARIES), so cardinality and dictionary
sizes were 0, and it decoded COLUMN_ENCODING opposite to ``04 vertipaq_analyzer``.
It is now a thin PROJECTION of the one extractor (``core.dax.vpa_extractor`` +
``pbi_perf_core.vpa``): same sizes (Col Size = Data + Dictionary + Hierarchies),
same encoding (1 = HASH, 2 = VALUE), DMV-only (no DAX statistics), plus the
hot/warn/info verdict per column.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List

from core.dax.vertipaq_analyzer import encoding_label  # noqa: F401 - legacy re-export

logger = logging.getLogger(__name__)

# Verdict thresholds — kept conservative so noise doesn't dominate. Tweak
# only if real-world models surface false positives/negatives.
_HOT_TOTAL_MB = 50.0
_HOT_TOTAL_MB_WITH_BAD_RLE = 10.0
_HOT_RLE_THRESHOLD = 0.30
_WARN_TOTAL_MB = 10.0
_WARN_HIGH_CARDINALITY = 1_000_000


def _bytes_to_mb(b: Any) -> float:
    try:
        return round(float(b) / 1024.0 / 1024.0, 3)
    except (TypeError, ValueError):
        return 0.0


def _verdict_for(total_mb: float, cardinality: int, rle_efficiency: float) -> Dict[str, str]:
    if total_mb > _HOT_TOTAL_MB:
        return {
            "verdict": "hot",
            "verdict_reason": (f"Total {total_mb}MB exceeds {_HOT_TOTAL_MB}MB threshold"),
        }
    if total_mb > _HOT_TOTAL_MB_WITH_BAD_RLE and rle_efficiency < _HOT_RLE_THRESHOLD:
        return {
            "verdict": "hot",
            "verdict_reason": (
                f"Total {total_mb}MB AND RLE efficiency {rle_efficiency:.2f} "
                f"<{_HOT_RLE_THRESHOLD} (poor compression)"
            ),
        }
    if total_mb > _WARN_TOTAL_MB:
        return {
            "verdict": "warn",
            "verdict_reason": f"Total {total_mb}MB exceeds {_WARN_TOTAL_MB}MB threshold",
        }
    if cardinality > _WARN_HIGH_CARDINALITY:
        return {
            "verdict": "warn",
            "verdict_reason": (
                f"Cardinality {cardinality:,} exceeds {_WARN_HIGH_CARDINALITY:,} "
                f"(may cause dictionary bloat)"
            ),
        }
    return {"verdict": "info", "verdict_reason": ""}


def build_report(
    qe,
    top_n: int = 50,
    include_table_rollup: bool = True,
    include_verdict: bool = True,
) -> Dict[str, Any]:
    """Build the VertiPaq size report from the shared extractor (DMV-only)."""
    from core.dax.vpa_extractor import extract_model_metrics

    model = extract_model_metrics(qe, include_stats=False)["model"]
    all_columns = model.all_columns()
    if not all_columns:
        return {
            "success": False,
            "error": (
                "DISCOVER_STORAGE_TABLE_COLUMNS returned no data — VertiPaq DMVs "
                "may be unavailable on this connection"
            ),
            "error_type": "dmv_unavailable",
        }

    columns: List[Dict[str, Any]] = []
    for c in all_columns:
        col_report: Dict[str, Any] = {
            "table": c.table_name,
            "column": c.column_name,
            # H$ attribute-hierarchy row count (DMV fallback); exact DISTINCTCOUNT
            # needs 04 vertipaq_analyzer include_stats=true.
            "cardinality": int(c.cardinality or 0),
            "data_size_mb": _bytes_to_mb(c.data_size),
            "dict_size_mb": _bytes_to_mb(c.dictionary_size),
            "hier_size_mb": _bytes_to_mb(c.hierarchies_size),
            "total_size_mb": _bytes_to_mb(c.total_size),
            "encoding": c.encoding or "UNKNOWN",
            "segments": c.segment_count,
            # No DMV exposes a real RLE ratio; the old value was a constant 1.0.
            "rle_efficiency": None,
        }
        if include_verdict:
            col_report.update(
                _verdict_for(col_report["total_size_mb"], col_report["cardinality"], 1.0)
            )
        columns.append(col_report)

    columns.sort(key=lambda c: c["total_size_mb"], reverse=True)
    truncated = len(columns) > top_n
    top_columns = columns[:top_n]

    stats = {"hot": 0, "warn": 0, "info": 0}
    if include_verdict:
        for c in columns:
            v = c.get("verdict", "info")
            stats[v] = stats.get(v, 0) + 1

    out: Dict[str, Any] = {
        "success": True,
        "model_total_mb": _bytes_to_mb(model.model_size),
        "columns": top_columns,
        "column_count_total": len(columns),
        "column_count_returned": len(top_columns),
        "truncated": truncated,
        "source": "vpa_extractor (DMV only)",
    }
    if include_verdict:
        out["stats"] = stats

    if include_table_rollup:
        rollup_map: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"total_mb": 0.0, "column_count": 0}
        )
        for c in columns:
            t = c["table"] or "(unknown)"
            rollup_map[t]["total_mb"] += c["total_size_mb"]
            rollup_map[t]["column_count"] += 1
        rollup = [
            {
                "table": t,
                "total_mb": round(v["total_mb"], 3),
                "column_count": int(v["column_count"]),
            }
            for t, v in rollup_map.items()
        ]
        rollup.sort(key=lambda x: x["total_mb"], reverse=True)
        out["table_rollup"] = rollup

    return out


def vertipaq_report(
    top_n: int = 50,
    include_table_rollup: bool = True,
    include_verdict: bool = True,
) -> Dict[str, Any]:
    """Handler-level entry. Resolves the live executor and calls build_report."""
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
            "error": "query_executor not available",
            "error_type": "not_connected",
        }
    return build_report(
        qe,
        top_n=int(top_n),
        include_table_rollup=bool(include_table_rollup),
        include_verdict=bool(include_verdict),
    )
