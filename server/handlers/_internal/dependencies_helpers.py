"""
INTERNAL HELPER — Not a registered MCP tool.
Provides helper functions consumed by active handlers.

Handles measure dependency analysis; consumed by dax_context_handler.py (05_DAX_Intelligence).
"""

from typing import Dict, Any, List, Optional
import logging
import threading
from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from core.utilities.diagram_html_generator import generate_dependency_html
from core.dax.dax_reference_parser import normalize_dax_name
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

# Sidebar data for the (opt-in) HTML Model Browser, cached in memory per model
# state (J-12): key = connection identity + model generation, so any write or
# model switch recomputes it, and a repeat call is free.
_sidebar_cache: Dict[str, Any] = {"key": None, "data": None}
_sidebar_cache_lock = threading.Lock()


def _model_state_key() -> Any:
    try:
        ident = connection_state.get_connection_identity() or {}
    except Exception:  # noqa: BLE001
        ident = {}
    return (
        ident.get("port"),
        ident.get("database"),
        ident.get("connection_generation"),
        ident.get("model_generation", getattr(connection_state, "model_generation", None)),
    )


def _info_rows(query_executor, view: str) -> Optional[List[Dict[str, Any]]]:
    try:
        res = query_executor.execute_info_query(view, top_n=100000)
    except TypeError:
        res = query_executor.execute_info_query(view)
    except Exception as e:  # noqa: BLE001
        logger.debug("INFO.%s failed: %s", view, e)
        return None
    if not isinstance(res, dict) or not res.get("success"):
        return None
    rows = res.get("rows")
    return rows if isinstance(rows, list) else None


def _compute_sidebar_data(query_executor, measures_rows=None) -> dict:
    """ALL measures + columns + their dependencies from ONE INFO.MEASURES and ONE
    INFO.COLUMNS query, parsed once each with the lexer-based reference scanner.

    J-12: the old path called ``analyze_dependencies`` + ``find_measure_usage``
    for every measure (each running its own INFO.MEASURES query): ~2N DAX
    round-trips and O(N^2) parsing to render one measure's diagram.
    """
    from core.graph.builder import build_model_graph
    from core.graph.model_source import ModelSnapshot

    mrows = (
        measures_rows
        if measures_rows is not None
        else (_info_rows(query_executor, "MEASURES") or [])
    )
    crows = _info_rows(query_executor, "COLUMNS") or []
    snap = ModelSnapshot(source="info_query")
    all_measures: List[Dict[str, Any]] = []
    all_columns: List[Dict[str, Any]] = []
    for m in mrows:
        t, n = m.get("Table") or "", m.get("Name") or ""
        if not t or not n:
            continue
        expr = m.get("Expression") or ""
        all_measures.append({"table": t, "name": n, "expression": expr})
        snap.measures.append({"table": t, "name": n, "expression": expr})
        snap.tables.setdefault(t, {})
    for c in crows:
        t = c.get("Table") or ""
        n = c.get("Name") or c.get("ExplicitName") or c.get("InferredName") or ""
        try:
            ctype = int(c.get("Type") or 1)
        except (TypeError, ValueError):
            ctype = 1
        # J-12: INFO.COLUMNS [Type] 3 = RowNumber (skip), 2 = Calculated (keep).
        if not t or not n or ctype == 3:
            continue
        all_columns.append(
            {
                "table": t,
                "name": n,
                "dataType": c.get("DataType") or c.get("ExplicitDataType") or "",
                "isHidden": c.get("IsHidden", False),
                "isKey": c.get("IsKey", False),
                "columnType": "Calculated" if ctype == 2 else "Data",
            }
        )
        snap.columns.append(
            {
                "table": t,
                "name": n,
                "type": "calculated" if ctype == 2 else "data",
                "expression": c.get("Expression") if ctype == 2 else None,
            }
        )
        snap.tables.setdefault(t, {})
    g = build_model_graph(snap)

    def _key(node_id: str) -> str:
        return node_id.split(":", 1)[1]

    all_dependencies: Dict[str, Any] = {}
    for m in all_measures:
        nid = f"measure:{m['table']}[{m['name']}]"
        out_edges = [e for e in g.outgoing(nid) if e.kind == "references"]
        in_edges = [e for e in g.incoming(nid) if e.kind == "references"]
        all_dependencies[_key(nid)] = {
            "key": _key(nid),
            "type": "measure",
            "table": m["table"],
            "name": m["name"],
            "upstream": {
                "measures": sorted(
                    {_key(e.dst) for e in out_edges if e.dst.startswith("measure:")}
                ),
                "columns": sorted({_key(e.dst) for e in out_edges if e.dst.startswith("column:")}),
            },
            "downstream": {
                "measures": sorted({_key(e.src) for e in in_edges if e.src.startswith("measure:")}),
                "visuals": [],
            },
        }
    for c in all_columns:
        nid = f"column:{c['table']}[{c['name']}]"
        if _key(nid) in all_dependencies:
            continue
        users = sorted(
            {
                _key(e.src)
                for e in g.incoming(nid)
                if e.kind == "references" and e.src.startswith("measure:")
            }
        )
        all_dependencies[_key(nid)] = {
            "key": _key(nid),
            "type": "column",
            "table": c["table"],
            "name": c["name"],
            "upstream": {"measures": [], "columns": []},
            "downstream": {"measures": users, "visuals": []},
        }
    return {
        "all_measures": all_measures,
        "all_columns": all_columns,
        "all_dependencies": all_dependencies,
    }


