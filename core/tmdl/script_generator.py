"""
TMDL Script Generator - Programmatic TMDL generation

Generate TMDL scripts for tables (columns + partitions), measures,
relationships and calculation groups.

Review G-24 (2026-09-22): the output is real TMDL now — ``measure 'X' =``
with the body at +2 indent (it emitted ``expression:`` which TOM rejects),
``///`` descriptions (TMDL has no ``description:`` property), names emitted
through :func:`core.tmdl.tmdl_tree.tmdl_name` (``''`` escapes, quoting when
needed), properties before child objects, columns bound with
``sourceColumn``. Every shape round-trips through
:class:`core.tmdl.unified_parser.UnifiedTmdlParser` and the TOM serializer.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .tmdl_tree import format_object_ref, tmdl_name

logger = logging.getLogger(__name__)


@dataclass
class ColumnDef:
    """Column definition"""

    name: str
    data_type: str
    format_string: Optional[str] = None
    is_hidden: bool = False
    display_folder: Optional[str] = None
    summarize_by: str = "sum"
    source_column: Optional[str] = None
    description: Optional[str] = None
    expression: Optional[str] = None  # calculated column


@dataclass
class PartitionDef:
    """Partition definition"""

    name: str
    mode: str = "import"  # import, directQuery, dual
    source_expression: str = ""
    kind: str = "m"  # m | calculated


@dataclass
class CalcItemDef:
    """Calculation item definition"""

    name: str
    expression: str
    ordinal: int
    format_string: Optional[str] = None  # a formatStringDefinition DAX expression


def emit_description(text: Optional[str], level: int) -> List[str]:
    """``///`` lines for *text* at *level*."""
    if not text:
        return []
    tabs = "\t" * level
    return [f"{tabs}/// {ln}".rstrip() for ln in str(text).splitlines()]


def emit_expression(header: str, expression: Optional[str], level: int) -> List[str]:
    """``header = expression`` with the TMDL body rule (continuation at +2)."""
    tabs = "\t" * level
    if expression is None or not str(expression).strip():
        return [f"{tabs}{header}"]
    lines = str(expression).replace("\r\n", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) == 1 and not lines[0].lstrip().startswith("```"):
        return [f"{tabs}{header} = {lines[0].strip()}"]
    indents = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    common = min(indents) if indents else 0
    body = "\t" * (level + 2)
    out = [f"{tabs}{header} ="]
    out.extend(body + ln[common:] if ln.strip() else "" for ln in lines)
    return out


