"""Sep-2026 BPA rules in ``config/bpa_rules_comprehensive.json`` (the live default ruleset):

* ``TEXTCONTAINS_REQUIRES_FULLTEXT_INDEX`` (error)
* ``APPROXIMATEDISTINCTCOUNT_IN_MEASURE`` (warning)
* ``STRING_INDEX_ON_LOW_VALUE_COLUMN`` (info)

Each rule has positive and negative fixtures over a TMSL-shaped model (lowerCamel, defaults
omitted) and runs through the real analyzer with the shipped file.
"""

from __future__ import annotations

import copy
import os

import pytest

from core.analysis.bpa_analyzer import BPAAnalyzer, BPASeverity

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPREHENSIVE = os.path.join(ROOT, "config", "bpa_rules_comprehensive.json")

TEXT = "TEXTCONTAINS_REQUIRES_FULLTEXT_INDEX"
APPROX = "APPROXIMATEDISTINCTCOUNT_IN_MEASURE"
INDEX = "STRING_INDEX_ON_LOW_VALUE_COLUMN"


@pytest.fixture(scope="module")
def analyzer():
    return BPAAnalyzer(COMPREHENSIVE)


def col(name, **extra):
    return {"name": name, "dataType": "string", "sourceColumn": name, **extra}


def measure(name, expression):
    return {"name": name, "expression": expression}


def model(columns=(), measures=(), calc_columns=(), relationships=()):
    return {
        "tables": [
            {
                "name": "Hotel Stays",  # a space: the rule must escape / quote table names
                "columns": list(columns) + list(calc_columns),
                "measures": list(measures),
            }
        ],
        "relationships": list(relationships),
    }


def hits(analyzer, m, rule_id):
    res = analyzer.run(m)
    assert rule_id not in res.coverage["unsupported_rule_ids"], res.coverage["unsupported_reasons"]
    return sorted(v.object_name for v in res.violations if v.rule_id == rule_id)


def test_the_three_rules_ship_evaluate_and_have_the_documented_severity(analyzer):
    by_id = {r.id: r for r in analyzer.rules}
    assert by_id[TEXT].severity is BPASeverity.ERROR
    assert by_id[APPROX].severity is BPASeverity.WARNING
    assert by_id[INDEX].severity is BPASeverity.INFO
    res = analyzer.run(model())
    unsupported = set(res.coverage["unsupported_rule_ids"])
    assert not unsupported & {TEXT, APPROX, INDEX}
    assert analyzer.load_errors == []


# --- (a) TEXTCONTAINS / TEXTSIMILARITY without a full-text index --------------------------------


def test_textcontains_on_an_unindexed_column_is_an_error(analyzer):
    m = model(
        columns=[col("Name")],
        measures=[
            measure(
                "Spa hits",
                "COUNTROWS(FILTER('Hotel Stays', TEXTCONTAINS('Hotel Stays'[Name], \"spa\")))",
            )
        ],
    )
    assert hits(analyzer, m, TEXT) == ["Name"]


def test_textsimilarity_and_case_and_spacing_variants_are_caught(analyzer):
    m = model(
        columns=[col("Name"), col("Notes")],
        measures=[
            measure(
                "Score",
                "MAXX('Hotel Stays', textsimilarity ( 'Hotel Stays'[Name], \"x\", FUZZYMATCHING))",
            ),
            measure(
                "Hits", "SUMX('Hotel Stays', IF(TextContains('Hotel Stays'  [Notes], \"y\"), 1))"
            ),
        ],
    )
    assert hits(analyzer, m, TEXT) == ["Name", "Notes"]


@pytest.mark.parametrize("behavior", ["full", "explicit"])
def test_a_persisted_full_text_index_clears_the_error(analyzer, behavior):
    m = model(
        columns=[col("Name", fullTextIndexingBehavior=behavior)],
        measures=[
            measure(
                "Hits",
                "COUNTROWS(FILTER('Hotel Stays', TEXTCONTAINS('Hotel Stays'[Name], \"spa\")))",
            )
        ],
    )
    assert hits(analyzer, m, TEXT) == []


def test_off_is_still_an_error(analyzer):
    m = model(
        columns=[col("Name", fullTextIndexingBehavior="off")],
        measures=[
            measure(
                "Hits",
                "COUNTROWS(FILTER('Hotel Stays', TEXTCONTAINS('Hotel Stays'[Name], \"spa\")))",
            )
        ],
    )
    assert hits(analyzer, m, TEXT) == ["Name"]


def test_a_column_nobody_searches_is_not_flagged(analyzer):
    m = model(
        columns=[col("Name"), col("Other")],
        measures=[
            measure(
                "Hits",
                "COUNTROWS(FILTER('Hotel Stays', TEXTCONTAINS('Hotel Stays'[Name], \"spa\")))",
            )
        ],
    )
    assert hits(analyzer, m, TEXT) == ["Name"]  # 'Other' is not searched


def test_other_string_functions_do_not_trigger_the_rule(analyzer):
    m = model(
        columns=[col("Name")],
        measures=[
            measure(
                "A",
                "COUNTROWS(FILTER('Hotel Stays', CONTAINSSTRING('Hotel Stays'[Name], \"spa\")))",
            ),
            measure(
                "B",
                "COUNTROWS(FILTER('Hotel Stays', SEARCH(\"spa\", 'Hotel Stays'[Name], 1, 0) > 0))",
            ),
        ],
    )
    assert hits(analyzer, m, TEXT) == []


