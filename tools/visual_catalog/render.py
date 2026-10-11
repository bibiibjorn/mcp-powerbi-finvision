"""Render markdown for per-type files, shared containers, INDEX."""

from __future__ import annotations

import json
from typing import Iterable, List, Mapping, Tuple

from tools.visual_catalog import crosslinks as xl
from tools.visual_catalog.inventory import VisualInstance
from tools.visual_catalog.schema import ContainerSurface, PropertySpec, RolesSurface
from tools.visual_catalog.types import IN_SCOPE_TYPES

TemplatePick = Tuple[VisualInstance, str, bool]

PROPERTY_HEADER = (
    "| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |\n"
    "|----------|------|---------|------------------------|-----------------|-------|"
)

SHARED_CONTAINER_NAMES = {
    "title",
    "background",
    "border",
    "padding",
    "visualHeader",
    "general",
    "categoryAxis",
    "valueAxis",
    "legend",
    "dataLabels",
    "plotArea",
}


def _fence(slug: str, body: str = "") -> str:
    return (
        f"<!-- MANUAL: {slug} -->\n{body or 'TODO: fill in during enrichment.'}\n<!-- /MANUAL -->"
    )


def _fmt_default(value) -> str:
    return "—" if value is None else f"`{json.dumps(value, ensure_ascii=False)}`"


def _fmt_range(prop: PropertySpec) -> str:
    if not prop.allowed_values:
        return "—"
    return ", ".join(f"`{json.dumps(v, ensure_ascii=False)}`" for v in prop.allowed_values)


def _render_property_table(container: ContainerSurface) -> str:
    lines = [PROPERTY_HEADER]
    for p in sorted(container.properties, key=lambda x: x.name):
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{p.name}`",
                    f"`{p.type}`",
                    _fmt_default(p.default),
                    _fmt_range(p),
                    "yes" if p.selector_aware else "no",
                    (p.description or "").replace("|", "\\|"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _render_containers_section(containers: Iterable[ContainerSurface]) -> str:
    lines = []
    for c in containers:
        if c.container in SHARED_CONTAINER_NAMES:
            link = xl.shared_container(c.container)
            lines.append(
                f"- **{c.container}** ({len(c.properties)} properties) — see [{link}]({link})"
            )
        else:
            lines.append(
                f"- **{c.container}** ({len(c.properties)} properties) — documented inline below"
            )
    return "\n".join(lines)


def _render_properties_section(containers: Iterable[ContainerSurface]) -> str:
    blocks = []
    for c in containers:
        if c.container in SHARED_CONTAINER_NAMES:
            link = xl.shared_container(c.container)
            blocks.append(f"### {c.container}\n\nSee [{link}]({link}).")
        else:
            blocks.append(f"### {c.container}\n\n{_render_property_table(c)}")
    return "\n\n".join(blocks)


def _render_roles_table(roles: RolesSurface) -> str:
    lines = ["| Role | Required | Multiple | Accepts |", "|------|----------|----------|---------|"]
    for name in sorted(roles.roles):
        r = roles.roles[name]
        lines.append(
            f"| `{r.name}` | {'yes' if r.required else 'no'} | "
            f"{'yes' if r.multiple else 'no'} | {', '.join(r.accepts)} |"
        )
    return "\n".join(lines)


INLINE_JSON_LINE_BUDGET = 200


def _render_template_block(pick: TemplatePick) -> str:
    inst, raw_json, has_png = pick
    base = f"{inst.page_display_name}__{inst.visual_id}"
    shot = (
        f"Screenshot: ![{inst.visual_type}](./_extracts/{base}.png)"
        if has_png
        else "Screenshot: ⚠️ screenshot unavailable"
    )
    # Large visual.json extracts (pivotTable, tableEx, combo charts) blow past
    # the file-size budget. Inline only small ones; link out the rest.
    line_count = raw_json.count("\n") + 1
    if line_count > INLINE_JSON_LINE_BUDGET:
        json_block = (
            f"_Extract is {line_count} lines — see "
            f"[./_extracts/{base}.json](./_extracts/{base}.json)._"
        )
    else:
        json_block = f"```json\n{raw_json.rstrip()}\n```"
    return (
        f"### R0101 — {inst.page_display_name} (visual {inst.visual_id})\n\n"
        f"{shot}\n"
        f"Source: [./_extracts/{base}.json](./_extracts/{base}.json)\n\n"
        f"**Configuration highlights:**\n{_fence('per-template-notes')}\n\n"
        f"{json_block}"
    )


def render_type_file(
    *,
    visual_type: str,
    roles: RolesSurface,
    containers: List[ContainerSurface],
    picked_templates: List[TemplatePick],
    minimal_template_json: str,
) -> str:
    parts = [
        f"# {visual_type}",
        "",
        "## Overview",
        "",
        _fence("overview"),
        "",
        _fence("when-to-use"),
        "",
        "## Roles",
        "",
        _render_roles_table(roles),
        "",
        "## Containers",
        "",
        _render_containers_section(containers) or "_No containers documented by pbir schema._",
        "",
        "## Properties",
        "",
        _render_properties_section(containers) or "_No properties documented by pbir schema._",
        "",
        "## Conditional formatting",
        "",
        "See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).",
        "",
        "## Templates",
        "",
        "### Minimal",
        "",
        "```json",
        minimal_template_json.rstrip(),
        "```",
        "",
    ]
    for pick in picked_templates:
        parts.append(_render_template_block(pick))
        parts.append("")
    parts.extend(
        [
            "## Gotchas",
            "",
            _fence("gotchas"),
            "",
            "## Related",
            "",
            f"- {xl.mcp_op('11_PBIP_Authoring', 'pbir_add_visuals')} — canonical authoring path",
            f"- pbir CLI: [{xl.pbir_cli_ref(visual_type)}]({xl.pbir_cli_ref(visual_type)})",
            f"- JSON format: [{xl.pbir_json_ref(visual_type)}]({xl.pbir_json_ref(visual_type)})",
            f"- {xl.skill_pointer('reports:pbir-cli')}, {xl.skill_pointer('pbir-page-builder')}",
            # P0-70 fix: removed R0101_PAGE_CATALOG.md backlink — file is no longer
            # shipped. Use `07_Visual_Operations {operation: "list"}` to enumerate
            # in-report examples at runtime.
            "",
            _fence("related-extras"),
            "",
        ]
    )
    return "\n".join(parts)


