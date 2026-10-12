"""
Anomaly Detection for Debug Results

Detects statistical outliers, unexpected nulls, value distribution anomalies,
and semantically unexpected values in query results.
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_TOTAL_FLAG_KEYS = ("IsGrandTotalRowTotal", "[IsGrandTotalRowTotal]")


def _numeric(value: Any) -> Optional[float]:
    """A number from a result cell, or None (F-20).

    Result cells can arrive as numbers or as their string rendering; both count.
    Booleans are never numbers.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None  # NaN -> None
    if isinstance(value, str):
        text = value.strip().replace(" ", "")
        if not text:
            return None
        try:
            num = float(text)
        except ValueError:
            return None
        return num if num == num else None
    return None


def _is_total_row(row: Dict[str, Any]) -> bool:
    for key in _TOTAL_FLAG_KEYS:
        if key in row:
            return str(row[key]).lower() == "true"
    return False


def _is_flag_column(name: str) -> bool:
    bare = name.strip("[]")
    return bare.startswith("Is") and bare.endswith("Total")


@dataclass
class Anomaly:
    """Represents a detected anomaly in query results."""

    type: str  # 'outlier', 'null_concentration', 'null_presence', 'unexpected_negative',
    # 'extreme_percentage', 'extreme_outlier', 'empty_result', 'variance'
    severity: str  # 'info', 'warning', 'critical'
    column: str  # Column where anomaly was detected ('*' for row-level)
    description: str  # Human-readable description
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnomalyReport:
    """Complete anomaly analysis report."""

    anomalies: List[Anomaly] = field(default_factory=list)
    stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # Per-column statistics

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        if not self.anomalies:
            return {}

        return {
            "count": len(self.anomalies),
            "by_severity": {
                "critical": len([a for a in self.anomalies if a.severity == "critical"]),
                "warning": len([a for a in self.anomalies if a.severity == "warning"]),
                "info": len([a for a in self.anomalies if a.severity == "info"]),
            },
            "items": [
                {
                    "type": a.type,
                    "severity": a.severity,
                    "column": a.column,
                    "description": a.description,
                }
                for a in self.anomalies
            ],
        }

    def has_issues(self) -> bool:
        """Check if any significant anomalies were found."""
        return any(a.severity in ("warning", "critical") for a in self.anomalies)


