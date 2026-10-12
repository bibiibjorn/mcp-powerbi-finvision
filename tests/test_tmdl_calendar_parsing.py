"""Tests for CL 1701 ``calendar`` block tolerance in the unified TMDL parser.

Compatibility level 1701 (preview) introduces a ``calendar`` object nested
inside a table, holding one or more ``calendarColumnGroup`` children:

    table 'Date'
        calendar Fiscal
            calendarColumnGroup year
                primaryColumn: [FY]
                associatedColumn: [FY Label]

The exact child-property set is not fully documented, so the parser is built
for TOLERANCE: it must never raise, never drop the enclosing table, and never
mis-attribute a calendar's child properties to the table's columns/measures.
Unrecognized child properties land in a catch-all dict so nothing is lost.

The regression that matters most is the mis-attribution one — before the fix,
``primaryColumn:`` / ``associatedColumn:`` lines were swallowed by the
preceding ``column`` body loop.
"""

from __future__ import annotations

import pytest

from core.tmdl.models import TmdlCalendar, TmdlCalendarColumnGroup, TmdlModel, TmdlTable
from core.tmdl.unified_parser import UnifiedTmdlParser, parse_tmdl_model
from core.tmdl.validator import TmdlValidator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CALENDAR_TABLE = """\
table Date
\tcolumn FY
\t\tdataType: string
\t\tsummarizeBy: none

\tcalendar Fiscal
\t\tcalendarColumnGroup year
\t\t\tprimaryColumn: [FY]
\t\t\tassociatedColumn: [FY Label]
\t\tcalendarColumnGroup month
\t\t\tprimaryColumn: [FM]

\tcolumn FM
\t\tdataType: string

\tmeasure Sales = SUM(Date[Amount])
\t\tformatString: "#,0"
"""

_NO_CALENDAR_TABLE = """\
table Sales
\tcolumn Amount
\t\tdataType: double

\tmeasure Total = SUM(Sales[Amount])
"""


@pytest.fixture
def parser(tmp_path):
    """A UnifiedTmdlParser anchored on an empty temp definition folder.

    Only ``_parse_table_content`` (string entry point) is exercised, but the
    constructor requires a ``definition/`` folder to exist.
    """
    (tmp_path / "definition").mkdir()
    return UnifiedTmdlParser(str(tmp_path))


