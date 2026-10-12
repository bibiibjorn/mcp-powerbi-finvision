"""Parse DAX logical/physical plan text + xmSQL queries into structured trees.

Plan-text grammar (as the engine actually emits it — see the ``pbi-query-plan``
skill's ``plan-operator-reference.md``)::

    AggregationSpool<Cache>: SpoolPhyOp #Records=16
    Spool_Iterator<Spool>: IterPhyOp IterCols(0)('Product'[ColorName]) #Records=16
    Sum_Vertipaq: ScaLogOp DependOnCols(0)('Product'[ColorName]) ...

* One leading TAB = one nesting level (some traces indent with spaces instead).
* The operator name is everything before the first colon; a ``<...>`` suffix is
  the spool kind (``AggregationSpool<GroupBy>`` → base ``AggregationSpool``,
  kind ``GroupBy``).
* The operator *class* is the first token after the colon: physical plans use
  ``IterPhyOp`` / ``LookupPhyOp`` / ``SpoolPhyOp`` (note ``PhyOp``, not
  ``PhysOp``), logical plans use ``RelLogOp`` / ``ScaLogOp``.
* ``#Records`` is the single most important number in a physical plan.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional

# One tab renders as this many columns when a plan mixes tabs and spaces.
_TAB_WIDTH = 4

_OP_CLASSES = frozenset(
    {"IterPhyOp", "LookupPhyOp", "SpoolPhyOp", "RelLogOp", "ScaLogOp", "RelPhyOp", "ScaPhyOp"}
)

# `#Records=16`, `#KeyCols=2`, `#Recs=16` (older builds shorten Records).
_COUNT_RE = re.compile(r"#(Records|Recs|KeyCols|ValueCols|FieldCols)\s*=\s*(\d+)")
# `IterCols(0, 1)('Product'[ColorName], 'Date'[Date])` — grab the whole thing.
_COLS_RE = re.compile(
    r"\b(IterCols|LookupCols|DependOnCols|RequiredCols)\s*(\([^)]*\)(?:\s*\([^)]*\))?)"
)
_DOMINANT_RE = re.compile(r"\bDominantValue\s*=\s*(\S+)")


def _indent_width(line: str) -> int:
    """Leading-whitespace width, counting a tab as ``_TAB_WIDTH`` columns."""
    width = 0
    for ch in line:
        if ch == "\t":
            width += _TAB_WIDTH
        elif ch == " ":
            width += 1
        else:
            break
    return width


def _parse_line(body: str) -> Dict[str, Any]:
    """Parse one stripped plan line into a node dict (without children)."""
    head, _, tail = body.partition(":")
    operator = head.strip() or "?"
    rest = tail.strip()

    spool_kind: Optional[str] = None
    operator_base = operator
    if operator.endswith(">") and "<" in operator:
        operator_base, _, kind = operator.partition("<")
        operator_base = operator_base.strip()
        spool_kind = kind[:-1].strip() or None

    op_class: Optional[str] = None
    if rest:
        first = rest.split(None, 1)[0].rstrip(":")
        if first in _OP_CLASSES:
            op_class = first

    counts: Dict[str, int] = {}
    recs_fallback: Optional[int] = None
    for name, value in _COUNT_RE.findall(body):
        if name == "Recs":
            # Legacy short spelling — only used when `#Records` is absent.
            if recs_fallback is None:
                recs_fallback = int(value)
            continue
        counts.setdefault(name, int(value))
    if "Records" not in counts and recs_fallback is not None:
        counts["Records"] = recs_fallback

    cols: Dict[str, str] = {}
    for name, payload in _COLS_RE.findall(body):
        cols.setdefault(name, f"{name}{payload}")

    dominant = _DOMINANT_RE.search(body)

    return {
        "operator": operator,
        "operator_base": operator_base,
        "spool_kind": spool_kind,
        "op_class": op_class,
        "records": counts.get("Records"),
        "key_cols": counts.get("KeyCols"),
        "value_cols": counts.get("ValueCols"),
        "field_cols": counts.get("FieldCols"),
        "iter_cols": cols.get("IterCols"),
        "lookup_cols": cols.get("LookupCols"),
        "depend_on_cols": cols.get("DependOnCols"),
        "required_cols": cols.get("RequiredCols"),
        "dominant_value": dominant.group(1) if dominant else None,
        "raw": body,
        "children": [],
    }


def parse_logical_plan(text: str) -> Dict[str, Any]:
    """Parse DAX plan text (logical OR physical) into an operator tree.

    Kept under the historical name for compatibility — it handles both plan
    kinds, since they share the indentation grammar and differ only in the
    operator class suffix.

    ``depth`` is the raw indentation width (tabs expanded), matching the
    previous behaviour so depth-based heuristics keep working. ``nesting`` is
    the normalized 0-based nesting level. The synthetic ROOT carries
    ``depth: -1``.
    """
    root: Dict[str, Any] = {
        "operator": "ROOT",
        "operator_base": "ROOT",
        "spool_kind": None,
        "op_class": None,
        "depth": -1,
        "nesting": -1,
        "records": None,
        "key_cols": None,
        "value_cols": None,
        "field_cols": None,
        "iter_cols": None,
        "lookup_cols": None,
        "depend_on_cols": None,
        "required_cols": None,
        "dominant_value": None,
        "raw": "",
        "children": [],
    }
    stack: List[Dict[str, Any]] = [root]
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        depth = _indent_width(line)
        node = _parse_line(line.strip())
        node["depth"] = depth
        while len(stack) > 1 and stack[-1]["depth"] >= depth:
            stack.pop()
        node["nesting"] = stack[-1]["nesting"] + 1
        stack[-1]["children"].append(node)
        stack.append(node)
    return root


def walk(tree: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Yield every node in the tree (DFS)."""
    yield tree
    for c in tree.get("children", []):
        yield from walk(c)


