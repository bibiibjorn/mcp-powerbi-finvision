"""
INTERNAL HELPER — Not a registered MCP tool.
Provides helper functions consumed by active handlers.

Provides handle_report_info and handle_report_measure_usage for report_operations_handler.py
(07_Report_Operations.info and .measure_usage operations).
"""

from typing import Dict, Any, List, Optional
import logging
from pathlib import Path
from core.utilities.pbip_utils import (
    normalize_path as _normalize_path,
    find_definition_folder as _find_definition_folder,
    load_json_file as _load_json_file,
)
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


def _walk_for_measure_refs(obj, measures: set) -> None:
    """Recursively walk JSON to find Measure references (e.g. in objects, conditional formatting)."""
    if isinstance(obj, dict):
        if "Measure" in obj and isinstance(obj["Measure"], dict):
            ref = obj["Measure"]
            entity = (ref.get("Expression") or {}).get("SourceRef", {}).get("Entity", "")
            prop = ref.get("Property", "")
            if entity and prop:
                measures.add((entity, prop))
        for value in obj.values():
            _walk_for_measure_refs(value, measures)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_measure_refs(item, measures)


def _extract_measures_from_visual(visual_data: Dict) -> List[Dict]:
    """Extract ALL measure references from a visual: queryState, objects, and visual-level filters.

    Returns a list of dicts with keys: entity, measure, context (bucket name / 'objects' / 'filter').
    """
    results = []
    seen = set()  # (entity, measure, context) dedup

    visual = visual_data.get("visual", {})

    # 1. Query state projections (the main data fields)
    query = visual.get("query", {})
    query_state = query.get("queryState", {})
    for bucket_name, bucket_data in query_state.items():
        for proj in bucket_data.get("projections", []):
            field = proj.get("field", {})
            if "Measure" in field:
                m = field["Measure"]
                entity = m.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
                prop = m.get("Property", "")
                if entity and prop:
                    key = (entity, prop, bucket_name)
                    if key not in seen:
                        seen.add(key)
                        results.append({"entity": entity, "measure": prop, "context": bucket_name})

    # 2. Visual objects (conditional formatting, data labels, reference lines, dynamic titles, etc.)
    obj_measures: set = set()
    for section_key in ("objects", "visualContainerObjects", "vcObjects"):
        section = visual.get(section_key, {})
        if section:
            _walk_for_measure_refs(section, obj_measures)
    for entity, prop in obj_measures:
        key = (entity, prop, "objects")
        if key not in seen:
            seen.add(key)
            results.append({"entity": entity, "measure": prop, "context": "objects"})

    # 3. Visual-level filters — H-22: they live in the TOP-LEVEL filterConfig
    # (never visual.filters). A measure used ONLY as a visual-level filter
    # (visibility gates like "Show In Slot = 1") is in use: missing it here
    # made measure_usage report such measures unused -> pruned -> broken report.
    from core.pbip.pbir_model import get_visual_filters

    filter_measures: set = set()
    _walk_for_measure_refs(get_visual_filters(visual_data), filter_measures)
    for entity, prop in sorted(filter_measures):
        key = (entity, prop, "filter")
        if key not in seen:
            seen.add(key)
            results.append({"entity": entity, "measure": prop, "context": "filter"})

    return results


def _extract_measures_from_filter_config(filter_config: Dict) -> List[Dict]:
    """Extract measure references from a filterConfig (report.json or page.json)."""
    results = []
    for flt in filter_config.get("filters", []):
        field = flt.get("field", {})
        if "Measure" in field:
            m = field["Measure"]
            entity = m.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
            prop = m.get("Property", "")
            if entity and prop:
                results.append({"entity": entity, "measure": prop})
    return results


