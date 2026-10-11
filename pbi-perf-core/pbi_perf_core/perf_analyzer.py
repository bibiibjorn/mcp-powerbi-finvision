"""Power BI Performance Analyzer parity: DAX / Display / **Other**.

Performance Analyzer reports three numbers per visual, and the one that matters
on a busy page is the one nobody can query: **Other**.  It is not serialised
anywhere - it is the residual::

    Other = Visual Container Lifecycle - DAX - DirectQuery - Render - Parameters

That residual is overwhelmingly *waiting*: the visual asking for a query slot
while other visuals on the page hold them.  Which is why a page with twenty
visuals feels slow even when every individual query is fast.

Three lanes are supported here:

``parse_performance_export``
    Read Desktop's ``PowerBIPerformanceData.json`` - real, measured, includes
    the true Other.
``simulate_page``
    Given each visual's measured DAX time, model the queue with a K-slot list
    scheduler to *estimate* Other before you have a pane to read.
``summarise_rows``
    Shape either of the above into one comparable table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

__all__ = [
    "VisualTiming",
    "parse_performance_export",
    "simulate_page",
    "summarise_rows",
    "DEFAULT_QUERY_SLOTS",
]

#: Power BI does not document a fixed number of concurrent visual queries.
#: Six matches commonly observed desktop behaviour and is the default here -
#: every output that uses it is labelled an estimate.
DEFAULT_QUERY_SLOTS = 6

_LIFECYCLE = "Visual Container Lifecycle"
_QUERY = "Query"
_EXECUTE_DAX = "Execute DAX Query"
_EXECUTE_DIRECTQUERY = "Execute Direct Query"
_RENDER = "Render"
_QUERY_PENDING = "Query Pending"
_QUERY_GENERATION = "Query Generation"
_EVALUATED_PARAMETERS = "Evaluated Parameters"


@dataclass
class VisualTiming:
    """One visual's timings, however they were obtained."""

    visual: str = ""
    visual_id: str = ""
    visual_type: str = ""
    total_ms: float = 0.0
    dax_ms: float = 0.0
    direct_query_ms: float = 0.0
    display_ms: float = 0.0
    other_ms: float = 0.0
    parameters_ms: float = 0.0
    query_pending_ms: float = 0.0
    query_text: str = ""
    row_count: int | None = None
    start_ms: float = 0.0
    end_ms: float = 0.0
    # Filled in when the query is also traced through the storage engine.
    se_ms: float | None = None
    fe_ms: float | None = None
    se_queries: int | None = None
    cache_hits: int | None = None
    peak_memory_kb: int | None = None
    estimated: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "visual": self.visual,
            "total_ms": round(self.total_ms, 1),
            "dax_ms": round(self.dax_ms, 1),
            "display_ms": round(self.display_ms, 1),
            "other_ms": round(self.other_ms, 1),
        }
        if self.visual_type:
            out["type"] = self.visual_type
        if self.direct_query_ms:
            out["direct_query_ms"] = round(self.direct_query_ms, 1)
        if self.parameters_ms:
            out["parameters_ms"] = round(self.parameters_ms, 1)
        if self.query_pending_ms:
            out["query_pending_ms"] = round(self.query_pending_ms, 1)
        if self.se_ms is not None:
            out["se_ms"] = round(self.se_ms, 1)
            out["fe_ms"] = round(self.fe_ms or 0, 1)
            if self.dax_ms:
                out["se_pct"] = round(100.0 * self.se_ms / self.dax_ms, 1)
        if self.se_queries is not None:
            out["se_queries"] = self.se_queries
        if self.cache_hits is not None:
            out["cache_hits"] = self.cache_hits
        if self.peak_memory_kb is not None:
            out["peak_memory_kb"] = self.peak_memory_kb
        if self.row_count is not None:
            out["row_count"] = self.row_count
        if self.estimated:
            out["estimated"] = True
        return out