def max_records(tree: Dict[str, Any]) -> Optional[int]:
    """Largest ``#Records`` anywhere in the tree, or None when unannotated."""
    best: Optional[int] = None
    for node in walk(tree):
        rec = node.get("records")
        if isinstance(rec, int) and (best is None or rec > best):
            best = rec
    return best


def operator_histogram(tree: Dict[str, Any]) -> Dict[str, int]:
    """Count of each operator name in the tree (excluding the synthetic ROOT)."""
    hist: Dict[str, int] = {}
    for node in walk(tree):
        op = str(node.get("operator", ""))
        if not op or op == "ROOT":
            continue
        hist[op] = hist.get(op, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: (-kv[1], kv[0])))


def plan_summary(tree: Dict[str, Any]) -> Dict[str, Any]:
    """Headline numbers for a parsed plan: max #Records + its operator."""
    top: Optional[Dict[str, Any]] = None
    count = 0
    for node in walk(tree):
        if node.get("operator") == "ROOT" and node.get("depth") == -1:
            continue
        count += 1
        rec = node.get("records")
        if isinstance(rec, int) and (top is None or rec > int(top["records"])):
            top = node
    return {
        "max_records": top["records"] if top else None,
        "max_records_operator": top["operator"] if top else None,
        "node_count": count,
        "operator_histogram": operator_histogram(tree),
    }


# --------------------------------------------------------------------------- #
# xmSQL
# --------------------------------------------------------------------------- #

# The six callback forms the SE can emit into an xmSQL query.
_CALLBACK_FORMS = (
    "CallbackDataID",
    "EncodeCallback",
    "LogAbsValueCallback",
    "RoundValueCallback",
    "MinMaxColumnPositionCallback",
    "Cond",
)

