"""Tests for the impersonated executor wrapper used by debug ops."""

from core.infrastructure.impersonated_executor import (
    ImpersonatedExecutor,
    get_executor_for_args,
)


def test_get_executor_for_args_returns_live_when_no_roles(monkeypatch):
    from core.infrastructure.connection_state import connection_state

    sentinel = object()
    monkeypatch.setattr(connection_state, "query_executor", sentinel, raising=False)
    qe = get_executor_for_args({})
    assert qe is sentinel


def test_get_executor_for_args_returns_impersonated_when_roles(monkeypatch):
    qe = get_executor_for_args({"roles": ["RLS"]})
    assert isinstance(qe, ImpersonatedExecutor)
    assert qe.roles == ["RLS"]


def test_get_executor_for_args_accepts_comma_string():
    qe = get_executor_for_args({"roles": "RoleA, RoleB,RoleC"})
    assert isinstance(qe, ImpersonatedExecutor)
    assert qe.roles == ["RoleA", "RoleB", "RoleC"]


def test_impersonated_executor_returns_executor_shape(monkeypatch):
    from core.infrastructure import role_impersonation as ri

    monkeypatch.setattr(
        ri,
        "run_as_role",
        lambda **kw: {
            "success": True,
            "rows": [{"a": 1}],
            "columns": ["a"],
            "row_count": 1,
            "impersonated": {"roles": kw["roles"], "effective_user_name": None},
            "truncated": False,
        },
    )
    qe = ImpersonatedExecutor(roles=["RLS"])
    res = qe.validate_and_execute_dax("EVALUATE {1}", top_n=10)
    assert res["success"] is True
    assert res["row_count"] == 1
    assert "execution_time_ms" in res
    assert res["impersonated"]["roles"] == ["RLS"]


def test_impersonated_executor_propagates_failure(monkeypatch):
    from core.infrastructure import role_impersonation as ri

    monkeypatch.setattr(
        ri,
        "run_as_role",
        lambda **kw: {
            "success": False,
            "error": "denied",
            "error_type": "ols_blocked",
            "impersonated": {"roles": kw["roles"], "effective_user_name": None},
        },
    )
    qe = ImpersonatedExecutor(roles=["LowPrivRole"])
    res = qe.validate_and_execute_dax("EVALUATE Sales", top_n=10)
    assert res["success"] is False
    assert res["error_type"] == "ols_blocked"
    assert "execution_time_ms" in res
