"""
Aggregation Report Builder Module

Generates HTML and JSON reports for aggregation analysis results.
Includes enhanced visualizations for:
- Aggregation Coverage Map
- Quality Analysis Dashboard
- Hit Rate Analysis with Miss Reasons
- Slicer Impact Analysis
- Cross-Filter Impact Matrix
- Automatic Recommendations with TMDL/DAX Code
"""

import json
import html
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Any

from .aggregation_analyzer import AggregationAnalysisResult

# Import new analysis result types

logger = logging.getLogger(__name__)


class AggregationReportBuilder:
    """Generates reports for aggregation analysis."""

    def __init__(self, analysis_result: AggregationAnalysisResult):
        """
        Initialize the report builder.

        Args:
            analysis_result: Complete aggregation analysis result
        """
        self.result = analysis_result

    def build_summary_text(self) -> str:
        """Build a text summary of the analysis."""
        lines = []
        lines.append("=" * 60)
        lines.append("AGGREGATION ANALYSIS SUMMARY")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"Model: {self.result.model_name}")
        lines.append(f"Path: {self.result.model_path}")
        lines.append(f"Analysis Time: {self.result.analysis_timestamp}")
        lines.append("")

        # Aggregation Infrastructure
        lines.append("-" * 40)
        lines.append("AGGREGATION INFRASTRUCTURE")
        lines.append("-" * 40)
        lines.append("")

        lines.append(f"Aggregation Tables Found: {len(self.result.aggregation_tables)}")
        for i, table in enumerate(self.result.aggregation_tables, 1):
            lines.append(f"  {i}. {table.name} (Level {table.level} - {table.level_name})")
            if table.grain_columns:
                lines.append(f"     Grain: {', '.join(table.grain_columns[:5])}")
            if table.aggregated_columns:
                col_names = [c.name for c in table.aggregated_columns[:5]]
                lines.append(f"     Measures: {', '.join(col_names)}")
        lines.append("")

        if self.result.agg_level_measures:
            lines.append(
                f"Aggregation Level Measure: {self.result.agg_level_measures[0].table}[{self.result.agg_level_measures[0].name}]"
            )
            alm = self.result.agg_level_measures[0]
            for level, desc in alm.levels.items():
                triggers = []
                if level == 1:
                    triggers = alm.detail_trigger_columns[:3]
                elif level == 2:
                    triggers = alm.mid_level_trigger_columns[:3]
                trigger_str = f" (Triggered by: {', '.join(triggers)})" if triggers else ""
                lines.append(f"  Level {level} ({desc}){trigger_str}")
            lines.append("")

        lines.append(f"Aggregation-Aware Measures: {len(self.result.agg_aware_measures)}")
        for measure in self.result.agg_aware_measures[:10]:
            lines.append(f"  - {measure.name}")
        if len(self.result.agg_aware_measures) > 10:
            lines.append(f"  ... and {len(self.result.agg_aware_measures) - 10} more")
        lines.append("")

        # Report Analysis
        if self.result.report_summary:
            lines.append("-" * 40)
            lines.append("REPORT ANALYSIS")
            lines.append("-" * 40)
            lines.append("")

            rs = self.result.report_summary
            lines.append(f"Total Pages: {rs.total_pages}")
            lines.append(f"Total Visuals: {rs.total_visuals}")
            lines.append(f"Visuals Analyzed: {rs.visuals_analyzed}")
            lines.append("")

            lines.append("Aggregation Distribution:")
            for table, count in rs.agg_table_breakdown.items():
                pct = rs.agg_table_percentages.get(table, 0)
                lines.append(f"  {table}: {count} visuals ({pct:.1f}%)")
            lines.append("")

            lines.append(f"OPTIMIZATION SCORE: {rs.optimization_score:.0f}/100")
            lines.append("")

            # Recommendations
            if rs.recommendations:
                lines.append("-" * 40)
                lines.append("RECOMMENDATIONS")
                lines.append("-" * 40)
                lines.append("")
                for i, rec in enumerate(rs.recommendations, 1):
                    lines.append(f"{i}. {rec}")
                lines.append("")

        # Errors/Warnings
        if self.result.errors:
            lines.append("-" * 40)
            lines.append("ERRORS")
            lines.append("-" * 40)
            for err in self.result.errors:
                lines.append(f"  - {err}")
            lines.append("")

        if self.result.warnings:
            lines.append("-" * 40)
            lines.append("WARNINGS")
            lines.append("-" * 40)
            for warn in self.result.warnings:
                lines.append(f"  - {warn}")
            lines.append("")

        return "\n".join(lines)

    def build_detailed_text(self) -> str:
        """Build a detailed text report including per-visual analysis in a readable format."""
        lines = [self.build_summary_text()]

        if self.result.report_summary:
            lines.append("")
            lines.append("=" * 70)
            lines.append("📊 VISUAL ANALYSIS BY PAGE")
            lines.append("=" * 70)

            # Level labels for readability
            level_labels = {
                1: "Base Table (detail data)",
                2: "Mid-Level Aggregation",
                3: "High-Level Aggregation",
            }

            for page in self.result.report_summary.pages:
                # Page header with summary
                level_summary = " | ".join(
                    f"L{lvl}: {cnt}" for lvl, cnt in sorted(page.agg_level_breakdown.items())
                )
                lines.append("")
                lines.append(f"┌{'─' * 68}┐")
                lines.append(f"│ 📄 {page.page_name:<63} │")
                lines.append(f"│    {page.visuals_analyzed} visuals • {level_summary:<53} │")
                lines.append(f"└{'─' * 68}┘")

                for visual in page.visuals:
                    title = visual.visual_title or f"[{visual.visual_id[:12]}...]"
                    level = visual.determined_agg_level
                    level_label = level_labels.get(level, f"Level {level}")
                    table = visual.determined_agg_table or "Base Table"

                    # Visual entry
                    lines.append("")
                    lines.append(f"  ▶ {title}")
                    lines.append(f"    Type: {visual.visual_type}")
                    lines.append(f"    Aggregation: Level {level} ({level_label})")
                    lines.append(f"    Using Table: {table}")
                    lines.append(f"    Reason: {visual.reasoning}")

                    if visual.measures_used:
                        lines.append(f"    Measures: {', '.join(visual.measures_used)}")

                    if visual.columns_in_context:
                        cols = [f"{c.table}[{c.column}]" for c in visual.columns_in_context[:4]]
                        extra = (
                            f" (+{len(visual.columns_in_context) - 4} more)"
                            if len(visual.columns_in_context) > 4
                            else ""
                        )
                        lines.append(f"    Filter Context: {', '.join(cols)}{extra}")

                    if visual.optimization_notes:
                        for note in visual.optimization_notes:
                            lines.append(f"    ⚠️ {note}")

            lines.append("")
            lines.append("=" * 70)

        return "\n".join(lines)

    def build_json_export(self) -> Dict[str, Any]:
        """Build a JSON-serializable dictionary of the analysis."""

        def serialize_dataclass(obj):
            """Recursively serialize dataclass objects."""
            if hasattr(obj, "__dataclass_fields__"):
                return {k: serialize_dataclass(v) for k, v in asdict(obj).items()}
            elif isinstance(obj, list):
                return [serialize_dataclass(item) for item in obj]
            elif isinstance(obj, dict):
                return {k: serialize_dataclass(v) for k, v in obj.items()}
            elif hasattr(obj, "value"):  # Enum
                return obj.value
            else:
                return obj

        result_dict = {
            "model_name": self.result.model_name,
            "model_path": self.result.model_path,
            "analysis_timestamp": self.result.analysis_timestamp,
            "has_report": self.result.has_report,
            "aggregation_tables": [
                {
                    "name": t.name,
                    "level": t.level,
                    "level_name": t.level_name,
                    "is_hidden": t.is_hidden,
                    "grain_columns": t.grain_columns,
                    "aggregated_columns": [
                        {"name": c.name, "function": c.aggregation_function}
                        for c in t.aggregated_columns
                    ],
                    "related_dimensions": t.related_dimensions,
                    "detection_confidence": t.detection_confidence,
                }
                for t in self.result.aggregation_tables
            ],
            "base_fact_tables": self.result.base_fact_tables,
            "agg_level_measures": [
                {
                    "table": m.table,
                    "name": m.name,
                    "detail_trigger_columns": m.detail_trigger_columns,
                    "mid_level_trigger_columns": m.mid_level_trigger_columns,
                    "levels": m.levels,
                }
                for m in self.result.agg_level_measures
            ],
            "agg_aware_measures": [
                {
                    "table": m.table,
                    "name": m.name,
                    "uses_agg_level_measure": m.uses_agg_level_measure,
                    "table_switches": m.table_switches,
                    "is_base_only": m.is_base_only,
                }
                for m in self.result.agg_aware_measures
            ],
            "errors": self.result.errors,
            "warnings": self.result.warnings,
        }

        if self.result.report_summary:
            rs = self.result.report_summary
            result_dict["report_summary"] = {
                "total_pages": rs.total_pages,
                "total_visuals": rs.total_visuals,
                "visuals_analyzed": rs.visuals_analyzed,
                "agg_table_breakdown": rs.agg_table_breakdown,
                "agg_level_breakdown": {str(k): v for k, v in rs.agg_level_breakdown.items()},
                "agg_table_percentages": rs.agg_table_percentages,
                "optimization_score": rs.optimization_score,
                "recommendations": rs.recommendations,
                "pages": [
                    {
                        "page_id": p.page_id,
                        "page_name": p.page_name,
                        "total_visuals": p.total_visuals,
                        "visuals_analyzed": p.visuals_analyzed,
                        "agg_table_breakdown": p.agg_table_breakdown,
                        "agg_level_breakdown": {
                            str(k): v for k, v in p.agg_level_breakdown.items()
                        },
                        "optimization_opportunities": p.optimization_opportunities,
                        "visuals": [
                            {
                                "visual_id": v.visual_id,
                                "visual_type": v.visual_type,
                                "visual_title": v.visual_title,
                                "measures_used": v.measures_used,
                                "agg_level": v.determined_agg_level,
                                "agg_level_name": v.determined_agg_level_name,
                                "agg_table": v.determined_agg_table,
                                "reasoning": v.reasoning,
                                "columns_in_context": [
                                    f"{c.table}[{c.column}]" for c in v.columns_in_context
                                ],
                                "optimization_notes": v.optimization_notes,
                            }
                            for v in p.visuals
                        ],
                    }
                    for p in rs.pages
                ],
            }

        return result_dict

    def build_html_report(self) -> str:
        """Build an interactive HTML report."""
        # Get color palette
        colors = {
            "primary": "#2563eb",
            "success": "#16a34a",
            "warning": "#d97706",
            "danger": "#dc2626",
            "info": "#0891b2",
            "bg": "#f8fafc",
            "card": "#ffffff",
            "text": "#1e293b",
            "text_muted": "#64748b",
            "border": "#e2e8f0",
        }

        # Build HTML
        html_parts = []

        # Header
        html_parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aggregation Analysis - {html.escape(self.result.model_name)}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: {colors["bg"]};
            color: {colors["text"]};
            line-height: 1.6;
            padding: 20px;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .header {{
            background: linear-gradient(135deg, {colors["primary"]}, #1d4ed8);
            color: white;
            padding: 30px;
            border-radius: 12px;
            margin-bottom: 24px;
        }}
        .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .header .subtitle {{ opacity: 0.9; font-size: 14px; }}
        .card {{
            background: {colors["card"]};
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            border: 1px solid {colors["border"]};
        }}
        .card h2 {{
            font-size: 18px;
            margin-bottom: 16px;
            padding-bottom: 12px;
            border-bottom: 1px solid {colors["border"]};
        }}
        .grid {{ display: grid; gap: 20px; }}
        .grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}
        .grid-3 {{ grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); }}
        .stat-box {{
            text-align: center;
            padding: 20px;
            background: {colors["bg"]};
            border-radius: 8px;
        }}
        .stat-value {{ font-size: 32px; font-weight: bold; color: {colors["primary"]}; }}
        .stat-label {{ font-size: 14px; color: {colors["text_muted"]}; margin-top: 4px; }}
        .score-ring {{
            width: 120px;
            height: 120px;
            margin: 0 auto 16px;
            position: relative;
        }}
        .score-ring svg {{ transform: rotate(-90deg); }}
        .score-ring .bg {{ fill: none; stroke: {colors["border"]}; stroke-width: 12; }}
        .score-ring .progress {{ fill: none; stroke-width: 12; stroke-linecap: round; transition: stroke-dashoffset 0.5s; }}
        .score-value {{
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            font-size: 28px;
            font-weight: bold;
        }}
        .table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
        .table th, .table td {{ padding: 12px; text-align: left; border-bottom: 1px solid {colors["border"]}; }}
        .table th {{ background: {colors["bg"]}; font-weight: 600; }}
        .table tr:hover {{ background: {colors["bg"]}; }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
        }}
        .badge-success {{ background: #dcfce7; color: #166534; }}
        .badge-warning {{ background: #fef3c7; color: #92400e; }}
        .badge-danger {{ background: #fee2e2; color: #991b1b; }}
        .badge-info {{ background: #e0f2fe; color: #075985; }}
        .progress-bar {{
            height: 8px;
            background: {colors["border"]};
            border-radius: 4px;
            overflow: hidden;
        }}
        .progress-bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
        .collapsible {{ cursor: pointer; user-select: none; }}
        .collapsible:hover {{ background: {colors["bg"]}; }}
        .collapsible::before {{ content: "▶"; display: inline-block; margin-right: 8px; transition: transform 0.2s; }}
        .collapsible.active::before {{ transform: rotate(90deg); }}
        .collapse-content {{ display: none; padding: 16px; background: {colors["bg"]}; border-radius: 8px; margin-top: 8px; }}
        .collapse-content.show {{ display: block; }}
        .chart-container {{ height: 200px; display: flex; align-items: flex-end; gap: 8px; padding: 20px 0; }}
        .chart-bar {{ flex: 1; background: {colors["primary"]}; border-radius: 4px 4px 0 0; transition: height 0.3s; position: relative; }}
        .chart-bar:hover {{ opacity: 0.8; }}
        .chart-label {{ text-align: center; font-size: 11px; color: {colors["text_muted"]}; margin-top: 8px; }}
        .recommendation {{ padding: 12px 16px; background: #fef3c7; border-left: 4px solid #d97706; margin-bottom: 8px; border-radius: 0 8px 8px 0; }}
        .visual-card {{ padding: 16px; border: 1px solid {colors["border"]}; border-radius: 8px; margin-bottom: 12px; }}
        .visual-card h4 {{ font-size: 14px; margin-bottom: 8px; }}
        .visual-meta {{ font-size: 12px; color: {colors["text_muted"]}; }}
        code {{ background: {colors["bg"]}; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
    </style>
</head>
<body>
<div class="container">
""")

        # Header
        html_parts.append(f"""
    <div class="header">
        <h1>Aggregation Analysis Report</h1>
        <div class="subtitle">{html.escape(self.result.model_name)} | Generated: {self.result.analysis_timestamp[:19]}</div>
    </div>
""")

        # Overview Stats
        html_parts.append(self._build_overview_section(colors))

        # Aggregation Infrastructure
        html_parts.append(self._build_infrastructure_section(colors))

        # Report Analysis (if available)
        if self.result.report_summary:
            html_parts.append(self._build_report_analysis_section(colors))

        # NEW: Coverage Map
        if self.result.report_summary:
            html_parts.append(self._build_coverage_map_section(colors))

        # NEW: Quality Analysis
        if self.result.quality_analysis:
            html_parts.append(self._build_quality_analysis_section(colors))

        # NEW: Hit Rate Analysis
        if self.result.hit_rate_analysis:
            html_parts.append(self._build_hit_rate_section(colors))

        # NEW: Slicer Impact Analysis
        if self.result.slicer_impact_analysis:
            html_parts.append(self._build_slicer_impact_section(colors))

        # NEW: Cross-Filter Analysis
        if self.result.cross_filter_analysis:
            html_parts.append(self._build_cross_filter_section(colors))

        # NEW: Aggregation Recommendations
        if self.result.recommendations:
            html_parts.append(self._build_auto_recommendations_section(colors))

        # Legacy Recommendations
        if self.result.report_summary and self.result.report_summary.recommendations:
            html_parts.append(self._build_recommendations_section(colors))

        # Page Details (collapsible)
        if self.result.report_summary:
            html_parts.append(self._build_page_details_section(colors))

        # Footer
        html_parts.append("""
    <div style="text-align: center; padding: 20px; color: #64748b; font-size: 12px;">
        Generated by MCP-PowerBi-Finvision Aggregation Analyzer
    </div>
</div>
<script>
document.querySelectorAll('.collapsible').forEach(el => {
    el.addEventListener('click', function() {
        this.classList.toggle('active');
        const content = this.nextElementSibling;
        content.classList.toggle('show');
    });
});
</script>
</body>
</html>
""")

        return "".join(html_parts)

    def _build_overview_section(self, colors: Dict) -> str:
        """Build the overview statistics section."""
        agg_count = len(self.result.aggregation_tables)
        measure_count = len(self.result.agg_aware_measures)
        level_count = len(self.result.agg_level_measures)

        visuals_analyzed = 0
        optimization_score = 0
        if self.result.report_summary:
            visuals_analyzed = self.result.report_summary.visuals_analyzed
            optimization_score = self.result.report_summary.optimization_score

        # Determine score color
        if optimization_score >= 70:
            score_color = colors["success"]
        elif optimization_score >= 40:
            score_color = colors["warning"]
        else:
            score_color = colors["danger"]

        # Calculate circumference for score ring
        circumference = 2 * 3.14159 * 50
        offset = circumference - (optimization_score / 100) * circumference

        return f"""
    <div class="card">
        <h2>Overview</h2>
        <div class="grid grid-3">
            <div class="stat-box">
                <div class="stat-value">{agg_count}</div>
                <div class="stat-label">Aggregation Tables</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{measure_count}</div>
                <div class="stat-label">Agg-Aware Measures</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{level_count}</div>
                <div class="stat-label">Agg Level Measures</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{visuals_analyzed}</div>
                <div class="stat-label">Visuals Analyzed</div>
            </div>
        </div>
        <div style="text-align: center; margin-top: 24px;">
            <div class="score-ring">
                <svg width="120" height="120">
                    <circle class="bg" cx="60" cy="60" r="50"/>
                    <circle class="progress" cx="60" cy="60" r="50"
                        stroke="{score_color}"
                        stroke-dasharray="{circumference}"
                        stroke-dashoffset="{offset}"/>
                </svg>
                <div class="score-value" style="color: {score_color}">{optimization_score:.0f}</div>
            </div>
            <div style="font-weight: 600;">Optimization Score</div>
            <div style="font-size: 12px; color: {colors["text_muted"]};">Higher = More Aggregation Usage</div>
        </div>
    </div>
"""

    def _build_infrastructure_section(self, colors: Dict) -> str:
        """Build the aggregation infrastructure section."""
        parts = ["""
    <div class="card">
        <h2>Aggregation Infrastructure</h2>
"""]

        # Aggregation Tables
        if self.result.aggregation_tables:
            parts.append(
                '<h3 style="font-size: 15px; margin: 16px 0 12px;">Detected Aggregation Tables</h3>'
            )
            parts.append('<table class="table"><thead><tr>')
            parts.append("<th>Table Name</th><th>Level</th><th>Grain</th><th>Confidence</th>")
            parts.append("</tr></thead><tbody>")

            for table in self.result.aggregation_tables:
                grain = ", ".join(table.grain_columns[:3]) if table.grain_columns else "-"
                if len(table.grain_columns) > 3:
                    grain += f" (+{len(table.grain_columns) - 3})"

                conf_class = (
                    "badge-success" if table.detection_confidence > 0.7 else "badge-warning"
                )
                parts.append(f"""
                <tr>
                    <td><code>{html.escape(table.name)}</code></td>
                    <td><span class="badge badge-info">Level {table.level}</span></td>
                    <td>{html.escape(grain)}</td>
                    <td><span class="badge {conf_class}">{table.detection_confidence:.0%}</span></td>
                </tr>
""")
            parts.append("</tbody></table>")

        # Aggregation Level Measure
        if self.result.agg_level_measures:
            alm = self.result.agg_level_measures[0]
            parts.append(f"""
        <h3 style="font-size: 15px; margin: 24px 0 12px;">Aggregation Level Measure</h3>
        <div style="padding: 16px; background: {colors["bg"]}; border-radius: 8px;">
            <div style="font-weight: 600; margin-bottom: 8px;">{html.escape(alm.table)}[{html.escape(alm.name)}]</div>
            <table class="table" style="margin-top: 12px;">
                <thead><tr><th>Level</th><th>Description</th><th>Trigger Columns</th></tr></thead>
                <tbody>
""")
            for level, desc in alm.levels.items():
                triggers = []
                if level == 1:
                    triggers = alm.detail_trigger_columns[:3]
                elif level == 2:
                    triggers = alm.mid_level_trigger_columns[:3]
                trigger_str = ", ".join(triggers) if triggers else "Default"
                parts.append(
                    f"<tr><td>Level {level}</td><td>{html.escape(desc)}</td><td><code>{html.escape(trigger_str)}</code></td></tr>"
                )
            parts.append("</tbody></table></div>")

        # Aggregation-Aware Measures
        if self.result.agg_aware_measures:
            parts.append(f"""
        <h3 style="font-size: 15px; margin: 24px 0 12px;">Aggregation-Aware Measures ({len(self.result.agg_aware_measures)})</h3>
        <div style="display: flex; flex-wrap: wrap; gap: 8px;">
""")
            for measure in self.result.agg_aware_measures[:20]:
                parts.append(
                    f'<span class="badge badge-success">{html.escape(measure.name)}</span>'
                )
            if len(self.result.agg_aware_measures) > 20:
                parts.append(
                    f'<span class="badge badge-info">+{len(self.result.agg_aware_measures) - 20} more</span>'
                )
            parts.append("</div>")

        parts.append("</div>")
        return "".join(parts)

    def _build_report_analysis_section(self, colors: Dict) -> str:
        """Build the report analysis section."""
        if not self.result.report_summary:
            return ""

        rs = self.result.report_summary

        # Build bar chart data
        max_count = max(rs.agg_table_breakdown.values()) if rs.agg_table_breakdown else 1
        bars = []
        for table, count in rs.agg_table_breakdown.items():
            height = (count / max_count) * 150
            color = colors["success"] if "Agg" in table else colors["warning"]
            bars.append(f"""
            <div style="flex: 1; text-align: center;">
                <div style="height: 150px; display: flex; align-items: flex-end; justify-content: center;">
                    <div style="width: 80%; height: {height}px; background: {color}; border-radius: 4px 4px 0 0;"></div>
                </div>
                <div class="chart-label">{html.escape(table[:20])}</div>
                <div style="font-weight: 600;">{count}</div>
            </div>
""")

        return f'''
    <div class="card">
        <h2>Report Analysis</h2>
        <div class="grid grid-2">
            <div>
                <h3 style="font-size: 15px; margin-bottom: 12px;">Aggregation Table Usage</h3>
                <div style="display: flex; height: 200px; align-items: flex-end; gap: 16px; padding: 20px 0;">
                    {''.join(bars)}
                </div>
            </div>
            <div>
                <h3 style="font-size: 15px; margin-bottom: 12px;">Level Distribution</h3>
                <table class="table">
                    <thead><tr><th>Level</th><th>Visuals</th><th>Percentage</th></tr></thead>
                    <tbody>
                    {''.join(f"""<tr><td>Level {level}</td><td>{count}</td><td>
                        <div class="progress-bar" style="width: 100px; display: inline-block; vertical-align: middle;">
                            <div class="progress-bar-fill" style="width: {count/rs.visuals_analyzed*100 if rs.visuals_analyzed else 0:.0f}%; background: {colors["primary"]};"></div>
                        </div>
                        {count/rs.visuals_analyzed*100 if rs.visuals_analyzed else 0:.1f}%
                    </td></tr>""" for level, count in sorted(rs.agg_level_breakdown.items()))}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
'''

    def _build_recommendations_section(self, colors: Dict) -> str:
        """Build the recommendations section."""
        if not self.result.report_summary or not self.result.report_summary.recommendations:
            return ""

        recs = "".join(
            f'<div class="recommendation">{html.escape(r)}</div>'
            for r in self.result.report_summary.recommendations
        )

        return f"""
    <div class="card">
        <h2>Recommendations</h2>
        {recs}
    </div>
"""

    def _build_page_details_section(self, colors: Dict) -> str:
        """Build the collapsible page details section with improved visual design."""
        if not self.result.report_summary:
            return ""

        # Level badge colors
        level_colors = {
            1: {"bg": "#fef2f2", "text": "#dc2626", "label": "Base Table"},
            2: {"bg": "#fefce8", "text": "#ca8a04", "label": "Mid-Level"},
            3: {"bg": "#f0fdf4", "text": "#16a34a", "label": "High-Level"},
        }

        parts = [f"""
<div class="card">
    <h2>Page Details</h2>
    <div style="font-size: 13px; color: {colors["text_muted"]}; margin-bottom: 16px;">
        Click on a page to expand and see visual details
    </div>
"""]

        for page in self.result.report_summary.pages:
            # Build level summary badges
            level_badges = []
            for level, count in sorted(page.agg_level_breakdown.items()):
                lc = level_colors.get(level, level_colors[2])
                level_badges.append(
                    f'<span style="background: {lc["bg"]}; color: {lc["text"]}; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 500; margin-right: 6px;">L{level}: {count}</span>'
                )

            parts.append(f"""
    <div class="collapsible" style="padding: 16px; border: 1px solid {colors["border"]}; border-radius: 8px; margin-bottom: 8px; cursor: pointer; transition: background 0.2s;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <strong style="font-size: 15px;">{html.escape(page.page_name)}</strong>
                <span style="color: {colors["text_muted"]}; margin-left: 12px; font-size: 13px;">
                    {page.visuals_analyzed} visual{"s" if page.visuals_analyzed != 1 else ""}
                </span>
            </div>
            <div>{''.join(level_badges)}</div>
        </div>
    </div>
    <div class="collapse-content" style="margin-left: 16px; margin-bottom: 16px;">
        <div style="display: grid; gap: 12px; margin-top: 8px;">
""")

            for visual in page.visuals:
                level = visual.determined_agg_level
                lc = level_colors.get(level, level_colors[2])

                # Build measures list with nice formatting
                measures_html = ""
                if visual.measures_used:
                    measures_html = f"""
                <div style="margin-top: 10px;">
                    <span style="font-weight: 500; color: {colors["text"]};">Measures:</span>
                    <span style="color: {colors["text_muted"]}; font-size: 13px;"> {html.escape(", ".join(visual.measures_used))}</span>
                </div>"""

                # Build columns in context
                columns_html = ""
                if visual.columns_in_context:
                    cols = [f"{c.table}[{c.column}]" for c in visual.columns_in_context[:5]]
                    if len(visual.columns_in_context) > 5:
                        cols.append(f"(+{len(visual.columns_in_context) - 5} more)")
                    columns_html = f"""
                <div style="margin-top: 6px;">
                    <span style="font-weight: 500; color: {colors["text"]};">Filter Context:</span>
                    <span style="color: {colors["text_muted"]}; font-size: 13px;"> {html.escape(", ".join(cols))}</span>
                </div>"""

                # Build optimization notes
                notes_html = ""
                if visual.optimization_notes:
                    notes_html = f"""
                <div style="margin-top: 8px; padding: 8px 12px; background: #fef3c7; border-radius: 6px; font-size: 12px; color: #92400e;">
                    <strong>⚠️ Note:</strong> {html.escape("; ".join(visual.optimization_notes))}
                </div>"""

                parts.append(f"""
            <div style="background: {colors["bg"]}; border: 1px solid {colors["border"]}; border-radius: 8px; padding: 14px; border-left: 4px solid {lc["text"]};">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
                    <div>
                        <div style="font-weight: 600; color: {colors["text"]}; margin-bottom: 4px;">
                            {html.escape(visual.visual_title or f"Visual {visual.visual_id[:8]}")}
                        </div>
                        <div style="font-size: 12px; color: {colors["text_muted"]};">
                            {html.escape(visual.visual_type)}
                        </div>
                    </div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <span style="background: {lc["bg"]}; color: {lc["text"]}; padding: 4px 10px; border-radius: 16px; font-size: 12px; font-weight: 600;">
                            Level {level}
                        </span>
                        <span style="background: {colors["card"]}; border: 1px solid {colors["border"]}; padding: 4px 10px; border-radius: 16px; font-size: 12px; color: {colors["text_muted"]};">
                            {html.escape(visual.determined_agg_table or "Base Table")}
                        </span>
                    </div>
                </div>
                <div style="background: {colors["card"]}; padding: 10px 12px; border-radius: 6px; font-size: 13px; color: {colors["text"]};">
                    <strong>Why this level:</strong> {html.escape(visual.reasoning)}
                </div>
                {measures_html}
                {columns_html}
                {notes_html}
            </div>
""")

            parts.append("""
        </div>
    </div>
""")

        parts.append("</div>")
        return "".join(parts)

    # =========================================================================
    # NEW: Enhanced Analysis Sections
    # =========================================================================

    def _build_coverage_map_section(self, colors: Dict) -> str:
        """Build the aggregation coverage map visualization."""
        if not self.result.report_summary:
            return ""

        rs = self.result.report_summary
        pages = rs.pages

        # Level colors
        level_colors = {
            1: "#dc2626",  # Red - Base table
            2: "#f59e0b",  # Amber - Mid-level
            3: "#22c55e",  # Green - High-level
        }

        parts = [f"""
    <div class="card">
        <h2>Aggregation Coverage Map</h2>
        <div style="font-size: 13px; color: {colors["text_muted"]}; margin-bottom: 16px;">
            Visual representation of aggregation level usage across all report pages
        </div>
        <div style="display: flex; gap: 16px; margin-bottom: 20px;">
            <span style="display: flex; align-items: center; gap: 6px;"><span style="width: 12px; height: 12px; background: {level_colors[1]}; border-radius: 2px;"></span> Base Table</span>
            <span style="display: flex; align-items: center; gap: 6px;"><span style="width: 12px; height: 12px; background: {level_colors[2]}; border-radius: 2px;"></span> Mid-Level Agg</span>
            <span style="display: flex; align-items: center; gap: 6px;"><span style="width: 12px; height: 12px; background: {level_colors[3]}; border-radius: 2px;"></span> High-Level Agg</span>
        </div>
"""]

        for page in pages:
            if not page.visuals:
                continue

            # Calculate page hit rate
            base_count = page.agg_level_breakdown.get(1, 0)
            hit_rate = (
                100 - (base_count / page.visuals_analyzed * 100) if page.visuals_analyzed > 0 else 0
            )

            parts.append(f"""
        <div style="margin-bottom: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <strong>{html.escape(page.page_name)}</strong>
                <span style="font-size: 13px; color: {colors["text_muted"]};">
                    Hit Rate: <span style="color: {colors["success"] if hit_rate >= 70 else colors["warning"] if hit_rate >= 40 else colors["danger"]}; font-weight: 600;">{hit_rate:.0f}%</span>
                </span>
            </div>
            <div style="display: flex; gap: 4px; flex-wrap: wrap;">
""")

            for visual in page.visuals:
                level = visual.determined_agg_level
                color = level_colors.get(level, level_colors[2])
                title_text = html.escape(visual.visual_title or visual.visual_type)

                parts.append(f"""
                <div style="width: 24px; height: 24px; background: {color}; border-radius: 4px; cursor: pointer;"
                     title="{title_text}: Level {level}"></div>
""")

            parts.append("""
            </div>
        </div>
""")

        parts.append("</div>")
        return "".join(parts)

    def _build_quality_analysis_section(self, colors: Dict) -> str:
        """Build the quality analysis section."""
        qa = self.result.quality_analysis
        if not qa:
            return ""

        parts = ["""
    <div class="card">
        <h2>Quality Analysis</h2>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px;">
"""]

        # Overall quality score
        score = qa.overall_quality_score
        score_color = (
            colors["success"]
            if score >= 70
            else colors["warning"] if score >= 40 else colors["danger"]
        )

        parts.append(f"""
            <div class="stat-box">
                <div class="stat-value" style="color: {score_color};">{score:.0f}</div>
                <div class="stat-label">Overall Quality Score</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{len(qa.table_quality)}</div>
                <div class="stat-label">Tables Analyzed</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{qa.measure_quality.total_measures}</div>
                <div class="stat-label">Measures Analyzed</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{len(qa.all_issues)}</div>
                <div class="stat-label">Issues Found</div>
            </div>
        </div>
""")

        # Table Quality Details
        if qa.table_quality:
            parts.append("""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">Aggregation Table Quality</h3>
        <table class="table">
            <thead><tr><th>Table</th><th>Grain</th><th>Coverage</th><th>Overall</th><th>Issues</th></tr></thead>
            <tbody>
""")
            for tq in qa.table_quality:
                issue_count = len(tq.issues)
                issue_class = (
                    "badge-danger"
                    if issue_count > 2
                    else "badge-warning" if issue_count > 0 else "badge-success"
                )

                parts.append(f"""
                <tr>
                    <td><code>{html.escape(tq.table_name)}</code></td>
                    <td>{tq.grain_score:.0f}</td>
                    <td>{tq.coverage_score:.0f}</td>
                    <td><span class="badge {"badge-success" if tq.overall_score >= 70 else "badge-warning" if tq.overall_score >= 40 else "badge-danger"}">{tq.overall_score:.0f}</span></td>
                    <td><span class="badge {issue_class}">{issue_count}</span></td>
                </tr>
""")
            parts.append("</tbody></table>")

        # Critical Issues
        critical_issues = [
            i for tq in qa.table_quality for i in tq.issues if i.severity == "critical"
        ]
        if critical_issues:
            parts.append("""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">Critical Issues</h3>
""")
            for issue in critical_issues[:5]:
                parts.append(f"""
        <div style="padding: 12px; background: #fee2e2; border-left: 4px solid #dc2626; margin-bottom: 8px; border-radius: 0 8px 8px 0;">
            <strong>{html.escape(issue.issue_type)}</strong>: {html.escape(issue.description)}
            {f'<div style="margin-top: 6px; font-size: 12px; color: #64748b;">Suggestion: {html.escape(issue.suggestion)}</div>' if issue.suggestion else ''}
        </div>
""")

        parts.append("</div>")
        return "".join(parts)

    def _build_hit_rate_section(self, colors: Dict) -> str:
        """Build the hit rate analysis section."""
        hr = self.result.hit_rate_analysis
        if not hr:
            return ""

        parts = [f"""
    <div class="card">
        <h2>Aggregation Hit Rate Analysis</h2>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 24px;">
            <div class="stat-box">
                <div class="stat-value" style="color: {colors["success"] if hr.overall_hit_rate >= 70 else colors["warning"] if hr.overall_hit_rate >= 40 else colors["danger"]};">{hr.overall_hit_rate:.0f}%</div>
                <div class="stat-label">Overall Hit Rate</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{hr.visuals_using_aggregation}</div>
                <div class="stat-label">Aggregation Hits</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{hr.visuals_using_base}</div>
                <div class="stat-label">Base Table Misses</div>
            </div>
        </div>
"""]

        # Miss Reason Breakdown
        if hr.miss_summary:
            parts.append("""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">Miss Reason Breakdown</h3>
        <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px;">
""")
            reason_colors = {
                "DETAIL_COLUMN_FILTER": "#ef4444",
                "DIMENSION_NOT_IN_AGG": "#f97316",
                "MEASURE_NOT_AGG_AWARE": "#eab308",
                "CROSS_FILTER_PROPAGATION": "#8b5cf6",
                "SLICER_SELECTION": "#06b6d4",
            }

            for reason, count in hr.miss_summary.items():
                reason_name = reason.name if hasattr(reason, "name") else str(reason)
                color = reason_colors.get(reason_name, colors["text_muted"])
                parts.append(f"""
            <div style="padding: 8px 14px; background: {color}20; border: 1px solid {color}; border-radius: 8px;">
                <div style="font-weight: 600; color: {color};">{count}</div>
                <div style="font-size: 11px; color: {colors["text_muted"]};">{reason_name.replace("_", " ").title()}</div>
            </div>
""")
            parts.append("</div>")

        # Page Hit Rates - Expandable
        if hr.page_hit_rates:
            # Build lookup of visuals by page from report_summary
            page_visuals = {}
            if self.result.report_summary:
                for page in self.result.report_summary.pages:
                    page_visuals[page.page_name] = page.visuals

            # Build lookup of misses by page
            page_misses = {}
            for miss in hr.all_misses:
                if miss.page_name not in page_misses:
                    page_misses[miss.page_name] = {}
                page_misses[miss.page_name][miss.visual_id] = miss

            parts.append(f"""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">Hit Rate by Page</h3>
        <div style="display: grid; grid-template-columns: 1fr 200px 80px 80px; align-items: center; gap: 16px; padding: 8px 16px; background: {colors["bg"]}; border-radius: 8px 8px 0 0; font-size: 12px; font-weight: 600; color: {colors["text_muted"]}; margin-bottom: 4px;">
            <div>Page</div>
            <div>Hit Rate</div>
            <div style="text-align: center;">Hits</div>
            <div style="text-align: center;">Misses</div>
        </div>
""")
            for idx, phr in enumerate(hr.page_hit_rates):
                page_id = f"page-hit-{idx}"
                hit_color = (
                    colors["success"]
                    if phr.page_hit_rate >= 70
                    else colors["warning"] if phr.page_hit_rate >= 40 else colors["danger"]
                )

                # Get visuals for this page
                visuals = page_visuals.get(phr.page_name, [])
                misses = page_misses.get(phr.page_name, {})

                # Separate hits and misses
                hits = []
                miss_list = []
                for v in visuals:
                    if v.determined_agg_level == 1:  # Base table = miss
                        miss_info = misses.get(v.visual_id)
                        miss_list.append((v, miss_info))
                    else:
                        hits.append(v)

                parts.append(f"""
        <div class="collapsible" id="{page_id}" style="padding: 12px 16px; border: 1px solid {colors["border"]}; border-radius: 8px; margin-bottom: 8px;">
            <div style="display: grid; grid-template-columns: 1fr 200px 80px 80px; align-items: center; gap: 16px;">
                <div style="font-weight: 500;">{html.escape(phr.page_name)}</div>
                <div style="display: flex; align-items: center; gap: 8px;">
                    <div class="progress-bar" style="width: 80px;">
                        <div class="progress-bar-fill" style="width: {phr.page_hit_rate:.0f}%; background: {hit_color};"></div>
                    </div>
                    <span>{phr.page_hit_rate:.0f}%</span>
                </div>
                <div style="text-align: center;">{phr.using_aggregation}</div>
                <div style="text-align: center; color: {colors["danger"] if phr.using_base_table > 0 else colors["text_muted"]};">{phr.using_base_table}</div>
            </div>
        </div>
        <div class="collapse-content" style="margin-left: 20px; margin-bottom: 16px;">
""")

                # Show hits section
                if hits:
                    parts.append(f"""
            <div style="margin-bottom: 16px;">
                <div style="font-weight: 600; color: {colors["success"]}; margin-bottom: 8px; font-size: 13px;">
                    ✓ Aggregation Hits ({len(hits)})
                </div>
                <div style="display: grid; gap: 6px;">
""")
                    for v in hits:
                        agg_table = v.determined_agg_table or f"Level {v.determined_agg_level}"
                        parts.append(f"""
                    <div style="display: flex; align-items: center; gap: 12px; padding: 8px 12px; background: #f0fdf4; border-radius: 6px; border-left: 3px solid {colors["success"]};">
                        <div style="flex: 1;">
                            <span style="font-weight: 500;">{html.escape(v.visual_title or v.visual_id[:12])}</span>
                            <span style="color: {colors["text_muted"]}; font-size: 12px; margin-left: 8px;">{html.escape(v.visual_type)}</span>
                        </div>
                        <div style="font-size: 12px; color: {colors["success"]}; font-weight: 500;">
                            {html.escape(agg_table)}
                        </div>
                    </div>
""")
                    parts.append("""
                </div>
            </div>
""")

                # Show misses section
                if miss_list:
                    parts.append(f"""
            <div>
                <div style="font-weight: 600; color: {colors["danger"]}; margin-bottom: 8px; font-size: 13px;">
                    ✗ Base Table Misses ({len(miss_list)})
                </div>
                <div style="display: grid; gap: 6px;">
""")
                    for v, miss_info in miss_list:
                        # Get miss reason if available
                        reason_text = ""
                        if miss_info:
                            reason_name = (
                                miss_info.reason.name
                                if hasattr(miss_info.reason, "name")
                                else str(miss_info.reason)
                            )
                            reason_text = reason_name.replace("_", " ").title()
                            if miss_info.triggering_columns:
                                reason_text += f" ({', '.join(miss_info.triggering_columns[:2])})"

                        parts.append(f"""
                    <div style="padding: 8px 12px; background: #fef2f2; border-radius: 6px; border-left: 3px solid {colors["danger"]};">
                        <div style="display: flex; align-items: center; gap: 12px;">
                            <div style="flex: 1;">
                                <span style="font-weight: 500;">{html.escape(v.visual_title or v.visual_id[:12])}</span>
                                <span style="color: {colors["text_muted"]}; font-size: 12px; margin-left: 8px;">{html.escape(v.visual_type)}</span>
                            </div>
                            <div style="font-size: 12px; color: {colors["danger"]};">
                                Base Table
                            </div>
                        </div>
                        {f'<div style="font-size: 11px; color: {colors["text_muted"]}; margin-top: 4px;">Reason: {html.escape(reason_text)}</div>' if reason_text else ''}
                    </div>
""")
                    parts.append("""
                </div>
            </div>
""")

                parts.append("""
        </div>
""")

        # Top Opportunities
        if hr.opportunity_rankings:
            parts.append("""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">Top Improvement Opportunities</h3>
""")
            for opp in hr.opportunity_rankings[:5]:
                parts.append(f"""
        <div class="recommendation">
            <strong>#{opp.rank} - {html.escape(opp.opportunity_type.replace("_", " ").title())}</strong>: {html.escape(opp.description)}
            <div style="font-size: 12px; color: #64748b; margin-top: 4px;">
                Impact: +{opp.estimated_hit_rate_improvement:.0f}% hit rate • Affected visuals: {opp.visuals_impacted} • Effort: {opp.implementation_effort}
            </div>
        </div>
""")

        parts.append("</div>")
        return "".join(parts)

    def _build_slicer_impact_section(self, colors: Dict) -> str:
        """Build the slicer impact analysis section."""
        si = self.result.slicer_impact_analysis
        if not si:
            return ""

        parts = [f"""
    <div class="card">
        <h2>Slicer Impact Analysis</h2>
        <div style="font-size: 13px; color: {colors["text_muted"]}; margin-bottom: 16px;">
            Analysis of how slicers affect aggregation level selection
        </div>
"""]

        # Summary stats
        parts.append(f"""
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 24px;">
            <div class="stat-box">
                <div class="stat-value">{si.total_slicers}</div>
                <div class="stat-label">Total Slicers</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{si.slicers_forcing_base}</div>
                <div class="stat-label">Slicers Forcing Base</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{si.worst_case_hit_rate:.0f}%</div>
                <div class="stat-label">Worst Case Hit Rate</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{si.best_case_hit_rate:.0f}%</div>
                <div class="stat-label">Best Case Hit Rate</div>
            </div>
        </div>
""")

        # High Impact Slicers
        high_impact = [s for s in si.slicer_impacts if s.impact_level.value in ["high", "critical"]]
        if high_impact:
            parts.append("""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">High Impact Slicers</h3>
        <table class="table">
            <thead><tr><th>Slicer</th><th>Column</th><th>Impact</th><th>Visuals Affected</th></tr></thead>
            <tbody>
""")
            for slicer in high_impact[:10]:
                impact_class = (
                    "badge-danger" if slicer.impact_level.value == "critical" else "badge-warning"
                )
                parts.append(f"""
                <tr>
                    <td>{html.escape(slicer.slicer_id[:20])}</td>
                    <td><code>{html.escape(slicer.column)}</code></td>
                    <td><span class="badge {impact_class}">{slicer.impact_level.value.upper()}</span></td>
                    <td>{slicer.affected_visuals_total}</td>
                </tr>
""")
            parts.append("</tbody></table>")

        # Sync Group Analysis
        if si.sync_group_analyses:
            parts.append("""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">Slicer Sync Groups</h3>
""")
            for sg in si.sync_group_analyses:
                has_cross_page = len(sg.affected_pages) > 1
                parts.append(f"""
        <div style="padding: 12px; background: {colors["bg"]}; border-radius: 8px; margin-bottom: 8px;">
            <strong>Sync Group: {html.escape(sg.sync_group_id)}</strong>
            <div style="font-size: 13px; color: {colors["text_muted"]}; margin-top: 4px;">
                Pages: {len(sg.affected_pages)} • Cross-page impact: {'Yes' if has_cross_page else 'No'}
            </div>
        </div>
""")

        parts.append("</div>")
        return "".join(parts)

    def _build_cross_filter_section(self, colors: Dict) -> str:
        """Build the cross-filter analysis section."""
        cf = self.result.cross_filter_analysis
        if not cf:
            return ""

        parts = [f"""
    <div class="card">
        <h2>Cross-Filter Impact Analysis</h2>
        <div style="font-size: 13px; color: {colors["text_muted"]}; margin-bottom: 16px;">
            Impact of cross-filtering and cross-highlighting on aggregation levels
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 24px;">
            <div class="stat-box">
                <div class="stat-value">{cf.total_interactions_analyzed}</div>
                <div class="stat-label">Interactions Analyzed</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{cf.critical_interactions}</div>
                <div class="stat-label">Critical Interactions</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{cf.interactions_causing_level_drops}</div>
                <div class="stat-label">Causing Level Drops</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{cf.estimated_hit_rate_impact:.0f}%</div>
                <div class="stat-label">Hit Rate Impact</div>
            </div>
        </div>
"""]

        # Problematic Interactions
        if cf.disable_interaction_recommendations:
            parts.append("""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">Recommended Interaction Changes</h3>
""")
            for source, target, reason in cf.disable_interaction_recommendations[:5]:
                parts.append(f"""
        <div style="padding: 12px; background: #fef3c7; border-left: 4px solid #f59e0b; margin-bottom: 8px; border-radius: 0 8px 8px 0;">
            <strong>Disable cross-filter</strong>: {html.escape(source)} → {html.escape(target)}
            <div style="font-size: 12px; color: #64748b; margin-top: 4px;">
                Reason: {html.escape(reason)}
            </div>
        </div>
""")

        # Page Interaction Summary
        if cf.page_matrices:
            parts.append("""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">Interaction Summary by Page</h3>
        <table class="table">
            <thead><tr><th>Page</th><th>Visuals</th><th>Interactions</th><th>Critical</th><th>Density</th></tr></thead>
            <tbody>
""")
            for pm in cf.page_matrices:
                parts.append(f"""
                <tr>
                    <td>{html.escape(pm.page_name)}</td>
                    <td>{pm.visual_count}</td>
                    <td>{pm.total_interactions}</td>
                    <td><span class="badge {"badge-danger" if pm.critical_interactions > 3 else "badge-warning" if pm.critical_interactions > 0 else "badge-success"}">{pm.critical_interactions}</span></td>
                    <td>{pm.interaction_density:.0%}</td>
                </tr>
""")
            parts.append("</tbody></table>")

        parts.append("</div>")
        return "".join(parts)

    def _build_auto_recommendations_section(self, colors: Dict) -> str:
        """Build the automatic aggregation recommendations section."""
        rec = self.result.recommendations
        if not rec:
            return ""

        parts = [f"""
    <div class="card">
        <h2>Aggregation Recommendations</h2>
        <div style="font-size: 13px; color: {colors["text_muted"]}; margin-bottom: 16px;">
            Auto-generated recommendations for new aggregation tables and measures
        </div>
"""]

        # Summary
        parts.append(f"""
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 24px;">
            <div class="stat-box">
                <div class="stat-value">{len(rec.recommended_tables)}</div>
                <div class="stat-label">Recommended Tables</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{len(rec.recommended_agg_aware_measures)}</div>
                <div class="stat-label">Recommended Measures</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{rec.total_improvement_potential:.0f}%</div>
                <div class="stat-label">Est. Hit Rate Improvement</div>
            </div>
        </div>
""")

        # Recommended Tables
        if rec.recommended_tables:
            parts.append("""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">Recommended Aggregation Tables</h3>
""")
            for table in rec.recommended_tables:
                grain_cols = ", ".join([c.column_name for c in table.grain_columns[:4]])
                if len(table.grain_columns) > 4:
                    grain_cols += f" (+{len(table.grain_columns) - 4} more)"

                # Calculate priority based on priority_score
                priority = (
                    "high"
                    if table.priority_score >= 70
                    else "medium" if table.priority_score >= 40 else "low"
                )
                priority_color = (
                    colors["danger"]
                    if priority == "high"
                    else colors["warning"] if priority == "medium" else colors["text_muted"]
                )

                parts.append(f"""
        <div class="collapsible" style="padding: 16px; background: {colors["bg"]}; border-radius: 8px; margin-bottom: 8px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <strong>{html.escape(table.name)}</strong>
                    <span class="badge badge-info" style="margin-left: 8px;">Level {table.level}</span>
                </div>
                <span style="font-size: 13px; color: {colors["text_muted"]};">
                    Priority Score: <span style="color: {priority_color}; font-weight: 600;">{table.priority_score:.0f}</span>
                </span>
            </div>
            <div style="font-size: 13px; color: {colors["text_muted"]}; margin-top: 4px;">
                Grain: {html.escape(grain_cols)}
            </div>
        </div>
        <div class="collapse-content" style="margin-bottom: 12px;">
            <div style="margin-bottom: 12px;">
                <strong>Level:</strong> {html.escape(table.level_description)}
            </div>
            <div style="margin-bottom: 12px;">
                <strong>Estimated Rows:</strong> {table.estimated_row_count:,} • Hit Rate Improvement: +{table.hit_rate_improvement:.0f}%
            </div>
            <div style="margin-bottom: 12px;">
                <strong>Visuals Benefiting:</strong> {table.visuals_that_would_benefit}
            </div>
            <div>
                <strong>TMDL Code:</strong>
                <pre style="background: #1e293b; color: #e2e8f0; padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 12px; margin-top: 8px;">{html.escape(table.tmdl_code[:500] if table.tmdl_code else "// Generate with full analysis")}</pre>
            </div>
        </div>
""")

        # Recommended Level Measure
        if rec.recommended_level_measure:
            lm = rec.recommended_level_measure
            parts.append(f"""
        <h3 style="font-size: 15px; margin: 20px 0 12px;">Recommended Aggregation Level Measure</h3>
        <div style="padding: 16px; background: {colors["bg"]}; border-radius: 8px;">
            <strong>{html.escape(lm.measure_name)}</strong>
            <pre style="background: #1e293b; color: #e2e8f0; padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 12px; margin-top: 8px;">{html.escape(lm.dax_code[:800] if lm.dax_code else "// Generate with full analysis")}</pre>
        </div>
""")

        parts.append("</div>")
        return "".join(parts)

    def save_html_report(self, output_path: str) -> str:
        """Save HTML report to file."""
        html_content = self.build_html_report()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_content, encoding="utf-8")
        logger.info(f"HTML report saved to {output_path}")
        return str(path)

    def save_json_export(self, output_path: str) -> str:
        """Save JSON export to file."""
        json_data = self.build_json_export()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")
        logger.info(f"JSON export saved to {output_path}")
        return str(path)