def test_commented_out_or_quoted_text_does_not_trigger_the_rule(analyzer):
    m = model(
        columns=[col("Name")],
        measures=[
            measure("Commented", "// TEXTCONTAINS('Hotel Stays'[Name], \"spa\")\n1"),
            measure("Quoted", "\"TEXTCONTAINS('Hotel Stays'[Name], x)\""),
        ],
    )
    assert hits(analyzer, m, TEXT) == []


def test_non_string_columns_are_never_flagged(analyzer):
    m = model(
        columns=[{"name": "Nights", "dataType": "int64", "sourceColumn": "Nights"}],
        measures=[measure("Odd", "TEXTCONTAINS('Hotel Stays'[Nights], \"1\")")],
    )
    assert hits(analyzer, m, TEXT) == []


def test_a_calculated_column_using_textcontains_counts_as_a_search(analyzer):
    m = model(
        columns=[col("Name")],
        calc_columns=[
            {
                "name": "Is Spa",
                "type": "calculated",
                "dataType": "boolean",
                "expression": "TEXTCONTAINS('Hotel Stays'[Name], \"spa\")",
            }
        ],
    )
    assert hits(analyzer, m, TEXT) == ["Name"]


# --- (b) APPROXIMATEDISTINCTCOUNT -----------------------------------------------------------------


def test_approximate_distinct_count_in_a_measure_is_flagged(analyzer):
    m = model(
        columns=[col("Guest Id")],
        measures=[
            measure("Guests ~", "APPROXIMATEDISTINCTCOUNT('Hotel Stays'[Guest Id])"),
            measure("Guests spaced", "approximatedistinctcount ( 'Hotel Stays'[Guest Id] )"),
            measure("Guests", "DISTINCTCOUNT('Hotel Stays'[Guest Id])"),
            measure(
                "Commented",
                "// APPROXIMATEDISTINCTCOUNT(x)\nDISTINCTCOUNT('Hotel Stays'[Guest Id])",
            ),
        ],
    )
    assert hits(analyzer, m, APPROX) == ["Guests spaced", "Guests ~"]


def test_approximate_distinct_count_message_warns_about_exactness(analyzer):
    rule = next(r for r in analyzer.rules if r.id == APPROX)
    text = rule.description.lower()
    assert "1.6" in text and "financial" in text and "exact" in text


# --- (c) persisted string index on a low-value column ---------------------------------------------


REL = [
    {
        "name": "r1",
        "fromTable": "Hotel Stays",
        "fromColumn": "Hotel Code",
        "toTable": "Hotels",
        "toColumn": "Hotel Code",
    }
]


def test_index_on_a_relationship_column_is_info(analyzer):
    m = model(
        columns=[
            col("Hotel Code", stringIndexingBehavior="full"),
            col("Name", stringIndexingBehavior="full"),
        ],
        relationships=REL,
    )
    assert hits(analyzer, m, INDEX) == ["Hotel Code"]


def test_index_on_a_key_column_is_info(analyzer):
    m = model(columns=[col("Booking Id", isKey=True, stringIndexingBehavior="explicit")])
    assert hits(analyzer, m, INDEX) == ["Booking Id"]


def test_index_on_a_low_cardinality_column_needs_the_vertipaq_annotation(analyzer):
    low = col(
        "Star Rating",
        stringIndexingBehavior="full",
        annotations=[{"name": "Vertipaq_Cardinality", "value": "5"}],
    )
    high = col(
        "Name",
        stringIndexingBehavior="full",
        annotations=[{"name": "Vertipaq_Cardinality", "value": "48000"}],
    )
    assert hits(analyzer, model(columns=[low, high]), INDEX) == ["Star Rating"]
    no_stats = col("Star Rating", stringIndexingBehavior="full")
    assert hits(analyzer, model(columns=[no_stats]), INDEX) == []  # cannot know: silent


def test_auto_or_off_and_non_string_columns_are_not_flagged(analyzer):
    m = model(
        columns=[
            col("Hotel Code"),  # default = auto
            col("Other", stringIndexingBehavior="off"),
            {
                "name": "Nights",
                "dataType": "int64",
                "sourceColumn": "Nights",
                "isKey": True,
                "stringIndexingBehavior": "full",
            },
        ],
        relationships=REL,
    )
    assert hits(analyzer, m, INDEX) == []


def test_an_indexed_searched_text_column_is_clean_for_all_three_rules(analyzer):
    m = model(
        columns=[
            col("Description", fullTextIndexingBehavior="full", stringIndexingBehavior="full")
        ],
        measures=[
            measure(
                "Hits",
                "COUNTROWS(FILTER('Hotel Stays', TEXTCONTAINS('Hotel Stays'[Description], \"spa\")))",
            )
        ],
    )
    res = analyzer.run(copy.deepcopy(m))
    assert not {v.rule_id for v in res.violations} & {TEXT, APPROX, INDEX}


def test_model_view_exposes_the_new_column_properties():
    from core.analysis.bpa_model import ModelView

    view = ModelView(model(columns=[col("A", stringIndexingBehavior="full"), col("B")]))
    a, b = [c for c in view.all_columns]
    assert a.bpa_get("StringIndexingBehavior") == "Full"
    assert b.bpa_get("StringIndexingBehavior") == "Auto"
    assert b.bpa_get("FullTextIndexingBehavior") == "Off"
