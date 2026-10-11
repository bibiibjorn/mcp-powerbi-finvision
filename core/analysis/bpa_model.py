"""TOM-like object graph over a TMSL model, for the BPA rule evaluator.

``JsonSerializer.SerializeObject(Model)`` (the BPA input) is lowerCamel TMSL:
it omits every property that still has its default value, spells enums
lowerCamel (``"bothDirections"``, ``"int64"``) and marks calculated columns with
``"type": "calculated"``. Tabular Editor rules are written against the TOM
object model (``CrossFilteringBehavior``, ``UsedInRelationships``,
``ReferencedBy`` …). This module bridges the two:

- every node answers the TOM property names a rule uses, with the TOM default
  when the TMSL key is absent (``IsHidden`` -> ``False``, ``IsAvailableInMDX`` ->
  ``True``, ``FromCardinality`` -> ``Many`` …);
- enum properties come back as :class:`EnumValue` (case-insensitive equality);
- navigation properties are real objects (``FromColumn.Table.Name``);
- derived collections are computed once per model (``UsedInRelationships``,
  ``UsedInSortBy``, ``UsedInHierarchies``, ``ReferencedBy``, ``DependsOn``,
  ``RowLevelSecurity`` …);
- DAX text is exposed as :class:`DaxText` — a ``str`` whose ``sanitized`` twin
  has comments and string-literal contents blanked (same length), which the
  regex / ``Contains`` helpers scan, so commented-out code and text inside
  strings no longer trigger rules (I-12);
- an unknown member raises :class:`BpaUnsupported` instead of returning
  ``None`` (I-13: an unimplemented construct must not read as "compliant").
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from core.analysis.bpa_expression import BpaRuntimeError, BpaUnsupported, EnumValue

__all__ = [
    "DaxText",
    "MText",
    "ModelView",
    "Node",
    "SUPPORTED_SCOPES",
    "normalize_scope",
    "text_of",
]


# ───────────────────────────── text helpers ────────────────────────────


def text_of(value: Any) -> str:
    """TMSL text: expressions/descriptions may be serialised as a list of lines."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value)


def _blank(chars: List[str], start: int, end: int) -> None:
    for k in range(start, end):
        if chars[k] not in "\r\n":
            chars[k] = " "


def sanitize_dax(text: str) -> str:
    """Blank DAX comments and string-literal contents, preserving length/newlines."""
    if not text:
        return text
    try:
        from core.dax.dax_reference_parser import get_lexer
        from core.dax.tokenizer import TokenType

        tokens = get_lexer().tokenize(text)
    except Exception:  # noqa: BLE001 — never let sanitising break a rule run
        return text
    chars = list(text)
    for t in tokens:
        if t.type in (TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK):
            _blank(chars, t.start, t.end)
        elif t.type == TokenType.STRING and t.end - t.start >= 2:
            _blank(chars, t.start + 1, t.end - 1)
    return "".join(chars)


def sanitize_m(text: str) -> str:
    """Blank Power Query M comments (``//`` and ``/* */``) outside string literals."""
    if not text:
        return text
    from core.analysis.m_practices import strip_m_comments

    return strip_m_comments(text)


class DaxText(str):
    """A DAX expression; ``sanitized`` has comments and string contents blanked."""

    @property
    def sanitized(self) -> str:
        cached = self.__dict__.get("_sanitized")
        if cached is None:
            cached = sanitize_dax(str(self))
            self.__dict__["_sanitized"] = cached
        return cached


class MText(str):
    """An M expression; ``sanitized`` has comments blanked (strings kept)."""

    @property
    def sanitized(self) -> str:
        cached = self.__dict__.get("_sanitized")
        if cached is None:
            cached = sanitize_m(str(self))
            self.__dict__["_sanitized"] = cached
        return cached


def _pascal(value: Any) -> str:
    text = str(value)
    if not text:
        return text
    return text[0].upper() + text[1:]


def _enum(value: Any, default: str, type_name: str) -> EnumValue:
    if value is None or value == "":
        return EnumValue(default, type_name)
    if isinstance(value, EnumValue):
        return value
    return EnumValue(_pascal(value), type_name)


def _norm(name: Any) -> str:
    return " ".join(str(name or "").lower().split())


# ─────────────────────────── collection types ──────────────────────────


class RefList(list):  # type: ignore[type-arg]
    """``ReferencedBy``: a list with TE's ``AllMeasures`` / ``AllColumns`` members."""

    def bpa_get(self, name: str) -> Any:
        low = name.lower()
        if low in ("count", "length"):
            return len(self)
        kinds = {
            "allmeasures": ("Measure",),
            "measures": ("Measure",),
            "allcolumns": ("Column",),
            "columns": ("Column",),
            "alltables": ("Table",),
            "tables": ("Table",),
            "allcalculationitems": ("CalculationItem",),
        }.get(low)
        if kinds is not None:
            return [n for n in self if getattr(n, "kind", None) in kinds]
        raise BpaUnsupported(f"ReferencedBy.{name}")

    def bpa_call(self, name: str, args: List[Any]) -> Any:
        if name.lower() == "deep" and not args:
            return self  # direct references only (documented limitation)
        raise BpaUnsupported(f"ReferencedBy.{name}()")


class _Indexer:
    """``InPerspective[name]`` / ``RowLevelSecurity[role]`` style indexers."""

    def __init__(self, label: str, fn: Callable[[Any], Any]) -> None:
        self.label = label
        self._fn = fn

    def bpa_index(self, key: Any) -> Any:
        return self._fn(key)

    def bpa_get(self, name: str) -> Any:
        raise BpaUnsupported(f"{self.label}.{name}")


class _IndexedList(list):  # type: ignore[type-arg]
    """A list that also supports ``[key]`` lookup by name (TE indexers)."""

    def __init__(self, items: Iterable[Any], lookup: Callable[[Any], Any]) -> None:
        super().__init__(items)
        self._lookup = lookup

    def bpa_index(self, key: Any) -> Any:
        if isinstance(key, (int, float)) and not isinstance(key, bool):
            k = int(key)
            if k < 0 or k >= len(self):
                raise BpaRuntimeError(f"index {k} out of range")
            return self[k]
        return self._lookup(key)

    def bpa_get(self, name: str) -> Any:
        if name.lower() in ("count", "length"):
            return len(self)
        raise BpaUnsupported(f"collection member {name}")


# ───────────────────────────── property specs ──────────────────────────
# (tmsl key(s), converter, default). Converter: "str" "text" "dax" "m" "bool"
# "int" "raw" or ("enum", TypeName, default-member).

_COMMON = {
    "name": ("name", "str", ""),
    "description": ("description", "text", ""),
    "ishidden": ("isHidden", "bool", False),
    "lineagetag": ("lineageTag", "str", ""),
    "sourcelineagetag": ("sourceLineageTag", "str", ""),
}

