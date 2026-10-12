"""
PBIP Operations Handler
Unified handler for offline PBIP analysis operations with caching.

Provides granular, composable operations on PBIP projects:
- analyze: Full analysis with HTML report
- query_dependencies: Dependency graph queries
- query_measures: Measure search and filtering
- query_relationships: Relationship quality analysis
- query_unused: Unused object detection
- validate_model: TMDL validation and linting
- compare_models: Offline model comparison
- generate_documentation: Markdown documentation generation
- validate_report / preflight: read-only "will Desktop open this / is it safe to commit"
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from server.registry import ToolDefinition
from server.progress import emit_progress
from server.pbip_cache import pbip_cache, normalize_pbip_path
from server.tool_schemas import TOOL_SCHEMAS
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


_cache = pbip_cache
_normalize_pbip_path = normalize_pbip_path


# ═════════════════════════════════════════════════════════════════════
# Dispatchers
# ═════════════════════════════════════════════════════════════════════

# P2-181: Removed handle_pbip_model_analysis and handle_pbip_query — neither
# was registered as an MCP tool. Only handle_pbip_operations (the unified
# variant) is wired up in register_pbip_operations_handler. The inner
# _handle_* functions below are still used by the unified dispatcher.


# ═════════════════════════════════════════════════════════════════════
# Operation handlers
# ═════════════════════════════════════════════════════════════════════


def _handle_analyze(args: Dict[str, Any]) -> Dict[str, Any]:
    """Full analysis with HTML report (uses cache for parsed model data)."""
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    try:
        from core.pbip.pbip_enhanced_analyzer import EnhancedPbipAnalyzer
        from core.pbip.pbip_html_generator import PbipHtmlGenerator

        emit_progress(1, 4, "Parsing PBIP project")
        # A-13: the ORIGINAL path lets the cache scope to the named report
        data = _cache.get_or_parse(pbip_path)

        model_data = data.model_data
        report_data = data.report_data
        dependencies = data.dependencies

        # Run enhanced analysis (BPA, lineage, quality metrics)
        emit_progress(2, 4, "Running BPA + lineage + quality analysis")
        analyzer = EnhancedPbipAnalyzer(
            model_data=model_data, dependencies=dependencies, report_data=report_data
        )
        result = analyzer.run_full_analysis()

        # Determine output path
        server_root = Path(__file__).parent.parent.parent
        default_exports_path = server_root / "exports"
        default_exports_path.mkdir(exist_ok=True)

        output_path = args.get("output_path", str(default_exports_path))
        if not os.path.isabs(output_path):
            output_path = str(server_root / output_path)

        repo_name = _project_name(pbip_path, model_data) or "PBIP_Repository"
        output_format = (args.get("output_format") or "html").lower()
        summary = _analysis_summary(model_data, dependencies, result, data)

        if output_format in ("summary", "detailed", "json"):
            emit_progress(3, 4, "Writing analysis JSON")
            import json as _json

            os.makedirs(output_path, exist_ok=True)
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in repo_name)[:80]
            json_path = os.path.join(output_path, f"{safe}_analysis.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                # P-12: ``dumps`` runs on the C encoder; ``dump(fh)`` was the
                # pure-Python one (92-105 ms vs 15 ms on a 1.4 MB result).
                fh.write(
                    _json.dumps(
                        {"summary": summary, "analysis": result},
                        ensure_ascii=False,
                        default=str,
                        separators=(",", ":"),
                    )
                )
            emit_progress(4, 4, "Complete")
            out: Dict[str, Any] = {
                "success": True,
                "output_format": output_format,
                "json_report": json_path,
                "summary": summary,
            }
            if output_format == "detailed":
                out["analysis"] = result
            return out

        # Generate HTML report
        emit_progress(3, 4, "Generating HTML report")
        html_generator = PbipHtmlGenerator()
        html_file_path = html_generator.generate_full_report(
            model_data=model_data,
            report_data=report_data,
            dependencies=dependencies,
            output_path=output_path,
            repository_name=repo_name,
            enhanced_results=result,
        )
        opened = False
        if args.get("auto_open") and html_file_path:
            try:
                import webbrowser

                opened = bool(webbrowser.open(Path(html_file_path).resolve().as_uri()))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"auto_open failed: {e}")

        emit_progress(4, 4, "Complete")
        return {
            "success": True,
            "output_format": "html",
            "html_report": html_file_path,
            "opened_in_browser": opened,
            "summary": summary,
            "message": f"HTML report generated: {html_file_path}",
        }

    except Exception as e:
        logger.error(f"Error in analyze operation: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


def _handle_query_dependencies(args: Dict[str, Any]) -> Dict[str, Any]:
    """Query dependency graph for a specific object."""
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    object_name = args.get("object_name")
    direction = args.get("direction", "both")  # "forward", "reverse", "both"

    try:
        data = _cache.get_or_parse(pbip_path)  # A-13: original path
        deps = data.dependencies

        result: Dict[str, Any] = {"success": True}

        if object_name:
            m2m = deps.get("measure_to_measure", {})
            m2m_rev = deps.get("measure_to_measure_reverse", {})
            m2c = deps.get("measure_to_column", {})
            c2m = deps.get("column_to_measure", {})
            c2fp = deps.get("column_to_field_params", {})
            candidates = _resolve_object(object_name, data.model_data)
            if not candidates:
                return {
                    "success": False,
                    "error": f"object {object_name!r} not found in the model",
                    "error_type": "not_found",
                }
            if len(candidates) > 1:
                return {
                    "success": False,
                    "error_type": "ambiguous",
                    "error": f"{object_name!r} matches several objects; qualify it as Table[Name]",
                    "candidates": [f"{t}[{n}]" for t, n, _k in candidates],
                }
            table, name, kind = candidates[0]
            resolved_key = f"{table}[{name}]"
            result.update({"object": resolved_key, "object_type": kind})
            reasons = (deps.get("usage_reasons") or {}).get(
                " ".join(resolved_key.lower().split()), []
            )
            if kind == "measure":
                if direction in ("forward", "both"):
                    result["depends_on_measures"] = m2m.get(resolved_key, [])
                    result["depends_on_columns"] = m2c.get(resolved_key, [])
                if direction in ("reverse", "both"):
                    result["referenced_by"] = m2m_rev.get(resolved_key, [])
            else:
                if direction in ("reverse", "both"):
                    result["referenced_by"] = c2m.get(resolved_key, [])
                    result["referenced_by_field_parameters"] = c2fp.get(resolved_key, [])
                if direction in ("forward", "both"):
                    # a calculated column's own references
                    result["depends_on_columns"] = [
                        c for c, refs in c2m.items() if resolved_key in refs
                    ]
            result["used_by"] = reasons
        else:
            # Return summary statistics
            m2m = deps.get("measure_to_measure", {})
            m2c = deps.get("measure_to_column", {})
            unused = deps.get("unused_columns", [])
            unused_measures = deps.get("unused_measures", [])

            result["summary"] = {
                "measures_with_dependencies": len(m2m),
                "measures_with_column_refs": len(m2c),
                "unused_columns": len(unused) if isinstance(unused, list) else 0,
                "unused_measures": len(unused_measures) if isinstance(unused_measures, list) else 0,
            }
            # Include the full maps for detailed inspection
            result["measure_to_measure"] = m2m
            result["measure_to_column"] = m2c

        return result

    except Exception as e:
        logger.error(f"Error querying dependencies: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


def _handle_query_measures(args: Dict[str, Any]) -> Dict[str, Any]:
    """List/search measures with filtering."""
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    table_filter = args.get("table")
    folder_filter = args.get("display_folder")
    name_pattern = args.get("pattern")
    expr_search = args.get("expression_search")

    try:
        data = _cache.get_or_parse(pbip_path)  # A-13: original path
        model = data.model_data

        measures = []
        for table in model.get("tables", []):
            table_name = table.get("name", "")

            if table_filter and table_filter.lower() != table_name.lower():
                continue

            for m in table.get("measures", []):
                m_name = m.get("name") or ""
                m_folder = m.get("display_folder") or ""
                m_expr = m.get("expression") or ""

                if folder_filter and not m_folder.lower().startswith(folder_filter.lower()):
                    continue

                if name_pattern:
                    try:
                        if not re.search(name_pattern, m_name, re.IGNORECASE):
                            continue
                    except re.error:
                        if name_pattern.lower() not in m_name.lower():
                            continue

                if expr_search:
                    try:
                        if not re.search(expr_search, m_expr, re.IGNORECASE):
                            continue
                    except re.error:
                        if expr_search.lower() not in m_expr.lower():
                            continue

                measures.append(
                    {
                        "table": table_name,
                        "name": m_name,
                        "expression": m.get("expression", ""),
                        "display_folder": m_folder,
                        "format_string": m.get("format_string", ""),
                        "is_hidden": m.get("is_hidden", False),
                        "description": m.get("description", ""),
                    }
                )

        return {
            "success": True,
            "total": len(measures),
            "measures": measures,
        }

    except Exception as e:
        logger.error(f"Error querying measures: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


def _handle_query_relationships(args: Dict[str, Any]) -> Dict[str, Any]:
    """List relationships with quality analysis."""
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    try:
        data = _cache.get_or_parse(pbip_path)  # A-13: original path
        model = data.model_data

        relationships = model.get("relationships", [])

        # Compute quality indicators
        issues = []
        bidirectional = []
        inactive = []
        many_to_many = []

        for rel in relationships:
            # Check for many-to-many
            from_card = (rel.get("from_cardinality") or "").lower()
            to_card = (rel.get("to_cardinality") or "").lower()
            if from_card == "many" and to_card == "many":
                many_to_many.append(rel)
                issues.append(
                    f"Many-to-many: {rel.get('from_table', '?')} <-> {rel.get('to_table', '?')}"
                )

            # Check for bidirectional
            cross_filter = (rel.get("cross_filtering_behavior") or "").lower()
            if cross_filter in ("bothDirections", "bothdirections", "both"):
                bidirectional.append(rel)
                issues.append(
                    f"Bidirectional: {rel.get('from_table', '?')} <-> {rel.get('to_table', '?')}"
                )

            # Check for inactive
            if not rel.get("is_active", True):
                inactive.append(rel)

        return {
            "success": True,
            "total": len(relationships),
            "relationships": relationships,
            "quality": {
                "many_to_many_count": len(many_to_many),
                "bidirectional_count": len(bidirectional),
                "inactive_count": len(inactive),
                "issues": issues,
            },
        }

    except Exception as e:
        logger.error(f"Error querying relationships: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


def _handle_query_unused(args: Dict[str, Any]) -> Dict[str, Any]:
    """Find unused measures and columns (cached, across ALL reports in project).

    Uses PbipModelCache for fast repeated lookups. Always analyzes all reports
    found in the project. For scoped analysis (specific reports only), use
    05_Column_Usage_Mapping with operation=get_unused_columns_pbip instead.
    """
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    try:
        data = _cache.get_or_parse(pbip_path)  # A-13: original path
        deps = data.dependencies

        unused_cols = deps.get("unused_columns", [])
        unused_measures = deps.get("unused_measures", [])

        out = {
            "success": True,
            "unused_columns": unused_cols if isinstance(unused_cols, list) else [],
            "unused_columns_count": len(unused_cols) if isinstance(unused_cols, list) else 0,
            "unused_measures": unused_measures if isinstance(unused_measures, list) else [],
            "unused_measures_count": (
                len(unused_measures) if isinstance(unused_measures, list) else 0
            ),
            "unused_functions": deps.get("unused_functions", []),
            "coverage": deps.get("coverage", {}),
            "reports_analyzed": (deps.get("summary") or {}).get("reports_analyzed"),
        }
        if args.get("include_reasons"):
            out["usage_reasons"] = deps.get("usage_reasons", {})
        return out

    except Exception as e:
        logger.error(f"Error querying unused objects: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


def _handle_scan_broken_refs(args: Dict[str, Any]) -> Dict[str, Any]:
    """Scan visual.json files for broken field references against the model."""
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    try:
        from core.pbip.pbip_visual_validator import scan_broken_visual_references

        # Try to use cached model data for faster lookups (A-13: original path)
        tmdl_model = None
        try:
            data = _cache.get_or_parse(pbip_path)
            tmdl_model = data.model_data
        except Exception:
            pass  # Fall back to on-disk TMDL parsing inside the validator

        def _scan() -> Dict[str, Any]:
            return scan_broken_visual_references(pbip_path, tmdl_model=tmdl_model)

        # P-10: the scan re-reads every visual.json (0.8 s per 1,000 visuals); an
        # unchanged report + model returns the memoised (identical) result.
        trees = _scan_trees(pbip_path)
        if not trees:
            return _scan()
        return pbip_cache.memoize_by_trees("scan_broken_refs", str(pbip_path), trees, _scan)

    except Exception as e:
        logger.error(f"Error scanning broken references: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


def _scan_trees(pbip_path: str) -> List[str]:
    """Every folder ``scan_broken_visual_references`` reads: report + model definitions."""
    try:
        from core.pbip.pbir_model import resolve_pbip

        res = resolve_pbip(pbip_path)
        trees: List[str] = []
        for r in res.get("reports") or []:
            if r.get("definition_dir"):
                trees.append(str(r["definition_dir"]))
            md = r.get("model_dir")
            if md:
                defn = os.path.join(md, "definition")
                trees.append(defn if os.path.isdir(defn) else str(md))
        return trees
    except Exception as e:  # noqa: BLE001 -- no memo, the scan reports its own errors
        logger.debug(f"scan trees unavailable for {pbip_path}: {e}")
        return []


def _handle_validate_model(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run TMDL validation on a PBIP folder."""
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    try:
        from core.tmdl.validator import TmdlValidator

        definition_path = _model_definition_dir(pbip_path)
        if not definition_path:
            return error_envelope(
                "not_found",
                "Could not find a semantic model definition/ folder for this path",
            )

        tom_mode = str(args.get("tom_check") or "auto")

        def _validate():
            return TmdlValidator({"tom_check": tom_mode}).validate_syntax(definition_path)

        # P-10: unchanged TMDL -> the same verdict (parse + TOM check) without re-running it.
        result = pbip_cache.memoize_by_trees(
            "validate_model", (definition_path, tom_mode), [definition_path], _validate
        )

        def row(e):
            return {
                "file": e.file,
                "line": e.line,
                "code": e.code,
                "message": e.message,
                "severity": e.severity.value,
            }

        return {
            "success": True,
            "definition_path": definition_path,
            "validation_result": {
                "is_valid": result.is_valid,
                "errors": [row(e) for e in result.errors],
                "warnings": [row(w) for w in result.warnings],
                "error_count": len(result.errors),
                "warning_count": len(result.warnings),
                "tom_check": result.tom_check,
            },
            "note": (
                "A 'compatibility level 1702 is below ... 1000000 needed for function' message "
                "is the TOM client library's UDF gate, not a model defect (tom_check.udf_gate)."
            ),
        }

    except ImportError:
        return error_envelope("manager_unavailable", "TmdlValidator not available")
    except Exception as e:
        logger.error(f"Error validating model: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


def _handle_validate_report(args: Dict[str, Any]) -> Dict[str, Any]:
    """F-06: read-only report + model validation (``preflight level=load|commit``).

    One op for "will Desktop open this, and is it safe to commit": JSON validity, required
    fields, byPath binding, page / bookmark / interaction cross-references, resource
    packages, inverted-selection polarity, TMDL block structure + the TOM parse, plus (when
    installed) the READ-ONLY ``powerbi-report-author validate``. Never writes anything.
    """
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")
    level = str(args.get("level") or "load").lower()
    if level not in ("load", "commit"):
        return error_envelope(
            "invalid_argument", f"level must be 'load' or 'commit', got {level!r}"
        )
    tom_mode = str(args.get("tom_check") or "auto").lower()
    use_cli = args.get("report_author_cli")
    use_cli = (
        True if use_cli is None else str(use_cli).strip().lower() not in ("false", "0", "no", "off")
    )
    try:
        from core.pbip.preflight import run_preflight

        emit_progress(1, 2, f"Preflight ({level})")
        result = run_preflight(
            str(pbip_path),
            level,
            tom_check=tom_mode,
            report_author=use_cli,
            git_base=args.get("git_base"),
            include_unstaged=str(args.get("include_unstaged", False)).strip().lower()
            in ("true", "1", "yes", "on"),
            max_findings=_preflight_cap(args),
            examples_per_code=1 if _concise(args) else 3,
        )
        emit_progress(2, 2, "Complete")
        if result.get("success") is False:
            return result
        result["operation"] = args.get("operation") or "validate_report"
        return result
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error in preflight: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


def _handle_compare_models(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two PBIP projects offline (F-17: the ONE compare engine, ``compare_targets``).

    ``source_path`` (old) vs ``target_path`` (new); ``from_ref`` / ``to_ref`` compare that side as
    committed at a git ref instead of the working tree (``from_ref=HEAD`` + the same path on both
    sides = "what changed since the last commit"). Keys: ``comparison`` (the differ's result with
    ``has_changes``), plus ``summary``, ``affects_results`` / ``cosmetic`` (the split a deploy
    pre-check wants), ``sources`` and ``warnings``.
    """
    source_path = args.get("source_path")
    target_path = args.get("target_path")

    if not source_path or not target_path:
        return error_envelope("missing_argument", "Both source_path and target_path are required")

    def side(path: str, ref: Any) -> Dict[str, Any]:
        return {"git_ref": str(ref), "pbip_path": path} if ref else {"pbip_path": path}

    try:
        from core.comparison.model_comparison_orchestrator import ModelComparisonOrchestrator

        return ModelComparisonOrchestrator().compare_targets(
            side(source_path, args.get("from_ref")),
            side(target_path, args.get("to_ref")),
            report=False,
            include_diff=True,
        )

    except ImportError:
        return error_envelope("manager_unavailable", "core.comparison is not available")
    except Exception as e:
        logger.error(f"Error comparing models: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


def _handle_generate_documentation(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate markdown documentation from PBIP metadata."""
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    output_path = args.get("output_path")

    try:
        data = _cache.get_or_parse(pbip_path)  # A-13: original path
        model = data.model_data
        deps = data.dependencies

        md = _generate_markdown(model, deps)

        # Save to file if output_path specified
        if output_path:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
            return {"success": True, "file": str(out), "message": f"Documentation saved to {out}"}

        # No output_path: write to the default exports dir and return the path,
        # NOT the full markdown inline (audit M5 — avoids tens of KB of tokens).
        server_root = Path(__file__).parent.parent.parent
        default_exports_path = server_root / "exports"
        default_exports_path.mkdir(exist_ok=True)
        # derive a stable filename from the model/project name if available, else a fixed name
        model_name = _project_name(pbip_path, model) or "model"
        safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(model_name))[:60]
        out = default_exports_path / f"{safe}_documentation.md"
        out.write_text(md, encoding="utf-8")
        return {
            "success": True,
            "file": str(out),
            "message": f"Documentation written to {out}",
            "summary": md[:500],
            "length_chars": len(md),
        }

    except Exception as e:
        logger.error(f"Error generating documentation: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


def _handle_git_diff(args: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze git changes to PBIP project with semantic understanding."""
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    from_ref = args.get("from_ref")
    to_ref = args.get("to_ref")
    base_branch = args.get("base_branch")

    try:
        from core.pbip.pbip_git_analyzer import PbipGitAnalyzer

        path = _normalize_pbip_path(pbip_path)
        # G-15: scope the diff to the named report/model (not the whole repo)
        analyzer = PbipGitAnalyzer(path, pathspecs=_git_pathspecs(pbip_path))

        if base_branch:
            # PR summary mode
            result = analyzer.summarize_pr_changes(base_branch)
        elif from_ref:
            # Commit diff mode
            result = analyzer.analyze_commit_diff(
                from_ref=from_ref,
                to_ref=to_ref or "HEAD",
            )
        else:
            # Working changes mode (default)
            result = analyzer.analyze_working_changes()

        return {"success": True, **result}

    except ValueError as ve:
        return error_envelope("operation_failed", str(ve))
    except Exception as e:
        logger.error(f"Error in git diff analysis: {e}", exc_info=True)
        return error_envelope("operation_failed", str(e))


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


def _model_definition_dir(pbip_path: str):
    """The TMDL definition folder of the model *pbip_path* resolves to."""
    try:
        from core.pbip.pbir_model import resolve_pbip

        res = resolve_pbip(pbip_path)
        for m in res.get("models") or []:
            d = m.get("definition_dir") or os.path.join(m.get("model_dir", ""), "definition")
            if d and os.path.isdir(d):
                return str(d)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"resolve_pbip failed for {pbip_path}: {e}")
    p = Path(_normalize_pbip_path(pbip_path))
    for cand in (p / "definition", p):
        if (cand / "model.tmdl").is_file() or (cand / "tables").is_dir():
            return str(cand)
    return None


def _git_pathspecs(pbip_path: str):
    """Report + model folders (+ .pbip) *pbip_path* resolves to; None = the folder."""
    try:
        from core.pbip.pbir_model import resolve_pbip

        res = resolve_pbip(pbip_path)
    except Exception:  # noqa: BLE001
        return None
    if res.get("target") in ("folder", "none"):
        return None
    specs = [r["report_dir"] for r in res.get("reports") or [] if r.get("report_dir")]
    specs += [m["model_dir"] for m in res.get("models") or [] if m.get("model_dir")]
    if res.get("pbip_file"):
        specs.append(res["pbip_file"])
    return specs or None


def _project_name(pbip_path: str, model: Any) -> str:
    """Stable per-project file stem: model folder / database name / path stem."""
    if isinstance(model, dict):
        folder = model.get("model_folder")
        if folder:
            stem = os.path.basename(str(folder).rstrip("/\\"))
            if stem.lower().endswith(".semanticmodel"):
                stem = stem[: -len(".semanticmodel")]
            if stem:
                return stem
        db = model.get("database") or {}
        if isinstance(db, dict) and db.get("name"):
            return str(db["name"])
    stem = Path(str(pbip_path)).stem or os.path.basename(str(pbip_path).rstrip("/\\"))
    return stem


def _resolve_object(object_name: str, model: Dict[str, Any]) -> List[tuple]:
    """``[(table, name, 'measure'|'column')]`` for ``T[N]`` / ``'T'[N]`` / ``[N]`` / ``N``
    (case-insensitive)."""
    from core.dax.dax_names import split_qualified, unbracket

    text = (object_name or "").strip()
    if "[" in text and not text.startswith("["):
        table, name = split_qualified(text)
    else:
        table, name = "", unbracket(text)

    def n(x: Any) -> str:
        return " ".join(str(x or "").lower().split())

    out = []
    for t in model.get("tables", []) or []:
        tn = t.get("name", "")
        if table and n(tn) != n(table):
            continue
        for m in t.get("measures", []) or []:
            if n(m.get("name")) == n(name):
                out.append((tn, m.get("name"), "measure"))
        for c in t.get("columns", []) or []:
            if n(c.get("name")) == n(name):
                out.append((tn, c.get("name"), "column"))
    return out


def _analysis_summary(
    model: Dict[str, Any], deps: Dict[str, Any], enhanced: Dict[str, Any], project: Any
) -> Dict[str, Any]:
    tables = model.get("tables", []) or []
    summary = {
        "tables": len(tables),
        "measures": sum(len(t.get("measures", []) or []) for t in tables),
        "columns": sum(len(t.get("columns", []) or []) for t in tables),
        "relationships": len(model.get("relationships", []) or []),
        "functions": len(model.get("functions", []) or []),
        "unused_measures": len(deps.get("unused_measures", []) or []),
        "unused_columns": len(deps.get("unused_columns", []) or []),
        "coverage": deps.get("coverage", {}),
        "reports_analyzed": (deps.get("summary") or {}).get("reports_analyzed"),
    }
    bpa = (enhanced or {}).get("analyses", {}).get("bpa") if isinstance(enhanced, dict) else None
    if isinstance(bpa, dict):
        summary["bpa"] = {
            k: bpa.get(k) for k in ("ran", "violations_total", "summary", "error") if k in bpa
        }
    warnings = (
        (getattr(project, "project_info", None) or {}).get("warnings")
        if project is not None
        else None
    )
    if warnings:
        summary["warnings"] = warnings
    return summary


# ═════════════════════════════════════════════════════════════════════
# Markdown documentation generator
# ═════════════════════════════════════════════════════════════════════


def _generate_markdown(model: Dict[str, Any], deps: Dict[str, Any]) -> str:
    """Generate comprehensive markdown documentation."""
    lines: List[str] = []

    # Header
    db_name = ""
    db = model.get("database")
    if isinstance(db, dict):
        db_name = db.get("name", "")
    lines.append(f"# {db_name or 'Power BI Model'} Documentation\n")

    # Overview
    tables = model.get("tables", [])
    rels = model.get("relationships", [])
    total_cols = sum(len(t.get("columns", [])) for t in tables)
    total_measures = sum(len(t.get("measures", [])) for t in tables)

    lines.append("## Overview\n")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Tables | {len(tables)} |")
    lines.append(f"| Columns | {total_cols} |")
    lines.append(f"| Measures | {total_measures} |")
    lines.append(f"| Relationships | {len(rels)} |")
    lines.append("")

    # Tables
    lines.append("## Tables\n")
    for table in sorted(tables, key=lambda t: t.get("name", "")):
        name = table.get("name", "")
        desc = table.get("description", "")
        hidden = " *(hidden)*" if table.get("is_hidden") else ""
        cols = table.get("columns", [])
        measures = table.get("measures", [])

        lines.append(f"### {name}{hidden}\n")
        if desc:
            lines.append(f"{desc}\n")

        # Columns
        if cols:
            lines.append("#### Columns\n")
            lines.append("| Name | Type | Key | Hidden |")
            lines.append("|------|------|-----|--------|")
            for c in cols:
                key = "Yes" if c.get("is_key") else ""
                hidden_c = "Yes" if c.get("is_hidden") else ""
                dtype = c.get("data_type", "")
                lines.append(f"| {c.get('name', '')} | {dtype} | {key} | {hidden_c} |")
            lines.append("")

        # Measures
        if measures:
            lines.append("#### Measures\n")
            for m in measures:
                m_name = m.get("name", "")
                expr = m.get("expression", "")
                folder = m.get("display_folder", "")
                desc_m = m.get("description", "")
                fmt = m.get("format_string", "")

                lines.append(f"**{m_name}**")
                if folder:
                    lines.append(f"  - Folder: `{folder}`")
                if desc_m:
                    lines.append(f"  - {desc_m}")
                if fmt:
                    lines.append(f"  - Format: `{fmt}`")
                if expr:
                    lines.append(f"  ```dax\n  {expr}\n  ```")
                lines.append("")

    # Relationships (Mermaid diagram)
    if rels:
        lines.append("## Relationships\n")
        lines.append("```mermaid")
        lines.append("erDiagram")
        for r in rels:
            from_t = r.get("from_table", r.get("fromTable", "?"))
            to_t = r.get("to_table", r.get("toTable", "?"))
            from_c = r.get("from_column_name", r.get("fromColumn", ""))
            to_c = r.get("to_column_name", r.get("toColumn", ""))
            card = r.get("from_cardinality", "many")
            to_card = r.get("to_cardinality", "one")

            # Mermaid cardinality notation
            left = "||" if card and "one" in str(card).lower() else "}o"
            right = "||" if to_card and "one" in str(to_card).lower() else "o{"
            active = "--" if r.get("is_active", True) else ".."

            # Sanitize names for Mermaid (no spaces)
            safe_from = from_t.replace(" ", "_") if from_t else "Unknown"
            safe_to = to_t.replace(" ", "_") if to_t else "Unknown"

            lines.append(f'    {safe_from} {left}{active}{right} {safe_to} : "{from_c} -> {to_c}"')
        lines.append("```\n")

        # Relationship table
        lines.append("| From | To | Cardinality | Active | Cross-Filter |")
        lines.append("|------|-----|-------------|--------|-------------|")
        for r in rels:
            from_t = r.get("from_table", r.get("fromTable", "?"))
            from_c = r.get("from_column_name", r.get("fromColumn", ""))
            to_t = r.get("to_table", r.get("toTable", "?"))
            to_c = r.get("to_column_name", r.get("toColumn", ""))
            active = "Yes" if r.get("is_active", True) else "No"
            cross = r.get("cross_filtering_behavior", "")
            lines.append(
                f"| {from_t}.{from_c} | {to_t}.{to_c} | {r.get('from_cardinality', '?')}-to-{r.get('to_cardinality', '?')} | {active} | {cross} |"
            )
        lines.append("")

    # Unused objects
    unused_cols = deps.get("unused_columns", [])
    unused_measures = deps.get("unused_measures", [])
    if unused_cols or unused_measures:
        lines.append("## Unused Objects\n")
        if unused_cols:
            lines.append(f"### Unused Columns ({len(unused_cols)})\n")
            for c in unused_cols[:50]:  # Limit to 50
                lines.append(f"- {c}")
            if len(unused_cols) > 50:
                lines.append(f"- ... and {len(unused_cols) - 50} more")
            lines.append("")
        if unused_measures:
            lines.append(f"### Unused Measures ({len(unused_measures)})\n")
            for m in unused_measures[:50]:
                lines.append(f"- {m}")
            if len(unused_measures) > 50:
                lines.append(f"- ... and {len(unused_measures) - 50} more")
            lines.append("")

    # Expressions
    expressions = model.get("expressions", [])
    if expressions and not (len(expressions) == 1 and "content" in expressions[0]):
        lines.append("## Shared Expressions (M Parameters)\n")
        for expr in expressions:
            if isinstance(expr, dict) and "name" in expr:
                lines.append(f"**{expr['name']}**")
                if expr.get("expression"):
                    lines.append(f"```m\n{expr['expression']}\n```")
                lines.append("")

    # Roles
    roles = model.get("roles", [])
    if roles:
        lines.append("## Security Roles\n")
        for role in roles:
            r_name = role.get("name", "")
            lines.append(f"### {r_name}\n")
            perms = role.get("table_permissions", [])
            if perms:
                for tp in perms:
                    lines.append(
                        f"- **{tp.get('table', '')}**: `{tp.get('filter_expression', 'N/A')}`"
                    )
            lines.append("")

    lines.append("---\n*Generated by MCP-PowerBi-Finvision*\n")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════
# Registration
# ═════════════════════════════════════════════════════════════════════


def handle_pbip_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Unified dispatcher for all offline PBIP operations."""
    operation = args.get("operation")
    if not operation:
        return error_envelope("missing_argument", "operation parameter is required")

    # Model analysis operations
    analysis_ops = {
        "analyze": _handle_analyze,
        "validate_model": _handle_validate_model,
        "compare_models": _handle_compare_models,
        "generate_documentation": _handle_generate_documentation,
        "validate_report": _handle_validate_report,
        "preflight": _handle_validate_report,
    }
    # Query operations
    query_ops = {
        "query_dependencies": _handle_query_dependencies,
        "query_measures": _handle_query_measures,
        "query_relationships": _handle_query_relationships,
        "query_unused": _handle_query_unused,
        "scan_broken_refs": _handle_scan_broken_refs,
        "git_diff": _handle_git_diff,
    }

    all_ops = {**analysis_ops, **query_ops}
    handler = all_ops.get(operation)
    if handler:
        return handler(args)

    # Delegated operations (absorbed from standalone tools)
    if operation == "dependency_html":
        from server.handlers._internal.hybrid_analysis_helpers import (
            handle_generate_pbip_dependency_diagram,
        )

        # Bridge: pbip_path -> pbip_folder_path expected by the original handler
        delegated = dict(args)
        if "pbip_path" in delegated and "pbip_folder_path" not in delegated:
            delegated["pbip_folder_path"] = delegated["pbip_path"]
        emit_progress(1, 3, "Parsing PBIP + building dependency graph")
        result = handle_generate_pbip_dependency_diagram(delegated)
        emit_progress(3, 3, "Dependency HTML generated")
        return result
    elif operation == "aggregation_analysis":
        from server.handlers._internal.aggregation_helpers import handle_aggregation_analysis

        emit_progress(1, 2, "Running aggregation analysis")
        result = handle_aggregation_analysis(args)
        emit_progress(2, 2, "Complete")
        return result

    valid = ", ".join(list(all_ops) + ["dependency_html", "aggregation_analysis"])
    return error_envelope(
        "invalid_operation",
        f"Unknown operation: {operation}. Valid: {valid}",
    )


def register_pbip_operations_handler(registry):
    """Register unified PBIP operations handler."""
    tool = ToolDefinition(
        name="07_PBIP_Operations",
        description=(
            "Offline PBIP analysis (no connection): analyze, validate_model, compare_models, "
            "validate_report (alias preflight), generate_documentation, query_dependencies, query_measures, query_relationships, "
            "query_unused, scan_broken_refs, git_diff, dependency_html, aggregation_analysis. "
            "The HTML MODEL REPORT is operation=analyze with output_format=html (+ auto_open) "
            "- NOT dependency_html (a dependency browser) and NOT generate_documentation "
            "(Markdown). Queryable graph: 16_Dependency_Graph."
        ),
        guide_notes=(
            "Offline PBIP analysis and queries (no live connection needed):"
            " analyze, validate_model, compare_models, generate_documentation,"
            " validate_report (= preflight level=load|commit: read-only 'will Desktop open this"
            " / safe to commit' - JSON, required fields, byPath, pages/bookmarks/interactions,"
            " resource packages, In.Values polarity, TMDL + TOM parse; findings carry"
            " load_blocking + repair_hint; level=commit adds the git staged-file scope),"
            " query_dependencies, query_measures, query_relationships,"
            " query_unused (cached, all reports in project),"
            " scan_broken_refs, git_diff,"
            " dependency_html, aggregation_analysis."
            " The HTML MODEL REPORT is operation=analyze with"
            " output_format=html (add auto_open=true) — NOT dependency_html"
            " (that is only an interactive dependency browser) and NOT"
            " generate_documentation (that writes a Markdown file)."
            " output_format=json|summary|detailed on analyze writes/returns the analysis as JSON."
            " For structured queryable graph (JSON, JSONPath, get_upstream/"
            "downstream/find_unused/find_cycles/get_impact) use 16_Dependency_Graph."
        ),
        handler=handle_pbip_operations,
        input_schema=TOOL_SCHEMAS["pbip_operations"],
        category="pbip",
        sort_order=70,
        # G-21: analyze / generate_documentation / dependency_html WRITE report
        # files under exports/ (never into the PBIP project) -> not read-only.
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    registry.register(tool)
    logger.info("Registered unified pbip_operations handler")


def _concise(args: Dict[str, Any]) -> bool:
    return str(args.get("response_format") or "").lower() == "concise"


def _preflight_cap(args: Dict[str, Any]) -> int:
    """Inline findings cap for validate_report: explicit max_findings, else 10 in concise mode
    and 200 otherwise (by_code always carries every code with its count)."""
    raw = args.get("max_findings")
    if raw is not None:
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    return 10 if _concise(args) else 200
