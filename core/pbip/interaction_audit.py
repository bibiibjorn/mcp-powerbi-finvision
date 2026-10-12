"""Slicer reach / NoFilter audit (F-12) -- read-only, offline.

For every slicer on every page: which data visuals its selection can reach through the
model, which visuals declare ``NoFilter``, which declared rules sit on a pair the slicer
can or cannot affect, which isolations a caller's ``policy`` asks for that are missing,
and the plan a UI suite needs (what to click, what must / must not change).

Ported from ``~/.claude/skills/pbi-perf-workflow/scripts/slicer_interactions.py`` (and the
logic of ``pbi-ui-test/scripts/slicer_suite_plan.py``) so it runs inside the server on the
graph instead of three exported JSON files:

* ``reach(S)``  = the slicer's source tables + every table a filter on them propagates to over
  relationships (one -> many; both ways for 1:1, many:many and bi-directional; INACTIVE
  relationships included because a measure can ``USERELATIONSHIP`` them);
* ``read(visual)`` = tables of the columns it projects / formats by + for every measure shown:
  the tables its value DAX reads (the dependency graph: measure and UDF bodies, transitively,
  including bare ``Table`` arguments passed to UDFs that ``CALCDEPENDENCY`` misses) and its
  own dynamic format string's reads; a filter-only measure contributes its value reads; a
  field-parameter table expands to its candidate columns / measures; a calculation-group
  table to its calculation items' reads;
* ``needed`` = ``read(visual) & reach(S)`` is non-empty, or the slicer sits on a calculation
  group and the visual shows a measure.

Semantics (identical to the script): a NoFilter rule on a pair the slicer CANNOT affect is
the *perf-correct* rule (no pointless re-query; ``no_effect``); a NoFilter rule on a pair it
CAN affect is an ISOLATION (``blocks_dependency``): intentional only when the policy asks for
it, otherwise it is reported as ``wrong`` (the visual silently ignores a slicer it depends on).
Rules are never auto-removed here; ``suggested`` lists additions and dangling rules only and
puts the doubtful isolations under ``review``.

CAVEAT: DAX that neutralises a filter (``REMOVEFILTERS`` / ``ALL``) is not modelled; such a
pair is reported as reached (conservative: a needed rule is never called redundant).
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from core.graph.builder import ModelIndex, build_model_graph
from core.graph.models import Graph
from core.graph.report_source import iter_field_refs
from core.pbip import pbir_model

logger = logging.getLogger(__name__)

_NAMEOF_RE = re.compile(r"NAMEOF\s*\(\s*'((?:[^']|'')+)'\s*\[([^\]]+)\]\s*\)", re.I)
_NAMEOF_BARE_RE = re.compile(r"NAMEOF\s*\(\s*([A-Za-z_]\w*)\s*\[([^\]]+)\]\s*\)", re.I)
_FILTERING_TYPES = {None, "", "Filter", "DataFilter", "Highlight", "HighlightFilter"}


def _cf(s: Any) -> str:
    return " ".join(str(s).split()).casefold() if s is not None else ""


def _card(v: Any) -> Optional[str]:
    t = str(v).strip().lower() if v is not None else ""
    if t in ("1", "one"):
        return "one"
    if t in ("2", "many"):
        return "many"
    return None


def _is_both(v: Any) -> bool:
    return str(v).strip().lower() in ("2", "both", "bothdirections", "both_directions")


# ---------------------------------------------------------------------------
# model view (graph + relationships)
# ---------------------------------------------------------------------------


class ModelView:
    """Filter propagation + per-measure table reads over the dependency graph."""

    def __init__(self, snapshot: Any) -> None:
        self.snap = snapshot
        self.index = ModelIndex(snapshot)
        self.graph: Graph = build_model_graph(snapshot)
        self.tables: Dict[str, str] = {_cf(t): t for t in snapshot.tables}
        self.down: Dict[str, Set[str]] = defaultdict(set)
        for rel in snapshot.relationships:
            f, t = _cf(rel["from_table"]), _cf(rel["to_table"])
            fc = _card(rel.get("from_card")) or "many"
            tc = _card(rel.get("to_card")) or "one"
            if tc == "one" and fc != "one":
                self.down[t].add(f)
            elif fc == "one" and tc != "one":
                self.down[f].add(t)
            else:
                self.down[t].add(f)
                self.down[f].add(t)
            if _is_both(rel.get("cross_filter")):
                self.down[t].add(f)
                self.down[f].add(t)
        # field-parameter candidates: NAMEOF('T'[c])
        self.fp: Dict[str, List[Tuple[str, str]]] = {}
        for tname, info in snapshot.tables.items():
            if info.get("field_parameter") and info.get("expression"):
                expr = str(info["expression"])
                cands = [(_cf(a.replace("''", "'")), _cf(b)) for a, b in _NAMEOF_RE.findall(expr)]
                cands += [(_cf(a), _cf(b)) for a, b in _NAMEOF_BARE_RE.findall(expr)]
                self.fp[_cf(tname)] = cands
        self.calc_groups: Set[str] = {
            _cf(t) for t, i in snapshot.tables.items() if i.get("calc_group")
        }
        self._measure_home: Dict[str, str] = {}
        for m in snapshot.measures:
            self._measure_home.setdefault(_cf(m["name"]), _cf(m["table"]))
        self._reads: Dict[Tuple[str, bool], Set[str]] = {}
        self._reach: Dict[frozenset, Set[str]] = {}

    # -- reads ---------------------------------------------------------------
    def _node_tables(self, dst: str) -> Optional[str]:
        kind, _, rest = dst.partition(":")
        if kind == "table":
            return _cf(rest)
        if kind in ("column", "hierarchy") and "[" in rest:
            return _cf(rest.rsplit("[", 1)[0])
        return None

    def reads_of_node(self, node_id: str, include_format_string: bool = True) -> Set[str]:
        key = (node_id, include_format_string)
        if key in self._reads:
            return self._reads[key]
        out: Set[str] = set()
        seen = {node_id}
        stack = [(node_id, True)]
        while stack:
            cur, top = stack.pop()
            for e in self.graph.outgoing(cur):
                if e.kind not in ("references", "calls"):
                    continue
                via = (e.attrs or {}).get("via")
                if via == "detail_rows":
                    continue
                if via == "format_string" and not (top and include_format_string):
                    continue
                t = self._node_tables(e.dst)
                if t:
                    out.add(t)
                elif e.dst not in seen and e.dst.split(":", 1)[0] in (
                    "measure",
                    "function",
                    "calc_group_item",
                ):
                    seen.add(e.dst)
                    stack.append((e.dst, False))
        self._reads[key] = out
        return out

    def measure_reads(
        self, name: str, table: Optional[str] = None, include_fs: bool = True
    ) -> Set[str]:
        mid = self.index.measure_id(name, table) or self.index.measure_id(name)
        return self.reads_of_node(mid, include_fs) if mid else set()

    def is_measure(self, table_cf: str, name_cf: str) -> bool:
        return self._measure_home.get(name_cf) == table_cf

    def expand(self, table_cf: str) -> Set[str]:
        out = {table_cf}
        for tt, c in self.fp.get(table_cf, ()):
            out.add(tt)
            if self.is_measure(tt, c):
                out |= self.measure_reads(c, None, True)
        if table_cf in self.calc_groups:
            for ci in self.snap.calc_items:
                if _cf(ci["table"]) == table_cf:
                    out |= self.reads_of_node(f"calc_group_item:{ci['table']}[{ci['name']}]", True)
        return out

    def reach(self, tables: Iterable[str]) -> Set[str]:
        key = frozenset(tables)
        if key not in self._reach:
            seen, todo = set(key), list(key)
            while todo:
                for n in self.down.get(todo.pop(), ()):
                    if n not in seen:
                        seen.add(n)
                        todo.append(n)
            self._reach[key] = seen
        return self._reach[key]


# ---------------------------------------------------------------------------
# visual facts
# ---------------------------------------------------------------------------


def _title_of(data: Dict[str, Any]) -> Optional[str]:
    visual = data.get("visual") if isinstance(data.get("visual"), dict) else {}
    for e in (visual.get("visualContainerObjects") or {}).get("title") or []:
        v = (
            ((((e or {}).get("properties") or {}).get("text") or {}).get("expr") or {})
            .get("Literal", {})
            .get("Value")
        )
        if isinstance(v, str) and v:
            return v.strip("'")
    return None


def _refs(node: Any) -> List[Tuple[str, str, str]]:
    return [ref for ref, _lvl in iter_field_refs(node)]


def _visual_facts(mv: Optional[ModelView], data: Dict[str, Any]) -> Dict[str, Any]:
    visual = data.get("visual") if isinstance(data.get("visual"), dict) else {}
    vtype = visual.get("visualType") or ""
    qs = ((visual.get("query") or {}).get("queryState")) or {}
    has_query = any(isinstance(r, dict) and r.get("projections") for r in qs.values())
    body = {k: v for k, v in data.items() if k != "filterConfig"}
    facts: Dict[str, Any] = {
        "type": vtype,
        "title": _title_of(data),
        "hidden": bool(data.get("isHidden")),
        "has_query": has_query,
        "is_slicer": "licer" in vtype,
        "src": set(),
        "read": set(),
        "has_measure": False,
        "kind": None,
        "field": None,
    }
    refs = _refs(body)
    cols = [(t, n) for k, t, n in refs if k in ("column", "hierarchy")]
    measures = [(t, n) for k, t, n in refs if k == "measure"]
    facts["has_measure"] = bool(measures)
    facts["bound"] = sorted({f"{t}[{n}]" for t, n in cols} | {f"{t}[{n}]" for t, n in measures})
    if mv is None:
        return facts
    read: Set[str] = {_cf(t) for t, _n in cols}
    for t, n in measures:
        read |= mv.measure_reads(n, t, True)
    for flt in ((data.get("filterConfig") or {}).get("filters")) or []:
        if not isinstance(flt, dict) or not flt.get("filter"):
            continue  # an unconditioned filter field reads nothing
        for k, t, n in _refs(flt):
            if k in ("column", "hierarchy"):
                read.add(_cf(t))
            elif k == "measure":
                read |= mv.measure_reads(n, t, False)
    for t in list(read):
        read |= mv.expand(t)
    facts["read"] = read
    if facts["is_slicer"]:
        src: Set[str] = set()
        kind = None
        for role in qs.values():
            if not isinstance(role, dict):
                continue
            for fp in role.get("fieldParameters") or []:
                ent = (
                    (
                        (((fp or {}).get("parameterExpr") or {}).get("Column") or {}).get(
                            "Expression"
                        )
                        or {}
                    ).get("SourceRef")
                    or {}
                ).get("Entity")
                if ent:
                    kind = "data (field-parameter driven)"
                    for tt, c in mv.fp.get(_cf(ent), ()):
                        if not mv.is_measure(tt, c):
                            src.add(tt)
            for pr in role.get("projections") or []:
                for k, t, _n in _refs((pr or {}).get("field")):
                    if k in ("column", "hierarchy"):
                        src.add(_cf(t))
        if kind is None and src:
            kind = (
                "field-parameter picker"
                if src <= set(mv.fp)
                else (
                    "calculation group"
                    if src & mv.calc_groups
                    else (
                        "parameter (disconnected table)"
                        if not (mv.reach(src) - src - set(mv.fp))
                        else "data (dimension)"
                    )
                )
            )
        facts["src"], facts["kind"] = src, kind
        fields = sorted({f"{t}[{n}]" for k, t, n in _refs(qs) if k in ("column", "hierarchy")})
        facts["field"] = ", ".join(fields[:3]) or None
    return facts


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------


def _selector_matches(sel: Any, vid: str, facts: Dict[str, Any]) -> bool:
    if sel in (None, "*"):
        return True
    s = str(sel)
    if s.lower().startswith("type:"):
        return _cf(facts.get("type")) == _cf(s[5:])
    return s == vid or (facts.get("title") is not None and _cf(facts["title"]) == _cf(s))


def _policy_pairs(policy: Optional[Dict[str, Any]]) -> List[Tuple[Any, Any]]:
    """Normalise a policy to ``(slicer selector, visual selector)`` isolation pairs."""
    out: List[Tuple[Any, Any]] = []
    if not isinstance(policy, dict):
        return out
    for p in policy.get("isolate") or []:
        if isinstance(p, dict):
            out.append((p.get("slicer", "*"), p.get("visual", "*")))
    for v in policy.get("isolate_visuals") or []:
        out.append(("*", v))
    for t in policy.get("isolate_visual_types") or []:
        out.append(("*", f"type:{t}"))
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _with_extensions(snapshot: Any, definition_dir: Path) -> Any:
    """Copy of *snapshot* whose measures include the report-level (extension) measures."""
    ext_path = definition_dir / "reportExtensions.json"
    if not ext_path.is_file():
        return snapshot
    try:
        data = json.loads(ext_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return snapshot
    import copy

    snap = copy.copy(snapshot)
    snap.measures = list(snapshot.measures)
    snap.tables = dict(snapshot.tables)
    for ent in data.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        tname = str(ent.get("name") or "")
        for m in ent.get("measures") or []:
            if isinstance(m, dict) and m.get("name"):
                snap.tables.setdefault(
                    tname,
                    {
                        "expression": None,
                        "calculated": False,
                        "field_parameter": False,
                        "calc_group": False,
                        "detail_rows": None,
                    },
                )
                snap.measures.append(
                    {
                        "table": tname,
                        "name": str(m["name"]),
                        "expression": str(m.get("expression") or ""),
                        "format_string_expr": None,
                        "detail_rows_expr": None,
                    }
                )
    return snap


def audit_interactions(
    definition_dir: Any,
    snapshot: Any = None,
    *,
    page: Optional[str] = None,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Slicer reach / NoFilter audit of one report definition. Never writes.

    ``snapshot`` = ``core.graph.model_source.ModelSnapshot`` of the bound model (live INFO.* or
    offline TMDL via ``snapshot_from_model_data``). Without it only the declared rules and the
    dangling check run (``model_available: false``; no reach claims are made).
    """
    from core.pbip.authoring.targets import load_page_visuals, resolve_page_dir

    ddir = Path(definition_dir)
    pages_dir = ddir / "pages"
    if not pages_dir.is_dir():
        return {"success": False, "error_type": "not_found", "error": f"no pages/ under {ddir}"}
    if page:
        pdir, err = resolve_page_dir(ddir, page)
        if err:
            return err
        page_dirs = [pdir]
    else:
        page_dirs = sorted(p for p in pages_dir.iterdir() if p.is_dir())

    mv: Optional[ModelView] = None
    if snapshot is not None:
        mv = ModelView(_with_extensions(snapshot, ddir))
    isolate = _policy_pairs(policy)

    out_pages: List[Dict[str, Any]] = []
    totals = defaultdict(int)
    for pdir in page_dirs:
        pjson = pbir_model._load_json_quiet(pdir / "page.json") or {}
        vmap = load_page_visuals(pdir)
        facts = {vid: _visual_facts(mv, d) for vid, d in vmap.items() if "visual" in d}
        rules: Dict[Tuple[str, str], str] = {}
        dangling: List[Dict[str, Any]] = []
        for it in pbir_model.get_page_interactions(pjson):
            s, t, ty = it.get("source"), it.get("target"), it.get("type")
            if s not in vmap or t not in vmap:
                dangling.append({"source": s, "target": t, "type": ty})
                continue
            rules[(s, t)] = ty or ""
        slicers = []
        for sid, sf in sorted(facts.items()):
            if not sf["is_slicer"]:
                continue
            entry: Dict[str, Any] = {
                "slicer": sid,
                "title": sf["title"],
                "type": sf["type"],
                "field": sf["field"],
                "kind": sf["kind"],
                "needed": [],
                "no_effect": [],
                "no_filter_rules": [],
                "wrong_no_filter": [],
                "redundant_no_filter": [],
                "missing_no_filter": [],
                "missing_isolation": [],
                "review": [],
            }
            reach: Set[str] = set()
            if mv is not None and sf["src"]:
                reach = mv.reach(sf["src"])
                entry["reach_tables"] = sorted(mv.tables.get(t, t) for t in reach)[:50]
            elif mv is not None:
                entry["note"] = "slicer projects no model column; reach unknown"
            for tid, tf in sorted(facts.items()):
                if tid == sid or not tf["has_query"]:
                    continue
                rule = rules.get((sid, tid))
                declared_nf = rule == "NoFilter"
                policy_hit = any(
                    _selector_matches(ps, sid, sf) and _selector_matches(pv, tid, tf)
                    for ps, pv in isolate
                )
                brief = {"visual": tid, "title": tf["title"], "type": tf["type"]}
                if mv is None or not sf["src"]:
                    needed: Optional[bool] = None
                else:
                    hit = tf["read"] & reach
                    cg = sf["src"] & mv.calc_groups
                    if cg and tf["has_measure"]:
                        hit = hit | cg
                    needed = bool(hit)
                if needed is True:
                    entry["needed"].append(
                        {
                            **brief,
                            "via": sorted(mv.tables.get(t, t) for t in hit)[:8],
                            "rule": rule or "default",
                        }
                    )
                elif needed is False:
                    entry["no_effect"].append({**brief, "rule": rule or "default"})
                if declared_nf:
                    verdict = (
                        "unknown"
                        if needed is None
                        else (
                            "no_effect"
                            if not needed
                            else "intentional_isolation" if policy_hit else "blocks_dependency"
                        )
                    )
                    entry["no_filter_rules"].append({**brief, "verdict": verdict})
                    if verdict == "no_effect":
                        entry["redundant_no_filter"].append(tid)
                    elif verdict == "blocks_dependency":
                        entry["wrong_no_filter"].append(
                            {
                                **brief,
                                "reason": "the slicer can reach this visual (via "
                                + ", ".join(sorted(mv.tables.get(t, t) for t in hit)[:4])
                                + ") but NoFilter blocks it and no policy asks for the isolation",
                            }
                        )
                        entry["review"].append(tid)
                elif needed is False and rule in _FILTERING_TYPES and not policy_hit:
                    entry["missing_no_filter"].append(
                        {
                            **brief,
                            "reason": "slicer cannot affect it: "
                            "NoFilter avoids a pointless re-query",
                        }
                    )
                if policy_hit and not declared_nf and needed is not False:
                    entry["missing_isolation"].append(
                        {
                            **brief,
                            "reason": "policy isolates this visual from "
                            "the slicer but no NoFilter rule exists",
                        }
                    )
            slicers.append(entry)
            totals["slicers"] += 1
            totals["needed_pairs"] += len(entry["needed"])
            totals["no_effect_pairs"] += len(entry["no_effect"])
            totals["no_filter_rules"] += len(entry["no_filter_rules"])
            totals["wrong_no_filter"] += len(entry["wrong_no_filter"])
            totals["redundant_no_filter"] += len(entry["redundant_no_filter"])
            totals["missing_no_filter"] += len(entry["missing_no_filter"])
            totals["missing_isolation"] += len(entry["missing_isolation"])
        # suggested complete NoFilter set for the page (additions + dangling removals only)
        add = [
            {"source": e["slicer"], "target": m["visual"], "type": "NoFilter", "why": "no_effect"}
            for e in slicers
            for m in e["missing_no_filter"]
        ]
        add += [
            {
                "source": e["slicer"],
                "target": m["visual"],
                "type": "NoFilter",
                "why": "policy_isolation",
            }
            for e in slicers
            for m in e["missing_isolation"]
        ]
        page_entry = {
            "page_id": pdir.name,
            "page_name": pjson.get("displayName"),
            "visuals": len(vmap),
            "slicers": slicers,
            "dangling_interactions": dangling,
            "suggested": {
                "add_no_filter": add,
                "remove_dangling": dangling,
                "review_rules": [
                    {"source": e["slicer"], "target": t} for e in slicers for t in e["review"]
                ],
            },
            "suite_plan": build_invariance_plan_for_page(pdir.name, slicers),
        }
        totals["dangling"] += len(dangling)
        out_pages.append(page_entry)

    return {
        "success": True,
        "read_only": True,
        "model_available": mv is not None,
        "summary": dict(totals),
        "pages": out_pages,
        "policy_applied": bool(isolate),
        "semantics": {
            "no_effect": "NoFilter on a pair the slicer cannot affect: perf-correct (slicer_interactions.py)",
            "blocks_dependency": "NoFilter on a pair the slicer CAN affect: the visual ignores a slicer it "
            "depends on - wrong unless the policy asks for the isolation",
            "caveat": "REMOVEFILTERS/ALL neutralisation is not modelled (such pairs count as reached)",
        },
    }


