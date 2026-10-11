"""2026-10-03 deep review, slice D: codemod findings D-04 (use_divide), D-12 (remove_unused_variables),
D-13 (transaction verification of measures that are BLANK at the grand total)."""

from types import SimpleNamespace as NS

import pytest

from core.codemods import transaction as tx
from core.codemods.transforms.remove_unused_variables import RemoveUnusedVariables
from core.codemods.transforms.use_divide import UseDivide


def _model(expression, name="M"):
    m = NS(name=name, expression=expression, table_name="T")
    return NS(tables=[NS(name="T", columns=[], measures=[m])])


# ------------------------------------------------------------------------------------- D-12


def test_d12_comment_between_vars_survives_the_removal():
    expr = "VAR _unused = 1\n// ---- main calc: do not simplify ----\nVAR _x = SUM(Sales[Amount])\nRETURN _x"
    out = RemoveUnusedVariables._rewrite(expr)
    assert out == "// ---- main calc: do not simplify ----\nVAR _x = SUM(Sales[Amount])\nRETURN _x"


def test_d12_blank_lines_inside_a_string_literal_are_untouched():
    expr = 'VAR _unused = 1\nVAR _svg = "<svg>\n\n<rect/>\n\n</svg>"\nRETURN _svg'
    out = RemoveUnusedVariables._rewrite(expr)
    assert out == 'VAR _svg = "<svg>\n\n<rect/>\n\n</svg>"\nRETURN _svg'


def test_d12_trailing_comment_of_the_removed_declaration_goes_with_it():
    out = RemoveUnusedVariables._rewrite("VAR _dead = 1 // not needed\nVAR _x = 2\nRETURN _x")
    assert out == "VAR _x = 2\nRETURN _x"


def test_d12_block_comment_before_the_block_and_whole_block_removal():
    assert (
        RemoveUnusedVariables._rewrite("/* header */\nVAR _unused = 1\nRETURN [A]")
        == "/* header */\n[A]"
    )
    assert RemoveUnusedVariables._rewrite("VAR _y = [Y]\nVAR _x = _y\nRETURN 5") == "5"


def test_d12_the_users_own_blank_lines_are_kept():
    expr = "VAR _a = 1\n\nVAR _unused = 2\n\nVAR _b = _a\nRETURN _b"
    out = RemoveUnusedVariables._rewrite(expr)
    # the user's blank line between the kept VARs survives; the two that framed the removed one
    # collapse into one instead of piling up
    assert out == "VAR _a = 1\n\nVAR _b = _a\nRETURN _b"


def test_d12_nothing_to_remove_is_a_no_op():
    assert RemoveUnusedVariables._rewrite("VAR _x = 1\nRETURN _x") is None


# ------------------------------------------------------------------------------------- D-04


@pytest.mark.parametrize(
    "expr",
    [
        "[Sales] / 1000",
        "[Seconds] / (60 * 60 * 24)",
        "[Sales] / -1000",
        "[a] / POWER(10, 6)",
        "[Sales] / 1E3",
    ],
)
def test_d04_use_divide_honours_the_constant_denominator_exemption(expr):
    assert UseDivide._rewrite(expr) is None
    assert UseDivide().preview(_model(expr), {}).edits == []


def test_d04_use_divide_still_rewrites_a_real_division():
    assert UseDivide._rewrite("[a] / [b]") == "DIVIDE([a], [b])"
    assert UseDivide._rewrite("[a] / ([b] - 1)") == "DIVIDE([a], ([b] - 1))"
    assert UseDivide._rewrite("[a] / 0") == "DIVIDE([a], 0)"  # zero is not an exempt constant


def test_d04_auto_fixable_flag_only_when_the_transform_would_edit():
    from server.handlers.dax_context_handler import _fix_would_edit

    assert _fix_would_edit("CORR_DIVISION_WITHOUT_DIVIDE", "[a] / [b]")
    # in-iterator division: the rule fires (info) but use_divide does not touch it
    assert not _fix_would_edit("CORR_DIVISION_WITHOUT_DIVIDE", "SUMX(T, T[a] / T[b])")
    assert _fix_would_edit("MAINT_UNUSED_VAR", "VAR _u = 1 VAR _x = 2 RETURN _x")
    assert not _fix_would_edit("MAINT_UNUSED_VAR", "VAR _x = 2 RETURN _x")


# ------------------------------------------------------------------------------------- D-13


def test_d13_measure_blank_at_the_grand_total_is_inconclusive_not_a_semantic_change():
    v = tx._compare_values({"T[M]": None}, {"T[M]": None})
    assert v["inconclusive"] is True and v["equivalent"] is None
    assert v["reason"] == "inconclusive_all_blank"
    assert v["inconclusive_measures"] == ["T[M]"]
    assert not tx._verification_failed(v)


def test_d13_other_measures_are_still_verified_and_mismatches_still_fail():
    ok = tx._compare_values({"T[M]": None, "T[N]": 5}, {"T[M]": None, "T[N]": 5})
    assert ok["equivalent"] is True and ok["inconclusive_measures"] == ["T[M]"]
    bad = tx._compare_values({"T[M]": None, "T[N]": 5}, {"T[M]": None, "T[N]": 6})
    assert bad["equivalent"] is False and tx._verification_failed(bad)
    blank_to_value = tx._compare_values({"T[M]": None}, {"T[M]": 3})
    assert tx._verification_failed(blank_to_value)  # BLANK -> a value IS a change
    assert tx._verification_failed(tx._compare_values({"T[M]": 1}, None))


@pytest.fixture
def _journal(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_CODEMOD_JOURNAL_DIR", str(tmp_path))
    return tmp_path


class _Persister:
    def __init__(self):
        self.upserts = []

    def upsert_measure(self, *, table_name, measure_name, dax_expression, **kw):
        self.upserts.append((table_name, measure_name, dax_expression))
        return {"success": True}

    def delete_measure(self, *, table_name, measure_name, **kw):
        return {"success": True}


def _apply_remove_unused(evaluator):
    class _M:
        def __init__(self, name, expression):
            self.name, self.expression = name, expression

    model = NS(tables=[NS(name="T", measures=[_M("Kpi", "VAR _u = 1 VAR _x = [Base] RETURN _x")])])
    plan = tx.preview(model, "remove_unused_variables", {})["plan_hash"]
    persister = _Persister()
    out = tx.apply(
        model,
        "remove_unused_variables",
        {},
        plan,
        persister=persister,
        evaluator=evaluator,
        verify=True,
    )
    return out, persister


def test_d13_apply_keeps_a_rewrite_whose_measure_is_blank_at_the_grand_total(_journal):
    """End to end: a HASONEVALUE-guarded KPI is BLANK at the grand total before and after. The
    correct rewrite used to be rolled back as ``semantic_change_detected``."""
    out, persister = _apply_remove_unused(lambda refs: {f"{t}[{m}]": None for t, m in refs})
    assert out["success"] is True, out
    assert out["verification"]["inconclusive"] is True
    assert out.get("rolled_back") is None
    assert any("inconclusive" in w for w in out["warnings"])
    assert len(persister.upserts) == 1  # written once, never restored


def test_d13_apply_still_rolls_back_a_real_value_change(_journal):
    values = iter([{"T[Kpi]": 1}, {"T[Kpi]": 2}])
    out, persister = _apply_remove_unused(lambda refs: next(values))
    assert out["success"] is False and out["error_type"] == "semantic_change_detected"
    assert out["rolled_back"] is not None