def _find_all_report_definitions(pbip_path: str) -> List[Dict]:
    """Find all report definition folders under a PBIP path.

    Returns a list of dicts: [{'name': 'ReportName', 'definition_path': Path}, ...]
    """
    path = Path(_normalize_path(pbip_path))
    if not path.exists():
        return []

    results = []

    # If it's a .pbip file, look for .Report folder
    if path.is_file() and path.suffix == ".pbip":
        report_folder = path.parent / f"{path.stem}.Report"
        if report_folder.exists():
            definition = report_folder / "definition"
            if definition.exists():
                results.append({"name": path.stem, "definition_path": definition})
        return results

    # If it's a .Report folder
    if path.is_dir() and path.name.endswith(".Report"):
        definition = path / "definition"
        if definition.exists():
            report_name = path.name.replace(".Report", "")
            results.append({"name": report_name, "definition_path": definition})
        return results

    # If it's a definition folder
    if path.is_dir() and path.name == "definition":
        parent_name = path.parent.name.replace(".Report", "")
        results.append({"name": parent_name, "definition_path": path})
        return results

    # If it's a directory, search for ALL .Report folders
    if path.is_dir():
        for item in path.iterdir():
            if item.is_dir() and item.name.endswith(".Report"):
                definition = item / "definition"
                if definition.exists():
                    report_name = item.name.replace(".Report", "")
                    results.append({"name": report_name, "definition_path": definition})
        # Also check if definition exists directly
        if not results:
            definition = path / "definition"
            if definition.exists():
                results.append({"name": path.name, "definition_path": definition})

    return results


# _normalize_path, _find_definition_folder, _load_json_file imported from core.utilities.pbip_utils


def _extract_field_reference(field_data: Dict) -> Optional[Dict]:
    """Extract field reference from a field definition"""
    result = {}

    # Handle Column reference
    if "Column" in field_data:
        column = field_data["Column"]
        entity = column.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
        property_name = column.get("Property", "")
        result = {
            "type": "column",
            "entity": entity,
            "property": property_name,
            "reference": f"{entity}[{property_name}]",
        }
    # Handle Measure reference
    elif "Measure" in field_data:
        measure = field_data["Measure"]
        entity = measure.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
        property_name = measure.get("Property", "")
        result = {
            "type": "measure",
            "entity": entity,
            "property": property_name,
            "reference": f"[{property_name}]",
        }
    # Handle Aggregation
    elif "Aggregation" in field_data:
        agg = field_data["Aggregation"]
        expression = agg.get("Expression", {})
        if "Column" in expression:
            column = expression["Column"]
            entity = column.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
            property_name = column.get("Property", "")
            agg_func = agg.get("Function", 0)
            agg_names = {0: "Sum", 1: "Avg", 2: "Min", 3: "Max", 4: "Count", 5: "CountDistinct"}
            result = {
                "type": "aggregation",
                "entity": entity,
                "property": property_name,
                "function": agg_names.get(agg_func, str(agg_func)),
                "reference": f"{agg_names.get(agg_func, 'Agg')}({entity}[{property_name}])",
            }
    # Handle HierarchyLevel
    elif "HierarchyLevel" in field_data:
        hier = field_data["HierarchyLevel"]
        entity = (
            hier.get("Expression", {})
            .get("Hierarchy", {})
            .get("Expression", {})
            .get("SourceRef", {})
            .get("Entity", "")
        )
        hierarchy = hier.get("Expression", {}).get("Hierarchy", {}).get("Hierarchy", "")
        level = hier.get("Level", "")
        result = {
            "type": "hierarchy_level",
            "entity": entity,
            "hierarchy": hierarchy,
            "level": level,
            "reference": f"{entity}[{hierarchy}].[{level}]",
        }

    return result if result else None


def _extract_filter_values(filter_data: Dict) -> List[str]:
    """Extract filter values from filter definition"""
    values = []

    where_clause = filter_data.get("Where", [])
    for condition in where_clause:
        in_clause = condition.get("Condition", {}).get("In", {})
        filter_values = in_clause.get("Values", [])
        for value_list in filter_values:
            for value_item in value_list:
                literal = value_item.get("Literal", {})
                val = literal.get("Value", "")
                # Clean up the value
                if isinstance(val, str):
                    if val.startswith("'") and val.endswith("'"):
                        val = val[1:-1]
                values.append(val)

    return values


