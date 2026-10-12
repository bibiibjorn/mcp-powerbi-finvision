"""Model.ValueFilterBehavior read/write — ModelPropertiesManager + handler dispatch.

TOM is mocked at the manager boundary (no live AMO/CLR). The ValueFilterBehaviorType
enum is injected via ``sys.modules`` with the ``Microsoft`` /
``Microsoft.AnalysisServices`` parents chained, because the manager resolves it
with a lazy ``import Microsoft.AnalysisServices.Tabular as TOM`` inside the method
body (Python resolves ``import x.y.z as Z`` by attribute lookup on the parent).
"""

import sys
import types

import pytest

from core.analysis.model_settings_validator import (
    ModelSettingsValidator,
    VALUE_FILTER_BEHAVIOR_MIN_CL,
)
from core.autonomous.gating import invalidate_policy_cache
from core.operations.model_properties_manager import ModelPropertiesManager
import server.handlers.model_operations_handler as moh

# ---- Fakes ---------------------------------------------------------------


class _FakeEnum:
    """Stand-in for the CLR ValueFilterBehaviorType enum members."""

    Automatic = "Automatic"
    Independent = "Independent"
    Coalesced = "Coalesced"


class _FakeModel:
    def __init__(self, behavior="Automatic", expose_behavior=True, save_raises=False):
        if expose_behavior:
            self.ValueFilterBehavior = behavior
        self.Culture = "en-US"
        self.DefaultMode = "Import"
        self.save_calls = 0
        self.undo_calls = 0
        self._save_raises = save_raises

    def SaveChanges(self):
        self.save_calls += 1
        if self._save_raises:
            raise RuntimeError("save blew up")

    def UndoLocalChanges(self):
        self.undo_calls += 1


class _FakeDb:
    def __init__(self, compatibility_level=1606):
        self.CompatibilityLevel = compatibility_level


class _FakeServer:
    def __init__(self):
        self.disconnected = False

    def Disconnect(self):
        self.disconnected = True


def _mgr(model, db=None, server=None):
    """Manager with the AMO acquisition helper stubbed out."""
    mgr = ModelPropertiesManager()
    srv = server or _FakeServer()
    mgr._get_server_db_model = lambda: (srv, db or _FakeDb(), model, None)  # type: ignore[method-assign]
    return mgr, srv


@pytest.fixture
def fake_tom(monkeypatch):
    """Inject a TOM module exposing ValueFilterBehaviorType."""
    tabular = types.SimpleNamespace(ValueFilterBehaviorType=_FakeEnum)
    parent = types.SimpleNamespace(Tabular=tabular)
    grand = types.SimpleNamespace(AnalysisServices=parent)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", tabular)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices", parent)
    monkeypatch.setitem(sys.modules, "Microsoft", grand)
    return tabular


# ---- get_properties ------------------------------------------------------


def test_get_properties_returns_behavior_name():
    mgr, srv = _mgr(_FakeModel(behavior="Independent"), _FakeDb(1606))
    res = mgr.get_properties()

    assert res["success"] is True
    assert res["value_filter_behavior"] == "Independent"
    assert res["value_filter_behavior_supported"] is True
    assert res["compatibility_level"] == 1606
    assert res["culture"] == "en-US"
    assert res["default_mode"] == "Import"
    # Independent is correct -> no advisory
    assert "advisory" not in res
    assert srv.disconnected is True


def test_get_properties_maps_raw_clr_int_to_name():
    """Some pythonnet builds hand back the underlying enum int — never leak it."""
    mgr, _ = _mgr(_FakeModel(behavior=2))
    res = mgr.get_properties()
    assert res["value_filter_behavior"] == "Coalesced"


def test_get_properties_advises_on_automatic():
    mgr, _ = _mgr(_FakeModel(behavior="Automatic"))
    res = mgr.get_properties()
    assert res["value_filter_behavior"] == "Automatic"
    assert "SUMMARIZECOLUMNS" in res["advisory"]


def test_get_properties_when_property_absent():
    mgr, _ = _mgr(_FakeModel(expose_behavior=False))
    res = mgr.get_properties()
    assert res["success"] is True
    assert res["value_filter_behavior"] is None
    assert res["value_filter_behavior_supported"] is False


def test_get_properties_connection_error():
    mgr = ModelPropertiesManager()
    mgr._get_server_db_model = lambda: (None, None, None, "Not connected")  # type: ignore[method-assign]
    res = mgr.get_properties()
    assert res["success"] is False
    assert res["error_type"] == "connection_error"


