"""DAX reference cascade for TOM rename operations (review C-01, C-04, C-06).

TOM/AMO does NOT rewrite DAX text when an object is renamed: every expression
that names the old measure/column/table must be updated by us. Structural
references (relationships, sort-by, hierarchy levels, perspectives, culture
translations) are object pointers and survive on their own.

The cascade is lexer-driven (``core.dax.dax_rename``), so:

* string literals and comments are never rewritten;
* bare table references are cascaded (``COUNTROWS(Old)``, ``ALL(Old)``);
* the new table name is always emitted quoted with ``''`` escaping, the new
  column/measure name bracketed with ``]]`` escaping (no regex replacement
  strings, so a ``\\`` in a name is literal);
* ``Sales[Amount]`` never matches ``MySales[Amount]``.

Sites visited: measure expressions, format-string definitions, detail-rows
definitions, KPI target/status/trend, calculated columns, **calculated**
partitions only (Power Query M partitions are never touched — an M record
field ``[Value]`` is not a DAX reference), calculation items (+ their format
strings), calc-group selection expressions, RLS filters. JSON linguistic
metadata in cultures is cascaded where it is unambiguous; everything the
cascade cannot reach is reported as ``untouched``.

Every function returns the list of updated object paths (backward compatible)
and, when ``record`` is given, appends one dict per site with ``path``, ``kind``,
``before`` and ``after``. Records carry a private ``_restore`` callable that
puts the ``before`` text back — :func:`restore_records` uses it to roll a
cascade back exactly. ``apply=False`` computes the records without mutating
anything (rename preview).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from core.dax.dax_names import column_ref
from core.dax.dax_reference_parser import scan_references
from core.dax.dax_rename import rename_column_refs, rename_measure_refs, rename_table_refs

logger = logging.getLogger(__name__)

__all__ = [
    "cascade_column_rename",
    "cascade_measure_rename",
    "cascade_table_rename",
    "is_calculated_partition",
    "public_records",
    "restore_records",
]

Getter = Callable[[], Optional[str]]
Setter = Callable[[str], None]


# --------------------------------------------------------------------------- sites


class _Site:
    __slots__ = ("path", "kind", "context_table", "get", "set")

    def __init__(
        self, path: str, kind: str, context_table: Optional[str], get: Getter, set: Setter
    ):
        self.path = path
        self.kind = kind
        self.context_table = context_table
        self.get = get
        self.set = set


def _iter(coll) -> List[Any]:
    if coll is None:
        return []
    try:
        return list(coll)
    except TypeError:
        return []


def _attr_site(obj, attr: str, path: str, kind: str, ctx: Optional[str]) -> Optional[_Site]:
    if obj is None:
        return None
    try:
        value = getattr(obj, attr, None)
    except Exception:  # noqa: BLE001 - CLR property getter
        return None
    if not isinstance(value, str) or not value:
        return None

    def _get(o=obj, a=attr):
        return getattr(o, a, None)

    def _set(v, o=obj, a=attr):
        setattr(o, a, v)

    return _Site(path, kind, ctx, _get, _set)


def is_calculated_partition(partition) -> bool:
    """True only for a DAX (calculated) partition source — never Power Query M."""
    st = getattr(partition, "SourceType", None)
    if st is not None:
        return str(st).split(".")[-1] == "Calculated"
    src = getattr(partition, "Source", None)
    return type(src).__name__ == "CalculatedPartitionSource"


def _iter_sites(model) -> Iterator[_Site]:
    for tbl in _iter(getattr(model, "Tables", None)):
        tname = getattr(tbl, "Name", "")
        for m in _iter(getattr(tbl, "Measures", None)):
            base = f"'{tname}'[{getattr(m, 'Name', '')}]"
            for site in (
                _attr_site(m, "Expression", base, "measure", tname),
                _attr_site(
                    getattr(m, "FormatStringDefinition", None),
                    "Expression",
                    f"{base} (format string)",
                    "format_string",
                    tname,
                ),
                _attr_site(
                    getattr(m, "DetailRowsDefinition", None),
                    "Expression",
                    f"{base} (detail rows)",
                    "detail_rows",
                    tname,
                ),
            ):
                if site:
                    yield site
            kpi = getattr(m, "KPI", None)
            for attr in ("TargetExpression", "StatusExpression", "TrendExpression"):
                site = _attr_site(kpi, attr, f"{base} (KPI {attr})", "kpi", tname)
                if site:
                    yield site
        for col in _iter(getattr(tbl, "Columns", None)):
            site = _attr_site(
                col,
                "Expression",
                f"'{tname}'[{getattr(col, 'Name', '')}] (calc column)",
                "calc_column",
                tname,
            )
            if site:
                yield site
        for part in _iter(getattr(tbl, "Partitions", None)):
            if not is_calculated_partition(part):
                continue
            site = _attr_site(
                getattr(part, "Source", None),
                "Expression",
                f"'{tname}' (calc table / field parameter)",
                "calc_partition",
                None,
            )
            if site:
                yield site
        cg = getattr(tbl, "CalculationGroup", None)
        if cg is not None:
            for item in _iter(getattr(cg, "CalculationItems", None)):
                iname = getattr(item, "Name", "")
                for site in (
                    _attr_site(
                        item, "Expression", f"'{tname}' calc item '{iname}'", "calc_item", None
                    ),
                    _attr_site(
                        getattr(item, "FormatStringDefinition", None),
                        "Expression",
                        f"'{tname}' calc item '{iname}' (format string)",
                        "calc_item_format_string",
                        None,
                    ),
                ):
                    if site:
                        yield site
            for attr in ("MultipleOrEmptySelectionExpression", "NoSelectionExpression"):
                site = _attr_site(
                    getattr(cg, attr, None),
                    "Expression",
                    f"'{tname}' ({attr})",
                    "calc_group_selection",
                    None,
                )
                if site:
                    yield site
    for role in _iter(getattr(model, "Roles", None)):
        for tp in _iter(getattr(role, "TablePermissions", None)):
            target = getattr(getattr(tp, "Table", None), "Name", None) or getattr(tp, "Name", None)
            site = _attr_site(
                tp,
                "FilterExpression",
                f"Role '{getattr(role, 'Name', '')}' -> '{target}' (RLS)",
                "rls",
                target,
            )
            if site:
                yield site


# ------------------------------------------------------------------------ driver


def _run(
    model,
    rewrite: Callable[[_Site, str], Tuple[str, int]],
    *,
    record: Optional[List[Dict[str, Any]]],
    apply: bool,
) -> List[str]:
    updated: List[str] = []
    for site in list(_iter_sites(model)):
        before = site.get()
        if not isinstance(before, str) or not before:
            continue
        try:
            after, count = rewrite(site, before)
        except Exception as e:  # noqa: BLE001 - never let one bad expression abort the cascade
            logger.warning("rename cascade skipped %s: %s", site.path, e)
            if record is not None:
                record.append(
                    {"path": site.path, "kind": "untouched", "reason": f"lexer error: {e}"}
                )
            continue
        if count <= 0 or after == before:
            continue
        if apply:
            site.set(after)
        updated.append(site.path)
        if record is not None:
            record.append(
                {
                    "path": site.path,
                    "kind": site.kind,
                    "before": before,
                    "after": after,
                    "_restore": (lambda s=site, b=before: s.set(b)),
                }
            )
    return updated


def restore_records(record: List[Dict[str, Any]]) -> int:
    """Put every recorded site back to its ``before`` text; returns the count."""
    restored = 0
    for rec in reversed(record or []):
        fn = rec.get("_restore")
        if callable(fn):
            try:
                fn()
                restored += 1
            except Exception as e:  # noqa: BLE001
                logger.error("restore of %s failed: %s", rec.get("path"), e)
    return restored


def public_records(record: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Records without the private ``_restore`` callables (JSON-safe)."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in record or []]


def _norm(name: Optional[str]) -> str:
    return " ".join((name or "").lower().split())


def _measure_names(model) -> set:
    return {
        _norm(getattr(m, "Name", ""))
        for t in _iter(getattr(model, "Tables", None))
        for m in _iter(getattr(t, "Measures", None))
    }


def _table_columns(model, table_name: str) -> set:
    for t in _iter(getattr(model, "Tables", None)):
        if getattr(t, "Name", None) == table_name:
            return {_norm(getattr(c, "Name", "")) for c in _iter(getattr(t, "Columns", None))}
    return set()


# -------------------------------------------------------- linguistic metadata (C-01)


def _cascade_linguistic(
    model,
    mutate: Callable[[Any], int],
    *,
    record: Optional[List[Dict[str, Any]]],
    apply: bool,
) -> List[str]:
    """Rewrite JSON linguistic metadata via *mutate(parsed_json) -> count*."""
    updated: List[str] = []
    for culture in _iter(getattr(model, "Cultures", None)):
        lm = getattr(culture, "LinguisticMetadata", None)
        content = getattr(lm, "Content", None) if lm is not None else None
        if not isinstance(content, str) or not content.strip():
            continue
        cname = getattr(culture, "Name", "")
        ctype = str(getattr(lm, "ContentType", "Json")).split(".")[-1].lower()
        path = f"Culture '{cname}' (linguistic metadata)"
        if ctype != "json":
            if record is not None:
                record.append(
                    {"path": path, "kind": "untouched", "reason": "XML linguistic metadata"}
                )
            continue
        try:
            doc = json.loads(content)
        except ValueError:
            if record is not None:
                record.append({"path": path, "kind": "untouched", "reason": "unparseable JSON"})
            continue
        if mutate(doc) <= 0:
            continue
        after = json.dumps(doc, indent=2, ensure_ascii=False)
        if apply:
            lm.Content = after
        updated.append(path)
        if record is not None:
            record.append(
                {
                    "path": path,
                    "kind": "linguistic_metadata",
                    "before": content,
                    "after": after,
                    "_restore": (lambda o=lm, b=content: setattr(o, "Content", b)),
                }
            )
    return updated


def _walk_bindings(node, fn: Callable[[Dict[str, Any]], int]) -> int:
    count = 0
    if isinstance(node, dict):
        count += fn(node)
        for v in node.values():
            count += _walk_bindings(v, fn)
    elif isinstance(node, list):
        for v in node:
            count += _walk_bindings(v, fn)
    return count


# ---------------------------------------------------------------------- public API


def cascade_measure_rename(
    model,
    old_name: str,
    new_name: str,
    table_name: str = "",
    *,
    record: Optional[List[Dict[str, Any]]] = None,
    apply: bool = True,
) -> List[str]:
    """Rewrite references to measure *old_name* (hosted on *table_name*) as *new_name*.

    Lone ``[old]`` everywhere; ``'Home'[old]`` too unless the home table has a
    COLUMN named *old* (then the qualified form is that column). In calculated
    columns / RLS filters of a table that owns a column named *old*, a lone
    ``[old]`` is that column and is left alone. A bare ``[new]`` passed to
    ``NAMEOF`` is qualified as ``'Home'[new]`` (NAMEOF needs the table for
    names with special characters).
    """
    if not table_name:
        candidates = []
        for tbl in _iter(getattr(model, "Tables", None)):
            for m in _iter(getattr(tbl, "Measures", None)):
                if getattr(m, "Name", None) in (new_name, old_name):
                    candidates.append(getattr(tbl, "Name", ""))
                    break
        if len(candidates) > 1:
            raise ValueError(
                f"Ambiguous measure rename: measure '{new_name}' exists in "
                f"multiple tables {candidates}. Pass table_name explicitly."
            )
        table_name = candidates[0] if candidates else ""

    home_cols = _table_columns(model, table_name) if table_name else set()
    home_for_qualified = table_name if table_name and _norm(old_name) not in home_cols else None
    col_cache: Dict[str, set] = {}

    def _ctx_has_column(ctx: Optional[str]) -> bool:
        if not ctx:
            return False
        if ctx not in col_cache:
            col_cache[ctx] = _table_columns(model, ctx)
        return _norm(old_name) in col_cache[ctx]

    def _rewrite(site: _Site, expr: str) -> Tuple[str, int]:
        if site.kind in ("calc_column", "rls") and _ctx_has_column(site.context_table):
            if not home_for_qualified:
                return expr, 0
            new_expr, n = rename_column_refs(expr, home_for_qualified, old_name, new_name)
        else:
            new_expr, n = rename_measure_refs(
                expr, old_name, new_name, home_table=home_for_qualified
            )
        if n and table_name:
            new_expr = _qualify_nameof(new_expr, new_name, table_name)
        return new_expr, n

    updated = _run(model, _rewrite, record=record, apply=apply)

    if table_name:

        def _mutate(doc) -> int:
            def fn(d):
                if (
                    d.get("ConceptualEntity") == table_name
                    and d.get("ConceptualProperty") == old_name
                ):
                    d["ConceptualProperty"] = new_name
                    return 1
                return 0

            return _walk_bindings(doc, fn)

        updated.extend(_cascade_linguistic(model, _mutate, record=record, apply=apply))
    return updated


def _qualify_nameof(expr: str, name: str, table: str) -> str:
    """``NAMEOF([name])`` -> ``NAMEOF('table'[name])`` (lexer spans, right to left)."""
    target = _norm(name)
    edits = [
        (r.start, r.end)
        for r in scan_references(expr)
        if r.kind == "bracket" and r.function == "NAMEOF" and _norm(r.name) == target
    ]
    for start, end in sorted(edits, reverse=True):
        expr = expr[:start] + column_ref(table, name) + expr[end:]
    return expr


def cascade_column_rename(
    model,
    table_name: str,
    old_name: str,
    new_name: str,
    *,
    record: Optional[List[Dict[str, Any]]] = None,
    apply: bool = True,
) -> List[str]:
    """Rewrite references to column ``table_name[old_name]`` as ``[new_name]``.

    Qualified ``'T'[old]`` / ``T[old]`` everywhere (never ``MyT[old]``). A lone
    ``[old]`` is rewritten only in expressions evaluated in *table_name*'s row
    context — its calculated columns, RLS filters on it, and its own measures
    (``SUMX(T, [old])``) — and only when no MEASURE named *old* exists (then a
    lone ``[old]`` is that measure).
    """
    lone_is_measure = _norm(old_name) in _measure_names(model)

    def _rewrite(site: _Site, expr: str) -> Tuple[str, int]:
        same_ctx = site.context_table == table_name and site.kind in (
            "calc_column",
            "rls",
            "measure",
            "format_string",
            "detail_rows",
            "kpi",
        )
        return rename_column_refs(
            expr,
            table_name,
            old_name,
            new_name,
            include_unqualified=bool(same_ctx and not lone_is_measure),
        )

    updated = _run(model, _rewrite, record=record, apply=apply)

    def _mutate(doc) -> int:
        def fn(d):
            if d.get("ConceptualEntity") == table_name and d.get("ConceptualProperty") == old_name:
                d["ConceptualProperty"] = new_name
                return 1
            return 0

        return _walk_bindings(doc, fn)

    updated.extend(_cascade_linguistic(model, _mutate, record=record, apply=apply))
    return updated


def cascade_table_rename(
    model,
    old_name: str,
    new_name: str,
    *,
    record: Optional[List[Dict[str, Any]]] = None,
    apply: bool = True,
) -> List[str]:
    """Rewrite every reference to table *old_name* (qualified or bare) as ``'new_name'``."""

    def _rewrite(_site: _Site, expr: str) -> Tuple[str, int]:
        return rename_table_refs(expr, old_name, new_name)

    updated = _run(model, _rewrite, record=record, apply=apply)

    def _mutate(doc) -> int:
        def fn(d):
            if d.get("ConceptualEntity") == old_name:
                d["ConceptualEntity"] = new_name
                return 1
            return 0

        return _walk_bindings(doc, fn)

    updated.extend(_cascade_linguistic(model, _mutate, record=record, apply=apply))
    return updated
