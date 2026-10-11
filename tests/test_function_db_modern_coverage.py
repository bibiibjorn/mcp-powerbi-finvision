"""Coverage tests for modern DAX functions in the function knowledge DB.

Phase 1.1 of the DAX/modeling intelligence plan: the catalog was missing the
2025-26 surface (visual-calc aggregations, calendar-based time intelligence,
INFO.VIEW.* introspection, TABLEOF). These tests pin the required metadata so a
future edit to functions.json cannot silently drop or mis-classify them.
"""

import pytest

from core.dax.knowledge.function_db import DaxFunctionDatabase


@pytest.fixture
def db():
    DaxFunctionDatabase.reset()
    instance = DaxFunctionDatabase.get()
    yield instance
    DaxFunctionDatabase.reset()


# ── Visual-calculation aggregations (GA May 2026) ───────────────────────────
# These live in visual.json (PBIR), NOT in model measures. They are flagged via
# the dedicated "visual_calculation" category so a measure-context rule can warn
# when one is used in a model measure.

VISUAL_CALC_FUNCS = [
    "RUNNINGSUM",
    "RUNNINGAVERAGE",
    "MOVINGAVERAGE",
    "PERCENTOFPARENT",
    "COLLAPSE",
    "EXPAND",
    "LOOKUPWITHTOTALS",
]


@pytest.mark.parametrize("name", VISUAL_CALC_FUNCS)
def test_visual_calc_function_present_and_categorized(db, name):
    func = db.lookup(name)
    assert func is not None, f"{name} missing from function DB"
    assert func.category == "visual_calculation", (
        f"{name} must use the visual_calculation category so measure-context "
        f"rules can flag it; got {func.category!r}"
    )
    # Visual calcs are evaluated by the visual, never SE-pushed in the model.
    assert func.se_pushable == "fe_only"


# ── Calendar-based / week time intelligence (Preview Sep 2025) ──────────────

CALENDAR_TI_FUNCS = [
    "TOTALWTD",
    "DATESWTD",
    "STARTOFWEEK",
    "ENDOFWEEK",
    "NEXTWEEK",
    "PREVIOUSWEEK",
    "OPENINGBALANCEWEEK",
    "CLOSINGBALANCEWEEK",
]


@pytest.mark.parametrize("name", CALENDAR_TI_FUNCS)
def test_calendar_time_intelligence_present(db, name):
    func = db.lookup(name)
    assert func is not None, f"{name} missing from function DB"
    assert func.category == "time_intelligence"


def test_totalwtd_modifies_filter_context(db):
    # TOTAL* wrappers apply a date filter, so they create/modify filter context.
    assert db.creates_filter_context("TOTALWTD") is True


# ── Model introspection (INFO.VIEW.* + TABLEOF) ─────────────────────────────

INFO_VIEW_FUNCS = [
    "INFO.VIEW.MEASURES",
    "INFO.VIEW.TABLES",
    "INFO.VIEW.COLUMNS",
    "INFO.VIEW.RELATIONSHIPS",
]


@pytest.mark.parametrize("name", INFO_VIEW_FUNCS)
def test_info_view_function_present(db, name):
    func = db.lookup(name)
    assert func is not None, f"{name} missing from function DB"
    assert func.category == "info"
    assert func.return_type == "table"


def test_tableof_present(db):
    func = db.lookup("TABLEOF")
    assert func is not None
    assert func.return_type == "table"


# ── Sanity: lookups stay case-insensitive and dotted names resolve ──────────


def test_dotted_info_view_name_is_function(db):
    assert db.is_function("info.view.measures") is True


def test_no_modern_function_has_blank_performance_notes(db):
    for name in VISUAL_CALC_FUNCS + CALENDAR_TI_FUNCS + INFO_VIEW_FUNCS + ["TABLEOF"]:
        func = db.lookup(name)
        assert (
            func is not None and func.performance_notes.strip()
        ), f"{name} should carry a non-empty performance_notes hint"
