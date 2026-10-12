"""
DAX Reference Parser

Shared module for parsing DAX expressions and extracting references to tables, columns, and measures.
This module breaks the circular dependency between core/model and core/documentation.

2026-09-22 (review D-02): rebuilt on ``core.dax.tokenizer.DaxLexer``. The old
string-unaware regexes stripped ``//`` comments inside string literals (every
reference after ``"http://…"`` in an SVG measure vanished), mis-parsed the DAX
escapes ``'O''Brien'`` / ``[a]]b]``, reported ``[Red]`` from format strings and
produced garbage tables from ``TREATAS({"a","b"}, T[c])``. Its output feeds the
unused-column analysis and the destructive-op evidence gate, so correctness wins
over cleverness: strings and comments are never looked into, names are
unescaped, unquoted ``Sales[Date]`` is a qualified reference (it no longer marks
``Budget[Date]`` used), and table arguments are recognised by position.

Enhanced output (``enhanced=True``) additionally reports RELATED / RELATEDTABLE /
USERELATIONSHIP / TREATAS / CROSSFILTER usage, VAR definitions and the columns
compared inside filter predicates.
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from core.dax.dax_names import qualified_table_span, split_qualified, unbracket, unquote_table
from core.dax.tokenizer import DaxLexer, Token, TokenType

logger = logging.getLogger(__name__)

# Functions whose arguments at these (0-based) positions are TABLE expressions.
# "*" = every argument, "rest" = every argument after the first. A bare
# identifier in one of these positions is a table reference even without a model
# index (``COUNTROWS(Sales)``).
_TABLE_ARG_POSITIONS: Dict[str, Any] = {
    **{
        fn: {0}
        for fn in (
            "SUMX",
            "AVERAGEX",
            "MINX",
            "MAXX",
            "COUNTX",
            "COUNTAX",
            "PRODUCTX",
            "CONCATENATEX",
            "RANKX",
            "FILTER",
            "ADDCOLUMNS",
            "SELECTCOLUMNS",
            "GENERATE",
            "GENERATEALL",
            "STDEVX.S",
            "STDEVX.P",
            "VARX.S",
            "VARX.P",
            "PERCENTILEX.INC",
            "PERCENTILEX.EXC",
            "MEDIANX",
            "GEOMEANX",
            "COUNTROWS",
            "ISEMPTY",
            "VALUES",
            "DISTINCT",
            "ALLNOBLANKROW",
            "ALLCROSSFILTERED",
            "RELATEDTABLE",
            "SUMMARIZE",
            "GROUPBY",
            "CONTAINS",
            "CONTAINSROW",
            "ALLEXCEPT",
            "TREATAS",
            "DETAILROWS",
            "KEEPFILTERS",
            "TOCSV",
            "TOJSON",
        )
    },
    **{
        fn: "*"
        for fn in (
            "ALL",
            "ALLSELECTED",
            "REMOVEFILTERS",
            "CROSSJOIN",
            "UNION",
            "INTERSECT",
            "EXCEPT",
            "NATURALINNERJOIN",
            "NATURALLEFTOUTERJOIN",
            "CALCULATETABLE",
        )
    },
    "CALCULATE": "rest",  # CALCULATE([m], Sales) — a table filter argument
    "TOPN": {1},
    "SAMPLE": {1},
    "SUBSTITUTEWITHINDEX": {0, 2},
}

# Time-intelligence functions and the argument that carries the DATES: a date column, or - with
# calendar-based time intelligence (Sep 2025) - the NAME OF A CALENDAR object. A table name in that
# position is a calendar reference, not a table (2026-10-03 review D-20).
_CALENDAR_ARG_POSITION: Dict[str, int] = {
    **{
        fn: 0
        for fn in (
            "DATESYTD",
            "DATESQTD",
            "DATESMTD",
            "DATESWTD",
            "SAMEPERIODLASTYEAR",
            "PARALLELPERIOD",
            "DATEADD",
            "DATESINPERIOD",
            "DATESBETWEEN",
            "PREVIOUSDAY",
            "PREVIOUSWEEK",
            "PREVIOUSMONTH",
            "PREVIOUSQUARTER",
            "PREVIOUSYEAR",
            "NEXTDAY",
            "NEXTWEEK",
            "NEXTMONTH",
            "NEXTQUARTER",
            "NEXTYEAR",
            "STARTOFWEEK",
            "STARTOFMONTH",
            "STARTOFQUARTER",
            "STARTOFYEAR",
            "ENDOFWEEK",
            "ENDOFMONTH",
            "ENDOFQUARTER",
            "ENDOFYEAR",
            "FIRSTDATE",
            "LASTDATE",
        )
    },
    **{
        fn: 1
        for fn in (
            "TOTALYTD",
            "TOTALQTD",
            "TOTALMTD",
            "TOTALWTD",
            "OPENINGBALANCEWEEK",
            "OPENINGBALANCEMONTH",
            "OPENINGBALANCEQUARTER",
            "OPENINGBALANCEYEAR",
            "CLOSINGBALANCEWEEK",
            "CLOSINGBALANCEMONTH",
            "CLOSINGBALANCEQUARTER",
            "CLOSINGBALANCEYEAR",
        )
    },
}

# Bare words that are enum constants / type names in function signatures, never tables.
_BARE_CONSTANTS: Set[str] = {
    "YEAR",
    "QUARTER",
    "MONTH",
    "WEEK",
    "DAY",
    "HOUR",
    "MINUTE",
    "SECOND",
    "BOTH",
    "NONE",
    "ONEWAY",
    "ONEWAY_RIGHTFILTERSLEFT",
    "ONEWAY_LEFTFILTERSRIGHT",
    "SKIP",
    "DENSE",
    "ABS",
    "REL",
    "INTEGER",
    "DOUBLE",
    "STRING",
    "BOOLEAN",
    "CURRENCY",
    "DATETIME",
    "BLANK",
    "NULL",
    "KEEP",
    "FIRST",
    "LAST",
    "DEFAULT",
}

# Functions whose STRING arguments name virtual (extension) columns.
_VIRTUAL_COLUMN_FUNCTIONS: Set[str] = {
    "ADDCOLUMNS",
    "SELECTCOLUMNS",
    "SUMMARIZE",
    "SUMMARIZECOLUMNS",
    "GROUPBY",
    "ROW",
    "DATATABLE",
    "ADDMISSINGITEMS",
    "SUBSTITUTEWITHINDEX",
}

_COMPARISON_OPS: Set[str] = {"=", "==", "<>", "<", ">", "<=", ">="}

_LEXER_LOCK = threading.Lock()
_LEXER: Optional[DaxLexer] = None


def get_lexer() -> DaxLexer:
    """Shared lexer that knows the DAX function catalogue (lazy, thread-safe)."""
    global _LEXER
    if _LEXER is None:
        with _LEXER_LOCK:
            if _LEXER is None:
                try:
                    from core.dax.knowledge import DaxFunctionDatabase

                    names = DaxFunctionDatabase.get().get_function_names()
                except Exception:  # noqa: BLE001 - never block ref parsing on the DB
                    names = set()
                _LEXER = DaxLexer(function_names=names)
    return _LEXER


@dataclass(frozen=True)
class DaxRef:
    """One lexically resolved reference in a DAX expression.

    ``kind``: ``"qualified"`` (``'T'[c]`` / ``T[c]``), ``"bracket"`` (a lone
    ``[x]`` — a measure, or a column in row context), ``"table"`` (``'T'`` or a
    bare ``T``) or ``"calendar"`` (a calendar object named in the dates argument of a
    time-intelligence function: ``TOTALYTD([Sales], 'Fiscal Calendar')``; ``table`` holds its name). Names are UNESCAPED. ``*_start``/``*_end`` are character offsets
    into the source so callers (``core.dax.dax_rename``) can rewrite in place;
    ``-1`` when that part does not exist.
    """

    kind: str
    table: str
    name: str
    start: int
    end: int
    table_start: int
    table_end: int
    name_start: int
    name_end: int
    table_quoted: bool
    function: str  # enclosing function (upper-case) or ""
    arg_index: int  # argument position inside ``function`` or -1


def declared_names(code: List[Token]) -> Set[str]:
    """Upper-cased VAR names and UDF parameter names declared in *code*
    (a comment/whitespace-free token list)."""
    names: Set[str] = set()
    n = len(code)
    for i, t in enumerate(code):
        if t.type == TokenType.KEYWORD and t.value.upper() == "VAR":
            if i + 1 < n and code[i + 1].type in (TokenType.IDENTIFIER, TokenType.KEYWORD):
                names.add(code[i + 1].value.upper())
            continue
        if not (t.type == TokenType.OPERATOR and t.value == "=>"):
            continue
        # (p1: TYPE, VAL p2 ...) => body — find the parameter list before "=>".
        j = i - 1
        if j < 0 or code[j].type != TokenType.PAREN_CLOSE:
            continue
        depth = 0
        k = j
        while k >= 0:
            if code[k].type == TokenType.PAREN_CLOSE:
                depth += 1
            elif code[k].type == TokenType.PAREN_OPEN:
                depth -= 1
                if depth == 0:
                    break
            k -= 1
        if k < 0:
            continue
        depth = 0
        want = False
        for m in range(k, j + 1):
            tk = code[m]
            if tk.type == TokenType.PAREN_OPEN:
                depth += 1
                want = depth == 1 or want
                continue
            if tk.type == TokenType.PAREN_CLOSE:
                depth -= 1
                continue
            if depth == 1 and tk.type == TokenType.COMMA:
                want = True
                continue
            if want and depth == 1 and tk.type in (TokenType.IDENTIFIER, TokenType.KEYWORD):
                if tk.value.upper() in ("VAL", "EXPR"):
                    continue  # passing-mode prefix, the name follows
                names.add(tk.value.upper())
                want = False
    return names


def scan_references(
    expression: str,
    *,
    known_tables: Optional[Set[str]] = None,
    code: Optional[List[Token]] = None,
) -> List[DaxRef]:
    """Walk the lexer token stream of *expression* and return every reference.

    Strings, comments and ``dt"…"`` literals are never looked into. A bare
    identifier counts as a table reference when *known_tables* (normalized:
    lower-case, collapsed whitespace) contains it, or — without a model index —
    when it sits in a table-argument position of a table function
    (``COUNTROWS(Sales)``) or directly after ``EVALUATE``. VAR names and UDF
    parameters never count as tables and never qualify a column.
    """
    if not isinstance(expression, str) or not expression:
        return []
    if code is None:
        code = get_lexer().tokenize_code(expression)
    declared = declared_names(code)
    refs: List[DaxRef] = []
    stack: List[List[Any]] = []  # [function_upper, arg_index] per open paren/brace
    n = len(code)
    for i, t in enumerate(code):
        tt = t.type
        if tt == TokenType.PAREN_OPEN:
            prev = code[i - 1] if i > 0 else None
            fn = ""
            if prev is not None and prev.type in (
                TokenType.FUNCTION,
                TokenType.IDENTIFIER,
                TokenType.KEYWORD,
            ):
                fn = prev.value.upper()
            stack.append([fn, 0])
            continue
        if tt == TokenType.BRACE_OPEN:
            stack.append(["{", 0])
            continue
        if tt in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            if stack:
                stack.pop()
            continue
        if tt == TokenType.COMMA:
            if stack:
                stack[-1][1] += 1
            continue

        fn, arg = (stack[-1][0], stack[-1][1]) if stack else ("", -1)

        if tt == TokenType.QUALIFIED_REF:
            table, name = split_qualified(t.value)
            split = qualified_table_span(t.value)
            bpos = t.value.find("[", split)
            refs.append(
                DaxRef(
                    "qualified",
                    table,
                    name,
                    t.start,
                    t.end,
                    t.start,
                    t.start + split,
                    t.start + bpos,
                    t.end,
                    True,
                    fn,
                    arg,
                )
            )
            continue

        if tt == TokenType.COLUMN_REF:
            prev = code[i - 1] if i > 0 else None
            name = unbracket(t.value)
            if (
                prev is not None
                and prev.type == TokenType.IDENTIFIER
                and prev.value.upper() not in declared
            ):
                refs.append(
                    DaxRef(
                        "qualified",
                        prev.value,
                        name,
                        prev.start,
                        t.end,
                        prev.start,
                        prev.end,
                        t.start,
                        t.end,
                        False,
                        fn,
                        arg,
                    )
                )
            else:
                refs.append(
                    DaxRef(
                        "bracket", "", name, t.start, t.end, -1, -1, t.start, t.end, False, fn, arg
                    )
                )
            continue

        if tt == TokenType.TABLE_REF:
            refs.append(
                DaxRef(
                    "calendar" if _CALENDAR_ARG_POSITION.get(fn) == arg else "table",
                    unquote_table(t.value),
                    "",
                    t.start,
                    t.end,
                    t.start,
                    t.end,
                    -1,
                    -1,
                    True,
                    fn,
                    arg,
                )
            )
            continue

        if tt != TokenType.IDENTIFIER:
            continue
        upper = t.value.upper()
        nxt = code[i + 1] if i + 1 < n else None
        prev = code[i - 1] if i > 0 else None
        if nxt is not None and nxt.type in (
            TokenType.COLUMN_REF,
            TokenType.PAREN_OPEN,
            TokenType.DOT,
            TokenType.COLON,
        ):
            continue
        if upper in declared:
            continue
        if prev is not None and (
            prev.type == TokenType.DOT
            or (prev.type == TokenType.KEYWORD and prev.value.upper() in ("VAR", "FUNCTION"))
        ):
            continue
        if _CALENDAR_ARG_POSITION.get(fn) == arg and upper not in _BARE_CONSTANTS:
            refs.append(
                DaxRef(
                    "calendar", t.value, "", t.start, t.end, t.start, t.end, -1, -1, False, fn, arg
                )
            )
            continue
        is_table = False
        if known_tables:
            is_table = " ".join(t.value.lower().split()) in known_tables
        elif upper not in _BARE_CONSTANTS:
            if (
                prev is not None
                and prev.type == TokenType.KEYWORD
                and prev.value.upper() == "EVALUATE"
            ):
                is_table = True
            else:
                pos = _TABLE_ARG_POSITIONS.get(fn)
                if pos == "*":
                    is_table = True
                elif pos == "rest":
                    is_table = arg >= 1
                elif isinstance(pos, set):
                    is_table = arg in pos
        if is_table:
            refs.append(
                DaxRef("table", t.value, "", t.start, t.end, t.start, t.end, -1, -1, False, fn, arg)
            )
    return refs


def _virtual_column_names(code: List[Token]) -> Set[str]:
    """Normalized names of extension columns created in the expression
    (``ADDCOLUMNS(T, "Tmp", …)`` → ``tmp``), so ``[Tmp]`` is not a phantom
    measure."""
    out: Set[str] = set()
    stack: List[str] = []
    for i, t in enumerate(code):
        if t.type == TokenType.PAREN_OPEN:
            prev = code[i - 1] if i > 0 else None
            stack.append(prev.value.upper() if prev is not None else "")
        elif t.type == TokenType.PAREN_CLOSE:
            if stack:
                stack.pop()
        elif (
            t.type == TokenType.STRING
            and stack
            and stack[-1] in _VIRTUAL_COLUMN_FUNCTIONS
            and 0 < i < len(code) - 1
            and code[i + 1].type == TokenType.COMMA
            and code[i - 1].type in (TokenType.COMMA, TokenType.PAREN_OPEN)
        ):
            inner = t.value[1:-1] if len(t.value) >= 2 else ""
            out.add(_normalize_name(inner.replace('""', '"')))
    return out


@dataclass
class RelationshipReference:
    """Represents a relationship reference in DAX (RELATED, USERELATIONSHIP, etc.)"""

    function: str  # RELATED, RELATEDTABLE, USERELATIONSHIP, TREATAS, CROSSFILTER
    from_table: str
    from_column: Optional[str]
    to_table: Optional[str] = None
    to_column: Optional[str] = None
    direction: Optional[str] = None  # For CROSSFILTER: NONE, ONEWAY, BOTH


@dataclass
class VariableDefinition:
    """Represents a VAR definition in DAX"""

    name: str
    position: int  # Character position in expression


@dataclass
class DaxReferenceResult:
    """Enhanced result structure for DAX reference parsing"""

    tables: List[str] = field(default_factory=list)
    columns: List[Tuple[str, str]] = field(default_factory=list)
    measures: List[Tuple[str, str]] = field(default_factory=list)
    identifiers: List[str] = field(default_factory=list)
    # Enhanced fields
    relationship_refs: List[RelationshipReference] = field(default_factory=list)
    variables: List[VariableDefinition] = field(default_factory=list)
    filter_columns: List[Tuple[str, str]] = field(
        default_factory=list
    )  # Columns used in CALCULATE filters
    uses_inactive_relationships: bool = False
    relationship_functions_used: List[str] = field(
        default_factory=list
    )  # RELATED, RELATEDTABLE, etc.

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for backward compatibility"""
        return {
            "tables": self.tables,
            "columns": self.columns,
            "measures": self.measures,
            "identifiers": self.identifiers,
            "relationship_refs": [
                {
                    "function": r.function,
                    "from_table": r.from_table,
                    "from_column": r.from_column,
                    "to_table": r.to_table,
                    "to_column": r.to_column,
                    "direction": r.direction,
                }
                for r in self.relationship_refs
            ],
            "variables": [{"name": v.name, "position": v.position} for v in self.variables],
            "filter_columns": self.filter_columns,
            "uses_inactive_relationships": self.uses_inactive_relationships,
            "relationship_functions_used": self.relationship_functions_used,
        }


