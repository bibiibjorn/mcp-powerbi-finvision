"""Regression: query_handler error returns must carry an `error_type`.

The dispatch-layer `enrich_error_response` no-ops on any result lacking
`error_type`, so a bare `{success: False, error: ...}` is a structural dead end
for an autonomous caller — no recovery hint can ever attach. Every failure path
here must set a machine-actionable `error_type`.
"""

import types


import server.handlers.query_handler as qh


def test_run_dax_missing_query_has_error_type(monkeypatch):
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True)
    out = qh.handle_run_dax({})
    assert out["success"] is False
    assert out.get("error_type"), "missing error_type on missing-query path"


def test_trace_missing_query_has_error_type(monkeypatch):
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True)
    out = qh.handle_run_dax_trace({})
    assert out["success"] is False
    assert out.get("error_type")


def test_warm_cache_missing_args_has_error_type(monkeypatch):
    monkeypatch.setattr(
        qh.connection_state, "query_executor", types.SimpleNamespace(), raising=False
    )
    out = qh._handle_warm_cache({})
    assert out["success"] is False
    assert out.get("error_type")


def test_test_rls_missing_role_has_error_type(monkeypatch):
    import core.infrastructure.connection_state as cs

    monkeypatch.setattr(cs.connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(cs.connection_state, "rls_manager", object(), raising=False)
    out = qh._handle_test_rls({})
    assert out["success"] is False
    assert out.get("error_type")


def test_test_rls_not_connected_has_error_type(monkeypatch):
    import core.infrastructure.connection_state as cs

    monkeypatch.setattr(cs.connection_state, "is_connected", lambda: False)
    out = qh._handle_test_rls({})
    assert out["success"] is False
    assert out.get("error_type") == "not_connected"


def test_execute_info_query_generic_error_has_error_type(monkeypatch):
    """execute_info_query's generic except must tag a machine-actionable type."""
    from core.infrastructure import query_executor as qe

    class _Stub(qe.OptimizedQueryExecutor):
        def __init__(self):
            pass

        def validate_and_execute_dax(self, *a, **k):
            raise RuntimeError("boom in SE")

    stub = _Stub()
    out = stub.execute_info_query("VIEW.MEASURES")
    assert out["success"] is False
    assert out.get("error_type"), "generic INFO-query failure has no error_type"
