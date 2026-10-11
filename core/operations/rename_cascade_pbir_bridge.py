"""Bridge between live-model rename ops (TOM) and PBIR JSON cascade.

When a measure / column / table is renamed in the live model, EVERY report
bound to that model needs the same rename applied across visual.json,
page.json, report.json, filters.json, reportExtensions.json and bookmark JSON.

H-13 (deep review 2026-09-22): the user's layout is thin reports bound to a
shared model through ``definition.pbir datasetReference.byPath``. The old
resolver picked ``sorted(glob('*.Report'))[0]`` (R0200 instead of
R0201..R0203) or, for the model's own ``.pbip``, nothing at all. The bridge
now resolves the path with ``pbir_model.resolve_pbip``, takes the semantic
model(s) it names, and cascades into ``find_reports_bound_to_model`` for each
-- all bound reports, with a per-report result.

PBI Desktop overwrites PBIR files on save, so this should be invoked while
PBI is closed, OR with the caller aware that the result needs a reload to
land. The result includes ``reload_required: True`` + ``reload_op_chain``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.pbip.cascade_rename import CascadeRenameEngine

logger = logging.getLogger(__name__)


_VALID_KINDS = {"measure", "column", "table", "hierarchy"}


def resolve_bound_reports(pbip_path: str) -> Tuple[List[Path], Optional[str], List[str]]:
    """``(report_dirs, error, warnings)`` for a rename cascade.

    * ``.pbip`` / ``.Report`` / ``.SemanticModel`` / project folder accepted.
    * Every report bound (``byPath``) to the resolved model(s) is returned,
      not just the one named by the path: a model rename breaks all of them.
    * A path that resolves to more than one semantic model is ambiguous
      (``error`` set) -- the caller must name the model's project.
    """
    from core.pbip.pbir_model import find_reports_bound_to_model, resolve_pbip

    res = resolve_pbip(pbip_path)
    warnings = list(res.get("warnings") or [])
    models = res.get("models") or []
    reports: List[Path] = []
    if len(models) > 1:
        names = ", ".join(m["name"] for m in models)
        return (
            [],
            (
                f"'{pbip_path}' resolves to {len(models)} semantic models ({names}); pass the "
                "model's .pbip / .SemanticModel folder or one report's .pbip."
            ),
            warnings,
        )
    if models:
        for r in find_reports_bound_to_model(models[0]["model_dir"]):
            rp = Path(r)
            if (rp / "definition").is_dir() and rp not in reports:
                reports.append(rp)
    for r in res.get("reports") or []:
        rp = Path(r["report_dir"])
        # A report bound to ANOTHER model -- or live-connected (byConnection,
        # no model_dir) -- is not affected by a rename in this model.
        if models and (
            not r.get("model_dir") or Path(r["model_dir"]) != Path(models[0]["model_dir"])
        ):
            continue
        if (rp / "definition").is_dir() and rp not in reports:
            reports.append(rp)
    return reports, None, warnings


def apply_pbir_cascade(
    pbip_path: str,
    kind: str,
    old_name: str,
    new_name: str,
    table_name: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Apply one typed rename across every report bound to the renamed model.

    Args:
        pbip_path: a .pbip file, .Report / .SemanticModel folder or project folder.
        kind: 'measure' | 'column' | 'table' | 'hierarchy'.
        old_name, new_name: name strings (no brackets).
        table_name: owning table of a column / measure / hierarchy -- scopes
            the rename to that table (a column rename no longer touches
            same-named columns of other tables). Ignored for 'table'.
        dry_run: if True, count replacements but write nothing.

    Returns the aggregated legacy keys (``success``, ``files_modified``,
    ``replacements``, ``errors``, ``report_path`` = first report) plus
    ``report_paths`` and ``reports`` (one result per bound report).
    """
    if kind not in _VALID_KINDS:
        return {
            "success": False,
            "error": f"Unsupported kind '{kind}'. Use one of {sorted(_VALID_KINDS)}.",
        }
    if not old_name or not new_name:
        return {"success": False, "error": "old_name and new_name are required"}

    reports, err, warnings = resolve_bound_reports(pbip_path)
    if err:
        return {"success": False, "error": err, "error_type": "ambiguous_model"}
    if not reports:
        return {
            "success": False,
            "error": f"No .Report folder found for '{pbip_path}' (no report is bound to it)",
            "warnings": warnings,
        }

    per_report: List[Dict[str, Any]] = []
    total_files = 0
    total_repl = 0
    errors: List[str] = []
    deneb: List[str] = []
    for report in reports:
        engine = CascadeRenameEngine(str(report))
        raw = engine.apply_renames_typed(
            {kind: {old_name: new_name}},
            dry_run=dry_run,
            table_name=None if kind == "table" else table_name,
        )
        total_files += int(raw.get("files_modified", 0) or 0)
        total_repl += int(raw.get("replacements", 0) or 0)
        errors.extend(raw.get("errors") or [])
        deneb.extend(raw.get("deneb_specs_mentioning_old_name") or [])
        per_report.append(
            {
                "report_path": str(report),
                "success": bool(raw.get("success")),
                "files_modified": raw.get("files_modified", 0),
                "replacements": raw.get("replacements", 0),
                "changed_files": raw.get("changed_files", []),
                "errors": raw.get("errors", []),
            }
        )

    result: Dict[str, Any] = {
        "success": all(r["success"] for r in per_report),
        "files_modified": total_files,
        "replacements": total_repl,
        "errors": errors,
        "dry_run": dry_run,
        "kind": kind,
        "table_name": table_name,
        "report_path": str(reports[0]),
        "report_paths": [str(r) for r in reports],
        "reports": per_report,
    }
    if table_name is None and kind in ("column", "hierarchy"):
        result["warning"] = (
            "table_name not given: the rename was applied to same-named "
            f"{kind}s of EVERY table. Pass table_name to scope it."
        )
    if deneb:
        result["deneb_specs_mentioning_old_name"] = deneb[:50]
    if warnings:
        result["warnings"] = warnings
    result["reload_required"] = bool(total_files) and not dry_run
    if result["reload_required"]:
        result["reload_op_chain"] = ["17_Desktop_Bridge:reload_and_screenshot"]
    return result
