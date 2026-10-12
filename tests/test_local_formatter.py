"""F-23 (2026-10-03 deep review): deterministic local DAX formatter + ``format_dax engine=local``.

Round-trip corpus: every snippet of ``tests/_dax_corpus_data.py`` must format, stay
token-equivalent (the same guard the daxformatter client uses), keep its comments and be
idempotent; plus golden layouts, comment / string fidelity, a seeded comment-injection fuzz
(idempotency + equivalence) and the handler wiring (no network, no opt-in needed).
"""

import random

import pytest

from core.dax.dax_formatter_client import token_equivalent
from core.dax.local_formatter import format_dax_local, format_many_local
from core.dax.tokenizer import DaxLexer, TokenType
from tests._dax_corpus_data import C

_CORPUS = [(e["id"], e["dax"]) for e in C]


def _fmt(text, **kw):
    res = format_dax_local(text, **kw)
    assert res["success"], res
    return res["formatted"]


def _comments(text):
    return [
        t.value.rstrip()
        for t in DaxLexer().tokenize(text)
        if t.type in (TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK)
    ]


# --- round-trip corpus -----------------------------------------------------------------------


@pytest.mark.parametrize("cid,dax", _CORPUS, ids=[c for c, _ in _CORPUS])
def test_corpus_formats_equivalent_commented_and_idempotent(cid, dax):
    out = _fmt(dax)
    assert token_equivalent(dax, out) == (True, None)
    assert _comments(out) == _comments(dax)
    assert _fmt(out) == out
    # a narrow and a wide layout are both stable too
    for width, skip in ((30, False), (200, True)):
        o = _fmt(dax, max_line_length=width, skip_space_after_function=skip)
        assert token_equivalent(dax, o)[0]
        assert _fmt(o, max_line_length=width, skip_space_after_function=skip) == o


# --- golden layouts ----------------------------------------------------------------------------


def test_golden_var_return_and_short_calls_stay_on_one_line():
    src = "VAR _s = SUM(Sales[Amount]) VAR _c = SUM(Sales[Cost]) RETURN DIVIDE(_s - _c, _s)"
    assert _fmt(src) == (
        "VAR _s =\n"
        "    SUM ( Sales[Amount] )\n"
        "VAR _c =\n"
        "    SUM ( Sales[Cost] )\n"
        "RETURN\n"
        "    DIVIDE ( _s - _c, _s )"
    )


def test_golden_long_call_one_argument_per_line():
    src = (
        "CALCULATE(SUMX(FILTER(ALL('Date'), 'Date'[Date] <= MAX('Date'[Date])), "
        'Sales[Amount] * Sales[Qty]), KEEPFILTERS(\'Product\'[Color] IN {"Blue", "Red"}))'
    )
    assert _fmt(src) == (
        "CALCULATE (\n"
        "    SUMX (\n"
        "        FILTER ( ALL ( 'Date' ), 'Date'[Date] <= MAX ( 'Date'[Date] ) ),\n"
        "        Sales[Amount] * Sales[Qty]\n"
        "    ),\n"
        '    KEEPFILTERS ( \'Product\'[Color] IN { "Blue", "Red" } )\n'
        ")"
    )


def test_golden_operator_chain_breaks_before_the_operator():
    src = " + ".join(f"[Sales Amount {w}]" for w in ("One", "Two", "Three", "Four", "Five", "Six"))
    out = _fmt(src)
    lines = out.splitlines()
    assert lines[0] == "[Sales Amount One]"
    assert all(line.startswith("    + [Sales Amount ") for line in lines[1:])
    assert all(len(line) <= 90 for line in lines)


def test_case_spacing_and_unary_signs():
    out = _fmt("sumx(sales,sales[qty]*sales[price])+udf_thing(1,2)+-[x]-(-3)")
    # built-ins upper-case, a user-defined function / table keep their spelling
    assert out == "SUMX ( sales, sales[qty] * sales[price] ) + udf_thing ( 1, 2 ) + -[x] - ( -3 )"


def test_skip_space_after_function_and_empty_call():
    assert _fmt("blank()", skip_space_after_function=True) == "BLANK()"
    assert _fmt("blank()") == "BLANK ()"
    assert _fmt("sum(x[a])", skip_space_after_function=True) == "SUM( x[a] )"


def test_lambda_udf_and_doc_comments():
    out = _fmt("/// Adds tax\nFUNCTION AddTax = (amount : NUMERIC) => amount * 1.1")
    assert out == "/// Adds tax\nFUNCTION AddTax = (amount : NUMERIC) => amount * 1.1"
    body = _fmt("(x : NUMERIC, y : TABLE VAL) => VAR t = x * 2 RETURN t + COUNTROWS(y)")
    assert body == (
        "(x : NUMERIC, y : TABLE VAL) =>\n"
        "    VAR t =\n"
        "        x * 2\n"
        "    RETURN\n"
        "        t + COUNTROWS ( y )"
    )


