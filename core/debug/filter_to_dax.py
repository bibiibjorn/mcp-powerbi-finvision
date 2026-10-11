"""
Filter to DAX Converter

Converts PBIP filter definitions and slicer selections to executable DAX expressions.

The PBIR filter grammar (the semantic-query ``Where`` conditions) is compiled
recursively: ``In`` (values or a TopN subquery table), ``Not``, ``And``, ``Or``,
``Comparison`` (int ``ComparisonKind`` 0..4 or the legacy names), ``Between``
(literal bounds or relative-date ``DateSpan`` bounds), ``Contains`` /
``StartsWith`` / ``EndsWith`` and the legacy ``IsBlank`` / ``IsNotBlank``.
Operands are ``Column``, ``Measure``, ``Aggregation`` (implicit measure),
``HierarchyLevel``, ``Literal`` and the ``Now`` / ``DateAdd`` / ``DateSpan``
date expressions. Inverted selection (``isInvertedSelectionMode``) is read with
``core.pbip.pbir_model`` so an exclude list is never re-emitted as an include
list (F-02 / F-04). Anything the compiler cannot express is reported through
``convert_filter_detailed`` instead of silently vanishing (F-04, F-39(3)).
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from core.dax.dax_names import column_ref as _column_ref
from core.dax.dax_names import dax_string, quote_table

logger = logging.getLogger(__name__)


@dataclass
class TypedValue:
    """
    A value with preserved type information from PBIP literals.

    PBIP stores values with type indicators:
    - 'text'L for strings
    - 123L for integers
    - 123.45D for decimals
    - trueL/falseL for booleans

    This class preserves the original type so we generate correct DAX.
    """

    value: Any  # The actual value
    value_type: str  # 'string', 'integer', 'decimal', 'boolean', 'date', 'unknown'

    def __repr__(self):
        return f"TypedValue({self.value!r}, {self.value_type})"


@dataclass
class FilterExpression:
    """Represents a converted DAX filter expression."""

    dax: str  # The DAX expression (e.g., "'Date'[Year] IN {2024, 2025}")
    source: str  # Where it came from: 'report', 'page', 'visual', 'slicer', 'manual'
    table: str  # Table name
    column: str  # Column name
    condition_type: str  # 'In', 'Comparison', 'Between', 'Not', 'IsBlank', etc.
    values: List[Any]  # The filter values
    original: Dict[str, Any] = field(default_factory=dict)  # Original filter definition
    is_field_parameter: bool = False  # True if this is a field parameter table (has composite keys)
    classification: str = (
        "data"  # Filter classification: 'data', 'field_parameter', 'ui_control', 'unknown'
    )
    has_null_values: bool = False  # True if filter contains null/blank values
    # Composite TREATAS support (multi-column field params: sf Row Drill, sf Slicer 1, etc.)
    composite_columns: Optional[List[str]] = (
        None  # Full 'Table'[Col] refs for all columns; when set, use composite TREATAS
    )
    composite_tuples: Optional[List[List[Any]]] = None  # List of value tuples, one per selected row
    # --- F-02..F-06: fidelity metadata -------------------------------------
    #: "include" | "exclude" (inverted selection / NOT IN).
    polarity: str = "include"
    #: A DAX TABLE expression usable as a SUMMARIZECOLUMNS / CALCULATE filter
    #: argument (TREATAS for plain include lists, FILTER(KEEPFILTERS(VALUES(..)),
    #: <predicate>) for everything else). None -> the builder derives it.
    filter_table_dax: Optional[str] = None
    #: DAX literal text of every value (parallel to ``values``) for include lists.
    literals: Optional[List[str]] = None
    #: 'Table'[Column] refs the predicate references.
    columns: List[str] = field(default_factory=list)
    #: Measure (value) filter: the predicate tests a measure, not a column.
    is_measure_filter: bool = False
    #: The measure expression the value filter evaluates ('T'[M], SUM('T'[C])...).
    measure_expression: Optional[str] = None
    #: Predicate with the measure replaced by ``VALUE_PLACEHOLDER``.
    value_predicate: Optional[str] = None
    #: TopN filter spec: {column_ref, n, order_expression, descending}.
    topn: Optional[Dict[str, Any]] = None
    #: Human-readable caveats (relative date clock, approximated shapes...).
    notes: List[str] = field(default_factory=list)

    def calculate_arg(self) -> Optional[str]:
        """This filter as a CALCULATE / CALCULATETABLE filter argument, or None.

        Measure (value) filters cannot be a CALCULATE argument (a boolean filter
        argument may not reference a measure); callers must apply them on the
        grouped rows instead and report them as not applied.
        """
        if self.is_measure_filter:
            return None
        if self.filter_table_dax:
            return self.filter_table_dax
        return self.dax or None


#: Placeholder the builder swaps for the value-filter projection column.
VALUE_PLACEHOLDER = "__MCP_VALUE__"


# Field parameter table detection patterns
# These tables have composite keys and cause errors when used in filters
# NOTE: Only 'sf ' (slicer field) prefix indicates field parameters.
# The 's ' prefix is for regular selection/disconnected slicer tables (NOT field parameters).
# True field parameters have SystemFlags="2" or contain NAMEOF() in their DAX.
# NOTE: These patterns are tuned for Finvision model naming conventions. Other deployments may need to customize.
FIELD_PARAMETER_PATTERNS = [
    "sf filter",  # Field parameter filter tables (sf Filter 1, sf Filter X)
    "sf row",  # Row drill field parameters (sf Row Drill)
    "sf slicer",  # Slicer field parameters (sf Slicer 1, sf Slicer X)
    "sf column",  # Column field parameters (sf Column X)
    "sf period",  # Period field parameters (sf Period Slicer)
    "sf time",  # Time field parameters (sf Time Series)
    "mf ",  # Measure field parameters (mf Return (%), mf Performance)
    "field parameter",
    "fieldparameter",
    "_fp_",  # Alternative naming pattern
    "slicer param",
    "slicerparam",
    "_field_param",  # Another common pattern
]

# UI control / formatting table patterns - filters on these affect UI, not data
UI_CONTROL_PATTERNS = [
    "decimal",  # Decimal formatting tables
    "scale",  # Scale formatting tables
    "format",  # Format control tables
    "display",  # Display control tables
    "_ui_",  # UI control tables
    "_ctrl_",  # Control tables
]


# Filter classification types
class FilterClassification:
    """Classification of filters by their purpose."""

    DATA = "data"  # Actual data filters (Family, Period, Currency, etc.)
    FIELD_PARAMETER = "field_parameter"  # Field parameter tables (composite keys)
    UI_CONTROL = "ui_control"  # UI control filters (formatting, display options)
    UNKNOWN = "unknown"


def is_field_parameter_table(table_name: str) -> bool:
    """
    Detect if a table is a field parameter table.

    Field parameter tables (sf*/mf* prefix) have composite keys that cause errors in DAX filters.
    The model's measures handle field parameter logic internally — never include these in TREATAS.
    """
    if not table_name:
        return False

    table_lower = table_name.lower().strip("'")

    # Check for common field parameter patterns
    for pattern in FIELD_PARAMETER_PATTERNS:
        if pattern in table_lower:
            return True

    # Check for specific naming conventions with space after prefix
    # Only 'sf ' (slicer field) prefix indicates field parameters
    # Tables like "sf Filter 1", "sf Row Drill", "sf Slicer 1"
    # NOTE: 's ' prefix is for regular selection tables (s Period View, s Reporting Currency)
    # which are NOT field parameters - they're disconnected slicer tables
    prefixes = ["sf ", "'sf ", "mf ", "'mf "]
    for prefix in prefixes:
        if table_lower.startswith(prefix):
            # sf/mf prefix tables are field parameters
            return True

    # Check for field parameter indicator (often have 'Fields' suffix)
    if table_lower.endswith(" fields") or table_lower.endswith("fields"):
        return True

    return False


def is_ui_control_table(table_name: str) -> bool:
    """
    Detect if a table is a UI control / formatting table.

    True for pure formatting/display tables. Note: 's ' prefix tables are regular selection
    tables that PBI always includes in queries — they are NOT UI controls.
    """
    if not table_name:
        return False

    table_lower = table_name.lower().strip("'")

    # 's ' prefix = regular selection / disconnected slicer tables (e.g., 's Scale',
    # 's Decimal', 's Reporting Currency'). Power BI always includes these in the
    # visual query TREATAS filters — never classify them as UI controls.
    if table_lower.startswith("s ") and not table_lower.startswith("sf "):
        return False

    for pattern in UI_CONTROL_PATTERNS:
        if pattern in table_lower:
            return True

    return False


def classify_filter(table_name: str, column_name: str = None) -> str:
    """
    Classify a filter as DATA, FIELD_PARAMETER, UI_CONTROL, or UNKNOWN.
    """
    if is_field_parameter_table(table_name):
        return FilterClassification.FIELD_PARAMETER

    if is_ui_control_table(table_name):
        return FilterClassification.UI_CONTROL

    # Default to data filter
    return FilterClassification.DATA


def calculate_filter_args(filters: List["FilterExpression"]) -> Tuple[List[str], List[Dict]]:
    """Split filters into valid CALCULATE filter arguments and the ones that aren't.

    Only DATA filters are applied (field-parameter and UI-control filters change
    which measure/format is shown, not the data -- F-27). Measure (value)
    filters cannot be a CALCULATE argument; they are returned in ``skipped``
    with a reason so the caller can report ``fidelity: partial``.
    """
    args: List[str] = []
    skipped: List[Dict] = []
    for f in filters or []:
        classification = getattr(f, "classification", FilterClassification.DATA)
        if classification != FilterClassification.DATA:
            continue
        arg = f.calculate_arg() if hasattr(f, "calculate_arg") else getattr(f, "dax", None)
        if not arg:
            skipped.append(
                {
                    "table": getattr(f, "table", ""),
                    "column": getattr(f, "column", ""),
                    "source": getattr(f, "source", ""),
                    "type": getattr(f, "condition_type", ""),
                    "reason": (
                        "measure (value) filter - it filters the visual's grouped rows and "
                        "cannot be a CALCULATE argument"
                        if getattr(f, "is_measure_filter", False)
                        else "no DAX expression"
                    ),
                }
            )
            continue
        args.append(arg)
    return args, skipped


# ---------------------------------------------------------------------------
# PBIR semantic-query enums
# ---------------------------------------------------------------------------

#: ``Comparison.ComparisonKind`` -- ints in PBIR (pbir_model.ADVANCED_FILTER_COMPARISON),
#: names in older layouts. There is no NotEqual kind in PBIR.
_COMPARISON_OPERATORS: Dict[Any, str] = {
    0: "=",
    1: ">",
    2: ">=",
    3: "<",
    4: "<=",
    "Equal": "=",
    "GreaterThan": ">",
    "GreaterThanOrEqual": ">=",
    "LessThan": "<",
    "LessThanOrEqual": "<=",
    "NotEqual": "<>",
}

#: ``TimeUnit`` of DateAdd / DateSpan (semantic-query enum).
_TIME_UNITS: Dict[int, str] = {
    0: "day",
    1: "week",
    2: "month",
    3: "year",
    4: "decade",
    5: "second",
    6: "minute",
    7: "hour",
}

#: ``Aggregation.Function`` (QueryAggregateFunction).
_AGGREGATION_FUNCTIONS: Dict[int, str] = {
    0: "SUM",
    1: "AVERAGE",
    2: "DISTINCTCOUNT",
    3: "MIN",
    4: "MAX",
    5: "COUNTA",
    6: "MEDIAN",
    7: "STDEV.S",
    8: "VAR.S",
}


class _Unsupported(Exception):
    """A filter shape the compiler cannot express in DAX."""


@dataclass
class _CompileContext:
    aliases: Dict[str, str] = field(default_factory=dict)  # From Name -> Entity
    subqueries: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # Name -> Query
    columns: List[Tuple[str, str]] = field(default_factory=list)
    measures: List[str] = field(default_factory=list)  # measure expressions
    values: List[Any] = field(default_factory=list)
    literals: List[str] = field(default_factory=list)
    has_null: bool = False
    kinds: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    topn: Optional[Dict[str, Any]] = None
    replace_measures: bool = False


class FilterToDaxConverter:
    """
    Converts PBIP filter definitions to executable DAX expressions.

    Supports:
    - Report/Page/Visual level filters
    - Slicer selections
    - Various condition types (In, Comparison, Between, Not, IsBlank)
    - Type-aware value formatting (preserves string vs numeric types)
    """

    # Power BI data type mapping (covers string names, .NET types, and AS enum integers)
    POWERBI_TYPE_MAP = {
        "String": "string",
        "Int64": "integer",
        "Double": "decimal",
        "Decimal": "decimal",
        "Currency": "decimal",
        "DateTime": "date",
        "Date": "date",
        "Boolean": "boolean",
        "Binary": "string",  # Treat as string for filtering
        # .NET type names (returned by some DMV configurations)
        "System.String": "string",
        "System.Int64": "integer",
        "System.Double": "decimal",
        "System.Decimal": "decimal",
        "System.DateTime": "date",
        "System.Boolean": "boolean",
        # AS DataType enum integer values
        "2": "string",
        "6": "integer",
        "8": "decimal",
        "9": "date",
        "10": "decimal",
        "11": "boolean",
        "17": "string",
    }

    def __init__(self):
        """Initialize the converter."""
        self.logger = logging.getLogger(__name__)
        # Cache for column data types: {'TableName.ColumnName': 'string'|'integer'|...}
        self._column_types: Dict[str, str] = {}

    def set_column_types(self, column_types: Dict[str, str]) -> None:
        """
        Set column types from model metadata. column_types maps 'Table.Column' → type string.
        """
        self._column_types = column_types

    def set_column_type(self, table: str, column: str, data_type: str) -> None:
        """
        Set a single column's data type (Power BI type e.g. 'String', 'Int64' mapped internally).
        """
        key = f"{table}.{column}"
        # Map Power BI type to internal type if needed
        mapped_type = self.POWERBI_TYPE_MAP.get(data_type, data_type.lower())
        self._column_types[key] = mapped_type
        self.logger.debug(f"Set column type: {key} = {mapped_type}")

    def get_column_type(self, table: str, column: str) -> Optional[str]:
        """Get the cached data type for a column."""
        key = f"{table}.{column}"
        return self._column_types.get(key)

    def load_column_types_from_model(self, query_executor) -> int:
        """
        Load column data types from the connected Power BI model; returns count loaded.
        """
        if not query_executor:
            return 0

        try:
            # Query COLUMNS DMV to get data types
            result = query_executor.execute_info_query("COLUMNS")
            if not result.get("success") or not result.get("rows"):
                return 0

            count = 0
            for row in result["rows"]:
                # K-08(b): INFO.COLUMNS rows carry Table (resolved from TableID by
                # execute_info_query) / ExplicitName|InferredName /
                # ExplicitDataType — never TableName/ColumnName/DataType.
                if str(row.get("Type", row.get("[Type]", ""))) == "3":
                    continue  # RowNumber column
                table_name = str(
                    row.get("Table") or row.get("TableName") or row.get("[TableName]") or ""
                )
                column_name = str(
                    row.get("ExplicitName")
                    or row.get("InferredName")
                    or row.get("ColumnName")
                    or row.get("[ColumnName]")
                    or ""
                )
                raw_type = row.get("ExplicitDataType")
                if raw_type is None or str(raw_type) == "1":  # 1 = Automatic
                    raw_type = row.get("InferredDataType", raw_type)
                if raw_type is None:
                    raw_type = row.get("DataType", row.get("[DataType]", ""))
                data_type = str(raw_type) if raw_type is not None else ""

                # Clean up table name (remove quotes)
                if table_name.startswith("'") and table_name.endswith("'"):
                    table_name = table_name[1:-1]

                if table_name and column_name and data_type:
                    self.set_column_type(table_name, column_name, data_type)
                    count += 1

            self.logger.info(f"Loaded {count} column types from model")
            return count

        except Exception as e:
            self.logger.warning(f"Error loading column types from model: {e}")
            return 0

    # ------------------------------------------------------------------
    # PBIR filterConfig entries (report / page / visual level)
    # ------------------------------------------------------------------

    def convert_filter(
        self,
        filter_def: Dict[str, Any],
        source: str = "unknown",
        visual_json: Optional[Dict[str, Any]] = None,
    ) -> Optional[FilterExpression]:
        """
        Convert a single PBIP filter definition to a DAX FilterExpression, or None.

        None means "no filter to apply" (inactive entry) or "could not convert";
        use :meth:`convert_filter_detailed` to tell the two apart.
        """
        expr, _issue = self.convert_filter_detailed(filter_def, source, visual_json)
        return expr

    def convert_filter_detailed(
        self,
        filter_def: Dict[str, Any],
        source: str = "unknown",
        visual_json: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[FilterExpression], Optional[Dict[str, Any]]]:
        """Convert a filterConfig entry; returns ``(expression, issue)``.

        ``issue`` is None on success. Otherwise it is ``{"name", "source", "type",
        "reason", "inactive"}``: ``inactive: True`` marks an entry that filters
        nothing (no ``filter`` / ``Where``, an inverted "select all", a
        drillthrough placeholder), ``inactive: False`` a shape that could not be
        converted -- the reconstructed query then does NOT reproduce the visual
        (``fidelity: partial``).
        """
        if not isinstance(filter_def, dict):
            return None, None
        name = filter_def.get("name", "")
        ftype = filter_def.get("type", "")

        def _issue(reason: str, inactive: bool) -> Dict[str, Any]:
            target_table, target_col = self._extract_target(filter_def)
            return {
                "name": name,
                "source": source,
                "type": ftype,
                "table": target_table,
                "column": target_col,
                "reason": reason,
                "inactive": inactive,
            }

        try:
            query_filter = filter_def.get("filter")
            where = (query_filter or {}).get("Where") if isinstance(query_filter, dict) else None
            inverted = self._is_inverted(filter_def, visual_json)
            if not where:
                if inverted:
                    return None, _issue("inverted selection with no exclusions (all)", True)
                if filter_def.get("howCreated") == "Drillthrough":
                    return None, _issue("drillthrough placeholder (applied when invoked)", True)
                return None, _issue("no filter condition set", True)

            ctx = _CompileContext()
            self._load_from(query_filter.get("From") or [], ctx)
            target_table, target_col = self._extract_target(filter_def)

            parts: List[str] = []
            for clause in where:
                condition = clause.get("Condition") if isinstance(clause, dict) else None
                if not isinstance(condition, dict):
                    continue
                parts.append(self._compile_condition(condition, ctx, (target_table, target_col)))
            if not parts:
                return None, _issue("empty Where clause", True)
            predicate = parts[0] if len(parts) == 1 else "(" + " && ".join(parts) + ")"

            plain_in = ctx.kinds == ["In"] and len(where) == 1
            polarity = "include"
            if inverted and plain_in:
                # Legacy/advanced-slicer shape: In.Values + inverted flag = EXCLUDE list.
                predicate = f"NOT({predicate})"
                polarity = "exclude"
            elif "NotIn" in ctx.kinds:
                polarity = "exclude"
            elif inverted:
                ctx.notes.append(
                    "isInvertedSelectionMode on a non-categorical condition was ignored"
                )

            expr = self._finish(
                filter_def, source, predicate, ctx, polarity, plain_in and not inverted
            )
            return expr, None
        except _Unsupported as e:
            return None, _issue(str(e), False)
        except Exception as e:  # noqa: BLE001 — a malformed entry must not abort the visual
            self.logger.warning(f"Error converting filter {name!r}: {e}")
            return None, _issue(f"conversion error: {e}", False)

    def _is_inverted(self, filter_def: Dict[str, Any], visual_json: Optional[Dict]) -> bool:
        try:
            from core.pbip.pbir_model import is_inverted_selection

            return bool(is_inverted_selection(filter_def, visual_json))
        except Exception:  # pragma: no cover - pure function, defensive
            return False

    def _finish(
        self,
        filter_def: Dict[str, Any],
        source: str,
        predicate: str,
        ctx: _CompileContext,
        polarity: str,
        plain_include: bool,
    ) -> FilterExpression:
        target_table, target_col = self._extract_target(filter_def)
        columns = list(dict.fromkeys(ctx.columns))
        table = target_table or (columns[0][0] if columns else "")
        column = target_col or (columns[0][1] if columns else "")
        col_refs = [_column_ref(t, c) for t, c in columns]
        measures = list(dict.fromkeys(ctx.measures))
        ftype = filter_def.get("type", "")

        fe = FilterExpression(
            dax=predicate,
            source=source,
            table=table,
            column=column,
            condition_type=(
                "IsNotBlank"
                if ctx.kinds == ["Comparison", "Not"] and predicate.startswith("NOT(ISBLANK(")
                else self._condition_type(ctx, ftype, polarity)
            ),
            values=list(ctx.values),
            original=filter_def,
            is_field_parameter=is_field_parameter_table(table),
            classification=classify_filter(table, column),
            has_null_values=ctx.has_null,
            polarity=polarity,
            columns=col_refs,
            notes=list(dict.fromkeys(ctx.notes)),
        )

        if measures:
            fe.is_measure_filter = True
            fe.measure_expression = measures[0]
            if len(measures) > 1:
                fe.notes.append(
                    "value filter compares several measures; only the first is projected"
                )
            # Recompile with the measure replaced by the projection placeholder.
            vctx = _CompileContext(aliases=dict(ctx.aliases), subqueries=dict(ctx.subqueries))
            vctx.replace_measures = True
            where = (filter_def.get("filter") or {}).get("Where") or []
            vparts = [
                self._compile_condition(w["Condition"], vctx, (target_table, target_col))
                for w in where
                if isinstance(w, dict) and isinstance(w.get("Condition"), dict)
            ]
            fe.value_predicate = vparts[0] if len(vparts) == 1 else "(" + " && ".join(vparts) + ")"
            return fe

        if ctx.topn is not None:
            fe.topn = ctx.topn
            fe.filter_table_dax = f"KEEPFILTERS({self._topn_table(ctx.topn)})"
            fe.dax = fe.filter_table_dax
            return fe

        if plain_include and len(col_refs) == 1 and ctx.literals:
            fe.literals = list(ctx.literals)
            values_set = ", ".join(ctx.literals + (["BLANK()"] if ctx.has_null else []))
            fe.filter_table_dax = f"TREATAS({{{values_set}}}, {col_refs[0]})"
        else:
            fe.filter_table_dax = self._filter_table(columns, predicate)
        return fe

    @staticmethod
    def _condition_type(ctx: _CompileContext, ftype: str, polarity: str) -> str:
        kinds = ctx.kinds
        if ctx.measures:
            return "Measure"
        if ctx.topn is not None or ftype == "TopN":
            return "TopN"
        if "RelativeDate" in kinds or ftype in ("RelativeDate", "RelativeTime"):
            return "RelativeDate"
        if kinds == ["In"]:
            return "Not" if polarity == "exclude" else "In"
        if kinds == ["NotIn"]:
            return "Not"
        if len(kinds) == 1:
            return kinds[0]
        if set(kinds) <= {"Comparison", "And"} and "Comparison" in kinds:
            return "Between" if kinds.count("Comparison") == 2 else "Advanced"
        return "Advanced"

    @staticmethod
    def _filter_table(columns: List[Tuple[str, str]], predicate: str) -> str:
        """PBI's filter-table shape for a non-TREATAS predicate."""
        if not columns:
            return f"FILTER({{1}}, {predicate})"
        refs = [_column_ref(t, c) for t, c in columns]
        if len(columns) == 1:
            base = f"VALUES({refs[0]})"
        elif len({t for t, _ in columns}) == 1:
            base = f"SUMMARIZE({quote_table(columns[0][0])}, {', '.join(refs)})"
        else:
            base = "CROSSJOIN(" + ", ".join(f"VALUES({r})" for r in refs) + ")"
        return f"FILTER(KEEPFILTERS({base}), {predicate})"

    @staticmethod
    def _topn_table(topn: Dict[str, Any]) -> str:
        direction = "DESC" if topn.get("descending", True) else "ASC"
        col = topn["column_ref"]
        return f"TOPN({int(topn['n'])}, VALUES({col}), {topn['order_expression']}, {direction})"

    # -- compiler --------------------------------------------------------

    def _load_from(self, from_list: List[Dict[str, Any]], ctx: _CompileContext) -> None:
        for item in from_list or []:
            if not isinstance(item, dict):
                continue
            alias = item.get("Name")
            if not alias:
                continue
            if item.get("Entity"):
                ctx.aliases[alias] = item["Entity"]
            sub = (
                (item.get("Expression") or {}).get("Subquery")
                if isinstance(item.get("Expression"), dict)
                else None
            )
            if isinstance(sub, dict) and isinstance(sub.get("Query"), dict):
                ctx.subqueries[alias] = sub["Query"]

    @staticmethod
    def _entity(source_ref: Dict[str, Any], ctx: _CompileContext) -> str:
        if not isinstance(source_ref, dict):
            return ""
        if source_ref.get("Entity"):
            return source_ref["Entity"]
        alias = source_ref.get("Source", "")
        return ctx.aliases.get(alias, alias)

    def _compile_operand(
        self, expr: Dict[str, Any], ctx: _CompileContext
    ) -> Tuple[str, Optional[Tuple[str, str]]]:
        """Compile an operand; returns (dax, (table, column) when it is a column)."""
        if not isinstance(expr, dict):
            raise _Unsupported(f"operand is not an object: {expr!r}")
        if "Column" in expr:
            col = expr["Column"]
            table = self._entity((col.get("Expression") or {}).get("SourceRef", {}), ctx)
            name = col.get("Property", "")
            if not table or not name:
                raise _Unsupported("column operand without entity/property")
            ctx.columns.append((table, name))
            return _column_ref(table, name), (table, name)
        if "Measure" in expr:
            m = expr["Measure"]
            table = self._entity((m.get("Expression") or {}).get("SourceRef", {}), ctx)
            name = m.get("Property", "")
            if not name:
                raise _Unsupported("measure operand without property")
            ref = _column_ref(table, name) if table else f"[{name.replace(']', ']]')}]"
            ctx.measures.append(ref)
            return (VALUE_PLACEHOLDER if ctx.replace_measures else ref), None
        if "Aggregation" in expr:
            agg = expr["Aggregation"]
            fn = _AGGREGATION_FUNCTIONS.get(agg.get("Function"))
            inner = agg.get("Expression") or {}
            if fn is None or "Column" not in inner:
                raise _Unsupported(f"aggregation function {agg.get('Function')!r}")
            col = inner["Column"]
            table = self._entity((col.get("Expression") or {}).get("SourceRef", {}), ctx)
            ref = f"{fn}({_column_ref(table, col.get('Property', ''))})"
            ctx.measures.append(ref)
            ctx.notes.append(f"implicit measure {ref} (Aggregation function {agg.get('Function')})")
            return (VALUE_PLACEHOLDER if ctx.replace_measures else ref), None
        if "HierarchyLevel" in expr:
            hl = expr["HierarchyLevel"]
            hier = (hl.get("Expression") or {}).get("Hierarchy") or {}
            table = self._entity((hier.get("Expression") or {}).get("SourceRef", {}), ctx)
            level = hl.get("Level", "")
            if not table or not level:
                raise _Unsupported("hierarchy level without entity/level")
            ctx.columns.append((table, level))
            ctx.notes.append(f"hierarchy level {level!r} treated as column {level!r}")
            return _column_ref(table, level), (table, level)
        if "Literal" in expr:
            return self._literal_dax(expr["Literal"].get("Value"), ctx), None
        if any(k in expr for k in ("Now", "DateAdd", "DateSpan")):
            start, _end = self._date_expr(expr, ctx)
            return start, None
        raise _Unsupported(f"operand kind {next(iter(expr), '?')!r}")

    def _literal_dax(self, raw: Any, ctx: Optional[_CompileContext], col=None) -> str:
        typed = self._clean_literal_value(raw)
        if self._is_null_value(raw) or typed.value is None:
            if ctx is not None:
                ctx.has_null = True
            return "BLANK()"
        if col is not None and typed.value_type == "unknown":
            known = self.get_column_type(*col)
            if known:
                typed = TypedValue(typed.value, known)
        return self._format_single_value_for_dax(typed)

    def _date_expr(self, expr: Dict[str, Any], ctx: _CompileContext) -> Tuple[str, Optional[str]]:
        """(start, end) of a date expression; end is None unless it is a DateSpan."""
        if "Now" in expr:
            ctx.kinds.append("RelativeDate")
            ctx.notes.append("relative date evaluated against the engine clock (NOW())")
            return "NOW()", None
        if "DateAdd" in expr:
            da = expr["DateAdd"]
            base, _ = self._date_expr(da.get("Expression") or {}, ctx)
            amount = int(da.get("Amount", 0) or 0)
            unit = _TIME_UNITS.get(da.get("TimeUnit"))
            if unit == "day":
                return f"({base} + {amount})", None
            if unit == "week":
                return f"({base} + {7 * amount})", None
            if unit == "month":
                return f"EDATE({base}, {amount})", None
            if unit == "year":
                return f"EDATE({base}, {12 * amount})", None
            if unit == "decade":
                return f"EDATE({base}, {120 * amount})", None
            if unit in ("hour", "minute", "second"):
                div = {"hour": 24, "minute": 1440, "second": 86400}[unit]
                return f"({base} + {amount} / {div})", None
            raise _Unsupported(f"DateAdd TimeUnit {da.get('TimeUnit')!r}")
        if "DateSpan" in expr:
            ds = expr["DateSpan"]
            base, _ = self._date_expr(ds.get("Expression") or {}, ctx)
            unit = _TIME_UNITS.get(ds.get("TimeUnit"))
            day = f"DATE(YEAR({base}), MONTH({base}), DAY({base}))"
            if unit == "day":
                return day, f"({day} + 1)"
            if unit == "week":
                start = f"({day} - WEEKDAY({base}, 1) + 1)"
                ctx.notes.append("week span assumes a Sunday week start")
                return start, f"({start} + 7)"
            if unit == "month":
                start = f"DATE(YEAR({base}), MONTH({base}), 1)"
                return start, f"EDATE({start}, 1)"
            if unit == "year":
                start = f"DATE(YEAR({base}), 1, 1)"
                return start, f"DATE(YEAR({base}) + 1, 1, 1)"
            raise _Unsupported(f"DateSpan TimeUnit {ds.get('TimeUnit')!r}")
        if "Literal" in expr:
            return self._literal_dax(expr["Literal"].get("Value"), ctx), None
        raise _Unsupported(f"date expression {next(iter(expr), '?')!r}")

    def _compile_condition(
        self,
        cond: Dict[str, Any],
        ctx: _CompileContext,
        target: Tuple[Optional[str], Optional[str]],
    ) -> str:
        if "In" in cond:
            return self._compile_in(cond["In"], ctx)
        if "Not" in cond:
            inner = (cond["Not"] or {}).get("Expression") or {}
            before = len(ctx.kinds)
            compiled = self._compile_condition(inner, ctx, target)
            if "In" in inner and ctx.kinds[before : before + 1] == ["In"]:
                ctx.kinds[before] = "NotIn"
            else:
                ctx.kinds.append("Not")
            return f"NOT({compiled})"
        if "And" in cond or "Or" in cond:
            key = "And" if "And" in cond else "Or"
            node = cond[key] or {}
            left = self._compile_condition(node.get("Left") or {}, ctx, target)
            right = self._compile_condition(node.get("Right") or {}, ctx, target)
            ctx.kinds.append(key)
            op = "&&" if key == "And" else "||"
            return f"({left} {op} {right})"
        if "Comparison" in cond:
            return self._compile_comparison(cond["Comparison"], ctx)
        if "Between" in cond:
            return self._compile_between(cond["Between"], ctx, target)
        for key, template in (
            ("Contains", "CONTAINSSTRING({l}, {r})"),
            ("StartsWith", "LEFT({l}, LEN({r})) = {r}"),
            ("EndsWith", "RIGHT({l}, LEN({r})) = {r}"),
        ):
            if key in cond:
                node = cond[key] or {}
                left, _ = self._compile_operand(node.get("Left") or {}, ctx)
                right, _ = self._compile_operand(node.get("Right") or {}, ctx)
                ctx.kinds.append(key)
                return template.format(l=left, r=right)
        if "IsBlank" in cond or "IsNotBlank" in cond:
            if not target[0] or not target[1]:
                raise _Unsupported("IsBlank without a target column")
            ctx.columns.append((target[0], target[1]))
            ref = _column_ref(target[0], target[1])
            if "IsBlank" in cond:
                ctx.kinds.append("IsBlank")
                ctx.has_null = True
                return f"ISBLANK({ref})"
            ctx.kinds.append("IsNotBlank")
            return f"NOT(ISBLANK({ref}))"
        raise _Unsupported(f"condition kind {next(iter(cond), '?')!r}")

    def _compile_in(self, node: Dict[str, Any], ctx: _CompileContext) -> str:
        exprs = node.get("Expressions") or []
        if not exprs:
            raise _Unsupported("In without Expressions")
        compiled = [self._compile_operand(e, ctx) for e in exprs]
        refs = [c[0] for c in compiled]
        cols = [c[1] for c in compiled]
        if "Table" in node:
            source = ((node.get("Table") or {}).get("SourceRef") or {}).get("Source", "")
            query = ctx.subqueries.get(source)
            if query is None or len(refs) != 1:
                raise _Unsupported("In.Table subquery that is not a TopN")
            ctx.topn = self._compile_topn(query, refs[0], ctx)
            ctx.kinds.append("TopN")
            return f"{refs[0]} IN {self._topn_table(ctx.topn)}"
        rows = node.get("Values") or []
        if not rows:
            raise _Unsupported("In with an empty Values list (Desktop rejects it)")
        ctx.kinds.append("In")
        if len(refs) == 1:
            literals: List[str] = []
            has_null = False
            for row in rows:
                for item in row or []:
                    if not isinstance(item, dict) or "Literal" not in item:
                        raise _Unsupported("In value that is not a literal")
                    raw = item["Literal"].get("Value")
                    typed = self._clean_literal_value(raw)
                    if self._is_null_value(raw) or typed.value is None:
                        has_null = True
                        continue
                    literals.append(self._literal_dax(raw, None, cols[0]))
                    ctx.values.append(typed.value)
            ctx.literals.extend(literals)
            parts = []
            if has_null:
                ctx.has_null = True
                parts.append(f"ISBLANK({refs[0]})")
            if literals:
                parts.append(f"{refs[0]} IN {{{', '.join(literals)}}}")
            return parts[0] if len(parts) == 1 else "(" + " || ".join(parts) + ")"
        tuples: List[str] = []
        for row in rows:
            items = [
                self._literal_dax(
                    i["Literal"].get("Value"), ctx, cols[k] if k < len(cols) else None
                )
                for k, i in enumerate(row or [])
                if isinstance(i, dict) and "Literal" in i
            ]
            if len(items) != len(refs):
                raise _Unsupported("In tuple arity does not match its column count")
            first = self._clean_literal_value(row[0]["Literal"].get("Value"))
            ctx.values.append(first.value)
            tuples.append("(" + ", ".join(items) + ")")
        return f"({', '.join(refs)}) IN {{{', '.join(tuples)}}}"

    def _compile_topn(self, query: Dict[str, Any], column_ref: str, outer: _CompileContext) -> Dict:
        sub = _CompileContext()
        self._load_from(query.get("From") or [], sub)
        n = query.get("Top")
        order_by = query.get("OrderBy") or []
        if not isinstance(n, int) or n <= 0 or not order_by:
            raise _Unsupported("TopN subquery without Top/OrderBy")
        first = order_by[0]
        order_expr, _ = self._compile_operand(first.get("Expression") or {}, sub)
        # Direction: 1 = Ascending, 2 = Descending (QuerySortDirection).
        descending = first.get("Direction", 2) != 1
        outer.notes.append(
            "TopN evaluated in the visual's other filters (engine TOPN, ties included)"
        )
        return {
            "column_ref": column_ref,
            "n": n,
            "order_expression": order_expr,
            "descending": descending,
        }

    def _compile_comparison(self, node: Dict[str, Any], ctx: _CompileContext) -> str:
        kind = node.get("ComparisonKind", 0)
        op = _COMPARISON_OPERATORS.get(kind)
        if op is None and isinstance(kind, str) and kind.isdigit():
            op = _COMPARISON_OPERATORS.get(int(kind))
        if op is None:
            raise _Unsupported(f"ComparisonKind {kind!r}")
        left, col = self._compile_operand(node.get("Left") or {}, ctx)
        right_node = node.get("Right") or {}
        ctx.kinds.append("Comparison")
        if "Literal" in right_node and self._is_null_value(right_node["Literal"].get("Value")):
            ctx.has_null = True
            if op == "=":
                return f"ISBLANK({left})"
            if op == "<>":
                return f"NOT(ISBLANK({left}))"
            raise _Unsupported(f"comparison {op} against null")
        if "DateSpan" in right_node:
            start, end = self._date_expr(right_node, ctx)
            return {
                "=": f"({left} >= {start} && {left} < {end})",
                ">": f"{left} >= {end}",
                ">=": f"{left} >= {start}",
                "<": f"{left} < {start}",
                "<=": f"{left} < {end}",
                "<>": f"({left} < {start} || {left} >= {end})",
            }[op]
        if "Literal" in right_node:
            right = self._literal_dax(right_node["Literal"].get("Value"), ctx, col)
        else:
            right, _ = self._compile_operand(right_node, ctx)
        return f"{left} {op} {right}"

    def _compile_between(
        self,
        node: Dict[str, Any],
        ctx: _CompileContext,
        target: Tuple[Optional[str], Optional[str]],
    ) -> str:
        col = None
        if node.get("Expression"):
            ref, col = self._compile_operand(node["Expression"], ctx)
        elif target[0] and target[1]:
            ctx.columns.append((target[0], target[1]))
            col = (target[0], target[1])
            ref = _column_ref(target[0], target[1])
        else:
            raise _Unsupported("Between without an expression")
        lower = node.get("LowerBound") or node.get("Lower") or {}
        upper = node.get("UpperBound") or node.get("Upper") or {}
        ctx.kinds.append("Between")
        lo_start, _lo_end = self._bound(lower, ctx, col)
        hi_start, hi_end = self._bound(upper, ctx, col)
        if hi_end is not None:
            return f"({ref} >= {lo_start} && {ref} < {hi_end})"
        return f"({ref} >= {lo_start} && {ref} <= {hi_start})"

    def _bound(self, node: Dict[str, Any], ctx: _CompileContext, col) -> Tuple[str, Optional[str]]:
        if not isinstance(node, dict) or not node:
            raise _Unsupported("Between bound missing")
        if "Literal" in node:
            return self._literal_dax(node["Literal"].get("Value"), ctx, col), None
        return self._date_expr(node, ctx)

    # ------------------------------------------------------------------
    # Slicer selections
    # ------------------------------------------------------------------

    def convert_slicer_selection(self, slicer_info: Dict[str, Any]) -> Optional[FilterExpression]:
        """
        Convert slicer selection state to a DAX FilterExpression, or None if no filter needed.

        slicer_info keys: entity, property, selected_values, is_inverted_selection,
        selection_mode, extra_columns (composite TREATAS), composite_values.
        ``is_inverted_selection`` True means the values are an EXCLUDE list.
        """
        try:
            entity = slicer_info.get("entity", "")
            column = slicer_info.get("property", "")
            selected_values = slicer_info.get("selected_values", [])
            is_inverted = slicer_info.get("is_inverted_selection", False)
            extra_columns = slicer_info.get("extra_columns", [])
            composite_values = slicer_info.get("composite_values", None)

            if not entity or not column:
                return None

            # Inverted with nothing excluded = "Select all": no filter at all.
            if is_inverted and not selected_values and not composite_values:
                return None

            col_ref = _column_ref(entity, column)

            # --- Composite multi-column TREATAS path (sf Row Drill, sf Slicer 1, etc.) ---
            if extra_columns and composite_values:
                all_col_refs = [col_ref] + list(extra_columns)
                cleaned_tuples = []
                raw_first_col_values = []
                for row in composite_values:
                    cleaned_row = []
                    for val in row:
                        if isinstance(val, TypedValue):
                            cleaned_row.append(val)
                        else:
                            cleaned_row.append(
                                self._clean_literal_value(val)
                                if isinstance(val, str)
                                else TypedValue(val, "unknown")
                            )
                    cleaned_tuples.append(cleaned_row)
                    if cleaned_row:
                        raw_first_col_values.append(
                            cleaned_row[0].value
                            if isinstance(cleaned_row[0], TypedValue)
                            else cleaned_row[0]
                        )

                if not cleaned_tuples:
                    return None

                rows = [
                    "(" + ", ".join(self._format_single_value_for_dax(v) for v in r) + ")"
                    for r in cleaned_tuples
                ]
                values_set = "{" + ", ".join(rows) + "}"
                cols_text = ", ".join(all_col_refs)
                if is_inverted:
                    predicate = f"NOT(({cols_text}) IN {values_set})"
                    filter_table = (
                        f"FILTER(KEEPFILTERS(SUMMARIZE({quote_table(entity)}, {cols_text})), "
                        f"{predicate})"
                    )
                    dax = filter_table
                else:
                    filter_table = f"TREATAS({values_set}, {cols_text})"
                    dax = filter_table
                filter_classification = classify_filter(entity, column)
                return FilterExpression(
                    dax=dax,
                    source="slicer",
                    table=entity,
                    column=column,
                    condition_type="Composite",
                    values=raw_first_col_values,
                    original=slicer_info,
                    is_field_parameter=True,
                    classification=filter_classification,
                    has_null_values=False,
                    composite_columns=all_col_refs,
                    composite_tuples=cleaned_tuples,
                    polarity="exclude" if is_inverted else "include",
                    filter_table_dax=filter_table,
                    columns=all_col_refs,
                )

            # --- Standard single-column path ---
            if not selected_values:
                return None

            typed_values = []
            raw_values = []
            null_values = []

            for val in selected_values:
                if isinstance(val, TypedValue):
                    typed_val = val
                else:
                    typed_val = (
                        self._clean_literal_value(val)
                        if isinstance(val, str)
                        else TypedValue(val, "unknown")
                    )
                if self._is_null_value(typed_val):
                    null_values.append(typed_val)
                else:
                    known = self.get_column_type(entity, column)
                    if known and typed_val.value_type == "unknown":
                        typed_val = TypedValue(typed_val.value, known)
                    typed_values.append(typed_val)
                    raw_values.append(typed_val.value if isinstance(typed_val, TypedValue) else val)

            has_null = len(null_values) > 0
            literals = [self._format_single_value_for_dax(v) for v in typed_values]

            dax_parts = []
            if is_inverted:
                if null_values:
                    dax_parts.append(f"NOT(ISBLANK({col_ref}))")
                if literals:
                    dax_parts.append(f"NOT({col_ref} IN {{{', '.join(literals)}}})")
                if not dax_parts:
                    return None
                dax = f"({' && '.join(dax_parts)})" if len(dax_parts) > 1 else dax_parts[0]
                condition_type = "Not"
                filter_table = f"FILTER(KEEPFILTERS(VALUES({col_ref})), {dax})"
            else:
                if null_values:
                    dax_parts.append(f"ISBLANK({col_ref})")
                if literals:
                    dax_parts.append(f"{col_ref} IN {{{', '.join(literals)}}}")
                if not dax_parts:
                    return None
                dax = f"({' || '.join(dax_parts)})" if len(dax_parts) > 1 else dax_parts[0]
                condition_type = "In"
                values_set = ", ".join(literals + (["BLANK()"] if has_null else []))
                filter_table = f"TREATAS({{{values_set}}}, {col_ref})"

            return FilterExpression(
                dax=dax,
                source="slicer",
                table=entity,
                column=column,
                condition_type=condition_type,
                values=raw_values,
                original=slicer_info,
                is_field_parameter=is_field_parameter_table(entity),
                classification=classify_filter(entity, column),
                has_null_values=has_null,
                polarity="exclude" if is_inverted else "include",
                filter_table_dax=filter_table,
                literals=literals if not is_inverted else None,
                columns=[col_ref],
            )

        except Exception as e:
            self.logger.warning(f"Error converting slicer selection: {e}")
            return None

    def manual_filter(self, dax: str, source: str = "manual") -> FilterExpression:
        """A caller-supplied DAX predicate (or table expression) as a filter.

        The builder wraps manual filters as ``KEEPFILTERS(<dax>)`` CALCULATETABLE
        arguments around the visual's core query (F-06); the text is never
        re-parsed into a TREATAS.
        """
        from core.dax.dax_reference_parser import scan_references

        table, column = "", ""
        try:
            refs = [r for r in scan_references(dax) if r.kind == "qualified"]
            if refs:
                table, column = refs[0].table, refs[0].name
        except Exception:  # noqa: BLE001 — naming is cosmetic
            pass
        return FilterExpression(
            dax=dax,
            source=source,
            table=table,
            column=column,
            condition_type="Manual",
            values=[],
            classification=FilterClassification.DATA,
            filter_table_dax=f"KEEPFILTERS({dax})",
        )

    def equality_filter(
        self, table: str, column: str, value: Any, source: str = "test"
    ) -> FilterExpression:
        """``'T'[C] = <typed literal>`` for one value (filter_permutation / matrix, F-37).

        The literal follows the column's model data type when it is known, so an
        Int64 column gets ``2024`` rather than ``"2024"``.
        """
        known = self.get_column_type(table, column)
        if value is None:
            literal = "BLANK()"
        elif known:
            literal = self._format_single_value_for_dax(TypedValue(value, known))
        else:
            literal = self._format_single_value_for_dax(value)
        ref = _column_ref(table, column)
        predicate = f"ISBLANK({ref})" if value is None else f"{ref} = {literal}"
        return FilterExpression(
            dax=predicate,
            source=source,
            table=table,
            column=column,
            condition_type="In",
            values=[value],
            classification=classify_filter(table, column),
            filter_table_dax=f"TREATAS({{{literal}}}, {ref})",
            literals=[literal],
            columns=[ref],
        )

    # ------------------------------------------------------------------
    # Legacy helpers (kept for callers/tests of the old API)
    # ------------------------------------------------------------------

    def _extract_target(self, filter_def: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """Extract table and column (or measure) from a filter definition.

        PBIR writes ``field`` (Column / Measure / Aggregation / HierarchyLevel);
        older layouts use ``expression`` or ``target``; the last resort is the
        first column of the first Where condition (F-04c).
        """
        try:
            target = filter_def.get("target", {})
            if target:
                table = target.get("table", "")
                column = target.get("column", target.get("measure", ""))
                if table and column:
                    return table, column

            for key in ("field", "expression"):
                node = filter_def.get(key)
                if isinstance(node, dict):
                    found = self._node_target(node, {})
                    if found[0] and found[1]:
                        return found

            filter_obj = filter_def.get("filter") or {}
            ctx = _CompileContext()
            self._load_from(filter_obj.get("From") or [], ctx)
            for clause in filter_obj.get("Where") or []:
                found = self._first_target(clause.get("Condition") or {}, ctx.aliases)
                if found[0] and found[1]:
                    return found
            return None, None

        except Exception as e:
            self.logger.debug(f"Error extracting target: {e}")
            return None, None

    def _node_target(
        self, node: Dict[str, Any], aliases: Dict[str, str]
    ) -> Tuple[Optional[str], Optional[str]]:
        for kind in ("Column", "Measure"):
            if kind in node:
                ref = node[kind]
                src = (ref.get("Expression") or {}).get("SourceRef", {})
                table = src.get("Entity") or aliases.get(src.get("Source", ""), src.get("Source"))
                return table, ref.get("Property")
        if "Aggregation" in node:
            return self._node_target(node["Aggregation"].get("Expression") or {}, aliases)
        if "HierarchyLevel" in node:
            hl = node["HierarchyLevel"]
            hier = (hl.get("Expression") or {}).get("Hierarchy") or {}
            src = (hier.get("Expression") or {}).get("SourceRef", {})
            table = src.get("Entity") or aliases.get(src.get("Source", ""), src.get("Source"))
            return table, hl.get("Level")
        return None, None

    def _first_target(
        self, cond: Dict[str, Any], aliases: Dict[str, str]
    ) -> Tuple[Optional[str], Optional[str]]:
        if not isinstance(cond, dict):
            return None, None
        if "In" in cond:
            exprs = (cond["In"] or {}).get("Expressions") or []
            return self._node_target(exprs[0], aliases) if exprs else (None, None)
        if "Not" in cond:
            return self._first_target((cond["Not"] or {}).get("Expression") or {}, aliases)
        for key in ("And", "Or"):
            if key in cond:
                return self._first_target((cond[key] or {}).get("Left") or {}, aliases)
        for key in ("Comparison", "Contains", "StartsWith", "EndsWith"):
            if key in cond:
                return self._node_target((cond[key] or {}).get("Left") or {}, aliases)
        if "Between" in cond:
            return self._node_target((cond["Between"] or {}).get("Expression") or {}, aliases)
        return None, None

    def _clean_literal_value(self, value: Any) -> TypedValue:
        """
        Clean Power BI literal values from PBIP JSON format.

        Power BI stores literals with type suffixes:
        - 'text'L for strings
        - 123L for integers
        - 123.45D for decimals
        - 123.45M for currency
        - trueL or falseL for booleans

        Returns a TypedValue that preserves the original type information.
        """
        if not isinstance(value, str):
            # Already a Python type - infer type
            if value is None:
                return TypedValue(None, "unknown")
            if isinstance(value, bool):
                return TypedValue(value, "boolean")
            elif isinstance(value, int):
                return TypedValue(value, "integer")
            elif isinstance(value, float):
                return TypedValue(value, "decimal")
            return TypedValue(value, "unknown")

        if value.strip().lower() == "null":
            return TypedValue(None, "unknown")

        # Handle datetime literals: datetime'2025-05-05T00:00:00' (time kept when present)
        dt_match = re.match(r"datetime'(\d{4})-(\d{2})-(\d{2})(?:T([\d:.]+))?'", value)
        if dt_match:
            date_str = f"{dt_match.group(1)}-{dt_match.group(2)}-{dt_match.group(3)}"
            time_part = dt_match.group(4) or ""
            if time_part and time_part.split(".")[0] not in ("00:00:00", "00:00"):
                date_str = f"{date_str}T{time_part}"
            return TypedValue(date_str, "date")

        # Handle quoted literals with L suffix: 'value'L -> value (STRING)
        if value.endswith("'L") and value.startswith("'"):
            inner = value[1:-2].replace("''", "'")  # unescape embedded single quotes
            return TypedValue(inner, "string")  # Definitely a string!
        elif value.endswith('"L') and value.startswith('"'):
            return TypedValue(value[1:-2], "string")  # Definitely a string!

        # Handle quoted values without L suffix (still strings)
        elif value.startswith("'") and value.endswith("'") and len(value) >= 2:
            inner = value[1:-1].replace("''", "'")  # unescape embedded single quotes
            return TypedValue(inner, "string")
        elif value.startswith('"') and value.endswith('"') and len(value) >= 2:
            return TypedValue(value[1:-1], "string")

        # Handle unquoted boolean with L suffix: trueL, falseL
        elif value.lower() in ("truel", "falsel"):
            return TypedValue(value.lower() == "truel", "boolean")

        # Handle numeric with L suffix: 123L -> 123 (INTEGER)
        elif value.endswith("L") and value[:-1].lstrip("-").isdigit():
            return TypedValue(int(value[:-1]), "integer")

        # Handle decimal with D suffix: 123.45D -> 123.45 (DECIMAL)
        elif value.endswith("D"):
            try:
                return TypedValue(float(value[:-1]), "decimal")
            except ValueError:
                pass

        # Handle currency with M suffix: 123.45M -> 123.45 (DECIMAL)
        elif value.endswith("M"):
            try:
                return TypedValue(float(value[:-1]), "decimal")
            except ValueError:
                pass

        # Check if the value is a boolean string
        if value.lower() in ("true", "false"):
            return TypedValue(value.lower() == "true", "boolean")

        # Fallback - treat as unknown (will be auto-detected in format function)
        return TypedValue(value, "unknown")

    def _is_null_value(self, value: Any) -> bool:
        """Check if a value represents null/blank."""
        if value is None:
            return True
        if isinstance(value, str):
            val_lower = value.lower().strip()
            return val_lower in ("null", "'null'l", '"null"l', "blank", "'blank'l")
        if isinstance(value, TypedValue):
            return self._is_null_value(value.value)
        return False

    def _format_values_for_dax(self, values: List[Any]) -> str:
        """Format a list of values for DAX IN clause."""
        formatted = []
        for val in values:
            formatted.append(self._format_single_value_for_dax(val))
        return ", ".join(formatted)

    @staticmethod
    def _date_literal(str_val: str) -> str:
        date_part, _, time_part = str_val.partition("T")
        out = f"DATE({date_part[:4]}, {int(date_part[5:7])}, {int(date_part[8:10])})"
        if time_part:
            hms = (time_part.split(".")[0].split(":") + ["0", "0", "0"])[:3]
            out += f" + TIME({int(hms[0])}, {int(hms[1])}, {int(hms[2])})"
        return out

    def _format_single_value_for_dax(self, value: Any) -> str:
        """
        Format a single value for DAX.

        Handles TypedValue objects to preserve original type information,
        preventing type mismatches like string "0" being formatted as integer 0.
        """
        # Handle TypedValue objects - respect the original type!
        if isinstance(value, TypedValue):
            typed_val = value
            raw_value = typed_val.value

            if raw_value is None:
                return "BLANK()"

            # Use the known type
            if typed_val.value_type == "string":
                # Always format as string, even if it looks numeric!
                return dax_string(str(raw_value))
            elif typed_val.value_type == "boolean":
                if isinstance(raw_value, bool):
                    return "TRUE" if raw_value else "FALSE"
                return "TRUE" if str(raw_value).lower() == "true" else "FALSE"
            elif typed_val.value_type == "integer":
                try:
                    return str(int(float(raw_value)))
                except (TypeError, ValueError):
                    return dax_string(str(raw_value))
            elif typed_val.value_type == "decimal":
                try:
                    num = float(raw_value)
                except (TypeError, ValueError):
                    return dax_string(str(raw_value))
                return str(int(num)) if num.is_integer() and abs(num) < 1e15 else repr(num)
            elif typed_val.value_type == "date":
                str_val = str(raw_value)
                if len(str_val) >= 10 and str_val[4] == "-" and str_val[7] == "-":
                    return self._date_literal(str_val)
                return dax_string(str_val)
            else:
                # 'unknown' type - fall through to auto-detection below
                value = raw_value

        # Handle raw values (backward compatibility and unknown types)
        if value is None:
            return "BLANK()"

        # Check if it's a boolean (Python bool or string representation)
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return "TRUE" if value.lower() == "true" else "FALSE"

        # Check if it's already a numeric Python type
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, float):
            return str(value)

        # Check if it's a date (ISO format)
        if isinstance(value, str) and len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
            try:
                return self._date_literal(value)
            except ValueError:
                pass

        # For string values that might look numeric:
        # Only parse as number if it's clearly numeric (no leading zeros except "0")
        if isinstance(value, str):
            stripped = value.strip()
            # Check if it's a clean numeric value (not a string that happens to contain digits)
            is_clean_integer = stripped.lstrip("-").isdigit() and (
                len(stripped.lstrip("-")) == 1 or not stripped.lstrip("-").startswith("0")
            )
            is_clean_decimal = False
            if not is_clean_integer and "." in stripped:
                parts = stripped.lstrip("-").split(".")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    is_clean_decimal = True

            if is_clean_integer:
                return stripped
            elif is_clean_decimal:
                return stripped

        # Default: treat as string value - escape quotes
        return dax_string(str(value))

    def build_calculate_expression(self, measure_expr: str, filters: List[FilterExpression]) -> str:
        """
        Build a CALCULATE expression wrapping measure_expr with the DATA filters applied.
        """
        filter_dax, _skipped = calculate_filter_args(filters)
        if not filter_dax:
            return measure_expr

        filters_str = ",\n    ".join(filter_dax)
        return f"CALCULATE(\n    {measure_expr},\n    {filters_str}\n)"

    def build_evaluate_query(
        self, measure_name: str, filters: List[FilterExpression], result_name: str = "Result"
    ) -> str:
        """
        Build a complete EVALUATE ROW query wrapping measure_name with filters applied.
        """
        calculate_expr = self.build_calculate_expression(measure_name, filters)
        return f"EVALUATE\nROW({dax_string(result_name)}, {calculate_expr})"
