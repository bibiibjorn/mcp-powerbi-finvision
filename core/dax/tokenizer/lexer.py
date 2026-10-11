"""DAX Lexer — single-pass tokenizer producing typed token streams.

This is THE DAX front-end of the repo (2026-09-22 review D-01/D-16/D-27): every
consumer that needs to know what is code, what is a name and what is a literal
should lex with ``DaxLexer`` instead of running regexes over raw text.

Lexical rules implemented (Microsoft DAX syntax reference):

- comments: ``// …`` and ``-- …`` to end of line, ``/* … */`` blocks;
- ``"…"`` string literals, ``""`` escapes a quote; strings may span lines;
- ``'…'`` quoted table names, ``''`` escapes an apostrophe;
- ``[…]`` column / measure names, ``]]`` escapes a closing bracket;
- ``'T'[c]`` is one QUALIFIED_REF token (spaces/tabs allowed between the parts);
- numbers ``1``, ``1.5``, ``.5``, ``2.``, ``1e-3``, ``1.2E+4``;
- ``dt"2024-01-01"`` datetime literals (DATETIME token);
- operators ``+ - * / ^ = == <> < > <= >= && || & =>``;
- ``( ) { } , ; : .`` punctuation;
- dotted function names (``STDEVX.S``, ``PERCENTILEX.INC``, ``INFO.VIEW.TABLES``,
  ``IF.EAGER``) become one FUNCTION token;
- keywords incl. DEFINE / MEASURE / VAR / COLUMN / TABLE / FUNCTION / RETURN /
  EVALUATE / ORDER BY / START AT; unicode letters in unquoted names.

The lexer never raises. An unterminated delimited token is emitted with
``terminated=False`` so callers (the syntax gate) can report it precisely.
"""

import bisect
import re
import threading
from typing import Dict, List, Optional, Set, Tuple

from .tokens import Token, TokenType

_KEYWORDS: Set[str] = {
    "VAR",
    "RETURN",
    "IF",
    "ELSE",
    "SWITCH",
    "TRUE",
    "FALSE",
    "AND",
    "OR",
    "NOT",
    "IN",
    "EVALUATE",
    "DEFINE",
    "MEASURE",
    "ORDER",
    "BY",
    "ASC",
    "DESC",
    "COLUMN",
    "TABLE",
    "START",
    "AT",
    "FUNCTION",
    "CALCULATIONGROUP",
    "CALCULATIONITEM",
}

# Longest first; "--" is handled as a comment before operators are considered.
_MULTI_CHAR_OPS: Tuple[str, ...] = ("<=", ">=", "<>", "&&", "||", "==", "=>")
_SINGLE_CHAR_OPS: Set[str] = {"+", "-", "*", "/", "^", "=", "<", ">", "&", "|"}

_PUNCT: Dict[str, TokenType] = {
    "(": TokenType.PAREN_OPEN,
    ")": TokenType.PAREN_CLOSE,
    "{": TokenType.BRACE_OPEN,
    "}": TokenType.BRACE_CLOSE,
    ",": TokenType.COMMA,
    ";": TokenType.SEMICOLON,
    ":": TokenType.COLON,
}

#: Token types that carry no code (safe to drop for structural analysis).
TRIVIA_TYPES = frozenset(
    {
        TokenType.COMMENT_LINE,
        TokenType.COMMENT_BLOCK,
        TokenType.WHITESPACE,
        TokenType.NEWLINE,
    }
)

_NEWLINE_RE = re.compile(r"\r\n|\n|\r")

# Per-thread cache of paren maps keyed by token-list identity (D-15). Rules call
# ``extract_function_args`` once per function token; recomputing the map over
# the whole stream each time made the analyzer quadratic.
_PMAP_CACHE = threading.local()
_PMAP_CACHE_SLOTS = 8


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _is_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


