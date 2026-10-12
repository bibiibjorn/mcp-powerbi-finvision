"""
TMDL Text Generator

Converts parsed TMDL JSON structure back to TMDL text format
for display in diff views.
"""

import logging
from typing import Dict, Any, List

from core.dax.dax_names import quote_table

from .script_generator import emit_expression

logger = logging.getLogger(__name__)


class TmdlTextGenerator:
    """Generates TMDL text from parsed TMDL structure."""

    def __init__(self, tmdl_data: Dict[str, Any]):
        """
        Initialize generator.

        Args:
            tmdl_data: Parsed TMDL structure from model export
        """
        self.tmdl_data = tmdl_data
        self.indent_level = 0

    @staticmethod
    def _escape_tmdl_string(value: str) -> str:
        """Escape quotes in a TMDL string value by doubling them.

        In TMDL format, double quotes inside quoted strings must be
        escaped by doubling them (e.g., 'He said "hello"' becomes
        'He said ""hello""').

        Args:
            value: The raw string value to escape

        Returns:
            The escaped string safe for embedding in TMDL double-quoted literals
        """
        return value.replace('"', '""')

    def generate_full_tmdl(self) -> str:
        """Generate complete TMDL text representation."""
        lines = []

        # Model header
        model = self.tmdl_data.get("model", {})
        if model:
            lines.append(f"model {model.get('name', 'Model')}")
            lines.append("")
            if model.get("compatibility_level"):
                lines.append(f"\tcompatibilityLevel: {model['compatibility_level']}")
            if model.get("default_mode"):
                lines.append(f"\tdefaultMode: {model['default_mode']}")
            lines.append("")

        # Tables
        tables = self.tmdl_data.get("tables", {})
        if isinstance(tables, dict):
            tables = list(tables.values())

        for table in tables:
            lines.extend(self._generate_table(table))
            lines.append("")

        # Relationships
        relationships = self.tmdl_data.get("relationships", [])
        if relationships:
            lines.append("/// Relationships")
            lines.append("")
            for rel in relationships:
                lines.extend(self._generate_relationship(rel))
                lines.append("")

        # Roles
        roles = self.tmdl_data.get("roles", [])
        if roles:
            lines.append("/// Roles")
            lines.append("")
            for role in roles:
                lines.extend(self._generate_role(role))
                lines.append("")

        return "\n".join(lines)

    def _generate_table(self, table: Dict[str, Any]) -> List[str]:
        """Generate TMDL for a single table."""
        lines = []
        table_name = table.get("name", "Unknown")

        lines.append(f"table {quote_table(table_name)}")

        # Table properties
        if table.get("is_hidden"):
            lines.append("\tisHidden: true")

        if table.get("description"):
            lines.append(f"\tdescription: \"{self._escape_tmdl_string(table['description'])}\"")

        # Columns
        for col in table.get("columns", []):
            lines.append("")
            lines.extend(self._generate_column(col))

        # Measures
        for measure in table.get("measures", []):
            lines.append("")
            lines.extend(self._generate_measure(measure))

        # Hierarchies
        for hierarchy in table.get("hierarchies", []):
            lines.append("")
            lines.extend(self._generate_hierarchy(hierarchy))

        # Calendars (CL 1701)
        for calendar in table.get("calendars", []):
            lines.append("")
            lines.extend(self._generate_calendar(calendar))

        # Partitions
        for partition in table.get("partitions", []):
            lines.append("")
            lines.extend(self._generate_partition(partition))

        return lines

    def _generate_calendar(self, calendar: Dict[str, Any]) -> List[str]:
        """Generate TMDL for a CL 1701 calendar block."""
        lines = []
        cal_name = calendar.get("name", "Unknown")

        lines.append(f"\tcalendar {quote_table(cal_name)}")

        for key, value in (calendar.get("properties") or {}).items():
            lines.append(f"\t\t{key}: {value}")

        for group in calendar.get("column_groups", []):
            lines.append(f"\t\tcalendarColumnGroup {group.get('name', 'Unknown')}")
            if group.get("primary_column"):
                lines.append(f"\t\t\tprimaryColumn: {group['primary_column']}")
            if group.get("associated_column"):
                lines.append(f"\t\t\tassociatedColumn: {group['associated_column']}")
            for key, value in (group.get("properties") or {}).items():
                lines.append(f"\t\t\t{key}: {value}")

        return lines

    def _generate_column(self, col: Dict[str, Any]) -> List[str]:
        """Generate TMDL for a column."""
        lines = []
        col_name = col.get("name", "Unknown")

        # Column declaration
        # Review G-24: the body sits at +2 (it was emitted at the property
        # indent, which re-parses as properties); names keep their '' escapes.
        is_calculated = col.get("is_calculated", False)
        header = f"column {quote_table(col_name)}"
        if is_calculated and col.get("expression"):
            lines.extend(emit_expression(header, col["expression"], 1))
        else:
            lines.append(f"\t{header}")

        # Column properties
        if col.get("data_type"):
            lines.append(f"\t\tdataType: {col['data_type']}")

        if col.get("source_column"):
            lines.append(f"\t\tsourceColumn: {col['source_column']}")

        if col.get("format_string"):
            lines.append(f"\t\tformatString: \"{self._escape_tmdl_string(col['format_string'])}\"")

        if col.get("display_folder"):
            lines.append(
                f"\t\tdisplayFolder: \"{self._escape_tmdl_string(col['display_folder'])}\""
            )

        if col.get("is_hidden"):
            lines.append("\t\tisHidden: true")

        if col.get("description"):
            lines.append(f"\t\tdescription: \"{self._escape_tmdl_string(col['description'])}\"")

        return lines

    def _generate_measure(self, measure: Dict[str, Any]) -> List[str]:
        """Generate TMDL for a measure."""
        lines = []
        measure_name = measure.get("name", "Unknown")

        # Measure declaration with expression
        lines.extend(
            emit_expression(f"measure {quote_table(measure_name)}", measure.get("expression"), 1)
        )

        # Measure properties
        if measure.get("format_string"):
            lines.append(
                f"\t\tformatString: \"{self._escape_tmdl_string(measure['format_string'])}\""
            )

        if measure.get("display_folder"):
            lines.append(
                f"\t\tdisplayFolder: \"{self._escape_tmdl_string(measure['display_folder'])}\""
            )

        if measure.get("is_hidden"):
            lines.append("\t\tisHidden: true")

        if measure.get("description"):
            lines.append(f"\t\tdescription: \"{self._escape_tmdl_string(measure['description'])}\"")

        if measure.get("format_string_definition"):
            lines.extend(
                emit_expression("formatStringDefinition", measure["format_string_definition"], 2)
            )

        return lines

    def _generate_hierarchy(self, hierarchy: Dict[str, Any]) -> List[str]:
        """Generate TMDL for a hierarchy."""
        lines = []
        hier_name = hierarchy.get("name", "Unknown")

        lines.append(f"\thierarchy {quote_table(hier_name)}")

        for level in hierarchy.get("levels", []):
            level_name = level.get("name", "Unknown")
            level_col = level.get("column", "")
            lines.append(f"\t\tlevel {quote_table(level_name)}")
            if level_col:
                lines.append(f"\t\t\tcolumn: {level_col}")

        return lines

    def _generate_partition(self, partition: Dict[str, Any]) -> List[str]:
        """Generate TMDL for a partition."""
        lines = []
        part_name = partition.get("name", "Unknown")
        part_type = partition.get("type", "m")

        lines.append(f"\tpartition {quote_table(part_name)} = {part_type}")

        if partition.get("mode"):
            lines.append(f"\t\tmode: {partition['mode']}")

        if partition.get("source"):
            lines.extend(emit_expression("source", partition["source"], 2))

        return lines

    def _generate_relationship(self, rel: Dict[str, Any]) -> List[str]:
        """Generate TMDL for a relationship."""
        lines = []

        from_col = rel.get("from_column", "")
        to_col = rel.get("to_column", "")

        lines.append(f"relationship {rel.get('id') or rel.get('name') or ''}".rstrip())

        if from_col:
            lines.append(f"\tfromColumn: {from_col}")

        if rel.get("from_cardinality"):
            lines.append(f"\tfromCardinality: {rel['from_cardinality']}")

        if to_col:
            lines.append(f"\ttoColumn: {to_col}")

        if rel.get("to_cardinality"):
            lines.append(f"\ttoCardinality: {rel['to_cardinality']}")

        if rel.get("is_active") is False:
            lines.append("\tisActive: false")

        if rel.get("cross_filtering_behavior"):
            lines.append(f"\tcrossFilteringBehavior: {rel['cross_filtering_behavior']}")

        return lines

    def _generate_role(self, role: Dict[str, Any]) -> List[str]:
        """Generate TMDL for a role."""
        lines = []
        role_name = role.get("name", "Unknown")

        lines.append(f"role {quote_table(role_name)}")

        for perm in role.get("table_permissions", []):
            table_name = perm.get("table", "")
            filter_expr = perm.get("filter_expression", "")

            if table_name:
                lines.extend(
                    emit_expression(f"tablePermission {quote_table(table_name)}", filter_expr, 1)
                )

        return lines


def generate_tmdl_text(tmdl_data: Dict[str, Any]) -> str:
    """
    Convenience function to generate TMDL text.

    Args:
        tmdl_data: Parsed TMDL structure

    Returns:
        TMDL text representation
    """
    generator = TmdlTextGenerator(tmdl_data)
    return generator.generate_full_tmdl()
