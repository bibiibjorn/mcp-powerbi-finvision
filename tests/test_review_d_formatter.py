"""2026-09-22 deep review, slice D — DAX Formatter client + format_dax (D-20).

Sending DAX to daxformatter.com is opt-in; formatted text is written back only
when it is token-equivalent to the original; line endings follow the original;
work stops at a time budget.
"""

from unittest.mock import MagicMock

import pytest

from core.dax import dax_formatter_client as dfc


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# --- opt-in gate ---------------------------------------------------------------------------


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(dfc.ENABLE_ENV_VAR, raising=False)
    from core.config.config_manager import config

    monkeypatch.setattr(config, "get", lambda key, default=None: default)
    assert dfc.is_enabled() is False


@pytest.mark.parametrize("val,expected", [("1", True), ("true", True), ("0", False), ("no", False)])
def test_env_var_controls_opt_in(monkeypatch, val, expected):
    monkeypatch.setenv(dfc.ENABLE_ENV_VAR, val)
    assert dfc.is_enabled() is expected


def test_format_dax_refuses_to_send_when_disabled(monkeypatch):
    from server.handlers import tmdl_handler

    monkeypatch.setenv(dfc.ENABLE_ENV_VAR, "0")
    import requests

    called = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: called.append(1))
    res = tmdl_handler.handle_tmdl_operations({"operation": "format_dax", "format_all": True})
    assert res["success"] is False
    assert res["error_type"] == "external_formatter_disabled"
    assert "daxformatter.com" in res["error"]
    assert called == []


# --- token equivalence ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("sum(Sales[Amount])", "SUM ( Sales[Amount] )"),
        ("CALCULATE([S],'T'[a]=1)//c", "CALCULATE (\r\n    [S],\r\n    'T'[a] = 1\r\n)"),
        ("SUM(Sales[Amount])", "SUM ( 'Sales'[Amount] )"),
        ("1.50 + .5", "1.5 + 0.5"),
    ],
)
def test_token_equivalent_ignores_layout_case_and_quoting(a, b):
    ok, why = dfc.token_equivalent(a, b)
    assert ok, why


@pytest.mark.parametrize(
    "a,b",
    [
        ("SUM(Sales[Amount])", "<html>Service unavailable</html>"),
        ('IF([a], "x", "y")', 'IF ( [a], "X", "y" )'),
        ("[a] + 1", "[a] + 2"),
        ("[a] + 1", "[a]"),
    ],
)
def test_token_equivalent_rejects_changed_dax(a, b):
    ok, why = dfc.token_equivalent(a, b)
    assert not ok and why


def test_line_endings_follow_the_original(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp("SUM (\r\n    T[a]\r\n)"))
    out = dfc.format_one("SUM(T[a])")
    assert "\r\n" not in out["formatted"] and out["token_equivalent"] is True
    out = dfc.format_one("SUM(\r\nT[a])")
    assert "\r\n" in out["formatted"]


def test_is_unchanged_ignores_crlf():
    assert dfc.is_unchanged("SUM(T[a])\n", "SUM(T[a])\r\n")


def test_caller_app_sent(monkeypatch):
    import requests

    seen = {}

    def post(url, json=None, timeout=None):
        seen.update(json)
        return _Resp("SUM ( T[a] )")

    monkeypatch.setattr(requests, "post", post)
    dfc.format_one("SUM(T[a])")
    assert seen["CallerApp"] == "MCP-PowerBi-Finvision"


def test_format_many_time_budget_returns_remaining(monkeypatch):
    monkeypatch.setattr(
        dfc, "format_one", lambda e, **k: {"success": True, "formatted": e + " ", "original": e}
    )
    out = dfc.format_many(
        [{"key": f"T[m{i}]", "expression": "1"} for i in range(3)], delay_s=0, time_budget_s=0
    )
    assert out["timed_out"] is True and out["remaining"] == ["T[m0]", "T[m1]", "T[m2]"]
    assert out["success"] is False


# --- handler write guard ----------------------------------------------------------------------


def _live_model(expr):
    m = MagicMock()
    m.name = "M1"
    m.expression = expr
    t = MagicMock()
    t.name = "T"
    t.measures = [m]
    model = MagicMock()
    model.tables = [t]
    return model


def test_format_dax_never_writes_non_equivalent_text(monkeypatch):
    from core.infrastructure.connection_state import connection_state
    from server.handlers import tmdl_handler
    import core.infrastructure.live_model_facade as lmf
    import core.operations.measure_operations as mo

    monkeypatch.setenv(dfc.ENABLE_ENV_VAR, "1")
    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(lmf, "build_live_model", lambda: _live_model("SUM(T[a])"))
    writer = MagicMock()
    writer.return_value.execute.return_value = {"success": True}
    monkeypatch.setattr(mo, "MeasureOperationsHandler", writer)
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp("<html>Rate limited</html>"))

    res = tmdl_handler.handle_tmdl_operations({"operation": "format_dax", "format_all": True})
    assert res["stats"]["written"] == 0
    assert res["stats"]["rejected_not_equivalent"] == 1
    assert res["success"] is False
    writer.return_value.execute.assert_not_called()


def test_format_dax_writes_equivalent_text(monkeypatch):
    from core.infrastructure.connection_state import connection_state
    from server.handlers import tmdl_handler
    import core.infrastructure.live_model_facade as lmf
    import core.operations.measure_operations as mo

    monkeypatch.setenv(dfc.ENABLE_ENV_VAR, "1")
    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(lmf, "build_live_model", lambda: _live_model("SUM(T[a])"))
    writer = MagicMock()
    writer.return_value.execute.return_value = {"success": True}
    monkeypatch.setattr(mo, "MeasureOperationsHandler", writer)
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp("SUM ( T[a] )"))

    res = tmdl_handler.handle_tmdl_operations({"operation": "format_dax", "format_all": True})
    assert res["stats"]["written"] == 1
    assert res["external_service"] == "daxformatter.com"
    args = writer.return_value.execute.call_args[0][0]
    assert args["expression"] == "SUM ( T[a] )"
