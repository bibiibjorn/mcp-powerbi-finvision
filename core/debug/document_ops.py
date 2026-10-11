"""``DebugOperations`` methods split out of ``core.debug.debug_operations`` (C-17): pure move, no behaviour change.

``DebugOperations`` inherits :class:`DebugDocumentMixin`, so every ``self.<method>`` call and every import of
``core.debug.debug_operations.DebugOperations`` keeps working.
"""

from typing import Any, Dict, Optional
from datetime import datetime
from core.infrastructure.adomd_utils import engine_message
from core.debug.debug_types import LineageInfo


class DebugDocumentMixin:
    """Methods of :class:`DebugOperations` (document_ops.py)."""

    def document_page(
        self, page_name: str, lightweight: bool = True, include_ui_elements: bool = False
    ) -> Dict[str, Any]:
        """
        Generate documentation for all visuals on a page.

        lightweight=True (default) skips expensive measure expansion and filter context building.
        include_ui_elements=False (default) shows only data-bearing visuals.
        """
        # Get all visuals first to count totals
        all_visuals = self.builder.list_visuals(page_name, include_ui_elements=True)
        slicers = self.builder.list_slicers(page_name)

        # Count UI vs data visuals for summary
        total_elements = len(all_visuals)
        data_visuals = [v for v in all_visuals if v.get("is_data_visual", False)]
        ui_elements = [v for v in all_visuals if not v.get("is_data_visual", False)]

        # Filter to just data visuals unless UI elements requested
        visuals = all_visuals if include_ui_elements else data_visuals

        visual_docs = []

        # In lightweight mode, get page-level filter counts once for all visuals
        page_filter_count = 0
        report_filter_count = 0
        if lightweight:
            # Get page filters once (cached internally)
            page_path = self.builder._find_page_by_name(page_name)
            if page_path:
                page_filters = self.builder._get_page_filters(page_path)
                page_filter_count = len(page_filters)
            report_filters = self.builder._get_report_filters()
            report_filter_count = len(report_filters)

        for visual in visuals:
            if visual.get("is_slicer"):
                continue

            try:
                if lightweight:
                    # Fast path: use pre-extracted visual info without building queries
                    # Visual-level filters are already in the visual data from list_visuals
                    visual_filter_count = len(visual.get("filters", []))

                    visual_docs.append(
                        {
                            "id": visual["id"],
                            "name": visual.get("friendly_name", visual["id"]),
                            "type": visual.get("type_display", visual.get("type")),
                            "title": visual.get("title"),
                            "is_data_visual": visual.get("is_data_visual", True),
                            "measures": visual.get("measures", []),
                            "columns": visual.get("columns", []),
                            "filters": {
                                "report": report_filter_count,
                                "page": page_filter_count,
                                "visual": visual_filter_count,
                                "slicer": len(slicers),  # All slicers affect all visuals
                            },
                        }
                    )
                else:
                    # Full path: build complete query with filter context
                    # Skip expand_measures for documentation - we don't need DAX expressions
                    query_result = self.builder.build_visual_query(
                        page_name=page_name,
                        visual_id=visual["id"],
                        expand_measures=False,  # Skip expensive DMV queries
                    )

                    filter_info = {"report": 0, "page": 0, "visual": 0, "slicer": 0}

                    if query_result:
                        filter_info = {
                            "report": len(query_result.filter_context.report_filters),
                            "page": len(query_result.filter_context.page_filters),
                            "visual": len(query_result.filter_context.visual_filters),
                            "slicer": len(query_result.filter_context.slicer_filters),
                        }

                    visual_docs.append(
                        {
                            "id": visual["id"],
                            "name": visual.get("friendly_name", visual["id"]),
                            "type": visual.get("type_display", visual.get("type")),
                            "title": visual.get("title"),
                            "is_data_visual": visual.get("is_data_visual", True),
                            "measures": visual.get("measures", []),
                            "columns": visual.get("columns", []),
                            "filters": filter_info,
                        }
                    )

            except Exception as e:
                visual_docs.append(
                    {
                        "id": visual["id"],
                        "name": visual.get("friendly_name", visual["id"]),
                        "type": visual.get("type", "unknown"),
                        "error": engine_message(e),
                    }
                )

        slicer_docs = [
            {
                "id": s.slicer_id,
                "field": s.field_reference,
                "table": s.table,
                "column": s.column,
                "selection_mode": s.selection_mode,
                "current_selection": s.selected_values[:5] if s.selected_values else [],
                "selection_count": len(s.selected_values),
            }
            for s in slicers
        ]

        # Build visual type breakdown for summary (UI vs Data)
        ui_type_counts = {}
        data_type_counts = {}
        for v in all_visuals:
            vtype = v.get("type_display", v.get("type", "Unknown"))
            if v.get("is_data_visual", False):
                data_type_counts[vtype] = data_type_counts.get(vtype, 0) + 1
            else:
                ui_type_counts[vtype] = ui_type_counts.get(vtype, 0) + 1

        # Count slicers separately (they're in ui_elements but shown separately)
        slicer_count_in_ui = sum(1 for v in ui_elements if v.get("is_slicer", False))

        return {
            "success": True,
            "page": page_name,
            "data_visual_count": len(visual_docs),  # Renamed for clarity
            "slicer_count": len(slicer_docs),
            "visuals": visual_docs,
            "slicers": slicer_docs,
            "summary": {
                "total_page_elements": total_elements,
                "data_visuals": len(data_visuals),
                "slicers": len(slicer_docs),
                "ui_elements": len(ui_elements)
                - slicer_count_in_ui,  # Exclude slicers from UI count
                "showing": "all elements" if include_ui_elements else "data visuals only",
                "total_measures": len(set(m for v in visual_docs for m in v.get("measures", []))),
                "total_columns": len(set(c for v in visual_docs for c in v.get("columns", []))),
                "data_visual_types": data_type_counts,
                "ui_element_types": ui_type_counts,
            },
        }

    def document_report(self, lightweight: bool = True) -> Dict[str, Any]:
        """
        Generate complete documentation for the entire report. lightweight=True skips expensive ops.
        """
        pages = self.builder.list_pages()

        report_doc = {
            "success": True,
            "pages": [],
            "summary": {
                "page_count": len(pages),
                "total_visuals": 0,
                "total_slicers": 0,
                "all_measures": set(),
                "all_columns": set(),
            },
        }

        for page in pages:
            page_doc = self.document_page(page["name"], lightweight=lightweight)

            # Audit T101: document_page returns 'data_visual_count', not
            # 'visual_count'. Use the canonical key (with the legacy fallback
            # for compatibility with any older callers).
            visual_count = page_doc.get("data_visual_count", page_doc.get("visual_count", 0))
            report_doc["pages"].append(
                {
                    "name": page["name"],
                    "id": page["id"],
                    "ordinal": page.get("ordinal", 0),
                    "visual_count": visual_count,
                    "slicer_count": page_doc.get("slicer_count", 0),
                    "visuals": page_doc.get("visuals", []),
                    "slicers": page_doc.get("slicers", []),
                }
            )

            report_doc["summary"]["total_visuals"] += visual_count
            report_doc["summary"]["total_slicers"] += page_doc.get("slicer_count", 0)

            for v in page_doc.get("visuals", []):
                report_doc["summary"]["all_measures"].update(v.get("measures", []))
                report_doc["summary"]["all_columns"].update(v.get("columns", []))

        # Convert sets to lists for JSON
        report_doc["summary"]["all_measures"] = list(report_doc["summary"]["all_measures"])
        report_doc["summary"]["all_columns"] = list(report_doc["summary"]["all_columns"])
        report_doc["summary"]["measure_count"] = len(report_doc["summary"]["all_measures"])
        report_doc["summary"]["column_count"] = len(report_doc["summary"]["all_columns"])

        return report_doc

    def measure_lineage(self, measure_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Show which visuals use which measures. measure_name=None traces all measures.
        """
        pages = self.builder.list_pages()

        # Build measure -> visuals mapping
        measure_map: Dict[str, LineageInfo] = {}

        for page in pages:
            visuals = self.builder.list_visuals(page["name"])

            for visual in visuals:
                if visual.get("is_slicer"):
                    continue

                for m in visual.get("measures", []):
                    clean_m = m.strip("[]")

                    # Skip if specific measure requested and this isn't it
                    if measure_name and clean_m.lower() != measure_name.strip("[]").lower():
                        continue

                    if clean_m not in measure_map:
                        measure_map[clean_m] = LineageInfo(name=clean_m, table=None)

                    measure_map[clean_m].visuals.append(
                        {
                            "visual_id": visual["id"],
                            "visual_name": visual.get("friendly_name", visual["id"]),
                            "visual_type": visual.get("type", "unknown"),
                            "page": page["name"],
                        }
                    )

                    if page["name"] not in measure_map[clean_m].pages:
                        measure_map[clean_m].pages.append(page["name"])

                    measure_map[clean_m].usage_count += 1

        # Sort by usage
        sorted_measures = sorted(measure_map.values(), key=lambda x: x.usage_count, reverse=True)

        return {
            "success": True,
            "measure_filter": measure_name,
            "measures_found": len(sorted_measures),
            "lineage": [
                {
                    "measure": m.name,
                    "usage_count": m.usage_count,
                    "pages": m.pages,
                    "page_count": len(m.pages),
                    "visuals": m.visuals,
                }
                for m in sorted_measures
            ],
        }

    def filter_lineage(self, page_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Show which filters affect which visuals. page_name=None analyzes all pages.
        """
        pages = self.builder.list_pages()
        if page_name:
            pages = [p for p in pages if p["name"].lower() == page_name.lower()]

        # Build filter -> visuals mapping
        filter_map: Dict[str, Dict] = {}

        # Report-level filters affect all visuals
        report_filters = self.builder._get_report_filters()
        for rf in report_filters:
            expr = self.builder.converter.convert_filter(rf, source="report")
            if expr:
                key = f"report:{expr.table}.{expr.column}"
                if key not in filter_map:
                    filter_map[key] = {
                        "level": "report",
                        "table": expr.table,
                        "column": expr.column,
                        "dax": expr.dax,
                        "affects_all": True,
                        "pages": [],
                        "visual_count": 0,
                    }

        for page in pages:
            page_path = self.builder._find_page_by_name(page["name"])
            if not page_path:
                continue

            # Page-level filters
            page_filters = self.builder._get_page_filters(page_path)
            for pf in page_filters:
                expr = self.builder.converter.convert_filter(pf, source="page")
                if expr:
                    key = f"page:{page['name']}:{expr.table}.{expr.column}"
                    if key not in filter_map:
                        filter_map[key] = {
                            "level": "page",
                            "page": page["name"],
                            "table": expr.table,
                            "column": expr.column,
                            "dax": expr.dax,
                            "affects_all": False,
                            "visual_count": 0,
                        }

            # Slicer filters
            slicers = self.builder.list_slicers(page["name"])
            for slicer in slicers:
                key = f"slicer:{page['name']}:{slicer.table}.{slicer.column}"
                if key not in filter_map:
                    filter_map[key] = {
                        "level": "slicer",
                        "page": page["name"],
                        "table": slicer.table,
                        "column": slicer.column,
                        "field": slicer.field_reference,
                        "affects_all": False,
                        "current_selection": (
                            slicer.selected_values[:3] if slicer.selected_values else []
                        ),
                        "visual_count": 0,
                    }

            # Count affected visuals
            visuals = self.builder.list_visuals(page["name"])
            non_slicer_count = len([v for v in visuals if not v.get("is_slicer")])

            for key, info in filter_map.items():
                if info["level"] == "report":
                    info["visual_count"] += non_slicer_count
                    if page["name"] not in info["pages"]:
                        info["pages"].append(page["name"])
                elif info.get("page") == page["name"]:
                    info["visual_count"] = non_slicer_count

        return {
            "success": True,
            "page_filter": page_name,
            "filters_found": len(filter_map),
            "lineage": list(filter_map.values()),
        }

    def export_debug_report(
        self,
        page_name: Optional[str] = None,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        format: str = "markdown",
    ) -> Dict[str, Any]:
        """
        Export debug info as a markdown or JSON report (format='markdown'|'json').
        """
        if page_name and (visual_id or visual_name):
            # Export single visual
            query_result = self.builder.build_visual_query(
                page_name=page_name, visual_id=visual_id, visual_name=visual_name
            )

            if not query_result:
                return {"success": False, "error": "Visual not found"}

            report_data = {
                "type": "visual",
                "visual": {
                    "id": query_result.visual_info.visual_id,
                    "name": query_result.visual_info.visual_name,
                    "type": query_result.visual_info.visual_type,
                    "page": page_name,
                    "title": query_result.visual_info.title,
                    "measures": query_result.visual_info.measures,
                    "columns": query_result.visual_info.columns,
                },
                "filter_context": query_result.filter_breakdown,
                "query": query_result.dax_query,
                "expanded_query": query_result.expanded_query,
            }
        elif page_name:
            # Export page
            report_data = self.document_page(page_name)
            report_data["type"] = "page"
        else:
            # Export entire report
            report_data = self.document_report()
            report_data["type"] = "report"

        if format == "markdown":
            markdown = self._generate_markdown_report(report_data)
            return {"success": True, "format": "markdown", "content": markdown, "data": report_data}
        else:
            return {"success": True, "format": "json", "data": report_data}

    def _generate_markdown_report(self, data: Dict[str, Any]) -> str:
        """Generate markdown report from data."""
        lines = []
        report_type = data.get("type", "unknown")

        lines.append("# Power BI Debug Report")
        lines.append("")
        lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        if report_type == "visual":
            visual = data.get("visual", {})
            lines.append(f"## Visual: {visual.get('name', 'Unknown')}")
            lines.append("")
            lines.append("| Property | Value |")
            lines.append("|----------|-------|")
            lines.append(f"| ID | `{visual.get('id')}` |")
            lines.append(f"| Type | {visual.get('type')} |")
            lines.append(f"| Page | {visual.get('page')} |")
            lines.append(f"| Measures | {', '.join(visual.get('measures', []))} |")
            lines.append(f"| Columns | {', '.join(visual.get('columns', []))} |")
            lines.append("")

            lines.append("### Filter Context")
            lines.append("")

            filter_context = data.get("filter_context", {})
            for level, filters in filter_context.items():
                if filters:
                    lines.append(f"**{level.replace('_', ' ').title()}:**")
                    for f in filters:
                        lines.append(f"- `{f.get('dax', f)}`")
                    lines.append("")

            lines.append("### Generated Query")
            lines.append("")
            lines.append("```dax")
            lines.append(data.get("query", "N/A"))
            lines.append("```")

        elif report_type == "page":
            lines.append(f"## Page: {data.get('page', 'Unknown')}")
            lines.append("")
            lines.append(f"**Visuals:** {data.get('visual_count', 0)}")
            lines.append(f"**Slicers:** {data.get('slicer_count', 0)}")
            lines.append("")

            lines.append("### Visuals")
            lines.append("")
            for v in data.get("visuals", []):
                lines.append(f"- **{v.get('name')}** ({v.get('type')})")
                if v.get("measures"):
                    lines.append(f"  - Measures: {', '.join(v['measures'])}")

        elif report_type == "report":
            summary = data.get("summary", {})
            lines.append("## Report Summary")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| Pages | {summary.get('page_count', 0)} |")
            lines.append(f"| Total Visuals | {summary.get('total_visuals', 0)} |")
            lines.append(f"| Total Slicers | {summary.get('total_slicers', 0)} |")
            lines.append(f"| Unique Measures | {summary.get('measure_count', 0)} |")
            lines.append("")

            for page in data.get("pages", []):
                lines.append(f"### {page.get('name')}")
                lines.append("")
                lines.append(f"- Visuals: {page.get('visual_count', 0)}")
                lines.append(f"- Slicers: {page.get('slicer_count', 0)}")
                lines.append("")

        return "\n".join(lines)