def _write_model(root, table_content: str, *, table_file: str = "Date.tmdl"):
    """Write a minimal on-disk TMDL model and return its root path."""
    definition = root / "definition"
    (definition / "tables").mkdir(parents=True)
    (definition / "database.tmdl").write_text(
        "database Test\n\tcompatibilityLevel: 1701\n", encoding="utf-8"
    )
    (definition / "model.tmdl").write_text("model Model\n\tculture: en-US\n", encoding="utf-8")
    (definition / "tables" / table_file).write_text(table_content, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Regression: sibling columns / measures survive a calendar block
# ---------------------------------------------------------------------------


def test_calendar_does_not_swallow_sibling_columns_and_measures(parser):
    """The table and ALL its columns/measures survive alongside a calendar."""
    table = parser._parse_table_content(_CALENDAR_TABLE)

    assert table is not None
    assert table.name == "Date"
    assert [c.name for c in table.columns] == ["FY", "FM"]
    assert [m.name for m in table.measures] == ["Sales"]


def test_calendar_properties_not_misattributed_to_columns(parser):
    """``primaryColumn`` / ``associatedColumn`` must NOT leak onto a column.

    Pre-fix behaviour: the ``column FY`` body loop consumed the calendar's
    child properties, so FY.properties held ``primaryColumn: '[FM]'``.
    """
    table = parser._parse_table_content(_CALENDAR_TABLE)

    fy = next(c for c in table.columns if c.name == "FY")
    assert "primaryColumn" not in fy.properties
    assert "associatedColumn" not in fy.properties
    assert fy.data_type == "string"
    assert fy.summarize_by == "none"

    # And the measure kept its own property.
    sales = next(m for m in table.measures if m.name == "Sales")
    assert sales.format_string == "#,0"


# ---------------------------------------------------------------------------
# Calendar structure captured
# ---------------------------------------------------------------------------


def test_calendar_and_column_groups_captured(parser):
    table = parser._parse_table_content(_CALENDAR_TABLE)

    assert len(table.calendars) == 1
    cal = table.calendars[0]
    assert isinstance(cal, TmdlCalendar)
    assert cal.name == "Fiscal"
    assert [g.name for g in cal.column_groups] == ["year", "month"]


def test_calendar_primary_and_associated_columns_captured(parser):
    table = parser._parse_table_content(_CALENDAR_TABLE)
    year, month = table.calendars[0].column_groups

    assert isinstance(year, TmdlCalendarColumnGroup)
    assert year.primary_column == "[FY]"
    assert year.associated_column == "[FY Label]"
    assert month.primary_column == "[FM]"
    assert month.associated_column is None


# ---------------------------------------------------------------------------
# Tolerance: unknown properties preserved, never raise
# ---------------------------------------------------------------------------


def test_unknown_calendar_properties_go_to_catch_all(parser):
    content = """\
table Date
\tcalendar Fiscal
\t\tsomeFutureFlag: true
\t\tcalendarColumnGroup week
\t\t\tprimaryColumn: [FW]
\t\t\tunknownChildProp: 42
\t\t\tanotherOne: "hello"
"""
    table = parser._parse_table_content(content)

    cal = table.calendars[0]
    assert cal.properties.get("someFutureFlag") == "true"

    week = cal.column_groups[0]
    assert week.primary_column == "[FW]"
    assert week.properties.get("unknownChildProp") == "42"
    assert week.properties.get("anotherOne") == "hello"


def test_calendar_with_no_column_groups_does_not_raise(parser):
    content = "table Date\n\tcalendar Fiscal\n\n\tcolumn FY\n\t\tdataType: string\n"
    table = parser._parse_table_content(content)

    assert len(table.calendars) == 1
    assert table.calendars[0].column_groups == []
    assert [c.name for c in table.columns] == ["FY"]


def test_calendar_quoted_name(parser):
    content = "table Date\n\tcalendar 'Fiscal Year 445'\n\t\tcalendarColumnGroup year\n"
    table = parser._parse_table_content(content)

    assert table.calendars[0].name == "Fiscal Year 445"


def test_multiple_calendars_on_one_table(parser):
    content = (
        "table Date\n"
        "\tcalendar Fiscal\n"
        "\t\tcalendarColumnGroup year\n"
        "\t\t\tprimaryColumn: [FY]\n"
        "\tcalendar Retail\n"
        "\t\tcalendarColumnGroup week\n"
        "\t\t\tprimaryColumn: [RW]\n"
    )
    table = parser._parse_table_content(content)

    assert [c.name for c in table.calendars] == ["Fiscal", "Retail"]
    assert table.calendars[1].column_groups[0].primary_column == "[RW]"


# ---------------------------------------------------------------------------
# Tables without calendars
# ---------------------------------------------------------------------------


def test_table_without_calendar_yields_empty_list(parser):
    table = parser._parse_table_content(_NO_CALENDAR_TABLE)

    assert table.calendars == []
    assert [c.name for c in table.columns] == ["Amount"]
    assert [m.name for m in table.measures] == ["Total"]


def test_default_tmdl_table_has_empty_calendars():
    assert TmdlTable(name="X").calendars == []


# ---------------------------------------------------------------------------
# Full on-disk parse + dict round-trip
# ---------------------------------------------------------------------------


def test_full_model_parse_with_calendar(tmp_path):
    _write_model(tmp_path, _CALENDAR_TABLE)
    model = parse_tmdl_model(str(tmp_path))

    date = next(t for t in model.tables if t.name == "Date")
    assert [c.name for c in date.columns] == ["FY", "FM"]
    assert date.calendars[0].column_groups[0].primary_column == "[FY]"


def test_to_dict_carries_calendars(parser):
    table = parser._parse_table_content(_CALENDAR_TABLE)
    model = TmdlModel(tables=[table])

    data = model.to_dict()
    assert data["tables"][0]["calendars"][0]["name"] == "Fiscal"

    cal = data["tables"][0]["calendars"][0]
    assert [g["name"] for g in cal["column_groups"]] == ["year", "month"]
    assert cal["column_groups"][0]["associated_column"] == "[FY Label]"


def test_text_generator_round_trips_calendars(parser):
    from core.tmdl.tmdl_text_generator import generate_tmdl_text

    table = parser._parse_table_content(_CALENDAR_TABLE)
    text = generate_tmdl_text(TmdlModel(tables=[table]).to_dict())

    assert "calendar 'Fiscal'" in text
    assert "calendarColumnGroup year" in text
    assert "primaryColumn: [FY]" in text
    assert "associatedColumn: [FY Label]" in text

    # And the emitted text re-parses into the same shape.
    reparsed = parser._parse_table_content(
        "\n".join(line for line in text.split("\n") if line.strip())
    )
    assert reparsed.calendars[0].column_groups[0].primary_column == "[FY]"


# ---------------------------------------------------------------------------
# Validator tolerance
# ---------------------------------------------------------------------------


def test_validator_reports_no_error_for_calendar_block(tmp_path):
    _write_model(tmp_path, _CALENDAR_TABLE)
    result = TmdlValidator().validate_syntax(str(tmp_path / "definition"))

    assert result.is_valid is True
    assert result.errors == []
    calendar_findings = [
        e for e in result.errors + result.warnings if "calendar" in e.message.lower()
    ]
    assert calendar_findings == []
