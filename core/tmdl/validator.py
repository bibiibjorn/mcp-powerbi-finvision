"""
TMDL Validator - structural validation, reference integrity and linting.

Review G-05 (2026-09-22): the previous validator checked dataType names only,
so orphaned child properties, unterminated quotes, dangling relationship /
sortBy / hierarchy references and column-0 objects all passed. It now runs on
the indentation-scoped tree (:mod:`core.tmdl.tmdl_tree`) and checks what the
TOM ``TmdlSerializer`` rejects when Desktop opens the project:

ERRORS (the project will not open)
  TMDL010 line that is neither an object nor a property
  TMDL011 unterminated quoted name           TMDL012 unterminated ``` block
  TMDL013 indentation jump (orphaned block)   TMDL014 inconsistent sibling indentation
  TMDL016 ``///`` description with no object  TMDL017 property after a child object
  TMDL018 duplicate property in one object (the orphan-merge signature)
  TMDL019 ``key: value`` property owning indented lines (orphaned children)
  TMDL020 unexpected root object in a folder file (e.g. ``measure`` at column 0)
  TMDL021 duplicate object name              TMDL001 invalid dataType
  TMDL030 relationship end not found          TMDL031 sortByColumn not found
  TMDL032 hierarchy level / groupByColumn / alternateOf column not found
  TMDL035 queryGroup not declared             TMDL036 ``ref table`` without a table
  TMDL038 role / perspective / translation names an unknown object

WARNINGS (the project opens; the object evaluates to an error)
  TMDL033 DAX reference to an unknown table / column / measure
  TMDL015 ``///`` description not aligned with its object

Not produced here: the "compatibility level 1702 is below ... 1000000 needed
for function X" message. That is the TOM client library's UDF gate (bundled
TOM 17.0.30 and TE3's TOM both raise it on an untouched S02 tree); it is not
a model defect. :func:`core.tmdl.tom_check.tom_deserialize_check` runs the
real TOM deserializer and classifies exactly that message as ``udf_gate``.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .tmdl_tree import TmdlDocument, TmdlNode, parse_document, split_object_ref

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    """Severity levels for validation issues"""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationError:
    """Represents a validation error or warning"""

    file: str
    line: int
    column: int
    severity: ValidationSeverity
    code: str
    message: str
    suggestion: Optional[str] = None


@dataclass
class LintIssue:
    """Represents a linting/best practices issue"""

    file: str
    line: int
    severity: ValidationSeverity
    rule: str
    message: str
    suggestion: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of TMDL validation"""

    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)
    info: List[ValidationError] = field(default_factory=list)
    files_checked: int = 0
    tom_check: Optional[Dict[str, Any]] = None

    @property
    def total_errors(self) -> int:
        return len(self.errors)

    @property
    def total_warnings(self) -> int:
        return len(self.warnings)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        out: Dict[str, Any] = {
            "success": self.is_valid,
            "errors": [
                {
                    "file": e.file,
                    "line": e.line,
                    "column": e.column,
                    "severity": e.severity.value,
                    "code": e.code,
                    "message": e.message,
                    "suggestion": e.suggestion,
                }
                for e in self.errors + self.warnings + self.info
            ],
            "summary": {
                "total_errors": self.total_errors,
                "total_warnings": self.total_warnings,
                "files_checked": self.files_checked,
            },
        }
        if self.tom_check is not None:
            out["tom_check"] = self.tom_check
        return out


# Child OBJECTS — once one appears, TOM rejects a later plain property of the
# same parent ("Invalid indentation"). Properties must precede child objects.
_CHILD_OBJECTS = frozenset(
    {
        "annotation",
        "extendedProperty",
        "changedProperty",
        "formatStringDefinition",
        "detailRowsDefinition",
        "kpi",
        "variation",
        "column",
        "measure",
        "hierarchy",
        "level",
        "partition",
        "calculationItem",
        "calculationGroup",
        "calendar",
        "calendarColumnGroup",
        "relatedColumnDetails",
        "alternateOf",
        "refreshPolicy",
        "tablePermission",
        "columnPermission",
        "member",
        "perspectiveTable",
        "perspectiveColumn",
        "perspectiveMeasure",
        "perspectiveHierarchy",
        "linguisticMetadata",
        "translations",
    }
)

