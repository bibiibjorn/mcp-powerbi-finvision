"""INTERNAL HELPER — Not a registered MCP tool.

Slicer pane-audit + bulk-rename operations consumed by
the visual / slicer operation handlers.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.pbip.authoring import safe_io
from core.pbip.pbir_model import encode_literal
from core.utilities.pbip_utils import SKIP_WRITE, load_json_file
from server.handlers._internal.slicer_helpers import _find_slicers

# Generic columns that don't tell you what dimension a slicer is bound to.
# When the dim's column is one of these, comparison falls back to the table
# name (with storage prefixes stripped).
_GENERIC_COLS = {"category", "value", "values", "label", "name", "id"}

# PBIP storage-prefix conventions used at Finvision.
_STORAGE_PREFIXES = ("sf ", "mf ", "s ", "d ", "f ")

_NON_ALPHANUM_RE = re.compile(r"[^a-z0-9]+")


def _strip_storage_prefix(table: str) -> str:
    lower = table.lower()
    for prefix in _STORAGE_PREFIXES:
        if lower.startswith(prefix):
            return table[len(prefix) :]
    return table


def _normalize(label: str) -> str:
    """Lowercase + remove non-alphanumeric for comparison."""
    return _NON_ALPHANUM_RE.sub("", label.lower())


def _expected_label_for(slicer: Dict[str, Any]) -> str:
    """The 'truthful' label this slicer SHOULD have, based on its binding."""
    prop = slicer.get("property", "") or ""
    entity = slicer.get("entity", "") or ""
    if _normalize(prop) in _GENERIC_COLS or not prop:
        return _strip_storage_prefix(entity)
    return prop


def _is_title_truthful(slicer: Dict[str, Any]) -> bool:
    title = slicer.get("title")
    if title is None:
        return True  # untitled isn't "misleading" per se
    expected = _expected_label_for(slicer)
    return _normalize(expected) in _normalize(title) or _normalize(title) in _normalize(expected)


def _op_audit_pane_collisions(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    """Audit each page for slicers sharing a pane title (collisions) and for
    titled slicers whose title doesn't match their bound dim (misleading).
    """
    page_filter = args.get("page_id") or args.get("page_name")
    include_no_title = args.get("include_no_title", True)

    slicers = _find_slicers(definition_path, None, None, None, page=page_filter, page_exact=False)

    by_page: Dict[str, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))
    for s in slicers:
        page = s.get("page_id") or s.get("page_name") or ""
        title = s.get("title")
        key = (title or "").strip().lower() if title else "(no title)"
        if key == "(no title)" and not include_no_title:
            continue
        by_page[page][key].append(s)

    collisions: List[Dict[str, Any]] = []
    misleading: List[Dict[str, Any]] = []
    label_counter: Dict[str, set] = defaultdict(set)
    pages_affected = set()

    for page, groups in by_page.items():
        for label, members in groups.items():
            if len(members) >= 2:
                collisions.append(
                    {
                        "page": page,
                        "label": label if label != "(no title)" else "(no title)",
                        "slicers": [
                            {
                                "visual_name": m["visual_name"],
                                "dim": f"{m['entity']}.{m['property']}",
                                "x": (m.get("position") or {}).get("x"),
                                "y": (m.get("position") or {}).get("y"),
                            }
                            for m in members
                        ],
                    }
                )
                label_counter[label].add(page)
                pages_affected.add(page)

    for s in slicers:
        if s.get("title") is None:
            continue
        if not _is_title_truthful(s):
            misleading.append(
                {
                    "page": s.get("page_name", ""),
                    "visual_name": s["visual_name"],
                    "title": s["title"],
                    "dim": f"{s['entity']}.{s['property']}",
                    "suggested_rename": _expected_label_for(s),
                }
            )
            pages_affected.add(s.get("page_name", ""))

    return {
        "success": True,
        "total_collisions": len(collisions),
        "total_misleading": len(misleading),
        "pages_affected": len(pages_affected),
        "recurring_collision_labels": [
            {"label": label, "pages": len(pages)}
            for label, pages in sorted(label_counter.items(), key=lambda kv: -len(kv[1]))
            if len(pages) >= 2
        ],
        "collisions": collisions,
        "misleading": misleading,
    }


def _key(s: Dict[str, Any]) -> Tuple[str, str]:
    """H-24b: slicers are identified by (page_id, visual_name) — 18 R0201 ids repeat per page."""
    return (s.get("page_id") or s.get("page_name") or "", s["visual_name"])


def _override_for(s: Dict[str, Any], custom_overrides: Dict[str, str]) -> Optional[str]:
    for k in (f"{s.get('page_id')}/{s['visual_name']}", f"{s.get('page_name')}/{s['visual_name']}"):
        if k in custom_overrides:
            return custom_overrides[k]
    return custom_overrides.get(s["visual_name"])


def _plan_renames_for_page(
    members: List[Dict],
    custom_overrides: Dict[str, str],
    strategy: str = "auto",
) -> List[Dict[str, Any]]:
    """Compute renames per page. Implements rules A (single misleading) and
    B (multi-member groups: 0-match / 1-match / N-match) plus positional
    fallback. Cross-group conflict resolution is layered on by the caller.

    ``strategy``: ``auto`` (default rules), ``positional_only`` (every
    collision group gets ``#N`` suffixes, titles are never replaced by the
    dim name), ``append_dim`` (a colliding or misleading title becomes
    ``"<title> (<dim label>)"``).
    """
    by_title: Dict[str, List[Dict]] = defaultdict(list)
    for s in members:
        key = (s.get("title") or "").strip().lower()
        by_title[key].append(s)

    def entry(s: Dict, new_title: str, rule: str) -> Dict[str, Any]:
        return {
            "page": s.get("page_name", ""),
            "page_id": s.get("page_id"),
            "visual_name": s["visual_name"],
            "current_title": s.get("title"),
            "new_title": new_title,
            "rule": rule,
            "dim": f"{s['entity']}.{s['property']}",
        }

    plan: List[Dict[str, Any]] = []
    for s in members:
        forced = _override_for(s, custom_overrides)
        if forced is not None:
            plan.append(entry(s, forced, "custom_override"))

    seen = {_key(p) for p in plan}

    for key, group in by_title.items():
        non_overridden = [m for m in group if _key(m) not in seen]
        if not non_overridden:
            continue
        if len(non_overridden) == 1:
            s = non_overridden[0]
            if s.get("title") is None or _is_title_truthful(s):
                continue
            if strategy == "positional_only":
                continue
            if strategy == "append_dim":
                plan.append(entry(s, f"{s['title']} ({_expected_label_for(s)})", "append_dim"))
            else:
                plan.append(entry(s, _expected_label_for(s), "rule_a_dim_only"))
            continue

        truthful = [m for m in non_overridden if _is_title_truthful(m)]
        n_match = len(truthful)
        ordered = sorted(
            non_overridden,
            key=lambda m: (
                (m.get("position") or {}).get("x") or 0,
                (m.get("position") or {}).get("y") or 0,
            ),
        )
        if strategy == "positional_only" or (strategy == "auto" and n_match > 1):
            base = (ordered[0].get("title") or _expected_label_for(ordered[0])).strip()
            for idx, m in enumerate(ordered, start=1):
                plan.append(entry(m, f"{base} #{idx}", "rule_b_positional"))
        elif strategy == "append_dim":
            for m in ordered:
                plan.append(entry(m, f"{m.get('title')} ({_expected_label_for(m)})", "append_dim"))
        elif n_match == 0:
            for m in non_overridden:
                plan.append(entry(m, _expected_label_for(m), "rule_a_dim_only"))
        else:  # n_match == 1
            keeper = truthful[0]
            for m in non_overridden:
                if _key(m) == _key(keeper):
                    continue
                plan.append(entry(m, _expected_label_for(m), "rule_a_keeper"))

    return plan


def _set_title(data: Dict[str, Any], new_title: str) -> Any:
    """Mutate visual.json: set title[0].properties.text (whatever the key order — H-24a)."""
    vco = data.setdefault("visual", {}).setdefault("visualContainerObjects", {})
    titles = vco.setdefault("title", [])
    if not titles:
        titles.append({"properties": {}})
    props = titles[0].setdefault("properties", {})
    new_lit = encode_literal(new_title, "string")
    current = ((props.get("text") or {}).get("expr") or {}).get("Literal", {}).get("Value")
    if current == new_lit:
        return SKIP_WRITE
    props["text"] = {"expr": {"Literal": {"Value": new_lit}}}
    return None


def _apply_rename(file_path: Path, new_title: str) -> str:
    """Apply a single rename, returning a status string.

    Returns one of: 'changed', 'already_correct', 'inserted', 'error'.
    Parse -> mutate -> serialise with the file's own byte traits (EOL, BOM,
    indent, raw UTF-8) — no regex that silently misses titles whose ``show``
    / ``titleWrap`` key precedes ``text`` (151 R0105 slicers, H-24a).
    """
    before = load_json_file(file_path) or {}
    had_title = bool(
        ((before.get("visual") or {}).get("visualContainerObjects") or {}).get("title")
    )
    try:
        changed = safe_io.update_json(file_path, lambda d: _set_title(d, new_title))
    except safe_io.WriteError:
        return "error"
    if not changed:
        return "already_correct"
    return "changed" if had_title else "inserted"


def _disambiguate_plan(
    plan: List[Dict[str, Any]],
    all_slicers: List[Dict],
    max_rounds: int = 3,
) -> List[Dict[str, Any]]:
    """After initial planning, simulate the resulting per-page label space
    and add positional #N suffixes to any remaining collisions. Up to
    ``max_rounds`` passes; in practice 1 pass resolves everything.

    Side-effect: entries that originally had no rename now get a
    'cross_group_disambiguated' rule and a #N suffix.
    """
    plan_by_id = {(p.get("page_id") or p.get("page") or "", p["visual_name"]): p for p in plan}

    for _round in range(max_rounds):
        per_page: Dict[str, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))
        for s in all_slicers:
            page = s.get("page_id") or s.get("page_name") or ""
            ent = plan_by_id.get(_key(s))
            final_title = ent["new_title"] if ent else s.get("title")
            if final_title is None:
                continue
            per_page[page][final_title.strip().lower()].append(s)

        changed = False
        for page, groups in per_page.items():
            for label, members in groups.items():
                if len(members) < 2:
                    continue
                ordered = sorted(
                    members,
                    key=lambda m: (
                        (m.get("position") or {}).get("x") or 0,
                        (m.get("position") or {}).get("y") or 0,
                        m["visual_name"],
                    ),
                )
                first_entry = plan_by_id.get(_key(ordered[0]))
                base = (
                    first_entry["new_title"] if first_entry else ordered[0].get("title", "")
                ).strip()
                for idx, m in enumerate(ordered, start=1):
                    new_title = f"{base} #{idx}"
                    existing = plan_by_id.get(_key(m))
                    if existing is not None:
                        if existing["new_title"] != new_title:
                            existing["new_title"] = new_title
                            changed = True
                    else:
                        plan_by_id[_key(m)] = {
                            "page": m.get("page_name", ""),
                            "page_id": m.get("page_id"),
                            "visual_name": m["visual_name"],
                            "current_title": m.get("title"),
                            "new_title": new_title,
                            "rule": "cross_group_disambiguated",
                            "dim": f"{m['entity']}.{m['property']}",
                        }
                        changed = True
        if not changed:
            break

    return list(plan_by_id.values())


_STRATEGIES = ("auto", "append_dim", "positional_only")


def _op_bulk_rename_pane_names(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    page_filter = args.get("page_id") or args.get("page_name")
    custom_overrides: Dict[str, str] = args.get("custom_overrides") or {}
    dry_run: bool = args.get("dry_run", False)
    strategy = args.get("strategy") or "auto"
    if strategy not in _STRATEGIES:
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": f"strategy must be one of {', '.join(_STRATEGIES)}",
        }

    slicers = _find_slicers(definition_path, None, None, None, page=page_filter, page_exact=False)

    # H-24b: a bare visual_name override that exists on several pages is ambiguous.
    ids_pages: Dict[str, List[str]] = defaultdict(list)
    for s in slicers:
        ids_pages[s["visual_name"]].append(s.get("page_id") or "")
    ambiguous = [k for k in custom_overrides if "/" not in k and len(ids_pages.get(k, [])) > 1]
    if ambiguous:
        return {
            "success": False,
            "error_type": "ambiguous_override",
            "error": (
                "custom_overrides keys exist on several pages; key them as "
                "'<page_id>/<visual_name>': " + ", ".join(ambiguous)
            ),
            "candidates": {k: ids_pages[k] for k in ambiguous},
        }

    by_page: Dict[str, List[Dict]] = defaultdict(list)
    for s in slicers:
        by_page[s.get("page_id") or s.get("page_name") or ""].append(s)

    plan: List[Dict[str, Any]] = []
    for _, members in by_page.items():
        plan.extend(_plan_renames_for_page(members, custom_overrides, strategy))

    plan = _disambiguate_plan(plan, slicers)

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "strategy": strategy,
            "total_planned": len(plan),
            "applied": 0,
            "plan": [{**p, "status": "would_change"} for p in plan],
        }

    applied = 0
    already_correct = 0
    errors = 0
    enriched: List[Dict[str, Any]] = []
    by_id = {_key(s): s for s in slicers}
    for p in plan:
        s = by_id[(p.get("page_id") or p.get("page") or "", p["visual_name"])]
        status = _apply_rename(Path(s["file_path"]), p["new_title"])
        if status in ("changed", "inserted"):
            applied += 1
        elif status == "already_correct":
            already_correct += 1
        else:
            errors += 1
        enriched.append({**p, "status": status})

    return {
        "success": errors == 0,
        "dry_run": False,
        "strategy": strategy,
        "total_planned": len(plan),
        "applied": applied,
        "already_correct": already_correct,
        "errors": errors,
        "plan": enriched,
    }
