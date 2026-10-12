"""
Business Impact Mapper
Maps technical issues to business impact and consequences
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


# Business impact mapping for common Power BI issues
BUSINESS_IMPACT_MAP = {
    # Relationship Issues
    "bidirectional_relationship": {
        "impact": "Can cause incorrect calculations and ambiguous filter paths",
        "consequence": "Reports may show wrong numbers or fail to filter properly",
        "urgency": "HIGH",
        "business_risk": "Data accuracy and trust in reports",
        "fix_effort": "Medium - Requires analysis of relationship paths and testing",
        "recommendation": "Convert to single-direction relationships where possible",
    },
    "inactive_relationship": {
        "impact": "Inactive relationships may indicate unused or misconfigured connections",
        "consequence": "Potential data quality issues or performance overhead",
        "urgency": "MEDIUM",
        "business_risk": "Incomplete or inaccurate analysis",
        "fix_effort": "Low - Review and either activate or remove",
        "recommendation": "Review inactive relationships to ensure they are intentional",
    },
    "circular_dependency": {
        "impact": "Circular dependencies prevent model from calculating correctly",
        "consequence": "Model refresh will fail, reports cannot be updated",
        "urgency": "CRITICAL",
        "business_risk": "Complete failure of reporting solution",
        "fix_effort": "High - Requires redesign of calculation logic",
        "recommendation": "Break the circular reference by introducing intermediate calculations",
    },
    "many_to_many_relationship": {
        "impact": "Many-to-many relationships can cause performance issues and unexpected results",
        "consequence": "Slow query performance and potential double-counting",
        "urgency": "HIGH",
        "business_risk": "Incorrect aggregations and slow user experience",
        "fix_effort": "High - May require bridge tables or model redesign",
        "recommendation": "Consider using bridge tables for many-to-many scenarios",
    },
    # DAX Issues
    "dax_syntax_error": {
        "impact": "DAX syntax errors prevent measures from calculating",
        "consequence": "Reports show errors or blank values",
        "urgency": "CRITICAL",
        "business_risk": "Reports cannot be used",
        "fix_effort": "Low to Medium - Fix syntax errors",
        "recommendation": "Use DAX intelligence tool to identify and fix syntax issues",
    },
    "dax_performance_issue": {
        "impact": "Slow DAX queries degrade user experience",
        "consequence": "Reports take too long to load, users get frustrated",
        "urgency": "MEDIUM",
        "business_risk": "User adoption and satisfaction",
        "fix_effort": "Medium to High - Requires optimization",
        "recommendation": "Optimize DAX with proper CALCULATE context and avoid expensive functions",
    },
    "context_transition_issue": {
        "impact": "Incorrect context transitions lead to wrong calculations",
        "consequence": "Measures show incorrect values",
        "urgency": "HIGH",
        "business_risk": "Data accuracy",
        "fix_effort": "Medium - Requires DAX expertise",
        "recommendation": "Review CALCULATE and filter context usage",
    },
    # Performance Issues
    "high_cardinality_column": {
        "impact": "High cardinality columns consume more memory and slow down queries",
        "consequence": "Model size increases, refresh times slow down",
        "urgency": "MEDIUM",
        "business_risk": "Scalability and performance",
        "fix_effort": "Medium - May require data model changes",
        "recommendation": "Consider removing or hashing high cardinality columns",
    },
    "calculated_column_performance": {
        "impact": "Calculated columns are evaluated during refresh and consume memory",
        "consequence": "Slow refresh times and increased memory usage",
        "urgency": "MEDIUM",
        "business_risk": "Operational efficiency",
        "fix_effort": "Medium - Convert to measures or move to data source",
        "recommendation": "Convert calculated columns to measures where possible",
    },
    "missing_aggregation": {
        "impact": "Columns without proper aggregation may cause query performance issues",
        "consequence": "Slow visuals and poor user experience",
        "urgency": "LOW",
        "business_risk": "User experience",
        "fix_effort": "Low - Set proper default aggregation",
        "recommendation": "Set default aggregation for numeric columns",
    },
    # Data Quality Issues
    "duplicate_rows": {
        "impact": "Duplicate rows can inflate counts and skew calculations",
        "consequence": "Incorrect totals and metrics",
        "urgency": "HIGH",
        "business_risk": "Data accuracy and trust",
        "fix_effort": "Medium - Clean data at source or add deduplication",
        "recommendation": "Remove duplicates at the data source or use DISTINCT in DAX",
    },
    "null_values": {
        "impact": "Null values in key columns can cause join failures and missing data",
        "consequence": "Incomplete analysis and incorrect totals",
        "urgency": "MEDIUM",
        "business_risk": "Data completeness",
        "fix_effort": "Low to Medium - Handle nulls in ETL or DAX",
        "recommendation": "Handle null values in Power Query or use COALESCE in DAX",
    },
    "data_type_mismatch": {
        "impact": "Data type mismatches cause errors and prevent joins",
        "consequence": "Relationships fail, data cannot be combined",
        "urgency": "HIGH",
        "business_risk": "Data integration",
        "fix_effort": "Low - Fix data types in Power Query",
        "recommendation": "Ensure consistent data types across tables",
    },
    # Best Practice Violations
    "naming_convention": {
        "impact": "Poor naming conventions reduce maintainability",
        "consequence": "Difficult for team to understand and maintain model",
        "urgency": "LOW",
        "business_risk": "Long-term maintainability",
        "fix_effort": "Low - Rename objects",
        "recommendation": "Use consistent naming conventions (e.g., Table Name, measureName)",
    },
    "missing_description": {
        "impact": "Missing descriptions make model harder to understand",
        "consequence": "Knowledge transfer issues, onboarding delays",
        "urgency": "LOW",
        "business_risk": "Knowledge management",
        "fix_effort": "Low - Add descriptions",
        "recommendation": "Document all measures and tables with clear descriptions",
    },
    "unused_object": {
        "impact": "Unused objects increase model size and complexity",
        "consequence": "Slower refresh, larger file size, maintenance overhead",
        "urgency": "LOW",
        "business_risk": "Maintenance efficiency",
        "fix_effort": "Low - Remove unused objects",
        "recommendation": "Remove unused tables, columns, and measures",
    },
    # Security Issues
    "missing_rls": {
        "impact": "Missing RLS may expose sensitive data",
        "consequence": "Data security and compliance risks",
        "urgency": "CRITICAL",
        "business_risk": "Data security and regulatory compliance",
        "fix_effort": "Medium - Design and implement RLS",
        "recommendation": "Implement Row-Level Security (RLS) for sensitive data",
    },
    "rls_performance": {
        "impact": "Complex RLS rules can slow down queries significantly",
        "consequence": "Poor user experience with secured reports",
        "urgency": "MEDIUM",
        "business_risk": "User adoption vs security trade-off",
        "fix_effort": "Medium to High - Optimize RLS rules",
        "recommendation": "Simplify RLS rules or use security tables",
    },
    # M Query Issues
    "m_query_folding": {
        "impact": "Queries without folding load more data into memory",
        "consequence": "Slow refresh times and increased memory usage",
        "urgency": "MEDIUM",
        "business_risk": "Operational efficiency",
        "fix_effort": "Medium - Restructure Power Query",
        "recommendation": "Restructure queries to enable query folding",
    },
    "m_query_error": {
        "impact": "M query errors prevent data refresh",
        "consequence": "Reports show stale data or fail completely",
        "urgency": "CRITICAL",
        "business_risk": "Data freshness and availability",
        "fix_effort": "Low to Medium - Fix query errors",
        "recommendation": "Review and fix Power Query errors",
    },
}


def enrich_issue_with_impact(issue: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrich an analysis issue with business impact context

    Args:
        issue: Issue dictionary from analysis results

    Returns:
        Enriched issue dictionary with impact, consequences, and recommendations
    """
    try:
        # Get rule_id or issue type to look up impact
        rule_id = issue.get("rule_id", "").lower()
        issue_type = issue.get("type", "").lower()
        severity = issue.get("severity", "medium").lower()

        # Try to find matching impact info
        impact_info = None

        # Direct match on rule_id
        if rule_id in BUSINESS_IMPACT_MAP:
            impact_info = BUSINESS_IMPACT_MAP[rule_id]
        # Try issue_type
        elif issue_type in BUSINESS_IMPACT_MAP:
            impact_info = BUSINESS_IMPACT_MAP[issue_type]
        # Try partial matches
        else:
            for key, value in BUSINESS_IMPACT_MAP.items():
                if key in rule_id or key in issue_type:
                    impact_info = value
                    break

        # If we found impact info, enrich the issue
        if impact_info:
            issue["business_impact"] = {
                "impact": impact_info["impact"],
                "consequence": impact_info["consequence"],
                "urgency": impact_info["urgency"],
                "business_risk": impact_info["business_risk"],
                "fix_effort": impact_info["fix_effort"],
                "recommendation": impact_info["recommendation"],
            }
            issue["why_it_matters"] = impact_info["impact"]
            issue["consequences"] = impact_info["consequence"]
            issue["fix_effort"] = impact_info["fix_effort"]
        else:
            # Provide generic impact based on severity
            issue["business_impact"] = _get_generic_impact(severity)
            issue["why_it_matters"] = f"This {severity}-severity issue may affect model quality"
            issue["consequences"] = "Review and address to maintain model health"

    except Exception as e:
        logger.error(f"Error enriching issue with impact: {e}", exc_info=True)

    return issue


