"""Deterministic local DAX formatter (2026-10-03 deep review, F-23).

``02_TMDL_Operations format_dax engine=local``: no network, no third party. The
daxformatter.com client posts measure text (client-specific logic) to an external
service; this module formats on the machine with the repo lexer.

Style (SQLBI long form, ``skip_space_after_function`` switches to ``SUM( x )``)::

    VAR _x =
        CALCULATE (
            [Sales],
            'Date'[Year] = 2024
        )
    RETURN
        DIVIDE ( _x, [Cost] )

- function / keyword upper-casing (built-ins only: a user-defined function, a VAR
  name, a table name keeps its case); spaces around binary operators, none after a
  unary sign; ``FUNC ( a, b )`` / ``{ a, b }`` / ``( a + b )``;
- a call that fits ``max_line_length`` stays on one line; otherwise one argument per
  line (4 spaces per nesting level, closing paren on its own line); a too long
  operator chain breaks before its lowest-precedence operator;
- VAR/RETURN blocks, lambdas (``(x : T) =>``), ``DEFINE`` / ``EVALUATE`` / ``ORDER BY``
  queries and UDF ``///`` doc comments;
- comments (``//``, ``--``, ``/* */``, ``///``) and string literals are carried byte
  exact. A trailing comment stays at the end of its line; a comment on its own line
  stays on its own line; one blank line between statements is kept;
- the output is a pure function of the token stream (+ comment attachment), so
  ``format(format(x)) == format(x)``.

Fail-safe: every result is checked with the same token-equivalence test the
daxformatter client uses (``dax_formatter_client.token_equivalent``) plus "the comment
sequence is unchanged". A text that is not provably the same DAX is returned with
``success: False`` and is never written by the handler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from core.dax.tokenizer import DaxLexer, Token, TokenType

__all__ = ["DaxFormatError", "format_dax_local", "format_many_local"]

_INDENT = 4
_DEFAULT_LINE_LEN = 90

# Built-ins the function database may not list (case is only normalised for names that are
# KNOWN built-ins; a user-defined function keeps the author's spelling).
_EXTRA_BUILTINS = frozenset(
    {
        "SELECTEDMEASURE",
        "SELECTEDMEASURENAME",
        "SELECTEDMEASUREFORMATSTRING",
        "ISSELECTEDMEASURE",
        "ISBLANK",
        "ISEMPTY",
        "ISERROR",
        "ISLOGICAL",
        "ISNONTEXT",
        "ISNUMBER",
        "ISTEXT",
        "ISSTRING",
        "ISBOOLEAN",
        "ISDOUBLE",
        "ISINT64",
        "ISINTEGER",
        "ISDECIMAL",
        "ISCURRENCY",
        "ISDATETIME",
        "ISNUMERIC",
        "ISFILTERED",
        "ISCROSSFILTERED",
        "ISINSCOPE",
        "ISATLEVEL",
        "HASONEVALUE",
        "HASONEFILTER",
        "SELECTEDVALUE",
        "NAMEOF",
        "TABLEOF",
        "TOCSV",
        "TOJSON",
        "USERCULTURE",
        "USEROBJECTID",
        "USERNAME",
        "USERPRINCIPALNAME",
        "CUSTOMDATA",
        "EXTERNALMEASURE",
        "EVALUATEANDLOG",
        "TEXTCONTAINS",
        "TEXTSIMILARITY",
        "APPROXIMATEDISTINCTCOUNT",
        "REMOVEFILTERS",
        "KEEPFILTERS",
        "TREATAS",
        "BLANK",
        "TODAY",
        "NOW",
        "UTCNOW",
        "UTCTODAY",
        "ERROR",
        "DIVIDE",
        "COALESCE",
        "COUNTROWS",
        "DISTINCTCOUNT",
        "DISTINCTCOUNTNOBLANK",
        "RUNNINGSUM",
        "MOVINGAVERAGE",
        "COLLAPSE",
        "COLLAPSEALL",
        "EXPAND",
        "EXPANDALL",
        "PERCENTOFPARENT",
        "LOOKUP",
        "LOOKUPWITHTOTALS",
        "RANGE",
        "PREVIOUS",
        "NEXT",
        "FIRST",
        "LAST",
        "ORDERBY",
        "PARTITIONBY",
        "MATCHBY",
        "WINDOW",
        "OFFSET",
        "INDEX",
        "RANK",
        "ROWNUMBER",
    }
)

# Keywords that are safe to upper-case wherever they appear: they cannot be a table / VAR
# name that the author chose (an unquoted ``Table`` / ``Start`` / ``At`` could be).
_UPPER_KEYWORDS = frozenset(
    {
        "VAR",
        "RETURN",
        "IF",
        "SWITCH",
        "TRUE",
        "FALSE",
        "AND",
        "OR",
        "NOT",
        "IN",
        "EVALUATE",
        "DEFINE",
        "FUNCTION",
        "ASC",
        "DESC",
    }
)

# Keywords that can head a call (``TRUE ()``, ``NOT ( x )``) - everything else
# (RETURN, IN, VAR, EVALUATE ...) followed by "(" is just a keyword and a group.
_HEAD_KEYWORDS = frozenset({"IF", "SWITCH", "AND", "OR", "NOT", "TRUE", "FALSE"})

# Binary operator precedence (low -> high); the layout breaks before the lowest present.
_PRECEDENCE = {
    "||": 1,
    "&&": 2,
    "=": 3,
    "==": 3,
    "<>": 3,
    "<": 3,
    ">": 3,
    "<=": 3,
    ">=": 3,
    "&": 4,
    "+": 5,
    "-": 5,
    "*": 6,
    "/": 6,
    "^": 7,
}

_UNARY_AFTER_KEYWORDS = frozenset({"RETURN", "IN", "NOT", "AND", "OR", "ELSE"})
_QUERY_SECTION = frozenset({"DEFINE", "EVALUATE"})
_DEFINITION_KEYWORDS = frozenset({"MEASURE", "FUNCTION", "COLUMN", "TABLE"})


class DaxFormatError(ValueError):
    """The text cannot be laid out safely (unbalanced brackets, unterminated token)."""


# ---------------------------------------------------------------------------
# Tree
# ---------------------------------------------------------------------------


@dataclass
class _Comment:
    text: str
    is_line: bool
    multiline: bool
    blank_before: bool


@dataclass(eq=False)
class _Atom:
    tok: Token
    ws_before: bool
    blank_before: bool
    lead: List[_Comment] = field(default_factory=list)  # own-line comments before the token
    inline: List[_Comment] = field(default_factory=list)  # /* c */ before the token, same line
    trail: List[_Comment] = field(default_factory=list)  # comments after it, end of line
    after: List[_Comment] = field(default_factory=list)  # /* c */ right after it, before , or )
    unary: bool = False
    text: str = ""

    @property
    def type(self) -> TokenType:
        return self.tok.type

    @property
    def value(self) -> str:
        return self.tok.value

    @property
    def multiline(self) -> bool:
        if "\n" in self.tok.value or "\r" in self.tok.value:
            return True
        return any(c.multiline for c in self.inline + self.after)


@dataclass(eq=False)
class _Seq:
    items: List[Union["_Atom", "_Group"]] = field(default_factory=list)


@dataclass(eq=False)
class _Group:
    head: Optional[_Atom]
    open: _Atom
    args: List[_Seq]
    commas: List[_Atom]
    close: _Atom
    lambda_params: bool = False

    @property
    def braces(self) -> bool:
        return self.open.type == TokenType.BRACE_OPEN


_Item = Union[_Atom, _Group]


def _first_atom(it: _Item) -> _Atom:
    return it if isinstance(it, _Atom) else (it.head or it.open)


def _last_atom(it: _Item) -> _Atom:
    return it if isinstance(it, _Atom) else it.close


def _atoms_of(it: _Item) -> List[_Atom]:
    if isinstance(it, _Atom):
        return [it]
    out: List[_Atom] = []
    if it.head is not None:
        out.append(it.head)
    out.append(it.open)
    for i, arg in enumerate(it.args):
        for sub in arg.items:
            out.extend(_atoms_of(sub))
        if i < len(it.commas):
            out.append(it.commas[i])
    out.append(it.close)
    return out


def _atoms_of_items(items: Sequence[_Item]) -> List[_Atom]:
    out: List[_Atom] = []
    for it in items:
        out.extend(_atoms_of(it))
    return out


def _hard(atoms: Sequence[_Atom]) -> bool:
    """True when the atoms cannot share a line: an own-line comment, a multi-line token or
    comment, or a comment that must end its line followed by more tokens."""
    n = len(atoms)
    for i, a in enumerate(atoms):
        if (a.lead and i > 0) or a.multiline:
            return True  # (own-line comments before the FIRST atom are emitted outside the text)
        if i < n - 1 and a.trail:
            return True
        if a.type == TokenType.KEYWORD and a.value.upper() in ("VAR", "RETURN"):
            return True  # a VAR block always gets its own lines
    return False


def _is_head(a: _Atom) -> bool:
    if a.type in (TokenType.FUNCTION, TokenType.IDENTIFIER):
        return True
    return a.type == TokenType.KEYWORD and a.value.upper() in _HEAD_KEYWORDS


# ---------------------------------------------------------------------------
# Comment attachment + parse
# ---------------------------------------------------------------------------


def _count_newlines(text: str) -> int:
    return (
        text.count("\r\n")
        + text.replace("\r\n", "").count("\n")
        + text.replace("\r\n", "").count("\r")
    )


def _attach(tokens: List[Token]) -> Tuple[List[_Atom], List[_Comment]]:
    atoms: List[_Atom] = []
    leads: List[_Comment] = []
    inlines: List[_Comment] = []
    prev: Optional[_Atom] = None
    nl_run = 0  # NEWLINE tokens since the last code token / comment (blank-line detection)
    nl_since_code = 0  # newlines since the last code token
    n = len(tokens)
    for i, t in enumerate(tokens):
        tt = t.type
        if tt == TokenType.WHITESPACE:
            continue
        if tt == TokenType.NEWLINE:
            nl_run += 1
            nl_since_code += 1
            continue
        if tt in (TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK):
            if not t.terminated:
                raise DaxFormatError("unterminated block comment")
            is_line = tt == TokenType.COMMENT_LINE
            ml = (not is_line) and ("\n" in t.value or "\r" in t.value)
            c = _Comment(t.value.rstrip() if is_line else t.value, is_line, ml, nl_run >= 2)
            code_follows = False
            before_closer = False
            if not is_line:
                j = i + 1
                while j < n:
                    tj = tokens[j]
                    if tj.type == TokenType.WHITESPACE:
                        j += 1
                        continue
                    if (
                        tj.type == TokenType.COMMENT_BLOCK
                        and "\n" not in tj.value
                        and "\r" not in tj.value
                    ):
                        j += 1
                        continue
                    code_follows = tj.type not in (
                        TokenType.NEWLINE,
                        TokenType.COMMENT_LINE,
                        TokenType.COMMENT_BLOCK,
                    )
                    before_closer = tj.type in (
                        TokenType.COMMA,
                        TokenType.PAREN_CLOSE,
                        TokenType.BRACE_CLOSE,
                    )
                    break
            if code_follows and before_closer:
                # "x /* c */ , y" / "x /* c */ )": stays glued to the previous token; anything
                # that would reorder against a pending comment goes on its own line instead
                if prev is not None and not ml and not leads and not prev.trail:
                    prev.after.append(c)
                else:
                    leads.append(c)
            elif code_follows:
                inlines.append(c)
            elif prev is not None and nl_since_code == 0:
                prev.trail.append(c)
            else:
                leads.append(c)
            nl_run = 0
            if ml:
                nl_since_code += _count_newlines(t.value)
            continue
        if not t.terminated:
            raise DaxFormatError(f"unterminated token {t.value[:20]!r}")
        a = _Atom(
            tok=t,
            ws_before=prev is not None and t.start != prev.tok.end,
            blank_before=nl_run >= 2,
            lead=leads,
            inline=inlines,
        )
        leads, inlines = [], []
        atoms.append(a)
        prev = a
        nl_run = 0
        nl_since_code = 0
    return atoms, leads  # leftover own-line comments = after the last token


@dataclass(eq=False)
class _Frame:
    head: Optional[_Atom]
    open: _Atom
    parent: _Seq
    args: List[_Seq] = field(default_factory=list)
    commas: List[_Atom] = field(default_factory=list)
    cur: _Seq = field(default_factory=_Seq)


def _is_arrow(it: _Item) -> bool:
    return isinstance(it, _Atom) and it.type == TokenType.OPERATOR and it.value == "=>"


def _finalize_seq(seq: _Seq) -> None:
    items = seq.items
    for k, it in enumerate(items):
        if isinstance(it, _Atom):
            if it.type == TokenType.OPERATOR and it.value in ("+", "-"):
                prev = items[k - 1] if k else None
                pa = prev if isinstance(prev, _Atom) else None
                it.unary = prev is None or (
                    pa is not None
                    and (
                        pa.type in (TokenType.OPERATOR, TokenType.COMMA, TokenType.COLON)
                        or (
                            pa.type == TokenType.KEYWORD
                            and pa.value.upper() in _UNARY_AFTER_KEYWORDS
                        )
                    )
                )
        elif not it.braces and it.head is None and k + 1 < len(items) and _is_arrow(items[k + 1]):
            it.lambda_params = True


def _parse(atoms: List[_Atom]) -> _Seq:
    root = _Seq()
    cur = root
    frames: List[_Frame] = []
    prev_atom: Optional[_Atom] = None
    for a in atoms:
        t = a.type
        if t == TokenType.COMMA and a.lead and prev_atom is not None:
            # own-line comment before a comma: it ends the previous line instead
            prev_atom.trail.extend(a.lead)
            a.lead = []
        if t in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
            head: Optional[_Atom] = None
            last_item = cur.items[-1] if cur.items else None
            if t == TokenType.PAREN_OPEN and isinstance(last_item, _Atom) and _is_head(last_item):
                head = last_item
                cur.items.pop()
            fr = _Frame(head=head, open=a, parent=cur)
            frames.append(fr)
            cur = fr.cur
        elif t in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            if not frames:
                raise DaxFormatError("unbalanced closing bracket")
            fr = frames.pop()
            if (fr.open.type == TokenType.PAREN_OPEN) != (t == TokenType.PAREN_CLOSE):
                raise DaxFormatError("mismatched ( ) / { }")
            _finalize_seq(cur)
            fr.args.append(cur)
            g = _Group(fr.head, fr.open, fr.args, fr.commas, a)
            cur = fr.parent
            cur.items.append(g)
        elif t == TokenType.COMMA and frames:
            fr = frames[-1]
            _finalize_seq(cur)
            fr.args.append(cur)
            fr.commas.append(a)
            cur = _Seq()
        else:
            cur.items.append(a)
        prev_atom = a
    if frames:
        raise DaxFormatError("unbalanced opening bracket")
    _finalize_seq(root)
    return root


def _set_texts(atoms: List[_Atom], builtins: "set[str]") -> None:
    n = len(atoms)
    for i, a in enumerate(atoms):
        v = a.value
        nxt = atoms[i + 1] if i + 1 < n else None
        prv = atoms[i - 1] if i else None
        t = a.type
        adjacent_ref = (
            nxt is not None and nxt.type == TokenType.COLUMN_REF and nxt.tok.start == a.tok.end
        )
        text = v
        if t == TokenType.FUNCTION:
            text = v.upper()
        elif t == TokenType.KEYWORD and not adjacent_ref:
            up = v.upper()
            if up in _UPPER_KEYWORDS:
                text = up
            elif (
                up == "MEASURE"
                and nxt is not None
                and nxt.type
                in (
                    TokenType.IDENTIFIER,
                    TokenType.TABLE_REF,
                    TokenType.QUALIFIED_REF,
                )
            ):
                text = up
            elif up == "ORDER" and nxt is not None and nxt.value.upper() == "BY":
                text = up
            elif up == "BY" and prv is not None and prv.value.upper() == "ORDER":
                text = up
            elif up == "START" and nxt is not None and nxt.value.upper() == "AT":
                text = up
            elif up == "AT" and prv is not None and prv.value.upper() == "START":
                text = up
        elif t == TokenType.IDENTIFIER and nxt is not None and nxt.type == TokenType.PAREN_OPEN:
            if v.upper() in builtins:
                text = v.upper()
        a.text = text


# ---------------------------------------------------------------------------
# Output builder
# ---------------------------------------------------------------------------


class _Out:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self.cur = ""
        self.pending: List[Tuple[str, bool, bool]] = []

    def col(self) -> int:
        return len(self.cur.rsplit("\n", 1)[-1])

    def at_line_start(self) -> bool:
        return not self.cur.strip()

    def put(self, s: str) -> None:
        self.cur += s

    def trail(self, c: _Comment) -> None:
        self.pending.append((c.text, c.is_line, c.multiline))

    def newline(self, indent: int) -> None:
        text = self.cur.rstrip()
        if text or self.pending:
            pad = " " * indent  # where the next line starts: a re-read lexes them as its leads
            split = False
            for ctext, is_line, is_ml in self.pending:
                if split:  # after a line / multi-line comment each further one goes below
                    self.lines.append(text)
                    text = pad + ctext
                else:
                    text = (text + " " if text.strip() else pad) + ctext
                split = split or is_line or is_ml
            self.lines.append(text)
        self.cur = " " * indent
        self.pending = []

    def blank(self) -> None:
        if self.at_line_start() and not self.pending and self.lines and self.lines[-1] != "":
            self.lines.append("")

    def finish(self) -> str:
        self.newline(0)
        lines = list(self.lines)
        while lines and lines[-1] == "":
            lines.pop()
        while lines and lines[0] == "":
            lines.pop(0)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


class _Layout:
    def __init__(self, max_line_length: int, skip_space: bool) -> None:
        self.max = max(20, int(max_line_length))
        self.skip_space = bool(skip_space)
        self.out = _Out()

    # -- spacing --------------------------------------------------------

    @staticmethod
    def _gap(prev: Optional[_Item], cur: _Item) -> str:
        if prev is None:
            return ""
        a = _first_atom(cur)
        p = _last_atom(prev)
        if a.type == TokenType.COMMA:
            return ""
        if isinstance(prev, _Atom) and prev.unary:
            return ""
        if a.type == TokenType.DOT or p.type == TokenType.DOT:
            return ""
        if a.type in (TokenType.COLON, TokenType.UNKNOWN) or p.type in (
            TokenType.COLON,
            TokenType.UNKNOWN,
        ):
            return " " if a.ws_before else ""
        if (
            isinstance(prev, _Atom)
            and isinstance(cur, _Atom)
            and a.type == TokenType.COLUMN_REF
            and p.type == TokenType.IDENTIFIER
            and not a.ws_before
        ):
            return ""
        return " "

    # -- flat text ------------------------------------------------------

    @staticmethod
    def _aft(a: _Atom) -> str:
        return "".join(" " + c.text for c in a.after)

    def _flat_item(self, it: _Item) -> str:
        if isinstance(it, _Atom):
            return "".join(c.text + " " for c in it.inline) + it.text + self._aft(it)
        g = it
        first = g.head or g.open
        pre = "".join(c.text + " " for c in first.inline) if g.head is not None else ""
        parts: List[str] = []
        for i, a in enumerate(g.args):
            s = self._flat_seq_unchecked(a.items)
            if i < len(g.commas):
                s += "," + self._aft(g.commas[i])
            parts.append(s)
        inner = " ".join(parts)
        empty = all(not a.items for a in g.args) and not g.commas
        oaft = self._aft(g.open)
        end = self._aft(g.close)
        if g.braces:
            binl = "".join(x.text + " " for x in g.open.inline)
            return binl + "{" + oaft + (" }" if empty else " " + inner + " }") + end
        if g.head is not None:
            h = g.head.text + self._aft(g.head)
            mid = "".join(x.text + " " for x in g.open.inline)
            sp = "" if self.skip_space else " "
            if empty:
                return pre + h + sp + mid + "(" + oaft + (" )" if oaft else ")") + end
            return pre + h + sp + mid + "(" + oaft + " " + inner + " )" + end
        inl = "".join(x.text + " " for x in g.open.inline)
        if g.lambda_params:
            return inl + "(" + oaft + (inner if not oaft else " " + inner) + ")" + end
        if empty:
            return inl + "(" + oaft + (" )" if oaft else ")") + end
        return inl + "(" + oaft + " " + inner + " )" + end

    def _flat_seq_unchecked(self, items: Sequence[_Item]) -> str:
        parts: List[str] = []
        prev: Optional[_Item] = None
        for it in items:
            parts.append(self._gap(prev, it) + self._flat_item(it))
            prev = it
        return "".join(parts)

    def _flat(self, items: Sequence[_Item]) -> Optional[str]:
        if not items:
            return ""
        if _hard(_atoms_of_items(items)):
            return None
        return self._flat_seq_unchecked(items)

    # -- emission primitives ---------------------------------------------

    def _emit_leads(self, a: _Atom, ind: int) -> None:
        o = self.out
        for c in a.lead:
            if not o.at_line_start() or o.pending:
                o.newline(ind)
            if c.blank_before:
                o.blank()
            o.put(c.text)
            o.newline(ind)

    def _emit_atom(self, a: _Atom, ind: int, gap: str = "", text: Optional[str] = None) -> None:
        o = self.out
        if o.pending:
            o.newline(ind)
            gap = ""
        self._emit_leads(a, ind)
        if o.at_line_start():
            gap = ""
            if a.blank_before:
                o.blank()
        o.put(gap)
        for c in a.inline:
            o.put(c.text + " ")
        o.put(a.text if text is None else text)
        for c in a.after:
            o.put(" " + c.text)
        for c in a.trail:
            o.trail(c)

    def _emit_comma(self, a: _Atom) -> None:
        self.out.put(a.text)
        for c in a.after:
            if self.out.pending:  # an earlier comment still waits for the end of this line
                self.out.trail(c)
            else:
                self.out.put(" " + c.text)
        for c in a.trail:
            self.out.trail(c)

    def _put_flat(self, items: Sequence[_Item], flat: str, ind: int, gap: str) -> None:
        o = self.out
        if o.pending:
            o.newline(ind)
            gap = ""
        first = _first_atom(items[0])
        self._emit_leads(first, ind)
        if o.at_line_start():
            gap = ""
            if first.blank_before:
                o.blank()
        o.put(gap + flat)
        for c in _last_atom(items[-1]).trail:
            o.trail(c)

    # -- expression layout -----------------------------------------------

    def seq(self, items: Sequence[_Item], ind: int, tail: int = 0, gap: str = "") -> None:
        if not items:
            return
        arrow: Optional[int] = None
        for k, it in enumerate(items):
            if isinstance(it, _Atom):
                if it.type == TokenType.OPERATOR and it.value == "=>" and arrow is None:
                    arrow = k
                elif it.type == TokenType.KEYWORD and it.value.upper() in ("VAR", "RETURN"):
                    if arrow is not None and 0 < arrow < k:
                        # lambda with a VAR body: "(x) =>" then the body one level in
                        self._expr(items[:arrow], ind, 3, gap)
                        self._emit_atom(items[arrow], ind + _INDENT, " ")  # type: ignore[arg-type]
                        self.out.newline(ind + _INDENT)
                        self.seq(items[arrow + 1 :], ind + _INDENT, tail)
                    else:
                        self._var_blocks(items, ind, tail)
                    return
        self._expr(items, ind, tail, gap)

    def _fits(self, flat: str, tail: int, gap: str, items: Sequence[_Item], ind: int = 0) -> bool:
        # A "/* c */" right before a comma / bracket is not counted: once the layout breaks
        # there, it ends its line and re-lexes as a trailing comment, which has no width.
        extra = sum(len(c.text) + 1 for a in _atoms_of_items(items) for c in a.after)
        if items and _first_atom(items[0]).lead:
            start = ind  # the comment lines are emitted first, the text starts a fresh line
        else:
            start = self.out.col() + len(gap)
        return start + len(flat) - extra + tail <= self.max

    def _expr(self, items: Sequence[_Item], ind: int, tail: int, gap: str = "") -> None:
        if not items:
            return
        flat = self._flat(items)
        if flat is not None and self._fits(flat, tail, gap, items, ind):
            self._put_flat(items, flat, ind, gap)
            return
        # 1. top-level commas (ORDER BY lists, stray lists)
        pieces = self._split(items, lambda a: a.type == TokenType.COMMA)
        if len(pieces) > 1:
            for k, (sep, piece) in enumerate(pieces):
                if k:
                    self.out.newline(ind)
                last = k == len(pieces) - 1
                self.seq(piece, ind, tail if last else 1)
                if not last:
                    nxt_sep = pieces[k + 1][0]
                    assert nxt_sep is not None
                    self._emit_comma(nxt_sep)
            return
        # 2. lambda arrow: params =>, body on the next line
        arrow = [
            k
            for k, it in enumerate(items)
            if isinstance(it, _Atom) and it.type == TokenType.OPERATOR and it.value == "=>"
        ]
        if arrow and arrow[0] > 0 and arrow[0] < len(items) - 1:
            k = arrow[0]
            self._expr(items[:k], ind, 3, gap)
            self._emit_atom(items[k], ind + _INDENT, " ")  # type: ignore[arg-type]
            self.out.newline(ind + _INDENT)
            self.seq(items[k + 1 :], ind + _INDENT, tail)
            return
        # 3. lowest-precedence binary operator
        ops = [
            (k, _PRECEDENCE[it.value])
            for k, it in enumerate(items)
            if isinstance(it, _Atom)
            and it.type == TokenType.OPERATOR
            and not it.unary
            and it.value in _PRECEDENCE
            and k > 0
        ]
        if ops:
            low = min(p for _, p in ops)
            cuts = [k for k, p in ops if p == low]
            segs: List[Tuple[Optional[_Atom], Sequence[_Item]]] = []
            start = 0
            op_atom: Optional[_Atom] = None
            for k in cuts:
                segs.append((op_atom, items[start:k]))
                op_atom = items[k]  # type: ignore[assignment]
                start = k + 1
            segs.append((op_atom, items[start:]))
            for n, (op, seg) in enumerate(segs):
                last = n == len(segs) - 1
                if op is None:
                    self._expr(seg, ind, 0, gap)
                    continue
                self.out.newline(ind + _INDENT)
                self._emit_atom(op, ind + _INDENT)
                if seg:
                    self.out.put(" ")
                    self._expr(seg, ind + _INDENT, tail if last else 0)
            return
        self._sequential(items, ind, tail, gap)

    @staticmethod
    def _split(items: Sequence[_Item], pred) -> List[Tuple[Optional[_Atom], Sequence[_Item]]]:
        out: List[Tuple[Optional[_Atom], Sequence[_Item]]] = []
        start = 0
        sep: Optional[_Atom] = None
        for k, it in enumerate(items):
            if isinstance(it, _Atom) and pred(it):
                out.append((sep, items[start:k]))
                sep = it
                start = k + 1
        out.append((sep, items[start:]))
        return out

    def _sequential(self, items: Sequence[_Item], ind: int, tail: int, gap: str = "") -> None:
        prev: Optional[_Item] = None
        n = len(items)
        for k, it in enumerate(items):
            g = gap if k == 0 else self._gap(prev, it)
            cont = ind if k == 0 else ind + _INDENT
            if isinstance(it, _Atom):
                if it.type == TokenType.COMMA:
                    self._emit_comma(it)
                else:
                    self._emit_atom(it, cont, g)
            else:
                self._group(it, cont, tail if k == n - 1 else 0, g)
            prev = it

    # -- groups -----------------------------------------------------------

    def _group(self, g: _Group, ind: int, tail: int, gap: str) -> None:
        o = self.out
        atoms = _atoms_of(g)
        if not _hard(atoms):
            flat = self._flat_item(g)
            if self._fits(flat, tail, gap, [g], ind):
                self._put_flat([g], flat, ind, gap)
                return
        # broken: HEAD (  /  one argument per line  /  )
        if g.head is not None:
            self._emit_atom(g.head, ind, gap)
            open_gap = "" if self.skip_space else " "
        else:
            open_gap = gap
        self._emit_atom(g.open, ind, open_gap)
        empty = all(not a.items for a in g.args)
        if not empty:
            for i, arg in enumerate(g.args):
                o.newline(ind + _INDENT)
                last = i == len(g.args) - 1
                self.seq(arg.items, ind + _INDENT, 0 if last else 1)
                if not last:
                    self._emit_comma(g.commas[i])
        for c in g.close.lead:
            o.newline(ind + _INDENT)
            if c.blank_before:
                o.blank()
            o.put(c.text)
        o.newline(ind)
        o.put(g.close.text)
        for c in g.close.after:
            o.put(" " + c.text)
        for c in g.close.trail:
            o.trail(c)

    # -- VAR / RETURN ----------------------------------------------------

    @staticmethod
    def _kw(it: _Item, *words: str) -> bool:
        return isinstance(it, _Atom) and it.type == TokenType.KEYWORD and it.value.upper() in words

    def _var_blocks(self, items: Sequence[_Item], ind: int, tail: int) -> None:
        o = self.out
        n = len(items)
        i = 0
        first_kw = next(k for k, it in enumerate(items) if self._kw(it, "VAR", "RETURN"))
        if first_kw:
            self._expr(items[:first_kw], ind, 0)
            o.newline(ind)
            i = first_kw
        while i < n:
            it = items[i]
            if not o.at_line_start() or o.pending:
                o.newline(ind)
            if self._kw(it, "RETURN"):
                self._emit_atom(it, ind)  # type: ignore[arg-type]
                o.newline(ind + _INDENT)
                self.seq(items[i + 1 :], ind + _INDENT, tail)
                return
            if not self._kw(it, "VAR"):
                self._sequential(items[i:], ind, tail)
                return
            eq = next(
                (
                    j
                    for j in range(i + 1, n)
                    if isinstance(items[j], _Atom)
                    and items[j].type == TokenType.OPERATOR  # type: ignore[union-attr]
                    and items[j].value == "="  # type: ignore[union-attr]
                ),
                None,
            )
            if eq is None:
                self._sequential(items[i:], ind, tail)
                return
            nxt = next((j for j in range(eq + 1, n) if self._kw(items[j], "VAR", "RETURN")), n)
            self._sequential(items[i : eq + 1], ind, 0)
            o.newline(ind + _INDENT)
            self.seq(items[eq + 1 : nxt], ind + _INDENT, 0)
            i = nxt
            if i < n:
                o.newline(ind)

    # -- queries ----------------------------------------------------------

    def root(self, root: _Seq) -> None:
        items = root.items
        if any(self._kw(it, "DEFINE", "EVALUATE") for it in items):
            self._query(items)
        else:
            self.seq(items, 0, 0)

    def _query(self, items: Sequence[_Item]) -> None:
        o = self.out
        n = len(items)

        def is_marker(k: int) -> bool:
            it = items[k]
            if self._kw(it, "DEFINE", "EVALUATE"):
                return True
            if self._kw(it, "ORDER") and k + 1 < n and self._kw(items[k + 1], "BY"):
                return True
            if self._kw(it, "START") and k + 1 < n and self._kw(items[k + 1], "AT"):
                return True
            return False

        marks = [j for j in range(n) if is_marker(j)]
        if marks and marks[0] > 0:
            self.seq(items[: marks[0]], 0, 0)
            o.newline(0)
        for m, start in enumerate(marks):
            end = marks[m + 1] if m + 1 < len(marks) else n
            head = items[start]
            if not o.at_line_start() or o.pending:
                o.newline(0)
            if self._kw(head, "DEFINE"):
                self._emit_atom(head, 0)  # type: ignore[arg-type]
                for defn in self._definitions(items[start + 1 : end]):
                    o.newline(_INDENT)
                    self._definition(defn)
            elif self._kw(head, "EVALUATE"):
                self._emit_atom(head, 0)  # type: ignore[arg-type]
                o.newline(0)
                self.seq(items[start + 1 : end], 0, 0)
            else:  # ORDER BY / START AT
                self._emit_atom(head, 0)  # type: ignore[arg-type]
                self._emit_atom(items[start + 1], 0, " ")  # type: ignore[arg-type]
                body = items[start + 2 : end]
                flat = self._flat(body)
                if body and flat is not None and self._fits(flat, 0, " ", body):
                    self._put_flat(body, flat, 0, " ")
                elif body:
                    o.newline(_INDENT)
                    self._expr(body, _INDENT, 0)

    def _definitions(self, items: Sequence[_Item]) -> List[Sequence[_Item]]:
        n = len(items)
        starts: List[int] = []
        for k, it in enumerate(items):
            if isinstance(it, _Atom) and it.type == TokenType.KEYWORD:
                up = it.value.upper()
                if up in _DEFINITION_KEYWORDS:
                    nxt = items[k + 1] if k + 1 < n else None
                    # a definition keyword is followed by a name (MEASURE T[m], FUNCTION f ...)
                    if nxt is not None and not (
                        isinstance(nxt, _Atom) and nxt.type == TokenType.OPERATOR
                    ):
                        starts.append(k)
                elif up == "VAR" and (not starts or self._kw(items[starts[0]], "VAR")):
                    starts.append(k)
        if not starts:
            return [items] if items else []
        out: List[Sequence[_Item]] = []
        if starts[0] > 0:
            out.append(items[: starts[0]])
        for m, s in enumerate(starts):
            e = starts[m + 1] if m + 1 < len(starts) else n
            out.append(items[s:e])
        return out

    def _definition(self, items: Sequence[_Item]) -> None:
        o = self.out
        eq = next(
            (
                j
                for j, it in enumerate(items)
                if isinstance(it, _Atom) and it.type == TokenType.OPERATOR and it.value == "="
            ),
            None,
        )
        if eq is None:
            self._sequential(items, _INDENT, 0)
            return
        self._sequential(items[: eq + 1], _INDENT, 0)
        body = items[eq + 1 :]
        if any(
            isinstance(it, _Atom) and it.type == TokenType.OPERATOR and it.value == "=>"
            for it in body
        ):
            self.seq(body, _INDENT, 0, " ")
        else:
            o.newline(2 * _INDENT)
            self.seq(body, 2 * _INDENT, 0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _builtin_names() -> "set[str]":
    names = set(_EXTRA_BUILTINS)
    try:
        from core.dax.knowledge.function_db import DaxFunctionDatabase

        names |= DaxFunctionDatabase.get().get_function_names()
    except Exception:  # noqa: BLE001 - the extra list still covers the common names
        pass
    return names


def _comment_texts(dax: str) -> List[str]:
    from core.dax.tokenizer import DaxLexer as _L

    return [
        t.value.rstrip()  # trailing blanks of a line comment are not content
        for t in _L().tokenize(dax or "")
        if t.type in (TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK)
    ]


def _layout_text(expression: str, max_line_length: int, skip_space_after_function: bool) -> str:
    builtins = _builtin_names()
    lexer = DaxLexer(function_names=builtins)
    tokens = lexer.tokenize(expression)
    atoms, eof_comments = _attach(tokens)
    _set_texts(atoms, builtins)
    lay = _Layout(max_line_length, skip_space_after_function)
    if atoms:
        root = _parse(atoms)
        lay.root(root)
    o = lay.out
    for c in eof_comments:
        o.newline(0)
        if c.blank_before:
            o.blank()
        o.put(c.text)
    return o.finish()


def _restore_line_endings(original: str, formatted: str) -> str:
    if "\r\n" in (original or ""):
        return formatted.replace("\r\n", "\n").replace("\n", "\r\n")
    return formatted


def format_dax_local(
    expression: str,
    *,
    max_line_length: int = _DEFAULT_LINE_LEN,
    skip_space_after_function: bool = False,
) -> Dict[str, Any]:
    """Format one DAX expression. Returns ``{success, formatted, original, token_equivalent,
    error?}``; ``success`` is False (and ``formatted`` is the untouched original) when the text
    cannot be laid out or the result is not provably the same DAX."""
    original = expression or ""
    if not original.strip():
        return {
            "success": True,
            "formatted": "",
            "original": expression,
            "token_equivalent": True,
        }
    try:
        formatted = _layout_text(original, max_line_length, skip_space_after_function)
    except DaxFormatError as e:
        return {
            "success": False,
            "error": f"local formatter refused the text: {e}",
            "original": expression,
            "formatted": expression,
        }
    except RecursionError:
        return {
            "success": False,
            "error": "local formatter refused the text: nesting too deep",
            "original": expression,
            "formatted": expression,
        }
    formatted = _restore_line_endings(original, formatted)
    from core.dax.dax_formatter_client import token_equivalent

    equivalent, why = token_equivalent(original, formatted)
    if equivalent and _comment_texts(original) != _comment_texts(formatted):
        equivalent, why = False, "comment sequence changed"
    if not equivalent:
        return {
            "success": False,
            "error": f"local formatter result is not token-equivalent ({why}); not used",
            "original": expression,
            "formatted": expression,
            "token_equivalent": False,
            "equivalence_detail": why,
        }
    return {
        "success": True,
        "formatted": formatted,
        "original": expression,
        "token_equivalent": True,
    }


def format_many_local(
    expressions: List[Dict[str, Any]],
    *,
    max_line_length: int = _DEFAULT_LINE_LEN,
    skip_space_after_function: bool = False,
    on_error: str = "skip",
) -> Dict[str, Any]:
    """Same result shape as ``dax_formatter_client.format_many`` (``expressions`` = list of
    ``{key, expression}``) so the handler can swap engines. No network, no time budget."""
    from core.dax.dax_formatter_client import is_unchanged

    results: List[Dict[str, Any]] = []
    succeeded = failed = unchanged = 0
    remaining: List[Any] = []
    for i, item in enumerate(expressions):
        key = item.get("key")
        expr = item.get("expression", "") or ""
        out = format_dax_local(
            expr,
            max_line_length=max_line_length,
            skip_space_after_function=skip_space_after_function,
        )
        if out.get("success"):
            if is_unchanged(expr, out.get("formatted") or ""):
                unchanged += 1
            else:
                succeeded += 1
            results.append({"key": key, **out})
        else:
            failed += 1
            results.append(
                {"key": key, "success": False, "error": out.get("error"), "original": expr}
            )
            if on_error == "abort":
                remaining = [x.get("key") for x in expressions[i + 1 :]]
                break
    return {
        "success": failed == 0,
        "results": results,
        "remaining": remaining,
        "timed_out": False,
        "stats": {
            "total": len(expressions),
            "succeeded": succeeded,
            "unchanged": unchanged,
            "not_token_equivalent": 0,
            "failed": failed,
            "not_processed": len(remaining),
        },
    }