# ---- set_value_filter_behavior ------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("Automatic", "Automatic"),
        ("independent", "Independent"),
        ("COALESCED", "Coalesced"),
    ],
)
def test_set_maps_each_value_and_saves_once(fake_tom, given, expected):
    model = _FakeModel(behavior="Automatic")
    mgr, srv = _mgr(model)

    res = mgr.set_value_filter_behavior(behavior=given)

    assert res["success"] is True
    assert res["value_filter_behavior"] == expected
    assert res["previous_value_filter_behavior"] == "Automatic"
    assert model.ValueFilterBehavior == expected
    assert model.save_calls == 1
    assert model.undo_calls == 0
    assert srv.disconnected is True


def test_set_invalid_value_does_not_save(fake_tom):
    model = _FakeModel()
    mgr, _ = _mgr(model)

    res = mgr.set_value_filter_behavior(behavior="Coalescent")

    assert res["success"] is False
    assert res["error_type"] == "invalid_parameters"
    assert res["valid_values"] == ["Automatic", "Independent", "Coalesced"]
    assert model.save_calls == 0
    # Validation happens before the model is even acquired.
    assert model.ValueFilterBehavior == "Automatic"


def test_set_missing_value_does_not_save(fake_tom):
    model = _FakeModel()
    mgr, _ = _mgr(model)
    res = mgr.set_value_filter_behavior()
    assert res["error_type"] == "invalid_parameters"
    assert model.save_calls == 0


def test_set_when_tom_property_absent_reports_unsupported(fake_tom):
    model = _FakeModel(expose_behavior=False)
    mgr, _ = _mgr(model)

    res = mgr.set_value_filter_behavior(behavior="Independent")

    assert res["success"] is False
    assert res["error_type"] == "unsupported_by_amo"
    assert res["required_compatibility_level"] == 1606
    assert model.save_calls == 0


def test_set_when_enum_type_absent_reports_unsupported(monkeypatch):
    """Older AMO DLL: the property exists but ValueFilterBehaviorType does not."""
    tabular = types.SimpleNamespace()  # no ValueFilterBehaviorType
    parent = types.SimpleNamespace(Tabular=tabular)
    grand = types.SimpleNamespace(AnalysisServices=parent)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", tabular)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices", parent)
    monkeypatch.setitem(sys.modules, "Microsoft", grand)

    model = _FakeModel()
    mgr, _ = _mgr(model)
    res = mgr.set_value_filter_behavior(behavior="Independent")

    assert res["success"] is False
    assert res["error_type"] == "unsupported_by_amo"
    assert model.save_calls == 0


def test_set_low_compatibility_level_blocked(fake_tom):
    model = _FakeModel()
    mgr, _ = _mgr(model, _FakeDb(compatibility_level=1550))

    res = mgr.set_value_filter_behavior(behavior="Independent")

    assert res["success"] is False
    assert res["error_type"] == "compatibility_level_too_low"
    assert res["compatibility_level"] == 1550
    assert res["required_compatibility_level"] == 1606
    assert model.save_calls == 0
    assert model.ValueFilterBehavior == "Automatic"


def test_set_save_failure_undoes_local_changes(fake_tom):
    model = _FakeModel(save_raises=True)
    mgr, srv = _mgr(model)

    res = mgr.set_value_filter_behavior(behavior="Independent")

    assert res["success"] is False
    assert res["error_type"] == "tom_error"
    assert model.undo_calls == 1
    assert srv.disconnected is True


# ---- handler dispatch + gating ------------------------------------------


