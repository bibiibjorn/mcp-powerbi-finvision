"""BR-05: per-visual crops.

* v1 finding (live spike 2026-09-30): a v1 page capture is the canvas VIEWPORT (filter pane +
  warning banners included, 3437x812 at scale 1 on a wide window), not the page. The old
  ``_crop_page_visuals`` scaled visual coordinates by ``png_size / page_size`` as if the PNG
  were the page, so every crop from such a capture landed on the wrong pixels. The page
  rectangle is now located first. Tested on SYNTHETIC viewport PNGs only - no real Desktop
  capture was available for this change.
* v2: ``report.snapshot.capture/v2`` region crops are used only when the live manifest lists
  the method (it is flighted per instance); otherwise the v1 crop runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.autonomous import desktop_bridge as db
from tests._fake_bridge_lib import install_fake_cli

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

CANVAS = (232, 232, 232)
BANNER = (255, 244, 206)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 160, 0)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _page(root: Path, w=1280, h=720, page_id="p1"):
    page = root / "definition" / "pages" / page_id
    (page / "visuals").mkdir(parents=True)
    (page / "page.json").write_text(
        json.dumps({"name": page_id, "displayName": "P", "width": w, "height": h}), encoding="utf-8"
    )


def _visual(root: Path, vid, *, x, y, w, h, parent=None, group=False, hidden=False, page_id="p1"):
    d = root / "definition" / "pages" / page_id / "visuals" / vid
    d.mkdir(parents=True)
    data = {"name": vid, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h}}
    if group:
        data["visualGroup"] = {"displayName": vid, "groupMode": "ScaleMode"}
    else:
        data["visual"] = {"visualType": "cardVisual"}
    if parent:
        data["parentGroupName"] = parent
    if hidden:
        data["isHidden"] = True
    (d / "visual.json").write_text(json.dumps(data), encoding="utf-8")


def _fill(img: Image.Image, box, colour):
    img.paste(colour, (int(box[0]), int(box[1]), int(box[0] + box[2]), int(box[1] + box[3])))


def viewport_png(
    path: Path,
    *,
    size=(3437, 812),
    page_px=(620, 100, 1152, 648),
    page_units=(1280, 720),
    banner_h=40,
    pane_x=2937,
    visuals=(),
    page_colour=WHITE,
):
    """A synthetic v1 capture: canvas margin, a warning banner, the page, a filter pane.

    ``visuals`` = ``[(x, y, w, h, colour)]`` in PAGE units, painted at the zoomed position.
    """
    img = Image.new("RGB", size, CANVAS)
    if banner_h:
        _fill(img, (0, 0, size[0], banner_h), BANNER)
    if pane_x is not None:
        _fill(img, (pane_x, 0, size[0] - pane_x, size[1]), WHITE)
        for i in range(8):  # "text" in the pane
            _fill(img, (pane_x + 20, 30 + i * 60, 200, 10), (90, 90, 90))
    px, py, pw, ph = page_px
    _fill(img, page_px, page_colour)
    zx, zy = pw / page_units[0], ph / page_units[1]
    for vx, vy, vw, vh, colour in visuals:
        _fill(img, (px + vx * zx, py + vy * zy, vw * zx, vh * zy), colour)
    img.save(path)
    return img


def _centre(path):
    with Image.open(path) as im:
        return im.getpixel((im.size[0] // 2, im.size[1] // 2))


def _report(tmp_path):
    root = tmp_path / "R.Report"
    _page(root)
    _visual(root, "v1", x=10, y=20, w=300, h=120)
    _visual(root, "g1", x=100, y=300, w=400, h=200, group=True)
    _visual(root, "c1", x=10, y=10, w=100, h=50, parent="g1")  # page-absolute (110, 310)
    _visual(root, "far", x=1110, y=620, w=170, h=100)  # touches the bottom-right corner
    return root


VISUALS = [
    (10, 20, 300, 120, RED),
    (110, 310, 100, 50, BLUE),
    (1110, 620, 170, 100, GREEN),
]


def _crops(res):
    return {c["visual_id"]: c["crop_path"] for c in res["crops"]}


def _run(tmp_path, png, root):
    return db._crop_page_visuals(
        full_page_png=str(png),
        report_root=str(root),
        page_id="p1",
        crops_dir=str(tmp_path / "crops"),
        optimize=False,
    )


# ---------------------------------------------------------------------------
# what the old code did (regression documentation)
# ---------------------------------------------------------------------------
def test_legacy_mapping_on_a_viewport_capture_lands_on_the_wrong_pixels(tmp_path, monkeypatch):
    """The pre-fix behaviour, reproduced by switching the page search off: the PNG is treated
    as the page, so a visual's crop samples canvas/banner pixels instead of its own colour."""
    root = _report(tmp_path)
    png = tmp_path / "vp.png"
    viewport_png(png, visuals=VISUALS)
    monkeypatch.setattr(db, "_detect_page_rect", lambda img, aspect: None)
    res = _run(tmp_path, png, root)
    assert res["crop_mapping"]["method"] == "assumed_full_png"
    assert "misaligned" in res["warnings"][0]
    assert _centre(_crops(res)["v1"]) != RED  # wrong pixels - exactly the bug


