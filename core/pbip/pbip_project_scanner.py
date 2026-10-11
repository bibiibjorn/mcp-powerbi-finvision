"""
PBIP Project Scanner - Scans repositories for Power BI Project files.

This module identifies semantic models and reports within a repository (or a
single project path) and links every report to the model it is bound to.

G-06: the old scanner only understood the Desktop "one .pbip + same-name
.Report + same-name .SemanticModel" layout. The real layout is thin reports
whose ``definition.pbir`` binds to a SHARED model via
``datasetReference.byPath`` (``R0201.Report -> ../S02-SemanticModel
.SemanticModel``), plus model-only ``.pbip`` files whose artifact names a
missing / differently-named report -- so it found zero models. Resolution now
follows the real pointer chain through ``core.pbip.pbir_model`` (the one
place that knows the PBIP shapes).
"""

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def project_info_from_resolution(res: Dict[str, Any], base_dir: str) -> Dict[str, Any]:
    """Convert a ``pbir_model.resolve_pbip`` result to the scanner's legacy shape.

    ``{"semantic_models": [...], "reports": [...], "target", "warnings"}``.
    Model entries keep the historic keys (``pbip_file``, ``name``, ``type``,
    ``relative_path``, ``model_folder``, ``definition_path``, ``has_tmdl``)
    plus ``reports`` (names of the reports bound to it). Report entries keep
    ``pbip_file``, ``name``, ``type``, ``relative_path``, ``report_folder``,
    ``linked_model`` (the raw ``byPath`` string) and ``linked_model_name``,
    plus ``linked_model_folder`` and ``dataset_reference``.
    """

    def rel(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        try:
            return os.path.relpath(path, base_dir)
        except ValueError:  # different drive
            return path

    reports_by_dir = {r["report_dir"]: r for r in res.get("reports", [])}
    models: List[Dict[str, Any]] = []
    model_names: Dict[str, str] = {}
    for m in res.get("models", []):
        model_names[os.path.normcase(m["model_dir"])] = m["name"]
        models.append(
            {
                "pbip_file": m["pbip_file"],
                "name": m["name"],
                "type": "SemanticModel",
                "relative_path": rel(m["pbip_file"] or m["model_dir"]),
                "model_folder": m["model_dir"],
                "definition_path": m["definition_dir"],
                "has_tmdl": m["format"] == "tmdl",
                "format": m["format"],
                "reports": [
                    reports_by_dir[d]["name"] for d in m["report_dirs"] if d in reports_by_dir
                ],
            }
        )
    reports: List[Dict[str, Any]] = []
    for r in res.get("reports", []):
        model_dir = r["model_dir"]
        linked_name = model_names.get(os.path.normcase(model_dir)) if model_dir else None
        entry: Dict[str, Any] = {
            "pbip_file": r["pbip_file"],
            "name": r["name"],
            "type": "Report",
            "relative_path": rel(r["pbip_file"] or r["report_dir"]),
            "report_folder": r["report_dir"],
            "linked_model": r["model_path"],
            "linked_model_folder": model_dir,
            "dataset_reference": r["dataset_reference"],
        }
        if linked_name:
            entry["linked_model_name"] = linked_name
        if r.get("note"):
            entry["note"] = r["note"]
        reports.append(entry)
    return {
        "semantic_models": models,
        "reports": reports,
        "target": res.get("target"),
        "warnings": list(res.get("warnings", [])),
    }
