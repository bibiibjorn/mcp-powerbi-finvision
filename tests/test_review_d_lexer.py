"""2026-09-22 deep review, slice D — the DAX lexer (D-01, D-15, D-16).

The lexer is the single DAX front-end: comments, strings, quoted tables and
bracket names must be recognised exactly, or every consumer (rules, codemods,
reference extraction, the syntax gate) misreads the expression.
"""

import time

import pytest

from core.dax.knowledge import DaxFunctionDatabase
from core.dax.tokenizer import DaxLexer, TokenType


@pytest.fixture(scope="module")
def lx():
    return DaxLexer(function_names=DaxFunctionDatabase.get().get_function_names())


def kinds(lx, s):
    return [(t.type, t.value) for t in lx.tokenize_code(s)]


# --- D-01: "--" line comments --------------------------------------------------


def test_double_dash_is_a_line_comment(lx):
    toks = kinds(lx, "SUM(T[a]) -- total of [Fake]\n+ 1")
    assert (TokenType.COLUMN_REF, "[Fake]") not in toks
    assert toks[-2:] == [(TokenType.OPERATOR, "+"), (TokenType.NUMBER, "1")]
    full = [t for t in lx.tokenize("x -- c\ny") if t.type == TokenType.COMMENT_LINE]
    assert [t.value for t in full] == ["-- c"]


def test_double_dash_comment_does_not_create_a_var(lx):
    toks = kinds(lx, "VAR a = [A]\n-- VAR b = old\nRETURN a")
    assert [v for t, v in toks if t == TokenType.KEYWORD] == ["VAR", "RETURN"]


def test_remove_unused_variables_keeps_return_after_dash_comment():
    """The D-01 failure chain: lint -> remove_unused_variables used to write a
    measure whose RETURN was commented out."""
    from core.codemods.transforms.remove_unused_variables import RemoveUnusedVariables

    expr = "VAR a = [A]\n-- VAR b = old\nRETURN a"
    assert RemoveUnusedVariables._remove_one_unused(expr) is None


def test_slash_comments_and_block_comments(lx):
    assert kinds(lx, "1 // [x]\n+ 2 /* [y] ( */ + 3") == [
        (TokenType.NUMBER, "1"),
        (TokenType.OPERATOR, "+"),
        (TokenType.NUMBER, "2"),
        (TokenType.OPERATOR, "+"),
        (TokenType.NUMBER, "3"),
    ]


def test_unterminated_block_comment_is_flagged(lx):
    t = [t for t in lx.tokenize("1 /* open") if t.type == TokenType.COMMENT_BLOCK][0]
    assert t.terminated is False


# --- literals and names ---------------------------------------------------------


@pytest.mark.parametrize(
    "src,ttype,value",
    [
        ('"a ""q"" // x -- y /* z"', TokenType.STRING, '"a ""q"" // x -- y /* z"'),
        ("'O''Brien'[X]", TokenType.QUALIFIED_REF, "'O''Brien'[X]"),
        ("[Col ]]name]", TokenType.COLUMN_REF, "[Col ]]name]"),
        ("'Table Name'", TokenType.TABLE_REF, "'Table Name'"),
        ("'Sales' [Amount]", TokenType.QUALIFIED_REF, "'Sales' [Amount]"),
        ("'Sales'\t[Amount]", TokenType.QUALIFIED_REF, "'Sales'\t[Amount]"),
        ('dt"2024-01-01"', TokenType.DATETIME, 'dt"2024-01-01"'),
        ("[Men's Sales]", TokenType.COLUMN_REF, "[Men's Sales]"),
        ("[Rate )]", TokenType.COLUMN_REF, "[Rate )]"),
    ],
)
def test_single_token_literals(lx, src, ttype, value):
    toks = lx.tokenize_code(src)
    assert len(toks) == 1
    assert toks[0].type == ttype
    assert toks[0].value == value
    assert toks[0].terminated is True


@pytest.mark.parametrize(
    "src,ttype",
    [
        ('"abc', TokenType.STRING),
        ("'abc", TokenType.TABLE_REF),
        ("[abc", TokenType.COLUMN_REF),
        ('"a""', TokenType.STRING),
        ("'T'[c", TokenType.QUALIFIED_REF),
    ],
)
def test_unterminated_delimited_tokens(lx, src, ttype):
    toks = lx.tokenize_code(src)
    assert toks[-1].type == ttype and toks[-1].terminated is False


@pytest.mark.parametrize("num", ["1", "1.5", ".5", "2.", "1e-3", "1.2E+4", "10e2"])
def test_numbers(lx, num):
    assert kinds(lx, num) == [(TokenType.NUMBER, num)]


def test_number_followed_by_identifier_is_not_scientific(lx):
    assert kinds(lx, "1else")[0] == (TokenType.NUMBER, "1")


def test_unicode_names(lx):
    toks = kinds(lx, "m_Measures[Δ vs PY] + [€ Sales] + Überblick")
    assert (TokenType.COLUMN_REF, "[Δ vs PY]") in toks
    assert (TokenType.COLUMN_REF, "[€ Sales]") in toks
    assert (TokenType.IDENTIFIER, "Überblick") in toks


# --- D-16: coverage gaps ------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    ["STDEVX.S", "PERCENTILEX.INC", "INFO.VIEW.MEASURES", "CHISQ.DIST.RT", "IF.EAGER"],
)
def test_dotted_function_names_are_one_function_token(lx, call):
    toks = lx.tokenize_code(f"{call}(T, T[a])")
    assert toks[0].type == TokenType.FUNCTION
    assert toks[0].value.upper() == call


