"""``VisualQueryBuilder`` methods split out of ``core.debug.visual_query_builder`` (C-17): pure move, no behaviour change.

``VisualQueryBuilder`` inherits :class:`VisualReportReaderMixin`, so every ``self.<method>`` call and every import of
``core.debug.visual_query_builder.VisualQueryBuilder`` keeps working.
"""

from typing import Any, Dict, List, Optional
from pathlib import Path
from core.dax.dax_names import bracket, column_ref
import json
from core.debug.visual_query_types import (
    DATA_VISUAL_TYPES,
    SLICER_VISUAL_TYPES,
    SlicerState,
    UI_VISUAL_TYPES,
    VisualInfo,
    _aggregation_expression,
    _aggregation_label,
    _iter_projections,
    _sanitize_alias,
    _walk_for_measure_refs,
)


class VisualReportReaderMixin:
    """Methods of :class:`VisualQueryBuilder` (visual_report_reader.py)."""

    def list_pages(self) -> List[Dict[str, str]]:
        """List all pages in the report."""
        pages = []
        pages_path = self.definition_path / "pages"

        if not pages_path.exists():
            return pages

        for page_folder in pages_path.iterdir():
            if page_folder.is_dir():
                page_json = page_folder / "page.json"
                if page_json.exists():
                    try:
                        with open(page_json, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            pages.append(
                                {
                                    "id": page_folder.name,
                                    "name": data.get("displayName", page_folder.name),
                                    "ordinal": data.get("ordinal", 0),
                                }
                            )
                    except Exception as e:
                        self.logger.debug(f"Error reading page: {e}")

        # Sort by ordinal
        pages.sort(key=lambda x: x.get("ordinal", 0))
        return pages

    def list_visuals(
        self, page_name: str, include_ui_elements: bool = True
    ) -> List[Dict[str, Any]]:
        """
        List all visuals on a page with friendly names.

        include_ui_elements=False excludes shapes/buttons/groups for cleaner output.
        """
        visuals = []
        page_path = self._find_page_by_name(page_name)

        if not page_path:
            return visuals

        visuals_path = page_path / "visuals"
        if not visuals_path.exists():
            return visuals

        for visual_folder in visuals_path.iterdir():
            if visual_folder.is_dir():
                visual_json = visual_folder / "visual.json"
                if visual_json.exists():
                    try:
                        with open(visual_json, "r", encoding="utf-8") as f:
                            data = json.load(f)

                            # Check for visual groups FIRST (containers without 'visual' property)
                            is_visual_group = "visualGroup" in data
                            visual = data.get("visual", {})

                            if is_visual_group:
                                visual_type = "visualGroup"
                                # Get displayName from visualGroup for naming
                                group_info = data.get("visualGroup", {})
                                title = group_info.get("displayName")
                            else:
                                visual_type = visual.get("visualType", "unknown")
                                # Extract title from visualContainerObjects
                                title = self._extract_visual_title(visual)

                            # Determine if this is a data-bearing visual
                            is_data_visual = self._is_data_visual(visual_type, data)

                            # Skip UI elements if requested
                            if not include_ui_elements and not is_data_visual:
                                continue

                            # Extract measures and columns for better naming
                            measures, columns = self._extract_visual_fields(visual)

                            # Visual-level filters (top-level filterConfig, F-03)
                            from core.pbip.pbir_model import get_visual_filters

                            visual_filters = get_visual_filters(data)

                            # Build friendly name
                            friendly_name = self._build_visual_friendly_name(
                                title=title,
                                visual_type=visual_type,
                                measures=measures,
                                columns=columns,
                                visual_id=visual_folder.name,
                            )

                            visuals.append(
                                {
                                    "id": visual_folder.name,
                                    "name": data.get("name", ""),
                                    "friendly_name": friendly_name,
                                    "title": title,
                                    "type": visual_type,
                                    "type_display": self._get_visual_type_display(visual_type),
                                    "is_slicer": visual_type in SLICER_VISUAL_TYPES,
                                    "is_visual_group": is_visual_group,
                                    "is_data_visual": is_data_visual,
                                    "measures": measures,
                                    "columns": columns,
                                    "filters": visual_filters,  # Include for lightweight documentation
                                    "position": data.get("position", {}),
                                }
                            )
                    except Exception as e:
                        self.logger.debug(f"Error reading visual: {e}")

        return visuals

    def _extract_visual_title(self, visual: Dict) -> Optional[str]:
        """Extract the display title from a visual's configuration."""
        # Try visualContainerObjects.title (most common location)
        visual_container_objects = visual.get("visualContainerObjects", {})
        title_config = visual_container_objects.get("title", [])
        if title_config:
            title_props = title_config[0].get("properties", {})
            text_expr = title_props.get("text", {}).get("expr", {})
            if "Literal" in text_expr:
                title = text_expr["Literal"].get("Value", "").strip("'\"")
                if title:
                    return title

        # Try vcObjects.title (alternative location)
        vc_objects = visual.get("vcObjects", {})
        title_config = vc_objects.get("title", [])
        if title_config:
            title_props = title_config[0].get("properties", {})
            text_val = title_props.get("text", {})
            if isinstance(text_val, str):
                return text_val.strip("'\"")
            text_expr = text_val.get("expr", {})
            if "Literal" in text_expr:
                title = text_expr["Literal"].get("Value", "").strip("'\"")
                if title:
                    return title

        return None

    def _extract_visual_fields(self, visual: Dict) -> tuple:
        """Extract measures and columns from a visual's query configuration.

        F-12: every ``queryState`` role is scanned (``Data`` on cardVisual,
        ``Y2`` on combo charts, ``dataset`` on Deneb...), not a fixed list.
        """
        measures: List[str] = []
        columns: List[str] = []
        for _role, proj in _iter_projections(visual):
            fld = proj.get("field", {}) or {}
            if "Measure" in fld:
                prop = fld["Measure"].get("Property", "")
                if prop and prop not in measures:
                    measures.append(prop)
            elif "Aggregation" in fld:
                label = _aggregation_label(fld["Aggregation"])
                if label and label not in measures:
                    measures.append(label)
            if "Column" in fld:
                prop = fld["Column"].get("Property", "")
                if prop and prop not in columns:
                    columns.append(prop)
        return measures, columns

    def _build_visual_friendly_name(
        self,
        title: Optional[str],
        visual_type: str,
        measures: List[str],
        columns: List[str],
        visual_id: str,
    ) -> str:
        """Build a human-friendly name for a visual."""
        # Priority 1: Use title if available
        if title:
            return title

        # Priority 2: Build name from type + fields
        type_display = self._get_visual_type_display(visual_type)

        if measures:
            # Use first measure for naming
            measure_name = measures[0]
            if len(measures) > 1:
                return f"{type_display}: {measure_name} (+{len(measures)-1})"
            return f"{type_display}: {measure_name}"

        if columns:
            # Use first column for naming
            col_name = columns[0]
            if len(columns) > 1:
                return f"{type_display}: {col_name} (+{len(columns)-1})"
            return f"{type_display}: {col_name}"

        # Priority 3: Just type + short ID
        short_id = visual_id[:8] if len(visual_id) > 8 else visual_id
        return f"{type_display} ({short_id})"

    def _is_data_visual(self, visual_type: str, data: Dict) -> bool:
        """
        True if the visual displays analytical data (chart/table/matrix) vs UI/layout element.
        """
        # Visual groups are never data visuals
        if "visualGroup" in data:
            return False

        # Empty/unknown type with no visual property is a group/container
        if not visual_type or visual_type == "unknown":
            return False

        # Check against known UI types
        if visual_type.lower() in {t.lower() for t in UI_VISUAL_TYPES}:
            return False

        # Special handling for card visuals - single-field cards showing context (date, user)
        # are UI elements, cards with actual measures are data visuals
        # Note: multiRowCard is always UI (in UI_VISUAL_TYPES) since they're used for labels
        visual_type_lower = visual_type.lower()
        if visual_type_lower == "card":
            visual = data.get("visual", {})
            measures, columns = self._extract_visual_fields(visual)
            # Cards with measures are data visuals, pure column cards are context/UI
            return len(measures) > 0

        # Check against known data visual types
        if visual_type_lower in {t.lower() for t in DATA_VISUAL_TYPES}:
            return True

        # For unknown types, check if it has data bindings
        visual = data.get("visual", {})
        query = visual.get("query", {})
        query_state = query.get("queryState", {})

        # If it has query projections in ANY role (Data, dataset, Y2...), it's likely
        # a data visual (F-12).
        del query_state
        return any(True for _ in _iter_projections(visual))

    def _get_visual_type_display(self, visual_type: str) -> str:
        """Get human-friendly display name for visual type."""
        type_mapping = {
            "pivotTable": "Matrix",
            "tableEx": "Table",
            "columnChart": "Column Chart",
            "barChart": "Bar Chart",
            "lineChart": "Line Chart",
            "areaChart": "Area Chart",
            "lineStackedColumnComboChart": "Combo Chart",
            "clusteredBarChart": "Clustered Bar",
            "clusteredColumnChart": "Clustered Column",
            "stackedBarChart": "Stacked Bar",
            "stackedColumnChart": "Stacked Column",
            "hundredPercentStackedBarChart": "100% Stacked Bar",
            "hundredPercentStackedColumnChart": "100% Stacked Column",
            "pieChart": "Pie Chart",
            "donutChart": "Donut Chart",
            "treemap": "Treemap",
            "map": "Map",
            "filledMap": "Filled Map",
            "shapeMap": "Shape Map",
            "slicer": "Slicer",
            "advancedSlicerVisual": "Advanced Slicer",
            "card": "Card",
            "multiRowCard": "Multi-row Card",
            "kpi": "KPI",
            "gauge": "Gauge",
            "scatterChart": "Scatter Chart",
            "funnel": "Funnel",
            "waterfallChart": "Waterfall",
            "ribbonChart": "Ribbon Chart",
            "decompositionTreeVisual": "Decomposition Tree",
            "keyInfluencers": "Key Influencers",
            "qnaVisual": "Q&A",
            "textbox": "Text Box",
            "image": "Image",
            "shape": "Shape",
            "actionButton": "Button",
            "bookmarkNavigator": "Bookmark Navigator",
            "pageNavigator": "Page Navigator",
            "visualGroup": "Visual Group",
            "unknown": "Unknown",
        }
        return type_mapping.get(visual_type, visual_type.replace("Chart", " Chart").title())

    def list_slicers(self, page_name: Optional[str] = None) -> List[SlicerState]:
        """
        List all slicers and their current selections. page_name filters to a specific page.
        """
        all_slicers = []
        pages_path = self.definition_path / "pages"

        if not pages_path.exists():
            return all_slicers

        for page_folder in pages_path.iterdir():
            if not page_folder.is_dir():
                continue

            # Get page display name
            page_display_name = self._get_page_display_name(page_folder)

            if page_name and page_display_name.lower() != page_name.lower():
                continue

            slicers = self._get_page_slicers(page_folder, page_display_name)
            all_slicers.extend(slicers)

        return all_slicers

    def _find_page_by_name(self, page_name: str) -> Optional[Path]:
        """Find page folder by display name or page id. Results are cached.

        Delegates the actual lookup to ``core.utilities.pbip_utils.find_page_folder``
        (DUP-5 consolidated). Local cache is preserved to avoid hitting the
        canonical resolver on every visual lookup in a hot debug loop.
        """
        from core.utilities.pbip_utils import find_page_folder

        cache_key = (page_name or "").lower()
        if cache_key in self._page_path_cache:
            return self._page_path_cache[cache_key]

        result = find_page_folder(self.definition_path, page_name)
        self._page_path_cache[cache_key] = result
        return result

    def _get_page_display_name(self, page_folder: Path) -> str:
        """Get display name from page.json."""
        page_json = page_folder / "page.json"
        if page_json.exists():
            try:
                with open(page_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("displayName", page_folder.name)
            except Exception:
                pass
        return page_folder.name

    def _find_visual(
        self, page_path: Path, visual_id: Optional[str], visual_name: Optional[str]
    ) -> Optional[VisualInfo]:
        """
        Find and parse a visual by ID, name, or friendly name.

        Matching priority:
        1. Exact ID match
        2. Exact name match (from visual.json 'name' field)
        3. Title match (from visualContainerObjects.title)
        4. Friendly name match (type + measures)
        5. Partial/fuzzy match on title or friendly name
        """
        visuals_path = page_path / "visuals"

        if not visuals_path.exists():
            return None

        page_display_name = self._get_page_display_name(page_path)

        # First pass: Try exact matches
        for visual_folder in visuals_path.iterdir():
            if not visual_folder.is_dir():
                continue

            # Exact ID match
            if visual_id and visual_folder.name == visual_id:
                visual_json = visual_folder / "visual.json"
                if visual_json.exists():
                    try:
                        with open(visual_json, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        visual = data.get("visual", {})
                        return self._parse_visual_info(
                            data, visual, visual_folder.name, page_display_name, page_path.name
                        )
                    except Exception as e:
                        self.logger.debug(f"Error reading visual: {e}")

        # Second pass: Try name/title/friendly_name matches
        if visual_name:
            visual_name_lower = visual_name.lower().strip()

            for visual_folder in visuals_path.iterdir():
                if not visual_folder.is_dir():
                    continue

                visual_json = visual_folder / "visual.json"
                if not visual_json.exists():
                    continue

                try:
                    with open(visual_json, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    visual = data.get("visual", {})
                    visual_type = visual.get("visualType", "unknown")

                    # Check stored name
                    stored_name = data.get("name", "")
                    if stored_name and stored_name.lower() == visual_name_lower:
                        return self._parse_visual_info(
                            data, visual, visual_folder.name, page_display_name, page_path.name
                        )

                    # Check title
                    title = self._extract_visual_title(visual)
                    if title and title.lower() == visual_name_lower:
                        return self._parse_visual_info(
                            data, visual, visual_folder.name, page_display_name, page_path.name
                        )

                    # Check friendly name
                    measures, columns = self._extract_visual_fields(visual)
                    friendly_name = self._build_visual_friendly_name(
                        title=title,
                        visual_type=visual_type,
                        measures=measures,
                        columns=columns,
                        visual_id=visual_folder.name,
                    )
                    if friendly_name.lower() == visual_name_lower:
                        return self._parse_visual_info(
                            data, visual, visual_folder.name, page_display_name, page_path.name
                        )

                except Exception as e:
                    self.logger.debug(f"Error reading visual: {e}")

            # Third pass: Try partial/contains matches
            for visual_folder in visuals_path.iterdir():
                if not visual_folder.is_dir():
                    continue

                visual_json = visual_folder / "visual.json"
                if not visual_json.exists():
                    continue

                try:
                    with open(visual_json, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    visual = data.get("visual", {})
                    visual_type = visual.get("visualType", "unknown")

                    # Check if search term is contained in title
                    title = self._extract_visual_title(visual)
                    if title and visual_name_lower in title.lower():
                        return self._parse_visual_info(
                            data, visual, visual_folder.name, page_display_name, page_path.name
                        )

                    # Check if search term is contained in friendly name
                    measures, columns = self._extract_visual_fields(visual)
                    friendly_name = self._build_visual_friendly_name(
                        title=title,
                        visual_type=visual_type,
                        measures=measures,
                        columns=columns,
                        visual_id=visual_folder.name,
                    )
                    if visual_name_lower in friendly_name.lower():
                        return self._parse_visual_info(
                            data, visual, visual_folder.name, page_display_name, page_path.name
                        )

                    # Check if search term matches visual type
                    type_display = self._get_visual_type_display(visual_type)
                    if (
                        visual_name_lower == type_display.lower()
                        or visual_name_lower == visual_type.lower()
                    ):
                        return self._parse_visual_info(
                            data, visual, visual_folder.name, page_display_name, page_path.name
                        )

                except Exception as e:
                    self.logger.debug(f"Error reading visual: {e}")

        return None

    def _parse_visual_info(
        self, data: Dict, visual: Dict, visual_id: str, page_name: str, page_id: str
    ) -> VisualInfo:
        """Parse visual information from JSON.

        F-12: every queryState role is read. Measures come from ``Measure`` and
        ``Aggregation`` (implicit measure) projections, grouping columns from
        active ``Column`` projections. F-03: visual-level filters are read from
        the TOP-LEVEL ``filterConfig.filters`` (``visual.filters`` is a legacy
        fallback) through ``core.pbip.pbir_model.get_visual_filters``.
        """
        from core.pbip.pbir_model import get_visual_filters

        measures: List[str] = []
        columns: List[str] = []
        row_columns: List[str] = []
        measure_aliases: Dict[str, str] = {}
        measure_entities: Dict[str, str] = {}
        roles: Dict[str, int] = {}

        for role, proj in _iter_projections(visual):
            roles[role] = roles.get(role, 0) + 1
            fld = proj.get("field", {}) or {}
            if "Measure" in fld:
                measure_ref = fld["Measure"]
                entity = (
                    (measure_ref.get("Expression") or {}).get("SourceRef", {}).get("Entity", "")
                )
                prop = measure_ref.get("Property", "")
                if prop:
                    ref = bracket(prop)
                    if ref not in measures:
                        measures.append(ref)
                    if entity:
                        measure_entities.setdefault(prop, entity)
            elif "Aggregation" in fld:
                expr = _aggregation_expression(fld["Aggregation"])
                if expr and expr not in measures:
                    measures.append(expr)
                    measure_aliases[expr] = _sanitize_alias(
                        _aggregation_label(fld["Aggregation"]) or expr
                    )

            if "Column" in fld:
                # Skip inactive projections — these are field-parameter-controlled
                # alternatives that Power BI does NOT include as direct groupby axes
                # (e.g., 'd Asset Class'[Asset Class] with active:false on a matrix
                # whose rows are driven by sf Row Drill field parameter)
                if proj.get("active") is False:
                    continue
                col_ref = fld["Column"]
                table = (col_ref.get("Expression") or {}).get("SourceRef", {}).get("Entity", "")
                prop = col_ref.get("Property", "")
                if table and prop:
                    ref = column_ref(table, prop)
                    if ref not in columns:
                        columns.append(ref)
                    if role == "Rows" and ref not in row_columns:
                        row_columns.append(ref)

        filters = list(get_visual_filters(data))

        # Extract measures from visual objects (data labels, conditional formatting,
        # reference lines, dynamic titles, axis min/max, etc.)
        # Classify as scalar (no group-by needed, second EVALUATE) vs grouped (main query).
        #
        # Scalar contexts: visualContainerObjects (title, subtitle), vcObjects (title),
        #   and objects keys for axis bounds / reference lines.
        # Grouped contexts: objects keys for data labels, conditional formatting.
        SCALAR_OBJECT_KEYS = {
            "categoryAxis",
            "valueAxis",
            "xAxis",
            "yAxis",
            "y1Axis",
            "y2Axis",
            "referenceLine",
            "constantLine",
            "percentileLine",
        }
        query_measure_names = set(measures)  # [MeasureName] refs already collected
        grouped_measures_set: set = set()
        scalar_measures_set: set = set()

        # visualContainerObjects & vcObjects → always scalar (titles, subtitles)
        for section_key in ("visualContainerObjects", "vcObjects"):
            section = visual.get(section_key, {})
            if section:
                _walk_for_measure_refs(section, scalar_measures_set)

        # objects → classify per key
        objects_section = visual.get("objects", {})
        if objects_section:
            for key, value in objects_section.items():
                target = scalar_measures_set if key in SCALAR_OBJECT_KEYS else grouped_measures_set
                _walk_for_measure_refs(value, target)

        # Grouped object measures: only include those NOT already in queryState
        object_measure_list = [
            (entity, prop)
            for entity, prop in grouped_measures_set
            if bracket(prop) not in query_measure_names
        ]
        # Scalar measures: ALWAYS include (even if in queryState projections).
        # PBI evaluates scalar measures (titles, axis bounds) in a separate EVALUATE
        # without group-by columns, regardless of whether they appear in queryState.
        # If a scalar measure is also in queryState, remove it from the main measures
        # list so it doesn't end up in the grouped SUMMARIZECOLUMNS.
        scalar_measure_list = list(scalar_measures_set)
        scalar_prop_names = {bracket(prop) for _, prop in scalar_measures_set}
        measures = [m for m in measures if m not in scalar_prop_names]

        # Get title
        title = None
        visual_container_objects = visual.get("visualContainerObjects", {})
        title_config = visual_container_objects.get("title", [])
        if title_config:
            title_props = title_config[0].get("properties", {})
            text_expr = title_props.get("text", {}).get("expr", {})
            if "Literal" in text_expr:
                title = text_expr["Literal"].get("Value", "").strip("'")

        # F-19: the visual's own sort order and matrix subtotal switch.
        sort_definition = list(
            (((visual.get("query") or {}).get("sortDefinition") or {}).get("sort")) or []
        )
        row_subtotals: Optional[bool] = None
        for entry in (visual.get("objects") or {}).get("subTotals") or []:
            props = entry.get("properties") if isinstance(entry, dict) else None
            lit = (((props or {}).get("rowSubtotals") or {}).get("expr") or {}).get("Literal")
            if isinstance(lit, dict) and isinstance(lit.get("Value"), str):
                row_subtotals = lit["Value"].strip().lower() == "true"

        return VisualInfo(
            visual_id=visual_id,
            visual_type=visual.get("visualType", "unknown"),
            visual_name=data.get("name"),
            page_name=page_name,
            page_id=page_id,
            title=title,
            measures=measures,
            columns=columns,
            filters=filters,
            object_measures=object_measure_list,
            scalar_measures=scalar_measure_list,
            row_columns=row_columns,
            measure_aliases=measure_aliases,
            measure_entities=measure_entities,
            sort_definition=sort_definition,
            row_subtotals=row_subtotals,
            roles=roles,
            visual_json=data,
        )

    def _get_report_filters(self) -> List[Dict]:
        """Get report-level filters."""
        if self._report_filters_cache is not None:
            return self._report_filters_cache

        filters = []
        report_json = self.definition_path / "report.json"

        if report_json.exists():
            try:
                with open(report_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    filter_config = data.get("filterConfig", {})
                    filters = filter_config.get("filters", [])
            except Exception as e:
                self.logger.debug(f"Error reading report filters: {e}")

        self._report_filters_cache = filters
        return filters

    def _get_page_filters(self, page_path: Path) -> List[Dict]:
        """Get page-level filters. Results are cached."""
        # Use page path as cache key
        cache_key = str(page_path)
        if cache_key in self._page_filters_cache:
            return self._page_filters_cache[cache_key]

        filters = []
        page_json = page_path / "page.json"

        if page_json.exists():
            try:
                with open(page_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    filter_config = data.get("filterConfig", {})
                    filters = filter_config.get("filters", [])
            except Exception as e:
                self.logger.debug(f"Error reading page filters: {e}")

        self._page_filters_cache[cache_key] = filters
        return filters

    def _get_page_slicers(self, page_path: Path, page_name: str) -> List[SlicerState]:
        """Get all slicers on a page with their current selections. Results are cached."""
        # Check cache first
        cache_key = page_name.lower()
        if cache_key in self._slicers_cache:
            return self._slicers_cache[cache_key]

        slicers = []
        visuals_path = page_path / "visuals"

        if not visuals_path.exists():
            self._slicers_cache[cache_key] = slicers
            return slicers

        for visual_folder in visuals_path.iterdir():
            if not visual_folder.is_dir():
                continue

            visual_json = visual_folder / "visual.json"
            if not visual_json.exists():
                continue

            try:
                with open(visual_json, "r", encoding="utf-8") as f:
                    data = json.load(f)

                visual = data.get("visual", {})
                if visual.get("visualType") not in SLICER_VISUAL_TYPES:
                    continue

                slicer_state = self._parse_slicer_state(data, visual, visual_folder.name, page_name)
                if slicer_state:
                    slicers.append(slicer_state)

            except Exception as e:
                self.logger.debug(f"Error reading slicer: {e}")

        self._slicers_cache[cache_key] = slicers
        return slicers

    @staticmethod
    def _resolve_field_param_column(table: str, prop: str) -> str:
        """
        Resolve the actual DAX column name for a field parameter slicer projection.

        Power BI field parameter tables store selections in a hidden 'Fields' column
        (e.g., 'sf Filter 1'[sf Filter 1 Fields]).  The visual.json projection records
        the *display* column whose Property name matches the table name.  When we detect
        this pattern we append ' Fields' to get the real TREATAS target column.

        Rule:  if is_field_parameter AND property == table_name  →  use '{property} Fields'
               otherwise keep property as-is  (e.g., 'Category' stays 'Category')
        """
        from .filter_to_dax import is_field_parameter_table

        if is_field_parameter_table(table) and prop == table:
            return f"{prop} Fields"
        return prop

    @staticmethod
    def _slicer_selection_inverted(
        data: Dict[str, Any],
        visual: Dict[str, Any],
        selection_filter: Optional[Dict[str, Any]],
        table: str,
    ) -> bool:
        """Is the slicer's persisted selection an EXCLUDE list? (F-04a)

        True when the selection itself is ``Not(In ...)``, when the visual-level
        ``visual.objects.data[].properties.isInvertedSelectionMode`` is set, or
        when a ``filterConfig`` entry on the slicer's table that carries NO
        condition of its own is flagged ``isInvertedSelectionMode: true`` -- the
        R0105/R0201 shape where the flag governs the ``objects.general`` payload.
        A filterConfig entry WITH its own condition describes that condition only
        (e.g. hiding options from the slicer), not the selection.
        """
        from core.pbip.pbir_model import filter_polarity, is_inverted_selection

        if selection_filter:
            if filter_polarity(selection_filter, data) == "exclude":
                return True
        vobjs = visual.get("objects") or {}
        for section in ("data", "general", "selection"):
            for entry in vobjs.get(section) or []:
                props = entry.get("properties") if isinstance(entry, dict) else None
                flag = ((props or {}).get("isInvertedSelectionMode") or {}).get("expr", {})
                lit = (flag.get("Literal") or {}).get("Value") if isinstance(flag, dict) else None
                if isinstance(lit, str) and lit.strip().lower() == "true":
                    return True
        fc = data.get("filterConfig") if isinstance(data, dict) else None
        for entry in (fc or {}).get("filters") or []:
            if not isinstance(entry, dict) or entry.get("filter"):
                continue
            fld = entry.get("field") or {}
            node = fld.get("Column") or fld.get("Measure") or {}
            entity = ((node.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
            if entity and table and entity != table:
                continue
            own = ((entry.get("objects") or {}).get("general")) or []
            for g in own:
                props = g.get("properties") if isinstance(g, dict) else None
                if props and "isInvertedSelectionMode" in props:
                    if is_inverted_selection(entry):
                        return True
        return False

    def _parse_slicer_state(
        self, data: Dict, visual: Dict, visual_id: str, page_name: str
    ) -> Optional[SlicerState]:
        """Parse slicer state from visual JSON.

        Handles both single-column slicers (regular and single-field-param like sf Filter 1)
        and multi-column composite field parameter slicers (sf Row Drill, sf Slicer 1) that
        require composite-key TREATAS with tuple values.
        """
        try:
            from .filter_to_dax import is_field_parameter_table  # noqa: F401

            # Get field reference(s)
            query = visual.get("query", {})
            query_state = query.get("queryState", {})
            values_section = query_state.get("Values", {})
            projections = values_section.get("projections", [])

            if not projections:
                return None

            # Parse ALL projections (field param slicers can have 2: Category + Fields).
            # Skip projections marked inactive — hierarchical slicers include dormant
            # drill-down levels with "active": false that are not part of the current filter.
            proj_columns: List[Dict] = []  # [{table, column}]
            for proj in projections:
                if proj.get("active") is False:
                    continue
                field = proj.get("field", {})
                col_ref = field.get("Column", {})
                if not col_ref:
                    continue
                expr = col_ref.get("Expression", {})
                source_ref = expr.get("SourceRef", {})
                t = source_ref.get("Entity", "")
                p = col_ref.get("Property", "")
                if t and p:
                    # Resolve to actual DAX column (handles 'sf Filter 1' → 'sf Filter 1 Fields')
                    resolved_p = self._resolve_field_param_column(t, p)
                    proj_columns.append({"table": t, "column": resolved_p})

            if not proj_columns:
                return None

            table = proj_columns[0]["table"]
            column = proj_columns[0]["column"]

            # Get selection state
            objects = visual.get("objects", {})

            # Selection mode
            selection_config = objects.get("selection", [{}])
            selection_props = selection_config[0].get("properties", {}) if selection_config else {}
            single_select = (
                selection_props.get("singleSelect", {})
                .get("expr", {})
                .get("Literal", {})
                .get("Value", "false")
                == "true"
                or selection_props.get("strictSingleSelect", {})
                .get("expr", {})
                .get("Literal", {})
                .get("Value", "false")
                == "true"
            )

            # Inverted selection (F-04a) -- resolved further down once the
            # selection filter and its columns are known.
            is_inverted = False

            # Current selections — parse as tuples to support multi-column slicers
            general_config = objects.get("general", [{}])
            general_props = general_config[0].get("properties", {}) if general_config else {}
            current_filter = general_props.get("filter", {}).get("filter", {})

            # Determine the authoritative column list from the current filter's
            # In.Expressions — Power BI hierarchical slicers can have multiple active
            # projections but filter on only a subset (e.g., parent "JAN 26" selected
            # without a child date). Trusting projections alone would produce a
            # composite TREATAS with more columns than the filter's value tuples,
            # causing a "column count mismatch" error in the DAX engine.
            filter_columns: List[Dict] = []  # [{table, column}] parsed from filter Expressions
            if current_filter:
                where_clause = current_filter.get("Where", [])
                if where_clause:
                    first_cond = where_clause[0].get("Condition", {}) or {}
                    first_in = (
                        first_cond.get("In")
                        or (((first_cond.get("Not") or {}).get("Expression") or {}).get("In"))
                        or {}
                    )
                    for expr_item in first_in.get("Expressions", []):
                        col_ref = expr_item.get("Column", {})
                        if not col_ref:
                            continue
                        inner_expr = col_ref.get("Expression", {})
                        source_ref = inner_expr.get("SourceRef", {})
                        # Entity may live on SourceRef directly, or Source alias may point
                        # at the filter's "From" clause — resolve via From when needed.
                        ent = source_ref.get("Entity", "")
                        if not ent:
                            alias = source_ref.get("Source", "")
                            for from_item in current_filter.get("From", []):
                                if from_item.get("Name") == alias:
                                    ent = from_item.get("Entity", "")
                                    break
                        prop = col_ref.get("Property", "")
                        if ent and prop:
                            filter_columns.append(
                                {
                                    "table": ent,
                                    "column": self._resolve_field_param_column(ent, prop),
                                }
                            )

            # Use filter-declared columns when present; they're the ground truth. If the
            # filter is empty (no current selection) fall back to projections.
            effective_columns = filter_columns if filter_columns else proj_columns
            if not effective_columns:
                return None

            table = effective_columns[0]["table"]
            column = effective_columns[0]["column"]
            is_multi_col = len(effective_columns) > 1
            selected_values: List[Any] = []
            composite_values: Optional[List[List[Any]]] = [] if is_multi_col else None

            if current_filter:
                where_clause = current_filter.get("Where", [])
                for condition in where_clause:
                    cond = condition.get("Condition", {}) or {}
                    in_clause = cond.get("In")
                    if in_clause is None and "Not" in cond:
                        # "Select all except ...": Not(In) = an EXCLUDE list.
                        in_clause = ((cond.get("Not") or {}).get("Expression") or {}).get("In")
                    in_clause = in_clause or {}
                    values_list = in_clause.get("Values", [])
                    for value_group in values_list:
                        if is_multi_col:
                            # Each value_group is a tuple: one literal per filter column
                            tuple_row = []
                            for value_item in value_group:
                                literal = value_item.get("Literal", {})
                                tuple_row.append(literal.get("Value", ""))
                            # Guard against arity mismatch — a malformed filter with fewer
                            # literals than columns would produce an invalid TREATAS.
                            if len(tuple_row) != len(effective_columns):
                                self.logger.debug(
                                    f"Slicer {visual_id}: tuple arity {len(tuple_row)} != "
                                    f"column count {len(effective_columns)}; skipping row"
                                )
                                continue
                            composite_values.append(tuple_row)
                            # Also store just the first column's value in selected_values (for display)
                            if tuple_row:
                                selected_values.append(tuple_row[0])
                        else:
                            # Single-column: keep flat list of literal values
                            for value_item in value_group:
                                literal = value_item.get("Literal", {})
                                val = literal.get("Value", "")
                                selected_values.append(val)
            else:
                self.logger.debug(f"Slicer {visual_id} ({table}.{column}): No current_filter found")

            is_inverted = self._slicer_selection_inverted(data, visual, current_filter, table)

            # Determine selection mode
            if is_inverted and single_select:
                selection_mode = "single_select_all"
            elif single_select:
                selection_mode = "single_select"
            else:
                selection_mode = "multi_select"

            # Extra columns for composite TREATAS (second filter column onwards)
            extra_columns: List[str] = []
            if is_multi_col:
                for pc in effective_columns[1:]:
                    extra_columns.append(f"'{pc['table']}'[{pc['column']}]")

            self.logger.debug(
                f"Slicer {visual_id} ({table}.{column}): "
                f"{len(selected_values)} values, mode={selection_mode}, "
                f"multi_col={is_multi_col}, extra_cols={extra_columns}"
            )

            return SlicerState(
                slicer_id=visual_id,
                page_name=page_name,
                table=table,
                column=column,
                field_reference=f"'{table}'[{column}]",
                selected_values=selected_values,
                selection_mode=selection_mode,
                is_inverted=is_inverted,
                extra_columns=extra_columns,
                composite_values=composite_values if composite_values else None,
            )

        except Exception as e:
            self.logger.debug(f"Error parsing slicer state: {e}")
            return None