# Plain properties / bare flags that must come before child objects.
_ORDERED_FLAGS = frozenset(
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
        "discourageImplicitMeasures",
    }
)

# Children that may legitimately repeat under one parent.
_REPEATABLE = frozenset(
    {
        "annotation",
        "extendedProperty",
        "changedProperty",
        "column",
        "measure",
        "hierarchy",
        "level",
        "partition",
        "calculationItem",
        "calendar",
        "calendarColumnGroup",
        "variation",
        "tablePermission",
        "columnPermission",
        "member",
        "perspectiveTable",
        "perspectiveColumn",
        "perspectiveMeasure",
        "perspectiveHierarchy",
        "groupByColumn",
        "table",
        "model",
        "ref",
        "relationship",
        "expression",
        "function",
        "role",
        "queryGroup",
    }
)

_ROOT_KEYWORDS = {
    "tables": {"table"},
    "roles": {"role"},
    "perspectives": {"perspective"},
    "cultures": {"cultureInfo", "culture"},
    "relationships": {"relationship"},
}


def _definition_dir(path: Path) -> Path:
    if (path / "definition").is_dir():
        return path / "definition"
    return path


class _ModelIndex:
    """Names of every object, for reference checks (case-insensitive)."""

    def __init__(self) -> None:
        self.tables: Dict[str, str] = {}
        self.columns: Dict[str, Dict[str, str]] = {}
        self.measures: Dict[str, Dict[str, str]] = {}
        self.all_measures: Set[str] = set()
        self.all_columns: Set[str] = set()
        self.hierarchies: Dict[str, Set[str]] = {}
        self.query_groups: Set[str] = set()
        self.functions: Set[str] = set()
        self.ref_tables: List[Tuple[str, int, str]] = []

    @staticmethod
    def k(name: Optional[str]) -> str:
        return " ".join((name or "").lower().split())

    def has_column(self, table: str, column: str) -> bool:
        return self.k(column) in self.columns.get(self.k(table), {})

    def has_field(self, table: str, name: str) -> bool:
        t = self.k(table)
        n = self.k(name)
        return n in self.columns.get(t, {}) or n in self.measures.get(t, {})


