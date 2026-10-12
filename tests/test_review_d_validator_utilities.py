"""2026-09-22 deep review, slice D — the pre-execution syntax gate (D-03) and
the paren scanners in dax_utilities (D-17).

DaxValidator runs before every validate_and_execute_dax call; it must never
reject valid DAX. When unsure it passes and lets the engine decide.
"""

import pytest

from core.dax.dax_utilities import extract_function_body, find_matching_paren, get_line_column
from core.dax.dax_validator import DaxValidator, code_only_text, has_non_constant_division

VALID = [
    'EVALUATE ROW("x", 1) // don\'t count returns',
    'EVALUATE ROW("x", [Men\'s Sales])',
    'EVALUATE ROW("x", 1) /* (draft */',
    'EVALUATE ROW("x", 1) -- (draft',
    'EVALUATE ROW("x", [Undefined Rate])',
    'EVALUATE ROW("label", "Define scope")',
    "EVALUATE ROW(\"x\", SUM('O''Brien'[X]))",
    'EVALUATE ROW("x", [Rate )])',
    "EVALUATE {1, 2, 3}",
    "DEFINE MEASURE 'T'[a] = 1 EVALUATE ROW(\"a\", [a])",
    'DEFINE FUNCTION f = (x: NUMERIC VAL) => x * 2 EVALUATE ROW("a", f(2))',
    '// DEFINE in a comment\nEVALUATE ROW("x", 1)',
    'EVALUATE ROW("x", dt"2024-01-01")',
]


@pytest.mark.parametrize("query", VALID)
def test_valid_queries_pass_the_gate(query):
    assert DaxValidator.validate_query_syntax(query) == []
    assert DaxValidator.validate_complete_dax_query(query) == []


@pytest.mark.parametrize(
    "query,needle",
    [
        ('EVALUATE ROW("x", SUM(T[a])', "parentheses"),
        ('EVALUATE ROW("x", SUM(T[a))', "brackets"),
        ('EVALUATE ROW("x, 1)', "double quotes"),
        ('EVALUATE ROW("x", SUM(\'T[a]))', "single quotes"),
        ('EVALUATE ROW("x", 1))', "parentheses"),
        ("EVALUATE {1, 2", "braces"),
    ],
)
def test_genuine_lexical_errors_are_still_reported(query, needle):
    errs = DaxValidator.validate_query_syntax(query)
    assert errs and any(needle in e for e in errs), errs
    assert all("Unbalanced" in e for e in errs)


def test_define_structure_uses_keyword_tokens():
    assert DaxValidator.validate_complete_dax_query("DEFINE MEASURE T[a]=1") == [
        "DAX query must contain at least one EVALUATE statement."
    ]
    errs = DaxValidator.validate_complete_dax_query('EVALUATE ROW("a",1) DEFINE MEASURE T[a]=1')
    assert "DEFINE statement must come before any EVALUATE statement." in errs
    errs = DaxValidator.validate_complete_dax_query('DEFINE EVALUATE ROW("a",1)')
    assert any("at least one definition" in e for e in errs)


def test_is_table_expression_ignores_leading_comment():
    assert DaxValidator.is_table_expression("// c\nFILTER(T, T[a] = 1)") is True
    assert DaxValidator.is_table_expression("SUM(T[a])") is False
    assert DaxValidator.is_table_expression("{1,2}") is True


def test_complexity_counts_calls_not_comment_text():
    c = DaxValidator.analyze_complexity("CALCULATE(SUM(T[a])) // FILTER( FILTER( FILTER(")
    assert c["filter_count"] == 0 and c["calculate_count"] == 1 and c["max_nesting_level"] == 2


def test_pattern_recommendations_ignore_comments_and_strings():
    _w, recs = DaxValidator.analyze_patterns('[A] + 1 // a/b FORMAT( SUMMARIZE(\n+ LEN("x/y")')
    assert not any("DIVIDE(" in r for r in recs)
    assert not any("FORMAT" in r for r in recs)
    assert not any("SUMMARIZECOLUMNS" in r for r in recs)


def test_constant_denominator_needs_no_divide():
    assert has_non_constant_division("[Sales] / 1000") is False
    assert has_non_constant_division("[Sales] / 0") is True
    assert has_non_constant_division("[Sales] / [Units]") is True
    _w, recs = DaxValidator.analyze_patterns("[Sales] / 1000")
    assert not any("DIVIDE(" in r for r in recs)


def test_code_only_text_blanks_literals():
    assert code_only_text('[a] & "x/y" // z') == '[a] & ""  '


# --- D-17: paren matching is bracket/comment aware -----------------------------------


def test_extract_function_body_with_apostrophe_in_bracket_name():
    dax = "CALCULATE([Men's Sales], T[a] = 1) + [Other]"
    assert extract_function_body(dax, dax.index("(") + 1) == "[Men's Sales], T[a] = 1"
    assert find_matching_paren(dax, dax.index("(")) == dax.index(")")


def test_extract_function_body_with_paren_in_bracket_name():
    dax = "SUM(T[Rate )]) + 1"
    assert extract_function_body(dax, 4) == "T[Rate )]"


def test_extract_function_body_skips_comments():
    dax = "F(a -- )\n, b /* ) */) + x"
    assert extract_function_body(dax, 2) == "a -- )\n, b /* ) */"


def test_unmatched_returns_rest_and_minus_one():
    assert extract_function_body("F(a, b", 2) == "a, b"
    assert find_matching_paren("F(a, b", 1) == -1


def test_get_line_column():
    assert get_line_column("ab\ncd", 4) == (2, 2)
    assert get_line_column("ab", 0) == (1, 1)
