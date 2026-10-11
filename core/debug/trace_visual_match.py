"""Map captured live-trace queries to the PBIR visuals that sent them (F-02).

Two evidence tiers, strongest first:

1. **ApplicationContext** (``method: application_context``): when the trace event carries the
   engine's ``{"Sources":[{"ReportId":..,"VisualId":..}]}`` blob, the visual id is read straight
   off it. Whether Desktop fills that column locally is UNVERIFIED (DAX Studio 3.6.0 only proves
   the column is subscribable), so nothing here requires it.
2. **Query shape** (``method: query_shape``): the captured DAX's group-by columns, measure /
   value-field set and filter tables are compared with the query reconstructed from each PBIR
   visual (``VisualQueryBuilder``). The score is a weighted Jaccard; the result carries a
   ``confidence`` and the runner-up candidates. A tie between DIFFERENT visuals is never
   auto-picked (``ambiguous: true``, ``match: null``): two identical cards on two pages look the
   same to the engine.

Pure + lexer-driven (``core.debug.query_shape_diff.query_shape``); the only model access is the
builder the caller passes in.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

#: Component weights of the shape score (sum 1.0).
W_GROUP_BY, W_MEASURES, W_FILTERS = 0.50, 0.35, 0.15
#: Below this the best candidate is reported but never assigned.
MIN_ASSIGN_SCORE = 0.60
#: Two DIFFERENT visuals this close are a tie: nothing is auto-picked.
TIE_MARGIN = 0.02
#: Cap on visuals reconstructed per call (each needs a query build).
MAX_INDEXED_VISUALS = 400

_ID_RE = re.compile(r"""["']?VisualId["']?\s*[:=]\s*["']?([A-Za-z0-9_-]{4,64})""", re.IGNORECASE)
_REPORT_RE = re.compile(
    r"""["']?ReportId["']?\s*[:=]\s*["']?([A-Za-z0-9_-]{4,64})""", re.IGNORECASE
)
#: DAX bookkeeping columns every visual query carries; they say nothing about WHICH visual.
_NOISE_REF = re.compile(r"^(isgrandtotal|issubtotal)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Tier 1: ApplicationContext
# ---------------------------------------------------------------------------

#: Event keys that may carry the engine's application-context / request-properties blob.
_CONTEXT_KEYS = ("application_context", "request_properties", "request_parameters")


def application_context_ref(events: Iterable[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """``{"visual_id", "report_id"?}`` from the first event whose context blob names a visual."""
    for ev in events:
        for key in _CONTEXT_KEYS:
            raw = ev.get(key)
            if raw is None:
                continue
            text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
            m = _ID_RE.search(text)
            if m:
                out = {"visual_id": m.group(1)}
                r = _REPORT_RE.search(text)
                if r:
                    out["report_id"] = r.group(1)
                return out
    return None


# ---------------------------------------------------------------------------
# Tier 2: query shape
# ---------------------------------------------------------------------------


def _table_of(ref: str) -> str:
    """``'Sales'[Amount]`` -> ``sales`` (casefolded table part)."""
    from core.dax.dax_names import split_qualified

    table, _col = split_qualified(ref)
    return (table or "").strip("'").casefold()


def shape_signature(dax: str) -> Dict[str, Set[str]]:
    """``{group_by, measures, filter_tables}`` of a DAX query (all casefolded sets).

    ``measures`` = unqualified ``[Name]`` references plus qualified references that are neither a
    group-by nor a filter column (measures written as ``'T'[M]`` and aggregated value fields).
    """
    from core.debug.query_shape_diff import _tokens, query_shape
    from core.dax.tokenizer.tokens import TokenType

    shape = query_shape(dax)
    group_by = {g.casefold() for g in shape["group_by"]}
    filter_cols = {f.casefold() for f in shape["filter_columns"]}
    value_fields = {f.casefold() for f in shape["fields"]} - group_by - filter_cols
    measures: Set[str] = set(value_fields)
    _lexer, tokens = _tokens(dax)
    prev = None
    for tok in tokens:
        if tok.type in (
            TokenType.WHITESPACE,
            TokenType.NEWLINE,
            TokenType.COMMENT_LINE,
            TokenType.COMMENT_BLOCK,
        ):
            continue
        if tok.type == TokenType.COLUMN_REF:
            name = tok.value.strip("[]").casefold()
            via_var = prev is not None and prev.type in (TokenType.IDENTIFIER, TokenType.TABLE_REF)
            if name and not via_var and not _NOISE_REF.match(name):
                measures.add(f"[{name}]")
        prev = tok
    return {
        "group_by": group_by,
        "measures": measures,
        "filter_tables": {_table_of(f) for f in filter_cols if _table_of(f)},
    }


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def similarity(captured: Dict[str, Set[str]], visual: Dict[str, Set[str]]) -> Dict[str, Any]:
    """Weighted shape similarity in [0, 1] with its three components."""
    if not (captured["group_by"] or captured["measures"]) or not (
        visual["group_by"] or visual["measures"]
    ):
        return {"score": 0.0, "group_by": 0.0, "measures": 0.0, "filters": 0.0}
    gb = _jaccard(captured["group_by"], visual["group_by"])
    ms = _jaccard(captured["measures"], visual["measures"])
    fl = _jaccard(captured["filter_tables"], visual["filter_tables"])
    return {
        "score": round(W_GROUP_BY * gb + W_MEASURES * ms + W_FILTERS * fl, 4),
        "group_by": round(gb, 3),
        "measures": round(ms, 3),
        "filters": round(fl, 3),
    }


def build_visual_index(
    builder: Any,
    *,
    page_name: Optional[str] = None,
    include_slicers: bool = True,
    limit: int = MAX_INDEXED_VISUALS,
) -> Dict[str, Any]:
    """Reconstruct a query for every visual (of ``page_name`` or of all pages) and sign it."""
    pages = [page_name] if page_name else [p.get("name") for p in builder.list_pages()]
    visuals: List[Dict[str, Any]] = []
    unbuildable: List[Dict[str, str]] = []
    truncated = False
    for page in pages:
        for v in builder.list_visuals(page):
            if len(visuals) >= limit:
                truncated = True
                break
            vid = v.get("id")
            try:
                built = builder.build_visual_query(
                    page_name=page, visual_id=vid, include_slicers=include_slicers
                )
            except Exception as exc:  # noqa: BLE001 - one bad visual must not stop the index
                unbuildable.append(
                    {"visual_id": str(vid), "reason": f"{type(exc).__name__}: {exc}"[:200]}
                )
                continue
            dax = getattr(built, "dax_query", None) if built else None
            if not dax:
                unbuildable.append({"visual_id": str(vid), "reason": "no query could be built"})
                continue
            visuals.append(
                {
                    "visual_id": str(vid),
                    "page": page,
                    "visual_name": v.get("friendly_name") or vid,
                    "visual_type": v.get("type"),
                    "query": dax,
                    "signature": shape_signature(dax),
                }
            )
        if truncated:
            break
    return {"visuals": visuals, "unbuildable": unbuildable, "truncated": truncated}


def _confidence(score: float, margin: float) -> str:
    if score >= 0.95 and margin >= 0.10:
        return "high"
    if score >= 0.80 and margin >= 0.05:
        return "medium"
    return "low"


def _candidate(v: Dict[str, Any], sim: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "visual_id": v["visual_id"],
        "page": v["page"],
        "visual_name": v["visual_name"],
        "visual_type": v.get("visual_type"),
        "score": sim["score"],
    }


def match_query(
    query: Dict[str, Any],
    index: List[Dict[str, Any]],
    *,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """Match ONE captured query row. ``text`` = its full DAX (rows only carry a preview)."""
    by_id = {v["visual_id"]: v for v in index}
    ref = query.get("application_context") or None
    dax = text if text is not None else (query.get("query") or "")
    sig = shape_signature(dax) if dax else None
    ranked: List[tuple] = []
    if sig is not None:
        for v in index:
            ranked.append((similarity(sig, v["signature"]), v))
        ranked.sort(key=lambda t: -t[0]["score"])
    out: Dict[str, Any] = {"seq": query.get("seq"), "request_id": query.get("request_id")}
    if ref and ref.get("visual_id") in by_id:
        v = by_id[ref["visual_id"]]
        sim = next((s for s, c in ranked if c is v), None)
        out["match"] = {
            **_candidate(v, sim or {"score": 1.0}),
            "score": 1.0,
            "confidence": "exact",
            "method": "application_context",
        }
        if sim is not None:
            out["match"]["shape_score"] = sim["score"]
        out["ambiguous"] = False
        return out
    if ref:
        out["application_context_unresolved"] = ref  # names a visual this report does not hold
    if not ranked:
        out.update(
            match=None, ambiguous=False, reason="no_query_text" if not dax else "empty_index"
        )
        return out
    best_sim, best = ranked[0]
    second = ranked[1][0]["score"] if len(ranked) > 1 else 0.0
    margin = round(best_sim["score"] - second, 4)
    out["candidates"] = [_candidate(c, s) for s, c in ranked[:3]]
    if best_sim["score"] < MIN_ASSIGN_SCORE:
        out.update(
            match=None, ambiguous=False, reason="below_threshold", best_score=best_sim["score"]
        )
        return out
    tied = [c for s, c in ranked if best_sim["score"] - s["score"] < TIE_MARGIN]
    if len(tied) > 1:
        out.update(
            match=None,
            ambiguous=True,
            reason="tie_between_visuals",
            best_score=best_sim["score"],
            tied=[f"{c['page']}/{c['visual_id']}" for c in tied][:10],
        )
        return out
    out["match"] = {
        **_candidate(best, best_sim),
        "confidence": _confidence(best_sim["score"], margin),
        "method": "query_shape",
        "margin": margin,
        "components": {k: best_sim[k] for k in ("group_by", "measures", "filters")},
    }
    out["ambiguous"] = False
    return out


def match_queries(
    queries: List[Dict[str, Any]],
    index: List[Dict[str, Any]],
    *,
    text_of: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Match every captured query row; adds a per-visual roll-up (count, total / max ms)."""
    results: List[Dict[str, Any]] = []
    by_visual: Dict[str, Dict[str, Any]] = {}
    matched = ambiguous = 0
    for q in queries:
        r = match_query(q, index, text=text_of(q) if text_of else None)
        results.append(r)
        m = r.get("match")
        if r.get("ambiguous"):
            ambiguous += 1
        if not m:
            continue
        matched += 1
        slot = by_visual.setdefault(
            m["visual_id"],
            {
                "visual_id": m["visual_id"],
                "page": m["page"],
                "visual_name": m["visual_name"],
                "queries": 0,
                "total_ms": 0.0,
                "max_ms": 0.0,
                "seqs": [],
            },
        )
        ms = float(q.get("total_ms") or 0)
        slot["queries"] += 1
        slot["total_ms"] = round(slot["total_ms"] + ms, 1)
        slot["max_ms"] = max(slot["max_ms"], ms)
        slot["seqs"].append(q.get("seq"))
    return {
        "matches": results,
        "summary": {
            "queries": len(queries),
            "matched": matched,
            "ambiguous": ambiguous,
            "unmatched": len(queries) - matched - ambiguous,
        },
        "by_visual": sorted(by_visual.values(), key=lambda s: -s["total_ms"]),
    }


