"""A-10 / A-11 / L-07 / L-08: per-OPERATION read/write classification, the
read-only + confirm gates built on it, and annotation accuracy.

The old gates were tool-level: read_only_mode blocked 01_Connection (so the
server could not even connect) and every 02/09 read; confirm_destructive
blocked `02 measure/list` until an undeclared `confirmed=true` was passed.
"""

from __future__ import annotations

import pytest

from server.handlers import register_all_handlers
from server.op_classification import (
    DEFAULT_OPERATION,
    classify,
    classify_known,
    tool_levels,
)
from server.registry import HandlerRegistry


@pytest.fixture(scope="module")
def registry():
    reg = HandlerRegistry()
    register_all_handlers(reg)
    return reg


def _enum_ops(tool_def):
    props = tool_def.input_schema.get("properties", {})
    return list((props.get("operation") or {}).get("enum") or [])


def test_every_schema_operation_is_explicitly_classified(registry):
    """No op of any registered tool may fall into the unknown→write default."""
    missing = []
    for td in registry.get_all_tools():
        if td.name in ("02_Model_Operations", "03_Batch_Operations"):
            continue  # covered by the dedicated tests below
        ops = _enum_ops(td)
        if not ops:
            if classify_known(td.name, {}) is None:
                missing.append((td.name, None))
            continue
        for op in ops:
            if classify_known(td.name, {"operation": op}) is None:
                missing.append((td.name, op))
    assert not missing, f"unclassified tool/op pairs: {missing}"


def test_every_model_operation_is_classified():
    from server.handlers.model_operations_handler import _valid_operations

    missing = [
        (ot, op)
        for ot, ops in _valid_operations.items()
        for op in ops
        if classify_known("02_Model_Operations", {"object_type": ot, "operation": op}) is None
    ]
    assert not missing, missing


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"object_type": "measure", "operation": "list"}, "read"),
        ({"object_type": "measure"}, "read"),  # handler default op = list
        ({"object_type": "measure", "operation": "create"}, "write"),
        ({"object_type": "measure", "operation": "upsert"}, "write"),
        ({"object_type": "measure", "operation": "delete"}, "destructive"),
        ({"object_type": "table", "operation": "safe_rename"}, "destructive"),
        ({"object_type": "model", "operation": "refresh"}, "destructive"),
        ({"object_type": "calculation_group", "operation": "scaffold"}, "read"),
        ({"object_type": "function", "operation": "daxlib_search"}, "read"),
        ({"object_type": "role", "operation": "list_members"}, "read"),
    ],
)
def test_model_operations_levels(args, expected):
    assert classify("02_Model_Operations", args) == expected


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"object_type": "measures", "batch_operation": "create"}, "write"),
        ({"object_type": "measures", "batch_operation": "delete"}, "destructive"),
        (
            {"object_type": "measures", "batch_operation": "create", "options": {"dry_run": True}},
            "read",
        ),
        ({"object_type": "changeset", "preview": True, "changeset": {}}, "read"),
        ({"object_type": "changeset", "changeset": {"measures": {"create": [{}]}}}, "write"),
        ({"object_type": "changeset", "changeset": {"columns": {"delete": [{}]}}}, "destructive"),
        ({"object_type": "ti_suite"}, "read"),
        ({"object_type": "ti_suite", "apply": True}, "write"),
        ({"operation": "measures", "batch_operation": "rename"}, "destructive"),  # legacy alias
    ],
)
def test_batch_levels(args, expected):
    assert classify("03_Batch_Operations", args) == expected


