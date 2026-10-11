"""Pinning tests for the unified TMDL parser.

``core/tmdl/unified_parser.py`` is the canonical parser that produces typed
dataclasses (TmdlTable, TmdlMeasure, etc.) from TMDL text. It's ~1570 lines
with one ``_parse_<object>`` method per TMDL object type (~15 of them). Prior
audit P2-32 asked for a shared body-line helper; ``_classify_body_line`` was
already extracted (commit c3aaf0d) but the per-object body loops still
duplicate the same state machine.

These tests pin the observable output of the top handful of object types so
a further refactor can't silently change parsed structure. Uses small literal
TMDL fragments via the parser's string-entry method ``_parse_table_content``
so tests don't need on-disk fixtures.
"""

from __future__ import annotations

import tempfile
import os
import pytest

from core.tmdl.unified_parser import UnifiedTmdlParser


@pytest.fixture
def parser():
    """A UnifiedTmdlParser anchored on an empty temp dir.

    The parser needs a TMDL folder on disk for directory-scanning methods,
    but we only exercise ``_parse_table_content`` which accepts a string.
    A temp dir with a ``definition/`` stub satisfies the constructor's
    existence check.
    """
    with tempfile.TemporaryDirectory() as td:
        # Constructor expects a .tmdl/.SemanticModel root with ``definition/``
        # inside. Create the minimal structure.
        os.makedirs(os.path.join(td, "definition"), exist_ok=True)
        yield UnifiedTmdlParser(td)


# ---------------------------------------------------------------------------
# Column parsing via _parse_table_content
# ---------------------------------------------------------------------------

_COLUMN_TABLE = """\
table Sales
\tcolumn Amount
\t\tdataType: double
\t\tsummarizeBy: sum
\t\tsourceColumn: Amount

\tcolumn Cost
\t\tdataType: double
\t\tisHidden
\t\tformatString: "#,0.00"
"""


def test_column_basic_fields_extracted(parser):
    table = parser._parse_table_content(_COLUMN_TABLE)
    assert table is not None
    assert table.name == "Sales"
    names = {c.name for c in table.columns}
    assert {"Amount", "Cost"} <= names


def test_column_datatype_and_summarizeby(parser):
    table = parser._parse_table_content(_COLUMN_TABLE)
    amount = next(c for c in table.columns if c.name == "Amount")
    assert amount.data_type == "double"
    assert amount.summarize_by == "sum"
    assert amount.source_column == "Amount"


def test_column_bare_ishidden_flag(parser):
    """Bare ``isHidden`` line (no ``: true``) — TMDL idiom."""
    table = parser._parse_table_content(_COLUMN_TABLE)
    cost = next(c for c in table.columns if c.name == "Cost")
    assert cost.is_hidden is True


def test_calculated_column_detected(parser):
    text = """\
table Sales
\tcolumn Margin = [Revenue] - [Cost]
\t\tdataType: double
"""
    table = parser._parse_table_content(text)
    col = table.columns[0]
    assert col.is_calculated is True
    assert col.expression and "[Revenue]" in col.expression


# ---------------------------------------------------------------------------
# Measure parsing
# ---------------------------------------------------------------------------

_MEASURE_TABLE = """\
table Sales
\tmeasure 'Total Sales' =
\t\tSUM ( Sales[Amount] )
\t\tformatString: "#,0"
\t\tdisplayFolder: Core

\tmeasure Profit = [Total Sales] - [Total Cost]
"""


def test_measure_names_extracted(parser):
    table = parser._parse_table_content(_MEASURE_TABLE)
    names = {m.name for m in table.measures}
    assert {"Total Sales", "Profit"} <= names


def test_measure_expression_captured(parser):
    table = parser._parse_table_content(_MEASURE_TABLE)
    total = next(m for m in table.measures if m.name == "Total Sales")
    assert total.expression and "SUM" in total.expression


def test_measure_formatstring_and_folder(parser):
    """Fixed 2026-04-24: when a measure expression is multi-line AND followed
    immediately by sibling property lines at the SAME indent (the common
    PBIP TMDL idiom), the parser now correctly separates them instead of
    absorbing the property lines into the expression.

    Previous bug: ``_extract_expression`` had a base-indent-only end
    condition, so properties at ``indent == base_indent`` fell through to
    ``expr_parts.append``. Fixed by adding paren-balance-aware sibling-
    property detection (see ``_looks_like_sibling_property`` +
    ``_net_paren_depth`` in unified_parser.py).
    """
    table = parser._parse_table_content(_MEASURE_TABLE)
    total = next(m for m in table.measures if m.name == "Total Sales")
    # Expression should contain ONLY the DAX body, no property tail.
    assert total.expression is not None
    assert "SUM" in total.expression
    assert (
        "formatString" not in total.expression
    ), f"property leaked into expression: {total.expression!r}"
    assert (
        "displayFolder" not in total.expression
    ), f"property leaked into expression: {total.expression!r}"
    # Properties should be on their own fields now.
    assert total.format_string is not None, "formatString not parsed"
    assert "#,0" in total.format_string
    assert total.display_folder == "Core"