def _extract_filters_from_config(filter_config: Dict) -> List[Dict]:
    """Extract filters from a filterConfig structure (used in both report.json and page.json)"""
    filters = []

    page_filters = filter_config.get("filters", [])

    for flt in page_filters:
        filter_info = {
            "name": flt.get("name", ""),
            "type": flt.get("type", ""),
            "how_created": flt.get("howCreated", ""),
        }

        # Check for ordinal (report-level filters have this)
        if "ordinal" in flt:
            filter_info["ordinal"] = flt.get("ordinal")

        # Extract field reference
        field = flt.get("field", {})
        field_ref = _extract_field_reference(field)
        if field_ref:
            filter_info["field"] = field_ref

        # Extract filter values
        filter_def = flt.get("filter", {})
        if filter_def:
            filter_info["values"] = _extract_filter_values(filter_def)
        else:
            # No filter applied means "All" is selected
            filter_info["values"] = ["(All)"]

        # Check for additional settings
        objects = flt.get("objects", {})
        general = objects.get("general", [])
        if general and len(general) > 0:
            props = general[0].get("properties", {})
            # Check for inverted selection mode
            if "isInvertedSelectionMode" in props:
                expr = props["isInvertedSelectionMode"].get("expr", {})
                literal = expr.get("Literal", {})
                if literal.get("Value") == "true":
                    filter_info["is_inverted"] = True
            # Check for single select requirement
            if "requireSingleSelect" in props:
                expr = props["requireSingleSelect"].get("expr", {})
                literal = expr.get("Literal", {})
                if literal.get("Value") == "true":
                    filter_info["single_select"] = True

        filters.append(filter_info)

    return filters


def _extract_page_filters(page_data: Dict) -> List[Dict]:
    """Extract filter pane filters from page.json"""
    filter_config = page_data.get("filterConfig", {})
    return _extract_filters_from_config(filter_config)


def _extract_report_filters(report_data: Dict) -> List[Dict]:
    """Extract 'Filters on all pages' from report.json"""
    filter_config = report_data.get("filterConfig", {})
    return _extract_filters_from_config(filter_config)


def _extract_visual_info(visual_data: Dict, visual_path: Path) -> Dict:
    """Extract information about a visual"""
    visual = visual_data.get("visual", {})
    visual_group = visual_data.get("visualGroup", {})

    result = {
        "name": visual_data.get("name", ""),
        "position": visual_data.get("position", {}),
        "is_hidden": visual_data.get("isHidden", False),
        "parent_group": visual_data.get("parentGroupName", None),
    }

    # Visual type and query info
    if visual:
        result["visual_type"] = visual.get("visualType", "")

        # Get title if available
        vc_objects = visual.get("visualContainerObjects", {})
        title_config = vc_objects.get("title", [])
        if title_config and len(title_config) > 0:
            title_props = title_config[0].get("properties", {})
            title_text = title_props.get("text", {})
            if "expr" in title_text:
                literal = title_text["expr"].get("Literal", {})
                title = literal.get("Value", "").strip("'")
                result["title"] = title

        # Extract fields used in the visual
        query = visual.get("query", {})
        query_state = query.get("queryState", {})

        fields = []
        for bucket_name, bucket_data in query_state.items():
            projections = bucket_data.get("projections", [])
            for proj in projections:
                field_data = proj.get("field", {})
                field_ref = _extract_field_reference(field_data)
                if field_ref:
                    field_info = {
                        "bucket": bucket_name,
                        "display_name": proj.get("displayName", proj.get("nativeQueryRef", "")),
                        "query_ref": proj.get("queryRef", ""),
                        **field_ref,
                    }
                    fields.append(field_info)

        if fields:
            result["fields"] = fields

        # H-09/H-22: visual-level filters live in the TOP-LEVEL filterConfig.
        from core.pbip.pbir_model import get_visual_filters

        visual_filters = get_visual_filters(visual_data)
        if visual_filters:
            result["filters"] = _extract_filters_from_config({"filters": visual_filters})

        # Check for sync group (slicers)
        sync_group = visual.get("syncGroup", {})
        if sync_group:
            result["sync_group"] = sync_group.get("groupName", "")

    # Visual group info
    if visual_group:
        result["is_group"] = True
        result["group_display_name"] = visual_group.get("displayName", "")
        result["group_mode"] = visual_group.get("groupMode", "")

    # Annotations (name-value pairs)
    annotations = visual_data.get("annotations", [])
    if annotations:
        result["annotations"] = annotations

    # Mobile layout presence
    result["has_mobile_layout"] = (visual_path.parent / "mobile.json").exists()

    return result


