"""Walk a PBIR thin-report folder, index visuals, copy extracts."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class VisualInstance:
    page_id: str
    page_display_name: str
    visual_id: str
    visual_type: str
    position: Dict[str, float]
    parent_group_id: Optional[str]
    configured_containers: List[str]
    cf_present: bool
    role_bindings: Dict[str, int]
    visual_json_path: Path
    is_hidden: bool = False


@dataclass(frozen=True)
class GroupInstance:
    """A visualGroup container. Children's `position` is GROUP-LOCAL — resolve
    page-absolute coords by summing positions up the parent chain."""

    page_id: str
    group_id: str
    position: Dict[str, float]
    parent_group_id: Optional[str]
    is_hidden: bool = False


@dataclass
class ReportInventory:
    report_root: Path
    visuals: List[VisualInstance] = field(default_factory=list)
    groups: List[GroupInstance] = field(default_factory=list)


def _parse_page(page_dir: Path) -> tuple[str, str]:
    page_json = page_dir / "page.json"
    if not page_json.exists():
        return page_dir.name, page_dir.name
    data = json.loads(page_json.read_text(encoding="utf-8"))
    return data.get("name", page_dir.name), data.get("displayName", page_dir.name)


def _has_conditional_formatting(objects: dict) -> bool:
    text = json.dumps(objects)
    return "FillRule" in text or '"Conditional"' in text


def _position_of(data: dict) -> Dict[str, float]:
    pos = data.get("position", {}) or {}
    return {
        "x": float(pos.get("x", 0)),
        "y": float(pos.get("y", 0)),
        "width": float(pos.get("width", 0)),
        "height": float(pos.get("height", 0)),
    }


def _parse_group(data: dict, folder_name: str, page_id: str) -> Optional[GroupInstance]:
    if "visualGroup" not in data:
        return None
    return GroupInstance(
        page_id=page_id,
        group_id=data.get("name", folder_name),
        position=_position_of(data),
        parent_group_id=data.get("parentGroupName"),
        is_hidden=bool(data.get("isHidden", False)),
    )


def _parse_visual(visual_json: Path, page_id: str, page_display: str) -> Optional[VisualInstance]:
    try:
        data = json.loads(visual_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    visual_type = data.get("visual", {}).get("visualType")
    if not visual_type:
        return None

    objects = data.get("visual", {}).get("objects") or {}
    container_objects = data.get("visual", {}).get("visualContainerObjects") or {}
    configured = sorted(set(list(objects.keys()) + list(container_objects.keys())))

    role_bindings: Dict[str, int] = {}
    qstate = data.get("visual", {}).get("query", {}).get("queryState", {}) or {}
    for role, body in qstate.items():
        role_bindings[role] = len((body or {}).get("projections", []))

    return VisualInstance(
        page_id=page_id,
        page_display_name=page_display,
        visual_id=data.get("name", visual_json.parent.name),
        visual_type=visual_type,
        position=_position_of(data),
        parent_group_id=data.get("parentGroupName"),
        configured_containers=configured,
        cf_present=_has_conditional_formatting(objects),
        role_bindings=role_bindings,
        visual_json_path=visual_json,
        is_hidden=bool(data.get("isHidden", False)),
    )


def walk_report(report_root: Path) -> ReportInventory:
    report_root = Path(report_root)
    pages_dir = report_root / "definition" / "pages"
    if not pages_dir.exists():
        raise FileNotFoundError(f"No definition/pages under {report_root}")

    inventory = ReportInventory(report_root=report_root)
    for page_dir in sorted(pages_dir.iterdir()):
        if not page_dir.is_dir():
            continue
        page_id, page_display = _parse_page(page_dir)
        visuals_dir = page_dir / "visuals"
        if not visuals_dir.exists():
            continue
        for vdir in sorted(visuals_dir.iterdir()):
            if not vdir.is_dir():
                continue
            vjson = vdir / "visual.json"
            if not vjson.exists():
                continue
            instance = _parse_visual(vjson, page_id, page_display)
            if instance is not None:
                inventory.visuals.append(instance)
                continue
            # Not a visual — may be a visualGroup container (needed to resolve
            # children's group-local coords to page-absolute).
            try:
                data = json.loads(vjson.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            group = _parse_group(data, vdir.name, page_id)
            if group is not None:
                inventory.groups.append(group)
    return inventory


def group_by_type(inventory: ReportInventory) -> Dict[str, List[VisualInstance]]:
    grouped: Dict[str, List[VisualInstance]] = defaultdict(list)
    for v in inventory.visuals:
        grouped[v.visual_type].append(v)
    return dict(grouped)


def _slugify_page(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    return cleaned or "page"


def _infer_report_sha(report_root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(report_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "not-in-git"
    except FileNotFoundError:
        return "git-not-available"


def harvest_r0101(
    report_root: Path,
    extracts_root: Path,
    report_sha: Optional[str] = None,
) -> List[str]:
    """Copy every R0101 visual.json into extracts_root; write _metadata.json."""
    report_root = Path(report_root)
    extracts_root = Path(extracts_root)
    extracts_root.mkdir(parents=True, exist_ok=True)

    warnings: List[str] = []
    inventory = walk_report(report_root)
    sha = report_sha or _infer_report_sha(report_root)

    count = 0
    for inst in inventory.visuals:
        filename = f"{_slugify_page(inst.page_display_name)}__{inst.visual_id}.json"
        dest = extracts_root / filename
        try:
            shutil.copy2(inst.visual_json_path, dest)
            count += 1
        except OSError as e:
            warnings.append(f"copy failed {inst.visual_json_path} → {dest}: {e}")

    metadata = {
        "report_sha": sha,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "report_root": str(report_root),
        "visual_count": count,
        "types_seen": sorted({v.visual_type for v in inventory.visuals}),
    }
    (extracts_root / "_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return warnings
