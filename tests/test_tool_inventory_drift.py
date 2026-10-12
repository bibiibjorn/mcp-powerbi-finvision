"""S-14: the tool reference (Part III of docs/GUIDE.md) and the live registry name the same operations,
both directions.

``tests/test_tool_reference_ops.py`` already proves *schema enum -> section mentions the word*
(a loose ``\\bop\\b`` search anywhere in the section). This file adds the two checks it cannot:

* **doc -> registry**: every operation a section's op table (or the 02 object_type table) lists as
  a row exists in the registry (a removed/renamed op cannot linger in the doc);
* **registry -> doc table**: every registry operation has a table ROW (not just a passing mention)
  in its tool's section, except those §III.28 lists as "added after the last full doc sync".

The inventory comes from ``tools/dump_tool_inventory.py`` (the same generator the skill-verify
ground truth uses), so the drift gate and the skills' ground truth cannot disagree.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from server.resources import GUIDE_RELATIVE_PATH, tool_reference_region

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / GUIDE_RELATIVE_PATH
TICK = re.compile(r"`([A-Za-z0-9_]+)`")


def _load_inventory_module():
    spec = importlib.util.spec_from_file_location(
        "dump_tool_inventory", REPO / "tools" / "dump_tool_inventory.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def inventory_module():
    return _load_inventory_module()


@pytest.fixture(scope="module")
def inventory(inventory_module):
    return inventory_module.build_inventory()["tools"]


@pytest.fixture(scope="module")
def sections():
    text = tool_reference_region(DOC.read_text(encoding="utf-8"))
    assert text, "docs/GUIDE.md has no tool-reference:start/end markers"
    parts = re.split(r"^## III\.[0-9a-z]+ `([^`]+)`", text, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def _table_rows(body: str, header_first_cell: str):
    """Yield the cell lists of the data rows of every markdown table whose first header cell is
    ``header_first_cell`` (after stripping backticks)."""
    in_table = False
    for line in body.splitlines():
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0].replace("`", "").strip() == header_first_cell:
            in_table = True
            continue
        if in_table and not set(cells[0]) <= set("-: "):
            yield cells


def _top_level_ops(cell: str):
    """First backticked token of each top-level (paren depth 0) comma segment of ``cell``."""
    depth, segment, segments = 0, "", []
    for ch in cell:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch in ",/" and depth == 0:
            segments.append(segment)
            segment = ""
        else:
            segment += ch
    segments.append(segment)
    ops = []
    for seg in segments:
        found = TICK.search(seg)
        if found and seg.strip().startswith("`"):
            ops.append(found.group(1))
    return ops


def _row_names(body: str):
    """Every backticked name in a row's first cell, aliases included (``validate_report (alias
    `preflight`)`` names both)."""
    names = set()
    for cells in _table_rows(body, "Operation"):
        names.update(TICK.findall(cells[0]))
    return names


def _documented_ops(tool: str, body: str):
    """Operations the section documents as table rows (names only, no params)."""
    ops = set()
    if tool == "02_Model_Operations":
        for cells in _table_rows(body, "object_type"):
            if len(cells) >= 2:
                ops.update(_top_level_ops(cells[1]))
        return ops
    for cells in _table_rows(body, "Operation"):
        ops.update(_top_level_ops(cells[0]))
    return ops


def _section_28_tokens(sections) -> set:
    """Names §III.28 explicitly lists as ops that are not (yet) folded into the tables."""
    body = next((b for k, b in sections.items() if k.startswith("III.28")), "")
    return set(TICK.findall(body))


def test_every_documented_op_row_exists_in_the_registry(inventory, sections, inventory_module):
    stale = {}
    for tool, body in sections.items():
        if tool not in inventory:
            continue
        known = inventory_module.all_op_names(inventory[tool])
        if not known:
            continue
        unknown = sorted(op for op in _documented_ops(tool, body) if op not in known)
        if unknown:
            stale[tool] = unknown
    assert not stale, f"tool-reference op rows with no registry operation: {stale}"


# Real doc gaps this test found: documented as prose under the table, no table row yet. Add the
# row, then delete the entry (the tests below fail on a stale entry so the list cannot rot).
KNOWN_PROSE_ONLY_OPS: dict = {}
KNOWN_PROSE_ONLY_OBJECT_TYPES: set = set()


def _ops_without_row(inventory, sections):
    missing = {}
    for tool, entry in inventory.items():
        enum = entry.get("ops") or []
        if tool == "02_Model_Operations" or not enum or tool not in sections:
            continue
        documented = _documented_ops(tool, sections[tool]) | _row_names(sections[tool])
        if not documented:
            continue  # prose-only section (e.g. 10_Show_User_Guide); the loose test covers it
        absent = {op for op in enum if op not in documented and op != "upsert"}
        if absent:
            missing[tool] = absent
    return missing


def test_every_registry_op_has_a_table_row(inventory, sections):
    missing = _ops_without_row(inventory, sections)
    unexpected = {
        t: sorted(ops - KNOWN_PROSE_ONLY_OPS.get(t, set()))
        for t, ops in missing.items()
        if ops - KNOWN_PROSE_ONLY_OPS.get(t, set())
    }
    assert (
        not unexpected
    ), f"registry operations with no row in their tool-reference table: {unexpected}"


def test_known_prose_only_ops_are_still_gaps(inventory, sections):
    missing = _ops_without_row(inventory, sections)
    healed = {
        t: sorted(ops - missing.get(t, set()))
        for t, ops in KNOWN_PROSE_ONLY_OPS.items()
        if ops - missing.get(t, set())
    }
    assert not healed, f"now have a table row - drop them from KNOWN_PROSE_ONLY_OPS: {healed}"


def _documented_object_types(sections):
    types = set()
    for cells in _table_rows(sections["02_Model_Operations"], "object_type"):
        types.update(re.findall(r"`([a-z_]+)`", cells[0]))
    return types


def test_model_operations_object_types_each_have_a_row(inventory, sections):
    documented_types = _documented_object_types(sections)
    enum = set(inventory["02_Model_Operations"]["object_types"] or [])
    assert sorted(enum - documented_types - KNOWN_PROSE_ONLY_OBJECT_TYPES) == []
    assert sorted(documented_types - enum) == []
    healed = KNOWN_PROSE_ONLY_OBJECT_TYPES & documented_types
    assert not healed, f"now have a table row - drop from KNOWN_PROSE_ONLY_OBJECT_TYPES: {healed}"


def test_inventory_covers_every_registered_tool(inventory, sections):
    assert sorted(set(inventory) - set(sections)) == []
