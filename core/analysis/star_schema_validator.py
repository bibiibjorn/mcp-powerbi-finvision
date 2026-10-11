"""Star-schema / topology validator (Phase 3.1).

Pure-Python, model-shape-driven design-quality checks that the BPA layer did not
cover: fact-to-fact relationships, snowflake dimensions, disconnected tables, and
a missing date table. Operates on the model dict produced by
``analysis_orchestrator.list_relationships_simple`` so it is unit-testable
without a live connection.

Edge convention: a star edge points from the MANY side (fact/child) to the ONE
side (dimension/parent) — ``fromCardinality == 'Many'`` and
``toCardinality == 'One'``, fromTable -> toTable.

Heuristics are deliberately conservative; over-firing (telling the user their
correct model is wrong) is the primary failure mode for this kind of advisor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple


@dataclass(frozen=True)
class TopologyFinding:
    """A single model-topology design finding."""

    finding_id: str
    severity: str  # "info" | "low" | "medium" | "high" | "critical"
    object_type: str
    object_name: str
    description: str
    recommendation: str


# Tables that are disconnected BY DESIGN — never flag them as orphans.
_DATE_NAME_RE = re.compile(
    r"\b(dates?|calendars?|kalender|datum)\b|dim[\s_]?dates?\b|dates?[\s_]?dim\b", re.IGNORECASE
)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _is_card_many(value: Any) -> bool:
    return str(value).strip().lower() in {"many", "2", "*", "m"}


class StarSchemaValidator:
    """Detect star-schema deviations from a model dict."""

    def validate(self, model: Dict[str, Any]) -> List[TopologyFinding]:
        tables = model.get("tables", []) or []
        relationships = model.get("relationships", []) or []

        if not tables:
            return []

        table_names = [t.get("name") for t in tables if t.get("name")]
        table_by_name = {t.get("name"): t for t in tables if t.get("name")}

        active_rels = [r for r in relationships if r.get("isActive", True)]

        # Classify by role across all active relationships:
        #   - many_side_source: appears as the MANY-side 'from' of some edge
        #   - one_side_target:  appears as the ONE-side 'to' of some edge (a
        #     dimension that something points at)
        # A FACT is a table that is a many-side source AND is never a one-side
        # target. A snowflaked dimension is BOTH (many toward its parent, one
        # toward the fact), so excluding one-side targets keeps dimensions out of
        # the fact set and prevents false fact-to-fact findings.
        many_side_source: Set[str] = set()
        one_side_target: Set[str] = set()
        for r in active_rels:
            if r.get("fromTable") and _is_card_many(r.get("fromCardinality", "Many")):
                many_side_source.add(r["fromTable"])
            if r.get("toTable") and not _is_card_many(r.get("toCardinality", "One")):
                one_side_target.add(r["toTable"])
        fact_like: Set[str] = many_side_source - one_side_target

        # Tables that participate in any relationship (either side).
        connected: Set[str] = set()
        for r in relationships:
            if r.get("fromTable"):
                connected.add(r["fromTable"])
            if r.get("toTable"):
                connected.add(r["toTable"])

        findings: List[TopologyFinding] = []
        findings.extend(self._check_fact_to_fact(active_rels, fact_like))
        findings.extend(self._check_snowflake(active_rels, fact_like))
        dax_read = {str(n) for n in model.get("daxReferencedTables") or []}
        findings.extend(self._check_disconnected(table_names, table_by_name, connected | dax_read))
        findings.extend(self._check_missing_date_table(tables, table_by_name))
        findings.extend(self._check_ambiguous_paths(active_rels))
        return findings

    # ── Fact-to-fact ────────────────────────────────────────────────────────

    def _check_fact_to_fact(self, rels, fact_like) -> List[TopologyFinding]:
        out: List[TopologyFinding] = []
        seen: Set[frozenset] = set()
        for r in rels:
            a, b = r.get("fromTable"), r.get("toTable")
            if not a or not b or a == b:
                continue
            # Both endpoints are fact-like AND they are joined directly.
            if a in fact_like and b in fact_like:
                key = frozenset((a, b))
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    TopologyFinding(
                        finding_id="FACT_TO_FACT",
                        severity="high",
                        object_type="Relationship",
                        object_name=f"{a} <-> {b}",
                        description=(
                            f"'{a}' and '{b}' are both fact tables joined directly. "
                            "Fact-to-fact relationships produce ambiguous filter "
                            "propagation and incorrect aggregations."
                        ),
                        recommendation=(
                            "Route the relationship through a shared (conformed) "
                            "dimension instead of joining the two fact tables "
                            "directly."
                        ),
                    )
                )
        return out

    # ── Snowflake dimension ─────────────────────────────────────────────────

    def _check_snowflake(self, rels, fact_like) -> List[TopologyFinding]:
        out: List[TopologyFinding] = []
        for r in rels:
            child, parent = r.get("fromTable"), r.get("toTable")
            if not child or not parent or child == parent:
                continue
            # A dim->dim edge: the child (Many side) is NOT a fact and the parent
            # is also not a fact. That is a snowflake leg; the parent dimension
            # only reaches facts via the child dimension.
            if child not in fact_like and parent not in fact_like:
                out.append(
                    TopologyFinding(
                        finding_id="SNOWFLAKE_DIMENSION",
                        severity="medium",
                        object_type="Table",
                        object_name=parent,
                        description=(
                            f"'{parent}' is a snowflaked dimension: it connects to "
                            f"facts only through '{child}', not directly."
                        ),
                        recommendation=(
                            f"Denormalize '{parent}' into '{child}' in Power Query "
                            "so the dimension is a single flat table joined directly "
                            "to the fact."
                        ),
                    )
                )
        return out

    # ── Disconnected table ──────────────────────────────────────────────────

    def _check_disconnected(self, table_names, table_by_name, connected) -> List[TopologyFinding]:
        out: List[TopologyFinding] = []
        for name in table_names:
            if name in connected:
                continue
            if self._is_disconnected_by_design(table_by_name.get(name, {})):
                continue
            out.append(
                TopologyFinding(
                    finding_id="DISCONNECTED_TABLE",
                    severity="low",
                    object_type="Table",
                    object_name=name,
                    description=(
                        f"'{name}' participates in no relationship and may be "
                        "stranded from the model."
                    ),
                    recommendation=(
                        "Add a relationship to connect it, or confirm it is "
                        "intentionally disconnected (parameter/what-if table)."
                    ),
                )
            )
        return out

    @staticmethod
    def _is_disconnected_by_design(table: Dict[str, Any]) -> bool:
        if _truthy(table.get("isCalculationGroup")):
            return True
        if _truthy(table.get("isFieldParameter")) or _truthy(table.get("isParameter")):
            return True
        # 2026-09-22 (I-06): auto date tables are private; a measure table holds
        # measures and at most a placeholder column — neither is an orphan.
        if _truthy(table.get("isPrivate")):
            return True
        name = str(table.get("name") or "")
        if name.startswith(("LocalDateTable_", "DateTableTemplate_")):
            return True
        try:
            if int(table.get("measureCount") or 0) > 0 and int(table.get("columnCount") or 0) <= 1:
                return True
        except (TypeError, ValueError):
            pass
        # Field-parameter tables expose an "extendedProperties" Parameter marker
        # in TOM; tolerate either shape.
        ext = table.get("extendedProperties") or table.get("annotations") or {}
        if isinstance(ext, dict) and "ParameterMetadata" in ext:
            return True
        return False

    # ── Missing date table ──────────────────────────────────────────────────

    def _check_missing_date_table(self, tables, table_by_name) -> List[TopologyFinding]:
        for t in tables:
            name = t.get("name") or ""
            if str(t.get("dataCategory") or "").strip().lower() == "time":
                return []
            if t.get("calendars"):
                return []  # a Calendar object (Sep 2025) replaces the marked classic date table
            if _DATE_NAME_RE.search(name):
                return []
        return [
            TopologyFinding(
                finding_id="MISSING_DATE_TABLE",
                severity="medium",
                object_type="Model",
                object_name="(model)",
                description=(
                    "No date/calendar dimension was detected. Time-intelligence "
                    "DAX requires a marked, contiguous date table."
                ),
                recommendation=(
                    "Add a dedicated Date table (marked as a date table / "
                    "dataCategory='Time') and relate it to each fact's date key."
                ),
            )
        ]

    # ── Ambiguous filter paths ──────────────────────────────────────────────

    def _check_ambiguous_paths(self, rels) -> List[TopologyFinding]:
        """More than one active filter path from one table to another (I-30 #4).

        A path follows filter PROPAGATION: a relationship filters from its ONE side (``toTable``)
        to its MANY side (``fromTable``), and also the other way when it is bidirectional. Two
        distinct propagation paths between the same pair of tables make the result depend on which
        one the engine takes (Power BI refuses to activate the second relationship). The classic
        multi-fact star (two facts sharing two dimensions, all single direction) has no such pair.
        """
        out: List[TopologyFinding] = []
        for src, dst, p1, p2 in find_ambiguous_paths(rels):
            out.append(
                TopologyFinding(
                    finding_id="AMBIGUOUS_RELATIONSHIP_PATH",
                    severity="high",
                    object_type="Relationship",
                    object_name=f"{src} -> {dst}",
                    description=(
                        f"Filters from '{src}' reach '{dst}' along more than one active path: "
                        f"{' -> '.join(p1)}  and  {' -> '.join(p2)}. The query result depends on "
                        "which path the engine picks."
                    ),
                    recommendation=(
                        "Make a bidirectional relationship on the cycle single-direction, "
                        "deactivate or remove one of the relationships, and use USERELATIONSHIP / "
                        "CROSSFILTER inside the measure that needs the other path."
                    ),
                )
            )
        return out


_MAX_AMBIGUITY_FINDINGS = 10
_PATH_SEARCH_BUDGET = 50_000


def _both_directions(rel: Dict[str, Any]) -> bool:
    return str(rel.get("crossFilteringBehavior", "")).strip().lower() in {
        "bothdirections",
        "both",
        "2",
    }


def find_ambiguous_paths(
    relationships: List[Dict[str, Any]],
) -> List[Tuple[str, str, List[str], List[str]]]:
    """``[(source, target, path1, path2)]``: pairs with two distinct active propagation paths.

    ``relationships`` are TMSL / normalised relationship dicts (``fromTable``, ``toTable``,
    ``isActive``, ``crossFilteringBehavior``). One representative pair per distinct set of
    relationships on the cycle; the search is bounded (a dense bidirectional model is reported
    from what was found, never hangs).
    """
    edges: Dict[str, List[Tuple[int, str]]] = {}
    for i, r in enumerate(relationships or []):
        if not _truthy(r.get("isActive", True)):
            continue
        frm, to = r.get("fromTable"), r.get("toTable")
        if not frm or not to or frm == to:
            continue
        edges.setdefault(str(to), []).append((i, str(frm)))  # ONE side filters the MANY side
        if _both_directions(r):
            edges.setdefault(str(frm), []).append((i, str(to)))

    found: List[Tuple[str, str, List[str], List[str]]] = []
    seen_cycles: Set[frozenset] = set()
    for src in sorted(edges):
        budget = [_PATH_SEARCH_BUDGET]
        first: Dict[str, Tuple[List[str], List[int]]] = {}

        def walk(node: str, nodes: List[str], used: List[int]) -> None:
            if budget[0] <= 0 or len(found) >= _MAX_AMBIGUITY_FINDINGS:
                return
            for edge_id, nxt in edges.get(node, ()):
                if nxt in nodes:
                    continue
                budget[0] -= 1
                path_nodes, path_edges = nodes + [nxt], used + [edge_id]
                prior = first.get(nxt)
                if prior is None:
                    first[nxt] = (path_nodes, path_edges)
                elif prior[1] != path_edges:
                    key = frozenset(prior[1]) | frozenset(path_edges)
                    if key not in seen_cycles and len(found) < _MAX_AMBIGUITY_FINDINGS:
                        seen_cycles.add(key)
                        found.append((src, nxt, prior[0], path_nodes))
                    continue  # no need to extend a path into an already ambiguous target
                walk(nxt, path_nodes, path_edges)

        walk(src, [src], [])
        if len(found) >= _MAX_AMBIGUITY_FINDINGS:
            break
    return found