@pytest.fixture
def _gate_off(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    invalidate_policy_cache()
    yield
    invalidate_policy_cache()


def test_handler_get_properties_is_gate_skipped(monkeypatch):
    """Read-only op: must not be gated even under STRICT."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    invalidate_policy_cache()
    monkeypatch.setattr(
        moh._model_properties,
        "get_properties",
        lambda: {"success": True, "value_filter_behavior": "Coalesced"},
    )

    res = moh.handle_model_operations({"object_type": "model", "operation": "get_properties"})
    assert res["success"] is True
    assert res["value_filter_behavior"] == "Coalesced"
    invalidate_policy_cache()


def test_handler_routes_set_value_filter_behavior(_gate_off, monkeypatch):
    calls = []

    def _spy(behavior=None):
        calls.append(behavior)
        return {"success": True, "value_filter_behavior": behavior}

    monkeypatch.setattr(moh._model_properties, "set_value_filter_behavior", _spy)

    res = moh.handle_model_operations(
        {
            "object_type": "model",
            "operation": "set_value_filter_behavior",
            "behavior": "Independent",
        }
    )
    assert res["success"] is True
    assert calls == ["Independent"]


def test_handler_set_blocked_under_strict_without_confirm(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    invalidate_policy_cache()
    called = []
    monkeypatch.setattr(
        moh._model_properties,
        "set_value_filter_behavior",
        lambda behavior=None: called.append(behavior) or {"success": True},
    )

    res = moh.handle_model_operations(
        {
            "object_type": "model",
            "operation": "set_value_filter_behavior",
            "behavior": "Independent",
        }
    )
    assert res.get("success") is False
    assert called == []
    invalidate_policy_cache()


def test_handler_set_allowed_under_strict_with_confirm(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    invalidate_policy_cache()
    monkeypatch.setattr(
        moh._model_properties,
        "set_value_filter_behavior",
        lambda behavior=None: {"success": True, "value_filter_behavior": behavior},
    )

    res = moh.handle_model_operations(
        {
            "object_type": "model",
            "operation": "set_value_filter_behavior",
            "behavior": "Independent",
            "confirm_destructive": True,
        }
    )
    assert res["success"] is True
    invalidate_policy_cache()


def test_handler_rejects_unknown_model_operation(_gate_off):
    res = moh.handle_model_operations({"object_type": "model", "operation": "nope"})
    assert res["success"] is False
    assert "get_properties" in res["valid_operations"]
    assert "set_value_filter_behavior" in res["valid_operations"]


def test_schema_exposes_behavior_arg():
    from server.tool_schemas import TOOL_SCHEMAS

    prop = TOOL_SCHEMAS["model_operations"]["properties"]["behavior"]
    assert prop["enum"] == ["Automatic", "Independent", "Coalesced"]


# ---- integrity finding (Deliverable 3) ----------------------------------


@pytest.mark.parametrize("behavior", ["Automatic", "Coalesced", "automatic"])
def test_validator_flags_legacy_behavior(behavior):
    findings = ModelSettingsValidator().validate(
        {
            "value_filter_behavior": behavior,
            "value_filter_behavior_supported": True,
            "compatibility_level": 1606,
        }
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.finding_id == "VALUE_FILTER_BEHAVIOR_NOT_INDEPENDENT"
    # 2026-10-03 D-18: 'Automatic' is Independent on models created 2025+ -> a heads-up (low);
    # an explicit 'Coalesced' is the legacy behaviour -> medium
    assert f.severity == ("medium" if behavior == "Coalesced" else "low")
    assert f.object_type == "Model"
    assert "SUMMARIZECOLUMNS" in f.description
    assert "set_value_filter_behavior" in f.recommendation


def test_validator_silent_on_independent():
    assert (
        ModelSettingsValidator().validate(
            {
                "value_filter_behavior": "Independent",
                "value_filter_behavior_supported": True,
                "compatibility_level": 1606,
            }
        )
        == []
    )


@pytest.mark.parametrize(
    "model",
    [
        {},  # nothing known
        {"value_filter_behavior": None, "compatibility_level": 1606},
        # SDK/model cannot express it — must NOT fire (the bpa.json false positive)
        {"value_filter_behavior": None, "value_filter_behavior_supported": False},
        # Below the minimum CL
        {
            "value_filter_behavior": "Automatic",
            "value_filter_behavior_supported": True,
            "compatibility_level": VALUE_FILTER_BEHAVIOR_MIN_CL - 1,
        },
        # Unparseable CL — fail silent rather than guess
        {"value_filter_behavior": "Automatic", "compatibility_level": "n/a"},
    ],
)
def test_validator_fails_silent_when_unknowable(model):
    assert ModelSettingsValidator().validate(model) == []


def test_orchestrator_wires_model_settings_findings():
    from core.orchestration.analysis_orchestrator import AnalysisOrchestrator

    class _Props:
        @staticmethod
        def get_properties():
            return {
                "success": True,
                "value_filter_behavior": "Automatic",
                "value_filter_behavior_supported": True,
                "compatibility_level": 1606,
            }

    res = AnalysisOrchestrator(config={})._run_model_settings_analysis(properties_manager=_Props())
    assert res["success"] is True
    assert res["finding_count"] == 1
    assert res["findings"][0]["finding_id"] == "VALUE_FILTER_BEHAVIOR_NOT_INDEPENDENT"
    assert res["properties"]["value_filter_behavior"] == "Automatic"


def test_orchestrator_propagates_properties_failure():
    from core.orchestration.analysis_orchestrator import AnalysisOrchestrator

    class _Props:
        @staticmethod
        def get_properties():
            return {"success": False, "error": "Not connected", "error_type": "connection_error"}

    res = AnalysisOrchestrator(config={})._run_model_settings_analysis(properties_manager=_Props())
    assert res["success"] is False
    assert res["error_type"] == "connection_error"
