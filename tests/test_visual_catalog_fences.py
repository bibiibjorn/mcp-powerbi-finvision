"""Tests for manual-fence preservation."""

from tools.visual_catalog import fences as fn


def test_extract_fences():
    src = """
<!-- MANUAL: overview -->
A card is used for KPIs.
<!-- /MANUAL -->
"""
    assert fn.extract_fences(src) == {"overview": "A card is used for KPIs."}


def test_merge_preserves_previous_blocks():
    previous = """<!-- MANUAL: overview -->
Hand-written prose.
<!-- /MANUAL -->

<!-- MANUAL: gotchas -->
fontSize + calloutSize compound.
<!-- /MANUAL -->
"""
    regenerated = """<!-- MANUAL: overview -->
PLACEHOLDER
<!-- /MANUAL -->

<!-- MANUAL: gotchas -->
PLACEHOLDER
<!-- /MANUAL -->
"""
    merged = fn.merge_manual(regenerated=regenerated, previous=previous)
    assert "Hand-written prose." in merged
    assert "fontSize + calloutSize compound." in merged
    assert "PLACEHOLDER" not in merged


def test_merge_keeps_regenerated_content_outside_fences():
    previous = "<!-- MANUAL: overview -->\nold\n<!-- /MANUAL -->\n"
    regenerated = (
        "# cardVisual — 2026\n\n<!-- MANUAL: overview -->\nplaceholder\n<!-- /MANUAL -->\n"
    )
    merged = fn.merge_manual(regenerated=regenerated, previous=previous)
    assert "# cardVisual — 2026" in merged
    assert "old" in merged
    assert "placeholder" not in merged


def test_idempotent_when_unchanged():
    text = "<!-- MANUAL: gotchas -->\nxyz\n<!-- /MANUAL -->\n"
    assert fn.merge_manual(regenerated=text, previous=text) == text


def test_has_manual_blocks_detector():
    assert fn.has_manual_blocks("x <!-- MANUAL: gotchas -->\ny\n<!-- /MANUAL -->")
    assert not fn.has_manual_blocks("nope")


def test_unknown_slug_raises():
    import pytest

    with pytest.raises(fn.InvalidManualSlugError):
        fn.extract_fences("<!-- MANUAL: madeupslug -->\nx\n<!-- /MANUAL -->")
