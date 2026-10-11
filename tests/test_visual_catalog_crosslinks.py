"""Tests for cross-link formatters."""

from tools.visual_catalog import crosslinks as xl


def test_pbir_cli_ref_points_relative():
    link = xl.pbir_cli_ref("cardVisual")
    assert link == "../GUIDE.md#cli-cardvisual"
    assert link.startswith("../")


def test_pbir_json_ref_points_relative():
    link = xl.pbir_json_ref("cardVisual")
    assert link == "../GUIDE.md#json-cardvisual"
    assert link.startswith("../")


def test_shared_container_link_is_relative():
    assert xl.shared_container("background") == "./_shared/containers-background.md"


def test_skill_pointer_format():
    assert xl.skill_pointer("pbir-page-builder") == "skill: pbir-page-builder"


def test_mcp_op_format():
    s = xl.mcp_op("11_PBIP_Authoring", "pbir_add_visuals")
    assert "11_PBIP_Authoring" in s and "pbir_add_visuals" in s
