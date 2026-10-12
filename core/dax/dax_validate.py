"""Bind-only rewrite for ``04_Run_DAX mode=validate`` (PV-01).

Goal: let the DAX engine parse and BIND a query (unknown names, wrong arity,
type errors, syntax) without paying for its execution.

Strategy ("define-var / dead branch"). Every top-level ``EVALUATE <expr>`` of the
query is moved into a query-scope ``VAR`` and referenced from an ``IF(FALSE(), ...)``
branch of a trivial ``EVALUATE ROW(...)``::

    DEFINE                                   <- the caller's own DEFINE block is kept
        MEASURE 'T'[M] = ...
        VAR __mcp_validate_1 = <expr of the 1st EVALUATE>
        VAR __mcp_validate_2 = <expr of the 2nd EVALUATE>
    EVALUATE ROW("__mcp_validate", IF(FALSE(), COUNTROWS(__mcp_validate_1) + ..., 0))

The engine resolves every name and checks every type when it compiles the script;
the dead ``IF`` branch is never evaluated, so a heavy query costs the same as a
``ROW`` (and the caller still gets a hard 10 s command timeout as a backstop).

Limits (documented in the tool guide, keep in sync):

* ``ORDER BY`` / ``START AT`` clauses are NOT part of the rewrite, so names used only
  there (e.g. an ORDER BY on a renamed column) are not checked.
* Only what the engine binds statically is checked; run-time errors (division by zero
  in a branch that is taken, a circular measure detected during evaluation) are not.
* A SECOND ``DEFINE`` after an ``EVALUATE`` is not part of the single DEFINE block the
  rewrite builds: the query is validated up to it and the rest is reported under
  ``not_validated`` (N-29) instead of being swallowed into a VAR (a bogus syntax error).
* A query without a top-level ``EVALUATE`` (a DMV ``SELECT``, a bare expression) is refused.

The pure functions here run offline; the executor sends the rewritten text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.dax.tokenizer import TRIVIA_TYPES, DaxLexer, TokenType

_WORD_TYPES = (TokenType.KEYWORD, TokenType.IDENTIFIER, TokenType.FUNCTION)
_VAR_PREFIX = "__mcp_validate_"

# Engine positions look like ``Query (3, 12) Failed to resolve name ...``.
_POSITION = re.compile(r"Query \((\d+), ?(\d+)\)")

_lexer = DaxLexer()


@dataclass
class BindOnlyPlan:
    """Result of :func:`build_bind_only_query`."""

    ok: bool
    query: str = ""
    error: str = ""
    statements: int = 0
    #: Clauses of the original that are dropped by the rewrite (``ORDER BY`` ...).
    dropped: List[str] = field(default_factory=list)
    #: ``(wrapped_start, original_start, length)`` spans used to translate error positions.
    spans: List[Tuple[int, int, int]] = field(default_factory=list)
    original: str = ""


def _code_tokens(query: str):
    return [t for t in _lexer.tokenize(query) if t.type not in TRIVIA_TYPES]


def build_bind_only_query(query: str) -> BindOnlyPlan:
    """Rewrite ``query`` into the bind-only form (see the module docstring)."""
    if not isinstance(query, str) or not query.strip():
        return BindOnlyPlan(False, error="query is empty", original=query or "")
    tokens = _code_tokens(query)

    depth = 0
    evaluates: List[int] = []  # index into tokens of each depth-0 EVALUATE
    order_at: Dict[int, int] = {}  # evaluate index -> char offset of its ORDER BY
    has_define = False
    cut: Optional[int] = None  # char offset of a second top-level DEFINE
    for i, tok in enumerate(tokens):
        if tok.type in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
            depth += 1
        elif tok.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            depth = max(0, depth - 1)
        elif depth == 0 and tok.type in _WORD_TYPES:
            word = tok.value.upper()
            if word == "EVALUATE":
                evaluates.append(i)
            elif word == "DEFINE" and not evaluates:
                has_define = True
            elif word == "DEFINE" and evaluates:
                cut = tok.start  # a second DEFINE block: stop the rewrite here (N-29)
                tokens = tokens[:i]
                break
            elif (
                word == "ORDER"
                and evaluates
                and (evaluates[-1] not in order_at)
                and i + 1 < len(tokens)
                and tokens[i + 1].value.upper() == "BY"
            ):
                order_at[evaluates[-1]] = tok.start

    if not evaluates:
        return BindOnlyPlan(
            False,
            error=(
                "validate needs a query with a top-level EVALUATE statement "
                "(DMV SELECT statements and bare expressions cannot be bound)"
            ),
            original=query,
        )

    first = tokens[evaluates[0]]
    prefix = query[: first.start]
    parts: List[str] = [prefix if has_define else prefix + "DEFINE\n"]
    if has_define and not prefix.endswith("\n"):
        parts[0] = prefix.rstrip(" \t") + "\n"
    text_len = len(parts[0])
    spans: List[Tuple[int, int, int]] = [(0, 0, min(len(prefix), text_len))]
    dropped: List[str] = []

    names: List[str] = []
    limit = len(query) if cut is None else cut
    if cut is not None:
        dropped.append(
            "everything from the second DEFINE (line %d) on" % (query[:cut].count(chr(10)) + 1)
        )
    for n, tok_idx in enumerate(evaluates, start=1):
        ev = tokens[tok_idx]
        end = limit
        if tok_idx in order_at:
            end = order_at[tok_idx]
            dropped.append("ORDER BY / START AT of EVALUATE #%d" % n)
        if n < len(evaluates):
            end = min(end, tokens[evaluates[n]].start)
        expr = query[ev.end : end]
        if not expr.strip():
            return BindOnlyPlan(
                False,
                error=f"EVALUATE statement #{n} has no expression",
                original=query,
            )
        name = f"{_VAR_PREFIX}{n}"
        names.append(name)
        head = f"VAR {name} ="
        parts.append(head)
        text_len += len(head)
        spans.append((text_len, ev.end, len(expr)))
        parts.append(expr)
        text_len += len(expr)
        parts.append("\n")
        text_len += 1

    probe = " + ".join(f"COUNTROWS({n})" for n in names)
    parts.append(f'EVALUATE ROW("__mcp_validate", IF(FALSE(), {probe}, 0))')
    return BindOnlyPlan(
        True,
        query="".join(parts),
        statements=len(evaluates),
        dropped=dropped,
        spans=spans,
        original=query,
    )


# ---------------------------------------------------------------------------
# Error position translation
# ---------------------------------------------------------------------------


def _offset(text: str, line: int, col: int) -> Optional[int]:
    lines = text.split("\n")
    if line < 1 or line > len(lines):
        return None
    off = sum(len(x) + 1 for x in lines[: line - 1]) + max(col - 1, 0)
    return off if off <= len(text) else None


def _line_col(text: str, off: int) -> Tuple[int, int]:
    before = text[:off]
    line = before.count("\n") + 1
    col = off - (before.rfind("\n") + 1) + 1
    return line, col


def remap_engine_positions(message: str, plan: BindOnlyPlan) -> str:
    """Translate ``Query (line, col)`` in an engine message from the rewritten text back
    to the caller's original text (best effort; positions outside the moved
    expressions are left alone)."""
    if not plan.ok or not message:
        return message

    def sub(m: "re.Match[str]") -> str:
        off = _offset(plan.query, int(m.group(1)), int(m.group(2)))
        if off is None:
            return m.group(0)
        for wrapped_start, orig_start, length in plan.spans:
            if wrapped_start <= off < wrapped_start + max(length, 1):
                line, col = _line_col(plan.original, orig_start + (off - wrapped_start))
                return f"Query ({line}, {col})"
        return m.group(0)

    return _POSITION.sub(sub, message)