@pytest.mark.parametrize(
    "tool,args,expected",
    [
        ("01_Connection", {"operation": "connect"}, "read"),
        ("01_Connection", {}, "read"),
        ("02_TMDL_Operations", {"operation": "find_replace"}, "read"),  # dry_run default True
        ("02_TMDL_Operations", {"operation": "find_replace", "dry_run": False}, "write"),
        ("02_TMDL_Operations", {"operation": "bulk_rename", "dry_run": False}, "destructive"),
        ("02_TMDL_Operations", {"operation": "format_dax"}, "write"),  # dry_run default False
        ("02_TMDL_Operations", {"operation": "format_dax", "dry_run": True}, "read"),
        ("07_Visual_Operations", {}, "read"),
        ("07_Visual_Operations", {"operation": "manage_visual_calcs"}, "read"),
        (
            "07_Visual_Operations",
            {"operation": "manage_visual_calcs", "sub_operation": "delete"},
            "destructive",
        ),
        ("07_Page_Operations", {"operation": "clear_filters"}, "destructive"),
        ("07_Page_Operations", {"operation": "set_interaction", "dry_run": True}, "read"),
        # A-06 (WP9): gate_and_wrap honours dry_run for EVERY write op of the
        # PBIR authoring tools (safe_io write scope) -> a dry run is a read.
        ("07_Theme_Operations", {"operation": "apply_theme", "dry_run": True}, "read"),
        ("07_Theme_Operations", {"operation": "apply_theme"}, "destructive"),
        ("07_Page_Operations", {"operation": "delete", "dry_run": True}, "read"),
        ("07_Visual_Operations", {"operation": "hide"}, "write"),
        ("11_PBIP_Authoring", {"operation": "clone_report", "dry_run": True}, "read"),
        (
            "07_Report_Operations",
            {"operation": "manage_extension_measures", "sub_operation": "list"},
            "read",
        ),
        ("09_Debug_Operations", {"operation": "visual"}, "read"),
        ("09_Debug_Operations", {"operation": "debug_loop"}, "read"),  # ask_user default
        (
            "09_Debug_Operations",
            {"operation": "debug_loop", "fix_strategy": "live_tom"},
            "destructive",
        ),
        ("15_DAX_Refactor", {"operation": "preview"}, "read"),
        ("15_DAX_Refactor", {"operation": "apply"}, "destructive"),
        ("17_Desktop_Bridge", {"operation": "status"}, "read"),
        ("17_Desktop_Bridge", {"operation": "reload"}, "destructive"),
        ("17_Desktop_Bridge", {"operation": "save"}, "destructive"),
        ("99_Unknown_Tool", {"operation": "x"}, "write"),
        ("07_Visual_Operations", {"operation": "brand_new_op"}, "write"),
    ],
)
def test_levels(tool, args, expected):
    assert classify(tool, args) == expected


def test_default_operations_match_schema_defaults(registry):
    for tool_name, op in DEFAULT_OPERATION.items():
        props = registry.get_tool_def(tool_name).input_schema.get("properties", {})
        schema_default = (props.get("operation") or {}).get("default")
        if schema_default is not None:
            assert schema_default == op, tool_name


# ---------------------------------------------------------------------------
# Annotations (A-10 / L-08)
# ---------------------------------------------------------------------------

_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
# 07_PBIP writes HTML / Markdown / JSON reports under exports/ (review G-21)
_ARTIFACT_WRITERS = {"08_Documentation_Word", "07_PBIP_Operations"}


def test_every_tool_declares_all_four_hints_explicitly(registry):
    missing = {
        td.name: [h for h in _HINTS if h not in (td.annotations or {})]
        for td in registry.get_all_tools()
    }
    missing = {k: v for k, v in missing.items() if v}
    assert not missing, missing


def test_annotations_agree_with_the_op_classification(registry):
    wrong = []
    for td in registry.get_all_tools():
        ann = td.annotations or {}
        levels = tool_levels(td.name)
        if ann.get("readOnlyHint") is True and levels != {"read"}:
            wrong.append((td.name, "readOnlyHint but has write ops"))
        if "destructive" in levels and ann.get("destructiveHint") is not True:
            wrong.append((td.name, "destructive ops but destructiveHint is not True"))
        # 08 only writes the Word file the caller asked for (not the model /
        # report), so it is a 'read' for the server gates but honestly not
        # readOnly for a client approval UI.
        if (
            levels == {"read"}
            and ann.get("readOnlyHint") is not True
            and td.name not in _ARTIFACT_WRITERS
        ):
            wrong.append((td.name, "all ops read but readOnlyHint is not True"))
    assert not wrong, wrong


