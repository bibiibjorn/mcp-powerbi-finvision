"""
PBIP Dependency HTML Generator
Creates a clean, professional dependency analysis page for PBIP projects.
Displays dependencies in organized tables and lists without complex graph visualizations.

Scope (audit T119): this module produces a TABLE-BASED dependency report
from OFFLINE PBIP files. For an INTERACTIVE Mermaid graph from the
LIVE TOM model, use ``core/utilities/diagram_html_generator.py``. The
two are intentionally separate generators with different output shapes.
"""

import html
import os
import logging
import pathlib
import re
import webbrowser
import json
from datetime import datetime
from typing import Dict, Any, List, Optional, Set, Tuple

_STATIC = pathlib.Path(__file__).parent / "static" / "pbip_dependency.html"

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z_]*__")


def script_json(value: Any) -> str:
    """JSON safe to embed inside ``<script>``: no ``</`` (ends the element) and
    no ``<!--`` (HTML comment state). 2026-09-22 (I-19)."""
    return json.dumps(value).replace("</", "<\\/").replace("<!--", "<\\u0021--")


def fill_template(template: str, replacements: Dict[str, str]) -> str:
    """Single-pass placeholder substitution: a VALUE that happens to contain
    ``__SUMMARY_JSON__`` is never substituted again (the old sequential
    ``str.replace`` loop did)."""
    return _PLACEHOLDER_RE.sub(lambda m: replacements.get(m.group(0), m.group(0)), template)