_PROPS: Dict[str, Dict[str, Tuple[Any, Any, Any]]] = {
    "Model": {
        "name": ("name", "str", "Model"),
        "description": ("description", "text", ""),
        "culture": ("culture", "str", ""),
        "collation": ("collation", "str", ""),
        "defaultmode": ("defaultMode", ("enum", "ModeType", "Import"), None),
        "discourageimplicitmeasures": ("discourageImplicitMeasures", "bool", False),
        "valuefilterbehavior": (
            "valueFilterBehavior",
            ("enum", "ValueFilterBehaviorType", "Automatic"),
            None,
        ),
        "defaultpowerbidatasourceversion": (
            "defaultPowerBIDataSourceVersion",
            ("enum", "PowerBIDataSourceVersion", "PowerBI_V1"),
            None,
        ),
        "sourcequeryculture": ("sourceQueryCulture", "str", ""),
    },
    "Table": {
        **_COMMON,
        "datacategory": ("dataCategory", "str", ""),
        "isprivate": ("isPrivate", "bool", False),
        "showasvariationsonly": ("showAsVariationsOnly", "bool", False),
        "excludefrommodelrefresh": ("excludeFromModelRefresh", "bool", False),
        "defaultdetailrowsexpression": ("defaultDetailRowsDefinition.expression", "dax", ""),
        "alternatesourceprecedence": ("alternateSourcePrecedence", "int", 0),
    },
    "Column": {
        **_COMMON,
        "datatype": ("dataType", ("enum", "DataType", "Unknown"), None),
        "sourcecolumn": ("sourceColumn", "str", ""),
        "expression": ("expression", "dax", ""),
        "formatstring": ("formatString", "str", ""),
        "displayfolder": ("displayFolder", "str", ""),
        "datacategory": ("dataCategory", "str", ""),
        "summarizeby": ("summarizeBy", ("enum", "AggregateFunction", "Default"), None),
        "iskey": ("isKey", "bool", False),
        "isnullable": ("isNullable", "bool", True),
        "isunique": ("isUnique", "bool", False),
        "isavailableinmdx": ("isAvailableInMdx", "bool", True),
        "isdefaultlabel": ("isDefaultLabel", "bool", False),
        "isdefaultimage": ("isDefaultImage", "bool", False),
        "isdatatypeinferred": ("isDataTypeInferred", "bool", False),
        "isnameinferred": ("isNameInferred", "bool", False),
        "encodinghint": ("encodingHint", ("enum", "EncodingHintType", "Default"), None),
        "keepuniquerows": ("keepUniqueRows", "bool", False),
        "sourceprovidertype": ("sourceProviderType", "str", ""),
        "displayordinal": ("displayOrdinal", "int", 0),
        # Sep 2026 (CL 1707 / 1708); TMSL omits the defaults (Auto / Off).
        "stringindexingbehavior": (
            "stringIndexingBehavior",
            ("enum", "IndexingBehavior", "Auto"),
            None,
        ),
        "fulltextindexingbehavior": (
            "fullTextIndexingBehavior",
            ("enum", "IndexingBehavior", "Off"),
            None,
        ),
    },
    "Measure": {
        **_COMMON,
        "expression": ("expression", "dax", ""),
        "formatstring": ("formatString", "str", ""),
        "displayfolder": ("displayFolder", "str", ""),
        "datacategory": ("dataCategory", "str", ""),
        "datatype": ("dataType", ("enum", "DataType", "Unknown"), None),
        "issimplemeasure": ("isSimpleMeasure", "bool", False),
        "formatstringexpression": ("formatStringDefinition.expression", "dax", ""),
        "detailrowsexpression": ("detailRowsDefinition.expression", "dax", ""),
    },
    "Hierarchy": {
        **_COMMON,
        "displayfolder": ("displayFolder", "str", ""),
        "hidemembers": ("hideMembers", ("enum", "HierarchyHideMembersType", "Default"), None),
    },
    "Level": {
        "name": ("name", "str", ""),
        "description": ("description", "text", ""),
        "ordinal": ("ordinal", "int", 0),
        "lineagetag": ("lineageTag", "str", ""),
    },
    "Partition": {
        "name": ("name", "str", ""),
        "description": ("description", "text", ""),
        "mode": ("mode", ("enum", "ModeType", "Default"), None),
        "dataview": ("dataView", ("enum", "DataViewType", "Default"), None),
    },
    "Relationship": {
        "name": ("name", "str", ""),
        "isactive": ("isActive", "bool", True),
        "fromcardinality": (
            "fromCardinality",
            ("enum", "RelationshipEndCardinality", "Many"),
            None,
        ),
        "tocardinality": ("toCardinality", ("enum", "RelationshipEndCardinality", "One"), None),
        "crossfilteringbehavior": (
            "crossFilteringBehavior",
            ("enum", "CrossFilteringBehavior", "OneDirection"),
            None,
        ),
        "securityfilteringbehavior": (
            "securityFilteringBehavior",
            ("enum", "SecurityFilteringBehavior", "OneDirection"),
            None,
        ),
        "relyonreferentialintegrity": ("relyOnReferentialIntegrity", "bool", False),
        "joinondatebehavior": (
            "joinOnDateBehavior",
            ("enum", "DateTimeRelationshipBehavior", "DateAndTime"),
            None,
        ),
        "type": ("type", ("enum", "RelationshipType", "SingleColumn"), None),
    },
    "Perspective": {"name": ("name", "str", ""), "description": ("description", "text", "")},
    "Culture": {"name": ("name", "str", "")},
    "ModelRole": {
        "name": ("name", "str", ""),
        "description": ("description", "text", ""),
        "modelpermission": ("modelPermission", ("enum", "ModelPermission", "Read"), None),
    },
    "ModelRoleMember": {
        "name": ("memberName", "str", ""),
        "membername": ("memberName", "str", ""),
        "memberid": ("memberId", "str", ""),
        "identityprovider": ("identityProvider", "str", ""),
    },
    "TablePermission": {
        "filterexpression": ("filterExpression", "dax", ""),
        "expression": ("filterExpression", "dax", ""),
        "metadatapermission": (
            "metadataPermission",
            ("enum", "MetadataPermission", "Default"),
            None,
        ),
    },
    "KPI": {
        "description": ("description", "text", ""),
        "targetexpression": ("targetExpression", "dax", ""),
        "statusexpression": ("statusExpression", "dax", ""),
        "trendexpression": ("trendExpression", "dax", ""),
        "targetformatstring": ("targetFormatString", "str", ""),
        "statusgraphic": ("statusGraphic", "str", ""),
        "trendgraphic": ("trendGraphic", "str", ""),
    },
    "CalculationItem": {
        "name": ("name", "str", ""),
        "description": ("description", "text", ""),
        "expression": ("expression", "dax", ""),
        "ordinal": ("ordinal", "int", -1),
        "formatstringexpression": ("formatStringDefinition.expression", "dax", ""),
    },
    "NamedExpression": {
        **_COMMON,
        "kind": ("kind", ("enum", "ExpressionKind", "M"), None),
        "expression": ("expression", "m", ""),
    },
    "DataSource": {
        "name": ("name", "str", ""),
        "description": ("description", "text", ""),
        "type": ("type", ("enum", "DataSourceType", "Provider"), None),
        "connectionstring": ("connectionString", "str", ""),
    },
    "Function": {
        **_COMMON,
        "expression": ("expression", "dax", ""),
    },
}


