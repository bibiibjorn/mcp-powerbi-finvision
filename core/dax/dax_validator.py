"""
DAX Validation and Analysis - Enhanced

Advanced DAX syntax validation, complexity analysis, and optimization suggestions.
Based on tabular-mcp best practices.

2026-09-22 (review D-03): every lexical check runs on ``core.dax.tokenizer``
tokens. The previous character scanners treated ``'`` as a quote everywhere
(``[Men's Sales]`` and ``// don't`` were "unbalanced"), counted parens inside
comments and found "DEFINE" inside ``[Undefined Rate]`` / ``"Define scope"`` —
valid queries were refused before reaching the engine. The gate is now
deliberately permissive: the ONLY errors it reports are a missing closing
delimiter (string, quoted table, bracket name), unbalanced ``( )`` / ``{ }`` in
code, and DEFINE/EVALUATE keyword ordering. Everything else is left to the
engine, which is the real validator.
"""

import re
import logging
from typing import List, Dict, Any, Tuple

from core.dax.dax_names import quote_table
from core.dax.tokenizer import DaxLexer, Token, TokenType, TRIVIA_TYPES

logger = logging.getLogger(__name__)

_LEXER = DaxLexer()

# Token types whose text is a literal / name, never code.
_OPAQUE = frozenset(
    {
        TokenType.STRING,
        TokenType.DATETIME,
        TokenType.TABLE_REF,
        TokenType.COLUMN_REF,
        TokenType.QUALIFIED_REF,
        TokenType.COMMENT_LINE,
        TokenType.COMMENT_BLOCK,
    }
)

_DEFINITION_KEYWORDS = {"MEASURE", "VAR", "TABLE", "COLUMN", "FUNCTION", "CALCULATIONGROUP"}


def _code_tokens(text: str) -> List[Token]:
    return [t for t in _LEXER.tokenize(text or "") if t.type not in TRIVIA_TYPES]


def code_only_text(text: str) -> str:
    """Render *text* with comments removed and string/datetime literal CONTENTS
    blanked (``"abc"`` -> ``""``), so substring heuristics never match inside
    comments, format strings or labels. Names ([...], '...') are kept."""
    parts: List[str] = []
    for t in _LEXER.tokenize(text or ""):
        if t.type in (TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK):
            parts.append(" ")
        elif t.type in (TokenType.STRING, TokenType.DATETIME):
            parts.append('""')
        else:
            parts.append(t.value)
    return "".join(parts)


def has_non_constant_division(text: str) -> bool:
    """True when the code divides with ``/`` by something other than a numeric
    literal. ``[Sales] / 1000`` is the idiomatic form (SQLBI: use ``/`` when the
    denominator is a non-zero constant) and does not need DIVIDE."""
    code = _code_tokens(text)
    for i, t in enumerate(code):
        if t.type == TokenType.OPERATOR and t.value == "/":
            nxt = code[i + 1] if i + 1 < len(code) else None
            if nxt is None or nxt.type != TokenType.NUMBER:
                return True
            try:
                if float(nxt.value) == 0.0:
                    return True
            except ValueError:
                return True
    return False