class AnomalyDetector:
    """
    Detects anomalies in query result sets.

    Detection types:
    - Empty results (no rows returned)
    - Statistical outliers (IQR method)
    - Null concentration (>50% nulls in a column)
    - Null presence (>10% nulls, info level)
    - Unexpected negative values (in columns like NAV, asset, balance)
    - Extreme percentage values (e.g., -902% MWR, 544% return)
    - Extreme outliers (>100x median, regardless of column name)
    - High variance in numeric columns (coefficient of variation > 2.0)
    """

    # Configurable thresholds
    NULL_THRESHOLD_PCT = 0.5  # Warn if >50% nulls
    NULL_PRESENCE_PCT = 0.1  # Info if >10% nulls (presence check)
    IQR_MULTIPLIER = 3.0  # Wider IQR bound to reduce false positives on financial data
    MIN_ROWS_FOR_STATS = 10  # Minimum rows for statistical analysis
    HIGH_VARIANCE_CV = 2.0  # Coefficient of variation threshold

    # Columns that should typically be non-negative
    NON_NEGATIVE_KEYWORDS = [
        "sales",
        "revenue",
        "amount",
        "count",
        "quantity",
        "total",
        "price",
        "cost",
        "profit",
        "units",
        "volume",
        # Financial keywords
        "nav",
        "asset",
        "balance",
        "aum",
        "market value",
        "net asset",
    ]

    # Columns that represent percentages/rates (should typically be bounded)
    PERCENTAGE_KEYWORDS = [
        "%",
        "pct",
        "percent",
        "return",
        "rate",
        "yield",
        "growth",
        "mwr",
        "twr",
        "irr",
        "margin",
        "ratio",
    ]

    # Reasonable bounds for percentage values
    # Support both decimal form (1.0 = 100%) and percentage form (-902 = -902%)
    # Decimal form bounds (for values like 0.05 = 5%, 1.5 = 150%)
    PERCENTAGE_LOWER_BOUND_DECIMAL = -2.0  # -200% in decimal form
    PERCENTAGE_UPPER_BOUND_DECIMAL = 5.0  # 500% in decimal form

    # Percentage form bounds (for values like -902 = -902%, 544 = 544%)
    # These are more common in financial reports
    PERCENTAGE_LOWER_BOUND_PCT = -200  # -200% in percentage form
    PERCENTAGE_UPPER_BOUND_PCT = 500  # 500% in percentage form

    # Extreme value thresholds (for any numeric column)
    # Values beyond these are almost always anomalies
    EXTREME_VALUE_MULTIPLIER = 100  # Values > 100x median are extreme

    def __init__(self):
        """Initialize the anomaly detector."""
        pass

    def analyze(
        self, rows: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None
    ) -> AnomalyReport:
        """
        Analyze query result rows for anomalies; returns AnomalyReport.
        """
        report = AnomalyReport()

        # Check for empty results
        if not rows:
            report.anomalies.append(
                Anomaly(
                    type="empty_result",
                    severity="warning",
                    column="*",
                    description="Query returned no rows",
                    details={"row_count": 0},
                )
            )
            return report

        # Grand-total / subtotal rows are not data points: they would be the
        # biggest "outlier" of every matrix (F-20).
        data_rows = [r for r in rows if not _is_total_row(r)] or rows

        # Analyze each column
        columns = [c for c in rows[0].keys() if not _is_flag_column(c)]

        for col_name in columns:
            values = [row.get(col_name) for row in data_rows]

            # Check for null concentration
            null_anomaly = self._check_null_concentration(col_name, values)
            if null_anomaly:
                report.anomalies.append(null_anomaly)

            # Extract numeric values for statistical analysis (numeric strings count)
            numeric_values = [n for n in (_numeric(v) for v in values) if n is not None]

            if len(numeric_values) >= self.MIN_ROWS_FOR_STATS:
                # Compute and store statistics
                stats = self._compute_stats(numeric_values)
                report.stats[col_name] = stats

                # Check for statistical outliers
                outlier_anomalies = self._check_outliers(col_name, numeric_values, stats)
                report.anomalies.extend(outlier_anomalies)

                # Check for high variance
                variance_anomaly = self._check_high_variance(col_name, stats)
                if variance_anomaly:
                    report.anomalies.append(variance_anomaly)

            # Check for semantically unexpected values
            unexpected = self._check_unexpected_values(col_name, values, context)
            report.anomalies.extend(unexpected)

        return report

    def _check_null_concentration(self, col_name: str, values: List[Any]) -> Optional[Anomaly]:
        """Check if column has unexpected null concentration."""
        if not values:
            return None

        null_count = sum(1 for v in values if v is None)
        total = len(values)
        null_pct = null_count / total

        if null_count == total:
            return Anomaly(
                type="null_concentration",
                severity="critical",
                column=col_name,
                description=f"All {total} values are NULL",
                details={"null_count": null_count, "null_pct": 1.0, "total": total},
            )
        elif null_pct > self.NULL_THRESHOLD_PCT:
            return Anomaly(
                type="null_concentration",
                severity="warning",
                column=col_name,
                description=f"{null_count}/{total} ({null_pct:.0%}) values are NULL",
                details={"null_count": null_count, "null_pct": round(null_pct, 2), "total": total},
            )
        elif null_pct > self.NULL_PRESENCE_PCT:
            # Info-level alert for notable null presence (>10%)
            return Anomaly(
                type="null_presence",
                severity="info",
                column=col_name,
                description=f"{null_count}/{total} ({null_pct:.0%}) values are NULL",
                details={"null_count": null_count, "null_pct": round(null_pct, 2), "total": total},
            )

        return None

    def _check_outliers(
        self, col_name: str, values: List[float], stats: Dict[str, Any]
    ) -> List[Anomaly]:
        """Detect statistical outliers using IQR method."""
        anomalies = []

        if len(values) < self.MIN_ROWS_FOR_STATS:
            return anomalies

        q1 = stats.get("q1", 0)
        q3 = stats.get("q3", 0)
        iqr = q3 - q1

        if iqr == 0:
            return anomalies  # All values are the same, no outliers

        lower_bound = q1 - (self.IQR_MULTIPLIER * iqr)
        upper_bound = q3 + (self.IQR_MULTIPLIER * iqr)

        outliers_low = [v for v in values if v < lower_bound]
        outliers_high = [v for v in values if v > upper_bound]
        outliers = outliers_low + outliers_high

        if outliers:
            # Determine severity based on outlier count
            outlier_pct = len(outliers) / len(values)
            severity = "warning" if outlier_pct > 0.1 else "info"

            anomalies.append(
                Anomaly(
                    type="outlier",
                    severity=severity,
                    column=col_name,
                    description=f"{len(outliers)} outliers ({outlier_pct:.0%}) outside IQR bounds",
                    details={
                        "outlier_count": len(outliers),
                        "bounds": {"lower": round(lower_bound, 2), "upper": round(upper_bound, 2)},
                        "examples_low": sorted(outliers_low)[:3] if outliers_low else [],
                        "examples_high": (
                            sorted(outliers_high, reverse=True)[:3] if outliers_high else []
                        ),
                    },
                )
            )

        return anomalies

    def _check_high_variance(self, col_name: str, stats: Dict[str, Any]) -> Optional[Anomaly]:
        """Check for unusually high variance (coefficient of variation)."""
        mean = stats.get("mean", 0)
        stdev = stats.get("stdev", 0)

        if mean == 0 or stdev == 0:
            return None

        cv = abs(stdev / mean)  # Coefficient of variation

        if cv > self.HIGH_VARIANCE_CV:
            return Anomaly(
                type="variance",
                severity="info",
                column=col_name,
                description=f"High variance detected (CV={cv:.2f})",
                details={
                    "coefficient_of_variation": round(cv, 2),
                    "mean": round(mean, 2),
                    "stdev": round(stdev, 2),
                },
            )

        return None

    def _check_unexpected_values(
        self, col_name: str, values: List[Any], context: Optional[Dict[str, Any]]
    ) -> List[Anomaly]:
        """Check for semantically unexpected values based on column name."""
        anomalies = []
        col_lower = col_name.lower()

        # Get numeric values (numeric strings count, F-20)
        numeric_values = [n for n in (_numeric(v) for v in values) if n is not None]

        if not numeric_values:
            return anomalies

        # Check for negative values in columns that should be non-negative
        should_be_positive = any(kw in col_lower for kw in self.NON_NEGATIVE_KEYWORDS)

        if should_be_positive:
            negative_values = [v for v in numeric_values if v < 0]

            if negative_values:
                # Severity based on magnitude - very negative values are critical
                max_negative = min(negative_values)
                severity = "critical" if max_negative < -100000 else "warning"

                anomalies.append(
                    Anomaly(
                        type="unexpected_negative",
                        severity=severity,
                        column=col_name,
                        description=f"{len(negative_values)} negative values in {col_name} (min: {max_negative:,.0f})",
                        details={
                            "negative_count": len(negative_values),
                            "min_value": max_negative,
                            "examples": sorted(negative_values)[:5],
                        },
                    )
                )

        # Check for extreme percentage values - support both decimal and percentage forms
        is_percentage = any(kw in col_lower for kw in self.PERCENTAGE_KEYWORDS)

        if is_percentage:
            # Determine if values are in decimal form (e.g., 0.05) or percentage form (e.g., 5)
            # Heuristic: if most absolute values < 10, assume decimal form
            abs_values = [abs(v) for v in numeric_values if v != 0]
            if abs_values:
                median_abs = sorted(abs_values)[len(abs_values) // 2]
                is_decimal_form = median_abs < 10

                if is_decimal_form:
                    # Decimal form: 1.0 = 100%
                    lower_bound = self.PERCENTAGE_LOWER_BOUND_DECIMAL
                    upper_bound = self.PERCENTAGE_UPPER_BOUND_DECIMAL
                    bound_label = f"{lower_bound*100:.0f}%/{upper_bound*100:.0f}%"
                else:
                    # Percentage form: 100 = 100%
                    lower_bound = self.PERCENTAGE_LOWER_BOUND_PCT
                    upper_bound = self.PERCENTAGE_UPPER_BOUND_PCT
                    bound_label = f"{lower_bound}%/{upper_bound}%"

                extreme_low = [v for v in numeric_values if v < lower_bound]
                extreme_high = [v for v in numeric_values if v > upper_bound]

                if extreme_low or extreme_high:
                    # Severity: critical if values are extremely out of bounds
                    worst_low = min(extreme_low) if extreme_low else 0
                    worst_high = max(extreme_high) if extreme_high else 0
                    severity = (
                        "critical"
                        if (worst_low < lower_bound * 3 or worst_high > upper_bound * 2)
                        else "warning"
                    )

                    anomalies.append(
                        Anomaly(
                            type="extreme_percentage",
                            severity=severity,
                            column=col_name,
                            description=f'Extreme percentage values: {len(extreme_low)} below {lower_bound}{"%" if not is_decimal_form else ""}, {len(extreme_high)} above {upper_bound}{"%" if not is_decimal_form else ""} (bounds: {bound_label})',
                            details={
                                "extreme_low_count": len(extreme_low),
                                "extreme_high_count": len(extreme_high),
                                "examples_low": sorted(extreme_low)[:5] if extreme_low else [],
                                "examples_high": (
                                    sorted(extreme_high, reverse=True)[:5] if extreme_high else []
                                ),
                                "bounds": {"lower": lower_bound, "upper": upper_bound},
                                "value_form": "decimal" if is_decimal_form else "percentage",
                            },
                        )
                    )

        # Check for extreme values regardless of column name (universal check)
        # This catches outliers that don't match keyword patterns
        if len(numeric_values) >= 3:
            sorted_vals = sorted(numeric_values)
            median = sorted_vals[len(sorted_vals) // 2]

            if median != 0:
                # Find values that are extremely far from the median
                extreme_outliers = []
                for v in numeric_values:
                    ratio = abs(v / median) if median != 0 else abs(v)
                    if ratio > self.EXTREME_VALUE_MULTIPLIER:
                        extreme_outliers.append(v)

                if extreme_outliers:
                    anomalies.append(
                        Anomaly(
                            type="extreme_outlier",
                            severity="warning",
                            column=col_name,
                            description=f"{len(extreme_outliers)} extreme outliers (>{self.EXTREME_VALUE_MULTIPLIER}x median of {median:,.2f})",
                            details={
                                "outlier_count": len(extreme_outliers),
                                "median": round(median, 4),
                                "examples": sorted(
                                    extreme_outliers, key=lambda x: abs(x), reverse=True
                                )[:5],
                            },
                        )
                    )

        return anomalies

    def _compute_stats(self, values: List[float]) -> Dict[str, Any]:
        """Compute basic statistics for a numeric column."""
        if not values:
            return {}

        sorted_vals = sorted(values)
        n = len(sorted_vals)

        # Calculate mean
        mean = sum(values) / n

        # Calculate standard deviation
        if n > 1:
            variance = sum((x - mean) ** 2 for x in values) / (n - 1)
            stdev = variance**0.5
        else:
            stdev = 0

        # Calculate quartiles
        q1_idx = n // 4
        q3_idx = (3 * n) // 4
        median_idx = n // 2

        return {
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "mean": round(mean, 4),
            "median": sorted_vals[median_idx],
            "stdev": round(stdev, 4),
            "q1": sorted_vals[q1_idx],
            "q3": sorted_vals[q3_idx],
            "count": n,
        }


def analyze_results(rows: List[Dict], context: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Convenience wrapper: analyze rows and return anomaly dict (empty if no anomalies).
    """
    detector = AnomalyDetector()
    report = detector.analyze(rows, context)
    return report.to_dict()
