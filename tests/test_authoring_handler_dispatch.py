"""Tests for server.handlers.authoring_handler — the dispatch shell.

Backs MCP tool 11_PBIP_Authoring. The underlying engines (CloneEngine,
PageBuilder, pbir_add_visuals.add_visuals) and the delegated
visual_creation_handler ops already have / will have their own tests. This
file pins the *dispatcher's* contract:

  - operation validation: missing -> specific error; unknown -> valid ops list
  - pbip_path validation: missing -> clean error for disk-write ops; not
    required for read-only ops
  - read-only ops (list_templates / get_template) skip the autonomous gate
  - create_visual / delete_visual DELEGATE to visual_creation_handler
    (single source of truth) with the resolved definition path
  - pbir_add_visuals routes to the MOCKED pbir bulk function (never shells out
    to the real pbir CLI)
  - create_page routes to the page builder; missing page_name -> clean error
  - reload_required hint propagates on a successful disk-write op

Boundary discipline: every engine / delegate / pbir subprocess is mocked. The
_op_* functions import their engine lazily inside the function body and call
core.utilities.pbip_utils.resolve_definition_path at the top, so we patch both
resolve_definition_path (to avoid touching the real filesystem) and the lazily
imported targets at their source module.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from server.handlers import authoring_handler as ah

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    """Default policy = OFF so the dispatcher logic is exercised without each
    test having to mock gating. conftest auto-resets the gate-policy cache."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()


@pytest.fixture
def fake_resolve(monkeypatch):
    """Patch resolve_definition_path so disk-write ops resolve to a fake path
    without scanning the filesystem. The handler imports it at module top
    (`from core.utilities.pbip_utils import resolve_definition_path`) so we
    patch the name bound in the handler module."""
    m = MagicMock(return_value={"path": "/fake/Report.Report/definition"})
    monkeypatch.setattr(ah, "resolve_definition_path", m)
    return m


# ---------------------------------------------------------------------------
# Operation validation
# ---------------------------------------------------------------------------


def test_missing_operation_returns_specific_error():
    result = ah.handle_pbip_authoring({})
    assert result["success"] is False
    # P1-121: specific "operation is required" message, not "Unknown operation: ''".
    assert "operation is required" in result["error"]


def test_unknown_operation_returns_valid_operations(fake_resolve):
    result = ah.handle_pbip_authoring({"operation": "frobnicate", "pbip_path": "/fake/x.pbip"})
    assert result["success"] is False
    assert "Unknown operation: 'frobnicate'" in result["error"]
    # The dispatcher lists the valid ops alphabetically in the error.
    for op in ("clone_page", "create_visual", "pbir_add_visuals", "list_templates"):
        assert op in result["error"]


def test_missing_pbip_path_for_write_op_returns_clean_error():
    """A disk-write op without pbip_path must fail fast with a clean error
    (no engine import, no gating)."""
    result = ah.handle_pbip_authoring({"operation": "create_page"})
    assert result["success"] is False
    assert "pbip_path is required" in result["error"]


def test_read_only_op_does_not_require_pbip_path():
    """list_templates is read-only and must run with no pbip_path."""
    fake = {"success": True, "operation": "list_templates", "templates": [], "count": 0}
    with patch(
        "server.handlers.visual_creation_handler.op_list_templates", return_value=fake
    ) as deleg:
        result = ah.handle_pbip_authoring({"operation": "list_templates"})
    assert result["success"] is True
    deleg.assert_called_once()


# ---------------------------------------------------------------------------
# Read-only ops skip the gate
# ---------------------------------------------------------------------------


