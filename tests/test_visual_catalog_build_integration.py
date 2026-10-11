"""End-to-end: harvest → render → artifact checks."""

import json
from pathlib import Path

import pytest

from tools.visual_catalog import build

pytestmark = pytest.mark.slow

FIXTURE = Path(__file__).parent / "fixtures" / "visual_catalog" / "report_mini"


def _seed_schema(cache_root: Path, visual_type: str) -> None:
    d = cache_root / visual_type
    d.mkdir(parents=True, exist_ok=True)
    (d / "_roles.json").write_text(
        json.dumps(
            {
                "visualType": visual_type,
                "roles": {
                    "Data": {"required": True, "multiple": True, "accepts": ["Column", "Measure"]}
                },
            }
        ),
        encoding="utf-8",
    )
    (d / "_containers.json").write_text(
        json.dumps(
            {
                "visual_type": visual_type,
                "containers": [{"name": "background", "property_count": 3}],
            }
        ),
        encoding="utf-8",
    )
    (d / "background.json").write_text(
        json.dumps(
            {
                "type": visual_type,
                "container": "background",
                "properties": {"show": {"type": "boolean", "description": "Show"}},
            }
        ),
        encoding="utf-8",
    )


def test_build_from_cached_schema(tmp_path):
    out = tmp_path / "catalog"
    schema_cache = out / "_cache" / "schema"
    schema_cache.mkdir(parents=True)
    _seed_schema(schema_cache, "cardVisual")
    _seed_schema(schema_cache, "columnChart")

    build.build_from_cached_schema(
        report_root=FIXTURE,
        out_dir=out,
        only_types=["cardVisual", "columnChart"],
    )

    assert (out / "INDEX.md").exists()
    assert (out / "cardVisual.md").exists()
    assert (out / "columnChart.md").exists()
    assert (out / "_shared" / "containers-background.md").exists()
    assert (out / "_shared" / "conditional-formatting.md").exists()
    assert (out / "_extracts" / "_metadata.json").exists()

    card_md = (out / "cardVisual.md").read_text(encoding="utf-8")
    assert "## Roles" in card_md
    assert "KPI Overview__v001" in card_md


def test_rebuild_preserves_manual_content(tmp_path):
    out = tmp_path / "catalog"
    schema_cache = out / "_cache" / "schema"
    schema_cache.mkdir(parents=True)
    _seed_schema(schema_cache, "cardVisual")

    build.build_from_cached_schema(report_root=FIXTURE, out_dir=out, only_types=["cardVisual"])

    card_path = out / "cardVisual.md"
    original = card_path.read_text(encoding="utf-8")
    card_path.write_text(
        original.replace(
            "<!-- MANUAL: overview -->\nTODO: fill in during enrichment.",
            "<!-- MANUAL: overview -->\nCustom enriched prose.",
        ),
        encoding="utf-8",
    )

    build.build_from_cached_schema(report_root=FIXTURE, out_dir=out, only_types=["cardVisual"])
    after = card_path.read_text(encoding="utf-8")
    assert "Custom enriched prose." in after


def test_list_screenshots_needed_returns_picked_instances(tmp_path):
    out = tmp_path / "catalog"
    schema_cache = out / "_cache" / "schema"
    schema_cache.mkdir(parents=True)
    _seed_schema(schema_cache, "cardVisual")
    _seed_schema(schema_cache, "columnChart")

    build.build_from_cached_schema(
        report_root=FIXTURE, out_dir=out, only_types=["cardVisual", "columnChart"]
    )

    needed = build.list_screenshots_needed(report_root=FIXTURE, out_dir=out)
    assert len(needed) >= 1
    for item in needed:
        assert {
            "page_display_name",
            "page_id",
            "visual_id",
            "visual_type",
            "x",
            "y",
            "width",
            "height",
            "output_path",
        } <= set(item.keys())
