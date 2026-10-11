"""
Call Tree Builder - Hierarchical DAX expression breakdown

Provides:
- Parent-child DAX expression hierarchy
- Context transitions at each node
- Iteration and row context tracking
- Navigation of subexpressions
"""

import logging
import re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

from core.dax.dax_utilities import (
    normalize_dax,
    find_matching_paren,
)

logger = logging.getLogger(__name__)


class NodeType(Enum):
    """Type of call tree node"""

    ROOT = "root"
    CALCULATE = "calculate"
    ITERATOR = "iterator"
    MEASURE_REF = "measure_ref"
    FUNCTION = "function"
    COLUMN_REF = "column_ref"
    FILTER = "filter"
    VARIABLE = "variable"
    LITERAL = "literal"


@dataclass
class CallTreeNode:
    """Node in the call tree"""

    node_id: int
    node_type: NodeType
    expression: str
    function_name: Optional[str] = None
    parent_id: Optional[int] = None
    children: List["CallTreeNode"] = field(default_factory=list)

    # Context information
    has_context_transition: bool = False
    is_iterator: bool = False
    estimated_iterations: Optional[int] = None
    row_context_active: bool = False

    # Performance metadata
    performance_impact: str = "low"  # "low", "medium", "high", "critical"
    warning_message: Optional[str] = None

    # Position in original DAX
    start_pos: int = 0
    end_pos: int = 0

    def add_child(self, child: "CallTreeNode"):
        """Add a child node"""
        child.parent_id = self.node_id
        self.children.append(child)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "expression": (
                self.expression[:100] + "..." if len(self.expression) > 100 else self.expression
            ),
            "function_name": self.function_name,
            "parent_id": self.parent_id,
            "has_context_transition": self.has_context_transition,
            "is_iterator": self.is_iterator,
            "estimated_iterations": self.estimated_iterations,
            "row_context_active": self.row_context_active,
            "performance_impact": self.performance_impact,
            "warning_message": self.warning_message,
            "children": [child.to_dict() for child in self.children],
        }


