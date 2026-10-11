"""Clone ONE visual (or one visual group) within a page or onto another page (F-16).

``sync_visual`` only copies onto existing targets and ``clone_page`` clones whole
pages; before this, a single visual could only be copied by hand, and every hand
copy lost its page interactions -- ``feedback_cloned_visuals_lose_interactions``:
11 cloned visuals went blank with zero errors because the NoFilter rules did not
follow. This module copies

* the visual folder (``visual.json`` + ``mobile.json``) with FRESH ids: the visual
  name, ``parentGroupName`` links inside a cloned group, and the filter names in
  its ``filterConfig`` -- replaced structurally (exact string values / dict keys),
  PLUS ids hidden inside base64 blobs of the COPIED folder only (never anywhere
  else in the report);
* the parent-group chain, one of three ways (``group_mode``): ``keep`` (same page,
  stay in the source's group), ``clone_group`` (clone the ancestor group chain,
  empty, onto the target page and parent the clone in it) or ``top_level`` (drop
  the parent; group-local coordinates are converted to page-absolute and back);
* ``inherit_interactions=true``: every ``visualInteractions`` entry of the source
  in BOTH directions (and, for a group, inside the copied set);
* ``copy_bookmark_refs=true``: the source's bookmark captures
  (``visualContainers`` / ``visualContainerGroups`` / ``options.targetVisualNames``).

Every write goes through ``safe_io`` (dry-run aware, locked, root-checked), so
``dry_run=true`` reports what would be created / changed without touching disk.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.pbip import pbir_model
from core.pbip.authoring import safe_io
from core.pbip.authoring.clone_engine import remap_exact_ids
from core.pbip.authoring.id_generator import generate_visual_id
from core.pbip.authoring.targets import (
    absolute_offset,
    load_page_visuals,
    resolve_page_dir,
    resolve_visual_dir,
)
from core.utilities.pbip_utils import SKIP_WRITE, detect_json_traits, serialize_json

logger = logging.getLogger(__name__)

GROUP_MODES = ("auto", "keep", "clone_group", "top_level")
_B64_RE = re.compile(r"^[A-Za-z0-9+/_-]{24,}={0,2}$")
_Z_STEP = 1000


# ---------------------------------------------------------------------------
# id remapping, including base64 blobs of the copied folder
# ---------------------------------------------------------------------------


def _remap_blob(value: str, mapping: Dict[str, str]) -> Tuple[str, int]:
    """Replace ids inside a base64 string that decodes to UTF-8 text. ``(new, count)``."""
    if not _B64_RE.match(value):
        return value, 0
    urlsafe = "-" in value or "_" in value
    pad = "=" * (-len(value.rstrip("=")) % 4) if "=" not in value else ""
    try:
        raw = (base64.urlsafe_b64decode if urlsafe else base64.b64decode)(value + pad)
        text = raw.decode("utf-8")
    except Exception:  # noqa: BLE001 - not base64 text: leave alone
        return value, 0
    new_text, n = text, 0
    for old, new in mapping.items():
        if old in new_text:
            n += new_text.count(old)
            new_text = new_text.replace(old, new)
    if not n:
        return value, 0
    enc = (base64.urlsafe_b64encode if urlsafe else base64.b64encode)(new_text.encode("utf-8"))
    out = enc.decode("ascii")
    if "=" not in value:
        out = out.rstrip("=")
    return out, n


def remap_ids_in_copy(obj: Any, mapping: Dict[str, str]) -> Tuple[Any, int, int]:
    """``(new_obj, exact_replacements, blob_replacements)`` over one copied document."""
    new_obj, exact = remap_exact_ids(obj, mapping)
    blobs = 0

    def walk(node: Any) -> Any:
        nonlocal blobs
        if isinstance(node, str):
            new, n = _remap_blob(node, mapping)
            blobs += n
            return new
        if isinstance(node, list):
            return [walk(x) for x in node]
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        return node

    return walk(new_obj), exact, blobs


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _strict(
    d: Optional[Dict[str, Any]], keys: Tuple[str, ...], label: str
) -> Optional[Dict[str, float]]:
    """Numeric copy of the caller's offset / position object; ValueError when not numeric."""
    if d is None:
        return None
    out: Dict[str, float] = {}
    for k in keys:
        if k in d and d[k] is not None:
            try:
                out[k] = float(d[k])
            except (TypeError, ValueError) as e:
                raise ValueError(f"{label}.{k} must be numeric, got {d[k]!r}") from e
    return out


