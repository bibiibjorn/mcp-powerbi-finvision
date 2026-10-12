"""
Unified TMDL Parser

Single canonical parser for all TMDL (Tabular Model Definition Language) files.
Produces typed TmdlModel dataclasses from core.tmdl.models.

Review G-07 (2026-09-22): the parser is now indentation-scoped. Every file is
first read into a :mod:`core.tmdl.tmdl_tree` document (header at level N,
properties/children at N+1, ``=`` bodies at N+2+, ``///`` descriptions on the
next object) and the typed objects are mapped from that tree. That fixes, in
one place: lost ``///`` descriptions, ``isHidden`` swallowed into a preceding
expression, calculation groups keeping only their first item, ``cultureInfo``
files, multi-line RLS filters and OLS ``columnPermission``, measures without
an expression, table annotations absorbed into partition M, ``''``-escaped
names, BOM-prefixed files, UDF ``function`` blocks and ``extendedProperty``
JSON leaking into property keys.

The legacy line helpers (``_extract_expression`` & co.) were removed as dead
code (deep review C-03).
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (
    TmdlAnnotation,
    TmdlCalculationItem,
    TmdlCalendar,
    TmdlCalendarColumnGroup,
    TmdlColumn,
    TmdlColumnPermission,
    TmdlCulture,
    TmdlDatabase,
    TmdlDatasource,
    TmdlExpression,
    TmdlFunction,
    TmdlHierarchy,
    TmdlHierarchyLevel,
    TmdlMeasure,
    TmdlModel,
    TmdlModelProperties,
    TmdlPartition,
    TmdlPerspective,
    TmdlPerspectiveItem,
    TmdlRelationship,
    TmdlRole,
    TmdlTable,
    TmdlTablePermission,
    TmdlTranslation,
)
from .tmdl_tree import TmdlDocument, TmdlNode, parse_document, parse_text

logger = logging.getLogger(__name__)

_KPI_KEYS = {
    "targetExpression": "target_expression",
    "targetFormatString": "target_format_string",
    "statusExpression": "status_expression",
    "statusGraphic": "status_graphic",
    "trendExpression": "trend_expression",
    "trendGraphic": "trend_graphic",
}


class UnifiedTmdlParser:
    """
    Canonical parser for TMDL definition folders.

    Parses the definition/ folder structure into typed TmdlModel dataclasses:
    tables, columns, measures, hierarchies, partitions, calculation items,
    calendars, relationships, expressions, datasources, roles (RLS + OLS),
    perspectives, cultures (translations + linguistic metadata) and UDFs.
    """

    def __init__(self, tmdl_path: Optional[str] = None):
        """
        Args:
            tmdl_path: Path to the TMDL root (contains ``definition/``), a
                ``.SemanticModel`` folder, or the ``definition`` folder itself.
                ``None`` builds a parser for :meth:`parse_folder` /
                :meth:`_parse_table_content` only.
        """
        self.issues: List[Dict[str, Any]] = []
        if tmdl_path is None:
            self.tmdl_path = None
            self.definition_path = None
            return
        self.tmdl_path = Path(tmdl_path)
        self.definition_path = self._resolve_definition(self.tmdl_path)
        logger.info(f"Initialized unified TMDL parser for: {tmdl_path}")

    @staticmethod
    def _resolve_definition(root: Path) -> Path:
        definition = root / "definition"
        if definition.exists():
            return definition
        if (root / "model.tmdl").exists() or (root / "tables").is_dir():
            return root
        raise FileNotFoundError(f"Definition folder not found at: {definition}")

    def parse_folder(self, tmdl_path: str) -> TmdlModel:
        """Parse *tmdl_path* (any accepted form) — convenience for no-arg construction."""
        self.tmdl_path = Path(tmdl_path)
        self.definition_path = self._resolve_definition(self.tmdl_path)
        return self.parse_full_model()

    def parse_full_model(self) -> TmdlModel:
        """Parse the complete TMDL model structure."""
        if self.definition_path is None:
            raise FileNotFoundError("UnifiedTmdlParser has no TMDL path")
        logger.info("Parsing full TMDL model")
        self.issues = []

        model = TmdlModel(
            database=self._parse_database(),
            model=self._parse_model(),
            tables=self._parse_tables(),
            relationships=self._parse_relationships(),
            roles=self._parse_roles(),
            perspectives=self._parse_perspectives(),
            expressions=self._parse_expressions(),
            datasources=self._parse_datasources(),
            cultures=self._parse_cultures(),
            functions=self._parse_functions(),
        )
        model.parse_issues = list(self.issues)

        logger.info(
            f"Parsed model: {len(model.tables)} tables, "
            f"{sum(len(t.measures) for t in model.tables)} measures, "
            f"{len(model.relationships)} relationships, {len(model.functions)} functions"
        )
        return model

    # ─── file helpers ────────────────────────────────────────────────

    def _doc(self, path: Path) -> Optional[TmdlDocument]:
        try:
            doc = parse_document(str(path))
        except (OSError, UnicodeDecodeError) as e:
            logger.error(f"Error reading {path.name}: {e}")
            self.issues.append({"file": str(path), "line": 0, "code": "READ", "message": str(e)})
            return None
        for issue in doc.issues:
            self.issues.append(
                {
                    "file": str(path),
                    "line": issue.line,
                    "code": issue.code,
                    "message": issue.message,
                }
            )
        return doc

    def _files(self, sub: str) -> List[Path]:
        folder = self.definition_path / sub  # type: ignore[operator]
        if not folder.is_dir():
            return []
        return sorted(folder.glob("*.tmdl"))

    def _root_file(self, name: str) -> Optional[TmdlDocument]:
        path = self.definition_path / name  # type: ignore[operator]
        if not path.exists():
            return None
        return self._doc(path)

    # ─── Database & Model ────────────────────────────────────────────

    def _parse_database(self) -> Optional[TmdlDatabase]:
        doc = self._root_file("database.tmdl")
        if doc is None:
            logger.warning("database.tmdl not found")
            return None
        db = TmdlDatabase()
        for root in doc.roots:
            if root.keyword != "database":
                continue
            db.name = root.name
            for c in root.children:
                if c.kind != "property" or c.has_expression:
                    continue
                if c.keyword == "compatibilityLevel":
                    try:
                        db.compatibility_level = int(str(c.value).strip())
                    except (TypeError, ValueError):
                        db.properties[c.keyword] = c.value
                else:
                    db.properties[c.keyword] = c.value
        return db

    def _parse_model(self) -> Optional[TmdlModelProperties]:
        doc = self._root_file("model.tmdl")
        if doc is None:
            logger.warning("model.tmdl not found")
            return None
        props = TmdlModelProperties()
        for root in doc.roots:
            if root.keyword == "model":
                props.name = root.name
                for c in root.children:
                    if c.keyword == "culture" and c.kind == "property":
                        props.culture = c.value
                    elif c.keyword == "defaultPowerBIDataSourceVersion":
                        props.default_power_bi_data_source_version = c.value
                    elif c.keyword == "discourageImplicitMeasures":
                        props.discourage_implicit_measures = _node_bool(c)
                    elif c.keyword == "annotation":
                        props.properties.setdefault("_annotations", []).append(
                            {"name": c.name, "value": _annotation_value(c)}
                        )
                    elif c.kind == "property":
                        props.properties[c.keyword] = c.expression if c.has_expression else c.value
                    elif c.kind == "flag" and not c.children:
                        props.properties[c.keyword] = True
            elif root.keyword == "annotation":
                props.properties.setdefault("_annotations", []).append(
                    {"name": root.name, "value": _annotation_value(root)}
                )
            elif root.keyword == "ref":
                props.properties.setdefault("_refs", []).append(
                    {"kind": root.ref_kind, "name": root.name}
                )
        return props

    # ─── Tables ──────────────────────────────────────────────────────

    def _parse_tables(self) -> List[TmdlTable]:
        tables: List[TmdlTable] = []
        files = self._files("tables")
        if not files:
            logger.warning("tables/ directory not found or empty")
        for table_file in files:
            doc = self._doc(table_file)
            if doc is None:
                continue
            for root in doc.roots:
                if root.keyword != "table":
                    continue
                table = self._table_from_node(root)
                table._source_file = table_file.name
                tables.append(table)
        logger.debug(f"Parsed {len(tables)} tables")
        return tables

    def _parse_table_content(self, content: str) -> Optional[TmdlTable]:
        """Parse table TMDL *text* into a TmdlTable (string entry point)."""
        doc = parse_text(content)
        for root in doc.roots:
            if root.keyword == "table":
                return self._table_from_node(root)
        return None

    def _table_from_node(self, node: TmdlNode) -> TmdlTable:
        table = TmdlTable(name=node.name or "")
        table.description = node.description
        for c in node.children:
            kw = c.keyword
            if kw == "column":
                table.columns.append(_column_from_node(c))
            elif kw == "measure":
                table.measures.append(_measure_from_node(c))
            elif kw == "hierarchy":
                table.hierarchies.append(_hierarchy_from_node(c))
            elif kw == "partition":
                table.partitions.append(_partition_from_node(c))
            elif kw == "calculationGroup":
                table.is_calculation_group = True
                for g in c.children:
                    if g.keyword == "calculationItem":
                        table.calculation_items.append(_calc_item_from_node(g))
                    elif g.kind == "property" and not g.has_expression:
                        table.properties[f"calculationGroup.{g.keyword}"] = g.value
                    elif g.kind == "property" and g.has_expression:
                        table.properties[f"calculationGroup.{g.keyword}"] = g.expression
            elif kw == "calculationItem":  # tolerated at table level (older tools)
                table.is_calculation_group = True
                table.calculation_items.append(_calc_item_from_node(c))
            elif kw == "calendar":
                table.calendars.append(_calendar_from_node(c))
            elif kw == "annotation":
                table.annotations.append(_annotation_from_node(c))
            elif kw == "isHidden":
                table.is_hidden = _node_bool(c)
            elif kw == "lineageTag":
                table.lineage_tag = c.value
            elif kw == "description" and c.kind == "property":
                table.description = c.value
            elif c.kind == "property":
                table.properties[kw] = c.expression if c.has_expression else c.value
            elif c.kind == "flag" and not c.children:
                table.properties[kw] = True
        return table

    # ─── Relationships ───────────────────────────────────────────────

    def _parse_relationships(self) -> List[TmdlRelationship]:
        docs: List[TmdlDocument] = []
        rel_file = self.definition_path / "relationships.tmdl"  # type: ignore[operator]
        if rel_file.exists():
            d = self._doc(rel_file)
            if d is not None:
                docs.append(d)
        for f in self._files("relationships"):
            d = self._doc(f)
            if d is not None:
                docs.append(d)
        relationships: List[TmdlRelationship] = []
        for doc in docs:
            for root in doc.roots:
                if root.keyword != "relationship":
                    continue
                rel = TmdlRelationship(id=root.name or "")
                for c in root.children:
                    kw = c.keyword
                    if kw == "fromColumn":
                        rel.from_column = _raw_value(doc, c)
                    elif kw == "toColumn":
                        rel.to_column = _raw_value(doc, c)
                    elif kw == "fromCardinality":
                        rel.from_cardinality = c.value
                    elif kw == "toCardinality":
                        rel.to_cardinality = c.value
                    elif kw == "crossFilteringBehavior":
                        rel.cross_filtering_behavior = c.value
                    elif kw == "securityFilteringBehavior":
                        rel.security_filtering_behavior = c.value
                    elif kw == "relyOnReferentialIntegrity":
                        rel.rely_on_referential_integrity = _node_bool(c)
                    elif kw == "isActive":
                        rel.is_active = _node_bool(c)
                    elif kw == "annotation":
                        rel.annotations.append(_annotation_from_node(c))
                    elif c.kind == "property":
                        rel.properties[kw] = c.expression if c.has_expression else c.value
                relationships.append(rel)
        logger.debug(f"Parsed {len(relationships)} relationships")
        return relationships

    # ─── Expressions / datasources / functions ───────────────────────

    def _parse_expressions(self) -> List[TmdlExpression]:
        doc = self._root_file("expressions.tmdl")
        if doc is None:
            return []
        out: List[TmdlExpression] = []
        for root in doc.roots:
            if root.keyword != "expression":
                continue
            expr = TmdlExpression(name=root.name or "")
            text = root.expression
            if text and "\n" not in text and " meta " in text:
                text = text.split(" meta ")[0].strip()
            expr.expression = text
            for c in root.children:
                if c.keyword == "lineageTag":
                    expr.lineage_tag = c.value
                elif c.keyword == "queryGroup":
                    expr.query_group = c.value
                elif c.keyword == "annotation":
                    expr.annotations.append(_annotation_from_node(c))
                elif c.kind == "property":
                    expr.properties[c.keyword] = c.expression if c.has_expression else c.value
            if root.description:
                expr.properties["description"] = root.description
            out.append(expr)
        return out

    def _parse_datasources(self) -> List[TmdlDatasource]:
        doc = self._root_file("datasources.tmdl")
        if doc is None:
            return []
        out: List[TmdlDatasource] = []
        for root in doc.roots:
            if root.keyword.lower() != "datasource":
                continue
            ds = TmdlDatasource(name=root.name or "")
            for c in root.children:
                if c.keyword == "type":
                    ds.type = c.value
                elif c.keyword.lower() == "connectionstring":
                    ds.connection_string = c.value
                elif c.keyword == "provider":
                    ds.provider = c.value
                elif c.keyword == "annotation":
                    ds.annotations.append(_annotation_from_node(c))
                elif c.kind == "property":
                    ds.properties[c.keyword] = c.expression if c.has_expression else c.value
            out.append(ds)
        return out

    def _parse_functions(self) -> List[TmdlFunction]:
        docs: List[TmdlDocument] = []
        d = self._root_file("functions.tmdl")
        if d is not None:
            docs.append(d)
        for f in self._files("functions"):
            d2 = self._doc(f)
            if d2 is not None:
                docs.append(d2)
        out: List[TmdlFunction] = []
        for doc in docs:
            for root in doc.roots:
                if root.keyword != "function":
                    continue
                fn = TmdlFunction(name=root.name or "", expression=root.expression)
                fn.description = root.description
                for c in root.children:
                    if c.keyword == "lineageTag":
                        fn.lineage_tag = c.value
                    elif c.keyword == "isHidden":
                        fn.is_hidden = _node_bool(c)
                    elif c.keyword == "annotation":
                        fn.annotations.append(_annotation_from_node(c))
                    elif c.kind == "property":
                        fn.properties[c.keyword] = c.expression if c.has_expression else c.value
                out.append(fn)
        return out

    # ─── Roles ───────────────────────────────────────────────────────

    def _parse_roles(self) -> List[TmdlRole]:
        roles: List[TmdlRole] = []
        for role_file in self._files("roles"):
            doc = self._doc(role_file)
            if doc is None:
                continue
            for root in doc.roots:
                if root.keyword != "role":
                    continue
                role = _role_from_node(root)
                role._source_file = role_file.name
                roles.append(role)
        return roles

    def _parse_role_content(self, content: str) -> Optional[TmdlRole]:
        doc = parse_text(content)
        for root in doc.roots:
            if root.keyword == "role":
                return _role_from_node(root)
        return None

    # ─── Perspectives ────────────────────────────────────────────────

    def _parse_perspectives(self) -> List[TmdlPerspective]:
        out: List[TmdlPerspective] = []
        for f in self._files("perspectives"):
            doc = self._doc(f)
            if doc is None:
                continue
            for root in doc.roots:
                if root.keyword != "perspective":
                    continue
                p = _perspective_from_node(root)
                p._source_file = f.name
                out.append(p)
        return out

    def _parse_perspective_content(self, content: str) -> Optional[TmdlPerspective]:
        doc = parse_text(content)
        for root in doc.roots:
            if root.keyword == "perspective":
                return _perspective_from_node(root)
        return None

    # ─── Cultures ────────────────────────────────────────────────────

    def _parse_cultures(self) -> List[TmdlCulture]:
        out: List[TmdlCulture] = []
        for f in self._files("cultures"):
            doc = self._doc(f)
            if doc is None:
                continue
            for root in doc.roots:
                if root.keyword not in ("cultureInfo", "culture"):
                    continue
                c = _culture_from_node(root)
                c._source_file = f.name
                out.append(c)
        return out

    def _parse_culture_content(self, content: str) -> Optional[TmdlCulture]:
        doc = parse_text(content)
        for root in doc.roots:
            if root.keyword in ("cultureInfo", "culture"):
                return _culture_from_node(root)
        return None


# ═════════════════════════════════════════════════════════════════════
# Node → dataclass mapping
# ═════════════════════════════════════════════════════════════════════


def _node_bool(node: TmdlNode) -> bool:
    if node.kind == "flag":
        return True
    return _parse_bool(node.value if node.value not in ("",) else None)


def _raw_value(doc: TmdlDocument, node: TmdlNode) -> Optional[str]:
    """``key: value`` text exactly as written (keeps the inner quotes of refs)."""
    line = doc.lines[node.start]
    if ":" not in line:
        return node.value
    return line.split(":", 1)[1].strip()


def _annotation_value(node: TmdlNode) -> str:
    if node.has_expression:
        return node.expression or ""
    return node.value or ""


def _annotation_from_node(node: TmdlNode) -> TmdlAnnotation:
    value = _annotation_value(node)
    if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0] and "\n" not in value:
        value = value[1:-1]
    return TmdlAnnotation(name=node.name or "", value=value)


def _extended_properties(node: TmdlNode) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for c in node.children:
        if c.keyword == "extendedProperty" and c.name:
            out[c.name] = c.expression or ""
    return out


def _generic_prop(target_props: Dict[str, Any], c: TmdlNode) -> None:
    if c.kind == "property":
        target_props[c.keyword] = c.expression if c.has_expression else c.value
    elif c.kind == "flag" and not c.children:
        target_props[c.keyword] = True


def _column_from_node(node: TmdlNode) -> TmdlColumn:
    col = TmdlColumn(name=node.name or "", is_calculated=node.has_expression)
    col.description = node.description
    if node.has_expression:
        col.expression = node.expression
    for c in node.children:
        kw = c.keyword
        if kw == "dataType":
            col.data_type = c.value
        elif kw == "sourceColumn":
            col.source_column = c.value
        elif kw == "description" and c.kind == "property":
            col.description = c.value
        elif kw == "displayFolder":
            col.display_folder = c.value
        elif kw == "formatString":
            col.format_string = c.value
        elif kw == "dataCategory":
            col.data_category = c.value
        elif kw == "summarizeBy":
            col.summarize_by = c.value
        elif kw == "sortByColumn":
            col.sort_by_column = c.value
        elif kw == "isKey":
            col.is_key = _node_bool(c)
        elif kw == "isHidden":
            col.is_hidden = _node_bool(c)
        elif kw == "lineageTag":
            col.lineage_tag = c.value
        elif kw == "annotation":
            col.annotations.append(_annotation_from_node(c))
        elif kw == "relatedColumnDetails":
            for g in c.children:
                if g.keyword == "groupByColumn" and g.value:
                    col.group_by_columns.append(g.value)
        elif kw == "extendedProperty":
            continue
        elif kw in ("variation", "changedProperty", "alternateOf"):
            col.properties.setdefault(f"_{kw}", []).append(_node_summary(c))
        else:
            _generic_prop(col.properties, c)
    col.extended_properties = _extended_properties(node)
    return col


def _node_summary(node: TmdlNode) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": node.name}
    if node.has_expression:
        out["expression"] = node.expression
    for c in node.children:
        if c.kind == "property":
            out[c.keyword] = c.expression if c.has_expression else c.value
        elif c.kind == "flag":
            out[c.keyword] = True
    return out


def _measure_from_node(node: TmdlNode) -> TmdlMeasure:
    m = TmdlMeasure(name=node.name or "", expression=node.expression)
    m.description = node.description
    for c in node.children:
        kw = c.keyword
        if kw == "formatString":
            m.format_string = c.value
        elif kw == "formatStringDefinition":
            m.format_string_definition = c.expression if c.has_expression else c.value
        elif kw == "detailRowsDefinition":
            m.detail_rows_definition = c.expression if c.has_expression else c.value
        elif kw == "displayFolder":
            m.display_folder = c.value
        elif kw == "description" and c.kind == "property":
            m.description = c.value
        elif kw == "isHidden":
            m.is_hidden = _node_bool(c)
        elif kw == "dataCategory":
            m.data_category = c.value
        elif kw == "lineageTag":
            m.lineage_tag = c.value
        elif kw == "annotation":
            m.annotations.append(_annotation_from_node(c))
        elif kw == "kpi":
            for k in c.children:
                key = _KPI_KEYS.get(k.keyword, k.keyword)
                m.kpi[key] = k.expression if k.has_expression else k.value
        elif kw == "changedProperty":
            m.properties.setdefault("_changedProperty", []).append(
                c.expression if c.has_expression else c.name
            )
        elif kw == "extendedProperty":
            m.properties.setdefault("_extendedProperty", {})[c.name or ""] = c.expression
        else:
            _generic_prop(m.properties, c)
    return m


def _hierarchy_from_node(node: TmdlNode) -> TmdlHierarchy:
    h = TmdlHierarchy(name=node.name or "")
    if node.description:
        h.properties["description"] = node.description
    for c in node.children:
        kw = c.keyword
        if kw == "level":
            lv = TmdlHierarchyLevel(name=c.name or "")
            for p in c.children:
                if p.keyword == "column":
                    lv.column = p.value
                elif p.keyword == "ordinal":
                    try:
                        lv.ordinal = int(str(p.value))
                    except (TypeError, ValueError):
                        lv.ordinal = None
                elif p.keyword == "lineageTag":
                    lv.lineage_tag = p.value
            h.levels.append(lv)
        elif kw == "isHidden":
            h.is_hidden = _node_bool(c)
        elif kw == "lineageTag":
            h.lineage_tag = c.value
        elif kw == "annotation":
            h.properties.setdefault("_annotations", []).append(
                {"name": c.name, "value": _annotation_value(c)}
            )
        else:
            _generic_prop(h.properties, c)
    return h


def _partition_from_node(node: TmdlNode) -> TmdlPartition:
    part = TmdlPartition(name=node.name or "")
    header_expr = (node.expr_inline or "").strip()
    # `partition X = m` / `= calculated` / `= entity` — the header "expression"
    # is the partition kind; the real body is the `source` property.
    m = re.match(r"^(\w+)", header_expr)
    part.type = m.group(1) if m else (header_expr or None)
    for c in node.children:
        kw = c.keyword
        if kw == "source":
            part.source = c.expression if c.has_expression else c.value
            if c.children:
                # entity / policy-range partitions: `source` is a block
                part.properties["source_block"] = _node_summary(c)
        elif kw == "mode":
            part.mode = c.value
        elif kw == "queryGroup":
            part.query_group = c.value
        elif kw == "annotation":
            part.properties.setdefault("_annotations", []).append(
                {"name": c.name, "value": _annotation_value(c)}
            )
        else:
            _generic_prop(part.properties, c)
    return part


def _calc_item_from_node(node: TmdlNode) -> TmdlCalculationItem:
    ci = TmdlCalculationItem(name=node.name or "", expression=node.expression)
    ci.description = node.description
    for c in node.children:
        kw = c.keyword
        if kw == "ordinal":
            try:
                ci.ordinal = int(str(c.value))
            except (TypeError, ValueError):
                ci.ordinal = None
        elif kw == "formatStringDefinition":
            ci.format_string_definition = c.expression if c.has_expression else c.value
        elif kw == "description" and c.kind == "property":
            ci.description = c.value
        elif kw == "lineageTag":
            ci.lineage_tag = c.value
        elif kw == "annotation":
            ci.annotations.append(_annotation_from_node(c))
        else:
            _generic_prop(ci.properties, c)
    return ci


def _calendar_from_node(node: TmdlNode) -> TmdlCalendar:
    cal = TmdlCalendar(name=node.name or "")
    for c in node.children:
        if c.keyword == "calendarColumnGroup":
            g = TmdlCalendarColumnGroup(name=(c.name or "").strip())
            for p in c.children:
                if p.keyword == "primaryColumn":
                    g.primary_column = p.value
                elif p.keyword == "associatedColumn":
                    g.associated_column = p.value
                elif p.kind == "property":
                    g.properties[p.keyword] = p.expression if p.has_expression else p.value
                else:
                    g.properties[p.keyword if p.name is None else f"{p.keyword} {p.name}"] = True
            cal.column_groups.append(g)
        elif c.kind == "property":
            cal.properties[c.keyword] = c.expression if c.has_expression else c.value
        else:
            cal.properties[c.keyword if c.name is None else f"{c.keyword} {c.name}"] = True
    return cal


def _role_from_node(node: TmdlNode) -> TmdlRole:
    role = TmdlRole(name=node.name or "")
    role.description = node.description
    for c in node.children:
        kw = c.keyword
        if kw == "modelPermission":
            role.model_permission = c.value
        elif kw == "description" and c.kind == "property":
            role.description = c.value
        elif kw == "tablePermission":
            tp = TmdlTablePermission(table=c.name or "")
            if c.has_expression:
                tp.filter_expression = c.expression
            for p in c.children:
                if p.keyword == "filterExpression":
                    tp.filter_expression = p.expression if p.has_expression else p.value
                elif p.keyword == "metadataPermission":
                    tp.metadata_permission = p.value
                elif p.keyword == "columnPermission":
                    cp = TmdlColumnPermission(column=p.name or "")
                    for q in p.children:
                        if q.keyword == "metadataPermission":
                            cp.metadata_permission = q.value
                    tp.column_permissions.append(cp)
            role.table_permissions.append(tp)
        elif kw == "member":
            role.properties.setdefault("_members", []).append(c.name)
        elif kw == "annotation":
            role.annotations.append(_annotation_from_node(c))
        else:
            _generic_prop(role.properties, c)
    return role


def _perspective_from_node(node: TmdlNode) -> TmdlPerspective:
    persp = TmdlPerspective(name=node.name or "")
    persp.description = node.description
    for c in node.children:
        if c.keyword == "perspectiveTable":
            table = c.name or ""
            persp.items.append(TmdlPerspectiveItem(object_type="table", table=table))
            for p in c.children:
                kind = {
                    "perspectiveColumn": "column",
                    "perspectiveMeasure": "measure",
                    "perspectiveHierarchy": "hierarchy",
                }.get(p.keyword)
                if kind:
                    persp.items.append(
                        TmdlPerspectiveItem(object_type=kind, table=table, name=p.name)
                    )
        elif c.keyword == "annotation":
            persp.annotations.append(_annotation_from_node(c))
        elif c.keyword == "description" and c.kind == "property":
            persp.description = c.value
        else:
            _generic_prop(persp.properties, c)
    return persp


def _translation_props(trans: TmdlTranslation, node: TmdlNode) -> None:
    for p in node.children:
        if p.keyword == "translatedCaption":
            trans.translated_caption = p.value
        elif p.keyword == "translatedDescription":
            trans.translated_description = p.value
        elif p.keyword == "translatedDisplayFolder":
            trans.translated_display_folder = p.value


def _culture_from_node(node: TmdlNode) -> TmdlCulture:
    culture = TmdlCulture(name=node.name or "")
    for c in node.children:
        kw = c.keyword
        if kw == "linguisticMetadata":
            culture.linguistic_metadata = c.expression
            for p in c.children:
                if p.keyword == "contentType":
                    culture.linguistic_content_type = p.value
        elif kw == "translations":
            for model_node in c.children:
                for tnode in model_node.children:
                    if tnode.keyword != "table":
                        continue
                    table = tnode.name or ""
                    tt = TmdlTranslation(object_type="table", table=table)
                    _translation_props(tt, tnode)
                    if tt.translated_caption or tt.translated_description:
                        culture.translations.append(tt)
                    for onode in tnode.children:
                        if onode.keyword in ("column", "measure", "hierarchy"):
                            tr = TmdlTranslation(
                                object_type=onode.keyword, table=table, name=onode.name
                            )
                            _translation_props(tr, onode)
                            culture.translations.append(tr)
        elif kw == "translationTable":  # legacy / hand-written shape
            table = c.name or ""
            for p in c.children:
                kind = {
                    "translationColumn": "column",
                    "translationMeasure": "measure",
                    "translationHierarchy": "hierarchy",
                }.get(p.keyword)
                if kind:
                    tr = TmdlTranslation(object_type=kind, table=table, name=p.name)
                    _translation_props(tr, p)
                    culture.translations.append(tr)
                elif p.keyword == "translatedCaption":
                    culture.translations.append(
                        TmdlTranslation(
                            object_type="table", table=table, translated_caption=p.value
                        )
                    )
        else:
            _generic_prop(culture.properties, c)
    return culture


def _parse_bool(value: Any) -> bool:
    """Parse a TMDL boolean value."""
    if isinstance(value, bool):
        return value
    if value is None:
        return True  # Bare keyword without value means true in TMDL
    return str(value).lower() == "true"


# ═════════════════════════════════════════════════════════════════════
# Convenience function
# ═════════════════════════════════════════════════════════════════════


def parse_tmdl_model(tmdl_path: str) -> TmdlModel:
    """
    Parse a TMDL model from a definition folder.

    Args:
        tmdl_path: Path to TMDL root (containing definition/ subfolder)

    Returns:
        Parsed TmdlModel
    """
    parser = UnifiedTmdlParser(tmdl_path)
    return parser.parse_full_model()
