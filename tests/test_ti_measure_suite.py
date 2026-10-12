"""Tests for the time-intelligence measure-suite generator (pure logic)."""

from __future__ import annotations

import pytest

from core.operations.ti_measure_suite import (
    ALL_VARIANTS,
    DEFAULT_VARIANTS,
    TiSuiteError,
    generate_suite,
    parse_date_column,
)


class TestParseDateColumn:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("'Date'[Date]", "'Date'[Date]"),
            ("Date[Date]", "'Date'[Date]"),
            ("Date.Date", "'Date'[Date]"),
            ("'Calendar Table'[Day]", "'Calendar Table'[Day]"),
            ("  Date[ Date ]  ", "'Date'[Date]"),
        ],
    )
    def test_normalizes_qualified_refs(self, raw, expected):
        assert parse_date_column(raw) == expected

    def test_escapes_embedded_quote(self):
        assert parse_date_column("'Bob''s Date'[D]") == "'Bob''s Date'[D]"

    @pytest.mark.parametrize("bad", ["", "   ", "Date", "[Date]", None])
    def test_rejects_unqualified_or_empty(self, bad):
        """A bare column must be rejected — guessing the table silently builds
        measures against the wrong one."""
        with pytest.raises(TiSuiteError):
            parse_date_column(bad)


class TestGenerateSuite:
    def test_default_suite_shape(self):
        res = generate_suite(["Sales Amount"], date_column="'Date'[Date]")
        assert res["count"] == len(DEFAULT_VARIANTS)
        assert res["date_column"] == "'Date'[Date]"
        assert res["variants_used"] == DEFAULT_VARIANTS
        names = [m["name"] for m in res["measures"]]
        assert "Sales Amount YTD" in names
        assert "Sales Amount vs PY" in names
        assert "Sales Amount vs PY %" in names

    def test_to_date_expressions(self):
        res = generate_suite(["Sales"], date_column="Date[Date]", variants=["YTD", "QTD", "MTD"])
        exprs = {m["variant"]: m["expression"] for m in res["measures"]}
        assert exprs["YTD"] == "TOTALYTD([Sales], 'Date'[Date])"
        assert exprs["QTD"] == "TOTALQTD([Sales], 'Date'[Date])"
        assert exprs["MTD"] == "TOTALMTD([Sales], 'Date'[Date])"

    def test_prior_period_expressions(self):
        res = generate_suite(["Sales"], date_column="'Date'[Date]", variants=["PY", "PM", "PY_YTD"])
        exprs = {m["variant"]: m["expression"] for m in res["measures"]}
        assert exprs["PY"] == "CALCULATE([Sales], SAMEPERIODLASTYEAR('Date'[Date]))"
        assert exprs["PM"] == "CALCULATE([Sales], DATEADD('Date'[Date], -1, MONTH))"
        assert "TOTALYTD" in exprs["PY_YTD"] and "SAMEPERIODLASTYEAR" in exprs["PY_YTD"]

    def test_delta_references_generated_sibling(self):
        res = generate_suite(
            ["Sales"], date_column="'Date'[Date]", variants=["PY", "DELTA", "DELTA_PCT"]
        )
        by_variant = {m["variant"]: m for m in res["measures"]}
        assert by_variant["DELTA"]["expression"] == "[Sales] - [Sales PY]"
        assert by_variant["DELTA"]["depends_on"] == "Sales PY"
        # Percentage delta must guard the denominator with DIVIDE.
        assert by_variant["DELTA_PCT"]["expression"] == ("DIVIDE([Sales] - [Sales PY], [Sales PY])")
        assert by_variant["DELTA_PCT"]["format_string"] == "0.0%"

    def test_delta_without_its_sibling_is_skipped_loudly(self):
        """Emitting [Sales PY] when we aren't creating it would be invalid DAX."""
        res = generate_suite(["Sales"], date_column="'Date'[Date]", variants=["DELTA"])
        assert res["count"] == 0
        assert any("requires variant 'PY'" in w for w in res["warnings"])

    def test_week_variant_declares_its_requirement(self):
        res = generate_suite(["Sales"], date_column="'Date'[Date]", variants=["WTD"])
        assert res["measures"][0]["expression"] == "TOTALWTD([Sales], 'Date'[Date])"
        assert "CL 1701" in res["requires"]["WTD"]

    def test_multiple_base_measures_cross_product(self):
        res = generate_suite(
            ["Sales", "Margin"], date_column="'Date'[Date]", variants=["YTD", "PY"]
        )
        assert res["count"] == 4
        assert {m["base_measure"] for m in res["measures"]} == {"Sales", "Margin"}

    def test_bracketed_input_accepted(self):
        res = generate_suite(["[Sales]"], date_column="'Date'[Date]", variants=["YTD"])
        m = res["measures"][0]
        assert m["name"] == "Sales YTD"
        assert m["expression"] == "TOTALYTD([Sales], 'Date'[Date])"

    def test_optional_metadata_applied(self):
        res = generate_suite(
            ["Sales"],
            date_column="'Date'[Date]",
            variants=["YTD"],
            target_table="_Measures",
            display_folder="Time Intelligence",
            format_string="#,0",
        )
        m = res["measures"][0]
        assert m["table_name"] == "_Measures"
        assert m["display_folder"] == "Time Intelligence"
        assert m["format_string"] == "#,0"

    def test_variant_format_string_wins_over_global(self):
        res = generate_suite(
            ["Sales"],
            date_column="'Date'[Date]",
            variants=["PY", "DELTA_PCT"],
            format_string="#,0",
        )
        by_variant = {m["variant"]: m for m in res["measures"]}
        assert by_variant["DELTA_PCT"]["format_string"] == "0.0%"
        assert by_variant["PY"]["format_string"] == "#,0"

    def test_custom_name_pattern(self):
        res = generate_suite(
            ["Sales"],
            date_column="'Date'[Date]",
            variants=["YTD"],
            name_pattern="{suffix} {base}",
        )
        assert res["measures"][0]["name"] == "YTD Sales"

    def test_duplicate_names_are_skipped_not_silently_overwritten(self):
        res = generate_suite(["Sales", "Sales"], date_column="'Date'[Date]", variants=["YTD"])
        assert res["count"] == 1
        assert any("duplicate" in w for w in res["warnings"])

    def test_output_is_batch_ready(self):
        """Every entry must carry what 03_Batch_Operations create needs."""
        res = generate_suite(["Sales"], date_column="'Date'[Date]", target_table="_Measures")
        for m in res["measures"]:
            assert m["name"] and m["expression"] and m["table_name"] == "_Measures"


