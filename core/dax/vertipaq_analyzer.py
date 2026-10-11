"""
VertiPaq Metrics Analyzer - Column cardinality and memory impact analysis

Integrates with Power BI DMVs to provide:
- Column cardinality analysis
- Memory footprint analysis
- Data type optimization suggestions
- High-cardinality column detection
"""

import logging
import re
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Decoding of the COLUMN_ENCODING storage code. Review E-17: this module said
# 1 = VALUE / 2 = HASH while pbi_perf_core.vpa (the 04 vertipaq_analyzer op) said
# the opposite. LIVE-CHECKS.md settled it on the user's model: every DBTYPE_WSTR
# BASIC_DATA column (416 of them — text is always HASH) reports 1, so
# 1 = HASH, 2 = VALUE. The single source of truth is
# ``pbi_perf_core.vpa.ENCODING_NAMES``; this map is derived from it.
def _encoding_names() -> Dict[int, str]:
    try:
        from pbi_perf_core.vpa import ENCODING_NAMES

        return dict(ENCODING_NAMES)
    except Exception:  # noqa: BLE001 - pbi_perf_core missing: same values
        return {1: "HASH", 2: "VALUE", 0: "UNKNOWN"}


ENCODING_CODE_MAP: Dict[str, str] = {
    str(code): name.lower() for code, name in _encoding_names().items() if code
}


def encoding_label(code: Any) -> str:
    """Translate a COLUMN_ENCODING storage code to a label.

    Unknown / non-numeric codes pass through as ``type_<code>`` so we never
    crash and never silently mislabel. Comparisons downstream use ``.upper()``.
    """
    key = str(code).strip()
    if key in ENCODING_CODE_MAP:
        return ENCODING_CODE_MAP[key]
    return f"type_{key}" if key else "unknown"


@dataclass
class ColumnMetrics:
    """Metrics for a single column"""

    table_name: str
    column_name: str
    cardinality: int
    size_bytes: int
    data_type: str
    encoding: str = "unknown"
    dictionary_size_bytes: int = 0
    hierarchy_size_bytes: int = 0
    is_referenced: bool = False
    reference_count: int = 0

    @property
    def full_name(self) -> str:
        """Get fully qualified column name"""
        return f"{self.table_name}[{self.column_name}]"

    @property
    def cardinality_level(self) -> str:
        """Classify cardinality level"""
        if self.cardinality < 100:
            return "low"
        elif self.cardinality < 10_000:
            return "medium"
        elif self.cardinality < 100_000:
            return "high"
        else:
            return "very_high"

    @property
    def size_mb(self) -> float:
        """Get size in megabytes"""
        return self.size_bytes / (1024 * 1024)


@dataclass
class DataTypeOptimization:
    """Data type optimization suggestion"""

    column: str
    current_type: str
    suggested_type: str
    reason: str
    estimated_savings_bytes: int
    priority: str  # "high", "medium", "low"


@dataclass
class CardinalityImpact:
    """Impact assessment for high-cardinality columns"""

    column: str
    cardinality: int
    size_bytes: int
    usage_context: str  # "iterator", "filter", "calculate"
    performance_impact: str  # "critical", "high", "medium", "low"
    recommendation: str


