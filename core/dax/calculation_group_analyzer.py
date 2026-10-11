"""
Calculation Group Analyzer - Specialized analysis for calculation groups

Provides:
- Precedence conflict detection
- Performance impact assessment
- Design pattern validation
- Best practice recommendations
"""

import logging

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CalculationGroup:
    """Represents a calculation group.

    ``name`` is the calculation-group TABLE name (a calculation group has no
    name of its own); ``column_name`` is the column DAX filters on
    (``'Time Intelligence'[Period] = "YTD"``).
    """

    name: str
    precedence: int
    items: List[str]
    description: Optional[str] = None
    column_name: Optional[str] = None


def _cell(row: Dict[str, Any], key: str) -> Any:
    """Case-insensitive, bracket-tolerant DMV row lookup."""
    if key in row:
        return row[key]
    want = key.lower()
    for k, v in row.items():
        if str(k).strip("[]").lower() == want:
            return v
    return None


_ITERATORS = {
    "SUMX",
    "AVERAGEX",
    "MINX",
    "MAXX",
    "COUNTX",
    "COUNTAX",
    "PRODUCTX",
    "RANKX",
    "CONCATENATEX",
    "FILTER",
    "ADDCOLUMNS",
    "SELECTCOLUMNS",
    "GENERATE",
    "GENERATEALL",
    "STDEVX.S",
    "STDEVX.P",
    "VARX.S",
    "VARX.P",
    "PERCENTILEX.INC",
    "PERCENTILEX.EXC",
    "MEDIANX",
    "GEOMEANX",
}


@dataclass
class PrecedenceConflict:
    """Precedence conflict between calculation groups"""

    measure_name: str
    groups: List[Tuple[str, int]]  # (group_name, precedence)
    warning: str
    severity: str  # "info", "warning", "error"


@dataclass
class CalculationGroupIssue:
    """Issue found in calculation group usage"""

    issue_type: str
    severity: str
    message: str
    recommendation: str
    affected_groups: List[str]