# ---------------------------------------------------------------------------
# Glue: annotate a trace_fetch / trace_stop result; pick the capture of ONE visual
# ---------------------------------------------------------------------------


def annotate_trace_result(
    result: Dict[str, Any],
    builder: Any,
    *,
    page_name: Optional[str] = None,
    include_slicers: bool = True,
) -> Dict[str, Any]:
    """Add ``visual_match`` to every query row and a ``visual_matches`` roll-up (``match=visuals``).

    Rows must carry the private ``_text`` (``live_trace.fetch(keep_text=True)``); it is removed here.
    """
    queries = result.get("queries") or []
    idx = build_visual_index(builder, page_name=page_name, include_slicers=include_slicers)
    matched = match_queries(queries, idx["visuals"], text_of=lambda q: q.get("_text"))
    for q, m in zip(queries, matched["matches"]):
        q["visual_match"] = {k: v for k, v in m.items() if k not in ("seq", "request_id")}
        q.pop("_text", None)
    result["visual_matches"] = {
        **matched["summary"],
        "application_context_seen": sum(1 for q in queries if q.get("application_context")),
        "visuals_indexed": len(idx["visuals"]),
        "visuals_unbuildable": len(idx["unbuildable"]),
        "index_truncated": idx["truncated"],
        "by_visual": matched["by_visual"],
        "note": (
            "match_confidence: exact = ApplicationContext VisualId; high/medium/low = query-shape "
            "similarity (group-by, measures, filter tables) against each PBIR visual's reconstructed "
            "query. A tie between different visuals is reported as ambiguous and never auto-picked."
        ),
    }
    return result


