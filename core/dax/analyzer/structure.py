"""Token-structure helpers shared by the analyzer rules (2026-10-03 review D-03..D-27).

The rules work on the comment/whitespace-free token list. These helpers answer the
structural questions the canon rules need and that a flat scan cannot:

- ``call_frames`` -- for every token, the stack of enclosing calls;
- ``call_args`` / ``arg_slices`` -- the argument token slices of a call;
- ``fn_name`` -- the upper-case function name of a FUNCTION/KEYWORD/IDENTIFIER token;
- ``column_key`` -- normalised ``table|column`` of one column reference;
- ``const_value`` -- evaluate a purely literal arithmetic sub-expression.

Nothing here reads a model; everything is lexical.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from core.dax.tokenizer import Token, TokenType

_TRIVIA = (
    TokenType.WHITESPACE,
    TokenType.NEWLINE,
    TokenType.COMMENT_LINE,
    TokenType.COMMENT_BLOCK,
)

_NAME_TYPES = (TokenType.FUNCTION, TokenType.KEYWORD, TokenType.IDENTIFIER)


def sig(tokens: Sequence[Token]) -> List[Token]:
    """Tokens without trivia."""
    return [t for t in tokens if t.type not in _TRIVIA]


def fn_name(tok: Token) -> str:
    """Upper-case name of a callable token, ``""`` for anything else."""
    return tok.value.upper() if tok.type in _NAME_TYPES else ""


# Functions that evaluate some arguments LAZILY (only when the branch is taken): the first
# argument index that is conditional. IF.EAGER is eager by definition but listed so that no
# rewrite ever moves a reference out of one of its branches (2026-10-03 review D-02).
CONDITIONAL_FROM_ARG = {
    "IF": 1,
    "IF.EAGER": 1,
    "SWITCH": 1,
    "COALESCE": 1,
    "IFERROR": 1,
    "DIVIDE": 2,  # the alternate result
}


def is_conditional_arg(name: str, arg: int) -> bool:
    """True when argument *arg* of function *name* is evaluated only conditionally."""
    start = CONDITIONAL_FROM_ARG.get(name)
    return start is not None and arg >= start


@dataclass(frozen=True)
class CallFrame:
    """One enclosing paren / brace of a token."""

    name: str  # upper-case function name; "" = grouping paren; "{" = table constructor
    name_idx: int  # index of the name token (-1 when none)
    open_idx: int  # index of the opening ( or {
    arg: int  # zero-based argument index the token sits in


_FRAMES_CACHE = threading.local()


def call_frames(code: Sequence[Token]) -> List[Tuple[CallFrame, ...]]:
    """For each token, the tuple of enclosing frames (outermost first).

    The frame of an opening ``(`` / closing ``)`` token is its OUTER stack. Linear: the stack is an
    immutable tuple shared by every token between two brackets, and the result is memoised per
    token list (the analyzer hands the same list to every rule; token lists are never mutated).
    """
    cached = getattr(_FRAMES_CACHE, "entry", None)
    if cached is not None and cached[0] is code and cached[1] == len(code):
        return cached[2]
    out: List[Tuple[CallFrame, ...]] = []
    stack: Tuple[CallFrame, ...] = ()
    for i, t in enumerate(code):
        tt = t.type
        if tt in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            stack = stack[:-1]
            out.append(stack)
            continue
        out.append(stack)
        if tt == TokenType.PAREN_OPEN:
            prev = code[i - 1] if i > 0 else None
            if prev is not None and prev.type in _NAME_TYPES:
                stack = stack + (CallFrame(prev.value.upper(), i - 1, i, 0),)
            else:
                stack = stack + (CallFrame("", -1, i, 0),)
        elif tt == TokenType.BRACE_OPEN:
            stack = stack + (CallFrame("{", -1, i, 0),)
        elif tt == TokenType.COMMA and stack:
            top = stack[-1]
            stack = stack[:-1] + (CallFrame(top.name, top.name_idx, top.open_idx, top.arg + 1),)
    _FRAMES_CACHE.entry = (code, len(code), out)
    return out


def frame_stacks(code: Sequence[Token]) -> List[Tuple[Tuple[str, int], ...]]:
    """``(name, arg_index)`` view of ``call_frames`` (the form the codemods use)."""
    return [tuple((f.name, f.arg) for f in frames) for frames in call_frames(code)]


def matching_close(code: Sequence[Token], open_idx: int) -> int:
    """Index of the bracket closing the one at *open_idx*, or ``len(code)``."""
    depth = 0
    for j in range(open_idx, len(code)):
        tt = code[j].type
        if tt in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
            depth += 1
        elif tt in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            depth -= 1
            if depth == 0:
                return j
    return len(code)


def arg_slices(code: Sequence[Token], name_idx: int) -> List[Tuple[int, int]]:
    """``(start, end)`` index slices (end exclusive) of the arguments of the call whose
    name token is at *name_idx*. ``[]`` when it is not followed by ``(``."""
    n = len(code)
    open_idx = name_idx + 1
    if open_idx >= n or code[open_idx].type != TokenType.PAREN_OPEN:
        return []
    close = matching_close(code, open_idx)
    slices: List[Tuple[int, int]] = []
    start = open_idx + 1
    depth = 0
    for j in range(open_idx + 1, close):
        tt = code[j].type
        if tt in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
            depth += 1
        elif tt in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            depth -= 1
        elif tt == TokenType.COMMA and depth == 0:
            slices.append((start, j))
            start = j + 1
    if close > open_idx + 1 or slices:
        slices.append((start, close))
    return slices


def call_args(code: Sequence[Token], name_idx: int) -> List[List[Token]]:
    return [list(code[a:b]) for a, b in arg_slices(code, name_idx)]


def column_key(tokens: Sequence[Token]) -> str:
    """Normalised ``table|column`` when *tokens* is exactly one qualified column
    reference (``'T'[c]`` or ``T[c]``); ``""`` otherwise."""
    from core.dax.dax_names import split_qualified

    s = sig(tokens)
    if len(s) == 1 and s[0].type == TokenType.QUALIFIED_REF:
        t, c = split_qualified(s[0].value)
    elif len(s) == 2 and s[0].type == TokenType.IDENTIFIER and s[1].type == TokenType.COLUMN_REF:
        t, c = s[0].value, s[1].value[1:-1].replace("]]", "]")
    else:
        return ""
    return f"{' '.join(t.lower().split())}|{' '.join(c.lower().split())}"


def ref_table(tok: Token, prev: Optional[Token] = None) -> str:
    """Normalised table name of a qualified column ref / table ref token, else ``""``."""
    from core.dax.dax_names import split_qualified, unquote_table

    if tok.type == TokenType.QUALIFIED_REF:
        t, _c = split_qualified(tok.value)
        return " ".join(t.lower().split())
    if tok.type == TokenType.TABLE_REF:
        return " ".join(unquote_table(tok.value).lower().split())
    if tok.type == TokenType.COLUMN_REF and prev is not None and prev.type == TokenType.IDENTIFIER:
        return " ".join(prev.value.lower().split())
    return ""


def column_refs(tokens: Sequence[Token]) -> List[Tuple[int, str, str]]:
    """Every column reference in *tokens* as ``(index, table, column)`` (lower-case).

    A lone ``[x]`` (measure or extended column) has table ``""``.
    """
    from core.dax.dax_names import split_qualified

    out: List[Tuple[int, str, str]] = []
    for k, t in enumerate(tokens):
        if t.type == TokenType.QUALIFIED_REF:
            tb, col = split_qualified(t.value)
            out.append((k, " ".join(tb.lower().split()), " ".join(col.lower().split())))
        elif t.type == TokenType.COLUMN_REF:
            prev = tokens[k - 1] if k > 0 else None
            col = t.value[1:-1].replace("]]", "]")
            if prev is not None and prev.type == TokenType.IDENTIFIER:
                out.append((k, " ".join(prev.value.lower().split()), " ".join(col.lower().split())))
            else:
                out.append((k, "", " ".join(col.lower().split())))
    return out


# ---------------------------------------------------------------------------
# Literal arithmetic
# ---------------------------------------------------------------------------

_CONST_FUNCS = {
    "POWER": lambda a, b: a**b,
    "ABS": lambda a: abs(a),
    "SQRT": lambda a: math.sqrt(a),
    "EXP": lambda a: math.exp(a),
    "LN": lambda a: math.log(a),
    "LOG10": lambda a: math.log10(a),
    "INT": lambda a: math.floor(a),
    "ROUND": lambda a, b=0: round(a, int(b)),
    "MAX": lambda a, b: max(a, b),
    "MIN": lambda a, b: min(a, b),
}


def const_operand(code: Sequence[Token], pos: int) -> Optional[Tuple[float, int]]:
    """Evaluate the literal operand starting at *pos* (unary sign, number, parenthesised
    literal arithmetic, a pure function of literals, optionally ``^`` chained).

    Returns ``(value, next_pos)`` or None when it is not a compile-time constant.
    """
    n = len(code)
    if pos >= n:
        return None
    sign = 1.0
    while pos < n and code[pos].type == TokenType.OPERATOR and code[pos].value in ("+", "-"):
        if code[pos].value == "-":
            sign = -sign
        pos += 1
    if pos >= n:
        return None
    t = code[pos]
    val: Optional[float] = None
    nxt = pos + 1
    try:
        if t.type == TokenType.NUMBER:
            val = float(t.value)
        elif t.type == TokenType.PAREN_OPEN:
            close = matching_close(code, pos)
            if close >= n:
                return None
            val = _const_expr(code[pos + 1 : close])
            nxt = close + 1
        elif (
            t.type in (TokenType.FUNCTION, TokenType.IDENTIFIER)
            and t.value.upper() in _CONST_FUNCS
            and pos + 1 < n
            and code[pos + 1].type == TokenType.PAREN_OPEN
        ):
            slices = arg_slices(code, pos)
            if not slices:
                return None
            vals = [_const_expr(code[a:b]) for a, b in slices]
            if any(v is None for v in vals):
                return None
            val = float(_CONST_FUNCS[t.value.upper()](*vals))
            nxt = matching_close(code, pos + 1) + 1
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None
    if val is None:
        return None
    if nxt < n and code[nxt].type == TokenType.OPERATOR and code[nxt].value == "^":
        exp = const_operand(code, nxt + 1)
        if exp is None:
            return None
        try:
            val = val ** exp[0]
        except (ValueError, ZeroDivisionError, OverflowError):
            return None
        nxt = exp[1]
    return sign * val, nxt


def _const_expr(seg: Sequence[Token]) -> Optional[float]:
    """Evaluate ``a op b op c`` over literal operands (``+ - * /`` with the usual precedence)."""
    seg = list(seg)
    if not seg:
        return None
    first = const_operand(seg, 0)
    if first is None:
        return None
    terms: List[float] = [first[0]]
    ops: List[str] = []
    pos = first[1]
    while pos < len(seg):
        t = seg[pos]
        if t.type != TokenType.OPERATOR or t.value not in ("+", "-", "*", "/"):
            return None
        nxt = const_operand(seg, pos + 1)
        if nxt is None:
            return None
        ops.append(t.value)
        terms.append(nxt[0])
        pos = nxt[1]
    # precedence: * / first
    vals = [terms[0]]
    pend: List[str] = []
    for op, v in zip(ops, terms[1:]):
        if op == "*":
            vals[-1] *= v
        elif op == "/":
            vals[-1] /= v
        else:
            pend.append(op)
            vals.append(v)
    total = vals[0]
    for op, v in zip(pend, vals[1:]):
        total = total + v if op == "+" else total - v
    return total


def constant_denominator(code: Sequence[Token], div_idx: int) -> Optional[float]:
    """Value of the denominator operand following the ``/`` at *div_idx* when it is a
    literal constant, else None."""
    res = const_operand(code, div_idx + 1)
    return None if res is None else res[0]


def const_expression(seg: Sequence[Token]) -> Optional[float]:
    """Value of a COMPLETE literal arithmetic expression (``60 * 60 * 24``, ``POWER(10, 6)``,
    ``-1000``), or None when any part is not a compile-time constant."""
    try:
        return _const_expr(list(seg))
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None
