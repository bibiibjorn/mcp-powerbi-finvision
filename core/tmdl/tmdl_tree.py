"""
Indentation-scoped TMDL document tree (review G-07 / G-05 / G-13 / G-14 / G-15).

The single structural reader behind the unified parser, the validator, the
bulk editor, the measure migrator and the git analyzer. It never loses a line:
every node records the line span it owns, so editors can rewrite exactly the
object they target and keep every other byte (line endings, BOM, indentation).

TMDL grammar as written by the Desktop / TOM serializer:

* an object header ``keyword [name] [= expression]`` sits at indent level N;
* ``///`` lines at level N directly above a header are its description;
* its properties (``key: value``, ``key = expression``, bare flags such as
  ``isHidden``) and child objects sit at level N+1;
* the continuation lines of an ``=`` expression sit at level N+2 or deeper
  (both for object headers and for ``key = expression`` properties);
* a value that starts with ``````` runs verbatim until the closing ```````.

Hand-written TMDL (and older fixtures) sometimes put the expression body at
N+1, the same level as the properties. That form is accepted when the first
body line is not a property / flag / child keyword; the body then ends at the
first property-shaped line at that level outside parentheses.

Names: ``'quoted name'`` with ``''`` as the escaped apostrophe, or a bare
identifier. ``ref`` lines keep the referenced kind in ``ref_kind``
(``ref table Sales`` -> keyword ``ref``, ref_kind ``table``, name ``Sales``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

__all__ = [
    "TmdlNode",
    "TmdlDocument",
    "TreeIssue",
    "parse_document",
    "parse_text",
    "split_lines",
    "tmdl_name",
    "parse_tmdl_name",
    "split_object_ref",
    "format_object_ref",
    "KNOWN_FLAGS",
    "OBJECT_KEYWORDS",
    "DAX_EXPRESSION_KEYWORDS",
]

# Bare boolean properties Desktop writes without a value (``isHidden``).
KNOWN_FLAGS = frozenset(
    {
        "isHidden",
        "isKey",
        "isActive",
        "isNameInferred",
        "isDataTypeInferred",
        "isDefaultLabel",
        "isDefaultImage",
        "isAvailableInMdx",
        "isNullable",
        "isUnique",
        "keepUniqueRows",
        "isPrivate",
        "isRemoved",
        "isSimpleMeasure",
        "discourageImplicitMeasures",
        "legacyRedirects",
        "returnErrorValuesAsNull",
        "calculationGroup",
        "relatedColumnDetails",
        "translations",
        "dataAccessOptions",
    }
)

# Keywords that open a child object (``keyword name``).
OBJECT_KEYWORDS = frozenset(
    {
        "table",
        "column",
        "measure",
        "hierarchy",
        "level",
        "partition",
        "calculationItem",
        "calculationGroup",
        "calendar",
        "calendarColumnGroup",
        "annotation",
        "extendedProperty",
        "changedProperty",
        "variation",
        "relationship",
        "role",
        "tablePermission",
        "columnPermission",
        "member",
        "perspective",
        "perspectiveTable",
        "perspectiveColumn",
        "perspectiveMeasure",
        "perspectiveHierarchy",
        "cultureInfo",
        "culture",
        "linguisticMetadata",
        "expression",
        "function",
        "dataSource",
        "datasource",
        "model",
        "database",
        "ref",
        "kpi",
        "detailRowsDefinition",
        "alternateOf",
        "refreshPolicy",
        "queryGroup",
        "translationTable",
        "translationColumn",
        "translationMeasure",
        "translationHierarchy",
        "formatStringDefinition",
    }
)

# ``=`` bodies that are DAX (the rest — partitions of kind ``m``, shared
# expressions, JSON extended properties, annotations — are not).
DAX_EXPRESSION_KEYWORDS = frozenset(
    {
        "measure",
        "column",
        "calculationItem",
        "tablePermission",
        "function",
        "formatStringDefinition",
        "detailRowsDefinition",
        "targetExpression",
        "statusExpression",
        "trendExpression",
        "multipleOrEmptySelectionExpression",
        "noSelectionExpression",
    }
)

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SIMPLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROPERTY_SHAPE_RE = re.compile(r"^[a-z][a-zA-Z0-9_]*\s*[:=]")


# ─── Names ───────────────────────────────────────────────────────────


def tmdl_name(name: str) -> str:
    """Emit *name* the way TMDL accepts it everywhere.

    Simple identifiers stay bare (``Sales``); anything else — spaces, digits
    first, ``'``, ``.``, ``=``, ``:``, non-ASCII — is single-quoted with ``''``
    as the escaped apostrophe (quoting is always valid TMDL). Uses the DAX
    table-quoting helper, which has the same escape rule.
    """
    from core.dax.dax_names import quote_table

    text = str(name)
    if _SIMPLE_NAME_RE.match(text):
        return text
    return quote_table(text)


def parse_tmdl_name(text: str, pos: int = 0) -> Tuple[Optional[str], int, Optional[str]]:
    """Read one TMDL name starting at ``text[pos]``.

    Returns ``(name, end_pos, error)``. A quoted name un-doubles ``''``; a bare
    name runs up to ``.``, ``=``, ``:`` or the end (whitespace trimmed).
    ``error`` is set for an unterminated quote.
    """
    n = len(text)
    while pos < n and text[pos] in " \t":
        pos += 1
    if pos >= n:
        return None, pos, None
    ch = text[pos]
    if ch in ("'", '"'):
        q = ch
        i = pos + 1
        out: List[str] = []
        while i < n:
            if text[i] == q:
                if i + 1 < n and text[i + 1] == q:
                    out.append(q)
                    i += 2
                    continue
                return "".join(out), i + 1, None
            out.append(text[i])
            i += 1
        return "".join(out), n, "unterminated quoted name"
    i = pos
    while i < n and text[i] not in ".=:":
        i += 1
    name = text[pos:i].strip()
    return (name if name else None), i, None


def split_object_ref(ref: Optional[str]) -> Tuple[str, str]:
    """Split a TMDL object reference ``Table.Column`` into its UNESCAPED parts.

    Both parts may be quoted or bare: ``Sales.'Product Key'``,
    ``'Owner''s Book'.'Owner Key'``, ``f_Aging_Customer.'Company Code'``,
    ``'d Date'.Date``. A single part (no dot) is returned as ``("", part)``.
    """
    if not ref:
        return "", ""
    text = ref.strip()
    first, end, _ = parse_tmdl_name(text, 0)
    if first is None:
        return "", ""
    rest = text[end:].lstrip()
    if rest.startswith("."):
        second, _end2, _ = parse_tmdl_name(rest, 1)
        return first, second or ""
    return "", first


def format_object_ref(table: str, column: str) -> str:
    """Inverse of :func:`split_object_ref` (``'Owner''s Book'.'Owner Key'``)."""
    return f"{tmdl_name(table)}.{tmdl_name(column)}"


# ─── Tree ────────────────────────────────────────────────────────────


@dataclass
class TreeIssue:
    """A structural problem found while building the tree (1-based line)."""

    line: int
    code: str
    message: str


@dataclass
class TmdlNode:
    """One TMDL line-object and everything indented below it.

    ``kind``: ``"object"`` (``keyword name``), ``"property"`` (``key: value``
    or ``key = expression``) or ``"flag"`` (a bare word such as ``isHidden``
    or ``calculationGroup``; flags may still own children).
    Line indices are 0-based into :attr:`TmdlDocument.lines`; ``end`` is
    exclusive and excludes trailing blank lines.
    """

    keyword: str
    kind: str
    level: int
    start: int
    end: int = 0
    name: Optional[str] = None
    name_col: Tuple[int, int] = (-1, -1)
    ref_kind: Optional[str] = None
    value: Optional[str] = None
    has_expression: bool = False
    expression: Optional[str] = None
    expr_inline: str = ""
    expr_start: int = -1
    expr_end: int = -1
    expr_col: int = -1
    backtick: bool = False
    description: Optional[str] = None
    desc_start: int = -1
    children: List["TmdlNode"] = field(default_factory=list)
    parent: Optional["TmdlNode"] = field(default=None, repr=False)

    # -- navigation ---------------------------------------------------
    @property
    def span_start(self) -> int:
        """First line of the node INCLUDING its ``///`` description."""
        return self.desc_start if self.desc_start >= 0 else self.start

    def iter(self) -> Iterator["TmdlNode"]:
        """Depth-first walk (self first)."""
        yield self
        for child in self.children:
            yield from child.iter()

    def child(self, keyword: str) -> Optional["TmdlNode"]:
        for c in self.children:
            if c.keyword == keyword:
                return c
        return None

    def children_of(self, keyword: str) -> List["TmdlNode"]:
        return [c for c in self.children if c.keyword == keyword]

    def prop(self, key: str) -> Optional[str]:
        """Value of a ``key: value`` property (raw text, unquoted if wrapped)."""
        for c in self.children:
            if c.keyword == key and c.kind == "property" and not c.has_expression:
                return c.value
        return None

    def has_flag(self, key: str) -> bool:
        for c in self.children:
            if c.keyword == key:
                if c.kind == "flag":
                    return True
                if c.kind == "property" and not c.has_expression:
                    return str(c.value).strip().lower() in ("true", "")
        return False

    def ancestors(self) -> Iterator["TmdlNode"]:
        p = self.parent
        while p is not None:
            yield p
            p = p.parent