def _normalize_name(name: str) -> str:
    """Normalize a name for lookup - lowercase and collapse whitespace."""
    if not name:
        return ""
    return " ".join(name.lower().split())


class DaxReferenceIndex:
    """
    Index of known measures and columns for DAX reference resolution.

    Used to distinguish between measure and column references in DAX expressions.
    Enhanced to also track table relationships for better dependency analysis.
    """

    def __init__(self, measure_rows=None, column_rows=None, relationship_rows=None) -> None:
        """
        Initialize the reference index.

        Args:
            measure_rows: List of measure dictionaries with 'Table' and 'Name' keys
            column_rows: List of column dictionaries with 'Table' and 'Name' keys
            relationship_rows: Optional list of relationship dictionaries for relationship validation
        """
        self.measure_keys: Set[str] = set()
        self.measure_names: Dict[str, Set[str]] = {}  # normalized_name -> set of tables
        self.measure_name_original: Dict[str, str] = (
            {}
        )  # normalized_name -> original name (for case preservation)
        self.column_keys: Set[str] = set()
        self.column_names: Dict[str, Set[str]] = {}  # For column name to table mapping
        self.table_names: Set[str] = set()

        # Track relationships for USERELATIONSHIP validation
        self.relationship_pairs: Set[Tuple[str, str, str, str]] = (
            set()
        )  # (from_table, from_col, to_table, to_col)

        if measure_rows:
            for row in measure_rows:
                # Try both bracketed and non-bracketed column names (DMV queries vary)
                table = str(row.get("Table") or row.get("[Table]") or "").strip()
                name = str(row.get("Name") or row.get("[Name]") or "").strip()
                if table and name:
                    normalized = _normalize_name(name)
                    key = f"{_normalize_name(table)}|{normalized}"
                    self.measure_keys.add(key)
                    self.measure_names.setdefault(normalized, set()).add(table)
                    self.measure_name_original[normalized] = name  # Keep original casing
                    self.table_names.add(table)

        if column_rows:
            for row in column_rows:
                # Try both bracketed and non-bracketed column names (DMV queries vary)
                table = str(row.get("Table") or row.get("[Table]") or "").strip()
                name = str(row.get("Name") or row.get("[Name]") or "").strip()
                if table and name:
                    normalized = _normalize_name(name)
                    self.column_keys.add(f"{_normalize_name(table)}|{normalized}")
                    self.column_names.setdefault(normalized, set()).add(table)
                    self.table_names.add(table)

        if relationship_rows:
            for row in relationship_rows:
                from_table = str(row.get("FromTable") or row.get("FROMTABLE") or "").strip()
                from_col = str(row.get("FromColumn") or row.get("FROMCOLUMN") or "").strip()
                to_table = str(row.get("ToTable") or row.get("TOTABLE") or "").strip()
                to_col = str(row.get("ToColumn") or row.get("TOCOLUMN") or "").strip()
                if from_table and from_col and to_table and to_col:
                    self.relationship_pairs.add(
                        (from_table.lower(), from_col.lower(), to_table.lower(), to_col.lower())
                    )

    def normalized_tables(self) -> Set[str]:
        """Normalized (lower-case, collapsed whitespace) table names."""
        return {_normalize_name(t) for t in self.table_names}

    def is_valid_relationship(self, table1: str, col1: str, table2: str, col2: str) -> bool:
        """Check if a relationship exists between two column references"""
        key1 = (table1.lower(), col1.lower(), table2.lower(), col2.lower())
        key2 = (table2.lower(), col2.lower(), table1.lower(), col1.lower())
        return key1 in self.relationship_pairs or key2 in self.relationship_pairs


