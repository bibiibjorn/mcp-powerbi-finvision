"""
Analysis Handler
Handles model analysis tools including simple analysis, full analysis, BPA, performance, and validation
"""

from typing import Dict, Any
import logging
from server.registry import ToolDefinition
from server.progress import emit_progress
from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from core.utilities.business_impact import enrich_issue_with_impact, add_impact_summary
from core.orchestration.analysis_orchestrator import is_time_intelligence_name
from core.validation.error_response import error_envelope
from core.analysis.bpa_analyzer import superseded_rule_ids

logger = logging.getLogger(__name__)


def _extract_operation_summary(op_id: str, op_result: Dict[str, Any]) -> str:
    """
    Extract a concise success message from operation result.
    Optimized helper to avoid repetitive if-elif chains.
    """
    if op_id == "01_database":
        db_data = op_result.get("data", [{}])[0]
        return f"[OK] SUCCESS: {db_data.get('name', 'N/A')} (Compatibility: {db_data.get('compatibilityLevel', 0)})"
    elif op_id == "02_stats":
        counts = op_result.get("counts", {})
        return f"[OK] SUCCESS: {counts.get('tables', 0)} tables, {counts.get('measures', 0)} measures, {counts.get('columns', 0)} columns"
    elif op_id == "03_tables":
        return f"[OK] SUCCESS: Found {op_result.get('table_count', 0)} tables"
    elif op_id == "04_measures":
        return f"[OK] SUCCESS: Retrieved {len(op_result.get('data', []))} measures"
    elif op_id == "05_columns":
        data = op_result.get("data", [])
        col_count = sum(len(t.get("columns", [])) for t in data)
        return f"[OK] SUCCESS: Retrieved {col_count} columns across {len(data)} tables"
    elif op_id == "06_relationships":
        return f"[OK] SUCCESS: Found {len(op_result.get('data', []))} relationships"
    elif op_id == "07_calculation_groups":
        data = op_result.get("data", [])
        total_items = sum(len(cg.get("calculationItems", [])) for cg in data)
        return f"[OK] SUCCESS: Found {len(data)} calculation groups with {total_items} items"
    elif op_id == "08_roles":
        return f"[OK] SUCCESS: Found {len(op_result.get('data', []))} security roles"
    else:
        return "[OK] SUCCESS: Operation completed"


def _format_analysis_to_log(op_analysis: Dict[str, Any], op_name: str, op_time: float) -> list:
    """
    Format operation analysis into execution log lines.
    Optimized to consolidate formatting logic.
    """
    log_lines = ["", f"   === ANALYSIS SUMMARY FOR {op_name.upper()} ==="]

    if op_analysis.get("key_findings"):
        log_lines.append("   Key Findings:")
        log_lines.extend([f"     * {finding}" for finding in op_analysis["key_findings"]])

    if op_analysis.get("insights"):
        log_lines.append("   Insights:")
        log_lines.extend([f"     - {insight}" for insight in op_analysis["insights"]])

    if op_analysis.get("recommendations"):
        log_lines.append("   Recommendations:")
        log_lines.extend([f"     ! {rec}" for rec in op_analysis["recommendations"]])

    log_lines.append(f"   [TIME] Execution time: {op_time}s")
    log_lines.append("   " + "=" * 70)

    return log_lines


