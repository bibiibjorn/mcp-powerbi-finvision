"""Object images for the durable write journal (JR-01) - pure Python, no CLR.

A *model image* is the TOM TMSL serialisation of a model
(``JsonSerializer.SerializeObject(model, SerializeOptions(IgnoreTimestamps))``)
split into one JSON document per restorable object. The journal stores the
PRE and POST image of every object a write touched, so a write can be undone
(re-apply the pre-image) or replayed (re-apply the post-image) exactly.

Object kinds and their ref (the key used everywhere in the journal)::

    model                          the model's scalar properties (culture, valueFilterBehavior, ...)
    table:Sales                    the table shell (no columns/measures/partitions/hierarchies/calc items)
    column:Sales[Amount]           measure:Sales[Total]   partition:Sales[P1]
    hierarchy:Sales[H]             calcitem:Time[YTD]     (a calculation group is a table with items)
    relationship:<name>            role:<name>  perspective:<name>  culture:<name>
    expression:<name>              datasource:<name>    function:<name>  querygroup:<name>

A rename is detected through ``lineageTag`` (same kind, same non-empty tag, different name) and
reported as ONE ``renamed`` change - so undoing a column rename renames it back instead of
dropping and re-adding it (which would lose its data). Everything else is ``added`` / ``removed``
/ ``modified``.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "CHILD_KINDS",
    "TOP_LEVEL_KINDS",
    "make_ref",
    "split_model",
    "diff_maps",
    "ref_of",
    "subset_match",
    "same_image",
    "three_way_merge",
    "describe_change",
    "strip_volatile",
    "count_by_kind",
]

#: model-JSON list key -> kind (objects that hang directly off the model).
TOP_LEVEL_KINDS: Dict[str, str] = {
    "relationships": "relationship",
    "roles": "role",
    "perspectives": "perspective",
    "cultures": "culture",
    "expressions": "expression",
    "dataSources": "datasource",
    "functions": "function",
    "queryGroups": "querygroup",
}
#: table-JSON list key -> kind (objects that hang off a table).
_TABLE_CHILD_KEYS: Dict[str, str] = {
    "columns": "column",
    "measures": "measure",
    "partitions": "partition",
    "hierarchies": "hierarchy",
}
CHILD_KINDS = frozenset(_TABLE_CHILD_KEYS.values()) | {"calcitem"}
_MODEL_LIST_KEYS = frozenset({"tables", *TOP_LEVEL_KINDS})

#: Server-assigned timestamps: never part of an image (they change on every save).
_VOLATILE_KEYS = frozenset(
    {
        "modifiedTime",
        "structureModifiedTime",
        "refreshedTime",
        "lastProcessed",
        "lastUpdate",
        "lastSchemaUpdate",
    }
)

_KIND_ORDER = {
    k: i
    for i, k in enumerate(
        (
            "model",
            "table",
            "column",
            "measure",
            "partition",
            "hierarchy",
            "calcitem",
            "relationship",
            "role",
            "perspective",
            "culture",
            "expression",
            "datasource",
            "function",
            "querygroup",
        )
    )
}


def strip_volatile(value: Any) -> Any:
    """Recursively drop server timestamps (returns a new structure)."""
    if isinstance(value, dict):
        return {k: strip_volatile(v) for k, v in value.items() if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [strip_volatile(v) for v in value]
    return value


def make_ref(kind: str, table: Optional[str], name: Optional[str]) -> str:
    if kind == "model":
        return "model"
    if kind in CHILD_KINDS:
        return f"{kind}:{table}[{name}]"
    return f"{kind}:{name}"


def _image(
    kind: str, table: Optional[str], name: Optional[str], body: Dict[str, Any]
) -> Dict[str, Any]:
    return {"kind": kind, "table": table, "name": name, "json": body}


def _relationship_name(rel: Dict[str, Any]) -> str:
    name = rel.get("name")
    if name:
        return str(name)
    return (
        f"{rel.get('fromTable')}.{rel.get('fromColumn')}->"
        f"{rel.get('toTable')}.{rel.get('toColumn')}"
    )


def split_model(model_json: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Model TMSL -> ``{ref: {"kind","table","name","json"}}``."""
    mj = strip_volatile(model_json or {})
    images: Dict[str, Dict[str, Any]] = {}

    def put(kind: str, table: Optional[str], name: Optional[str], body: Dict[str, Any]) -> None:
        images[make_ref(kind, table, name)] = _image(kind, table, name, body)

    put("model", None, "model", {k: v for k, v in mj.items() if k not in _MODEL_LIST_KEYS})
    for tbl in mj.get("tables") or []:
        tname = tbl.get("name")
        shell = {k: v for k, v in tbl.items() if k not in _TABLE_CHILD_KEYS}
        items: List[Dict[str, Any]] = []
        cg = shell.get("calculationGroup")
        if isinstance(cg, dict):
            items = list(cg.get("calculationItems") or [])
            shell["calculationGroup"] = {k: v for k, v in cg.items() if k != "calculationItems"}
        put("table", None, tname, shell)
        for key, kind in _TABLE_CHILD_KEYS.items():
            for child in tbl.get(key) or []:
                put(kind, tname, child.get("name"), child)
        for item in items:
            put("calcitem", tname, item.get("name"), item)
    for key, kind in TOP_LEVEL_KINDS.items():
        for obj in mj.get(key) or []:
            name = _relationship_name(obj) if kind == "relationship" else obj.get("name")
            put(kind, None, name, obj)
    return images


