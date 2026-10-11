"""
TMDL Domain Models

Typed dataclasses representing all TMDL object types.
These provide the canonical schema for parsed TMDL data.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TmdlAnnotation:
    """An annotation (key-value metadata) on any TMDL object."""

    name: str
    value: str


@dataclass
class TmdlColumn:
    """A column in a table."""

    name: str
    is_calculated: bool = False
    data_type: Optional[str] = None
    source_column: Optional[str] = None
    expression: Optional[str] = None
    description: Optional[str] = None
    display_folder: Optional[str] = None
    format_string: Optional[str] = None
    data_category: Optional[str] = None
    summarize_by: Optional[str] = None
    sort_by_column: Optional[str] = None
    is_key: bool = False
    is_hidden: bool = False
    lineage_tag: Optional[str] = None
    annotations: List[TmdlAnnotation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    # relatedColumnDetails / groupByColumn targets (field parameters)
    group_by_columns: List[str] = field(default_factory=list)
    # extendedProperty <Name> = <json text> (e.g. ParameterMetadata)
    extended_properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class TmdlMeasure:
    """A measure with a DAX expression."""

    name: str
    expression: Optional[str] = None
    format_string: Optional[str] = None
    format_string_definition: Optional[str] = None
    display_folder: Optional[str] = None
    description: Optional[str] = None
    is_hidden: bool = False
    data_category: Optional[str] = None
    lineage_tag: Optional[str] = None
    annotations: List[TmdlAnnotation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    detail_rows_definition: Optional[str] = None
    # kpi block: {"target_expression": ..., "status_expression": ..., ...}
    kpi: Dict[str, Optional[str]] = field(default_factory=dict)


@dataclass
class TmdlHierarchyLevel:
    """A level within a hierarchy."""

    name: str
    column: Optional[str] = None
    ordinal: Optional[int] = None
    lineage_tag: Optional[str] = None


@dataclass
class TmdlHierarchy:
    """A hierarchy on a table."""

    name: str
    levels: List[TmdlHierarchyLevel] = field(default_factory=list)
    is_hidden: bool = False
    lineage_tag: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TmdlCalendarColumnGroup:
    """A ``calendarColumnGroup`` inside a CL 1701 ``calendar`` block.

    The documented child properties are ``primaryColumn`` and
    ``associatedColumn``; anything else PBI emits is preserved verbatim in
    ``properties`` so a preview-format change can't silently lose data.
    """

    name: str
    primary_column: Optional[str] = None
    associated_column: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TmdlCalendar:
    """A ``calendar`` object on a table (compatibility level 1701+).

    Backs the calendar-aware time-intelligence functions (TOTALWTD,
    STARTOFWEEK, ...). Unrecognized child properties go to ``properties``.
    """

    name: str
    column_groups: List[TmdlCalendarColumnGroup] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TmdlPartition:
    """A partition (data source) for a table."""

    name: str
    type: Optional[str] = None  # 'm', 'entity', 'calculated', etc.
    mode: Optional[str] = None  # 'import', 'directQuery', 'dual'
    source: Optional[str] = None  # M expression or DAX expression
    query_group: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TmdlCalculationItem:
    """A calculation item in a calculation group."""

    name: str
    expression: Optional[str] = None
    ordinal: Optional[int] = None
    format_string_definition: Optional[str] = None
    description: Optional[str] = None
    lineage_tag: Optional[str] = None
    annotations: List[TmdlAnnotation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TmdlTable:
    """A table in the model."""

    name: str
    columns: List[TmdlColumn] = field(default_factory=list)
    measures: List[TmdlMeasure] = field(default_factory=list)
    hierarchies: List[TmdlHierarchy] = field(default_factory=list)
    partitions: List[TmdlPartition] = field(default_factory=list)
    calculation_items: List[TmdlCalculationItem] = field(default_factory=list)
    calendars: List[TmdlCalendar] = field(default_factory=list)
    is_calculation_group: bool = False
    is_hidden: bool = False
    description: Optional[str] = None
    lineage_tag: Optional[str] = None
    annotations: List[TmdlAnnotation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    _source_file: Optional[str] = None


@dataclass
class TmdlRelationship:
    """A relationship between two tables."""

    id: str
    from_column: Optional[str] = None
    from_cardinality: Optional[str] = None
    to_column: Optional[str] = None
    to_cardinality: Optional[str] = None
    is_active: bool = True
    cross_filtering_behavior: Optional[str] = None
    security_filtering_behavior: Optional[str] = None
    rely_on_referential_integrity: bool = False
    annotations: List[TmdlAnnotation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)

    @property
    def from_table(self) -> str:
        """Extract table name from from_column reference."""
        return _extract_table_from_ref(self.from_column)

    @property
    def from_column_name(self) -> str:
        """Extract column name from from_column reference."""
        return _extract_column_from_ref(self.from_column)

    @property
    def to_table(self) -> str:
        """Extract table name from to_column reference."""
        return _extract_table_from_ref(self.to_column)

    @property
    def to_column_name(self) -> str:
        """Extract column name from to_column reference."""
        return _extract_column_from_ref(self.to_column)


@dataclass
class TmdlExpression:
    """A shared M expression or parameter."""

    name: str
    expression: Optional[str] = None
    kind: str = "m"
    lineage_tag: Optional[str] = None
    query_group: Optional[str] = None
    annotations: List[TmdlAnnotation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TmdlDatasource:
    """A data source definition."""

    name: str
    type: Optional[str] = None  # 'structured', 'provider', etc.
    connection_string: Optional[str] = None
    provider: Optional[str] = None
    annotations: List[TmdlAnnotation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TmdlColumnPermission:
    """An object-level-security entry (``columnPermission``) of a table permission."""

    column: str
    metadata_permission: Optional[str] = None


@dataclass
class TmdlTablePermission:
    """A table permission within a role (RLS filter + OLS column permissions)."""

    table: str
    filter_expression: Optional[str] = None
    metadata_permission: Optional[str] = None
    column_permissions: List[TmdlColumnPermission] = field(default_factory=list)


@dataclass
class TmdlRole:
    """A security role with RLS definitions."""

    name: str
    model_permission: Optional[str] = None  # 'read', 'administrator', etc.
    table_permissions: List[TmdlTablePermission] = field(default_factory=list)
    description: Optional[str] = None
    annotations: List[TmdlAnnotation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    _source_file: Optional[str] = None


@dataclass
class TmdlPerspectiveItem:
    """An item (table/column/measure) visible in a perspective."""

    object_type: str  # 'table', 'column', 'measure', 'hierarchy'
    table: str
    name: Optional[str] = None  # None for table-level visibility


@dataclass
class TmdlPerspective:
    """A perspective definition."""

    name: str
    items: List[TmdlPerspectiveItem] = field(default_factory=list)
    description: Optional[str] = None
    annotations: List[TmdlAnnotation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    _source_file: Optional[str] = None


@dataclass
class TmdlTranslation:
    """A translation for a model object."""

    object_type: str  # 'table', 'column', 'measure', 'hierarchy'
    table: str
    name: Optional[str] = None
    translated_caption: Optional[str] = None
    translated_description: Optional[str] = None
    translated_display_folder: Optional[str] = None


@dataclass
class TmdlCulture:
    """A culture/language definition with translations."""

    name: str  # e.g., 'en-US', 'nl-BE'
    translations: List[TmdlTranslation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    _source_file: Optional[str] = None
    # inline ``linguisticMetadata =`` JSON block (TMDL keeps it in the culture file)
    linguistic_metadata: Optional[str] = None
    linguistic_content_type: Optional[str] = None


@dataclass
class TmdlFunction:
    """A DAX user-defined function (``functions.tmdl``)."""

    name: str
    expression: Optional[str] = None
    description: Optional[str] = None
    is_hidden: bool = False
    lineage_tag: Optional[str] = None
    annotations: List[TmdlAnnotation] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TmdlDatabase:
    """Database-level metadata."""

    name: Optional[str] = None
    compatibility_level: Optional[int] = None
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TmdlModelProperties:
    """Model-level properties."""

    name: Optional[str] = None
    culture: Optional[str] = None
    default_power_bi_data_source_version: Optional[str] = None
    discourage_implicit_measures: Optional[bool] = None
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TmdlModel:
    """Complete parsed TMDL model."""

    database: Optional[TmdlDatabase] = None
    model: Optional[TmdlModelProperties] = None
    tables: List[TmdlTable] = field(default_factory=list)
    relationships: List[TmdlRelationship] = field(default_factory=list)
    roles: List[TmdlRole] = field(default_factory=list)
    perspectives: List[TmdlPerspective] = field(default_factory=list)
    expressions: List[TmdlExpression] = field(default_factory=list)
    datasources: List[TmdlDatasource] = field(default_factory=list)
    cultures: List[TmdlCulture] = field(default_factory=list)
    functions: List[TmdlFunction] = field(default_factory=list)
    # structural problems found while reading (see core.tmdl.tmdl_tree)
    parse_issues: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for backward compatibility with existing consumers."""
        return {
            "database": _database_to_dict(self.database),
            "model": _model_props_to_dict(self.model),
            "tables": [_table_to_dict(t) for t in self.tables],
            "relationships": [_relationship_to_dict(r) for r in self.relationships],
            "roles": [_role_to_dict(r) for r in self.roles],
            "perspectives": [_perspective_to_dict(p) for p in self.perspectives],
            "expressions": [_expression_to_dict(e) for e in self.expressions],
            "datasources": [_datasource_to_dict(d) for d in self.datasources],
            "cultures": [_culture_to_dict(c) for c in self.cultures],
            "functions": [_function_to_dict(f) for f in self.functions],
            "parse_issues": list(self.parse_issues),
        }


