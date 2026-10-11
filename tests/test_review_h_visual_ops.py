"""Deep review 2026-09-22, slice H — 07_Visual_Operations on real PBIR shapes.

H-05  set_raw_property: a JSON-string value for an object node is parsed, a
      type change needs force_type, a missing intermediate with
      create_missing=false is an error (never a write at the wrong path).
H-14  real locations: update_formatting -> visual.visualContainerObjects,
      set_sort -> visual.query.sortDefinition, inject_code -> objects.vega,
      sync_formatting copies the chrome.
H-15  one literal encoder (strings quoted + '' doubled, no exponent floats).
H-16 / L-02  explicit targets: no selector = refused unless match_all,
      several matches = refused unless allow_multiple, page id accepted,
      never a substring page match for writes.
H-25  delete: exact id, filter-layer guard + hide alternative (top-level
      isHidden), children keep their page position, one severity for both
      tools; group template has groupMode and no nested isHidden.
H-26  create: per-type role defaults, role validation, parent_group checked.
H-30  an exact visual id loads one file (no page-wide scan).
H-31  align works in the page-absolute frame for grouped visuals.
H-32  replace_measure / remove_field also update sort + per-measure formatting.
H-37  an unclosed ``[`` in json_path is an error envelope.
H-35  visual calcs are NativeVisualCalculation projections.
L-05  the 07_Visual TOOL_EXAMPLES run against a fixture with those names.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
OVERVIEW = "aa01overview00000001"


@pytest.fixture
def report(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return root / "RPT-Sales.Report"


def _vj(report: Path, vid: str, page: str = OVERVIEW) -> Path:
    return report / "definition" / "pages" / page / "visuals" / vid / "visual.json"


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig"))


def _op(report: Path, **args):
    from server.handlers.visual_operations_handler import handle_visual_operations

    return handle_visual_operations({"pbip_path": str(report), **args})


def _lit(node) -> str:
    return node["expr"]["Literal"]["Value"]


# ---------------------------------------------------------------------------
# H-05 / H-37
# ---------------------------------------------------------------------------


def test_h05_json_string_value_is_parsed_for_object_nodes(report):
    res = _op(
        report,
        operation="set_raw_property",
        page_name="Overview",
        visual_name="vc01cardvisual000001",
        json_path="visual.objects.value",
        value='[{"properties": {}}]',
    )
    assert res["success"], res
    value = _read(_vj(report, "vc01cardvisual000001"))["visual"]["objects"]["value"]
    assert isinstance(value, list)


def test_h05_type_change_needs_force_type(report):
    vj = _vj(report, "vc01cardvisual000001")
    before = vj.read_bytes()
    res = _op(
        report,
        operation="set_raw_property",
        page_name="Overview",
        visual_name="vc01cardvisual000001",
        json_path="visual.objects.value",
        value="plain text",
    )
    assert res["success"] is False
    assert vj.read_bytes() == before


def test_h05_missing_intermediate_without_create_missing_is_an_error(report):
    vj = _vj(report, "vc01cardvisual000001")
    before = vj.read_bytes()
    res = _op(
        report,
        operation="set_raw_property",
        page_name="Overview",
        visual_name="vc01cardvisual000001",
        json_path="visual.objects.legend[0].properties.show",
        value={"expr": 1},
        create_missing=False,
    )
    assert res["success"] is False
    assert "legend" in json.dumps(res["errors"])
    assert vj.read_bytes() == before


def test_h37_unclosed_bracket_is_an_envelope(report):
    res = _op(
        report,
        operation="set_raw_property",
        page_name="Overview",
        visual_name="vc01cardvisual000001",
        json_path="visual.objects[",
        value=1,
    )
    assert res["success"] is False
    assert res["error_type"] == "invalid_json_path"


# ---------------------------------------------------------------------------
# H-14 / H-15
# ---------------------------------------------------------------------------


def test_h14_h15_update_formatting_writes_visual_vco_with_quoted_literals(report):
    res = _op(
        report,
        operation="update_formatting",
        page_name="Overview",
        visual_name="vc01cardvisual000001",
        formatting_target="title",
        formatting_properties={"text": "Client's NAV YTD", "fontSize": 12},
    )
    assert res["success"], res
    data = _read(_vj(report, "vc01cardvisual000001"))
    assert "visualContainerObjects" not in data  # never top-level
    props = data["visual"]["visualContainerObjects"]["title"][0]["properties"]
    assert _lit(props["text"]) == "'Client''s NAV YTD'"
    assert _lit(props["fontSize"]) == "12D"


def test_h15_update_visual_config_quotes_strings_and_keeps_file(report):
    res = _op(
        report,
        operation="update_visual_config",
        page_name="Overview",
        visual_name="vc01cardvisual000001",
        config_type="value",
        property_name="fontFamily",
        property_value="Segoe UI",
    )
    assert res["success"], res
    data = _read(_vj(report, "vc01cardvisual000001"))
    assert isinstance(data, dict) and data["name"] == "vc01cardvisual000001"
    entry = next(e for e in data["visual"]["objects"]["value"] if "fontFamily" in e["properties"])
    assert _lit(entry["properties"]["fontFamily"]) == "'Segoe UI'"
    # the selector-scoped entry is not overwritten
    assert any(e.get("selector") == {"id": "default"} for e in data["visual"]["objects"]["value"])


def test_h15_small_float_has_no_exponent(report):
    _op(
        report,
        operation="update_visual_config",
        page_name="Overview",
        visual_name="vc01cardvisual000001",
        config_type="labels",
        property_name="x",
        property_value=0.00001,
    )
    props = _read(_vj(report, "vc01cardvisual000001"))["visual"]["objects"]["labels"][0][
        "properties"
    ]
    assert "e" not in _lit(props["x"]).lower()


def test_h14_set_sort_writes_sort_definition(report):
    res = _op(
        report,
        operation="set_sort",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
        sort_field="Product Line.Category",
        sort_direction="Ascending",
    )
    assert res["success"], res
    q = _read(_vj(report, "vm01matrixfilters001"))["visual"]["query"]
    assert "sort" not in _read(_vj(report, "vm01matrixfilters001"))["visual"]
    entry = q["sortDefinition"]["sort"][0]
    assert entry["direction"] == "Ascending"
    assert entry["field"]["Column"]["Property"] == "Category"
    res = _op(
        report,
        operation="set_sort",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
        sort_field="m Metrics.Net Δ Value",
        sort_direction="Descending",
    )
    entry = _read(_vj(report, "vm01matrixfilters001"))["visual"]["query"]["sortDefinition"]["sort"][
        0
    ]
    assert "Measure" in entry["field"]  # measure detected from the binding / model


def test_h14_inject_deneb_writes_objects_vega(report):
    res = _op(
        report,
        operation="inject_code",
        page_name="Overview",
        visual_name="vc02cardlegacy000001",
        code_type="deneb",
        code='{"mark":"bar"}',
        provider="vegaLite",
    )
    assert res["success"], res
    objs = _read(_vj(report, "vc02cardlegacy000001"))["visual"]["objects"]
    assert "denebConfig" not in objs
    props = objs["vega"][0]["properties"]
    assert json.loads(_lit(props["jsonSpec"])[1:-1].replace("''", "'")) == {"mark": "bar"}
    assert _lit(props["provider"]) == "'vegaLite'"


def test_h14_sync_formatting_copies_visual_vco(report):
    res = _op(
        report,
        operation="sync_formatting",
        source_visual_name="vc01cardvisual000001",
        source_page="Overview",
        target_visual_name="vc02cardlegacy000001",
        target_page="Overview",
    )
    assert res["success"], res
    tgt = _read(_vj(report, "vc02cardlegacy000001"))
    assert "visualContainerObjects" not in tgt
    assert _lit(tgt["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]) == (
        "'Total Amount'"
    )


# ---------------------------------------------------------------------------
# H-16 / L-02 / H-30
# ---------------------------------------------------------------------------


def test_l02_write_without_target_is_refused(report):
    res = _op(report, operation="update_position", x=5)
    assert res["error_type"] == "target_required"


def test_l02_several_matches_need_allow_multiple(report):
    before = {
        p.name: (p / "visual.json").read_bytes() for p in _vj(report, "x").parent.parent.iterdir()
    }
    res = _op(report, operation="update_position", page_name="Overview", x=5)
    assert res["error_type"] == "multiple_matches"
    assert res["matched"] == 9
    after = {
        p.name: (p / "visual.json").read_bytes() for p in _vj(report, "x").parent.parent.iterdir()
    }
    assert before == after


def test_h16_page_id_and_exact_title_resolve(report):
    res = _op(
        report, operation="update_position", page_name=OVERVIEW, visual_name="Total Amount", x=5
    )
    assert res["success"], res
    assert res["matched"] == 1
    assert _read(_vj(report, "vc01cardvisual000001"))["position"]["x"] == 5


def test_h16_substring_page_is_not_a_write_target(report):
    res = _op(
        report,
        operation="update_position",
        page_name="verv",
        visual_name="vc01cardvisual000001",
        x=5,
    )
    assert res["success"] is False


def test_h30_exact_id_reads_one_file(report, monkeypatch):
    from core.pbip.authoring import targets

    reads = []
    real = targets.read_json

    def counting(path):
        reads.append(Path(path).parent.name)
        return real(path)

    monkeypatch.setattr(targets, "read_json", counting)
    res = targets.find_visuals(report / "definition", page=OVERVIEW, visual="vg02groupchild000001")
    assert len(res["visuals"]) == 1
    # the visual itself + its one parent group — never the other 7 visuals
    assert sorted(set(reads)) == ["vg01group00000000001", "vg02groupchild000001"]
    assert res["visuals"][0]["position"]["x"] == 850  # page-absolute (840 + 10)


# ---------------------------------------------------------------------------
# H-25
# ---------------------------------------------------------------------------


def test_h25_deleting_a_slicer_needs_confirmation_and_dumps_state(report):
    vj = _vj(report, "vs01slicerinverted01")
    res = _op(
        report,
        operation="delete",
        page_id=OVERVIEW,
        visual_id="vs01slicerinverted01",
        confirm_destructive=True,
    )
    assert res["success"] is False
    assert res["error_type"] == "filter_layer_delete_requires_confirmation"
    state = res["filter_layer"]["vs01slicerinverted01"]
    assert state["inverted_filters"] == ["vf01invcategory00001"]
    assert state["general_filter"]["Where"]
    assert vj.exists()


def test_h25_hide_is_top_level_is_hidden(report):
    res = _op(report, operation="hide", page_name="Overview", visual_name="vs01slicerinverted01")
    assert res["success"], res
    data = _read(_vj(report, "vs01slicerinverted01"))
    assert data["isHidden"] is True
    assert data["filterConfig"]["filters"][0]["name"] == "vf01invcategory00001"  # layer kept
    res = _op(report, operation="unhide", page_name="Overview", visual_name="vs01slicerinverted01")
    assert "isHidden" not in _read(_vj(report, "vs01slicerinverted01"))


def test_h25_delete_requires_exact_id(report):
    res = _op(
        report,
        operation="delete",
        page_id=OVERVIEW,
        visual_id="Total Amount",
        confirm_destructive=True,
    )
    assert res["success"] is False
    assert _vj(report, "vc01cardvisual000001").exists()


def test_h25_unparented_children_keep_page_position(report):
    res = _op(
        report,
        operation="delete",
        page_id=OVERVIEW,
        visual_id="vg01group00000000001",
        delete_children=False,
        confirm_destructive=True,
    )
    assert res["success"], res
    child = _read(_vj(report, "vg02groupchild000001"))
    assert "parentGroupName" not in child
    assert (child["position"]["x"], child["position"]["y"]) == (850, 405)


def test_h25_both_tools_rate_delete_the_same(report):
    from server.handlers.visual_creation_handler import delete_severity

    one = delete_severity(
        {"page_id": OVERVIEW, "visual_id": "vc01cardvisual000001"}, report / "definition"
    )
    group = delete_severity(
        {"page_id": OVERVIEW, "visual_id": "vg01group00000000001"}, report / "definition"
    )
    assert str(one).endswith("DESTRUCTIVE") and str(group).endswith("CATASTROPHIC")


def test_h25_group_template_shape():
    from core.pbip.authoring.visual_builder import VisualBuilder

    b = VisualBuilder("group")
    b.hidden() if hasattr(b, "hidden") else setattr(b, "_is_hidden", True)
    g = b.build()
    assert g["visualGroup"]["groupMode"] == "ScaleMode"
    assert "isHidden" not in g["visualGroup"]
    assert g.get("isHidden") is True


# ---------------------------------------------------------------------------
# H-26
# ---------------------------------------------------------------------------


def test_h26_create_uses_per_type_roles(report):
    res = _op(
        report,
        operation="create",
        page_name="Overview",
        visual_type="columnChart",
        measures=[{"table": "Sales", "measure": "Total Amount"}],
        columns=[{"table": "Product Line", "column": "Category"}],
    )
    assert res["success"], res
    data = _read(_vj(report, res["visual_id"]))
    qs = data["visual"]["query"]["queryState"]
    assert set(qs) == {"Y", "Category"}


def test_h26_unknown_parent_group_is_refused(report):
    res = _op(
        report,
        operation="create",
        page_name="Overview",
        visual_type="cardVisual",
        parent_group="nope",
        measures=[{"table": "Sales", "measure": "Total Amount"}],
    )
    assert res["success"] is False and res["error_type"] == "parent_group_not_found"


def test_h26_parent_group_converts_page_absolute_position(report):
    res = _op(
        report,
        operation="create",
        page_name="Overview",
        visual_type="cardVisual",
        parent_group="vg01group00000000001",
        position={"x": 860, "y": 420, "width": 100, "height": 50},
        measures=[{"table": "Sales", "measure": "Total Amount"}],
    )
    assert res["success"], res
    pos = _read(_vj(report, res["visual_id"]))["position"]
    assert (pos["x"], pos["y"]) == (20, 20)


def test_h26_invalid_role_is_refused(report):
    res = _op(
        report,
        operation="create",
        page_name="Overview",
        visual_type="cardVisual",
        measures=[{"table": "Sales", "measure": "Total Amount", "bucket": "Nope"}],
    )
    assert res["success"] is False


# ---------------------------------------------------------------------------
# H-31 / H-32
# ---------------------------------------------------------------------------


def test_h31_align_uses_page_absolute_frame(report):
    res = _op(
        report,
        operation="align",
        page_name="Overview",
        visual_names=["vg02groupchild000001", "vc01cardvisual000001"],
        alignment="left",
    )
    assert res["success"], res
    child = _read(_vj(report, "vg02groupchild000001"))["position"]
    card = _read(_vj(report, "vc01cardvisual000001"))["position"]
    # both left edges now at page x = 840 (the card); the child stays group-local
    assert card["x"] == 840
    assert child["x"] == 0


def test_h32_replace_measure_retargets_sort_and_formatting(report):
    vj = _vj(report, "vm01matrixfilters001")
    data = _read(vj)
    data["visual"]["objects"]["labels"] = [
        {
            "properties": {
                "color": {"solid": {"color": {"expr": {"Literal": {"Value": "'#FF0000'"}}}}}
            },
            "selector": {"metadata": "Sales.Total Amount"},
        }
    ]
    vj.write_text(json.dumps(data, indent=2), encoding="utf-8")
    res = _op(
        report,
        operation="replace_measure",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
        source_entity="Sales",
        source_property="Total Amount",
        target_entity="Sales",
        target_property="Amount Guarded",
    )
    assert res["success"], res
    data = _read(vj)
    sort_field = data["visual"]["query"]["sortDefinition"]["sort"][0]["field"]["Measure"]
    assert sort_field["Property"] == "Amount Guarded"
    assert data["visual"]["objects"]["labels"][0]["selector"]["metadata"] == "Sales.Amount Guarded"


def test_h32_remove_field_strips_sort_and_formatting(report):
    vj = _vj(report, "vm01matrixfilters001")
    data = _read(vj)
    data["visual"]["objects"]["labels"] = [
        {"properties": {}, "selector": {"metadata": "Sales.Total Amount"}}
    ]
    vj.write_text(json.dumps(data, indent=2), encoding="utf-8")
    res = _op(
        report,
        operation="remove_field",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
        table="Sales",
        field="Total Amount",
        confirm_destructive=True,
    )
    assert res["success"], res
    data = _read(vj)
    assert "sort" not in json.dumps(data["visual"]["query"].get("sortDefinition", {})) or not [
        s
        for s in data["visual"]["query"]["sortDefinition"].get("sort", [])
        if s["field"].get("Measure", {}).get("Property") == "Total Amount"
    ]
    assert not data["visual"]["objects"].get("labels")


def test_h32_add_field_hierarchy(report):
    res = _op(
        report,
        operation="add_field",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
        table="Sales",
        field="Order Date Hierarchy",
        hierarchy_level="Year",
        bucket="Rows",
        field_type="Hierarchy",
    )
    assert res["success"], res
    rows = _read(_vj(report, "vm01matrixfilters001"))["visual"]["query"]["queryState"]["Rows"]
    assert any("HierarchyLevel" in p["field"] for p in rows["projections"])


# ---------------------------------------------------------------------------
# H-35
# ---------------------------------------------------------------------------


def test_h35_visual_calc_is_a_native_projection(report):
    res = _op(
        report,
        operation="manage_visual_calcs",
        sub_operation="add",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
        calc_name="Running",
        expression="RUNNINGSUM([Total Amount])",
    )
    assert res["success"], res
    data = _read(_vj(report, "vm01matrixfilters001"))
    assert "visualCalculations" not in data["visual"]
    proj = data["visual"]["query"]["queryState"]["Values"]["projections"][-1]
    calc = proj["field"]["NativeVisualCalculation"]
    assert calc == {
        "Language": "dax",
        "Expression": "RUNNINGSUM([Total Amount])",
        "Name": "Running",
    }
    listed = _op(
        report,
        operation="manage_visual_calcs",
        sub_operation="list",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
    )
    assert "Running" in json.dumps(listed)
    res = _op(
        report,
        operation="manage_visual_calcs",
        sub_operation="delete",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
        calc_name="Running",
        confirm_destructive=True,
    )
    assert res["success"], res
    assert "NativeVisualCalculation" not in json.dumps(_read(_vj(report, "vm01matrixfilters001")))


# ---------------------------------------------------------------------------
# L-05 — run the tool's own examples on a fixture carrying their names
# ---------------------------------------------------------------------------


def test_l05_visual_examples_run_on_a_matching_fixture(report):
    from server.registry import TOOL_EXAMPLES

    defn = report / "definition"
    # Give the golden Overview page the ids / titles the examples use.
    src = _vj(report, "vc01cardvisual000001")
    ex_dir = src.parent.parent / "a1b2c3d4e5f60718"
    ex_dir.mkdir()
    data = _read(src)
    data["name"] = "a1b2c3d4e5f60718"
    ex_dir.joinpath("visual.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    line = _read(_vj(report, "vl01linereldate00001", "ab02details000000002"))
    line["name"] = "vl99salesbymonth0001"
    line.pop("filterConfig", None)
    line["visual"]["visualContainerObjects"] = {
        "title": [{"properties": {"text": {"expr": {"Literal": {"Value": "'Sales by Month'"}}}}}]
    }
    line_dir = src.parent.parent / "vl99salesbymonth0001"
    line_dir.mkdir()
    line_dir.joinpath("visual.json").write_text(json.dumps(line, indent=2), encoding="utf-8")

    for example in TOOL_EXAMPLES["07_Visual_Operations"]:
        args = copy.deepcopy(example)
        args["pbip_path"] = str(report)
        args["dry_run"] = True
        # map the examples' model names onto the fixture's model
        text = (
            json.dumps(args)
            .replace('"Total Sales"', '"Total Amount"')
            .replace('"Date"', '"Product Line"')
            .replace('"Month"', '"Region"')
        )
        res = _op(report, **json.loads(text))
        assert res.get("success") is not False, (example["operation"], res)
    assert defn.exists()


def test_l05_raw_ops_resolve_a_title_like_every_other_op(report):
    res = _op(
        report,
        operation="set_raw_property",
        page_name="Overview",
        visual_name="Total Amount",
        json_path="visual.objects.value[0].properties.bold",
        value=True,
        wrap_literal=True,
    )
    assert res["success"], res
    got = _op(
        report,
        operation="get_raw_property",
        page_name="Overview",
        visual_name="Total Amount",
        json_path="visual.objects.value[0].properties.bold",
    )
    assert got["success"], got
    assert "true" in json.dumps(got)


def test_l05_create_accepts_string_field_refs(report):
    res = _op(
        report,
        operation="create",
        page_name="Overview",
        visual_type="columnChart",
        measures=["Sales.Total Amount"],
        columns=["'Product Line'[Category]"],
    )
    assert res["success"], res
    qs = _read(_vj(report, res["visual_id"]))["visual"]["query"]["queryState"]
    assert qs["Y"]["projections"][0]["field"]["Measure"]["Property"] == "Total Amount"
    assert (
        qs["Category"]["projections"][0]["field"]["Column"]["Expression"]["SourceRef"]["Entity"]
        == "Product Line"
    )