def _get_page_display_name(page_folder: Path) -> Optional[str]:
    """Get just the display name from page.json without loading visuals.
    Used for early filtering by page name to avoid loading visual JSON files."""
    page_json_path = page_folder / "page.json"
    if not page_json_path.exists():
        return None
    page_data = _load_json_file(page_json_path)
    if not page_data:
        return None
    return page_data.get("displayName", page_folder.name)


def _summarize_visual(visual_info: Dict) -> Dict:
    """Create a compact summary of a visual for summary_only mode."""
    summary = {
        "name": visual_info.get("name", ""),
        "visual_type": visual_info.get("visual_type", ""),
    }
    if visual_info.get("title"):
        summary["title"] = visual_info["title"]
    if visual_info.get("is_hidden"):
        summary["is_hidden"] = True
    if visual_info.get("is_group"):
        summary["is_group"] = True
        if visual_info.get("group_display_name"):
            summary["group_display_name"] = visual_info["group_display_name"]
    if visual_info.get("sync_group"):
        summary["sync_group"] = visual_info["sync_group"]
    # Flatten fields to compact strings: "bucket: reference"
    fields = visual_info.get("fields", [])
    if fields:
        summary["fields"] = [
            f"{f.get('bucket', '?')}: {f.get('reference', f.get('display_name', '?'))}"
            for f in fields
        ]
    return summary


def _summarize_filter(filter_info: Dict) -> Dict:
    """Create a compact summary of a filter for summary_only mode."""
    MAX_FILTER_VALUES = 10
    ref = filter_info.get("field", {}).get("reference", filter_info.get("name", "?"))
    summary = {"field": ref, "type": filter_info.get("type", "")}
    values = filter_info.get("values", [])
    if values and values != ["(All)"]:
        if len(values) > MAX_FILTER_VALUES:
            summary["values"] = values[:MAX_FILTER_VALUES]
            summary["values_truncated"] = len(values)
        else:
            summary["values"] = values
    if filter_info.get("is_inverted"):
        summary["is_inverted"] = True
    return summary


def _get_page_info(page_folder: Path, summary_only: bool = False) -> Dict:
    """Get complete information for a page.

    Args:
        page_folder: Path to the page folder
        summary_only: If True, return compact visual/filter info to reduce token usage
    """
    page_json_path = page_folder / "page.json"
    if not page_json_path.exists():
        return None

    page_data = _load_json_file(page_json_path)
    if not page_data:
        return None

    page_info = {
        "page_id": page_folder.name,
        "display_name": page_data.get("displayName", page_folder.name),
    }

    # Page annotations
    annotations = page_data.get("annotations", [])
    if annotations:
        page_info["annotations"] = annotations

    if not summary_only:
        page_info["display_option"] = page_data.get("displayOption", "")
        page_info["width"] = page_data.get("width", 0)
        page_info["height"] = page_data.get("height", 0)

    # Extract filter pane filters
    filters = _extract_page_filters(page_data)
    if summary_only:
        page_info["filter_pane_filters"] = [_summarize_filter(f) for f in filters]
    else:
        page_info["filter_pane_filters"] = filters
    page_info["filter_count"] = len(filters)

    # Extract visuals
    visuals = []
    visuals_path = page_folder / "visuals"

    if visuals_path.exists():
        for visual_folder in visuals_path.iterdir():
            if not visual_folder.is_dir():
                continue

            visual_json_path = visual_folder / "visual.json"
            if not visual_json_path.exists():
                continue

            visual_data = _load_json_file(visual_json_path)
            if not visual_data:
                continue

            visual_info = _extract_visual_info(visual_data, visual_json_path)
            visuals.append(visual_info)

    if summary_only:
        page_info["visuals"] = [_summarize_visual(v) for v in visuals]
    else:
        page_info["visuals"] = visuals
    page_info["visual_count"] = len(visuals)

    return page_info


