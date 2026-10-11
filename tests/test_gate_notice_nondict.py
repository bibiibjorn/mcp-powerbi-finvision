"""M3: the autonomous_gated decorator must coerce a non-dict handler result
to a dict BEFORE merging, so the gate notice/warning is not silently dropped.
"""

import core.autonomous.gating as gating
from core.autonomous.gating import Severity, autonomous_gated


def _patch_gate_notice(monkeypatch):
    """Make require_autonomous_mode return an advisory notice (not a block)."""
    monkeypatch.setattr(
        gating,
        "require_autonomous_mode",
        lambda op_name, args, severity: {"success": True, "gate_notice": "advisory"},
    )


def test_nondict_result_coerced_and_notice_survives(monkeypatch):
    _patch_gate_notice(monkeypatch)

    payload = [1, 2, 3]

    @autonomous_gated("op.list", Severity.DESTRUCTIVE)
    def handler(args):
        return payload

    result = handler({})

    assert isinstance(result, dict)
    assert result["gate_notice"] == "advisory"
    assert result["result"] == payload
    assert result["success"] is True


def test_dict_result_still_gets_notice_merged(monkeypatch):
    _patch_gate_notice(monkeypatch)

    @autonomous_gated("op.dict", Severity.DESTRUCTIVE)
    def handler(args):
        return {"success": True, "rows": [10]}

    result = handler({})

    assert result["gate_notice"] == "advisory"
    assert result["rows"] == [10]


def test_gate_notice_is_in_copied_keys():
    # Guards the assumption that merge_gate_info copies gate_notice.
    assert "gate_notice" in gating._GATE_INFO_KEYS
