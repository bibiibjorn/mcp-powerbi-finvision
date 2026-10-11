"""Tabular-Editor-style BPA rule expressions: lexer, parser and evaluator.

2026-09-22 review (I-01 … I-05): the old evaluator was a cascade of anchored
regexes that decided compound rules by their first clause, wrote ``"True"``
back into the text when it reduced parentheses and compared enums
case-sensitively against TMSL's lowerCamel values. This module replaces it with
a real tokenizer + recursive-descent parser for the Dynamic-LINQ subset that
Tabular Editor BPA rules use, evaluated to Python values (never back to text).

Supported grammar (precedence low -> high)::

    or  / ||
    and / &&
    == = != <> < <= > >=
    + -            (string concatenation when either side is a string)
    * / %
    unary: not ! -
    postfix: .Member  .Method(args)  [index]
    primary: literals ("…" '…' numbers true false null), identifiers,
             ( expr ), Function(args)

Semantics follow Tabular Editor / C#:

- Unqualified identifiers resolve against the implicit ``it``; ``current`` is
  the object being analysed, ``outerIt`` the enclosing lambda's ``it``.
- Collection methods (``Any``/``All``/``Count``/``Where``/``Select``/``Sum``/
  ``Max``/``Min``/``First``/``Contains``/``Distinct``) take a predicate or
  selector evaluated per item with a fresh ``it``.
- Enum values compare case-insensitively by name (TMSL serialises
  ``bothDirections`` where TE rules write ``"BothDirections"`` or
  ``CrossFilteringBehavior.BothDirections``).
- ``RegEx.IsMatch`` / ``RegEx.Matches`` patterns are C#-unescaped when they
  still carry doubled backslashes (a rules file that was JSON-escaped twice),
  .NET inline options (``(?i)`` mid-pattern) are translated for Python's
  ``re``; a pattern that cannot be compiled raises :class:`BpaUnsupported`
  so the caller reports the rule as *unsupported* instead of silently
  never matching.

Anything the evaluator does not implement raises :class:`BpaUnsupported`
(never a silent ``False``); data-dependent failures (``Substring`` out of
range, comparing a string with a number) raise :class:`BpaRuntimeError`.
"""

from __future__ import annotations

import math
from functools import lru_cache
import re
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "BpaExpressionError",
    "BpaParseError",
    "BpaUnsupported",
    "BpaRuntimeError",
    "EnumValue",
    "CompiledExpression",
    "compile_expression",
    "compile_regex",
    "translate_regex",
    "regex_literals",
    "Evaluator",
]


# ─────────────────────────────── errors ────────────────────────────────


class BpaExpressionError(Exception):
    """Base class for rule-expression failures."""


class BpaParseError(BpaExpressionError):
    """The expression text is not valid in the supported grammar."""


class BpaUnsupported(BpaExpressionError):
    """The expression uses a construct this evaluator does not implement.

    A rule raising this is reported as *unsupported* (its verdict would be
    meaningless), never as "no violation".
    """


class BpaRuntimeError(BpaExpressionError):
    """A data-dependent evaluation failure for one object (e.g. a type error)."""


# ─────────────────────────────── values ────────────────────────────────


class EnumValue:
    """A TOM enum member. Equality with strings / other enums is case-insensitive."""

    __slots__ = ("name", "type_name")

    def __init__(self, name: str, type_name: Optional[str] = None) -> None:
        self.name = str(name)
        self.type_name = type_name

    def key(self) -> str:
        return self.name.lower()

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"EnumValue({self.name!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, EnumValue):
            return self.key() == other.key()
        if isinstance(other, str):
            return self.key() == other.lower()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("enum", self.key()))


