"""
Data Binding Builder for PBIP Visual Authoring

Builds Power BI data binding structures (queryState projections) from simple
(table, field) tuples. This reverses the extraction logic in pbip_report_analyzer.py.
"""

from typing import Any, Dict, Optional


def build_measure_binding(
    table: str,
    measure: str,
    display_name: Optional[str] = None,
    active: bool = True,
) -> Dict[str, Any]:
    """Build a measure projection for visual queryState.

    Args:
        table: Source table/entity name (e.g., "m Measure")
        measure: Measure property name (e.g., "Net Asset Value")
        display_name: Optional display name override
        active: Whether the field is active in the visual

    Returns:
        Complete projection dict for use in queryState
    """
    projection: Dict[str, Any] = {
        "field": {
            "Measure": {
                "Expression": {"SourceRef": {"Entity": table}},
                "Property": measure,
            }
        },
        "queryRef": f"{table}.{measure}",
        "nativeQueryRef": measure,
        "active": active,
    }
    if display_name:
        projection["displayName"] = display_name
    return projection


def build_column_binding(
    table: str,
    column: str,
    display_name: Optional[str] = None,
    active: bool = True,
) -> Dict[str, Any]:
    """Build a column projection for visual queryState.

    Args:
        table: Source table/entity name (e.g., "d Date")
        column: Column property name (e.g., "Month")
        display_name: Optional display name override
        active: Whether the field is active in the visual

    Returns:
        Complete projection dict for use in queryState
    """
    projection: Dict[str, Any] = {
        "field": {
            "Column": {
                "Expression": {"SourceRef": {"Entity": table}},
                "Property": column,
            }
        },
        "queryRef": f"{table}.{column}",
        "nativeQueryRef": column,
        "active": active,
    }
    if display_name:
        projection["displayName"] = display_name
    return projection


def build_hierarchy_binding(
    table: str,
    hierarchy: str,
    level: str,
    display_name: Optional[str] = None,
    active: bool = True,
) -> Dict[str, Any]:
    """Build a hierarchy level projection for visual queryState.

    Args:
        table: Source table/entity name
        hierarchy: Hierarchy name
        level: Level within the hierarchy
        display_name: Optional display name override
        active: Whether the field is active in the visual

    Returns:
        Complete projection dict for use in queryState
    """
    projection: Dict[str, Any] = {
        "field": {
            "HierarchyLevel": {
                "Expression": {
                    "Hierarchy": {
                        "Expression": {"SourceRef": {"Entity": table}},
                        "Hierarchy": hierarchy,
                    }
                },
                "Level": level,
            }
        },
        "queryRef": f"{table}.{hierarchy}.{level}",
        "nativeQueryRef": level,
        "active": active,
    }
    if display_name:
        projection["displayName"] = display_name
    return projection