def _pos(data: Dict[str, Any]) -> Dict[str, Any]:
    p = data.get("position")
    return p if isinstance(p, dict) else {}


def _abs_origin(page_dir: Path, group_id: str, gdata: Dict[str, Any]) -> Dict[str, float]:
    """Page-absolute top-left of a group container (its own position + its ancestors')."""
    off = absolute_offset(page_dir, gdata)
    pos = _pos(gdata)
    return {"x": off["x"] + _num(pos.get("x")), "y": off["y"] + _num(pos.get("y"))}


def _fmt(n: float) -> Any:
    return int(n) if float(n).is_integer() else round(n, 3)


def _descendants(vmap: Dict[str, Dict[str, Any]], root: str) -> List[str]:
    out: List[str] = []
    stack, seen = [root], {root}
    while stack:
        cur = stack.pop()
        for vid, d in vmap.items():
            if d.get("parentGroupName") == cur and vid not in seen:
                seen.add(vid)
                out.append(vid)
                stack.append(vid)
    return out


def _ancestor_chain(vmap: Dict[str, Dict[str, Any]], vid: str) -> List[str]:
    """Parent group ids from the immediate parent to the outermost."""
    chain: List[str] = []
    cur = (vmap.get(vid) or {}).get("parentGroupName")
    while isinstance(cur, str) and cur in vmap and cur not in chain:
        chain.append(cur)
        cur = vmap[cur].get("parentGroupName")
    return chain


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------


