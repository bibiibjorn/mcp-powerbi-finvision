"""Relative-link formatters for per-type markdown files."""

from __future__ import annotations


def pbir_cli_ref(visual_type: str) -> str:
    """Anchor of the type's row in the pbir CLI visual type catalog (Part V of docs/GUIDE.md)."""
    return f"../GUIDE.md#cli-{visual_type.lower()}"


def pbir_json_ref(visual_type: str) -> str:
    """Anchor of the type's row in the PBIR JSON roles-per-type table (Part V of docs/GUIDE.md)."""
    return f"../GUIDE.md#json-{visual_type.lower()}"


# Removed r0101_page_catalog_link(): R0101_PAGE_CATALOG.md is no longer
# shipped with the repo. P0-70 audit fix. If a future regenerator step
# needs to back-link to a catalog, re-introduce here.


def shared_container(name: str) -> str:
    return f"./_shared/containers-{name}.md"


def shared_convention(filename: str) -> str:
    return f"./_shared/{filename}"


def skill_pointer(skill_id: str) -> str:
    return f"skill: {skill_id}"


def mcp_op(tool: str, operation: str) -> str:
    return f"MCP op: `{tool}:{operation}`"