def _load_sidebar_data(force_refresh: bool = False, measures_rows=None) -> dict:
    """Sidebar data for the HTML Model Browser (all measures / columns / deps).

    Reused from memory while the model state (connection + model generation)
    is unchanged; ``force_refresh`` recomputes. (J-12: the default used to be
    ``force_refresh=True``, which deleted the disk cache and recomputed every
    dependency on every call.)
    """
    key = _model_state_key()
    with _sidebar_cache_lock:
        if not force_refresh and _sidebar_cache.get("key") == key and _sidebar_cache.get("data"):
            logger.debug("Using in-memory sidebar cache")
            return dict(_sidebar_cache["data"])

    query_executor = connection_state.query_executor
    if not query_executor:
        logger.warning("Query executor not available for sidebar data")
        return {"all_measures": [], "all_columns": [], "all_dependencies": {}}

    data = _compute_sidebar_data(query_executor, measures_rows=measures_rows)
    with _sidebar_cache_lock:
        _sidebar_cache["key"] = key
        _sidebar_cache["data"] = data
    logger.info(
        "Computed sidebar data: %d measures, %d columns, %d dependencies",
        len(data["all_measures"]),
        len(data["all_columns"]),
        len(data["all_dependencies"]),
    )
    return dict(data)


def _used_by_from_rows(
    rows: List[Dict[str, Any]], table: str, measure: str
) -> List[Dict[str, Any]]:
    """Measures whose expression references [measure] (lexer-based, one pass)."""
    from core.dax.dax_reference_parser import scan_references

    target = normalize_dax_name(measure)
    out: List[Dict[str, Any]] = []
    for m in rows:
        m_table, m_name = m.get("Table", "") or "", m.get("Name", "") or ""
        if m_table == table and m_name == measure:
            continue
        for r in scan_references(m.get("Expression") or ""):
            if r.kind == "bracket" or (
                r.kind == "qualified" and normalize_dax_name(r.table) == normalize_dax_name(table)
            ):
                if normalize_dax_name(r.name) == target:
                    out.append(
                        {
                            "table": m_table,
                            "measure": m_name,
                            "display_folder": m.get("DisplayFolder", "") or "",
                        }
                    )
                    break
    return out


