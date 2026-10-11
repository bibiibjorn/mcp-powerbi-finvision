"""Dataclasses for the unified DAX analyzer.

Data contracts used by the entire analysis + optimization system.
Includes backward-compatible format converters so old facades
(DaxBestPracticesAnalyzer, DaxRulesEngine, CallbackDetector) can
consume results without changing handler code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

# Severity ordering: critical first, info last.
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# Health-score deductions per severity level.
_SEVERITY_DEDUCTIONS = {"critical": 20, "high": 10, "medium": 5, "low": 2, "info": 1}
_MAX_DEDUCTIONS_PER_RULE = 3


def _card_is_many(value: Any) -> bool:
    return str(value).strip().lower() in {"many", "2", "*", "m"}


def _norm_storage_mode(value: Any) -> str:
    """Normalise a TOM / DMV storage mode (``Import``, ``DirectQuery``, ``Dual``,
    ``DirectLake`` or the TOM ``ModeType`` enum 0 Import / 1 DirectQuery / 2 Default / 3 Push /
    4 Dual / 5 DirectLake) to a lower-case name; '' = unknown (``Default`` resolves per model;
    ``Push`` partitions hold imported data)."""
    if value is None:
        return ""
    text = str(value).strip().lower().replace(" ", "").replace("_", "")
    if text in ("directquery", "1"):
        return "directquery"
    if text in ("import", "0", "push", "3"):
        return "import"
    if text in ("dual", "4"):
        return "dual"
    if text in ("directlake", "5"):
        return "directlake"
    return ""


@dataclass
class ModelSchema:
    """Live model metadata threaded into rule evaluation (Phase 2.1).

    Lets schema-aware rules resolve measure-vs-column ambiguity and reason about
    relationship topology / RLS. Built from the model dict via ``from_model``;
    rules must tolerate ``AnalysisContext.schema is None`` (offline / static
    analysis) and degrade to structural-only behaviour.
    """

    measure_names: Set[str] = field(default_factory=set)  # lower-cased
    column_names: Set[str] = field(default_factory=set)  # lower-cased
    table_names: Set[str] = field(default_factory=set)  # lower-cased
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    roles: List[Dict[str, Any]] = field(default_factory=list)
    # Optional, filled when the caller knows them (2026-10-03 review D-05 / D-09 / D-25..27).
    # All keys are lower-cased table names; empty = unknown (rules then stay conservative).
    # storage mode: import | directquery | dual | directlake
    table_storage_modes: Dict[str, str] = field(default_factory=dict)
    calculation_group_tables: Set[str] = field(default_factory=set)
    field_parameter_tables: Set[str] = field(default_factory=set)
    secured_tables: Set[str] = field(default_factory=set)  # tables an RLS role filters
    # UDF name (upper-case) -> per-parameter passing mode ("expr" | "val" | "") by position
    udf_signatures: Dict[str, List[str]] = field(default_factory=dict)
    # lower-cased table -> lower-cased column names (when the builder knows the owning table)
    table_columns: Dict[str, Set[str]] = field(default_factory=dict)

    @classmethod
    def from_model(cls, model: Dict[str, Any]) -> "ModelSchema":
        model = model or {}
        measures = {
            str(m.get("name", "")).strip().lower()
            for m in model.get("measures", []) or []
            if m.get("name")
        }
        columns = {
            str(c.get("name", "")).strip().lower()
            for c in model.get("columns", []) or []
            if c.get("name")
        }
        tables = {
            str(t.get("name", "")).strip().lower()
            for t in model.get("tables", []) or []
            if t.get("name")
        }
        by_table: Dict[str, Set[str]] = {}
        for c in model.get("columns", []) or []:
            tname = str(c.get("table", "")).strip().lower()
            cname = str(c.get("name", "")).strip().lower()
            if tname and cname:
                by_table.setdefault(tname, set()).add(cname)
        modes: Dict[str, str] = {}
        calc_groups: Set[str] = set()
        field_params: Set[str] = set()
        for t in model.get("tables", []) or []:
            name = str(t.get("name", "")).strip().lower()
            if not name:
                continue
            mode = _norm_storage_mode(
                t.get("storage_mode") or t.get("storageMode") or t.get("mode") or t.get("Mode")
            )
            if mode:
                modes[name] = mode
            if t.get("is_calculation_group") or t.get("isCalculationGroup"):
                calc_groups.add(name)
            if t.get("is_field_parameter") or t.get("isFieldParameter"):
                field_params.add(name)
        secured: Set[str] = set()
        for r in model.get("roles", []) or []:
            if not isinstance(r, dict):
                continue
            filters = r.get("table_filters")
            if isinstance(filters, dict):
                for tname in filters:
                    secured.add(str(tname).strip().lower())
            for tname in r.get("secured_tables", []) or []:
                secured.add(str(tname).strip().lower())
        return cls(
            measure_names=measures,
            column_names=columns,
            table_names=tables,
            relationships=list(model.get("relationships", []) or []),
            roles=list(model.get("roles", []) or []),
            table_storage_modes=modes,
            calculation_group_tables=calc_groups,
            field_parameter_tables=field_params,
            secured_tables=secured,
            table_columns=by_table,
        )

    @staticmethod
    def _strip_brackets(name: str) -> str:
        n = (name or "").strip()
        if n.startswith("[") and n.endswith("]"):
            n = n[1:-1]
        return n.strip().lower()

    def is_measure(self, name: str) -> bool:
        return self._strip_brackets(name) in self.measure_names

    def is_column(self, name: str) -> bool:
        return self._strip_brackets(name) in self.column_names

    def classify_ref(self, name: str) -> str:
        """Resolve a bare bracket ref to 'measure', 'column', or 'ambiguous'.

        Accepts the name with or without surrounding brackets. Returns
        'ambiguous' when the name is unknown, present in both sets, or no schema
        is loaded — so callers keep their conservative fallback.
        """
        key = self._strip_brackets(name)
        in_m = key in self.measure_names
        in_c = key in self.column_names
        if in_m and not in_c:
            return "measure"
        if in_c and not in_m:
            return "column"
        return "ambiguous"

    def has_cross_filtered_paths(self) -> bool:
        """True if any active relationship propagates filters in a way ``ALL``
        does not clear — many-to-many cardinality or bidirectional cross-filter —
        so ``ALLCROSSFILTERED`` is needed to remove them.

        Reads the relationship rows in any of the shapes the repo produces
        (review D-12): ``INFO.RELATIONSHIPS()`` / TMSCHEMA rows are PascalCase
        (``IsActive``, ``FromCardinality``, ``CrossFilteringBehavior``) with
        numeric enums (cardinality 1 = One, 2 = Many; cross-filter 1 = OneDirection,
        2 = BothDirections), optionally with ``[Key]`` brackets; hand-built dicts
        use camelCase strings. Keys are matched case-insensitively.
        """
        for r in self.relationships:
            if not isinstance(r, dict):
                continue
            row = {str(k).strip("[]").lower(): v for k, v in r.items()}
            active = row.get("isactive", True)
            if isinstance(active, str):
                active = active.strip().lower() not in ("false", "0", "no")
            if not active:
                continue
            both_many = _card_is_many(row.get("fromcardinality", "Many")) and _card_is_many(
                row.get("tocardinality", "One")
            )
            xfilter = str(row.get("crossfilteringbehavior", "")).strip().lower()
            bidirectional = xfilter in {"bothdirections", "both", "2"}
            if both_many or bidirectional:
                return True
        return False

    # ------------------------------------------------------------------
    # Storage mode (D-05)
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_table(name: str) -> str:
        return " ".join((name or "").strip().strip("'").replace("''", "'").lower().split())

    def storage_mode(self, table: str) -> str:
        """Lower-case storage mode of *table* ('' when unknown)."""
        return self.table_storage_modes.get(self._norm_table(table), "")

    def is_directquery_only(self, tables) -> bool:
        """True when *tables* is non-empty, every one has a known storage mode and all are
        DirectQuery. Unknown / Import / Dual tables make it False (rules then keep the
        Import-mode advice, worded conditionally)."""
        names = [self._norm_table(t) for t in tables or [] if t]
        if not names or not self.table_storage_modes:
            return False
        return all(self.table_storage_modes.get(n) == "directquery" for n in names)

    # ------------------------------------------------------------------
    # Relationship topology (D-09)
    # ------------------------------------------------------------------

    def _edges(self) -> List[Dict[str, Any]]:
        """Active relationships as ``{from, to, limited}`` with lower-case table names.

        ``limited`` marks many-to-many or bidirectional rows (the ones ``ALL`` does not
        clear). Rows without table names are skipped."""
        edges: List[Dict[str, Any]] = []
        for r in self.relationships:
            if not isinstance(r, dict):
                continue
            row = {str(k).strip("[]").replace("_", "").lower(): v for k, v in r.items()}
            active = row.get("isactive", True)
            if isinstance(active, str):
                active = active.strip().lower() not in ("false", "0", "no")
            if not active:
                continue
            frm = self._norm_table(str(row.get("fromtable") or ""))
            to = self._norm_table(str(row.get("totable") or ""))
            if not frm or not to:
                continue
            both_many = _card_is_many(row.get("fromcardinality", "Many")) and _card_is_many(
                row.get("tocardinality", "One")
            )
            xfilter = str(row.get("crossfilteringbehavior", "")).strip().lower()
            bidirectional = xfilter in {"bothdirections", "both", "2"}
            edges.append({"from": frm, "to": to, "limited": bool(both_many or bidirectional)})
        return edges

    def named_edges_available(self) -> bool:
        """True when the relationship rows carry table names (INFO.VIEW.RELATIONSHIPS or a
        builder that resolved the IDs); False for ID-only rows."""
        return bool(self._edges())

    def cross_filtered_tables(self, hops: int = 2) -> Set[str]:
        """Tables that can receive a filter through a limited (many-to-many / bidirectional)
        relationship: both endpoints of such a relationship plus everything within *hops*
        relationships of them. Empty when no named limited relationship is known."""
        edges = self._edges()
        affected: Set[str] = set()
        for e in edges:
            if e["limited"]:
                affected.add(e["from"])
                affected.add(e["to"])
        frontier = set(affected)
        for _ in range(max(0, hops - 1)):
            nxt: Set[str] = set()
            for e in edges:
                if e["from"] in frontier:
                    nxt.add(e["to"])
                if e["to"] in frontier:
                    nxt.add(e["from"])
            nxt -= affected
            affected |= nxt
            frontier = nxt
        return affected

    def one_side_tables(self) -> Set[str]:
        """Tables on the One side of an active relationship (dimension tables)."""
        out: Set[str] = set()
        for r in self.relationships:
            if not isinstance(r, dict):
                continue
            row = {str(k).strip("[]").replace("_", "").lower(): v for k, v in r.items()}
            to = self._norm_table(str(row.get("totable") or ""))
            frm = self._norm_table(str(row.get("fromtable") or ""))
            if to and not _card_is_many(row.get("tocardinality", "One")):
                out.add(to)
            if frm and not _card_is_many(row.get("fromcardinality", "Many")):
                out.add(frm)
        return out

    def has_limited_relationships(self) -> bool:
        """Deprecated name kept for callers — see ``has_cross_filtered_paths``.

        (Not "limited relationships" in the engine sense, which are M2M or
        cross-source-group relationships.)
        """
        return self.has_cross_filtered_paths()


@dataclass
class AnalysisContext:
    """Optional enrichment data for tiered analysis.

    Tier 1 (static) needs nothing. Tier 2 adds VertiPaq / model metadata.
    Tier 3 adds live trace data.
    """

    vertipaq_data: Optional[Dict[str, Any]] = None
    table_row_counts: Optional[Dict[str, int]] = None
    model_relationships: Optional[List[Dict]] = None
    calculation_groups: Optional[List[Dict]] = None
    trace_data: Optional[Dict[str, Any]] = None
    measure_name: Optional[str] = None
    table_name: Optional[str] = None
    schema: Optional[ModelSchema] = None
    # Rule ids the caller accepted for this expression (e.g. from a measure
    # annotation); dropped from the result and listed in ``suppressed``.
    suppressed_rules: Optional[Set[str]] = None
    # What the expression IS (None = a measure body): format_string | calc_item |
    # calc_item_format_string | calc_column | calc_table | rls | kpi | function.
    object_kind: Optional[str] = None


@dataclass
class AnalysisIssue:
    """A single analysis finding produced by the unified engine."""

    rule_id: str
    category: str  # performance | correctness | maintainability
    severity: str  # critical | high | medium | low | info
    title: str
    description: str
    fix_suggestion: str
    source: str  # static | vertipaq | trace

    # Optional detail fields
    location: Optional[str] = None
    code_before: Optional[str] = None
    code_after: Optional[str] = None
    estimated_improvement: Optional[str] = None
    rewrite_strategy: Optional[str] = None
    references: Optional[List[Dict]] = None
    confidence: str = "high"
    vertipaq_detail: Optional[str] = None
    trace_detail: Optional[str] = None
    match_text: Optional[str] = None
    line: int = 0

    # Function classification (populated when a specific function is flagged)
    flagged_function: Optional[str] = None  # e.g. "IF", "FORMAT", "DIVIDE"
    se_classification: Optional[str] = None  # se_safe | fe_only | expression_dependent
    callback_risk: Optional[str] = None  # none | low | medium | high

    # ------------------------------------------------------------------
    # Legacy conversion
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Convert to the legacy dict format used by best-practices results."""
        d: Dict[str, Any] = {
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "fix_suggestion": self.fix_suggestion,
            "source": self.source,
            "confidence": self.confidence,
            "line": self.line,
        }

        if self.location is not None:
            d["location"] = self.location
        if self.code_before is not None:
            d["code_example_before"] = self.code_before
        if self.code_after is not None:
            d["code_example_after"] = self.code_after
        if self.estimated_improvement is not None:
            d["estimated_improvement"] = self.estimated_improvement
        if self.rewrite_strategy is not None:
            d["rewrite_strategy"] = self.rewrite_strategy
        if self.references:
            # Legacy format uses singular "article_reference" with first ref.
            d["article_reference"] = self.references[0]
        if self.vertipaq_detail is not None:
            d["vertipaq_detail"] = self.vertipaq_detail
        if self.trace_detail is not None:
            d["trace_detail"] = self.trace_detail
        if self.match_text is not None:
            d["match_text"] = self.match_text
        if self.flagged_function is not None:
            d["flagged_function"] = self.flagged_function
            if self.se_classification:
                d["se_classification"] = self.se_classification
            if self.callback_risk and self.callback_risk != "none":
                d["callback_risk"] = self.callback_risk

        return d