_JOIN_FORMS = {
    "LEFT OUTER JOIN": r"LEFT\s+OUTER\s+JOIN",
    "INNER JOIN": r"(?<!REVERSE\s)INNER\s+JOIN",
    "REVERSE BITMAP JOIN": r"REVERSE\s+BITMAP\s+JOIN",
    "REVERSE HASH JOIN": r"REVERSE\s+HASH\s+JOIN",
}

# `Estimated size : 16, 128` and
# `Estimated size (volume, marshalling bytes): 16, 128`
_EST_SIZE_RE = re.compile(
    r"Estimated\s+size\s*(?:\([^)]*\))?\s*:\s*([\d,]+)\s*,\s*([\d,]+)",
    re.IGNORECASE,
)
_IN_LIST_RE = re.compile(r"\bINB?\s*\(([^)]*)\)", re.IGNORECASE)

#: An `IN (...)` list longer than this is a materialized filter list big enough
#: to matter (the FE had to push every distinct value into the SE query).
LARGE_IN_LIST_THRESHOLD = 100


def _count_values(payload: str) -> int:
    return len([v for v in payload.split(",") if v.strip()])


def parse_xmsql(text: str) -> Dict[str, Any]:
    """Structured summary of one xmSQL storage-engine query.

    The first three keys (``select_count``, ``from_tables``,
    ``has_distinctcount``) are the original contract and must not change shape —
    other code depends on them.
    """
    empty: Dict[str, Any] = {
        "select_count": 0,
        "from_tables": [],
        "has_distinctcount": False,
        "estimated_rows": None,
        "estimated_kb": None,
        "estimated_bytes": None,
        "callbacks": [],
        "callback_count": 0,
        "joins": {},
        "join_count": 0,
        "has_define_table": False,
        "has_inindex": False,
        "is_semijoin": False,
        "in_list_max_size": None,
        "has_large_in_list": False,
    }
    if not text:
        return empty

    select_count = len(re.findall(r"\bSELECT\b", text, re.IGNORECASE))
    from_tables = sorted({m.group(1) for m in re.finditer(r"FROM\s+'([^']+)'", text)})
    has_distinct = bool(re.search(r"DISTINCTCOUNT|DCOUNT", text, re.IGNORECASE))

    estimated_rows: Optional[int] = None
    estimated_bytes: Optional[int] = None
    estimated_kb: Optional[float] = None
    m_size = _EST_SIZE_RE.search(text)
    if m_size:
        estimated_rows = int(m_size.group(1).replace(",", ""))
        estimated_bytes = int(m_size.group(2).replace(",", ""))
        estimated_kb = round(estimated_bytes / 1024.0, 2)

    callbacks = [f for f in _CALLBACK_FORMS if re.search(rf"\b{f}\b", text)]
    callback_count = sum(len(re.findall(rf"\b{f}\b", text)) for f in callbacks)

    joins: Dict[str, int] = {}
    for label, pattern in _JOIN_FORMS.items():
        n = len(re.findall(pattern, text, re.IGNORECASE))
        if n:
            joins[label] = n

    has_define_table = bool(re.search(r"\bDEFINE\s+TABLE\b", text, re.IGNORECASE))
    has_inindex = bool(re.search(r"\bININDEX\b", text, re.IGNORECASE))

    in_sizes = [_count_values(m.group(1)) for m in _IN_LIST_RE.finditer(text)]
    in_list_max = max(in_sizes) if in_sizes else None

    return {
        "select_count": select_count,
        "from_tables": from_tables,
        "has_distinctcount": has_distinct,
        "estimated_rows": estimated_rows,
        "estimated_kb": estimated_kb,
        "estimated_bytes": estimated_bytes,
        "callbacks": callbacks,
        "callback_count": callback_count,
        "joins": joins,
        "join_count": sum(joins.values()),
        "has_define_table": has_define_table,
        "has_inindex": has_inindex,
        "is_semijoin": has_define_table and has_inindex,
        "in_list_max_size": in_list_max,
        "has_large_in_list": bool(in_list_max and in_list_max > LARGE_IN_LIST_THRESHOLD),
    }
