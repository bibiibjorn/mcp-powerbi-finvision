"""Write DAX Studio-compatible export files.

The point of matching the on-disk format is that a capture taken through the MCP
server can be opened in DAX Studio itself (File -> Open, or a ``.daxx`` bundle),
so the numbers can be checked against the tool everyone already trusts.

Field-for-field parity notes, taken from a real export:

* ``ServerTimings.json`` is ``FileFormatVersion`` 6, ``QueryPlan.json`` is 3.
* Default-valued row fields are **omitted**, not written as ``false``/``null``.
* Cache-match rows carry no ``Duration``/``CpuTime``/``EndTime``.
* Logical plan rows have no ``Records`` key at all.
"""

from __future__ import annotations

import json
import os
import zipfile
from typing import Any

from .query_plan import QueryPlan
from .server_timings import ServerTimingsResult

__all__ = [
    "build_server_timings_export",
    "build_query_plan_export",
    "write_server_timings",
    "write_query_plan",
    "write_daxx",
    "SERVER_TIMINGS_FORMAT_VERSION",
    "QUERY_PLAN_FORMAT_VERSION",
]

SERVER_TIMINGS_FORMAT_VERSION = 6
QUERY_PLAN_FORMAT_VERSION = 3


def _iso(value) -> str | None:
    return None if value is None else value.isoformat()


def build_server_timings_export(result: ServerTimingsResult) -> dict[str, Any]:
    """Build the ``ServerTimings.json`` payload."""
    payload: dict[str, Any] = {
        "FileFormatVersion": SERVER_TIMINGS_FORMAT_VERSION,
        "ActivityID": result.activity_id,
        "StorageEngineDuration": result.storage_engine_duration,
        "StorageEngineNetParallelDuration": result.storage_engine_net_parallel_duration,
        "FormulaEngineDuration": result.formula_engine_duration,
        "StorageEngineEvents": [row.to_export_dict() for row in result.events],
        "StorageEngineCpu": result.storage_engine_cpu,
        "TotalDuration": result.total_duration,
        "StorageEngineQueryCount": result.storage_engine_query_count,
        "VertipaqCacheMatches": result.vertipaq_cache_matches,
        "TotalCpuDuration": result.total_cpu_duration,
        "QueryEndDateTime": _iso(result.query_end),
        "QueryStartDateTime": _iso(result.query_start),
        "CommandText": result.command_text,
        "TimelineTotalDuration": result.timeline_total_duration,
    }
    if result.request_id:
        payload["RequestID"] = result.request_id
    if result.parameters:
        payload["Parameters"] = result.parameters
    if result.total_direct_query_duration:
        payload["TotalDirectQueryDuration"] = result.total_direct_query_duration
    if result.parallel_storage_engine_events_detected:
        payload["ParallelStorageEngineEventsDetected"] = True
    return payload


def build_query_plan_export(
    physical: QueryPlan | None,
    logical: QueryPlan | None,
    *,
    activity_id: str | None = None,
    request_id: str | None = None,
    command_text: str = "",
    parameters: str | None = None,
    start_datetime=None,
) -> dict[str, Any]:
    """Build the ``QueryPlan.json`` payload."""
    payload: dict[str, Any] = {
        "FileFormatVersion": QUERY_PLAN_FORMAT_VERSION,
        "PhysicalQueryPlanRows": physical.to_export_rows() if physical else [],
        "LogicalQueryPlanRows": logical.to_export_rows() if logical else [],
        "ActivityID": activity_id,
        "CommandText": command_text,
        "Parameters": parameters,
    }
    if request_id:
        payload["RequestID"] = request_id
    if start_datetime is not None:
        payload["StartDatetime"] = (
            start_datetime if isinstance(start_datetime, str) else _iso(start_datetime)
        )
    return payload


def _write_json(path: str, payload: dict[str, Any]) -> str:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
    return path


def write_server_timings(result: ServerTimingsResult, path: str) -> str:
    return _write_json(path, build_server_timings_export(result))


def write_query_plan(
    path: str,
    physical: QueryPlan | None,
    logical: QueryPlan | None,
    **kwargs: Any,
) -> str:
    return _write_json(path, build_query_plan_export(physical, logical, **kwargs))


#: One ``Default`` per part extension we write (``.dax`` text, ``.json``).
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="dax" ContentType="text/plain" />'
    '<Default Extension="json" ContentType="application/json" />'
    "</Types>"
)


def write_daxx(
    path: str,
    *,
    query_text: str,
    server_timings: dict[str, Any] | None = None,
    query_plan: dict[str, Any] | None = None,
    model_info: dict[str, Any] | None = None,
) -> str:
    """Write a ``.daxx`` bundle as an OPC package DAX Studio can open directly.

    DAX Studio opens ``.daxx`` with System.IO.Packaging, which only sees the
    parts declared in ``[Content_Types].xml``: without that part the package
    opened as an EMPTY document (F-11).  It is written first.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("[Content_Types].xml", _CONTENT_TYPES)
        bundle.writestr("Query.dax", query_text or "")
        if server_timings is not None:
            bundle.writestr(
                "ServerTimings.json",
                json.dumps(server_timings, indent=2, ensure_ascii=False, default=str),
            )
        if query_plan is not None:
            bundle.writestr(
                "QueryPlan.json",
                json.dumps(query_plan, indent=2, ensure_ascii=False, default=str),
            )
        if model_info is not None:
            bundle.writestr(
                "ModelInfo.json",
                json.dumps(model_info, indent=2, ensure_ascii=False, default=str),
            )
    return path
