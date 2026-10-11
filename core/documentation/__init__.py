"""
Documentation generation modules for Power BI models.

Word report rendering, model snapshots / change detection, complexity scoring
and the data collector behind 08_Documentation_Word.

2026-09-22 (I-27): the unreachable interactive HTML explorer and the PDF
converter / snapshot-comparison report (no tool ever called them) were removed.
"""

from .word_generator import render_word_report
from .snapshot_manager import (
    save_snapshot,
    load_snapshot,
    compute_diff,
    snapshot_from_context,
)
from .complexity_analyzer import calculate_measure_complexity
from .data_collector import collect_model_documentation

__all__ = [
    "render_word_report",
    "save_snapshot",
    "load_snapshot",
    "compute_diff",
    "snapshot_from_context",
    "calculate_measure_complexity",
    "collect_model_documentation",
]
