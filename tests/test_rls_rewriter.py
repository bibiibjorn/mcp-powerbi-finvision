"""Tests for core.dax.rls_rewriter — DAX filter rewriting for as-user RLS sim."""

import pytest

from core.dax.rls_rewriter import (
    build_as_user_query,
    rewrite_filter_expression,
    simulate_as_user,
)

# ---------- rewrite_filter_expression ----------


def test_rewrite_replaces_userprincipalname():
    out = rewrite_filter_expression("[User] = USERPRINCIPALNAME()", "vic@x.com")
    assert out == '[User] = "vic@x.com"'


def test_rewrite_replaces_username_and_userobjectid():
    out = rewrite_filter_expression("USERNAME() = [u] && USEROBJECTID() = [oid]", "a@b.c")
    assert out == '"a@b.c" = [u] && "a@b.c" = [oid]'


def test_rewrite_handles_whitespace_inside_call():
    out = rewrite_filter_expression("USERPRINCIPALNAME (  )", "x@y.z")
    assert out == '"x@y.z"'


def test_rewrite_is_case_insensitive():
    out = rewrite_filter_expression("userprincipalname()", "x@y.z")
    assert out == '"x@y.z"'


def test_rewrite_skips_string_literals():
    """Don't replace inside a DAX string literal."""
    src = '"USERPRINCIPALNAME() should not change here" + USERPRINCIPALNAME()'
    out = rewrite_filter_expression(src, "x@y.z")
    assert '"USERPRINCIPALNAME() should not change here"' in out
    assert '+ "x@y.z"' in out


def test_rewrite_handles_double_quote_escape_in_user_string():
    out = rewrite_filter_expression("USERPRINCIPALNAME()", 'user"with"quote')
    # DAX escapes " as ""
    assert out == '"user""with""quote"'


def test_rewrite_preserves_unrelated_expressions():
    src = "CALCULATE(SUM(T[A]), T[B] = 1)"
    assert rewrite_filter_expression(src, "x@y.z") == src


def test_rewrite_does_not_match_word_prefix():
    """CUSTOM_USERNAME() shouldn't be rewritten."""
    out = rewrite_filter_expression("CUSTOM_USERNAME()", "x@y.z")
    assert out == "CUSTOM_USERNAME()"


def test_rewrite_with_empty_inputs():
    assert rewrite_filter_expression("", "x@y.z") == ""
    assert rewrite_filter_expression("USERPRINCIPALNAME()", "") == "USERPRINCIPALNAME()"


# ---------- build_as_user_query ----------


def test_build_wraps_simple_query_with_one_filter():
    q = build_as_user_query(
        'EVALUATE ROW("x", SUM(Sales[Amount]))',
        [{"table": "Sales", "filter_expression": "Sales[User] = USERPRINCIPALNAME()"}],
        "vic@x.com",
    )
    assert "CALCULATETABLE(" in q
    assert "FILTER('Sales',\nSales[User] = \"vic@x.com\"\n)" in q
    assert q.startswith("EVALUATE")


def test_build_wraps_with_multiple_filters():
    q = build_as_user_query(
        'EVALUATE ROW("x", 1)',
        [
            {"table": "A", "filter_expression": "A[u] = USERPRINCIPALNAME()"},
            {"table": "B", "filter_expression": "B[u] = USERPRINCIPALNAME()"},
        ],
        "v@x.c",
    )
    assert q.count("FILTER(") == 2
    assert "FILTER('A'," in q
    assert "FILTER('B'," in q


def test_build_with_no_filters_returns_original():
    src = 'EVALUATE ROW("x", 1)'
    assert build_as_user_query(src, [], "v@x.c") == src


def test_build_preserves_define_block():
    q = build_as_user_query(
        'DEFINE MEASURE T[m] = SUM(T[v])\nEVALUATE ROW("x", [m])',
        [{"table": "T", "filter_expression": "T[u] = USERPRINCIPALNAME()"}],
        "v@x.c",
    )
    assert q.startswith("DEFINE")
    assert "EVALUATE\nCALCULATETABLE(" in q
    assert 'T[u] = "v@x.c"' in q


