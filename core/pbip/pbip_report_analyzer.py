"""
PBIR Report Analyzer - Parses PBIR report definitions and extracts visual metadata.

This module provides comprehensive parsing of Power BI Report (PBIR) definitions
including pages, visuals, bookmarks, and field references.
"""

import json
import logging
import os
from typing import Dict, List, Optional, Set, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# P2-56: tqdm is imported lazily inside the parse loop. This module also runs
# inside the MCP stdio server where progress-bar output corrupts stdout JSON,
# and module-top import adds startup cost for a dep that's only useful in
# interactive CLI runs.
from core.utilities.json_utils import load_json
from core.pbip.pbir_field_refs import extract_field_refs, refs_to_dicts, split_refs
from core.pbip.pbir_model import filter_polarity, get_bookmark_references, get_visual_filters

logger = logging.getLogger(__name__)


class PbirReportAnalyzer:
    """Parses PBIR report definitions and extracts visual metadata."""

    def __init__(self):
        """Initialize the report analyzer."""
        self.logger = logger

    def analyze_report(self, report_folder: str) -> Dict[str, Any]:
        """Parse all report definition files.

        Raises:
            FileNotFoundError: If report folder doesn't exist
            ValueError: If report format is invalid
        """
        if not os.path.exists(report_folder):
            raise FileNotFoundError(f"Report folder not found: {report_folder}")

        definition_path = os.path.join(report_folder, "definition")
        if not os.path.isdir(definition_path):
            raise ValueError(f"No definition folder found in {report_folder}")

        self.logger.info(f"Analyzing report: {report_folder}")

        result = {
            "report_folder": report_folder,
            "report": {},
            "pages": [],
            "bookmarks": [],
            "filters": [],
        }

        try:
            # Parse report.json
            report_file = os.path.join(definition_path, "report.json")
            if os.path.exists(report_file):
                result["report"] = self._parse_report_json(report_file)

            # Parse pages
            pages_path = os.path.join(definition_path, "pages")
            if os.path.isdir(pages_path):
                result["pages"] = self._parse_pages(pages_path)

            # Parse bookmarks
            bookmarks_path = os.path.join(definition_path, "bookmarks")
            if os.path.isdir(bookmarks_path):
                result["bookmarks"] = self._parse_bookmarks(bookmarks_path)

            # Report-level (extension) measures: their DAX counts as model usage
            result["report_measures"] = self._parse_report_extensions(definition_path)

            self.logger.info(
                f"Report analysis complete: {len(result['pages'])} pages, "
                f"{sum(len(p.get('visuals', [])) for p in result['pages'])} visuals"
            )

        except Exception as e:
            self.logger.error(f"Error analyzing report: {e}")
            raise

        return result

    def _parse_report_json(self, file_path: str) -> Dict[str, Any]:
        """Parse report.json file including report-level filters."""
        try:
            data = load_json(file_path)

            # Extract report-level filters (filters on all pages)
            report_filters = self._extract_report_filters(data)

            return {
                "name": data.get("name", ""),
                "description": data.get("description", ""),
                "version": data.get("$schema", ""),
                "config": data.get("config", {}),
                "filters": report_filters,
            }

        except (OSError, json.JSONDecodeError, KeyError) as e:
            self.logger.warning(f"Failed to parse report.json: {e}")
            return {}

    def _extract_report_filters(self, report_data: Dict) -> List[Dict[str, Any]]:
        """Extract report-level filters (filters on all pages) from report.json."""
        filters = []

        try:
            filter_config = report_data.get("filterConfig", {})
            report_filters = filter_config.get("filters", [])

            for filt in report_filters:
                filter_info = {
                    "name": filt.get("name", ""),
                    "field": self._extract_filter_field(filt.get("field", {})),
                    "how_created": filt.get("howCreated", ""),
                    "filter_level": "report",  # Mark as report-level filter
                    "filter_type": filt.get("type", ""),
                    "filter_values": self._extract_filter_values(filt),
                }
                filters.append(filter_info)

        except (KeyError, TypeError) as e:
            self.logger.warning(f"Error extracting report filters: {e}")

        return filters

    def _parse_pages(self, pages_path: str) -> List[Dict[str, Any]]:
        """Parse all page definitions."""
        pages = []

        try:
            # Each page has its own folder with page.json and visuals/
            for page_id in os.listdir(pages_path):
                page_folder = os.path.join(pages_path, page_id)

                if not os.path.isdir(page_folder):
                    continue

                page_json = os.path.join(page_folder, "page.json")
                if not os.path.exists(page_json):
                    continue

                page_data = self._parse_page_json(page_json, page_folder)
                if page_data:
                    pages.append(page_data)

        except OSError as e:
            self.logger.error(f"Error parsing pages: {e}")

        return pages

    def _parse_page_json(self, page_json: str, page_folder: str) -> Optional[Dict[str, Any]]:
        """Parse a single page JSON file."""
        try:
            data = load_json(page_json)

            page = {
                "id": data.get("name", ""),
                "display_name": data.get("displayName", ""),
                "width": data.get("width", 1280),
                "height": data.get("height", 720),
                "display_option": data.get("displayOption", ""),
                "filters": self._extract_page_filters(data),
                "visuals": [],
            }

            # Parse visuals
            visuals_path = os.path.join(page_folder, "visuals")
            if os.path.isdir(visuals_path):
                page["visuals"] = self._parse_visuals(visuals_path)

            return page

        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            self.logger.error(f"Error parsing page {page_json}: {e}")
            return None

    def _extract_page_filters(self, page_data: Dict) -> List[Dict[str, Any]]:
        """Extract filters from page configuration."""
        filters = []

        try:
            filter_config = page_data.get("filterConfig", {})
            page_filters = filter_config.get("filters", [])

            for filt in page_filters:
                filter_info = {
                    "name": filt.get("name", ""),
                    "field": self._extract_filter_field(filt.get("field", {})),
                    "how_created": filt.get("howCreated", ""),
                    "filter_level": "page",  # Mark as page-level filter
                    "filter_type": filt.get("type", ""),
                    "filter_values": self._extract_filter_values(filt),
                }
                filters.append(filter_info)

        except (KeyError, TypeError) as e:
            self.logger.warning(f"Error extracting page filters: {e}")

        return filters

    _FIELD_TYPE = {
        "column": "Column",
        "measure": "Measure",
        "hierarchy": "Hierarchy",
        "hierarchy_level": "HierarchyLevel",
    }

    def _extract_filter_field(self, field: Dict) -> Dict[str, str]:
        """Field of a filter definition (Column / Measure / Aggregation / HierarchyLevel)."""
        try:
            refs = [r for r in extract_field_refs(field) if r.kind != "selector"]
            if refs:
                r = refs[0]
                out = {
                    "type": self._FIELD_TYPE.get(r.kind, "Unknown"),
                    "table": r.table,
                    "name": r.name,
                }
                if r.level:
                    out["level"] = r.level
                if r.variation_column:
                    out["variation_column"] = r.variation_column
                return out
        except (KeyError, TypeError, AttributeError) as e:
            self.logger.warning(f"Error extracting filter field: {e}")

        return {"type": "Unknown", "table": "", "name": ""}

    def _extract_filter_values(self, filt: Dict) -> Dict[str, Any]:
        """Extract filter values from filter condition."""
        result = {
            "has_values": False,
            "values": [],
            "condition_type": None,
            "is_all_selected": False,
        }

        try:
            filter_condition = filt.get("filter", {})
            if not filter_condition:
                return result

            where_clauses = filter_condition.get("Where", [])
            for where in where_clauses:
                condition = where.get("Condition", {})

                # Handle "In" conditions (most common for categorical filters)
                if "In" in condition:
                    result["condition_type"] = "In"
                    in_condition = condition["In"]
                    values_list = in_condition.get("Values", [])

                    for value_group in values_list:
                        for val in value_group:
                            if "Literal" in val:
                                literal_value = val["Literal"].get("Value", "")
                                # Clean up the value (remove quotes)
                                if literal_value.startswith("'") and literal_value.endswith("'"):
                                    literal_value = literal_value[1:-1]
                                elif literal_value.startswith('"') and literal_value.endswith('"'):
                                    literal_value = literal_value[1:-1]
                                result["values"].append(literal_value)

                # Handle "Comparison" conditions (e.g., greater than, less than)
                elif "Comparison" in condition:
                    result["condition_type"] = "Comparison"
                    comp = condition["Comparison"]
                    comp_kind = comp.get("ComparisonKind", "")
                    right = comp.get("Right", {})
                    if "Literal" in right:
                        literal_value = right["Literal"].get("Value", "")
                        if literal_value.startswith("'") and literal_value.endswith("'"):
                            literal_value = literal_value[1:-1]
                        result["values"].append(f"{comp_kind}: {literal_value}")

                # Handle "Between" conditions
                elif "Between" in condition:
                    result["condition_type"] = "Between"
                    between = condition["Between"]
                    lower = between.get("Lower", {}).get("Literal", {}).get("Value", "")
                    upper = between.get("Upper", {}).get("Literal", {}).get("Value", "")
                    if lower.startswith("'"):
                        lower = lower[1:-1]
                    if upper.startswith("'"):
                        upper = upper[1:-1]
                    result["values"].append(f"{lower} to {upper}")

                # Handle "Not" conditions (exclusions)
                elif "Not" in condition:
                    result["condition_type"] = "Not"
                    not_condition = condition["Not"]
                    # Recursively extract the inner condition
                    if "In" in not_condition.get("Expression", {}):
                        inner_in = not_condition["Expression"]["In"]
                        values_list = inner_in.get("Values", [])
                        for value_group in values_list:
                            for val in value_group:
                                if "Literal" in val:
                                    literal_value = val["Literal"].get("Value", "")
                                    if literal_value.startswith("'") and literal_value.endswith(
                                        "'"
                                    ):
                                        literal_value = literal_value[1:-1]
                                    result["values"].append(f"NOT: {literal_value}")

            result["has_values"] = len(result["values"]) > 0

        except (KeyError, TypeError, IndexError) as e:
            self.logger.warning(f"Error extracting filter values: {e}")

        return result

    def _parse_visuals(self, visuals_path: str) -> List[Dict[str, Any]]:
        """Parse all visuals in a page with parallel processing."""
        visual_files = []

        try:
            # Collect all visual.json paths first
            for visual_id in os.listdir(visuals_path):
                visual_folder = os.path.join(visuals_path, visual_id)

                if not os.path.isdir(visual_folder):
                    continue

                visual_json = os.path.join(visual_folder, "visual.json")
                if os.path.exists(visual_json):
                    visual_files.append(visual_json)

            # Parse in parallel (I/O bound operation). Progress bar is optional
            # and only wired up when tqdm is importable AND stdout is a TTY — this
            # keeps the MCP stdio JSON stream clean (P2-56).
            visuals = []
            pbar = None
            try:
                import sys as _sys

                if _sys.stdout.isatty():
                    from tqdm import tqdm as _tqdm  # type: ignore

                    pbar = _tqdm(
                        total=len(visual_files), desc="Parsing visuals", unit="visual", leave=False
                    )
            except Exception:
                pbar = None

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(self._parse_visual_json, vf): vf for vf in visual_files}

                for future in as_completed(futures):
                    try:
                        visual_data = future.result()
                        if visual_data:
                            visuals.append(visual_data)
                    except Exception as e:
                        visual_file = futures[future]
                        self.logger.error(f"Error parsing {visual_file}: {e}")
                    if pbar is not None:
                        pbar.update(1)
            if pbar is not None:
                pbar.close()

        except OSError as e:
            self.logger.error(f"Error parsing visuals: {e}")

        return visuals

    # Visual types that are UI/layout elements, not data-bearing visuals
    UI_VISUAL_TYPES = {
        "shape",
        "basicShape",
        "image",
        "textbox",
        "button",
        "actionButton",
        "bookmarkNavigator",
        "pageNavigator",
        "navigatorButton",
        "visualGroup",
        "group",
        "SummarizeVisualContainer",
        "slicer",
        "advancedSlicerVisual",
        "multiRowCard",  # Multi-row cards are typically label/context displays
    }

    # Visual types that display actual data
    DATA_VISUAL_TYPES = {
        "pivotTable",
        "matrix",
        "table",
        "tableEx",
        "barChart",
        "clusteredBarChart",
        "stackedBarChart",
        "hundredPercentStackedBarChart",
        "columnChart",
        "clusteredColumnChart",
        "stackedColumnChart",
        "hundredPercentStackedColumnChart",
        "lineChart",
        "areaChart",
        "stackedAreaChart",
        "lineStackedColumnComboChart",
        "lineClusteredColumnComboChart",
        "pieChart",
        "donutChart",
        "treemap",
        "funnel",
        "scatterChart",
        "bubbleChart",
        "map",
        "filledMap",
        "azureMap",
        "shapeMap",
        "gauge",
        "kpi",
        "card",
        "multiRowCard",
        "waterfallChart",
        "ribbonChart",
        "decompositionTree",
        "aiVisualization",
        "keyInfluencers",
        "qnaVisual",
        "scriptVisual",
        "pythonVisual",
        "rScript",
    }

    def _parse_visual_json(self, visual_json: str) -> Optional[Dict[str, Any]]:
        """Parse a single visual JSON file."""
        try:
            data = load_json(visual_json)

            # Determine visual type - check for visual groups first
            visual_type = ""
            is_visual_group = False

            if "visualGroup" in data:
                # This is a visual group (container)
                visual_type = "visualGroup"
                is_visual_group = True
            else:
                visual_type = data.get("visual", {}).get("visualType", "")
                # SummarizeVisualContainer is a container type like visualGroup
                if visual_type == "SummarizeVisualContainer":
                    is_visual_group = True

            # Determine if this is a data-bearing visual
            is_data_visual = self._is_data_visual(visual_type, data)

            visual = {
                "id": data.get("name", ""),
                "visual_type": visual_type,
                "is_visual_group": is_visual_group,
                "is_data_visual": is_data_visual,
                "position": data.get("position", {}),
                "fields": {"columns": [], "measures": [], "hierarchies": []},
                "filters": [],
            }

            # Review G-09: ONE walk over the whole visual.json — every query role
            # (Deneb `dataset` included), sortDefinition, objects (CF, dynamic
            # titles), visualContainerObjects, the TOP-LEVEL filterConfig and
            # `SourceRef.Source` aliases. `fields` keeps the legacy shape (all
            # non-filter bindings); `field_refs` carries every ref + location.
            all_refs = extract_field_refs(data)
            visual["field_refs"] = refs_to_dicts(all_refs)
            visual["fields"] = split_refs([r for r in all_refs if r.location != "filter"])
            visual["bookmark_references"] = get_bookmark_references(data)

            # Extract visual name (for Selection Pane)
            visual["visual_name"] = self._extract_visual_title(data)

            # Extract visual title (the title text shown on the visual)
            visual["title"] = self._extract_visual_title_text(data)

            # Visual-level filters live in the TOP-LEVEL filterConfig.filters
            # (G-09: `visual.filters` does not exist in PBIR).
            for filt in get_visual_filters(data):
                filter_info = {
                    "name": filt.get("name", ""),
                    "field": self._extract_filter_field(filt.get("field", {})),
                    "type": filt.get("type", ""),
                    "polarity": filter_polarity(filt, data),
                    "filter_values": self._extract_filter_values(filt),
                }
                visual["filters"].append(filter_info)

            return visual

        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            self.logger.error(f"Error parsing visual {visual_json}: {e}")
            return None

    def _is_data_visual(self, visual_type: str, data: Dict) -> bool:
        """Return True if the visual is data-bearing (chart/table/matrix), False for UI/layout elements."""
        # Visual groups and summary containers are never data visuals
        if "visualGroup" in data:
            return False
        if visual_type == "SummarizeVisualContainer":
            return False

        # Empty type with no visual property is a group/container
        if not visual_type:
            return False

        # Check against known UI types
        if visual_type.lower() in {t.lower() for t in self.UI_VISUAL_TYPES}:
            return False

        # Special handling for card visuals - single-field cards showing context (date, user)
        # are UI elements, cards with actual measures are data visuals
        # Note: multiRowCard is always UI (in UI_VISUAL_TYPES) since they're used for labels
        visual_type_lower = visual_type.lower()
        if visual_type_lower == "card":
            visual_data = data.get("visual", {})
            query = visual_data.get("query", {})
            fields = self._extract_visual_fields(query)
            # Cards with measures are data visuals, pure column cards are context/UI
            return len(fields.get("measures", [])) > 0

        # Check against known data visual types
        if visual_type_lower in {t.lower() for t in self.DATA_VISUAL_TYPES}:
            return True

        # For unknown types, check if it has data bindings
        visual_data = data.get("visual", {})
        query = visual_data.get("query", {})
        query_state = query.get("queryState", {})

        # If it has query projections, it's likely a data visual
        has_projections = any(
            proj_type in query_state
            for proj_type in [
                "Category",
                "Y",
                "Values",
                "Rows",
                "Columns",
                "Legend",
                "X",
                "Size",
                "Details",
            ]
        )

        return has_projections

    def _extract_visual_fields(self, query: Dict) -> Dict[str, List]:
        """Recursively extract field references from a visual query definition."""
        # Use sets for O(1) lookups during collection
        field_sets = {"columns": set(), "measures": set(), "hierarchies": set()}

        try:
            # Process query state projections — EVERY role (G-09: Deneb's
            # `dataset` and any future role were missed by a fixed list)
            query_state = query.get("queryState", {}) or {}

            for proj_type in list(query_state.keys()):
                if isinstance(query_state.get(proj_type), dict):
                    proj_data = query_state[proj_type]
                    projections = proj_data.get("projections", [])
                    for proj in projections:
                        field = proj.get("field", {})
                        self._extract_field_from_projection(field, field_sets)

                    # Extract field parameter references (parameterExpr)
                    field_params = proj_data.get("fieldParameters", [])
                    for fp in field_params:
                        param_expr = fp.get("parameterExpr", {})
                        if param_expr:
                            self._extract_field_from_projection(param_expr, field_sets)

            # Also check bindings (for certain visual types)
            bindings = query.get("Binding", {})
            for binding_key, binding_value in bindings.items():
                if isinstance(binding_value, dict):
                    projections = binding_value.get("Projections", [])
                    for proj in projections:
                        self._extract_field_from_projection(proj, field_sets)

        except (KeyError, TypeError, AttributeError) as e:
            self.logger.warning(f"Error extracting visual fields: {e}")

        # Convert sets back to lists for JSON serialization
        return {
            "columns": [{"table": t, "column": c} for t, c in field_sets["columns"]],
            "measures": [{"table": t, "measure": m} for t, m in field_sets["measures"]],
            "hierarchies": [{"table": t, "hierarchy": h} for t, h in field_sets["hierarchies"]],
        }

    def _extract_field_from_projection(self, field: Dict, field_sets: Dict[str, set]) -> None:
        """Extract field information from a projection using sets for O(1) add."""
        try:
            if "Column" in field:
                column = field["Column"]
                entity = column.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
                prop = column.get("Property", "")

                if entity and prop:
                    field_sets["columns"].add((entity, prop))  # O(1) add

            elif "Measure" in field:
                measure = field["Measure"]
                entity = measure.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
                prop = measure.get("Property", "")

                if entity and prop:
                    field_sets["measures"].add((entity, prop))  # O(1) add

            elif "Hierarchy" in field:
                hierarchy = field["Hierarchy"]
                entity = hierarchy.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
                prop = hierarchy.get("Property", "")

                if entity and prop:
                    field_sets["hierarchies"].add((entity, prop))  # O(1) add

            # Recursively check for nested aggregation
            elif "Aggregation" in field:
                agg = field["Aggregation"]
                self._extract_field_from_projection(agg.get("Expression", {}), field_sets)

        except (KeyError, TypeError, AttributeError) as e:
            self.logger.warning(f"Error extracting field from projection: {e}")

    def _extract_visual_title(self, visual_data: Dict) -> Optional[str]:
        """Extract visual name for Selection Pane: vCO.title (if shown) > visualGroup.displayName > None."""
        try:
            # Method 1: Check visual.visualContainerObjects.title (if shown)
            # This is the PRIMARY method - used by Selection Pane when title is visible
            visual = visual_data.get("visual", {})
            visual_container_objects = visual.get("visualContainerObjects", {})
            title_objects = visual_container_objects.get("title", [])

            if isinstance(title_objects, list) and len(title_objects) > 0:
                title_props = title_objects[0].get("properties", {})

                # Check if title is shown
                show_expr = title_props.get("show", {}).get("expr", {}).get("Literal", {})
                is_shown = show_expr.get("Value") == "true" if show_expr else False

                if is_shown:
                    text_expr = title_props.get("text", {}).get("expr", {})

                    # Check for Literal value
                    literal = text_expr.get("Literal", {})
                    if literal:
                        value = literal.get("Value", "")
                        if value:
                            return value.strip("'\"")

                    # Check for Measure-based dynamic title
                    measure = text_expr.get("Measure", {})
                    if measure:
                        measure_name = measure.get("Property", "")
                        if measure_name:
                            # Return the measure name as a placeholder for dynamic title
                            # In Selection Pane, this evaluates dynamically (e.g., "Performance YTD")
                            return f"[Dynamic: {measure_name}]"

            # Method 2: Check visualGroup.displayName (for grouped visuals)
            visual_group = visual_data.get("visualGroup", {})
            if visual_group:
                display_name = visual_group.get("displayName")
                if display_name:
                    return display_name

            # P2-54: Methods 3-6 (legacy title paths vcObjects, config.singleVisual.vcObjects,
            # title.text, properties.text.expr) were dead code in modern PBIR reports.
            # Log once if we detect any legacy shape so the underlying stale export can
            # be flagged, then return None. Previous 120-line fallback ladder obscured
            # the authoritative paths above.
            if (
                visual.get("vcObjects", {}).get("title")
                or visual_data.get("config", {})
                .get("singleVisual", {})
                .get("vcObjects", {})
                .get("title")
                or visual_data.get("title")
                or visual_data.get("properties", {}).get("text")
            ):
                self.logger.warning(
                    "Legacy visual title shape detected — extraction skipped. "
                    "Re-export the report to modern PBIR format for proper title detection."
                )
            return None

        except (KeyError, TypeError, AttributeError) as e:
            self.logger.warning(f"Error extracting visual title: {e}")
            return None

    def _extract_visual_title_text(self, visual_data: Dict) -> Optional[str]:
        """Extract the title text shown on the visual (static or dynamic measure-based)."""
        try:
            visual = visual_data.get("visual", {})
            visual_container_objects = visual.get("visualContainerObjects", {})
            title_objects = visual_container_objects.get("title", [])

            if isinstance(title_objects, list) and len(title_objects) > 0:
                title_props = title_objects[0].get("properties", {})

                # Check if title is shown
                show_expr = title_props.get("show", {}).get("expr", {}).get("Literal", {})
                is_shown = show_expr.get("Value") == "true" if show_expr else False

                if is_shown:
                    text_expr = title_props.get("text", {}).get("expr", {})

                    # Check for Literal value (static title)
                    literal = text_expr.get("Literal", {})
                    if literal:
                        value = literal.get("Value", "")
                        if value:
                            return value.strip("'\"")

                    # Check for Measure-based dynamic title
                    measure = text_expr.get("Measure", {})
                    if measure:
                        measure_name = measure.get("Property", "")
                        if measure_name:
                            # Return the measure name for dynamic titles
                            return f"[Measure: {measure_name}]"

            return None

        except (KeyError, TypeError, AttributeError) as e:
            self.logger.warning(f"Error extracting visual title text: {e}")
            return None

    def _parse_bookmarks(self, bookmarks_path: str) -> List[Dict[str, Any]]:
        """Parse bookmark definitions."""
        bookmarks = []

        try:
            for filename in os.listdir(bookmarks_path):
                if filename.endswith(".bookmark.json"):
                    bookmark_file = os.path.join(bookmarks_path, filename)
                    bookmark = self._parse_bookmark_json(bookmark_file)
                    if bookmark:
                        bookmarks.append(bookmark)

        except OSError as e:
            self.logger.warning(f"Error parsing bookmarks: {e}")

        return bookmarks

    def _parse_bookmark_json(self, bookmark_file: str) -> Optional[Dict[str, Any]]:
        """Parse a single bookmark JSON file."""
        try:
            data = load_json(bookmark_file)
            state = data.get("explorationState") or data.get("state") or {}

            return {
                "id": data.get("name", ""),
                "display_name": data.get("displayName", ""),
                "state": state,
                # G-09: fields a bookmark filters on / captures count as usage
                "field_refs": refs_to_dicts(
                    extract_field_refs(state, base_path=("explorationState",))
                ),
            }

        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            self.logger.warning(f"Error parsing bookmark {bookmark_file}: {e}")
            return None

    def _parse_report_extensions(self, definition_path: str) -> List[Dict[str, Any]]:
        """Report-level measures from ``definition/reportExtensions.json``."""
        path = os.path.join(definition_path, "reportExtensions.json")
        if not os.path.isfile(path):
            return []
        try:
            data = load_json(path)
        except (OSError, ValueError) as e:
            self.logger.warning(f"Failed to parse reportExtensions.json: {e}")
            return []
        out: List[Dict[str, Any]] = []
        for ent in (data or {}).get("entities") or []:
            if not isinstance(ent, dict):
                continue
            for m in ent.get("measures") or []:
                if isinstance(m, dict) and m.get("name"):
                    out.append(
                        {
                            "table": ent.get("name", ""),
                            "name": m.get("name"),
                            "expression": m.get("expression", ""),
                            "data_type": m.get("dataType"),
                        }
                    )
        return out

    def extract_all_field_references(self, report_data: Dict) -> Dict[str, Set[str]]:
        """Extract all unique column, measure, and table references across the entire report."""
        all_refs = {"columns": set(), "measures": set(), "tables": set()}

        for page in report_data.get("pages", []):
            # Page filters
            for filt in page.get("filters", []):
                field = filt.get("field", {})
                table = field.get("table", "")
                name = field.get("name", "")

                if table and name:
                    all_refs["tables"].add(table)
                    field_type = field.get("type", "")

                    if field_type == "Column":
                        all_refs["columns"].add(f"{table}[{name}]")
                    elif field_type == "Measure":
                        all_refs["measures"].add(f"{table}[{name}]")

            # Visuals (bindings + visual-level filters)
            for visual in page.get("visuals", []):
                for ref in visual.get("field_refs", []):
                    if ref.get("table") and ref.get("name"):
                        all_refs["tables"].add(ref["table"])
                        bucket = "columns" if ref.get("kind") == "column" else "measures"
                        if ref.get("kind") in ("measure", "column", "selector"):
                            all_refs[bucket].add(f"{ref['table']}[{ref['name']}]")
                fields = visual.get("fields", {})

                for col in fields.get("columns", []):
                    table = col.get("table", "")
                    column = col.get("column", "")
                    if table and column:
                        all_refs["tables"].add(table)
                        all_refs["columns"].add(f"{table}[{column}]")

                for meas in fields.get("measures", []):
                    table = meas.get("table", "")
                    measure = meas.get("measure", "")
                    if table and measure:
                        all_refs["tables"].add(table)
                        all_refs["measures"].add(f"{table}[{measure}]")

        return all_refs