def render_index(
    usage_counts: Mapping[str, int],
    roles_by_type: Mapping[str, RolesSurface],
) -> str:
    def _roles_summary(vt: str) -> str:
        r = roles_by_type.get(vt)
        if r is None or not r.roles:
            return "—"
        return ", ".join(f"`{n}`" for n in sorted(r.roles))

    lines = [
        "# Visual Catalog — Index",
        "",
        "Agent-first reference for every in-scope Power BI visual type.",
        "",
        "| Visual type | R0101 instances | Primary roles | File |",
        "|-------------|-----------------|---------------|------|",
    ]
    for t in IN_SCOPE_TYPES:
        lines.append(
            f"| `{t}` | {usage_counts.get(t, 0)} | {_roles_summary(t)} | [{t}.md](./{t}.md) |"
        )
    lines.extend(
        [
            "",
            "## Shared references",
            "",
            "- [containers-title.md](./_shared/containers-title.md)",
            "- [containers-background.md](./_shared/containers-background.md)",
            "- [containers-border.md](./_shared/containers-border.md)",
            "- [containers-padding.md](./_shared/containers-padding.md)",
            "- [containers-visualHeader.md](./_shared/containers-visualHeader.md)",
            "- [containers-general.md](./_shared/containers-general.md)",
            "- [containers-axes.md](./_shared/containers-axes.md)",
            "- [containers-legend.md](./_shared/containers-legend.md)",
            "- [containers-dataLabels.md](./_shared/containers-dataLabels.md)",
            "- [containers-plotArea.md](./_shared/containers-plotArea.md)",
            "- [conditional-formatting.md](./_shared/conditional-formatting.md)",
            "- [field-binding.md](./_shared/field-binding.md)",
            "- [selectors.md](./_shared/selectors.md)",
            "- [literals.md](./_shared/literals.md)",
            "",
        ]
    )
    return "\n".join(lines)


def render_shared_container(name: str, properties: List[PropertySpec]) -> str:
    lines = [
        f"# Shared container: {name}",
        "",
        "Documented once and linked from per-type files.",
        "",
        _fence("overview"),
        "",
        "## Properties",
        "",
    ]
    if properties:
        lines.append(PROPERTY_HEADER)
        for p in sorted(properties, key=lambda x: x.name):
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{p.name}`",
                        f"`{p.type}`",
                        "—",
                        "—",
                        "yes" if p.selector_aware else "no",
                        (p.description or "").replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("_No mechanically-extracted properties yet. Fill in during enrichment._")
    lines.extend(["", _fence("gotchas"), ""])
    return "\n".join(lines)


def render_shared_convention(slug: str) -> str:
    titles = {
        "conditional-formatting": "Conditional formatting — reference",
        "field-binding": "Field binding — roles, Entity/Property format, escaping",
        "selectors": "Selectors — per-series / per-category overrides",
        "literals": "Literal encoding — numbers, strings, colors",
    }
    title = titles.get(slug, slug)
    return "\n".join(
        [
            f"# {title}",
            "",
            _fence("overview"),
            "",
            _fence("when-to-use"),
            "",
            "## Examples",
            "",
            _fence("related-extras"),
            "",
        ]
    )