# ---------------------------------------------------------------------------
# the fix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("factor", [1, 2])
def test_page_rect_is_found_inside_a_viewport_capture(tmp_path, factor):
    root = _report(tmp_path)
    png = tmp_path / "vp.png"
    viewport_png(
        png,
        size=(3437 * factor, 812 * factor),
        page_px=tuple(v * factor for v in (620, 100, 1152, 648)),
        banner_h=40 * factor,
        pane_x=2937 * factor,
        visuals=VISUALS,
    )
    res = _run(tmp_path, png, root)
    m = res["crop_mapping"]
    assert m["method"] == "detected" and "warnings" not in res
    x, y, w, h = m["page_rect_px"]
    for got, want in zip((x, y, w, h), (620 * factor, 100 * factor, 1152 * factor, 648 * factor)):
        assert abs(got - want) <= 4 * factor, (m["page_rect_px"], want)
    crops = _crops(res)
    assert res["capture_method"] == "v1_crop"
    assert _centre(crops["v1"]) == RED
    assert _centre(crops["c1"]) == BLUE  # group-local coords still resolve to page-absolute
    assert _centre(crops["far"]) == GREEN
    assert "g1" not in crops  # groups are containers, not visuals: no crop of their own


def test_crops_are_clipped_to_the_page_not_the_viewport(tmp_path):
    root = _report(tmp_path)
    png = tmp_path / "vp.png"
    viewport_png(png, visuals=VISUALS)
    res = _run(tmp_path, png, root)
    x, y, w, h = res["crop_mapping"]["page_rect_px"]
    with Image.open(_crops(res)["far"]) as far:
        # 'far' sits at the page's bottom-right corner: with 8 px padding it would spill into
        # the canvas/pane if it were clipped to the PNG instead of the page rectangle.
        assert far.size[0] <= 170 * 0.9 + 8 + 2 and far.size[1] <= 100 * 0.9 + 8 + 2
    with Image.open(_crops(res)["v1"]) as v1:
        assert v1.size[0] > 0


def test_banner_directly_above_the_page_is_not_part_of_the_page(tmp_path):
    root = _report(tmp_path)
    png = tmp_path / "vp.png"
    viewport_png(png, page_px=(620, 40, 1152, 648), visuals=VISUALS)  # page touches the banner
    res = _run(tmp_path, png, root)
    m = res["crop_mapping"]
    assert m["method"] == "detected"
    assert abs(m["page_rect_px"][1] - 40) <= 4 and abs(m["page_rect_px"][3] - 648) <= 6
    assert _centre(_crops(res)["v1"]) == RED


def test_a_4_by_3_page_is_found_too(tmp_path):
    root = tmp_path / "R.Report"
    _page(root, w=1024, h=768)
    _visual(root, "v1", x=100, y=100, w=200, h=100)
    png = tmp_path / "vp.png"
    viewport_png(
        png,
        page_px=(500, 60, 1000, 750),
        page_units=(1024, 768),
        visuals=[(100, 100, 200, 100, RED)],
        banner_h=0,
    )
    res = _run(tmp_path, png, root)
    assert res["crop_mapping"]["method"] == "detected"
    assert _centre(_crops(res)["v1"]) == RED