def _get_generic_impact(severity: str) -> Dict[str, str]:
    """Get generic impact info based on severity"""
    severity_map = {
        "critical": {
            "urgency": "CRITICAL",
            "business_risk": "Model functionality",
            "fix_effort": "Immediate attention required",
            "recommendation": "Address this issue immediately",
        },
        "high": {
            "urgency": "HIGH",
            "business_risk": "Data accuracy or performance",
            "fix_effort": "Should be addressed soon",
            "recommendation": "Review and fix to ensure model quality",
        },
        "medium": {
            "urgency": "MEDIUM",
            "business_risk": "Model maintainability",
            "fix_effort": "Plan to address in next update",
            "recommendation": "Consider addressing to improve model",
        },
        "low": {
            "urgency": "LOW",
            "business_risk": "Best practice compliance",
            "fix_effort": "Address when convenient",
            "recommendation": "Nice to fix, but not urgent",
        },
    }

    return severity_map.get(severity, severity_map["medium"])


def add_impact_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add an overall impact summary to analysis results

    Args:
        result: Analysis result dictionary

    Returns:
        Result with added impact_summary
    """
    try:
        issues = result.get("issues", [])
        if not issues:
            result["impact_summary"] = {
                "total_issues": 0,
                "critical_impact": 0,
                "high_impact": 0,
                "medium_impact": 0,
                "low_impact": 0,
                "overall_health": "EXCELLENT",
            }
            return result

        # Count by urgency
        critical = len(
            [
                i
                for i in issues
                if i.get("business_impact", {}).get("urgency") == "CRITICAL"
                or i.get("severity") == "critical"
            ]
        )
        high = len(
            [
                i
                for i in issues
                if i.get("business_impact", {}).get("urgency") == "HIGH"
                or i.get("severity") == "high"
            ]
        )
        medium = len(
            [
                i
                for i in issues
                if i.get("business_impact", {}).get("urgency") == "MEDIUM"
                or i.get("severity") == "medium"
            ]
        )
        low = len(
            [
                i
                for i in issues
                if i.get("business_impact", {}).get("urgency") == "LOW"
                or i.get("severity") == "low"
            ]
        )

        # Determine overall health
        if critical > 0:
            overall_health = "CRITICAL"
        elif high > 5:
            overall_health = "POOR"
        elif high > 0:
            overall_health = "FAIR"
        elif medium > 10:
            overall_health = "GOOD"
        else:
            overall_health = "EXCELLENT"

        result["impact_summary"] = {
            "total_issues": len(issues),
            "critical_impact": critical,
            "high_impact": high,
            "medium_impact": medium,
            "low_impact": low,
            "overall_health": overall_health,
            "action_required": critical > 0 or high > 0,
        }

        # Add priority recommendations
        if critical > 0:
            result["priority_action"] = f"URGENT: Address {critical} critical issue(s) immediately"
        elif high > 0:
            result["priority_action"] = f"Address {high} high-priority issue(s) soon"
        elif medium > 3:
            result["priority_action"] = f"Consider addressing {medium} medium-priority issues"
        else:
            result["priority_action"] = (
                "Model is in good shape - review low-priority items when convenient"
            )

    except Exception as e:
        logger.error(f"Error adding impact summary: {e}", exc_info=True)

    return result
