"""Find every reference to a measure across a TOM model (review E-06).

``inline_measure delete_after`` used to delete the target even when it was still
referenced from places the transform never scans. Before a codemod delete, the
persister calls :func:`find_measure_references` on the IN-MEMORY TOM model (after
the rewrites were applied, before ``SaveChanges``), covering every DAX-bearing
object kind:

- measure expressions, dynamic format strings, detail-rows expressions, KPI
  target/status/trend expressions;
- calculated columns and calculated-table partitions;
- calculation items (+ their format-string expressions);
- RLS table-permission filter expressions;
- model functions (UDFs; AMO often exposes none — see LIVE-CHECKS.md).

Pure and duck-typed on the PascalCase TOM surface, so tests use fakes.
"""

from __future__ import annotations

from typing import Any, Iterator, List, Optional, Set, Tuple

from core.codemods import dax_text


def _iter(coll: Any) -> List[Any]:
    if coll is None:
        return []
    try:
        return list(coll)
    except Exception:  # noqa: BLE001 - CLR collections
        return []


def _expr(obj: Any, *attrs: str) -> Optional[str]:
    cur = obj
    for a in attrs:
        if cur is None:
            return None
        try:
            cur = getattr(cur, a, None)
        except Exception:  # noqa: BLE001
            return None
    if cur is None:
        return None
    s = str(cur)
    return s if s.strip() else None


def iter_dax_expressions(model: Any) -> Iterator[Tuple[str, str, Optional[str], Set[str]]]:
    """Yield ``(kind, owner, expression, column_names_in_row_context)`` for every
    DAX expression in *model*. The last element lists the column names of the
    table whose row context the expression runs in (a lone ``[X]`` there may be
    a column, not a measure)."""
    for t in _iter(getattr(model, "Tables", None)):
        tname = str(getattr(t, "Name", ""))
        cols = {
            dax_text.norm_name(str(getattr(c, "Name", "")))
            for c in _iter(getattr(t, "Columns", None))
        }
        for m in _iter(getattr(t, "Measures", None)):
            owner = f"{tname}[{getattr(m, 'Name', '')}]"
            yield "measure", owner, _expr(m, "Expression"), set()
            yield "measure_format_string", owner, _expr(
                m, "FormatStringDefinition", "Expression"
            ), set()
            yield "measure_detail_rows", owner, _expr(
                m, "DetailRowsDefinition", "Expression"
            ), set()
            kpi = getattr(m, "KPI", None)
            if kpi is not None:
                for attr in ("TargetExpression", "StatusExpression", "TrendExpression"):
                    yield "kpi", owner, _expr(kpi, attr), set()
        for c in _iter(getattr(t, "Columns", None)):
            e = _expr(c, "Expression")
            if e is not None:
                yield "calculated_column", f"{tname}[{getattr(c, 'Name', '')}]", e, cols
        for p in _iter(getattr(t, "Partitions", None)):
            src = getattr(p, "Source", None)
            e = _expr(src, "Expression")
            if e is not None and "Calculated" in type(src).__name__ + str(
                getattr(p, "SourceType", "")
            ):
                yield "calculated_table", tname, e, set()
        cg = getattr(t, "CalculationGroup", None)
        if cg is not None:
            for item in _iter(getattr(cg, "CalculationItems", None)):
                owner = f"{tname}.{getattr(item, 'Name', '')}"
                yield "calculation_item", owner, _expr(item, "Expression"), set()
                yield "calculation_item_format_string", owner, _expr(
                    item, "FormatStringDefinition", "Expression"
                ), set()
    tables_cols = {
        str(getattr(t, "Name", "")): {
            dax_text.norm_name(str(getattr(c, "Name", "")))
            for c in _iter(getattr(t, "Columns", None))
        }
        for t in _iter(getattr(model, "Tables", None))
    }
    for role in _iter(getattr(model, "Roles", None)):
        for tp in _iter(getattr(role, "TablePermissions", None)):
            tbl = getattr(tp, "Table", None)
            tname = str(getattr(tbl, "Name", "") if tbl is not None else getattr(tp, "Name", ""))
            yield (
                "rls_filter",
                f"{getattr(role, 'Name', '')}:{tname}",
                _expr(tp, "FilterExpression"),
                tables_cols.get(tname, set()),
            )
    for fn in _iter(getattr(model, "Functions", None)):
        yield "function", str(getattr(fn, "Name", "")), _expr(fn, "Expression"), set()


def find_measure_references(
    model: Any,
    table: str,
    measure: str,
    *,
    exclude_owners: Optional[Set[str]] = None,
) -> List[dict]:
    """Every place in *model* that still references measure ``table[measure]``.

    ``exclude_owners``: owners (``"T[M]"``) to skip — the measure itself.
    Returns ``[{kind, owner, count}]`` (empty -> safe to delete as far as the
    model is concerned).
    """
    skip = {dax_text.norm_name(o) for o in (exclude_owners or set())}
    target = dax_text.norm_name(measure)
    hits: List[dict] = []
    for kind, owner, expr, row_cols in iter_dax_expressions(model):
        if not expr or dax_text.norm_name(owner) in skip:
            continue
        sites = dax_text.measure_ref_sites(expr, measure, table)
        if target in row_cols:
            # In a row context of a table that has a column with that name a
            # lone [X] is the column; only qualified refs count there.
            sites = [s for s in sites if s.qualified]
        if sites:
            hits.append({"kind": kind, "owner": owner, "count": len(sites)})
    return hits
