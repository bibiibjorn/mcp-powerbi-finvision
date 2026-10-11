"""DAX Studio / Performance Analyzer parity core.

One implementation of the performance maths, shared by the Power BI Desktop MCP
server (local models) and the Fabric MCP server (online models), so both report
identical numbers for identical events.

Modules
-------
``server_timings``
    DAX Studio ``ServerTimesModel.ProcessResults``: FE/SE split, batch handling,
    internal-scan rules, cache matches, timeline.
``query_plan``
    Physical/logical plan parsing, cardinality inheritance, operator census,
    findings, structural diff between two plans.
``xmsql``
    Storage-engine query text cleanup (id -> name, alias/lineage stripping).
``execution_metrics``
    Event 136 payload: peak memory, FE/SE CPU split, queue wait.
``vpa``
    VertiPaq Analyzer model metrics: sizes, cardinality, encoding, findings.
``perf_analyzer``
    Performance Analyzer semantics including the "Other" wait residual, plus a
    K-slot queue simulator for pages with many visuals.
``daxstudio_export``
    ``ServerTimings.json`` / ``QueryPlan.json`` / ``.daxx`` writers so a capture
    can be opened in DAX Studio itself.
"""

from __future__ import annotations

__version__ = "1.0.0"

from .daxstudio_export import (  # noqa: F401
    build_query_plan_export,
    build_server_timings_export,
    write_daxx,
    write_query_plan,
    write_server_timings,
)
from .execution_metrics import (  # noqa: F401
    parse_execution_metrics,
    summarise_execution_metrics,
)
from .perf_analyzer import (  # noqa: F401
    DEFAULT_QUERY_SLOTS,
    VisualTiming,
    parse_performance_export,
    simulate_page,
    summarise_rows,
)
from .query_plan import (  # noqa: F401
    QueryPlan,
    QueryPlanRow,
    analyse_plan,
    compare_plans,
    parse_plan,
)
from .server_timings import (  # noqa: F401
    EVENT_CLASS_NAMES,
    SEEventRow,
    ServerTimingsResult,
    TraceEvent,
    process_results,
)
from .vpa import (  # noqa: F401
    ColumnMetrics,
    ModelMetrics,
    TableMetrics,
    analyse_model,
    build_model_metrics,
)
from .xmsql import XmSqlCleaner, build_name_maps, clean_xmsql  # noqa: F401

__all__ = [
    "__version__",
    # server timings
    "TraceEvent",
    "SEEventRow",
    "ServerTimingsResult",
    "process_results",
    "EVENT_CLASS_NAMES",
    # query plan
    "QueryPlan",
    "QueryPlanRow",
    "parse_plan",
    "analyse_plan",
    "compare_plans",
    # xmsql
    "XmSqlCleaner",
    "clean_xmsql",
    "build_name_maps",
    # execution metrics
    "parse_execution_metrics",
    "summarise_execution_metrics",
    # vpa
    "ColumnMetrics",
    "TableMetrics",
    "ModelMetrics",
    "build_model_metrics",
    "analyse_model",
    # performance analyzer
    "VisualTiming",
    "parse_performance_export",
    "simulate_page",
    "summarise_rows",
    "DEFAULT_QUERY_SLOTS",
    # export
    "build_server_timings_export",
    "build_query_plan_export",
    "write_server_timings",
    "write_query_plan",
    "write_daxx",
]
