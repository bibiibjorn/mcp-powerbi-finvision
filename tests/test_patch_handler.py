"""03_Batch_Operations ``object_type=patch`` through the handler: preview (read-only, never gated),
apply (plan_hash, destructive gate by effect, ONE commit) and the ``from_bpa`` fix path."""

from __future__ import annotations

import pytest

from core.analysis import patch_engine as pe
from core.analysis.bpa_analyzer import BPAAnalyzer, rule_from_dict
from server.handlers import batch_operations_handler as boh
from tests._c_fake_tom import FakeServer, Obj
from tests.test_patch_engine import FakeTom, _tmsl

SEL = 'Measures.Where(Expression.Contains("SUMX") and not IsHidden)'
PATCH = {"DisplayFolder": "{Table.Name}/Iterators"}


@pytest.fixture
def world(monkeypatch):
    tom = FakeTom()
    server = FakeServer()
    commits = []
    monkeypatch.setattr(
        pe.PatchEngine, "_open", lambda self: (server, Obj(CompatibilityLevel=1702), tom, None)
    )
    monkeypatch.setattr(pe, "serialize_model", lambda model: _tmsl())
    monkeypatch.setattr(
        pe,
        "_lane_commit",
        lambda model, *, label, journal_meta=None, **kw: commits.append(label) or {},
    )
    monkeypatch.setattr(pe, "safe_undo", lambda model: None)
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    return tom, commits


def _call(**kw):
    return boh.handle_batch_operations({"object_type": "patch", **kw})


def test_preview_then_apply_with_hash(world):
    tom, commits = world
    pv = _call(select=SEL, patch=PATCH, preview=True)
    assert pv["success"] and pv["preview"] and pv["object_count"] == 2
    res = _call(select=SEL, patch=PATCH, plan_hash=pv["plan_hash"])
    assert res["success"] and res["applied"] == 2
    assert commits == ["patch/apply"]
    assert tom.Tables[0].Measures[0].DisplayFolder == "Sales/Iterators"


def test_apply_without_or_with_stale_hash_is_refused(world):
    _tom, commits = world
    assert _call(select=SEL, patch=PATCH)["error_type"] == "plan_hash_required"
    assert _call(select=SEL, patch=PATCH, plan_hash="f" * 64)["error_type"] == "stale_plan"
    assert commits == []


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"select": SEL},
        {"patch": PATCH},
        {"from_bpa": ["X"], "select": SEL},
        {"from_bpa": 5},
    ],
)
def test_argument_validation(world, kw):
    res = _call(**kw)
    assert res["success"] is False and res["error_type"] == "invalid_parameters"


def test_not_connected(monkeypatch):
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setattr(connection_state, "is_connected", lambda: False)
    assert _call(select=SEL, patch=PATCH, preview=True)["success"] is False


def test_severity_by_effect():
    base = {"high_impact": False, "object_count": 3, "delete_count": 0}
    assert boh._patch_severity(base).name == "DESTRUCTIVE"
    assert boh._patch_severity({**base, "object_count": 51}).name == "CATASTROPHIC"
    assert boh._patch_severity({**base, "delete_count": 11}).name == "CATASTROPHIC"
    assert boh._patch_severity({**base, "high_impact": True}).name == "CATASTROPHIC"


def test_high_impact_property_is_blocked_without_confirmation(world, monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()
    sel, patch = "Relationships", {"CrossFilteringBehavior": "OneDirection"}
    pv = _call(select=sel, patch=patch, preview=True)
    assert pv["success"] and pv["high_impact"] is True
    blocked = _call(select=sel, patch=patch, plan_hash=pv["plan_hash"])
    assert blocked["success"] is False and "confirm_destructive" in str(blocked)
    ok = _call(select=sel, patch=patch, plan_hash=pv["plan_hash"], confirm_destructive=True)
    assert ok["success"] is True


def test_from_bpa_through_the_handler(world, monkeypatch):
    tom, commits = world
    rule, _ = rule_from_dict(
        {
            "ID": "T_HIDE_ITER",
            "Name": "hide iterators",
            "Severity": 1,
            "Scope": "Measure",
            "Expression": 'Expression.Contains("SUMX")',
            "FixExpression": "IsHidden = true",
        }
    )
    analyzer = BPAAnalyzer()
    analyzer.rules.append(rule)
    monkeypatch.setattr(boh, "_patch_bpa_rules", lambda ids: (analyzer, []))
    pv = _call(from_bpa=["T_HIDE_ITER"], preview=True)
    assert pv["success"] and pv["mode"] == "bpa" and pv["operations"]["set"] == 2
    res = _call(from_bpa="T_HIDE_ITER", plan_hash=pv["plan_hash"])
    assert res["success"] and commits == ["patch/apply"]
    assert tom.Tables[1].Measures[0].IsHidden is True