def _to_ms(value: Any) -> float | None:
    """Epoch milliseconds for a numeric or ISO-8601 timestamp (F-31)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp() * 1000.0
    except ValueError:
        return None


def _nest_flat_events(events: list[Any]) -> list[Any]:
    """Desktop's FLAT export -> nested ``children`` with numeric times (F-31).

    ``PowerBIPerformanceData.json`` is a flat ``events[]`` list linked by
    ``id`` / ``parentId`` with ISO-timestamp ``start`` / ``end``, and an event
    may be written twice (once when it starts, once when it ends).  Already
    nested input is returned unchanged.
    """
    dicts = [e for e in events if isinstance(e, dict)]
    if not any("id" in e or "parentId" in e for e in dicts):
        return events
    if any(isinstance(e.get("children"), list) for e in dicts):
        return events
    nodes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    anonymous: list[dict[str, Any]] = []
    for event in dicts:
        eid = event.get("id")
        if eid is None:
            anonymous.append({**event, "children": []})
            continue
        key = str(eid)
        node = nodes.get(key)
        if node is None:
            node = nodes[key] = {"children": []}
            order.append(key)
        for k, v in event.items():
            if k == "metrics" and isinstance(v, dict) and isinstance(node.get("metrics"), dict):
                node["metrics"] = {**node["metrics"], **v}
            elif v is not None and (k not in node or node[k] in (None, "")):
                node[k] = v
    for node in list(nodes.values()) + anonymous:
        for key in ("start", "end"):
            ms = _to_ms(node.get(key))
            if ms is not None:
                node[key] = ms
    roots: list[dict[str, Any]] = []
    for key in order:
        node = nodes[key]
        parent = node.get("parentId")
        if parent is not None and str(parent) in nodes and str(parent) != key:
            nodes[str(parent)]["children"].append(node)
        else:
            roots.append(node)
    return roots + anonymous


def _duration(node: dict[str, Any]) -> float:
    start = _to_ms(node.get("start"))
    end = _to_ms(node.get("end"))
    if start is not None and end is not None:
        return end - start
    for key in ("durationMs", "duration", "Duration"):
        value = node.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("children", "Children", "events"):
        value = node.get(key)
        if isinstance(value, list):
            return [c for c in value if isinstance(c, dict)]
    return []


def _name(node: dict[str, Any]) -> str:
    for key in ("name", "Name", "component"):
        value = node.get(key)
        if isinstance(value, str):
            return value
    return ""


def _metrics(node: dict[str, Any]) -> dict[str, Any]:
    value = node.get("metrics") or node.get("Metrics")
    return value if isinstance(value, dict) else {}


def _walk(node: dict[str, Any]):
    yield node
    for child in _children(node):
        yield from _walk(child)


def _find_all(node: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [n for n in _walk(node) if _name(n) == name]


def parse_performance_export(data: dict[str, Any] | list) -> list[VisualTiming]:
    """Parse Desktop's exported ``PowerBIPerformanceData.json``.

    Handles both the object form (``{"version": ..., "events": [...]}``) and a
    bare list of events, nested (``children``) or flat (``id`` / ``parentId``
    with ISO timestamps, as Desktop writes it - F-31).
    """
    if isinstance(data, dict):
        events = data.get("events") or data.get("Events") or []
    else:
        events = data
    if not isinstance(events, list):
        return []
    events = _nest_flat_events(events)

    results: list[VisualTiming] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        for lifecycle in _find_all(event, _LIFECYCLE):
            results.append(_parse_lifecycle(lifecycle))
    return results


def _parse_lifecycle(node: dict[str, Any]) -> VisualTiming:
    metrics = _metrics(node)
    timing = VisualTiming(
        visual=str(metrics.get("visualTitle") or metrics.get("visualId") or "(unnamed)"),
        visual_id=str(metrics.get("visualId") or ""),
        visual_type=str(metrics.get("visualType") or ""),
        total_ms=_duration(node),
    )
    start = _to_ms(node.get("start"))
    end = _to_ms(node.get("end"))
    if start is not None:
        timing.start_ms = start
    if end is not None:
        timing.end_ms = end

    for dax in _find_all(node, _EXECUTE_DAX):
        timing.dax_ms += _duration(dax)
        dax_metrics = _metrics(dax)
        if not timing.query_text:
            timing.query_text = str(dax_metrics.get("QueryText") or "")
        rows = dax_metrics.get("RowCount")
        if isinstance(rows, (int, float)):
            timing.row_count = int(rows)

    for dq in _find_all(node, _EXECUTE_DIRECTQUERY):
        timing.direct_query_ms += _duration(dq)
    for render in _find_all(node, _RENDER):
        timing.display_ms += _duration(render)
    for pending in _find_all(node, _QUERY_PENDING):
        timing.query_pending_ms += _duration(pending)
    for params in _find_all(node, _EVALUATED_PARAMETERS):
        timing.parameters_ms += _duration(params)

    # The number Power BI shows as "Other" is the residual of the lifecycle.
    timing.other_ms = max(
        timing.total_ms
        - timing.dax_ms
        - timing.direct_query_ms
        - timing.display_ms
        - timing.parameters_ms,
        0.0,
    )
    return timing


def simulate_page(
    visuals: Sequence[VisualTiming | dict[str, Any]],
    k_slots: int = DEFAULT_QUERY_SLOTS,
    *,
    render_ms_default: float = 0.0,
) -> dict[str, Any]:
    """Model page load as a K-slot queue over the measured DAX times.

    A list scheduler: each visual takes the earliest free slot, longest query
    first (which is how a busy page behaves once queries are in flight).  The
    wait a visual accumulates before its slot frees is the estimate of "Other".
    """
    items: list[VisualTiming] = []
    for raw in visuals:
        if isinstance(raw, VisualTiming):
            items.append(raw)
        else:
            items.append(
                VisualTiming(
                    visual=str(raw.get("visual", raw.get("name", "(unnamed)"))),
                    visual_type=str(raw.get("type", raw.get("visual_type", ""))),
                    dax_ms=float(raw.get("dax_ms", raw.get("total_ms", 0)) or 0),
                    display_ms=float(raw.get("display_ms", render_ms_default) or 0),
                    se_ms=raw.get("se_ms"),
                    fe_ms=raw.get("fe_ms"),
                    se_queries=raw.get("se_queries"),
                    cache_hits=raw.get("cache_hits"),
                    peak_memory_kb=raw.get("peak_memory_kb"),
                )
            )

    if not items:
        return {
            "k_slots": k_slots,
            "visuals": [],
            "est_page_ms": 0,
            "critical_path_ms": 0,
            "total_wait_ms": 0,
            "note": "No visuals to simulate.",
        }

    slots = [0.0] * max(int(k_slots), 1)
    ordered = sorted(items, key=lambda v: -v.dax_ms)
    scheduled: list[VisualTiming] = []

    for visual in ordered:
        index = min(range(len(slots)), key=lambda i: slots[i])
        start = slots[index]
        end = start + visual.dax_ms
        slots[index] = end
        visual.start_ms = start
        visual.end_ms = end + visual.display_ms
        visual.other_ms = start  # the queue wait is the estimate of "Other"
        visual.total_ms = visual.dax_ms + visual.display_ms + visual.other_ms
        visual.estimated = True
        scheduled.append(visual)

    est_page_ms = max((v.end_ms for v in scheduled), default=0.0)
    serial_ms = sum(v.dax_ms for v in scheduled)
    critical_path_ms = max((v.dax_ms + v.display_ms for v in scheduled), default=0.0)
    total_wait = sum(v.other_ms for v in scheduled)

    slowest = sorted(scheduled, key=lambda v: -v.dax_ms)
    what_if: dict[str, Any] = {}
    for alt_k in sorted({1, 2, 4, 6, 8, max(1, k_slots * 2)}):
        alt_slots = [0.0] * alt_k
        for visual in sorted(scheduled, key=lambda v: -v.dax_ms):
            index = min(range(alt_k), key=lambda i: alt_slots[i])
            alt_slots[index] += visual.dax_ms
        what_if[f"k={alt_k}"] = round(max(alt_slots), 1)
    if slowest:
        without = [v for v in scheduled if v is not slowest[0]]
        alt_slots = [0.0] * max(int(k_slots), 1)
        for visual in sorted(without, key=lambda v: -v.dax_ms):
            index = min(range(len(alt_slots)), key=lambda i: alt_slots[i])
            alt_slots[index] += visual.dax_ms
        what_if["without_slowest_visual"] = {
            "visual": slowest[0].visual,
            "est_page_ms": round(max(alt_slots) if alt_slots else 0.0, 1),
            "saving_ms": round(est_page_ms - (max(alt_slots) if alt_slots else 0.0), 1),
        }

    return {
        "k_slots": k_slots,
        "visuals": [v.to_dict() for v in sorted(scheduled, key=lambda v: -v.total_ms)],
        "est_page_ms": round(est_page_ms, 1),
        "serial_ms": round(serial_ms, 1),
        "critical_path_ms": round(critical_path_ms, 1),
        "total_wait_ms": round(total_wait, 1),
        "wait_pct": round(100.0 * total_wait / max(sum(v.total_ms for v in scheduled), 1), 1),
        "what_if": what_if,
        "note": (
            "Wait ('Other') is ESTIMATED with a "
            f"{k_slots}-slot scheduler; Power BI does not publish its concurrency "
            "limit. Read Desktop's Performance Analyzer pane for measured values."
        ),
    }


def summarise_rows(rows: Iterable[VisualTiming], *, sort_by: str = "total_ms") -> dict[str, Any]:
    """Aggregate a set of visual timings into a page-level view."""
    items = list(rows)
    if not items:
        return {"visuals": [], "totals": {}}

    totals = {
        "visuals": len(items),
        "dax_ms": round(sum(v.dax_ms for v in items), 1),
        "display_ms": round(sum(v.display_ms for v in items), 1),
        "other_ms": round(sum(v.other_ms for v in items), 1),
        "total_ms": round(sum(v.total_ms for v in items), 1),
    }
    if totals["total_ms"]:
        totals["other_pct"] = round(100.0 * totals["other_ms"] / totals["total_ms"], 1)
        totals["dax_pct"] = round(100.0 * totals["dax_ms"] / totals["total_ms"], 1)

    wall_clock = None
    if any(v.end_ms for v in items):
        wall_clock = round(max(v.end_ms for v in items) - min(v.start_ms for v in items), 1)
        totals["wall_clock_ms"] = wall_clock

    key = lambda v: -getattr(v, sort_by, 0.0)  # noqa: E731
    return {
        "visuals": [v.to_dict() for v in sorted(items, key=key)],
        "totals": totals,
        "slowest": [v.visual for v in sorted(items, key=key)[:5]],
    }
