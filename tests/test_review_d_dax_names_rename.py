"""2026-09-22 deep review, slice D — DAX name escaping and lexer-driven renames.

``core.dax.dax_names`` is the only place DAX names are built (D-19) and
``core.dax.dax_rename`` the rename primitive other work packages use to replace
the regex cascades (rename_cascade, TMDL bulk_rename, the PBIR bridge).
"""

import pytest

from core.dax.dax_names import (
    bracket,
    column_ref,
    dax_string,
    measure_ref,
    split_qualified,
    quote_table,
    unbracket,
    unquote_table,
    unstring,
)
from core.dax.dax_rename import rename_column_refs, rename_measure_refs, rename_table_refs

# --- dax_names --------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Sales", "O'Brien", "a''b", "Date", "Δ Table", "'"])
def test_quote_table_roundtrip(name):
    q = quote_table(name)
    assert q.startswith("'") and q.endswith("'")
    assert unquote_table(q) == name


@pytest.mark.parametrize("name", ["Amount", "Col]x", "]]", 'Target "Q"', "Rate )"])
def test_bracket_roundtrip(name):
    b = bracket(name)
    assert unbracket(b) == name
    from core.dax.tokenizer import DaxLexer, TokenType

    toks = DaxLexer().tokenize_code(b)
    assert len(toks) == 1 and toks[0].type == TokenType.COLUMN_REF and toks[0].terminated


def test_dax_string_escapes_quotes():
    s = dax_string('Target "Q"')
    assert s == '"Target ""Q"""'
    assert unstring(s) == 'Target "Q"'


def test_column_and_measure_ref():
    assert column_ref("O'Brien", "a]b") == "'O''Brien'[a]]b]"
    assert measure_ref("x]") == "[x]]]"


@pytest.mark.parametrize(
    "tok,expected",
    [
        ("'O''Brien'[X]", ("O'Brien", "X")),
        ("'Sales' [Amount]", ("Sales", "Amount")),
        ("'a[b'[c]]d]", ("a[b", "c]d")),
        ("Sales[Amount]", ("Sales", "Amount")),
    ],
)
def test_split_qualified(tok, expected):
    assert split_qualified(tok) == expected


# --- rename_table_refs ---------------------------------------------------------------


def test_rename_table_all_forms_and_leaves_strings_comments():
    expr = (
        "CALCULATE(SUM('Old'[a]), ALL(Old), Old[b] = \"Old\") // Old\n"
        "+ COUNTROWS(RELATEDTABLE(old)) + TREATAS({1}, Old[c]) + [Old]"
    )
    out, n = rename_table_refs(expr, "Old", "New's")
    assert n == 5
    assert out == (
        "CALCULATE(SUM('New''s'[a]), ALL('New''s'), 'New''s'[b] = \"Old\") // Old\n"
        "+ COUNTROWS(RELATEDTABLE('New''s')) + TREATAS({1}, 'New''s'[c]) + [Old]"
    )


def test_rename_table_does_not_touch_prefix_matches_or_vars():
    expr = "SUM(MySales[x]) + SUM(Sales[x]) + VAR Salesx = 1 RETURN Salesx"
    out, n = rename_table_refs(expr, "Sales", "Fact")
    assert n == 1
    assert "MySales[x]" in out and "'Fact'[x]" in out and "Salesx" in out


def test_rename_table_bare_in_calculate_and_evaluate():
    out, n = rename_table_refs("EVALUATE Old", "Old", "New")
    assert (out, n) == ("EVALUATE 'New'", 1)
    out, n = rename_table_refs("CALCULATE([m], Old)", "Old", "New")
    assert out == "CALCULATE([m], 'New')"


def test_rename_table_escaped_old_name():
    out, n = rename_table_refs("SUM('O''Brien'[X])", "O'Brien", "OBrien")
    assert out == "SUM('OBrien'[X])" and n == 1


def test_rename_table_noop_on_non_string_and_same_name():
    assert rename_table_refs(None, "a", "b") == (None, 0)
    assert rename_table_refs("SUM(T[a])", "T", "T") == ("SUM(T[a])", 0)


# --- rename_column_refs ---------------------------------------------------------------


def test_rename_column_only_that_tables_column():
    expr = "SUM(Sales[Amount]) + SUM(MySales[Amount]) + [Amount] + SUM('Sales'[amount])"
    out, n = rename_column_refs(expr, "Sales", "Amount", "Amt")
    assert n == 2
    assert out == "SUM(Sales[Amt]) + SUM(MySales[Amount]) + [Amount] + SUM('Sales'[Amt])"


def test_rename_column_unqualified_only_when_asked():
    out, n = rename_column_refs(
        "Sales[Amount] * [Amount]", "Sales", "Amount", "Amt", include_unqualified=True
    )
    assert (out, n) == ("Sales[Amt] * [Amt]", 2)


def test_rename_column_escapes_new_name_and_matches_escaped_old():
    out, n = rename_column_refs("T[a]]b] + T[x]", "T", "a]b", "c]d")
    assert out == "T[c]]d] + T[x]" and n == 1


def test_rename_column_ignores_format_strings():
    out, n = rename_column_refs('FORMAT(T[Red], "0;[Red]-0")', "T", "Red", "Crimson")
    assert out == 'FORMAT(T[Crimson], "0;[Red]-0")' and n == 1


# --- rename_measure_refs ---------------------------------------------------------------


def test_rename_measure_lone_refs_and_home_table_refs():
    expr = "[Sales] + 'm'[Sales] + T[Sales] + \"[Sales]\" -- [Sales]\n+ [sales]"
    out, n = rename_measure_refs(expr, "Sales", "Revenue", home_table="m")
    assert n == 3
    assert out == "[Revenue] + 'm'[Revenue] + T[Sales] + \"[Sales]\" -- [Sales]\n+ [Revenue]"


def test_rename_measure_without_home_table_leaves_qualified():
    out, n = rename_measure_refs("[A] + m[A]", "A", "B")
    assert (out, n) == ("[B] + m[A]", 1)


def test_rename_preserves_bytes_outside_refs():
    expr = "VAR  x =\t[A]   // keep   me\r\nRETURN   x"
    out, n = rename_measure_refs(expr, "A", "Z")
    assert out == expr.replace("[A]", "[Z]") and n == 1
