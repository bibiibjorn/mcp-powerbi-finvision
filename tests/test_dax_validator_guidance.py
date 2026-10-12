"""Guards on DaxValidator.analyze_patterns guidance (Phase 0 correctness tail).

The validator used to recommend ADDING FORMAT() to division measures, which
contradicts AP-07 (FORMAT in a measure body returns text and breaks sorting /
aggregation; use the FormatString property or a dynamic format string instead).
"""

from core.dax.dax_validator import DaxValidator


def _recs(expr):
    _warnings, recommendations = DaxValidator.analyze_patterns(expr)
    return recommendations


def test_does_not_recommend_adding_format_function():
    recs = _recs("DIVIDE([Sales], [Cost])")
    assert not any("FORMAT function for better" in r for r in recs), recs
    # And it must not tell the user to wrap the measure in FORMAT(...).
    assert not any("format" in r.lower() and "presentation" in r.lower() for r in recs), recs


def test_recommends_format_string_property_when_format_is_misused():
    # When FORMAT() IS used in the measure body, steer to the Format String property.
    recs = _recs('FORMAT(DIVIDE([Sales], [Cost]), "0.0%")')
    assert any("format string" in r.lower() or "formatstring" in r.lower() for r in recs), recs


def test_plain_divide_gets_no_format_recommendation():
    # A plain division measure should NOT be nagged about formatting at all.
    recs = _recs("DIVIDE([Sales], [Cost])")
    assert not any("format" in r.lower() for r in recs), recs


def test_divide_guidance_still_present_for_plain_division():
    recs = _recs("[Sales] / [Cost]")
    assert any("DIVIDE(" in r for r in recs)
