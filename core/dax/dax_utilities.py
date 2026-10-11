"""
DAX Utilities - Shared helper functions for DAX parsing and analysis

Provides common utilities used across context_analyzer, call_tree_builder,
and other DAX analysis modules. This is a leaf dependency with no imports
from other core.dax modules.

Functions:
- normalize_dax: Remove comments and normalize whitespace
- extract_function_body: Extract function body with string-literal-aware
  parenthesis matching
- find_matching_paren: Find matching closing parenthesis with string
  literal awareness
- extract_variables: Extract VAR variable definitions from DAX
- get_line_column: Convert character position to line/column
- validate_dax_identifier: Validate DAX identifier names
"""

import re
from typing import Dict, Tuple

# --- Pre-compiled regex patterns ---

# Single-line comment: // to end of line
_SINGLE_LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)

# Multi-line comment: /* ... */
_MULTI_LINE_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# VAR declaration: VAR VarName =
_VAR_PATTERN_RE = re.compile(r"\bVAR\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*", re.IGNORECASE)

# Next VAR keyword (for variable boundary detection)
_NEXT_VAR_RE = re.compile(r"\bVAR\s+", re.IGNORECASE)

# RETURN keyword (for variable boundary detection)
_RETURN_RE = re.compile(r"\bRETURN\b", re.IGNORECASE)

# DAX identifier validation
_DAX_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def normalize_dax(dax: str) -> str:
    """
    Normalize DAX expression by removing comments and extra whitespace.

    Removes single-line (//) and multi-line (/* */) comments.

    Args:
        dax: Raw DAX expression

    Returns:
        DAX expression with comments removed
    """
    try:
        from core.dax.tokenizer import DaxLexer, TokenType

        lexer = DaxLexer()
        tokens = lexer.tokenize(dax)
        # Join all non-comment tokens, preserving whitespace and structure
        return "".join(
            t.value
            for t in tokens
            if t.type not in (TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK)
        )
    except Exception:
        # Fallback to original regex implementation
        result = _SINGLE_LINE_COMMENT_RE.sub("", dax)
        result = _MULTI_LINE_COMMENT_RE.sub("", result)
        return result


def _skip_opaque(text: str, pos: int) -> int:
    """If *pos* starts a DAX literal / name / comment, return the offset just
    past it; otherwise return *pos* unchanged.

    Handles ``"..."`` (``""`` escape), ``'...'`` (``''`` escape), ``[...]``
    (``]]`` escape — so ``[Men's Sales]`` and ``[Rate )]`` are opaque),
    ``// …`` / ``-- …`` line comments and ``/* … */`` blocks. An unterminated
    construct runs to the end of *text*.
    """
    length = len(text)
    ch = text[pos]
    nxt = text[pos + 1] if pos + 1 < length else ""
    if ch in ('"', "'", "["):
        close = "]" if ch == "[" else ch
        i = pos + 1
        while True:
            j = text.find(close, i)
            if j == -1:
                return length
            if j + 1 < length and text[j + 1] == close:
                i = j + 2
                continue
            return j + 1
    if (ch == "/" and nxt == "/") or (ch == "-" and nxt == "-"):
        j = pos
        while j < length and text[j] not in "\r\n":
            j += 1
        return j
    if ch == "/" and nxt == "*":
        j = text.find("*/", pos + 2)
        return length if j == -1 else j + 2
    return pos


def _scan_close_paren(text: str, pos: int, depth: int) -> int:
    """Return the index of the ``)`` that brings *depth* to 0, or -1."""
    length = len(text)
    while pos < length:
        ch = text[pos]
        if ch in ('"', "'", "[", "/", "-"):
            skipped = _skip_opaque(text, pos)
            if skipped != pos:
                pos = skipped
                continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    return -1


def extract_function_body(dax: str, start: int) -> str:
    """
    Extract function body from opening paren position using literal-,
    name- and comment-aware parenthesis matching.

    The caller should pass `start` as the position immediately AFTER the
    opening parenthesis (i.e., the first character inside the function
    body). The function returns the body text excluding the closing
    parenthesis.

    Parentheses inside string literals, quoted table names, ``[...]`` names
    (``[Rate )]``, ``[Men's Sales]``) and comments do not count.

    Args:
        dax: Full DAX expression
        start: Position right after the opening parenthesis

    Returns:
        Function body text (without the closing paren); everything to the end
        of the text when unmatched
    """
    close = _scan_close_paren(dax, start, 1)
    if close == -1:
        return dax[start:]
    return dax[start:close]


