"""
Column Usage Handler

MCP handler for column-measure usage mapping.
Provides tools to answer:
- Which measures use columns from specific tables?
- What columns does a measure reference?
- What measures reference a specific column?
"""

import logging
import threading
from typing import Dict, Any, List, Optional
from server.registry import ToolDefinition
from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from core.validation.path_guard import guard_or_error
from core.analysis.column_usage_analyzer import ColumnUsageAnalyzer
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

# Singleton analyzer instance (created on first use) - thread-safe
_analyzer_instance: Optional[ColumnUsageAnalyzer] = None
_analyzer_lock = threading.Lock()


def _get_analyzer() -> Optional[ColumnUsageAnalyzer]:
    """Get or create the column usage analyzer instance (thread-safe)"""
    global _analyzer_instance

    if not connection_state.is_connected():
        return None

    query_executor = connection_state.query_executor
    if not query_executor:
        return None

    if _analyzer_instance is None:
        with _analyzer_lock:
            # Double-checked locking
            if _analyzer_instance is None:
                _analyzer_instance = ColumnUsageAnalyzer(query_executor)
                logger.info("Created ColumnUsageAnalyzer instance")

    return _analyzer_instance


def _format_measures_by_table_output(result: Dict[str, Any], include_dax: bool = True) -> str:
    """Format the measures-by-table result for display"""
    lines = []

    lines.append("=" * 80)
    lines.append("  COLUMN USAGE ANALYSIS - Measures Using Tables")
    lines.append("=" * 80)
    lines.append("")

    tables_requested = result.get("tables_requested", [])
    lines.append(f"  Tables analyzed: {', '.join(tables_requested)}")
    lines.append("")

    summary = result.get("summary", {})
    lines.append("-" * 80)
    lines.append("  SUMMARY")
    lines.append("-" * 80)
    lines.append(f"  Tables with usage: {summary.get('tables_found', 0)}")
    lines.append(f"  Columns with usage: {summary.get('columns_with_usage', 0)}")
    lines.append(f"  Unique measures: {summary.get('unique_measures', 0)}")
    lines.append("")

    results = result.get("results", {})

    for table_name, columns in sorted(results.items()):
        lines.append("-" * 80)
        lines.append(f"  TABLE: {table_name}")
        lines.append("-" * 80)
        lines.append("")

        for col_name, measures in sorted(columns.items()):
            lines.append(f"    [{col_name}] - {len(measures)} measure(s)")
            for m in measures[:10]:  # Limit to first 10
                folder = f" ({m.get('display_folder', '')})" if m.get("display_folder") else ""
                lines.append(f"      -> {m['table']}[{m['measure']}]{folder}")
                # Include DAX if available and requested
                if include_dax and m.get("dax"):
                    dax = m["dax"].replace("\n", "\n          ")  # Indent multiline DAX
                    lines.append(f"          DAX: {dax}")
            if len(measures) > 10:
                lines.append(f"      ... and {len(measures) - 10} more")
            lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def _format_measures_flat_output(result: Dict[str, Any], include_dax: bool = True) -> str:
    """Format the flat measures list for display"""
    lines = []

    lines.append("=" * 80)
    lines.append("  MEASURES USING TABLES")
    lines.append("=" * 80)
    lines.append("")

    tables_requested = result.get("tables_requested", [])
    lines.append(f"  Tables analyzed: {', '.join(tables_requested)}")
    lines.append("")

    measures = result.get("measures", [])
    summary = result.get("summary", {})

    lines.append(f"  Total unique measures: {summary.get('unique_measures', len(measures))}")
    lines.append("")

    lines.append("-" * 80)
    lines.append("  MEASURES")
    lines.append("-" * 80)
    lines.append("")

    # Group by display folder for better organization
    by_folder: Dict[str, List[Dict]] = {}
    for m in measures:
        folder = m.get("display_folder", "") or "(No folder)"
        if folder not in by_folder:
            by_folder[folder] = []
        by_folder[folder].append(m)

    for folder, folder_measures in sorted(by_folder.items()):
        lines.append(f"  {folder}")
        for m in sorted(folder_measures, key=lambda x: x["measure"]):
            lines.append(f"    - {m['table']}[{m['measure']}]")
            # Include DAX if available and requested
            if include_dax and m.get("dax"):
                dax = m["dax"].replace("\n", "\n        ")  # Indent multiline DAX
                lines.append(f"        DAX: {dax}")
        lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def _format_measure_columns_output(result: Dict[str, Any], include_dax: bool = True) -> str:
    """Format measure-to-columns output"""
    lines = []

    measure_info = result.get("measure", {})
    columns = result.get("columns", [])
    dax_expression = result.get("dax", "")

    lines.append("=" * 80)
    lines.append(
        f"  COLUMNS USED BY MEASURE: {measure_info.get('table', '')}[{measure_info.get('measure', '')}]"
    )
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"  Total columns: {len(columns)}")
    lines.append("")

    # Include DAX expression if available
    if include_dax and dax_expression:
        lines.append("-" * 80)
        lines.append("  DAX EXPRESSION")
        lines.append("-" * 80)
        lines.append("")
        for dax_line in dax_expression.split("\n"):
            lines.append(f"    {dax_line}")
        lines.append("")

    # Group by table
    by_table: Dict[str, List[str]] = {}
    for col in columns:
        table = col.get("table", "")
        column = col.get("column", "")
        if table not in by_table:
            by_table[table] = []
        by_table[table].append(column)

    lines.append("-" * 80)
    lines.append("  REFERENCED COLUMNS")
    lines.append("-" * 80)
    for table, cols in sorted(by_table.items()):
        lines.append(f"  {table}")
        for col in sorted(cols):
            lines.append(f"    - [{col}]")
        lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def _format_unused_columns_output(result: Dict[str, Any]) -> str:
    """Format unused columns output - COMPLETE analysis, no other tools needed"""
    lines = []

    lines.append("=" * 80)
    lines.append("  COLUMN USAGE ANALYSIS - COMPLETE REPORT")
    lines.append("=" * 80)
    lines.append("")

    tables_filter = result.get("tables_filter")
    if tables_filter:
        lines.append(f"  Tables analyzed: {', '.join(tables_filter)}")
    else:
        lines.append("  Tables analyzed: ALL TABLES")
    lines.append("")

    # Summary with complete breakdown
    summary = result.get("summary", {})
    total = summary.get("total_columns_analyzed", 0)
    used_measures = summary.get("used_by_measures", 0)
    used_rels = summary.get("used_by_relationships_only", 0)
    used_sort_by = summary.get("used_by_sort_by", 0)
    used_fp = summary.get("used_by_field_params", 0)
    used_rls = summary.get("used_by_rls", 0)
    unused = summary.get("unused", 0)

    lines.append("-" * 80)
    lines.append("  SUMMARY")
    lines.append("-" * 80)
    lines.append(f"  Total columns analyzed: {total}")
    lines.append(f"  [+] Used by measures: {used_measures}")
    lines.append(f"  [+] Used by relationships only: {used_rels}")
    if used_sort_by:
        lines.append(f"  [+] Used as SortByColumn: {used_sort_by}")
    if used_fp:
        lines.append(f"  [+] Used by field parameters: {used_fp}")
    if used_rls:
        lines.append(f"  [+] Used by RLS (row-level security): {used_rls}")
    lines.append(f"  [-] UNUSED: {unused}")
    lines.append("")

    # Show unused columns (the main result)
    unused_by_table = result.get("unused_by_table", {})
    if unused_by_table:
        lines.append("-" * 80)
        lines.append("  [-] UNUSED COLUMNS (can potentially be removed)")
        lines.append("-" * 80)
        lines.append("")

        for table_name, columns in sorted(unused_by_table.items()):
            lines.append(f"  {table_name} ({len(columns)} unused)")
            for col in sorted(columns):
                lines.append(f"    - [{col}]")
            lines.append("")
    else:
        lines.append("-" * 80)
        lines.append("  [+] No unused columns found - all columns are in use!")
        lines.append("-" * 80)
        lines.append("")

    # Show relationship-only columns
    rel_by_table = result.get("used_by_relationships_only_by_table", {})
    if rel_by_table:
        lines.append("-" * 80)
        lines.append("  [+] COLUMNS USED BY RELATIONSHIPS ONLY")
        lines.append("      (Not in measures, but required for model relationships)")
        lines.append("-" * 80)
        lines.append("")

        for table_name, columns in sorted(rel_by_table.items()):
            lines.append(f"  {table_name} ({len(columns)} relationship keys)")
            for col in sorted(columns):
                lines.append(f"    - [{col}]")
            lines.append("")

    # Show SortByColumn columns
    sort_by_table = result.get("used_by_sort_by_by_table", {})
    if sort_by_table:
        lines.append("-" * 80)
        lines.append("  [+] COLUMNS USED AS SORTBYCOLUMN")
        lines.append("      (Used to sort other columns — do not remove)")
        lines.append("-" * 80)
        lines.append("")

        for table_name, columns in sorted(sort_by_table.items()):
            lines.append(f"  {table_name} ({len(columns)} sort columns)")
            for col in sorted(columns):
                lines.append(f"    - [{col}]")
            lines.append("")

    # Show field parameter columns
    fp_by_table = result.get("used_by_field_params_by_table", {})
    if fp_by_table:
        lines.append("-" * 80)
        lines.append("  [+] COLUMNS USED BY FIELD PARAMETERS")
        lines.append("      (Referenced by NAMEOF or internal to field parameter tables)")
        lines.append("-" * 80)
        lines.append("")

        for table_name, columns in sorted(fp_by_table.items()):
            lines.append(f"  {table_name} ({len(columns)} field param columns)")
            for col in sorted(columns):
                lines.append(f"    - [{col}]")
            lines.append("")

    # Show RLS columns
    rls_by_table = result.get("used_by_rls_by_table", {})
    if rls_by_table:
        lines.append("-" * 80)
        lines.append("  [+] COLUMNS USED BY RLS (ROW-LEVEL SECURITY)")
        lines.append("      (Referenced in security role filters — do not remove)")
        lines.append("-" * 80)
        lines.append("")

        for table_name, columns in sorted(rls_by_table.items()):
            lines.append(f"  {table_name} ({len(columns)} RLS columns)")
            for col in sorted(columns):
                lines.append(f"    - [{col}]")
            lines.append("")

    # Show columns used by measures (for completeness)
    used_by_table = result.get("used_by_measures_by_table", {})
    if used_by_table:
        lines.append("-" * 80)
        lines.append("  [+] COLUMNS USED BY MEASURES")
        lines.append("-" * 80)
        lines.append("")

        for table_name, columns in sorted(used_by_table.items()):
            lines.append(f"  {table_name} ({len(columns)} used)")
            for col in sorted(columns):
                lines.append(f"    - [{col}]")
            lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def _format_column_measures_output(result: Dict[str, Any], include_dax: bool = True) -> str:
    """Format column-to-measures output"""
    lines = []

    column_info = result.get("column", {})
    measures = result.get("measures", [])

    lines.append("=" * 80)
    lines.append(
        f"  MEASURES USING COLUMN: {column_info.get('table', '')}[{column_info.get('column', '')}]"
    )
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"  Total measures: {len(measures)}")
    lines.append("")

    lines.append("-" * 80)
    for m in measures:
        folder = f" ({m.get('display_folder', '')})" if m.get("display_folder") else ""
        lines.append(f"  - {m['table']}[{m['measure']}]{folder}")
        # Include DAX if available and requested
        if include_dax and m.get("dax"):
            dax = m["dax"].replace("\n", "\n      ")  # Indent multiline DAX
            lines.append(f"      DAX: {dax}")
            lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def handle_column_usage_mapping(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle column usage mapping operations.

    Operations:
    - get_measures_for_tables: Get all measures that use columns from specified tables
    - get_columns_for_measure: Get all columns used by a specific measure
    - get_measures_for_column: Get all measures that use a specific column
    - get_full_mapping: Get complete bidirectional mapping
    - get_unused_columns: Get columns not referenced by any measure
    - get_unused_columns_pbip: Find unused columns/measures from PBIP folder (scoped to explicit report_paths; use when you need to analyze a subset of reports e.g. only R01xx)
    """
    operation = args.get("operation", "get_measures_for_tables")

    # PBIP operation — no live connection required
    if operation == "get_unused_columns_pbip":
        return _handle_get_unused_columns_pbip(args)

    # All other operations require live connection
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    analyzer = _get_analyzer()
    if not analyzer:
        return ErrorHandler.handle_manager_unavailable("column_usage_analyzer")

    force_refresh = args.get("force_refresh", False)
    include_dax = args.get("include_dax", False)  # Default to False for size optimization

    try:
        if operation == "get_measures_for_tables":
            # Main use case: What measures use columns from these tables?
            tables = args.get("tables", [])
            if not tables:
                return error_envelope(
                    "missing_argument",
                    "tables parameter is required (list of table names)",
                )

            group_by = args.get("group_by", "table")
            result = analyzer.get_measures_using_tables(
                tables, force_refresh, group_by, include_dax
            )

            # Add formatted output
            if group_by == "table":
                result["formatted_output"] = _format_measures_by_table_output(result, include_dax)
            elif group_by == "flat":
                result["formatted_output"] = _format_measures_flat_output(result, include_dax)

            return result

        elif operation == "get_columns_for_measure":
            # What columns does this measure use?
            table = args.get("table")
            measure = args.get("measure")

            if not table or not measure:
                return error_envelope(
                    "missing_argument",
                    "table and measure parameters are required",
                )

            result = analyzer.get_columns_used_by_measure(
                table, measure, force_refresh, include_dax
            )
            result["formatted_output"] = _format_measure_columns_output(result, include_dax)
            return result

        elif operation == "get_measures_for_column":
            # What measures use this column?
            table = args.get("table")
            column = args.get("column")

            if not table or not column:
                return error_envelope(
                    "missing_argument",
                    "table and column parameters are required",
                )

            result = analyzer.get_measures_using_column(table, column, force_refresh, include_dax)
            result["formatted_output"] = _format_column_measures_output(result, include_dax)
            return result

        elif operation == "get_full_mapping":
            # Get complete bidirectional mapping with all data
            return analyzer.get_full_mapping(force_refresh, include_dax)

        elif operation == "get_unused_columns":
            # Get columns not used by any measure or relationship
            tables = args.get("tables")  # Optional filter
            result = analyzer.get_unused_columns(tables, force_refresh)
            result["formatted_output"] = _format_unused_columns_output(result)
            return result

        elif operation == "export_to_csv":
            # Export to CSV files for Excel
            tables = args.get("tables")  # Optional filter
            output_path = args.get("output_path")
            if output_path:
                safe_out, err = guard_or_error(
                    output_path,
                    "output_path",
                    create_parents=True,
                )
                if err:
                    return err
                output_path = str(safe_out)
            return analyzer.export_to_csv(tables, output_path, include_dax, force_refresh)

        elif operation == "export_measures":
            return handle_export_dax_measures(args)

        else:
            return error_envelope(
                "invalid_operation",
                f"Unknown operation: {operation}",
                valid_operations=[
                    "get_measures_for_tables",
                    "get_columns_for_measure",
                    "get_measures_for_column",
                    "get_full_mapping",
                    "get_unused_columns",
                    "export_to_csv",
                    "export_measures",
                ],
            )

    except Exception as e:
        logger.error(f"Error in column usage mapping: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("column_usage_mapping", e)


def _analyze_pbip_multi_report(
    pbip_path: str, report_paths: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Analyze a PBIP project across multiple reports.

    Without ``report_paths`` the result comes from the shared
    ``PbipModelCache`` (the same parse + per-report intersection that
    ``07_PBIP_Operations query_unused`` serves; P-02: ~10 ms on a hit instead
    of a 0.7-5 s re-parse). An explicit ``report_paths`` subset is not part of
    the cache scope and is parsed fresh.

    Args:
        pbip_path: Path to PBIP project directory or .pbip file
        report_paths: Optional explicit list of .Report folder paths.
                      If not provided, auto-discovers all reports.

    Returns:
        Dict with unused_columns, unused_measures, reports_analyzed, model stats
    """
    if not report_paths:
        return _unused_from_cache(pbip_path)
    return _analyze_pbip_fresh(pbip_path, report_paths)


def _unused_from_cache(pbip_path: str) -> Dict[str, Any]:
    """Unused columns/measures for the project scope, from ``PbipModelCache``."""
    from server.pbip_cache import pbip_cache

    project = pbip_cache.get_or_parse(pbip_path)
    deps = project.dependencies or {}
    cov = deps.get("coverage") or {}
    failed = list(cov.get("reports_failed") or [])
    report_data = project.report_data
    model_only = report_data is None
    if model_only:
        analyzed: List[str] = []
    else:
        analyzed = list(
            report_data.get("_report_names")
            or ([report_data["_report_name"]] if report_data.get("_report_name") else [])
        )
    tables = project.model_data.get("tables", []) or []
    complete = not failed and not model_only
    result: Dict[str, Any] = {
        "unused_columns": sorted(deps.get("unused_columns", []) or []),
        "unused_measures": sorted(deps.get("unused_measures", []) or []),
        "reports_analyzed": analyzed,
        "reports_failed": failed,
        "total_reports": len(analyzed) + len(failed),
        "total_columns": sum(len(t.get("columns", [])) for t in tables),
        "total_measures": sum(len(t.get("measures", [])) for t in tables),
        "complete": complete,
        "scope": "model-only" if model_only else "model+reports",
        "usage_reasons": dict(deps.get("usage_reasons") or {}),
    }
    if not complete:
        result["warning"] = _partial_warning(model_only, len(failed))
    return result


def _partial_warning(model_only: bool, n_failed: int) -> str:
    return (
        "PARTIAL result: "
        + (
            "no report could be analyzed (model-only: report bindings are NOT counted)"
            if model_only
            else f"{n_failed} report(s) failed to parse; their bindings are NOT counted"
        )
        + " -- do not delete objects on this list without checking"
    )


def _analyze_pbip_fresh(pbip_path: str, report_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """Fresh (uncached) per-report analysis for an explicit ``report_paths`` subset."""
    import os
    from core.pbip.pbir_model import resolve_pbip
    from core.pbip.pbip_model_analyzer import TmdlModelAnalyzer
    from core.pbip.pbip_report_analyzer import PbirReportAnalyzer
    from core.pbip.pbip_dependency_engine import PbipDependencyEngine

    # A-13 / G-06: resolve the ORIGINAL path (a report .pbip scopes to that
    # report + its byPath model; a model / folder to every bound report).
    res = resolve_pbip(pbip_path)
    models = res.get("models") or []
    if not models:
        raise ValueError(f"No PBIP semantic models found in: {pbip_path}")
    if len(models) > 1 and not report_paths:
        raise ValueError(
            "Ambiguous PBIP path: several semantic models ("
            + ", ".join(m.get("name", "?") for m in models)
            + "); pass a model .pbip / .SemanticModel folder or explicit report_paths"
        )
    model_folder = models[0].get("model_dir")
    if not model_folder:
        raise ValueError("Semantic model folder path not found")

    # Parse model once
    analyzer = TmdlModelAnalyzer()
    typed_model = analyzer.analyze_model_typed(model_folder)
    model_data = typed_model.to_dict()
    model_data["model_folder"] = model_folder

    # Determine report folders (only reports bound to THIS model)
    if report_paths:
        report_folders = report_paths
    else:
        report_folders = list(models[0].get("report_dirs") or [])

    # Run dependency analysis per report
    report_analyzer = PbirReportAnalyzer()
    all_unused_columns: List[set] = []
    all_unused_measures: List[set] = []
    reports_analyzed = []
    reports_failed = []
    reasons: Dict[str, List[str]] = {}

    for report_folder in report_folders:
        try:
            report_data = report_analyzer.analyze_report(report_folder)
            engine = PbipDependencyEngine(model_data, report_data)
            deps = engine.analyze_all_dependencies()
            all_unused_columns.append(set(deps.get("unused_columns", [])))
            all_unused_measures.append(set(deps.get("unused_measures", [])))
            for k, lst in (deps.get("usage_reasons") or {}).items():
                cur = reasons.setdefault(k, [])
                cur.extend(r for r in lst if r not in cur)
            reports_analyzed.append(os.path.basename(report_folder))
        except Exception as e:
            logger.warning(f"Failed to analyze report {report_folder}: {e}")
            reports_failed.append({"report": os.path.basename(str(report_folder)), "error": str(e)})

    # Fallback: if no reports found/parsed, run model-only analysis
    model_only = not all_unused_columns
    if model_only:
        engine = PbipDependencyEngine(model_data, None)
        deps = engine.analyze_all_dependencies()
        all_unused_columns.append(set(deps.get("unused_columns", [])))
        all_unused_measures.append(set(deps.get("unused_measures", [])))

    # Intersection: unused only if unused in ALL analyses
    final_unused_columns = sorted(all_unused_columns[0].intersection(*all_unused_columns[1:]))
    final_unused_measures = sorted(all_unused_measures[0].intersection(*all_unused_measures[1:]))

    # Count total columns in model
    total_columns = sum(len(table.get("columns", [])) for table in model_data.get("tables", []))
    total_measures = sum(len(table.get("measures", [])) for table in model_data.get("tables", []))

    # G-08 / G-28: a report that failed to parse is NOT silently dropped from
    # the intersection -- the result is marked partial and says why.
    complete = not reports_failed and not model_only
    result = {
        "unused_columns": final_unused_columns,
        "unused_measures": final_unused_measures,
        "reports_analyzed": reports_analyzed,
        "reports_failed": reports_failed,
        "total_reports": len(report_folders),
        "total_columns": total_columns,
        "total_measures": total_measures,
        "complete": complete,
        "scope": "model-only" if model_only else "model+reports",
        "usage_reasons": reasons,
    }
    if not complete:
        result["warning"] = _partial_warning(model_only, len(reports_failed))

    return result


def _handle_get_unused_columns_pbip(args: Dict[str, Any]) -> Dict[str, Any]:
    """Find unused columns/measures from PBIP folder, scoped to explicit report_paths.

    Unlike 07_PBIP_Operations query_unused (which uses the cache and always
    analyzes ALL reports), this operation accepts an explicit report_paths list
    so you can scope the analysis to a subset (e.g. only R01xx reports).
    Without report_paths it is served from the shared PbipModelCache (P-02);
    an explicit report_paths subset is parsed fresh.
    """
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope(
            "missing_argument",
            "pbip_path is required for PBIP analysis",
        )

    report_paths = args.get("report_paths")
    tables_filter = args.get("tables")

    try:
        result = _analyze_pbip_multi_report(pbip_path, report_paths)

        unused_columns = result["unused_columns"]
        unused_measures = result["unused_measures"]

        # Apply table filter if specified
        if tables_filter:
            tables_lower = {t.lower() for t in tables_filter}
            unused_columns = [
                c
                for c in unused_columns
                if "[" in c and c[: c.index("[")].strip("'\"").lower() in tables_lower
            ]

        # Group unused columns by table
        unused_by_table: Dict[str, List[str]] = {}
        for col_key in unused_columns:
            if "[" in col_key:
                bracket_idx = col_key.index("[")
                table = col_key[:bracket_idx].strip("'\"")
                column = col_key[bracket_idx + 1 :].rstrip("]")
                if table not in unused_by_table:
                    unused_by_table[table] = []
                unused_by_table[table].append(column)

        output = {
            "success": True,
            "unused_columns": unused_columns,
            "unused_columns_count": len(unused_columns),
            "unused_by_table": unused_by_table,
            "unused_measures": unused_measures,
            "unused_measures_count": len(unused_measures),
            "reports_analyzed": result["reports_analyzed"],
            "reports_failed": result.get("reports_failed", []),
            "complete": result.get("complete", True),
            "scope": result.get("scope"),
            "total_reports": result["total_reports"],
            "total_columns": result["total_columns"],
            "total_measures": result["total_measures"],
            "tables_filter": tables_filter,
        }
        if result.get("warning"):
            output["warning"] = result["warning"]
        if args.get("include_reasons"):
            output["usage_reasons"] = result.get("usage_reasons", {})
        output["formatted_output"] = _format_unused_columns_pbip_output(output)
        return output

    except Exception as e:
        logger.error(f"Error in PBIP column usage analysis: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("column_usage_pbip", e)


def _format_unused_columns_pbip_output(result: Dict[str, Any]) -> str:
    """Format unused columns output for PBIP multi-report analysis."""
    lines = []

    lines.append("=" * 80)
    lines.append("  COLUMN USAGE ANALYSIS - PBIP MULTI-REPORT")
    lines.append("=" * 80)
    lines.append("")

    # Reports analyzed
    reports = result.get("reports_analyzed", [])
    lines.append(f"  Reports analyzed: {len(reports)}")
    for r in reports:
        lines.append(f"    - {r}")

    reports_failed = result.get("reports_failed", [])
    if reports_failed:
        lines.append(f"  Reports failed: {len(reports_failed)}")
        for r in reports_failed:
            name = r.get("report") if isinstance(r, dict) else r
            err = f": {r.get('error')}" if isinstance(r, dict) and r.get("error") else ""
            lines.append(f"    - {name} (FAILED{err})")
    lines.append("")

    # Table filter info
    tables_filter = result.get("tables_filter")
    if tables_filter:
        lines.append(f"  Tables filtered: {', '.join(tables_filter)}")
    else:
        lines.append("  Tables analyzed: ALL TABLES")
    lines.append("")

    # Summary
    total_cols = result.get("total_columns", 0)
    total_measures = result.get("total_measures", 0)
    unused_count = result.get("unused_columns_count", 0)
    unused_measures_count = result.get("unused_measures_count", 0)

    lines.append("-" * 80)
    lines.append("  SUMMARY")
    lines.append("-" * 80)
    lines.append(f"  Total columns in model: {total_cols}")
    lines.append(f"  Total measures in model: {total_measures}")
    lines.append(f"  [-] UNUSED columns (not in ANY report): {unused_count}")
    lines.append(f"  [-] UNUSED measures (not in ANY report): {unused_measures_count}")
    lines.append("")

    # Unused columns grouped by table
    unused_by_table = result.get("unused_by_table", {})
    if unused_by_table:
        num_reports = len(reports)
        lines.append("-" * 80)
        lines.append("  [-] UNUSED COLUMNS")
        lines.append("      (not used in visuals, measures, filters, or field parameters")
        lines.append(f"       across ANY of the {num_reports} analyzed report(s))")
        lines.append("-" * 80)
        lines.append("")

        for table_name, columns in sorted(unused_by_table.items()):
            lines.append(f"  {table_name} ({len(columns)} unused)")
            for col in sorted(columns):
                lines.append(f"    - [{col}]")
            lines.append("")
    else:
        lines.append("-" * 80)
        lines.append("  [+] No unused columns found across all reports!")
        lines.append("-" * 80)
        lines.append("")

    # Unused measures
    unused_measures = result.get("unused_measures", [])
    if unused_measures:
        lines.append("-" * 80)
        lines.append(f"  [-] UNUSED MEASURES ({len(unused_measures)})")
        lines.append("-" * 80)
        lines.append("")
        for m in sorted(unused_measures):
            lines.append(f"    - {m}")
        lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def handle_export_dax_measures(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Export all DAX measures to CSV with table, name, display folder, and DAX expression.
    """
    import csv
    import os
    from datetime import datetime

    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    query_executor = connection_state.query_executor
    if not query_executor:
        return ErrorHandler.handle_manager_unavailable("query_executor")

    try:
        # Get all measures
        measures_result = query_executor.execute_info_query("MEASURES")
        if not measures_result.get("success"):
            return error_envelope(
                "operation_failed",
                f"Failed to get measures: {measures_result.get('error')}",
            )

        all_measures = measures_result.get("rows", [])

        # Determine output directory
        output_path = args.get("output_path")
        if output_path is None:
            output_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "exports"
            )
        else:
            safe_out, err = guard_or_error(
                output_path,
                "output_path",
                create_parents=True,
            )
            if err:
                return err
            output_path = str(safe_out)

        os.makedirs(output_path, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(output_path, f"all_dax_measures_{timestamp}.csv")

        row_count = 0
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Table", "Measure_Name", "Display_Folder", "DAX_Expression"])

            for m in all_measures:
                m_table = m.get("Table", "") or m.get("[Table]", "")
                m_name = m.get("Name", "") or m.get("[Name]", "")
                m_expression = m.get("Expression", "") or m.get("[Expression]", "")
                m_folder = m.get("DisplayFolder", "") or m.get("[DisplayFolder]", "") or ""

                if m_table and m_name:
                    writer.writerow([m_table, m_name, m_folder, m_expression])
                    row_count += 1

        logger.info(f"Exported {row_count} DAX measures to CSV: {csv_path}")

        return {
            "success": True,
            "file_path": csv_path,
            "statistics": {"measures_exported": row_count},
            "message": f"Exported {row_count} DAX measures to:\n  {csv_path}",
        }

    except Exception as e:
        logger.error(f"Error exporting DAX measures: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("export_dax_measures", e)


def register_column_usage_handler(registry):
    """Register the column usage mapping handler"""

    input_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "get_unused_columns",
                    "get_unused_columns_pbip",
                    "get_measures_for_tables",
                    "get_columns_for_measure",
                    "get_measures_for_column",
                    "get_full_mapping",
                    "export_to_csv",
                    "export_measures",
                ],
                "default": "get_unused_columns",
            },
            "pbip_path": {
                "type": "string",
                "description": (
                    "get_unused_columns_pbip: .pbip / .SemanticModel / folder (a model covers "
                    "every bound report)."
                ),
                "x-doc": "PBIP path (get_unused_columns_pbip): report .pbip = that report + its model; model .pbip / .SemanticModel / folder = every report bound to the model",
            },
            "include_reasons": {
                "type": "boolean",
                "description": "get_unused_columns_pbip: also return usage_reasons (why each used object is used)",
            },
            "report_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Report folder paths",
            },
            "tables": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Table name filter",
            },
            "table": {"type": "string"},
            "measure": {"type": "string"},
            "column": {"type": "string"},
            "group_by": {
                "type": "string",
                "enum": ["table", "column", "measure", "flat"],
                "default": "flat",
            },
            "output_path": {
                "type": "string",
                "description": "Directory path for CSV export (default: exports/)",
            },
            "include_dax": {"type": "boolean", "default": False},
            "force_refresh": {"type": "boolean", "default": False},
        },
        "required": ["operation"],
    }

    tool = ToolDefinition(
        name="05_Column_Usage_Mapping",
        description="Column usage mapping & targeted unused analysis. Live: measure-column mapping, CSV export. Offline: get_unused_columns_pbip scoped to specific report_paths (use for targeted R01/R02 analysis). For default unused-column analysis across the whole project prefer 07_PBIP_Operations operation=query_unused (offline, faster, multi-report aware).",
        handler=handle_column_usage_mapping,
        input_schema=input_schema,
        category="dax",
        sort_order=54,  # 05 = DAX Intelligence
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )

    registry.register(tool)
    logger.info("Registered column_usage_mapping handler")
