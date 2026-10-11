"""``DebugOperations`` methods split out of ``core.debug.debug_operations`` (C-17): pure move, no behaviour change.

``DebugOperations`` inherits :class:`DebugAnalysisMixin`, so every ``self.<method>`` call and every import of
``core.debug.debug_operations.DebugOperations`` keeps working.
"""

from typing import Any, Dict, List, Optional
from core.debug.debug_types import _first_value


class DebugAnalysisMixin:
    """Methods of :class:`DebugOperations` (analysis_ops.py)."""

    def decompose_value(
        self,
        page_name: str,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        dimension: Optional[str] = None,
        top_n: int = 10,
    ) -> Dict[str, Any]:
        """
        Break down an aggregated value by dimensions (dimension=None auto-detects).
        """
        if not self.qe:
            return {"success": False, "error": "Not connected to Power BI model"}

        query_result = self.builder.build_visual_query(
            page_name=page_name, visual_id=visual_id, visual_name=visual_name
        )

        if not query_result:
            return {"success": False, "error": "Visual not found"}

        # Get total value first: the measure over everything the visual shows,
        # read from ITS column (F-15), not the first column of the visual query.
        total_query, total_alias = self.builder.build_total_query(query_result)
        total_result = self.qe.validate_and_execute_dax(total_query, top_n=1, bypass_cache=True)
        total_value = None
        if total_result.get("success"):
            total_value = _first_value(total_result, total_alias)

        # Determine dimension to decompose by
        if not dimension:
            # Try to find a good dimension from the visual or model
            if query_result.visual_info.columns:
                dimension = query_result.visual_info.columns[0]
            else:
                # Query available dimensions
                return {
                    "success": False,
                    "error": "No dimension specified. Provide a dimension like \"'Product'[Category]\"",
                }

        # Build decomposition query
        measure = (
            query_result.visual_info.measures[0]
            if query_result.visual_info.measures
            else query_result.measure_name
        )
        measure = self.builder._as_measure_ref(measure)

        from .filter_to_dax import calculate_filter_args

        filter_dax, not_applied = calculate_filter_args(query_result.filter_context.all_filters())
        filter_str = ", ".join(filter_dax) if filter_dax else ""

        if filter_str:
            decomp_query = f"""EVALUATE
TOPN(
    {top_n},
    ADDCOLUMNS(
        CALCULATETABLE(VALUES({dimension}), {filter_str}),
        "Value", CALCULATE({measure}, {filter_str})
    ),
    [Value], DESC
)"""
        else:
            decomp_query = f"""EVALUATE
TOPN(
    {top_n},
    ADDCOLUMNS(
        VALUES({dimension}),
        "Value", {measure}
    ),
    [Value], DESC
)"""

        decomp_result = self.qe.validate_and_execute_dax(
            decomp_query, top_n=top_n, bypass_cache=True
        )

        if not decomp_result.get("success"):
            return {"success": False, "error": decomp_result.get("error"), "query": decomp_query}

        # Process results
        components = []
        running_total = 0

        for row in decomp_result.get("rows", []):
            values = list(row.values())
            dim_value = values[0] if len(values) > 0 else None
            measure_value = values[1] if len(values) > 1 else values[0]

            try:
                num_value = float(measure_value) if measure_value is not None else 0
                pct = (num_value / float(total_value) * 100) if total_value else 0
                running_total += num_value
            except (ValueError, TypeError):
                num_value = measure_value
                pct = 0

            components.append(
                {
                    "dimension_value": dim_value,
                    "value": measure_value,
                    "percentage": round(pct, 1),
                    "cumulative_pct": (
                        round(running_total / float(total_value) * 100, 1) if total_value else 0
                    ),
                }
            )

        return {
            "success": True,
            "visual": {
                "id": query_result.visual_info.visual_id,
                "name": query_result.visual_info.visual_name,
                "page": page_name,
            },
            "total_value": total_value,
            "dimension": dimension,
            "top_n": top_n,
            "components": components,
            "coverage": components[-1]["cumulative_pct"] if components else 0,
            "filters_not_applied": not_applied,
        }

    def contribution_analysis(
        self,
        page_name: str,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        dimension: Optional[str] = None,
        top_n: int = 10,
    ) -> Dict[str, Any]:
        """
        Identify which dimension values contribute most to the total (Pareto analysis).

        Similar to decompose but focused on identifying the 80/20 contributors.
        """
        decomp = self.decompose_value(page_name, visual_id, visual_name, dimension, top_n)

        if not decomp.get("success"):
            return decomp

        components = decomp.get("components", [])

        # Find the 80% threshold
        pareto_contributors = []
        others = []

        for comp in components:
            if comp["cumulative_pct"] <= 80:
                pareto_contributors.append(comp)
            else:
                others.append(comp)

        return {
            "success": True,
            "visual": decomp.get("visual"),
            "total_value": decomp.get("total_value"),
            "dimension": decomp.get("dimension"),
            "pareto_analysis": {
                "top_contributors": pareto_contributors,
                "top_contributor_count": len(pareto_contributors),
                "top_contributor_coverage": (
                    pareto_contributors[-1]["cumulative_pct"] if pareto_contributors else 0
                ),
                "remaining_items": len(others),
                "remaining_coverage": 100
                - (pareto_contributors[-1]["cumulative_pct"] if pareto_contributors else 0),
            },
            "insight": f'{len(pareto_contributors)} of {len(components)} {decomp.get("dimension", "items")} contribute ~80% of the total',
        }

    def trend_analysis(
        self,
        page_name: str,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        date_column: Optional[str] = None,
        granularity: str = "month",
    ) -> Dict[str, Any]:
        """
        Analyze a visual's value trend over time at a given granularity.
        """
        if not self.qe:
            return {"success": False, "error": "Not connected to Power BI model"}

        query_result = self.builder.build_visual_query(
            page_name=page_name, visual_id=visual_id, visual_name=visual_name
        )

        if not query_result:
            return {"success": False, "error": "Visual not found"}

        # Find date column
        if not date_column:
            # Try to detect from columns
            for col in query_result.visual_info.columns:
                if "date" in col.lower() or "time" in col.lower():
                    date_column = col
                    break

        if not date_column:
            return {
                "success": False,
                "error": "No date column specified. Provide date_column like \"'Date'[Date]\"",
            }

        measure = (
            query_result.visual_info.measures[0]
            if query_result.visual_info.measures
            else query_result.measure_name
        )
        measure = self.builder._as_measure_ref(measure)

        trend_query = self._trend_query(query_result, measure, date_column, granularity)
        result = self.qe.validate_and_execute_dax(trend_query, top_n=0, bypass_cache=True)

        if not result.get("success"):
            return {"success": False, "error": result.get("error"), "query": trend_query}

        # Process trend data
        trend_data = []
        prev_value = None

        from .visual_query_builder import row_value

        for row in result.get("rows", []):
            period = row_value(row, "Period")
            value = row_value(row, "Value")

            growth = None
            growth_pct = None

            if prev_value is not None and value is not None:
                try:
                    growth = float(value) - float(prev_value)
                    growth_pct = (growth / float(prev_value) * 100) if prev_value != 0 else None
                except (ValueError, TypeError):
                    pass

            trend_data.append(
                {
                    "period": period,
                    "value": value,
                    "growth": growth,
                    "growth_pct": round(growth_pct, 1) if growth_pct is not None else None,
                }
            )

            prev_value = value

        # Calculate overall trend
        if len(trend_data) >= 2:
            first_val = trend_data[0]["value"]
            last_val = trend_data[-1]["value"]
            try:
                overall_growth = float(last_val) - float(first_val)
                overall_growth_pct = (
                    (overall_growth / float(first_val) * 100) if first_val else None
                )
            except (ValueError, TypeError):
                overall_growth = None
                overall_growth_pct = None
        else:
            overall_growth = None
            overall_growth_pct = None

        # Determine trend direction
        positive_periods = sum(1 for t in trend_data if t.get("growth_pct") and t["growth_pct"] > 0)
        negative_periods = sum(1 for t in trend_data if t.get("growth_pct") and t["growth_pct"] < 0)

        if positive_periods > negative_periods * 2:
            trend_direction = "strongly_upward"
        elif positive_periods > negative_periods:
            trend_direction = "upward"
        elif negative_periods > positive_periods * 2:
            trend_direction = "strongly_downward"
        elif negative_periods > positive_periods:
            trend_direction = "downward"
        else:
            trend_direction = "stable"

        return {
            "success": True,
            "visual": {
                "id": query_result.visual_info.visual_id,
                "name": query_result.visual_info.visual_name,
                "page": page_name,
            },
            "date_column": date_column,
            "granularity": granularity,
            "periods": len(trend_data),
            "trend_direction": trend_direction,
            "overall_growth": overall_growth,
            "overall_growth_pct": round(overall_growth_pct, 1) if overall_growth_pct else None,
            "data": trend_data[:200],  # Limit output
            "data_truncated": len(trend_data) > 200,
            "query": trend_query,
        }

    def _trend_query(self, query_result, measure: str, date_column: str, granularity: str) -> str:
        """One row per PERIOD (not per day), in the visual's filter context (F-28).

        Periods are real date ranges ([__Start], [__End]) so weeks never merge
        across years and month/quarter growth is period-over-period. The measure
        is evaluated per period with the visual's DATA filters applied.
        """
        from .filter_to_dax import calculate_filter_args

        d = date_column
        day = f"DATE(YEAR({d}), MONTH({d}), DAY({d}))"
        spans = {
            "day": (day, f"{day} + 1", '"yyyy-mm-dd"'),
            "week": (
                f"{day} - WEEKDAY({d}, 2) + 1",
                f"{day} - WEEKDAY({d}, 2) + 8",
                '"yyyy-mm-dd"',
            ),
            "month": (
                f"DATE(YEAR({d}), MONTH({d}), 1)",
                f"EDATE(DATE(YEAR({d}), MONTH({d}), 1), 1)",
                '"yyyy-mm"',
            ),
            "quarter": (
                f"DATE(YEAR({d}), (QUARTER({d}) - 1) * 3 + 1, 1)",
                f"EDATE(DATE(YEAR({d}), (QUARTER({d}) - 1) * 3 + 1, 1), 3)",
                None,
            ),
            "year": (f"DATE(YEAR({d}), 1, 1)", f"DATE(YEAR({d}) + 1, 1, 1)", '"yyyy"'),
        }
        start, end, fmt = spans.get(granularity, spans["month"])
        filter_args, _skipped = calculate_filter_args(query_result.filter_context.all_filters())
        filters = ", ".join(filter_args)
        dates = f"CALCULATETABLE(VALUES({d}), {filters})" if filters else f"VALUES({d})"
        if fmt is None:
            label = 'FORMAT([__Start], "yyyy") & "-Q" & FORMAT(QUARTER([__Start]), "0")'
        else:
            label = f"FORMAT([__Start], {fmt})"
        window = f"KEEPFILTERS(FILTER(ALL({d}), {d} >= [__Start] && {d} < [__End]))"
        value_args = ", ".join([a for a in (filters, window) if a])
        return (
            "DEFINE\n"
            f"    VAR __Dates = {dates}\n"
            f'    VAR __Periods = GROUPBY(ADDCOLUMNS(__Dates, "__Start", {start}, "__End", {end}), '
            "[__Start], [__End])\n"
            "EVALUATE\n"
            f'ADDCOLUMNS(__Periods, "Period", {label}, "Value", CALCULATE({measure}, {value_args}))\n'
            "ORDER BY [__Start]"
        )

    def root_cause_analysis(
        self,
        page_name: str,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        baseline_filters: Optional[List[str]] = None,
        comparison_filters: Optional[List[str]] = None,
        dimensions: Optional[List[str]] = None,
        top_n: int = 5,
    ) -> Dict[str, Any]:
        """
        Analyze why a value changed between two filter contexts (baseline vs comparison).
        """
        if not self.qe:
            return {"success": False, "error": "Not connected to Power BI model"}

        query_result = self.builder.build_visual_query(
            page_name=page_name, visual_id=visual_id, visual_name=visual_name
        )

        if not query_result:
            return {"success": False, "error": "Visual not found"}

        measure = (
            query_result.visual_info.measures[0]
            if query_result.visual_info.measures
            else query_result.measure_name
        )
        measure = self.builder._as_measure_ref(measure)

        # Get baseline and comparison totals
        base_filters = ", ".join(baseline_filters) if baseline_filters else ""
        comp_filters = ", ".join(comparison_filters) if comparison_filters else ""

        if base_filters:
            base_query = f'EVALUATE ROW("Value", CALCULATE({measure}, {base_filters}))'
        else:
            base_query = f'EVALUATE ROW("Value", {measure})'

        if comp_filters:
            comp_query = f'EVALUATE ROW("Value", CALCULATE({measure}, {comp_filters}))'
        else:
            comp_query = f'EVALUATE ROW("Value", {measure})'

        base_result = self.qe.validate_and_execute_dax(base_query, top_n=1, bypass_cache=True)
        comp_result = self.qe.validate_and_execute_dax(comp_query, top_n=1, bypass_cache=True)

        baseline_value = None
        comparison_value = None

        if base_result.get("success") and base_result.get("rows"):
            baseline_value = list(base_result["rows"][0].values())[0]

        if comp_result.get("success") and comp_result.get("rows"):
            comparison_value = list(comp_result["rows"][0].values())[0]

        if baseline_value is None or comparison_value is None:
            return {
                "success": False,
                "error": "Could not get baseline or comparison values",
                "baseline_error": base_result.get("error"),
                "comparison_error": comp_result.get("error"),
            }

        try:
            total_change = float(comparison_value) - float(baseline_value)
            change_pct = (total_change / float(baseline_value) * 100) if baseline_value else None
        except (ValueError, TypeError):
            total_change = None
            change_pct = None

        # If no dimensions specified, try to use visual columns
        if not dimensions:
            dimensions = (
                query_result.visual_info.columns[:3] if query_result.visual_info.columns else []
            )

        # Analyze each dimension
        dimension_impacts = []

        for dim in dimensions:
            try:
                # Get breakdown by dimension for baseline and comparison
                if base_filters:
                    dim_base_query = f"""EVALUATE
ADDCOLUMNS(
    CALCULATETABLE(VALUES({dim}), {base_filters}),
    "Value", CALCULATE({measure}, {base_filters})
)"""
                else:
                    dim_base_query = f"""EVALUATE
ADDCOLUMNS(
    VALUES({dim}),
    "Value", {measure}
)"""

                if comp_filters:
                    dim_comp_query = f"""EVALUATE
ADDCOLUMNS(
    CALCULATETABLE(VALUES({dim}), {comp_filters}),
    "Value", CALCULATE({measure}, {comp_filters})
)"""
                else:
                    dim_comp_query = dim_base_query

                dim_base_result = self.qe.validate_and_execute_dax(
                    dim_base_query, top_n=50, bypass_cache=True
                )
                dim_comp_result = self.qe.validate_and_execute_dax(
                    dim_comp_query, top_n=50, bypass_cache=True
                )

                if not dim_base_result.get("success") or not dim_comp_result.get("success"):
                    continue

                # Build value maps
                base_map = {}
                for row in dim_base_result.get("rows", []):
                    vals = list(row.values())
                    if len(vals) >= 2:
                        base_map[vals[0]] = vals[1]

                comp_map = {}
                for row in dim_comp_result.get("rows", []):
                    vals = list(row.values())
                    if len(vals) >= 2:
                        comp_map[vals[0]] = vals[1]

                # Find top changers
                changes = []
                all_keys = set(base_map.keys()) | set(comp_map.keys())

                for key in all_keys:
                    base_val = base_map.get(key, 0) or 0
                    comp_val = comp_map.get(key, 0) or 0

                    try:
                        change = float(comp_val) - float(base_val)
                        changes.append(
                            {
                                "value": key,
                                "baseline": base_val,
                                "comparison": comp_val,
                                "change": change,
                                "abs_change": abs(change),
                            }
                        )
                    except (ValueError, TypeError):
                        continue

                # Sort by absolute change
                changes.sort(key=lambda x: x["abs_change"], reverse=True)
                top_changes = changes[:top_n]

                dimension_impacts.append(
                    {
                        "dimension": dim,
                        "top_changes": top_changes,
                        "total_positive": sum(c["change"] for c in changes if c["change"] > 0),
                        "total_negative": sum(c["change"] for c in changes if c["change"] < 0),
                    }
                )

            except Exception as e:
                self.logger.warning(f"Error analyzing dimension {dim}: {e}")

        return {
            "success": True,
            "visual": {
                "id": query_result.visual_info.visual_id,
                "name": query_result.visual_info.visual_name,
                "page": page_name,
            },
            "baseline": {"filters": baseline_filters or [], "value": baseline_value},
            "comparison": {"filters": comparison_filters or [], "value": comparison_value},
            "total_change": total_change,
            "change_pct": round(change_pct, 1) if change_pct else None,
            "dimension_analysis": dimension_impacts,
        }
