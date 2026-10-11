"""DAX query builder: measures + group-by columns + filters -> one SUMMARIZECOLUMNS query (F-20a).

Backs ``04_Run_DAX mode=build``. Pure text generation, no model access, so it is safe to call
without a connection; the handler optionally executes the result.

Inputs
------
``measures``  names (``"Total Sales"`` / ``"[Total Sales]"``) or ``{"name": alias, "expression": dax}``.
``group_by``  column refs (``"Date[Year]"`` / ``"'Date'[Year]"`` / ``{"table": ..., "column": ...}``).
``filters``   a filter is one of
              * ``{"column": "Date[Year]", "operator": "=", "values": [2025]}`` (operators ``=``, ``in``,
                ``<>``, ``not in``, ``>``, ``>=``, ``<``, ``<=``, ``between`` with ``values: [lo, hi]``),
              * a short string ``"Date[Year] = 2025"`` / ``"Product[Color] in {\"Red\", \"Blue\"}"``,
              * any other string, taken verbatim as a DAX TABLE expression (``TREATAS(...)``,
                ``FILTER(...)``, ``KEEPFILTERS(...)``): SUMMARIZECOLUMNS filter arguments must be tables.
              Literal values are numbers, booleans, strings, null, or ``{"dax": "DATE(2025,1,1)"}``.
``order_by``  ``"Date[Year] desc"`` / ``"[Total Sales] desc"`` / ``{"by": ..., "direction": "desc"}``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.dax.dax_names import bracket, column_ref, dax_string, split_qualified, unbracket

__all__ = ["BuildError", "build_query"]


class BuildError(ValueError):
    """The spec is not buildable (the message says what to change)."""


_OPS = {"=", "in", "<>", "not in", ">", ">=", "<", "<=", "between"}
_SIMPLE_RE = re.compile(
    r"^\s*(?P<col>(?:'(?:[^']|'')+'|[A-Za-z_][\w ]*)\[(?:[^\]]|\]\])+\])\s*"
    r"(?P<op>not\s+in|in|<>|>=|<=|=|>|<)\s*(?P<rest>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TABLE_FUNCS = (
    "TREATAS",
    "FILTER",
    "KEEPFILTERS",
    "CALCULATETABLE",
    "VALUES",
    "ALL",
    "ALLSELECTED",
    "SELECTCOLUMNS",
    "ADDCOLUMNS",
    "TOPN",
    "DISTINCT",
    "UNION",
    "INTERSECT",
    "EXCEPT",
    "TABLE",
    "DATESBETWEEN",
    "DATESINPERIOD",
    "DATESYTD",
)


def _literal(value: Any) -> str:
    if isinstance(value, dict):
        if "dax" in value and isinstance(value["dax"], str):
            return value["dax"]
        raise BuildError(f'unsupported filter value {value!r}: use a scalar or {{"dax": "..."}}')
    if value is None:
        return "BLANK()"
    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"
    if isinstance(value, (int, float)):
        return repr(value)
    return dax_string(str(value))


def _column(ref: Any) -> str:
    """``'Table'[Column]`` from a ``Table[Col]`` string or ``{table, column}``."""
    if isinstance(ref, dict):
        table, col = ref.get("table"), ref.get("column")
    else:
        table, col = split_qualified(str(ref or ""))
    if not table or not col:
        raise BuildError(f"column reference {ref!r} must look like Table[Column]")
    return column_ref(table, col)


def _split_values(rest: str) -> List[Any]:
    """Literals of ``{a, b}`` / ``a`` text (numbers, TRUE/FALSE, quoted strings, else verbatim DAX)."""
    inner = rest.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    parts: List[str] = []
    depth, cur, in_str = 0, "", False
    i = 0
    while i < len(inner):
        ch = inner[i]
        if in_str:
            cur += ch
            if ch == '"':
                if i + 1 < len(inner) and inner[i + 1] == '"':
                    cur += '"'
                    i += 1
                else:
                    in_str = False
        elif ch == '"':
            in_str = True
            cur += ch
        elif ch in "({":
            depth += 1
            cur += ch
        elif ch in ")}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    if cur.strip():
        parts.append(cur)
    out: List[Any] = []
    for raw in parts:
        t = raw.strip()
        if t.startswith('"') and t.endswith('"') and len(t) >= 2:
            out.append(t[1:-1].replace('""', '"'))
        elif re.fullmatch(r"-?\d+", t):
            out.append(int(t))
        elif re.fullmatch(r"-?\d*\.\d+", t):
            out.append(float(t))
        elif t.upper() in ("TRUE", "FALSE"):
            out.append(t.upper() == "TRUE")
        elif t:
            out.append({"dax": t})
    return out


def _filter(spec: Any) -> str:
    if isinstance(spec, str):
        m = _SIMPLE_RE.match(spec)
        if m:
            spec = {
                "column": m.group("col"),
                "operator": re.sub(r"\s+", " ", m.group("op").lower()),
                "values": _split_values(m.group("rest")),
            }
        else:
            text = spec.strip()
            head = re.match(r"([A-Za-z]+)\s*\(", text)
            if not head or head.group(1).upper() not in _TABLE_FUNCS:
                raise BuildError(
                    f"filter {spec!r} is neither 'Table[Col] <op> value' nor a DAX table "
                    f"expression starting with one of {', '.join(_TABLE_FUNCS[:6])}..."
                )
            return text
    if not isinstance(spec, dict):
        raise BuildError(f"filter {spec!r} must be a string or an object")
    col = _column(spec.get("column"))
    op = re.sub(r"\s+", " ", str(spec.get("operator") or "=").strip().lower())
    if op not in _OPS:
        raise BuildError(f"filter operator {op!r} must be one of {sorted(_OPS)}")
    values = spec.get("values", spec.get("value"))
    if values is None and op not in ("=", "in", "<>", "not in"):
        raise BuildError(f"filter on {spec.get('column')!r} needs values")
    vals = values if isinstance(values, list) else [values]
    lits = [_literal(v) for v in vals]
    if op in ("=", "in"):
        return f"TREATAS({{{', '.join(lits)}}}, {col})"
    if op in ("<>", "not in"):
        if len(lits) == 1:
            return f"FILTER(ALL({col}), {col} <> {lits[0]})"
        return f"FILTER(ALL({col}), NOT({col} IN {{{', '.join(lits)}}}))"
    if op == "between":
        if len(lits) != 2:
            raise BuildError("between needs exactly two values [low, high]")
        return f"FILTER(ALL({col}), {col} >= {lits[0]} && {col} <= {lits[1]})"
    if len(lits) != 1:
        raise BuildError(f"operator {op!r} needs exactly one value")
    return f"FILTER(ALL({col}), {col} {op} {lits[0]})"


def _measure(spec: Any) -> Tuple[str, str]:
    if isinstance(spec, dict):
        name, expr = spec.get("name"), spec.get("expression")
        if not name or not expr:
            raise BuildError(f"measure {spec!r} needs name and expression")
        return str(name), str(expr)
    text = str(spec or "").strip()
    if not text:
        raise BuildError("empty measure name")
    name = unbracket(text)
    return name, bracket(name)


def _order(spec: Any, measure_names: List[str]) -> str:
    direction = "ASC"
    if isinstance(spec, dict):
        by, d = spec.get("by"), spec.get("direction")
        direction = "DESC" if str(d or "").lower().startswith("d") else "ASC"
        text = str(by or "")
    else:
        text = str(spec or "").strip()
        m = re.match(r"^(?P<by>.+?)\s+(?P<d>asc|desc)$", text, re.IGNORECASE)
        if m:
            text, direction = m.group("by"), m.group("d").upper()
    if not text:
        raise BuildError("empty order_by entry")
    if text.startswith("[") or text in measure_names:
        return f"{bracket(unbracket(text))} {direction}"
    return f"{_column(text)} {direction}"


def build_query(
    measures: Optional[List[Any]] = None,
    group_by: Optional[List[Any]] = None,
    filters: Optional[List[Any]] = None,
    order_by: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Build ``EVALUATE SUMMARIZECOLUMNS(...)``. Raises :class:`BuildError` on a bad spec."""
    for label, value in (("measures", measures), ("group_by", group_by), ("filters", filters)):
        if value is not None and not isinstance(value, list):
            raise BuildError(f"{label} must be a list")
    measure_pairs = [_measure(m) for m in (measures or [])]
    groups = [_column(g) for g in (group_by or [])]
    if not measure_pairs and not groups:
        raise BuildError("pass at least one of measures or group_by")
    names = [n for n, _ in measure_pairs]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise BuildError(f"duplicate measure aliases: {dup}")
    args: List[str] = list(groups)
    args.extend(_filter(f) for f in (filters or []))
    for name, expr in measure_pairs:
        args.append(f"{dax_string(name)}, {expr}")
    body = ",\n    ".join(args)
    query = f"EVALUATE\nSUMMARIZECOLUMNS(\n    {body}\n)"
    if order_by:
        query += "\nORDER BY " + ", ".join(_order(o, names) for o in order_by)
    return {
        "query": query,
        "group_by": groups,
        "measures": names,
        "filter_count": len(filters or []),
    }
