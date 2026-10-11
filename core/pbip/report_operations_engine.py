"""
Report Operations Engine for PBIP Reports

Report-level operations: rename, rebind to a different semantic model,
backup/restore, and schema discovery for visual types and data roles.

No MCP awareness — pure domain logic operating on PBIP file structures.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from core.pbip.authoring import safe_io
from core.utilities.pbip_utils import load_json_file

logger = logging.getLogger(__name__)


def _report_folder_and_pbip(pbip_path: str) -> Any:
    """``(report_folder, pbip_file_or_None, error)`` for a .pbip / .Report / definition path."""
    from core.pbip.pbir_model import resolve_pbip

    path = Path(pbip_path)
    if path.is_dir() and path.name == "definition" and path.parent.name.endswith(".Report"):
        path = path.parent
    if path.is_dir() and path.name.endswith(".Report"):
        report_folder = path
        candidates = sorted(report_folder.parent.glob("*.pbip"))
        pbip_file = None
        for cand in candidates:
            data = load_json_file(cand) or {}
            for art in data.get("artifacts") or []:
                rel = ((art or {}).get("report") or {}).get("path")
                if rel and (cand.parent / rel).resolve() == report_folder.resolve():
                    pbip_file = cand
        if pbip_file is None:
            same = report_folder.parent / f"{report_folder.name[: -len('.Report')]}.pbip"
            pbip_file = same if same.exists() else None
        return report_folder, pbip_file, None
    if path.is_file() and path.suffix.lower() == ".pbip":
        res = resolve_pbip(path)
        reports = [r for r in res.get("reports") or [] if r.get("pbip_file")]
        if res.get("target") == "report" and len(res.get("reports") or []) == 1:
            return Path(res["reports"][0]["report_dir"]), path, None
        if reports:
            return Path(reports[0]["report_dir"]), path, None
        return (
            None,
            None,
            {
                "success": False,
                "error": f"{path.name} names no report (it is a model-only .pbip)",
            },
        )
    return (
        None,
        None,
        {
            "success": False,
            "error": f"Expected .pbip file or .Report folder, got: {path}",
        },
    )


def rename_report(pbip_path: str, new_name: str) -> Dict[str, Any]:
    """Rename a report: the .Report folder, its .pbip, and every pointer to them (H-23).

    Updates ``.platform`` ``metadata.displayName`` and rewrites
    ``artifacts[].report.path`` in every sibling ``.pbip`` that pointed at the
    old folder (the renamed report's own .pbip AND e.g. a model-only .pbip
    naming it) so the project still opens. ``definition.pbir`` ``byPath`` is
    relative to the unchanged parent folder and stays valid.
    """
    if not new_name or not new_name.strip():
        return {"success": False, "error": "New name is required"}
    if not safe_io.is_safe_segment(new_name):
        return {"success": False, "error": f"Invalid report name: {new_name!r}"}
    report_folder, pbip_file, err = _report_folder_and_pbip(pbip_path)
    if err:
        return err
    if not report_folder.exists():
        return {"success": False, "error": f"Report folder not found: {report_folder}"}
    old_name = report_folder.name[: -len(".Report")]
    new_report_folder = report_folder.parent / f"{new_name}.Report"
    if new_report_folder.exists():
        return {"success": False, "error": f"Target folder already exists: {new_report_folder}"}
    new_pbip_file = pbip_file.parent / f"{new_name}.pbip" if pbip_file else None
    if new_pbip_file is not None and new_pbip_file.exists():
        return {"success": False, "error": f"Target .pbip already exists: {new_pbip_file}"}
    safe_io.allow_root(report_folder.parent)

    pointer_files = []
    for cand in sorted(report_folder.parent.glob("*.pbip")):
        data = load_json_file(cand) or {}
        for art in data.get("artifacts") or []:
            rel = ((art or {}).get("report") or {}).get("path")
            if rel and (cand.parent / rel).resolve() == report_folder.resolve():
                pointer_files.append(cand)
                break

    safe_io.move_path(report_folder, new_report_folder)
    renamed_pbip = None
    if pbip_file is not None and pbip_file.exists() and pbip_file.stem == old_name:
        safe_io.move_path(pbip_file, new_pbip_file)
        renamed_pbip = new_pbip_file
        pointer_files = [new_pbip_file if p == pbip_file else p for p in pointer_files]

    def _repoint(data: Dict[str, Any]) -> None:
        for art in data.get("artifacts") or []:
            rep = (art or {}).get("report")
            if isinstance(rep, dict) and rep.get("path"):
                if Path(rep["path"]).name == report_folder.name:
                    rep["path"] = str(Path(rep["path"]).with_name(new_report_folder.name)).replace(
                        "\\", "/"
                    )

    for pf in pointer_files:
        target = pf
        if safe_io.dry_run_active() and renamed_pbip is not None and pf == renamed_pbip:
            target = pbip_file  # not really moved in a dry run
        if target is not None and target.exists():
            safe_io.update_json(target, _repoint)

    platform_path = new_report_folder / ".platform"
    if platform_path.exists():

        def _platform(data: Dict[str, Any]) -> None:
            data.setdefault("metadata", {})["displayName"] = new_name

        safe_io.update_json(platform_path, _platform)

    return {
        "success": True,
        "old_name": old_name,
        "new_name": new_name,
        "report_folder": str(new_report_folder),
        "pbip_file": str(renamed_pbip) if renamed_pbip else None,
        "pbip_pointers_updated": [str(p) for p in pointer_files],
    }


def rebind_report(
    definition_path: Path,
    model_path: str = None,
    model_id: str = None,
) -> Dict[str, Any]:
    """Rebind report to a different semantic model. Updates ``<report>/definition.pbir``.

    H-23: the file lives in the ``.Report`` folder (``definition/``'s parent),
    not inside ``definition/``. ``model_path`` (``byPath``, relative to the
    report folder) must point at an existing ``.SemanticModel`` folder;
    ``model_id`` writes ``byConnection`` in the shape of the file's schema
    (``definitionProperties/2.x`` = ``{"connectionString": ...}`` only).
    """
    if not model_path and not model_id:
        return {"success": False, "error": "Either model_path or model_id must be provided"}
    definition_path = Path(definition_path)
    report_dir = definition_path.parent if definition_path.name == "definition" else definition_path
    pbir_path = report_dir / "definition.pbir"
    pbir_data = load_json_file(pbir_path)
    if not pbir_data:
        return {"success": False, "error": f"Could not read definition.pbir at {pbir_path}"}
    if model_path:
        target = (report_dir / model_path).resolve()
        if not target.is_dir():
            return {
                "success": False,
                "error_type": "model_not_found",
                "error": f"model_path does not resolve to a folder from {report_dir}: {model_path}",
            }
    old_ref = pbir_data.get("datasetReference", {}) or {}
    old_binding = {
        "path": (old_ref.get("byPath") or {}).get("path"),
        "id": (old_ref.get("byConnection") or {}).get("connectionString"),
    }
    schema = str(pbir_data.get("$schema", ""))
    modern = "definitionProperties/2" in schema or str(pbir_data.get("version", "")) >= "4"

    def _mutate(data: Dict[str, Any]) -> None:
        if model_path:
            data["datasetReference"] = {"byPath": {"path": str(model_path).replace("\\", "/")}}
        elif modern:
            data["datasetReference"] = {"byConnection": {"connectionString": model_id}}
        else:
            data["datasetReference"] = {
                "byConnection": {
                    "connectionString": model_id,
                    "pbiServiceModelId": None,
                    "pbiModelVirtualServerName": "sobe_wowvirtualserver",
                    "pbiModelDatabaseName": model_id,
                    "name": "EntityDataSource",
                    "connectionType": "pbiServiceXmlaStyleLive",
                }
            }

    safe_io.update_json(pbir_path, _mutate)
    return {
        "success": True,
        "definition_pbir": str(pbir_path),
        "old_binding": old_binding,
        "new_binding": {"path": model_path, "id": model_id},
    }


def backup_report(
    report_path: Path,
    message: str = None,
) -> Dict[str, Any]:
    """Create a timestamped backup copy of the .Report folder (sibling folder)."""
    report_path = Path(report_path)
    if not report_path.exists():
        return {"success": False, "error": f"Report folder not found: {report_path}"}
    if not report_path.is_dir():
        return {"success": False, "error": f"Expected a directory, got: {report_path}"}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = report_path.name.replace(".Report", "")
    backup_path = report_path.parent / f"{base_name}.Report.backup_{timestamp}"
    if backup_path.exists():
        return {"success": False, "error": f"Backup path already exists: {backup_path}"}
    safe_io.allow_root(report_path.parent)
    safe_io.copy_tree(report_path, backup_path)
    if message and not safe_io.dry_run_active():
        info = (
            f"Backup created: {datetime.now().isoformat()}\n"
            f"Source: {report_path}\n"
            f"Message: {message}\n"
        ).encode("utf-8")
        safe_io.write_bytes_minimal(backup_path / "backup_info.txt", info, b"")

    page_count = 0
    visual_count = 0
    pages_dir = report_path / "definition" / "pages"
    if pages_dir.exists():
        for page_folder in pages_dir.iterdir():
            if page_folder.is_dir():
                page_count += 1
                visuals_dir = page_folder / "visuals"
                if visuals_dir.exists():
                    visual_count += sum(1 for v in visuals_dir.iterdir() if v.is_dir())
    return {
        "success": True,
        "backup_path": str(backup_path),
        "source_path": str(report_path),
        "timestamp": timestamp,
        "message": message,
        "page_count": page_count,
        "visual_count": visual_count,
    }


def restore_report(
    report_path: Path,
    backup_path: str = None,
) -> Dict[str, Any]:
    """Restore report from backup (the current folder is kept aside until the copy lands)."""
    report_path = Path(report_path)
    if backup_path:
        source = Path(backup_path)
    else:
        base_name = report_path.name.replace(".Report", "")
        backups = sorted(
            report_path.parent.glob(f"{base_name}.Report.backup_*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not backups:
            return {
                "success": False,
                "error": f"No backups found for {base_name} in {report_path.parent}",
            }
        source = backups[0]
    if not source.exists():
        return {"success": False, "error": f"Backup not found: {source}"}
    if not source.is_dir():
        return {"success": False, "error": f"Expected a directory, got: {source}"}
    safe_io.allow_root(report_path.parent)
    if safe_io.dry_run_active():
        return {
            "success": True,
            "dry_run": True,
            "would_restore_from": str(source),
            "would_restore_to": str(report_path),
        }

    temp_path = None
    if report_path.exists():
        # P1-50: probe for a lock BEFORE the destructive rename.
        probe_path = report_path.with_name(report_path.name + ".__lock_probe")
        try:
            report_path.rename(probe_path)
            probe_path.rename(report_path)
        except OSError as e:
            return {
                "success": False,
                "error": (
                    f"Report folder is locked (likely open in Power BI Desktop): {e}. "
                    f"Close the report in PBI Desktop and retry."
                ),
                "error_type": "file_in_use",
            }
        temp_path = report_path.with_name(report_path.name + ".restore_tmp")
        if temp_path.exists():
            safe_io.remove_path(temp_path)
        safe_io.move_path(report_path, temp_path)
    try:
        safe_io.copy_tree(source, report_path)
    except safe_io.WriteError:
        if temp_path and temp_path.exists():
            if report_path.exists():
                safe_io.remove_path(report_path)
            safe_io.move_path(temp_path, report_path)
        raise
    if temp_path and temp_path.exists():
        safe_io.remove_path(temp_path)
    backup_info = report_path / "backup_info.txt"
    if backup_info.exists():
        safe_io.remove_path(backup_info)
    return {
        "success": True,
        "restored_from": str(source),
        "restored_to": str(report_path),
    }


def discover_schema(
    definition_path: Path,
    visual_type: str = None,
) -> Dict[str, Any]:
    """Discover valid properties and data roles for visual types.

    Returns visual type -> {buckets, formatting_properties} mapping built
    from the known template registry and visual container objects.

    Args:
        definition_path: Path to the report's definition/ folder (used for context)
        visual_type: Optional filter to a specific visual type

    Returns:
        Dict with success status and schema information
    """
    # Lazy import to avoid circular dependency at module load time
    from core.pbip.authoring.visual_templates import (
        VISUAL_TYPE_BUCKETS,
        TEMPLATE_REGISTRY,
        get_template_catalog,
    )

    if visual_type:
        if visual_type not in TEMPLATE_REGISTRY:
            available = sorted(set(TEMPLATE_REGISTRY.keys()))
            return {
                "success": False,
                "error": (
                    f"Unknown visual type: '{visual_type}'. " f"Available: {', '.join(available)}"
                ),
            }

        # Get details for a single visual type
        buckets = VISUAL_TYPE_BUCKETS.get(visual_type, {})

        # Extract formatting properties from the template
        template_fn = TEMPLATE_REGISTRY[visual_type]
        template = template_fn(None)
        formatting_props = _extract_formatting_properties(template)

        return {
            "success": True,
            "visual_type": visual_type,
            "buckets": buckets,
            "formatting_properties": formatting_props,
        }

    # Return catalog of all visual types
    catalog = get_template_catalog()
    return {
        "success": True,
        "visual_types": catalog,
        "total_types": len(catalog),
    }


def _extract_formatting_properties(template: Dict[str, Any]) -> Dict[str, List[str]]:
    """Extract available formatting property names from a visual template dict."""
    result: Dict[str, List[str]] = {}

    # Extract from visual.objects
    objects = template.get("visual", {}).get("objects", {})
    for obj_name, obj_list in objects.items():
        if isinstance(obj_list, list):
            props = []
            for entry in obj_list:
                if isinstance(entry, dict):
                    for key in entry.get("properties", {}).keys():
                        if key not in props:
                            props.append(key)
            if props:
                result[obj_name] = props

    # Extract from visual.visualContainerObjects
    vco = template.get("visual", {}).get("visualContainerObjects", {})
    for obj_name, obj_list in vco.items():
        key = f"container.{obj_name}"
        if isinstance(obj_list, list):
            props = []
            for entry in obj_list:
                if isinstance(entry, dict):
                    for k in entry.get("properties", {}).keys():
                        if k not in props:
                            props.append(k)
            if props:
                result[key] = props

    return result
