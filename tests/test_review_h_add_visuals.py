"""Deep review 2026-09-22, slice H — 11_PBIP_Authoring:pbir_add_visuals.

H-18  field refs split at the FIRST dot, Measure vs Column from the bound
      model's TMDL (definition.pbir byPath), ``card`` binds cardVisual's
      ``Data`` role (was: blank card with the measure in ``Values``).
H-19  container coordinates are page-absolute (nested groups summed) and are
      computed when container_x / container_y are omitted.
H-01  every visual is written once through safe_io (no raw save_json_file):
      a dry run writes nothing.
H-36  new visual.json files take the ``$schema`` sibling visuals carry.
J-06  no subprocess is ever spawned; a traversal id is refused.
H-15  titles with an apostrophe are escaped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
OVERVIEW = "aa01overview00000001"


@pytest.fixture
def report(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    rep = root / "RPT-Sales.Report"
    # a group nested in the golden KPI tile group (page-absolute 840,400)
    inner = rep / "definition" / "pages" / OVERVIEW / "visuals" / "vg03innergroup000001"
    inner.mkdir()
    inner.joinpath("visual.json").write_text(
        json.dumps(
            {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.11.0/schema.json",
                "name": "vg03innergroup000001",
                "position": {"x": 50, "y": 30, "z": 0, "width": 200, "height": 60},
                "visualGroup": {"displayName": "Inner", "groupMode": "ScaleMode"},
                "parentGroupName": "vg01group00000000001",
            }
        ),
        encoding="utf-8",
    )
    return rep


def _add(report: Path, **args):
    from server.handlers.authoring_handler import handle_pbip_authoring

    return handle_pbip_authoring(
        {"operation": "pbir_add_visuals", "pbip_path": str(report), "page": "Overview", **args}
    )


def _new(report: Path, vid: str) -> dict:
    p = report / "definition" / "pages" / OVERVIEW / "visuals" / vid / "visual.json"
    return json.loads(p.read_text(encoding="utf-8-sig"))


def _proj(data: dict, role: str) -> dict:
    return data["visual"]["query"]["queryState"][role]["projections"][0]["field"]


# ---------------------------------------------------------------------------
# H-18
# ---------------------------------------------------------------------------


def test_h18_measure_kind_from_bound_model_and_first_dot_split(report):
    res = _add(
        report,
        visuals=[
            {
                "visual_type": "columnChart",
                "x": 0,
                "y": 0,
                "width": 300,
                "height": 200,
                "fields": {"Category": "Product Line.Category", "Y": "Sales.Total Amount"},
            }
        ],
    )
    assert res["success"], res
    data = _new(report, res["visual_ids"][0])
    assert "Measure" in _proj(data, "Y")  # no field_types hint needed
    assert "Column" in _proj(data, "Category")
    assert "field_warnings" not in res


def test_h18_split_prefers_a_table_the_model_has():
    from core.pbip.authoring.pbir_add_visuals import _split_field

    assert _split_field("m Measures.Growth 2.0") == ("m Measures", "Growth 2.0")
    assert _split_field("m Measure.pv108. Performance") == ("m Measure", "pv108. Performance")
    assert _split_field("A.B.C", {"A.B"}) == ("A.B", "C")
    assert _split_field("'Owner''s Book'[Owner]") == ("Owner's Book", "Owner")


def test_h18_card_binds_data_role(report):
    res = _add(
        report,
        visuals=[{"visual_type": "card", "fields": {"Values": "m Metrics.Net Δ Value"}}],
    )
    assert res["success"], res
    data = _new(report, res["visual_ids"][0])
    assert data["visual"]["visualType"] == "cardVisual"
    qs = data["visual"]["query"]["queryState"]
    assert list(qs) == ["Data"]
    assert _proj(data, "Data")["Measure"]["Property"] == "Net Δ Value"


def test_h18_unknown_field_is_reported(report):
    res = _add(
        report,
        visuals=[{"visual_type": "cardVisual", "fields": {"Data": "Sales.Nope"}}],
    )
    assert res["success"], res
    assert any("Nope" in w for w in res["field_warnings"])


# ---------------------------------------------------------------------------
# H-19
# ---------------------------------------------------------------------------


def test_h19_resolve_container_is_page_absolute(report):
    res = _add(report, operation="pbir_resolve_container", container_display_name="Inner")
    assert res["success"], res
    assert (res["coords"]["x"], res["coords"]["y"]) == (890.0, 430.0)
    assert res["coords"]["local"] == {"x": 50, "y": 30}


def test_h19_container_offset_computed_when_omitted(report):
    res = _add(
        report,
        container_display_name="Inner",
        visuals=[{"visual_type": "textbox", "x": 900, "y": 440, "width": 50, "height": 20}],
    )
    assert res["success"], res
    assert res["container_offset"]["source"] == "computed"
    data = _new(report, res["visual_ids"][0])
    assert data["parentGroupName"] == "vg03innergroup000001"
    assert (data["position"]["x"], data["position"]["y"]) == (10.0, 10.0)


def test_h19_unknown_container_id_refused(report):
    res = _add(
        report,
        container_group_id="vc01cardvisual000001",
        visuals=[{"visual_type": "textbox"}],
    )
    assert res["success"] is False and res["error_type"] == "container_not_found"


# ---------------------------------------------------------------------------
# H-01 / H-36 / H-15
# ---------------------------------------------------------------------------


def test_h01_dry_run_writes_nothing(report):
    visuals_dir = report / "definition" / "pages" / OVERVIEW / "visuals"
    before = sorted(p.name for p in visuals_dir.iterdir())
    res = _add(
        report,
        dry_run=True,
        container_display_name="Inner",
        visuals=[
            {
                "visual_type": "textbox",
                "x": 900,
                "y": 440,
                "visualContainerObjects": {"title": [{"properties": {}}]},
            }
        ],
    )
    assert res["dry_run"] is True and res["files_modified"] == 0
    assert sorted(p.name for p in visuals_dir.iterdir()) == before


def test_h36_new_visual_takes_sibling_schema_and_escapes_title(report):
    res = _add(
        report,
        visuals=[
            {
                "visual_type": "cardVisual",
                "title": "Client's NAV",
                "fields": {"Data": "Sales.Total Amount"},
            }
        ],
    )
    data = _new(report, res["visual_ids"][0])
    assert data["$schema"].endswith("/visualContainer/2.11.0/schema.json")
    text = data["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]
    assert text["expr"]["Literal"]["Value"] == "'Client''s NAV'"


# ---------------------------------------------------------------------------
# J-06
# ---------------------------------------------------------------------------


def test_j06_no_subprocess_is_spawned(report, monkeypatch):
    def boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("subprocess spawned")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    res = _add(report, visuals=[{"visual_type": "textbox"}])
    assert res["success"], res


@pytest.mark.parametrize("bad", ["..\\..\\evil", "../x", "a/b"])
def test_j06_traversal_ids_refused(report, bad):
    res = _add(report, container_group_id=bad, visuals=[{"visual_type": "textbox"}])
    assert res["success"] is False
    res = _add(report, page=bad, visuals=[{"visual_type": "textbox"}])
    assert res["success"] is False
    assert not (report.parent / "evil").exists()