class CallTreeBuilder:
    """
    Build hierarchical call tree from DAX expression

    Features:
    - Parse DAX into tree structure
    - Identify context transitions at each node
    - Track iterators and row contexts
    - Estimate performance impact
    """

    # Functions that create context transitions
    CALCULATE_FUNCTIONS = {"CALCULATE", "CALCULATETABLE"}

    # Iterator functions
    ITERATOR_FUNCTIONS = {
        "SUMX",
        "AVERAGEX",
        "MINX",
        "MAXX",
        "COUNTX",
        "FILTER",
        "ADDCOLUMNS",
        "SELECTCOLUMNS",
        "RANKX",
        "CONCATENATEX",
        "PRODUCTX",
        "STDEVX.S",
        "STDEVX.P",
        "VARX.S",
        "VARX.P",
        "TOPN",
        "SAMPLE",
    }

    # Aggregation functions
    AGGREGATION_FUNCTIONS = {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "DISTINCTCOUNT", "COUNTROWS"}

    def __init__(self, vertipaq_analyzer=None):
        """
        Initialize call tree builder

        Args:
            vertipaq_analyzer: Optional VertiPaq analyzer for cardinality data
        """
        self.vertipaq_analyzer = vertipaq_analyzer
        self._node_counter = 0

    def build_call_tree(self, dax_expression: str) -> CallTreeNode:
        """
        Build call tree from DAX expression

        Args:
            dax_expression: DAX expression to parse

        Returns:
            Root CallTreeNode
        """
        try:
            self._node_counter = 0

            # Normalize expression
            normalized = self._normalize_dax(dax_expression)

            # Create root node
            root = CallTreeNode(
                node_id=self._next_id(),
                node_type=NodeType.ROOT,
                expression=normalized,
                start_pos=0,
                end_pos=len(normalized),
            )

            # Parse the expression recursively. E-15: parsing runs on a copy
            # whose string literals are blanked (same length), so a "(" or
            # "[x]" inside a string is never a call / reference; the displayed
            # expressions come from the unmasked text at the same offsets.
            self._display = normalized
            self._parse_expression(self._mask_strings(normalized), root, 0, len(normalized))

            # Analyze context transitions
            self._analyze_context_transitions(root)

            # Estimate iterations using VertiPaq data
            if self.vertipaq_analyzer:
                self._estimate_iterations(root, dax_expression)

            # Assess performance impact
            self._assess_performance_impact(root)

            logger.info(f"Built call tree with {self._node_counter} nodes")

            return root

        except Exception as e:
            logger.error(f"Error building call tree: {e}", exc_info=True)
            # Return minimal root node on error
            return CallTreeNode(
                node_id=0,
                node_type=NodeType.ROOT,
                expression=dax_expression,
                warning_message=f"Parse error: {str(e)}",
            )

    def _normalize_dax(self, dax: str) -> str:
        """Normalize DAX expression"""
        return normalize_dax(dax).strip()

    def _next_id(self) -> int:
        """Get next node ID"""
        self._node_counter += 1
        return self._node_counter

    @staticmethod
    def _mask_strings(dax: str) -> str:
        """Blank the inside of every string literal (length preserved)."""
        try:
            from core.dax.tokenizer import DaxLexer, TokenType

            out = []
            for t in DaxLexer().tokenize(dax):
                if t.type == TokenType.STRING and len(t.value) >= 2:
                    out.append('"' + " " * (len(t.value) - 2) + '"')
                else:
                    out.append(t.value)
            masked = "".join(out)
            return masked if len(masked) == len(dax) else dax
        except Exception:  # noqa: BLE001
            return dax

    def _text(self, dax: str, a: int, b: int) -> str:
        disp = getattr(self, "_display", None)
        src = disp if isinstance(disp, str) and len(disp) == len(dax) else dax
        return src[a:b]

    def _parse_expression(self, dax: str, parent_node: CallTreeNode, start: int, end: int) -> None:
        """Parse ``dax[start:end]`` into child nodes of *parent_node*.

        E-15: every level only handles its TOP-LEVEL calls — a call that starts
        inside an already-consumed call span is left to the recursion into that
        call's arguments (previously every nested call was created again at
        every ancestor level: ~4^depth nodes, 18 s for an 18-deep IF chain).
        Measure references and VARs inside a call span are skipped for the same
        reason, so the tree is linear in the input.
        """
        raw = dax[start:end]
        lead = len(raw) - len(raw.lstrip())
        start += lead
        expr = raw.strip()

        if not expr:
            return

        # Top-level function calls (skip calls nested in a consumed span).
        func_pattern = r"\b([A-Z_][A-Z0-9_\.]*)\s*\("
        spans: List[Tuple[int, int, str, int]] = []  # (start, end_incl, name, open_paren)
        consumed_end = -1
        for match in re.finditer(func_pattern, expr, re.IGNORECASE):
            func_start = match.start()
            if func_start <= consumed_end:
                continue
            func_open_paren = match.end() - 1
            func_end = self._find_matching_paren(expr, func_open_paren)
            if func_end == -1:
                continue
            spans.append((func_start, func_end, match.group(1).upper(), func_open_paren))
            consumed_end = func_end

        def inside_call(pos: int) -> bool:
            return any(s0 <= pos <= s1 for s0, s1, _n, _p in spans)

        # VAR statements of THIS level (not inside a call's arguments)
        var_pattern = r"\bVAR\s+(\w+)\s*="
        var_matches = [
            m for m in re.finditer(var_pattern, expr, re.IGNORECASE) if not inside_call(m.start())
        ]

        if var_matches:
            return_match = None
            for rm in re.finditer(r"\bRETURN\b", expr, re.IGNORECASE):
                if rm.start() > var_matches[-1].start() and not inside_call(rm.start()):
                    return_match = rm
                    break
            for i, match in enumerate(var_matches):
                var_name = match.group(1)
                var_start = match.start()
                if i + 1 < len(var_matches):
                    var_end = var_matches[i + 1].start()
                elif return_match is not None:
                    var_end = return_match.start()
                else:
                    var_end = len(expr)

                var_value_start = match.end()
                var_value = self._text(dax, start + var_value_start, start + var_end).strip()
                if len(var_value) > 50:
                    var_value = var_value[:50] + "..."

                var_node = CallTreeNode(
                    node_id=self._next_id(),
                    node_type=NodeType.VARIABLE,
                    expression=f"VAR {var_name} = {var_value}",
                    function_name=f"VAR {var_name}",  # Show actual variable name
                    start_pos=start + var_start,
                    end_pos=start + var_end,
                )
                parent_node.add_child(var_node)
                self._parse_expression(dax, var_node, start + var_value_start, start + var_end)

            if return_match is not None:
                self._parse_expression(dax, parent_node, start + return_match.end(), end)
            return

        for func_start, func_end, func_name, func_open_paren in spans:
            if func_name in self.CALCULATE_FUNCTIONS:
                node_type = NodeType.CALCULATE
            elif func_name in self.ITERATOR_FUNCTIONS:
                node_type = NodeType.ITERATOR
            else:
                node_type = NodeType.FUNCTION

            func_node = CallTreeNode(
                node_id=self._next_id(),
                node_type=node_type,
                expression=self._text(dax, start + func_start, start + func_end + 1),
                function_name=func_name,
                start_pos=start + func_start,
                end_pos=start + func_end + 1,
                has_context_transition=(func_name in self.CALCULATE_FUNCTIONS),
                is_iterator=(func_name in self.ITERATOR_FUNCTIONS),
            )
            parent_node.add_child(func_node)
            self._parse_function_args(
                dax, func_node, start + func_open_paren + 1, expr[func_open_paren + 1 : func_end]
            )

        # Measure references of this level (not inside a call handled above)
        measure_pattern = r"\[((?:[^\]]|\]\])+)\]"
        for match in re.finditer(measure_pattern, expr):
            if inside_call(match.start()):
                continue
            context_before = expr[max(0, match.start() - 20) : match.start()]
            if re.search(r"(?:'[^']+'|\w+)\s*$", context_before):
                continue  # table-qualified -> column (or Home[measure])
            measure_name = match.group(1)
            measure_node = CallTreeNode(
                node_id=self._next_id(),
                node_type=NodeType.MEASURE_REF,
                expression=f"[{measure_name}]",
                function_name=f"[{measure_name}]",  # Show actual measure name
                start_pos=start + match.start(),
                end_pos=start + match.end(),
                has_context_transition=True,  # Implicit CALCULATE
            )
            parent_node.add_child(measure_node)

    def _parse_function_args(
        self, dax: str, parent_node: CallTreeNode, start: int, args_expr: str
    ) -> None:
        """Parse function arguments (offset-exact: a repeated argument no longer
        resolves to the first occurrence's position)."""
        for arg_start, arg_end in self._split_by_comma_spans(args_expr):
            self._parse_expression(dax, parent_node, start + arg_start, start + arg_end)

    @staticmethod
    def _split_by_comma_spans(expr: str) -> List[Tuple[int, int]]:
        """(start, end) spans of the depth-0 comma-separated parts of *expr*
        (strings are already masked; ``[...]`` names are skipped)."""
        spans: List[Tuple[int, int]] = []
        depth = 0
        seg = 0
        in_bracket = False
        for i, ch in enumerate(expr):
            if in_bracket:
                if ch == "]":
                    in_bracket = False
                continue
            if ch == "[":
                in_bracket = True
            elif ch in "({":
                depth += 1
            elif ch in ")}":
                depth -= 1
            elif ch == "," and depth == 0:
                spans.append((seg, i))
                seg = i + 1
        spans.append((seg, len(expr)))
        return [(a, b) for a, b in spans if expr[a:b].strip()]

    def _find_matching_paren(self, expr: str, open_pos: int) -> int:
        """Find matching closing parenthesis (string-literal-aware)"""
        return find_matching_paren(expr, open_pos)

    def _analyze_context_transitions(self, node: CallTreeNode) -> None:
        """Analyze context transitions in tree"""
        # Process children first (bottom-up)
        for child in node.children:
            self._analyze_context_transitions(child)

        # Check if this node creates a context transition
        if node.node_type == NodeType.CALCULATE:
            node.has_context_transition = True

        # Check if iterator contains measure references
        if node.node_type == NodeType.ITERATOR:
            has_measure_refs = self._has_measure_references(node)
            if has_measure_refs:
                node.warning_message = (
                    f"{node.function_name} iterator contains measure references. "
                    f"This causes context transition in EACH iteration."
                )

    def _has_measure_references(self, node: CallTreeNode) -> bool:
        """Check if node tree contains measure references"""
        if node.node_type == NodeType.MEASURE_REF:
            return True

        for child in node.children:
            if self._has_measure_references(child):
                return True

        return False

    def _estimate_iterations(self, node: CallTreeNode, original_dax: str) -> None:
        """Estimate iteration count using VertiPaq analyzer"""
        if not self.vertipaq_analyzer:
            return

        # Process children first
        for child in node.children:
            self._estimate_iterations(child, original_dax)

        # Estimate for iterator nodes
        if node.node_type == NodeType.ITERATOR and node.function_name:
            # Extract table reference from iterator
            # Pattern: SUMX(TableName, ...)
            table_pattern = r"\w+\s*\(\s*([^,\(\)]+)"
            match = re.search(table_pattern, node.expression)

            if match:
                table_ref = match.group(1).strip()

                # Try to get cardinality
                # First, check if it's a simple table reference
                if "[" not in table_ref:
                    # It's a table name, get row count
                    # We'd need to query COUNTROWS via DMV or estimate
                    # For now, mark as unknown
                    node.estimated_iterations = None
                else:
                    # It's an expression, try to extract columns
                    column_analysis = self.vertipaq_analyzer.analyze_dax_columns(table_ref)
                    if column_analysis.get("success"):
                        # Use highest cardinality as estimate
                        max_card = 0
                        for col_data in column_analysis.get("column_analysis", {}).values():
                            if isinstance(col_data, dict):
                                card = col_data.get("cardinality", 0)
                                max_card = max(max_card, card)

                        if max_card > 0:
                            node.estimated_iterations = max_card

    def _assess_performance_impact(self, node: CallTreeNode) -> None:
        """Assess performance impact of node"""
        # Process children first
        for child in node.children:
            self._assess_performance_impact(child)

        # Assess based on node type and metrics
        if node.node_type == NodeType.ITERATOR:
            if node.estimated_iterations:
                if node.estimated_iterations >= 1_000_000:
                    node.performance_impact = "critical"
                    if not node.warning_message:
                        node.warning_message = ""
                    node.warning_message += (
                        f" CRITICAL: Estimated {node.estimated_iterations:,} iterations!"
                    )
                elif node.estimated_iterations >= 100_000:
                    node.performance_impact = "high"
                    if not node.warning_message:
                        node.warning_message = ""
                    node.warning_message += (
                        f" HIGH: Estimated {node.estimated_iterations:,} iterations."
                    )
                elif node.estimated_iterations >= 10_000:
                    node.performance_impact = "medium"

            # Check if iterator has measure references
            if self._has_measure_references(node):
                # Upgrade impact level
                if node.performance_impact == "low":
                    node.performance_impact = "medium"
                elif node.performance_impact == "medium":
                    node.performance_impact = "high"

        elif node.node_type == NodeType.CALCULATE:
            # Check nesting depth
            depth = self._get_nesting_depth(node)
            if depth > 3:
                node.performance_impact = "medium"
                node.warning_message = f"CALCULATE nesting depth: {depth}"

    def _get_nesting_depth(self, node: CallTreeNode) -> int:
        """Get nesting depth of CALCULATE nodes"""
        if not node.children:
            return 0

        max_depth = 0
        for child in node.children:
            child_depth = self._get_nesting_depth(child)
            if child.node_type == NodeType.CALCULATE:
                child_depth += 1
            max_depth = max(max_depth, child_depth)

        return max_depth

    def visualize_tree(
        self, node: CallTreeNode, indent: int = 0, prefix: str = "", is_last: bool = True
    ) -> str:
        """
        Generate enhanced ASCII visualization of call tree with box-drawing characters

        Args:
            node: Root node
            indent: Current indentation level
            prefix: Accumulated prefix for tree lines
            is_last: Whether this is the last sibling

        Returns:
            ASCII tree string with proper hierarchy visualization
        """
        lines = []

        # Root node handling
        if indent == 0:
            # Add tree summary header
            stats = self._collect_tree_stats(node)
            lines.append("┌" + "─" * 60 + "┐")
            lines.append(f"│ 📊 DAX Call Tree Analysis{' ' * 34}│")
            lines.append("├" + "─" * 60 + "┤")
            lines.append(
                f"│ Functions: {stats['total_functions']:<5} │ Variables: {stats['total_variables']:<5} │ Measures: {stats['total_measures']:<5} │"
            )
            lines.append(
                f"│ Iterators: {stats['total_iterators']:<5} │ CALCULATE: {stats['total_calculates']:<5} │ Depth: {stats['max_depth']:<7} │"
            )
            if stats["total_iterations"] > 0:
                iter_str = f"{stats['total_iterations']:,}"
                impact = (
                    "🔴 CRITICAL"
                    if stats["total_iterations"] >= 1_000_000
                    else "🟡 HIGH" if stats["total_iterations"] >= 100_000 else "🟢 OK"
                )
                lines.append(f"│ Est. Iterations: {iter_str:<15} │ Impact: {impact:<17}│")
            lines.append("└" + "─" * 60 + "┘")
            lines.append("")

            # Root node
            node_repr = self._format_node_enhanced(node)
            lines.append(f"📁 {node_repr}")
            current_prefix = "   "
        else:
            # Branch characters
            branch = "└── " if is_last else "├── "
            node_repr = self._format_node_enhanced(node)
            lines.append(f"{prefix}{branch}{node_repr}")

            # Update prefix for children
            current_prefix = prefix + ("    " if is_last else "│   ")

        # Add children with proper tree structure
        child_count = len(node.children)
        for i, child in enumerate(node.children):
            is_last_child = i == child_count - 1
            child_lines = self.visualize_tree(child, indent + 1, current_prefix, is_last_child)
            lines.append(child_lines)

        # Add legend at the end of root
        if indent == 0:
            lines.append("")
            lines.append("─" * 62)
            lines.append("Legend:")
            lines.append("  📦 VAR      Variable definition")
            lines.append("  ⚡ CALCULATE Context modifier (filter context change)")
            lines.append("  🔄 Iterator  Row-by-row iteration (SUMX, FILTER, etc.)")
            lines.append("  📊 Measure   Measure reference (implicit CALCULATE)")
            lines.append("  ⚙️  Function  DAX function call")
            lines.append("  🔍 Filter    Filter expression")
            lines.append("")
            lines.append("Performance Indicators:")
            lines.append("  🟢 Low impact    🟡 Medium impact    🔴 High/Critical")
            lines.append("─" * 62)

        return "\n".join(lines)

    def _collect_tree_stats(self, node: CallTreeNode) -> Dict[str, Any]:
        """Collect statistics about the tree for the summary header"""
        stats = {
            "total_functions": 0,
            "total_variables": 0,
            "total_measures": 0,
            "total_iterators": 0,
            "total_calculates": 0,
            "total_iterations": 0,
            "max_depth": 0,
        }

        def traverse(n: CallTreeNode, depth: int = 0):
            stats["max_depth"] = max(stats["max_depth"], depth)

            if n.node_type == NodeType.FUNCTION:
                stats["total_functions"] += 1
            elif n.node_type == NodeType.VARIABLE:
                stats["total_variables"] += 1
            elif n.node_type == NodeType.MEASURE_REF:
                stats["total_measures"] += 1
            elif n.node_type == NodeType.ITERATOR:
                stats["total_iterators"] += 1
                stats["total_functions"] += 1
                if n.estimated_iterations:
                    stats["total_iterations"] += n.estimated_iterations
            elif n.node_type == NodeType.CALCULATE:
                stats["total_calculates"] += 1
                stats["total_functions"] += 1

            for child in n.children:
                traverse(child, depth + 1)

        traverse(node)
        return stats

    def _format_node_enhanced(self, node: CallTreeNode) -> str:
        """Format node with enhanced visual representation"""
        parts = []

        # Add icon based on node type
        icon_map = {
            NodeType.VARIABLE: "📦",
            NodeType.MEASURE_REF: "📊",
            NodeType.CALCULATE: "⚡",
            NodeType.ITERATOR: "🔄",
            NodeType.FILTER: "🔍",
            NodeType.FUNCTION: "⚙️",
            NodeType.ROOT: "📁",
            NodeType.COLUMN_REF: "📋",
            NodeType.LITERAL: "💎",
        }
        icon = icon_map.get(node.node_type, "")

        # Build node label
        if node.function_name:
            label = node.function_name
        else:
            label = node.node_type.value

        # Add performance indicator
        perf_indicator = ""
        if node.performance_impact == "critical":
            perf_indicator = " 🔴"
        elif node.performance_impact == "high":
            perf_indicator = " 🟡"
        elif node.is_iterator or node.has_context_transition:
            perf_indicator = " 🟢"

        # Build main part
        parts.append(f"{icon} {label}{perf_indicator}")

        # Add context transition marker
        if node.has_context_transition and node.node_type != NodeType.CALCULATE:
            parts.append("⟵ context transition")

        # Add iteration count if available
        if node.is_iterator and node.estimated_iterations:
            iter_count = f"{node.estimated_iterations:,}"
            parts.append(f"({iter_count} iterations)")

        # Add warning if present (shortened)
        if node.warning_message:
            # Extract just the key warning
            warning_short = node.warning_message.split(".")[0][:40]
            if len(node.warning_message) > 40:
                warning_short += "..."
            parts.append(f"⚠️ {warning_short}")

        return " ".join(parts)
