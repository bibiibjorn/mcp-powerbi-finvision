"""``09_Debug_Operations compare mode=live_swap`` (F-08): server-layer shell around
:mod:`core.debug.live_swap`.

Resolves the visual's real query and the candidate patch, gates the call as a destructive write, wires
the live environment (TOM measure read / write through ``write_lane.commit``, the journal's
``checkpoint_create`` / ``checkpoint_restore``, the 13_Query_Plan traced cold run, the executor's rows)
into :class:`core.debug.live_swap.SwapEnv` and returns the verdict.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from core.infrastructure.connection_state import connection_state
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

#: Seconds of the tool timeout held back for the restore + its verification.
_RESTORE_RESERVE_S = 45.0
_MAX_RUNS = 10


@contextmanager
def _model_session() -> Iterator[Tuple[Any, Optional[str]]]:
    from core.operations.amo_helpers import get_server_db_model

    server, _db, model, err = get_server_db_model(connection_state)
    try:
        yield model, err
    finally:
        try:
            if server is not None:
                server.Disconnect()
        except Exception:  # noqa: BLE001
            pass


def _measure(model: Any, table: str, name: str) -> Any:
    t = model.Tables.Find(table)
    return t.Measures.Find(name) if t is not None else None


def _find_tables(patches: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Fill in a missing ``table_name`` from the live model; an error envelope when it cannot."""
    missing = [p for p in patches if not p.get("table_name")]
    if not missing:
        return None
    with _model_session() as (model, err):
        if model is None:
            return error_envelope("connection_error", err or "Not connected")
        for p in missing:
            hits = [t.Name for t in model.Tables if t.Measures.Find(p["measure_name"]) is not None]
            if len(hits) != 1:
                return error_envelope(
                    "not_found" if not hits else "ambiguous_target",
                    f"measure {p['measure_name']!r} "
                    + (
                        "not found"
                        if not hits
                        else f"exists in several tables {hits}; pass table_name"
                    ),
                )
            p["table_name"] = hits[0]
    return None


def _patch_refs(patch: Dict[str, Any]) -> List[Tuple[str, Optional[str]]]:
    """``(name, table-or-None)`` of every measure-shaped reference in a candidate body."""
    from core.dax.dax_reference_parser import scan_references

    out: List[Tuple[str, Optional[str]]] = []
    for ref in scan_references(str(patch.get("expression") or "")):
        if ref.kind not in ("bracket", "qualified"):
            continue
        table = str(ref.table).strip().lower() if ref.kind == "qualified" else None
        out.append((str(ref.name).strip().lower(), table))
    return out


def _refers_to(
    refs: List[Tuple[str, Optional[str]]], name: str, table: str, defer_unknown_table: bool
) -> bool:
    for rname, rtable in refs:
        if rname != name:
            continue
        if rtable is not None:
            if table and rtable != table:
                continue  # a column / measure of another table that shares the name
            if not table and defer_unknown_table:
                continue  # cannot tell a measure from a column yet: decided after table lookup
        return True
    return False


def circular_patch_error(
    patches: List[Dict[str, Any]], *, defer_unknown_table: bool = False
) -> Optional[Dict[str, Any]]:
    """``circular_candidate`` when a candidate body references the measure it replaces, or when
    candidate bodies reference each other in a cycle (A's body reads B and B's body reads A).

    Writing ``[M] + 0`` as the body of ``[M]`` would be a circular dependency the engine rejects
    mid-swap. Lexer-based (strings / comments are never read), case-insensitive; a qualified
    ``T[M]`` counts only when ``T`` is the patched measure's table (when that is known).

    When a patch's table is not known, a qualified ``Other[M]`` is treated as circular (it may
    be the measure itself; conservative) unless ``defer_unknown_table`` is set: ``handle_live_swap``
    first runs the check that way, then again once ``_find_tables`` has resolved every table, so
    a plain column ``Other[M]`` is no longer mistaken for the patched measure."""
    keys = [
        (
            str(p.get("measure_name") or "").strip().lower(),
            str(p.get("table_name") or "").strip().lower(),
        )
        for p in patches
    ]
    refs = [_patch_refs(p) for p in patches]
    selfref = {
        i
        for i, (name, table) in enumerate(keys)
        if _refers_to(refs[i], name, table, defer_unknown_table)
    }
    edges = {
        i: {
            j
            for j, (name, table) in enumerate(keys)
            if j != i and _refers_to(refs[i], name, table, defer_unknown_table)
        }
        for i in range(len(patches))
    }

    def reaches(start: int) -> bool:
        seen: Set[int] = set()
        stack = list(edges[start])
        while stack:
            node = stack.pop()
            if node == start:
                return True
            if node not in seen:
                seen.add(node)
                stack.extend(edges[node])
        return False

    cyclic = {i for i in edges if i not in selfref and reaches(i)}
    if not selfref and not cyclic:
        return None

    def names(ix: Set[int]) -> List[str]:
        return [str(patches[i].get("measure_name")) for i in sorted(ix)]

    parts = []
    if selfref:
        parts.append(
            "the candidate body of "
            + ", ".join(f"[{n}]" for n in names(selfref))
            + " references the measure it replaces"
        )
    if cyclic:
        parts.append(
            "the candidate bodies of "
            + ", ".join(f"[{n}]" for n in names(cyclic))
            + " reference each other in a cycle"
        )
    return error_envelope(
        "circular_candidate",
        "; ".join(parts) + ": writing them would create a circular dependency. "
        "Inline the original body into the candidate (or patch a different measure). "
        "Nothing was changed.",
        patched=names(selfref | cyclic),
    )


