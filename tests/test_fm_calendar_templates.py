"""F-20e: ``table generate_calendar calendar_template`` (standard | iso_week | 445 | 454 | 544).

The DAX itself cannot run here (no engine): the tests pin the generated structure, the argument
validation and that the retail-calendar arithmetic the DAX encodes is right (a Python mirror of the
same formulas over known dates). LIVE CHECK: run the generated expression with ``04_Run_DAX
mode=validate`` and spot-check a few dates before relating the table.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from core.operations.table_operations import TableOperationsHandler as T


def balanced(text: str) -> bool:
    depth = 0
    in_str = False
    for ch in text:
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            depth += (ch == "(") - (ch == ")")
            if depth < 0:
                return False
    return depth == 0 and not in_str


def test_standard_is_unchanged_and_default():
    std = T._generate_calendar_dax(2024, 2025)
    assert std == T._generate_calendar_dax(2024, 2025, template="standard")
    assert std.startswith("ADDCOLUMNS(\n    CALENDAR(DATE(2024, 1, 1), DATE(2025, 12, 31))")
    assert '"IsCurrentMonth"' in std and "ISOYear" not in std and "FiscalWeek" not in std
    fiscal = T._generate_calendar_dax(2024, 2025, include_fiscal=True, fiscal_start_month=4)
    assert '"FiscalMonth", MOD(MONTH([Date]) - 4 + 12, 12) + 1' in fiscal


def test_iso_week_columns():
    dax = T._generate_calendar_dax(2024, 2025, template="iso_week")
    assert balanced(dax)
    assert '"ISOYear", YEAR([Date] + 4 - WEEKDAY([Date], 2))' in dax
    assert '"ISOWeek", WEEKNUM([Date], 21)' in dax
    assert '"ISOYearWeek"' in dax and '"-W"' in dax
    assert '"Year", YEAR([Date])' in dax  # the standard columns stay


@pytest.mark.parametrize("tpl,a,b", [("445", 4, 8), ("454", 4, 9), ("544", 5, 9)])
def test_retail_template_structure(tpl, a, b):
    dax = T._generate_calendar_dax(2024, 2026, template=tpl, fiscal_start_month=2)
    assert balanced(dax)
    for col in (
        "FiscalYear",
        "FiscalYearStart",
        "FiscalWeek",
        "FiscalQuarterNo",
        "FiscalQuarter",
        "FiscalPeriod",
    ):
        assert f'"{col}"' in dax
    assert "DATE(YEAR([Date]), 2, 1)" in dax and "DATE([FiscalYear], 2, 1)" in dax
    assert f"<= {a}, 1, IF(" in dax and f"<= {b}, 2, 3)" in dax
    assert '"FiscalMonth"' not in dax  # the month-based fiscal columns are not mixed in


def retail(d: date, month: int, pattern: str):
    """Python mirror of the DAX: (fiscal year, week, quarter no, period)."""

    def first_monday(y):
        first = date(y, month, 1)
        return first + timedelta(days=(8 - first.isoweekday()) % 7)

    fy = d.year if d >= first_monday(d.year) else d.year - 1
    week = (d - first_monday(fy)).days // 7 + 1
    q = min((week - 1) // 13 + 1, 4)
    wq = week - (q - 1) * 13
    a, b = int(pattern[0]), int(pattern[1])
    period = (q - 1) * 3 + (1 if wq <= a else 2 if wq <= a + b else 3)
    return fy, week, q, period


def test_retail_arithmetic_mirror():
    # fiscal year 2024 starts on the first Monday of January 2024 = 1 Jan 2024
    assert retail(date(2024, 1, 1), 1, "445") == (2024, 1, 1, 1)
    assert retail(date(2024, 1, 28), 1, "445") == (2024, 4, 1, 1)  # last day of week 4
    assert retail(date(2024, 1, 29), 1, "445")[1:] == (5, 1, 2)  # week 5 starts period 2
    assert retail(date(2024, 3, 31), 1, "445")[1:] == (13, 1, 3)
    assert retail(date(2024, 4, 1), 1, "445")[1:] == (14, 2, 4)  # quarter 2
    assert retail(date(2024, 12, 29), 1, "445")[1:] == (52, 4, 12)
    # 1 Jan 2025 is a Wednesday: FY2025 starts Monday 6 Jan 2025, so 30 Dec 2024 is week 53 of FY2024
    assert retail(date(2024, 12, 30), 1, "445") == (2024, 53, 4, 12)
    assert retail(date(2025, 1, 6), 1, "445") == (2025, 1, 1, 1)
    assert retail(date(2025, 1, 5), 1, "445")[0] == 2024
    # 5-4-4: week 5 is still period 1; 4-5-4: week 9 is still period 2
    assert retail(date(2024, 1, 29), 1, "544")[3] == 1
    assert retail(date(2024, 2, 26), 1, "454")[3] == 2


class FakeTables:
    def __init__(self):
        self.created = None

    def create_table(self, **kw):
        self.created = kw
        return {"success": True}


@pytest.fixture
def handler(monkeypatch):
    from core.operations import table_operations as mod

    tc = FakeTables()
    monkeypatch.setattr(mod, "get_manager_or_error", lambda name, *a: tc)
    monkeypatch.setattr(
        T, "_mark_as_date_table", staticmethod(lambda crud, name: {"success": True})
    )
    return T(), tc


def test_generate_calendar_passes_the_template(handler):
    h, tc = handler
    out = h._generate_calendar({"start_year": 2024, "end_year": 2025, "calendar_template": "445"})
    assert out["success"] and out["calendar_info"]["calendar_template"] == "445"
    assert out["calendar_info"]["fiscal_start_month"] == 1  # retail default
    assert (
        "FiscalPeriod" in tc.created["expression"] and "template 445" in tc.created["description"]
    )
    h._generate_calendar({"start_year": 2024, "end_year": 2025, "calendar_template": "iso_week"})
    assert "ISOWeek" in tc.created["expression"]
    std = h._generate_calendar({"start_year": 2024, "end_year": 2025})
    assert (
        std["calendar_info"]["calendar_template"] == "standard"
        and std["calendar_info"]["fiscal_start_month"] is None
    )


def test_invalid_template_is_refused_before_a_write(handler):
    h, tc = handler
    out = h._generate_calendar({"calendar_template": "13-month"})
    assert out["error_type"] == "invalid_parameters" and tc.created is None
    assert re.search(r"iso_week", out["error"])
