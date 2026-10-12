"""L-13 / A-19 / A-03: one canonical error envelope, typed errors, and no
stale v6 tool names in the guidance the server emits.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from core.validation.error_handler import ErrorHandler
from core.validation.error_response import (
    ErrorResponse,
    error_envelope,
    infer_error_type,
    normalize_error_envelope,
    quick_error,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_error_envelope_shape():
    env = error_envelope("timeout", "slow", operation_id=3, skipped=None)
    assert env == {"success": False, "error_type": "timeout", "error": "slow", "operation_id": 3}


@pytest.mark.parametrize(
    "legacy,expected",
    [
        (
            {"ok": False, "err": "boom", "err_type": "x"},
            {"success": False, "error": "boom", "error_type": "x"},
        ),
        (
            {"success": False, "message": "Table 'X' not found"},
            {"error": "Table 'X' not found", "error_type": "not_found"},
        ),
        (
            {"success": False, "error": "Unknown operation: foo. Valid: a, b"},
            {"error_type": "invalid_operation"},
        ),
        (
            {"success": False, "error": "object_type is required"},
            {"error_type": "missing_argument"},
        ),
        (
            {"success": False, "error": "Not connected to Power BI Desktop"},
            {"error_type": "not_connected"},
        ),
        ({"success": False, "error": "weird"}, {"error_type": "unspecified"}),
        ({"success": False, "error": "x", "error_type": "kept"}, {"error_type": "kept"}),
    ],
)
def test_normalize_error_envelope(legacy, expected):
    out = normalize_error_envelope(dict(legacy))
    for key, value in expected.items():
        assert out[key] == value
    assert "ok" not in out and "err" not in out and "err_type" not in out


def test_success_results_are_untouched_except_alias_fold():
    assert normalize_error_envelope({"success": True, "rows": [{"ok": 1}]}) == {
        "success": True,
        "rows": [{"ok": 1}],
    }
    # nested payload keys are never rewritten
    assert normalize_error_envelope({"ok": True, "data": {"err": 1}}) == {
        "success": True,
        "data": {"err": 1},
    }


def test_compact_helpers_now_emit_canonical_keys():
    assert ErrorResponse(error="e", error_type="t", fix="f").to_dict(compact=True) == {
        "success": False,
        "error": "e",
        "error_type": "t",
        "fix": "f",
    }
    q = quick_error("Unknown operation: zz")
    assert q["success"] is False and q["error_type"] == "invalid_operation"


def test_not_connected_points_at_registered_tool():
    """A-19: the most frequent error named v6 tools that no longer exist."""
    env = ErrorHandler.handle_not_connected()
    text = repr(env)
    assert "detect_powerbi_desktop" not in text and "connect_to_powerbi" not in text
    wf = env["context"]["connection_workflow"]
    assert wf["step2"]["tool"] == "01_Connection"
    assert wf["step2"]["args"] == {"operation": "ensure"}
    assert "01_Connection" in env["suggestions"][0]


def test_unexpected_error_no_longer_advises_blind_retry():
    env = ErrorHandler.handle_unexpected_error("02_Model_Operations", RuntimeError("x"))
    assert "Try the operation again" not in repr(env)
    assert "verify" in env["context"]["troubleshooting"]["retry"].lower()


_LEGACY_TOOL_NAMES = {
    "detect_powerbi_desktop",
    "connect_to_powerbi",
    "run_dax_query",
    "preview_table_data",
    "12_Autonomous_Workflow",
    "07_PBIP_Dependency_Analysis",
    "set_pbip_path",
}


def _string_literals(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc_nodes.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in doc_nodes
        ):
            yield node.value


def test_protocol_layer_strings_reference_no_removed_tools():
    """A-19 fix: grep string literals (not docstrings) of the protocol layer for
    tool / op names that are not registered any more."""
    files = [
        *ROOT.glob("server/*.py"),
        *ROOT.glob("core/validation/*.py"),
        ROOT / "server" / "handlers" / "user_guide_handler.py",
        ROOT / "src" / "pbixray_server_enhanced.py",
    ]
    hits = []
    for f in files:
        for s in _string_literals(f):
            for name in _LEGACY_TOOL_NAMES:
                if name in s:
                    hits.append((f.name, name))
    assert not hits, hits


def test_infer_error_type_prefers_specific_rules():
    assert infer_error_type("Invalid operation 'x' for measure") == "invalid_operation"
    assert infer_error_type("Measure 'Q' not found") == "not_found"
    assert infer_error_type(None) == "unspecified"
