"""DAX Studio ``QueryPlanRow`` parity plus plan analysis.

A DAX query plan arrives as one big tab-indented text blob in the TextData of a
``DAXQueryPlan`` (112) trace event.  DAX Studio turns it into rows where the
indent level *is* the tree depth.  The one non-obvious rule is cardinality
inheritance: in a physical plan many operators carry no ``#Records`` of their
own, and DAX Studio shows the cardinality of their direct children instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = [
    "QueryPlanRow",
    "QueryPlan",
    "parse_plan",
    "analyse_plan",
    "compare_plans",
    "HIGHLIGHT_RE",
]

#: DAX Studio highlights the rows where a datacache is produced or consumed.
HIGHLIGHT_RE = re.compile(r"Cache:|VertipaqResult:|DirectQueryResult|DataPostFilter:")

_RECORDS_RE = re.compile(r"#Records=(\d+)")
_RECS_RE = re.compile(r"#Recs=(\d+)")
#: ``GroupSemijoin: IterPhyOp LogOp=GroupSemiJoin ...`` -> ``GroupSemijoin: IterPhyOp``;
#: ``'T'[Col]: ScaLogOp DependOnCols(1)(...)`` -> ``'T'[Col]: ScaLogOp``.  The
#: name may contain spaces (quoted table names); it ends at the first ``: ``
#: followed by the operator CLASS token (F-25).
_OPERATOR_RE = re.compile(r"^(?P<name>.+?):\s+(?P<cls>[A-Za-z_]\w*)")


@dataclass
class QueryPlanRow:
    """One line of a query plan."""

    row_number: int
    level: int
    operation: str
    indented_operation: str
    records: int | None = None
    next_sibling_row_number: int = 0
    highlight_row: bool = False

    def to_export_dict(self, *, include_records: bool) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if include_records:
            out["Records"] = self.records
        out["Operation"] = self.operation
        out["IndentedOperation"] = self.indented_operation
        out["Level"] = self.level
        out["RowNumber"] = self.row_number
        out["NextSiblingRowNumber"] = self.next_sibling_row_number
        out["HighlightRow"] = self.highlight_row
        return out

    @property
    def operator(self) -> str:
        """The operator name without its properties, e.g. ``Cache: IterPhyOp``.

        ``Name: Class`` and nothing after it (F-25): logical rows carry
        ``DependOnCols(...)``, ``RequiredCols(...)`` and data types after the
        class, and keeping them made every row its own "operator" (714
        distinct operators on a 9,919-row plan).
        """
        text = self.operation.strip()
        match = _OPERATOR_RE.match(text)
        if match:
            return f"{match.group('name').strip()}: {match.group('cls')}"
        cut = len(text)
        for marker in (" LogOp=", " IterCols", " LookupCols", " #", " DominantValue="):
            idx = text.find(marker)
            if 0 < idx < cut:
                cut = idx
        return text[:cut].strip()


@dataclass
class QueryPlan:
    """A parsed physical or logical plan."""

    kind: str  # "physical" | "logical"
    rows: list[QueryPlanRow] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def max_records(self) -> int:
        values = [r.records for r in self.rows if r.records is not None]
        return max(values) if values else 0

    @property
    def highlight_count(self) -> int:
        return sum(1 for r in self.rows if r.highlight_row)

    def operator_census(self, top_n: int | None = None) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.operator] = counts.get(row.operator, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return ordered[:top_n] if top_n else ordered

    def top_records(self, limit: int = 10) -> list[QueryPlanRow]:
        scored = [r for r in self.rows if r.records is not None]
        scored.sort(key=lambda r: (-(r.records or 0), r.row_number))
        return scored[:limit]

    def to_export_rows(self) -> list[dict[str, Any]]:
        include = self.kind == "physical"
        return [r.to_export_dict(include_records=include) for r in self.rows]


def parse_plan(text: str, kind: str = "physical") -> QueryPlan:
    """Parse raw plan text into rows.

    ``Level`` is the number of leading tabs, which is exactly how the engine
    encodes tree depth.  Physical rows that carry no ``#Records`` inherit the
    cardinality of their direct children.
    """
    plan = QueryPlan(kind=kind)
    if not text:
        return plan

    lines = [ln for ln in re.split(r"\r\n|\n|\r", text)]
    row_number = 0
    for line in lines:
        if not line.strip():
            continue
        row_number += 1
        level = len(line) - len(line.lstrip("\t"))
        operation = line.strip("\t").rstrip()
        records: int | None = None
        match = _RECORDS_RE.search(operation) or _RECS_RE.search(operation)
        if match:
            records = int(match.group(1))
        plan.rows.append(
            QueryPlanRow(
                row_number=row_number,
                level=level,
                operation=operation,
                indented_operation=" " * 4 * level + operation,
                records=records,
                highlight_row=bool(HIGHLIGHT_RE.search(operation)),
            )
        )

    _compute_siblings(plan.rows)
    if kind == "physical":
        _inherit_records(plan.rows)
    return plan


def _compute_siblings(rows: list[QueryPlanRow]) -> None:
    """``NextSiblingRowNumber`` = last row of this row's subtree."""
    stack: list[QueryPlanRow] = []
    for row in rows:
        while stack and stack[-1].level >= row.level:
            finished = stack.pop()
            finished.next_sibling_row_number = row.row_number - 1
        stack.append(row)
    last_row = rows[-1].row_number if rows else 0
    while stack:
        stack.pop().next_sibling_row_number = last_row