def test_define_evaluate_query_layout():
    out = _fmt(
        "DEFINE MEASURE Sales[A] = SUM(Sales[Amount]) "
        "EVALUATE SUMMARIZECOLUMNS('Date'[Year], \"A\", [A]) ORDER BY 'Date'[Year] DESC"
    )
    assert out == (
        "DEFINE\n"
        "    MEASURE Sales[A] =\n"
        "        SUM ( Sales[Amount] )\n"
        "EVALUATE\n"
        "SUMMARIZECOLUMNS ( 'Date'[Year], \"A\", [A] )\n"
        "ORDER BY 'Date'[Year] DESC"
    )


def test_var_inside_a_call_gets_its_own_lines():
    out = _fmt("IF([Sales] > 0, VAR _c = [Cost] RETURN [Sales] - _c)")
    assert out == (
        "IF (\n"
        "    [Sales] > 0,\n"
        "    VAR _c =\n"
        "        [Cost]\n"
        "    RETURN\n"
        "        [Sales] - _c\n"
        ")"
    )


def test_empty_arguments_are_kept():
    assert _fmt("RUNNINGSUM([Sales Amount], ROWS, , HIGHESTPARENT)") == (
        "RUNNINGSUM ( [Sales Amount], ROWS, , HIGHESTPARENT )"
    )


def test_range_colon_is_not_spaced():
    assert _fmt("STARTOFWEEK('Date'[Date]):ENDOFWEEK('Date'[Date])") == (
        "STARTOFWEEK ( 'Date'[Date] ):ENDOFWEEK ( 'Date'[Date] )"
    )


# --- comments and strings are carried exactly ----------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "// head\n-- second\n/* block\nacross lines */\nSUM(Sales[Amount])",
        "SUM(Sales[Amount]) // tail\n// own line\n/* end */",
        "CALCULATE(\n  [Sales], // base\n  'Date'[Year] = 2024, /* filter */ Product[Color] = \"Red\"\n)",
        "VAR x = 1 // one\n\n\nVAR y = /* two */ 2\nRETURN x + y /* end */",
        "DEFINE\n    /// Description\n    FUNCTION f = (x : ANYVAL) => x + 1\nEVALUATE { f(1) }",
        "SUM(x[a] /* before comma */, 2)",
    ],
)
def test_comments_survive_in_order(src):
    out = _fmt(src)
    assert _comments(out) == _comments(src)
    assert token_equivalent(src, out)[0]
    assert _fmt(out) == out


def test_trailing_comments_stay_at_the_end_of_their_line():
    out = _fmt("CALCULATE([Sales], // base\n'Date'[Year] = 2024 // filter\n)")
    assert out == "CALCULATE (\n    [Sales], // base\n    'Date'[Year] = 2024 // filter\n)"


def test_string_literals_are_byte_exact():
    src = 'SUMX(T, IF(T[a] = "x  //  y --  /* z */  ""q""", "multi\nline  ", "  "))'
    out = _fmt(src)
    for lit in ('"x  //  y --  /* z */  ""q"""', '"multi\nline  "', '"  "'):
        assert lit in out
    assert token_equivalent(src, out)[0]


def test_crlf_input_keeps_crlf_output():
    out = _fmt("VAR x = 1\r\nRETURN x")
    assert "\r\n" in out and "\n" not in out.replace("\r\n", "")


def test_unbalanced_or_unterminated_text_is_refused_and_untouched():
    for bad in ("SUM(Sales[Amount]", "SUM(Sales[Amount]))", 'x & "open', "[Sales"):
        res = format_dax_local(bad)
        assert res["success"] is False
        assert res["formatted"] == bad


def test_blank_input_is_a_noop():
    assert format_dax_local("  \n ")["formatted"] == ""


# --- seeded comment-injection fuzz (idempotency + equivalence) -----------------------------------

_FUZZ_SNIPPETS = [
    "DIVIDE([Sales], CALCULATE([Sales], REMOVEFILTERS(Customer), VALUES(Customer[Continent])))",
    "VAR _a = CALCULATE([Sales], 'Date'[Year] = 2024) RETURN IF(_a > 0, _a - [Cost], BLANK())",
    "SUMX(Sales, Sales[Qty] * RELATED(Product[Price])) + [Margin] * 2",
    'SWITCH(TRUE(), [Sales] > 100, "A", [Sales] > 50, "B", "C")',
]
_INJECTIONS = [" /*b*/ ", "\n// own\n", " // trail\n", "\n\n", " /* ml\nx */ ", "\n/* blk */\n"]


