"""
Visual Context Flow Diagram Generator

Generates ASCII and HTML visualizations of DAX context flow
Similar to SQLBI's visual explanations
"""

import logging
from typing import List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FlowStep:
    """Represents a step in the context flow"""

    step_number: int
    description: str
    filter_context: List[str]
    row_context: Optional[str]
    transition_type: Optional[str]  # None, "row_to_filter", "filter_to_row"


class VisualFlowDiagramGenerator:
    """
    Generate visual diagrams of DAX context flow

    Creates both ASCII and HTML visualizations showing:
    - Filter context progression
    - Row context activation/deactivation
    - Context transitions
    - CALCULATE modifications
    """

    def __init__(self):
        """Initialize diagram generator"""
        pass

    def _extract_code_snippet(
        self, dax_expression: str, position: int, context_length: int = 40
    ) -> str:
        """
        Extract a code snippet around a specific position in DAX expression

        Args:
            dax_expression: Full DAX expression
            position: Character position to extract around
            context_length: Number of characters to show before/after position

        Returns:
            Code snippet with the position highlighted
        """
        if not dax_expression:
            return ""

        start = max(0, position - context_length)
        end = min(len(dax_expression), position + context_length)

        snippet = dax_expression[start:end]

        # Clean up the snippet (remove excess whitespace)
        snippet = " ".join(snippet.split())

        # Add ellipsis if truncated
        if start > 0:
            snippet = "..." + snippet
        if end < len(dax_expression):
            snippet = snippet + "..."

        return snippet

    def generate_ascii_diagram(self, context_analysis, dax_expression: str = None) -> str:
        """
        Generate ASCII diagram of context flow

        Args:
            context_analysis: ContextFlowExplanation from DaxContextAnalyzer
            dax_expression: Optional original DAX expression to extract code snippets

        Returns:
            ASCII diagram string
        """
        try:
            lines = []

            # Header
            lines.append("=" * 80)
            lines.append("  DAX CONTEXT FLOW DIAGRAM")
            lines.append("=" * 80)
            lines.append("")

            # Show variables if any
            if context_analysis.transitions and any(
                t.variables_in_scope for t in context_analysis.transitions
            ):
                all_vars = set()
                for t in context_analysis.transitions:
                    all_vars.update(t.variables_in_scope)

                if all_vars:
                    lines.append("Variables in Scope:")
                    for var_name in sorted(all_vars):
                        lines.append(f"  VAR {var_name}")
                    lines.append("")

            # Legend
            lines.append("Legend:")
            lines.append("  [F] = Filter Context")
            lines.append("  [R] = Row Context")
            lines.append("  --> = Context Transition")
            lines.append("  ~~> = Measure Reference (Implicit CALCULATE)")
            lines.append("")
            lines.append("=" * 80)
            lines.append("")

            if not context_analysis.transitions:
                lines.append("No context transitions detected.")
                lines.append("")
                lines.append("[F] Initial Filter Context (from report/slicer)")
                lines.append("     |")
                lines.append("     v")
                lines.append("[Result] Simple calculation, no context changes")
                return "\n".join(lines)

            # Build flow diagram
            current_state = "filter"  # Start in filter context
            step_num = 0

            lines.append("[F] Initial Filter Context")
            lines.append("     |  (Inherited from report visuals and slicers)")
            lines.append("     v")

            for transition in context_analysis.transitions:
                step_num += 1

                if transition.type.value == "explicit_calculate":
                    lines.append("     | ")
                    lines.append(
                        f"[{step_num}]  {transition.function} (Line {transition.line}, Col {transition.column})"
                    )

                    # Show actual DAX code snippet if available
                    if dax_expression:
                        snippet = self._extract_code_snippet(
                            dax_expression, transition.location, 50
                        )
                        lines.append(f"     |      DAX: {snippet}")

                    lines.append(
                        "     |  --> Context Transition: Row → Filter (if row context exists)"
                    )

                    # Show specific filter modifications if available
                    if transition.filter_context_after:
                        lines.append(
                            f"     |      Applies {len(transition.filter_context_after)} filter(s):"
                        )
                        for i, filter_expr in enumerate(transition.filter_context_after[:2], 1):
                            # Truncate long expressions
                            filter_display = (
                                filter_expr if len(filter_expr) <= 60 else filter_expr[:60] + "..."
                            )
                            lines.append(f"     |        {i}. {filter_display}")
                        if len(transition.filter_context_after) > 2:
                            lines.append(
                                f"     |        ... (+{len(transition.filter_context_after) - 2} more filters)"
                            )
                    else:
                        lines.append("     |      Modifies filter context")

                    lines.append("     v")

                    # Show resulting filter context
                    if transition.filter_context_after:
                        lines.append(
                            f"[F] Modified Filter Context ({len(transition.filter_context_after)} filters applied)"
                        )
                    else:
                        lines.append("[F] Modified Filter Context")
                    current_state = "filter"

                elif transition.type.value == "iterator":
                    lines.append("     | ")
                    lines.append(
                        f"[{step_num}]  {transition.function} (Line {transition.line}, Col {transition.column})"
                    )

                    # Show actual DAX code snippet if available
                    if dax_expression:
                        snippet = self._extract_code_snippet(
                            dax_expression, transition.location, 50
                        )
                        lines.append(f"     |      DAX: {snippet}")

                    # Show table and columns being iterated
                    if transition.table_name:
                        lines.append(
                            f"     |  Creates Row Context for iteration over table '{transition.table_name}'"
                        )
                        if transition.column_names:
                            cols_display = ", ".join(transition.column_names[:3])
                            if len(transition.column_names) > 3:
                                cols_display += f", ... (+{len(transition.column_names) - 3} more)"
                            lines.append(f"     |  Columns referenced: {cols_display}")
                    else:
                        lines.append("     |  Creates Row Context for iteration")

                    lines.append("     v")

                    # Show table being iterated with full info
                    if transition.table_name:
                        lines.append(f"[R] Row Context: '{transition.table_name}' table")
                    else:
                        lines.append("[R] Row Context: Iterating over table rows")
                    lines.append("     |  (Each row evaluated separately)")
                    current_state = "row"

                    # Check if there are measure references inside
                    if transition.performance_impact.value != "low":
                        lines.append("     |")
                        lines.append("     | ⚠️  WARNING: Measure references inside iterator")
                        lines.append("     |     Each measure reference causes context transition")
                        lines.append("     |     This happens for EVERY ROW!")
                        lines.append("     |")
                        lines.append("     +---> For each row:")
                        lines.append("     |       [R] Current Row")
                        lines.append("     |         ~~> Implicit CALCULATE (measure ref)")
                        lines.append("     |       [F] Transition to Filter Context")
                        lines.append("     |         (Evaluate measure)")
                        lines.append("     |       [Result] Back to Row Context")
                        lines.append("     |     Loop repeats...")

                    lines.append("     v")

                elif transition.type.value == "implicit_measure":
                    lines.append("     | ")
                    # Show actual measure name prominently
                    measure_display = (
                        f"[{transition.measure_name}]"
                        if transition.measure_name
                        else "Measure Reference"
                    )
                    lines.append(
                        f"[{step_num}]  MEASURE_REFERENCE: {measure_display} (Line {transition.line}, Col {transition.column})"
                    )

                    # Show actual DAX code snippet if available
                    if dax_expression:
                        snippet = self._extract_code_snippet(
                            dax_expression, transition.location, 50
                        )
                        lines.append(f"     |      DAX: {snippet}")

                    if current_state == "row":
                        lines.append("     |  ~~> Implicit CALCULATE (in row context)")
                        lines.append("     |      Row → Filter transition")
                        lines.append("     v")
                        lines.append(f"[F] Evaluate measure: {measure_display}")
                        lines.append("     |  (Filter context created from current row)")
                        lines.append("     v")
                        lines.append("[R] Return to Row Context")
                    else:
                        lines.append("     |  ~~> Implicit CALCULATE")
                        lines.append("     |      Uses current filter context")
                        lines.append("     v")
                        lines.append(f"[F] Evaluate measure: {measure_display}")

                # Show variables in scope if any (for debugging context)
                if transition.variables_in_scope and len(transition.variables_in_scope) > 0:
                    vars_display = ", ".join(transition.variables_in_scope[:3])
                    if len(transition.variables_in_scope) > 3:
                        vars_display += f", ... (+{len(transition.variables_in_scope) - 3} more)"
                    lines.append(f"     |  Available variables: {vars_display}")

                lines.append("")

            # Final result
            lines.append("     |")
            lines.append("     v")
            lines.append("[RESULT] Final calculation result")
            lines.append("")
            lines.append("=" * 80)

            # Add summary
            lines.append("SUMMARY:")
            lines.append(f"  Total transitions: {len(context_analysis.transitions)}")
            lines.append(f"  Complexity score: {context_analysis.complexity_score}/100")
            lines.append(f"  Max nesting level: {context_analysis.max_nesting_level}")

            if context_analysis.warnings:
                lines.append("")
                lines.append("WARNINGS:")
                for warning in context_analysis.warnings:
                    lines.append(f"  ⚠️  {warning.message}")

            lines.append("=" * 80)

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error generating ASCII diagram: {e}", exc_info=True)
            return f"Error generating diagram: {str(e)}"