class TestGenerateSuiteErrors:
    def test_empty_base_measures(self):
        with pytest.raises(TiSuiteError, match="empty"):
            generate_suite([], date_column="'Date'[Date]")

    def test_unknown_variant_lists_known_ones(self):
        with pytest.raises(TiSuiteError) as exc:
            generate_suite(["Sales"], date_column="'Date'[Date]", variants=["BOGUS"])
        assert "BOGUS" in str(exc.value)
        assert "YTD" in str(exc.value)

    def test_empty_variant_selection(self):
        with pytest.raises(TiSuiteError, match="empty selection"):
            generate_suite(["Sales"], date_column="'Date'[Date]", variants=[])

    def test_bad_name_pattern(self):
        with pytest.raises(TiSuiteError, match="name_pattern"):
            generate_suite(["Sales"], date_column="'Date'[Date]", name_pattern="{base} only")

    def test_malformed_measure_reference(self):
        with pytest.raises(TiSuiteError, match="malformed"):
            generate_suite(["Sal]es"], date_column="'Date'[Date]")

    def test_all_variants_are_generatable(self):
        """Every advertised variant must actually produce a measure (with its
        delta prerequisites present), so ALL_VARIANTS can't advertise a lie."""
        res = generate_suite(["Sales"], date_column="'Date'[Date]", variants=ALL_VARIANTS)
        assert res["count"] == len(ALL_VARIANTS)
        assert not res.get("warnings")