def _dig(data: Dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _convert(raw: Any, conv: Any, default: Any) -> Any:
    if isinstance(conv, tuple) and conv and conv[0] == "enum":
        return _enum(raw, conv[2], conv[1])
    if raw is None:
        if conv == "dax":
            return DaxText("")
        if conv == "m":
            return MText("")
        return default
    if conv == "str":
        return text_of(raw)
    if conv == "text":
        return text_of(raw)
    if conv == "dax":
        return DaxText(text_of(raw))
    if conv == "m":
        return MText(text_of(raw))
    if conv == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() == "true"
        return bool(raw)
    if conv == "int":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


# ─────────────────────────────── nodes ─────────────────────────────────


class Node:
    """One TOM object. ``kind`` is the TE ObjectType; ``scope`` the TE rule scope."""

    def __init__(
        self,
        view: "ModelView",
        kind: str,
        data: Dict[str, Any],
        *,
        scope: Optional[str] = None,
        table: Optional["Node"] = None,
        parent: Optional["Node"] = None,
        name: Optional[str] = None,
    ) -> None:
        self.view = view
        self.kind = kind
        self.scope = scope or kind
        self.data = data if isinstance(data, dict) else {}
        self.table = table
        self.parent = parent
        self.bpa_name = name if name is not None else text_of(self.data.get("name"))
        self.extra: Dict[str, Any] = {}
        # Member values are immutable for the life of a view (one analysis
        # run), so memoise them — DaxText.sanitized is then lexed once per
        # object instead of once per rule.
        self._vc: Dict[str, Any] = {}

    def __repr__(self) -> str:
        return f"<{self.scope} {self.bpa_name!r}>"

    def __str__(self) -> str:
        return self.bpa_name

    # evaluator protocol --------------------------------------------------
    def bpa_get(self, name: str) -> Any:
        low = name.lower()
        try:
            return self._vc[low]
        except KeyError:
            pass
        value = self._resolve(name, low)
        self._vc[low] = value
        return value

    def _resolve(self, name: str, low: str) -> Any:
        if low in self.extra:
            value = self.extra[low]
            return value() if callable(value) else value
        handler = _COMPUTED.get(low)
        if handler is not None:
            result = handler(self)
            if result is not _NOT_APPLICABLE:
                return result
        spec = _PROPS.get(self.kind, {}).get(low)
        if spec is not None:
            key, conv, default = spec
            raw = _dig(self.data, key) if "." in key else self.data.get(key)
            if raw is None and key != key.lower():
                raw = self.data.get(key.lower())
            return _convert(raw, conv, default)
        # A TMSL key TOM exposes that we have no explicit spec for.
        for candidate in (name, name[:1].lower() + name[1:]):
            if candidate in self.data:
                return self.data[candidate]
        raise BpaUnsupported(f"{self.kind}.{name}")

    def bpa_call(self, name: str, args: List[Any]) -> Any:
        low = name.lower()
        if low == "getannotation" and len(args) == 1:
            return self.annotation(str(args[0]))
        if low == "hasannotation" and len(args) == 1:
            return self.annotation(str(args[0])) is not None
        if low == "getextendedproperty" and len(args) == 1:
            return self.extended_property(str(args[0]))
        if low == "hasextendedproperty" and len(args) == 1:
            return self.extended_property(str(args[0])) is not None
        if low == "tostring" and not args:
            return self.bpa_name
        if low == "equals" and len(args) == 1:
            return args[0] is self
        raise BpaUnsupported(f"{self.kind}.{name}()")

    # helpers ------------------------------------------------------------
    def annotation(self, name: str) -> Optional[str]:
        for a in self.data.get("annotations") or []:
            if isinstance(a, dict) and a.get("name") == name:
                return text_of(a.get("value"))
        return None

    def extended_property(self, name: str) -> Any:
        for p in self.data.get("extendedProperties") or []:
            if isinstance(p, dict) and p.get("name") == name:
                value = p.get("value")
                return json.dumps(value) if isinstance(value, (dict, list)) else value
        return None

    @property
    def display_name(self) -> str:
        if self.kind == "Relationship":
            d = self.data
            return (
                f"{d.get('fromTable', '?')}[{d.get('fromColumn', '?')}] -> "
                f"{d.get('toTable', '?')}[{d.get('toColumn', '?')}]"
            )
        if self.kind == "Level" and self.parent is not None:
            return f"{self.parent.bpa_name}.{self.bpa_name}"
        if self.kind in ("TablePermission", "ModelRoleMember") and self.parent is not None:
            return f"{self.parent.bpa_name} / {self.bpa_name}"
        return self.bpa_name


_NOT_APPLICABLE = object()


def _only(*kinds: str) -> Callable[[Callable[[Node], Any]], Callable[[Node], Any]]:
    def deco(fn: Callable[[Node], Any]) -> Callable[[Node], Any]:
        def wrapped(node: Node) -> Any:
            if node.kind not in kinds:
                return _NOT_APPLICABLE
            return fn(node)

        return wrapped

    return deco


# ── computed members (resolved before the plain property specs) ──────────


def _c_model(node: Node) -> Any:
    return node.view.model_node


def _c_table(node: Node) -> Any:
    if node.kind in ("Table",):
        return _NOT_APPLICABLE
    if node.table is None:
        return _NOT_APPLICABLE
    return node.table


def _c_parent(node: Node) -> Any:
    return node.parent if node.parent is not None else node.view.model_node


def _c_object_type(node: Node) -> Any:
    return EnumValue(node.kind if node.kind != "ModelRole" else "Role", "ObjectType")


_TYPE_NAMES = {
    "Table": "Table",
    "CalculatedTable": "Calculated Table",
    "CalculationGroupTable": "Calculation Group Table",
    "DataColumn": "Column",
    "CalculatedColumn": "Calculated Column",
    "CalculatedTableColumn": "Calculated Table Column",
    "Measure": "Measure",
    "Hierarchy": "Hierarchy",
    "Level": "Level",
    "Partition": "Partition",
    "Relationship": "Relationship",
    "Perspective": "Perspective",
    "Culture": "Translation",
    "ModelRole": "Role",
    "ModelRoleMember": "Role Member",
    "TablePermission": "Table Permission",
    "KPI": "KPI",
    "CalculationGroup": "Calculation Group Table",
    "CalculationItem": "Calculation Item",
    "NamedExpression": "Shared Expression",
    "ProviderDataSource": "Data Source",
    "StructuredDataSource": "Data Source",
    "Function": "Function",
    "Model": "Model",
}


def _c_object_type_name(node: Node) -> Any:
    if node.kind == "Table":
        if node.scope == "Table":
            mode = str(node.view.table_mode(node)).lower()
            if mode == "directquery":
                return "Table (DirectQuery)"
            if mode == "dual":
                return "Table (Dual)"
            return "Table"
        return _TYPE_NAMES.get(node.scope, node.scope)
    return _TYPE_NAMES.get(node.scope, node.kind)


def _c_dax_object_name(node: Node) -> Any:
    # Canonical DAX name quoting lives in core.dax.dax_names (deep review C-15); imported
    # lazily like the other core.dax uses here so loading BPA does not load the DAX package.
    from core.dax.dax_names import bracket, quote_table

    if node.kind == "Table":
        return quote_table(node.bpa_name)
    if node.kind in ("Column", "Measure"):
        return bracket(node.bpa_name)
    return _NOT_APPLICABLE


def _c_dax_object_full_name(node: Node) -> Any:
    from core.dax.dax_names import bracket, quote_table

    if node.kind == "Table":
        return quote_table(node.bpa_name)
    if node.kind in ("Column", "Measure") and node.table is not None:
        return quote_table(node.table.bpa_name) + bracket(node.bpa_name)
    return _NOT_APPLICABLE


def _c_annotations(node: Node) -> Any:
    return [
        {"name": a.get("name"), "value": text_of(a.get("value"))}
        for a in node.data.get("annotations") or []
        if isinstance(a, dict)
    ]


def _c_referenced_by(node: Node) -> Any:
    return node.view.referenced_by(node)


def _c_depends_on(node: Node) -> Any:
    return node.view.depends_on(node)


def _c_in_perspective(node: Node) -> Any:
    return _Indexer("InPerspective", lambda key: node.view.in_perspective(node, key))


@_only("Table")
def _c_columns(node: Node) -> Any:
    return node.view.columns_of(node)


@_only("Table")
def _c_measures(node: Node) -> Any:
    return node.view.measures_of(node)


@_only("Table")
def _c_hierarchies(node: Node) -> Any:
    return node.view.hierarchies_of(node)


@_only("Table")
def _c_partitions(node: Node) -> Any:
    return node.view.partitions_of(node)


@_only("Table")
def _c_calculation_group(node: Node) -> Any:
    return node if node.scope == "CalculationGroup" else None


@_only("Table")
def _c_calculation_items(node: Node) -> Any:
    return node.view.calc_items_of(node)


@_only("Table")
def _c_precedence(node: Node) -> Any:
    cg = node.data.get("calculationGroup") or {}
    return int(cg.get("precedence") or 0)


@_only("Table", "Column")
def _c_used_in_relationships(node: Node) -> Any:
    return node.view.relationships_using(node)


@_only("Column")
def _c_used_in_sort_by(node: Node) -> Any:
    return node.view.sorted_by_this(node)


@_only("Column")
def _c_used_in_hierarchies(node: Node) -> Any:
    return node.view.hierarchies_using(node)


@_only("Column")
def _c_used_in_variations(node: Node) -> Any:
    return node.view.variations_using(node)


@_only("Column")
def _c_variations(node: Node) -> Any:
    return [
        Node(node.view, "Variation", v, table=node.table, parent=node)
        for v in node.data.get("variations") or []
        if isinstance(v, dict)
    ]


@_only("Column")
def _c_sort_by_column(node: Node) -> Any:
    return (
        node.view.column(node.table, node.data.get("sortByColumn"))
        if node.data.get("sortByColumn")
        else None
    )


@_only("Column")
def _c_alternate_of(node: Node) -> Any:
    alt = node.data.get("alternateOf")
    return (
        Node(node.view, "AlternateOf", alt, table=node.table, parent=node, name="AlternateOf")
        if isinstance(alt, dict)
        else None
    )


@_only("Column")
def _c_type(node: Node) -> Any:
    return _enum(node.data.get("type"), "Data", "ColumnType")


@_only("Table", "Column")
def _c_rls(node: Node) -> Any:
    return node.view.table_rls(node) if node.kind == "Table" else _NOT_APPLICABLE


@_only("ModelRole")
def _c_role_rls(node: Node) -> Any:
    return node.view.role_rls(node)


@_only("Table", "Column")
def _c_ols(node: Node) -> Any:
    return node.view.ols(node)


@_only("Table")
def _c_source_expression(node: Node) -> Any:
    parts = node.view.partitions_of(node)
    return parts[0].bpa_get("Expression") if parts else DaxText("")


@_only("Table")
def _c_table_source_type(node: Node) -> Any:
    parts = node.view.partitions_of(node)
    return parts[0].bpa_get("SourceType") if parts else EnumValue("None", "PartitionSourceType")


@_only("Table")
def _c_table_expression(node: Node) -> Any:
    if node.scope != "CalculatedTable":
        return _NOT_APPLICABLE
    parts = node.view.partitions_of(node)
    return parts[0].bpa_get("Expression") if parts else DaxText("")


@_only("Hierarchy")
def _c_levels(node: Node) -> Any:
    return node.view.levels_of(node)


@_only("Level")
def _c_level_column(node: Node) -> Any:
    return node.view.column(node.table, node.data.get("column"))


@_only("Level")
def _c_level_hierarchy(node: Node) -> Any:
    return node.parent


@_only("Partition")
def _c_source_type(node: Node) -> Any:
    src = node.data.get("source") or {}
    raw = src.get("type") if isinstance(src, dict) else None
    return _enum(raw, "Query", "PartitionSourceType")


@_only("Partition")
def _c_query(node: Node) -> Any:
    return node.view.partition_text(node)


@_only("Partition")
def _c_partition_source(node: Node) -> Any:
    src = node.data.get("source") if isinstance(node.data.get("source"), dict) else {}
    s = Node(node.view, "PartitionSource", src, table=node.table, parent=node, name=node.bpa_name)
    s.extra.update(
        {
            "type": _c_source_type(node),
            "expression": node.view.partition_text(node),
            "query": node.view.partition_text(node),
            "datasource": lambda: node.view.data_source(src.get("dataSource")),
        }
    )
    return s


@_only("Partition")
def _c_partition_data_source(node: Node) -> Any:
    src = node.data.get("source") or {}
    return node.view.data_source(src.get("dataSource") if isinstance(src, dict) else None)


@_only("Relationship")
def _c_from_table(node: Node) -> Any:
    return node.view.table(node.data.get("fromTable"))


@_only("Relationship")
def _c_to_table(node: Node) -> Any:
    return node.view.table(node.data.get("toTable"))


@_only("Relationship")
def _c_from_column(node: Node) -> Any:
    return node.view.column(
        node.view.table(node.data.get("fromTable")), node.data.get("fromColumn")
    )


@_only("Relationship")
def _c_to_column(node: Node) -> Any:
    return node.view.column(node.view.table(node.data.get("toTable")), node.data.get("toColumn"))


@_only("Measure")
def _c_kpi(node: Node) -> Any:
    return node.view.kpi_of(node)


@_only("KPI")
def _c_kpi_measure(node: Node) -> Any:
    return node.parent


@_only("KPI")
def _c_kpi_name(node: Node) -> Any:
    return node.bpa_name


@_only("CalculationItem")
def _c_calc_group(node: Node) -> Any:
    return node.table


@_only("ModelRole")
def _c_members(node: Node) -> Any:
    return node.view.members_of(node)


@_only("ModelRole")
def _c_table_permissions(node: Node) -> Any:
    return node.view.permissions_of(node)


@_only("TablePermission")
def _c_perm_role(node: Node) -> Any:
    return node.parent


@_only("TablePermission")
def _c_perm_name(node: Node) -> Any:
    return node.bpa_name


@_only("DataSource")
def _c_used_by_partitions(node: Node) -> Any:
    return node.view.partitions_using_source(node)


@_only("Model")
def _c_model_tables(node: Node) -> Any:
    return node.view.tables


@_only("Model")
def _c_all_columns(node: Node) -> Any:
    return node.view.all_columns


@_only("Model")
def _c_all_measures(node: Node) -> Any:
    return node.view.all_measures


@_only("Model")
def _c_all_hierarchies(node: Node) -> Any:
    return node.view.all_hierarchies


@_only("Model")
def _c_all_levels(node: Node) -> Any:
    return node.view.all_levels


@_only("Model")
def _c_all_partitions(node: Node) -> Any:
    return node.view.all_partitions


@_only("Model")
def _c_all_calc_items(node: Node) -> Any:
    return node.view.all_calc_items


@_only("Model")
def _c_relationships(node: Node) -> Any:
    return node.view.relationships


@_only("Model")
def _c_perspectives(node: Node) -> Any:
    return node.view.perspectives


@_only("Model")
def _c_cultures(node: Node) -> Any:
    return node.view.cultures


@_only("Model")
def _c_roles(node: Node) -> Any:
    return node.view.roles


@_only("Model")
def _c_expressions(node: Node) -> Any:
    return node.view.expressions


@_only("Model")
def _c_data_sources(node: Node) -> Any:
    return node.view.data_sources


@_only("Model")
def _c_functions(node: Node) -> Any:
    return node.view.functions


@_only("Table")
def _c_calendars(node: Node) -> Any:
    """Calendar objects of a table (Sep 2025 calendar-based time intelligence)."""
    return [
        Node(node.view, "Calendar", c, table=node, parent=node)
        for c in node.data.get("calendars") or []
        if isinstance(c, dict)
    ]


@_only("Model")
def _c_calc_groups(node: Node) -> Any:
    return [t for t in node.view.tables if t.scope == "CalculationGroup"]


_COMPUTED: Dict[str, Callable[[Node], Any]] = {
    "model": _c_model,
    "table": _c_table,
    "parent": _c_parent,
    "objecttype": _c_object_type,
    "objecttypename": _c_object_type_name,
    "daxobjectname": _c_dax_object_name,
    "daxobjectfullname": _c_dax_object_full_name,
    "annotations": _c_annotations,
    "referencedby": _c_referenced_by,
    "dependson": _c_depends_on,
    "inperspective": _c_in_perspective,
    "columns": _c_columns,
    "measures": _c_measures,
    "hierarchies": _c_hierarchies,
    "partitions": _c_partitions,
    "calculationgroup": _c_calculation_group,
    "calculationitems": _c_calculation_items,
    "precedence": _c_precedence,
    "usedinrelationships": _c_used_in_relationships,
    "usedinsortby": _c_used_in_sort_by,
    "usedinhierarchies": _c_used_in_hierarchies,
    "usedinvariations": _c_used_in_variations,
    "variations": _c_variations,
    "sortbycolumn": _c_sort_by_column,
    "alternateof": _c_alternate_of,
    "type": _c_type,
    "rowlevelsecurity": lambda n: _c_rls(n) if n.kind != "ModelRole" else _c_role_rls(n),
    "objectlevelsecurity": _c_ols,
    "sourceexpression": _c_source_expression,
    "sourcetype": lambda n: _c_table_source_type(n) if n.kind == "Table" else _c_source_type(n),
    "expression": lambda n: (
        _c_table_expression(n)
        if n.kind == "Table"
        else (_c_query(n) if n.kind == "Partition" else _NOT_APPLICABLE)
    ),
    "query": _c_query,
    "source": _c_partition_source,
    "datasource": _c_partition_data_source,
    "levels": _c_levels,
    "column": _c_level_column,
    "hierarchy": _c_level_hierarchy,
    "fromtable": _c_from_table,
    "totable": _c_to_table,
    "fromcolumn": _c_from_column,
    "tocolumn": _c_to_column,
    "kpi": _c_kpi,
    "measure": _c_kpi_measure,
    "calculationgroupname": _c_calc_group,
    "members": _c_members,
    "tablepermissions": _c_table_permissions,
    "role": _c_perm_role,
    "usedbypartitions": _c_used_by_partitions,
    "tables": _c_model_tables,
    "allcolumns": _c_all_columns,
    "allmeasures": _c_all_measures,
    "allhierarchies": _c_all_hierarchies,
    "alllevels": _c_all_levels,
    "allpartitions": _c_all_partitions,
    "allcalculationitems": _c_all_calc_items,
    "relationships": _c_relationships,
    "allrelationships": _c_relationships,
    "perspectives": _c_perspectives,
    "cultures": _c_cultures,
    "roles": _c_roles,
    "expressions": _c_expressions,
    "datasources": _c_data_sources,
    "functions": _c_functions,
    "calendars": _c_calendars,
    "calculationgroups": _c_calc_groups,
}


def _c_name(node: Node) -> Any:
    if node.kind in ("KPI", "TablePermission"):
        return node.bpa_name
    return _NOT_APPLICABLE


_COMPUTED["name"] = _c_name


# ───────────────────────────── scopes ──────────────────────────────────

SUPPORTED_SCOPES = (
    "Model",
    "Table",
    "CalculatedTable",
    "CalculationGroup",
    "DataColumn",
    "CalculatedColumn",
    "CalculatedTableColumn",
    "Measure",
    "Hierarchy",
    "Level",
    "Partition",
    "Relationship",
    "Perspective",
    "Culture",
    "ModelRole",
    "ModelRoleMember",
    "TablePermission",
    "KPI",
    "CalculationItem",
    "NamedExpression",
    "ProviderDataSource",
    "StructuredDataSource",
    "Function",
)

#: view ``Node.kind`` -> the ``core.operations.tom_properties`` object kind (patchable objects).
TOM_KIND_OF = {
    "Table": "table",
    "Column": "column",
    "Measure": "measure",
    "Hierarchy": "hierarchy",
    "Relationship": "relationship",
    "Partition": "partition",
    "ModelRole": "role",
    "NamedExpression": "named_expression",
    "Perspective": "perspective",
    "DataSource": "data_source",
}

_SCOPE_ALIASES = {
    "column": "DataColumn",
    "datacolumn": "DataColumn",
    "calculatedcolumn": "CalculatedColumn",
    "calculatedtablecolumn": "CalculatedTableColumn",
    "table": "Table",
    "calculatedtable": "CalculatedTable",
    "calculationgroup": "CalculationGroup",
    "calculationgrouptable": "CalculationGroup",
    "measure": "Measure",
    "hierarchy": "Hierarchy",
    "level": "Level",
    "partition": "Partition",
    "relationship": "Relationship",
    "singlecolumnrelationship": "Relationship",
    "perspective": "Perspective",
    "culture": "Culture",
    "modelrole": "ModelRole",
    "role": "ModelRole",
    "modelrolemember": "ModelRoleMember",
    "tablepermission": "TablePermission",
    "kpi": "KPI",
    "calculationitem": "CalculationItem",
    "namedexpression": "NamedExpression",
    "expression": "NamedExpression",
    "providerdatasource": "ProviderDataSource",
    "structureddatasource": "StructuredDataSource",
    "function": "Function",
    "userdefinedfunction": "Function",
    "model": "Model",
}


def normalize_scope(scope: str) -> Optional[str]:
    """TE scope name -> canonical scope, or ``None`` when unsupported.

    ``DataSource`` expands to both provider and structured sources.
    """
    s = str(scope or "").strip()
    if not s:
        return None
    return _SCOPE_ALIASES.get(s.lower())


# ───────────────────────────── model view ──────────────────────────────


def resolve_tmsl_model(tmsl: Any) -> Optional[Dict[str, Any]]:
    """Return the model object from the TMSL shapes callers pass."""
    if isinstance(tmsl, (str, bytes)):
        try:
            tmsl = json.loads(tmsl)
        except (ValueError, TypeError):
            return None
    if not isinstance(tmsl, dict):
        return None
    model = (tmsl.get("create", {}) or {}).get("database", {}) or {}
    model = model.get("model") if isinstance(model, dict) else None
    if isinstance(model, dict) and model:
        return model
    if isinstance(tmsl.get("model"), dict) and tmsl.get("model"):
        return tmsl["model"]
    if any(k in tmsl for k in ("tables", "relationships", "roles", "expressions")):
        return tmsl
    return None


class ModelView:
    """Builds the node graph for one TMSL model (one per analysis run)."""

    def __init__(self, model: Dict[str, Any]) -> None:
        self.model = model if isinstance(model, dict) else {}
        self.model_node = Node(
            self, "Model", self.model, name=text_of(self.model.get("name")) or "Model"
        )
        self.tables: List[Node] = []
        self._tables_by_norm: Dict[str, Node] = {}
        self._columns: Dict[int, List[Node]] = {}
        self._all_columns_incl_rownumber: Dict[int, List[Node]] = {}
        self._measures: Dict[int, List[Node]] = {}
        self._hierarchies: Dict[int, List[Node]] = {}
        self._levels: Dict[int, List[Node]] = {}
        self._partitions: Dict[int, List[Node]] = {}
        self._calc_items: Dict[int, List[Node]] = {}
        self._kpis: Dict[int, Optional[Node]] = {}
        self._col_by_norm: Dict[Tuple[int, str], Node] = {}
        self._measure_by_norm: Dict[str, Node] = {}
        self.all_columns: List[Node] = []
        self.all_measures: List[Node] = []
        self.all_hierarchies: List[Node] = []
        self.all_levels: List[Node] = []
        self.all_partitions: List[Node] = []
        self.all_calc_items: List[Node] = []
        self.all_kpis: List[Node] = []
        self._build_tables()
        self.relationships: List[Node] = [
            Node(
                self,
                "Relationship",
                r,
                name=text_of(r.get("name")) or f"{r.get('fromTable')}.{r.get('fromColumn')}",
            )
            for r in self.model.get("relationships") or []
            if isinstance(r, dict)
        ]
        self.perspectives = [
            Node(self, "Perspective", p)
            for p in self.model.get("perspectives") or []
            if isinstance(p, dict)
        ]
        self.cultures = [
            Node(self, "Culture", c)
            for c in self.model.get("cultures") or []
            if isinstance(c, dict)
        ]
        self.roles = [
            Node(self, "ModelRole", r) for r in self.model.get("roles") or [] if isinstance(r, dict)
        ]
        self.expressions = [
            Node(self, "NamedExpression", e)
            for e in self.model.get("expressions") or []
            if isinstance(e, dict)
        ]
        self.data_sources = []
        for ds in self.model.get("dataSources") or []:
            if not isinstance(ds, dict):
                continue
            scope = (
                "StructuredDataSource"
                if str(ds.get("type") or "").lower() == "structured"
                else "ProviderDataSource"
            )
            self.data_sources.append(Node(self, "DataSource", ds, scope=scope))
        self.functions = [
            Node(self, "Function", f)
            for f in self.model.get("functions") or []
            if isinstance(f, dict)
        ]
        self._members: Dict[int, List[Node]] = {}
        self._permissions: Dict[int, List[Node]] = {}
        for role in self.roles:
            self._members[id(role)] = [
                Node(self, "ModelRoleMember", m, parent=role, name=text_of(m.get("memberName")))
                for m in role.data.get("members") or []
                if isinstance(m, dict)
            ]
            perms = []
            for p in role.data.get("tablePermissions") or []:
                if not isinstance(p, dict):
                    continue
                tname = text_of(p.get("name"))
                perms.append(
                    Node(
                        self, "TablePermission", p, table=self.table(tname), parent=role, name=tname
                    )
                )
            self._permissions[id(role)] = perms
        self._rels_by_table: Dict[int, List[Node]] = {}
        self._rels_by_column: Dict[int, List[Node]] = {}
        for rel in self.relationships:
            for side in ("from", "to"):
                t = self.table(rel.data.get(f"{side}Table"))
                if t is None:
                    continue
                self._rels_by_table.setdefault(id(t), [])
                if rel not in self._rels_by_table[id(t)]:
                    self._rels_by_table[id(t)].append(rel)
                c = self.column(t, rel.data.get(f"{side}Column"))
                if c is not None:
                    self._rels_by_column.setdefault(id(c), []).append(rel)
        self._sorted_by: Optional[Dict[int, List[Node]]] = None
        self._hier_using: Optional[Dict[int, List[Node]]] = None
        self._deps: Optional[Dict[int, List[Node]]] = None
        self._refs: Optional[Dict[int, RefList]] = None

    # construction -------------------------------------------------------
    def _build_tables(self) -> None:
        for t in self.model.get("tables") or []:
            if not isinstance(t, dict):
                continue
            parts = t.get("partitions") or []
            first_src = (
                (parts[0] or {}).get("source") if parts and isinstance(parts[0], dict) else None
            )
            src_type = (
                str((first_src or {}).get("type") or "").lower()
                if isinstance(first_src, dict)
                else ""
            )
            if isinstance(t.get("calculationGroup"), dict):
                scope = "CalculationGroup"
            elif src_type == "calculated":
                scope = "CalculatedTable"
            else:
                scope = "Table"
            node = Node(self, "Table", t, scope=scope)
            self.tables.append(node)
            self._tables_by_norm.setdefault(_norm(node.bpa_name), node)
            cols: List[Node] = []
            every: List[Node] = []
            for c in t.get("columns") or []:
                if not isinstance(c, dict):
                    continue
                ctype = str(c.get("type") or "data").lower()
                cscope = {
                    "calculated": "CalculatedColumn",
                    "calculatedtablecolumn": "CalculatedTableColumn",
                    "rownumber": "RowNumber",
                }.get(ctype, "DataColumn")
                cn = Node(self, "Column", c, scope=cscope, table=node, parent=node)
                every.append(cn)
                if cscope == "RowNumber":
                    continue  # never user-visible; excluded from Columns like TE
                cols.append(cn)
                self._col_by_norm.setdefault((id(node), _norm(cn.bpa_name)), cn)
            self._columns[id(node)] = cols
            self._all_columns_incl_rownumber[id(node)] = every
            self.all_columns.extend(cols)
            meas = []
            for m in t.get("measures") or []:
                if not isinstance(m, dict):
                    continue
                mn = Node(self, "Measure", m, table=node, parent=node)
                meas.append(mn)
                self._measure_by_norm.setdefault(_norm(mn.bpa_name), mn)
                kpi = m.get("kpi")
                if isinstance(kpi, dict):
                    kn = Node(self, "KPI", kpi, table=node, parent=mn, name=mn.bpa_name)
                    self._kpis[id(mn)] = kn
                    self.all_kpis.append(kn)
            self._measures[id(node)] = meas
            self.all_measures.extend(meas)
            hiers = []
            for h in t.get("hierarchies") or []:
                if not isinstance(h, dict):
                    continue
                hn = Node(self, "Hierarchy", h, table=node, parent=node)
                hiers.append(hn)
                lv = [
                    Node(self, "Level", lvl, table=node, parent=hn)
                    for lvl in h.get("levels") or []
                    if isinstance(lvl, dict)
                ]
                self._levels[id(hn)] = lv
                self.all_levels.extend(lv)
            self._hierarchies[id(node)] = hiers
            self.all_hierarchies.extend(hiers)
            pl = [
                Node(self, "Partition", p, table=node, parent=node)
                for p in parts
                if isinstance(p, dict)
            ]
            self._partitions[id(node)] = pl
            self.all_partitions.extend(pl)
            items = []
            cg = t.get("calculationGroup")
            if isinstance(cg, dict):
                items = [
                    Node(self, "CalculationItem", ci, table=node, parent=node)
                    for ci in cg.get("calculationItems") or []
                    if isinstance(ci, dict)
                ]
            self._calc_items[id(node)] = items
            self.all_calc_items.extend(items)

    # lookups ------------------------------------------------------------
    def table(self, name: Any) -> Optional[Node]:
        if name is None:
            return None
        if isinstance(name, Node):
            return name if name.kind == "Table" else None
        return self._tables_by_norm.get(_norm(name))

    def column(self, table: Optional[Node], name: Any) -> Optional[Node]:
        if table is None or name is None:
            return None
        return self._col_by_norm.get((id(table), _norm(name)))

    def measure(self, name: Any) -> Optional[Node]:
        return self._measure_by_norm.get(_norm(name))

    def columns_of(self, table: Node) -> List[Node]:
        return self._columns.get(id(table), [])

    def measures_of(self, table: Node) -> List[Node]:
        return self._measures.get(id(table), [])

    def hierarchies_of(self, table: Node) -> List[Node]:
        return self._hierarchies.get(id(table), [])

    def levels_of(self, hierarchy: Node) -> List[Node]:
        return self._levels.get(id(hierarchy), [])

    def partitions_of(self, table: Node) -> List[Node]:
        return self._partitions.get(id(table), [])

    def calc_items_of(self, table: Node) -> List[Node]:
        return self._calc_items.get(id(table), [])

    def kpi_of(self, measure: Node) -> Optional[Node]:
        return self._kpis.get(id(measure))

    def members_of(self, role: Node) -> List[Node]:
        return self._members.get(id(role), [])

    def permissions_of(self, role: Node) -> List[Node]:
        return self._permissions.get(id(role), [])

    def data_source(self, name: Any) -> Optional[Node]:
        if not name:
            return None
        for ds in self.data_sources:
            if _norm(ds.bpa_name) == _norm(name):
                return ds
        return None

    def table_mode(self, table: Node) -> str:
        parts = self.partitions_of(table)
        mode = (
            (parts[0].data.get("mode") if parts else None)
            or self.model.get("defaultMode")
            or "import"
        )
        return str(mode)

    def partition_text(self, partition: Node) -> str:
        src = partition.data.get("source")
        if not isinstance(src, dict):
            return MText("")
        raw = src.get("expression")
        if raw is None:
            raw = src.get("query")
        text = text_of(raw)
        stype = str(src.get("type") or "").lower()
        if stype in ("calculated", "calculationgroup"):
            return DaxText(text)
        if stype == "m":
            return MText(text)
        return text

    # derived collections -------------------------------------------------
    def relationships_using(self, node: Node) -> List[Node]:
        if node.kind == "Table":
            return self._rels_by_table.get(id(node), [])
        return self._rels_by_column.get(id(node), [])

    def sorted_by_this(self, column: Node) -> List[Node]:
        if self._sorted_by is None:
            index: Dict[int, List[Node]] = {}
            for c in self.all_columns:
                target = (
                    self.column(c.table, c.data.get("sortByColumn"))
                    if c.data.get("sortByColumn")
                    else None
                )
                if target is not None:
                    index.setdefault(id(target), []).append(c)
            self._sorted_by = index
        return self._sorted_by.get(id(column), [])

    def hierarchies_using(self, column: Node) -> List[Node]:
        if self._hier_using is None:
            index: Dict[int, List[Node]] = {}
            for h in self.all_hierarchies:
                for lvl in self.levels_of(h):
                    c = self.column(h.table, lvl.data.get("column"))
                    if c is not None and h not in index.setdefault(id(c), []):
                        index[id(c)].append(h)
            self._hier_using = index
        return self._hier_using.get(id(column), [])

    def variations_using(self, column: Node) -> List[Node]:
        out = []
        for c in self.all_columns:
            for v in c.data.get("variations") or []:
                if not isinstance(v, dict):
                    continue
                dh = v.get("defaultHierarchy") or {}
                hier_table = self.table(dh.get("table")) if isinstance(dh, dict) else None
                if hier_table is None:
                    continue
                for h in self.hierarchies_of(hier_table):
                    if _norm(h.bpa_name) == _norm(
                        dh.get("hierarchy")
                    ) and h in self.hierarchies_using(column):
                        out.append(Node(self, "Variation", v, table=c.table, parent=c))
        return out

    def table_rls(self, table: Node) -> _IndexedList:
        """Filter expression per role that has a permission on *table* (None when
        the permission carries no row filter, e.g. OLS only). Indexable by role."""
        values: List[Optional[str]] = []
        by_role: Dict[str, Optional[str]] = {}
        for role in self.roles:
            for perm in self.permissions_of(role):
                if perm.table is table:
                    text = text_of(perm.data.get("filterExpression"))
                    expr = DaxText(text) if text else None
                    values.append(expr)
                    by_role[_norm(role.bpa_name)] = expr
        return _IndexedList(
            values, lambda key: by_role.get(_norm(key.bpa_name if isinstance(key, Node) else key))
        )

    def role_rls(self, role: Node) -> _IndexedList:
        """Filter expression per table the role has a permission on. Indexable by table."""
        values: List[Optional[str]] = []
        by_table: Dict[str, Optional[str]] = {}
        for p in self.permissions_of(role):
            text = text_of(p.data.get("filterExpression"))
            expr = DaxText(text) if text else None
            values.append(expr)
            by_table[_norm(p.bpa_name)] = expr
        return _IndexedList(
            values, lambda key: by_table.get(_norm(key.bpa_name if isinstance(key, Node) else key))
        )

    def ols(self, node: Node) -> _IndexedList:
        values: List[EnumValue] = []
        by_role: Dict[str, EnumValue] = {}
        table = node if node.kind == "Table" else node.table
        for role in self.roles:
            value = EnumValue("Default", "MetadataPermission")
            for perm in self.permissions_of(role):
                if perm.table is not table:
                    continue
                if node.kind == "Table":
                    value = _enum(
                        perm.data.get("metadataPermission"), "Default", "MetadataPermission"
                    )
                else:
                    for cp in perm.data.get("columnPermissions") or []:
                        if isinstance(cp, dict) and _norm(cp.get("name")) == _norm(node.bpa_name):
                            value = _enum(
                                cp.get("metadataPermission"), "Default", "MetadataPermission"
                            )
            values.append(value)
            by_role[_norm(role.bpa_name)] = value
        return _IndexedList(values, lambda key: by_role.get(_norm(key), EnumValue("Default")))

    def in_perspective(self, node: Node, perspective: Any) -> bool:
        pname = perspective.bpa_name if isinstance(perspective, Node) else perspective
        for p in self.perspectives:
            if _norm(p.bpa_name) != _norm(pname):
                continue
            for pt in p.data.get("tables") or []:
                if not isinstance(pt, dict):
                    continue
                table = node if node.kind == "Table" else node.table
                if table is None or _norm(pt.get("name")) != _norm(table.bpa_name):
                    continue
                if node.kind == "Table":
                    return True
                key = {"Column": "columns", "Measure": "measures", "Hierarchy": "hierarchies"}.get(
                    node.kind
                )
                if key is None:
                    return True
                return any(
                    isinstance(o, dict) and _norm(o.get("name")) == _norm(node.bpa_name)
                    for o in pt.get(key) or []
                )
            return False
        raise BpaRuntimeError(f"perspective {pname!r} not found")

    def partitions_using_source(self, ds: Node) -> List[Node]:
        out = []
        for p in self.all_partitions:
            src = p.data.get("source")
            if isinstance(src, dict) and _norm(src.get("dataSource")) == _norm(ds.bpa_name):
                out.append(p)
        return out

    # dependencies ---------------------------------------------------------
    def _expression_owners(self) -> Iterator[Tuple[Node, str, Optional[Node]]]:
        """(owner, DAX text, home table for unqualified column refs)."""
        for m in self.all_measures:
            parts = [
                text_of(m.data.get("expression")),
                text_of(_dig(m.data, "formatStringDefinition.expression")),
                text_of(_dig(m.data, "detailRowsDefinition.expression")),
            ]
            kpi = self.kpi_of(m)
            if kpi is not None:
                parts.extend(
                    text_of(kpi.data.get(k))
                    for k in ("targetExpression", "statusExpression", "trendExpression")
                )
            yield m, "\n".join(p for p in parts if p), m.table
        for c in self.all_columns:
            if c.scope == "CalculatedColumn":
                yield c, text_of(c.data.get("expression")), c.table
        for t in self.tables:
            if t.scope == "CalculatedTable":
                parts = self.partitions_of(t)
                if parts:
                    yield t, str(self.partition_text(parts[0])), None
        for ci in self.all_calc_items:
            yield ci, "\n".join(
                p
                for p in (
                    text_of(ci.data.get("expression")),
                    text_of(_dig(ci.data, "formatStringDefinition.expression")),
                )
                if p
            ), None
        for role in self.roles:
            for perm in self.permissions_of(role):
                yield perm, text_of(perm.data.get("filterExpression")), perm.table
        for fn in self.functions:
            yield fn, text_of(fn.data.get("expression")), None

    def _build_dependencies(self) -> None:
        from core.dax.dax_reference_parser import scan_references

        deps: Dict[int, List[Node]] = {}
        refs: Dict[int, RefList] = {}
        known_tables = set(self._tables_by_norm)
        columns_by_name: Dict[str, List[Node]] = {}
        for c in self.all_columns:
            columns_by_name.setdefault(_norm(c.bpa_name), []).append(c)
        for owner, text, home in self._expression_owners():
            if not text:
                deps[id(owner)] = []
                continue
            try:
                found = scan_references(text, known_tables=known_tables)
            except Exception:  # noqa: BLE001 — a lexer failure must not kill the run
                found = []
            grouped: Dict[int, Tuple[Node, List[bool]]] = {}
            for ref in found:
                target: Optional[Node] = None
                qualified = False
                if ref.kind == "qualified":
                    t = self.table(ref.table)
                    target = self.column(t, ref.name)
                    if target is None:
                        target = self.measure(ref.name)
                    qualified = True
                elif ref.kind == "bracket":
                    target = self.measure(ref.name)
                    if target is None and home is not None:
                        target = self.column(home, ref.name)
                    if target is None:
                        candidates = columns_by_name.get(_norm(ref.name)) or []
                        target = candidates[0] if len(candidates) == 1 else None
                elif ref.kind == "table":
                    target = self.table(ref.table)
                    qualified = True
                if target is None or target is owner:
                    continue
                entry = grouped.setdefault(id(target), (target, []))
                entry[1].append(qualified)
            pairs = []
            for target, flags in grouped.values():
                kv = Node(self, "DependsOnEntry", {}, name=target.bpa_name)
                occurrences = [_occurrence(self, flag) for flag in flags]
                kv.extra.update({"key": target, "value": occurrences})
                pairs.append(kv)
                refs.setdefault(id(target), RefList()).append(owner)
            deps[id(owner)] = pairs
        self._deps = deps
        self._refs = refs

    def depends_on(self, node: Node) -> List[Node]:
        if self._deps is None:
            self._build_dependencies()
        assert self._deps is not None
        return self._deps.get(id(node), [])

    def referenced_by(self, node: Node) -> RefList:
        if self._refs is None:
            self._build_dependencies()
        assert self._refs is not None
        return self._refs.get(id(node), RefList())

    # scope iteration -------------------------------------------------------
    def iter_scope(self, scope: str) -> Iterator[Node]:
        if scope == "Model":
            yield self.model_node
        elif scope in ("Table", "CalculatedTable", "CalculationGroup"):
            yield from (t for t in self.tables if t.scope == scope)
        elif scope in ("DataColumn", "CalculatedColumn", "CalculatedTableColumn"):
            yield from (c for c in self.all_columns if c.scope == scope)
        elif scope == "Measure":
            yield from self.all_measures
        elif scope == "Hierarchy":
            yield from self.all_hierarchies
        elif scope == "Level":
            yield from self.all_levels
        elif scope == "Partition":
            # Calculation-group tables carry a synthetic partition; TE skips it.
            yield from (
                p
                for p in self.all_partitions
                if p.table is not None and p.table.scope != "CalculationGroup"
            )
        elif scope == "Relationship":
            yield from self.relationships
        elif scope == "Perspective":
            yield from self.perspectives
        elif scope == "Culture":
            yield from self.cultures
        elif scope == "ModelRole":
            yield from self.roles
        elif scope == "ModelRoleMember":
            for r in self.roles:
                yield from self.members_of(r)
        elif scope == "TablePermission":
            for r in self.roles:
                yield from self.permissions_of(r)
        elif scope == "KPI":
            yield from self.all_kpis
        elif scope == "CalculationItem":
            yield from self.all_calc_items
        elif scope == "NamedExpression":
            yield from self.expressions
        elif scope in ("ProviderDataSource", "StructuredDataSource"):
            yield from (d for d in self.data_sources if d.scope == scope)
        elif scope == "Function":
            yield from self.functions


def _occurrence(view: ModelView, qualified: bool) -> Node:
    occ = Node(view, "ObjectReference", {}, name="reference")
    occ.extra["fullyqualified"] = qualified
    return occ