class VertiPaqAnalyzer:
    """
    VertiPaq Metrics Analyzer

    Analyzes column-level metrics from VertiPaq storage engine
    to provide performance optimization guidance
    """

    # Cardinality thresholds for performance warnings
    ITERATOR_CARDINALITY_WARNING = 100_000
    ITERATOR_CARDINALITY_CRITICAL = 1_000_000
    FILTER_CARDINALITY_WARNING = 500_000

    def __init__(self, connection_state=None):
        """
        Initialize VertiPaq Analyzer

        Args:
            connection_state: Optional connection state for DMV queries
        """
        self.connection_state = connection_state
        self._column_cache: Dict[str, ColumnMetrics] = {}
        self._cache_loaded = False
        self._dmv_diagnostic: Optional[str] = None

    def load_column_metrics(self) -> bool:
        """Load per-column storage metrics through THE VertiPaq extractor.

        Review E-17/E-19: the three legacy loaders (INFO.STORAGETABLECOLUMNS,
        a DISCOVER_STORAGE_TABLE_COLUMNS fallback on columns that do not exist,
        a dead ``result["data"]`` DAX fallback) are gone; sizes, encoding and
        the H$ cardinality fallback come from ``core.dax.vpa_extractor`` +
        ``pbi_perf_core.vpa`` — the same numbers ``04 vertipaq_analyzer`` shows.
        """
        try:
            if not self.connection_state or not self.connection_state.is_connected():
                logger.warning("Not connected - cannot load VertiPaq metrics")
                return False
            qe = self.connection_state.query_executor
            if not qe:
                logger.warning("Query executor not available")
                return False
            from core.dax.vpa_extractor import extract_model_metrics

            model = extract_model_metrics(qe, include_stats=False)["model"]
            self._column_cache.clear()
            for col in model.all_columns():
                metrics = ColumnMetrics(
                    table_name=col.table_name,
                    column_name=col.column_name,
                    cardinality=int(col.cardinality or 0),
                    size_bytes=int(col.total_size),
                    data_type=col.data_type or "unknown",
                    encoding=(col.encoding or "unknown").lower(),
                    dictionary_size_bytes=int(col.dictionary_size),
                    hierarchy_size_bytes=int(col.hierarchies_size),
                )
                self._column_cache[metrics.full_name] = metrics
            self._cache_loaded = bool(self._column_cache)
            self._dmv_diagnostic = f"vpa_extractor: {len(self._column_cache)} columns loaded"
            return self._cache_loaded
        except Exception as e:
            logger.error(f"Error loading VertiPaq metrics: {e}", exc_info=True)
            self._dmv_diagnostic = f"Exception: {e}"
            return False

    def get_column_metrics(self, column_ref: str) -> Optional[ColumnMetrics]:
        """
        Get metrics for a specific column

        Args:
            column_ref: Column reference like "Sales[Amount]" or "TableName[ColumnName]"

        Returns:
            ColumnMetrics if found, None otherwise
        """
        # Ensure cache is loaded
        if not self._cache_loaded:
            cache_success = self.load_column_metrics()
            if not cache_success:
                logger.warning(
                    f"Failed to load DMV cache, will attempt direct calculation for {column_ref}"
                )

        # Normalize column reference
        normalized = self._normalize_column_ref(column_ref)
        logger.debug(f"Looking up column: original='{column_ref}', normalized='{normalized}'")

        # First, try to get from cache (DMV data)
        cached = self._column_cache.get(normalized)

        if cached:
            logger.debug(f"Found {normalized} in cache with cardinality {cached.cardinality:,}")
            return cached

        # If not in cache, log available columns for debugging
        if self._cache_loaded and len(self._column_cache) > 0:
            # Log a few similar column names to help diagnose
            similar_cols = [k for k in self._column_cache.keys() if normalized.split("[")[0] in k]
            if similar_cols:
                logger.debug(
                    f"Column {normalized} not found. Similar columns in cache: {similar_cols[:5]}"
                )
            else:
                logger.debug(
                    f"Column {normalized} not found. Cache has {len(self._column_cache)} columns"
                )

        # Try to calculate cardinality directly using DAX
        logger.debug(f"Column {normalized} not in cache, attempting fallback calculation")
        cached = self._calculate_column_cardinality(column_ref)
        if cached:
            # Add to cache for future use
            self._column_cache[normalized] = cached
            logger.info(
                f"Successfully calculated metrics for {normalized} using DAX fallback: cardinality={cached.cardinality:,}"
            )
        else:
            logger.warning(
                f"Could not retrieve metrics for {normalized} from either DMV or DAX calculation"
            )

        return cached

    def analyze_dax_columns(self, dax_expression: str) -> Dict[str, Any]:
        """
        Analyze columns referenced in DAX expression

        Args:
            dax_expression: DAX expression to analyze

        Returns:
            Dictionary with column analysis results
        """
        try:
            # Ensure cache is loaded first
            if not self._cache_loaded:
                cache_loaded = self.load_column_metrics()
                if cache_loaded:
                    logger.info("VertiPaq metrics loaded from DMV")
                else:
                    logger.warning(
                        "VertiPaq metrics not available from DMV - will use fallback calculation"
                    )

            # Extract column references from DAX
            column_refs = self._extract_column_references(dax_expression)

            if not column_refs:
                return {
                    "success": True,
                    "columns_analyzed": 0,
                    "column_analysis": {},
                    "total_cardinality": 0,
                    "total_size_mb": 0.0,
                    "high_cardinality_columns": [],
                    "optimizations": [],
                    "note": "No column references found in DAX expression (might be using only measures)",
                }

            # Get metrics for each column
            column_analysis = {}
            total_cardinality = 0
            total_size_bytes = 0
            high_cardinality_columns = []
            metrics_available_count = 0

            for col_ref in column_refs:
                metrics = self.get_column_metrics(col_ref)

                if metrics:
                    metrics_available_count += 1

                    # Determine usage context
                    usage_context = self._determine_usage_context(dax_expression, col_ref)

                    # Assess impact
                    impact = self._assess_column_impact(metrics, usage_context)

                    column_analysis[col_ref] = {
                        "cardinality": metrics.cardinality,
                        "cardinality_level": metrics.cardinality_level,
                        "size_bytes": metrics.size_bytes,
                        "size_mb": round(metrics.size_mb, 2),
                        "data_type": metrics.data_type,
                        "encoding": metrics.encoding,
                        "usage_context": usage_context,
                        "performance_impact": impact.performance_impact,
                        "recommendation": impact.recommendation,
                        "metrics_source": "dmv" if self._cache_loaded else "calculated",
                    }

                    total_cardinality += metrics.cardinality
                    total_size_bytes += metrics.size_bytes

                    if metrics.cardinality_level in ["high", "very_high"]:
                        high_cardinality_columns.append(col_ref)
                else:
                    # Column not found - might be measure, calculated column, or invalid reference
                    logger.warning(f"No metrics available for {col_ref}")
                    column_analysis[col_ref] = {
                        "status": "metrics_unavailable",
                        "message": "No metrics available",
                        "note": "This might be a measure, calculated column, or the column doesn't exist in the model",
                    }

            # Get optimization suggestions
            optimizations = self._get_optimization_suggestions(column_analysis, dax_expression)

            # Prepare result with metadata about data quality
            result = {
                "success": True,
                "columns_analyzed": len(column_refs),
                "columns_with_metrics": metrics_available_count,
                "column_analysis": column_analysis,
                "total_cardinality": total_cardinality,
                "total_size_mb": round(total_size_bytes / (1024 * 1024), 2),
                "high_cardinality_columns": high_cardinality_columns,
                "optimizations": optimizations,
                "data_source": "dmv" if self._cache_loaded else "calculated",
            }

            # Add warning if no metrics were available
            if metrics_available_count == 0:
                diag = self._dmv_diagnostic or "unknown"
                cache_info = (
                    f"cache_loaded={self._cache_loaded}, cache_size={len(self._column_cache)}"
                )
                sample_refs = list(column_refs)[:3]
                sample_cache = list(self._column_cache.keys())[:3] if self._column_cache else []
                result["warning"] = (
                    f"No VertiPaq metrics matched. DMV: {diag}. {cache_info}. "
                    f"Refs: {sample_refs}. Cache sample: {sample_cache}."
                )
                logger.warning(result["warning"])

            return result

        except Exception as e:
            logger.error(f"Error analyzing DAX columns: {e}", exc_info=True)
            return {"success": False, "error": str(e), "columns_analyzed": 0}

    def _extract_column_references(self, dax: str) -> Set[str]:
        """Qualified column references (``Table[Column]``) — lexer-driven, so
        strings and comments never contribute (WP3 ``scan_references``)."""
        from core.dax.dax_reference_parser import scan_references

        return {
            f"{ref.table}[{ref.name}]" for ref in scan_references(dax) if ref.kind == "qualified"
        }

    def _normalize_column_ref(self, col_ref: str) -> str:
        """Normalize column reference to standard format"""
        from core.dax.dax_names import split_qualified

        table, column = split_qualified(col_ref)
        return f"{table}[{column}]" if column else col_ref.replace("'", "")

    def _calculate_column_cardinality(self, column_ref: str) -> Optional[ColumnMetrics]:
        """Exact DISTINCTCOUNT for one column (not in the DMV cache).

        Uses the extractor's batched stat query (quoting/escaping handled
        there). The size is reported as 0 — the old version fabricated one from
        "20 bytes per value" and returned it as if measured.
        """
        try:
            if not self.connection_state or not self.connection_state.is_connected():
                return None
            qe = self.connection_state.query_executor
            if not qe:
                return None
            from core.dax.dax_names import split_qualified
            from core.dax.vpa_extractor import collect_column_stats

            table_name, column_name = split_qualified(column_ref)
            if not table_name or not column_name:
                return None
            value = collect_column_stats(qe, [(table_name, column_name)]).get(
                f"{table_name}[{column_name}]"
            )
            if value is None:
                return None
            return ColumnMetrics(
                table_name=table_name,
                column_name=column_name,
                cardinality=int(value),
                size_bytes=0,
                data_type="unknown",
                encoding="unknown",
            )
        except Exception as e:
            logger.warning(f"Error calculating cardinality for {column_ref}: {e}", exc_info=True)
            return None

    def _determine_usage_context(self, dax: str, column_ref: str) -> str:
        """Determine how a column is used in the DAX expression"""
        # Find the position of the column reference
        pos = dax.find(column_ref)
        if pos == -1:
            # Try without table name
            col_name_match = re.search(r"\[([^\]]+)\]", column_ref)
            if col_name_match:
                col_name = col_name_match.group(0)
                pos = dax.find(col_name)

        if pos == -1:
            return "unknown"

        # Look for context around the column
        context_before = dax[max(0, pos - 50) : pos].upper()

        # Check for iterator functions
        iterator_functions = [
            "SUMX",
            "AVERAGEX",
            "MINX",
            "MAXX",
            "COUNTX",
            "FILTER",
            "ADDCOLUMNS",
            "SELECTCOLUMNS",
        ]

        for func in iterator_functions:
            if func in context_before:
                return "iterator"

        # Check for CALCULATE/FILTER
        if "CALCULATE" in context_before or "FILTER" in context_before:
            return "filter"

        # Check for aggregation functions
        if any(agg in context_before for agg in ["SUM", "AVERAGE", "MIN", "MAX", "COUNT"]):
            return "aggregation"

        return "general"

    def _assess_column_impact(
        self, metrics: ColumnMetrics, usage_context: str
    ) -> CardinalityImpact:
        """Assess performance impact of column usage"""

        cardinality = metrics.cardinality
        performance_impact = "low"
        recommendation = "No optimization needed"

        if usage_context == "iterator":
            if cardinality >= self.ITERATOR_CARDINALITY_CRITICAL:
                performance_impact = "critical"
                recommendation = (
                    f"CRITICAL: Iterator over {cardinality:,} rows will cause severe performance issues. "
                    f"Consider pre-aggregating data or using set-based operations instead of row-by-row iteration."
                )
            elif cardinality >= self.ITERATOR_CARDINALITY_WARNING:
                performance_impact = "high"
                recommendation = (
                    f"HIGH: Iterator over {cardinality:,} rows may impact performance. "
                    f"Consider using variables to cache calculations or reducing iterations."
                )
            elif cardinality >= 10_000:
                performance_impact = "medium"
                recommendation = (
                    f"MEDIUM: Iterator over {cardinality:,} rows. "
                    f"Monitor performance and consider optimization if slow."
                )

        elif usage_context == "filter":
            if cardinality >= self.FILTER_CARDINALITY_WARNING:
                performance_impact = "medium"
                recommendation = (
                    f"High-cardinality column ({cardinality:,} unique values) used in filter context. "
                    f"Consider using surrogate keys or reducing cardinality if possible."
                )

        # Check data type optimization opportunities
        if metrics.data_type == "String" and cardinality < 1000:
            recommendation += (
                " Consider converting to integer type with lookup table for better compression."
            )

        return CardinalityImpact(
            column=metrics.full_name,
            cardinality=cardinality,
            size_bytes=metrics.size_bytes,
            usage_context=usage_context,
            performance_impact=performance_impact,
            recommendation=recommendation,
        )

    def _get_optimization_suggestions(
        self, column_analysis: Dict[str, Any], dax_expression: str
    ) -> List[Dict[str, Any]]:
        """Get optimization suggestions based on column analysis"""
        suggestions = []

        for col_ref, analysis in column_analysis.items():
            if isinstance(analysis, dict) and "performance_impact" in analysis:
                impact = analysis["performance_impact"]

                if impact in ["critical", "high"]:
                    suggestions.append(
                        {
                            "column": col_ref,
                            "severity": impact,
                            "issue": f"High cardinality ({analysis['cardinality']:,}) in {analysis['usage_context']} context",
                            "recommendation": analysis["recommendation"],
                            "data_type": analysis.get("data_type", "unknown"),
                            "size_mb": analysis.get("size_mb", 0),
                        }
                    )

        # Check for string columns that could be optimized
        for col_ref, analysis in column_analysis.items():
            if isinstance(analysis, dict) and analysis.get("data_type") == "String":
                cardinality = analysis.get("cardinality", 0)
                if cardinality < 1000:
                    suggestions.append(
                        {
                            "column": col_ref,
                            "severity": "medium",
                            "issue": "String column with low cardinality",
                            "recommendation": (
                                f"Convert to integer type with lookup table. "
                                f"Current: {analysis.get('size_mb', 0):.2f} MB, "
                                f"Estimated savings: {analysis.get('size_mb', 0) * 0.7:.2f} MB"
                            ),
                            "optimization_type": "data_type",
                        }
                    )

        # Encoding-aware suggestions
        for col_ref, analysis in column_analysis.items():
            if not isinstance(analysis, dict):
                continue
            encoding = (analysis.get("encoding") or "").upper()
            cardinality = analysis.get("cardinality", 0)
            size_mb = analysis.get("size_mb", 0)

            # Hash-encoded column with very high cardinality
            if "HASH" in encoding and cardinality > 500_000:
                suggestions.append(
                    {
                        "column": col_ref,
                        "severity": "critical",
                        "issue": (
                            f"Hash-encoded column with {cardinality:,} distinct values — "
                            f"hash encoding is expensive at high cardinality"
                        ),
                        "recommendation": (
                            "Review column necessity. Consider splitting into "
                            "multiple lower-cardinality columns, or remove if unused. "
                            "Hash encoding at this cardinality causes significant memory "
                            "and query overhead."
                        ),
                        "optimization_type": "encoding",
                    }
                )
            # Numeric column with hash encoding — could benefit from value encoding
            elif "HASH" in encoding and analysis.get("data_type") in (
                "Int64",
                "Double",
                "Decimal",
                "Currency",
            ):
                suggestions.append(
                    {
                        "column": col_ref,
                        "severity": "medium",
                        "issue": (
                            f"Numeric column using hash encoding ({cardinality:,} distinct values)"
                        ),
                        "recommendation": (
                            "Reduce distinct values to enable value encoding. "
                            "Value encoding is more efficient for numeric columns — "
                            "consider rounding, binning, or removing unnecessary precision."
                        ),
                        "optimization_type": "encoding",
                    }
                )

            # Very high cardinality + large size — column splitting opportunity
            if cardinality > 1_000_000 and size_mb > 50:
                suggestions.append(
                    {
                        "column": col_ref,
                        "severity": "high",
                        "issue": (
                            f"Column has {cardinality:,} distinct values and "
                            f"{size_mb:.1f} MB — candidate for column splitting"
                        ),
                        "recommendation": (
                            "Split into multiple columns with lower cardinality. "
                            "Per SQLBI research, splitting a high-cardinality column "
                            "can reduce memory by 93%+. Example: split a datetime "
                            "column into separate date and time columns."
                        ),
                        "optimization_type": "column_splitting",
                    }
                )

        return suggestions

    def get_summary_statistics(self) -> Dict[str, Any]:
        """Get summary statistics for all columns in cache"""
        if not self._cache_loaded:
            self.load_column_metrics()

        if not self._column_cache:
            return {"error": "No metrics available"}

        total_columns = len(self._column_cache)
        total_size_bytes = sum(m.size_bytes for m in self._column_cache.values())

        # Categorize by cardinality
        cardinality_distribution = {"low": 0, "medium": 0, "high": 0, "very_high": 0}

        for metrics in self._column_cache.values():
            cardinality_distribution[metrics.cardinality_level] += 1

        # Top 10 largest columns
        largest_columns = sorted(
            self._column_cache.values(), key=lambda m: m.size_bytes, reverse=True
        )[:10]

        return {
            "total_columns": total_columns,
            "total_size_mb": round(total_size_bytes / (1024 * 1024), 2),
            "cardinality_distribution": cardinality_distribution,
            "largest_columns": [
                {
                    "column": m.full_name,
                    "size_mb": round(m.size_mb, 2),
                    "cardinality": m.cardinality,
                    "data_type": m.data_type,
                }
                for m in largest_columns
            ],
        }