def _inherit_records(rows: list[QueryPlanRow]) -> None:
    """Rows without ``#Records`` take the max cardinality of their DIRECT children.

    F-35: a direct-children index (one pass with a stack) instead of scanning
    each row's whole subtree, which grew superlinearly on deep plans.
    """
    children: dict[int, list[QueryPlanRow]] = {}
    stack: list[QueryPlanRow] = []
    for row in rows:
        while stack and stack[-1].level >= row.level:
            stack.pop()
        if stack and stack[-1].level == row.level - 1:
            children.setdefault(stack[-1].row_number, []).append(row)
        stack.append(row)
    for row in reversed(rows):
        if row.records is not None:
            continue
        values = [c.records for c in children.get(row.row_number, ()) if c.records is not None]
        if values:
            row.records = max(values)


def analyse_plan(
    physical: QueryPlan | None,
    logical: QueryPlan | None = None,
    *,
    result_rows: int | None = None,
    se_query_count: int | None = None,
    top_n: int = 20,
) -> dict[str, Any]:
    """Turn a plan into the findings an optimiser actually acts on."""
    findings: list[dict[str, str]] = []
    out: dict[str, Any] = {}

    if physical is not None:
        out["physical_rows"] = len(physical)
        out["physical_max_records"] = physical.max_records
        out["physical_highlighted"] = physical.highlight_count
        out["operator_census"] = [
            {"operator": op, "count": n} for op, n in physical.operator_census(top_n)
        ]
        out["top_records"] = [
            {
                "row_number": r.row_number,
                "level": r.level,
                "records": r.records,
                "operation": r.operation[:200],
            }
            for r in physical.top_records(10)
        ]

        census = dict(physical.operator_census())
        cache_consumers = sum(n for op, n in census.items() if op.startswith("Cache:"))
        out["cache_consumers"] = cache_consumers
        if se_query_count is not None:
            out["se_query_count"] = se_query_count
            if cache_consumers > se_query_count * 2 and se_query_count > 0:
                findings.append(
                    {
                        "severity": "info",
                        "code": "CACHE_FANOUT",
                        "detail": (
                            f"{cache_consumers} datacache consumers for "
                            f"{se_query_count} storage engine queries - the formula "
                            "engine re-reads the same caches many times."
                        ),
                    }
                )

        if result_rows and physical.max_records:
            ratio = physical.max_records / max(result_rows, 1)
            out["materialisation_ratio"] = round(ratio, 1)
            if ratio >= 100:
                findings.append(
                    {
                        "severity": "high",
                        "code": "OVER_MATERIALISATION",
                        "detail": (
                            f"Peak #Records {physical.max_records:,} against "
                            f"{result_rows:,} result rows ({ratio:,.0f}x). The plan "
                            "materialises data it then discards."
                        ),
                    }
                )

        cross_apply = census.get("CrossApply: IterPhyOp", 0)
        if cross_apply:
            findings.append(
                {
                    "severity": "high" if cross_apply > 50 else "medium",
                    "code": "CROSSAPPLY",
                    "detail": (
                        f"{cross_apply} CrossApply operators - row-by-row expansion "
                        "in the formula engine. Push both sides into CALCULATE "
                        "filter arguments."
                    ),
                }
            )
        group_by_spool = census.get("AggregationSpool<GroupBy>: SpoolPhyOp", 0)
        if group_by_spool:
            findings.append(
                {
                    "severity": "high" if group_by_spool > 100 else "medium",
                    "code": "FE_GROUPBY",
                    "detail": (
                        f"{group_by_spool} AggregationSpool<GroupBy> operators - the "
                        "formula engine is grouping what VertiPaq could group. "
                        "Prefer SUMMARIZECOLUMNS / column predicates."
                    ),
                }
            )
        multi_valued = census.get("Spool_MultiValuedHashLookup: IterPhyOp", 0)
        if multi_valued:
            findings.append(
                {
                    "severity": "medium",
                    "code": "MULTIVALUED_LOOKUP",
                    "detail": (
                        f"{multi_valued} Spool_MultiValuedHashLookup - a key matches "
                        "many rows; check relationship grain and cardinality."
                    ),
                }
            )
        filter_ops = census.get("Filter: IterPhyOp", 0)
        if filter_ops and physical.max_records > 10000:
            findings.append(
                {
                    "severity": "medium",
                    "code": "LATE_FILTER",
                    "detail": (
                        f"{filter_ops} Filter operators above a plan peaking at "
                        f"{physical.max_records:,} records - the predicate is applied "
                        "after materialisation instead of in the storage engine."
                    ),
                }
            )

    if logical is not None:
        out["logical_rows"] = len(logical)
        out["logical_census"] = [
            {"operator": op, "count": n} for op, n in logical.operator_census(top_n)
        ]
        lcensus = dict(logical.operator_census())
        sumx = sum(n for op, n in lcensus.items() if op.startswith("SumX"))
        sum_vertipaq = sum(n for op, n in lcensus.items() if op.startswith("Sum_Vertipaq"))
        if sumx and not sum_vertipaq:
            findings.append(
                {
                    "severity": "high",
                    "code": "NO_SE_SUM",
                    "detail": (
                        f"{sumx} SumX operators with no Sum_Vertipaq - the sum is "
                        "iterated in the formula engine instead of pushed to VertiPaq."
                    ),
                }
            )

    out["findings"] = findings
    return out