@dataclass
class TmdlDocument:
    """A parsed TMDL file: raw lines (EOL kept), root nodes and issues."""

    path: Optional[str]
    raw_lines: List[str]
    lines: List[str]
    roots: List[TmdlNode]
    issues: List[TreeIssue]
    bom: bool = False
    newline: str = "\n"

    def iter(self) -> Iterator[TmdlNode]:
        for r in self.roots:
            yield from r.iter()

    def text(self) -> str:
        return "".join(self.raw_lines)


def split_lines(text: str) -> Tuple[List[str], List[str]]:
    """``(raw_lines_with_eol, lines_without_eol)`` — never loses a byte."""
    raw = text.splitlines(keepends=True)
    stripped = [ln.rstrip("\r\n") for ln in raw]
    return raw, stripped


class _Indent:
    """Indent-level calculator (tabs = 1 level; spaces grouped by a unit)."""

    def __init__(self, lines: List[str]) -> None:
        unit = 0
        for ln in lines:
            if ln[:1] != " ":
                continue
            k = len(ln) - len(ln.lstrip(" "))
            if k < len(ln) and (unit == 0 or k < unit):
                unit = k
        self.unit = unit if unit in (2, 3, 4) else 4

    def level(self, line: str) -> int:
        tabs = len(line) - len(line.lstrip("	"))
        if line[tabs : tabs + 1] != " ":
            return tabs
        lvl = 0
        spaces = 0
        for ch in line:
            if ch == "	":
                lvl += 1 + spaces // self.unit
                spaces = 0
            elif ch == " ":
                spaces += 1
            else:
                break
        return lvl + spaces // self.unit