def clone_visual(
    definition_path: Path,
    source_page: str,
    source_visual: str,
    *,
    target_page: Optional[str] = None,
    offset: Optional[Dict[str, Any]] = None,
    position: Optional[Dict[str, Any]] = None,
    position_frame: str = "page",
    group_mode: str = "auto",
    parent_group: Optional[str] = None,
    inherit_interactions: bool = False,
    interaction_map: Optional[Dict[str, str]] = None,
    copy_bookmark_refs: bool = False,
) -> Dict[str, Any]:
    """Clone one visual / group. See the module docstring. Returns a result envelope.

    ``offset`` ``{x, y}`` (page-absolute px) is added to the source position (default
    ``{x: 20, y: 20}`` on the same page, ``{0, 0}`` on another page); ``position``
    (``x``/``y`` in ``position_frame`` = ``page`` | ``group``; ``width``/``height``)
    overrides it for the cloned root. ``parent_group`` re-parents under an existing
    group of the TARGET page (coordinates are converted). ``interaction_map``
    ``{source page visual id: target page visual id}`` lets cross-page interactions
    follow.
    """
    definition_path = Path(definition_path)
    offset = _strict(offset, ("x", "y"), "offset")
    position = _strict(position, ("x", "y", "width", "height"), "position")
    if group_mode not in GROUP_MODES:
        return _err("invalid_args", f"group_mode must be one of {list(GROUP_MODES)}")
    if position_frame not in ("page", "group"):
        return _err("invalid_args", "position_frame must be 'page' or 'group'")

    src_page_dir, perr = resolve_page_dir(definition_path, source_page)
    if perr:
        perr["error"] = f"Source page not found: {source_page} ({perr.get('error')})"
        return perr
    src_vdir, verr = resolve_visual_dir(src_page_dir, source_visual)
    if verr:
        return verr
    src_id = src_vdir.name
    if target_page:
        dst_page_dir, terr = resolve_page_dir(definition_path, target_page)
        if terr:
            terr["error"] = f"Target page not found: {target_page} ({terr.get('error')})"
            return terr
    else:
        dst_page_dir = src_page_dir
    same_page = dst_page_dir == src_page_dir

    src_map = load_page_visuals(src_page_dir)
    dst_map = src_map if same_page else load_page_visuals(dst_page_dir)
    src_data = src_map.get(src_id)
    if not isinstance(src_data, dict):
        return _err("visual_not_found", f"visual.json of {src_id} is unreadable")

    # -- the set being copied ------------------------------------------------
    members = [src_id] + _descendants(src_map, src_id)
    chain = _ancestor_chain(src_map, src_id)
    existing_ids = set(dst_map) | set(src_map)
    id_map: Dict[str, str] = {}
    for vid in members:
        id_map[vid] = _fresh_id(existing_ids)
    notes: List[str] = []

    # -- parent handling -----------------------------------------------------
    mode = group_mode
    if parent_group:
        if parent_group not in dst_map or "visualGroup" not in dst_map[parent_group]:
            return _err(
                "parent_group_not_found",
                f"parent_group {parent_group!r} is not a visual group on page {dst_page_dir.name}",
            )
        if parent_group in members:
            return _err(
                "invalid_args", "parent_group cannot be the visual being cloned (or its child)"
            )
        mode = "parent_group"
    elif mode == "auto":
        mode = "keep" if same_page else "top_level"
    if mode == "keep" and not same_page:
        notes.append("group_mode=keep needs the same page; used top_level instead")
        mode = "top_level"
    if mode in ("keep", "clone_group", "parent_group") and not chain and mode != "parent_group":
        mode = "top_level"  # nothing to keep / clone

    cloned_groups: List[str] = []
    if mode == "clone_group":
        for gid in chain:
            cloned_groups.append(gid)
            id_map[gid] = _fresh_id(existing_ids)

    # -- geometry ------------------------------------------------------------
    src_off = absolute_offset(src_page_dir, src_data)
    sp = _pos(src_data)
    src_abs = {"x": src_off["x"] + _num(sp.get("x")), "y": src_off["y"] + _num(sp.get("y"))}
    dx, dy = (20.0, 20.0) if same_page else (0.0, 0.0)
    if offset:
        dx, dy = _num(offset.get("x")), _num(offset.get("y"))
    tgt_abs = {"x": src_abs["x"] + dx, "y": src_abs["y"] + dy}

    final_parent: Optional[str] = None
    if mode == "keep":
        final_parent = src_data.get("parentGroupName")
    elif mode == "clone_group":
        final_parent = id_map[chain[0]]
    elif mode == "parent_group":
        final_parent = parent_group

    parent_origin = {"x": 0.0, "y": 0.0}
    if mode in ("keep", "parent_group") and final_parent:
        parent_origin = _abs_origin(dst_page_dir, final_parent, dst_map[final_parent])
    if position:
        if "x" in position:
            base = parent_origin if position_frame == "group" else {"x": 0.0, "y": 0.0}
            tgt_abs["x"] = _num(position["x"]) + base["x"]
        if "y" in position:
            base = parent_origin if position_frame == "group" else {"x": 0.0, "y": 0.0}
            tgt_abs["y"] = _num(position["y"]) + base["y"]
    delta = {"x": tgt_abs["x"] - src_abs["x"], "y": tgt_abs["y"] - src_abs["y"]}

    max_z = max((_num(_pos(d).get("z")) for d in dst_map.values()), default=0.0)
    max_tab = max((_num(_pos(d).get("tabOrder")) for d in dst_map.values()), default=0.0)

    # -- build the new documents (in memory) --------------------------------
    exact_total = blob_total = 0
    files: List[Tuple[Path, Any, bytes]] = []  # (dest path, data, source raw bytes)
    skipped_files: List[str] = []
    dst_vroot = dst_page_dir / "visuals"
    filter_names: Dict[str, str] = {}

    def _clone_folder(old_id: str, new_id: str, mutate) -> None:
        nonlocal exact_total, blob_total
        sdir = src_page_dir / "visuals" / old_id
        for f in sorted(p for p in sdir.rglob("*") if p.is_file()):
            rel = f.relative_to(sdir)
            if f.suffix.lower() != ".json":
                skipped_files.append(str(rel))
                continue
            try:
                raw = f.read_bytes()
                data = json.loads(raw.decode("utf-8-sig"))
            except (OSError, ValueError, UnicodeDecodeError) as e:
                raise safe_io.WriteError(f"cannot read {f}: {e}", f) from e
            if rel.name == "visual.json" and isinstance(data, dict):
                for flt in pbir_model.get_visual_filters(data):
                    nm = flt.get("name")
                    if isinstance(nm, str) and nm and nm not in filter_names:
                        filter_names[nm] = generate_visual_id()
                data, n_exact, n_blob = remap_ids_in_copy(data, {**id_map, **filter_names})
                exact_total += n_exact
                blob_total += n_blob
                mutate(data)
            else:
                data, n_exact, n_blob = remap_ids_in_copy(data, id_map)
                exact_total += n_exact
                blob_total += n_blob
            files.append((dst_vroot / new_id / rel, data, raw))

    root_new = id_map[src_id]

    def _mutate_root(d: Dict[str, Any]) -> None:
        pos = d.setdefault("position", {})
        if final_parent is not None and mode in ("keep", "parent_group", "clone_group"):
            d["parentGroupName"] = final_parent
        else:
            d.pop("parentGroupName", None)
        if mode == "clone_group":
            # the cloned group chain keeps the source's group-local coordinates
            pos["x"] = _fmt(_num(sp.get("x")))
            pos["y"] = _fmt(_num(sp.get("y")))
        else:
            pos["x"] = _fmt(tgt_abs["x"] - parent_origin["x"])
            pos["y"] = _fmt(tgt_abs["y"] - parent_origin["y"])
        if position:
            if "width" in position:
                pos["width"] = _fmt(_num(position["width"]))
            if "height" in position:
                pos["height"] = _fmt(_num(position["height"]))
        pos["z"] = _fmt(max_z + _Z_STEP)
        pos["tabOrder"] = _fmt(max_tab + _Z_STEP)

    z_delta = max_z + _Z_STEP - _num(sp.get("z"))
    tab_delta = max_tab + _Z_STEP - _num(sp.get("tabOrder"))

    def _mutate_member(d: Dict[str, Any]) -> None:
        # descendants: parentGroupName already remapped by remap_exact_ids; z / tabOrder share
        # the page-wide order space, so they move with the cloned group
        pos = d.get("position")
        if isinstance(pos, dict):
            if "z" in pos:
                pos["z"] = _fmt(_num(pos["z"]) + z_delta)
            if "tabOrder" in pos:
                pos["tabOrder"] = _fmt(_num(pos["tabOrder"]) + tab_delta)

    for vid in members:
        _clone_folder(vid, id_map[vid], _mutate_root if vid == src_id else _mutate_member)

    # cloned ancestor groups: empty container copies (no children but the clone)
    for gid in cloned_groups:
        gdata = copy.deepcopy(src_map[gid])
        gdata, n_exact, n_blob = remap_ids_in_copy(gdata, id_map)
        exact_total += n_exact
        blob_total += n_blob
        parent = src_map[gid].get("parentGroupName")
        if parent in id_map:
            gdata["parentGroupName"] = id_map[parent]
        else:
            gdata.pop("parentGroupName", None)
        if gid == chain[-1]:  # outermost cloned group takes the page delta
            gp = gdata.setdefault("position", {})
            gp["x"] = _fmt(_num(gp.get("x")) + delta["x"])
            gp["y"] = _fmt(_num(gp.get("y")) + delta["y"])
        files.append(
            (
                dst_vroot / id_map[gid] / "visual.json",
                gdata,
                (src_page_dir / "visuals" / gid / "visual.json").read_bytes(),
            )
        )

    # -- interactions --------------------------------------------------------
    interactions_added = 0
    interactions_skipped = 0
    src_page_json = safe_io.read_json(src_page_dir / "page.json") or {}
    src_interactions = pbir_model.get_page_interactions(src_page_json)
    wanted: List[Dict[str, Any]] = []
    member_set = set(members)
    imap = dict(interaction_map or {})
    relevant = [
        i
        for i in src_interactions
        if i.get("source") in member_set or i.get("target") in member_set
    ]
    if inherit_interactions:
        dst_ids = set(dst_map)
        for it in relevant:
            s, t = it.get("source"), it.get("target")
            ns = id_map.get(s) if s in member_set else (s if same_page else imap.get(s))
            nt = id_map.get(t) if t in member_set else (t if same_page else imap.get(t))
            if (
                not ns
                or not nt
                or (ns not in dst_ids and ns not in id_map.values())
                or (nt not in dst_ids and nt not in id_map.values())
            ):
                interactions_skipped += 1
                continue
            wanted.append(
                {
                    **{k: v for k, v in it.items() if k not in ("source", "target")},
                    "source": ns,
                    "target": nt,
                }
            )
        if interactions_skipped and not same_page:
            notes.append(
                f"{interactions_skipped} interaction(s) point at visuals that do not exist on the "
                "target page; pass interaction_map {source_visual_id: target_visual_id} to carry them"
            )

    # -- WRITE ---------------------------------------------------------------
    for dest, data, raw in files:
        safe_io.make_dirs(dest.parent)
        new_bytes = serialize_json(data, detect_json_traits(raw))
        safe_io.write_bytes_minimal(dest, new_bytes, b"")

    if wanted:
        dst_page_json = dst_page_dir / "page.json"

        def _add_interactions(page: Dict[str, Any]) -> None:
            cur = pbir_model.get_page_interactions(page)
            have = {(i.get("source"), i.get("target")) for i in cur}
            nonlocal interactions_added
            for it in wanted:
                if (it["source"], it["target"]) not in have:
                    cur.append(it)
                    have.add((it["source"], it["target"]))
                    interactions_added += 1
            pbir_model.set_page_interactions(page, cur)

        safe_io.update_json(dst_page_json, _add_interactions)

    bookmarks_updated = 0
    if copy_bookmark_refs:
        bookmarks_updated = _copy_bookmark_refs(
            definition_path, src_page_dir.name, dst_page_dir.name, id_map, members
        )

    result: Dict[str, Any] = {
        "success": True,
        "source_page": src_page_dir.name,
        "target_page": dst_page_dir.name,
        "source_visual": src_id,
        "new_visual_id": root_new,
        "id_mapping": id_map,
        "visual_count": len(members) + len(cloned_groups),
        "group_mode": mode,
        "parent_group": final_parent,
        "position": {
            "page_absolute": {"x": _fmt(tgt_abs["x"]), "y": _fmt(tgt_abs["y"])},
            "frame": position_frame,
        },
        "references_remapped": exact_total,
        "blob_refs_remapped": blob_total,
        "filter_names_regenerated": len(filter_names),
        "interactions_in_source": len(relevant),
        "interactions_added": interactions_added,
        "bookmarks_updated": bookmarks_updated,
        "path": str(dst_vroot / root_new),
    }
    if skipped_files:
        result["skipped_files"] = skipped_files
        notes.append("non-JSON files in the visual folder were not copied")
    if relevant and not inherit_interactions:
        result["interactions_not_inherited"] = len(relevant)
        notes.append(
            f"the source has {len(relevant)} visualInteractions entr(ies); the clone follows none of "
            "them (a cloned slicer would now filter everything). Re-run with "
            "inherit_interactions=true, or set them with 07_Page_Operations set_interaction."
        )
    if notes:
        result["notes"] = notes
    return result


