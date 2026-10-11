"""Dataclasses for the DAX optimizer."""

from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass
class RewriteResult:
    """Result of a single DAX rewrite transformation."""

    strategy: str
    rule_id: str
    original_fragment: str
    rewritten_fragment: str
    full_rewritten_dax: str
    explanation: str
    confidence: str  # "high", "medium", "low"
    estimated_improvement: str
    validation_passed: bool
    # True for advice that is only valid under conditions the optimizer cannot
    # prove statically (review E-04/E-09): never applied, full_rewritten_dax is
    # the input unchanged, the condition is stated in ``explanation``.
    suggestion_only: bool = False
    # Structural-gate verdict when validation failed (None when it passed).
    rejection_reason: Optional[str] = None


@dataclass
class OptimizationResult:
    """Complete optimization result for a measure."""

    success: bool
    measure_name: Optional[str]
    original_dax: str
    analysis: Any  # UnifiedAnalysisResult
    rewrites: List[RewriteResult]
    final_dax: Optional[str]
    applied: bool
    apply_error: Optional[str]
    improvement_summary: str