def test_dotted_call_joined_even_without_function_catalogue():
    toks = DaxLexer().tokenize_code("STDEVX.S(T, T[a])")
    assert toks[0].value == "STDEVX.S"
    assert toks[1].type == TokenType.PAREN_OPEN


def test_udf_namespace_in_header_is_not_joined(lx):
    toks = lx.tokenize_code("FUNCTION Sales.AddMargin = (x) => x")
    assert [t.type for t in toks[:4]] == [
        TokenType.KEYWORD,
        TokenType.IDENTIFIER,
        TokenType.DOT,
        TokenType.IDENTIFIER,
    ]


def test_if_eager_is_not_the_if_keyword(lx):
    toks = lx.tokenize_code("IF.EAGER(a, b, c)")
    assert toks[0].value == "IF.EAGER"
    assert not (toks[0].type == TokenType.KEYWORD)


@pytest.mark.parametrize(
    "src,expected",
    [
        ("2 ^ 3", [(TokenType.NUMBER, "2"), (TokenType.OPERATOR, "^"), (TokenType.NUMBER, "3")]),
        (
            "a == b",
            [(TokenType.IDENTIFIER, "a"), (TokenType.OPERATOR, "=="), (TokenType.IDENTIFIER, "b")],
        ),
        (
            "{1;2}",
            [
                (TokenType.BRACE_OPEN, "{"),
                (TokenType.NUMBER, "1"),
                (TokenType.SEMICOLON, ";"),
                (TokenType.NUMBER, "2"),
                (TokenType.BRACE_CLOSE, "}"),
            ],
        ),
        (
            "a <> b",
            [(TokenType.IDENTIFIER, "a"), (TokenType.OPERATOR, "<>"), (TokenType.IDENTIFIER, "b")],
        ),
        (
            "a && b || c",
            [
                (TokenType.IDENTIFIER, "a"),
                (TokenType.OPERATOR, "&&"),
                (TokenType.IDENTIFIER, "b"),
                (TokenType.OPERATOR, "||"),
                (TokenType.IDENTIFIER, "c"),
            ],
        ),
    ],
)
def test_operators_and_punctuation(lx, src, expected):
    assert kinds(lx, src) == expected


def test_udf_signature_tokens(lx):
    toks = kinds(lx, "FUNCTION f = (x: SCALAR NUMERIC VAL) => x*2")
    assert toks[0] == (TokenType.KEYWORD, "FUNCTION")
    assert (TokenType.COLON, ":") in toks
    assert (TokenType.OPERATOR, "=>") in toks


@pytest.mark.parametrize(
    "kw",
    [
        "DEFINE",
        "MEASURE",
        "VAR",
        "COLUMN",
        "TABLE",
        "FUNCTION",
        "RETURN",
        "EVALUATE",
        "ORDER",
        "BY",
    ],
)
def test_query_keywords(lx, kw):
    assert lx.tokenize_code(kw)[0].type == TokenType.KEYWORD


def test_multiline_string_reports_start_line(lx):
    toks = [
        t
        for t in lx.tokenize('X\n"a\nb"\n[Y]')
        if t.type in (TokenType.STRING, TokenType.COLUMN_REF)
    ]
    assert [(t.type, t.line) for t in toks] == [(TokenType.STRING, 2), (TokenType.COLUMN_REF, 4)]


def test_crlf_positions(lx):
    toks = lx.tokenize_code("A\r\nB")
    assert [(t.value, t.line, t.col) for t in toks] == [("A", 1, 1), ("B", 2, 1)]


def test_token_spans_reconstruct_the_source(lx):
    src = "VAR x = 'O''B'[c] // hi\r\n RETURN IF.EAGER(x > .5, \"a\"\"b\", [m]]x]) /* z */"
    assert "".join(t.value for t in lx.tokenize(src)) == src


def test_function_args_split_on_top_level_commas_only(lx):
    toks = lx.tokenize_code('TREATAS({"a","b"}, T[c])')
    args = lx.extract_function_args(toks, 0)
    assert len(args) == 2
    assert [t.value for t in args[1]] == ["T", "[c]"]


def test_extract_function_args_requires_adjacent_paren(lx):
    toks = lx.tokenize_code("SUM + (1, 2)")
    assert lx.extract_function_args(toks, 0) == []


# --- D-15: the analyzer is no longer quadratic -------------------------------------------


def _big_switch(n):
    arms = ",\n    ".join(f"[Sel] = {i}, CALCULATE([M{i}], 'T'[C] = \"{i}\")" for i in range(n))
    return f"VAR _s = [Sel]\nRETURN SWITCH(TRUE(),\n    {arms},\n    BLANK())"


def test_480_arm_switch_analyzes_fast():
    from core.dax.analyzer.unified_analyzer import get_shared_analyzer

    analyzer = get_shared_analyzer()
    analyzer.analyze(_big_switch(10))  # warm
    start = time.perf_counter()
    analyzer.analyze(_big_switch(480))
    elapsed = time.perf_counter() - start
    # Old code: ~2.3 s (build_paren_map recomputed per rule call). Target < 0.2 s;
    # the assert leaves head-room for slow CI machines.
    assert elapsed < 0.6, f"480-arm SWITCH took {elapsed:.3f}s"


def test_paren_map_is_cached_per_token_list(lx):
    toks = lx.tokenize_code("F(G(1), H(2))")
    assert lx.paren_map(toks) is lx.paren_map(toks)
