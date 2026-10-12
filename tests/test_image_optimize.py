"""Tests for image_optimize — visual-token math, Anthropic resize algorithm, PNG optimize.

The token math and resize numbers are pinned to Anthropic's published vision docs
(high-resolution tier = Opus 4.8: max long edge 2576 px, 4784 visual tokens;
one visual token per 28x28 px patch; cost = ceil(w/28) * ceil(h/28)).
"""

from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from core.autonomous import image_optimize as io  # noqa: E402


# --------------------------------------------------------------------------
# count_image_tokens — one token per 28x28 patch
# --------------------------------------------------------------------------
def test_token_count_exact_patch_multiple():
    # 28x28 -> exactly one patch
    assert io.count_image_tokens(28, 28) == 1


def test_token_count_rounds_up_partial_patches():
    # 29x29 -> 2x2 patches (ceil)
    assert io.count_image_tokens(29, 29) == 4


def test_token_count_anthropic_worked_example_1000():
    # Anthropic docs: 1000x1000 = 1296 visual tokens (ceil(1000/28)=36; 36*36)
    assert io.count_image_tokens(1000, 1000) == 1296


def test_token_count_anthropic_worked_example_1092():
    # Anthropic docs: 1092x1092 = 1521 visual tokens (ceil(1092/28)=39; 39*39)
    assert io.count_image_tokens(1092, 1092) == 1521


# --------------------------------------------------------------------------
# resized_size — dual-constraint (edge AND token budget), aspect-preserving
# --------------------------------------------------------------------------
def test_resize_a4_portrait_anthropic_example():
    # Anthropic docs worked example: 1075x1520 -> 924x1307 (token limit triggers,
    # not the edge limit). Pinned to the standard tier the example uses.
    assert io.resized_size(1075, 1520, max_edge=1568, max_tokens=1568) == (924, 1307)


def test_resize_noop_when_already_fits_highres():
    # 1920x1080 on the high-res tier: 1920<=2576 and tokens 2691<=4784 -> unchanged.
    assert io.count_image_tokens(1920, 1080) == 2691
    assert io.resized_size(1920, 1080) == (1920, 1080)


def test_resize_small_image_unchanged():
    assert io.resized_size(400, 300) == (400, 300)


def test_resize_downscales_oversized_to_fit_both_limits():
    # 4000x3000 exceeds both edge (2576) and token (4784) limits on high-res tier.
    w, h = io.resized_size(4000, 3000)
    assert max(w, h) <= 2576
    assert io.count_image_tokens(w, h) <= 4784
    # aspect ratio preserved (4:3) within rounding
    assert abs((w / h) - (4000 / 3000)) < 0.02


def test_resize_never_upscales():
    # A tiny image must never be enlarged.
    w, h = io.resized_size(100, 80)
    assert (w, h) == (100, 80)


def test_resize_landscape_edge_limit_high_res():
    # Very wide image: long edge must be clamped to 2576 on high-res tier.
    w, h = io.resized_size(6000, 1000)
    assert w <= 2576
    assert io.count_image_tokens(w, h) <= 4784


# --------------------------------------------------------------------------
# optimize_png — PIL round-trip, downscale-only, returns stats
# --------------------------------------------------------------------------
def _png(path: Path, size):
    Image.new("RGB", size, color=(180, 180, 180)).save(path, format="PNG")


def test_optimize_downscales_oversized_png(tmp_path):
    src = tmp_path / "big.png"
    _png(src, (4000, 3000))
    result = io.optimize_png(src)
    assert result["resized"] is True
    assert result["original_size"] == (4000, 3000)
    fw, fh = result["final_size"]
    assert max(fw, fh) <= 2576
    assert io.count_image_tokens(fw, fh) <= 4784
    # File on disk actually shrank to the reported size.
    assert Image.open(src).size == (fw, fh)


def test_optimize_leaves_fitting_png_untouched(tmp_path):
    src = tmp_path / "ok.png"
    _png(src, (1280, 720))
    result = io.optimize_png(src)
    assert result["resized"] is False
    assert result["original_size"] == (1280, 720)
    assert result["final_size"] == (1280, 720)
    assert Image.open(src).size == (1280, 720)


def test_optimize_never_upscales_small_png(tmp_path):
    src = tmp_path / "tiny.png"
    _png(src, (200, 150))
    result = io.optimize_png(src)
    assert result["resized"] is False
    assert Image.open(src).size == (200, 150)


def test_optimize_reports_visual_tokens(tmp_path):
    src = tmp_path / "p.png"
    _png(src, (1000, 1000))
    result = io.optimize_png(src)
    assert result["visual_tokens"] == 1296


def test_optimize_missing_file_returns_noop(tmp_path):
    # Optimize must be a safe no-op when the CLI never wrote a PNG (so turning it
    # on by default can't break a screenshot op whose capture failed upstream).
    missing = tmp_path / "nope.png"
    result = io.optimize_png(missing)
    assert result["resized"] is False
    assert result.get("skipped") is True
