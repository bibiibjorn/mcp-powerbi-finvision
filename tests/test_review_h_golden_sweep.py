"""Deep review 2026-09-22, H-41 + A-06 — every PBIR write op on the golden fixture.

H-41: the old suite used synthetic fixtures that encoded the same invented
shapes as the code, so it stayed green while every engine wrote keys Desktop
does not read. This sweep runs EVERY write operation of the six authoring
tools, through the tool handlers, against a copy of the Desktop-shaped golden
report (``tests/fixtures/pbir_golden``) and then checks the whole report
against the real-PBIR invariants below.

A-06: the same matrix with ``dry_run=true`` must leave the tree byte-identical
(every op of these tools honours dry_run through the gate's write scope) and
classify as a READ.

Invariants checked after each real write (``_check_report``):
* every ``*.json`` parses; no stray ``definition/pages.json``;
* ``pages/pages.json`` ``pageOrder`` / ``activePageName`` / ``landingPageName``
  name existing pages;
* ``page.json``: string ``displayOption`` / ``visibility`` / ``type``, no
  legacy ``interactions`` key, every ``visualInteractions`` endpoint exists;
* ``visual.json``: no ``visual.filters``, no top-level
  ``visualContainerObjects``, no ``visual.sort``, no ``vCO.action``, no
  ``objects.denebConfig``, ``isHidden`` never inside ``visualGroup``, every
  ``parentGroupName`` exists, no ``In.Values: []`` anywhere;
* ``bookmarks.json``: group ``children`` are strings naming existing files.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
O = "aa01overview00000001"  # noqa: E741
D = "ab02details000000002"
T = "ac03tooltip000000003"
GROUP = "bg00000000000000grp1"
A1, A2, A3 = "bm0000000000000000a1", "bm0000000000000000a2", "bm0000000000000000a3"


def _prepare(root: Path) -> Path:
    shutil.copytree(GOLDEN, root)
    rep = root / "RPT-Sales.Report"
    theme_dir = rep / "StaticResources" / "RegisteredResources"
    theme_dir.mkdir(parents=True)
    (theme_dir / "Golden.json").write_text(
        json.dumps({"name": "Golden", "dataColors": ["#111111"], "visualStyles": {}}),
        encoding="utf-8",
    )
    rj = rep / "definition" / "report.json"
    data = json.loads(rj.read_text(encoding="utf-8"))
    data["themeCollection"]["customTheme"] = {"name": "Golden.json", "type": "RegisteredResources"}
    rj.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # a real CF value + column widths on the matrix (copy_cf / remove_cf /
    # sync_column_widths need something to act on)
    vj = rep / "definition" / "pages" / O / "visuals" / "vm01matrixfilters001" / "visual.json"
    m = json.loads(vj.read_text(encoding="utf-8"))
    m["visual"]["objects"]["values"] = [
        {
            "properties": {
                "backColor": {
                    "solid": {
                        "color": {
                            "expr": {
                                "FillRule": {
                                    "Input": {
                                        "Measure": {
                                            "Expression": {"SourceRef": {"Entity": "Sales"}},
                                            "Property": "Total Amount",
                                        }
                                    },
                                    "FillRule": {
                                        "linearGradient2": {
                                            "min": {"color": {"Literal": {"Value": "'#FFFFFF'"}}},
                                            "max": {"color": {"Literal": {"Value": "'#118DFF'"}}},
                                        }
                                    },
                                }
                            }
                        }
                    }
                }
            },
            "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]},
        }
    ]
    m["visual"]["objects"]["columnWidth"] = [
        {
            "properties": {"value": {"expr": {"Literal": {"Value": "120D"}}}},
            "selector": {"metadata": "Sales.Total Amount"},
        }
    ]
    vj.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    (root / "import-theme.json").write_text(
        json.dumps({"name": "Imported", "dataColors": ["#222222"]}), encoding="utf-8"
    )
    return rep


def _handler(tool: str):
    if tool == "07_Page_Operations":
        from server.handlers.page_operations_handler import handle_page_operations as h
    elif tool == "07_Visual_Operations":
        from server.handlers.visual_operations_handler import handle_visual_operations as h
    elif tool == "07_Bookmark_Operations":
        from server.handlers.bookmark_operations_handler import handle_bookmark_operations as h
    elif tool == "07_Theme_Operations":
        from server.handlers.theme_operations_handler import handle_theme_operations as h
    elif tool == "07_Report_Operations":
        from server.handlers.report_operations_handler import handle_report_operations as h
    else:
        from server.handlers.authoring_handler import handle_pbip_authoring as h
    return h


P, V, B, TH, R, A = (
    "07_Page_Operations",
    "07_Visual_Operations",
    "07_Bookmark_Operations",
    "07_Theme_Operations",
    "07_Report_Operations",
    "11_PBIP_Authoring",
)

#: (tool, args) for EVERY write operation of the six tools.
OPS: List[Tuple[str, Dict[str, Any]]] = [
    # --- 07_Page_Operations
    (P, {"operation": "create", "page_name": "Sweep"}),
    (P, {"operation": "clone", "source_page": O}),
    (P, {"operation": "reorder", "page_order": [T, O, D]}),
    (P, {"operation": "resize", "page_name": "Overview", "width": 1300, "height": 720}),
    (P, {"operation": "set_display", "page_name": "Overview", "display_option": "ActualSize"}),
    (P, {"operation": "set_background", "page_name": "Overview", "color": "#101010"}),
    (P, {"operation": "set_wallpaper", "page_name": "Overview", "color": "#202020"}),
    (
        P,
        {
            "operation": "set_drillthrough",
            "page_name": "Overview",
            "table": "Product Line",
            "field": "Region",
        },
    ),
    (P, {"operation": "set_tooltip", "page_name": "Overview"}),
    (P, {"operation": "hide", "page_name": "Overview"}),
    (P, {"operation": "show", "page_name": "Details"}),
    (
        P,
        {
            "operation": "set_interaction",
            "page_name": "Overview",
            "source_visual": "vs01slicerinverted01",
            "target_visual": "vm01matrixfilters001",
            "interaction_type": "NoFilter",
        },
    ),
    (
        P,
        {
            "operation": "bulk_set_interactions",
            "page_name": "Overview",
            "interactions": [
                {
                    "source": "vm01matrixfilters001",
                    "target": "vc01cardvisual000001",
                    "type": "NoFilter",
                }
            ],
        },
    ),
    (
        P,
        {
            "operation": "add_filter",
            "level": "page",
            "page_name": "Details",
            "table": "Sales",
            "field": "Amount",
            "filter_type": "Advanced",
            "operator": "GreaterThan",
            "values": [10],
        },
    ),
    (P, {"operation": "set_filter", "filter_name": "pf01category00000001", "values": ["Bikes"]}),
    (P, {"operation": "hide_filter", "filter_name": "pf01category00000001"}),
    (P, {"operation": "unhide_filter", "filter_name": "pf01category00000001"}),
    (P, {"operation": "lock_filter", "filter_name": "pf01category00000001"}),
    (P, {"operation": "unlock_filter", "filter_name": "pf01category00000001"}),
    (P, {"operation": "delete", "page_name": "Amount Tooltip"}),
    (P, {"operation": "clear_filters", "level": "page", "page_name": "Overview"}),
    # --- 07_Visual_Operations
    (
        V,
        {
            "operation": "create",
            "page_name": "Overview",
            "visual_type": "cardVisual",
            "measures": [{"table": "Sales", "measure": "Total Amount"}],
        },
    ),
    (V, {"operation": "create_group", "page_name": "Overview", "group_name": "G2"}),
    (V, {"operation": "delete", "page_id": O, "visual_id": "vc02cardlegacy000001"}),
    (V, {"operation": "hide", "page_name": "Overview", "visual_name": "vs01slicerinverted01"}),
    (V, {"operation": "unhide", "page_name": "Overview", "visual_name": "vs01slicerinverted01"}),
    (
        V,
        {
            "operation": "update_position",
            "page_name": "Overview",
            "visual_name": "vg02groupchild000001",
            "x": 860,
        },
    ),
    (
        V,
        {
            "operation": "update_visual_config",
            "page_name": "Overview",
            "visual_name": "vc01cardvisual000001",
            "config_type": "value",
            "property_name": "fontFamily",
            "property_value": "Segoe UI",
        },
    ),
    (
        V,
        {
            "operation": "update_formatting",
            "page_name": "Overview",
            "visual_name": "vc01cardvisual000001",
            "formatting_target": "title",
            "formatting_properties": {"text": "O'Neil"},
        },
    ),
    (
        V,
        {
            "operation": "align",
            "page_name": "Overview",
            "alignment": "top",
            "visual_names": ["vc01cardvisual000001", "vg02groupchild000001"],
        },
    ),
    (
        V,
        {
            "operation": "add_field",
            "page_name": "Overview",
            "visual_name": "vm01matrixfilters001",
            "table": "Product Line",
            "field": "Region",
            "bucket": "Rows",
        },
    ),
    (
        V,
        {
            "operation": "set_sort",
            "page_name": "Overview",
            "visual_name": "vm01matrixfilters001",
            "sort_field": "Product Line.Category",
            "sort_direction": "Ascending",
        },
    ),
    (
        V,
        {
            "operation": "set_action",
            "page_name": "Overview",
            "visual_name": "vb01actionbutton0001",
            "action_type": "PageNavigation",
            "action_target": "Details",
        },
    ),
    (
        V,
        {
            "operation": "inject_code",
            "page_name": "Overview",
            "visual_name": "vc02cardlegacy000001",
            "code_type": "deneb",
            "code": "{}",
            "provider": "vega",
        },
    ),
    (
        V,
        {
            "operation": "manage_visual_calcs",
            "sub_operation": "add",
            "page_name": "Overview",
            "visual_name": "vm01matrixfilters001",
            "calc_name": "C1",
            "expression": "[Total Amount]",
        },
    ),
    (
        V,
        {
            "operation": "configure_slicer",
            "page_name": "Overview",
            "visual_name": "vs01slicerinverted01",
        },
    ),
    (
        V,
        {
            "operation": "replace_measure",
            "page_name": "Overview",
            "visual_name": "vc01cardvisual000001",
            "source_entity": "Sales",
            "source_property": "Total Amount",
            "target_entity": "Sales",
            "target_property": "Amount Guarded",
        },
    ),
    (
        V,
        {
            "operation": "sync_visual",
            "source_visual_name": "vc01cardvisual000001",
            "source_page": "Overview",
            "target_pages": ["Details"],
            "sync_position": False,
        },
    ),
    (
        V,
        {
            "operation": "sync_column_widths",
            "source_visual_name": "vm01matrixfilters001",
            "source_page": "Overview",
            "target_pages": ["Details"],
        },
    ),
    (
        V,
        {
            "operation": "sync_formatting",
            "source_visual_name": "vc01cardvisual000001",
            "source_page": "Overview",
            "target_visual_name": "vc02cardlegacy000001",
            "target_page": "Overview",
        },
    ),
    (
        V,
        {
            "operation": "set_raw_property",
            "page_name": "Overview",
            "visual_name": "vc01cardvisual000001",
            "json_path": "visual.objects.value[0].properties.bold",
            "value": True,
            "wrap_literal": True,
        },
    ),
    (V, {"operation": "bulk_rename_pane_names", "page_name": "Overview"}),
    (
        V,
        {
            "operation": "clear_default_selection",
            "page_name": "Overview",
            "visual_name": "vs02slicervisinv0002",
        },
    ),
    (
        V,
        {
            "operation": "set_default_selection",
            "page_name": "Overview",
            "visual_name": "vs02slicervisinv0002",
            "values": ["EU"],
            "polarity": "include",
        },
    ),
    (
        V,
        {
            "operation": "delete_raw_property",
            "page_name": "Overview",
            "visual_name": "vc01cardvisual000001",
            "json_path": "visual.objects.layout",
        },
    ),
    (
        V,
        {
            "operation": "remove_field",
            "page_name": "Overview",
            "visual_name": "vm01matrixfilters001",
            "table": "m Metrics",
            "field": "Net Δ Value",
        },
    ),
    # --- 07_Bookmark_Operations
    (B, {"operation": "create", "display_name": "Sweep BM", "page_name": "Overview"}),
    (B, {"operation": "rename", "bookmark_id": A1, "new_name": "Renamed"}),
    (B, {"operation": "update", "bookmark_id": A1, "capture_data": False}),
    (B, {"operation": "set_capture", "bookmark_id": A2, "capture_display": False}),
    (
        B,
        {
            "operation": "set_affected_visuals",
            "bookmark_id": A2,
            "visual_ids": ["vc01cardvisual000001"],
        },
    ),
    (B, {"operation": "create_group", "display_name": "G", "bookmark_ids": [A3]}),
    (B, {"operation": "update_group", "group_id": GROUP, "bookmark_ids": [A1]}),
    (B, {"operation": "delete", "bookmark_id": A2}),
    (B, {"operation": "delete_group", "group_id": GROUP}),
    # --- 07_Theme_Operations
    (
        TH,
        {
            "operation": "set_formatting",
            "visual_type_target": "cardVisual",
            "formatting": {"value": {"fontSize": 12}},
        },
    ),
    (
        TH,
        {
            "operation": "push_visual",
            "page_name": "Overview",
            "visual_name": "vc01cardvisual000001",
        },
    ),
    (
        TH,
        {
            "operation": "set_font",
            "text_class": "title",
            "font_family": "Segoe UI",
            "font_size": 12,
        },
    ),
    (
        TH,
        {
            "operation": "add_cf",
            "page_name": "Overview",
            "visual_name": "vm01matrixfilters001",
            "container": "values",
            "property_name": "fontColor",
            "rule_type": "color_scale",
            "cf_config": {"table": "Sales", "field": "Total Amount"},
            "selector_metadata": "Sales.Total Amount",
        },
    ),
    (
        TH,
        {
            "operation": "copy_cf",
            "source_page": "Overview",
            "source_visual": "vm01matrixfilters001",
            "target_page": "Overview",
            "target_visual": "vc01cardvisual000001",
            "container": "values",
            "property_name": "backColor",
        },
    ),
    (TH, {"operation": "set_colors", "colors": {"background": "#FFFFFF"}}),
    (TH, {"operation": "apply_theme", "theme": {"name": "Applied", "dataColors": ["#333333"]}}),
    (TH, {"operation": "import_theme", "source": "<ROOT>/import-theme.json"}),
    (TH, {"operation": "clear_visual_overrides"}),
    (
        TH,
        {
            "operation": "remove_cf",
            "page_name": "Overview",
            "visual_name": "vm01matrixfilters001",
            "container": "values",
            "property_name": "backColor",
        },
    ),
    # --- 07_Report_Operations
    (R, {"operation": "rename", "new_name": "RPT-Sales2"}),
    (R, {"operation": "rebind", "model_path": "../MDL-Core.SemanticModel"}),
    (R, {"operation": "backup", "message": "sweep"}),
    (
        R,
        {
            "operation": "manage_extension_measures",
            "sub_operation": "add",
            "measure_name": "Ext",
            "expression": "1",
            "table_ref": "Sales",
        },
    ),
    # --- 11_PBIP_Authoring
    (A, {"operation": "clone_page", "source_page": O}),
    (A, {"operation": "create_page", "page_name": "Sweep 11"}),
    (A, {"operation": "create_visual", "page_name": "Overview", "visual_type": "textbox"}),
    (A, {"operation": "create_visual_group", "page_name": "Overview", "group_name": "G11"}),
    (A, {"operation": "clone_visual", "source_page": O, "visual_id": "vc01cardvisual000001"}),
    (
        A,
        {
            "operation": "pbir_add_visuals",
            "page": "Overview",
            "container_display_name": "KPI tile",
            "visuals": [
                {
                    "visual_type": "cardVisual",
                    "x": 850,
                    "y": 410,
                    "fields": {"Data": "Sales.Total Amount"},
                }
            ],
        },
    ),
    (A, {"operation": "clone_report", "target_path": "<ROOT>/copies/RPT-Clone.Report"}),
    (A, {"operation": "delete_page", "page_id": T}),
    (A, {"operation": "delete_visual", "page_id": O, "visual_id": "vc02cardlegacy000001"}),
    # LAST on purpose: it changes a display name other ops address pages by.
    (P, {"operation": "rename", "page_name": D, "new_display_name": "Details Renamed"}),
]


def _ids(ops):
    return [
        f"{t[:9]}:{a['operation']}" + (f"/{a['sub_operation']}" if "sub_operation" in a else "")
        for t, a in ops
    ]


def _call(root: Path, rep: Path, tool: str, args: Dict[str, Any], **extra):
    call = json.loads(json.dumps(args).replace("<ROOT>", str(root).replace("\\", "/")))
    call.setdefault("pbip_path", str(rep))
    call["confirm_destructive"] = True
    call["confirm_delete_filter_layer"] = call.get("confirm_delete_filter_layer", False)
    call.update(extra)
    return _handler(tool)(call)


def _tree_hash(root: Path) -> Dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_every_write_op_of_the_six_tools_is_in_the_sweep():
    from server.op_classification import _OP_TABLES

    covered = {(t, a["operation"]) for t, a in OPS}
    for tool in (P, V, B, TH, R, A):
        for op, level in _OP_TABLES[tool].items():
            if level == "read" or (tool, op) == (R, "restore"):
                continue  # restore: needs a backup -> covered below
            assert (tool, op) in covered, f"{tool}:{op} missing from the golden sweep"


def test_a06_dry_run_sweep_leaves_the_tree_byte_identical(tmp_path):
    from server.op_classification import classify

    root = tmp_path / "GoldenCo"
    rep = _prepare(root)
    before = _tree_hash(root)
    failures = []
    for tool, args in OPS:
        assert classify(tool, {**args, "dry_run": True}) == "read", (tool, args["operation"])
        res = _call(root, rep, tool, args, dry_run=True)
        if res.get("success") is False:
            failures.append((tool, args["operation"], res.get("error_type"), res.get("error")))
        assert res.get("files_modified", 0) == 0, (tool, args["operation"], res)
    assert _tree_hash(root) == before
    assert failures == [], failures


# ---------------------------------------------------------------------------
# Real writes: each op on its own copy, then the invariants
# ---------------------------------------------------------------------------


def _literal_values_empty(node: Any) -> bool:
    if isinstance(node, dict):
        inn = node.get("In")
        if isinstance(inn, dict) and "Values" in inn and inn["Values"] == []:
            return True
        return any(_literal_values_empty(v) for v in node.values())
    if isinstance(node, list):
        return any(_literal_values_empty(v) for v in node)
    return False


def _check_report(rep: Path) -> List[str]:
    problems: List[str] = []
    defn = rep / "definition"
    for f in rep.rglob("*.json"):
        try:
            json.loads(f.read_text(encoding="utf-8-sig"))
        except ValueError as e:
            problems.append(f"unparsable {f.relative_to(rep)}: {e}")
    if problems:
        return problems
    if (defn / "pages.json").exists():
        problems.append("stray definition/pages.json")
    pages_dir = defn / "pages"
    page_ids = {p.name for p in pages_dir.iterdir() if p.is_dir()}
    meta = json.loads((pages_dir / "pages.json").read_text(encoding="utf-8-sig"))
    for pid in meta.get("pageOrder") or []:
        if pid not in page_ids:
            problems.append(f"pageOrder names missing page {pid}")
    for key in ("activePageName", "landingPageName"):
        if meta.get(key) and meta[key] not in page_ids:
            problems.append(f"{key} names missing page {meta[key]}")
    for pid in page_ids:
        page = json.loads((pages_dir / pid / "page.json").read_text(encoding="utf-8-sig"))
        for key in ("displayOption", "visibility", "type"):
            if key in page and not isinstance(page[key], str):
                problems.append(f"{pid}: {key} is {page[key]!r} (must be a string enum)")
        if "interactions" in page:
            problems.append(f"{pid}: legacy 'interactions' key")
        vroot = pages_dir / pid / "visuals"
        vids = {v.name for v in vroot.iterdir()} if vroot.is_dir() else set()
        for i in page.get("visualInteractions") or []:
            if i.get("source") not in vids or i.get("target") not in vids:
                problems.append(f"{pid}: dangling interaction {i}")
        if _literal_values_empty(page):
            problems.append(f"{pid}: In.Values []")
        for vid in vids:
            vj = vroot / vid / "visual.json"
            if not vj.exists():
                problems.append(f"{pid}/{vid}: folder without visual.json")
                continue
            data = json.loads(vj.read_text(encoding="utf-8-sig"))
            visual = data.get("visual") or {}
            if "visualContainerObjects" in data:
                problems.append(f"{vid}: top-level visualContainerObjects")
            for bad in ("filters", "sort", "visualCalculations"):
                if bad in visual:
                    problems.append(f"{vid}: visual.{bad}")
            if "action" in (visual.get("visualContainerObjects") or {}):
                problems.append(f"{vid}: vCO.action")
            if "denebConfig" in (visual.get("objects") or {}):
                problems.append(f"{vid}: objects.denebConfig")
            if "isHidden" in (data.get("visualGroup") or {}):
                problems.append(f"{vid}: visualGroup.isHidden")
            if data.get("parentGroupName") and data["parentGroupName"] not in vids:
                problems.append(f"{vid}: dangling parentGroupName")
            if _literal_values_empty(data):
                problems.append(f"{vid}: In.Values []")
    bm_index = defn / "bookmarks" / "bookmarks.json"
    if bm_index.exists():
        idx = json.loads(bm_index.read_text(encoding="utf-8-sig"))
        for item in idx.get("items") or []:
            for child in item.get("children") or []:
                if not isinstance(child, str):
                    problems.append(f"bookmark child {child!r} is not a string")
                elif not (defn / "bookmarks" / f"{child}.bookmark.json").exists():
                    problems.append(f"bookmark child {child} has no file")
    return problems


@pytest.mark.parametrize("tool,args", OPS, ids=_ids(OPS))
def test_h41_write_op_keeps_real_pbir_invariants(tmp_path, tool, args):
    root = tmp_path / "GoldenCo"
    rep = _prepare(root)
    res = _call(root, rep, tool, args)
    assert res.get("success") is not False, res
    if args["operation"] == "rename" and tool == R:
        rep = root / "RPT-Sales2.Report"
    assert _check_report(rep) == []
    if args["operation"] == "clone_report":
        assert _check_report(root / "copies" / "RPT-Clone.Report") == []


def test_h41_restore_after_backup(tmp_path):
    root = tmp_path / "GoldenCo"
    rep = _prepare(root)
    res = _call(root, rep, R, {"operation": "backup", "message": "b"})
    assert res["success"], res
    backup_path = res.get("backup_path") or res.get("path")
    card = rep / "definition" / "pages" / O / "visuals" / "vc01cardvisual000001" / "visual.json"
    original = card.read_bytes()
    card.write_text("{}", encoding="utf-8")
    res = _call(root, rep, R, {"operation": "restore", "backup_path": backup_path})
    assert res["success"], res
    assert card.read_bytes() == original
    assert _check_report(rep) == []