def _format_dependency_analysis_output(
    table: str, measure: str, deps_result: Dict[str, Any], diagram_result: Dict[str, Any] = None
) -> str:
    """
    Format dependency analysis into a professional, readable output.

    Returns a formatted string with clear sections for:
    - Header with measure identification
    - Summary statistics
    - Dependencies breakdown by category
    - Mermaid diagram (if available)
    """
    lines = []

    # ═══════════════════════════════════════════════════════════════════════════
    # HEADER
    # ═══════════════════════════════════════════════════════════════════════════
    lines.append("═" * 80)
    lines.append("  MEASURE DEPENDENCY ANALYSIS")
    lines.append("═" * 80)
    lines.append("")
    lines.append(f"  Measure: {table}[{measure}]")
    lines.append("")

    # ───────────────────────────────────────────────────────────────────────────
    # SUMMARY STATISTICS
    # ───────────────────────────────────────────────────────────────────────────
    ref_measures = deps_result.get("referenced_measures", [])
    ref_columns = deps_result.get("referenced_columns", [])
    ref_tables = deps_result.get("referenced_tables", [])
    used_by_measures = deps_result.get("used_by_measures", [])

    lines.append("─" * 80)
    lines.append("  SUMMARY")
    lines.append("─" * 80)
    lines.append("")
    lines.append("  ┌─────────────────────────────────────────────────────────────────┐")
    lines.append("  │  DEPENDS ON (upstream):                                         │")
    lines.append(
        f"  │    Measures:           {len(ref_measures):>4}                                      │"
    )
    lines.append(
        f"  │    Columns:            {len(ref_columns):>4}                                      │"
    )
    lines.append(
        f"  │    Tables:             {len(ref_tables):>4}                                      │"
    )
    lines.append("  ├─────────────────────────────────────────────────────────────────┤")
    lines.append("  │  USED BY (downstream):                                          │")
    lines.append(
        f"  │    Measures:           {len(used_by_measures):>4}                                      │"
    )
    lines.append("  └─────────────────────────────────────────────────────────────────┘")
    lines.append("")

    # ───────────────────────────────────────────────────────────────────────────
    # MEASURE EXPRESSION
    # ───────────────────────────────────────────────────────────────────────────
    expression = deps_result.get("expression", "")
    if expression:
        lines.append("─" * 80)
        lines.append("  DAX EXPRESSION")
        lines.append("─" * 80)
        lines.append("")
        # Format expression with indentation
        for expr_line in expression.split("\n"):
            lines.append(f"    {expr_line}")
        lines.append("")

    # ───────────────────────────────────────────────────────────────────────────
    # REFERENCED MEASURES
    # ───────────────────────────────────────────────────────────────────────────
    if ref_measures:
        lines.append("─" * 80)
        lines.append(f"  REFERENCED MEASURES ({len(ref_measures)})")
        lines.append("─" * 80)
        lines.append("")

        # Group by table
        measures_by_table: Dict[str, List[str]] = {}
        for tbl, msr in ref_measures:
            if tbl not in measures_by_table:
                measures_by_table[tbl] = []
            measures_by_table[tbl].append(msr)

        for tbl, measures in sorted(measures_by_table.items()):
            lines.append(f"    {tbl}")
            for msr in sorted(measures):
                lines.append(f"      ├── [{msr}]")
        lines.append("")

    # ───────────────────────────────────────────────────────────────────────────
    # REFERENCED COLUMNS
    # ───────────────────────────────────────────────────────────────────────────
    if ref_columns:
        lines.append("─" * 80)
        lines.append(f"  REFERENCED COLUMNS ({len(ref_columns)})")
        lines.append("─" * 80)
        lines.append("")

        # Group by table
        columns_by_table: Dict[str, List[str]] = {}
        for tbl, col in ref_columns:
            if tbl not in columns_by_table:
                columns_by_table[tbl] = []
            columns_by_table[tbl].append(col)

        for tbl, columns in sorted(columns_by_table.items()):
            lines.append(f"    {tbl}")
            for col in sorted(columns):
                lines.append(f"      ├── [{col}]")
        lines.append("")

    # ───────────────────────────────────────────────────────────────────────────
    # REFERENCED TABLES
    # ───────────────────────────────────────────────────────────────────────────
    if ref_tables:
        lines.append("─" * 80)
        lines.append(f"  REFERENCED TABLES ({len(ref_tables)})")
        lines.append("─" * 80)
        lines.append("")
        for tbl in sorted(ref_tables):
            lines.append(f"    • {tbl}")
        lines.append("")

    # ───────────────────────────────────────────────────────────────────────────
    # USED BY MEASURES (Reverse Dependencies)
    # ───────────────────────────────────────────────────────────────────────────
    if used_by_measures:
        lines.append("─" * 80)
        lines.append(f"  USED BY MEASURES ({len(used_by_measures)}) - What depends on this measure")
        lines.append("─" * 80)
        lines.append("")

        # Group by table
        used_by_by_table: Dict[str, List[str]] = {}
        for item in used_by_measures:
            tbl = item.get("table", "")
            msr = item.get("measure", "")
            if tbl not in used_by_by_table:
                used_by_by_table[tbl] = []
            used_by_by_table[tbl].append(msr)

        for tbl, measures in sorted(used_by_by_table.items()):
            lines.append(f"    {tbl}")
            for msr in sorted(measures):
                lines.append(f"      ├── [{msr}]")
        lines.append("")
    else:
        lines.append("─" * 80)
        lines.append("  USED BY MEASURES (0) - What depends on this measure")
        lines.append("─" * 80)
        lines.append("")
        lines.append("    (No other measures reference this measure)")
        lines.append("")

    # ───────────────────────────────────────────────────────────────────────────
    # DEPENDENCY TREE PREVIEW
    # ───────────────────────────────────────────────────────────────────────────
    dep_tree = deps_result.get("dependency_tree")
    if dep_tree:
        lines.append("─" * 80)
        lines.append("  DEPENDENCY TREE (depth: 3)")
        lines.append("─" * 80)
        lines.append("")
        _format_tree_node(dep_tree, lines, indent=4, is_last=True)
        lines.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════════════════════════════════════════
    lines.append("═" * 80)

    return "\n".join(lines)


