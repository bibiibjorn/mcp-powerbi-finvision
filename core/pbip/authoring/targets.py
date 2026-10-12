"""Page / visual target resolution for PBIR authoring (WP9).

One page / visual resolver for every 07_Visual_Operations op (H-16, H-30,
L-02, L-05). Rules:

* **Page** (``page_id`` or ``page_name``): folder id, then EXACT display
  name (case-insensitive), then a UNIQUE display-name prefix — the same rule
  as ``pbip_utils.find_page_folder``. Never a substring match for writes:
  ``"P&L"`` no longer hits six pages. Read ops (``list``) fall back to a
  substring filter and say so (``page_match: "substring"``).
* **Visual** (``visual_id`` or ``visual_name``): exact folder id first, then
  EXACT title (case-insensitive). ``visual_name`` therefore means "id or
  exact title" in every op (L-05).
* **display_title**: exact title matches win; otherwise a substring filter.
* **Write guard (L-02)**: a write with no page / visual / title selector is
  refused unless ``match_all=true``; a write matching more than one visual is
  refused unless ``allow_multiple=true`` (or ``match_all``). The refusal lists
  the matches so the caller sees the blast radius.
* **Performance (H-30)**: the page is resolved first and only its visuals
  are read; an exact visual id short-circuits to one file; group offsets
  come from one per-page id -> position map (no parent-chain reloads).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.pbip.authoring.safe_io import is_safe_segment, read_json
from core.utilities.pbip_utils import get_page_display_name

logger = logging.getLogger(__name__)


def _title_of(visual_data: Dict[str, Any]) -> Optional[str]:
    visual = visual_data.get("visual") if isinstance(visual_data, dict) else None
    vco = visual.get("visualContainerObjects") if isinstance(visual, dict) else None
    for entry in (vco or {}).get("title") or []:
        props = entry.get("properties") if isinstance(entry, dict) else None
        text = (props or {}).get("text")
        lit = ((text or {}).get("expr") or {}).get("Literal") if isinstance(text, dict) else None
        value = lit.get("Value") if isinstance(lit, dict) else None
        if isinstance(value, str):
            if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
                return value[1:-1].replace("''", "'")
            return value
    return None


def list_pages(definition_path: Path) -> List[Tuple[Path, str]]:
    pages_dir = Path(definition_path) / "pages"
    try:
        dirs = sorted(p for p in pages_dir.iterdir() if p.is_dir())
    except OSError:
        return []
    return [(d, get_page_display_name(d)) for d in dirs]


def resolve_page(
    definition_path: Path, page: Optional[str], *, allow_substring: bool = False
) -> Dict[str, Any]:
    """Resolve one page selector -> ``{"pages": [dir...], "match": ...}`` or ``{"error"...}``.

    ``page`` None -> every page (``match: "all"``).
    """
    definition_path = Path(definition_path)
    if page is None or page == "":
        return {"pages": [d for d, _ in list_pages(definition_path)], "match": "all"}
    raw = str(page).strip()
    seg = raw[: -len(".Page")] if raw.endswith(".Page") else raw
    pages_dir = definition_path / "pages"
    # 1. Page folder id (only a single safe path segment can address a folder).
    for cand in (raw, seg):
        if is_safe_segment(cand) and (pages_dir / cand).is_dir():
            return {"pages": [pages_dir / cand], "match": "exact"}
    pages = list_pages(definition_path)
    low = seg.lower()
    # 2. Exact display name (case-insensitive) — must be unique.
    exact = [(d, n) for d, n in pages if n.lower() == low]
    if len(exact) == 1:
        return {"pages": [exact[0][0]], "match": "exact"}
    # 3. Unique display-name prefix.
    if not exact:
        starts = [(d, n) for d, n in pages if n.lower().startswith(low)]
        if len(starts) == 1:
            return {"pages": [starts[0][0]], "match": "prefix"}
    subs = [(d, n) for d, n in pages if low in n.lower()]
    if allow_substring and subs and not exact:
        return {"pages": [d for d, _ in subs], "match": "substring"}
    cands = exact or subs
    if len(cands) > 1:
        return {
            "error": (
                f"page {page!r} is ambiguous ({len(cands)} pages match); pass the page id "
                "(page_id) or the exact display name"
            ),
            "error_type": "ambiguous_page",
            "candidates": [{"page_id": d.name, "page_name": n} for d, n in cands[:25]],
        }
    return {
        "error": f"page not found: {page!r}",
        "error_type": "page_not_found",
        "candidates": [{"page_id": d.name, "page_name": n} for d, n in subs[:25]],
    }


def _page_offsets(visual_map: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """id -> absolute offset of its parent chain (group-local -> page-absolute)."""
    memo: Dict[str, Dict[str, float]] = {}

    def offset(vid: str, seen: set) -> Dict[str, float]:
        if vid in memo:
            return memo[vid]
        data = visual_map.get(vid) or {}
        parent = data.get("parentGroupName")
        if not parent or parent in seen or parent not in visual_map:
            memo[vid] = {"x": 0.0, "y": 0.0}
            return memo[vid]
        seen.add(parent)
        pos = visual_map[parent].get("position") or {}
        up = offset(parent, seen)
        res = {
            "x": up["x"] + float(pos.get("x", 0) or 0),
            "y": up["y"] + float(pos.get("y", 0) or 0),
        }
        memo[vid] = res
        return res

    for vid in visual_map:
        offset(vid, {vid})
    return memo


def load_page_visuals(page_dir: Path) -> Dict[str, Dict[str, Any]]:
    """``{visual_id: visual.json dict}`` for one page (unreadable files skipped)."""
    out: Dict[str, Dict[str, Any]] = {}
    vroot = Path(page_dir) / "visuals"
    try:
        dirs = sorted(v for v in vroot.iterdir() if v.is_dir())
    except OSError:
        return out
    for vdir in dirs:
        data = read_json(vdir / "visual.json")
        if isinstance(data, dict):
            out[vdir.name] = data
    return out


def absolute_offset(page_dir: Path, visual_data: Dict[str, Any]) -> Dict[str, float]:
    """Page-absolute offset of the group chain of ONE visual (H-19 / H-31)."""
    parent = visual_data.get("parentGroupName")
    total = {"x": 0.0, "y": 0.0}
    seen: set = set()
    vroot = Path(page_dir) / "visuals"
    while parent and parent not in seen and is_safe_segment(parent):
        seen.add(parent)
        pdata = read_json(vroot / parent / "visual.json")
        if not isinstance(pdata, dict):
            break
        pos = pdata.get("position") or {}
        total["x"] += float(pos.get("x", 0) or 0)
        total["y"] += float(pos.get("y", 0) or 0)
        parent = pdata.get("parentGroupName")
    return total


def _info(
    vid: str,
    data: Dict[str, Any],
    page_dir: Path,
    page_name: str,
    offsets: Dict[str, Dict[str, float]],
) -> Dict[str, Any]:
    pos = data.get("position") or {}
    off = offsets.get(vid) or {"x": 0.0, "y": 0.0}
    rel_x = pos.get("x", 0)
    rel_y = pos.get("y", 0)
    visual = data.get("visual") or {}
    vpath = page_dir / "visuals" / vid / "visual.json"
    info: Dict[str, Any] = {
        "file_path": str(vpath),
        "visual_name": data.get("name", vid),
        "display_title": _title_of(data),
        "visual_type": visual.get("visualType")
        or ("visualGroup" if "visualGroup" in data else "unknown"),
        "position": {
            "x": (rel_x or 0) + off["x"],
            "y": (rel_y or 0) + off["y"],
            "z": pos.get("z", 0),
            "height": pos.get("height", 0),
            "width": pos.get("width", 0),
            "tab_order": pos.get("tabOrder", 0),
        },
        "_relative_position": {"x": rel_x, "y": rel_y},
        "_parent_offset": off,
        "is_hidden": bool(data.get("isHidden", False)),
        "parent_group": data.get("parentGroupName"),
        "page_id": page_dir.name,
        "page_name": page_name,
    }
    if data.get("annotations"):
        info["annotations"] = data["annotations"]
    info["has_mobile_layout"] = (vpath.parent / "mobile.json").exists()
    return info


def find_visuals(
    definition_path: Path,
    *,
    page: Optional[str] = None,
    visual: Optional[str] = None,
    display_title: Optional[str] = None,
    visual_type: Optional[str] = None,
    include_hidden: bool = True,
    allow_page_substring: bool = False,
) -> Dict[str, Any]:
    """Find visuals -> ``{"visuals": [info...], "page_match": ...}`` or an error dict."""
    definition_path = Path(definition_path)
    pres = resolve_page(definition_path, page, allow_substring=allow_page_substring)
    if "error" in pres:
        return {"success": False, **pres}
    out: List[Dict[str, Any]] = []
    vneedle = str(visual).strip() if visual else None
    for page_dir in pres["pages"]:
        page_name = get_page_display_name(page_dir)
        if vneedle and is_safe_segment(vneedle) and (page_dir / "visuals" / vneedle).is_dir():
            # Exact id: load this visual (and its group chain) only.
            data = read_json(page_dir / "visuals" / vneedle / "visual.json")
            if not isinstance(data, dict):
                continue
            offs = {vneedle: absolute_offset(page_dir, data)}
            candidates = [(vneedle, data)]
        else:
            vmap = load_page_visuals(page_dir)
            if not vmap:
                continue
            offs = _page_offsets(vmap)
            candidates = list(vmap.items())
            if vneedle:
                low = vneedle.lower()
                by_id = [(k, d) for k, d in candidates if k.lower() == low]
                candidates = by_id or [
                    (k, d) for k, d in candidates if (_title_of(d) or "").lower() == low
                ]
        page_hits: List[Dict[str, Any]] = []
        for vid, data in candidates:
            info = _info(vid, data, page_dir, page_name, offs)
            if not include_hidden and info["is_hidden"]:
                continue
            if visual_type and visual_type.lower() != str(info["visual_type"]).lower():
                continue
            page_hits.append(info)
        if display_title:
            low = display_title.lower()
            exact = [i for i in page_hits if (i["display_title"] or "").lower() == low]
            page_hits = exact or [i for i in page_hits if low in (i["display_title"] or "").lower()]
        out.extend(page_hits)
    return {"success": True, "visuals": out, "page_match": pres["match"]}


def _brief(v: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "visual_name": v.get("visual_name"),
        "page_id": v.get("page_id"),
        "page_name": v.get("page_name"),
        "display_title": v.get("display_title"),
        "visual_type": v.get("visual_type"),
    }


def resolve_targets(
    definition_path: Path,
    args: Dict[str, Any],
    *,
    write: bool,
    op: str,
    single: bool = False,
    include_hidden_default: bool = True,
) -> Dict[str, Any]:
    """Selector args -> ``{"visuals": [...], "matched": n}`` or an error envelope.

    ``single=True`` (raw-property / sort / action / code ops) always demands
    exactly one visual.
    """
    page = args.get("page_id") or args.get("page_name")
    visual = args.get("visual_id") or args.get("visual_name")
    title = args.get("display_title")
    vtype = args.get("visual_type")
    match_all = bool(args.get("match_all", False))
    allow_multiple = bool(args.get("allow_multiple", False)) or match_all
    if write and not (page or visual or title) and not match_all:
        return {
            "success": False,
            "error_type": "target_required",
            "error": (
                f"{op} needs an explicit target: pass visual_name (id or exact title) "
                "and page_name/page_id, or display_title; pass match_all=true to edit "
                "EVERY matching visual in the report."
            ),
        }
    res = find_visuals(
        definition_path,
        page=page,
        visual=visual,
        display_title=title,
        visual_type=vtype,
        include_hidden=args.get("include_hidden", include_hidden_default),
        allow_page_substring=not write,
    )
    if not res.get("success"):
        return res
    visuals = res["visuals"]
    if not visuals:
        where = f" on page {page!r}" if page else ""
        return {
            "success": False,
            "error_type": "visual_not_found",
            "error": (
                f"No visual matches {visual or title or vtype or '(any)'}{where}. "
                'Use operation "list" to see available visuals.'
            ),
            "matched": 0,
        }
    if write and len(visuals) > 1 and (single or not allow_multiple):
        pages = sorted({v["page_id"] for v in visuals})
        return {
            "success": False,
            "error_type": "multiple_matches",
            "error": (
                f"{op} matched {len(visuals)} visuals on {len(pages)} page(s). "
                + (
                    "This op edits exactly one visual: pass the visual id and page id."
                    if single
                    else "Narrow the selector (visual id + page id) or pass "
                    "allow_multiple=true to edit all of them."
                )
            ),
            "matched": len(visuals),
            "matches": [_brief(v) for v in visuals[:50]],
        }
    return {
        "success": True,
        "visuals": visuals,
        "matched": len(visuals),
        "page_match": res["page_match"],
    }


def resolve_page_dir(
    definition_path: Path, page: Optional[str]
) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    """Exactly ONE page (id / exact name / unique prefix) -> ``(page_dir, None)``.

    On failure returns ``(None, error_envelope)`` (``page_not_found`` /
    ``ambiguous_page`` with candidates). Use this in engines instead of the
    old substring loops (H-28).
    """
    if not page:
        return None, {
            "success": False,
            "error_type": "invalid_args",
            "error": "page_name (or page_id) is required",
        }
    res = resolve_page(Path(definition_path), page, allow_substring=False)
    if "error" in res:
        return None, {"success": False, **res}
    return res["pages"][0], None


def resolve_visual_dir(
    page_dir: Path, visual: Optional[str], *, allow_title: bool = True
) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    """Exactly ONE visual on a page: exact id, else exact title (unique).

    No prefix / substring matching. ``allow_title=False`` = id only (deletes).
    """
    if not visual:
        return None, {
            "success": False,
            "error_type": "invalid_args",
            "error": "visual_name (or visual_id) is required",
        }
    raw = str(visual).strip()
    vroot = Path(page_dir) / "visuals"
    if is_safe_segment(raw) and (vroot / raw).is_dir():
        return vroot / raw, None
    titled: List[Tuple[str, Optional[str]]] = []
    for vid, data in load_page_visuals(page_dir).items():
        titled.append((vid, _title_of(data)))
    exact = [vid for vid, t in titled if t is not None and t.lower() == raw.lower()]
    if allow_title and len(exact) == 1:
        return vroot / exact[0], None
    if exact:
        return None, {
            "success": False,
            "error_type": "visual_id_required" if not allow_title else "ambiguous_visual",
            "error": (
                f"{raw!r} is a title, not a visual id; pass the id"
                if not allow_title and len(exact) == 1
                else f"{len(exact)} visuals are titled {raw!r}; pass the visual id"
            ),
            "candidates": [{"visual_name": vid, "display_title": raw} for vid in exact[:25]],
        }
    return None, {
        "success": False,
        "error_type": "visual_not_found",
        "error": f"visual not found: {raw!r} on page {Path(page_dir).name}",
    }