def test_build_handles_already_quoted_table_name():
    q = build_as_user_query(
        'EVALUATE ROW("x", 1)',
        [{"table": "'Already Quoted'", "filter_expression": "[u] = USERPRINCIPALNAME()"}],
        "v@x.c",
    )
    # Should not double-quote
    assert "FILTER('Already Quoted'," in q
    assert "FILTER(''Already Quoted''," not in q


def test_build_raises_on_empty_query():
    with pytest.raises(ValueError):
        build_as_user_query("", [{"table": "T", "filter_expression": "1=1"}], "v@x.c")


def test_build_raises_on_define_without_evaluate():
    with pytest.raises(ValueError):
        build_as_user_query(
            "DEFINE MEASURE T[m] = 1",  # no EVALUATE
            [{"table": "T", "filter_expression": "T[u] = USERPRINCIPALNAME()"}],
            "v@x.c",
        )


# ---------- simulate_as_user (handler-level smoke tests) ----------


def test_simulate_requires_role_name(monkeypatch):
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    res = simulate_as_user('EVALUATE ROW("x", 1)', role_name="", as_user="v@x.c")
    assert res["success"] is False
    assert res["error_type"] == "invalid_args"


def test_simulate_requires_as_user(monkeypatch):
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    res = simulate_as_user('EVALUATE ROW("x", 1)', role_name="RLS", as_user="")
    assert res["success"] is False
    assert res["error_type"] == "invalid_args"


def test_simulate_disconnected(monkeypatch):
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setattr(connection_state, "is_connected", lambda: False)
    res = simulate_as_user('EVALUATE ROW("x", 1)', role_name="RLS", as_user="v@x.c")
    assert res["success"] is False
    assert res["error_type"] == "not_connected"


def test_simulate_missing_role(monkeypatch):
    from core.infrastructure.connection_state import connection_state

    class _FakeRlsMgr:
        def list_roles(self):
            return {"success": True, "roles": [{"name": "OtherRole"}]}

    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(connection_state, "rls_manager", _FakeRlsMgr(), raising=False)
    res = simulate_as_user('EVALUATE ROW("x", 1)', role_name="RLS", as_user="v@x.c")
    assert res["success"] is False
    assert res["error_type"] == "invalid_args"
    assert "available_roles" in res


def test_simulate_full_flow_with_mocked_executor(monkeypatch):
    from core.infrastructure.connection_state import connection_state

    class _FakeRlsMgr:
        def list_roles(self):
            return {
                "success": True,
                "roles": [
                    {
                        "name": "RLS",
                        "table_permissions": [
                            {
                                "table": "Sales",
                                "filter_expression": "Sales[u] = USERPRINCIPALNAME()",
                            }
                        ],
                    }
                ],
            }

    captured: dict = {}

    class _FakeExecutor:
        def validate_and_execute_dax(self, query, top_n=0, bypass_cache=True):
            captured["query"] = query
            return {
                "success": True,
                "rows": [{"x": 42}],
                "columns": ["x"],
                "row_count": 1,
                "execution_time_ms": 12.3,
            }

    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(connection_state, "rls_manager", _FakeRlsMgr(), raising=False)
    monkeypatch.setattr(connection_state, "query_executor", _FakeExecutor(), raising=False)

    res = simulate_as_user('EVALUATE ROW("x", 1)', role_name="RLS", as_user="vic@x.com")
    assert res["success"] is True
    assert res["row_count"] == 1
    assert res["simulated"] == {"as_user": "vic@x.com", "role_name": "RLS", "filters_rewritten": 1}
    # The rewritten query should contain the substituted UPN
    assert '"vic@x.com"' in res["rewritten_query"]
    # And the executor should have received that exact query
    assert captured["query"] == res["rewritten_query"]