class DaxValidator:
    """
    Advanced DAX validation with syntax checking, complexity analysis,
    and performance recommendations.
    """

    # Table expression patterns
    TABLE_FUNCTIONS = [
        "SELECTCOLUMNS",
        "ADDCOLUMNS",
        "SUMMARIZE",
        "FILTER",
        "VALUES",
        "ALL",
        "DISTINCT",
        "UNION",
        "INTERSECT",
        "EXCEPT",
        "CROSSJOIN",
        "NATURALINNERJOIN",
        "NATURALLEFTOUTERJOIN",
        "TOPN",
        "SAMPLE",
        "DATATABLE",
        "SUBSTITUTEWITHINDEX",
        "GROUPBY",
        "SUMMARIZECOLUMNS",
        "TREATAS",
        "CALCULATETABLE",
    ]

    @staticmethod
    def _normalize_whitespace_preserving_strings(text: str) -> str:
        """Collapse whitespace outside literals/names/comments; keep them intact."""
        if text is None:
            return ""
        out: List[str] = []
        for t in _LEXER.tokenize(text):
            if t.type in (TokenType.WHITESPACE, TokenType.NEWLINE):
                if out and out[-1] != " ":
                    out.append(" ")
                elif not out:
                    out.append(" ")
            elif t.type == TokenType.COMMENT_LINE:
                # A line comment must stay terminated when newlines collapse.
                out.append(t.value)
                out.append("\n")
            else:
                out.append(t.value)
        return "".join(out)

    @staticmethod
    def normalize_query(query: str) -> str:
        """Normalize line endings and whitespace (outside strings)."""
        if query is None:
            return ""
        # Standardize newlines
        q = re.sub(r"\r\n?|\n", "\n", str(query))
        q = DaxValidator._normalize_whitespace_preserving_strings(q)
        return q.strip()

    @staticmethod
    def validate_identifier_safety(identifier: str) -> bool:
        """Validate identifier for safety only — length/non-empty/no NUL byte.

        P2-33: renamed from `validate_identifier` to disambiguate from the
        *strict* pattern check `dax_utilities.validate_dax_identifier`. Both
        names still callable via the deprecated alias below; remove alias
        after one release.
        """
        return (
            bool(identifier)
            and len(identifier.strip()) > 0
            and len(identifier) <= 128
            and "\0" not in identifier
        )

    @staticmethod
    def validate_identifier(identifier: str) -> bool:
        """DEPRECATED alias — use validate_identifier_safety.

        Kept so external callers don't break; internal callers should migrate.
        """
        return DaxValidator.validate_identifier_safety(identifier)

    @staticmethod
    def escape_identifier(identifier: str) -> str:
        """
        Escape DAX identifier for safe use.

        Args:
            identifier: Identifier to escape

        Returns:
            Escaped identifier wrapped in quotes
        """
        if not DaxValidator.validate_identifier(identifier):
            raise ValueError(f"Invalid identifier: {identifier}")
        return quote_table(identifier)

    @staticmethod
    def check_balanced_delimiters(
        query: str, open_char: str, close_char: str, name: str
    ) -> List[str]:
        """Check balanced delimiters (parentheses, brackets, braces) in CODE only.

        ``( )`` and ``{ }`` are counted as code tokens — never inside strings,
        names or comments. ``[ ]`` delimit names, so the only bracket errors are
        a name whose closing ``]`` is missing and a stray ``]`` in code.
        """
        errors: List[str] = []
        tokens = _LEXER.tokenize(query or "")

        if open_char == "[" and close_char == "]":
            for t in tokens:
                if t.type in (TokenType.COLUMN_REF, TokenType.QUALIFIED_REF) and not t.terminated:
                    errors.append(
                        f"Unbalanced {name}: '[' not closed (name starting at position {t.start})"
                    )
                    return errors
                if t.type == TokenType.UNKNOWN and t.value == "]":
                    errors.append(f"Unbalanced {name}: extra ']' at position {t.start}")
                    return errors
            return errors

        pairs = {
            ("(", ")"): (TokenType.PAREN_OPEN, TokenType.PAREN_CLOSE),
            ("{", "}"): (TokenType.BRACE_OPEN, TokenType.BRACE_CLOSE),
        }
        typed = pairs.get((open_char, close_char))
        balance = 0
        for t in tokens:
            if typed is not None:
                is_open = t.type == typed[0]
                is_close = t.type == typed[1]
            else:
                if t.type in _OPAQUE:
                    continue
                is_open = open_char in t.value
                is_close = close_char in t.value
            if is_open:
                balance += 1
            elif is_close:
                balance -= 1
                if balance < 0:
                    errors.append(f"Unbalanced {name}: extra '{close_char}' at position {t.start}")
                    return errors

        if balance > 0:
            errors.append(f"Unbalanced {name}: {balance} '{open_char}' not closed")

        return errors

    @staticmethod
    def check_balanced_quotes(query: str) -> List[str]:
        """Report string literals / quoted table names without a closing quote.

        Apostrophes inside ``[...]`` names, string literals and comments are not
        quotes; ``''`` / ``""`` are escapes.
        """
        errors: List[str] = []
        double_open = False
        single_open = False
        for t in _LEXER.tokenize(query or ""):
            if t.terminated:
                continue
            if t.type in (TokenType.STRING, TokenType.DATETIME):
                double_open = True
            elif t.type == TokenType.TABLE_REF:
                single_open = True
        if double_open:
            errors.append("Unbalanced double quotes: string literal not closed")
        if single_open:
            errors.append("Unbalanced single quotes: identifier not closed")
        return errors

    @staticmethod
    def validate_query_syntax(query: str) -> List[str]:
        """
        Validate basic DAX syntax (lexical level only — the engine decides the rest).

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        if not query or not query.strip():
            errors.append("Query cannot be empty")
            return errors

        # Check balanced delimiters
        errors.extend(DaxValidator.check_balanced_delimiters(query, "(", ")", "parentheses"))
        errors.extend(DaxValidator.check_balanced_delimiters(query, "[", "]", "brackets"))
        errors.extend(DaxValidator.check_balanced_delimiters(query, "{", "}", "braces"))
        errors.extend(DaxValidator.check_balanced_quotes(query))

        return errors

    @staticmethod
    def validate_complete_dax_query(query: str) -> List[str]:
        """Validate higher-level DAX query structure (DEFINE/EVALUATE ordering, single DEFINE, etc.).

        DEFINE / EVALUATE are recognised as KEYWORD tokens only — never inside
        names (``[Undefined Rate]``), strings (``"Define scope"``) or comments.
        """
        errors: List[str] = []
        if not query or not query.strip():
            return ["Query cannot be empty"]

        code = _code_tokens(query)
        define_idx = [
            i
            for i, t in enumerate(code)
            if t.type == TokenType.KEYWORD and t.value.upper() == "DEFINE"
        ]
        eval_idx = [
            i
            for i, t in enumerate(code)
            if t.type == TokenType.KEYWORD and t.value.upper() == "EVALUATE"
        ]

        if define_idx:
            if not eval_idx:
                errors.append("DAX query must contain at least one EVALUATE statement.")
            elif define_idx[0] > eval_idx[0]:
                errors.append("DEFINE statement must come before any EVALUATE statement.")

            if len(define_idx) > 1:
                errors.append("Only one DEFINE block is allowed in a DAX query.")

            first = define_idx[0]
            nxt = code[first + 1] if first + 1 < len(code) else None
            if nxt is None or (nxt.type == TokenType.KEYWORD and nxt.value.upper() == "EVALUATE"):
                errors.append(
                    "DEFINE block must contain at least one definition (MEASURE, VAR, TABLE, COLUMN or FUNCTION)."
                )
            elif not (
                nxt.value.upper() in _DEFINITION_KEYWORDS
                and nxt.type in (TokenType.KEYWORD, TokenType.IDENTIFIER)
            ):
                errors.append(
                    "DEFINE block must contain at least one valid definition (MEASURE, VAR, TABLE, COLUMN or FUNCTION)."
                )

        errors.extend(DaxValidator.check_balanced_delimiters(query, "(", ")", "parentheses"))
        errors.extend(DaxValidator.check_balanced_delimiters(query, "[", "]", "brackets"))
        errors.extend(DaxValidator.check_balanced_quotes(query))

        return errors

    @staticmethod
    def is_table_expression(query: str) -> bool:
        """Determine if expression is table or scalar (first code token decides)."""
        if not query:
            return False

        code = _code_tokens(query)
        if not code:
            return False
        first = code[0]

        # Bare quoted table reference
        if first.type == TokenType.TABLE_REF and len(code) == 1:
            return True

        # Table constructor { ... }
        if first.type == TokenType.BRACE_OPEN:
            return True

        if first.type in (TokenType.IDENTIFIER, TokenType.KEYWORD, TokenType.FUNCTION):
            return first.value.upper() in DaxValidator.TABLE_FUNCTIONS

        return False

    @staticmethod
    def analyze_complexity(expression: str) -> Dict[str, Any]:
        """
        Analyze DAX expression complexity.

        Returns:
            Complexity metrics and rating
        """
        if not expression:
            return {"complexity_score": 0, "level": "None"}

        code = _code_tokens(expression)
        calls = [
            t.value.upper()
            for i, t in enumerate(code[:-1])
            if t.type in (TokenType.FUNCTION, TokenType.IDENTIFIER, TokenType.KEYWORD)
            and code[i + 1].type == TokenType.PAREN_OPEN
        ]
        function_count = len(calls)

        # Count nesting level
        max_nesting = DaxValidator._count_max_nesting(expression)

        # Count specific patterns
        filter_count = sum(1 for c in calls if c == "FILTER")
        calculate_count = sum(1 for c in calls if c == "CALCULATE")

        # Calculate complexity score
        complexity_score = (
            (function_count * 2) + (max_nesting * 3) + (filter_count * 4) + (calculate_count * 2)
        )

        # Determine level
        if complexity_score <= 5:
            level = "Low"
        elif complexity_score <= 15:
            level = "Medium"
        elif complexity_score <= 30:
            level = "High"
        else:
            level = "Very High"

        return {
            "complexity_score": complexity_score,
            "level": level,
            "function_count": function_count,
            "max_nesting_level": max_nesting,
            "filter_count": filter_count,
            "calculate_count": calculate_count,
            "expression_length": len(expression),
        }

    @staticmethod
    def _count_max_nesting(expression: str) -> int:
        """Count maximum nesting level of parentheses (code only)."""
        max_level = 0
        current_level = 0
        for t in _code_tokens(expression):
            if t.type == TokenType.PAREN_OPEN:
                current_level += 1
                max_level = max(max_level, current_level)
            elif t.type == TokenType.PAREN_CLOSE:
                current_level -= 1
        return max_level

    @staticmethod
    def analyze_patterns(expression: str) -> Tuple[List[str], List[str]]:
        """
        Analyze DAX for anti-patterns and provide recommendations.

        Heuristics run on ``code_only_text`` so comments, labels and format
        strings never trigger them.

        Returns:
            Tuple of (warnings, recommendations)
        """
        warnings = []
        recommendations = []

        if not expression:
            return warnings, recommendations

        raw = expression
        expression = code_only_text(expression)
        expr_upper = expression.upper()

        # Check for anti-patterns
        if re.search(r"\bSUMX\s*\(\s*FILTER\s*\(", expression, re.IGNORECASE):
            warnings.append(
                "SUMX(FILTER(...)) detected - filtering a whole table is only slower than a "
                "column predicate in CALCULATE when the predicate is on one column"
            )

        if re.search(r"CALCULATE\s*\(\s*CALCULATE", expression, re.IGNORECASE):
            warnings.append("Nested CALCULATE functions detected - may cause unexpected results")

        calculate_count = len(re.findall(r"\bCALCULATE\s*\(", expression, re.IGNORECASE))
        if calculate_count > 3:
            warnings.append(
                f"High number of CALCULATE functions ({calculate_count}) - consider simplifying"
            )

        # Recommendations
        if re.search(r"\bSUM\s*\(", expression, re.IGNORECASE) and "CALCULATE" not in expr_upper:
            recommendations.append("Consider using CALCULATE with filters for more flexibility")

        if len(raw) > 500:
            recommendations.append("Long expression - consider breaking into multiple measures")

        if re.search(r"\bFORMAT\s*\(", expression, re.IGNORECASE):
            recommendations.append(
                "Avoid FORMAT() in a measure body — it returns text and breaks "
                "sorting/aggregation. Set the measure's Format String property (or "
                "a dynamic format string) instead."
            )

        # Prefer DIVIDE over / for safe division — not needed for a constant denominator.
        if has_non_constant_division(raw) and not re.search(
            r"\bDIVIDE\s*\(", expression, re.IGNORECASE
        ):
            recommendations.append(
                "Use DIVIDE(x, y) instead of x / y to avoid division-by-zero and BLANK handling issues"
            )

        # SUMMARIZE vs SUMMARIZECOLUMNS guidance
        if re.search(r"\bSUMMARIZE\s*\(", expression, re.IGNORECASE):
            recommendations.append(
                "Consider SUMMARIZECOLUMNS instead of SUMMARIZE for query patterns to avoid context transition surprises"
            )

        # Encourage variables for repeated expressions
        if len(re.findall(r"\bSUM\s*\(", expression, re.IGNORECASE)) >= 2 and not re.search(
            r"\bVAR\b", expr_upper
        ):
            recommendations.append(
                "Use VAR to store repeated expressions and reference them to reduce recomputation"
            )

        # Excessive iterator usage
        if (
            len(re.findall(r"\b(SUMX|AVERAGEX|COUNTX|MAXX|MINX)\s*\(", expression, re.IGNORECASE))
            > 2
        ):
            warnings.append(
                "Multiple iterator functions detected - ensure they are necessary and consider pre-aggregations"
            )

        return warnings, recommendations

    @staticmethod
    def generate_optimization_suggestions(query: str) -> List[str]:
        """Generate optimization suggestions for query (code only — see analyze_patterns)."""
        suggestions = []

        raw = query or ""
        query = code_only_text(raw)
        query_upper = query.upper()

        if re.search(r"\bSUMX\s*\(\s*FILTER\s*\(", query, re.IGNORECASE):
            suggestions.append(
                "SUMX(FILTER(T, T[col] = x), ...) with a single-column predicate can be "
                "CALCULATE(SUMX(T, ...), T[col] = x)"
            )

        if len(re.findall(r"\bCALCULATE\s*\(", query, re.IGNORECASE)) > 2:
            suggestions.append("Consolidate multiple CALCULATE functions to reduce complexity")

        if re.search(r"\bALL\s*\(", query, re.IGNORECASE) and "CALCULATE" not in query_upper:
            suggestions.append("Using ALL() without CALCULATE may not provide expected results")

        if len(raw) > 1000:
            suggestions.append("Break down large query into smaller, manageable parts")

        iterator_count = len(
            re.findall(r"\b(SUMX|AVERAGEX|COUNTX|MAXX|MINX)\s*\(", query, re.IGNORECASE)
        )
        if iterator_count > 2:
            suggestions.append("Multiple iterator functions detected - ensure they are necessary")

        # Prefer SUMMARIZECOLUMNS over SUMMARIZE in query context
        if re.search(r"\bSUMMARIZE\s*\(", query, re.IGNORECASE):
            suggestions.append(
                "Consider SUMMARIZECOLUMNS instead of SUMMARIZE for queries to avoid context transition issues"
            )

        # Encourage use of KEEPFILTERS when stacking additional filters in CALCULATE
        if (
            re.search(r"\bCALCULATE\s*\(", query, re.IGNORECASE)
            and re.search(r"\bFILTER\s*\(", query, re.IGNORECASE)
            and "KEEPFILTERS" not in query_upper
        ):
            suggestions.append(
                "When adding filters in CALCULATE, consider KEEPFILTERS to preserve existing filter context"
            )

        # Warn about ALLSELECTED misuse
        if (
            re.search(r"\bALLSELECTED\s*\(", query, re.IGNORECASE)
            and "CALCULATE" not in query_upper
        ):
            suggestions.append(
                "ALLSELECTED is typically used within CALCULATE; verify the intended filter context behavior"
            )

        # DMV best practice: materialize before projection
        if (
            re.search(r"\$SYSTEM\.", query, re.IGNORECASE)
            and re.search(r"\bSELECTCOLUMNS\b", query, re.IGNORECASE)
            and not re.search(r"\bTOPN\s*\(", query, re.IGNORECASE)
        ):
            suggestions.append(
                "When querying $SYSTEM DMVs with SELECTCOLUMNS, wrap the source in TOPN(...) to ensure proper materialization"
            )

        return suggestions