def compare_plans(before: QueryPlan, after: QueryPlan) -> dict[str, Any]:
    """Structural diff of two plans: what the rewrite actually changed."""
    before_census = dict(before.operator_census())
    after_census = dict(after.operator_census())
    operators = sorted(set(before_census) | set(after_census))

    deltas = []
    for op in operators:
        b = before_census.get(op, 0)
        a = after_census.get(op, 0)
        if a != b:
            deltas.append({"operator": op, "before": b, "after": a, "delta": a - b})
    deltas.sort(key=lambda d: -abs(d["delta"]))

    return {
        "rows": {
            "before": len(before),
            "after": len(after),
            "delta": len(after) - len(before),
        },
        "max_records": {
            "before": before.max_records,
            "after": after.max_records,
            "delta": after.max_records - before.max_records,
        },
        "highlighted": {
            "before": before.highlight_count,
            "after": after.highlight_count,
            "delta": after.highlight_count - before.highlight_count,
        },
        "operator_deltas": deltas[:40],
        "removed_operators": [
            op for op in operators if op in before_census and op not in after_census
        ],
        "added_operators": [
            op for op in operators if op in after_census and op not in before_census
        ],
    }


def rows_from_export(rows: Iterable[dict[str, Any]], kind: str) -> QueryPlan:
    """Rebuild a :class:`QueryPlan` from an exported ``QueryPlan.json``."""
    plan = QueryPlan(kind=kind)
    for raw in rows:
        plan.rows.append(
            QueryPlanRow(
                row_number=int(raw.get("RowNumber", 0)),
                level=int(raw.get("Level", 0)),
                operation=raw.get("Operation", ""),
                indented_operation=raw.get("IndentedOperation", ""),
                records=raw.get("Records"),
                next_sibling_row_number=int(raw.get("NextSiblingRowNumber", 0)),
                highlight_row=bool(raw.get("HighlightRow", False)),
            )
        )
    return plan