def find_matching_paren(expr: str, open_pos: int) -> int:
    """
    Find matching closing parenthesis with literal / name / comment awareness.

    Args:
        expr: Expression string
        open_pos: Position of the opening parenthesis

    Returns:
        Position of matching closing parenthesis, or -1 if not found
    """
    return _scan_close_paren(expr, open_pos + 1, 1)


def extract_variables(dax: str) -> Dict[str, str]:
    """
    Extract VAR variable definitions from a DAX expression.

    Args:
        dax: DAX expression (should be normalized first)

    Returns:
        Dict mapping variable names to their definitions (truncated
        to 100 chars)
    """
    try:
        return _extract_variables_tokenizer(dax)
    except Exception:
        return _extract_variables_regex(dax)


def _extract_variables_tokenizer(dax: str) -> Dict[str, str]:
    """Token-based VAR extraction — primary path."""
    from core.dax.tokenizer import DaxLexer, TokenType

    lexer = DaxLexer()
    tokens = lexer.tokenize_code(dax)
    variables: Dict[str, str] = {}
    i = 0

    while i < len(tokens):
        t = tokens[i]
        if t.type == TokenType.KEYWORD and t.value.upper() == "VAR":
            # Next code token should be the variable name (IDENTIFIER)
            if i + 1 < len(tokens) and tokens[i + 1].type == TokenType.IDENTIFIER:
                var_name = tokens[i + 1].value
                # Find the = operator
                j = i + 2
                if (
                    j < len(tokens)
                    and tokens[j].type == TokenType.OPERATOR
                    and tokens[j].value == "="
                ):
                    j += 1  # skip past =
                # Collect definition tokens until next VAR or RETURN keyword
                def_start = j
                while j < len(tokens):
                    if tokens[j].type == TokenType.KEYWORD and tokens[j].value.upper() in (
                        "VAR",
                        "RETURN",
                    ):
                        break
                    j += 1
                # Build definition from original source span for faithful repr
                if def_start < j and def_start < len(tokens) and j - 1 < len(tokens):
                    span_start = tokens[def_start].start
                    span_end = tokens[j - 1].end
                    definition = dax[span_start:span_end].strip()
                else:
                    definition = ""
                # Truncate long definitions
                if len(definition) > 100:
                    definition = definition[:100] + "..."
                variables[var_name] = definition
                i = j
                continue
        i += 1

    return variables


def _extract_variables_regex(dax: str) -> Dict[str, str]:
    """Regex-based VAR extraction — fallback path."""
    variables: Dict[str, str] = {}

    for match in _VAR_PATTERN_RE.finditer(dax):
        var_name = match.group(1)
        start_pos = match.end()

        # Extract the variable definition (until next VAR or RETURN)
        remaining = dax[start_pos:]
        next_var = _NEXT_VAR_RE.search(remaining)
        next_return = _RETURN_RE.search(remaining)

        end_pos = len(remaining)
        if next_var and next_return:
            end_pos = min(next_var.start(), next_return.start())
        elif next_var:
            end_pos = next_var.start()
        elif next_return:
            end_pos = next_return.start()

        definition = remaining[:end_pos].strip()

        # Truncate long definitions
        if len(definition) > 100:
            definition = definition[:100] + "..."

        variables[var_name] = definition

    return variables


def get_line_column(text: str, position: int) -> Tuple[int, int]:
    """
    Convert a character position to a 1-based (line, column) tuple.

    Args:
        text: Full text
        position: Character offset (0-based)

    Returns:
        Tuple of (line_number, column_number), both 1-based
    """
    position = max(0, min(position, len(text)))
    line = text.count("\n", 0, position) + 1
    last_nl = text.rfind("\n", 0, position)
    return line, position - last_nl


def validate_dax_identifier(name: str) -> bool:
    """Validate DAX identifier against strict naming pattern (letters, digits, underscores).

    NOTE: For loose safety-only validation (length/null checks),
    see dax_validator.validate_identifier() instead.
    """
    if not name:
        return False
    return bool(_DAX_IDENTIFIER_RE.fullmatch(name))
