"""Direct-write rebuild of pbir_add_visuals — field split, type resolution,
spec→dict build, atomic add_visuals, rollback, and no-subprocess guarantee.

Mocks only the disk/connection boundary; never invokes pbir or a live model."""

import json
from pathlib import Path

import pytest

from core.pbip.authoring import pbir_add_visuals as pav


# ---------------------------------------------------------------------------
# Task 1 — field helpers
# ---------------------------------------------------------------------------
def test_split_field_table_dot_field():
    assert pav._split_field("s Axis Horizon.Horizon") == ("s Axis Horizon", "Horizon")
    assert pav._split_field("m Measures.Net Asset Value") == ("m Measures", "Net Asset Value")


def test_split_field_invalid():
    with pytest.raises(ValueError):
        pav._split_field("NoDotHere")


def test_resolve_field_type_explicit_hint():
    assert pav._resolve_field_type("T", "F", hint="Measure", measure_names=set()) == "Measure"
    assert pav._resolve_field_type("T", "F", hint="Column", measure_names={"F"}) == "Column"


def test_resolve_field_type_model_lookup():
    assert (
        pav._resolve_field_type(
            "m M", "Net Asset Value", hint=None, measure_names={"Net Asset Value"}
        )
        == "Measure"
    )


def test_resolve_field_type_heuristic_default_column():
    assert (
        pav._resolve_field_type("s Axis Horizon", "Horizon", hint=None, measure_names=set())
        == "Column"
    )


# ---------------------------------------------------------------------------
# Task 2 — _build_visual_dict
# ---------------------------------------------------------------------------
def test_build_visual_dict_column_field():
    spec = {
        "visual_type": "advancedSlicerVisual",
        "x": 60,
        "y": 70,
        "width": 220,
        "height": 40,
        "title": "Test",
        "fields": {"Values": "s Axis Horizon.Horizon"},
    }
    vid, vdict = pav._build_visual_dict(spec, measure_names=set())
    assert len(vid) == 20
    assert vdict["name"] == vid
    assert vdict["visual"]["visualType"] == "advancedSlicerVisual"
    proj = vdict["visual"]["query"]["queryState"]["Values"]["projections"][0]
    assert "Column" in proj["field"]
    assert proj["field"]["Column"]["Property"] == "Horizon"
    assert vdict["position"]["x"] == 60


def test_build_visual_dict_measure_field():
    spec = {
        "visual_type": "card",
        "x": 0,
        "y": 0,
        "width": 100,
        "height": 80,
        "fields": {"Values": "m Measures.Net Asset Value"},
    }
    vid, vdict = pav._build_visual_dict(spec, measure_names={"Net Asset Value"})
    # H-18c: `card` is authored as cardVisual, whose value role is Data (the
    # old assertion pinned the blank-card bug: measure in Values, Data empty).
    qs = vdict["visual"]["query"]["queryState"]
    assert vdict["visual"]["visualType"] == "cardVisual"
    assert "Values" not in qs
    proj = qs["Data"]["projections"][0]
    assert "Measure" in proj["field"]


def test_build_visual_dict_bad_field_raises():
    spec = {"visual_type": "card", "fields": {"Values": "NoDot"}}
    with pytest.raises(ValueError):
        pav._build_visual_dict(spec, measure_names=set())


# ---------------------------------------------------------------------------
# Task 3 — add_visuals (atomic direct-write)
# ---------------------------------------------------------------------------
def _mk_report(tmp_path) -> Path:
    """Minimal Report.Report/definition with one page folder. Returns definition dir."""
    defn = tmp_path / "R.Report" / "definition"
    page = defn / "pages" / "page01"
    (page / "visuals").mkdir(parents=True)
    (page / "page.json").write_text(
        json.dumps({"name": "page01", "displayName": "P"}), encoding="utf-8"
    )
    (defn / "pages" / "pages.json").write_text(
        json.dumps({"pageOrder": ["page01"]}), encoding="utf-8"
    )
    return defn


def test_add_visuals_writes_directly_no_subprocess(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pav.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess used")),
    )
    defn = _mk_report(tmp_path)
    res = pav.add_visuals(
        defn,
        "page01",
        [
            {
                "visual_type": "advancedSlicerVisual",
                "x": 60,
                "y": 70,
                "width": 220,
                "height": 40,
                "title": "T",
                "fields": {"Values": "s Axis Horizon.Horizon"},
            }
        ],
    )
    assert res["success"] is True
    assert res["created_count"] == 1
    vid = res["visual_ids"][0]
    vj = defn / "pages" / "page01" / "visuals" / vid / "visual.json"
    assert vj.exists()
    data = json.loads(vj.read_text(encoding="utf-8"))
    assert data["visual"]["visualType"] == "advancedSlicerVisual"


def test_add_visuals_atomic_bad_spec_writes_nothing(tmp_path):
    defn = _mk_report(tmp_path)
    visuals_dir = defn / "pages" / "page01" / "visuals"
    res = pav.add_visuals(
        defn,
        "page01",
        [
            {
                "visual_type": "advancedSlicerVisual",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 40,
                "fields": {"Values": "s Axis Horizon.Horizon"},
            },
            {"visual_type": "card", "fields": {"Values": "NoDotField"}},  # bad
        ],
    )
    assert res["success"] is False
    assert res["error_type"] in ("field_unresolved", "validation_failed")
    assert list(visuals_dir.iterdir()) == []


def test_add_visuals_rollback_on_write_error(tmp_path, monkeypatch):
    defn = _mk_report(tmp_path)
    visuals_dir = defn / "pages" / "page01" / "visuals"
    calls = {"n": 0}
    real_write = pav.write_visual

    def flaky_write(vdir, vdict):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real_write(vdir, vdict)

    monkeypatch.setattr(pav, "write_visual", flaky_write)
    res = pav.add_visuals(
        defn,
        "page01",
        [
            {
                "visual_type": "card",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 40,
                "fields": {"Values": "s T.A"},
            },
            {
                "visual_type": "card",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 40,
                "fields": {"Values": "s T.B"},
            },
        ],
    )
    assert res["success"] is False
    assert res["error_type"] == "write_failed"
    assert list(visuals_dir.iterdir()) == []


def test_add_visuals_page_not_found(tmp_path):
    defn = _mk_report(tmp_path)
    res = pav.add_visuals(defn, "nope", [{"visual_type": "card", "fields": {}}])
    assert res["success"] is False
    assert res["error_type"] == "page_not_found"


def test_add_visuals_missing_definition_path(tmp_path):
    res = pav.add_visuals(tmp_path / "nope", "p", [])
    assert res["success"] is False
    assert "definition path not found" in res["error"]


def test_add_visuals_bad_role_blocks_before_write(tmp_path):
    """Role pre-flight rejects a bogus role before anything is written."""
    defn = _mk_report(tmp_path)
    visuals_dir = defn / "pages" / "page01" / "visuals"
    res = pav.add_visuals(
        defn,
        "page01",
        [{"visual_type": "cardVisual", "fields": {"NotARole": "T.F"}}],
    )
    assert res["success"] is False
    assert res["error_type"] == "invalid_role"
    assert list(visuals_dir.iterdir()) == []
