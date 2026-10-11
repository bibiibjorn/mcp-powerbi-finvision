"""Tests for pbir-schema parsing + harvest."""

import json
from pathlib import Path

import pytest

from tools.visual_catalog import schema as sch

FIXTURES = Path(__file__).parent / "fixtures" / "visual_catalog"


def _load(name: str):
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


def test_parse_describe_produces_property_specs():
    surface = sch.parse_describe_json(_load("schema_cardVisual_background.json"))
    assert surface.visual_type == "cardVisual"
    assert surface.container == "background"
    names = {p.name for p in surface.properties}
    assert {"color", "show", "transparency"} <= names
    show_prop = next(p for p in surface.properties if p.name == "show")
    assert show_prop.type == "boolean"


def test_parse_containers_json():
    listing = sch.parse_containers_json(_load("schema_barChart_containers.json"))
    assert listing.visual_type == "barChart"
    assert len(listing.containers) > 0
    for c in listing.containers:
        assert c.property_count >= 0


def test_parse_roles_json():
    roles = sch.parse_roles_json(_load("schema_cardVisual_roles.json"))
    assert roles.visual_type == "cardVisual"
    assert "Data" in roles.roles
    data = roles.roles["Data"]
    assert data.required and data.multiple
    assert "Column" in data.accepts and "Measure" in data.accepts


def test_parse_describe_missing_keys_raises():
    with pytest.raises(sch.SchemaParseError):
        sch.parse_describe_json({"type": "barChart", "container": "legend"})


def test_harvest_schema_writes_cache_with_mocked_pbir(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        subcommand = cmd[2]  # ["pbir", "schema", "<subcommand>", ...]
        if subcommand == "roles":
            payload = {
                "visualType": "cardVisual",
                "roles": {
                    "Data": {"required": True, "multiple": True, "accepts": ["Column", "Measure"]}
                },
            }
        elif subcommand == "containers":
            payload = {
                "visual_type": "cardVisual",
                "containers": [{"name": "background", "property_count": 3}],
            }
        elif subcommand == "describe":
            payload = {
                "type": "cardVisual",
                "container": "background",
                "properties": {"show": {"type": "boolean", "description": "Show"}},
            }
        else:
            raise AssertionError(f"unexpected: {cmd}")

        class R:
            returncode = 0
            stdout = json.dumps(payload)
            stderr = ""

        return R()

    monkeypatch.setattr(sch, "_run_pbir", fake_run)
    warnings = sch.harvest_schema(cache_root=tmp_path, only_types=["cardVisual"])
    assert (tmp_path / "cardVisual" / "_roles.json").exists()
    assert (tmp_path / "cardVisual" / "_containers.json").exists()
    assert (tmp_path / "cardVisual" / "background.json").exists()
    assert warnings == []
    assert calls