def test_png_with_the_page_aspect_ratio_keeps_the_legacy_mapping(tmp_path):
    root = tmp_path / "R.Report"
    _page(root)
    _visual(root, "v1", x=10, y=20, w=300, h=120)
    png = tmp_path / "page.png"
    Image.new("RGB", (2560, 1440), CANVAS).save(png)  # scale 2, exactly the page
    res = _run(tmp_path, png, root)
    assert res["crop_mapping"]["method"] == "full_png" and "warnings" not in res
    with Image.open(_crops(res)["v1"]) as c:
        assert c.size == (300 * 2 + 16, 120 * 2 + 16)


def test_undetectable_page_falls_back_to_the_legacy_mapping_with_a_warning(tmp_path):
    root = tmp_path / "R.Report"
    _page(root)
    _visual(root, "v1", x=10, y=20, w=300, h=120)
    png = tmp_path / "blank.png"
    Image.new("RGB", (3000, 700), CANVAS).save(png)  # all canvas: no page-shaped content
    res = _run(tmp_path, png, root)
    assert res["crop_mapping"]["method"] == "assumed_full_png"
    assert res["warnings"] and "aspect" in res["crop_mapping"]["reason"]
    assert [c["visual_id"] for c in res["crops"]] == [
        "v1"
    ]  # still produced, never worse than before


def test_op_screenshot_reports_the_mapping_and_v1_method(monkeypatch, tmp_path):
    root = _report(tmp_path)
    png = tmp_path / "vp.png"
    viewport_png(png, visuals=VISUALS)
    _stub_instance(monkeypatch)
    res = db.op_screenshot(
        page="P",
        pid=7,
        output=str(png),
        per_visual=True,
        report_root=str(root),
        crops_dir=str(tmp_path / "crops"),
        optimize=False,
    )
    assert res["visual_crops"]["capture_method"] == "v1_crop"
    assert res["visual_crops"]["crop_mapping"]["method"] == "detected"
    assert "v2_unavailable_reason" in res["visual_crops"]  # conftest stubs the helper off


# ---------------------------------------------------------------------------
# v2 region captures
# ---------------------------------------------------------------------------
def _stub_instance(monkeypatch):
    inst = {"pid": 7, "currentFilePath": "x.pbip", "pages": [{"id": "p1", "displayName": "P"}]}
    monkeypatch.setattr(db, "op_status", lambda **k: {"success": True, "instances": [inst]})
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {}})
    monkeypatch.setattr(db, "_optimize_output", lambda out, *a: None)


