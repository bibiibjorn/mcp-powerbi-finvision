"""Tests for the per-visual formatting pass-through helper in pbir_add_visuals.

`_apply_visual_formatting` merges `objects` / `visualContainerObjects` into a
created visual.json. (The old pbir-CLI spec-stripping + spec-to-id matching
tests were removed in v7.8 when bulk creation became direct-write.)
"""

from __future__ import annotations

import json
from pathlib import Path

from core.pbip.authoring import pbir_add_visuals as mod


def test_apply_visual_formatting_merges_objects(tmp_path: Path) -> None:
    vdir = tmp_path / "abc123"
    vdir.mkdir()
    (vdir / "visual.json").write_text(
        json.dumps({"visual": {"objects": {"existing": [{"properties": {}}]}}}),
        encoding="utf-8",
    )
    mod._apply_visual_formatting(
        vdir,
        objects={"newKey": [{"properties": {"foo": "bar"}}]},
        vco=None,
    )
    saved = json.loads((vdir / "visual.json").read_text(encoding="utf-8"))
    objs = saved["visual"]["objects"]
    # Existing object survives.
    assert "existing" in objs
    # New object added.
    assert objs["newKey"][0]["properties"]["foo"] == "bar"


def test_apply_visual_formatting_merges_vco(tmp_path: Path) -> None:
    vdir = tmp_path / "abc123"
    vdir.mkdir()
    (vdir / "visual.json").write_text(
        json.dumps({"visual": {"visualContainerObjects": {"title": [{}]}}}),
        encoding="utf-8",
    )
    mod._apply_visual_formatting(
        vdir,
        objects=None,
        vco={"background": [{"properties": {"color": {"solid": {"color": "#FFF"}}}}]},
    )
    saved = json.loads((vdir / "visual.json").read_text(encoding="utf-8"))
    vco = saved["visual"]["visualContainerObjects"]
    assert "title" in vco
    assert "background" in vco


def test_apply_visual_formatting_no_op_when_both_none(tmp_path: Path) -> None:
    vdir = tmp_path / "abc123"
    vdir.mkdir()
    payload = {"visual": {"objects": {"x": []}}}
    (vdir / "visual.json").write_text(json.dumps(payload), encoding="utf-8")
    mod._apply_visual_formatting(vdir, objects=None, vco=None)
    saved = json.loads((vdir / "visual.json").read_text(encoding="utf-8"))
    assert saved == payload  # untouched
