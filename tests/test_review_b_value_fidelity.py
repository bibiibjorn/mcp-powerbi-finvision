"""2026-09-22 review, slice B — locale-neutral values + reader materialisation (B-06, B-20).

pythonnet does not convert System.Decimal / System.DateTime; ``str()`` on them
calls .NET ToString() under the CURRENT culture ('1234,56', '22/09/2026 13:45:00'
on en-BE). The old reader stringified every cell, so numbers and bools came back
as strings too, and it always materialised up to 10k rows whatever top_n was.

Compatibility note: callers that parsed those strings (float('1234,56') etc.)
now receive native floats / ints / bools / ISO-8601 strings.
"""

from __future__ import annotations

import datetime as dt
import decimal

import pytest

from core.infrastructure.adomd_utils import clr_to_py, read_rows
from tests.fake_adomd import FakeClrDateTime, FakeClrDecimal, FakeReader, FakeServer, make_state

CS = "Data Source=localhost:50001"


def test_clr_decimal_becomes_a_float_not_a_culture_string():
    value = FakeClrDecimal("1234.56", culture_text="1234,56")
    assert clr_to_py(value) == pytest.approx(1234.56)


def test_clr_datetime_becomes_iso_8601():
    value = FakeClrDateTime(2026, 9, 22, 13, 45, 0)
    assert clr_to_py(value) == "2026-09-22T13:45:00"


@pytest.mark.parametrize(
    "raw,expected",
    [(7, 7), (True, True), (False, False), (1.5, 1.5), ("x", "x"), (None, None)],
)
def test_native_python_values_stay_native(raw, expected):
    out = clr_to_py(raw)
    assert out == expected and type(out) is type(expected)


def test_python_decimal_datetime_and_nan():
    assert clr_to_py(decimal.Decimal("2.50")) == 2.5
    assert clr_to_py(dt.datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"
    assert clr_to_py(float("nan")) is None


def test_executor_rows_are_native_types(monkeypatch):
    server = FakeServer()
    server.respond(
        "Money",
        [
            {
                "[Amount]": FakeClrDecimal("1234.56", "1234,56"),
                "[When]": FakeClrDateTime(2026, 9, 22, 13, 45),
                "[Count]": 7,
                "[Flag]": True,
            }
        ],
    )
    state, _cm, _ = make_state(monkeypatch, {CS: server})
    res = state.query_executor.validate_and_execute_dax("EVALUATE Money", bypass_cache=True)
    row = res["rows"][0]
    assert row["[Amount]"] == pytest.approx(1234.56)
    assert row["[When]"] == "2026-09-22T13:45:00"
    assert row["[Count]"] == 7 and row["[Flag]"] is True


def test_dmv_rows_are_native_and_exposed_as_rows_and_data(monkeypatch):
    server = FakeServer()
    server.respond("DISCOVER_THING", [{"NAME": "2024", "SIZE": 10}])
    state, _cm, _ = make_state(monkeypatch, {CS: server})
    res = state.query_executor.execute_dmv_query("SELECT * FROM $SYSTEM.DISCOVER_THING")
    assert res["success"] is True
    assert res["data"] == res["rows"]
    # A numeric-looking STRING column is no longer coerced to int.
    assert res["rows"][0] == {"NAME": "2024", "SIZE": 10}


def test_reader_honours_max_rows_and_peeks_for_truncation():
    reader = FakeReader([{"[i]": i} for i in range(10)])
    cols, rows, truncated = read_rows(reader, 3)
    assert cols == ["[i]"] and len(rows) == 3 and truncated is True
    reader = FakeReader([{"[i]": i} for i in range(3)])
    _c, rows, truncated = read_rows(reader, 3)
    assert len(rows) == 3 and truncated is False


def test_top_n_caps_the_reader_not_just_the_response(monkeypatch):
    """B-20: DEFINE / TOPN queries were materialised up to 10k rows, then cut to 100."""
    server = FakeServer()
    server.respond("DEFINE", [{"[i]": i} for i in range(500)])
    state, _cm, _ = make_state(monkeypatch, {CS: server})
    res = state.query_executor.validate_and_execute_dax(
        "DEFINE VAR x = 1 EVALUATE Big", top_n=25, bypass_cache=True
    )
    assert res["row_count"] == 25
    assert res["truncated"] is True and res["has_more"] is True


def test_impersonated_rows_use_the_same_conversion(monkeypatch):
    """B-06: run_as_role used to keep ints but stringify Decimals — now identical."""
    from core.infrastructure import role_impersonation as ri
    from core.infrastructure.connection_state import connection_state

    class _Cmd:
        CommandText = ""
        CommandTimeout = 0

        def ExecuteReader(self):
            return FakeReader([{"[v]": FakeClrDecimal("7.5", "7,5"), "[n]": 7}])

    class _Conn:
        def __init__(self, conn_str):
            self.conn_str = conn_str

        def Open(self):
            pass

        def CreateCommand(self):
            return _Cmd()

        def Close(self):
            pass

    class _Mgr:
        connection_string = "Data Source=localhost:1234"

    monkeypatch.setattr(ri, "_load_adomd", lambda: (_Conn, None))
    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(connection_state, "connection_manager", _Mgr(), raising=False)
    res = ri.run_as_role("EVALUATE X", roles=["R"])
    assert res["success"] is True
    assert res["rows"] == [{"[v]": 7.5, "[n]": 7}]