class TmdlScriptGenerator:
    """Generate TMDL scripts programmatically."""

    def __init__(self):
        """Initialize script generator"""

    def generate_table(
        self,
        name: str,
        columns: List[ColumnDef],
        partitions: Optional[List[PartitionDef]] = None,
        description: Optional[str] = None,
    ) -> str:
        """TMDL for a table (level 0)."""
        parts: List[str] = emit_description(description, 0)
        parts.append(f"table {tmdl_name(name)}")
        parts.append(f"\tlineageTag: {self._generate_lineage_tag()}")
        for col in columns:
            parts.append("")
            parts.extend(self._generate_column(col))
        for partition in partitions or []:
            parts.append("")
            parts.extend(self._generate_partition(partition))
        return "\n".join(parts) + "\n"

    def _generate_column(self, col: ColumnDef) -> List[str]:
        lines = emit_description(col.description, 1)
        header = f"column {tmdl_name(col.name)}"
        lines.extend(
            emit_expression(header, col.expression, 1) if col.expression else [f"\t{header}"]
        )
        lines.append(f"\t\tdataType: {col.data_type}")
        if col.is_hidden:
            lines.append("\t\tisHidden")
        if col.format_string:
            lines.append(f"\t\tformatString: {col.format_string}")
        if col.display_folder:
            lines.append(f"\t\tdisplayFolder: {col.display_folder}")
        lines.append(f"\t\tlineageTag: {self._generate_lineage_tag()}")
        lines.append(f"\t\tsummarizeBy: {col.summarize_by}")
        if not col.expression:
            lines.append(f"\t\tsourceColumn: {col.source_column or col.name}")
        return lines

    def _generate_partition(self, partition: PartitionDef) -> List[str]:
        lines = [f"\tpartition {tmdl_name(partition.name)} = {partition.kind or 'm'}"]
        lines.append(f"\t\tmode: {partition.mode}")
        if partition.source_expression:
            lines.extend(emit_expression("source", partition.source_expression, 2))
        return lines

    def generate_measure(
        self,
        table: str,
        name: str,
        expression: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> str:
        """TMDL for a measure at level 0 (indent every line by one tab to embed it
        in a ``table`` block). Properties: formatString, displayFolder, isHidden,
        description, formatStringDefinition, dataCategory."""
        props = properties or {}
        lines = emit_description(props.get("description"), 0)
        lines.extend(emit_expression(f"measure {tmdl_name(name)}", expression, 0))
        if props.get("formatString"):
            lines.append(f"\tformatString: {props['formatString']}")
        if props.get("displayFolder"):
            lines.append(f"\tdisplayFolder: {props['displayFolder']}")
        if props.get("dataCategory"):
            lines.append(f"\tdataCategory: {props['dataCategory']}")
        if props.get("isHidden"):
            lines.append("\tisHidden")
        lines.append(f"\tlineageTag: {self._generate_lineage_tag()}")
        if props.get("formatStringDefinition"):
            lines.append("")
            lines.extend(
                emit_expression("formatStringDefinition", props["formatStringDefinition"], 1)
            )
        return "\n".join(lines) + "\n"

    def generate_relationship(
        self,
        from_table: str,
        from_column: str,
        to_table: str,
        to_column: str,
        cardinality: str = "many-to-one",
        cross_filter_direction: str = "single",
        is_active: bool = True,
    ) -> str:
        """TMDL for a relationship (``relationships.tmdl`` entry)."""
        lines = [f"relationship {uuid.uuid4()}"]
        if not is_active:
            lines.append("\tisActive: false")
        if cross_filter_direction == "both":
            lines.append("\tcrossFilteringBehavior: bothDirections")
        card = {
            "many-to-one": ("many", "one"),
            "one-to-many": ("one", "many"),
            "one-to-one": ("one", "one"),
            "many-to-many": ("many", "many"),
        }.get(cardinality)
        if card is None:
            raise ValueError(f"Unsupported cardinality: {cardinality}")
        if card[0] != "many":
            lines.append(f"\tfromCardinality: {card[0]}")
        if card[1] != "one":
            lines.append(f"\ttoCardinality: {card[1]}")
        lines.append(f"\tfromColumn: {format_object_ref(from_table, from_column)}")
        lines.append(f"\ttoColumn: {format_object_ref(to_table, to_column)}")
        return "\n".join(lines) + "\n"

    def generate_calculation_group(
        self,
        name: str,
        items: List[CalcItemDef],
        description: Optional[str] = None,
        precedence: Optional[int] = None,
    ) -> str:
        """TMDL for a calculation-group table."""
        lines = emit_description(description, 0)
        lines.append(f"table {tmdl_name(name)}")
        lines.append(f"\tlineageTag: {self._generate_lineage_tag()}")
        lines.append("")
        lines.append("\tcalculationGroup")
        if precedence is not None:
            lines.append(f"\t\tprecedence: {int(precedence)}")
        for item in sorted(items, key=lambda x: x.ordinal):
            lines.append("")
            lines.extend(self._generate_calculation_item(item))
        lines.append("")
        lines.append("\tcolumn Name")
        lines.append("\t\tdataType: string")
        lines.append(f"\t\tlineageTag: {self._generate_lineage_tag()}")
        lines.append("\t\tsummarizeBy: none")
        lines.append("\t\tsourceColumn: Name")
        lines.append("\t\tsortByColumn: Ordinal")
        lines.append("")
        lines.append("\tcolumn Ordinal")
        lines.append("\t\tdataType: int64")
        lines.append("\t\tformatString: 0")
        lines.append(f"\t\tlineageTag: {self._generate_lineage_tag()}")
        lines.append("\t\tsummarizeBy: sum")
        lines.append("\t\tsourceColumn: Ordinal")
        return "\n".join(lines) + "\n"

    def _generate_calculation_item(self, item: CalcItemDef) -> List[str]:
        lines = emit_expression(f"calculationItem {tmdl_name(item.name)}", item.expression, 2)
        lines.append(f"\t\t\tordinal: {item.ordinal}")
        if item.format_string:
            lines.append("")
            lines.extend(emit_expression("formatStringDefinition", item.format_string, 3))
        return lines

    def generate_from_definition(self, object_type: str, definition: Dict[str, Any]) -> str:
        """Generate TMDL from a definition dict (object_type: table, measure,
        relationship, calculation_group / calc_group)."""
        if object_type == "table":
            columns = [ColumnDef(**col) for col in definition.get("columns", [])]
            partitions = None
            if "partitions" in definition:
                partitions = [PartitionDef(**part) for part in definition["partitions"]]
            return self.generate_table(
                name=definition["name"],
                columns=columns,
                partitions=partitions,
                description=definition.get("description"),
            )
        if object_type == "measure":
            return self.generate_measure(
                table=definition.get("table", ""),
                name=definition["name"],
                expression=definition["expression"],
                properties=definition.get("properties", {}),
            )
        if object_type == "relationship":
            return self.generate_relationship(
                from_table=definition["from_table"],
                from_column=definition["from_column"],
                to_table=definition["to_table"],
                to_column=definition["to_column"],
                cardinality=definition.get("cardinality", "many-to-one"),
                cross_filter_direction=definition.get("cross_filter_direction", "single"),
                is_active=definition.get("is_active", True),
            )
        if object_type in ("calculation_group", "calc_group"):
            items = [CalcItemDef(**item) for item in definition.get("items", [])]
            return self.generate_calculation_group(
                name=definition["name"],
                items=items,
                description=definition.get("description"),
                precedence=definition.get("precedence"),
            )
        raise ValueError(f"Unsupported object type: {object_type}")

    def _generate_lineage_tag(self) -> str:
        return str(uuid.uuid4())
