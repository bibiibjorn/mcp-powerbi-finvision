"""Parse the ``ExecutionMetrics`` (event class 136) JSON payload.

This event is the only place the engine reports peak memory, the formula/storage
CPU split and the queue wait (``executionDelayMs``) for a query.  It fires on
Power BI Desktop as well as in the Service - verified 2026-09-02 against a local
Desktop instance, which contradicts the older "Service only" guidance.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["parse_execution_metrics", "summarise_execution_metrics", "FIELD_NOTES"]

#: Documented meaning of each field, surfaced in tool output so the numbers are
#: interpretable without leaving the terminal.
FIELD_NOTES: dict[str, str] = {
    "durationMs": "Total query duration measured by the engine",
    "totalCpuTimeMs": "All CPU across formula and storage engines",
    "queryProcessingCpuTimeMs": "Formula engine CPU (single-threaded work)",
    "vertipaqJobCpuTimeMs": "Storage engine CPU (parallel scans)",
    "executionDelayMs": "Time queued waiting for an engine thread - capacity pressure",
    "approximatePeakMemConsumptionKB": "Peak memory for this query",
    "queryResultRows": "Rows returned to the client",
    "directQueryTotalTimeMs": "Total time spent in DirectQuery sources",
    "directQueryConnectionTimeMs": "DirectQuery connection establishment time",
    "directQueryIterationTimeMs": "Time iterating DirectQuery results",
    "directQueryTotalRows": "Rows read from DirectQuery sources",
    "capacityThrottlingMs": "Time throttled by capacity limits (Fabric/Premium)",
    "mEngineCpuTimeMs": "Power Query (M) engine CPU",
    "mEnginePeakMemoryKB": "Power Query (M) engine peak memory",
    "externalQueryExecutionTimeMs": "Time in external/composite sources",
    "queryDialect": "3 = DAX, 1 = MDX",
    "commandType": "Statement type reported by the engine",
    "errorCount": "Errors raised during execution",
    "intendedUsage": "Engine's classification of the request",
    "tabularConnectionMode": "Connection mode used",
}

#: Both spellings occur across engine versions; normalise to the short one.
_PEAK_MEM_ALIASES = (
    "approximatePeakMemConsumptionKB",
    "approximatePeakMemoryConsumptionKB",
)


def parse_execution_metrics(text: str | None) -> dict[str, Any]:
    """Parse the JSON payload; returns ``{}`` when absent or malformed."""
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}

    for alias in _PEAK_MEM_ALIASES[1:]:
        if alias in data and _PEAK_MEM_ALIASES[0] not in data:
            data[_PEAK_MEM_ALIASES[0]] = data[alias]

    return data


def summarise_execution_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """The handful of fields worth showing by default, with derived splits."""
    if not metrics:
        return {}

    total_cpu = metrics.get("totalCpuTimeMs")
    fe_cpu = metrics.get("queryProcessingCpuTimeMs")
    se_cpu = metrics.get("vertipaqJobCpuTimeMs")
    duration = metrics.get("durationMs")

    summary: dict[str, Any] = {
        "duration_ms": duration,
        "total_cpu_ms": total_cpu,
        "fe_cpu_ms": fe_cpu,
        "se_cpu_ms": se_cpu,
        "execution_delay_ms": metrics.get("executionDelayMs"),
        "peak_memory_kb": metrics.get(_PEAK_MEM_ALIASES[0]),
        "result_rows": metrics.get("queryResultRows"),
        "query_dialect": metrics.get("queryDialect"),
    }

    if isinstance(total_cpu, (int, float)) and total_cpu:
        if isinstance(fe_cpu, (int, float)):
            summary["fe_cpu_pct"] = round(100.0 * fe_cpu / total_cpu, 1)
        if isinstance(se_cpu, (int, float)):
            summary["se_cpu_pct"] = round(100.0 * se_cpu / total_cpu, 1)
    if isinstance(duration, (int, float)) and duration and isinstance(total_cpu, (int, float)):
        # >1 means the query used more CPU than wall clock, i.e. it parallelised.
        summary["cpu_parallelism"] = round(total_cpu / duration, 2)

    for optional in (
        "capacityThrottlingMs",
        "directQueryTotalTimeMs",
        "directQueryTotalRows",
        "mEngineCpuTimeMs",
        "externalQueryExecutionTimeMs",
        "errorCount",
    ):
        if metrics.get(optional):
            summary[optional] = metrics[optional]

    return {k: v for k, v in summary.items() if v is not None}