# --- Helper functions for column reference parsing ---


def _extract_table_from_ref(ref: Optional[str]) -> str:
    """Table part of a TMDL ``Table.Column`` reference (quoted or bare, ``''`` unescaped).

    Review G-08: relationship ends such as ``f_Aging_Customer.'Company Code'``
    (bare table) used to return "" and the relationship key looked unused.
    """
    from core.tmdl.tmdl_tree import split_object_ref

    return split_object_ref(ref)[0]


def _extract_column_from_ref(ref: Optional[str]) -> str:
    """Column part of a TMDL ``Table.Column`` reference."""
    if not ref:
        return ""
    from core.tmdl.tmdl_tree import split_object_ref

    table, column = split_object_ref(ref)
    return column if (table or column) else ref


# --- to_dict converters for backward compatibility ---


def _annotation_to_dict(a: TmdlAnnotation) -> Dict[str, str]:
    return {"name": a.name, "value": a.value}


def _column_to_dict(c: TmdlColumn) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "name": c.name,
        "is_calculated": c.is_calculated,
        "data_type": c.data_type,
        "source_column": c.source_column,
        "expression": c.expression,
        "description": c.description,
        "display_folder": c.display_folder,
        "format_string": c.format_string,
        "data_category": c.data_category,
        "summarize_by": c.summarize_by,
        "sort_by_column": c.sort_by_column,
        "is_key": c.is_key,
        "is_hidden": c.is_hidden,
        "annotations": [_annotation_to_dict(a) for a in c.annotations],
        "properties": c.properties,
    }
    if c.lineage_tag:
        d["lineage_tag"] = c.lineage_tag
    if c.group_by_columns:
        d["group_by_columns"] = list(c.group_by_columns)
    if c.extended_properties:
        d["extended_properties"] = dict(c.extended_properties)
    return d


