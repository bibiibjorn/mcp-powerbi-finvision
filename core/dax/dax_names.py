"""DAX name quoting / escaping helpers — the ONLY place DAX names get built.

DAX has three delimited lexical forms, each with its own doubling escape and
none with backslash escapes:

- table names:   ``'Sales Fact'``      — ``'`` inside is written ``''``
- column/measure names: ``[Net Sales]`` — ``]`` inside is written ``]]``
- string literals: ``"Label"``          — ``"`` inside is written ``""``

Every generator that splices a model name into DAX text must go through these
helpers (2026-09-22 review D-19: ``expand_measure`` emitted
``ROW("Target "Q"", 'm'[Target "Q"])``). ``quote_table`` ALWAYS quotes — a quoted
table name is valid for every name, while deciding when bare is safe needs the
reserved-word list and unicode rules, which is not worth the risk.
"""

from __future__ import annotations

from typing import Tuple

__all__ = [
    "quote_table",
    "bracket",
    "dax_string",
    "column_ref",
    "measure_ref",
    "unquote_table",
    "unbracket",
    "unstring",
    "split_qualified",
]


def quote_table(name: str) -> str:
    """``O'Brien`` -> ``'O''Brien'`` (always quoted, apostrophes doubled)."""
    return "'" + str(name).replace("'", "''") + "'"


def bracket(name: str) -> str:
    """``Col]x`` -> ``[Col]]x]`` (closing brackets doubled)."""
    return "[" + str(name).replace("]", "]]") + "]"


def dax_string(s: str) -> str:
    """Wrap *s* as a DAX string literal, doubling embedded double quotes.

    Example: ``say "hi"`` becomes a literal whose inner quotes are written twice.
    """
    return '"' + str(s).replace('"', '""') + '"'


def column_ref(table: str, column: str) -> str:
    """Fully qualified, always-safe column (or measure) reference ``'T'[C]``."""
    return quote_table(table) + bracket(column)


def measure_ref(name: str) -> str:
    """Unqualified measure reference ``[M]`` (brackets escaped)."""
    return bracket(name)


def unquote_table(tok: str) -> str:
    """Inverse of ``quote_table``; a bare (unquoted) name is returned stripped.

    ``'O''Brien'`` -> ``O'Brien``; ``Sales`` -> ``Sales``. A malformed token
    (opening quote without a closing one) is unescaped as far as possible.
    """
    t = (tok or "").strip()
    if t.startswith("'"):
        inner = t[1:-1] if len(t) >= 2 and t.endswith("'") else t[1:]
        return inner.replace("''", "'")
    return t


def unbracket(tok: str) -> str:
    """Inverse of ``bracket``: ``[Col]]x]`` -> ``Col]x``; bare text is stripped."""
    t = (tok or "").strip()
    if t.startswith("["):
        inner = t[1:-1] if len(t) >= 2 and t.endswith("]") else t[1:]
        return inner.replace("]]", "]")
    return t


def unstring(tok: str) -> str:
    """Inverse of ``dax_string`` (strips the outer quotes, un-doubles inner ones)."""
    t = tok or ""
    if t.startswith('"'):
        inner = t[1:-1] if len(t) >= 2 and t.endswith('"') else t[1:]
        return inner.replace('""', '"')
    return t


def split_qualified(tok: str) -> Tuple[str, str]:
    """Split a QUALIFIED_REF token value (``'T'[c]`` / ``'T' [c]``) into the
    UNESCAPED ``(table, column)``. The table part ends at the first unescaped
    closing quote, so ``'a[b'[c]`` splits correctly."""
    t = tok or ""
    if not t.startswith("'"):
        i = t.find("[")
        if i == -1:
            return t.strip(), ""
        return t[:i].strip(), unbracket(t[i:])
    i = 1
    n = len(t)
    while i < n:
        if t[i] == "'":
            if i + 1 < n and t[i + 1] == "'":
                i += 2
                continue
            break
        i += 1
    table_part = t[: i + 1]
    rest = t[i + 1 :].lstrip(" \t")
    return unquote_table(table_part), unbracket(rest)


def qualified_table_span(tok: str) -> int:
    """Length of the quoted table part of a QUALIFIED_REF value (``'T'`` in
    ``'T' [c]``), i.e. the offset where the table part ends."""
    t = tok or ""
    i = 1
    n = len(t)
    while i < n:
        if t[i] == "'":
            if i + 1 < n and t[i + 1] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return n
