"""Tests for screenshot cropping."""

from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from tools.visual_catalog import crop as cp  # noqa: E402


def _png(path: Path, size=(1280, 720)):
    Image.new("RGB", size, color=(200, 200, 200)).save(path)


def test_crop_applies_padding(tmp_path):
    src = tmp_path / "page.png"
    _png(src)
    out = tmp_path / "visual.png"
    cp.crop_visual(full_page_png=src, x=100, y=50, width=300, height=200, output_png=out, padding=8)
    assert out.exists()
    assert Image.open(out).size == (316, 216)


def test_crop_clamps_to_bounds(tmp_path):
    src = tmp_path / "page.png"
    _png(src, size=(800, 600))
    out = tmp_path / "visual.png"
    cp.crop_visual(
        full_page_png=src, x=700, y=500, width=300, height=300, output_png=out, padding=8
    )
    sz = Image.open(out).size
    assert sz[0] <= 800 and sz[1] <= 600


def test_offscreen_raises(tmp_path):
    src = tmp_path / "page.png"
    _png(src, size=(800, 600))
    out = tmp_path / "visual.png"
    with pytest.raises(cp.OutOfBoundsError):
        cp.crop_visual(full_page_png=src, x=2000, y=2000, width=100, height=100, output_png=out)