def _measure_to_dict(m: TmdlMeasure) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "name": m.name,
        "expression": m.expression,
        "format_string": m.format_string,
        "display_folder": m.display_folder,
        "description": m.description,
        "is_hidden": m.is_hidden,
        "data_category": m.data_category,
        "annotations": [_annotation_to_dict(a) for a in m.annotations],
        "properties": m.properties,
    }
    if m.format_string_definition:
        d["format_string_definition"] = m.format_string_definition
    if m.lineage_tag:
        d["lineage_tag"] = m.lineage_tag
    if m.detail_rows_definition:
        d["detail_rows_definition"] = m.detail_rows_definition
    if m.kpi:
        d["kpi"] = dict(m.kpi)
    return d


def _hierarchy_to_dict(h: TmdlHierarchy) -> Dict[str, Any]:
    return {
        "name": h.name,
        "levels": [
            {"name": lv.name, "column": lv.column, "ordinal": lv.ordinal} for lv in h.levels
        ],
        "is_hidden": h.is_hidden,
        "properties": h.properties,
    }


def _partition_to_dict(p: TmdlPartition) -> Dict[str, Any]:
    return {
        "name": p.name,
        "type": p.type,
        "mode": p.mode,
        "source": p.source,
        "query_group": p.query_group,
        "properties": p.properties,
    }


def _calc_item_to_dict(ci: TmdlCalculationItem) -> Dict[str, Any]:
    return {
        "name": ci.name,
        "expression": ci.expression,
        "ordinal": ci.ordinal,
        "format_string_definition": ci.format_string_definition,
        "description": ci.description,
        "annotations": [_annotation_to_dict(a) for a in ci.annotations],
        "properties": ci.properties,
    }


def _calendar_to_dict(cal: TmdlCalendar) -> Dict[str, Any]:
    return {
        "name": cal.name,
        "column_groups": [
            {
                "name": g.name,
                "primary_column": g.primary_column,
                "associated_column": g.associated_column,
                "properties": g.properties,
            }
            for g in cal.column_groups
        ],
        "properties": cal.properties,
    }


