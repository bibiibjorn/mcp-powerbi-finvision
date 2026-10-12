"""Tests for role/OLS impersonation runner (no live AMO/CLR)."""

from core.infrastructure.role_impersonation import (
    _build_impersonation_conn_str,
    run_as_role,
)


def test_build_conn_str_appends_roles():
    s = _build_impersonation_conn_str("Data Source=localhost:51386", ["RoleA", "RoleB"])
    assert s.endswith("Roles=RoleA,RoleB")


def test_build_conn_str_appends_effective_user_name():
    s = _build_impersonation_conn_str("Data Source=localhost:51386", ["RoleA"], "user@tenant.com")
    assert "Roles=RoleA" in s
    assert "EffectiveUserName=user@tenant.com" in s


def test_build_conn_str_no_role_list():
    s = _build_impersonation_conn_str("Data Source=localhost:51386", [])
    assert "Roles=" not in s


def test_run_as_role_disconnected_errors(monkeypatch):
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setattr(connection_state, "is_connected", lambda: False)
    res = run_as_role("EVALUATE {1}", roles=["A"])
    assert res["success"] is False
    assert res["error_type"] == "not_connected"


def test_run_as_role_requires_role_list(monkeypatch):
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    res = run_as_role("EVALUATE {1}", roles=[])
    assert res["success"] is False
    assert res["error_type"] == "invalid_args"


def test_desktop_effective_user_error_is_classified(monkeypatch):
    """PBI Desktop rejects EffectiveUserName — the helper detects that
    specific failure shape and returns error_type=desktop_no_effective_user."""
    from core.infrastructure import role_impersonation as ri

    class _FakeConn:
        def __init__(self, conn_str):
            pass

        def Open(self):
            raise RuntimeError(
                "The following system error occurred: "
                "The name provided is not a properly formed account name."
            )

    monkeypatch.setattr(ri, "_load_adomd", lambda: (_FakeConn, None))
    from core.infrastructure.connection_state import connection_state

    class _FakeMgr:
        connection_string = "Data Source=localhost:1234"

    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(connection_state, "connection_manager", _FakeMgr(), raising=False)

    res = ri.run_as_role("EVALUATE {1}", roles=["RLS"], effective_user_name="user@example.com")
    assert res["success"] is False
    assert res["error_type"] == "desktop_no_effective_user"
    assert "Power BI Desktop does not support" in res["error"]
