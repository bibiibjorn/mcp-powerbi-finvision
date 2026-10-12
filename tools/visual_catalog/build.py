"""Top-level driver: harvest schema + R0101, render all markdown.

Not a CLI — import these functions and call directly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from tools.visual_catalog.fences import (
    InvalidManualSlugError,
    has_manual_blocks,
    merge_manual,
)
from tools.visual_catalog.inventory import harvest_r0101, walk_report, group_by_type
from tools.visual_catalog.picker import PICKER_OVERRIDES, pick_templates
from tools.visual_catalog.render import (
    render_index,
    render_shared_container,
    render_shared_convention,
    render_type_file,
    TemplatePick,
)
from tools.visual_catalog.schema import (
    harvest_schema,
    parse_containers_json,
    parse_describe_json,
    parse_roles_json,
    RolesSurface,
)
from tools.visual_catalog.types import IN_SCOPE_TYPES

SHARED_CONTAINERS = (
    "title",
    "background",
    "border",
    "padding",
    "visualHeader",
    "general",
    "axes",
    "legend",
    "dataLabels",
    "plotArea",
)
SHARED_CONVENTIONS = ("conditional-formatting", "field-binding", "selectors", "literals")


def _slugify_page(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "page"


def _load_schema(cache_root: Path, visual_type: str):
    base = cache_root / visual_type
    roles_path = base / "_roles.json"
    containers_path = base / "_containers.json"
    if not base.exists() or not roles_path.exists() or not containers_path.exists():
        return None, []
    roles = parse_roles_json(json.loads(roles_path.read_text(encoding="utf-8")))
    listing = parse_containers_json(json.loads(containers_path.read_text(encoding="utf-8")))
    containers = []
    for entry in listing.containers:
        p = base / f"{entry.name}.json"
        if not p.exists():
            continue
        containers.append(parse_describe_json(json.loads(p.read_text(encoding="utf-8"))))
    return roles, containers


def _write_if_changed(path: Path, new_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    if previous and has_manual_blocks(previous):
        new_text = merge_manual(regenerated=new_text, previous=previous)
    if previous != new_text:
        path.write_text(new_text, encoding="utf-8")


def _minimal_template(visual_type: str) -> str:
    return json.dumps(
        {"visual": {"visualType": visual_type, "query": {"queryState": {}}}},
        indent=2,
    )


def build_from_cached_schema(
    *,
    report_root: Path,
    out_dir: Path,
    only_types: Optional[Iterable[str]] = None,
) -> List[str]:
    """Render catalog from already-harvested schema cache + report extracts."""
    report_root = Path(report_root)
    out_dir = Path(out_dir)
    schema_cache = out_dir / "_cache" / "schema"
    extracts_root = out_dir / "_extracts"

    warnings: List[str] = []

    # 1. Harvest R0101 extracts (idempotent)
    warnings.extend(harvest_r0101(report_root=report_root, extracts_root=extracts_root))

    # 2. Inventory
    inventory = walk_report(report_root)
    grouped = group_by_type(inventory)

    # 3. Per-type files
    types = list(only_types) if only_types else list(IN_SCOPE_TYPES)
    roles_by_type: Dict[str, RolesSurface] = {}
    picked_basenames: set[str] = set()

    for t in types:
        roles, containers = _load_schema(schema_cache, t)
        if roles is None:
            warnings.append(f"no schema cache for {t}; skipping per-type file")
            continue
        roles_by_type[t] = roles

        instances = grouped.get(t, [])
        overrides = PICKER_OVERRIDES.get(t)
        picked = pick_templates(instances, cap=4, overrides=overrides)

        template_picks: List[TemplatePick] = []
        for inst in picked:
            base = f"{_slugify_page(inst.page_display_name)}__{inst.visual_id}"
            picked_basenames.add(base)
            jpath = extracts_root / f"{base}.json"
            ppath = extracts_root / f"{base}.png"
            raw_json = jpath.read_text(encoding="utf-8") if jpath.exists() else "{}"
            template_picks.append((inst, raw_json, ppath.exists()))

        md = render_type_file(
            visual_type=t,
            roles=roles,
            containers=containers,
            picked_templates=template_picks,
            minimal_template_json=_minimal_template(t),
        )
        try:
            _write_if_changed(out_dir / f"{t}.md", md)
        except InvalidManualSlugError as e:
            warnings.append(f"{t}: {e}")

    # 4. Shared files
    shared_dir = out_dir / "_shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    for name in SHARED_CONTAINERS:
        _write_if_changed(shared_dir / f"containers-{name}.md", render_shared_container(name, []))
    for conv in SHARED_CONVENTIONS:
        _write_if_changed(shared_dir / f"{conv}.md", render_shared_convention(conv))

    # 5. INDEX
    usage_counts = {t: len(grouped.get(t, [])) for t in IN_SCOPE_TYPES}
    _write_if_changed(out_dir / "INDEX.md", render_index(usage_counts, roles_by_type))

    # 6. Trim _extracts/ to picker-selected only. Full R0101 is large (~50MB
    # as raw visual.json copies); keeping only the referenced templates is
    # enough for per-type files and keeps the repo lean. Only trim when we
    # ran the full type set — partial runs (only_types=...) shouldn't delete
    # extracts for types we didn't process.
    if only_types is None:
        for p in extracts_root.glob("*.json"):
            if p.name == "_metadata.json":
                continue
            if p.stem not in picked_basenames:
                p.unlink()

    return warnings


def build_all(
    *,
    report_root: Path,
    out_dir: Path,
    only_types: Optional[Iterable[str]] = None,
) -> List[str]:
    """Harvest pbir schema to cache, then render everything."""
    out_dir = Path(out_dir)
    schema_cache = out_dir / "_cache" / "schema"
    harvest_warnings = harvest_schema(cache_root=schema_cache, only_types=only_types)
    render_warnings = build_from_cached_schema(
        report_root=report_root,
        out_dir=out_dir,
        only_types=only_types,
    )
    return harvest_warnings + render_warnings


def list_screenshots_needed(*, report_root: Path, out_dir: Path) -> List[dict]:
    """Return picker-selected R0101 instances that still need a PNG crop.

    Consumed by the agent driving 12_Autonomous_Workflow.
    """
    out_dir = Path(out_dir)
    extracts_root = out_dir / "_extracts"
    inventory = walk_report(report_root)
    grouped = group_by_type(inventory)

    needed: List[dict] = []
    for t in IN_SCOPE_TYPES:
        overrides = PICKER_OVERRIDES.get(t)
        for inst in pick_templates(grouped.get(t, []), cap=4, overrides=overrides):
            out_path = (
                extracts_root / f"{_slugify_page(inst.page_display_name)}__{inst.visual_id}.png"
            )
            if out_path.exists():
                continue
            needed.append(
                {
                    "page_display_name": inst.page_display_name,
                    "page_id": inst.page_id,
                    "visual_id": inst.visual_id,
                    "visual_type": inst.visual_type,
                    "x": inst.position["x"],
                    "y": inst.position["y"],
                    "width": inst.position["width"],
                    "height": inst.position["height"],
                    "output_path": str(out_path),
                }
            )
    return needed
