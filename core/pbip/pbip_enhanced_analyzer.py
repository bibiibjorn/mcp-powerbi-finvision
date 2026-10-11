"""
Enhanced PBIP Analyzer - Comprehensive analysis including BPA, lineage, quality metrics.

This module provides advanced analysis capabilities for PBIP projects including:
- Best Practice Analysis (BPA) integration
- Column-level lineage and impact analysis
- Data type and cardinality analysis
- Relationship quality metrics
- DAX code quality metrics
- Calculation group analysis
- Perspective analysis
- Naming convention validation
"""

import logging
import re
from typing import Dict, List, Optional, Any, Set, Tuple
from collections import Counter
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def _end_cardinality(value: Optional[str], default: str) -> str:
    text = str(value).strip().lower() if value else default
    return "one" if text == "one" else "many"


def _rel_fields(rel: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise one relationship dict (TMDL model dict or TMSL-style) to bare names.

    ``TmdlModel.to_dict()`` relationships carry ``from_table`` / ``from_column_name``
    (bare names) next to ``from_column`` (the raw ``'T'.'C'`` reference) and
    ``from_cardinality`` / ``to_cardinality`` -- there is no ``cardinality`` key and
    the old ``from_column`` compare never matched a bare column name (G-20).
    """
    from core.tmdl.models import _extract_column_from_ref, _extract_table_from_ref

    def pick(*keys: str) -> Any:
        for k in keys:
            if rel.get(k) not in (None, ""):
                return rel[k]
        return ""

    from_ref = pick("from_column", "fromColumn")
    to_ref = pick("to_column", "toColumn")
    from_table = pick("from_table", "fromTable") or _extract_table_from_ref(from_ref)
    to_table = pick("to_table", "toTable") or _extract_table_from_ref(to_ref)
    from_col = pick("from_column_name") or _extract_column_from_ref(from_ref)
    to_col = pick("to_column_name") or _extract_column_from_ref(to_ref)
    cardinality = pick("cardinality", "multiplicity")
    if not cardinality:
        f = _end_cardinality(pick("from_cardinality", "fromCardinality"), "many")
        t = _end_cardinality(pick("to_cardinality", "toCardinality"), "one")
        cardinality = {
            ("one", "many"): "OneToMany",
            ("many", "one"): "ManyToOne",
            ("one", "one"): "OneToOne",
            ("many", "many"): "ManyToMany",
        }[(f, t)]
    cross = pick("cross_filtering_behavior", "cross_filter_behavior", "crossFilteringBehavior")
    return {
        "from_table": from_table,
        "from_column": from_col,
        "to_table": to_table,
        "to_column": to_col,
        "cardinality": cardinality,
        "cross_filter": str(cross or ""),
    }


class ColumnLineageAnalyzer:
    """Analyzes column-level lineage and impact across the model."""

    def __init__(self, model_data: Dict, dependencies: Dict, report_data: Optional[Dict] = None):
        """Initialize lineage analyzer."""
        self.model = model_data
        self.dependencies = dependencies
        self.report = report_data
        self.logger = logger
        self._indexes: Optional[
            Tuple[Dict[str, List[Dict[str, str]]], Dict[Tuple[str, str], List[Dict[str, str]]]]
        ] = None

    def _build_indexes(self):
        """Inverted indexes, built once per analyzer (P-08 / G-20).

        ``column key -> [visual entries]`` and ``(table, column) -> [relationship
        entries]``: lineage no longer scans every visual / relationship per column
        (O(columns x visuals) -> O(visuals + relationships + columns)).
        """
        if self._indexes is not None:
            return self._indexes
        visual_index: Dict[str, List[Dict[str, str]]] = {}
        if self.report:
            for visual_key, deps in self.dependencies.get("visual_dependencies", {}).items():
                entry = {
                    "visual_key": visual_key,
                    "visual_type": deps.get("visual_type", ""),
                    "page": deps.get("page", ""),
                }
                for column_key in dict.fromkeys(deps.get("columns", [])):  # once per visual
                    visual_index.setdefault(column_key, []).append(entry)
        rel_index: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
        for rel in self.model.get("relationships", []):
            r = _rel_fields(rel)
            ft, fc, tt, tc = r["from_table"], r["from_column"], r["to_table"], r["to_column"]
            rel_index.setdefault((ft, fc), []).append(
                {"role": "from", "to_table": tt, "to_column": tc, "cardinality": r["cardinality"]}
            )
            if (tt, tc) != (ft, fc):
                rel_index.setdefault((tt, tc), []).append(
                    {
                        "role": "to",
                        "from_table": ft,
                        "from_column": fc,
                        "cardinality": r["cardinality"],
                    }
                )
        self._indexes = (visual_index, rel_index)
        return self._indexes

    def _column_info(self, table_name: str, column: Dict, unused: Set[str]) -> Dict[str, Any]:
        visual_index, rel_index = self._build_indexes()
        column_name = column.get("name", "")
        column_key = f"{table_name}[{column_name}]"
        used_in_measures = self.dependencies.get("column_to_measure", {}).get(column_key, [])
        used_in_relationships = rel_index.get((table_name, column_name), [])
        used_in_visuals = visual_index.get(column_key, [])
        return {
            "table": table_name,
            "column": column_name,
            "data_type": column.get("data_type", "Unknown"),
            "is_calculated": bool(column.get("expression", "")),
            "source_expression": column.get("expression", ""),
            "used_in_measures": used_in_measures,
            "used_in_relationships": used_in_relationships,
            "used_in_visuals": used_in_visuals,
            "upstream_columns": [],  # Columns this column depends on
            "downstream_usage": {},  # Where this column is used
            "usage_score": len(used_in_measures)
            + len(used_in_relationships)
            + len(used_in_visuals),
            "is_orphan": column_key in unused,
        }

    def analyze_column_lineage(self) -> Dict[str, Any]:
        """Analyze column lineage from source to destination."""
        lineage_map = {}
        unused_columns_set = set(self.dependencies.get("unused_columns", []))
        for table in self.model.get("tables", []):
            table_name = table.get("name", "")
            for column in table.get("columns", []):
                info = self._column_info(table_name, column, unused_columns_set)
                lineage_map[f"{table_name}[{info['column']}]"] = info
        return lineage_map

    def _find_relationship_usage(self, table_name: str, column_name: str) -> List[Dict[str, str]]:
        """Find relationships that use this column."""
        return list(self._build_indexes()[1].get((table_name, column_name), []))

    def _find_visual_usage(self, column_key: str) -> List[Dict[str, str]]:
        """Find visuals that use this column."""
        return list(self._build_indexes()[0].get(column_key, []))

    def calculate_column_impact(self, column_key: str) -> Dict[str, Any]:
        """Calculate the impact of changing or removing a column. column_key: 'Table[Column]'."""
        unused = set(self.dependencies.get("unused_columns", []))
        col_info = None
        for table in self.model.get("tables", []):
            table_name = table.get("name", "")
            for column in table.get("columns", []):
                if f"{table_name}[{column.get('name', '')}]" == column_key:
                    col_info = self._column_info(table_name, column, unused)
                    break
            if col_info is not None:
                break

        if col_info is None:
            return {"error": f"Column {column_key} not found"}

        return {
            "column": column_key,
            "direct_impact": {
                "measures": len(col_info["used_in_measures"]),
                "relationships": len(col_info["used_in_relationships"]),
                "visuals": len(col_info["used_in_visuals"]),
            },
            "affected_objects": {
                "measures": col_info["used_in_measures"],
                "relationships": col_info["used_in_relationships"],
                "visuals": col_info["used_in_visuals"],
            },
            "risk_level": self._calculate_risk_level(col_info),
            "recommendations": self._generate_column_recommendations(col_info),
        }

    def _calculate_risk_level(self, col_info: Dict) -> str:
        """Calculate risk level for column changes."""
        usage_score = col_info["usage_score"]

        if usage_score == 0:
            return "LOW"  # Unused column
        elif usage_score <= 3:
            return "MEDIUM"
        elif usage_score <= 10:
            return "HIGH"
        else:
            return "CRITICAL"

    def _generate_column_recommendations(self, col_info: Dict) -> List[str]:
        """Generate recommendations for column usage."""
        recommendations = []

        if col_info["is_orphan"]:
            recommendations.append(
                "This column appears to be unused and could be removed to reduce model size"
            )

        if col_info["is_calculated"] and len(col_info["used_in_measures"]) == 0:
            recommendations.append(
                "Consider moving this calculated column logic to a measure for better performance"
            )

        if len(col_info["used_in_relationships"]) > 0 and col_info["is_calculated"]:
            recommendations.append(
                "WARNING: Calculated columns in relationships can cause performance issues"
            )

        return recommendations


class DataTypeCardinalityAnalyzer:
    """Analyzes data types and cardinality for optimization recommendations."""

    def __init__(self, model_data: Dict):
        """Initialize analyzer with model data."""
        self.model = model_data
        self.logger = logger

    def analyze_data_types(self) -> Dict[str, Any]:
        """Analyze data types for optimization opportunities."""
        type_issues = []
        type_summary = Counter()

        for table in self.model.get("tables", []):
            table_name = table.get("name", "")

            for column in table.get("columns", []):
                column_name = column.get("name", "")
                data_type = column.get("data_type", "String")

                type_summary[data_type] += 1

                # Check for inefficient data types
                issues = self._check_data_type_issues(table_name, column_name, data_type, column)
                type_issues.extend(issues)

        return {
            "type_summary": dict(type_summary),
            "type_issues": type_issues,
            "recommendations": self._generate_type_recommendations(type_issues),
        }

    def _check_data_type_issues(
        self, table_name: str, column_name: str, data_type: str, column: Dict
    ) -> List[Dict[str, Any]]:
        """Check for data type optimization opportunities."""
        issues = []

        # Int64 could be Int32
        if data_type == "Int64":
            issues.append(
                {
                    "severity": "INFO",
                    "table": table_name,
                    "column": column_name,
                    "current_type": data_type,
                    "issue": "Int64 columns use more memory than Int32",
                    "recommendation": "Consider using Int32 if values fit within -2,147,483,648 to 2,147,483,647",
                    "impact": "MEDIUM",
                }
            )

        # Decimal should be Double for aggregations
        if data_type == "Decimal" and not column.get("expression"):
            issues.append(
                {
                    "severity": "WARNING",
                    "table": table_name,
                    "column": column_name,
                    "current_type": data_type,
                    "issue": "Decimal type can be slower for aggregations",
                    "recommendation": "Use Double for numeric columns that will be aggregated",
                    "impact": "MEDIUM",
                }
            )

        # String length analysis (if format_string contains length hint)
        if data_type == "String":
            # Check for potential date/time strings
            if any(kw in column_name.lower() for kw in ["date", "time", "year", "month"]):
                issues.append(
                    {
                        "severity": "WARNING",
                        "table": table_name,
                        "column": column_name,
                        "current_type": data_type,
                        "issue": "Date/time data stored as string",
                        "recommendation": "Use DateTime or Int64 (YYYYMMDD) for better performance",
                        "impact": "HIGH",
                    }
                )

        return issues

    def _generate_type_recommendations(self, issues: List[Dict]) -> List[str]:
        """Generate summary recommendations from type issues."""
        recommendations = []

        high_impact = [i for i in issues if i.get("impact") == "HIGH"]
        if high_impact:
            recommendations.append(
                f"Found {len(high_impact)} high-impact data type issues that should be addressed"
            )

        int64_count = len([i for i in issues if i.get("current_type") == "Int64"])
        if int64_count > 5:
            recommendations.append(
                f"Consider reviewing {int64_count} Int64 columns for potential Int32 conversion"
            )

        return recommendations

    def analyze_cardinality(self) -> Dict[str, Any]:
        """Analyze column cardinality for performance insights (static analysis only; runtime requires Vertipaq)."""
        cardinality_warnings = []

        for table in self.model.get("tables", []):
            table_name = table.get("name", "")

            for column in table.get("columns", []):
                column_name = column.get("name", "")
                data_type = column.get("data_type", "")

                # Heuristic warnings
                warnings = self._check_cardinality_heuristics(
                    table_name, column_name, data_type, column
                )
                cardinality_warnings.extend(warnings)

        return {
            "cardinality_warnings": cardinality_warnings,
            "summary": {
                "total_warnings": len(cardinality_warnings),
                "high_cardinality_columns": len(
                    [w for w in cardinality_warnings if w.get("type") == "high"]
                ),
            },
        }

    def _check_cardinality_heuristics(
        self, table_name: str, column_name: str, data_type: str, column: Dict
    ) -> List[Dict[str, Any]]:
        """Check for potential high-cardinality issues using heuristics."""
        warnings = []

        # ID columns are often high cardinality
        if re.search(r"\b(id|key|guid|uuid)\b", column_name, re.IGNORECASE):
            warnings.append(
                {
                    "type": "high",
                    "table": table_name,
                    "column": column_name,
                    "reason": "Column name suggests high cardinality (ID/Key)",
                    "recommendation": "Mark as hidden if not used for filtering",
                    "is_hidden": column.get("is_hidden", False),
                }
            )

        # DateTime columns can be high cardinality
        if data_type == "DateTime":
            warnings.append(
                {
                    "type": "high",
                    "table": table_name,
                    "column": column_name,
                    "reason": "DateTime columns have high cardinality",
                    "recommendation": "Consider using separate Date and Time columns, or pre-aggregating data",
                    "is_hidden": column.get("is_hidden", False),
                }
            )

        return warnings


class RelationshipQualityAnalyzer:
    """Analyzes relationship quality and detects issues."""

    def __init__(self, model_data: Dict):
        """Initialize with model data."""
        self.model = model_data
        self.logger = logger

    def analyze_relationships(self) -> Dict[str, Any]:
        """Analyze all relationships for quality and issues."""
        relationship_issues = []
        relationship_metrics = {
            "total": 0,
            "one_to_many": 0,
            "many_to_one": 0,
            "one_to_one": 0,
            "many_to_many": 0,
            "bi_directional": 0,
            "inactive": 0,
            "issues_found": 0,
        }

        for rel in self.model.get("relationships", []):
            relationship_metrics["total"] += 1

            fields = _rel_fields(rel)
            # Count by cardinality
            cardinality = fields["cardinality"]
            if "OneToMany" in cardinality or "1:*" in cardinality:
                relationship_metrics["one_to_many"] += 1
            elif "ManyToOne" in cardinality or "*:1" in cardinality:
                relationship_metrics["many_to_one"] += 1
            elif "OneToOne" in cardinality or "1:1" in cardinality:
                relationship_metrics["one_to_one"] += 1
            elif "ManyToMany" in cardinality or "*:*" in cardinality:
                relationship_metrics["many_to_many"] += 1

            # Check for bi-directional
            cross_filter = fields["cross_filter"]
            if "BothDirections" in cross_filter or "both" in cross_filter.lower():
                relationship_metrics["bi_directional"] += 1

            # Check for inactive
            if not rel.get("is_active", rel.get("isActive", True)):
                relationship_metrics["inactive"] += 1

            # Analyze issues
            issues = self._check_relationship_issues(rel)
            relationship_issues.extend(issues)

        relationship_metrics["issues_found"] = len(relationship_issues)

        return {
            "metrics": relationship_metrics,
            "issues": relationship_issues,
            "recommendations": self._generate_relationship_recommendations(
                relationship_issues, relationship_metrics
            ),
        }

    def _check_relationship_issues(self, rel: Dict) -> List[Dict[str, Any]]:
        """Check a single relationship for issues."""
        issues = []

        fields = _rel_fields(rel)
        from_table = fields["from_table"]
        to_table = fields["to_table"]
        cardinality = fields["cardinality"]
        cross_filter = fields["cross_filter"]

        # Many-to-many warning
        if "ManyToMany" in cardinality or "*:*" in cardinality:
            issues.append(
                {
                    "severity": "ERROR",
                    "type": "many_to_many",
                    "from_table": from_table,
                    "to_table": to_table,
                    "issue": "Many-to-many relationship detected",
                    "recommendation": "Use a bridge table to resolve many-to-many relationships",
                    "impact": "HIGH",
                }
            )

        # Bi-directional filtering warning
        if "BothDirections" in cross_filter or "both" in cross_filter.lower():
            issues.append(
                {
                    "severity": "WARNING",
                    "type": "bi_directional",
                    "from_table": from_table,
                    "to_table": to_table,
                    "issue": "Bi-directional cross-filtering enabled",
                    "recommendation": "Avoid bi-directional filtering unless necessary; can cause performance issues and ambiguity",
                    "impact": "MEDIUM",
                }
            )

        # Circular dependency check (simple heuristic)
        if from_table == to_table:
            issues.append(
                {
                    "severity": "ERROR",
                    "type": "self_reference",
                    "from_table": from_table,
                    "to_table": to_table,
                    "issue": "Self-referencing relationship",
                    "recommendation": "Review model design; self-references require careful handling",
                    "impact": "HIGH",
                }
            )

        return issues

    def _generate_relationship_recommendations(
        self, issues: List[Dict], metrics: Dict
    ) -> List[str]:
        """Generate relationship recommendations."""
        recommendations = []

        if metrics["many_to_many"] > 0:
            recommendations.append(
                f"Found {metrics['many_to_many']} many-to-many relationships. "
                "Consider using bridge tables for better performance."
            )

        if metrics["bi_directional"] > metrics["total"] * 0.3:  # More than 30%
            recommendations.append(
                f"{metrics['bi_directional']} relationships use bi-directional filtering. "
                "This can impact performance and query clarity."
            )

        if metrics["inactive"] > 0:
            recommendations.append(
                f"Found {metrics['inactive']} inactive relationships. "
                "Review if these are needed or should be removed."
            )

        return recommendations


class DaxCodeQualityAnalyzer:
    """Analyzes DAX code quality metrics."""

    def __init__(self, model_data: Dict):
        """Initialize with model data."""
        self.model = model_data
        self.logger = logger

    def analyze_dax_quality(self) -> Dict[str, Any]:
        """Analyze DAX code quality across all measures."""
        quality_issues = []
        complexity_scores = []

        for table in self.model.get("tables", []):
            table_name = table.get("name", "")

            for measure in table.get("measures", []):
                measure_name = measure.get("name", "")
                expression = measure.get("expression", "")

                if not expression:
                    continue

                # Calculate metrics
                metrics = self._calculate_dax_metrics(expression)
                complexity_scores.append(metrics["complexity_score"])

                # Check for issues
                issues = self._check_dax_issues(table_name, measure_name, expression, metrics)
                quality_issues.extend(issues)

        avg_complexity = sum(complexity_scores) / len(complexity_scores) if complexity_scores else 0

        return {
            "quality_issues": quality_issues,
            "summary": {
                "total_measures": len(complexity_scores),
                "avg_complexity": round(avg_complexity, 2),
                "high_complexity_measures": len([s for s in complexity_scores if s > 15]),
                "total_issues": len(quality_issues),
            },
        }

    def _calculate_dax_metrics(self, expression: str) -> Dict[str, Any]:
        """Calculate DAX complexity metrics."""
        # Cyclomatic complexity (simplified)
        complexity_score = 1  # Base complexity
        complexity_score += expression.upper().count("IF(")
        complexity_score += expression.upper().count("SWITCH(")
        complexity_score += expression.upper().count("AND(")
        complexity_score += expression.upper().count("OR(")

        # Nesting depth
        max_nesting = self._calculate_max_nesting(expression)

        # Function count
        function_count = len(re.findall(r"\b[A-Z][A-Z0-9_]*\s*\(", expression))

        # Variable usage
        variable_count = expression.upper().count("VAR ")

        # Length
        expression_length = len(expression)

        # Calculate score
        total_score = (
            complexity_score
            + (max_nesting * 2)
            + (function_count * 0.5)
            + (expression_length / 100)
        )

        return {
            "complexity_score": round(total_score, 2),
            "max_nesting": max_nesting,
            "function_count": function_count,
            "variable_count": variable_count,
            "expression_length": expression_length,
            "has_variables": variable_count > 0,
        }

    def _calculate_max_nesting(self, expression: str) -> int:
        """Calculate maximum parenthesis nesting depth."""
        max_depth = 0
        current_depth = 0

        for char in expression:
            if char == "(":
                current_depth += 1
                max_depth = max(max_depth, current_depth)
            elif char == ")":
                current_depth -= 1

        return max_depth

    def _check_dax_issues(
        self, table_name: str, measure_name: str, expression: str, metrics: Dict
    ) -> List[Dict[str, Any]]:
        """Check for DAX anti-patterns and issues."""
        issues = []

        # High complexity
        if metrics["complexity_score"] > 15:
            issues.append(
                {
                    "severity": "WARNING",
                    "type": "high_complexity",
                    "table": table_name,
                    "measure": measure_name,
                    "issue": f"High complexity score: {metrics['complexity_score']}",
                    "recommendation": "Consider breaking this measure into smaller, reusable measures",
                    "complexity_score": metrics["complexity_score"],
                }
            )

        # Deep nesting
        if metrics["max_nesting"] > 5:
            issues.append(
                {
                    "severity": "WARNING",
                    "type": "deep_nesting",
                    "table": table_name,
                    "measure": measure_name,
                    "issue": f"Deep nesting level: {metrics['max_nesting']}",
                    "recommendation": "Use variables to flatten nested expressions",
                }
            )

        # CALCULATE anti-pattern
        if expression.upper().count("CALCULATE") > 3:
            issues.append(
                {
                    "severity": "INFO",
                    "type": "excessive_calculate",
                    "table": table_name,
                    "measure": measure_name,
                    "issue": "Multiple CALCULATE functions detected",
                    "recommendation": "Review if calculations can be simplified or combined",
                }
            )

        # No variables in complex measure
        if metrics["complexity_score"] > 10 and not metrics["has_variables"]:
            issues.append(
                {
                    "severity": "INFO",
                    "type": "no_variables",
                    "table": table_name,
                    "measure": measure_name,
                    "issue": "Complex measure without variables",
                    "recommendation": "Use VAR to improve readability and potentially performance",
                }
            )

        # SUMX with FILTER (potential performance issue)
        if re.search(r"SUMX\s*\(\s*FILTER", expression, re.IGNORECASE):
            issues.append(
                {
                    "severity": "WARNING",
                    "type": "sumx_filter",
                    "table": table_name,
                    "measure": measure_name,
                    "issue": "SUMX(FILTER(...)) pattern detected",
                    "recommendation": "Consider using CALCULATE with filters for better performance",
                }
            )

        return issues


class NamingConventionValidator:
    """Validates object naming conventions."""

    def __init__(self, model_data: Dict):
        """Initialize validator."""
        self.model = model_data
        self.logger = logger

    def validate_naming_conventions(self, rules: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Validate naming conventions against customizable rules. rules: e.g. {"measure_prefix": "m_"}."""
        # Default rules
        default_rules = {
            "measure_prefix": "",  # Empty = no required prefix
            "table_pascal_case": True,
            "column_pascal_case": True,
            "no_spaces": False,  # Allow spaces by default
            "max_length": 100,
        }

        if rules:
            default_rules.update(rules)

        violations = []

        # Check table names
        for table in self.model.get("tables", []):
            table_name = table.get("name", "")
            table_violations = self._check_name_conventions(
                table_name, "Table", table_name, None, default_rules
            )
            violations.extend(table_violations)

            # Check columns
            for column in table.get("columns", []):
                column_name = column.get("name", "")
                col_violations = self._check_name_conventions(
                    column_name, "Column", table_name, column_name, default_rules
                )
                violations.extend(col_violations)

            # Check measures
            for measure in table.get("measures", []):
                measure_name = measure.get("name", "")
                measure_violations = self._check_name_conventions(
                    measure_name,
                    "Measure",
                    table_name,
                    measure_name,
                    default_rules,
                    prefix_rule=default_rules.get("measure_prefix"),
                )
                violations.extend(measure_violations)

        return {
            "violations": violations,
            "summary": {
                "total_violations": len(violations),
                "by_type": self._count_by_type(violations),
                "by_severity": self._count_by_severity(violations),
            },
        }

    def _check_name_conventions(
        self,
        name: str,
        obj_type: str,
        table_name: str,
        obj_name: Optional[str],
        rules: Dict,
        prefix_rule: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Check a single name against conventions."""
        violations = []

        # Prefix check
        if prefix_rule and not name.startswith(prefix_rule):
            violations.append(
                {
                    "severity": "INFO",
                    "type": "missing_prefix",
                    "object_type": obj_type,
                    "table": table_name,
                    "object": obj_name or name,
                    "issue": f"Missing prefix '{prefix_rule}'",
                    "current_name": name,
                }
            )

        # Spaces check
        if rules.get("no_spaces") and " " in name:
            violations.append(
                {
                    "severity": "INFO",
                    "type": "contains_spaces",
                    "object_type": obj_type,
                    "table": table_name,
                    "object": obj_name or name,
                    "issue": "Name contains spaces",
                    "current_name": name,
                }
            )

        # Length check
        max_len = rules.get("max_length", 100)
        if len(name) > max_len:
            violations.append(
                {
                    "severity": "WARNING",
                    "type": "name_too_long",
                    "object_type": obj_type,
                    "table": table_name,
                    "object": obj_name or name,
                    "issue": f"Name exceeds {max_len} characters ({len(name)})",
                    "current_name": name,
                }
            )

        # Special characters check
        if re.search(r"[^\w\s\-_]", name):
            violations.append(
                {
                    "severity": "WARNING",
                    "type": "special_characters",
                    "object_type": obj_type,
                    "table": table_name,
                    "object": obj_name or name,
                    "issue": "Name contains special characters",
                    "current_name": name,
                }
            )

        return violations

    def _count_by_type(self, violations: List[Dict]) -> Dict[str, int]:
        """Count violations by type."""
        counts = Counter(v["type"] for v in violations)
        return dict(counts)

    def _count_by_severity(self, violations: List[Dict]) -> Dict[str, int]:
        """Count violations by severity."""
        counts = Counter(v["severity"] for v in violations)
        return dict(counts)


class PerspectiveAnalyzer:
    """Analyzes perspectives and object visibility."""

    def __init__(self, model_data: Dict):
        """Initialize analyzer."""
        self.model = model_data
        self.logger = logger

    def analyze_perspectives(self) -> Dict[str, Any]:
        """Analyze perspective usage and coverage."""
        perspectives = self.model.get("perspectives", [])

        if not perspectives:
            return {"has_perspectives": False, "message": "No perspectives defined in the model"}

        perspective_analysis = []

        for persp in perspectives:
            persp_name = persp.get("name", "")
            persp_items = persp.get("perspectiveTableReferences", [])

            # Count objects in perspective
            table_count = len(persp_items)
            column_count = sum(
                len(item.get("perspectiveColumnReferences", [])) for item in persp_items
            )
            measure_count = sum(
                len(item.get("perspectiveMeasureReferences", [])) for item in persp_items
            )

            perspective_analysis.append(
                {
                    "name": persp_name,
                    "table_count": table_count,
                    "column_count": column_count,
                    "measure_count": measure_count,
                    "total_objects": table_count + column_count + measure_count,
                }
            )

        # Check for unused perspectives (empty)
        unused_perspectives = [p for p in perspective_analysis if p["total_objects"] == 0]

        return {
            "has_perspectives": True,
            "perspective_count": len(perspectives),
            "perspectives": perspective_analysis,
            "unused_perspectives": unused_perspectives,
            "summary": {
                "total_perspectives": len(perspectives),
                "unused_count": len(unused_perspectives),
            },
        }


class EnhancedPbipAnalyzer:
    """
    Main enhanced analyzer that orchestrates all analysis components.
    """

    def __init__(
        self,
        model_data: Dict,
        report_data: Optional[Dict] = None,
        dependencies: Optional[Dict] = None,
    ):
        """Initialize enhanced analyzer."""
        self.model = model_data
        self.report = report_data
        self.dependencies = dependencies or {}
        self.logger = logger

        # Initialize sub-analyzers
        self.lineage_analyzer = ColumnLineageAnalyzer(model_data, self.dependencies, report_data)
        self.type_analyzer = DataTypeCardinalityAnalyzer(model_data)
        self.relationship_analyzer = RelationshipQualityAnalyzer(model_data)
        self.dax_analyzer = DaxCodeQualityAnalyzer(model_data)
        self.naming_validator = NamingConventionValidator(model_data)
        self.perspective_analyzer = PerspectiveAnalyzer(model_data)

    def _filter_bpa_violations(self, violations: List) -> List:
        """
        Filter BPA violations to remove false positives for PBIP analysis.

        PBIP analysis works with TMDL metadata files only - NO data access.
        We filter out:
        1. Usage-based violations (unused columns/measures) - use dependency analysis instead
        2. Data-based violations (empty columns, cardinality) - PBIP has no data access
        """
        unused_columns_set = set(self.dependencies.get("unused_columns", []))
        unused_measures_set = set(self.dependencies.get("unused_measures", []))

        # Rules that require runtime data access (INVALID for PBIP analysis)
        DATA_DEPENDENT_RULES = {
            "AVOID_COLUMNS_WITH_NO_DATA",  # Can't check if columns are empty without data
            "NO_EMPTY_PARTITIONS",  # Can't check row counts without data
            "REDUCE_HIGH_CARDINALITY",  # Cardinality requires Vertipaq/runtime stats
        }

        filtered = []
        for v in violations:
            # Skip data-dependent rules entirely for PBIP analysis
            if v.rule_id in DATA_DEPENDENT_RULES:
                continue

            # For "Remove unused columns" rule, verify it's actually unused
            if v.rule_id == "REMOVE_UNUSED_COLUMNS":
                # Build column key
                if v.table_name and v.object_name:
                    col_key = f"{v.table_name}[{v.object_name}]"
                    # Only include if actually in unused set
                    if col_key in unused_columns_set:
                        filtered.append(v)
                continue

            # For "Remove unused measures" rule, verify it's actually unused
            if v.rule_id == "REMOVE_UNUSED_MEASURES":
                # Build measure key
                if v.table_name and v.object_name:
                    meas_key = f"{v.table_name}[{v.object_name}]"
                    # Only include if actually in unused set
                    if meas_key in unused_measures_set:
                        filtered.append(v)
                continue

            # Keep all other violations
            filtered.append(v)

        return filtered

    def _calculate_violations_summary(self, violations: List) -> Dict[str, Any]:
        """Calculate summary statistics for filtered BPA violations."""
        from collections import defaultdict

        total = len(violations)
        by_severity = defaultdict(int)
        by_category = defaultdict(int)

        for v in violations:
            by_severity[v.severity.name] += 1
            by_category[v.category] += 1

        return {"total": total, "by_severity": dict(by_severity), "by_category": dict(by_category)}

    def _transform_pbip_to_tmsl(self, pbip_model: Dict[str, Any]) -> Dict[str, Any]:
        """Transform PBIP snake_case model structure to TMSL camelCase/PascalCase for BPA analyzer compatibility."""
        tmsl_model = {"tables": []}

        # Transform tables
        for table in pbip_model.get("tables", []):
            table_name = table.get("name")
            tmsl_table = {
                "name": table_name,
                "Name": table_name,  # Both cases for compatibility
                "IsHidden": table.get("is_hidden", False),
                "isHidden": table.get("is_hidden", False),
                "columns": [],
                "measures": [],
            }

            # Transform columns
            for col in table.get("columns", []):
                col_name = col.get("name")

                tmsl_col = {
                    "name": col_name,
                    "Name": col_name,
                    "dataType": col.get("data_type", ""),
                    "DataType": col.get("data_type", ""),
                    "sourceColumn": col.get("source_column", ""),
                    "SourceColumn": col.get("source_column", ""),
                    "IsHidden": col.get("is_hidden", False),
                    "isHidden": col.get("is_hidden", False),
                    "formatString": col.get("format_string", ""),
                    "FormatString": col.get("format_string", ""),
                    "dataCategory": col.get("data_category", ""),
                    "DataCategory": col.get("data_category", ""),
                    "displayFolder": col.get("display_folder", ""),
                    "DisplayFolder": col.get("display_folder", ""),
                    "description": col.get("description", ""),
                    "Description": col.get("description", ""),
                    "Expression": col.get("expression", ""),  # For calculated columns
                }
                tmsl_table["columns"].append(tmsl_col)

            # Transform measures
            for meas in table.get("measures", []):
                tmsl_meas = {
                    "name": meas.get("name"),
                    "Name": meas.get("name"),
                    "expression": meas.get("expression", ""),
                    "Expression": meas.get("expression", ""),
                    "formatString": meas.get("format_string", ""),
                    "FormatString": meas.get("format_string", ""),
                    "isHidden": meas.get("is_hidden", False),
                    "IsHidden": meas.get("is_hidden", False),
                    "displayFolder": meas.get("display_folder", ""),
                    "DisplayFolder": meas.get("display_folder", ""),
                    "description": meas.get("description", ""),
                    "Description": meas.get("description", ""),
                }
                tmsl_table["measures"].append(tmsl_meas)

            tmsl_model["tables"].append(tmsl_table)

        # Transform relationships (G-20: bare from/to names + real cardinality; the old
        # code sent the raw ``'T'.'C'`` reference, a non-existent ``name`` key and a
        # hard-coded OneToMany)
        tmsl_model["relationships"] = []
        for rel in pbip_model.get("relationships", []):
            f = _rel_fields(rel)
            name = (
                rel.get("name")
                or rel.get("id")
                or (f"{f['from_table']}.{f['from_column']}->{f['to_table']}.{f['to_column']}")
            )
            cross = f["cross_filter"] or "oneDirection"
            security = rel.get("security_filtering_behavior") or "oneDirection"
            from_card = _end_cardinality(
                rel.get("from_cardinality", rel.get("fromCardinality")), "many"
            )
            to_card = _end_cardinality(rel.get("to_cardinality", rel.get("toCardinality")), "one")
            active = rel.get("is_active", rel.get("isActive", True))
            tmsl_rel = {
                "name": name,
                "Name": name,
                "fromTable": f["from_table"],
                "FromTable": f["from_table"],
                "fromColumn": f["from_column"],
                "FromColumn": f["from_column"],
                "toTable": f["to_table"],
                "ToTable": f["to_table"],
                "toColumn": f["to_column"],
                "ToColumn": f["to_column"],
                "fromCardinality": from_card,
                "toCardinality": to_card,
                "crossFilteringBehavior": cross,
                "CrossFilteringBehavior": cross,
                "isActive": active,
                "IsActive": active,
                "securityFilteringBehavior": security,
                "SecurityFilteringBehavior": security,
                "Multiplicity": f["cardinality"],
                "multiplicity": f["cardinality"],
            }
            tmsl_model["relationships"].append(tmsl_rel)

        return tmsl_model

    def _model_name(self) -> str:
        """Model name: the database name, else the model folder stem (never the old "Unknown")."""
        name = self.model.get("name")
        if name:
            return str(name)
        db = self.model.get("database")
        if isinstance(db, dict) and db.get("name"):
            return str(db["name"])
        folder = self.model.get("model_folder")
        if folder:
            stem = Path(str(folder).rstrip("/\\")).name
            if stem.lower().endswith(".semanticmodel"):
                stem = stem[: -len(".semanticmodel")]
            if stem:
                return stem
        return "Unknown"

    #: Cap on the BPA violations carried inside the analysis result (the summary
    #: always counts all of them).
    MAX_BPA_VIOLATIONS = 1000

    def run_full_analysis(
        self, bpa_rules_path: Optional[str] = None, include_bpa: bool = True
    ) -> Dict[str, Any]:
        """Run complete enhanced analysis across all sub-analyzers.

        BPA runs with the built-in rule set (plus *bpa_rules_path* when given)
        unless ``include_bpa=False`` (G-20: it used to be skipped because no rules
        path was ever passed, and imported a module that does not exist here).
        """
        self.logger.info("Starting enhanced PBIP analysis")

        results = {
            "timestamp": datetime.now().isoformat(),
            "model_name": self._model_name(),
            "analyses": {},
        }

        # 1. Column Lineage & Impact Analysis
        self.logger.info("Analyzing column lineage...")
        results["analyses"]["column_lineage"] = self.lineage_analyzer.analyze_column_lineage()

        # 2. Data Type & Cardinality Analysis
        self.logger.info("Analyzing data types and cardinality...")
        results["analyses"]["data_types"] = self.type_analyzer.analyze_data_types()
        results["analyses"]["cardinality"] = self.type_analyzer.analyze_cardinality()

        # 3. Relationship Quality Metrics
        self.logger.info("Analyzing relationship quality...")
        results["analyses"]["relationships"] = self.relationship_analyzer.analyze_relationships()

        # 4. DAX Code Quality Metrics
        self.logger.info("Analyzing DAX code quality...")
        results["analyses"]["dax_quality"] = self.dax_analyzer.analyze_dax_quality()

        # 5. Naming Convention Validation
        self.logger.info("Validating naming conventions...")
        results["analyses"][
            "naming_conventions"
        ] = self.naming_validator.validate_naming_conventions()

        # 6. Perspective Analysis
        self.logger.info("Analyzing perspectives...")
        results["analyses"]["perspectives"] = self.perspective_analyzer.analyze_perspectives()

        # 7. BPA Analysis
        if include_bpa or bpa_rules_path:
            self.logger.info("Running Best Practice Analyzer...")
            try:
                from core.analysis.bpa_analyzer import BPAAnalyzer

                bpa = BPAAnalyzer(bpa_rules_path)
                # Transform PBIP model structure to TMSL structure for BPA compatibility
                tmsl_model = self._transform_pbip_to_tmsl(self.model)
                violations = bpa.analyze_model(tmsl_model)

                # Filter violations to remove false positives based on dependency analysis
                filtered_violations = self._filter_bpa_violations(violations)

                # Recalculate summary based on filtered violations
                filtered_summary = self._calculate_violations_summary(filtered_violations)

                shown = filtered_violations[: self.MAX_BPA_VIOLATIONS]
                results["analyses"]["bpa"] = {
                    "ran": True,
                    "violations_total": len(filtered_violations),
                    "truncated": len(filtered_violations) > len(shown),
                    "violations": [
                        {
                            "rule_id": v.rule_id,
                            "rule_name": v.rule_name,
                            "category": v.category,
                            "severity": v.severity.name,
                            "description": v.description,
                            "object_type": v.object_type,
                            "object_name": v.object_name,
                            "table_name": v.table_name,
                            "details": v.details,
                        }
                        for v in shown
                    ],
                    "summary": filtered_summary,
                }
            except Exception as e:
                self.logger.error(f"BPA analysis failed: {e}")
                results["analyses"]["bpa"] = {"ran": False, "error": str(e)}

        self.logger.info("Enhanced analysis complete")

        return results