def _export_measure_usage_csv(
    pages_output, report_filters_output, all_measures, reports_scanned, export_path
):
    """Export measure usage data to CSV file.

    Creates a CSV with columns: Report, Page, Measure.
    One row per measure per page.
    """
    import csv
    from datetime import datetime

    export_dir = (
        Path(export_path) if export_path else Path(__file__).parent.parent.parent / "exports"
    )
    export_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = export_dir / f"measure_usage_{timestamp}.csv"

    row_count = 0
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Report", "Page", "Measure"])

        # Report-level filter measures
        for rname, measures in sorted(report_filters_output.items()):
            for m in measures:
                writer.writerow([rname, "[Report-level filters]", m])
                row_count += 1

        # Page measures
        for page_entry in pages_output:
            page_name = page_entry["page"]
            # Extract report name from prefix if present: "[ReportName] PageName"
            report = ""
            pname = page_name
            if page_name.startswith("["):
                bracket_end = page_name.find("] ")
                if bracket_end > 0:
                    report = page_name[1:bracket_end]
                    pname = page_name[bracket_end + 2 :]
            for m in page_entry["measures"]:
                writer.writerow([report, pname, m])
                row_count += 1

    return {
        "success": True,
        "file_path": str(csv_path),
        "rows_exported": row_count,
        "total_unique_measures": len(all_measures),
        "reports_scanned": reports_scanned,
        "message": f"Exported {row_count} rows ({len(all_measures)} unique measures) to:\n  {csv_path}",
    }


def _format_measure_usage_text(pages_output, report_filters_output, all_measures, reports_scanned):
    """Format measure usage as a simple readable text list.

    Output format:
      == Report Name ==
      Page Name (N measures)
        - Table[Measure1]
        - Table[Measure2]
    """
    lines = []
    lines.append(
        f"Measure Usage: {len(all_measures)} unique measures across {len(reports_scanned)} report(s)"
    )
    lines.append(f"Reports: {', '.join(reports_scanned)}")
    lines.append("")

    # Report-level filter measures
    for rname, measures in sorted(report_filters_output.items()):
        lines.append(f"== {rname} - Report-level filters ({len(measures)} measures) ==")
        for m in measures:
            lines.append(f"  - {m}")
        lines.append("")

    # Group pages by report prefix
    current_report = None
    for page_entry in pages_output:
        page_name = page_entry["page"]
        measures = page_entry["measures"]

        # Extract report name from prefix
        report = ""
        pname = page_name
        if page_name.startswith("["):
            bracket_end = page_name.find("] ")
            if bracket_end > 0:
                report = page_name[1:bracket_end]
                pname = page_name[bracket_end + 2 :]

        if report != current_report:
            if current_report is not None:
                lines.append("")
            lines.append(f"== {report or reports_scanned[0]} ==")
            current_report = report

        lines.append(f"{pname} ({len(measures)} measures)")
        for m in measures:
            lines.append(f"  - {m}")

    return {
        "success": True,
        "total_unique_measures": len(all_measures),
        "reports_scanned": reports_scanned,
        "text": "\n".join(lines),
    }


