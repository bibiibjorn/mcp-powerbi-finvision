"""
Aggregation Table Detection and Matching

Detects aggregation tables in the model and suggests their use when the query
grain matches the aggregation grain.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AggregationTableInfo:
    """Information about a detected aggregation table."""

    name: str
    grain_columns: List[str]
    source_table: Optional[str]
    detection_method: str  # 'pattern', 'expression', 'structure'
    confidence: float


@dataclass
class AggregationMatch:
    """Result of aggregation table matching for a query."""

    agg_table: str
    grain_columns: List[str]
    match_confidence: float
    query_columns: List[str]
    recommendation: str


class AggregationMatcher:
    """
    Detects aggregation tables and suggests their use based on query grain.

    Detection methods:
    1. Naming patterns (Agg_, _Summary, PreAgg, etc.)
    2. Calculated table expressions (SUMMARIZE, GROUPBY)
    3. Table structure (few columns, no detail rows)
    """

    # Naming patterns for aggregation tables
    AGG_NAME_PATTERNS = [
        re.compile(r"^Agg[_\s]", re.IGNORECASE),
        re.compile(r"[_\s]Agg$", re.IGNORECASE),
        re.compile(r"^Aggregat", re.IGNORECASE),
        re.compile(r"^Summary[_\s]", re.IGNORECASE),
        re.compile(r"[_\s]Summary$", re.IGNORECASE),
        re.compile(r"^Pre[_\s]?Agg", re.IGNORECASE),
        re.compile(r"^Fact[_\s]Agg", re.IGNORECASE),
    ]

    # Patterns in calculated table expressions that indicate aggregation
    AGG_EXPRESSION_PATTERNS = [
        re.compile(r"\bSUMMARIZECOLUMNS\s*\(", re.IGNORECASE),
        re.compile(r"\bSUMMARIZE\s*\(", re.IGNORECASE),
        re.compile(r"\bGROUPBY\s*\(", re.IGNORECASE),
        re.compile(r"\bADDCOLUMNS\s*\(\s*SUMMARIZE", re.IGNORECASE),
    ]

    def __init__(self, query_executor=None, aggregation_detector=None):
        """
        query_executor: QueryExecutor for DMV queries.
        """
        self.qe = query_executor
        self.detector = aggregation_detector
        self._agg_tables: Dict[str, AggregationTableInfo] = {}
        self._loaded = False

    def detect_aggregation_tables(self) -> Dict[str, AggregationTableInfo]:
        """
        Detect aggregation tables in the model; returns dict of name → AggregationTableInfo.
        """
        if self._loaded:
            return self._agg_tables

        # Try using existing detector first
        if self.detector:
            try:
                agg_tables = self.detector.detect_aggregation_tables()
                for agg in agg_tables:
                    self._agg_tables[agg.name] = AggregationTableInfo(
                        name=agg.name,
                        grain_columns=getattr(agg, "grain_columns", []),
                        source_table=getattr(agg, "source_table", None),
                        detection_method="detector",
                        confidence=0.90,
                    )
                self._loaded = True
                return self._agg_tables
            except Exception as e:
                logger.debug(f"AggregationTableDetector failed: {e}")

        # Fall back to our own detection
        if self.qe:
            self._detect_by_naming()
            self._detect_by_expression()

        self._loaded = True
        return self._agg_tables

    def _detect_by_naming(self) -> None:
        """Detect aggregation tables by naming patterns."""
        try:
            result = self.qe.execute_info_query("TABLES")
            if not result.get("success"):
                return

            for table in result.get("rows", []):
                name = table.get("Name", table.get("[Name]", ""))

                for pattern in self.AGG_NAME_PATTERNS:
                    if pattern.search(name):
                        self._agg_tables[name] = AggregationTableInfo(
                            name=name,
                            grain_columns=[],
                            source_table=None,
                            detection_method="pattern",
                            confidence=0.70,
                        )
                        logger.debug(f"Detected aggregation table by name: {name}")
                        break

        except Exception as e:
            logger.debug(f"Error detecting aggregations by naming: {e}")

    def _detect_by_expression(self) -> None:
        """Detect aggregation tables by calculated table expressions."""
        try:
            from .semantic_classifier import partition_sources

            # K-08a: INFO.PARTITIONS has [TableID], not [TableName].
            for row in partition_sources(self.qe):
                table_name = row["table"]
                source = row["source"]

                if not source or not table_name:
                    continue

                # Check for aggregation patterns
                for pattern in self.AGG_EXPRESSION_PATTERNS:
                    if pattern.search(source):
                        # Extract grain columns from expression
                        grain_cols = self._extract_grain_columns(source)

                        self._agg_tables[table_name] = AggregationTableInfo(
                            name=table_name,
                            grain_columns=grain_cols,
                            source_table=self._extract_source_table(source),
                            detection_method="expression",
                            confidence=0.85,
                        )
                        logger.debug(
                            f"Detected aggregation table by expression: {table_name} (grain: {grain_cols})"
                        )
                        break

        except Exception as e:
            logger.debug(f"Error detecting aggregations by expression: {e}")

    def _extract_grain_columns(self, expression: str) -> List[str]:
        """Grain columns ('T'[C] refs) of an aggregation expression (lexer-driven)."""
        from core.dax.dax_names import column_ref
        from core.dax.dax_reference_parser import scan_references

        columns: List[str] = []
        for ref in scan_references(expression or ""):
            if ref.kind == "qualified" and ref.table and ref.name:
                text = column_ref(ref.table, ref.name)
                if text not in columns:
                    columns.append(text)
        return columns[:10]

    def _extract_source_table(self, expression: str) -> Optional[str]:
        """First table argument of SUMMARIZE / GROUPBY / ALL (lexer-driven)."""
        from core.dax.dax_reference_parser import scan_references

        for ref in scan_references(expression or ""):
            if ref.kind == "table" and ref.function in ("SUMMARIZE", "GROUPBY", "ALL"):
                return ref.table or ref.name
        return None

    def find_matching_aggregation(
        self, grouping_columns: List[str], filter_columns: List[str]
    ) -> Optional[AggregationMatch]:
        """
        Find an aggregation table matching the query grain, or None if none qualifies.
        """
        self.detect_aggregation_tables()

        if not self._agg_tables:
            return None

        # Normalize column references
        def normalize_col(col: str) -> str:
            """Normalize column reference for comparison."""
            return col.lower().strip("'\"")

        query_cols = set(normalize_col(c) for c in grouping_columns + filter_columns)

        best_match = None
        best_score = 0

        for agg_name, agg_info in self._agg_tables.items():
            if not agg_info.grain_columns:
                continue

            grain_cols = set(normalize_col(c) for c in agg_info.grain_columns)

            # Check if query columns are a subset of aggregation grain
            if query_cols.issubset(grain_cols):
                # Score based on grain specificity (prefer smaller grain)
                score = agg_info.confidence * (1 / (len(grain_cols) + 1))

                if score > best_score:
                    best_score = score
                    best_match = AggregationMatch(
                        agg_table=agg_name,
                        grain_columns=agg_info.grain_columns,
                        match_confidence=agg_info.confidence,
                        query_columns=list(query_cols),
                        recommendation=f"Query grain matches aggregation table '{agg_name}'. "
                        f"Consider using for better performance.",
                    )

        return best_match

    def get_aggregation_info(self) -> Dict[str, Any]:
        """Get summary of detected aggregation tables."""
        self.detect_aggregation_tables()

        return {
            "count": len(self._agg_tables),
            "tables": [
                {
                    "name": info.name,
                    "grain_columns": info.grain_columns[:5],  # Limit for brevity
                    "source": info.source_table,
                    "detection": info.detection_method,
                }
                for info in self._agg_tables.values()
            ],
        }