# ---------------------------------------------------------------------------
# Annotations — used by every object type
# ---------------------------------------------------------------------------

_ANNOT_TABLE = """\
table Orders
\tcolumn OrderID
\t\tdataType: int64
\t\tannotation SummarizationSetBy = Automatic
\t\tannotation PBI_ResultType = Numeric
"""


def test_annotations_attached_to_column(parser):
    table = parser._parse_table_content(_ANNOT_TABLE)
    col = table.columns[0]
    names = {a.name for a in col.annotations}
    assert {"SummarizationSetBy", "PBI_ResultType"} <= names


# ---------------------------------------------------------------------------
# Deterministic re-parse (purity check)
# ---------------------------------------------------------------------------


def test_parser_is_deterministic(parser):
    """Parsing the same text twice yields structurally equal output."""
    text = _MEASURE_TABLE
    first = parser._parse_table_content(text)
    second = parser._parse_table_content(text)
    assert first.name == second.name
    assert [c.name for c in first.columns] == [c.name for c in second.columns]
    assert [m.name for m in first.measures] == [m.name for m in second.measures]


# ---------------------------------------------------------------------------
# Classify-body-line helper — P2-32 extracted this; pin its contract.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Regression tests for the 2026-04-24 expression-extraction fix
# ---------------------------------------------------------------------------


def test_measure_dax_with_colon_inside_switch(parser):
    """Audit T69 regression: DAX ``SWITCH ( TRUE ( ), cond : value, ... )``
    has colons inside parens. The sibling-property detector must NOT
    terminate the expression there.
    """
    text = """\
table Sales
\tmeasure 'Bucketed Sales' =
\t\tSWITCH (
\t\t\tTRUE ( ),
\t\t\t[Amount] > 1000, "High",
\t\t\t[Amount] > 100, "Mid",
\t\t\t"Low"
\t\t)
\t\tformatString: "#,0"
"""
    table = parser._parse_table_content(text)
    measure = table.measures[0]
    assert "SWITCH" in measure.expression
    # Colon-continuation-inside-parens stays in the expression.
    assert "[Amount] > 1000" in measure.expression
    # Property after the closing `)` is correctly separated.
    assert measure.format_string and "#,0" in measure.format_string
    assert "formatString" not in measure.expression


def test_measure_dax_with_var_return(parser):
    """Multi-line DAX with VAR / RETURN across many lines, then properties."""
    text = """\
table Sales
\tmeasure 'Complex Metric' =
\t\tVAR _x = SUM ( Sales[Amount] )
\t\tVAR _y = COUNTROWS ( Sales )
\t\tRETURN
\t\t\tDIVIDE ( _x, _y, 0 )
\t\tformatString: "#,0.00"
\t\tdisplayFolder: Core
"""
    table = parser._parse_table_content(text)
    measure = table.measures[0]
    assert "VAR _x" in measure.expression
    assert "RETURN" in measure.expression
    assert "DIVIDE" in measure.expression
    assert measure.format_string and "#,0.00" in measure.format_string
    assert measure.display_folder == "Core"


def test_partition_m_source_with_let_in(parser):
    """M partitions use ``source = let ... in Source``. The capitalized
    ``Source`` inside ``let`` must NOT be treated as a sibling property
    (property detector requires lowercase start — ``Source`` starts uppercase).
    """
    text = """\
table 'd Asset'
\tpartition 'd Asset' = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t    Source = p_data{[Item="d Asset",Kind="View"]}[Data]
\t\t\tin
\t\t\t    Source

\tannotation PBI_ResultType = Table
"""
    table = parser._parse_table_content(text)
    assert len(table.partitions) == 1
    part = table.partitions[0]
    # Mode is a property, parsed separately.
    assert getattr(part, "mode", None) == "import"
    # Source expression includes the let...in body.
    src = part.source_expression if hasattr(part, "source_expression") else part.source
    assert src is not None
    assert "let" in src
    assert "Source =" in src  # the M-language assignment inside let
    assert "p_data" in src


def test_calculated_column_expression_clean(parser):
    """Calculated columns had the same bug. Make sure they're fixed too."""
    text = """\
table Sales
\tcolumn Margin = [Revenue] - [Cost]
\t\tdataType: double
\t\tformatString: "#,0.00"
"""
    table = parser._parse_table_content(text)
    col = table.columns[0]
    assert col.is_calculated
    assert col.expression and "[Revenue]" in col.expression
    # Properties must be separated.
    assert "formatString" not in col.expression
    assert col.format_string and "#,0.00" in col.format_string
    assert col.data_type == "double"