def _single_qualified(arg: List[Token], declared: Set[str]) -> Optional[Tuple[str, str]]:
    """``(table, column)`` when *arg* is exactly one qualified column reference."""
    if len(arg) == 1 and arg[0].type == TokenType.QUALIFIED_REF:
        return split_qualified(arg[0].value)
    if (
        len(arg) == 2
        and arg[0].type == TokenType.IDENTIFIER
        and arg[1].type == TokenType.COLUMN_REF
        and arg[0].value.upper() not in declared
    ):
        return arg[0].value, unbracket(arg[1].value)
    return None


def _single_table(arg: List[Token], declared: Set[str]) -> Optional[str]:
    if len(arg) == 1:
        if arg[0].type == TokenType.TABLE_REF:
            return unquote_table(arg[0].value)
        if arg[0].type == TokenType.IDENTIFIER and arg[0].value.upper() not in declared:
            return arg[0].value
    return None


def parse_dax_references(
    expression: Optional[str],
    reference_index: Optional[DaxReferenceIndex] = None,
    enhanced: bool = False,
) -> Dict[str, Any]:
    """
    Parse DAX expression and extract references to tables, columns, and measures.

    Args:
        expression: The DAX expression to parse
        reference_index: Optional index to distinguish measures from columns (and
            to recognise bare table names anywhere in the expression)
        enhanced: If True, returns additional relationship / variable context

    Returns:
        Dictionary with keys:
        - tables: List of table names referenced (as written, unescaped)
        - columns: List of (table, column) tuples
        - measures: List of (table, measure) tuples — table "" when unqualified
          and not resolvable through the index
        - identifiers: List of all bracket-enclosed names (unescaped)

        When enhanced=True, also includes:
        - relationship_refs, variables, filter_columns,
          uses_inactive_relationships, relationship_functions_used

    Classification of a lone ``[X]``: a known measure → every table that owns a
    measure of that name; else a known column → every table owning such a column
    (conservative: a row-context column ref marks all candidates used); else an
    extension column created in the expression (``ADDCOLUMNS(…, "X", …)``) is
    skipped; else it is reported as measure ``("", X)``.

    Example:
        >>> parse_dax_references("CALCULATE([Sales], 'Product'[Category] = \\"Bikes\\")")
        {"tables": ["Product"], "columns": [("Product", "Category")],
         "measures": [("", "Sales")], "identifiers": ["Category", "Sales"]}
    """
    empty_result = (
        {
            "tables": [],
            "columns": [],
            "measures": [],
            "identifiers": [],
            "relationship_refs": [],
            "variables": [],
            "filter_columns": [],
            "uses_inactive_relationships": False,
            "relationship_functions_used": [],
        }
        if enhanced
        else {"tables": [], "columns": [], "measures": [], "identifiers": []}
    )

    if not isinstance(expression, str) or not expression.strip():
        return empty_result

    lexer = get_lexer()
    code = lexer.tokenize_code(expression)
    ref_idx = reference_index or DaxReferenceIndex()
    known_tables = ref_idx.normalized_tables() if ref_idx.table_names else None

    tables: Set[str] = set()
    columns: Set[Tuple[str, str]] = set()
    measures: Set[Tuple[str, str]] = set()
    identifiers: Set[str] = set()
    virtual_cols: Optional[Set[str]] = None

    refs = scan_references(expression, known_tables=known_tables, code=code)
    for ref in refs:
        if ref.kind == "table":
            if ref.table:
                tables.add(ref.table)
            continue
        if not ref.name:
            continue
        identifiers.add(ref.name)
        if ref.kind == "qualified":
            if not ref.table:
                continue
            tables.add(ref.table)
            key = f"{_normalize_name(ref.table)}|{_normalize_name(ref.name)}"
            if key in ref_idx.measure_keys:
                measures.add((ref.table, ref.name))
            else:
                columns.add((ref.table, ref.name))
            continue
        # lone [X]
        if ref.name.startswith("@"):
            continue  # extended column of a table expression ([@col])
        normalized = _normalize_name(ref.name)
        measure_owners = ref_idx.measure_names.get(normalized)
        if measure_owners:
            for tbl in measure_owners:
                measures.add((tbl, ref.name))
            continue
        column_owners = ref_idx.column_names.get(normalized)
        if column_owners:
            for tbl in column_owners:
                columns.add((tbl, ref.name))
                tables.add(tbl)
            continue
        if virtual_cols is None:
            virtual_cols = _virtual_column_names(code)
        if normalized in virtual_cols:
            continue
        measures.add(("", ref.name))

    result: Dict[str, Any] = {
        "tables": sorted(tables),
        "columns": sorted(columns),
        "measures": sorted(measures),
        "identifiers": sorted(identifiers),
    }

    if not enhanced:
        return result

    declared = declared_names(code)
    relationship_refs: List[RelationshipReference] = []
    relationship_functions_used: Set[str] = set()
    uses_inactive = False
    for i, t in enumerate(code):
        if t.type not in (TokenType.FUNCTION, TokenType.IDENTIFIER):
            continue
        fn = t.value.upper()
        if fn not in ("RELATED", "RELATEDTABLE", "USERELATIONSHIP", "TREATAS", "CROSSFILTER"):
            continue
        args = lexer.extract_function_args(code, i)
        if not args:
            continue
        if fn == "RELATED":
            qc = _single_qualified(args[0], declared)
            if qc:
                relationship_refs.append(RelationshipReference("RELATED", qc[0], qc[1]))
                relationship_functions_used.add(fn)
        elif fn == "RELATEDTABLE":
            tb = _single_table(args[0], declared)
            if tb:
                relationship_refs.append(RelationshipReference("RELATEDTABLE", tb, None))
                relationship_functions_used.add(fn)
                tables.add(tb)
        elif fn in ("USERELATIONSHIP", "CROSSFILTER"):
            if len(args) < 2:
                continue
            a = _single_qualified(args[0], declared)
            b = _single_qualified(args[1], declared)
            if not (a and b):
                continue
            direction = None
            if fn == "CROSSFILTER" and len(args) >= 3 and len(args[2]) == 1:
                direction = args[2][0].value.upper()
            relationship_refs.append(
                RelationshipReference(
                    fn, a[0], a[1], to_table=b[0], to_column=b[1], direction=direction
                )
            )
            relationship_functions_used.add(fn)
            if fn == "USERELATIONSHIP":
                uses_inactive = True
        elif fn == "TREATAS":
            for arg in args[1:]:
                qc = _single_qualified(arg, declared)
                if qc:
                    relationship_refs.append(RelationshipReference("TREATAS", qc[0], qc[1]))
                    relationship_functions_used.add(fn)

    variables = [
        VariableDefinition(name=code[i + 1].value, position=code[i + 1].start)
        for i, t in enumerate(code[:-1])
        if t.type == TokenType.KEYWORD
        and t.value.upper() == "VAR"
        and code[i + 1].type in (TokenType.IDENTIFIER, TokenType.KEYWORD)
    ]

    filter_columns: Set[Tuple[str, str]] = set()
    for i, t in enumerate(code[:-1]):
        nxt = code[i + 1]
        is_cmp = (nxt.type == TokenType.OPERATOR and nxt.value in _COMPARISON_OPS) or (
            nxt.type == TokenType.KEYWORD and nxt.value.upper() == "IN"
        )
        if not is_cmp:
            continue
        if t.type == TokenType.QUALIFIED_REF:
            filter_columns.add(split_qualified(t.value))
        elif (
            t.type == TokenType.COLUMN_REF
            and i > 0
            and code[i - 1].type == TokenType.IDENTIFIER
            and code[i - 1].value.upper() not in declared
        ):
            filter_columns.add((code[i - 1].value, unbracket(t.value)))

    result["tables"] = sorted(tables)
    result.update(
        {
            "relationship_refs": [
                {
                    "function": r.function,
                    "from_table": r.from_table,
                    "from_column": r.from_column,
                    "to_table": r.to_table,
                    "to_column": r.to_column,
                    "direction": r.direction,
                }
                for r in relationship_refs
            ],
            "variables": [{"name": v.name, "position": v.position} for v in variables],
            "filter_columns": sorted(filter_columns),
            "uses_inactive_relationships": uses_inactive,
            "relationship_functions_used": sorted(relationship_functions_used),
        }
    )
    return result