@dataclass
class RewriteCandidate:
    """An issue that has an automated rewrite strategy attached."""

    issue: AnalysisIssue
    strategy_name: str
    estimated_confidence: str = "medium"


@dataclass
class UnifiedAnalysisResult:
    """Complete analysis output from the unified DAX engine."""

    success: bool
    issues: List[AnalysisIssue]
    health_score: int  # 0-100
    tier_used: int  # 1, 2, or 3
    total_issues: int
    critical_issues: int
    high_issues: int
    medium_issues: int
    rewrite_candidates: List[RewriteCandidate] = field(default_factory=list)
    summary: str = ""
    tokens: Optional[Any] = None
    # Rule ids removed by `// lint-disable RULE` comments or context.suppressed_rules.
    suppressed: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_issues(
        cls,
        issues: List[AnalysisIssue],
        tier_used: int = 1,
        tokens: Optional[Any] = None,
    ) -> "UnifiedAnalysisResult":
        """Build a result from a flat list of issues.

        - Sorts by severity (critical first).
        - Computes health_score with per-severity deductions.
        - Extracts rewrite candidates from issues that carry a strategy.
        """
        sorted_issues = sorted(issues, key=lambda i: _SEVERITY_ORDER.get(i.severity, 99))

        # Severity counts
        counts: Dict[str, int] = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0,
        }
        for issue in sorted_issues:
            if issue.severity in counts:
                counts[issue.severity] += 1

        # Health score. A rule counts at most ``_MAX_DEDUCTIONS_PER_RULE`` times (2026-10-03 review
        # D-29): five findings of one rule on one measure say the same thing five times.
        score = 100
        per_rule: Dict[str, int] = {}
        for issue in sorted_issues:
            per_rule[issue.rule_id] = per_rule.get(issue.rule_id, 0) + 1
            if per_rule[issue.rule_id] <= _MAX_DEDUCTIONS_PER_RULE:
                score -= _SEVERITY_DEDUCTIONS.get(issue.severity, 0)
        score = max(0, score)

        # Rewrite candidates
        rewrite_candidates = [
            RewriteCandidate(
                issue=issue,
                strategy_name=issue.rewrite_strategy,  # type: ignore[arg-type]
                estimated_confidence="medium",
            )
            for issue in sorted_issues
            if issue.rewrite_strategy
        ]

        total = len(sorted_issues)
        summary = f"{total} issues found (score: {score}/100)"

        return cls(
            success=True,
            issues=sorted_issues,
            health_score=score,
            tier_used=tier_used,
            total_issues=total,
            critical_issues=counts["critical"],
            high_issues=counts["high"],
            medium_issues=counts["medium"],
            rewrite_candidates=rewrite_candidates,
            summary=summary,
            tokens=tokens,
        )

    # ------------------------------------------------------------------
    # Backward-compatible format converters
    # ------------------------------------------------------------------

    def to_best_practices_format(self) -> Dict[str, Any]:
        """Return a dict matching DaxBestPracticesAnalyzer.analyze() output."""
        # Deduplicate article references across all issues.
        seen_urls: set = set()
        articles: List[Dict] = []
        for issue in self.issues:
            if issue.references:
                for ref in issue.references:
                    url = ref.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        articles.append(ref)

        # Complexity heuristic: severity-weighted.
        if self.critical_issues > 0 or self.total_issues >= 5:
            complexity = "complex"
        elif self.high_issues > 0 or self.total_issues >= 2:
            complexity = "moderate"
        else:
            complexity = "simple"

        return {
            "success": True,
            "total_issues": self.total_issues,
            "critical_issues": self.critical_issues,
            "high_issues": self.high_issues,
            "medium_issues": self.medium_issues,
            "issues": [issue.to_dict() for issue in self.issues],
            "summary": self.summary,
            "articles_referenced": articles,
            "overall_score": self.health_score,
            "complexity_level": complexity,
        }

    def to_rules_engine_format(self) -> Dict[str, Any]:
        """Return a dict matching DaxRulesEngine.analyze() output."""
        categories: Dict[str, int] = {}
        engine_issues: List[Dict[str, Any]] = []

        for issue in self.issues:
            # Map category names to rules-engine categories.
            cat = issue.category
            if cat == "maintainability":
                cat = "readability"
            categories[cat] = categories.get(cat, 0) + 1

            engine_issues.append(
                {
                    "rule_id": issue.rule_id,
                    "category": issue.category,
                    "severity": issue.severity,
                    "description": issue.description,
                    "fix_suggestion": issue.fix_suggestion,
                    "line": issue.line,
                    "match_text": issue.match_text,
                }
            )

        return {
            "health_score": self.health_score,
            "issues": engine_issues,
            "issue_count": self.total_issues,
            "categories": categories,
        }

    def to_anti_patterns_format(self) -> Dict[str, Any]:
        """The ``anti_patterns`` block of 05 ``analyze`` / ``all`` / ``report``.

        2026-10-03 review D-01: there is ONE advice engine. This block used to come from a
        second, regex-based "research article" engine whose advice contradicted the retuned
        rules; it is now a view of this result's own findings (info findings excluded)."""
        matches: Dict[str, List[str]] = {}
        recommendations: List[str] = []
        for issue in self.issues:
            if issue.severity == "info":
                continue
            matches.setdefault(issue.rule_id, []).append(
                issue.location or issue.match_text or f"line {issue.line}"
            )
            if issue.fix_suggestion and issue.fix_suggestion not in recommendations:
                recommendations.append(issue.fix_suggestion)
        return {
            "success": True,
            "source": "unified analyzer (same rules as best_practices_analysis)",
            "patterns_detected": len(matches),
            "pattern_matches": matches,
            "recommendations": recommendations,
            "articles": [],
        }

    def to_callback_format(self) -> Dict[str, Any]:
        """Return a dict matching CallbackDetector.detect_dict() output."""
        detections: List[Dict[str, Any]] = []
        for issue in self.issues:
            if issue.rule_id.startswith("CB"):
                detections.append(
                    {
                        "rule_id": issue.rule_id,
                        "severity": issue.severity,
                        "description": issue.description,
                        "location": issue.location,
                        "fix_suggestion": issue.fix_suggestion,
                    }
                )

        return {
            "success": True,
            "total_detections": len(detections),
            "detections": detections,
        }