def resolve_patches(args: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """The candidate bodies: ``patch=[{table_name?, measure_name, expression}]`` or the single
    ``original_measure`` + ``optimized_expression`` (+ ``table_name``) pair."""
    raw = args.get("patch")
    patches: List[Dict[str, Any]] = []
    if raw is not None:
        if not isinstance(raw, list) or not raw:
            return [], error_envelope(
                "invalid_args", "patch must be a non-empty list of {measure_name, expression}"
            )
        for item in raw:
            if not isinstance(item, dict):
                return [], error_envelope("invalid_args", f"patch item {item!r} must be an object")
            name = item.get("measure_name") or item.get("name")
            expr = item.get("expression")
            if not name or not expr:
                return [], error_envelope(
                    "invalid_args", f"patch item {item!r} needs measure_name and expression"
                )
            patches.append(
                {
                    "table_name": item.get("table_name"),
                    "measure_name": str(name).strip("[]"),
                    "expression": str(expr),
                }
            )
    else:
        name, expr = args.get("original_measure"), args.get("optimized_expression")
        if not name or not expr:
            return [], error_envelope(
                "missing_argument",
                "live_swap needs the candidate: original_measure + optimized_expression, or patch=[...]",
            )
        patches.append(
            {
                "table_name": args.get("table_name"),
                "measure_name": str(name).strip().strip("[]"),
                "expression": str(expr),
            }
        )
    return patches, None


def resolve_query(args: Dict[str, Any]) -> Tuple[Optional[str], str, Optional[Dict[str, Any]]]:
    """``(query, source, error)``: an explicit ``reference_query`` / ``query``, else the visual's own
    query (the 09 ``visual`` reconstruction, or Desktop's Performance Analyzer query when given)."""
    explicit = args.get("reference_query") or args.get("query")
    if explicit:
        return str(explicit), "reference_query", None
    if not args.get("page_name") or not (args.get("visual_id") or args.get("visual_name")):
        return (
            None,
            "",
            error_envelope(
                "missing_argument",
                "live_swap needs the visual (page_name + visual_id/visual_name) or an explicit reference_query",
            ),
        )
    from server.handlers._debug.visual import handle_debug_visual

    resp = handle_debug_visual(
        {**args, "execute_query": False, "compact": True, "trace": False, "operation": "visual"}
    )
    if not isinstance(resp, dict) or resp.get("success") is False:
        return (
            None,
            "",
            (
                resp
                if isinstance(resp, dict)
                else error_envelope("operation_failed", "visual lookup failed")
            ),
        )
    if resp.get("executed_query_source") == "reference" and resp.get("reference_query"):
        return str(resp["reference_query"]), "performance_analyzer", None
    query = resp.get("query_with_manual_filters") or resp.get("query")
    if not query:
        return None, "", error_envelope("operation_failed", "the visual produced no DAX query")
    return str(query), "visual_reconstruction", None


def _time_budget_s() -> float:
    try:
        from core.config.config_manager import config
        from core.config.tool_timeouts import ToolTimeoutManager

        timeout = float(
            ToolTimeoutManager(config.get("tool_timeouts", {}) or {}).get_timeout(
                "09_Debug_Operations", default=180
            )
        )
    except Exception:  # noqa: BLE001
        timeout = 180.0
    return max(20.0, timeout - _RESTORE_RESERVE_S)


def build_env() -> "Any":
    from core.debug.live_swap import SwapEnv
    from core.dax.semantic_equivalence import compare_rows
    from core.operations.journal_manager import WriteJournalManager
    from core.operations.write_lane import commit
    from core.query_plan import capturer

    journal = WriteJournalManager()

    def read_expression(table: str, name: str) -> Optional[str]:
        with _model_session() as (model, err):
            if model is None:
                raise RuntimeError(err or "Not connected")
            m = _measure(model, table, name)
            return None if m is None else str(m.Expression or "")

    def write_expressions(bodies: Dict[Tuple[str, str], str]) -> None:
        with _model_session() as (model, err):
            if model is None:
                raise RuntimeError(err or "Not connected")
            for (table, name), body in bodies.items():
                m = _measure(model, table, name)
                if m is None:
                    raise RuntimeError(f"measure {table}[{name}] not found")
                m.Expression = body
            commit(model, label="debug/live_swap")

    def run_cold(query: str) -> Dict[str, Any]:
        out = capturer.benchmark_traced(dax_query=query, cold_runs=1, warm_runs=0, warmup=0)
        if not out.get("success"):
            raise RuntimeError(out.get("error") or "traced run failed")
        runs = (out.get("cold") or {}).get("runs") or []
        if not runs:
            raise RuntimeError("no traced run returned")
        row = dict(runs[0])
        row["cold_cache_honoured"] = out.get("cold_cache_honoured")
        return row

    def run_rows(query: str) -> Dict[str, Any]:
        qe = connection_state.query_executor
        if qe is None:
            return {
                "success": False,
                "error": "no query executor",
                "error_type": "manager_unavailable",
            }
        return qe.validate_and_execute_dax(query, top_n=None, bypass_cache=True)

    return SwapEnv(
        read_expression=read_expression,
        write_expressions=write_expressions,
        checkpoint_create=lambda name: journal.checkpoint_create({"checkpoint": name}),
        checkpoint_restore=lambda name, preview: journal.checkpoint_restore(
            {"checkpoint": name, "preview": bool(preview)}
        ),
        run_cold=run_cold,
        run_rows=run_rows,
        compare_rows=lambda a, b, tol: compare_rows(a, b, tolerance=tol),
        stats=capturer._stats,  # noqa: SLF001 - the 13_Query_Plan statistics, called not modified
        is_cancelled=_is_cancelled,
    )


def _is_cancelled() -> bool:
    try:
        from server.request_context import is_cancelled

        return bool(is_cancelled())
    except Exception:  # noqa: BLE001
        return False


def handle_live_swap(args: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point for ``compare mode=live_swap`` (``dry_run=true`` plans without touching anything)."""
    from core.autonomous.gating import Severity, merge_gate_info, require_autonomous_mode
    from core.debug.live_swap import SwapSpec, run_live_swap

    dry = args.get("dry_run") is True or str(args.get("dry_run")).lower() == "true"
    patches, err = resolve_patches(args)
    if err:
        return err
    # Early, offline pass (cheap, needs no connection); qualified refs of a patch whose table is
    # still unknown wait for the second pass below.
    err = circular_patch_error(patches, defer_unknown_table=True)
    if err:
        return err
    if not connection_state.is_connected():
        return error_envelope("not_connected", "Not connected to Power BI model")
    from core.infrastructure import offline_model, xmla_target

    if xmla_target.is_active() or offline_model.is_active():
        return error_envelope(
            "live_swap_unavailable",
            "live_swap needs a live Desktop model (it writes, queries cold and restores); the "
            "published (XMLA) and offline-folder targets cannot do that",
        )
    try:
        runs = max(1, min(int(args.get("runs") or 3), _MAX_RUNS))
    except (TypeError, ValueError):
        runs = 3
    err = _find_tables(patches)
    if err:
        return err
    err = circular_patch_error(patches)  # every table is known now
    if err:
        return err
    query, source, err = resolve_query(args)
    if err:
        return err
    spec = SwapSpec(
        query=str(query),
        patches=patches,
        runs=runs,
        tolerance=float(args.get("tolerance", 0.001)),
        time_budget_s=_time_budget_s(),
        query_source=source,
    )
    if dry:
        from core.debug.live_swap import sequence

        return {
            "success": True,
            "mode": "live_swap",
            "dry_run": True,
            "query": spec.query,
            "query_source": source,
            "patched": [{k: p[k] for k in ("table_name", "measure_name")} for p in patches],
            "order": sequence(runs),
            "model_writes_estimated": sum(
                1 for a, b in zip(sequence(runs), sequence(runs)[1:]) if a != b
            ),
            "restore": "checkpoint_restore only when the model still differs (preview first), then verified",
            "note": "dry_run: nothing was written. The real run checkpoints, swaps, measures cold, restores and verifies.",
        }
    gate = require_autonomous_mode(
        "09_Debug_Operations:compare/live_swap", args, Severity.DESTRUCTIVE
    )
    if gate is not None and gate.get("success") is False:
        return gate
    result = run_live_swap(spec, build_env())
    if isinstance(result, dict):
        merge_gate_info(result, gate)
    return result