class TmdlValidator:
    """
    TMDL Validation and Linting Engine (structural + reference integrity).
    """

    # P2-27: superset of values PBI Desktop emits in the .pbip format.
    VALID_DATA_TYPES = {
        "automatic",
        "binary",
        "boolean",
        "currency",
        "dateTime",
        "decimal",
        "double",
        "int64",
        "rowNumber",
        "string",
        "variant",
    }

    VALID_CARDINALITIES = {"many", "one", "none"}

    RESERVED_KEYWORDS = {
        "table",
        "column",
        "measure",
        "relationship",
        "partition",
        "annotation",
        "role",
        "expression",
        "dataType",
        "formatString",
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.validation_rules = self.config.get("validation_rules", {})
        self.linting_enabled = self.config.get("linting", {}).get("enabled", True)
        # "off" | "auto" (run TOM when the CLR is available) | "required"
        self.tom_mode = str(self.config.get("tom_check", "off"))

        self.tables: Set[str] = set()
        self.columns: Dict[str, Set[str]] = {}
        self.measures: Dict[str, Set[str]] = {}
        self.relationships: List[Dict[str, str]] = []
        self._docs: List[Tuple[str, TmdlDocument]] = []
        self._index = _ModelIndex()

    # ─── public API ────────────────────────────────────────────────

    def validate_syntax(self, tmdl_path: str) -> ValidationResult:
        """Validate a TMDL definition folder (or its parent)."""
        path = Path(tmdl_path)
        if not path.exists():
            return ValidationResult(
                is_valid=False,
                errors=[
                    ValidationError(
                        file=str(path),
                        line=0,
                        column=0,
                        severity=ValidationSeverity.ERROR,
                        code="TMDL000",
                        message=f"TMDL path does not exist: {tmdl_path}",
                        suggestion="Verify the path is correct and accessible",
                    )
                ],
            )
        try:
            result = ValidationResult(is_valid=True)
            definition = _definition_dir(path)
            self._load(definition, result)
            self._collect_index()
            for rel, doc in self._docs:
                self._check_structure(rel, doc, result)
            self._check_duplicates(result)
            self._check_references(result)
            if self.tom_mode in ("auto", "required"):
                from .tom_check import tom_deserialize_check

                tom = tom_deserialize_check(str(definition))
                result.tom_check = tom
                if tom.get("ran") and tom.get("ok") is False:
                    result.errors.append(
                        ValidationError(
                            file=str(definition),
                            line=0,
                            column=0,
                            severity=ValidationSeverity.ERROR,
                            code="TMDL090",
                            message=f"TOM TmdlSerializer rejected the folder: {tom.get('error')}",
                        )
                    )
                elif not tom.get("ran") and self.tom_mode == "required":
                    result.errors.append(
                        ValidationError(
                            file=str(definition),
                            line=0,
                            column=0,
                            severity=ValidationSeverity.ERROR,
                            code="TMDL091",
                            message=f"TOM check required but unavailable: {tom.get('reason')}",
                        )
                    )
            result.is_valid = len(result.errors) == 0
            logger.info(
                f"TMDL validation complete: {result.files_checked} files, "
                f"{result.total_errors} errors, {result.total_warnings} warnings"
            )
            return result
        except Exception as e:  # noqa: BLE001 - validator must report, not raise
            logger.error(f"Error during TMDL validation: {e}", exc_info=True)
            return ValidationResult(
                is_valid=False,
                errors=[
                    ValidationError(
                        file=tmdl_path,
                        line=0,
                        column=0,
                        severity=ValidationSeverity.ERROR,
                        code="TMDL999",
                        message=f"Validation failed: {str(e)}",
                        suggestion="Check logs for details",
                    )
                ],
            )

    # ─── loading ───────────────────────────────────────────────────

    def _load(self, definition: Path, result: ValidationResult) -> None:
        self._docs = []
        files: List[Tuple[str, Path]] = []
        for name in (
            "database.tmdl",
            "model.tmdl",
            "relationships.tmdl",
            "expressions.tmdl",
            "functions.tmdl",
            "datasources.tmdl",
        ):
            p = definition / name
            if p.is_file():
                files.append((name, p))
        for sub in ("tables", "roles", "perspectives", "cultures", "relationships", "functions"):
            d = definition / sub
            if d.is_dir():
                for p in sorted(d.glob("*.tmdl")):
                    files.append((f"{sub}/{p.name}", p))
        for rel, p in files:
            try:
                doc = parse_document(str(p))
            except (OSError, UnicodeDecodeError) as e:
                result.errors.append(
                    ValidationError(
                        file=str(p),
                        line=0,
                        column=0,
                        severity=ValidationSeverity.ERROR,
                        code="TMDL998",
                        message=f"Failed to read file: {e}",
                        suggestion="Check file encoding and permissions",
                    )
                )
                continue
            self._docs.append((rel, doc))
            result.files_checked += 1

    def _collect_index(self) -> None:
        idx = _ModelIndex()
        self.tables, self.columns, self.measures, self.relationships = set(), {}, {}, []
        for rel, doc in self._docs:
            for root in doc.roots:
                if root.keyword == "table" and root.name:
                    tk = idx.k(root.name)
                    idx.tables[tk] = root.name
                    self.tables.add(root.name)
                    cols = idx.columns.setdefault(tk, {})
                    meas = idx.measures.setdefault(tk, {})
                    self.columns.setdefault(root.name, set())
                    self.measures.setdefault(root.name, set())
                    for c in root.children:
                        if c.keyword == "column" and c.name:
                            cols[idx.k(c.name)] = c.name
                            idx.all_columns.add(idx.k(c.name))
                            self.columns[root.name].add(c.name)
                        elif c.keyword == "measure" and c.name:
                            meas[idx.k(c.name)] = c.name
                            idx.all_measures.add(idx.k(c.name))
                            self.measures[root.name].add(c.name)
                        elif c.keyword == "hierarchy" and c.name:
                            idx.hierarchies.setdefault(tk, set()).add(idx.k(c.name))
                elif root.keyword == "queryGroup" and root.name:
                    idx.query_groups.add(idx.k(root.name))
                elif root.keyword == "function" and root.name:
                    idx.functions.add(idx.k(root.name))
                elif root.keyword == "ref" and root.ref_kind == "table":
                    idx.ref_tables.append((rel, root.start, root.name or ""))
                elif root.keyword == "relationship":
                    self.relationships.append(
                        {
                            "id": root.name or "",
                            "from": root.prop("fromColumn") or "",
                            "to": root.prop("toColumn") or "",
                        }
                    )
        self._index = idx

    # ─── structure ─────────────────────────────────────────────────

    def _err(
        self,
        result: ValidationResult,
        doc: TmdlDocument,
        line: int,
        code: str,
        message: str,
        suggestion: Optional[str] = None,
        warning: bool = False,
    ) -> None:
        entry = ValidationError(
            file=str(doc.path),
            line=line,
            column=0,
            severity=ValidationSeverity.WARNING if warning else ValidationSeverity.ERROR,
            code=code,
            message=message,
            suggestion=suggestion,
        )
        (result.warnings if warning else result.errors).append(entry)

    def _check_structure(self, rel: str, doc: TmdlDocument, result: ValidationResult) -> None:
        for issue in doc.issues:
            self._err(
                result,
                doc,
                issue.line,
                issue.code,
                issue.message,
                suggestion="Fix the indentation / quoting so every line belongs to one object",
                warning=issue.code == "TMDL015",
            )
        folder = rel.split("/", 1)[0] if "/" in rel else ""
        allowed = _ROOT_KEYWORDS.get(folder)
        if allowed is not None:
            roots = [r for r in doc.roots]
            main = [r for r in roots if r.keyword in allowed]
            for r in roots:
                if r.keyword not in allowed:
                    self._err(
                        result,
                        doc,
                        r.start + 1,
                        "TMDL020",
                        f"'{r.keyword}' at column 0 in {rel} (expected {sorted(allowed)[0]})",
                        suggestion="Indent it under its parent object",
                    )
            if folder != "relationships" and len(main) > 1:
                self._err(
                    result,
                    doc,
                    main[1].start + 1,
                    "TMDL020",
                    f"{len(main)} {main[0].keyword} objects in one file",
                )
        for node in doc.iter():
            self._check_node(doc, node, result)

    def _check_node(self, doc: TmdlDocument, node: TmdlNode, result: ValidationResult) -> None:
        if node.kind == "property" and not node.has_expression and node.children:
            self._err(
                result,
                doc,
                node.children[0].start + 1,
                "TMDL019",
                f"'{node.keyword}: …' owns indented lines — orphaned children "
                f"(first: {doc.lines[node.children[0].start].strip()[:50]!r})",
                suggestion="A header line above these lines was probably deleted",
            )
        if node.keyword == "dataType" and node.kind == "property":
            dt = (node.value or "").strip()
            if dt and dt not in self.VALID_DATA_TYPES:
                self._err(
                    result,
                    doc,
                    node.start + 1,
                    "TMDL001",
                    f"Invalid data type '{dt}'. Valid types: "
                    f"{', '.join(sorted(self.VALID_DATA_TYPES))}",
                    suggestion="Use a valid data type (e.g., 'string', 'int64', 'double')",
                )
        seen_child_object: Optional[TmdlNode] = None
        seen_props: Dict[str, int] = {}
        for c in node.children:
            is_plain_prop = (
                c.kind == "property" and not c.has_expression and c.keyword not in _CHILD_OBJECTS
            ) or (c.kind == "flag" and c.keyword in _ORDERED_FLAGS)
            if c.keyword in _CHILD_OBJECTS:
                if seen_child_object is None:
                    seen_child_object = c
            elif is_plain_prop and seen_child_object is not None and node.keyword != "model":
                self._err(
                    result,
                    doc,
                    c.start + 1,
                    "TMDL017",
                    f"property '{c.keyword}' after child object '{seen_child_object.keyword}' "
                    f"in {node.keyword} {node.name or ''}".rstrip(),
                    suggestion="Move plain properties above annotations / formatStringDefinition / "
                    "changedProperty / child objects",
                )
            if c.keyword and c.keyword not in _REPEATABLE and c.kind in ("property", "flag"):
                if c.keyword in seen_props:
                    self._err(
                        result,
                        doc,
                        c.start + 1,
                        "TMDL018",
                        f"duplicate '{c.keyword}' in {node.keyword} {node.name or ''} "
                        f"(first at line {seen_props[c.keyword]}) — an orphaned block merged "
                        "into the previous object?".replace("  ", " "),
                        suggestion="Check whether a header line above was deleted",
                    )
                else:
                    seen_props[c.keyword] = c.start + 1

    def _check_duplicates(self, result: ValidationResult) -> None:
        k = _ModelIndex.k
        tables: Dict[str, Tuple[TmdlDocument, TmdlNode]] = {}
        measures: Dict[str, Tuple[TmdlDocument, TmdlNode]] = {}
        named: Dict[Tuple[str, str], Tuple[TmdlDocument, TmdlNode]] = {}
        for _rel, doc in self._docs:
            for root in doc.roots:
                if root.keyword in ("role", "function", "perspective", "expression") and root.name:
                    key = (root.keyword, k(root.name))
                    if key in named:
                        self._err(
                            result,
                            doc,
                            root.start + 1,
                            "TMDL021",
                            f"duplicate {root.keyword} name '{root.name}'",
                        )
                    named[key] = (doc, root)
                if root.keyword != "table" or not root.name:
                    continue
                if k(root.name) in tables:
                    self._err(
                        result,
                        doc,
                        root.start + 1,
                        "TMDL021",
                        f"duplicate table name '{root.name}'",
                    )
                tables[k(root.name)] = (doc, root)
                fields: Dict[str, TmdlNode] = {}
                for c in root.children:
                    if c.keyword not in ("column", "measure", "hierarchy") or not c.name:
                        continue
                    key2 = k(c.name) if c.keyword != "hierarchy" else "h:" + k(c.name)
                    if key2 in fields:
                        self._err(
                            result,
                            doc,
                            c.start + 1,
                            "TMDL021",
                            f"duplicate name '{c.name}' in table '{root.name}' "
                            f"({fields[key2].keyword} and {c.keyword})",
                        )
                    fields[key2] = c
                    if c.keyword == "measure":
                        if k(c.name) in measures:
                            other = measures[k(c.name)][1]
                            self._err(
                                result,
                                doc,
                                c.start + 1,
                                "TMDL021",
                                f"measure '{c.name}' also defined in table "
                                f"'{other.parent.name if other.parent else '?'}'",
                            )
                        measures[k(c.name)] = (doc, c)

    # ─── references ────────────────────────────────────────────────

    def _check_references(self, result: ValidationResult) -> None:
        idx = self._index
        k = idx.k
        table_files = {k(r.name) for _rel, d in self._docs for r in d.roots if r.keyword == "table"}
        for _rel, doc in self._docs:
            for node in doc.iter():
                kw = node.keyword
                if (
                    kw in ("fromColumn", "toColumn")
                    and node.parent is not None
                    and node.parent.keyword == "relationship"
                ):
                    raw = (
                        doc.lines[node.start].split(":", 1)[1].strip()
                        if ":" in doc.lines[node.start]
                        else ""
                    )
                    t, c = split_object_ref(raw)
                    if not t or not idx.has_column(t, c):
                        self._err(
                            result,
                            doc,
                            node.start + 1,
                            "TMDL030",
                            f"relationship {node.parent.name}: {kw} '{raw}' does not exist",
                            suggestion="Rename the relationship end together with the column/table",
                        )
                elif kw == "sortByColumn":
                    table = _owning_table(node)
                    if table and not idx.has_column(table, node.value or ""):
                        self._err(
                            result,
                            doc,
                            node.start + 1,
                            "TMDL031",
                            f"sortByColumn '{node.value}' is not a column of '{table}'",
                        )
                elif kw in ("groupByColumn",) or (
                    kw == "column"
                    and node.kind == "property"
                    and node.parent is not None
                    and node.parent.keyword == "level"
                ):
                    table = _owning_table(node)
                    if table and not idx.has_column(table, node.value or ""):
                        self._err(
                            result,
                            doc,
                            node.start + 1,
                            "TMDL032",
                            f"{kw} '{node.value}' is not a column of '{table}'",
                        )
                elif kw == "queryGroup" and node.kind == "property":
                    if k(node.value) not in idx.query_groups:
                        self._err(
                            result,
                            doc,
                            node.start + 1,
                            "TMDL035",
                            f"queryGroup '{node.value}' is not declared (model.tmdl 'queryGroup …')",
                        )
                elif kw == "tablePermission" and node.name and k(node.name) not in idx.tables:
                    self._err(
                        result,
                        doc,
                        node.start + 1,
                        "TMDL038",
                        f"role permission on unknown table '{node.name}'",
                    )
                elif kw == "columnPermission" and node.parent is not None and node.name:
                    if not idx.has_column(node.parent.name or "", node.name):
                        self._err(
                            result,
                            doc,
                            node.start + 1,
                            "TMDL038",
                            f"columnPermission '{node.name}' is not a column of '{node.parent.name}'",
                        )
                elif kw == "perspectiveTable" and node.name and k(node.name) not in idx.tables:
                    self._err(
                        result,
                        doc,
                        node.start + 1,
                        "TMDL038",
                        f"perspective names unknown table '{node.name}'",
                    )
                elif kw in ("perspectiveColumn", "perspectiveMeasure") and node.parent is not None:
                    if not idx.has_field(node.parent.name or "", node.name or ""):
                        self._err(
                            result,
                            doc,
                            node.start + 1,
                            "TMDL038",
                            f"perspective names unknown object '{node.parent.name}'[{node.name}]",
                        )
                if node.has_expression and _is_dax_carrier(node):
                    self._check_dax(doc, node, result)
        for rel, line, name in idx.ref_tables:
            if k(name) not in table_files:
                doc = next(d for r, d in self._docs if r == rel)
                self._err(
                    result,
                    doc,
                    line + 1,
                    "TMDL036",
                    f"'ref table {name}' but no table '{name}' exists",
                )

    def _check_dax(self, doc: TmdlDocument, node: TmdlNode, result: ValidationResult) -> None:
        expr = node.expression
        if not expr:
            return
        try:
            from core.dax.dax_reference_parser import scan_references
        except ImportError:  # pragma: no cover
            return
        idx = self._index
        k = idx.k
        try:
            refs = scan_references(expr, known_tables=set(idx.tables))
        except Exception:  # noqa: BLE001 - lexer failure is not a model error
            return
        for ref in refs:
            if ref.kind == "table":
                if k(ref.table) not in idx.tables and ref.table_quoted:
                    self._dax_warn(doc, node, result, f"unknown table '{ref.table}'")
            elif ref.kind == "qualified":
                tk = k(ref.table)
                if tk not in idx.tables:
                    self._dax_warn(doc, node, result, f"unknown table in '{ref.table}'[{ref.name}]")
                elif not idx.has_field(ref.table, ref.name) and k(ref.name) not in idx.all_measures:
                    self._dax_warn(
                        doc,
                        node,
                        result,
                        f"'{ref.table}'[{ref.name}] is not a column or measure of that table",
                    )

    def _dax_warn(
        self, doc: TmdlDocument, node: TmdlNode, result: ValidationResult, msg: str
    ) -> None:
        owner = f"{node.keyword} {node.name or ''}".strip()
        if node.kind == "property" and node.parent is not None:
            owner = f"{node.parent.keyword} {node.parent.name or ''} / {node.keyword}".strip()
        self._err(
            result,
            doc,
            node.start + 1,
            "TMDL033",
            f"{owner}: {msg}",
            warning=True,
            suggestion="Update the reference (rename cascade) or restore the object",
        )

    # ─── lint ──────────────────────────────────────────────────────


def _owning_table(node: TmdlNode) -> Optional[str]:
    for a in node.ancestors():
        if a.keyword == "table":
            return a.name
    return None


def _is_dax_carrier(node: TmdlNode) -> bool:
    """``=`` bodies that are DAX (not M partitions, JSON, annotations)."""
    kw = node.keyword
    if kw in (
        "measure",
        "calculationItem",
        "function",
        "tablePermission",
        "formatStringDefinition",
        "detailRowsDefinition",
        "targetExpression",
        "statusExpression",
        "trendExpression",
    ):
        return True
    if kw == "column" and node.kind == "object":
        return True
    if kw == "source" and node.parent is not None and node.parent.keyword == "partition":
        return (node.parent.expr_inline or "").strip().lower().startswith("calculated")
    return False
