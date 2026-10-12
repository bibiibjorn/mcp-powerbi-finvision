"""Platform-currency tests for the 2025-2026 DAX function additions.

Canonical catalog: ``core/dax/knowledge/functions.json`` (loaded by
``DaxFunctionDatabase``). These tests assert through the DB's PUBLIC API
(``lookup`` / ``is_function`` / ``get_by_category`` / ``get_function_names``)
so a storage refactor can't quietly drop an entry.

Covered additions:
- Week-grain calendar-aware time intelligence (Sept 2025, CL 1701):
  TOTALWTD, STARTOFWEEK, ENDOFWEEK, NEXTWEEK, PREVIOUSWEEK,
  OPENINGBALANCEWEEK, CLOSINGBALANCEWEEK.
- TABLEOF (Feb 2026) — column/measure/calendar reference -> table reference.
- Visual calculations (June 2025): LOOKUP, LOOKUPWITHTOTALS.
- The optional calendar-reference overload note on the classic TI functions.

There is no separate hard-coded function allowlist to keep in lockstep: the
lexer receives ``function_names`` from this DB, and
``DaxValidator.TABLE_FUNCTIONS`` is a narrow table-expression pattern list,
not a general validity gate.
"""

from __future__ import annotations

import pytest

from core.dax.dax_validator import DaxValidator
from core.dax.knowledge.function_db import DaxFunctionDatabase
from core.dax.tokenizer.lexer import DaxLexer
from core.dax.tokenizer.tokens import TokenType

WEEK_TIME_INTELLIGENCE = [
    "TOTALWTD",
    "STARTOFWEEK",
    "ENDOFWEEK",
    "NEXTWEEK",
    "PREVIOUSWEEK",
    "OPENINGBALANCEWEEK",
    "CLOSINGBALANCEWEEK",
]

VISUAL_CALCULATIONS = ["LOOKUP", "LOOKUPWITHTOTALS"]

ALL_NEW = WEEK_TIME_INTELLIGENCE + VISUAL_CALCULATIONS + ["TABLEOF"]


@pytest.fixture(scope="module")
def db() -> DaxFunctionDatabase:
    return DaxFunctionDatabase.get()


# ---------------------------------------------------------------------------
# Presence via the public API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_NEW)
def test_function_is_known(db, name):
    assert db.is_function(name) is True
    assert db.lookup(name) is not None


@pytest.mark.parametrize("name", ALL_NEW)
def test_function_lookup_is_case_insensitive(db, name):
    assert db.lookup(name.lower()) is not None
    assert db.lookup(name.title()).name == name


@pytest.mark.parametrize("name", ALL_NEW)
def test_function_in_name_set(db, name):
    assert name in db.get_function_names()


@pytest.mark.parametrize("name", ALL_NEW)
def test_function_has_populated_prose(db, name):
    """Every new entry documents its behaviour in performance_notes."""
    func = db.lookup(name)
    assert func.performance_notes.strip()


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", WEEK_TIME_INTELLIGENCE)
def test_week_functions_are_time_intelligence(db, name):
    assert db.lookup(name).category == "time_intelligence"


@pytest.mark.parametrize("name", WEEK_TIME_INTELLIGENCE)
def test_week_functions_discoverable_by_category(db, name):
    names = {f.name for f in db.get_by_category("time_intelligence")}
    assert name in names


@pytest.mark.parametrize("name", VISUAL_CALCULATIONS)
def test_visual_calculations_use_existing_category(db, name):
    assert db.lookup(name).category == "visual_calculation"
    names = {f.name for f in db.get_by_category("visual_calculation")}
    assert name in names


def test_tableof_returns_a_table(db):
    func = db.lookup("TABLEOF")
    assert func.return_type == "table"
    # Feb 2026: takes a column, measure or calendar reference.
    assert "calendar" in func.performance_notes.lower()


# ---------------------------------------------------------------------------
# Week-grain semantics mirror the MTD/QTD/YTD siblings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "week_fn,sibling",
    [
        ("TOTALWTD", "TOTALMTD"),
        ("STARTOFWEEK", "STARTOFMONTH"),
        ("ENDOFWEEK", "ENDOFMONTH"),
        ("NEXTWEEK", "NEXTMONTH"),
        ("PREVIOUSWEEK", "PREVIOUSMONTH"),
        ("OPENINGBALANCEWEEK", "OPENINGBALANCEMONTH"),
        ("CLOSINGBALANCEWEEK", "CLOSINGBALANCEMONTH"),
    ],
)
def test_week_function_mirrors_month_sibling_shape(db, week_fn, sibling):
    """Week functions mirror their MTD/QTD/YTD siblings' return/context shape."""
    wk = db.lookup(week_fn)
    mo = db.lookup(sibling)
    assert wk.return_type == mo.return_type
    assert wk.creates_filter_context == mo.creates_filter_context
    assert wk.creates_row_context == mo.creates_row_context


@pytest.mark.parametrize("name", WEEK_TIME_INTELLIGENCE)
def test_week_functions_document_calendar_requirement(db, name):
    """They require a calendar-based TI model (CL 1701) / an explicit week grain."""
    func = db.lookup(name)
    assert "calendar" in func.performance_notes.lower()
    assert any("alendar" in p.get("name", "") for p in func.parameters)


@pytest.mark.parametrize(
    "name", ["TOTALYTD", "TOTALQTD", "TOTALMTD", "DATEADD", "PARALLELPERIOD", "SAMEPERIODLASTYEAR"]
)
def test_classic_ti_documents_optional_calendar_overload(db, name):
    """CL 1701 added an optional calendar-reference argument to these."""
    assert "CL 1701+" in db.lookup(name).performance_notes


# ---------------------------------------------------------------------------
# Tokenizer / validator acceptance
# ---------------------------------------------------------------------------


def test_lexer_classifies_new_functions_as_functions(db):
    lexer = DaxLexer(function_names=db.get_function_names())
    tokens = lexer.tokenize('TOTALWTD([Sales], "Fiscal") + TABLEOF([Sales])')
    functions = [t.value for t in tokens if t.type == TokenType.FUNCTION]
    assert "TOTALWTD" in functions
    assert "TABLEOF" in functions


def test_validator_accepts_totalwtd_and_tableof_snippet():
    query = 'EVALUATE ROW("wtd", TOTALWTD([Sales], "Fiscal"))'
    assert DaxValidator.validate_complete_dax_query(query) == []
    assert DaxValidator.validate_query_syntax("EVALUATE TABLEOF([Sales])") == []


# ---------------------------------------------------------------------------
# Catalog sanity
# ---------------------------------------------------------------------------


def test_catalog_has_no_duplicate_names(db):
    names = [f.name for f in db.all_functions()]
    assert len(names) == len(set(names))
    assert db.count() == len(names)


def test_lookup_and_lookupwithtotals_are_distinct(db):
    lookup = db.lookup("LOOKUP")
    lwt = db.lookup("LOOKUPWITHTOTALS")
    assert lookup.name != lwt.name
    assert lookup.performance_notes != lwt.performance_notes
