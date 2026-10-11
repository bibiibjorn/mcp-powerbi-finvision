"""
Aggregation Detector Module

Detects aggregation tables, aggregation level measures, and aggregation-aware measures
in Power BI semantic models.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

from core.dax.dax_reference_parser import scan_references

logger = logging.getLogger(__name__)


@dataclass
class AggregatedColumn:
    """Represents an aggregated column in an aggregation table."""

    name: str
    source_expression: Optional[str] = None
    aggregation_function: Optional[str] = None  # SUM, COUNT, etc.
    source_table: Optional[str] = None
    source_column: Optional[str] = None


@dataclass
class AggregationTable:
    """Represents a detected aggregation table."""

    name: str
    level: int  # 1=base (not an agg), 2=mid, 3=high, etc.
    level_name: str  # Human-readable level name
    is_hidden: bool
    source_expression: Optional[str]  # SUMMARIZECOLUMNS definition
    grain_columns: List[str]  # Columns defining the grain
    aggregated_columns: List[AggregatedColumn]
    related_dimensions: List[str]
    estimated_row_count: Optional[int] = None
    detection_confidence: float = 1.0  # 0-1 confidence score
    detection_reasons: List[str] = field(default_factory=list)


@dataclass
class AggLevelMeasure:
    """Represents an aggregation level detection measure."""

    table: str
    name: str
    expression: str
    detail_trigger_columns: List[str]  # Columns that force base table (level 1)
    mid_level_trigger_columns: List[str]  # Columns that allow mid-level agg
    high_level_trigger_columns: List[str]  # Columns for highest aggregation
    levels: Dict[int, str]  # level_num -> description/table name
    level_var_mapping: Dict[str, int]  # VAR name -> level it triggers
    default_level: int = 3


@dataclass
class AggAwareMeasure:
    """Represents a measure that switches between aggregation tables."""

    table: str
    name: str
    expression: str
    uses_agg_level_measure: Optional[str]  # Reference to level measure
    table_switches: Dict[int, str]  # level -> table name used
    column_switches: Dict[int, str]  # level -> column name used
    dependencies: List[str]  # Other measures this depends on
    is_base_only: bool = False  # True if measure only uses base table


class AggregationTableDetector:
    """Detects aggregation tables and related measures in Power BI models."""

    # Naming patterns that suggest aggregation tables
    AGG_NAME_PATTERNS = [
        r"^Agg[_\s]",  # Agg_, Agg
        r"^Aggregat",  # Aggregation, Aggregated
        r"[_\s]Agg$",  # _Agg, Agg suffix
        r"^Summary[_\s]",  # Summary_
        r"^Fact[_\s]Agg",  # Fact_Agg
        r"_Summary$",  # _Summary suffix
        r"^Pre[_\s]?Agg",  # PreAgg, Pre_Agg
    ]

    # Naming patterns that indicate dimension tables (should NOT be detected as aggregation)
    DIM_NAME_PATTERNS = [
        r"^Dim[_\s]",  # Dim_, Dim_YearMonth, Dim YearQuarter, etc.
        r"^d[_\s]",  # d_ prefix for dimensions
        r"^Dimension[_\s]",  # Dimension_
        r"^Lookup[_\s]",  # Lookup_
        r"^Bridge[_\s]",  # Bridge_
        r"^Calendar",  # Calendar tables
        r"^Date$",  # Date table
        r"^Time$",  # Time table
    ]

    # Patterns in calculated table expressions that indicate aggregation
    AGG_EXPRESSION_PATTERNS = [
        r"\bSUMMARIZECOLUMNS\s*\(",
        r"\bSUMMARIZE\s*\(",
        r"\bGROUPBY\s*\(",
        r"\bADDCOLUMNS\s*\(\s*SUMMARIZE",
    ]

    # Patterns for aggregation level measures
    AGG_LEVEL_MEASURE_PATTERNS = [
        r"ISFILTERED\s*\([^)]+\)\s*\|\|",  # Multiple ISFILTERED with OR
        r"SWITCH\s*\(\s*TRUE\s*\(\s*\)",  # SWITCH(TRUE(), ...) pattern
    ]

    # Pattern to extract ISFILTERED columns
    ISFILTERED_PATTERN = re.compile(
        r"ISFILTERED\s*\(\s*'?([^'\[\)]+)'?\s*\[([^\]]+)\]\s*\)", re.IGNORECASE
    )

    # Pattern to extract VAR definitions with ISFILTERED
    VAR_ISFILTERED_PATTERN = re.compile(
        r"VAR\s+(_\w+)\s*=\s*((?:[^V]|V(?!AR\s))*?ISFILTERED[^V]*?)(?=VAR\s|RETURN)",
        re.IGNORECASE | re.DOTALL,
    )

    # Pattern to extract SWITCH cases
    SWITCH_CASE_PATTERN = re.compile(
        r"SWITCH\s*\(\s*(?:TRUE\s*\(\s*\)|_\w+)\s*,\s*(.+)\)", re.IGNORECASE | re.DOTALL
    )

    def __init__(self, model_data: Dict[str, Any]):
        """
        Initialize detector with parsed model data.

        Args:
            model_data: Parsed model from TmdlParser.parse_full_model()
        """
        self.model = model_data
        self.tables = model_data.get("tables", [])
        self.relationships = model_data.get("relationships", [])

        # Build lookup maps
        self._table_map: Dict[str, Dict] = {t.get("name", ""): t for t in self.tables}
        self._measure_map: Dict[str, Tuple[str, Dict]] = (
            {}
        )  # measure_name -> (table_name, measure_data)
        self._build_measure_map()

    def _build_measure_map(self) -> None:
        """Build a map of all measures in the model."""
        for table in self.tables:
            table_name = table.get("name", "")
            for measure in table.get("measures", []):
                measure_name = measure.get("name", "")
                self._measure_map[measure_name] = (table_name, measure)
                # Also store with table prefix
                self._measure_map[f"{table_name}[{measure_name}]"] = (table_name, measure)

    def detect_all(self) -> Dict[str, Any]:
        """
        Perform full aggregation detection.

        Returns:
            Dictionary containing:
            - aggregation_tables: List of detected aggregation tables
            - base_fact_tables: List of likely base fact tables
            - agg_level_measures: List of aggregation level detection measures
            - agg_aware_measures: List of aggregation-aware measures
        """
        logger.info("Starting aggregation detection")

        # Detect aggregation tables
        agg_tables = self.detect_aggregation_tables()
        logger.info(f"Detected {len(agg_tables)} aggregation tables")

        # Identify base fact tables (tables that agg tables aggregate from)
        base_tables = self._identify_base_tables(agg_tables)
        logger.info(f"Identified {len(base_tables)} base fact tables")

        # Detect aggregation level measures
        agg_level_measures = self.detect_aggregation_level_measures()
        logger.info(f"Detected {len(agg_level_measures)} aggregation level measures")

        # Detect aggregation-aware measures
        agg_aware_measures = self.detect_aggregation_aware_measures(agg_tables, agg_level_measures)
        logger.info(f"Detected {len(agg_aware_measures)} aggregation-aware measures")

        return {
            "aggregation_tables": agg_tables,
            "base_fact_tables": base_tables,
            "agg_level_measures": agg_level_measures,
            "agg_aware_measures": agg_aware_measures,
        }

    def detect_aggregation_tables(self) -> List[AggregationTable]:
        """
        Detect aggregation tables in the model.

        Uses multiple heuristics:
        1. Naming patterns (Agg_, _Agg, Summary, etc.)
        2. Hidden status
        3. Calculated table with SUMMARIZECOLUMNS/GROUPBY
        4. Column patterns matching fact table subsets
        """
        agg_tables = []

        for table in self.tables:
            table_name = table.get("name", "")
            if not table_name:
                continue

            # Skip system tables
            if table_name.startswith("LocalDateTable_") or table_name.startswith(
                "DateTableTemplate_"
            ):
                continue

            # Skip dimension tables - they should never be detected as aggregation tables
            if self._is_dimension_table(table_name):
                logger.debug(f"Skipping dimension table: {table_name}")
                continue

            score = 0.0
            reasons = []

            # Check naming pattern
            name_match = self._check_name_pattern(table_name)
            if name_match:
                score += 0.4
                reasons.append(f"Name matches aggregation pattern: {name_match}")

            # Check if hidden
            is_hidden = table.get("is_hidden", False)
            if is_hidden:
                score += 0.2
                reasons.append("Table is hidden")

            # Check for calculated table with aggregation expression
            partitions = table.get("partitions", [])
            agg_expression = None
            grain_columns = []
            aggregated_cols = []

            for partition in partitions:
                source = partition.get("source", "")
                if source and self._is_aggregation_expression(source):
                    score += 0.4
                    reasons.append("Calculated table uses SUMMARIZECOLUMNS/GROUPBY")
                    agg_expression = source
                    grain_columns, aggregated_cols = self._parse_aggregation_expression(source)
                    break

            # If no explicit aggregation expression, check column patterns
            if not agg_expression:
                columns = table.get("columns", [])
                has_sum_columns = any(
                    c.get("summarize_by") == "sum"
                    or "Amount" in c.get("name", "")
                    or "Total" in c.get("name", "")
                    for c in columns
                )
                has_key_columns = any(
                    "Key" in c.get("name", "") or c.get("source_column", "") for c in columns
                )
                if has_sum_columns and has_key_columns and len(columns) < 15:
                    score += 0.2
                    reasons.append("Has aggregated value columns with dimension keys")

            # Threshold for detection
            if score >= 0.4:
                # Determine level based on grain
                level = self._estimate_aggregation_level(grain_columns, table_name)
                level_name = self._get_level_name(level, table_name)

                # Get related dimensions from relationships
                related_dims = self._get_related_dimensions(table_name)

                # Parse aggregated columns if not already done
                if not aggregated_cols:
                    aggregated_cols = self._extract_aggregated_columns(table)

                agg_table = AggregationTable(
                    name=table_name,
                    level=level,
                    level_name=level_name,
                    is_hidden=is_hidden,
                    source_expression=agg_expression,
                    grain_columns=grain_columns,
                    aggregated_columns=aggregated_cols,
                    related_dimensions=related_dims,
                    detection_confidence=min(score, 1.0),
                    detection_reasons=reasons,
                )
                agg_tables.append(agg_table)

        # Sort by level (higher aggregation = higher level number)
        agg_tables.sort(key=lambda x: x.level, reverse=True)
        return agg_tables

    def _check_name_pattern(self, table_name: str) -> Optional[str]:
        """Check if table name matches aggregation patterns."""
        for pattern in self.AGG_NAME_PATTERNS:
            if re.search(pattern, table_name, re.IGNORECASE):
                return pattern
        return None

    def _is_dimension_table(self, table_name: str) -> bool:
        """Check if table name matches dimension table patterns."""
        for pattern in self.DIM_NAME_PATTERNS:
            if re.search(pattern, table_name, re.IGNORECASE):
                return True
        return False

    def _is_aggregation_expression(self, expression: str) -> bool:
        """Check if expression is an aggregation calculation."""
        for pattern in self.AGG_EXPRESSION_PATTERNS:
            if re.search(pattern, expression, re.IGNORECASE):
                return True
        return False

    def _parse_aggregation_expression(
        self, expression: str
    ) -> Tuple[List[str], List[AggregatedColumn]]:
        """
        Parse a SUMMARIZECOLUMNS or GROUPBY expression.

        Returns:
            Tuple of (grain_columns, aggregated_columns)
        """
        grain_columns = []
        aggregated_columns = []

        # Extract columns from SUMMARIZECOLUMNS
        # Pattern: SUMMARIZECOLUMNS(Table[Col1], Table[Col2], ..., "Name", SUM(...))

        # Find grain columns (table[column] references before first string literal)
        col_pattern = re.compile(r"'([^']+)'\s*\[([^\]]+)\]")
        matches = col_pattern.findall(expression)

        # First few matches are typically grain columns
        in_grain = True
        for table, col in matches:
            if in_grain:
                grain_columns.append(f"{table}[{col}]")

        # Find aggregated columns ("Name", AGG_FUNC(...))
        agg_col_pattern = re.compile(
            r'"(\w+)"\s*,\s*(SUM|COUNT|COUNTROWS|AVERAGE|MIN|MAX)\s*\(\s*([^)]+)\)', re.IGNORECASE
        )
        for match in agg_col_pattern.finditer(expression):
            col_name = match.group(1)
            agg_func = match.group(2).upper()
            source_ref = match.group(3).strip()

            # Parse source reference
            source_table = None
            source_col = None
            source_match = re.match(r"'?([^'\[]+)'?\s*\[([^\]]+)\]", source_ref)
            if source_match:
                source_table = source_match.group(1)
                source_col = source_match.group(2)

            aggregated_columns.append(
                AggregatedColumn(
                    name=col_name,
                    source_expression=source_ref,
                    aggregation_function=agg_func,
                    source_table=source_table,
                    source_column=source_col,
                )
            )

        return grain_columns, aggregated_columns

    def _estimate_aggregation_level(self, grain_columns: List[str], table_name: str) -> int:
        """
        Estimate aggregation level based on grain.

        Lower grain count = higher aggregation level.
        Returns: 2 for mid-level, 3 for high-level, etc.
        """
        if not grain_columns:
            # Guess from name
            name_lower = table_name.lower()
            if "quarter" in name_lower or "year" in name_lower:
                return 3  # High level
            elif "month" in name_lower:
                return 2  # Mid level
            return 2  # Default mid-level

        grain_count = len(grain_columns)
        if grain_count <= 2:
            return 3  # High aggregation (few dimensions)
        elif grain_count <= 4:
            return 2  # Mid aggregation
        else:
            return 2  # Still mid-level for more dimensions

    def _get_level_name(self, level: int, table_name: str) -> str:
        """Get human-readable level name."""
        if level >= 3:
            return "High-Level Aggregation"
        elif level == 2:
            return "Mid-Level Aggregation"
        else:
            return "Low-Level Aggregation"

    def _get_related_dimensions(self, table_name: str) -> List[str]:
        """Get dimension tables related to this table."""
        related = []
        for rel in self.relationships:
            from_col = rel.get("from_column", "")
            to_col = rel.get("to_column", "")

            # Parse table.column format
            if from_col and "." in from_col:
                from_table = from_col.split(".")[0]
                if from_table == table_name:
                    to_table = to_col.split(".")[0] if "." in to_col else ""
                    if to_table and to_table not in related:
                        related.append(to_table)

            if to_col and "." in to_col:
                to_table = to_col.split(".")[0]
                if to_table == table_name:
                    from_table = from_col.split(".")[0] if "." in from_col else ""
                    if from_table and from_table not in related:
                        related.append(from_table)

        return related

    def _extract_aggregated_columns(self, table: Dict) -> List[AggregatedColumn]:
        """Extract aggregated columns from table definition."""
        agg_cols = []
        for col in table.get("columns", []):
            col_name = col.get("name", "")
            summarize_by = col.get("summarize_by", "")
            source_col = col.get("source_column", "")

            if (
                summarize_by == "sum"
                or "Amount" in col_name
                or "Total" in col_name
                or "Count" in col_name
            ):
                agg_cols.append(
                    AggregatedColumn(
                        name=col_name,
                        source_expression=source_col,
                        aggregation_function="SUM" if summarize_by == "sum" else None,
                    )
                )
        return agg_cols

    def _identify_base_tables(self, agg_tables: List[AggregationTable]) -> List[str]:
        """Identify base fact tables that aggregation tables derive from."""
        base_tables = set()

        for agg_table in agg_tables:
            # Check aggregated columns for source tables
            for col in agg_table.aggregated_columns:
                if col.source_table:
                    base_tables.add(col.source_table)

            # Check expression for table references
            if agg_table.source_expression:
                # Find table references in expression
                table_refs = re.findall(
                    r"(?:SUM|COUNT|COUNTROWS)\s*\(\s*'?([^'\[\)]+)'?",
                    agg_table.source_expression,
                    re.IGNORECASE,
                )
                for ref in table_refs:
                    if ref and ref not in [at.name for at in agg_tables]:
                        base_tables.add(ref)

        return list(base_tables)

    def detect_aggregation_level_measures(self) -> List[AggLevelMeasure]:
        """
        Detect measures that implement aggregation level detection.

        These measures typically:
        - Use multiple ISFILTERED() checks
        - Use SWITCH(TRUE(), ...) to return level numbers
        - Return integer values (1, 2, 3) indicating aggregation level
        """
        agg_level_measures = []

        for table in self.tables:
            table_name = table.get("name", "")
            for measure in table.get("measures", []):
                measure_name = measure.get("name", "")
                expression = measure.get("expression", "")

                if not expression:
                    continue

                # Check for aggregation level measure patterns
                isfiltered_count = len(self.ISFILTERED_PATTERN.findall(expression))
                has_switch_true = bool(
                    re.search(r"SWITCH\s*\(\s*TRUE\s*\(\s*\)", expression, re.IGNORECASE)
                )

                # Need multiple ISFILTERED and a SWITCH pattern
                if isfiltered_count >= 3 and has_switch_true:
                    # Parse the measure to extract level rules
                    parsed = self._parse_aggregation_level_measure(expression)

                    if parsed:
                        agg_level_measure = AggLevelMeasure(
                            table=table_name,
                            name=measure_name,
                            expression=expression,
                            detail_trigger_columns=parsed["detail_triggers"],
                            mid_level_trigger_columns=parsed["mid_level_triggers"],
                            high_level_trigger_columns=parsed.get("high_level_triggers", []),
                            levels=parsed["levels"],
                            level_var_mapping=parsed["var_mapping"],
                            default_level=parsed.get("default_level", 3),
                        )
                        agg_level_measures.append(agg_level_measure)

        return agg_level_measures

    def _parse_aggregation_level_measure(self, expression: str) -> Optional[Dict[str, Any]]:
        """
        Parse an aggregation level measure to extract rules.

        Returns dictionary with:
        - detail_triggers: columns that force detail level
        - mid_level_triggers: columns for mid-level
        - levels: mapping of level number to description
        - var_mapping: VAR name to level mapping
        """
        result = {
            "detail_triggers": [],
            "mid_level_triggers": [],
            "high_level_triggers": [],
            "levels": {},
            "var_mapping": {},
            "default_level": 3,
        }

        # Extract VAR definitions with ISFILTERED
        var_matches = self.VAR_ISFILTERED_PATTERN.findall(expression)

        var_to_columns: Dict[str, List[str]] = {}
        for var_name, var_body in var_matches:
            columns = []
            for table, col in self.ISFILTERED_PATTERN.findall(var_body):
                columns.append(f"{table}[{col}]")
            var_to_columns[var_name] = columns

        # Extract SWITCH mapping
        # Look for pattern: SWITCH(TRUE(), _Var1, 1, _Var2, 2, default)
        switch_match = re.search(
            r"SWITCH\s*\(\s*TRUE\s*\(\s*\)\s*,\s*(.+?)\s*\)", expression, re.IGNORECASE | re.DOTALL
        )

        if switch_match:
            switch_body = switch_match.group(1)
            # Parse switch cases: _VarName, value, _VarName, value, ...
            # Split by comma but be careful with nested expressions
            parts = self._split_switch_cases(switch_body)

            i = 0
            while i < len(parts) - 1:
                condition = parts[i].strip()
                value = parts[i + 1].strip()

                # Try to parse value as integer
                try:
                    level = int(value)

                    # Check if condition is a VAR reference
                    if condition.startswith("_"):
                        var_name = condition
                        result["var_mapping"][var_name] = level

                        # Map columns to appropriate trigger list
                        if var_name in var_to_columns:
                            if level == 1:
                                result["detail_triggers"].extend(var_to_columns[var_name])
                                result["levels"][1] = "Base Table (Detail)"
                            elif level == 2:
                                result["mid_level_triggers"].extend(var_to_columns[var_name])
                                result["levels"][2] = "Mid-Level Aggregation"
                            else:
                                result["high_level_triggers"].extend(var_to_columns[var_name])
                                result["levels"][level] = f"Level {level} Aggregation"

                    i += 2
                except ValueError:
                    # Not a level number, might be the default
                    if i == len(parts) - 1:
                        try:
                            result["default_level"] = int(condition)
                        except ValueError:
                            pass
                    i += 1

        # If no levels detected, use defaults
        if not result["levels"]:
            result["levels"] = {
                1: "Base Table (Detail)",
                2: "Mid-Level Aggregation",
                3: "High-Level Aggregation",
            }

        return result if result["detail_triggers"] or result["mid_level_triggers"] else None

    def _split_switch_cases(self, switch_body: str) -> List[str]:
        """Split SWITCH cases handling nested parentheses."""
        parts = []
        current = ""
        paren_depth = 0

        for char in switch_body:
            if char == "(":
                paren_depth += 1
                current += char
            elif char == ")":
                paren_depth -= 1
                current += char
            elif char == "," and paren_depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += char

        if current.strip():
            parts.append(current.strip())

        return parts

    def detect_aggregation_aware_measures(
        self, agg_tables: List[AggregationTable], agg_level_measures: List[AggLevelMeasure]
    ) -> List[AggAwareMeasure]:
        """
        Detect measures that switch between base and aggregation tables.

        These measures typically:
        - Reference an aggregation level measure
        - Use SWITCH to select from different table sources
        - Reference columns from aggregation tables
        """
        agg_aware_measures = []

        # Agg tables by normalized name (lower-case, collapsed whitespace: the
        # key scan_references uses). 2026-09-22 (I-23): references are found
        # with the DAX lexer - the old ``name in expression`` substring test
        # matched inside strings/comments/longer names and its ``\w+`` column
        # regex missed quoted names with spaces.
        agg_table_names = {" ".join(at.name.lower().split()): at for at in agg_tables}

        # Get level measure names for reference checking
        level_measure_refs = set()
        for lm in agg_level_measures:
            level_measure_refs.add(f"[{lm.name}]")
            level_measure_refs.add(f"{lm.table}[{lm.name}]")

        for table in self.tables:
            table_name = table.get("name", "")
            for measure in table.get("measures", []):
                measure_name = measure.get("name", "")
                expression = measure.get("expression", "")

                if not expression:
                    continue

                # Skip if this is itself an aggregation level measure
                if any(
                    lm.name == measure_name and lm.table == table_name for lm in agg_level_measures
                ):
                    continue

                # Check if measure references an aggregation level measure
                uses_level_measure = None
                for ref in level_measure_refs:
                    if ref in expression:
                        uses_level_measure = ref.strip("[]")
                        break

                # Check if measure references aggregation tables
                references_agg_tables = False
                table_switches: Dict[int, str] = {}
                column_switches: Dict[int, str] = {}

                if agg_table_names:
                    for ref in scan_references(expression, known_tables=set(agg_table_names)):
                        if ref.kind not in ("qualified", "table"):
                            continue
                        agg_table = agg_table_names.get(" ".join(ref.table.lower().split()))
                        if agg_table is None:
                            continue
                        references_agg_tables = True
                        # Which level uses this table
                        table_switches[agg_table.level] = agg_table.name
                        if ref.kind == "qualified":
                            column_switches[agg_table.level] = f"{agg_table.name}[{ref.name}]"

                # Check for base table references (level 1)
                has_switch = bool(re.search(r"\bSWITCH\s*\(", expression, re.IGNORECASE))

                # Measure is aggregation-aware if it references level measure OR agg tables with SWITCH
                if uses_level_measure or (references_agg_tables and has_switch):
                    # Find measure dependencies
                    dependencies = self._find_measure_dependencies(expression)

                    # Determine if base-only (no agg table references)
                    is_base_only = not references_agg_tables

                    agg_aware = AggAwareMeasure(
                        table=table_name,
                        name=measure_name,
                        expression=expression,
                        uses_agg_level_measure=uses_level_measure,
                        table_switches=table_switches,
                        column_switches=column_switches,
                        dependencies=dependencies,
                        is_base_only=is_base_only,
                    )
                    agg_aware_measures.append(agg_aware)

        return agg_aware_measures

    def _find_measure_dependencies(self, expression: str) -> List[str]:
        """Find other measures referenced in an expression."""
        dependencies = []

        # Pattern for measure references: [MeasureName]
        measure_refs = re.findall(r"\[([^\]]+)\]", expression)

        for ref in measure_refs:
            # Check if this is a known measure
            if ref in self._measure_map:
                if ref not in dependencies:
                    dependencies.append(ref)

        return dependencies
