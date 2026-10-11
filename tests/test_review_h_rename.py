"""Deep review 2026-09-22, slice H — renames that touch report files.

H-12  structural cascade: table rename renames ``SourceRef.Entity`` (+
      queryRef), column rename is scoped to its table, measure rename
      rebuilds queryRef / nativeQueryRef / selector.metadata, the file keeps
      CRLF and no trailing newline.
H-13  the live-rename bridge cascades into EVERY report bound to the model
      (thin reports via definition.pbir byPath), not the first *.Report.
H-23  rename_report repoints ``.pbip artifacts[].report.path``; rebind edits
      ``<report>/definition.pbir`` and validates byPath.
H-24  slicer pane rename: parse/mutate (titles with ``show`` first), keyed by
      (page, visual) — duplicate visual ids across pages both renamed.
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
def root(tmp_path) -> Path:
    r = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, r)
    return r


def _vj(root: Path, vid: str, page: str = OVERVIEW, report: str = "RPT-Sales") -> Path:
    return (
        root / f"{report}.Report" / "definition" / "pages" / page / "visuals" / vid / "visual.json"
    )


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------------------
# H-12
# ---------------------------------------------------------------------------


def test_h12_table_rename_renames_entities_and_query_refs(root):
    from core.pbip.cascade_rename import CascadeRenameEngine, RenameSpec

    res = CascadeRenameEngine(str(root / "RPT-Sales.Report")).apply_specs(
        [RenameSpec("table", "Product Line", "Product Group")]
    )
    assert res["success"] and res["files_modified"] > 0
    data = _read(_vj(root, "vs01slicerinverted01"))
    proj = data["visual"]["query"]["queryState"]["Values"]["projections"][0]
    assert proj["field"]["Column"]["Expression"]["SourceRef"]["Entity"] == "Product Group"
    assert proj["queryRef"] == "Product Group.Category"
    assert data["filterConfig"]["filters"][0]["filter"]["From"][0]["Entity"] == "Product Group"
    page = _read(root / "RPT-Sales.Report" / "definition" / "pages" / OVERVIEW / "page.json")
    assert (
        page["filterConfig"]["filters"][0]["field"]["Column"]["Expression"]["SourceRef"]["Entity"]
        == "Product Group"
    )
    assert "Product Line" not in json.dumps(data)


def test_h12_column_rename_is_scoped_to_its_table(root):
    from core.pbip.cascade_rename import CascadeRenameEngine

    # Two tables with a "Region"-like clash: rename Product Line[Region] only.
    vj = _vj(root, "vm01matrixfilters001")
    data = _read(vj)
    data["visual"]["query"]["queryState"]["Rows"]["projections"].append(
        {
            "field": {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": "Owner's Book"}},
                    "Property": "Region",
                }
            },
            "queryRef": "Owner's Book.Region",
        }
    )
    vj.write_text(json.dumps(data, indent=2), encoding="utf-8")
    res = CascadeRenameEngine(str(root / "RPT-Sales.Report")).apply_renames_typed(
        {"column": {"Region": "Area"}}, table_name="Product Line"
    )
    assert res["success"], res
    data = _read(vj)
    rows = data["visual"]["query"]["queryState"]["Rows"]["projections"]
    assert rows[-1]["field"]["Column"]["Property"] == "Region"  # other table untouched
    region_filter = next(
        f for f in data["filterConfig"]["filters"] if f["name"] == "vf05region0000000001"
    )
    assert region_filter["field"]["Column"]["Property"] == "Area"
    cond_col = region_filter["filter"]["Where"][0]["Condition"]["In"]["Expressions"][0]["Column"]
    assert cond_col["Property"] == "Area"  # alias 'p' resolved to Product Line


def test_h12_measure_rename_rebuilds_refs_and_keeps_bytes(root):
    from core.pbip.cascade_rename import CascadeRenameEngine

    vj = _vj(root, "vm01matrixfilters001")
    data = _read(vj)
    data["visual"]["objects"]["labels"] = [
        {"properties": {}, "selector": {"metadata": "Sales.Total Amount"}}
    ]
    raw = json.dumps(data, indent=2, ensure_ascii=False).replace("\n", "\r\n")
    vj.write_bytes(raw.encode("utf-8"))
    res = CascadeRenameEngine(str(root / "RPT-Sales.Report")).apply_renames_typed(
        {"measure": {"Total Amount": "Revenue"}}, table_name="Sales"
    )
    assert res["success"], res
    out_raw = vj.read_bytes()
    assert b"\r\n" in out_raw and not out_raw.endswith(b"\n")  # CRLF, no trailing newline
    assert "Δ".encode("utf-8") in out_raw  # raw UTF-8 kept (H-36)
    data = json.loads(out_raw.decode("utf-8"))
    proj = data["visual"]["query"]["queryState"]["Values"]["projections"][0]
    assert proj["field"]["Measure"]["Property"] == "Revenue"
    assert proj["queryRef"] == "Sales.Revenue"
    assert proj["nativeQueryRef"] == "Revenue"
    assert data["visual"]["objects"]["labels"][0]["selector"]["metadata"] == "Sales.Revenue"
    assert (
        data["visual"]["query"]["sortDefinition"]["sort"][0]["field"]["Measure"]["Property"]
        == "Revenue"
    )


def test_h12_display_text_is_never_renamed(root):
    from core.pbip.cascade_rename import CascadeRenameEngine

    card = _vj(root, "vc01cardvisual000001")
    before_title = _read(card)["visual"]["visualContainerObjects"]["title"]
    CascadeRenameEngine(str(root / "RPT-Sales.Report")).apply_renames_typed(
        {"measure": {"Total Amount": "Revenue"}}, table_name="Sales"
    )
    data = _read(card)
    assert data["visual"]["visualContainerObjects"]["title"] == before_title  # 'Total Amount' title
    assert (
        data["visual"]["query"]["queryState"]["Data"]["projections"][0]["field"]["Measure"][
            "Property"
        ]
        == "Revenue"
    )


# ---------------------------------------------------------------------------
# H-13
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", ["MDL-Core.pbip", "MDL-Core.SemanticModel", "RPT-Sales.pbip"])
def test_h13_cascade_reaches_every_bound_report(root, entry):
    from core.operations.rename_cascade_pbir_bridge import apply_pbir_cascade

    res = apply_pbir_cascade(
        pbip_path=str(root / entry),
        kind="measure",
        old_name="Net Δ Value",
        new_name="Net Delta",
        table_name="m Metrics",
    )
    assert res["success"], res
    names = sorted(Path(p).name for p in res["report_paths"])
    assert names == ["RPT-Finance.Report", "RPT-Sales.Report"]  # never the byConnection one
    fin = _read(_vj(root, "vf01financecard00001", "ba01finance000000001", "RPT-Finance"))
    assert (
        fin["visual"]["query"]["queryState"]["Data"]["projections"][0]["field"]["Measure"][
            "Property"
        ]
        == "Net Delta"
    )
    legacy = _read(_vj(root, "vc02cardlegacy000001"))
    assert "Net Delta" in json.dumps(legacy, ensure_ascii=False)


def test_h13_project_folder_with_one_model_resolves(root):
    from core.operations.rename_cascade_pbir_bridge import resolve_bound_reports

    reports, err, _w = resolve_bound_reports(str(root))
    assert err is None
    assert sorted(p.name for p in reports) == ["RPT-Finance.Report", "RPT-Sales.Report"]


# ---------------------------------------------------------------------------
# H-23
# ---------------------------------------------------------------------------


def test_h23_rename_report_repoints_pbip(root):
    from server.handlers.report_operations_handler import handle_report_operations

    res = handle_report_operations(
        {
            "operation": "rename",
            "pbip_path": str(root / "RPT-Finance.pbip"),
            "new_name": "RPT-Fin2",
            "confirm_destructive": True,
        }
    )
    assert res["success"], res
    assert (root / "RPT-Fin2.Report" / "definition").is_dir()
    pbip = _read(root / "RPT-Fin2.pbip")
    assert pbip["artifacts"][0]["report"]["path"] == "RPT-Fin2.Report"
    assert not (root / "RPT-Finance.pbip").exists()


def test_h23_rebind_edits_report_definition_pbir(root):
    from server.handlers.report_operations_handler import handle_report_operations

    rep = root / "RPT-Remote.Report"
    res = handle_report_operations(
        {"operation": "rebind", "pbip_path": str(rep), "model_path": "../MDL-Core.SemanticModel"}
    )
    assert res["success"], res
    assert _read(rep / "definition.pbir")["datasetReference"] == {
        "byPath": {"path": "../MDL-Core.SemanticModel"}
    }
    bad = handle_report_operations(
        {"operation": "rebind", "pbip_path": str(rep), "model_path": "../Nope.SemanticModel"}
    )
    assert bad["success"] is False


# ---------------------------------------------------------------------------
# H-24
# ---------------------------------------------------------------------------


def _slicer_report(tmp_path: Path) -> Path:
    src = _read(_vj(GOLDEN, "vs01slicerinverted01"))
    defn = tmp_path / "S.Report" / "definition"
    for pid in ("p1", "p2"):
        vdir = defn / "pages" / pid / "visuals" / "sl1"
        vdir.mkdir(parents=True)
        (defn / "pages" / pid / "page.json").write_text(
            json.dumps({"name": pid, "displayName": f"Page {pid}"}), encoding="utf-8"
        )
        v = copy.deepcopy(src)
        v["name"] = "sl1"
        v.pop("filterConfig", None)
        v["visual"]["visualContainerObjects"] = {
            "title": [
                {
                    "properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "text": {"expr": {"Literal": {"Value": "'Misleading'"}}},
                    }
                }
            ]
        }
        (vdir / "visual.json").write_text(json.dumps(v, indent=2), encoding="utf-8")
    return defn


def test_h24_show_first_title_and_duplicate_ids_are_both_renamed(tmp_path):
    from server.handlers._internal.slicer_pane_audit import _op_bulk_rename_pane_names

    defn = _slicer_report(tmp_path)
    res = _op_bulk_rename_pane_names({}, defn)
    assert res["success"], res
    assert res["applied"] == 2
    for pid in ("p1", "p2"):
        props = _read(defn / "pages" / pid / "visuals" / "sl1" / "visual.json")["visual"][
            "visualContainerObjects"
        ]["title"][0]["properties"]
        assert props["text"]["expr"]["Literal"]["Value"] == "'Product Line'"
        assert props["show"]["expr"]["Literal"]["Value"] == "true"


def test_h24_bare_override_on_duplicate_id_is_ambiguous(tmp_path):
    from server.handlers._internal.slicer_pane_audit import _op_bulk_rename_pane_names

    defn = _slicer_report(tmp_path)
    res = _op_bulk_rename_pane_names({"custom_overrides": {"sl1": "X"}}, defn)
    assert res["error_type"] == "ambiguous_override"
    res = _op_bulk_rename_pane_names({"custom_overrides": {"p2/sl1": "Only here"}}, defn)
    assert res["success"], res
    t1 = _read(defn / "pages" / "p1" / "visuals" / "sl1" / "visual.json")
    t2 = _read(defn / "pages" / "p2" / "visuals" / "sl1" / "visual.json")
    assert (
        t2["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"][
            "Value"
        ]
        == "'Only here'"
    )
    assert (
        t1["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"][
            "Value"
        ]
        == "'Product Line'"
    )  # p1 still gets the normal rule-A rename
