"""Tests for DAX tokenizer escape handling (']]' inside [...])."""

from __future__ import annotations


from core.dax.tokenizer import DaxLexer, TokenType
from core.dax.dax_best_practices import _has_var_and_return_tokens


def _tokens_of_type(dax: str, type_: TokenType):
    return [t for t in DaxLexer().tokenize(dax) if t.type == type_]


# ---------------------------------------------------------------------------
# ']]' escape inside [Column]
# ---------------------------------------------------------------------------


def test_column_ref_with_escaped_bracket():
    """[Net]]Sales] is the DAX-escaped form of a column literally called 'Net]Sales'."""
    dax = "EVALUATE { 'Sales'[Net]]Sales] }"
    refs = _tokens_of_type(dax, TokenType.QUALIFIED_REF)
    # The qualified ref must include the full escaped column name.
    assert any("Net]]Sales" in r.value for r in refs)


def test_unqualified_column_ref_with_escaped_bracket():
    dax = "EVALUATE { [Var]]iance] }"
    refs = _tokens_of_type(dax, TokenType.COLUMN_REF)
    assert any("Var]]iance" in r.value for r in refs)


def test_simple_column_ref_still_tokenizes():
    dax = "EVALUATE { [Sales] }"
    refs = _tokens_of_type(dax, TokenType.COLUMN_REF)
    assert any(r.value == "[Sales]" for r in refs)


# ---------------------------------------------------------------------------
# VAR-without-RETURN false-positive avoidance (audit T58)
# ---------------------------------------------------------------------------


def test_var_in_string_literal_does_not_count():
    """The substring 'VAR' inside a string literal must NOT trigger has_vars."""
    dax = 'EVALUATE { "VAR field placeholder" }'
    has_var, has_return = _has_var_and_return_tokens(dax)
    assert has_var is False
    assert has_return is False


def test_var_inside_column_name_does_not_count():
    """A column called [VARIANCE] must NOT trigger has_vars (substring 'VAR')."""
    dax = "EVALUATE { 'Stats'[VARIANCE] }"
    has_var, has_return = _has_var_and_return_tokens(dax)
    assert has_var is False


def test_real_var_return_is_detected():
    dax = "VAR _x = [Sales] RETURN _x * 2"
    has_var, has_return = _has_var_and_return_tokens(dax)
    assert has_var is True
    assert has_return is True


def test_real_var_without_return_is_detected():
    """Pathological but valid input — VAR without RETURN."""
    dax = "VAR _x = [Sales]"
    has_var, has_return = _has_var_and_return_tokens(dax)
    assert has_var is True
    assert has_return is False