class CalculationGroupAnalyzer:
    """
    Analyzer for Calculation Group usage and best practices

    Features:
    - Detect calculation group references in DAX
    - Check for precedence conflicts
    - Validate design patterns
    - Assess performance impact
    """

    # Best practice limits
    MAX_RECOMMENDED_GROUPS = 3
    MAX_ITEMS_PER_GROUP = 20

    def __init__(self, connection_state=None):
        """
        Initialize calculation group analyzer

        Args:
            connection_state: Optional connection state for querying model
        """
        self.connection_state = connection_state
        self._calc_groups: Dict[str, CalculationGroup] = {}
        self._groups_loaded = False

    def load_calculation_groups(self) -> bool:
        """
        Load calculation groups from model

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.connection_state or not self.connection_state.is_connected():
                logger.warning("Not connected - cannot load calculation groups")
                return False

            qe = self.connection_state.query_executor
            if not qe:
                logger.warning("Query executor not available")
                return False

            # Review D-21: TMSCHEMA_CALCULATION_GROUPS has no [Name] column — the
            # name belongs to the table (TableID), and items reference their group
            # by [CalculationGroupID]. Load the four DMVs once and join in Python
            # (no name ever interpolated into a query).
            def _rows(query: str) -> List[Dict[str, Any]]:
                res = qe.execute_dmv_query(query)
                if not isinstance(res, dict) or not res.get("success"):
                    return []
                return list(res.get("data") or res.get("rows") or [])

            groups = _rows(
                "SELECT [ID], [TableID], [Description], [Precedence] "
                "FROM $SYSTEM.TMSCHEMA_CALCULATION_GROUPS"
            )
            if not groups:
                logger.info("No calculation groups found in model")
                return False
            tables = {
                str(_cell(r, "ID")): str(_cell(r, "Name") or "")
                for r in _rows("SELECT [ID], [Name] FROM $SYSTEM.TMSCHEMA_TABLES")
            }
            items_by_group: Dict[str, List[Tuple[int, str]]] = {}
            for r in _rows(
                "SELECT [CalculationGroupID], [Name], [Ordinal] "
                "FROM $SYSTEM.TMSCHEMA_CALCULATION_ITEMS"
            ):
                try:
                    ordinal = int(_cell(r, "Ordinal") or 0)
                except (TypeError, ValueError):
                    ordinal = 0
                items_by_group.setdefault(str(_cell(r, "CalculationGroupID")), []).append(
                    (ordinal, str(_cell(r, "Name") or ""))
                )
            column_by_table: Dict[str, str] = {}
            for r in _rows(
                "SELECT [TableID], [ExplicitName], [Type] FROM $SYSTEM.TMSCHEMA_COLUMNS"
            ):
                tid = str(_cell(r, "TableID"))
                try:
                    ctype = int(_cell(r, "Type") or 0)
                except (TypeError, ValueError):
                    ctype = 0
                if ctype != 3 and tid not in column_by_table:  # 3 = RowNumber
                    column_by_table[tid] = str(_cell(r, "ExplicitName") or "")

            self._calc_groups.clear()
            for row in groups:
                table_id = str(_cell(row, "TableID"))
                name = tables.get(table_id, "")
                if not name:
                    continue
                try:
                    precedence = int(_cell(row, "Precedence") or 0)
                except (TypeError, ValueError):
                    precedence = 0
                items = [n for _, n in sorted(items_by_group.get(str(_cell(row, "ID")), []))]
                self._calc_groups[name] = CalculationGroup(
                    name=name,
                    precedence=precedence,
                    items=items,
                    description=_cell(row, "Description"),
                    column_name=column_by_table.get(table_id) or None,
                )

            self._groups_loaded = True
            logger.info(f"Loaded {len(self._calc_groups)} calculation groups")
            return bool(self._calc_groups)

        except Exception as e:
            logger.error(f"Error loading calculation groups: {e}", exc_info=True)
            return False

    def analyze_dax_with_calc_groups(
        self, dax_expression: str, measure_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze DAX expression for calculation group usage

        Args:
            dax_expression: DAX expression to analyze
            measure_name: Optional measure name for context

        Returns:
            Dictionary with analysis results
        """
        try:
            # Ensure groups are loaded
            if not self._groups_loaded:
                self.load_calculation_groups()

            # Detect calculation group references
            groups_detected = self._detect_calc_group_references(dax_expression)

            # Check for precedence conflicts
            conflicts = []
            if len(groups_detected) > 1 and measure_name:
                conflicts = self._check_precedence_conflicts(measure_name, groups_detected)

            # Assess performance impact
            performance_impact = self._assess_performance_impact(groups_detected)

            # Validate design patterns
            issues = self._validate_design_patterns(dax_expression, groups_detected)

            # Get recommendations
            recommendations = self._get_recommendations(
                groups_detected, conflicts, issues, performance_impact
            )

            return {
                "success": True,
                "groups_loaded": self._groups_loaded,
                "groups_detected": [g.name for g in groups_detected],
                "items_referenced": {
                    g.name: self._items_referenced(dax_expression, g) for g in groups_detected
                },
                "group_count": len(groups_detected),
                "precedence_conflicts": [
                    {
                        "measure": c.measure_name,
                        "groups": [{"name": g[0], "precedence": g[1]} for g in c.groups],
                        "warning": c.warning,
                        "severity": c.severity,
                    }
                    for c in conflicts
                ],
                "performance_impact": performance_impact,
                "issues": [
                    {
                        "type": i.issue_type,
                        "severity": i.severity,
                        "message": i.message,
                        "recommendation": i.recommendation,
                        "affected_groups": i.affected_groups,
                    }
                    for i in issues
                ],
                "recommendations": recommendations,
            }

        except Exception as e:
            logger.error(f"Error analyzing calculation groups: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _detect_calc_group_references(self, dax: str) -> List[CalculationGroup]:
        """Detect calculation-group usage in DAX (lexer-based, review D-21).

        Calculation items are applied by filtering the group's column —
        ``CALCULATE([M], 'Time Intelligence'[Period] = "YTD")``, ``TREATAS``,
        ``IN { … }`` — so a group is detected when its table is referenced
        (qualified column or table reference). Strings and comments never match.
        """
        from core.dax.dax_reference_parser import scan_references

        if not self._calc_groups:
            return []
        referenced = {
            " ".join(r.table.lower().split())
            for r in scan_references(dax or "")
            if r.kind in ("qualified", "table") and r.table
        }
        return [
            g for key, g in self._calc_groups.items() if " ".join(key.lower().split()) in referenced
        ]

    def _items_referenced(self, dax: str, group: CalculationGroup) -> List[str]:
        """Calculation items named by a string literal in *dax*."""
        from core.dax.dax_names import unstring
        from core.dax.dax_reference_parser import get_lexer
        from core.dax.tokenizer import TokenType

        wanted = {i.lower(): i for i in group.items}
        out: List[str] = []
        for t in get_lexer().tokenize_code(dax or ""):
            if t.type == TokenType.STRING:
                hit = wanted.get(unstring(t.value).lower())
                if hit and hit not in out:
                    out.append(hit)
        return out

    def _refs_inside_iterator(self, dax: str, group: CalculationGroup) -> bool:
        """True when a reference to *group*'s table sits inside an iterator call."""
        from core.dax.dax_reference_parser import get_lexer, scan_references
        from core.dax.tokenizer import TokenType

        lexer = get_lexer()
        code = lexer.tokenize_code(dax or "")
        pmap = lexer.paren_map(code)
        spans = []
        for i, t in enumerate(code[:-1]):
            if t.type == TokenType.FUNCTION and t.value.upper() in _ITERATORS:
                if code[i + 1].type == TokenType.PAREN_OPEN and (i + 1) in pmap:
                    spans.append((code[i + 1].start, code[pmap[i + 1]].end))
        if not spans:
            return False
        key = " ".join(group.name.lower().split())
        for r in scan_references(dax or "", code=code):
            if r.kind in ("qualified", "table") and " ".join(r.table.lower().split()) == key:
                if any(a <= r.start < b for a, b in spans):
                    return True
        return False

    def _check_precedence_conflicts(
        self, measure_name: str, groups: List[CalculationGroup]
    ) -> List[PrecedenceConflict]:
        """Check for precedence conflicts"""
        conflicts = []

        if len(groups) <= 1:
            return conflicts

        # Sort by precedence
        sorted_groups = sorted(groups, key=lambda g: g.precedence)

        # Check for ambiguous precedence
        precedences = [g.precedence for g in groups]
        if len(set(precedences)) != len(precedences):
            # Duplicate precedences found
            conflicts.append(
                PrecedenceConflict(
                    measure_name=measure_name,
                    groups=[(g.name, g.precedence) for g in groups],
                    warning="Duplicate precedence values detected! This may cause unpredictable behavior.",
                    severity="error",
                )
            )
        else:
            # Just a warning about multiple groups
            conflicts.append(
                PrecedenceConflict(
                    measure_name=measure_name,
                    groups=[(g.name, g.precedence) for g in sorted_groups],
                    warning=(
                        f"Multiple calculation groups applied to this measure. "
                        f"Execution order: {' → '.join(g.name for g in sorted_groups)} "
                        f"(based on precedence values). Verify this is intended."
                    ),
                    severity="warning",
                )
            )

        return conflicts

    def _assess_performance_impact(self, groups: List[CalculationGroup]) -> str:
        """Assess performance impact of calculation groups"""
        if not groups:
            return "none"

        total_items = sum(len(g.items) for g in groups)

        if len(groups) > self.MAX_RECOMMENDED_GROUPS:
            return "high"
        elif total_items > self.MAX_ITEMS_PER_GROUP:
            return "medium"
        elif len(groups) > 1:
            return "medium"
        else:
            return "low"

    def _validate_design_patterns(
        self, dax: str, groups: List[CalculationGroup]
    ) -> List[CalculationGroupIssue]:
        """Validate calculation group design patterns"""
        issues = []

        # Check 1: Too many calculation groups
        if len(groups) > self.MAX_RECOMMENDED_GROUPS:
            issues.append(
                CalculationGroupIssue(
                    issue_type="too_many_groups",
                    severity="warning",
                    message=f"Using {len(groups)} calculation groups on a single measure",
                    recommendation=(
                        "Consider consolidating calculation groups using SWITCH statements "
                        "for better performance and maintainability."
                    ),
                    affected_groups=[g.name for g in groups],
                )
            )

        # Check 2: Calculation group used inside iterator (token spans, not a
        # "any iterator anywhere" substring test)
        for group in groups:
            if self._refs_inside_iterator(dax, group):
                issues.append(
                    CalculationGroupIssue(
                        issue_type="calc_group_in_iterator",
                        severity="warning",
                        message=f"Calculation group '{group.name}' is filtered inside an iterator",
                        recommendation=(
                            "Using calculation groups inside iterators can cause performance issues. "
                            "Consider pre-calculating the value or restructuring the measure."
                        ),
                        affected_groups=[group.name],
                    )
                )

        # Check 3: Nested calculation group references
        if len(groups) > 2:
            issues.append(
                CalculationGroupIssue(
                    issue_type="complex_nesting",
                    severity="info",
                    message=f"Complex interaction between {len(groups)} calculation groups",
                    recommendation=(
                        "Complex calculation group interactions can be difficult to debug. "
                        "Document the intended behavior and test thoroughly."
                    ),
                    affected_groups=[g.name for g in groups],
                )
            )

        # Check 4: SELECTEDMEASURE usage (function token, not a substring)
        from core.dax.dax_reference_parser import get_lexer
        from core.dax.tokenizer import TokenType

        if any(
            t.type == TokenType.FUNCTION and t.value.upper() == "SELECTEDMEASURE"
            for t in get_lexer().tokenize_code(dax or "")
        ):
            issues.append(
                CalculationGroupIssue(
                    issue_type="selectedmeasure_usage",
                    severity="info",
                    message="SELECTEDMEASURE() detected - calculation group pattern",
                    recommendation=(
                        "Ensure SELECTEDMEASURE() is used correctly within calculation group items. "
                        "This function only works in calculation group context."
                    ),
                    affected_groups=[g.name for g in groups],
                )
            )

        return issues

    def _get_recommendations(
        self,
        groups: List[CalculationGroup],
        conflicts: List[PrecedenceConflict],
        issues: List[CalculationGroupIssue],
        performance_impact: str,
    ) -> List[str]:
        """Get optimization recommendations"""
        recommendations = []

        # Performance recommendations
        if performance_impact == "high":
            recommendations.append(
                "High performance impact detected. Consider consolidating calculation groups "
                "using SWITCH for shared logic between calculation items."
            )

        # Precedence recommendations
        if conflicts:
            has_errors = any(c.severity == "error" for c in conflicts)
            if has_errors:
                recommendations.append(
                    "CRITICAL: Fix precedence conflicts immediately. Duplicate precedence values "
                    "will cause unpredictable behavior."
                )
            else:
                recommendations.append(
                    "Verify the execution order of multiple calculation groups matches your intent. "
                    "Document the expected behavior for future reference."
                )

        # Design pattern recommendations
        if len(groups) > 2:
            recommendations.append(
                "Consider using variables (VAR) to cache calculation group results when "
                "the same calculation group is referenced multiple times."
            )

        # General best practices
        if groups:
            recommendations.append(
                "Ensure the 'Discourage implicit measures' property is enabled in your model, "
                "as calculation items don't apply to implicit measures."
            )

            # Check if composite model warning is needed
            recommendations.append(
                "If using a composite model, ensure calculation groups are in the 'local' part "
                "while measures are in the 'remote' part for proper functionality."
            )

        return recommendations
