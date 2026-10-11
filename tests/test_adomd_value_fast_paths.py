"""WP-P wave 2 -- ``clr_to_py`` DateTime / Decimal fast paths (P-09).

DateTime: ONE ``Ticks`` read instead of 8 CLR property calls; Decimal:
``System.Decimal.ToDouble`` resolved once; the CLR type name is cached per real
pythonnet type. Values must be identical to the old per-cell conversion,
including the edge cases (MinValue/MaxValue, 100 ns ticks, DBNull, 28-digit decimals).
"""

from __future__ import annotations

import datetime as dt
import decimal

import pytest

from core.infrastructure import adomd_utils as au
from tests.fake_adomd import FakeClrDateTime, FakeClrDecimal

try:  # real CLR objects only where pythonnet + .NET are present
    import clr  # noqa: F401
    import System

    HAVE_CLR = True
except Exception:  # noqa: BLE001
    HAVE_CLR = False

needs_clr = pytest.mark.skipif(not HAVE_CLR, reason="pythonnet / .NET not available")


def _old_datetime(value):
    """The pre-P-09 component-wise conversion (the reference)."""
    ticks = int(value.Ticks)
    micro = (ticks % 10_000_000) // 10
    return dt.datetime(
        int(value.Year),
        int(value.Month),
        int(value.Day),
        int(value.Hour),
        int(value.Minute),
        int(value.Second),
        micro,
    ).isoformat()


@needs_clr
@pytest.mark.parametrize(
    "make",
    [
        lambda: System.DateTime.MinValue,
        lambda: System.DateTime.MaxValue,
        lambda: System.DateTime(9),  # 900 ns: truncated to 0 us
        lambda: System.DateTime(10),
        lambda: System.DateTime(2026, 9, 22, 13, 45, 0).AddTicks(1234567),
        lambda: System.DateTime(2020, 2, 29, 23, 59, 59, 999),
        lambda: System.DateTime.SpecifyKind(
            System.DateTime(2024, 5, 6, 7, 8, 9), System.DateTimeKind.Utc
        ),
        lambda: System.DateTimeOffset(2024, 5, 6, 7, 8, 9, System.TimeSpan(2, 0, 0)),
        lambda: System.DateTimeOffset.MaxValue,
    ],
)
def test_real_datetime_is_identical_to_the_componentwise_conversion(make):
    value = make()
    assert au.clr_to_py(value) == _old_datetime(value)


@needs_clr
def test_real_datetime_random_ticks_identical():
    import random

    rng = random.Random(3)
    base = System.DateTime(1)
    for _ in range(2000):
        v = base.AddTicks(rng.randrange(0, 3155378975999999999))
        assert au.clr_to_py(v) == _old_datetime(v)


@needs_clr
@pytest.mark.parametrize(
    "text",
    [
        "79228162514264337593543950335",  # Decimal.MaxValue
        "-79228162514264337593543950335",
        "0",
        "0.0000000000000000000000000001",
        "1234567890.1234567890123456789",
        "-0.5",
        "1234.56",
    ],
)
def test_real_decimal_equals_to_double(text):
    value = System.Decimal.Parse(text, System.Globalization.CultureInfo.InvariantCulture)
    assert au.clr_to_py(value) == float(System.Decimal.ToDouble(value))
    assert type(au.clr_to_py(value)) is float


@needs_clr
def test_real_dbnull_and_other_clr_types_unchanged():
    assert au.clr_to_py(System.DBNull.Value) is None
    assert au.clr_to_py(System.TimeSpan(1, 2, 3)) == "01:02:03"
    assert au.clr_to_py(System.Int64(2**40)) == 2**40
    assert au.clr_to_py(System.Boolean(True)) is True


@needs_clr
def test_real_clr_types_are_cached_by_python_type():
    au._FAST_KIND.clear()
    au.clr_to_py(System.DateTime(2024, 1, 1))
    au.clr_to_py(System.Decimal(5))
    assert set(au._FAST_KIND.values()) >= {"DateTime", "Decimal"}


def test_fakes_are_never_type_cached_and_keep_their_own_conversion():
    au._FAST_KIND.clear()
    assert au.clr_to_py(FakeClrDateTime(2026, 9, 22, 13, 45)) == "2026-09-22T13:45:00"
    assert au.clr_to_py(FakeClrDecimal("1234.56", "1234,56")) == pytest.approx(1234.56)
    assert au._FAST_KIND == {}  # per-instance GetType() is still honoured


def test_a_system_typed_datetime_out_of_python_range_falls_back_to_str_safely():
    class DateTime:  # looks like a pythonnet type (module "System")
        __module__ = "System"
        Ticks = 3155378975999999999 * 100  # beyond datetime.max -> OverflowError
        Year, Month, Day, Hour, Minute, Second = 12345, 1, 1, 0, 0, 0

        def __str__(self):
            return "out of range"

    assert au.clr_to_py(DateTime()) == "out of range"
    au._FAST_KIND.pop(DateTime, None)


def test_decimal_without_clr_uses_the_text_fallback(monkeypatch):
    monkeypatch.setattr(au, "_SYS_TODOUBLE", None)
    monkeypatch.setattr(au, "_SYS_TODOUBLE_RESOLVED", True)
    assert au._clr_decimal_to_float(FakeClrDecimal("1", "1234,56")) == pytest.approx(1234.56)


def test_python_decimal_still_float_and_resolver_is_resolved_once(monkeypatch):
    assert au.clr_to_py(decimal.Decimal("2.50")) == 2.5
    calls = {"n": 0}
    monkeypatch.setattr(au, "_SYS_TODOUBLE_RESOLVED", False)
    monkeypatch.setattr(au, "_SYS_TODOUBLE", None)

    class _Sys:
        class Decimal:
            @staticmethod
            def ToDouble(v):
                calls["n"] += 1
                return 7.5

    import sys
    import types

    mod = types.ModuleType("System")
    mod.Decimal = _Sys.Decimal
    monkeypatch.setitem(sys.modules, "System", mod)
    assert au._clr_decimal_to_float(object()) == 7.5
    assert au._clr_decimal_to_float(object()) == 7.5
    assert calls["n"] == 2  # ToDouble per cell, but the import/resolve happened once
    assert au._SYS_TODOUBLE_RESOLVED is True