def test_comment_injection_fuzz_is_idempotent_and_equivalent():
    rnd = random.Random(20261003)
    for src in _FUZZ_SNIPPETS:
        toks = DaxLexer().tokenize(src)
        for _ in range(25):
            pos = rnd.randrange(len(toks) + 1)
            text = (
                "".join(t.value for t in toks[:pos])
                + rnd.choice(_INJECTIONS)
                + "".join(t.value for t in toks[pos:])
            )
            for width in (90, 40):
                res = format_dax_local(text, max_line_length=width)
                assert res["success"], (text, res)
                again = format_dax_local(res["formatted"], max_line_length=width)
                assert again["formatted"] == res["formatted"], text


# --- format_many_local + handler -----------------------------------------------------------------


def test_format_many_local_shape_matches_the_service_client():
    res = format_many_local(
        [
            {"key": "T[A]", "expression": "sum(Sales[Amount])"},
            {"key": "T[B]", "expression": "SUM ( Sales[Amount] )"},
            {"key": "T[C]", "expression": "SUM(Sales[Amount]"},
        ]
    )
    assert [r["key"] for r in res["results"]] == ["T[A]", "T[B]", "T[C]"]
    assert res["results"][0]["formatted"] == "SUM ( Sales[Amount] )"
    assert res["results"][2]["success"] is False
    assert res["success"] is False
    assert res["stats"]["succeeded"] == 1 and res["stats"]["unchanged"] == 1
    assert res["stats"]["failed"] == 1 and res["remaining"] == []


class _Meas:
    def __init__(self, name, expression):
        self.name, self.expression = name, expression


class _Tbl:
    def __init__(self, name, measures):
        self.name, self.measures = name, measures


class _Model:
    def __init__(self, tables):
        self.tables = tables


@pytest.fixture
def local_handler(monkeypatch):
    from core.infrastructure import live_model_facade
    from core.infrastructure.connection_state import connection_state
    from server.handlers import tmdl_handler

    monkeypatch.setenv("MCP_DAX_FORMATTER_ENABLED", "0")  # the external service is OFF
    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    model = _Model([_Tbl("Sales", [_Meas("Total", "sum(Sales[Amount])"), _Meas("Bad", "SUM(")])])
    monkeypatch.setattr(live_model_facade, "build_live_model", lambda *a, **k: model)
    import requests

    def boom(*a, **k):  # the local engine must never touch the network
        raise AssertionError("network used")

    monkeypatch.setattr(requests, "post", boom)
    return tmdl_handler


def test_engine_local_works_without_the_external_opt_in(local_handler):
    res = local_handler.handle_tmdl_operations(
        {
            "operation": "format_dax",
            "engine": "local",
            "measure_refs": ["Sales[Total]"],
            "dry_run": True,
        }
    )
    assert res["success"] is True
    assert res["engine"] == "local" and res["external_service"] is None
    assert res["formatted"][0]["after"] == "SUM ( Sales[Amount] )"
    assert res["formatted"][0]["written"] is False


def test_default_engine_still_needs_the_opt_in_and_points_to_local(local_handler):
    res = local_handler.handle_tmdl_operations({"operation": "format_dax", "format_all": True})
    assert res["success"] is False
    assert res["error_type"] == "external_formatter_disabled"
    assert "engine='local'" in res["suggestion"]


def test_invalid_engine_is_rejected(local_handler):
    res = local_handler.handle_tmdl_operations(
        {"operation": "format_dax", "engine": "magic", "format_all": True}
    )
    assert res["success"] is False and res["error_type"] == "invalid_argument"


def test_local_engine_writes_only_equivalent_text_and_reports_refusals(local_handler, monkeypatch):
    written = []

    class _FakeMeasureOps:
        def execute(self, payload):
            written.append(payload)
            return {"success": True}

    import core.operations.measure_operations as mo

    monkeypatch.setattr(mo, "MeasureOperationsHandler", _FakeMeasureOps)
    res = local_handler.handle_tmdl_operations(
        {"operation": "format_dax", "engine": "local", "format_all": True}
    )
    assert [w["measure_name"] for w in written] == ["Total"]
    assert written[0]["expression"] == "SUM ( Sales[Amount] )"
    rows = {r["ref"]: r for r in res["formatted"]}
    assert rows["Sales[Total]"]["written"] is True
    assert rows["Sales[Bad]"]["written"] is False and "refused" in rows["Sales[Bad]"]["error"]
    assert res["success"] is False  # one measure could not be formatted
