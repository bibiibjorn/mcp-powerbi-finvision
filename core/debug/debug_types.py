"""Leaf definitions split out of ``core.debug.debug_operations`` (C-17).

Pure move, no behaviour change: ``core.debug.debug_operations`` re-exports every name below.
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


def _first_value(exec_result: Dict[str, Any], alias: str) -> Any:
    """The *alias* column of the first row of a ROW(...) result (bracketed or bare)."""
    from .visual_query_builder import row_value

    rows = exec_result.get("rows") or []
    return row_value(rows[0], alias) if rows else None


@dataclass
class ValidationResult:
    """Result of a visual validation."""

    visual_id: str
    visual_name: str
    page_name: str
    measure_name: str
    value: Any
    execution_time_ms: float
    success: bool
    error: Optional[str] = None


@dataclass
class ProfileResult:
    """Result of profiling a visual."""

    visual_id: str
    visual_name: str
    visual_type: str
    page_name: str
    measures: List[str]
    avg_time_ms: float
    min_time_ms: float
    max_time_ms: float
    row_count: int
    filter_count: int
    issues: List[str] = field(default_factory=list)
    runs_ms: List[float] = field(default_factory=list)  # every measured run (F-18 history)


@dataclass
class LineageInfo:
    """Measure or filter lineage information."""

    name: str
    table: Optional[str]
    visuals: List[Dict[str, Any]] = field(default_factory=list)
    pages: List[str] = field(default_factory=list)
    usage_count: int = 0
