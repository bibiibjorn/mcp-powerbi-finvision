"""Undo / replay / restore planning for the write journal (JR-01) - pure Python, no CLR.

Every planner turns journal changes (or a checkpoint diff) plus the CURRENT live image map into an
ordered list of primitive ops the applier (:mod:`core.operations.journal_apply`) executes on a TOM
model in ONE SaveChanges, together with the conflicts and skipped changes it found::

    op = {"action": "add" | "remove" | "update", "ref", "kind", "table", "name",
          "json": <target image, add/update>, "current": <live image, update>,
          "rename_from": <old name, update that renames>, "entries": [journal ids],
          "destructive": bool, "needs_refresh": bool}

The planners simulate the changes one after the other on a *virtual* copy of the live state, so a
chain (rename, then edit the renamed object) is checked and reversed as a chain.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from core.operations.journal_images import (
    CHILD_KINDS,
    diff_maps,
    make_ref,
    same_image,
    subset_match,
    three_way_merge,
)

__all__ = [
    "plan_undo",
    "plan_replay",
    "plan_restore",
    "order_ops",
    "summarize_plan",
    "needs_removal_confirmation",
]

#: Kinds whose removal drops data or needs a confirmation (fail-closed gate in the manager).
_DATA_KINDS = frozenset({"table", "column", "partition"})
_GATED_REMOVE_KINDS = frozenset({"table", "culture", "expression"})
#: N-22: removing a column / partition loses data an undo cannot bring back, and removing more
#: than ``_BULK_REMOVE_LIMIT`` objects in one plan is a bulk delete - both need
#: ``confirm_destructive`` like a table removal.
_DATA_GATED_KINDS = frozenset({"column", "partition"})
_BULK_REMOVE_LIMIT = 10
_CONFLICT_UNDO = "properties changed after this entry (no longer its post-image)"
_CONFLICT_REPLAY = "properties changed since this entry's pre-image"


def _new_plan() -> Dict[str, Any]:
    return {"ops": [], "conflicts": [], "skipped": []}


def _live_json(virtual: Dict[str, Dict[str, Any]], ref: str) -> Optional[Dict[str, Any]]:
    img = virtual.get(ref)
    return None if img is None else img["json"]


def _add_op(
    plan,
    action,
    ch,
    *,
    target,
    current=None,
    rename_from=None,
    entry=None,
    locate_name=None,
    rename_first=False,
):
    kind = ch["kind"]
    op = {
        "action": action,
        "ref": ch["ref"],
        "kind": kind,
        "table": ch.get("table"),
        "name": ch.get("name"),
        # the object's name in the LIVE model when the op runs (differs from `name` for a
        # rename that has not been applied yet)
        "locate_name": locate_name or ch.get("name"),
        "json": target,
        "current": current,
        "rename_from": rename_from,
        "rename_first": bool(rename_first),
        "entries": [entry] if entry else [],
        "destructive": action == "remove",
        "needs_refresh": (
            (action in {"add", "remove"} and kind in _DATA_KINDS)
            or (kind == "partition" and action == "update")
        ),
    }
    plan["ops"].append(op)
    return op


def _conflict(plan, ch, entry, reason, live=None, keys=None):
    item = {
        "ref": ch["ref"],
        "entry": entry,
        "change": ch["change"],
        "reason": reason,
        "live_present": live is not None,
    }
    if keys:
        item["keys"] = list(keys)
    plan["conflicts"].append(item)


def _skip(plan, ch, entry, reason):
    plan["skipped"].append(
        {"ref": ch["ref"], "entry": entry, "change": ch["change"], "reason": reason}
    )


def _virtual_put(virtual, ch, body, *, table=None, name=None):
    img = {
        "kind": ch["kind"],
        "table": ch.get("table") if table is None else table,
        "name": ch.get("name") if name is None else name,
        "json": body,
    }
    virtual[make_ref(img["kind"], img["table"], img["name"])] = img


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------
def plan_undo(
    entries_newest_first: Iterable[Dict[str, Any]],
    live_map: Dict[str, Dict[str, Any]],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Revert *entries* (newest first). A change whose object no longer looks like the entry's
    post-image is a conflict (skipped) unless ``force``."""
    plan = _new_plan()
    virtual = dict(live_map)
    for entry in entries_newest_first:
        eid = entry.get("id")
        for ch in reversed(entry.get("changes") or []):
            kind = ch["change"]
            ref = ch["ref"]
            live = _live_json(virtual, ref)
            if kind == "added":
                if live is None:
                    _skip(plan, ch, eid, "already_absent")
                    continue
                if not force and not subset_match(ch["post"], live):
                    _conflict(
                        plan,
                        ch,
                        eid,
                        "object changed after this entry (no longer its post-image)",
                        live,
                    )
                    continue
                _add_op(plan, "remove", ch, target=None, current=live, entry=eid)
                virtual.pop(ref, None)
            elif kind == "removed":
                if live is not None:
                    if same_image(ch["pre"], live):
                        _skip(plan, ch, eid, "already_matches")
                    elif force:
                        _add_op(plan, "update", ch, target=ch["pre"], current=live, entry=eid)
                        _virtual_put(virtual, ch, ch["pre"])
                    else:
                        _conflict(plan, ch, eid, "an object with this name exists again", live)
                    continue
                _add_op(plan, "add", ch, target=ch["pre"], entry=eid)
                _virtual_put(virtual, ch, ch["pre"])
            elif kind == "modified":
                if live is None:
                    _conflict(plan, ch, eid, "object no longer exists", None)
                    continue
                merge = three_way_merge(ch["post"], ch["pre"], live, force=force)
                if merge["conflicts"]:
                    _conflict(plan, ch, eid, _CONFLICT_UNDO, live, merge["conflicts"])
                elif merge["changed"]:
                    _add_op(plan, "update", ch, target=merge["merged"], current=live, entry=eid)
                    _virtual_put(virtual, ch, merge["merged"])
                else:
                    _skip(plan, ch, eid, "already_matches")
            elif kind == "renamed":
                if live is None:
                    _conflict(plan, ch, eid, "object no longer exists under its new name", None)
                    continue
                merge = three_way_merge(ch["post"], ch["pre"], live, force=force)
                if merge["conflicts"]:
                    _conflict(plan, ch, eid, _CONFLICT_UNDO, live, merge["conflicts"])
                    continue
                _add_op(
                    plan,
                    "update",
                    ch,
                    target=merge["merged"],
                    current=live,
                    rename_from=ch["name"],
                    entry=eid,
                )
                virtual.pop(ref, None)
                _virtual_put(virtual, ch, merge["merged"], name=ch.get("pre_name"))
    return _finish(plan)


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------
def plan_replay(
    entries_oldest_first: Iterable[Dict[str, Any]],
    live_map: Dict[str, Dict[str, Any]],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Re-apply the post-images of *entries* (oldest first) onto the live model.

    Already-applied changes are skipped; a change whose base no longer matches (the live object is
    neither the entry's pre- nor post-image) is a conflict, skipped unless ``force``.
    """
    plan = _new_plan()
    virtual = dict(live_map)
    for entry in entries_oldest_first:
        eid = entry.get("id")
        for ch in entry.get("changes") or []:
            kind = ch["change"]
            ref = ch["ref"]
            live = _live_json(virtual, ref)
            if kind == "added":
                if live is None:
                    _add_op(plan, "add", ch, target=ch["post"], entry=eid)
                    _virtual_put(virtual, ch, ch["post"])
                elif subset_match(ch["post"], live):
                    _skip(plan, ch, eid, "already_applied")
                elif force:
                    _add_op(plan, "update", ch, target=ch["post"], current=live, entry=eid)
                    _virtual_put(virtual, ch, ch["post"])
                else:
                    _conflict(
                        plan,
                        ch,
                        eid,
                        "an object with this name exists with different content",
                        live,
                    )
            elif kind == "removed":
                if live is None:
                    _skip(plan, ch, eid, "already_applied")
                elif force or subset_match(ch["pre"], live):
                    _add_op(plan, "remove", ch, target=None, current=live, entry=eid)
                    virtual.pop(ref, None)
                else:
                    _conflict(plan, ch, eid, "object changed since this entry's pre-image", live)
            elif kind == "modified":
                if live is None:
                    _conflict(plan, ch, eid, "object does not exist", None)
                    continue
                merge = three_way_merge(ch["pre"], ch["post"], live, force=force)
                if merge["conflicts"]:
                    _conflict(plan, ch, eid, _CONFLICT_REPLAY, live, merge["conflicts"])
                elif merge["changed"]:
                    _add_op(plan, "update", ch, target=merge["merged"], current=live, entry=eid)
                    _virtual_put(virtual, ch, merge["merged"])
                else:
                    _skip(plan, ch, eid, "already_applied")
            elif kind == "renamed":
                new_live = live
                old_ref = ch.get("pre_ref") or make_ref(
                    ch["kind"], ch.get("table"), ch.get("pre_name")
                )
                old_live = _live_json(virtual, old_ref)
                if new_live is not None:
                    done = three_way_merge(ch["pre"], ch["post"], new_live)
                    if not done["changed"] and not done["conflicts"]:
                        _skip(plan, ch, eid, "already_applied")
                        continue
                if old_live is None:
                    _conflict(
                        plan, ch, eid, "renamed object not found in its expected state", new_live
                    )
                    continue
                merge = three_way_merge(ch["pre"], ch["post"], old_live, force=force)
                if merge["conflicts"]:
                    _conflict(plan, ch, eid, _CONFLICT_REPLAY, old_live, merge["conflicts"])
                    continue
                _add_op(
                    plan,
                    "update",
                    ch,
                    target=merge["merged"],
                    current=old_live,
                    rename_from=ch.get("pre_name"),
                    locate_name=ch.get("pre_name"),
                    rename_first=True,
                    entry=eid,
                )
                virtual.pop(old_ref, None)
                _virtual_put(virtual, ch, merge["merged"])
    return _finish(plan)


# ---------------------------------------------------------------------------
# checkpoint restore
# ---------------------------------------------------------------------------
def plan_restore(
    live_map: Dict[str, Dict[str, Any]], target_map: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """Make the live model equal *target_map* (a checkpoint): update, add and remove objects."""
    plan = _new_plan()
    for ch in diff_maps(live_map, target_map):
        kind = ch["change"]
        if kind == "added":  # in the checkpoint, missing live
            _add_op(plan, "add", ch, target=ch["post"])
        elif kind == "removed":  # live only
            _add_op(plan, "remove", ch, target=None, current=ch["pre"])
        elif kind == "modified":
            _add_op(plan, "update", ch, target=ch["post"], current=ch["pre"])
        elif kind == "renamed":
            _add_op(
                plan,
                "update",
                ch,
                target=ch["post"],
                current=ch["pre"],
                rename_from=ch.get("pre_name"),
                locate_name=ch.get("pre_name"),
                rename_first=True,
            )
    return _finish(plan)


# ---------------------------------------------------------------------------
# ordering + summary
# ---------------------------------------------------------------------------
def _phase(op: Dict[str, Any]) -> int:
    action, kind = op["action"], op["kind"]
    if action == "remove":
        return 20 if kind == "table" else 10
    if action == "add":
        if kind == "table":
            return 30
        if kind in CHILD_KINDS:
            return 50
        return 70 if kind == "relationship" else 60
    if kind == "model":
        return 40
    if kind == "table":
        # replay / restore rename a table BEFORE its children are located under the new name;
        # undo renames it back AFTER the children (which are still under the name it has now)
        return 5 if op.get("rename_first") else 80
    return 45


def order_ops(ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dependency order for one SaveChanges; child removals under a removed table are dropped and
    the children of an added table are folded into its JSON."""
    removed_tables = {o["name"] for o in ops if o["action"] == "remove" and o["kind"] == "table"}
    kept = [
        o
        for o in ops
        if not (
            o["action"] == "remove"
            and o["kind"] in CHILD_KINDS
            and o.get("table") in removed_tables
        )
    ]
    added_tables = {o["name"]: o for o in kept if o["action"] == "add" and o["kind"] == "table"}
    folded: List[Dict[str, Any]] = []
    for o in kept:
        tbl = added_tables.get(o.get("table")) if o["kind"] in CHILD_KINDS else None
        if o["action"] == "add" and tbl is not None:
            body = dict(tbl["json"])
            key = {
                "column": "columns",
                "measure": "measures",
                "partition": "partitions",
                "hierarchy": "hierarchies",
            }.get(o["kind"])
            if key:
                body[key] = list(body.get(key) or []) + [o["json"]]
            else:  # calculation item
                cg = dict(body.get("calculationGroup") or {})
                cg["calculationItems"] = list(cg.get("calculationItems") or []) + [o["json"]]
                body["calculationGroup"] = cg
            tbl["json"] = body
            continue
        folded.append(o)
    return sorted(folded, key=_phase)


def _finish(plan: Dict[str, Any]) -> Dict[str, Any]:
    plan["ops"] = order_ops(plan["ops"])
    return plan


def summarize_plan(plan: Dict[str, Any], *, limit: int = 100) -> Dict[str, Any]:
    """JSON-safe, size-capped view of a plan for the tool response."""
    ops = plan["ops"]
    counts: Dict[str, int] = {}
    for o in ops:
        counts[o["action"]] = counts.get(o["action"], 0) + 1
    shown = [
        {
            k: v
            for k, v in {
                "action": o["action"],
                "ref": o["ref"],
                "rename_from": o.get("rename_from"),
                "entries": o.get("entries") or None,
                "destructive": o["destructive"] or None,
                "needs_refresh": o["needs_refresh"] or None,
            }.items()
            if v is not None
        }
        for o in ops[:limit]
    ]
    out: Dict[str, Any] = {
        "changes": shown,
        "change_count": len(ops),
        "counts": counts,
        "conflicts": plan["conflicts"][:limit],
        "conflict_count": len(plan["conflicts"]),
        "skipped": plan["skipped"][:limit],
        "skipped_count": len(plan["skipped"]),
    }
    if len(ops) > limit:
        out["changes_truncated"] = True
    needs = [o["ref"] for o in ops if o["needs_refresh"]]
    if needs:
        out["needs_refresh"] = needs[:limit]
    gated = needs_removal_confirmation(plan)
    if gated:
        out["removes_gated_objects"] = gated[:limit]
    return out


def needs_removal_confirmation(plan: Dict[str, Any]) -> List[str]:
    """Refs the plan would remove that need ``confirm_destructive``: tables, cultures, named
    expressions, data-bearing columns / partitions - and EVERY removal once the plan removes more
    than ``_BULK_REMOVE_LIMIT`` objects."""
    removals = [o for o in plan["ops"] if o["action"] == "remove"]
    if len(removals) > _BULK_REMOVE_LIMIT:
        return [o["ref"] for o in removals]
    return [
        o["ref"]
        for o in removals
        if o["kind"] in _GATED_REMOVE_KINDS or o["kind"] in _DATA_GATED_KINDS
    ]