def _fresh_id(taken: Set[str]) -> str:
    while True:
        new = generate_visual_id()
        if new not in taken:
            taken.add(new)
            return new


def _err(error_type: str, msg: str) -> Dict[str, Any]:
    return {"success": False, "error_type": error_type, "error": msg}


# ---------------------------------------------------------------------------
# bookmarks
# ---------------------------------------------------------------------------


def _copy_bookmark_refs(
    definition_path: Path,
    src_page: str,
    dst_page: str,
    id_map: Dict[str, str],
    members: List[str],
) -> int:
    """Add the new ids next to the originals in every bookmark that captured the source.

    ``sections[src_page].visualContainers[old]`` is deep-copied to
    ``sections[dst_page].visualContainers[new]`` (the section must already exist on
    the target page); ``visualContainerGroups`` entries keyed by a copied group id
    follow with remapped ``children``; ``options.targetVisualNames`` gains the new ids.
    Returns the number of bookmark files (that would be) changed.
    """
    bdir = Path(definition_path) / "bookmarks"
    if not bdir.is_dir():
        return 0
    changed = 0
    member_set = set(members)
    for bfile in sorted(bdir.glob("*.bookmark.json")):

        def _mutate(bm: Any) -> Any:
            if not isinstance(bm, dict):
                return SKIP_WRITE
            touched = False
            sections = (bm.get("explorationState") or {}).get("sections") or {}
            src_sec = sections.get(src_page)
            dst_sec = sections.get(dst_page)
            if isinstance(src_sec, dict) and isinstance(dst_sec, dict):
                for key in ("visualContainers", "visualContainerGroups"):
                    s_block, d_block = src_sec.get(key), dst_sec.get(key)
                    if not isinstance(s_block, dict):
                        continue
                    if not isinstance(d_block, dict):
                        d_block = dst_sec.setdefault(key, {})
                    for old, entry in list(s_block.items()):
                        if old in member_set and old in id_map and id_map[old] not in d_block:
                            new_entry, _e, _b = remap_ids_in_copy(copy.deepcopy(entry), id_map)
                            d_block[id_map[old]] = new_entry
                            touched = True
            opts = bm.get("options")
            names = opts.get("targetVisualNames") if isinstance(opts, dict) else None
            if isinstance(names, list):
                for old in members:
                    if old in names and id_map[old] not in names:
                        names.append(id_map[old])
                        touched = True
            return None if touched else SKIP_WRITE

        try:
            if safe_io.update_json(bfile, _mutate):
                changed += 1
        except safe_io.WriteError as e:
            logger.warning("clone_visual: bookmark %s skipped: %s", bfile.name, e)
    return changed


__all__ = ["clone_visual", "remap_ids_in_copy", "GROUP_MODES"]