class DaxLexer:
    """Single-pass DAX tokenizer.

    Converts DAX source text into a flat list of ``Token`` objects,
    tracking line and column (of the token start) for every token.
    ``function_names`` (upper-case) lets the lexer classify ``NAME(`` as
    FUNCTION; without it a call lexes as IDENTIFIER/KEYWORD (dotted calls such
    as ``STDEVX.S(`` are still joined into a single token).
    """

    def __init__(self, function_names: Optional[Set[str]] = None) -> None:
        self._function_names: Set[str] = (
            {n.upper() for n in function_names} if function_names else set()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tokenize(self, dax: str) -> List[Token]:
        """Return the full token stream including whitespace and comments."""
        if not dax:
            return []
        line_starts = [0] + [m.end() for m in _NEWLINE_RE.finditer(dax)]

        def pos_to_line_col(p: int) -> Tuple[int, int]:
            idx = bisect.bisect_right(line_starts, p) - 1
            return idx + 1, p - line_starts[idx] + 1

        tokens: List[Token] = []
        length = len(dax)
        pos = 0

        def emit(ttype: TokenType, start: int, end: int, terminated: bool = True) -> None:
            ln, cl = pos_to_line_col(start)
            tokens.append(Token(ttype, dax[start:end], start, end, ln, cl, terminated))

        while pos < length:
            ch = dax[pos]
            nxt = dax[pos + 1] if pos + 1 < length else ""

            # --- Newlines (\r\n, \n or lone \r) ---
            if ch == "\r" and nxt == "\n":
                emit(TokenType.NEWLINE, pos, pos + 2)
                pos += 2
                continue
            if ch in ("\n", "\r"):
                emit(TokenType.NEWLINE, pos, pos + 1)
                pos += 1
                continue

            # --- Whitespace (spaces, tabs, other non-newline blanks) ---
            if ch in (" ", "\t", "\f", "\v", " ") or (ch.isspace() and ch not in "\r\n"):
                start = pos
                while pos < length and dax[pos] not in "\r\n" and dax[pos].isspace():
                    pos += 1
                emit(TokenType.WHITESPACE, start, pos)
                continue

            # --- Line comment: // or -- ---
            if (ch == "/" and nxt == "/") or (ch == "-" and nxt == "-"):
                start = pos
                while pos < length and dax[pos] not in "\r\n":
                    pos += 1
                emit(TokenType.COMMENT_LINE, start, pos)
                continue

            # --- Block comment ---
            if ch == "/" and nxt == "*":
                start = pos
                close = dax.find("*/", pos + 2)
                if close == -1:
                    pos = length
                    emit(TokenType.COMMENT_BLOCK, start, pos, terminated=False)
                else:
                    pos = close + 2
                    emit(TokenType.COMMENT_BLOCK, start, pos)
                continue

            # --- String literal ("..." with "" escape) ---
            if ch == '"':
                start = pos
                pos, ok = self._scan_quoted(dax, pos, '"')
                emit(TokenType.STRING, start, pos, terminated=ok)
                continue

            # --- Table ref / Qualified ref ('Table'[Col], 'Table' [Col]) ---
            if ch == "'":
                start = pos
                pos, ok = self._scan_quoted(dax, pos, "'")
                if not ok:
                    emit(TokenType.TABLE_REF, start, pos, terminated=False)
                    continue
                j = pos
                while j < length and dax[j] in (" ", "\t"):
                    j += 1
                if j < length and dax[j] == "[":
                    end, ok2 = self._scan_bracket(dax, j)
                    pos = end
                    emit(TokenType.QUALIFIED_REF, start, pos, terminated=ok2)
                else:
                    emit(TokenType.TABLE_REF, start, pos)
                continue

            # --- Column / measure ref [Name] ---
            if ch == "[":
                start = pos
                pos, ok = self._scan_bracket(dax, pos)
                emit(TokenType.COLUMN_REF, start, pos, terminated=ok)
                continue

            # --- Number (digits, decimal, scientific) ---
            if ch.isdigit() or (ch == "." and nxt.isdigit()):
                start = pos
                while pos < length and dax[pos].isdigit():
                    pos += 1
                if pos < length and dax[pos] == ".":
                    pos += 1
                    while pos < length and dax[pos].isdigit():
                        pos += 1
                if pos < length and dax[pos] in ("e", "E"):
                    k = pos + 1
                    if k < length and dax[k] in ("+", "-"):
                        k += 1
                    if k < length and dax[k].isdigit():
                        pos = k
                        while pos < length and dax[pos].isdigit():
                            pos += 1
                emit(TokenType.NUMBER, start, pos)
                continue

            # --- Punctuation ---
            ptype = _PUNCT.get(ch)
            if ptype is not None:
                emit(ptype, pos, pos + 1)
                pos += 1
                continue

            # --- Multi-char operators (longest first) ---
            two = dax[pos : pos + 2]
            if two in _MULTI_CHAR_OPS:
                emit(TokenType.OPERATOR, pos, pos + 2)
                pos += 2
                continue

            # --- Single-char operators ---
            if ch in _SINGLE_CHAR_OPS:
                emit(TokenType.OPERATOR, pos, pos + 1)
                pos += 1
                continue

            # --- Dot ---
            if ch == ".":
                emit(TokenType.DOT, pos, pos + 1)
                pos += 1
                continue

            # --- Identifier / Keyword / Function / dt"..." ---
            if _is_ident_start(ch):
                start = pos
                while pos < length and _is_ident_char(dax[pos]):
                    pos += 1
                word = dax[start:pos]

                # dt"2024-01-01" datetime literal
                if word.lower() == "dt" and pos < length and dax[pos] == '"':
                    end, ok = self._scan_quoted(dax, pos, '"')
                    pos = end
                    emit(TokenType.DATETIME, start, pos, terminated=ok)
                    continue

                pos = self._extend_dotted(dax, start, pos)
                word = dax[start:pos]
                token_type = self._classify_word(word.upper(), dax, pos, length)
                emit(token_type, start, pos)
                continue

            # --- Unknown ---
            emit(TokenType.UNKNOWN, pos, pos + 1)
            pos += 1

        return tokens

    def tokenize_code(self, dax: str) -> List[Token]:
        """Tokenize and strip comments, whitespace, and newlines."""
        return [t for t in self.tokenize(dax) if t.type not in TRIVIA_TYPES]

    def build_paren_map(self, tokens: List[Token]) -> Dict[int, int]:
        """Map PAREN_OPEN index -> matching PAREN_CLOSE index."""
        stack: List[int] = []
        pmap: Dict[int, int] = {}
        for i, tok in enumerate(tokens):
            if tok.type == TokenType.PAREN_OPEN:
                stack.append(i)
            elif tok.type == TokenType.PAREN_CLOSE:
                if stack:
                    pmap[stack.pop()] = i
        return pmap

    def paren_map(self, tokens: List[Token]) -> Dict[int, int]:
        """Cached ``build_paren_map`` keyed by the identity of *tokens*.

        The cache holds a reference to the list, so ``is`` identity is stable;
        it is per-thread and bounded. Callers must not mutate a token list after
        asking for its map (no caller does — token lists are built once).
        """
        slots = getattr(_PMAP_CACHE, "slots", None)
        if slots is None:
            slots = []
            _PMAP_CACHE.slots = slots
        for entry in slots:
            if entry[0] is tokens and entry[1] == len(tokens):
                return entry[2]
        pmap = self.build_paren_map(tokens)
        slots.insert(0, (tokens, len(tokens), pmap))
        del slots[_PMAP_CACHE_SLOTS:]
        return pmap

    def extract_function_args(self, tokens: List[Token], func_index: int) -> List[List[Token]]:
        """Extract argument token lists for a function call.

        *func_index* is the index of the FUNCTION/KEYWORD/IDENTIFIER token. The
        next non-trivia token must be ``(``; the matching ``)`` comes from the
        (cached) paren map, and the body is split on COMMA at depth 0, where
        depth counts both ``( )`` and ``{ }`` (``TREATAS({"a","b"}, T[c])`` has
        two arguments).
        """
        paren_idx: Optional[int] = None
        for i in range(func_index + 1, len(tokens)):
            ttype = tokens[i].type
            if ttype in TRIVIA_TYPES:
                continue
            if ttype == TokenType.PAREN_OPEN:
                paren_idx = i
            break

        if paren_idx is None:
            return []

        close_idx = self.paren_map(tokens).get(paren_idx)
        if close_idx is None:
            return []

        args: List[List[Token]] = []
        current: List[Token] = []
        depth = 0
        for i in range(paren_idx + 1, close_idx):
            tok = tokens[i]
            ttype = tok.type
            if ttype in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
                depth += 1
                current.append(tok)
            elif ttype in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
                depth -= 1
                current.append(tok)
            elif ttype == TokenType.COMMA and depth == 0:
                args.append(current)
                current = []
            else:
                current.append(tok)

        if current or args:
            args.append(current)

        return args

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_quoted(dax: str, pos: int, quote: str) -> Tuple[int, bool]:
        """Scan a *quote*-delimited literal starting at *pos* (the opening quote).

        A doubled quote is an escape. Returns (end_exclusive, terminated).
        """
        length = len(dax)
        i = pos + 1
        while True:
            j = dax.find(quote, i)
            if j == -1:
                return length, False
            if j + 1 < length and dax[j + 1] == quote:
                i = j + 2
                continue
            return j + 1, True

    @staticmethod
    def _scan_bracket(dax: str, pos: int) -> Tuple[int, bool]:
        """Scan a ``[...]`` name starting at *pos*; ``]]`` is an escaped ``]``."""
        length = len(dax)
        i = pos + 1
        while True:
            j = dax.find("]", i)
            if j == -1:
                return length, False
            if j + 1 < length and dax[j + 1] == "]":
                i = j + 2
                continue
            return j + 1, True

    def _extend_dotted(self, dax: str, start: int, end: int) -> int:
        """Greedily join ``NAME.NAME[.NAME]`` into one token when the joined
        name is a known function, or the full dotted name is a call
        (followed by ``(``). Returns the new end offset (unchanged if not)."""
        length = len(dax)
        if end >= length or dax[end] != ".":
            return end
        ends = [end]
        pos = end
        while pos + 1 < length and dax[pos] == "." and _is_ident_start(dax[pos + 1]):
            pos += 1
            while pos < length and _is_ident_char(dax[pos]):
                pos += 1
            ends.append(pos)
        if len(ends) == 1:
            return end
        if self._function_names:
            for cand in reversed(ends[1:]):
                if dax[start:cand].upper() in self._function_names:
                    return cand
        if self._next_non_ws_is_paren(dax, ends[-1], length):
            return ends[-1]
        return end

    def _classify_word(self, upper: str, dax: str, pos: int, length: int) -> TokenType:
        """Classify an identifier word as FUNCTION, KEYWORD, or IDENTIFIER."""
        followed_by_paren = self._next_non_ws_is_paren(dax, pos, length)

        # Function names take priority when followed by (
        if followed_by_paren and upper in self._function_names:
            return TokenType.FUNCTION

        # Keywords (IF/SWITCH/AND/OR/NOT not in function_names stay KEYWORD)
        if upper in _KEYWORDS:
            return TokenType.KEYWORD

        return TokenType.IDENTIFIER

    @staticmethod
    def _next_non_ws_is_paren(dax: str, pos: int, length: int) -> bool:
        """Check whether the next non-whitespace character is ``(``."""
        j = pos
        while j < length and dax[j] in (" ", "\t", "\r", "\n"):
            j += 1
        return j < length and dax[j] == "("