def _table_to_dict(t: TmdlTable) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "name": t.name,
        "columns": [_column_to_dict(c) for c in t.columns],
        "measures": [_measure_to_dict(m) for m in t.measures],
        "hierarchies": [_hierarchy_to_dict(h) for h in t.hierarchies],
        "partitions": [_partition_to_dict(p) for p in t.partitions],
        "calculation_items": [_calc_item_to_dict(ci) for ci in t.calculation_items],
        "calendars": [_calendar_to_dict(cal) for cal in t.calendars],
        "is_calculation_group": t.is_calculation_group,
        "is_hidden": t.is_hidden,
        "description": t.description,
        "annotations": [_annotation_to_dict(a) for a in t.annotations],
        "properties": t.properties,
    }
    if t.lineage_tag:
        d["lineage_tag"] = t.lineage_tag
    if t._source_file:
        d["_source_file"] = t._source_file
    return d


def _relationship_to_dict(r: TmdlRelationship) -> Dict[str, Any]:
    return {
        "id": r.id,
        "from_column": r.from_column,
        "from_cardinality": r.from_cardinality,
        "to_column": r.to_column,
        "to_cardinality": r.to_cardinality,
        "from_table": r.from_table,
        "from_column_name": r.from_column_name,
        "to_table": r.to_table,
        "to_column_name": r.to_column_name,
        "is_active": r.is_active,
        "cross_filtering_behavior": r.cross_filtering_behavior,
        "security_filtering_behavior": r.security_filtering_behavior,
        "rely_on_referential_integrity": r.rely_on_referential_integrity,
        "annotations": [_annotation_to_dict(a) for a in r.annotations],
        "properties": r.properties,
    }


def _expression_to_dict(e: TmdlExpression) -> Dict[str, Any]:
    return {
        "name": e.name,
        "expression": e.expression,
        "kind": e.kind,
        "query_group": e.query_group,
    }


def _datasource_to_dict(d: TmdlDatasource) -> Dict[str, Any]:
    return {
        "name": d.name,
        "type": d.type,
        "connection_string": d.connection_string,
        "provider": d.provider,
        "properties": d.properties,
    }


def _role_to_dict(r: TmdlRole) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "name": r.name,
        "model_permission": r.model_permission,
        "table_permissions": [
            {
                "table": tp.table,
                "filter_expression": tp.filter_expression,
                "metadata_permission": tp.metadata_permission,
                "column_permissions": [
                    {"column": cp.column, "metadata_permission": cp.metadata_permission}
                    for cp in tp.column_permissions
                ],
            }
            for tp in r.table_permissions
        ],
        "description": r.description,
        "properties": r.properties,
    }
    if r._source_file:
        result["_source_file"] = r._source_file
    return result


def _perspective_to_dict(p: TmdlPerspective) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "name": p.name,
        "items": [
            {"object_type": i.object_type, "table": i.table, "name": i.name} for i in p.items
        ],
        "description": p.description,
        "properties": p.properties,
    }
    if p._source_file:
        result["_source_file"] = p._source_file
    return result


def _culture_to_dict(c: TmdlCulture) -> Dict[str, Any]:
    return {
        "name": c.name,
        "translations": [
            {
                "object_type": t.object_type,
                "table": t.table,
                "name": t.name,
                "translated_caption": t.translated_caption,
                "translated_description": t.translated_description,
                "translated_display_folder": t.translated_display_folder,
            }
            for t in c.translations
        ],
        "properties": c.properties,
        # the JSON block itself can be megabytes; the typed model keeps it
        "has_linguistic_metadata": bool(c.linguistic_metadata),
        "linguistic_metadata_chars": len(c.linguistic_metadata or ""),
    }


def _function_to_dict(f: TmdlFunction) -> Dict[str, Any]:
    return {
        "name": f.name,
        "expression": f.expression,
        "description": f.description,
        "is_hidden": f.is_hidden,
        "lineage_tag": f.lineage_tag,
        "annotations": [_annotation_to_dict(a) for a in f.annotations],
        "properties": f.properties,
    }


def _database_to_dict(db: Optional[TmdlDatabase]) -> Optional[Dict[str, Any]]:
    if not db:
        return None
    return {
        "name": db.name,
        "compatibility_level": db.compatibility_level,
        "properties": db.properties,
    }


def _model_props_to_dict(m: Optional[TmdlModelProperties]) -> Optional[Dict[str, Any]]:
    if not m:
        return None
    return {
        "name": m.name,
        "culture": m.culture,
        "default_power_bi_data_source_version": m.default_power_bi_data_source_version,
        "discourage_implicit_measures": m.discourage_implicit_measures,
        "properties": m.properties,
    }