def handle_report_measure_usage(args: Dict[str, Any]) -> Dict[str, Any]:
    """Find all measures used across report folders, grouped by page.

    Scans queryState projections, visual objects (data labels, conditional formatting,
    reference lines, dynamic titles), visual-level filters, page-level filters, and
    report-level filters.
    """
    pbip_path = args.get("pbip_path")
    measure_filter = args.get("measure_filter", None)
    page_filter = args.get("page_name", None)
    output_format = args.get("output_format", "text")
    export_path = args.get("export_path", None)

    if not pbip_path:
        return error_envelope(
            "missing_argument",
            "pbip_path parameter is required",
        )

    report_defs = _find_all_report_definitions(pbip_path)
    if not report_defs:
        return error_envelope(
            "not_found",
            f"Could not find any report definition folders in: {pbip_path}",
        )

    # page_key -> set of measure keys ("Table[Measure]")
    page_measures: Dict[str, set] = {}
    # report-level measures (apply to all pages)
    report_filter_measures: Dict[str, set] = {}  # report_name -> set of measure keys

    for report_def in report_defs:
        report_name = report_def["name"]
        definition_path = report_def["definition_path"]
        prefix = f"[{report_name}] " if len(report_defs) > 1 else ""

        # 1. Report-level filters
        report_json_path = definition_path / "report.json"
        if report_json_path.exists():
            report_data = _load_json_file(report_json_path)
            if report_data:
                filter_config = report_data.get("filterConfig", {})
                for m_ref in _extract_measures_from_filter_config(filter_config):
                    m_key = f"{m_ref['entity']}[{m_ref['measure']}]"
                    report_filter_measures.setdefault(report_name, set()).add(m_key)

        # 2. Pages
        pages_path = definition_path / "pages"
        if not pages_path.exists():
            continue

        for page_folder in pages_path.iterdir():
            if not page_folder.is_dir():
                continue

            page_json_path = page_folder / "page.json"
            if not page_json_path.exists():
                continue

            page_data = _load_json_file(page_json_path)
            if not page_data:
                continue

            page_display_name = page_data.get("displayName", page_folder.name)

            if page_filter and page_filter.lower() not in page_display_name.lower():
                continue

            full_page_name = f"{prefix}{page_display_name}"
            measures_on_page = page_measures.setdefault(full_page_name, set())

            # 2a. Page-level filters
            page_filter_config = page_data.get("filterConfig", {})
            for m_ref in _extract_measures_from_filter_config(page_filter_config):
                measures_on_page.add(f"{m_ref['entity']}[{m_ref['measure']}]")

            # 2b. Visuals
            visuals_path = page_folder / "visuals"
            if not visuals_path.exists():
                continue

            for visual_folder in visuals_path.iterdir():
                if not visual_folder.is_dir():
                    continue

                visual_json_path = visual_folder / "visual.json"
                if not visual_json_path.exists():
                    continue

                visual_data = _load_json_file(visual_json_path)
                if not visual_data:
                    continue

                for m_ref in _extract_measures_from_visual(visual_data):
                    measures_on_page.add(f"{m_ref['entity']}[{m_ref['measure']}]")

    # Build page output
    all_measures: set = set()
    pages_output = []
    for page_name in sorted(page_measures.keys()):
        measures = sorted(page_measures[page_name])
        if measure_filter:
            measures = [m for m in measures if measure_filter.lower() in m.lower()]
        if measures:
            all_measures.update(measures)
            pages_output.append(
                {
                    "page": page_name,
                    "measures": measures,
                }
            )

    # Report-level filter measures (shared across all pages)
    report_filters_output = {}
    for rname, rms in report_filter_measures.items():
        filtered = sorted(rms)
        if measure_filter:
            filtered = [m for m in filtered if measure_filter.lower() in m.lower()]
        if filtered:
            all_measures.update(filtered)
            report_filters_output[rname] = filtered

    reports_scanned = [r["name"] for r in report_defs]

    # Export to CSV file if export_path provided
    if export_path is not None:
        return _export_measure_usage_csv(
            pages_output, report_filters_output, all_measures, reports_scanned, export_path
        )

    # Text format: simple readable list (default)
    if output_format == "text":
        return _format_measure_usage_text(
            pages_output, report_filters_output, all_measures, reports_scanned
        )

    # JSON format: structured data (original behavior)
    result: Dict[str, Any] = {
        "success": True,
        "total_unique_measures": len(all_measures),
        "reports_scanned": reports_scanned,
        "pages": pages_output,
    }
    if report_filters_output:
        result["report_level_filter_measures"] = report_filters_output

    return result


