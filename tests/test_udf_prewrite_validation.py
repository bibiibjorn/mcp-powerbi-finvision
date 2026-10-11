"""UDF pre-write validation, and the escaped-quote bug it exposed.

Two defects fixed together:

1. `FunctionCrudManager._validate_dax_expression` called `DaxValidator.validate()`
   — a method that does not exist. Every call raised `AttributeError` into a
   broad `except`, so the advertised "validate DAX before writing a UDF" was a
   silent no-op for every input.
2. Wiring it to the real helpers surfaced a bug in
   `DaxValidator.check_balanced_delimiters`: on an escaped `""` it skipped only
   one character, so the second quote re-opened the string and every delimiter
   after it went uncounted — valid DAX was reported as unbalanced.
"""

from __future__ import annotations

import pytest

from core.dax.dax_validator import DaxValidator
from core.operations.function_crud_manager import FunctionCrudManager


class TestEscapedQuoteDelimiterBalance:
    """Regression: an escaped quote must not swallow the rest of the string."""

    @pytest.mark.parametrize(
        "expr",
        [
            'IF(x = "a""b", 1, 0)',
            "SUM('Bob''s Table'[Amt])",
            'IF(x = "a""b", SUM(Sales[Amt]), 0)',
            'CONCATENATE("say ""hi""", "!")',
            '"""" & "x"',
        ],
    )
    def test_escaped_quotes_do_not_produce_false_unbalance(self, expr):
        v = DaxValidator()
        assert v.check_balanced_delimiters(expr, "(", ")", "parentheses") == []
        assert v.check_balanced_delimiters(expr, "[", "]", "brackets") == []

    @pytest.mark.parametrize(
        "expr,opener",
        [
            ('IF(x = "a""b", 1, 0', "("),
            ("SUM(Sales[Amt", "["),
        ],
    )
    def test_genuine_unbalance_after_an_escape_is_still_caught(self, expr, opener):
        """The fix must not blind the checker — errors after an escaped quote
        still have to be reported."""
        v = DaxValidator()
        close = ")" if opener == "(" else "]"
        errs = v.check_balanced_delimiters(expr, opener, close, "x")
        assert errs, f"expected an unbalance for {expr!r}"

    def test_delimiters_inside_a_real_string_are_still_ignored(self):
        v = DaxValidator()
        assert v.check_balanced_delimiters('"a ( b ["', "(", ")", "parens") == []


class TestUdfPreWriteValidation:
    """The validator must accept GA UDF syntax and reject structural damage."""

    @pytest.mark.parametrize(
        "expr",
        [
            "(VAL sales: DOUBLE, EXPR rate) => sales * rate",
            "(qty: INT64, rate: DOUBLE = 0.2) => qty * rate",
            '(t: TABLE) => CALCULATE(SUM(Sales[Amt]), Sales[Region] = "EU")',
            "/// Adds margin.\n(VAL s: DOUBLE) => s * 1.2",
            '(x) => IF(x = "a""b", 1, 0)',
        ],
    )
    def test_valid_ga_udf_bodies_pass(self, expr):
        assert FunctionCrudManager._validate_dax_expression(expr) is None

    @pytest.mark.parametrize(
        "expr",
        [
            "(x: DOUBLE) => SUM(Sales[Amt]",
            "(x) => Sales[Amt]]",
            '(x) => IF(x = "abc, 1, 0)',
        ],
    )
    def test_structurally_broken_bodies_are_rejected(self, expr):
        err = FunctionCrudManager._validate_dax_expression(expr)
        assert err, f"expected rejection for {expr!r}"
        assert "Unbalanced" in err

    @pytest.mark.parametrize("expr", ["", "   ", None])
    def test_empty_expression_rejected(self, expr):
        err = FunctionCrudManager._validate_dax_expression(expr)
        assert err and "required" in err

    def test_validation_actually_runs_now(self, monkeypatch):
        """The original bug was a silent no-op. Prove the helpers are reached."""
        calls = []
        real = DaxValidator.check_balanced_quotes
        monkeypatch.setattr(
            DaxValidator,
            "check_balanced_quotes",
            staticmethod(lambda q: calls.append(q) or real(q)),
        )
        FunctionCrudManager._validate_dax_expression("(x) => x")
        assert calls, "check_balanced_quotes was never called"

    def test_error_message_is_capped(self):
        """Many errors must not produce an unbounded message."""
        err = FunctionCrudManager._validate_dax_expression('(((((( "unclosed')
        assert err and err.count(";") <= 2

    def test_unavailable_validator_degrades_to_pass(self, monkeypatch):
        """A validator failure must not block a legitimate write."""

        def _boom(*a, **k):
            raise RuntimeError("validator exploded")

        monkeypatch.setattr(DaxValidator, "check_balanced_quotes", staticmethod(_boom))
        assert FunctionCrudManager._validate_dax_expression("(x) => x") is None