def _generate_operation_analysis(op_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate AI-optimized analysis summary for each operation.
    Provides insights, patterns, and recommendations specific to each operation.

    Audit T124 (deferred): the 250+ line if/elif chain below should be
    refactored into a dispatch dict op_id -> analyzer_fn so each branch
    can be unit-tested independently and emoji-heavy text moved out of
    the hot path. Tracked separately; high regression risk to refactor
    in this audit-cleanup pass.
    """
    if not result.get("success"):
        return {"status": "failed", "insights": [], "recommendations": []}

    analysis = {"status": "success", "insights": [], "recommendations": [], "key_findings": []}

    try:
        # Database operation analysis
        if op_id == "01_database":
            data = result.get("data", [{}])[0]
            compat = data.get("compatibilityLevel", 0)
            size_mb = (
                round(data.get("estimatedSize", 0) / (1024 * 1024), 2)
                if data.get("estimatedSize")
                else 0
            )
            db_name = data.get("name", "Unknown")

            analysis["key_findings"].append(f"Database: {db_name}")
            analysis["key_findings"].append(f"Compatibility Level: {compat}")

            # Detailed compatibility level interpretation
            if compat >= 1604:
                analysis["insights"].append(f"✅ Latest Power BI compatibility level ({compat})")
                analysis["insights"].append(
                    "   Supports: Calculation groups, enhanced refresh, DirectQuery improvements"
                )
            elif compat >= 1600:
                analysis["insights"].append(f"✅ Power BI Desktop format ({compat})")
                analysis["insights"].append(
                    "   Supports: Calculation groups, object-level security"
                )
            elif compat >= 1500:
                analysis["insights"].append(f"ℹ️ Azure Analysis Services format ({compat})")
                analysis["insights"].append("   Consider upgrading to 1600+ for Power BI features")
            elif compat >= 1400:
                analysis["insights"].append(f"⚠️ SQL Server 2017 format ({compat})")
                analysis["recommendations"].append(
                    "Upgrade compatibility level to access modern Power BI features"
                )
            else:
                analysis["insights"].append(f"⚠️ Legacy format ({compat})")
                analysis["recommendations"].append(
                    "IMPORTANT: Upgrade compatibility level for better performance and features"
                )

            # Size analysis with detailed recommendations
            if size_mb > 0:
                analysis["key_findings"].append(f"Model size: {size_mb} MB")

                if size_mb > 2000:
                    size_category = "Very Large (>2GB)"
                    analysis["insights"].append(f"🔴 {size_category} - Performance critical")
                    analysis["recommendations"].append(
                        "CRITICAL: Implement incremental refresh, aggregations, and column optimization"
                    )
                    analysis["recommendations"].append(
                        "Consider splitting into multiple models or using composite models"
                    )
                elif size_mb > 1000:
                    size_category = "Large (1-2GB)"
                    analysis["insights"].append(f"🟡 {size_category} - Monitor performance")
                    analysis["recommendations"].append(
                        "Implement incremental refresh for fact tables"
                    )
                    analysis["recommendations"].append(
                        "Review and remove unused columns and high-cardinality text fields"
                    )
                elif size_mb > 500:
                    size_category = "Medium (500MB-1GB)"
                    analysis["insights"].append(f"🟢 {size_category} - Good range")
                    analysis["recommendations"].append(
                        "Monitor refresh times and consider optimization strategies"
                    )
                elif size_mb > 100:
                    size_category = "Small-Medium (100-500MB)"
                    analysis["insights"].append(f"✅ {size_category} - Optimal size")
                else:
                    size_category = "Small (<100MB)"
                    analysis["insights"].append(
                        f"✅ {size_category} - Excellent performance expected"
                    )

        # Stats operation analysis
        elif op_id == "02_stats":
            counts = result.get("counts", {})
            tables = counts.get("tables", 0)
            measures = counts.get("measures", 0)
            columns = counts.get("columns", 0)
            rels = counts.get("relationships", 0)
            calc_groups = counts.get("calculation_groups", 0)
            roles = counts.get("roles", 0)
            partitions = counts.get("partitions", 0)

            analysis["key_findings"] = [
                f"📋 {tables} tables",
                f"📐 {columns} columns",
                f"📏 {measures} measures",
                f"🔗 {rels} relationships",
            ]

            if calc_groups > 0:
                analysis["key_findings"].append(f"⚡ {calc_groups} calculation groups")
            if roles > 0:
                analysis["key_findings"].append(f"🔒 {roles} security roles")

            # Complexity assessment
            complexity_score = 0
            if tables > 100:
                complexity_score += 2
                analysis["insights"].append(
                    "⚠️ Large model with many tables - ensure proper naming conventions"
                )
            elif tables > 50:
                complexity_score += 1
                analysis["insights"].append(
                    "ℹ️ Moderate table count - good organization is important"
                )
            else:
                analysis["insights"].append("✅ Manageable table count")

            if measures > 500:
                complexity_score += 3
                analysis["insights"].append(
                    "⚠️ Very high measure count - display folders are critical"
                )
                analysis["recommendations"].append(
                    "IMPORTANT: Organize measures with hierarchical display folders"
                )
            elif measures > 200:
                complexity_score += 2
                analysis["insights"].append(
                    "⚠️ High measure count - use display folders for organization"
                )
                analysis["recommendations"].append(
                    "Ensure measures are organized in logical display folder hierarchies"
                )
            elif measures > 100:
                complexity_score += 1
                analysis["insights"].append(
                    "ℹ️ Moderate measure count - maintain good organization"
                )

            if calc_groups > 0:
                analysis["insights"].append(
                    f"✅ Using {calc_groups} calculation groups - advanced DAX pattern"
                )
                analysis["insights"].append(
                    "   This indicates sophisticated time intelligence or scenario analysis"
                )

            # Ratio analysis with interpretation
            if tables > 0:
                cols_per_table = round(columns / tables, 1)
                measures_per_table = round(measures / tables, 1)
                rels_per_table = round(rels / tables, 1)

                analysis["insights"].append("Model ratios:")
                analysis["insights"].append(f"   • Avg {cols_per_table} columns per table")
                analysis["insights"].append(f"   • Avg {measures_per_table} measures per table")
                analysis["insights"].append(f"   • Avg {rels_per_table} relationships per table")

                # Interpret ratios
                if cols_per_table > 50:
                    analysis["recommendations"].append(
                        "High column-to-table ratio - review for denormalization or unused columns"
                    )
                elif cols_per_table < 5:
                    analysis["insights"].append(
                        "   ℹ️ Low column count per table - may indicate highly normalized structure"
                    )

                if measures_per_table > 30:
                    analysis["insights"].append(
                        "   ℹ️ Many measures per table - likely using dedicated measure tables (good!)"
                    )

            # Overall complexity assessment
            if complexity_score >= 5:
                analysis["insights"].append(
                    "🔴 Model Complexity: HIGH - requires strong governance and organization"
                )
            elif complexity_score >= 3:
                analysis["insights"].append(
                    "🟡 Model Complexity: MEDIUM - good practices important"
                )
            else:
                analysis["insights"].append("🟢 Model Complexity: LOW - easy to maintain")

            # Security and partitioning insights
            if roles > 0:
                analysis["insights"].append(f"✅ Security: {roles} RLS roles implemented")
            else:
                analysis["insights"].append("ℹ️ No RLS roles - data is accessible to all users")

            if partitions > tables:
                analysis["insights"].append(
                    f"ℹ️ {partitions} partitions across {tables} tables - likely using incremental refresh"
                )

        # Tables operation analysis
        elif op_id == "03_tables":
            data = result.get("data", [])
            table_count = len(data)

            def _n(t: Dict[str, Any], key: str) -> int:
                # I-15: list_tables_simple emits snake_case counts.
                camel = key.split("_")[0] + "".join(p.title() for p in key.split("_")[1:])
                return int(t.get(key, t.get(camel, 0)) or 0)

            measure_tables = [
                t for t in data if _n(t, "measure_count") > 10 and _n(t, "column_count") < 5
            ]
            large_tables = [t for t in data if _n(t, "column_count") > 50]
            hidden_tables = [t for t in data if t.get("is_hidden", t.get("isHidden", False))]

            fact_tables = [t for t in data if "fact" in t.get("name", "").lower()]
            dim_tables = [
                t
                for t in data
                if any(prefix in t.get("name", "").lower() for prefix in ["dim", "dimension"])
            ]

            total_columns = sum(_n(t, "column_count") for t in data)
            total_measures = sum(_n(t, "measure_count") for t in data)
            avg_cols_per_table = round(total_columns / table_count, 1) if table_count > 0 else 0

            analysis["key_findings"].append(f"{table_count} tables in model")
            analysis["key_findings"].append(
                f"{total_columns} total columns (avg {avg_cols_per_table} per table)"
            )
            analysis["key_findings"].append(
                f"{total_measures} total measures distributed across tables"
            )

            if measure_tables:
                measure_table_names = [t.get("name", "") for t in measure_tables[:3]]
                analysis["insights"].append(
                    f"✅ {len(measure_tables)} dedicated measure tables (best practice)"
                )
                analysis["insights"].append(f"   Examples: {', '.join(measure_table_names)}")

            if fact_tables:
                analysis["insights"].append(
                    f"Found {len(fact_tables)} fact tables (likely transactional data)"
                )
            if dim_tables:
                analysis["insights"].append(
                    f"Found {len(dim_tables)} dimension tables (lookup/reference data)"
                )

            if large_tables:
                large_table_details = [
                    f"{t.get('name', '')} ({_n(t, 'column_count')} cols)" for t in large_tables[:3]
                ]
                analysis["insights"].append(f"⚠️ {len(large_tables)} tables with >50 columns")
                analysis["insights"].append(f"   Examples: {', '.join(large_table_details)}")
                analysis["recommendations"].append(
                    "Review large tables for potential normalization or column hiding"
                )

            if hidden_tables:
                analysis["insights"].append(
                    f"ℹ️ {len(hidden_tables)} hidden tables (good for backend calculations)"
                )

        # Measures operation analysis
        elif op_id == "04_measures":
            data = result.get("data", [])
            measure_count = len(data)

            # Analyze display folders
            with_folders = [m for m in data if m.get("displayFolder")]
            folder_usage = (
                round(len(with_folders) / measure_count * 100, 1) if measure_count > 0 else 0
            )

            # Analyze folder structure
            folders = set()
            top_level_folders = set()
            for m in data:
                folder = m.get("displayFolder", "")
                if folder:
                    folders.add(folder)
                    top_level_folders.add(folder.split("\\")[0])

            # Detect naming patterns and measure types
            # I-25: word-bounded TI tokens ("Supply", "Monthly" are not TI).
            time_intel_measures = [m for m in data if is_time_intelligence_name(m.get("name", ""))]
            calc_measures = [
                m
                for m in data
                if any(x in m.get("name", "").lower() for x in ["calc", "calculated"])
            ]
            base_measures = [m for m in data if "base" in m.get("name", "").lower()]
            kpi_measures = [m for m in data if "kpi" in m.get("name", "").lower()]

            # Group measures by table
            measures_by_table = {}
            for m in data:
                table = m.get("table", "Unknown")
                measures_by_table[table] = measures_by_table.get(table, 0) + 1

            top_tables = sorted(measures_by_table.items(), key=lambda x: x[1], reverse=True)[:5]

            analysis["key_findings"].append(f"{measure_count} measures analyzed")
            analysis["key_findings"].append(f"{len(folders)} unique display folders used")
            analysis["key_findings"].append(f"{len(top_level_folders)} top-level folder categories")

            # Organization assessment
            if folder_usage > 80:
                analysis["insights"].append(
                    f"✅ Excellent organization: {folder_usage}% measures use display folders"
                )
            elif folder_usage > 50:
                analysis["insights"].append(
                    f"✔️ Good organization: {folder_usage}% measures use display folders"
                )
                analysis["recommendations"].append(
                    f"Improve organization: {100-folder_usage}% of measures lack display folders"
                )
            else:
                analysis["insights"].append(
                    f"⚠️ Poor organization: Only {folder_usage}% measures use display folders"
                )
                analysis["recommendations"].append(
                    "IMPORTANT: Organize measures with display folders for better user experience"
                )

            # Pattern analysis
            if time_intel_measures:
                examples = [m.get("name", "") for m in time_intel_measures[:3]]
                analysis["insights"].append(
                    f"Found {len(time_intel_measures)} time intelligence measures"
                )
                analysis["insights"].append(f"   Examples: {', '.join(examples)}")

            if calc_measures or base_measures:
                total_calc = len(calc_measures) + len(base_measures)
                analysis["insights"].append(
                    f"Found {total_calc} calculated/base measures (good DAX layering pattern)"
                )

            if kpi_measures:
                analysis["insights"].append(f"Found {len(kpi_measures)} KPI measures")

            # Table distribution
            analysis["insights"].append(
                f"Measures distributed across {len(measures_by_table)} tables"
            )
            if top_tables:
                top_table_info = [f"{table} ({count})" for table, count in top_tables[:3]]
                analysis["insights"].append(f"   Top tables: {', '.join(top_table_info)}")

            # Recommendations
            if len(measures_by_table) > 10 and not any(
                "measure" in table.lower() for table in measures_by_table.keys()
            ):
                analysis["recommendations"].append(
                    "Consider consolidating measures into dedicated measure tables"
                )

        # Columns operation analysis
        elif op_id == "05_columns":
            data = result.get("data", [])
            total_columns = sum(len(t.get("columns", [])) for t in data)
            table_count = len(data)

            # Extract all columns
            all_columns = [col for table in data for col in table.get("columns", [])]

            # Data type analysis
            data_types = {}
            for col in all_columns:
                dt = col.get("dataType", "Unknown")
                data_types[dt] = data_types.get(dt, 0) + 1

            # Identify calculated columns vs regular columns
            calculated_cols = [
                col for col in all_columns if str(col.get("type", "")).lower() == "calculated"
            ]

            # Identify hidden columns
            hidden_cols = [col for col in all_columns if col.get("isHidden", False)]

            # Find key columns (common patterns)
            key_cols = [
                col
                for col in all_columns
                if any(x in col.get("name", "").lower() for x in ["key", "id", "sk", "pk"])
            ]
            date_cols = [col for col in all_columns if col.get("dataType") == "DateTime"]

            analysis["key_findings"].append(f"{total_columns} columns across {table_count} tables")
            if calculated_cols:
                calc_pct = round(len(calculated_cols) / total_columns * 100, 1)
                analysis["key_findings"].append(
                    f"{len(calculated_cols)} calculated columns ({calc_pct}%)"
                )

            # Data type breakdown
            if data_types:
                data_type_summary = []
                sorted_types = sorted(data_types.items(), key=lambda x: x[1], reverse=True)
                for dt, count in sorted_types[:5]:
                    pct = round(count / total_columns * 100, 1)
                    data_type_summary.append(f"{dt}: {count} ({pct}%)")

                analysis["insights"].append("Data type distribution:")
                for dt_info in data_type_summary:
                    analysis["insights"].append(f"   • {dt_info}")

            # Column patterns
            if hidden_cols:
                hidden_pct = round(len(hidden_cols) / total_columns * 100, 1)
                analysis["insights"].append(
                    f"✅ {len(hidden_cols)} hidden columns ({hidden_pct}%) - good for data cleanliness"
                )

            if key_cols:
                analysis["insights"].append(
                    f"Found {len(key_cols)} key/ID columns for relationships"
                )

            if date_cols:
                date_table_names = set([col.get("table", "") for col in date_cols])
                analysis["insights"].append(
                    f"Found {len(date_cols)} date/time columns across {len(date_table_names)} tables"
                )

            # Calculated column warnings
            if calculated_cols:
                if len(calculated_cols) > 50:
                    analysis["recommendations"].append(
                        "⚠️ HIGH: Many calculated columns detected - consider moving logic to source or using measures"
                    )
                elif len(calculated_cols) > 20:
                    analysis["recommendations"].append(
                        "Review calculated columns - some may be better as measures for performance"
                    )
                else:
                    analysis["insights"].append(
                        f"ℹ️ {len(calculated_cols)} calculated columns (reasonable amount)"
                    )

        # Relationships operation analysis - ENHANCED with detailed cardinality patterns
        elif op_id == "06_relationships":
            data = result.get("data", [])
            total_rels = len(data)

            # Analyze ALL cardinality patterns
            one_to_one = [
                r
                for r in data
                if r.get("fromCardinality") == "One" and r.get("toCardinality") == "One"
            ]
            one_to_many = [
                r
                for r in data
                if r.get("fromCardinality") == "One" and r.get("toCardinality") == "Many"
            ]
            many_to_one = [
                r
                for r in data
                if r.get("fromCardinality") == "Many" and r.get("toCardinality") == "One"
            ]
            many_to_many = [
                r
                for r in data
                if r.get("fromCardinality") == "Many" and r.get("toCardinality") == "Many"
            ]

            bidirectional = [r for r in data if r.get("crossFilteringBehavior") == "BothDirections"]
            inactive = [r for r in data if not r.get("isActive")]

            # Key findings with cardinality breakdown
            analysis["key_findings"].append(f"{total_rels} relationships total")

            cardinality_breakdown = []
            if many_to_one:
                cardinality_breakdown.append(f"{len(many_to_one)} Many:One (standard)")
            if one_to_many:
                cardinality_breakdown.append(f"{len(one_to_many)} One:Many")
            if many_to_many:
                cardinality_breakdown.append(f"{len(many_to_many)} Many:Many")
            if one_to_one:
                cardinality_breakdown.append(f"{len(one_to_one)} One:One")

            if cardinality_breakdown:
                analysis["key_findings"].append("Cardinality: " + ", ".join(cardinality_breakdown))

            # PROMINENT warnings for problematic patterns
            if many_to_many:
                analysis["insights"].append(
                    f"⚠️ {len(many_to_many)} MANY-TO-MANY relationships detected"
                )

                # Show examples of M:M relationships
                m2m_examples = [
                    f"{r.get('fromTable')} ↔ {r.get('toTable')}" for r in many_to_many[:3]
                ]
                if m2m_examples:
                    analysis["insights"].append(f"   Examples: {', '.join(m2m_examples)}")

                if len(many_to_many) > 10:
                    analysis["recommendations"].append(
                        "⚠️ HIGH: Consider bridge tables for M:M relationships to improve performance"
                    )
                elif len(many_to_many) > 5:
                    analysis["recommendations"].append(
                        "MEDIUM: Review M:M relationships for potential optimization"
                    )
                else:
                    analysis["recommendations"].append("LOW: Monitor M:M relationship performance")

            if bidirectional:
                analysis["insights"].append(
                    f"⚠️ {len(bidirectional)} BI-DIRECTIONAL relationships found"
                )

                # Show examples of bidirectional relationships
                bidir_examples = [
                    f"{r.get('fromTable')} ↔ {r.get('toTable')}" for r in bidirectional[:3]
                ]
                if bidir_examples:
                    analysis["insights"].append(f"   Examples: {', '.join(bidir_examples)}")

                analysis["recommendations"].append(
                    "⚠️ Review bidirectional filters - can cause ambiguity and performance issues"
                )

            if inactive:
                analysis["insights"].append(
                    f"ℹ️ {len(inactive)} INACTIVE relationships (likely used with USERELATIONSHIP)"
                )

                # Show examples of inactive relationships
                inactive_examples = [
                    f"{r.get('fromTable')} → {r.get('toTable')}" for r in inactive[:3]
                ]
                if inactive_examples:
                    analysis["insights"].append(f"   Examples: {', '.join(inactive_examples)}")

            # Positive insights
            if not many_to_many and not bidirectional:
                analysis["insights"].append(
                    "✅ Clean relationship model - no M:M or bidirectional relationships"
                )

            if many_to_one and len(many_to_one) == total_rels - len(inactive):
                analysis["insights"].append(
                    "✅ Standard star schema pattern - all active relationships are Many:One"
                )

        # Calculation groups analysis
        elif op_id == "07_calculation_groups":
            data = result.get("data", [])
            group_count = len(data)
            total_items = sum(len(cg.get("calculationItems", [])) for cg in data)

            if group_count > 0:
                analysis["key_findings"].append(
                    f"{group_count} calculation groups with {total_items} items total"
                )

                # Detailed group analysis
                group_details = []
                time_groups = []
                currency_groups = []
                scenario_groups = []

                for cg in data:
                    name = cg.get("name", "")
                    items = cg.get("calculationItems", [])
                    item_count = len(items)

                    group_details.append(f"{name} ({item_count} items)")

                    # Categorize by pattern
                    name_lower = name.lower()
                    if any(x in name_lower for x in ["time", "date", "period", "ytd", "mtd"]):
                        time_groups.append(name)
                    if any(x in name_lower for x in ["currency", "fx", "exchange"]):
                        currency_groups.append(name)
                    if any(x in name_lower for x in ["scenario", "variance", "comparison"]):
                        scenario_groups.append(name)

                analysis["insights"].append(
                    "✅ Advanced DAX implementation with calculation groups"
                )

                # Show group details
                if len(group_details) <= 5:
                    for detail in group_details:
                        analysis["insights"].append(f"   • {detail}")
                else:
                    for detail in group_details[:3]:
                        analysis["insights"].append(f"   • {detail}")
                    analysis["insights"].append(f"   • ... and {len(group_details) - 3} more")

                # Pattern analysis
                patterns_found = []
                if time_groups:
                    patterns_found.append(f"Time intelligence ({len(time_groups)})")
                if currency_groups:
                    patterns_found.append(f"Currency conversion ({len(currency_groups)})")
                if scenario_groups:
                    patterns_found.append(f"Scenario analysis ({len(scenario_groups)})")

                if patterns_found:
                    analysis["insights"].append(f"Patterns detected: {', '.join(patterns_found)}")

                # Recommendations
                if total_items < 10:
                    analysis["insights"].append(
                        "ℹ️ Simple calculation group setup - good for focused scenarios"
                    )
                elif total_items > 30:
                    analysis["insights"].append("✅ Comprehensive calculation group implementation")
                    analysis["recommendations"].append(
                        "Ensure calculation items are well-documented for team understanding"
                    )

            else:
                analysis["insights"].append("ℹ️ No calculation groups found")
                analysis["recommendations"].append(
                    "Consider calculation groups for time intelligence (YTD, MTD, PY) to reduce measure count"
                )

        # Roles analysis
        elif op_id == "08_roles":
            data = result.get("data", [])
            role_count = len(data)

            if role_count > 0:
                analysis["key_findings"].append(f"{role_count} security roles configured")

                # Analyze role details
                with_permissions = [r for r in data if r.get("tablePermissionCount", 0) > 0]
                read_only = [r for r in data if r.get("modelPermission", "") == "Read"]
                admin_roles = [r for r in data if r.get("modelPermission", "") == "Administrator"]

                total_permissions = sum(r.get("tablePermissionCount", 0) for r in data)

                analysis["insights"].append("✅ Row-level security (RLS) is implemented")

                # Role details
                role_names = [r.get("name", "") for r in data[:5]]
                if len(role_names) <= 5:
                    analysis["insights"].append(f"Roles: {', '.join(role_names)}")
                else:
                    analysis["insights"].append(
                        f"Roles: {', '.join(role_names[:3])}, ... and {role_count - 3} more"
                    )

                # Permission analysis
                if with_permissions:
                    avg_perms = round(total_permissions / len(with_permissions), 1)
                    analysis["insights"].append(
                        f"{len(with_permissions)} roles with table-level filters"
                    )
                    analysis["insights"].append(
                        f"   • Average {avg_perms} table permissions per role"
                    )
                    analysis["insights"].append(
                        f"   • {total_permissions} total table permissions across all roles"
                    )

                    # Security complexity assessment
                    if total_permissions > 30:
                        analysis["insights"].append(
                            "🟡 Complex RLS implementation - ensure thorough testing"
                        )
                        analysis["recommendations"].append(
                            "Document RLS logic and test with different user contexts"
                        )
                    elif total_permissions > 10:
                        analysis["insights"].append("ℹ️ Moderate RLS complexity")
                        analysis["recommendations"].append(
                            "Test RLS with representative user accounts"
                        )
                    else:
                        analysis["insights"].append(
                            "✅ Simple RLS implementation - easy to maintain"
                        )
                else:
                    analysis["insights"].append("⚠️ Roles exist but no table permissions defined")
                    analysis["recommendations"].append(
                        "Define table permissions for roles to enforce data security"
                    )

                # Permission types
                if read_only:
                    analysis["insights"].append(
                        f"ℹ️ {len(read_only)} read-only roles (standard pattern)"
                    )
                if admin_roles:
                    analysis["insights"].append(f"ℹ️ {len(admin_roles)} administrator roles")

                # Best practices
                if role_count > 20:
                    analysis["recommendations"].append(
                        "Many roles defined - consider role consolidation or dynamic RLS with user tables"
                    )
                elif role_count > 10:
                    analysis["insights"].append(
                        "ℹ️ Multiple roles - good for different user groups"
                    )

            else:
                analysis["insights"].append("ℹ️ No security roles - model is open to all users")
                analysis["recommendations"].append(
                    "Consider implementing RLS if data should be restricted by user/group"
                )
                analysis["recommendations"].append(
                    "Common RLS scenarios: regional filtering, department access, customer-specific data"
                )

    except Exception as e:
        logger.error(f"Error generating analysis for {op_id}: {e}")
        analysis["insights"].append("Analysis generation encountered an error")

    return analysis


def handle_simple_analysis(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fast model operations based on Microsoft Official MCP Server operations.

    Modes:
    - 'all': Run ALL 10 Microsoft MCP operations in sequence (comprehensive overview)
    - 'tables': Ultra-fast table list (< 500ms) - Microsoft MCP List operation
    - 'stats': Fast model statistics (< 1s) - Microsoft MCP GetStats operation
    - 'measures': List measures (optional table filter) - Microsoft MCP Measure List operation
    - 'measure': Get measure details (requires table + measure_name) - Microsoft MCP Measure Get operation
    - 'columns': List columns (optional table filter) - Microsoft MCP Column List operation
    - 'relationships': List relationships - Microsoft MCP Relationship List operation
    - 'partitions': List partitions (optional table filter) - Microsoft MCP Partition List operation
    - 'roles': List security roles - Microsoft MCP Role List operation
    - 'database': List databases - Microsoft MCP Database List operation
    - 'calculation_groups': List calculation groups - Microsoft MCP ListGroups operation
    - 'storage': VertiPaq storage analysis - column/table size, encoding, compression, recommendations
    """
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    agent_policy = connection_state.agent_policy
    if not agent_policy:
        return ErrorHandler.handle_manager_unavailable("agent_policy")

    # Extract mode parameter (default: all - runs all 8 auto-executable operations)
    mode = args.get("mode", "all")

    # Special mode: Run ALL 8 auto-executable core operations (excluding partitions and measure get)
    if mode == "all":
        import time

        start_time = time.time()

        # Initialize with prominent execution header
        execution_log = []
        execution_log.append("=" * 80)
        execution_log.append(">>> MICROSOFT MCP OPERATIONS - SEQUENTIAL EXECUTION <<<")
        execution_log.append("=" * 80)
        execution_log.append("")

        # Define all operations to execute
        operations = [
            (
                "01_database",
                "Database Info",
                lambda: agent_policy.analysis_orch.list_databases_simple(connection_state),
            ),
            (
                "02_stats",
                "Model Statistics (GetStats)",
                lambda: agent_policy.analysis_orch.simple_model_analysis(connection_state),
            ),
            (
                "03_tables",
                "Tables List",
                lambda: agent_policy.analysis_orch.list_tables_simple(connection_state),
            ),
            (
                "04_measures",
                "Measures List (500 max)",
                lambda: agent_policy.analysis_orch.list_measures_simple(
                    connection_state, None, 500
                ),
            ),
            (
                "05_columns",
                "Columns List (1000 max)",
                lambda: agent_policy.analysis_orch.list_columns_simple(
                    connection_state, None, 1000
                ),
            ),
            (
                "06_relationships",
                "Relationships",
                lambda: agent_policy.analysis_orch.list_relationships_simple(
                    connection_state, False
                ),
            ),
            (
                "07_calculation_groups",
                "Calculation Groups",
                lambda: agent_policy.analysis_orch.list_calculation_groups_simple(connection_state),
            ),
            (
                "08_roles",
                "Security Roles",
                lambda: agent_policy.analysis_orch.list_roles_simple(connection_state),
            ),
        ]

        operations_results = {}

        # Execute each operation with prominent visual progress
        for idx, (op_id, op_name, op_func) in enumerate(operations, 1):
            # PROMINENTLY show operation start
            execution_log.append(f"[{idx}/{len(operations)}] >>> EXECUTING: {op_name}")
            execution_log.append("-" * 80)

            op_start = time.time()
            logger.info(f"[{idx}/{len(operations)}] Executing {op_name}")

            # Execute operation
            op_result = op_func()
            op_time = round(time.time() - op_start, 3)

            # Generate operation-specific analysis summary
            op_analysis = _generate_operation_analysis(op_id, op_result)

            # Add analysis summary and timing to operation result
            op_result_enhanced = dict(op_result)
            op_result_enhanced["execution_time_seconds"] = op_time
            op_result_enhanced["analysis_summary"] = op_analysis

            # Store enhanced result
            operations_results[op_id] = op_result_enhanced

            # PROMINENTLY show operation completion with details
            if op_result.get("success"):
                # Extract key metrics from result - optimized with helper function
                msg = _extract_operation_summary(op_id, op_result)
                execution_log.append(msg)

                # Add DETAILED analysis insights to execution log - optimized
                execution_log.extend(_format_analysis_to_log(op_analysis, op_name, op_time))
            else:
                error_msg = op_result.get("error", "Unknown error")
                execution_log.append(f"[FAIL] ERROR: {error_msg}")
                execution_log.append(f"   [TIME] Execution time: {op_time}s")
                logger.error(f"{op_name} FAILED: {error_msg}")

            execution_log.append("")  # Blank line for separation

        # Calculate total execution time
        execution_time = round(time.time() - start_time, 2)

        # Count successful operations
        successful = sum(1 for op in operations_results.values() if op.get("success"))

        # Add prominent execution summary
        execution_log.append("=" * 80)
        execution_log.append("EXECUTION SUMMARY")
        execution_log.append("=" * 80)
        execution_log.append(f"[OK] Successful operations: {successful}/{len(operations)}")
        execution_log.append(f"[TIME] Total execution time: {execution_time}s")
        execution_log.append(
            f"[AVG] Average time per operation: {round(execution_time/len(operations), 2)}s"
        )
        execution_log.append("")

        # Generate detailed Power BI expert analysis
        execution_log.append("=" * 80)
        execution_log.append("GENERATING POWER BI EXPERT ANALYSIS")
        execution_log.append("=" * 80)

        temp_results = {"operations": operations_results}
        analysis = agent_policy.analysis_orch.generate_expert_analysis(temp_results)

        execution_log.append("[OK] Expert analysis complete!")
        execution_log.append("")

        # Build final response with execution log at the TOP for prominence
        # CRITICAL: Format execution_log as a string so AI displays it properly
        formatted_execution_output = "\n".join(execution_log)

        results = {
            "success": True,
            "mode": "all",
            "operations_count": 8,
            "successful_operations": successful,
            "execution_time_seconds": execution_time,
            # FORMATTED OUTPUT - This is what the AI should show to the user
            # Single string containing the complete operation-by-operation breakdown
            "formatted_output": formatted_execution_output,
            # IMPORTANT: Show this message FIRST to direct the AI to display formatted_output
            "message": f"Completed {successful}/{len(operations)} Microsoft MCP operations. OPERATION-BY-OPERATION RESULTS (see formatted_output below):",
            # Raw execution log (array of strings) - kept for backward compatibility
            "execution_log": execution_log,
            # Individual operation results (optimized Microsoft MCP format)
            "operations": operations_results,
            # Expert analysis
            "expert_analysis": analysis,
            # Summary (high-level only)
            "summary": f"Successfully executed {successful}/{len(operations)} operations in {execution_time}s with detailed expert analysis and operation-by-operation breakdowns",
        }

        return results

    # Route to appropriate function based on mode
    if mode == "tables":
        result = agent_policy.analysis_orch.list_tables_simple(connection_state)
    elif mode == "stats":
        result = agent_policy.analysis_orch.simple_model_analysis(connection_state)
    elif mode == "measures":
        # Measure List operation
        table_name = args.get("table")
        max_results = args.get("max_results")
        result = agent_policy.analysis_orch.list_measures_simple(
            connection_state, table_name, max_results
        )
    elif mode == "measure":
        # Measure Get operation - requires table and measure_name
        table_name = args.get("table")
        measure_name = args.get("measure_name")
        if not table_name or not measure_name:
            return error_envelope(
                "missing_argument",
                'mode="measure" requires both table and measure_name parameters',
            )
        result = agent_policy.analysis_orch.get_measure_simple(
            connection_state, table_name, measure_name
        )
    elif mode == "columns":
        # Column List operation
        table_name = args.get("table")
        max_results = args.get("max_results")
        result = agent_policy.analysis_orch.list_columns_simple(
            connection_state, table_name, max_results
        )
    elif mode == "relationships":
        # Relationship List operation
        active_only = args.get("active_only", False)
        result = agent_policy.analysis_orch.list_relationships_simple(connection_state, active_only)
    elif mode == "roles":
        # Role List operation
        result = agent_policy.analysis_orch.list_roles_simple(connection_state)
    elif mode == "database":
        # Database List operation
        result = agent_policy.analysis_orch.list_databases_simple(connection_state)
    elif mode == "calculation_groups":
        # Calculation Group ListGroups operation
        result = agent_policy.analysis_orch.list_calculation_groups_simple(connection_state)
    elif mode == "storage":
        # VertiPaq storage analysis via DMVs
        result = _handle_storage_analysis()
    else:
        return error_envelope(
            "invalid_argument",
            f"Unknown mode: {mode}. Valid modes: all, tables, stats, "
            "measures, measure, columns, relationships, roles, "
            "database, calculation_groups, storage",
        )

    return result


def _handle_storage_analysis() -> Dict[str, Any]:
    """
    Run VertiPaq storage analysis using DMV queries.

    Returns structured storage report with per-table and
    per-column storage metrics plus recommendations.
    """
    try:
        qe = connection_state.query_executor
        if not qe:
            return error_envelope("manager_unavailable", "Query executor not available")

        from core.dax.vertipaq_storage_report import (
            VertiPaqStorageReport,
        )

        reporter = VertiPaqStorageReport(qe)
        report = reporter.generate_report()
        return report.to_dict()

    except Exception as e:
        logger.error(
            f"Storage analysis failed: {e}",
            exc_info=True,
        )
        return error_envelope("operation_failed", f"Storage analysis failed: {e}")


_RULESET_ANALYZERS: Dict[str, Any] = {}


def _ruleset_analyzer(name: str):
    """A (cached) analyzer for a shipped ruleset other than the live default.

    Runs are pure (``BPAAnalyzer.run``), so one instance per ruleset is safely
    shared between calls.
    """
    from core.analysis.bpa_analyzer import RULESET_FILES, BPAAnalyzer

    if name not in _RULESET_ANALYZERS:
        _RULESET_ANALYZERS[name] = BPAAnalyzer(RULESET_FILES[name])
    return _RULESET_ANALYZERS[name]


class _BpaOverride:
    """connection_state view whose ``bpa_analyzer`` is a ruleset analyzer."""

    def __init__(self, base, analyzer):
        self._base = base
        self.bpa_analyzer = analyzer

    def __getattr__(self, item):
        return getattr(self._base, item)


def _annotate_fixes(result: Dict[str, Any]) -> None:
    """``fix_available`` on the flattened ``issues`` (the BPA violation dicts already carry it)."""
    fixable = {
        (v.get("rule_id"), v.get("object_type"), v.get("object_name"), v.get("table_name"))
        for v in ((result.get("analyses") or {}).get("bpa") or {}).get("violations", []) or []
        if v.get("fix_available")
    }
    if not fixable:
        return
    for issue in result.get("issues") or []:
        if (
            issue.get("source") == "bpa"
            and (
                issue.get("rule_id"),
                issue.get("object_type"),
                issue.get("object_name"),
                issue.get("table"),
            )
            in fixable
        ):
            issue["fix_available"] = True


def _audit_section(result: Dict[str, Any], state: Any, include_runtime: bool) -> Dict[str, Any]:
    """F-05: tag findings with their taxonomy id and build the per-check verdict table."""
    from core.analysis import audit_taxonomy as at

    taxonomy = at.load_taxonomy()
    analyses = result.get("analyses") or {}
    bpa = analyses.get("bpa") or {}
    violations = bpa.get("violations", []) or []
    topology = analyses.get("topology") or {}
    topo_findings = topology.get("findings", []) or []
    for v in violations:
        at.tag(v, v.get("rule_id"), taxonomy)
    for f in topo_findings:
        at.tag(f, f.get("finding_id"), taxonomy)
    for issue in result.get("issues") or []:
        at.tag(issue, issue.get("rule_id"), taxonomy)

    extra: list = []
    runtime_notes: list = []
    runtime_ran = False
    if include_runtime:
        executor = getattr(state, "query_executor", None)
        if executor is None:
            runtime_notes.append("include_runtime: no query executor; CR10 stays requires_live")
        else:
            from core.orchestration.analysis_orchestrator import AnalysisOrchestrator

            model, err = AnalysisOrchestrator._fetch_tmsl_model(executor)
            if model is None:
                runtime_notes.append(f"include_runtime: no TMSL ({err}); CR10 stays requires_live")
            else:
                extra, runtime_notes = at.runtime_ri_findings(model, executor)
                runtime_ran = True
                for cid, f in extra:
                    result.setdefault("issues", []).append(
                        {
                            "source": "audit_runtime",
                            "rule_id": f["finding_id"],
                            "type": "integrity",
                            "severity": "high",
                            "object_type": f["object_type"],
                            "object_name": f["object_name"],
                            "description": f["description"],
                            "audit_id": cid,
                            "audit_citation": f"audit-categories.md:{cid}",
                        }
                    )
    coverage = bpa.get("coverage") or {}
    report = at.build_report(
        taxonomy,
        violations=violations,
        topology_findings=topo_findings,
        evaluated_rule_ids=coverage.get("evaluated_rule_ids", []) or [],
        topology_ok=bool(topology.get("success")),
        extra_findings=extra,
        runtime_ran=runtime_ran,
        vertipaq_ran=any(
            "include_vertipaq: annotated" in str(n) for n in bpa.get("notes", []) or []
        ),
    )
    report["success"] = True
    if runtime_notes:
        report["runtime_notes"] = runtime_notes
    return report


def handle_full_analysis(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Unified comprehensive model analysis combining best practices, performance, and integrity.

    ``rules_path`` rules apply to THIS call only (I-20: they used to be merged
    into the process-wide analyzer for every later caller). ``ruleset`` picks a
    shipped rule set: ``default`` (curated comprehensive rules), ``microsoft``
    (Microsoft's Tabular Editor BPARules) or ``all``.
    """
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    agent_policy = connection_state.agent_policy
    if not agent_policy:
        return ErrorHandler.handle_manager_unavailable("agent_policy")

    scope = args.get("scope", "all")
    depth = args.get("depth", "balanced")
    include_bpa = args.get("include_bpa", True)
    include_performance = args.get("include_performance", True)
    include_integrity = args.get("include_integrity", True)
    max_seconds = args.get("max_seconds", None)
    rules_path = args.get("rules_path")
    ruleset = str(args.get("ruleset") or "default").lower()
    include_vertipaq = bool(args.get("include_vertipaq", False))
    issues_max_raw = args.get("issues_max")
    issues_max = int(issues_max_raw) if issues_max_raw is not None else 500

    include_runtime = bool(args.get("include_runtime", False))

    if ruleset not in ("default", "microsoft", "all", "audit"):
        return {
            "success": False,
            "error": f"Unknown ruleset '{ruleset}'. Valid: default, microsoft, all, audit",
            "error_type": "invalid_input",
        }

    extra_rules: list = []
    rules_report = None
    state: Any = connection_state
    try:
        if ruleset == "microsoft":
            state = _BpaOverride(connection_state, _ruleset_analyzer("microsoft"))
        elif ruleset in ("all", "audit"):
            live = getattr(connection_state, "bpa_analyzer", None)
            ids = {r.id for r in getattr(live, "rules", []) or []}
            # A curated rule that supersedes a Microsoft rule must not double-report it.
            ids |= superseded_rule_ids(ids)
            extra_rules.extend(r for r in _ruleset_analyzer("microsoft").rules if r.id not in ids)
            if ruleset == "audit":
                # F-05: the taxonomy-only rules (config/audit_taxonomy.json maps every check id)
                from core.analysis.audit_taxonomy import audit_bpa_rules

                have = ids | {r.id for r in extra_rules}
                extra_rules.extend(r for r in audit_bpa_rules() if r.id not in have)
    except Exception as e:  # noqa: BLE001
        return {
            "success": False,
            "error": f"Failed to load ruleset '{ruleset}': {e}",
            "error_type": "rules_load_error",
        }

    if rules_path:
        from core.analysis.bpa_analyzer import load_rules_file
        from core.validation.path_guard import guard_or_error

        safe_rules, err = guard_or_error(rules_path, "rules_path", must_exist=True)
        if err:
            return err
        try:
            bpa = getattr(state, "bpa_analyzer", None)
            existing = {r.id for r in getattr(bpa, "rules", []) or []}
            existing |= {r.id for r in extra_rules}
            loaded, rules_report = load_rules_file(str(safe_rules), existing)
            extra_rules.extend(loaded)
        except Exception as e:
            logger.error(f"Failed to load external BPA rules from {rules_path}: {e}")
            return {
                "success": False,
                "error": f"Failed to load rules_path: {e}",
                "error_type": "rules_load_error",
            }

    emit_progress(1, 3, "Running analysis...")
    result = agent_policy.analysis_orch.comprehensive_analysis(
        state,
        scope=scope,
        depth=depth,
        include_bpa=include_bpa,
        include_performance=include_performance,
        include_integrity=include_integrity,
        max_seconds=max_seconds,
        extra_rules=extra_rules or None,
        include_vertipaq=include_vertipaq,
        issues_max=issues_max,
    )

    # Enrich the flattened issues (I-14: comprehensive_analysis now returns them).
    emit_progress(2, 3, "Enriching results...")
    if isinstance(result, dict) and result.get("success") and result.get("issues"):
        try:
            result["issues"] = [enrich_issue_with_impact(issue) for issue in result["issues"]]
            result = add_impact_summary(result)
        except Exception as e:
            logger.error(f"Error enriching issues with business impact: {e}", exc_info=True)

    if isinstance(result, dict) and result.get("success"):
        _annotate_fixes(result)
        if ruleset == "audit":
            try:
                result["audit"] = _audit_section(result, state, include_runtime)
            except Exception as e:  # noqa: BLE001 - the audit table must not sink the analysis
                logger.error(f"Audit taxonomy report failed: {e}", exc_info=True)
                result["audit"] = {
                    "success": False,
                    "error_type": "audit_report_failed",
                    "error": f"audit report failed: {e}",
                }

    emit_progress(3, 3, "Complete")
    if isinstance(result, dict):
        result["ruleset"] = ruleset
        if rules_report is not None:
            result["external_rules"] = rules_report
            result["external_rules_merged"] = rules_report.get("added", 0)
    return result


def handle_analysis_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch analysis operations: simple, full, compare"""
    operation = args.get("operation", "simple")

    if operation == "simple":
        return handle_simple_analysis(args)
    elif operation == "full":
        return handle_full_analysis(args)
    elif operation == "compare":
        from server.handlers._internal.comparison_helpers import handle_compare_pbi_models

        return handle_compare_pbi_models(args)
    else:
        return error_envelope(
            "invalid_operation",
            f"Unknown operation: {operation}. Valid: simple, full, compare",
        )


def register_analysis_handlers(registry):
    """Register consolidated analysis handler"""

    tool = ToolDefinition(
        name="06_Analysis_Operations",
        description="Model-wide analysis: simple (8 ops + insights, 2-5s), full (BPA with the curated ruleset or Microsoft BPARules + topology + M practices + integrity from one TMSL snapshot), compare (two open models, or old/new targets: port, XMLA, PBIP, git ref). Use this for structural rules across the whole model. For DAX expression-level analysis on a single measure use 05_DAX_Intelligence; for runtime SE/FE timing of a specific visual use 09_Debug_Operations.",
        handler=handle_analysis_operations,
        input_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["simple", "full", "compare"],
                    "default": "simple",
                },
                "mode": {
                    "type": "string",
                    "enum": [
                        "all",
                        "tables",
                        "stats",
                        "measures",
                        "measure",
                        "columns",
                        "relationships",
                        "roles",
                        "database",
                        "calculation_groups",
                        "storage",
                    ],
                    "description": "Simple analysis mode",
                    "default": "all",
                },
                "table": {"type": "string", "description": "Table filter"},
                "measure_name": {"type": "string", "description": "Required for mode=measure"},
                "max_results": {"type": "integer"},
                "active_only": {"type": "boolean", "default": False},
                "scope": {
                    "type": "string",
                    "enum": ["all", "best_practices", "performance", "integrity"],
                    "description": "Analysis scope",
                    "default": "all",
                },
                "depth": {
                    "type": "string",
                    "enum": ["fast", "balanced", "deep"],
                    "default": "balanced",
                },
                "include_bpa": {"type": "boolean", "default": True},
                "include_performance": {"type": "boolean", "default": True},
                "include_integrity": {"type": "boolean", "default": True},
                "max_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
                "rules_path": {
                    "type": "string",
                    "description": "operation=full: external BPA rules file (Tabular Editor array OR {rules:[...]}) added to the ruleset for THIS call only; the response reports added / skipped_duplicate_ids / invalid / unsupported",
                },
                "ruleset": {
                    "type": "string",
                    "enum": ["default", "microsoft", "all", "audit"],
                    "default": "default",
                    "description": "operation=full: BPA rule set - default (curated), microsoft (TE BPARules), all (both), audit (all + taxonomy rules; findings cite their audit id)",
                    "x-doc": "operation=full: shipped BPA rule set - default (curated), microsoft (Tabular Editor BPARules), all (both), audit (all + the audit-taxonomy rules: every finding cites its audit-categories.md id and `audit` carries a per-check verdict table; manual / requires_live / not_evaluated checks are never reported as passing)",
                },
                "include_runtime": {
                    "type": "boolean",
                    "default": False,
                    "description": "operation=full ruleset=audit: also run the runtime referential-integrity check (CR10: one small DAX query per active relationship, capped at 40)",
                },
                "include_vertipaq": {
                    "type": "boolean",
                    "default": False,
                    "description": "operation=full: read VertiPaq storage DMVs first so cardinality/row-count rules (Vertipaq_* annotations) evaluate",
                },
                "issues_max": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 500,
                    "description": "operation=full: cap on the flattened, severity-ranked `issues` list (issues_total is exact)",
                },
                "old_port": {
                    "type": "integer",
                    "description": "compare: OLD model port (omit both with exactly two instances open).",
                    "x-doc": "operation=compare: port of the OLD (base) model. Omit both ports with exactly two instances open to auto-pick (older instance = old)",
                },
                "new_port": {
                    "type": "integer",
                    "description": "operation=compare: port of the NEW model",
                },
                "old": {
                    "type": "object",
                    "description": "compare: OLD target {port} | {xmla:{workspace,model}} | {pbip_path} | {git_ref,pbip_path}",
                },
                "new": {
                    "type": "object",
                    "description": "compare: NEW target (same shapes as old); splits changes that affect results from cosmetic ones",
                },
                "report": {
                    "type": "boolean",
                    "default": True,
                    "description": "compare old/new: also write the HTML report (false = result only)",
                },
                "output_path": {
                    "type": "string",
                    "description": "operation=compare: HTML diff report path (default <repo>/exports/model_diffs/model_diff_<ts>.html)",
                },
                "generate_json": {
                    "type": "boolean",
                    "default": False,
                    "description": "operation=compare: also write the diff as JSON next to the HTML",
                },
            },
            "required": [],
        },
        category="analysis",
        sort_order=60,
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )

    registry.register(tool)
    logger.info("Registered analysis_operations handler")