class TypeRef:
    """A static type used as a call target (``RegEx``, ``string``, ``Convert`` …)."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"TypeRef({self.name})"


class MatchResult:
    """Result of ``RegEx.Match`` (``Success`` / ``Value``)."""

    __slots__ = ("success", "value")

    def __init__(self, m: Optional["re.Match[str]"]) -> None:
        self.success = m is not None
        self.value = m.group(0) if m is not None else ""


# Static types callable as ``Type.Method(...)``.
_STATIC_TYPES = {
    "regex": "RegEx",
    "string": "string",
    "convert": "Convert",
    "math": "Math",
    "char": "char",
    "int": "int",
    "decimal": "decimal",
    "double": "double",
    "datetime": "DateTime",
    "daxlint": "DaxLint",
}

# Enum type names: ``DataType.Int64`` is an enum literal. Several are ALSO
# property names (``DataType``, ``CrossFilteringBehavior``) — a bare identifier
# resolves to the property; ``EnumType.Member`` (not a method call) to the enum.
ENUM_TYPES = frozenset(
    {
        "datatype",
        "crossfilteringbehavior",
        "securityfilteringbehavior",
        "relationshipendcardinality",
        "cardinality",
        "modetype",
        "objecttype",
        "aggregatefunction",
        "columntype",
        "partitionsourcetype",
        "modelpermission",
        "metadatapermission",
        "regexoptions",
        "stringcomparison",
        "datasourcetype",
        "expressionkind",
        "encodinghinttype",
        "summarizeby",
    }
)


# ─────────────────────────────── lexer ─────────────────────────────────

_TWO_CHAR_OPS = ("==", "!=", "<>", "<=", ">=", "&&", "||", "=>")
_ONE_CHAR_OPS = "=<>!+-*/%?:"


class _Tok:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind: str, value: Any, pos: int) -> None:
        self.kind = kind  # NUM STR IDENT OP ( ) [ ] , . EOF
        self.value = value
        self.pos = pos

    def __repr__(self) -> str:
        return f"<{self.kind} {self.value!r}@{self.pos}>"


def _tokenize(text: str) -> List[_Tok]:
    toks: List[_Tok] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            j = i + 1
            buf: List[str] = []
            while True:
                if j >= n:
                    raise BpaParseError(f"unterminated string literal at {i}")
                c = text[j]
                if c == "\\" and j + 1 < n and text[j + 1] == quote:
                    # C# escaped quote (\")
                    buf.append(quote)
                    j += 2
                    continue
                if c == quote:
                    if j + 1 < n and text[j + 1] == quote:
                        # Dynamic-LINQ doubled quote ("")
                        buf.append(quote)
                        j += 2
                        continue
                    break
                buf.append(c)
                j += 1
            toks.append(_Tok("STR", "".join(buf), i))
            i = j + 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            m = re.match(r"\d*\.?\d+(?:[eE][+-]?\d+)?[mMdDfFlL]?", text[i:])
            if not m:  # pragma: no cover - guarded by the condition above
                raise BpaParseError(f"bad number at {i}")
            raw = m.group(0).rstrip("mMdDfFlL")
            val: Any = float(raw) if any(c in raw for c in ".eE") else int(raw)
            toks.append(_Tok("NUM", val, i))
            i += len(m.group(0))
            continue
        if ch.isalpha() or ch == "_" or ch == "@":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            toks.append(_Tok("IDENT", text[i:j].lstrip("@"), i))
            i = j
            continue
        two = text[i : i + 2]
        if two in _TWO_CHAR_OPS:
            toks.append(_Tok("OP", two, i))
            i += 2
            continue
        if ch in "()[],.":
            toks.append(_Tok(ch, ch, i))
            i += 1
            continue
        if ch in _ONE_CHAR_OPS:
            toks.append(_Tok("OP", ch, i))
            i += 1
            continue
        raise BpaParseError(f"unexpected character {ch!r} at {i}")
    toks.append(_Tok("EOF", None, n))
    return toks


# ─────────────────────────────── parser ────────────────────────────────
# AST nodes are tuples:
#   ("lit", value)              ("ident", name)
#   ("member", target, name)    ("call", target|None, name, [args])
#   ("index", target, idx)      ("unary", op, operand)
#   ("bin", op, left, right)    ("and", l, r)   ("or", l, r)
#   ("cond", test, a, b)        (iif / ?:)

_CMP_OPS = {
    "==": "==",
    "=": "==",
    "!=": "!=",
    "<>": "!=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
}


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.toks = _tokenize(text)
        self.i = 0

    # helpers
    def _peek(self) -> _Tok:
        return self.toks[self.i]

    def _next(self) -> _Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def _is_kw(self, tok: _Tok, *words: str) -> bool:
        return tok.kind == "IDENT" and tok.value.lower() in words

    def _expect(self, kind: str) -> _Tok:
        t = self._next()
        if t.kind != kind:
            raise BpaParseError(f"expected {kind!r} at {t.pos}, got {t.value!r}")
        return t

    def parse(self) -> tuple:
        node = self._conditional()
        if self._peek().kind != "EOF":
            t = self._peek()
            raise BpaParseError(f"unexpected token {t.value!r} at {t.pos}")
        return node

    def _conditional(self) -> tuple:
        node = self._or()
        t = self._peek()
        if t.kind == "OP" and t.value == "?":
            self._next()
            a = self._conditional()
            c = self._next()
            if not (c.kind == "OP" and c.value == ":"):
                raise BpaParseError(f"expected ':' at {c.pos}")
            b = self._conditional()
            return ("cond", node, a, b)
        return node

    def _or(self) -> tuple:
        node = self._and()
        while True:
            t = self._peek()
            if (t.kind == "OP" and t.value == "||") or self._is_kw(t, "or"):
                self._next()
                node = ("or", node, self._and())
            else:
                return node

    def _and(self) -> tuple:
        node = self._comparison()
        while True:
            t = self._peek()
            if (t.kind == "OP" and t.value == "&&") or self._is_kw(t, "and"):
                self._next()
                node = ("and", node, self._comparison())
            else:
                return node

    def _comparison(self) -> tuple:
        node = self._additive()
        while True:
            t = self._peek()
            if t.kind == "OP" and t.value in _CMP_OPS:
                self._next()
                node = ("bin", _CMP_OPS[t.value], node, self._additive())
            else:
                return node

    def _additive(self) -> tuple:
        node = self._multiplicative()
        while True:
            t = self._peek()
            if t.kind == "OP" and t.value in ("+", "-"):
                self._next()
                node = ("bin", t.value, node, self._multiplicative())
            else:
                return node

    def _multiplicative(self) -> tuple:
        node = self._unary()
        while True:
            t = self._peek()
            if t.kind == "OP" and t.value in ("*", "/", "%"):
                self._next()
                node = ("bin", t.value, node, self._unary())
            else:
                return node

    def _unary(self) -> tuple:
        t = self._peek()
        if (t.kind == "OP" and t.value == "!") or self._is_kw(t, "not"):
            self._next()
            return ("unary", "!", self._unary())
        if t.kind == "OP" and t.value == "-":
            self._next()
            return ("unary", "-", self._unary())
        if t.kind == "OP" and t.value == "+":
            self._next()
            return self._unary()
        return self._postfix()

    def _args(self) -> List[tuple]:
        self._expect("(")
        args: List[tuple] = []
        if self._peek().kind == ")":
            self._next()
            return args
        while True:
            args.append(self._conditional())
            t = self._next()
            if t.kind == ")":
                return args
            if t.kind != ",":
                raise BpaParseError(f"expected ',' or ')' at {t.pos}")

    def _postfix(self) -> tuple:
        node = self._primary()
        while True:
            t = self._peek()
            if t.kind == ".":
                self._next()
                name_tok = self._expect("IDENT")
                if self._peek().kind == "(":
                    node = ("call", node, name_tok.value, self._args())
                else:
                    node = ("member", node, name_tok.value)
            elif t.kind == "[":
                self._next()
                idx = self._conditional()
                self._expect("]")
                node = ("index", node, idx)
            else:
                return node

    def _primary(self) -> tuple:
        t = self._next()
        if t.kind == "NUM" or t.kind == "STR":
            return ("lit", t.value)
        if t.kind == "(":
            node = self._conditional()
            self._expect(")")
            return node
        if t.kind == "IDENT":
            low = t.value.lower()
            if low == "true":
                return ("lit", True)
            if low == "false":
                return ("lit", False)
            if low == "null":
                return ("lit", None)
            if self._peek().kind == "(":
                args = self._args()
                if low == "iif":
                    if len(args) != 3:
                        raise BpaParseError("iif() takes 3 arguments")
                    return ("cond", args[0], args[1], args[2])
                return ("call", None, t.value, args)
            return ("ident", t.value)
        raise BpaParseError(f"unexpected token {t.value!r} at {t.pos}")


# ───────────────────────────── regex helpers ───────────────────────────

_INLINE_FLAG_RE = re.compile(r"\(\?([imsx]+)\)")
_REGEX_CACHE: "OrderedDict[Tuple[str, int], re.Pattern[str]]" = OrderedDict()
_REGEX_CACHE_MAX = 1024
_REGEX_LOCK = threading.Lock()


def unescape_csharp(pattern: str) -> str:
    """Undo one level of C# string escaping (``\\\\`` -> ``\\``, ``\\"`` -> ``"``).

    Applied when a regex literal still carries doubled backslashes — the shape a
    rules file has when its patterns were JSON-escaped twice (I-01). A pattern
    written Tabular-Editor-style (single ``\\s``) is left untouched.
    """
    out: List[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n and pattern[i + 1] in ("\\", '"'):
            out.append(pattern[i + 1])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def translate_regex(pattern: str, flags: int = 0) -> Tuple[str, int]:
    """Translate a .NET regex into Python ``re`` syntax.

    - Doubled backslashes are C#-unescaped once (see :func:`unescape_csharp`).
    - Inline option groups ``(?i)`` / ``(?s)`` / ``(?m)`` / ``(?x)`` anywhere in
      the pattern become global flags (Python >= 3.11 rejects them mid-pattern;
      .NET scopes them from that point on — the global flag is the closest
      safe approximation for match/no-match rules).
    - ``(?<name>…)`` named groups become ``(?P<name>…)``; ``\\z`` becomes ``\\Z``.
    """
    if "\\\\" in pattern:
        pattern = unescape_csharp(pattern)

    def _flag(m: "re.Match[str]") -> str:
        nonlocal flags
        for ch in m.group(1):
            flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}[ch]
        return ""

    pattern = _INLINE_FLAG_RE.sub(_flag, pattern)
    pattern = re.sub(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>", r"(?P<\1>", pattern)
    pattern = pattern.replace("\\z", "\\Z")
    return pattern, flags


def compile_regex(pattern: str, flags: int = 0) -> "re.Pattern[str]":
    """Compile a (.NET-style) rule regex with a bounded cache.

    Raises :class:`BpaUnsupported` when the pattern cannot be compiled — a rule
    with a dead regex must be reported, not silently never match (I-01).
    """
    key = (pattern, flags)
    with _REGEX_LOCK:
        cached = _REGEX_CACHE.get(key)
        if cached is not None:
            _REGEX_CACHE.move_to_end(key)
            return cached
    py_pattern, py_flags = translate_regex(pattern, flags)
    try:
        compiled = re.compile(py_pattern, py_flags)
    except re.error as exc:
        raise BpaUnsupported(f"regex does not compile: {pattern!r} ({exc})") from exc
    with _REGEX_LOCK:
        _REGEX_CACHE[key] = compiled
        while len(_REGEX_CACHE) > _REGEX_CACHE_MAX:
            _REGEX_CACHE.popitem(last=False)
    return compiled


def _regex_flags(option: Any) -> int:
    if option is None:
        return 0
    if isinstance(option, EnumValue):
        option = option.name
    if isinstance(option, (int, float)) and not isinstance(option, bool):
        return re.IGNORECASE if int(option) & 1 else 0
    text = str(option).lower()
    flags = 0
    if "ignorecase" in text or "(?i)" in text or text.strip() == "i":
        flags |= re.IGNORECASE
    if "multiline" in text or "(?m)" in text:
        flags |= re.MULTILINE
    if "singleline" in text or "(?s)" in text:
        flags |= re.DOTALL
    return flags


def regex_literals(ast: tuple) -> List[Tuple[str, int]]:
    """Every ``RegEx.*`` call whose pattern (and option) are literals, as
    ``(pattern, flags)`` — so a rules file can be checked for dead regexes
    without evaluating anything."""
    found: List[Tuple[str, int]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, tuple) or not node:
            return
        if node[0] == "call":
            _, target, name, args = node
            if (
                isinstance(target, tuple)
                and target[0] == "ident"
                and target[1].lower() == "regex"
                and name.lower() in ("ismatch", "matches", "match", "replace")
                and len(args) >= 2
                and args[1][0] == "lit"
                and isinstance(args[1][1], str)
            ):
                flags = 0
                opt_idx = 3 if name.lower() == "replace" else 2
                if len(args) > opt_idx and args[opt_idx][0] == "lit":
                    flags = _regex_flags(args[opt_idx][1])
                found.append((args[1][1], flags))
        for child in node[1:]:
            if isinstance(child, tuple):
                walk(child)
            elif isinstance(child, list):
                for c in child:
                    walk(c)

    walk(ast)
    return found


# ───────────────────────────── compilation ─────────────────────────────


class CompiledExpression:
    """A parsed rule expression (immutable, shareable across threads)."""

    __slots__ = ("text", "ast")

    def __init__(self, text: str, ast: tuple) -> None:
        self.text = text
        self.ast = ast


def compile_expression(text: str) -> CompiledExpression:
    """Parse *text* and pre-compile its literal regexes.

    Raises :class:`BpaParseError` for invalid syntax and :class:`BpaUnsupported`
    for a literal regex that does not compile.
    """
    if not isinstance(text, str) or not text.strip():
        raise BpaParseError("empty expression")
    ast = _Parser(text).parse()
    for pattern, flags in regex_literals(ast):
        compile_regex(pattern, flags)
    return CompiledExpression(text, ast)


# ───────────────────────────── evaluation ──────────────────────────────


class _Scope:
    __slots__ = ("it", "outer", "root")

    def __init__(self, it: Any, outer: Optional["_Scope"], root: Any) -> None:
        self.it = it
        self.outer = outer
        self.root = root


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text_for_match(v: Any) -> str:
    """Text a regex/``Contains`` scans: comment/string-sanitised DAX when available."""
    if v is None:
        return ""
    sanitized = getattr(v, "sanitized", None)
    if isinstance(sanitized, str):
        return sanitized
    if isinstance(v, str):
        return v
    return str(v)


def _as_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _truthy_bool(v: Any, what: str) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        # TOM booleans are never null; a missing value means "false".
        return False
    raise BpaRuntimeError(f"{what}: expected a boolean, got {type(v).__name__} {v!r}")


def _eq(a: Any, b: Any) -> bool:
    if a is None or b is None:
        if a is None and b is None:
            return True
        # TOM strings are "" when unset; treat "" == null as C# would NOT —
        # but a missing object reference (None) vs "" is still unequal.
        return False
    if isinstance(a, EnumValue) or isinstance(b, EnumValue):
        ka = a.key() if isinstance(a, EnumValue) else str(a).lower()
        kb = b.key() if isinstance(b, EnumValue) else str(b).lower()
        return ka == kb
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_num(a) and _is_num(b):
        return a == b
    na = getattr(a, "bpa_name", None)
    nb = getattr(b, "bpa_name", None)
    if na is not None and nb is not None:
        return a is b
    if na is not None and isinstance(b, str):
        return str(na) == b
    if nb is not None and isinstance(a, str):
        return str(nb) == a
    if isinstance(a, str) and isinstance(b, str):
        return str.__eq__(str(a), str(b))
    return a == b


def _cmp(op: str, a: Any, b: Any) -> bool:
    if op == "==":
        return _eq(a, b)
    if op == "!=":
        return not _eq(a, b)
    if a is None or b is None:
        return False  # C# lifted comparison with null
    if isinstance(a, EnumValue) or isinstance(b, EnumValue):
        raise BpaRuntimeError(f"cannot order enum values ({a!r} {op} {b!r})")
    if _is_num(a) and _is_num(b):
        pass
    elif isinstance(a, str) and isinstance(b, str):
        a, b = str(a), str(b)
    else:
        raise BpaRuntimeError(f"cannot compare {type(a).__name__} {op} {type(b).__name__}")
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    return a >= b


def _arith(op: str, a: Any, b: Any) -> Any:
    if op == "+" and (isinstance(a, str) or isinstance(b, str)):
        return _as_str(a) + _as_str(b)
    if a is None or b is None:
        return None
    if isinstance(a, bool) or isinstance(b, bool) or not (_is_num(a) and _is_num(b)):
        raise BpaRuntimeError(f"cannot apply {op} to {type(a).__name__} and {type(b).__name__}")
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if b == 0:
            raise BpaRuntimeError("division by zero")
        if isinstance(a, int) and isinstance(b, int):
            return int(a / b)  # C# integer division truncates toward zero
        return a / b
    if op == "%":
        if b == 0:
            raise BpaRuntimeError("modulo by zero")
        return (
            math.fmod(a, b)
            if isinstance(a, float) or isinstance(b, float)
            else int(math.fmod(a, b))
        )
    raise BpaUnsupported(f"operator {op}")  # pragma: no cover


def _to_int64(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v))  # banker's rounding, as Convert.ToInt64(double)
    if isinstance(v, EnumValue):
        raise BpaRuntimeError("Convert.ToInt64 of an enum")
    text = str(v).strip()
    if text == "":
        raise BpaRuntimeError("Convert.ToInt64 of an empty string")
    try:
        return int(text)
    except ValueError as exc:
        raise BpaRuntimeError(f"Convert.ToInt64({text!r})") from exc


def _to_decimal(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if _is_num(v):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError as exc:
        raise BpaRuntimeError(f"Convert.ToDecimal({v!r})") from exc


def _ignore_case(arg: Any) -> bool:
    if arg is None:
        return False
    name = arg.name if isinstance(arg, EnumValue) else str(arg)
    return "ignorecase" in name.lower()


class Evaluator:
    """Evaluate compiled expressions against a model object graph.

    The object graph is duck-typed: nodes expose ``bpa_get(name)`` (raising
    :class:`BpaUnsupported` for an unknown member), optionally ``bpa_call(name,
    args)`` and ``bpa_index(key)``, and carry ``bpa_name``. Collections are
    Python lists (a list subclass may add members via ``bpa_get``).

    ``model`` is the root for the ``Model`` identifier. The per-run
    ``index_cache`` memoises equality lookups inside collection predicates
    (``Model.AllMeasures.Where(Name == current.Name)``); it is keyed on the
    collection's identity and bounded, and a new :class:`Evaluator` is built for
    every analysis run, so nothing leaks across runs (I-03).
    """

    def __init__(self, model: Any, *, index_cache_max: int = 256) -> None:
        self.model = model
        self._index_cache: "OrderedDict[Tuple[int, str], Dict[Any, List[Any]]]" = OrderedDict()
        self._index_cache_max = index_cache_max
        self.index_hits = 0
        self.index_misses = 0

    # public -------------------------------------------------------------
    def evaluate(self, compiled: CompiledExpression, obj: Any) -> Any:
        return self._eval(compiled.ast, _Scope(obj, None, obj))

    def cache_size(self) -> int:
        return len(self._index_cache)

    # core ---------------------------------------------------------------
    def _eval(self, node: tuple, scope: _Scope) -> Any:
        kind = node[0]
        if kind == "lit":
            return node[1]
        if kind == "ident":
            return self._ident(node[1], scope)
        if kind == "member":
            target = node[1]
            if target[0] == "ident" and target[1].lower() in ENUM_TYPES:
                return EnumValue(node[2], target[1])
            return self._member(self._eval(target, scope), node[2])
        if kind == "call":
            return self._call(node, scope)
        if kind == "index":
            return self._index(self._eval(node[1], scope), self._eval(node[2], scope))
        if kind == "and":
            left = _truthy_bool(self._eval(node[1], scope), "and")
            if not left:
                return False
            return _truthy_bool(self._eval(node[2], scope), "and")
        if kind == "or":
            left = _truthy_bool(self._eval(node[1], scope), "or")
            if left:
                return True
            return _truthy_bool(self._eval(node[2], scope), "or")
        if kind == "unary":
            val = self._eval(node[2], scope)
            if node[1] == "!":
                return not _truthy_bool(val, "not")
            if val is None:
                return None
            if not _is_num(val):
                raise BpaRuntimeError(f"cannot negate {type(val).__name__}")
            return -val
        if kind == "bin":
            op = node[1]
            a = self._eval(node[2], scope)
            b = self._eval(node[3], scope)
            if op in ("==", "!=", "<", "<=", ">", ">="):
                return _cmp(op, a, b)
            return _arith(op, a, b)
        if kind == "cond":
            test = _truthy_bool(self._eval(node[1], scope), "iif")
            return self._eval(node[2] if test else node[3], scope)
        raise BpaUnsupported(f"AST node {kind}")  # pragma: no cover

    def _ident(self, name: str, scope: _Scope) -> Any:
        low = name.lower()
        if low == "it":
            return scope.it
        if low == "outerit":
            return scope.outer.it if scope.outer is not None else scope.it
        if low == "current":
            return scope.root
        if low == "model":
            return self.model
        if low in _STATIC_TYPES:
            return TypeRef(_STATIC_TYPES[low])
        return self._member(scope.it, name)

    def _member(self, obj: Any, name: str) -> Any:
        low = name.lower()
        if obj is None:
            raise BpaRuntimeError(f"member {name} of null")
        getter = getattr(obj, "bpa_get", None)
        if getter is not None:
            return getter(name)
        if isinstance(obj, list):
            if low in ("count", "length"):
                return len(obj)
            raise BpaUnsupported(f"collection member {name}")
        if isinstance(obj, str):
            if low == "length":
                return len(obj)
            raise BpaUnsupported(f"string member {name}")
        if isinstance(obj, EnumValue):
            if low in ("name", "value"):
                return obj.name
            raise BpaUnsupported(f"enum member {name}")
        if isinstance(obj, MatchResult):
            if low == "success":
                return obj.success
            if low == "value":
                return obj.value
            raise BpaUnsupported(f"Match member {name}")
        if isinstance(obj, TypeRef):
            if obj.name == "string" and low == "empty":
                return ""
            raise BpaUnsupported(f"static member {obj.name}.{name}")
        raise BpaUnsupported(f"member {name} on {type(obj).__name__}")

    def _index(self, obj: Any, key: Any) -> Any:
        indexer = getattr(obj, "bpa_index", None)
        if indexer is not None:
            return indexer(key)
        if isinstance(obj, (list, str)):
            if not _is_num(key):
                raise BpaRuntimeError(f"index {key!r} is not a number")
            k = int(key)
            if k < 0 or k >= len(obj):
                raise BpaRuntimeError(f"index {k} out of range")
            return obj[k]
        raise BpaUnsupported(f"indexer on {type(obj).__name__}")

    # calls --------------------------------------------------------------
    def _call(self, node: tuple, scope: _Scope) -> Any:
        _, target_ast, name, args = node
        low = name.lower()
        if target_ast is None:
            return self._function(low, name, args, scope)
        if target_ast[0] == "ident" and target_ast[1].lower() in _STATIC_TYPES:
            return self._static(_STATIC_TYPES[target_ast[1].lower()], low, name, args, scope)
        target = self._eval(target_ast, scope)
        if isinstance(target, TypeRef):
            return self._static(target.name, low, name, args, scope)
        if isinstance(target, list):
            return self._collection(target, low, name, args, scope)
        if isinstance(target, str):
            return self._string_method(target, low, name, [self._eval(a, scope) for a in args])
        if low == "tostring" and not args and not hasattr(target, "bpa_call"):
            if target is None:
                raise BpaRuntimeError("ToString() of null")
            return _as_str(target)
        caller = getattr(target, "bpa_call", None)
        if caller is not None:
            return caller(name, [self._eval(a, scope) for a in args])
        if target is None:
            raise BpaRuntimeError(f"{name}() on null")
        if low == "equals" and len(args) == 1:
            return _eq(target, self._eval(args[0], scope))
        if low == "hasflag" and len(args) == 1:
            return _eq(target, self._eval(args[0], scope))
        raise BpaUnsupported(f"method {name} on {type(target).__name__}")

    def _function(self, low: str, name: str, args: List[tuple], scope: _Scope) -> Any:
        vals = [self._eval(a, scope) for a in args]
        if low == "char" and len(vals) == 1:
            if not _is_num(vals[0]):
                raise BpaRuntimeError("char() needs a number")
            return chr(int(vals[0]))
        if low in ("int", "int64", "int32", "long") and len(vals) == 1:
            return _to_int64(vals[0])
        if low in ("decimal", "double") and len(vals) == 1:
            return _to_decimal(vals[0])
        # Anything else is a method of the implicit ``it`` (GetAnnotation("x")).
        caller = getattr(scope.it, "bpa_call", None)
        if caller is not None:
            return caller(name, vals)
        raise BpaUnsupported(f"function {name}()")

    def _static(self, type_name: str, low: str, name: str, args: List[tuple], scope: _Scope) -> Any:
        vals = [self._eval(a, scope) for a in args]
        if type_name == "RegEx":
            return self._regex(low, name, vals)
        if type_name == "DaxLint":
            return self._daxlint(low, name, vals)
        if type_name == "string":
            if low in ("isnullorwhitespace",) and len(vals) == 1:
                v = vals[0]
                return v is None or _as_str(getattr(v, "bpa_name", v)).strip() == ""
            if low == "isnullorempty" and len(vals) == 1:
                v = vals[0]
                return v is None or _as_str(getattr(v, "bpa_name", v)) == ""
            if low == "concat":
                return "".join(_as_str(v) for v in vals)
            if low == "join" and len(vals) == 2 and isinstance(vals[1], list):
                return _as_str(vals[0]).join(_as_str(v) for v in vals[1])
            if low == "equals" and len(vals) >= 2:
                if len(vals) == 3 and _ignore_case(vals[2]):
                    return _as_str(vals[0]).lower() == _as_str(vals[1]).lower()
                return _eq(vals[0], vals[1])
            raise BpaUnsupported(f"string.{name}")
        if type_name == "Convert":
            if len(vals) != 1:
                raise BpaUnsupported(f"Convert.{name} with {len(vals)} args")
            if low in ("toint64", "toint32", "toint16", "tobyte"):
                return _to_int64(vals[0])
            if low in ("todecimal", "todouble", "tosingle"):
                return _to_decimal(vals[0])
            if low == "tostring":
                return _as_str(vals[0])
            if low == "toboolean":
                v = vals[0]
                if isinstance(v, bool):
                    return v
                if v is None:
                    return False
                if isinstance(v, str) and v.strip().lower() in ("true", "false"):
                    return v.strip().lower() == "true"
                if _is_num(v):
                    return v != 0
                raise BpaRuntimeError(f"Convert.ToBoolean({v!r})")
            raise BpaUnsupported(f"Convert.{name}")
        if type_name == "Math":
            nums = []
            for v in vals:
                if v is None:
                    raise BpaRuntimeError(f"Math.{name} of null")
                if not _is_num(v):
                    raise BpaRuntimeError(f"Math.{name} of {type(v).__name__}")
                nums.append(v)
            if low == "max" and len(nums) == 2:
                return max(nums)
            if low == "min" and len(nums) == 2:
                return min(nums)
            if low == "abs" and len(nums) == 1:
                return abs(nums[0])
            if low == "round" and len(nums) in (1, 2):
                return round(nums[0], int(nums[1]) if len(nums) == 2 else 0)
            if low == "floor" and len(nums) == 1:
                return math.floor(nums[0])
            if low == "ceiling" and len(nums) == 1:
                return math.ceil(nums[0])
            raise BpaUnsupported(f"Math.{name}")
        if type_name == "char":
            if len(vals) != 1:
                raise BpaUnsupported(f"char.{name}")
            c = _as_str(vals[0])
            if len(c) != 1:
                raise BpaRuntimeError(f"char.{name} expects one character")
            if low == "iscontrol":
                import unicodedata

                return unicodedata.category(c) == "Cc"
            if low == "iswhitespace":
                return c.isspace()
            if low == "isletter":
                return c.isalpha()
            if low == "isdigit":
                return c.isdigit()
            if low == "isletterordigit":
                return c.isalnum()
            if low == "isupper":
                return c.isupper()
            if low == "islower":
                return c.islower()
            if low == "ispunctuation":
                import unicodedata

                return unicodedata.category(c).startswith("P")
            raise BpaUnsupported(f"char.{name}")
        if type_name in ("int", "decimal", "double") and low == "parse" and len(vals) == 1:
            return _to_int64(vals[0]) if type_name == "int" else _to_decimal(vals[0])
        raise BpaUnsupported(f"{type_name}.{name}")

    def _daxlint(self, low: str, name: str, vals: List[Any]) -> Any:
        """``DaxLint.*`` - DAX checks run on the lexer / the shared analyzer instead of regexes
        over text (2026-10-03 review D-15: two heuristic engines disagreed on the same measure).

        - ``DaxLint.Fires(expr, ["minSeverity",] "RULE_ID", ...)``: the unified analyzer reports
          one of the rule ids at ``minSeverity`` (default ``low``) or worse;
        - ``DaxLint.HasFunction(expr, "SUM", ...)``: a call to one of the functions (a ``[SUM x]``
          name or a string never matches);
        - ``DaxLint.CountFunction(expr, "CALCULATE")``: number of calls to that function.
        """
        if not vals:
            raise BpaUnsupported(f"DaxLint.{name} needs an expression")
        text = "" if vals[0] is None else str(vals[0])
        args = [_as_str(v) for v in vals[1:]]
        if low == "fires":
            sev = "low"
            if args and args[0].lower() in _DAXLINT_SEVERITY:
                sev = args.pop(0).lower()
            if not args:
                raise BpaUnsupported("DaxLint.Fires needs at least one rule id")
            limit = _DAXLINT_SEVERITY[sev]
            wanted = {a.upper() for a in args}
            return any(
                rid.upper() in wanted and _DAXLINT_SEVERITY.get(sv, 9) <= limit
                for rid, sv in _daxlint_findings(text)
            )
        if low in ("hasfunction", "countfunction") and args:
            counts = _dax_function_counts(text)
            names = {a.upper() for a in args}
            n = sum(c for fn, c in counts.items() if fn in names)
            return n > 0 if low == "hasfunction" else n
        raise BpaUnsupported(f"DaxLint.{name}")

    def _regex(self, low: str, name: str, vals: List[Any]) -> Any:
        if low == "escape" and len(vals) == 1:
            return re.escape(_as_str(vals[0]))
        if low in ("ismatch", "matches", "match") and len(vals) in (2, 3):
            text = _text_for_match(vals[0])
            flags = _regex_flags(vals[2]) if len(vals) == 3 else 0
            rx = compile_regex(_as_str(vals[1]), flags)
            if low == "ismatch":
                return rx.search(text) is not None
            if low == "match":
                return MatchResult(rx.search(text))
            return [m.group(0) for m in rx.finditer(text)]
        if low == "replace" and len(vals) in (3, 4):
            flags = _regex_flags(vals[3]) if len(vals) == 4 else 0
            rx = compile_regex(_as_str(vals[1]), flags)
            repl = re.sub(r"\$(\d+)", r"\\\1", _as_str(vals[2]))
            return rx.sub(repl, _as_str(vals[0]))
        raise BpaUnsupported(f"RegEx.{name}")

    def _string_method(self, s: str, low: str, name: str, vals: List[Any]) -> Any:
        scan = _text_for_match(s)
        if low == "contains" and len(vals) in (1, 2):
            needle = _as_str(vals[0])
            if len(vals) == 2 and _ignore_case(vals[1]):
                return needle.lower() in scan.lower()
            return needle in scan
        if low in ("startswith", "endswith") and len(vals) in (1, 2):
            needle = _as_str(vals[0])
            hay = str(s)
            if len(vals) == 2 and _ignore_case(vals[1]):
                hay, needle = hay.lower(), needle.lower()
            return hay.startswith(needle) if low == "startswith" else hay.endswith(needle)
        if low == "indexof" and 1 <= len(vals) <= 3:
            needle = _as_str(vals[0])
            hay = scan
            start = 0
            for extra in vals[1:]:
                if _is_num(extra):
                    start = int(extra)
                elif _ignore_case(extra):
                    hay, needle = hay.lower(), needle.lower()
            return hay.find(needle, start)
        if low in ("toupper", "toupperinvariant") and not vals:
            return str(s).upper()
        if low in ("tolower", "tolowerinvariant") and not vals:
            return str(s).lower()
        if low in ("trim", "trimstart", "trimend"):
            chars = "".join(_as_str(v) for v in vals) if vals else None
            base = str(s)
            if low == "trim":
                return base.strip(chars) if chars else base.strip()
            if low == "trimstart":
                return base.lstrip(chars) if chars else base.lstrip()
            return base.rstrip(chars) if chars else base.rstrip()
        if low == "replace" and len(vals) == 2:
            old = _as_str(vals[0])
            if old == "":
                raise BpaRuntimeError("Replace with an empty search string")
            return str(s).replace(old, _as_str(vals[1]))
        if low == "substring" and len(vals) in (1, 2):
            base = str(s)
            if not all(_is_num(v) for v in vals):
                raise BpaRuntimeError("Substring needs numbers")
            start = int(vals[0])
            if start < 0 or start > len(base):
                raise BpaRuntimeError("Substring start out of range")
            if len(vals) == 1:
                return base[start:]
            length = int(vals[1])
            if length < 0 or start + length > len(base):
                raise BpaRuntimeError("Substring length out of range")
            return base[start : start + length]
        if low == "tochararray" and not vals:
            return list(str(s))
        if low == "split" and vals:
            sep = _as_str(vals[0])
            return str(s).split(sep) if sep else list(str(s))
        if low == "tostring" and not vals:
            return str(s)
        if low == "equals" and len(vals) in (1, 2):
            if len(vals) == 2 and _ignore_case(vals[1]):
                return str(s).lower() == _as_str(vals[0]).lower()
            return _eq(str(s), vals[0])
        if low == "normalize" and not vals:
            return str(s)
        raise BpaUnsupported(f"string method {name}")

    # collections --------------------------------------------------------
    def _lambda(self, body: tuple, scope: _Scope) -> Callable[[Any], Any]:
        def run(item: Any) -> Any:
            return self._eval(body, _Scope(item, scope, scope.root))

        return run

    def _collection(
        self, items: List[Any], low: str, name: str, args: List[tuple], scope: _Scope
    ) -> Any:
        if low == "all":
            if len(args) != 1:
                raise BpaUnsupported("All() needs exactly one predicate")
            pred = self._lambda(args[0], scope)
            return all(_truthy_bool(pred(i), "All") for i in items)
        if low in ("any", "count", "where", "first", "firstordefault", "single"):
            pred_ast = args[0] if args else None
            if len(args) > 1:
                raise BpaUnsupported(f"{name} with {len(args)} arguments")
            if pred_ast is None:
                if low == "any":
                    return len(items) > 0
                if low == "count":
                    return len(items)
                if low in ("first", "single"):
                    if not items:
                        raise BpaRuntimeError(f"{name}() of an empty sequence")
                    return items[0]
                return items[0] if items else None
            matches = self._filter(
                items, pred_ast, scope, stop_at_first=low in ("any", "first", "firstordefault")
            )
            if low == "any":
                return bool(matches)
            if low == "count":
                return len(matches)
            if low == "where":
                return matches
            if low in ("first", "single"):
                if not matches:
                    raise BpaRuntimeError(f"{name}() found no element")
                return matches[0]
            return matches[0] if matches else None
        if low == "select":
            if len(args) != 1:
                raise BpaUnsupported("Select needs one selector")
            sel = self._lambda(args[0], scope)
            return [sel(i) for i in items]
        if low in ("sum", "max", "min", "average"):
            if len(args) > 1:
                raise BpaUnsupported(f"{name} with {len(args)} arguments")
            values = [self._lambda(args[0], scope)(i) for i in items] if args else list(items)
            nums = [v for v in values if v is not None]
            if any(not _is_num(v) for v in nums):
                raise BpaRuntimeError(f"{name} over non-numeric values")
            if low == "sum":
                return sum(nums) if nums else 0
            if not nums:
                if low == "average":
                    raise BpaRuntimeError("Average of an empty sequence")
                raise BpaRuntimeError(f"{name} of an empty sequence")
            if low == "max":
                return max(nums)
            if low == "min":
                return min(nums)
            return sum(nums) / len(nums)
        if low == "contains" and len(args) == 1:
            val = self._eval(args[0], scope)
            return any(_eq(i, val) for i in items)
        if low == "distinct" and not args:
            out: List[Any] = []
            for i in items:
                if not any(_eq(i, o) for o in out):
                    out.append(i)
            return out
        if low in ("tolist", "toarray", "asenumerable") and not args:
            return list(items)
        if low == "orderby" and len(args) == 1:
            sel = self._lambda(args[0], scope)
            try:
                return sorted(
                    items, key=lambda i: _as_str(sel(i)) if not _is_num(sel(i)) else sel(i)
                )
            except TypeError as exc:
                raise BpaRuntimeError("OrderBy over mixed types") from exc
        getter = getattr(items, "bpa_call", None)
        if getter is not None:
            return getter(name, [self._eval(a, scope) for a in args])
        raise BpaUnsupported(f"collection method {name}")

    # Equality fast path (I-03): ``coll.Where(Prop == <expr independent of it>)``
    # is answered from a per-run hash index instead of re-evaluating the
    # predicate for every item — keeps O(n^2) rules (duplicate names) linear.
    def _filter(
        self, items: List[Any], pred_ast: tuple, scope: _Scope, *, stop_at_first: bool
    ) -> List[Any]:
        pred: Optional[Callable[[Any], Any]] = self._lambda(pred_ast, scope)
        candidates: Sequence[Any] = items
        fast = self._equality_split(pred_ast) if len(items) > 16 else None
        if fast is not None:
            key_ast, other_ast, residual_ast = fast
            try:
                key_val = self._eval(other_ast, _Scope(None, scope, scope.root))
            except BpaExpressionError:
                key_val = _NO_KEY
            if key_val is not _NO_KEY and _plain_key(key_val):
                index = self._equality_index(items, key_ast)
                if index is not None:
                    candidates = index.get(_index_key(key_val), ())
                    # Every candidate already satisfies the indexed conjunct (a plain-key hash
                    # lookup reproduces ``_eq`` exactly), so only the REST of the predicate is
                    # evaluated - the conjunctive form ``Name == x and Table.Name != y and ...``
                    # no longer re-checks the equality for each of the ~100 same-name candidates
                    # (2026-10-03 review P-18). No residual -> every candidate matches.
                    pred = self._lambda(residual_ast, scope) if residual_ast is not None else None
        out: List[Any] = []
        for item in candidates:
            if pred is None or _truthy_bool(pred(item), "predicate"):
                out.append(item)
                if stop_at_first:
                    break
        return out

    @staticmethod
    def _equality_split(ast: tuple) -> Optional[Tuple[tuple, tuple, Optional[tuple]]]:
        """Find a conjunct ``<item-only expr> == <item-independent expr>``.

        Returns ``(key_side, other_side, residual)`` where ``residual`` is the AND of every OTHER
        conjunct (None when the equality is the whole predicate). The index narrows the
        candidates to the items satisfying the equality; the residual decides the rest.
        """
        conjuncts: List[tuple] = []

        def flatten(node: tuple) -> None:
            if node[0] == "and":
                flatten(node[1])
                flatten(node[2])
            else:
                conjuncts.append(node)

        flatten(ast)
        for pos, node in enumerate(conjuncts):
            if node[0] != "bin" or node[1] != "==":
                continue
            left, right = node[2], node[3]
            for key_side, other in ((left, right), (right, left)):
                if _uses_it(key_side) and _only_item(key_side) and not _uses_it(other):
                    rest = conjuncts[:pos] + conjuncts[pos + 1 :]
                    residual: Optional[tuple] = None
                    for c in rest:
                        residual = c if residual is None else ("and", residual, c)
                    return key_side, other, residual
        return None

    def _equality_index(self, items: List[Any], key_ast: tuple) -> Optional[Dict[Any, List[Any]]]:
        key = (id(items), repr(key_ast))
        cached = self._index_cache.get(key)
        if cached is not None:
            self.index_hits += 1
            self._index_cache.move_to_end(key)
            return cached
        self.index_misses += 1
        index: Dict[Any, List[Any]] = {}
        try:
            for item in items:
                value = self._eval(key_ast, _Scope(item, None, item))
                if not _plain_key(value):
                    # Enum / object values use non-hash equality — no fast path.
                    return None
                index.setdefault(_index_key(value), []).append(item)
        except BpaExpressionError:
            return None
        # Keep ``items`` alive with the index so id() cannot be reused mid-run.
        index[_KEEPALIVE] = items  # type: ignore[index]
        self._index_cache[key] = index
        while len(self._index_cache) > self._index_cache_max:
            self._index_cache.popitem(last=False)
        return index


_NO_KEY = object()
_KEEPALIVE = object()

_DAXLINT_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@lru_cache(maxsize=8192)
def _daxlint_findings(text: str) -> Tuple[Tuple[str, str], ...]:
    """``(rule_id, severity)`` of every finding the shared analyzer reports for *text*."""
    from core.dax.analyzer.unified_analyzer import get_shared_analyzer

    try:
        res = get_shared_analyzer().analyze(text)
    except Exception:  # noqa: BLE001 - a lint failure must not break a BPA run
        return ()
    return tuple((i.rule_id, i.severity) for i in res.issues)


@lru_cache(maxsize=8192)
def _dax_function_counts(text: str) -> Dict[str, int]:
    """Upper-case name -> number of calls (``NAME(``) in *text*, lexer-based."""
    from core.dax.dax_reference_parser import get_lexer
    from core.dax.tokenizer import TokenType

    code = get_lexer().tokenize_code(text)
    counts: Dict[str, int] = {}
    for i, t in enumerate(code[:-1]):
        if (
            t.type in (TokenType.FUNCTION, TokenType.KEYWORD, TokenType.IDENTIFIER)
            and code[i + 1].type == TokenType.PAREN_OPEN
        ):
            counts[t.value.upper()] = counts.get(t.value.upper(), 0) + 1
    return counts


def _plain_key(v: Any) -> bool:
    """Values whose ``_eq`` semantics a hash lookup reproduces exactly."""
    if v is None or isinstance(v, (bool, int, float)):
        return True
    return isinstance(v, str) and getattr(v, "bpa_name", None) is None


def _index_key(v: Any) -> Any:
    if v is None:
        return ("null",)
    if isinstance(v, bool):
        return ("bool", v)
    if _is_num(v):
        return ("num", float(v))
    return ("str", str(v))


def _only_item(ast: Any) -> bool:
    """True when *ast* depends on nothing but the lambda item (no outerIt/current)."""
    if not isinstance(ast, tuple) or not ast:
        return True
    kind = ast[0]
    if kind == "ident":
        return ast[1].lower() not in ("outerit", "current")
    if kind == "call":
        _, target, _name, args = ast
        return (target is None or _only_item(target)) and all(_only_item(a) for a in args)
    return all(_only_item(c) for c in ast[1:] if isinstance(c, tuple))


def _uses_it(ast: Any) -> bool:
    """True when *ast* may depend on the lambda's implicit ``it``."""
    if not isinstance(ast, tuple) or not ast:
        return False
    kind = ast[0]
    if kind == "lit":
        return False
    if kind == "ident":
        low = ast[1].lower()
        return low not in ("outerit", "current", "model") and low not in _STATIC_TYPES
    if kind == "member":
        target = ast[1]
        if target[0] == "ident" and target[1].lower() in ENUM_TYPES:
            return False
        return _uses_it(target)
    if kind == "call":
        _, target, _name, args = ast
        if target is None:
            return True  # GetAnnotation("x") reads it
        if target[0] == "ident" and target[1].lower() in _STATIC_TYPES:
            return any(_uses_it(a) for a in args)
        # A nested lambda's own ``it`` is its item, but a bare identifier in it
        # may still be the outer item's property only via outerIt — be safe.
        return _uses_it(target) or any(_uses_it(a) for a in args)
    return any(_uses_it(c) for c in ast[1:] if isinstance(c, tuple))