def ref_of(image: Dict[str, Any]) -> str:
    return make_ref(image["kind"], image.get("table"), image.get("name"))


def _lineage(image: Dict[str, Any]) -> Optional[str]:
    tag = (image.get("json") or {}).get("lineageTag")
    return str(tag) if tag else None


def _change(kind, table, name, change, pre, post, pre_ref=None, pre_name=None):
    rec = {
        "ref": make_ref(kind, table, name),
        "kind": kind,
        "table": table,
        "name": name,
        "change": change,
        "pre": pre,
        "post": post,
    }
    if pre_ref is not None:
        rec["pre_ref"] = pre_ref
        rec["pre_name"] = pre_name
    return rec


def diff_maps(
    pre: Dict[str, Dict[str, Any]], post: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Changes turning image map *pre* into *post* (see the module docstring)."""
    pre = dict(pre)
    # 1. table renames (lineageTag): move the renamed table's children to the new name so an
    #    untouched child is not reported as removed+added.
    gone_tables = {r: i for r, i in pre.items() if i["kind"] == "table" and r not in post}
    new_tables = {r: i for r, i in post.items() if i["kind"] == "table" and r not in pre}
    table_map: Dict[str, str] = {}
    for oref, oimg in gone_tables.items():
        tag = _lineage(oimg)
        if not tag:
            continue
        for nref, nimg in new_tables.items():
            if _lineage(nimg) == tag and nimg["name"] not in table_map.values():
                table_map[oimg["name"]] = nimg["name"]
                break
    if table_map:
        remapped: Dict[str, Dict[str, Any]] = {}
        for ref, img in pre.items():
            t = img.get("table")
            if img["kind"] in CHILD_KINDS and t in table_map:
                img = dict(img, table=table_map[t])
                ref = ref_of(img)
            remapped[ref] = img
        pre = remapped

    removed = [r for r in pre if r not in post]
    added = [r for r in post if r not in pre]
    changes: List[Dict[str, Any]] = []

    # 2. renames: same kind (+ table), same lineageTag
    used_added: set = set()
    renamed_removed: set = set()
    by_tag: Dict[Tuple[str, Optional[str], str], str] = {}
    for a in added:
        img = post[a]
        tag = _lineage(img)
        if tag:
            by_tag[(img["kind"], img.get("table"), tag)] = a
    for r in removed:
        img = pre[r]
        tag = _lineage(img)
        if not tag:
            continue
        a = by_tag.get((img["kind"], img.get("table"), tag))
        if a is None or a in used_added:
            continue
        used_added.add(a)
        renamed_removed.add(r)
        nimg = post[a]
        changes.append(
            _change(
                nimg["kind"],
                nimg.get("table"),
                nimg["name"],
                "renamed",
                img["json"],
                nimg["json"],
                pre_ref=r,
                pre_name=img["name"],
            )
        )
    for r in removed:
        if r in renamed_removed:
            continue
        img = pre[r]
        changes.append(
            _change(img["kind"], img.get("table"), img["name"], "removed", img["json"], None)
        )
    for a in added:
        if a in used_added:
            continue
        img = post[a]
        changes.append(
            _change(img["kind"], img.get("table"), img["name"], "added", None, img["json"])
        )
    for ref, img in post.items():
        old = pre.get(ref)
        if old is not None and old["json"] != img["json"]:
            changes.append(
                _change(
                    img["kind"],
                    img.get("table"),
                    img["name"],
                    "modified",
                    old["json"],
                    img["json"],
                )
            )
    changes.sort(key=lambda c: (_KIND_ORDER.get(c["kind"], 99), c["ref"]))
    return changes


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def subset_match(expected: Any, live: Any) -> bool:
    """True when every key/value in *expected* is present in *live* (extra live keys ignored).

    A post-image is captured before ``SaveChanges``; the server may then add inferred keys
    (lineage tags, ...) to the object. Comparing "expected is contained in live" keeps undo /
    replay from reporting a false conflict for those.
    """
    if isinstance(expected, dict):
        if not isinstance(live, dict):
            return False
        return all(k in live and subset_match(v, live[k]) for k, v in expected.items())
    if isinstance(expected, list):
        if not isinstance(live, list):
            return False
        if expected and all(isinstance(e, dict) and "name" in e for e in expected):
            by_name = {e.get("name"): e for e in live if isinstance(e, dict)}
            return all(
                e["name"] in by_name and subset_match(e, by_name[e["name"]]) for e in expected
            )
        if len(expected) != len(live):
            return False
        return all(subset_match(e, x) for e, x in zip(expected, live))
    return expected == live


def same_image(a: Any, b: Any) -> bool:
    """Both directions of :func:`subset_match`."""
    return subset_match(a, b) and subset_match(b, a)


_ABSENT = object()


def _value_matches(expected: Any, live: Any) -> bool:
    """Per-key match; an absent key only matches an absent key (a removal is a value too)."""
    if expected is _ABSENT or live is _ABSENT:
        return expected is live
    return subset_match(expected, live)


def three_way_merge(
    base: Optional[Dict[str, Any]],
    target: Optional[Dict[str, Any]],
    live: Optional[Dict[str, Any]],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Property-level three-way merge used by undo (base = entry post, target = entry pre) and
    replay (base = entry pre, target = entry post).

    Only keys whose value differs between *base* and *target* are touched - a key present on one
    side and absent on the other counts (TMSL omits default values, so unhide / clear-description
    is a removal). For each such key: live still equals *base* -> move it to *target* (or delete
    it); live already equals *target* -> nothing to do; anything else changed since the entry and
    is a conflict (listed per key, left as live) unless *force*. Keys the entry never touched are
    copied from *live* untouched.

    Returns ``{"merged", "changed", "conflicts", "already"}`` (key lists, sorted).
    """
    base, target, live = base or {}, target or {}, live or {}
    merged = dict(live)
    changed: List[str] = []
    conflicts: List[str] = []
    already: List[str] = []
    for key in sorted(set(base) | set(target)):
        b = base.get(key, _ABSENT)
        t = target.get(key, _ABSENT)
        if _value_matches(b, t) and _value_matches(t, b):
            continue
        lv = live.get(key, _ABSENT)
        if not _value_matches(b, lv):  # live moved on since the entry's base
            if _value_matches(t, lv):
                already.append(key)
                continue
            if not force:
                conflicts.append(key)
                continue
        if t is _ABSENT:
            merged.pop(key, None)
        else:
            merged[key] = t
        changed.append(key)
    return {"merged": merged, "changed": changed, "conflicts": conflicts, "already": already}


# ---------------------------------------------------------------------------
# Readable diff
# ---------------------------------------------------------------------------
def _short(value: Any, limit: int = 400) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"... [{len(value) - limit} more chars]"
    if isinstance(value, (dict, list)):
        text = repr(value)
        return value if len(text) <= limit else text[:limit] + "..."
    return value


def describe_change(change: Dict[str, Any]) -> Dict[str, Any]:
    """Human-readable form of one journal change (property-level diff)."""
    out: Dict[str, Any] = {
        k: change.get(k) for k in ("ref", "kind", "change") if change.get(k) is not None
    }
    if change.get("pre_ref"):
        out["renamed_from"] = change["pre_ref"]
    pre = change.get("pre") or {}
    post = change.get("post") or {}
    if change["change"] == "added":
        out["created"] = {k: _short(v) for k, v in post.items()}
        return out
    if change["change"] == "removed":
        out["deleted"] = {k: _short(v) for k, v in pre.items()}
        return out
    props: Dict[str, Any] = {}
    for key in sorted(set(pre) | set(post)):
        a, b = pre.get(key), post.get(key)
        if a == b:
            continue
        entry: Dict[str, Any] = {"pre": _short(a), "post": _short(b)}
        if isinstance(a, str) and isinstance(b, str) and ("\n" in a or "\n" in b or len(a) > 120):
            lines = list(
                difflib.unified_diff(
                    a.splitlines(), b.splitlines(), "pre", "post", lineterm="", n=1
                )
            )
            if lines:
                entry["diff"] = lines[:60]
        props[key] = entry
    out["properties"] = props
    return out


def count_by_kind(changes: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for ch in changes:
        counts[ch["kind"]] = counts.get(ch["kind"], 0) + 1
    return counts
