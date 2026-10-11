"""``AnalysisOrchestrator`` methods split out of ``core.orchestration.analysis_orchestrator`` (C-17): pure move, no behaviour change.

``AnalysisOrchestrator`` inherits :class:`OrchestratorReportMixin`, so every ``self.<method>`` call and every import of
``core.orchestration.analysis_orchestrator.AnalysisOrchestrator`` keeps working.
"""

import logging
from typing import Any, Dict
import re

logger = logging.getLogger("core.orchestration.analysis_orchestrator")


class OrchestratorReportMixin:
    """Methods of :class:`AnalysisOrchestrator` (orchestrator_report.py)."""

    def _generate_relationship_diagram(self, relationships: list) -> str:
        """
        Generate ASCII diagram showing table relationships.

        Args:
            relationships: List of relationship dictionaries

        Returns:
            ASCII diagram as a string
        """
        if not relationships:
            return "No relationships found"

        # Group relationships by fromTable
        from collections import defaultdict

        table_rels = defaultdict(list)

        for rel in relationships:
            from_table = rel.get("fromTable", "")
            to_table = rel.get("toTable", "")
            from_card = rel.get("fromCardinality", "Many")
            to_card = rel.get("toCardinality", "One")
            is_active = rel.get("isActive", True)
            is_bidir = rel.get("crossFilteringBehavior") == "BothDirections"

            if from_table:
                table_rels[from_table].append(
                    {
                        "to": to_table,
                        "cardinality": f"{from_card}:{to_card}",
                        "active": is_active,
                        "bidirectional": is_bidir,
                    }
                )

        # Build ASCII diagram
        diagram_lines = []
        diagram_lines.append("=" * 80)
        diagram_lines.append("RELATIONSHIP DIAGRAM")
        diagram_lines.append("=" * 80)
        diagram_lines.append("")

        # Sort tables by number of relationships (descending) - fact tables usually have most
        sorted_tables = sorted(table_rels.items(), key=lambda x: len(x[1]), reverse=True)

        # Show top tables with their relationships
        for idx, (table, rels) in enumerate(sorted_tables[:15]):  # Limit to top 15 tables
            # Determine table type based on naming convention and relationship count
            table_type = ""
            if table.startswith("f_") or table.startswith("fact"):
                table_type = " (Fact)"
            elif table.startswith("d_") or table.startswith("dim"):
                table_type = " (Dimension)"
            elif table.startswith("m_"):
                table_type = " (Measures)"

            diagram_lines.append(f"{table}{table_type}")

            # Sort relationships by active status and table name
            sorted_rels = sorted(rels, key=lambda r: (not r["active"], r["to"]))

            for i, rel in enumerate(sorted_rels):
                is_last = i == len(sorted_rels) - 1
                # Use ASCII characters instead of Unicode box-drawing for better compatibility
                connector = "'-->" if is_last else "|-->"

                # Build relationship line
                rel_line = f"{connector} {rel['to']:<30} ({rel['cardinality']})"

                # Add indicators
                indicators = []
                if not rel["active"]:
                    indicators.append("INACTIVE")
                if rel["bidirectional"]:
                    indicators.append("BI-DIRECTIONAL")

                if indicators:
                    rel_line += f" [{', '.join(indicators)}]"

                diagram_lines.append(rel_line)

            diagram_lines.append("")  # Empty line between tables

        diagram_lines.append("=" * 80)

        return "\n".join(diagram_lines)

    def generate_expert_analysis(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate COMPREHENSIVE Power BI expert analysis from all operation results.

        Provides deep technical insights across:
        - Model architecture and patterns
        - Relationship complexity and issues
        - Measure organization and complexity
        - Calculation group implementation
        - Performance implications
        - Security implementation
        - Data quality and best practices

        Args:
            results: Dictionary containing all operation results

        Returns:
            Dictionary with detailed expert analysis and actionable insights
        """
        try:
            analysis = {
                "model_overview": {},
                "architecture_patterns": {},
                "table_analysis": {},
                "relationship_analysis": {},
                "measure_analysis": {},
                "calculation_group_analysis": {},
                "security_analysis": {},
                "performance_assessment": {},
                "data_quality": {},
                "recommendations": [],
            }

            ops = results.get("operations", {})

            # 1. Extract database info
            db_op = ops.get("01_database", {})
            if db_op.get("success"):
                db_data = db_op.get("data", [{}])[0]
                analysis["model_overview"] = {
                    "database_id": db_data.get("id", ""),
                    "compatibility_level": db_data.get("compatibilityLevel", 0),
                    "model_format": (
                        "Power BI Desktop"
                        if db_data.get("compatibilityLevel", 0) >= 1600
                        else "Analysis Services"
                    ),
                    "estimated_size_mb": (
                        round(db_data.get("estimatedSize", 0) / (1024 * 1024), 2)
                        if db_data.get("estimatedSize")
                        else 0
                    ),
                }

            # 2. Extract and DEEPLY ANALYZE stats
            stats_op = ops.get("02_stats", {})
            if stats_op.get("success"):
                counts = stats_op.get("counts", {})
                tables_list = stats_op.get("tables", [])

                # Basic counts
                total_tables = counts.get("tables", 0)
                total_columns = counts.get("columns", 0)
                total_measures = counts.get("measures", 0)
                total_relationships = counts.get("relationships", 0)

                analysis["model_overview"].update(
                    {
                        "total_tables": total_tables,
                        "total_columns": total_columns,
                        "total_measures": total_measures,
                        "total_relationships": total_relationships,
                        "calculation_groups": counts.get("calculation_groups", 0),
                        "security_roles": counts.get("roles", 0),
                    }
                )

                # DETAILED table classification
                measure_tables = [
                    t
                    for t in tables_list
                    if t.get("measure_count", 0) > 10 and t.get("column_count", 0) < 5
                ]
                fact_tables = [t for t in tables_list if t.get("type") == "fact"]
                dim_tables = [t for t in tables_list if t.get("type") == "dimension"]
                support_tables = [t for t in tables_list if t.get("type") == "support"]
                field_param_tables = [t for t in tables_list if t.get("type") == "field_parameter"]
                rls_tables = [t for t in tables_list if t.get("type") == "rls"]
                calc_group_tables = [t for t in tables_list if t.get("type") == "calculation_group"]
                other_tables = [t for t in tables_list if t.get("type") == "other"]

                # Complexity metrics
                avg_columns_per_table = (
                    round(total_columns / total_tables, 1) if total_tables > 0 else 0
                )
                avg_measures_per_table = (
                    round(total_measures / total_tables, 1) if total_tables > 0 else 0
                )
                measures_to_columns_ratio = (
                    round(total_measures / total_columns, 2) if total_columns > 0 else 0
                )

                # Find largest/most complex tables
                large_tables = sorted(
                    [t for t in tables_list if t.get("column_count", 0) > 30],
                    key=lambda x: x.get("column_count", 0),
                    reverse=True,
                )[:10]
                measure_heavy_tables = sorted(
                    [t for t in tables_list if t.get("measure_count", 0) > 20],
                    key=lambda x: x.get("measure_count", 0),
                    reverse=True,
                )[:10]

                analysis["architecture_patterns"] = {
                    "measure_tables": [t["name"] for t in measure_tables],
                    "measure_table_count": len(measure_tables),
                    "fact_tables": [t["name"] for t in fact_tables],
                    "fact_tables_count": len(fact_tables),
                    "dimension_tables": [t["name"] for t in dim_tables][:20],  # Show first 20
                    "dimension_tables_count": len(dim_tables),
                    "support_tables_count": len(support_tables),
                    "field_parameter_count": len(field_param_tables),
                    "field_parameter_tables": [t["name"] for t in field_param_tables][
                        :15
                    ],  # Show first 15
                    "rls_tables_count": len(rls_tables),
                    "calc_group_tables_count": len(calc_group_tables),
                    "other_tables_count": len(other_tables),
                    "uses_star_schema": len(fact_tables) > 0 and len(dim_tables) > 0,
                    "has_dedicated_measure_tables": len(measure_tables) > 0,
                    "schema_pattern": (
                        "Star Schema"
                        if (len(fact_tables) > 0 and len(dim_tables) > 0)
                        else "Unknown Pattern"
                    ),
                }

                analysis["table_analysis"] = {
                    "complexity_metrics": {
                        "avg_columns_per_table": avg_columns_per_table,
                        "avg_measures_per_table": avg_measures_per_table,
                        "measures_to_columns_ratio": measures_to_columns_ratio,
                        "complexity_score": (
                            "High"
                            if avg_columns_per_table > 50
                            else "Medium" if avg_columns_per_table > 20 else "Low"
                        ),
                    },
                    "largest_tables": [
                        {"name": t["name"], "columns": t.get("column_count", 0)}
                        for t in large_tables
                    ],
                    "measure_heavy_tables": [
                        {"name": t["name"], "measures": t.get("measure_count", 0)}
                        for t in measure_heavy_tables
                    ],
                    "hidden_tables_count": len(
                        [t for t in tables_list if t.get("is_hidden", False)]
                    ),
                    "table_type_distribution": {
                        "fact": len(fact_tables),
                        "dimension": len(dim_tables),
                        "measure": len(measure_tables),
                        "support": len(support_tables),
                        "field_parameter": len(field_param_tables),
                        "rls": len(rls_tables),
                        "calc_group": len(calc_group_tables),
                        "other": len(other_tables),
                    },
                }

            # 3. COMPREHENSIVE Measure Analysis
            measures_op = ops.get("04_measures", {})
            if measures_op.get("success"):
                measures_data = measures_op.get("data", [])

                # Analyze display folders hierarchies
                top_folders = {}
                all_folders = set()
                measures_with_folders = 0
                max_folder_depth = 0

                for m in measures_data:
                    folder = m.get("displayFolder", "")
                    if folder:
                        measures_with_folders += 1
                        all_folders.add(folder)

                        # Track folder hierarchy depth
                        depth = len(folder.split("\\"))
                        if depth > max_folder_depth:
                            max_folder_depth = depth

                        # Track top-level folders
                        top_folder = folder.split("\\")[0]
                        if top_folder not in top_folders:
                            top_folders[top_folder] = []
                        top_folders[top_folder].append(m.get("name", ""))

                folder_usage_pct = (
                    round(measures_with_folders / len(measures_data) * 100, 1)
                    if measures_data
                    else 0
                )

                # Identify measure patterns by name
                calc_measures = [
                    m
                    for m in measures_data
                    if "calc" in m.get("name", "").lower()
                    or "calculated" in m.get("name", "").lower()
                ]
                base_measures = [m for m in measures_data if "base" in m.get("name", "").lower()]
                ytd_measures = [
                    m
                    for m in measures_data
                    if re.search(
                        r"(?<![A-Za-z0-9])YTD(?![A-Za-z0-9])", m.get("name", ""), re.IGNORECASE
                    )
                ]
                mtd_measures = [
                    m
                    for m in measures_data
                    if re.search(
                        r"(?<![A-Za-z0-9])MTD(?![A-Za-z0-9])", m.get("name", ""), re.IGNORECASE
                    )
                ]
                # I-25: word-bounded — "Supply"/"Monthly"/"Only" are not last-year measures.
                ly_measures = [
                    m
                    for m in measures_data
                    if re.search(
                        r"(?<![A-Za-z0-9])(LY|PY|SPLY)(?![A-Za-z0-9])",
                        m.get("name", ""),
                        re.IGNORECASE,
                    )
                    or "last year" in m.get("name", "").lower()
                ]

                # Analyze folder structure quality
                folder_hierarchy = {folder: len(top_folders[folder]) for folder in top_folders}
                top_folders_by_size = sorted(
                    folder_hierarchy.items(), key=lambda x: x[1], reverse=True
                )[:15]

                analysis["measure_analysis"] = {
                    "total_measures": len(measures_data),
                    "organization": {
                        "measures_with_folders": measures_with_folders,
                        "folder_usage_percentage": folder_usage_pct,
                        "total_folders": len(all_folders),
                        "top_level_folders_count": len(top_folders),
                        "max_folder_depth": max_folder_depth,
                        "top_folders_by_measure_count": top_folders_by_size,
                        "organization_quality": (
                            "Excellent"
                            if folder_usage_pct > 90
                            else (
                                "Good"
                                if folder_usage_pct > 70
                                else "Fair" if folder_usage_pct > 40 else "Poor"
                            )
                        ),
                    },
                    "measure_patterns": {
                        "calc_measures": len(calc_measures),
                        "base_measures": len(base_measures),
                        "ytd_measures": len(ytd_measures),
                        "mtd_measures": len(mtd_measures),
                        "ly_measures": len(ly_measures),
                        "uses_time_intelligence": (
                            len(ytd_measures) + len(mtd_measures) + len(ly_measures)
                        )
                        > 5,
                    },
                    "measure_distribution": {
                        "avg_measures_per_folder": (
                            round(measures_with_folders / len(top_folders), 1) if top_folders else 0
                        ),
                        "largest_folder": (
                            top_folders_by_size[0][0] if top_folders_by_size else None
                        ),
                        "largest_folder_measure_count": (
                            top_folders_by_size[0][1] if top_folders_by_size else 0
                        ),
                    },
                }

            # 4. DEEP Relationship Analysis
            rel_op = ops.get("06_relationships", {})
            if rel_op.get("success"):
                rels = rel_op.get("data", [])

                # Categorize ALL relationship patterns
                many_to_many = [
                    r
                    for r in rels
                    if r.get("fromCardinality") == "Many" and r.get("toCardinality") == "Many"
                ]
                one_to_many = [
                    r
                    for r in rels
                    if r.get("fromCardinality") == "One" and r.get("toCardinality") == "Many"
                ]
                many_to_one = [
                    r
                    for r in rels
                    if r.get("fromCardinality") == "Many" and r.get("toCardinality") == "One"
                ]
                one_to_one = [
                    r
                    for r in rels
                    if r.get("fromCardinality") == "One" and r.get("toCardinality") == "One"
                ]
                bidirectional = [
                    r for r in rels if r.get("crossFilteringBehavior") == "BothDirections"
                ]
                inactive = [r for r in rels if not r.get("isActive")]

                # Analyze M:M relationship participants (which tables are involved)
                m2m_tables = set()
                m2m_examples = []
                for r in many_to_many[:10]:  # Get first 10 examples
                    from_table = r.get("fromTable", "")
                    to_table = r.get("toTable", "")
                    m2m_tables.add(from_table)
                    m2m_tables.add(to_table)
                    m2m_examples.append(
                        {
                            "from": from_table,
                            "to": to_table,
                            "active": r.get("isActive", True),
                            "bidirectional": r.get("crossFilteringBehavior") == "BothDirections",
                        }
                    )

                # Analyze bidirectional relationship participants
                bidir_examples = []
                for r in bidirectional[:10]:
                    bidir_examples.append(
                        {
                            "from": r.get("fromTable", ""),
                            "to": r.get("toTable", ""),
                            "cardinality": f"{r.get('fromCardinality', 'Unknown')}:{r.get('toCardinality', 'Unknown')}",
                        }
                    )

                # Find tables with most relationships (hub tables)
                table_rel_count = {}
                for r in rels:
                    from_table = r.get("fromTable", "")
                    to_table = r.get("toTable", "")
                    table_rel_count[from_table] = table_rel_count.get(from_table, 0) + 1
                    table_rel_count[to_table] = table_rel_count.get(to_table, 0) + 1

                hub_tables = sorted(table_rel_count.items(), key=lambda x: x[1], reverse=True)[:10]

                # Calculate relationship health metrics
                standard_rels = many_to_one + one_to_many
                standard_pct = round(len(standard_rels) / len(rels) * 100, 1) if rels else 0
                m2m_pct = round(len(many_to_many) / len(rels) * 100, 1) if rels else 0
                bidir_pct = round(len(bidirectional) / len(rels) * 100, 1) if rels else 0

                # Determine complexity level
                if m2m_pct > 25:
                    complexity = "Very Complex"
                    complexity_score = "Critical"
                elif m2m_pct > 15:
                    complexity = "Complex"
                    complexity_score = "High"
                elif m2m_pct > 5:
                    complexity = "Moderate"
                    complexity_score = "Medium"
                else:
                    complexity = "Standard"
                    complexity_score = "Low"

                # Generate relationship diagram
                relationship_diagram = self._generate_relationship_diagram(rels)

                analysis["relationship_analysis"] = {
                    "total_relationships": len(rels),
                    "cardinality_breakdown": {
                        "many_to_one": len(many_to_one),
                        "one_to_many": len(one_to_many),
                        "many_to_many": len(many_to_many),
                        "one_to_one": len(one_to_one),
                        "many_to_many_percentage": m2m_pct,
                        "bidirectional_count": len(bidirectional),
                        "bidirectional_percentage": bidir_pct,
                        "inactive_count": len(inactive),
                    },
                    "relationship_health": {
                        "standard_relationships": len(standard_rels),
                        "standard_percentage": standard_pct,
                        "health_score": (
                            "Excellent"
                            if standard_pct >= 95
                            else (
                                "Good"
                                if standard_pct >= 80
                                else "Fair" if standard_pct >= 60 else "Poor"
                            )
                        ),
                        "complexity_level": complexity,
                        "complexity_score": complexity_score,
                    },
                    "problem_relationships": {
                        "many_to_many_examples": m2m_examples,
                        "many_to_many_unique_tables": list(m2m_tables)[:20],
                        "bidirectional_examples": bidir_examples,
                    },
                    "hub_analysis": {
                        "hub_tables": [
                            {"table": t[0], "relationship_count": t[1]} for t in hub_tables
                        ],
                        "most_connected_table": hub_tables[0][0] if hub_tables else None,
                        "most_connected_count": hub_tables[0][1] if hub_tables else 0,
                    },
                    "diagram": relationship_diagram,
                }

            # 5. DETAILED Calculation Group Analysis
            calc_op = ops.get("07_calculation_groups", {})
            if calc_op.get("success"):
                calc_groups = calc_op.get("data", [])

                total_items = sum(len(cg.get("calculationItems", [])) for cg in calc_groups)

                # Analyze calculation group patterns
                time_intel_groups = [
                    cg
                    for cg in calc_groups
                    if any(
                        x in cg.get("name", "").lower()
                        for x in ["time", "date", "period", "ytd", "mtd"]
                    )
                ]
                currency_groups = [
                    cg
                    for cg in calc_groups
                    if any(x in cg.get("name", "").lower() for x in ["currency", "fx", "exchange"])
                ]
                scenario_groups = [
                    cg
                    for cg in calc_groups
                    if any(
                        x in cg.get("name", "").lower()
                        for x in ["scenario", "comparison", "variance"]
                    )
                ]

                # Detailed group information
                group_details = []
                for cg in calc_groups:
                    items = cg.get("calculationItems", [])
                    group_details.append(
                        {
                            "name": cg.get("name", ""),
                            "item_count": len(items),
                            "items": [item.get("name", "") for item in items],
                            "pattern": (
                                "Time Intelligence"
                                if cg in time_intel_groups
                                else (
                                    "Currency Conversion"
                                    if cg in currency_groups
                                    else (
                                        "Scenario Analysis"
                                        if cg in scenario_groups
                                        else "Custom Logic"
                                    )
                                )
                            ),
                        }
                    )

                analysis["calculation_group_analysis"] = {
                    "total_groups": len(calc_groups),
                    "total_items": total_items,
                    "avg_items_per_group": (
                        round(total_items / len(calc_groups), 1) if calc_groups else 0
                    ),
                    "patterns": {
                        "time_intelligence_count": len(time_intel_groups),
                        "currency_conversion_count": len(currency_groups),
                        "scenario_analysis_count": len(scenario_groups),
                        "uses_advanced_patterns": len(calc_groups) > 0,
                    },
                    "group_details": group_details,
                    "implementation_quality": (
                        "Sophisticated"
                        if len(calc_groups) >= 3
                        else "Good" if len(calc_groups) >= 1 else "None"
                    ),
                }

            # 6. Security Analysis
            roles_op = ops.get("08_roles", {})
            if roles_op.get("success"):
                roles = roles_op.get("data", [])

                # Analyze RLS implementation
                roles_with_filters = [r for r in roles if r.get("tablePermissionCount", 0) > 0]
                total_table_permissions = sum(r.get("tablePermissionCount", 0) for r in roles)

                analysis["security_analysis"] = {
                    "total_roles": len(roles),
                    "roles_with_filters": len(roles_with_filters),
                    "total_table_permissions": total_table_permissions,
                    "avg_tables_per_role": (
                        round(total_table_permissions / len(roles), 1) if roles else 0
                    ),
                    "rls_implemented": len(roles_with_filters) > 0,
                    "security_complexity": (
                        "High"
                        if total_table_permissions > 20
                        else "Medium" if total_table_permissions > 5 else "Low"
                    ),
                    "role_details": [
                        {
                            "name": r.get("name", ""),
                            "filtered_tables": r.get("tablePermissionCount", 0),
                        }
                        for r in roles
                    ],
                }

            # 7. Performance Assessment
            rel_analysis = analysis.get("relationship_analysis", {})
            table_analysis = analysis.get("table_analysis", {})
            size_mb = analysis["model_overview"].get("estimated_size_mb", 0)

            performance_score = 100
            performance_issues = []

            # Relationship complexity impact
            m2m_pct = rel_analysis.get("cardinality_breakdown", {}).get(
                "many_to_many_percentage", 0
            )
            if m2m_pct > 25:
                performance_score -= 30
                performance_issues.append("Critical: Excessive M:M relationships")
            elif m2m_pct > 15:
                performance_score -= 20
                performance_issues.append("High: Many M:M relationships")
            elif m2m_pct > 5:
                performance_score -= 10
                performance_issues.append("Medium: Some M:M relationships")

            # Bidirectional relationships impact
            bidir_pct = rel_analysis.get("cardinality_breakdown", {}).get(
                "bidirectional_percentage", 0
            )
            if bidir_pct > 10:
                performance_score -= 15
                performance_issues.append("Excessive bidirectional relationships")

            # Table complexity impact (F841: table_analysis used to be computed and ignored)
            if table_analysis.get("complexity_metrics", {}).get("complexity_score") == "High":
                performance_score -= 10
                performance_issues.append("Very wide tables (avg > 50 columns)")

            # Model size impact
            if size_mb > 2000:
                performance_score -= 20
                performance_issues.append("Very large model size")
            elif size_mb > 1000:
                performance_score -= 10
                performance_issues.append("Large model size")

            performance_score = max(0, performance_score)

            analysis["performance_assessment"] = {
                "performance_score": performance_score,
                "performance_grade": (
                    "A"
                    if performance_score >= 90
                    else (
                        "B"
                        if performance_score >= 75
                        else (
                            "C"
                            if performance_score >= 60
                            else "D" if performance_score >= 40 else "F"
                        )
                    )
                ),
                "performance_issues": performance_issues,
                "estimated_query_impact": (
                    "Low"
                    if performance_score >= 85
                    else (
                        "Medium"
                        if performance_score >= 70
                        else "High" if performance_score >= 50 else "Critical"
                    )
                ),
            }

            # 8. COMPREHENSIVE Recommendations
            recommendations = []

            # Critical M:M relationship issue
            m2m_count = rel_analysis.get("cardinality_breakdown", {}).get("many_to_many", 0)
            if m2m_count > 20:
                m2m_examples = rel_analysis.get("problem_relationships", {}).get(
                    "many_to_many_examples", []
                )[:5]
                examples_text = ", ".join([f"{r['from']} ↔ {r['to']}" for r in m2m_examples])
                recommendations.append(
                    {
                        "type": "architecture",
                        "priority": "critical",
                        "category": "Relationship Optimization",
                        "title": f"Critical: {m2m_count} Many-to-Many Relationships ({m2m_pct:.1f}%)",
                        "impact": "Severe performance degradation, complex filter propagation, ambiguous relationships",
                        "examples": examples_text,
                        "solution": "Create bridge tables for dimensional relationships. Replace M:M with two M:1 relationships through bridge table.",
                        "expected_benefit": "30-50% performance improvement for filtered queries",
                        "action_items": [
                            "Identify root cause: Are these field parameters or true M:M dimensions?",
                            "For field parameters: Consider consolidating or using dynamic measures instead",
                            "For dimensions: Design bridge tables with grain matching both sides",
                            "Test filter propagation after implementation",
                        ],
                    }
                )
            elif m2m_count > 10:
                recommendations.append(
                    {
                        "type": "architecture",
                        "priority": "high",
                        "category": "Relationship Optimization",
                        "title": f"High: {m2m_count} Many-to-Many Relationships",
                        "impact": "Moderate performance impact, filter complexity",
                        "solution": "Review and refactor critical M:M relationships to use bridge tables",
                        "expected_benefit": "15-25% performance improvement",
                    }
                )

            # Bidirectional relationships warning
            bidir_count = rel_analysis.get("cardinality_breakdown", {}).get(
                "bidirectional_count", 0
            )
            if bidir_count > 5:
                bidir_examples = rel_analysis.get("problem_relationships", {}).get(
                    "bidirectional_examples", []
                )[:5]
                examples_text = ", ".join(
                    [f"{r['from']} ↔ {r['to']} ({r['cardinality']})" for r in bidir_examples]
                )
                recommendations.append(
                    {
                        "type": "architecture",
                        "priority": "high",
                        "category": "Filter Propagation",
                        "title": f"Warning: {bidir_count} Bidirectional Relationships",
                        "impact": "Ambiguous filter paths, potential circular dependencies, performance overhead",
                        "examples": examples_text,
                        "solution": "Review necessity of bidirectional filters. Consider CROSSFILTER in measures instead.",
                        "action_items": [
                            "Document business reason for each bidirectional filter",
                            "Test if CROSSFILTER() in DAX can replace bidirectional relationships",
                            "Review for potential ambiguity with DAX Studio relationship analyzer",
                        ],
                    }
                )

            # Field parameter explosion
            field_param_count = analysis.get("architecture_patterns", {}).get(
                "field_parameter_count", 0
            )
            if field_param_count > 20:
                field_params = analysis.get("architecture_patterns", {}).get(
                    "field_parameter_tables", []
                )[:10]
                recommendations.append(
                    {
                        "type": "architecture",
                        "priority": "high",
                        "category": "Model Complexity",
                        "title": f"Field Parameter Explosion: {field_param_count} Parameter Tables",
                        "impact": "Model complexity, M:M relationship proliferation, maintenance overhead",
                        "examples": ", ".join(field_params),
                        "solution": "Consolidate related field parameters. Consider calculation groups or dynamic measures.",
                        "action_items": [
                            "Group related parameters (currency, time intelligence, scenarios)",
                            "Evaluate if calculation groups can replace some parameters",
                            "Consider dynamic measure approach for simpler scenarios",
                            "Document field parameter strategy for team",
                        ],
                    }
                )

            # Large model size warning
            if size_mb > 1000:
                recommendations.append(
                    {
                        "type": "performance",
                        "priority": "high",
                        "category": "Data Volume",
                        "title": f"Large Model: {size_mb:.1f} MB",
                        "impact": "Slow refresh, high memory consumption, slower query performance",
                        "solution": "Implement incremental refresh, optimize column cardinality, review unnecessary columns",
                        "expected_benefit": "40-60% size reduction, faster refresh and queries",
                        "action_items": [
                            "Enable incremental refresh for fact tables",
                            "Review column cardinality - remove high-cardinality text columns",
                            "Consider summarization/aggregation for old data",
                            "Audit and remove unused columns",
                        ],
                    }
                )

            # Positive recognitions
            folder_usage = (
                analysis.get("measure_analysis", {})
                .get("organization", {})
                .get("folder_usage_percentage", 0)
            )
            if folder_usage > 90:
                recommendations.append(
                    {
                        "type": "best_practice",
                        "priority": "info",
                        "category": "Organization",
                        "title": f"Excellent Measure Organization: {folder_usage:.1f}% using display folders",
                        "impact": "Improved user experience, easier maintenance, professional appearance",
                    }
                )

            calc_group_count = analysis.get("calculation_group_analysis", {}).get("total_groups", 0)
            if calc_group_count >= 3:
                group_names = ", ".join(
                    [
                        g["name"]
                        for g in analysis.get("calculation_group_analysis", {}).get(
                            "group_details", []
                        )[:5]
                    ]
                )
                recommendations.append(
                    {
                        "type": "best_practice",
                        "priority": "info",
                        "category": "Advanced DAX",
                        "title": f"Sophisticated Calculation Group Implementation: {calc_group_count} groups",
                        "impact": "Reduced measure count, consistent time intelligence, professional implementation",
                        "examples": group_names,
                    }
                )

            if analysis.get("architecture_patterns", {}).get("uses_star_schema"):
                fact_count = analysis.get("architecture_patterns", {}).get("fact_tables_count", 0)
                dim_count = analysis.get("architecture_patterns", {}).get(
                    "dimension_tables_count", 0
                )
                recommendations.append(
                    {
                        "type": "architecture",
                        "priority": "info",
                        "category": "Schema Design",
                        "title": f"Star Schema Pattern: {fact_count} fact tables, {dim_count} dimensions",
                        "impact": "Optimal for Power BI, excellent performance foundation",
                    }
                )

            # Security implementation
            rls_impl = analysis.get("security_analysis", {}).get("rls_implemented", False)
            if rls_impl:
                role_count = analysis.get("security_analysis", {}).get("total_roles", 0)
                recommendations.append(
                    {
                        "type": "security",
                        "priority": "info",
                        "category": "Data Security",
                        "title": f"Row-Level Security Implemented: {role_count} roles",
                        "impact": "Data access control enforced, compliance-ready",
                    }
                )

            analysis["recommendations"] = recommendations

            return {
                "success": True,
                "analysis": analysis,
                "summary": self._generate_summary_text(analysis),
            }

        except Exception as e:
            logger.error(f"Expert analysis generation failed: {e}")
            return {"success": False, "error": f"Expert analysis failed: {str(e)}"}

    def _generate_summary_text(self, analysis: Dict[str, Any]) -> str:
        """
        Generate comprehensive, screenshot-style Power BI expert analysis summary.
        Enhanced with detailed insights and professional formatting.
        """
        overview = analysis.get("model_overview", {})
        insights = analysis.get("data_model_insights", {})
        arch = analysis.get("architecture_patterns", {})
        recommendations = analysis.get("recommendations", [])

        summary_parts = []

        # Header
        summary_parts.append("=" * 80)
        summary_parts.append("POWER BI MODEL ANALYSIS - EXPERT SUMMARY")
        summary_parts.append("=" * 80)
        summary_parts.append("")

        # 1. MODEL OVERVIEW
        summary_parts.append("📊 MODEL OVERVIEW")
        summary_parts.append("-" * 80)
        summary_parts.append(f"Database ID:          {overview.get('database_id', 'N/A')}")
        summary_parts.append(
            f"Compatibility Level:  {overview.get('compatibility_level', 0)} ({overview.get('model_format', 'Unknown')})"
        )

        size_mb = overview.get("estimated_size_mb", 0)
        if size_mb > 0:
            size_category = (
                "Large (>1GB)"
                if size_mb > 1024
                else "Medium (100MB-1GB)" if size_mb > 100 else "Small (<100MB)"
            )
            summary_parts.append(f"Model Size:           {size_mb:.2f} MB ({size_category})")

        summary_parts.append("")
        summary_parts.append(f"📋 Tables:            {overview.get('total_tables', 0)}")
        summary_parts.append(f"📐 Columns:           {overview.get('total_columns', 0)}")
        summary_parts.append(f"📏 Measures:          {overview.get('total_measures', 0)}")
        summary_parts.append(f"🔗 Relationships:     {overview.get('total_relationships', 0)}")
        summary_parts.append(f"⚡ Calc Groups:       {overview.get('calculation_groups', 0)}")
        summary_parts.append(f"🔒 Security Roles:    {overview.get('security_roles', 0)}")
        summary_parts.append("")

        # 2. ARCHITECTURE PATTERNS
        summary_parts.append("🏗️  ARCHITECTURE PATTERNS")
        summary_parts.append("-" * 80)

        if arch.get("uses_star_schema"):
            summary_parts.append("✅ Star Schema Pattern Detected")
            summary_parts.append(f"   • Fact Tables:      {arch.get('fact_tables_count', 0)}")
            summary_parts.append(f"   • Dimension Tables: {arch.get('dimension_tables_count', 0)}")
        else:
            summary_parts.append("⚠️  No clear star schema pattern detected")

        if arch.get("has_dedicated_measure_tables"):
            measure_tables = arch.get("measure_tables", [])
            summary_parts.append(f"✅ Dedicated Measure Tables: {', '.join(measure_tables[:3])}")
        else:
            summary_parts.append("ℹ️  No dedicated measure tables (measures likely in fact tables)")

        summary_parts.append("")

        # 3. RELATIONSHIPS ANALYSIS
        rel_pattern = insights.get("relationship_patterns", {})
        if rel_pattern:
            summary_parts.append("🔗 RELATIONSHIP ANALYSIS")
            summary_parts.append("-" * 80)

            complexity = rel_pattern.get("complexity_level", "Unknown")
            complexity_icon = (
                "🔴" if complexity == "Complex" else "🟡" if complexity == "Standard" else "🟢"
            )
            summary_parts.append(f"{complexity_icon} Complexity Level: {complexity}")
            summary_parts.append("")

            total_rels = rel_pattern.get("total_relationships", 0)
            m2m = rel_pattern.get("many_to_many_count", 0)
            bidir = rel_pattern.get("bidirectional_count", 0)
            inactive = rel_pattern.get("inactive_count", 0)

            # Cardinality breakdown
            summary_parts.append(f"Total Relationships: {total_rels}")
            if m2m > 0:
                summary_parts.append(f"  ⚠️  Many-to-Many:     {m2m} (requires monitoring)")
            if bidir > 0:
                summary_parts.append(
                    f"  ⚠️  Bi-directional:   {bidir} (potential performance impact)"
                )
            if inactive > 0:
                summary_parts.append(
                    f"  ℹ️  Inactive:         {inactive} (used with USERELATIONSHIP)"
                )

            # Add health score
            standard_rels = total_rels - m2m - bidir
            health_pct = (standard_rels / total_rels * 100) if total_rels > 0 else 100
            health_icon = "✅" if health_pct >= 80 else "⚠️" if health_pct >= 60 else "🔴"
            summary_parts.append("")
            summary_parts.append(
                f"{health_icon} Relationship Health: {health_pct:.0f}% standard patterns"
            )
            summary_parts.append("")

        # 4. CALCULATION GROUPS
        calc_groups = insights.get("calculation_groups", {})
        if calc_groups.get("count", 0) > 0:
            summary_parts.append("⚡ CALCULATION GROUPS")
            summary_parts.append("-" * 80)
            summary_parts.append("✅ Using Calculation Groups - Advanced DAX Pattern")
            summary_parts.append(f"   • Groups:           {calc_groups['count']}")
            summary_parts.append(
                f"   • Total Items:      {calc_groups.get('total_calculation_items', 0)}"
            )

            group_names = calc_groups.get("group_names", [])
            if group_names:
                summary_parts.append(f"   • Groups:           {', '.join(group_names[:3])}")
                if len(group_names) > 3:
                    summary_parts.append(
                        f"                       ... and {len(group_names) - 3} more"
                    )
            summary_parts.append("")

        # 5. MEASURE ORGANIZATION
        measure_org = insights.get("measure_organization", {})
        if measure_org:
            summary_parts.append("📏 MEASURE ORGANIZATION")
            summary_parts.append("-" * 80)

            if measure_org.get("uses_display_folders"):
                summary_parts.append("✅ Measures organized with Display Folders")
                folders = measure_org.get("top_level_folders", [])
                if folders:
                    summary_parts.append(f"   • Top Folders: {', '.join(folders[:5])}")
            else:
                summary_parts.append("ℹ️  Measures not organized in display folders")

            summary_parts.append(
                f"   • Total Measures: {measure_org.get('total_measures_analyzed', 0)}"
            )
            summary_parts.append("")

        # 6. RECOMMENDATIONS
        if recommendations:
            summary_parts.append("💡 RECOMMENDATIONS")
            summary_parts.append("-" * 80)

            # Group by priority
            high_priority = [r for r in recommendations if r.get("priority") == "high"]
            medium_priority = [r for r in recommendations if r.get("priority") == "medium"]
            info_priority = [r for r in recommendations if r.get("priority") == "info"]

            if high_priority:
                summary_parts.append("🔴 HIGH PRIORITY:")
                for rec in high_priority:
                    summary_parts.append(f"   • {rec.get('message', '')}")
                summary_parts.append("")

            if medium_priority:
                summary_parts.append("🟡 MEDIUM PRIORITY:")
                for rec in medium_priority:
                    summary_parts.append(f"   • {rec.get('message', '')}")
                summary_parts.append("")

            if info_priority:
                summary_parts.append("✅ BEST PRACTICES DETECTED:")
                for rec in info_priority:
                    summary_parts.append(f"   • {rec.get('message', '')}")
                summary_parts.append("")

        # Footer
        summary_parts.append("=" * 80)
        summary_parts.append("Analysis completed successfully")
        summary_parts.append("=" * 80)

        return "\n".join(summary_parts)