def handle_report_info(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle report info request - dispatches by operation."""
    operation = args.get("operation", "info")

    if operation == "measure_usage":
        return handle_report_measure_usage(args)

    # Default: 'info' operation (original behavior)
    pbip_path = args.get("pbip_path")
    include_visuals = args.get("include_visuals", True)
    include_filters = args.get("include_filters", True)
    page_filter = args.get("page_name", None)
    summary_only = args.get("summary_only", True)
    max_visuals_per_page = args.get("max_visuals_per_page", 50)

    if not pbip_path:
        return error_envelope(
            "missing_argument",
            "pbip_path parameter is required - path to PBIP project, .Report folder, or definition folder",
        )

    # Find definition folder
    definition_path = _find_definition_folder(pbip_path)
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {pbip_path}. Ensure path points to a valid PBIP project.",
        )

    # Get pages folder
    pages_path = definition_path / "pages"
    if not pages_path.exists():
        return error_envelope(
            "not_found",
            f"No pages folder found in: {definition_path}",
        )

    # Load report.json for "Filters on all pages" and annotations
    report_json_path = definition_path / "report.json"
    report_level_filters = []
    report_annotations = []
    if report_json_path.exists():
        report_data = _load_json_file(report_json_path)
        if report_data:
            report_level_filters = _extract_report_filters(report_data)
            report_annotations = report_data.get("annotations", [])

    # Collect page information
    pages = []
    total_visuals = 0
    total_filters = 0
    visual_type_counts = {}

    for page_folder in pages_path.iterdir():
        if not page_folder.is_dir():
            continue

        # Early filter: check display name before loading visuals
        if page_filter:
            display_name = _get_page_display_name(page_folder)
            if not display_name or page_filter.lower() not in display_name.lower():
                continue

        page_info = _get_page_info(page_folder, summary_only=summary_only)
        if not page_info:
            continue

        # Count statistics (use visual_count which is always present)
        total_visuals += page_info["visual_count"]
        total_filters += page_info["filter_count"]

        # Count visual types from visuals list (present in both modes)
        for visual in page_info.get("visuals", []):
            vtype = visual.get("visual_type", "unknown")
            if vtype:
                visual_type_counts[vtype] = visual_type_counts.get(vtype, 0) + 1

        # Apply max_visuals_per_page cap (stats already counted above)
        if max_visuals_per_page > 0 and "visuals" in page_info:
            visuals_list = page_info["visuals"]
            if len(visuals_list) > max_visuals_per_page:
                page_info["visuals"] = visuals_list[:max_visuals_per_page]
                page_info["visuals_truncated"] = True

        # Optionally exclude visuals/filters from response
        if not include_visuals:
            page_info.pop("visuals", None)
        if not include_filters:
            page_info.pop("filter_pane_filters", None)

        pages.append(page_info)

    # Sort pages by display name
    pages.sort(key=lambda x: x.get("display_name", ""))

    # Summarize report-level filters if summary_only
    if summary_only and report_level_filters:
        report_level_filters = [_summarize_filter(f) for f in report_level_filters]

    result = {
        "success": True,
        "summary": {
            "total_pages": len(pages),
            "total_visuals": total_visuals,
            "total_filter_pane_filters": total_filters,
            "filters_on_all_pages_count": len(report_level_filters),
            "visual_types": visual_type_counts,
        },
        "filters_on_all_pages": report_level_filters,
        "pages": pages,
    }

    if report_annotations:
        result["annotations"] = report_annotations

    # Optionally exclude report-level filters
    if not include_filters:
        result.pop("filters_on_all_pages", None)

    return result
