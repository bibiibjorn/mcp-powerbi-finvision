"""Rewrite strategies for DAX optimization."""

from .base import RewriteStrategy  # noqa: F401
from .variable_extraction import VariableExtractionStrategy
from .calculate_optimization import CalculateOptimizationStrategy
from .iterator_optimization import IteratorOptimizationStrategy
from .pattern_replacement import PatternReplacementStrategy


def load_strategies() -> list:
    """Instantiate and return all rewrite strategies."""
    return [
        VariableExtractionStrategy(),
        CalculateOptimizationStrategy(),
        IteratorOptimizationStrategy(),
        PatternReplacementStrategy(),
    ]