def generate_pbip_dependency_html(
    dependency_data: Dict[str, Any],
    model_name: str = "Power BI Model",
    auto_open: bool = False,
    output_path: Optional[str] = None,
    main_item: Optional[str] = None,
    main_item_type: Optional[str] = None,
) -> Optional[str]:
    """
    Generate a professional HTML page with PBIP dependency analysis.

    Features a left sidebar with all measures, columns, and field parameters.
    Clicking any item in the sidebar shows its dependencies in a clean table format.

    Args:
        dependency_data: Output from PbipDependencyEngine.analyze_all_dependencies()
        model_name: Name of the model for display
        auto_open: Whether to open the HTML in browser (default False: agent use)
        output_path: Optional custom output path (callers path-guard it)
        main_item: Optional specific item to select initially (e.g., "Table[Measure]")
        main_item_type: Type of main_item ("measure", "column", "field_parameter")

    Returns:
        Path to generated HTML file or None if failed
    """
    if not dependency_data:
        logger.error("No dependency data provided")
        return None

    # Extract data from dependency engine output
    measure_to_measure = dependency_data.get("measure_to_measure", {})
    measure_to_measure_reverse = dependency_data.get("measure_to_measure_reverse", {})
    measure_to_column = dependency_data.get("measure_to_column", {})
    column_to_measure = dependency_data.get("column_to_measure", {})
    column_to_field_params = dependency_data.get("column_to_field_params", {})
    visual_dependencies = dependency_data.get("visual_dependencies", {})
    filter_pane_data = dependency_data.get("filter_pane_data", {})
    summary = dependency_data.get("summary", {})

    # Build comprehensive node lists
    all_measures: Set[str] = set()
    all_columns: Set[str] = set()
    all_visuals: Set[str] = set()
    all_field_params: Set[str] = set()

    # Collect all measures
    for measure_key in measure_to_measure.keys():
        all_measures.add(measure_key)
    for measure_key in measure_to_measure_reverse.keys():
        all_measures.add(measure_key)
    for measure_key in measure_to_column.keys():
        all_measures.add(measure_key)
    for measure_list in column_to_measure.values():
        all_measures.update(measure_list)

    # Collect all columns
    for column_key in column_to_measure.keys():
        all_columns.add(column_key)
    for column_list in measure_to_column.values():
        all_columns.update(column_list)
    for column_key in column_to_field_params.keys():
        all_columns.add(column_key)

    # Collect visuals
    for visual_key in visual_dependencies.keys():
        all_visuals.add(visual_key)

    # Collect field parameters
    for fp_list in column_to_field_params.values():
        all_field_params.update(fp_list)

    # Reverse index item -> visuals, built once (I-29: the old loops scanned
    # every visual once per measure and once per column).
    visuals_by_measure: Dict[str, List[Dict[str, Any]]] = {}
    visuals_by_column: Dict[str, List[Dict[str, Any]]] = {}
    for visual_key, visual_deps in visual_dependencies.items():
        entry = {
            "key": visual_key,
            "page": visual_deps.get("page", ""),
            "visual_type": visual_deps.get("visual_type", ""),
        }
        for mk in set(visual_deps.get("measures", []) or []):
            visuals_by_measure.setdefault(mk, []).append(entry)
        for ck in set(visual_deps.get("columns", []) or []):
            visuals_by_column.setdefault(ck, []).append(entry)

    # Pre-compute dependencies for EACH item
    item_dependencies = {}

    def extract_table_and_name(key: str) -> Tuple[str, str]:
        """Extract table and name from a key in Table[Name] format."""
        if "[" in key and "]" in key:
            bracket_idx = key.index("[")
            table = key[:bracket_idx].strip()
            name = key[bracket_idx + 1 :].rstrip("]").strip()
            if not table and name:
                return ("Unresolved", name)
            return (table if table else "Unresolved", name if name else key)
        return ("Unresolved", key)

    # Build dependencies for each MEASURE
    for measure_key in sorted(all_measures):
        table, name = extract_table_and_name(measure_key)

        # Upstream: what this measure depends on (columns and measures)
        upstream_measures = measure_to_measure.get(measure_key, [])
        upstream_columns = measure_to_column.get(measure_key, [])

        # Downstream: what depends on this measure
        downstream_measures = measure_to_measure_reverse.get(measure_key, [])
        downstream_visuals = visuals_by_measure.get(measure_key, [])

        item_dependencies[measure_key] = {
            "key": measure_key,
            "type": "measure",
            "table": table,
            "name": name,
            "upstream": {"measures": upstream_measures, "columns": upstream_columns},
            "downstream": {"measures": downstream_measures, "visuals": downstream_visuals},
        }

    # Build dependencies for each COLUMN
    for column_key in sorted(all_columns):
        table, name = extract_table_and_name(column_key)

        # Downstream: what uses this column
        downstream_measures = column_to_measure.get(column_key, [])
        downstream_field_params = column_to_field_params.get(column_key, [])
        downstream_visuals = visuals_by_column.get(column_key, [])

        item_dependencies[column_key] = {
            "key": column_key,
            "type": "column",
            "table": table,
            "name": name,
            "upstream": {"measures": [], "columns": []},
            "downstream": {
                "measures": downstream_measures,
                "visuals": downstream_visuals,
                "fieldParams": downstream_field_params,
            },
        }

    # Build dependencies for each FIELD PARAMETER
    for fp_name in sorted(all_field_params):
        # Upstream: columns referenced by this field parameter
        upstream_columns = []
        for column_key, fp_list in column_to_field_params.items():
            if fp_name in fp_list:
                upstream_columns.append(column_key)

        item_dependencies[fp_name] = {
            "key": fp_name,
            "type": "field_parameter",
            "table": fp_name,
            "name": fp_name,
            "upstream": {"measures": [], "columns": upstream_columns},
            "downstream": {"measures": [], "visuals": []},
        }

    # Build grouped data for sidebar
    measures_by_table = {}
    for measure_key in sorted(all_measures):
        table, name = extract_table_and_name(measure_key)
        if table not in measures_by_table:
            measures_by_table[table] = []
        measures_by_table[table].append({"name": name, "key": measure_key})

    columns_by_table = {}
    for column_key in sorted(all_columns):
        table, name = extract_table_and_name(column_key)
        if table not in columns_by_table:
            columns_by_table[table] = []
        columns_by_table[table].append({"name": name, "key": column_key})

    field_params_list = []
    for fp_name in sorted(all_field_params):
        field_params_list.append({"name": fp_name, "key": fp_name})

    # Determine initial selection
    initial_item = None
    if main_item and main_item in item_dependencies:
        initial_item = main_item
    elif all_measures:
        initial_item = sorted(all_measures)[0]
    elif all_columns:
        initial_item = sorted(all_columns)[0]
    elif all_field_params:
        initial_item = sorted(all_field_params)[0]

    # Prepare JSON data for JavaScript (script-safe: I-19)
    item_dependencies_json = script_json(item_dependencies)
    measures_by_table_json = script_json(measures_by_table)
    columns_by_table_json = script_json(columns_by_table)
    field_params_json = script_json(field_params_list)
    filter_pane_json = script_json(filter_pane_data)
    initial_item_json = script_json(initial_item)
    summary_json = script_json(summary)

    timestamp = datetime.now().strftime("%B %d, %Y at %H:%M")

    html_content = _STATIC.read_text(encoding="utf-8")
    replacements = {
        "__MODEL_NAME__": html.escape(str(model_name)),
        "__TIMESTAMP__": timestamp,
        "__LEN_MEASURES__": str(len(all_measures)),
        "__LEN_COLUMNS__": str(len(all_columns)),
        "__LEN_FIELD_PARAMS__": str(len(all_field_params)),
        "__MEASURES_BY_TABLE_JSON__": measures_by_table_json,
        "__COLUMNS_BY_TABLE_JSON__": columns_by_table_json,
        "__FIELD_PARAMS_JSON__": field_params_json,
        "__ITEM_DEPENDENCIES_JSON__": item_dependencies_json,
        "__FILTER_PANE_JSON__": filter_pane_json,
        "__INITIAL_ITEM_JSON__": initial_item_json,
        "__SUMMARY_JSON__": summary_json,
    }
    html_content = fill_template(html_content, replacements)

    try:
        # Determine output path
        if output_path:
            html_path = output_path
            os.makedirs(os.path.dirname(os.path.abspath(html_path)), exist_ok=True)
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            exports_dir = os.path.join(os.path.dirname(os.path.dirname(script_dir)), "exports")
            os.makedirs(exports_dir, exist_ok=True)
            html_path = os.path.join(exports_dir, "pbip_dependency_diagram.html")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info(f"Generated PBIP dependency analysis HTML: {html_path}")

        if auto_open:
            webbrowser.open(f'file:///{html_path.replace(os.sep, "/")}')

        return html_path

    except Exception as e:
        logger.error(f"Failed to generate PBIP dependency HTML: {e}")
        return None
