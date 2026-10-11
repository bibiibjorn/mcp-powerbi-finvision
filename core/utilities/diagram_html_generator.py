"""
Professional HTML generator for dependency diagrams.
Creates interactive, beautifully styled Mermaid diagrams that open in browser.
Supports sidebar with ALL measures/columns for instant switching.

Scope (audit T119): this module produces an INTERACTIVE Mermaid-based
dependency explorer for the LIVE TOM model. For OFFLINE PBIP
project-level dependency tables (no graph), use
``core/utilities/pbip_dependency_html_generator.py``. The two are
intentionally separate generators with different output shapes; do
not unify without redesigning the front-end.

The HTML/CSS/JS template lives in ``static/diagram_explorer.html``.
This module loads the template at call time and substitutes
``__PLACEHOLDER__`` tokens with computed Python values.
"""

import html
import os
import logging
import pathlib
import webbrowser
from datetime import datetime
from typing import Dict, Any, Optional, List

from core.utilities.pbip_dependency_html_generator import fill_template, script_json

logger = logging.getLogger(__name__)

# Path to the static HTML template (resolved at call time, not import time, so
# tests / packagers that move the file around still work).
_TEMPLATE_PATH = pathlib.Path(__file__).parent / "static" / "diagram_explorer.html"


def generate_dependency_html(
    mermaid_code: str,
    measure_table: str,
    measure_name: str,
    metadata: Dict[str, Any] = None,
    auto_open: bool = True,
    referenced_measures: list = None,
    referenced_columns: list = None,
    used_by_measures: list = None,
    # NEW: Sidebar data for all items
    all_measures: List[Dict[str, Any]] = None,
    all_columns: List[Dict[str, Any]] = None,
    all_dependencies: Dict[str, Any] = None,
    main_item: str = None,
) -> Optional[str]:
    """
    Generate a professional HTML page with interactive Mermaid diagram.

    Features a left sidebar with ALL measures and columns from the model.
    Click any item in the sidebar to view its dependencies.

    Args:
        mermaid_code: The Mermaid diagram code
        measure_table: Table containing the measure
        measure_name: Name of the measure
        metadata: Additional metadata (direction, depth, node_count, edge_count)
        auto_open: Whether to open the HTML in browser
        referenced_measures: List of (table, measure) tuples this measure depends on
        referenced_columns: List of (table, column) tuples this measure uses
        used_by_measures: List of {'table': ..., 'measure': ...} dicts of measures using this one
        all_measures: List of ALL measures in the model for sidebar
        all_columns: List of ALL columns in the model for sidebar
        all_dependencies: Pre-computed dependencies for all items (for instant switching)
        main_item: Currently selected item key (e.g., 'Table[Measure]')
    """
    if not mermaid_code:
        return None

    metadata = metadata or {}
    direction = metadata.get("direction", "bidirectional")
    depth = metadata.get("depth", 3)
    node_count = metadata.get("node_count", 0)
    edge_count = metadata.get("edge_count", 0)

    # Normalize data structures
    referenced_measures = referenced_measures or []
    referenced_columns = referenced_columns or []
    used_by_measures = used_by_measures or []

    # Helper to create Mermaid-compatible node IDs (shared with dependency_analyzer.py)
    from core.utilities.mermaid_utils import sanitize_node_id

    # Extract ALL node IDs from the mermaid code (includes transitive dependencies)
    # This ensures we catch all levels of dependencies, not just direct ones
    upstream_node_ids: List[str] = []
    downstream_node_ids: List[str] = []
    root_node_id = sanitize_node_id(f"{measure_table}[{measure_name}]")

    # Parse mermaid code to find all nodes in Dependencies vs Dependents subgraphs
    in_upstream_section = False
    in_downstream_section = False

    for line in mermaid_code.split("\n"):
        line_stripped = line.strip()

        # Track which subgraph we're in
        if "subgraph Dependencies" in line:
            in_upstream_section = True
            in_downstream_section = False
        elif "subgraph Dependents" in line:
            in_downstream_section = True
            in_upstream_section = False
        elif line_stripped == "end":
            in_upstream_section = False
            in_downstream_section = False

        # Extract node IDs from node definitions (lines with [...] or ["..."])
        if "[" in line and (":::" in line or "]" in line):
            parts = line_stripped.split("[")
            if parts:
                node_id = parts[0].strip()
                if node_id and node_id != "subgraph":
                    if in_upstream_section:
                        upstream_node_ids.append(node_id)
                    elif in_downstream_section:
                        downstream_node_ids.append(node_id)

    # Also add direct dependencies as fallback
    for tbl, name in referenced_measures:
        node_key = f"{tbl}[{name}]"
        node_id = sanitize_node_id(node_key)
        if node_id not in upstream_node_ids:
            upstream_node_ids.append(node_id)
    for tbl, name in referenced_columns:
        node_key = f"{tbl}[{name}]"
        node_id = sanitize_node_id(node_key)
        if node_id not in upstream_node_ids:
            upstream_node_ids.append(node_id)

    for item in used_by_measures:
        tbl = item.get("table", "")
        name = item.get("measure", "")
        if tbl and name:
            node_key = f"{tbl}[{name}]"
            node_id = sanitize_node_id(node_key)
            if node_id not in downstream_node_ids:
                downstream_node_ids.append(node_id)

    # Generate JSON data for JavaScript
    ref_measures_json = script_json([{"table": t, "name": m} for t, m in referenced_measures])
    ref_columns_json = script_json([{"table": t, "name": c} for t, c in referenced_columns])
    used_by_json = script_json(used_by_measures)
    upstream_ids_json = script_json(upstream_node_ids)
    downstream_ids_json = script_json(downstream_node_ids)
    root_id_json = script_json(root_node_id)

    # Process sidebar data (NEW)
    all_measures = all_measures or []
    all_columns = all_columns or []
    all_dependencies = all_dependencies or {}
    has_sidebar = bool(all_measures or all_columns or all_dependencies)

    # Build grouped lists for sidebar
    measures_by_table: Dict[str, List[Dict[str, str]]] = {}
    columns_by_table: Dict[str, List[Dict[str, str]]] = {}

    for m in all_measures:
        table = m.get("table", "Unknown")
        name = m.get("name", "")
        if table and name:
            measures_by_table.setdefault(table, []).append(
                {
                    "name": name,
                    "key": f"{table}[{name}]",
                }
            )

    for c in all_columns:
        table = c.get("table", "Unknown")
        name = c.get("name", "")
        if table and name:
            columns_by_table.setdefault(table, []).append(
                {
                    "name": name,
                    "key": f"{table}[{name}]",
                }
            )

    # Sort measures and columns within each table
    for table in measures_by_table:
        measures_by_table[table].sort(key=lambda x: x["name"])
    for table in columns_by_table:
        columns_by_table[table].sort(key=lambda x: x["name"])

    # Count totals for sidebar
    total_sidebar_measures = sum(len(m) for m in measures_by_table.values())
    total_sidebar_columns = sum(len(c) for c in columns_by_table.values())

    # Prepare sidebar JSON data
    measures_by_table_json = script_json(measures_by_table)
    columns_by_table_json = script_json(columns_by_table)
    all_dependencies_json = script_json(all_dependencies)
    main_item_key = main_item or f"{measure_table}[{measure_name}]"
    main_item_json = script_json(main_item_key)

    # Build node metadata map for info tooltips (expression for measures, dataType for columns)
    node_metadata: Dict[str, Dict[str, Any]] = {}
    for m in all_measures:
        m_key = f"{m.get('table', '')}[{m.get('name', '')}]"
        node_metadata[m_key] = {
            "type": "measure",
            "table": m.get("table", ""),
            "name": m.get("name", ""),
            "expression": m.get("expression", ""),
        }
    for c in all_columns:
        c_key = f"{c.get('table', '')}[{c.get('name', '')}]"
        node_metadata[c_key] = {
            "type": "column",
            "table": c.get("table", ""),
            "name": c.get("name", ""),
            "dataType": c.get("dataType", "Unknown"),
            "isHidden": c.get("isHidden", False),
            "isKey": c.get("isKey", False),
            "columnType": c.get("columnType", "Data"),
        }
    node_metadata_json = script_json(node_metadata)

    # Debug logging
    logger.info(f"Root node ID: {root_node_id}")
    logger.info(f"Upstream node IDs ({len(upstream_node_ids)}): {upstream_node_ids[:10]}...")
    logger.info(f"Downstream node IDs ({len(downstream_node_ids)}): {downstream_node_ids[:10]}...")

    timestamp = datetime.now().strftime("%B %d, %Y at %H:%M")

    # Compute placeholder values once.
    sidebar_display = "flex" if has_sidebar else "none"
    upstream_len = str(len(referenced_measures) + len(referenced_columns))
    downstream_len = str(len(used_by_measures))

    # Load and substitute the static template in ONE pass (``fill_template``): a value that
    # itself contains a ``__TOKEN__`` is never substituted again (C-06). Values are escaped for
    # their context: ``html.escape`` for text, ``script_json`` for JS literals.
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    substitutions = {
        "__MEASURE_NAME__": html.escape(str(measure_name)),
        "__MEASURE_TABLE__": html.escape(str(measure_table)),
        "__DOWNLOAD_NAME_JSON__": script_json(f"{measure_table}_{measure_name}_dependencies.svg"),
        "__SIDEBAR_DISPLAY__": sidebar_display,
        "__TOTAL_SIDEBAR_MEASURES__": str(total_sidebar_measures),
        "__TOTAL_SIDEBAR_COLUMNS__": str(total_sidebar_columns),
        "__TIMESTAMP__": timestamp,
        "__DIRECTION__": html.escape(str(direction)),
        "__DEPTH__": html.escape(str(depth)),
        "__UPSTREAM_LEN__": upstream_len,
        "__DOWNSTREAM_LEN__": downstream_len,
        "__NODE_COUNT__": str(node_count),
        "__EDGE_COUNT__": str(edge_count),
        # The <pre class="mermaid"> content is read back through innerHTML and entity-decoded by
        # Mermaid, so escaping &, < and > is lossless and keeps a name from closing the element.
        "__MERMAID_CODE__": html.escape(mermaid_code, quote=False),
        "__UPSTREAM_IDS_JSON__": upstream_ids_json,
        "__DOWNSTREAM_IDS_JSON__": downstream_ids_json,
        "__ROOT_ID_JSON__": root_id_json,
        "__MAIN_ITEM_JSON__": main_item_json,
        "__REF_MEASURES_JSON__": ref_measures_json,
        "__REF_COLUMNS_JSON__": ref_columns_json,
        "__USED_BY_JSON__": used_by_json,
        "__MEASURES_BY_TABLE_JSON__": measures_by_table_json,
        "__COLUMNS_BY_TABLE_JSON__": columns_by_table_json,
        "__ALL_DEPENDENCIES_JSON__": all_dependencies_json,
        "__NODE_METADATA_JSON__": node_metadata_json,
    }

    html_content = fill_template(template, substitutions)

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        exports_dir = os.path.join(os.path.dirname(os.path.dirname(script_dir)), "exports")
        os.makedirs(exports_dir, exist_ok=True)

        html_path = os.path.join(exports_dir, "dependency_diagram.html")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info(f"Generated dependency diagram HTML: {html_path}")

        if auto_open:
            webbrowser.open(f'file:///{html_path.replace(os.sep, "/")}')

        return html_path

    except Exception as e:
        logger.error(f"Failed to generate HTML diagram: {e}")
        return None