def parse_dax_references_enhanced(
    expression: Optional[str],
    reference_index: Optional[DaxReferenceIndex] = None,
) -> DaxReferenceResult:
    """
    Parse DAX expression with full enhanced analysis.

    This is a convenience wrapper that always returns a DaxReferenceResult
    with all enhanced fields populated.

    Args:
        expression: The DAX expression to parse
        reference_index: Optional index to distinguish measures from columns

    Returns:
        DaxReferenceResult dataclass with all fields populated
    """
    result_dict = parse_dax_references(expression, reference_index, enhanced=True)

    return DaxReferenceResult(
        tables=result_dict.get("tables", []),
        columns=result_dict.get("columns", []),
        measures=result_dict.get("measures", []),
        identifiers=result_dict.get("identifiers", []),
        relationship_refs=[
            RelationshipReference(
                function=r["function"],
                from_table=r["from_table"],
                from_column=r["from_column"],
                to_table=r.get("to_table"),
                to_column=r.get("to_column"),
                direction=r.get("direction"),
            )
            for r in result_dict.get("relationship_refs", [])
        ],
        variables=[
            VariableDefinition(name=v["name"], position=v["position"])
            for v in result_dict.get("variables", [])
        ],
        filter_columns=result_dict.get("filter_columns", []),
        uses_inactive_relationships=result_dict.get("uses_inactive_relationships", False),
        relationship_functions_used=result_dict.get("relationship_functions_used", []),
    )


__all__ = [
    "DaxRef",
    "DaxReferenceIndex",
    "DaxReferenceResult",
    "RelationshipReference",
    "VariableDefinition",
    "declared_names",
    "get_lexer",
    "parse_dax_references",
    "parse_dax_references_enhanced",
    "normalize_dax_name",
    "scan_references",
]


def normalize_dax_name(name: str) -> str:
    """
    Public alias for name normalization used in DAX reference lookups.
    Normalizes: lowercase + collapse whitespace.
    Use this when building lookups that need to match DAX references.
    """
    return _normalize_name(name)