def build_invariance_plan_for_page(
    page_id: str, slicers: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """The UI-suite plan of one page: what each slicer click must / must not change."""
    plan: List[Dict[str, Any]] = []
    for e in slicers:
        must_change = [
            {"visual": n["visual"], "title": n["title"], "type": n["type"]}
            for n in e["needed"]
            if n.get("rule") != "NoFilter"
        ]
        must_not = [
            {
                "visual": n["visual"],
                "title": n["title"],
                "type": n["type"],
                "reason": "model_unreachable",
            }
            for n in e["no_effect"]
        ]
        must_not += [
            {
                "visual": r["visual"],
                "title": r["title"],
                "type": r["type"],
                "reason": "declared_isolation",
            }
            for r in e["no_filter_rules"]
            if r["verdict"] in ("blocks_dependency", "intentional_isolation")
        ]
        plan.append(
            {
                "page": page_id,
                "slicer": e["slicer"],
                "slicer_title": e["title"],
                "field": e["field"],
                "kind": e["kind"],
                "click": {
                    "visual": e["slicer"],
                    "action": "select one value (not the saved default)",
                },
                "must_change": must_change,
                "must_not_change": must_not,
            }
        )
    return plan


def build_invariance_plan(audit: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten ``audit_interactions`` output into the invariance-test plan (09_Validate suite).

    One entry per slicer: ``must_not_change`` pairs return IDENTICAL results under different
    selections (NoFilter / model-unreachable); ``must_change`` are the positive controls.
    """
    return [row for p in audit.get("pages") or [] for row in p.get("suite_plan") or []]


__all__ = ["audit_interactions", "build_invariance_plan", "ModelView"]