#: Confidence tiers ``from_trace`` accepts as THE visual's query.
_FROM_TRACE_OK = ("exact", "high", "medium")


def capture_for_visual(
    result: Dict[str, Any],
    builder: Any,
    *,
    page_name: str,
    visual_id: str,
) -> Dict[str, Any]:
    """The latest captured query that matches ``visual_id`` well enough to replace the
    reconstruction (``09_Debug_Operations visual from_trace=true``).

    ``{"found": True, "query", "seq", "request_id", "match", "total_ms"}`` or
    ``{"found": False, "reason", "candidates"}``. Indexes only ``page_name``'s visuals: a query that
    resembles a visual on another page is not evidence about this one.
    """
    queries = result.get("queries") or []
    idx = build_visual_index(builder, page_name=page_name)
    matched = match_queries(queries, idx["visuals"], text_of=lambda q: q.get("_text"))
    mine: List[tuple] = []
    for q, m in zip(queries, matched["matches"]):
        mm = m.get("match")
        if mm and mm["visual_id"] == str(visual_id) and mm["confidence"] in _FROM_TRACE_OK:
            mine.append((q.get("seq") or 0, q, mm))
    if mine:
        mine.sort(key=lambda t: t[0])
        _seq, q, mm = mine[-1]
        return {
            "found": True,
            "query": q.get("_text") or "",
            "seq": q.get("seq"),
            "request_id": q.get("request_id"),
            "total_ms": q.get("total_ms"),
            "match": mm,
            "captures_for_visual": len(mine),
        }
    candidates = [
        {
            "seq": q.get("seq"),
            **{k: m.get(k) for k in ("reason", "best_score", "tied")},
            "candidates": m.get("candidates"),
        }
        for q, m in zip(queries, matched["matches"])
        if any(c["visual_id"] == str(visual_id) for c in (m.get("candidates") or []))
    ][:5]
    reason = "no captured query matches this visual"
    if not queries:
        reason = "the live trace has captured no queries yet (interact with the visual / switch to its page, then retry)"
    elif any(m.get("ambiguous") for m in matched["matches"]):
        reason = "the closest captured queries are ambiguous between visuals (identical shape): nothing auto-picked"
    return {
        "found": False,
        "reason": reason,
        "candidates": candidates,
        "queries_seen": len(queries),
    }