def test_list_templates_skips_gate_under_strict_policy(monkeypatch):
    """Read-only op must NOT be blocked even under STRICT — it touches no disk."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()

    fake = {"success": True, "operation": "list_templates", "templates": [], "count": 0}
    with patch(
        "server.handlers.visual_creation_handler.op_list_templates", return_value=fake
    ) as deleg:
        result = ah.handle_pbip_authoring({"operation": "list_templates"})
    assert result["success"] is True
    deleg.assert_called_once()
    # Read-only ops don't get a reload hint.
    assert "reload_required" not in result


def test_get_template_delegates_to_visual_creation(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()

    fake = {"success": True, "operation": "get_template", "template": {"x": 1}}
    with patch(
        "server.handlers.visual_creation_handler.op_get_template", return_value=fake
    ) as deleg:
        result = ah.handle_pbip_authoring({"operation": "get_template", "visual_type": "card"})
    assert result["success"] is True
    deleg.assert_called_once()


# ---------------------------------------------------------------------------
# Delegation to visual_creation_handler (single source of truth)
# ---------------------------------------------------------------------------


def test_create_visual_delegates_to_visual_creation_handler(fake_resolve):
    """create_visual must delegate to visual_creation_handler.op_create_visual
    with the *resolved* definition path (not the raw pbip_path)."""
    fake = {"success": True, "operation": "create", "visual_id": "abc"}
    with patch(
        "server.handlers.visual_creation_handler.op_create_visual", return_value=fake
    ) as deleg:
        args = {
            "operation": "create_visual",
            "pbip_path": "/fake/x.pbip",
            "page_name": "Page1",
            "visual_type": "card",
        }
        result = ah.handle_pbip_authoring(args)
    assert result["success"] is True
    deleg.assert_called_once_with(args, "/fake/Report.Report/definition")


def test_delete_visual_delegates_to_visual_creation_handler(fake_resolve):
    """delete_visual delegates to visual_creation_handler.op_delete_visual
    (which cleans cross-refs via reference_cleanup downstream)."""
    fake = {"success": True, "operation": "delete", "deleted": ["abc"]}
    with patch(
        "server.handlers.visual_creation_handler.op_delete_visual", return_value=fake
    ) as deleg:
        args = {
            "operation": "delete_visual",
            "pbip_path": "/fake/x.pbip",
            "page_name": "Page1",
            "visual_id": "abc",
        }
        result = ah.handle_pbip_authoring(args)
    assert result["success"] is True
    deleg.assert_called_once_with(args, "/fake/Report.Report/definition")


def test_create_visual_group_delegates_to_visual_creation_handler(fake_resolve):
    fake = {"success": True, "operation": "create_group", "group_id": "g1"}
    with patch(
        "server.handlers.visual_creation_handler.op_create_visual_group",
        return_value=fake,
    ) as deleg:
        args = {
            "operation": "create_visual_group",
            "pbip_path": "/fake/x.pbip",
            "page_name": "Page1",
            "group_name": "G1",
        }
        result = ah.handle_pbip_authoring(args)
    assert result["success"] is True
    deleg.assert_called_once_with(args, "/fake/Report.Report/definition")


# ---------------------------------------------------------------------------
# pbir_add_visuals — must route to the MOCKED bulk fn, never shell out
# ---------------------------------------------------------------------------


def test_pbir_add_visuals_routes_to_mocked_bulk_fn(fake_resolve):
    """pbir_add_visuals must call core.pbip.authoring.pbir_add_visuals.add_visuals
    with the resolved definition path, page, and visuals — and NEVER run the
    real pbir CLI subprocess."""
    fake = {"success": True, "visual_ids": ["v1"], "container_applied": []}
    with patch("core.pbip.authoring.pbir_add_visuals.add_visuals", return_value=fake) as bulk:
        args = {
            "operation": "pbir_add_visuals",
            "pbip_path": "/fake/x.pbip",
            "page": "page123",
            "visuals": [{"visual_type": "card", "x": 0, "y": 0}],
        }
        result = ah.handle_pbip_authoring(args)

    assert result["success"] is True
    assert result["operation"] == "pbir_add_visuals"
    bulk.assert_called_once()
    # add_visuals is keyword-driven; assert the load-bearing kwargs.
    _, kwargs = bulk.call_args
    assert kwargs["page"] == "page123"
    assert kwargs["visuals"] == [{"visual_type": "card", "x": 0, "y": 0}]
    # default timeout coerced to 120; omitted container coords stay None so
    # add_visuals computes the container's page-absolute offset (H-19 — the
    # old 0.0 default treated every nested group as sitting at the page origin).
    assert kwargs["timeout"] == 120
    assert kwargs["container_x"] is None


def test_pbir_add_visuals_empty_visuals_returns_clean_error(fake_resolve):
    """A missing / empty visuals list must fail before invoking the bulk fn."""
    with patch("core.pbip.authoring.pbir_add_visuals.add_visuals") as bulk:
        result = ah.handle_pbip_authoring(
            {
                "operation": "pbir_add_visuals",
                "pbip_path": "/fake/x.pbip",
                "page": "page123",
                "visuals": [],
            }
        )
    assert result["success"] is False
    assert "visuals must be a non-empty list" in result["error"]
    bulk.assert_not_called()


def test_pbir_add_visuals_missing_page_returns_clean_error(fake_resolve):
    with patch("core.pbip.authoring.pbir_add_visuals.add_visuals") as bulk:
        result = ah.handle_pbip_authoring(
            {
                "operation": "pbir_add_visuals",
                "pbip_path": "/fake/x.pbip",
                "visuals": [{"visual_type": "card", "x": 0, "y": 0}],
            }
        )
    assert result["success"] is False
    assert "page" in result["error"]
    bulk.assert_not_called()


# ---------------------------------------------------------------------------
# create_page routing + arg validation
# ---------------------------------------------------------------------------


def test_create_page_routes_to_page_builder(fake_resolve):
    """create_page must construct a PageBuilder and call .build()."""
    builder = MagicMock(name="PageBuilder_instance")
    builder.build.return_value = {"success": True, "page_id": "newpage"}
    PageBuilderCls = MagicMock(return_value=builder)
    with patch("core.pbip.authoring.page_builder.PageBuilder", PageBuilderCls):
        result = ah.handle_pbip_authoring(
            {
                "operation": "create_page",
                "pbip_path": "/fake/x.pbip",
                "page_name": "My New Page",
            }
        )
    assert result["success"] is True
    assert result["operation"] == "create_page"
    PageBuilderCls.assert_called_once_with("/fake/Report.Report/definition", "My New Page")
    builder.build.assert_called_once()


def test_create_page_missing_page_name_returns_clean_error(fake_resolve):
    """create_page resolves the path first, then requires page_name."""
    with patch("core.pbip.authoring.page_builder.PageBuilder") as PageBuilderCls:
        result = ah.handle_pbip_authoring({"operation": "create_page", "pbip_path": "/fake/x.pbip"})
    assert result["success"] is False
    assert "page_name is required" in result["error"]
    PageBuilderCls.assert_not_called()


# ---------------------------------------------------------------------------
# reload_required hint propagation
# ---------------------------------------------------------------------------


def test_reload_hint_propagates_on_successful_write(fake_resolve):
    """create_page is in OPS_REQUIRING_RELOAD; a successful disk-write op must
    carry reload_required + the reload op chain back to the caller."""
    builder = MagicMock()
    builder.build.return_value = {"success": True, "page_id": "newpage"}
    with patch("core.pbip.authoring.page_builder.PageBuilder", MagicMock(return_value=builder)):
        result = ah.handle_pbip_authoring(
            {
                "operation": "create_page",
                "pbip_path": "/fake/x.pbip",
                "page_name": "My New Page",
            }
        )
    assert result["success"] is True
    assert result.get("reload_required") is True
    assert "reload_op_chain" in result


# ---------------------------------------------------------------------------
# Registry hookup
# ---------------------------------------------------------------------------


def test_register_authoring_handler_registers_tool():
    registry = MagicMock()
    ah.register_authoring_handler(registry)
    registry.register.assert_called_once()
    tool = registry.register.call_args[0][0]
    assert tool.name == "11_PBIP_Authoring"
    assert tool.handler is ah.handle_pbip_authoring
    assert tool.category == "authoring"