def _format_tree_node(
    node: Dict[str, Any], lines: List[str], indent: int = 0, is_last: bool = True
) -> None:
    """Recursively format a dependency tree node."""
    prefix = " " * indent
    connector = "└── " if is_last else "├── "

    tbl = node.get("table", "")
    msr = node.get("measure", "")

    # Add node
    node_str = f"{tbl}[{msr}]"
    if node.get("circular"):
        node_str += " (circular reference)"
    if node.get("max_depth_reached"):
        node_str += " (max depth)"
    if node.get("error"):
        node_str += f" (error: {node['error']})"

    lines.append(f"{prefix}{connector}{node_str}")

    # Process children
    children = node.get("dependencies", [])
    for i, child in enumerate(children):
        is_last_child = i == len(children) - 1
        child_indent = indent + 4
        _format_tree_node(child, lines, child_indent, is_last_child)


def _generate_mermaid_markdown(diagram_result: Dict[str, Any], table: str, measure: str) -> str:
    """
    Generate a complete Mermaid diagram in markdown format.
    This is returned as a SEPARATE response item for proper rendering.
    """
    if not diagram_result or not diagram_result.get("success"):
        return ""

    mermaid_code = diagram_result.get("mermaid", "")
    if not mermaid_code:
        return ""

    lines = []
    lines.append("")
    lines.append("═" * 80)
    lines.append("  DEPENDENCY DIAGRAM")
    lines.append("═" * 80)
    lines.append("")
    lines.append(f"  Measure: {table}[{measure}]")
    lines.append(f"  Direction: {diagram_result.get('direction', 'upstream')}")
    lines.append(f"  Depth: {diagram_result.get('depth', 3)}")
    lines.append(f"  Nodes: {diagram_result.get('node_count', 0)}")
    lines.append(f"  Edges: {diagram_result.get('edge_count', 0)}")
    lines.append("")
    lines.append("─" * 80)
    lines.append("")
    lines.append("```mermaid")
    lines.append(mermaid_code)
    lines.append("```")
    lines.append("")
    lines.append("─" * 80)
    lines.append("  Legend:")
    lines.append("    • Blue box = Target measure (center)")
    lines.append("    • Green boxes = Dependencies (upstream - what this measure uses)")
    lines.append("    • Orange boxes = Dependents (downstream - what uses this measure)")
    lines.append("    • Arrows show dependency flow (dependency → consumer)")
    lines.append("─" * 80)

    return "\n".join(lines)


