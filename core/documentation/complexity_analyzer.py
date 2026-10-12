"""DAX measure complexity analysis for Power BI models."""

from __future__ import annotations

from typing import Any, Dict


def calculate_measure_complexity(expression: str, dependencies: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate complexity score for a DAX measure.

    Analyzes multiple factors including expression length, nesting depth,
    function complexity, and dependencies to produce an overall complexity score.

    Args:
        expression: The DAX expression to analyze
        dependencies: Dictionary containing measure/column/table dependencies

    Returns:
        dict: {
            "score": int (0-100),
            "level": str ("Low", "Medium", "High", "Very High"),
            "color": tuple (RGB),
            "factors": dict (breakdown of complexity factors)
        }
    """
    if not expression:
        return {"score": 0, "level": "N/A", "color": (128, 128, 128), "factors": {}}

    score = 0
    factors = {}

    # Expression length (0-20 points)
    expr_len = len(expression)
    length_score = min(20, expr_len // 50)
    score += length_score
    factors["length"] = length_score

    # Number of lines (0-15 points)
    lines = expression.count("\n") + 1
    line_score = min(15, lines // 2)
    score += line_score
    factors["lines"] = line_score

    # DAX function complexity (0-25 points)
    complex_functions = [
        "CALCULATE",
        "FILTER",
        "ALL",
        "ALLEXCEPT",
        "KEEPFILTERS",
        "USERELATIONSHIP",
        "SWITCH",
        "CONCATENATEX",
        "SUMMARIZE",
        "SUMMARIZECOLUMNS",
        "GENERATE",
        "TREATAS",
        "CROSSFILTER",
        "EARLIER",
        "RANKX",
    ]
    function_count = sum(expression.upper().count(f) for f in complex_functions)
    function_score = min(25, function_count * 3)
    score += function_score
    factors["complex_functions"] = function_score

    # Nesting depth (0-20 points) - count nested parentheses
    max_depth = 0
    current_depth = 0
    for char in expression:
        if char == "(":
            current_depth += 1
            max_depth = max(max_depth, current_depth)
        elif char == ")":
            current_depth -= 1
    depth_score = min(20, max_depth * 2)
    score += depth_score
    factors["nesting"] = depth_score

    # Dependencies (0-20 points)
    dep_count = len(dependencies.get("measures", [])) + len(dependencies.get("columns", []))
    dep_score = min(20, dep_count * 2)
    score += dep_score
    factors["dependencies"] = dep_score

    # Determine level and color
    if score <= 25:
        level = "Low"
        color = (0, 176, 80)  # Green
    elif score <= 50:
        level = "Medium"
        color = (255, 192, 0)  # Yellow/Orange
    elif score <= 75:
        level = "High"
        color = (255, 128, 0)  # Orange
    else:
        level = "Very High"
        color = (192, 0, 0)  # Red

    return {"score": min(100, score), "level": level, "color": color, "factors": factors}
