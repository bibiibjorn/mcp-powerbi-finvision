"""Regression: the optimizer apply path must call DAXInjector.upsert_measure
with the REAL parameter name `dax_expression`, not `expression`. The wrong
kwarg raised TypeError the moment any auto-apply feature invoked it — the
Phase 3 closed-loop work depends on this path working.

2026-09-22 review E-01: the injector must be the ADOMD-bound one the connection
state manages (``connection_state.dax_injector``), never
``DAXInjector(connection_state)`` — that one has no ``.State`` /
``.ConnectionString`` and fails every write with ``connection_lost``.
"""

from types import SimpleNamespace


class _FakeInjector:
    def __init__(self, capture):
        self.capture = capture

    def upsert_measure(
        self,
        table_name,
        measure_name,
        dax_expression,
        display_folder=None,
        description=None,
        format_string=None,
        format_string_expression=None,
        data_category=None,
        create_if_missing=True,
    ):
        self.capture["dax_expression"] = dax_expression
        self.capture["measure_name"] = measure_name
        self.capture["table_name"] = table_name
        self.capture["create_if_missing"] = create_if_missing
        return {"success": True}


def _state(capture):
    return SimpleNamespace(dax_injector=_FakeInjector(capture), is_connected=lambda: True)


def test_measure_applier_uses_managed_injector_and_dax_expression_kwarg():
    from core.dax.optimizer.measure_applier import MeasureApplier

    capture = {}
    result = MeasureApplier(connection_state=_state(capture)).apply(
        "Sales", "Fact", "SUM(Fact[Amount])"
    )
    assert result["success"] is True, f"apply failed (kwarg mismatch?): {result}"
    assert capture["dax_expression"] == "SUM(Fact[Amount])"
    assert capture["create_if_missing"] is False  # never creates a typo'd measure


def test_pipeline_apply_to_model_uses_managed_injector(monkeypatch):
    from core.dax.optimizer.pipeline import OptimizationPipeline, OptimizationResult

    capture = {}
    pipe = OptimizationPipeline.__new__(OptimizationPipeline)
    pipe._connection_state = _state(capture)
    res = OptimizationResult.__new__(OptimizationResult)
    res.original_dax = "X"
    res.final_dax = "SUM(F[A])"
    res.applied = False
    res.apply_error = None
    out = pipe._apply_to_model(res, measure_name="M", table_name="F")

    assert out.apply_error is None, f"apply raised: {out.apply_error}"
    assert out.applied is True
    assert capture["dax_expression"] == "SUM(F[A])"


def test_measure_applier_is_gated_under_strict(monkeypatch):
    from core.autonomous import gating
    from core.dax.optimizer.measure_applier import MeasureApplier

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    monkeypatch.setenv("MCP_GATE_ELICITATION", "0")
    gating.invalidate_policy_cache()
    capture = {}
    out = MeasureApplier(connection_state=_state(capture)).apply("M", "F", "1")
    assert out["success"] is False and capture == {}
    out = MeasureApplier(connection_state=_state(capture)).apply(
        "M", "F", "1", gate_args={"confirm_destructive": True}
    )
    assert out["success"] is True and capture["dax_expression"] == "1"


def test_codemod_default_persister_is_the_managed_injector(monkeypatch):
    """The real `_default_persister()` wiring (was `# pragma: no cover`)."""
    from core.codemods import transaction
    from core.infrastructure import connection_state as cs_mod

    sentinel = object()
    monkeypatch.setattr(cs_mod.connection_state, "dax_injector", sentinel, raising=False)
    assert transaction._default_persister() is sentinel