def _net_parens(text: str) -> int:
    """Net ``(`` minus ``)`` outside double-quoted strings and ``//``/``--`` comments."""
    depth = 0
    in_str = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    i += 2
                    continue
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "/-" and i + 1 < n and text[i + 1] == ch:
                break
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        i += 1
    return depth


def _is_structural(stripped: str) -> bool:
    """True when a line reads as a property / flag / child header, not DAX/M."""
    if not stripped:
        return False
    if stripped.startswith("///"):
        return True
    m = _IDENT_RE.match(stripped)
    if not m:
        return False
    word = m.group(0)
    rest = stripped[m.end() :]
    if not rest:
        return word in KNOWN_FLAGS
    if word in OBJECT_KEYWORDS and rest[:1] in (" ", "\t"):
        return True
    return bool(_PROPERTY_SHAPE_RE.match(stripped)) and stripped not in ("let", "in")


def _dedent(parts: List[str]) -> str:
    indents = [len(p) - len(p.lstrip()) for p in parts if p.strip()]
    common = min(indents) if indents else 0
    out = [p[common:] if p.strip() else "" for p in parts]
    return "\n".join(out).strip("\n")


class _Builder:
    def __init__(self, lines: List[str]) -> None:
        self.lines = lines
        self.n = len(lines)
        self.ind = _Indent(lines)
        self.issues: List[TreeIssue] = []
        self.levels = [self.ind.level(ln) if ln.strip() else -1 for ln in lines]

    # -- helpers ------------------------------------------------------
    def _next_nonblank(self, i: int) -> int:
        while i < self.n and not self.lines[i].strip():
            i += 1
        return i

    def _parse_header(self, idx: int, level: int) -> TmdlNode:
        line = self.lines[idx]
        stripped = line.strip()
        col0 = len(line) - len(line.lstrip())
        m = _IDENT_RE.match(stripped)
        if not m:
            node = TmdlNode(keyword="", kind="flag", level=level, start=idx, value=stripped)
            self.issues.append(
                TreeIssue(
                    idx + 1, "TMDL010", f"line is not a TMDL object or property: {stripped[:60]!r}"
                )
            )
            return node
        word = m.group(0)
        pos = m.end()
        rest_all = stripped[pos:]
        rest = rest_all.lstrip(" \t")
        # key: value
        if rest.startswith(":"):
            val = rest[1:].strip()
            return TmdlNode(
                keyword=word, kind="property", level=level, start=idx, value=_unwrap(val)
            )
        # key = expression (property form: no name)
        if rest.startswith("="):
            node = TmdlNode(keyword=word, kind="property", level=level, start=idx)
            node.has_expression = True
            eq_col = col0 + pos + (len(rest_all) - len(rest)) + 1
            node.expr_inline = rest[1:].strip()
            node.expr_col = eq_col
            return node
        if not rest:
            return TmdlNode(keyword=word, kind="flag", level=level, start=idx)
        node = TmdlNode(keyword=word, kind="object", level=level, start=idx)
        name_pos = pos + (len(rest_all) - len(rest))
        if word == "ref":
            m2 = _IDENT_RE.match(rest)
            if m2:
                node.ref_kind = m2.group(0)
                name_pos += m2.end()
        name, end, err = parse_tmdl_name(stripped, name_pos)
        if err:
            self.issues.append(TreeIssue(idx + 1, "TMDL011", f"{err} in {stripped[:80]!r}"))
        # the start column of the name token itself (skip blanks)
        k = name_pos
        while k < len(stripped) and stripped[k] in " \t":
            k += 1
        node.name = name
        node.name_col = (
            col0 + k,
            col0 + (end if stripped[k : k + 1] in ("'", '"') else _rstrip_end(stripped, k, end)),
        )
        tail = stripped[end:].lstrip(" \t")
        if tail.startswith("="):
            node.has_expression = True
            node.expr_inline = tail[1:].strip()
            node.expr_col = col0 + len(stripped) - len(tail) + 1
        elif tail and not tail.startswith("."):
            # e.g. `ref table X` handled above; anything else is kept as value
            node.value = tail
        return node

    def _collect_expression(self, node: TmdlNode, i: int) -> int:
        """Consume the ``=`` body of *node*; return the next unconsumed index."""
        lvl = node.level
        inline = node.expr_inline
        if inline.startswith("```"):
            node.backtick = True
            parts: List[str] = []
            first = inline[3:].strip()
            if first:
                parts.append(first)
            j = i
            while j < self.n:
                if self.lines[j].strip() == "```":
                    break
                parts.append(self.lines[j])
                j += 1
            if j >= self.n:
                self.issues.append(TreeIssue(node.start + 1, "TMDL012", "unterminated ``` block"))
                node.expression = _dedent(parts) or None
                node.expr_start, node.expr_end = node.start, self.n
                return self.n
            node.expression = _dedent(parts) or None
            node.expr_start, node.expr_end = node.start, j + 1
            return j + 1

        parts = []
        j = self._next_nonblank(i)
        cont_min = lvl + 2
        legacy = False
        if j < self.n:
            first_lvl = self.levels[j]
            first = self.lines[j].strip()
            if first_lvl == lvl + 1 and not _is_structural(first):
                legacy = True
                cont_min = lvl + 1
        depth = _net_parens(inline) if (legacy and inline) else 0
        last = node.start
        blanks: List[str] = []
        levels = self.levels
        lines = self.lines
        j = i
        while j < self.n:
            l2 = levels[j]
            if l2 < 0:  # blank line: kept only when the body continues after it
                blanks.append("")
                j += 1
                continue
            if l2 < cont_min:
                break
            if legacy and l2 == cont_min and depth <= 0:
                st = lines[j].strip()
                if _is_structural(st):
                    break
            ln = lines[j]
            if blanks:
                if parts:
                    parts.extend(blanks)
                blanks = []
            parts.append(ln)
            if legacy:
                depth += _net_parens(ln)
            last = j
            j += 1
        body = _dedent(parts) if parts else ""
        if inline and body:
            node.expression = inline + "\n" + body
        else:
            node.expression = (inline or body) or None
        node.expr_start = node.start
        node.expr_end = last + 1
        return last + 1

    # -- main recursion -------------------------------------------------
    def parse_block(
        self, i: int, parent_level: int, parent: Optional[TmdlNode]
    ) -> Tuple[List[TmdlNode], int]:
        nodes: List[TmdlNode] = []
        pending_desc: List[str] = []
        desc_start = -1
        desc_level = -1
        expected: Optional[int] = None
        while i < self.n:
            st = self.lines[i].strip()
            if not st:
                i += 1
                continue
            lvl = self.levels[i]
            if lvl <= parent_level:
                break
            if st.startswith("///"):
                if not pending_desc:
                    desc_start = i
                    desc_level = lvl
                text = st[3:]
                pending_desc.append(text[1:] if text.startswith(" ") else text)
                i += 1
                continue
            if expected is None:
                expected = lvl
                if parent is not None and lvl > parent_level + 1:
                    self.issues.append(
                        TreeIssue(
                            i + 1,
                            "TMDL013",
                            f"indentation jumps from level {parent_level} to {lvl} under "
                            f"{parent.keyword} {parent.name or ''}".rstrip(),
                        )
                    )
            elif lvl != expected:
                # a deeper line with no owner (orphaned child) or a shallower
                # sibling than the first child — both are indentation errors
                self.issues.append(
                    TreeIssue(
                        i + 1,
                        "TMDL014",
                        f"inconsistent indentation (level {lvl}, siblings at {expected}): {st[:60]!r}",
                    )
                )
            node = self._parse_header(i, lvl)
            node.parent = parent
            if pending_desc:
                node.description = "\n".join(pending_desc)
                node.desc_start = desc_start
                if desc_level != lvl:
                    self.issues.append(
                        TreeIssue(
                            desc_start + 1,
                            "TMDL015",
                            "/// description is not aligned with its object",
                        )
                    )
                pending_desc = []
                desc_start = -1
            j = i + 1
            if node.has_expression:
                j = self._collect_expression(node, j)
            children, j = self.parse_block(j, lvl, node)
            node.children = children
            if children:
                node.end = children[-1].end
            elif node.has_expression:
                node.end = max(node.expr_end, i + 1)
            else:
                node.end = i + 1
            nodes.append(node)
            i = j
        if pending_desc:
            self.issues.append(
                TreeIssue(desc_start + 1, "TMDL016", "/// description is not followed by an object")
            )
        return nodes, i


def _rstrip_end(text: str, start: int, end: int) -> int:
    k = end
    while k > start and text[k - 1] in " \t":
        k -= 1
    return k


def _unwrap(val: str) -> str:
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    return val


def parse_text(text: str, path: Optional[str] = None) -> TmdlDocument:
    """Parse TMDL *text* (BOM tolerated) into a :class:`TmdlDocument`."""
    bom = text.startswith("﻿")
    if bom:
        text = text[1:]
    raw, lines = split_lines(text)
    newline = "\r\n" if raw and raw[0].endswith("\r\n") else "\n"
    b = _Builder(lines)
    roots, _ = b.parse_block(0, -1, None)
    return TmdlDocument(
        path=path,
        raw_lines=raw,
        lines=lines,
        roots=roots,
        issues=b.issues,
        bom=bom,
        newline=newline,
    )


def parse_document(path: str) -> TmdlDocument:
    """Read *path* (UTF-8, line endings preserved) and parse it."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    return parse_text(text, path=path)
