"""Build-or-reuse entry point for the dependency graph (handler + evidence).

``get_or_build_graph`` is the one place that decides WHAT a graph covers and
whether a cached one is still valid (J-19):

* cache key = ``(scope, normalised pbip path, model_etag, report_signature,
  all_bound_reports)`` — scope and path are part of it (a ``scope=model``
  graph is never served for ``scope=both``), the report signature is a value
  signature (files, bytes, max mtime, dirs, max dir mtime) so deletions and
  mtime-preserving copies invalidate, and the model etag hashes every
  name + expression the graph is built from;
* the live model snapshot is re-read unless the connection identity, model
  generation and the model-version fingerprint are all unchanged;
* ``pbip_path`` falls back to ``connection_state.get_pbip_folder_path()`` (the
  old ``getattr(connection_state, "pbip_folder_path")`` never resolved);
* every graph carries ``meta``: ``graph_scope``, ``built_at``, ``model_etag``,
  ``report_side_available``, ``model_generation``, ``connection`` ...;
  ``freshness(graph)`` reports ``stale: true`` once the model changed since.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Tuple

from core.graph.cache import get_cache
from core.graph.models import Graph

logger = logging.getLogger(__name__)

_SNAPSHOT_LOCK = threading.Lock()
_SNAPSHOT_CACHE: Dict[str, Any] = {"key": None, "snapshot": None}


def _connection_state():
    from core.infrastructure.connection_state import connection_state

    return connection_state


def _identity(cs: Any) -> Tuple[Any, ...]:
    try:
        ident = cs.get_connection_identity() or {}
    except Exception:  # noqa: BLE001
        ident = {}
    return (
        ident.get("port"),
        ident.get("database"),
        ident.get("connection_generation"),
        ident.get("model_generation", getattr(cs, "model_generation", None)),
    )


def _fingerprint(qe: Any) -> Optional[Any]:
    fn = getattr(qe, "get_model_version_fingerprint", None)
    if not callable(fn):
        return None
    try:
        fp = fn()
    except Exception:  # noqa: BLE001
        return None
    return repr(fp) if fp is not None else None


def invalidate_snapshot_cache() -> None:
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE["key"] = None
        _SNAPSHOT_CACHE["snapshot"] = None


def load_model_snapshot(*, force: bool = False) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """``(snapshot, error)`` for the connected model (re-used while unchanged)."""
    cs = _connection_state()
    if not cs.is_connected():
        return None, {"error": "Not connected to Power BI Desktop", "error_type": "not_connected"}
    qe = getattr(cs, "query_executor", None)
    if qe is None:
        return None, {"error": "query executor unavailable", "error_type": "not_connected"}
    ident = _identity(cs)
    fp = _fingerprint(qe)
    key = (ident, fp) if fp is not None else None
    with _SNAPSHOT_LOCK:
        if not force and key is not None and _SNAPSHOT_CACHE["key"] == key:
            return _SNAPSHOT_CACHE["snapshot"], None
    from core.graph.model_source import load_live_snapshot

    snap = load_live_snapshot(qe)
    if not snap.tables and not snap.measures:
        return None, {
            "error": "could not materialize the live model (INFO.* queries returned nothing)",
            "error_type": "not_connected",
            "warnings": snap.warnings,
        }
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE["key"] = key
        _SNAPSHOT_CACHE["snapshot"] = snap
    return snap, None


def resolve_pbip_path(pbip_path: Optional[str]) -> Optional[str]:
    if pbip_path:
        return pbip_path
    try:
        cs = _connection_state()
        getter = getattr(cs, "get_pbip_folder_path", None)
        path = getter() if callable(getter) else getattr(cs, "pbip_path", None)
        if not path:
            info = cs.get_pbip_info() or {}
            path = info.get("file_full_path") or info.get("pbip_folder_path")
        return path or None
    except Exception:  # noqa: BLE001
        return None


def _norm(path: Optional[str]) -> str:
    try:
        from core.autonomous.edit_ledger import norm_path

        return norm_path(path)
    except Exception:  # noqa: BLE001
        return (path or "").lower()


def _report_signature(pbip_path: Optional[str]) -> Tuple[Any, ...]:
    if not pbip_path:
        return ()
    try:
        from core.graph.report_source import report_signature
        from core.pbip.pbir_model import resolve_pbip

        res = resolve_pbip(pbip_path)
        dirs = [
            str(r["definition_dir"]) for r in res.get("reports") or [] if r.get("definition_dir")
        ]
        pointer = []
        for r in res.get("reports") or []:
            pointer.append(str(r.get("model_dir") or ""))
        return (report_signature(dirs), tuple(sorted(dirs)), tuple(pointer))
    except Exception as e:  # noqa: BLE001
        logger.debug("report signature failed: %s", e)
        return ("unsigned",)


def get_or_build_graph(
    *,
    scope: str = "both",
    pbip_path: Optional[str] = None,
    use_cache: bool = True,
    all_bound_reports: bool = False,
    model: Any = None,
) -> Dict[str, Any]:
    """``{success, graph_id, graph, cached}`` or an error envelope.

    ``model`` injects a snapshot / facade (tests, offline callers) instead of
    reading the live model.
    """
    from core.graph.builder import build_graph

    if scope not in {"model", "report", "both"}:
        return {"success": False, "error": f"invalid scope {scope!r}", "error_type": "invalid_args"}
    snap = None
    model_generation = None
    connection = None
    if scope in {"model", "both"}:
        if model is not None:
            from core.graph.model_source import as_snapshot

            snap = as_snapshot(model)
        else:
            snap, err = load_model_snapshot()
            if err:
                return {"success": False, **err}
            cs = _connection_state()
            model_generation = getattr(cs, "model_generation", None)
            ident = _identity(cs)
            connection = {"port": ident[0], "database": ident[1]}
    path = resolve_pbip_path(pbip_path) if scope in {"report", "both"} else pbip_path
    if scope in {"report", "both"} and not path:
        return {
            "success": False,
            "error": "pbip_path is required for report scope (no PBIP path is known for the "
            "connected model)",
            "error_type": "invalid_args",
        }
    etag = snap.etag() if snap is not None else None
    key = (
        scope,
        _norm(path) if scope in {"report", "both"} else "",
        etag,
        _report_signature(path) if scope in {"report", "both"} else (),
        bool(all_bound_reports),
    )
    cache = get_cache()
    if use_cache:
        gid, graph = cache.get(key)
        if graph is not None:
            return {"success": True, "graph_id": gid, "graph": graph, "cached": True}
    from core.graph.builder import GraphBuildCancelled

    try:
        graph = build_graph(
            model=snap, pbip_path=path, scope=scope, include_bound_reports=all_bound_reports
        )
    except GraphBuildCancelled as e:
        # F-26: nothing is cached and no graph_id is issued: a partial graph must never be mistaken
        # for evidence (the destructive-op gate fails closed on success=false).
        return {
            "success": False,
            "cancelled": True,
            "partial": False,
            "error": str(e),
            "error_type": "cancelled",
            "suggestion": "Re-run build; nothing was cached.",
        }
    graph.meta["model_generation"] = model_generation
    graph.meta["connection"] = connection
    gid = cache.put(key, graph)
    return {"success": True, "graph_id": gid, "graph": graph, "cached": False}


def freshness(graph: Graph) -> Dict[str, Any]:
    """``{built_at, model_etag, graph_scope, stale, stale_reason?}`` for a cached graph."""
    meta = graph.meta or {}
    out: Dict[str, Any] = {
        "built_at": meta.get("built_at"),
        "model_etag": meta.get("model_etag"),
        "graph_scope": meta.get("graph_scope") or "unknown",
        "stale": False,
    }
    gen = meta.get("model_generation")
    if gen is not None:
        try:
            cs = _connection_state()
            now = getattr(cs, "model_generation", None)
            ident = _identity(cs)
            conn = meta.get("connection") or {}
            if now is not None and now != gen:
                out["stale"] = True
                out["stale_reason"] = (
                    f"the model changed since the graph was built (generation {gen} -> {now}); "
                    "rebuild before trusting an impact result"
                )
            elif conn and (conn.get("port"), conn.get("database")) != (ident[0], ident[1]):
                out["stale"] = True
                out["stale_reason"] = "connected to a different model since the graph was built"
        except Exception:  # noqa: BLE001
            pass
    return out


__all__ = [
    "get_or_build_graph",
    "load_model_snapshot",
    "resolve_pbip_path",
    "freshness",
    "invalidate_snapshot_cache",
]