def _fake_v2(monkeypatch, tmp_path, *, fail_ids=(), status="ok", ok=True, error=None):
    calls = []

    def fake_lib(command, args, **k):
        calls.append((command, list(args)))
        if not ok:
            return {"ok": False, "error_type": "bridge_lib_unavailable", "error": error or "no lib"}
        if status == "unsupported":
            return {
                "ok": True,
                "data": {
                    "status": "unsupported",
                    "reason": "the live manifest does not list report.snapshot.capture/v2",
                },
            }
        regions = json.loads(
            Path(args[args.index("--regions-file") + 1]).read_text(encoding="utf-8")
        )
        out_dir = Path(args[args.index("--output-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        captures, failures = [], []
        for r in regions:
            if r["id"] in fail_ids:
                failures.append({"id": r["id"], "error": {"message": "region outside page"}})
                continue
            p = out_dir / f"{r['id']}.png"
            Image.new("RGB", (int(r["width"]), int(r["height"])), RED).save(p)
            captures.append({"id": r["id"], "path": str(p), "bytes": 1})
        return {
            "ok": True,
            "data": {
                "status": "partial" if failures else "ok",
                "captures": captures,
                "failures": failures,
            },
        }

    monkeypatch.setattr(db, "run_bridge_lib", fake_lib)
    return calls


def _shot(tmp_path, root, **k):
    png = tmp_path / "vp.png"
    viewport_png(png, visuals=VISUALS)
    return db.op_screenshot(
        page="P",
        pid=7,
        output=str(png),
        per_visual=True,
        report_root=str(root),
        crops_dir=str(tmp_path / "crops"),
        optimize=False,
        **k,
    )


def test_v2_regions_are_used_when_the_manifest_lists_the_method(monkeypatch, tmp_path):
    root = _report(tmp_path)
    _visual(root, "hid", x=5, y=5, w=50, h=50, hidden=True)
    _stub_instance(monkeypatch)
    calls = _fake_v2(monkeypatch, tmp_path)
    monkeypatch.setattr(db, "_crop_page_visuals", lambda **k: pytest.fail("v1 crop must not run"))
    res = _shot(tmp_path, root)
    vc = res["visual_crops"]
    assert vc["capture_method"] == "v2_region" and vc["scale"] == 2.0
    assert sorted(c["visual_id"] for c in vc["crops"]) == ["c1", "far", "v1"]
    assert {s["visual_id"] for s in vc["skipped"]} == {"hid"}
    ((command, args),) = calls
    assert command == "capture-regions"
    assert args[args.index("--page") + 1] == "p1" and args[args.index("--scale") + 1] == "2"
    crop = {c["visual_id"]: c for c in vc["crops"]}
    # regions are PBIR page-absolute units: the group child resolves through its parent group
    assert crop["c1"]["region"] == {"x": 110.0, "y": 310.0, "width": 100.0, "height": 50.0}
    assert crop["v1"]["region"] == {"x": 10.0, "y": 20.0, "width": 300.0, "height": 120.0}
    assert Path(crop["v1"]["crop_path"]).is_file()


def test_v2_uses_the_requested_scale_and_clips_regions_to_the_page(monkeypatch, tmp_path):
    root = tmp_path / "R.Report"
    _page(root)
    _visual(root, "spill", x=1200, y=650, w=200, h=200)  # hangs over the page's bottom-right
    _stub_instance(monkeypatch)
    calls = _fake_v2(monkeypatch, tmp_path)
    res = _shot(tmp_path, root, scale=3)
    region = res["visual_crops"]["crops"][0]["region"]
    assert region == {"x": 1200.0, "y": 650.0, "width": 80.0, "height": 70.0}
    assert calls[0][1][calls[0][1].index("--scale") + 1] == "3"


def test_v2_unsupported_falls_back_to_the_v1_crop(monkeypatch, tmp_path):
    root = _report(tmp_path)
    _stub_instance(monkeypatch)
    _fake_v2(monkeypatch, tmp_path, status="unsupported")
    res = _shot(tmp_path, root)
    vc = res["visual_crops"]
    assert (
        vc["capture_method"] == "v1_crop"
        and "report.snapshot.capture/v2" in vc["v2_unavailable_reason"]
    )
    assert vc["crop_mapping"]["method"] == "detected"


def test_v2_helper_failure_falls_back_to_the_v1_crop(monkeypatch, tmp_path):
    root = _report(tmp_path)
    _stub_instance(monkeypatch)
    _fake_v2(monkeypatch, tmp_path, ok=False, error="needs >= 1.0.0")
    vc = _shot(tmp_path, root)["visual_crops"]
    assert vc["capture_method"] == "v1_crop" and "needs >= 1.0.0" in vc["v2_unavailable_reason"]


def test_v2_partial_failure_keeps_the_good_crops_and_reports_the_bad(monkeypatch, tmp_path):
    root = _report(tmp_path)
    _stub_instance(monkeypatch)
    _fake_v2(monkeypatch, tmp_path, fail_ids=("far",))
    vc = _shot(tmp_path, root)["visual_crops"]
    assert vc["capture_method"] == "v2_region"
    assert "far" not in {c["visual_id"] for c in vc["crops"]}
    assert any(
        s["visual_id"] == "far" and "region outside page" in s["reason"] for s in vc["skipped"]
    )


def test_v2_total_failure_falls_back_to_the_v1_crop(monkeypatch, tmp_path):
    root = _report(tmp_path)
    _stub_instance(monkeypatch)
    _fake_v2(monkeypatch, tmp_path, fail_ids=("v1", "c1", "far"))
    vc = _shot(tmp_path, root)["visual_crops"]
    assert (
        vc["capture_method"] == "v1_crop"
        and "every region capture failed" in vc["v2_unavailable_reason"]
    )


def test_v2_crash_never_fails_the_screenshot(monkeypatch, tmp_path):
    root = _report(tmp_path)
    _stub_instance(monkeypatch)
    monkeypatch.setattr(
        db, "run_bridge_lib", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    res = _shot(tmp_path, root)
    assert res["success"] is True and res["visual_crops"]["capture_method"] == "v1_crop"


def test_without_per_visual_the_helper_is_never_called(monkeypatch, tmp_path):
    _stub_instance(monkeypatch)
    calls = _fake_v2(monkeypatch, tmp_path)
    png = tmp_path / "p.png"
    Image.new("RGB", (1280, 720), CANVAS).save(png)
    res = db.op_screenshot(page="P", pid=7, output=str(png), optimize=False)
    assert res["success"] is True and calls == []


# ---------------------------------------------------------------------------
# the real helper's capture-regions against a fake library
# ---------------------------------------------------------------------------
@pytest.fixture
def helper_env(tmp_path, monkeypatch):
    env = install_fake_cli(
        tmp_path, monkeypatch, methods="report.snapshot.capture/v2,application.state.get/v1"
    )
    return {"log": env["log"], "tmp": tmp_path}


def _regions_args(tmp, regions, page="p1", scale="2"):
    rf = tmp / "regions.json"
    rf.write_text(json.dumps(regions), encoding="utf-8")
    return [
        "--pid",
        "7",
        "--page",
        page,
        "--scale",
        scale,
        "--regions-file",
        str(rf),
        "--output-dir",
        str(tmp / "out"),
    ]


def test_helper_capture_regions_writes_one_png_per_region_via_v2(helper_env):
    tmp = helper_env["tmp"]
    regs = [
        {"id": "a", "x": 1, "y": 2, "width": 30, "height": 40},
        {"id": "b/../c", "x": 0, "y": 0, "width": 5, "height": 5},
    ]
    res = db._exec_bridge_lib("capture-regions", _regions_args(tmp, regs), timeout=30)
    assert res["ok"] is True, res
    data = res["data"]
    assert data["status"] == "ok" and len(data["captures"]) == 2
    for c in data["captures"]:
        assert Path(c["path"]).is_file() and Path(c["path"]).parent == (tmp / "out")
    sent = [json.loads(x) for x in helper_env["log"].read_text(encoding="utf-8").splitlines()]
    calls = [e for e in sent if "method" in e]
    assert calls[0] == {
        "method": "report.snapshot.capture/v2",
        "args": {"pageId": "p1", "scale": 2, "region": {"x": 1, "y": 2, "width": 30, "height": 40}},
    }


def test_helper_capture_regions_reports_unsupported_without_capturing(helper_env, monkeypatch):
    monkeypatch.setenv("FAKE_LIB_METHODS", "report.snapshot.capture/v1,application.state.get/v1")
    res = db._exec_bridge_lib(
        "capture-regions",
        _regions_args(helper_env["tmp"], [{"id": "a", "x": 0, "y": 0, "width": 5, "height": 5}]),
        timeout=30,
    )
    assert res["ok"] is True and res["data"]["status"] == "unsupported"
    sent = [json.loads(x) for x in helper_env["log"].read_text(encoding="utf-8").splitlines()]
    assert not [e for e in sent if "method" in e]


def test_helper_capture_regions_partial_failure(helper_env, monkeypatch):
    monkeypatch.setenv("FAKE_LIB_MODE", "fail_second")
    regs = [
        {"id": "ok", "x": 1, "y": 1, "width": 5, "height": 5},
        {"id": "bad", "x": 999, "y": 1, "width": 5, "height": 5},
    ]
    res = db._exec_bridge_lib("capture-regions", _regions_args(helper_env["tmp"], regs), timeout=30)
    assert res["ok"] is True and res["data"]["status"] == "partial"
    assert [c["id"] for c in res["data"]["captures"]] == ["ok"]
    assert res["data"]["failures"][0]["id"] == "bad"