def test_specific_annotation_fixes(registry):
    assert registry.get_tool_def("17_Desktop_Bridge").annotations["destructiveHint"] is True
    assert registry.get_tool_def("15_DAX_Refactor").annotations["destructiveHint"] is True
    for name in ("13_Query_Plan", "16_Dependency_Graph", "01_Connection"):
        assert registry.get_tool_def(name).annotations["readOnlyHint"] is True, name


# ---------------------------------------------------------------------------
# Gates built on the classification
# ---------------------------------------------------------------------------


def _gated_dispatcher(read_only=False, confirm=False):
    from tests._mcp_harness import make_dispatcher, tool

    calls = []

    def handler(name):
        def _h(args):
            calls.append((name, args.get("operation"), args.get("confirm_destructive")))
            return {"success": True}

        return _h

    d = make_dispatcher(
        tool("01_Connection", handler("01"), category="core"),
        tool(
            "02_Model_Operations",
            handler("02"),
            read_only=False,
            destructive=True,
            properties={"object_type": {"type": "string"}, "operation": {"type": "string"}},
        ),
        tool(
            "17_Desktop_Bridge",
            handler("17"),
            read_only=False,
            destructive=True,
            properties={"operation": {"type": "string"}},
        ),
    )
    d._read_only = read_only
    d._confirm_destructive = confirm
    return d, calls


def test_read_only_mode_blocks_writes_per_operation_not_per_tool():
    d, calls = _gated_dispatcher(read_only=True)
    assert d.dispatch("01_Connection", {"operation": "connect"})["success"] is True
    assert (
        d.dispatch("02_Model_Operations", {"object_type": "measure", "operation": "list"})[
            "success"
        ]
        is True
    )
    blocked = d.dispatch("02_Model_Operations", {"object_type": "measure", "operation": "update"})
    assert blocked["success"] is False
    assert blocked["error_type"] == "read_only_mode"
    assert blocked["classification"] == "write"
    assert (
        d.dispatch("17_Desktop_Bridge", {"operation": "reload"})["error_type"] == "read_only_mode"
    )
    assert d.dispatch("17_Desktop_Bridge", {"operation": "status"})["success"] is True
    assert [c[:2] for c in calls] == [("01", "connect"), ("02", "list"), ("17", "status")]


def test_confirm_mode_gates_destructive_ops_only_and_uses_confirm_destructive():
    d, calls = _gated_dispatcher(confirm=True)
    # reads and plain writes of a destructive-capable tool pass
    assert d.dispatch("02_Model_Operations", {"object_type": "measure", "operation": "list"})[
        "success"
    ]
    assert d.dispatch("02_Model_Operations", {"object_type": "measure", "operation": "create"})[
        "success"
    ]
    blocked = d.dispatch("02_Model_Operations", {"object_type": "measure", "operation": "delete"})
    assert blocked["error_type"] == "confirmation_required"
    assert "confirm_destructive=true" in blocked["error"]
    assert "confirm_destructive=true" in blocked["suggestion"]  # enrichment now consistent
    ok = d.dispatch(
        "02_Model_Operations",
        {"object_type": "measure", "operation": "delete", "confirm_destructive": True},
    )
    assert ok["success"] is True


def test_legacy_confirmed_alias_is_folded_into_confirm_destructive():
    d, calls = _gated_dispatcher(confirm=True)
    out = d.dispatch(
        "02_Model_Operations",
        {"object_type": "measure", "operation": "delete", "confirmed": True},
    )
    assert out["success"] is True
    assert "confirmed" in out["deprecated_args"]
    # the handler sees the canonical flag (so the in-handler gate is cleared too)
    assert calls[-1] == ("02", "delete", True)
