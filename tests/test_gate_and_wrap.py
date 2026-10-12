"""Tests for server.handlers._authoring_utils.gate_and_wrap + coerce_num."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch, MagicMock

import pytest

from server.handlers._authoring_utils import gate_and_wrap, coerce_num

# ---------------------------------------------------------------------------
# coerce_num
# ---------------------------------------------------------------------------


class TestCoerceNum:
    def test_none_returns_default(self):
        assert coerce_num(None, "position.x", 5.0) == 5.0

    def test_int_passthrough(self):
        assert coerce_num(100, "position.x", 0) == 100.0

    def test_float_passthrough(self):
        assert coerce_num(100.5, "position.x", 0) == 100.5

    def test_numeric_string_coerced(self):
        # MCP callers routinely pass "100" as a string when JSON-serializing
        # args. The boundary coercion converts it silently.
        assert coerce_num("100", "position.x", 0) == 100.0

    def test_invalid_raises_descriptive(self):
        with pytest.raises(ValueError, match="position.x"):
            coerce_num("abc", "position.x", 0)


# ---------------------------------------------------------------------------
# gate_and_wrap
# ---------------------------------------------------------------------------


def _success_dispatch(args: Dict[str, Any], pbip_path: Any) -> Dict[str, Any]:
    return {"success": True, "message": "dispatched"}


def _error_dispatch(args: Dict[str, Any], pbip_path: Any) -> Dict[str, Any]:
    return {"success": False, "error": "something went wrong"}


class TestGateAndWrap:
    def test_read_only_op_skips_gating_and_reload_hint(self):
        """A read-only op should not trigger gating or attach reload_required."""
        with patch(
            "server.handlers._authoring_utils.require_autonomous_mode",
            return_value=None,
        ) as gate_mock:
            result = gate_and_wrap(
                args={"foo": "bar"},
                operation="list",
                tool_name="07_Visual_Operations",
                dispatch_fn=_success_dispatch,
                pbip_path_or_defn="/tmp/fake",
                read_only_ops={"list", "get"},
                catastrophic_ops=set(),
            )
        # Gating is checked (require_autonomous_mode returns None for read-only
        # via the `if operation not in ro_set` branch — but we must assert it
        # was NOT called for read-only ops).
        gate_mock.assert_not_called()
        assert result["success"] is True
        # No reload_required attached for read-only ops.
        assert "reload_required" not in result

    def test_mutating_op_gates_and_attaches_hint_on_success(self):
        """A destructive op should gate, dispatch, and attach reload_required."""
        with patch(
            "server.handlers._authoring_utils.require_autonomous_mode",
            return_value=None,  # gate allows the op
        ):
            with patch(
                "server.handlers._authoring_utils.attach_reload_hint",
                side_effect=lambda r, tool, op: {**r, "reload_required": True, "_tool": tool},
            ):
                result = gate_and_wrap(
                    args={"pbip_path": "/tmp/x.pbip"},
                    operation="create_visual",
                    tool_name="07_Visual_Operations",
                    dispatch_fn=_success_dispatch,
                    pbip_path_or_defn="/tmp/x.pbip",
                    read_only_ops={"list"},
                    catastrophic_ops=set(),
                )
        assert result["success"] is True
        assert result["reload_required"] is True
        assert result["_tool"] == "07_Visual_Operations"

    def test_gate_block_short_circuits(self):
        """When the gate returns success=False, dispatch is not called and no hint is attached."""
        dispatch_mock = MagicMock(return_value={"success": True})
        gate_reject = {
            "success": False,
            "error": "autonomous mode required",
            "error_type": "confirm_destructive_required",
        }
        with patch(
            "server.handlers._authoring_utils.require_autonomous_mode",
            return_value=gate_reject,
        ):
            with patch("server.handlers._authoring_utils.attach_reload_hint") as hint_mock:
                result = gate_and_wrap(
                    args={},
                    operation="create_visual",
                    tool_name="07_Visual_Operations",
                    dispatch_fn=dispatch_mock,
                    pbip_path_or_defn=None,
                    read_only_ops=set(),
                    catastrophic_ops=set(),
                )
        dispatch_mock.assert_not_called()
        hint_mock.assert_not_called()
        assert result is gate_reject

    def test_error_response_skips_reload_hint_by_default(self):
        """A failed dispatch must NOT attach reload_required — the op didn't touch disk."""
        with patch(
            "server.handlers._authoring_utils.require_autonomous_mode",
            return_value=None,
        ):
            with patch("server.handlers._authoring_utils.attach_reload_hint") as hint_mock:
                result = gate_and_wrap(
                    args={},
                    operation="create_visual",
                    tool_name="07_Visual_Operations",
                    dispatch_fn=_error_dispatch,
                    pbip_path_or_defn=None,
                    read_only_ops=set(),
                    catastrophic_ops=set(),
                )
        hint_mock.assert_not_called()
        assert result["success"] is False

    def test_catastrophic_op_escalates_severity(self):
        """Catastrophic ops pass a higher severity to require_autonomous_mode."""
        from core.autonomous.gating import Severity

        captured: Dict[str, Any] = {}

        def fake_gate(op_label, args, severity):
            captured["op_label"] = op_label
            captured["severity"] = severity
            return None

        with patch(
            "server.handlers._authoring_utils.require_autonomous_mode",
            side_effect=fake_gate,
        ):
            with patch(
                "server.handlers._authoring_utils.attach_reload_hint",
                side_effect=lambda r, t, o: r,
            ):
                gate_and_wrap(
                    args={},
                    operation="delete_page",
                    tool_name="11_PBIP_Authoring",
                    dispatch_fn=_success_dispatch,
                    pbip_path_or_defn=None,
                    read_only_ops=set(),
                    catastrophic_ops={"delete_page"},
                )
        assert captured["severity"] == Severity.CATASTROPHIC
        assert captured["op_label"] == "11_PBIP_Authoring:delete_page"