def handle_analyze_measure_dependencies(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze measure dependencies tree with professional formatted output.

    Returns a structured response with:
    - formatted_output: Professional text-based analysis summary
    - mermaid_diagram_output: Separate markdown block with Mermaid diagram
    - Raw data fields for programmatic access
    - used_by_measures: List of measures that USE this measure (reverse dependencies)
    """
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    dependency_analyzer = connection_state.dependency_analyzer
    if not dependency_analyzer:
        return ErrorHandler.handle_manager_unavailable("dependency_analyzer")

    table = args.get("table")
    measure = args.get("measure")
    include_diagram = args.get("include_diagram", True)  # Default to True

    if not table or not measure:
        return error_envelope(
            "missing_argument",
            "table and measure parameters are required",
        )

    # Get the base dependency analysis (what this measure depends on)
    deps_result = dependency_analyzer.analyze_dependencies(table, measure, include_diagram=False)

    if not deps_result.get("success"):
        return deps_result

    # J-12: fetch INFO.MEASURES ONCE and reuse it for the table enrichment, the
    # "used by" list and (with html=true) the sidebar.
    measures_rows = None
    query_executor = connection_state.query_executor
    if query_executor:
        measures_rows = _info_rows(query_executor, "MEASURES")

    # Enrich referenced_measures with proper table names when they come back empty
    # This happens when unqualified references like [MeasureName] are parsed
    referenced_measures = deps_result.get("referenced_measures", [])
    if referenced_measures and any(not tbl for tbl, _ in referenced_measures) and measures_rows:
        measure_name_to_info = {}
        for m in measures_rows:
            m_table = m.get("Table", "") or ""
            m_name = m.get("Name", "") or ""
            if m_name:
                measure_name_to_info.setdefault(
                    normalize_dax_name(m_name),
                    {"table": m_table, "display_folder": m.get("DisplayFolder", "") or ""},
                )
        enriched_measures = []
        for tbl, msr in referenced_measures:
            if not tbl and msr:
                info = measure_name_to_info.get(normalize_dax_name(msr))
                if info:
                    enriched_measures.append((info["table"] or "Unknown", msr))
                else:
                    logger.warning(f"Could not find table for measure [{msr}]")
                    enriched_measures.append(("Unknown", msr))
            else:
                enriched_measures.append((tbl, msr))
        deps_result["referenced_measures"] = enriched_measures

    # Get "used by" analysis (what measures depend on THIS measure)
    if measures_rows is not None:
        used_by_measures = _used_by_from_rows(measures_rows, table, measure)
    else:
        usage_result = dependency_analyzer.find_measure_usage(table, measure)
        used_by_measures = usage_result.get("used_by", []) if usage_result.get("success") else []

    # Add used_by to deps_result for formatting
    deps_result["used_by_measures"] = used_by_measures

    # Get the Mermaid diagram separately if requested - use BIDIRECTIONAL to show both directions
    diagram_result = None
    if include_diagram:
        diagram_result = dependency_analyzer.generate_impact_mermaid(table, measure)

    # Generate formatted outputs
    formatted_analysis = _format_dependency_analysis_output(
        table, measure, deps_result, diagram_result
    )
    mermaid_output = (
        _generate_mermaid_markdown(diagram_result, table, measure) if include_diagram else ""
    )

    # Build the response with clear separation
    response = {
        "success": True,
        "measure": {"table": table, "name": measure},
        # PRIMARY OUTPUT - Text analysis (display this first)
        "formatted_output": formatted_analysis,
        # DIAGRAM OUTPUT - Separate markdown with Mermaid (display after analysis)
        "mermaid_diagram_output": mermaid_output,
        # Instruction for AI/client on how to display
        "display_instructions": "Display formatted_output first, then mermaid_diagram_output as a separate code block.",
        # Raw data for programmatic access
        "expression": deps_result.get("expression", ""),
        "referenced_measures": deps_result.get("referenced_measures", []),
        "referenced_columns": deps_result.get("referenced_columns", []),
        "referenced_tables": deps_result.get("referenced_tables", []),
        "dependency_tree": deps_result.get("dependency_tree"),
        # USED BY - Reverse dependencies (what measures use this measure)
        "used_by_measures": used_by_measures,
        # Summary stats
        "summary": {
            "measure_count": len(deps_result.get("referenced_measures", [])),
            "column_count": len(deps_result.get("referenced_columns", [])),
            "table_count": len(deps_result.get("referenced_tables", [])),
            "total_dependencies": len(deps_result.get("referenced_measures", []))
            + len(deps_result.get("referenced_columns", [])),
            "used_by_count": len(used_by_measures),
        },
    }

    # Include raw mermaid code for clients that want to render it themselves
    if diagram_result and diagram_result.get("success"):
        mermaid_code = diagram_result.get("mermaid", "")
        response["mermaid_raw"] = mermaid_code
        response["diagram_metadata"] = {
            "direction": "bidirectional",
            "depth": diagram_result.get("depth", 3),
            "node_count": diagram_result.get("node_count", 0),
            "edge_count": diagram_result.get("edge_count", 0),
            "upstream_count": diagram_result.get(
                "upstream_count", len(deps_result.get("referenced_measures", []))
            ),
            "downstream_count": diagram_result.get("downstream_count", len(used_by_measures)),
        }

        # Interactive HTML diagram is OPT-IN (review D-10, 2026-09-22): it
        # recomputes the dependencies of EVERY measure for the sidebar (O(N^2)
        # DMV round-trips on large models) and used to open a browser on every
        # call. Pass html=true (and auto_open=true to open it).
        if mermaid_code and bool(args.get("html", False)):
            # Load sidebar data for the Model Browser
            sidebar_data = _load_sidebar_data(measures_rows=measures_rows)
            main_item_key = f"{table}[{measure}]"

            html_path = generate_dependency_html(
                mermaid_code=mermaid_code,
                measure_table=table,
                measure_name=measure,
                metadata=response.get("diagram_metadata", {}),
                auto_open=bool(args.get("auto_open", False)),
                referenced_measures=deps_result.get("referenced_measures", []),
                referenced_columns=deps_result.get("referenced_columns", []),
                used_by_measures=used_by_measures,
                # Sidebar data for Model Browser
                all_measures=sidebar_data.get("all_measures", []),
                all_columns=sidebar_data.get("all_columns", []),
                all_dependencies=sidebar_data.get("all_dependencies", {}),
                main_item=main_item_key,
            )
            if html_path:
                response["diagram_rendered"] = True
                response["html_diagram_path"] = html_path
                response["display_instructions"] = (
                    f"Interactive diagram opened in browser: {html_path}"
                )
                # Remove text-based mermaid since HTML is better
                if "mermaid_diagram_output" in response:
                    del response["mermaid_diagram_output"]
                if "mermaid_raw" in response:
                    del response["mermaid_raw"]
                logger.info(f"Generated HTML diagram: {html_path}")
            else:
                logger.warning("Failed to generate HTML diagram")
                response["diagram_render_error"] = "Failed to generate HTML diagram"

    return response


def handle_get_measure_impact(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get measure usage impact"""
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    dependency_analyzer = connection_state.dependency_analyzer
    if not dependency_analyzer:
        return ErrorHandler.handle_manager_unavailable("dependency_analyzer")

    table = args.get("table")
    measure = args.get("measure")

    if not table or not measure:
        return error_envelope(
            "missing_argument",
            "table and measure parameters are required",
        )

    return dependency_analyzer.get_measure_impact(table, measure)


def handle_dax_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch DAX operations: dependencies, impact, export"""
    operation = args.get("operation", "dependencies")

    if operation == "dependencies":
        return handle_analyze_measure_dependencies(args)
    elif operation == "impact":
        return handle_get_measure_impact(args)
    elif operation == "export":
        from server.handlers.column_usage_handler import handle_export_dax_measures

        return handle_export_dax_measures(args)
    else:
        return error_envelope(
            "invalid_operation",
            f"Unknown operation: {operation}. Valid: dependencies, impact, export",
        )
