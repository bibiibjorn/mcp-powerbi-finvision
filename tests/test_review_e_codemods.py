"""2026-09-22 review slice E — 15_DAX_Refactor transforms, transaction, journal.

Covers E-05 (inline context transition / comments / case / qualified / ]]),
E-06 (delete_after guards), E-10 (use_divide, remove_unused_variables,
swap_table_reference, extract_measure), E-11 (extract collisions), E-12 (plan
hash covers transform/args/identity), E-13 (rollback safety), E-14 (one atomic
write), E-22 (scope filter, full preview, not-persistable flag), E-27
(before/after verification) and K-04 (rollback deletes created measures).
Fakes only; the TOM-level tests use PascalCase TOM-shaped objects.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from core.codemods import dax_text
from core.codemods import transaction as tx
from core.codemods.transforms.extract_measure import ExtractMeasure
from core.codemods.transforms.inline_measure import InlineMeasure
from core.codemods.transforms.remove_unused_variables import RemoveUnusedVariables
from core.codemods.transforms.swap_table_reference import SwapTableReference
from core.codemods.transforms.use_divide import UseDivide
from core.dax.dax_injector import apply_changes_to_model

# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


def model(*measures, table="Sales", columns=(), extra_tables=()):
    t = NS(
        name=table,
        measures=[NS(name=n, expression=e) for n, e in measures],
        columns=[NS(name=c) for c in columns],
    )
    return NS(tables=[t, *extra_tables], relationships=[])


class Persister:
    """Legacy (non-atomic) persister fake."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.upserts, self.deletes = [], []

    def upsert_measure(self, *, table_name, measure_name, dax_expression, **kw):
        self.upserts.append((table_name, measure_name, dax_expression))
        if measure_name in self.fail:
            return {"success": False, "error": "boom"}
        return {"success": True}

    def delete_measure(self, *, table_name, measure_name, **kw):
        self.deletes.append((table_name, measure_name))
        return {"success": True}


class AtomicPersister:
    """Atomic persister fake over a PascalCase TOM model."""

    def __init__(self, tom):
        self.tom = tom
        self.calls = []

    def apply_measure_changes(self, upserts, deletes=None):
        self.calls.append((upserts, deletes))
        return apply_changes_to_model(self.tom, upserts, deletes, measure_factory=TomMeasure)


class Coll(list):
    def Add(self, x):  # noqa: N802 - TOM surface
        self.append(x)

    def Remove(self, x):  # noqa: N802
        self.remove(x)


class TomMeasure:
    def __init__(self, Name="", Expression=""):  # noqa: N803
        self.Name = Name
        self.Expression = Expression
        self.FormatString = None
        self.DisplayFolder = None
        self.Description = None
        self.DataCategory = None
        self.IsHidden = False
        self.ErrorMessage = ""
        self.KPI = None
        self.FormatStringDefinition = None
        self.DetailRowsDefinition = None


class TomColumn:
    def __init__(self, Name, Expression=None):  # noqa: N803
        self.Name = Name
        self.Expression = Expression


class TomTable:
    def __init__(self, Name, measures=(), columns=()):  # noqa: N803
        self.Name = Name
        self.Measures = Coll(measures)
        self.Columns = Coll(columns)
        self.Partitions = Coll()
        self.CalculationGroup = None


class TomModel:
    def __init__(self, tables, roles=()):
        self.Tables = Coll(tables)
        self.Roles = Coll(roles)
        self.Functions = Coll()
        self.saves = 0
        self.undos = 0
        self.break_on_save = None

    def SaveChanges(self):  # noqa: N802
        self.saves += 1
        if self.break_on_save:
            for t in self.Tables:
                for m in t.Measures:
                    if self.break_on_save in (m.Expression or ""):
                        m.ErrorMessage = "The syntax for 'x' is incorrect"
                    else:
                        m.ErrorMessage = ""

    def UndoLocalChanges(self):  # noqa: N802
        self.undos += 1


def tom_to_facade(tom):
    return NS(
        tables=[
            NS(
                name=t.Name,
                measures=[NS(name=m.Name, expression=m.Expression) for m in t.Measures],
                columns=[NS(name=c.Name) for c in t.Columns],
            )
            for t in tom.Tables
        ],
        relationships=[],
    )


@pytest.fixture(autouse=True)
def journal_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_CODEMOD_JOURNAL_DIR", str(tmp_path))
    return tmp_path


def no_visuals(table, measure):
    return {"checked": True, "visuals": [], "files": []}


def preview_apply(m, transform, args, persister, **kw):
    pv = tx.preview(m, transform, args, kw.get("identity"))
    return pv, tx.apply(m, transform, args, pv["plan_hash"], persister=persister, **kw)


# ---------------------------------------------------------------------------
# E-05 inline_measure
# ---------------------------------------------------------------------------


def _inline(m, ref="Sales[Total Sales]", **extra):
    out = InlineMeasure().preview(m, {"measure_ref": ref, **extra})
    return {e.target: e.after for e in out.edits}, out


def test_inline_wraps_row_context_site_in_calculate():
    m = model(("Total Sales", "SUM(Sales[Amount])"), ("PerCust", "SUMX(Customer, [Total Sales])"))
    edits, _ = _inline(m)
    assert edits["measure:Sales[PerCust]"] == "SUMX(Customer, CALCULATE(\nSUM(Sales[Amount])\n))"


def test_inline_filter_argument_site_is_wrapped_too():
    m = model(
        ("Total Sales", "SUM(Sales[Amount])"), ("F", "CALCULATE([X], Sales[Amt] > [Total Sales])")
    )
    edits, _ = _inline(m)
    assert "CALCULATE(\nSUM(Sales[Amount])\n)" in edits["measure:Sales[F]"]


def test_inline_trailing_comment_cannot_swallow_close_paren():
    m = model(
        ("Total Sales", "SUM(Sales[Amount]) // gross"), ("PerCust", "SUMX(Customer, [Total Sales])")
    )
    edits, _ = _inline(m)
    after = edits["measure:Sales[PerCust]"]
    assert dax_text.sanity_check(after, "SUMX(Customer, [Total Sales])") is None
    assert after.endswith("// gross\n))")


def test_inline_matches_case_insensitive_qualified_and_escaped_names():
    m = model(
        ("Total Sales", "SUM(Sales[Amount])"),
        ("Q", "Sales[Total Sales] * 2"),
        ("L", "[total sales] + 1"),
        ("QQ", "'Sales'[TOTAL SALES] - 1"),
        ("Other", "Product[Total Sales]"),  # a column of another table
    )
    edits, _ = _inline(m)
    assert edits["measure:Sales[Q]"] == "(\nSUM(Sales[Amount])\n) * 2"
    assert edits["measure:Sales[L]"] == "(\nSUM(Sales[Amount])\n) + 1"
    assert edits["measure:Sales[QQ]"].startswith("(\nSUM(Sales[Amount])\n)")
    assert "measure:Sales[Other]" not in edits

    m2 = model(("A]B", "1"), ("C", "[A]]B] + 1"))
    edits2, _ = _inline(m2, ref="Sales[A]]B]")
    assert edits2["measure:Sales[C]"] == "(\n1\n) + 1"


def test_inline_backslash_string_is_not_an_escape():
    m = model(("Total Sales", "SUM(Sales[Amount])"), ("S", '"C:\\" & [Total Sales] & "x"'))
    edits, _ = _inline(m)
    assert edits["measure:Sales[S]"] == '"C:\\" & (\nSUM(Sales[Amount])\n) & "x"'


def test_inline_skips_caller_with_clashing_var_names():
    m = model(("T1", "VAR x = 1 RETURN x"), ("C", "VAR x = 2 RETURN x + [T1]"))
    edits, pr = _inline(m, ref="Sales[T1]")
    assert edits == {}
    assert pr.skipped == ["measure:Sales[C]"]


def test_inline_delete_after_defaults_to_false():
    m = model(("Base", "1"), ("D", "[Base] * 2"))
    res = InlineMeasure().apply(
        m,
        {"measure_ref": "Sales[Base]"},
        InlineMeasure().preview(m, {"measure_ref": "Sales[Base]"}),
    )
    assert "delete" not in res


# ---------------------------------------------------------------------------
# E-06 delete_after guards
# ---------------------------------------------------------------------------


def test_delete_skipped_when_a_rewrite_fails():
    m = model(("Base", "1"), ("A", "[Base] + 1"), ("B", "[Base] + 2"))
    p = Persister(fail={"B"})
    _pv, out = preview_apply(
        m,
        "inline_measure",
        {"measure_ref": "Sales[Base]", "delete_after": True},
        p,
        visual_checker=no_visuals,
    )
    assert out["success"] is False
    assert p.deletes == []
    assert out["delete_skipped"]


def test_delete_skipped_without_report_scanner_or_when_visual_binds():
    m = model(("Base", "1"), ("A", "[Base] + 1"))
    args = {"measure_ref": "Sales[Base]", "delete_after": True}
    p = Persister()
    _pv, out = preview_apply(m, "inline_measure", args, p)
    assert p.deletes == [] and "unverifiable" in out["delete_skipped"]["reason"]

    p2 = Persister()
    _pv, out2 = preview_apply(
        m,
        "inline_measure",
        args,
        p2,
        visual_checker=lambda t, n: {
            "checked": True,
            "visuals": ["R:p/v"],
            "files": ["x/visual.json"],
        },
    )
    assert p2.deletes == [] and out2["affected_visuals"] == ["R:p/v"]


def test_delete_refused_while_calc_column_still_references_measure():
    tom = TomModel(
        [
            TomTable(
                "Sales",
                measures=[TomMeasure("Base", "1"), TomMeasure("A", "[Base] + 1")],
                columns=[TomColumn("Flag", "IF(Sales[Base] > 0, 1)")],
            )
        ]
    )
    facade = tom_to_facade(tom)
    p = AtomicPersister(tom)
    _pv, out = preview_apply(
        facade,
        "inline_measure",
        {"measure_ref": "Sales[Base]", "delete_after": True},
        p,
        visual_checker=no_visuals,
    )
    assert out["committed"] is True  # the rewrite itself is fine
    assert [m.Name for m in tom.Tables[0].Measures] == ["Base", "A"]  # not deleted
    refused = out["delete_skipped"]
    assert refused["referenced_by"][0]["kind"] == "calculated_column"


def test_delete_happens_when_nothing_references_it_and_snapshot_is_journaled():
    base = TomMeasure("Base", "1")
    base.FormatString = "0.00"
    base.DisplayFolder = "KPIs"
    tom = TomModel([TomTable("Sales", measures=[base, TomMeasure("A", "[Base] + 1")])])
    p = AtomicPersister(tom)
    _pv, out = preview_apply(
        tom_to_facade(tom),
        "inline_measure",
        {"measure_ref": "Sales[Base]", "delete_after": True},
        p,
        visual_checker=no_visuals,
    )
    assert out["success"] is True, out
    assert [m.Name for m in tom.Tables[0].Measures] == ["A"]
    assert tom.saves == 1  # ONE SaveChanges for rewrite + delete (E-14)
    journal = json.loads(open(out["journal_path"], encoding="utf-8").read())
    snap = journal["deleted"][0]["snapshot"]
    assert snap["format_string"] == "0.00" and snap["display_folder"] == "KPIs"


def test_legacy_delete_returning_success_false_is_an_error():
    class P(Persister):
        def delete_measure(self, **kw):
            return {"success": False, "error": "locked"}

    m = model(("Base", "1"), ("A", "[Base] + 1"))
    _pv, out = preview_apply(
        m,
        "inline_measure",
        {"measure_ref": "Sales[Base]", "delete_after": True},
        P(),
        visual_checker=no_visuals,
    )
    assert out["success"] is False
    assert out["deleted"] == []


# ---------------------------------------------------------------------------
# E-10 transforms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src, expected",
    [
        ("SUM(a) / SUM(b) // ratio", "DIVIDE(SUM(a), SUM(b)) // ratio"),
        ("[x] / [y] && [z]", None),
        ("[x] / [y] || [z]", None),
        ("[x] / [y] == [z]", None),
        ("[x] / [y] IN {1}", None),
        ("VAR a = 1 RETURN a / 2", None),
        ("[x] // c\n/ [y]", None),
    ],
)
def test_use_divide_guards(src, expected):
    assert UseDivide._rewrite(src) == expected


def test_use_divide_warns_it_is_a_semantic_change():
    out = UseDivide().preview(model(("M", "[a] / [b]")), {})
    assert any("SEMANTIC" in w for w in out.warnings)


def _ruv(expr):
    return RemoveUnusedVariables._rewrite(expr)


def test_remove_unused_last_var_drops_return():
    assert _ruv("VAR x = 1 RETURN 5") == "5"


def test_remove_unused_respects_nested_blocks():
    out = _ruv("VAR a = CALCULATE(VAR inner = 1 RETURN inner) RETURN 5")
    assert out == "5"
    out2 = _ruv("VAR a = CALCULATE(VAR inner = 1 VAR dead = 2 RETURN inner) RETURN a")
    assert out2 is not None and "dead" not in out2 and "RETURN inner" in out2
    assert dax_text.sanity_check(out2) is None


def test_remove_unused_collapses_long_chains_without_a_cap():
    n = 121
    lines = ["VAR v0 = 1"] + [f"VAR v{i} = v{i - 1} + 1" for i in range(1, n)]
    expr = "\n".join(lines) + "\nRETURN 42"
    assert _ruv(expr) == "42"


def test_remove_unused_refuses_ambiguous_bare_nested_var():
    assert _ruv("VAR a = VAR b = 1 RETURN b RETURN 5") is None


def test_swap_rewrites_bare_table_refs_and_keeps_measure_refs():
    m = model(
        ("Total Sales", "SUMX(Sales, Sales[Amount])"),
        ("B", "sales[Total Sales] + COUNTROWS('Sales') + SUM(Sales[Qty])"),
    )
    out = SwapTableReference().preview(m, {"from_table": "Sales", "to_table": "Sales2"})
    edits = {e.target: e.after for e in out.edits}
    assert edits["measure:Sales[Total Sales]"] == "SUMX(Sales2, Sales2[Amount])"
    assert edits["measure:Sales[B]"] == "sales[Total Sales] + COUNTROWS(Sales2) + SUM(Sales2[Qty])"
    assert any("measure reference" in w for w in out.warnings)


def test_extract_leaves_strings_and_comments_alone():
    m = model(("A", 'SUM(Sales[X]) * 2 & "SUM(Sales[X])" // SUM(Sales[X])'))
    out = ExtractMeasure().preview(
        m,
        {
            "expression": "sum(sales[x])",
            "new_name": "XT",
            "target_table": "Sales",
            "auto_detect": True,
        },
    )
    edits = {e.target: e.after for e in out.edits}
    assert edits["measure:Sales[A]"] == '[XT] * 2 & "SUM(Sales[X])" // SUM(Sales[X])'


def test_extract_refuses_row_context_filter_args_and_partial_operands():
    m = model(
        ("A", "SUMX(Customer, SUM(Sales[X]))"),
        ("B", "CALCULATE([M], FILTER(ALL(Sales), SUM(Sales[X]) > 1))"),
        ("C", "SUM(Sales[X]) + SUM(Sales[Y]) * 2"),
        ("D", "IF(SUM(Sales[X]) + SUM(Sales[Y]), 1)"),
    )
    out = ExtractMeasure().preview(
        m,
        {
            "expression": "SUM(Sales[X]) + SUM(Sales[Y])",
            "new_name": "XY",
            "target_table": "Sales",
            "auto_detect": True,
        },
    )
    edits = {e.target: e.after for e in out.edits}
    assert "measure:Sales[C]" not in edits  # a + b * c is not (a + b) * c
    assert edits["measure:Sales[D]"] == "IF([XY], 1)"
    out2 = ExtractMeasure().preview(
        m,
        {
            "expression": "SUM(Sales[X])",
            "new_name": "XT",
            "target_table": "Sales",
            "auto_detect": True,
        },
    )
    edits2 = {e.target: e.after for e in out2.edits}
    assert "measure:Sales[A]" not in edits2 and "measure:Sales[B]" not in edits2
    assert any("left in place" in w for w in out2.warnings)


def test_extract_refuses_naked_column_body():
    m = model(("A", "SUMX(Sales, Sales[Qty]*Sales[Price])"))
    out = ExtractMeasure().preview(
        m,
        {
            "expression": "Sales[Qty]*Sales[Price]",
            "new_name": "Line",
            "target_table": "Sales",
            "auto_detect": True,
        },
    )
    assert out.edits == []
    assert "naked" in " ".join(out.warnings) or "not inside an aggregation" in " ".join(
        out.warnings
    )


# ---------------------------------------------------------------------------
# E-11 / K-04 extract collisions + rollback of created measures
# ---------------------------------------------------------------------------


def test_extract_refuses_existing_measure_column_or_missing_table():
    m = model(("Margin", "1"), ("A", "SUM(Sales[X])"), columns=("Amount",))
    for name, table in (("margin", "Sales"), ("Amount", "Sales"), ("New", "Nope")):
        out = ExtractMeasure().preview(
            m,
            {
                "expression": "SUM(Sales[X])",
                "new_name": name,
                "target_table": table,
                "auto_detect": True,
            },
        )
        assert out.edits == [], (name, table)


def test_extract_apply_then_rollback_deletes_the_created_measure():
    tom = TomModel([TomTable("Sales", measures=[TomMeasure("A", "SUM(Sales[X]) * 2")])])
    p = AtomicPersister(tom)
    args = {
        "expression": "SUM(Sales[X])",
        "new_name": "XT",
        "target_table": "Sales",
        "auto_detect": True,
    }
    _pv, out = preview_apply(tom_to_facade(tom), "extract_measure", args, p, identity="db")
    assert out["success"] is True, out
    assert {m.Name for m in tom.Tables[0].Measures} == {"A", "XT"}

    rb = tx.rollback(out["journal_path"], persister=p, identity="db")
    assert rb["success"] is True, rb
    assert [m.Name for m in tom.Tables[0].Measures] == ["A"]
    assert tom.Tables[0].Measures[0].Expression == "SUM(Sales[X]) * 2"


def test_create_only_refuses_existing_measure_at_tom_level():
    tom = TomModel([TomTable("Sales", measures=[TomMeasure("XT", "old")])])
    res = apply_changes_to_model(
        tom,
        [{"table_name": "Sales", "measure_name": "xt", "dax_expression": "1", "create_only": True}],
        measure_factory=TomMeasure,
    )
    assert res["committed"] is False and res["conflicts"]
    assert tom.Tables[0].Measures[0].Expression == "old" and tom.saves == 0


# ---------------------------------------------------------------------------
# E-12 plan hash
# ---------------------------------------------------------------------------


def test_plan_hash_covers_args_transform_and_identity():
    m = model(("Base", "1"), ("A", "[Base] + 1"))
    pv = tx.preview(
        m, "inline_measure", {"measure_ref": "Sales[Base]", "delete_after": False}, "db1"
    )
    p = Persister()
    out = tx.apply(
        m,
        "inline_measure",
        {"measure_ref": "Sales[Base]"},
        pv["plan_hash"],
        persister=p,
        identity="db1",
    )
    assert out["error_type"] == "plan_stale" and p.upserts == []
    out = tx.apply(
        m,
        "inline_measure",
        {"measure_ref": "Sales[Base]", "delete_after": False},
        pv["plan_hash"],
        persister=p,
        identity="db2",
    )
    assert out["error_type"] == "plan_stale"


# ---------------------------------------------------------------------------
# E-13 rollback safety + write-ahead journal
# ---------------------------------------------------------------------------


def test_journal_is_written_before_the_first_write(journal_dir):
    seen = {}

    class P(Persister):
        def upsert_measure(self, **kw):
            files = list(journal_dir.glob("codemod-*.json"))
            seen["status"] = (
                json.loads(files[0].read_text(encoding="utf-8"))["status"] if files else None
            )
            return super().upsert_measure(**kw)

    m = model(("Base", "1"), ("A", "[Base] + 1"))
    preview_apply(m, "inline_measure", {"measure_ref": "Sales[Base]"}, P())
    assert seen["status"] == "pending"


def test_rollback_refuses_foreign_paths_and_other_databases(tmp_path_factory, journal_dir):
    other = tmp_path_factory.mktemp("elsewhere") / "codemod-1.json"
    other.write_text(json.dumps({"edits": []}), encoding="utf-8")
    assert tx.rollback(str(other), persister=Persister())["error_type"] == "invalid_args"

    j = journal_dir / "codemod-2.json"
    j.write_text(
        json.dumps(
            {
                "identity": {"database": "A"},
                "edits": [{"target": "measure:Sales[X]", "before": "1", "after": "2", "note": ""}],
            }
        ),
        encoding="utf-8",
    )
    assert (
        tx.rollback(str(j), persister=Persister(), identity="B")["error_type"]
        == "identity_mismatch"
    )

    legacy = journal_dir / "codemod-3.json"
    legacy.write_text(
        json.dumps(
            {"edits": [{"target": "measure:Sales[X]", "before": "1", "after": "2", "note": ""}]}
        ),
        encoding="utf-8",
    )
    assert tx.rollback(str(legacy), persister=Persister())["error_type"] == "identity_unverified"


def test_rollback_reports_failures_and_detects_later_edits():
    p = Persister(fail={"A"})
    m = model(("Base", "1"), ("A", "[Base] + 1"))
    _pv, out = preview_apply(
        m, "inline_measure", {"measure_ref": "Sales[Base]"}, Persister(), identity="db"
    )
    rb = tx.rollback(out["journal_path"], persister=p, identity="db")
    assert rb["success"] is False and rb["errors"]

    tom = TomModel(
        [TomTable("Sales", measures=[TomMeasure("Base", "1"), TomMeasure("A", "[Base] + 1")])]
    )
    ap = AtomicPersister(tom)
    _pv, out = preview_apply(
        tom_to_facade(tom), "inline_measure", {"measure_ref": "Sales[Base]"}, ap, identity="db"
    )
    assert out["success"] is True
    tom.Tables[0].Measures[1].Expression = "user edit"
    rb = tx.rollback(out["journal_path"], persister=ap, identity="db")
    assert rb["success"] is False and rb["error_type"] == "rollback_conflict"
    assert tom.Tables[0].Measures[1].Expression == "user edit"
    rb = tx.rollback(out["journal_path"], persister=ap, identity="db", force=True)
    assert rb["success"] is True and tom.Tables[0].Measures[1].Expression == "[Base] + 1"
    again = tx.rollback(out["journal_path"], persister=ap, identity="db")
    assert again["success"] is False  # already rolled back


def test_list_journals_lists_newest_first(journal_dir):
    m = model(("Base", "1"), ("A", "[Base] + 1"))
    _pv, out = preview_apply(
        m, "inline_measure", {"measure_ref": "Sales[Base]"}, Persister(), identity="db"
    )
    listing = tx.list_journals()
    assert listing["journals"][0]["journal_path"] == out["journal_path"]
    assert listing["journals"][0]["database"] == "db"


# ---------------------------------------------------------------------------
# E-14 atomic write / staleness at TOM level / post-save compile check
# ---------------------------------------------------------------------------


def test_tom_conflict_aborts_the_whole_plan():
    tom = TomModel([TomTable("Sales", measures=[TomMeasure("A", "1"), TomMeasure("B", "2")])])
    res = apply_changes_to_model(
        tom,
        [
            {
                "table_name": "Sales",
                "measure_name": "A",
                "dax_expression": "10",
                "expected_expression": "1",
            },
            {
                "table_name": "Sales",
                "measure_name": "B",
                "dax_expression": "20",
                "expected_expression": "stale",
            },
        ],
        measure_factory=TomMeasure,
    )
    assert res["committed"] is False and res["error_type"] == "plan_stale"
    assert [m.Expression for m in tom.Tables[0].Measures] == ["1", "2"]
    assert tom.saves == 0 and tom.undos == 1


def test_measure_broken_by_the_save_reverts_everything():
    tom = TomModel([TomTable("Sales", measures=[TomMeasure("A", "1"), TomMeasure("B", "2")])])
    tom.break_on_save = "BROKEN"
    res = apply_changes_to_model(
        tom,
        [
            {
                "table_name": "Sales",
                "measure_name": "A",
                "dax_expression": "10",
                "expected_expression": "1",
            },
            {
                "table_name": "Sales",
                "measure_name": "B",
                "dax_expression": "BROKEN(",
                "expected_expression": "2",
            },
        ],
        measure_factory=TomMeasure,
    )
    assert res["committed"] is False and res["error_type"] == "invalid_dax" and res["reverted"]
    assert [m.Expression for m in tom.Tables[0].Measures] == ["1", "2"]


def test_apply_uses_one_atomic_call():
    tom = TomModel(
        [TomTable("Sales", measures=[TomMeasure("M1", "[a] / [b]"), TomMeasure("M2", "[c] / [d]")])]
    )
    p = AtomicPersister(tom)
    _pv, out = preview_apply(tom_to_facade(tom), "use_divide", {}, p)
    assert out["success"] is True and len(p.calls) == 1 and tom.saves == 1


# ---------------------------------------------------------------------------
# E-22 scope / preview shape / not persistable
# ---------------------------------------------------------------------------


def test_scope_filter_limits_every_transform():
    m = model(("M1", "[a] / [b]"), ("M2", "[c] / [d]"))
    out = tx.preview(m, "use_divide", {"measures": ["Sales[M2]"]})
    assert [e["target"] for e in out["edits"]] == ["measure:Sales[M2]"]
    out = tx.preview(m, "use_divide", {"measures": ["m1"], "tables": ["sales"]})
    assert [e["target"] for e in out["edits"]] == ["measure:Sales[M1]"]


def test_preview_returns_full_bodies_and_diff():
    long_expr = "[a] / (" + " + ".join(f"[x{i}]" for i in range(80)) + ")"
    out = tx.preview(model(("M", long_expr)), "use_divide", {})
    e = out["edits"][0]
    assert e["before"] == long_expr and len(e["after"]) > 200 and e["diff"].startswith("---")


def test_move_batch_preview_is_flagged_not_persistable():
    m = model(("M", "1"))
    out = tx.preview(
        m, "move_measure_batch", {"moves": [{"measure_ref": "Sales[M]", "target_table": "X"}]}
    )
    assert out["persistable"] is False


def test_structurally_broken_rewrite_is_rejected_not_written():
    m = model(("M", "SUM(Sales[X])"))
    p = Persister()
    pv, out = preview_apply(m, "replace_pattern", {"pattern": r"\)$", "replacement": ""}, p)
    assert pv["edits"] == [] and pv["rejected_edits"][0]["target"] == "measure:Sales[M]"
    assert p.upserts == []


# ---------------------------------------------------------------------------
# E-27 before/after verification
# ---------------------------------------------------------------------------


def test_semantic_difference_rolls_the_apply_back():
    tom = TomModel(
        [TomTable("Sales", measures=[TomMeasure("Base", "1"), TomMeasure("A", "[Base] + 1")])]
    )
    p = AtomicPersister(tom)
    values = iter([{"Sales[A]": 2}, {"Sales[A]": 3}])
    _pv, out = preview_apply(
        tom_to_facade(tom),
        "inline_measure",
        {"measure_ref": "Sales[Base]"},
        p,
        evaluator=lambda refs: next(values),
        identity="db",
    )
    assert out["success"] is False and out["error_type"] == "semantic_change_detected"
    assert out["rolled_back"]["success"] is True
    assert tom.Tables[0].Measures[1].Expression == "[Base] + 1"


def test_equal_values_keep_the_apply():
    tom = TomModel(
        [TomTable("Sales", measures=[TomMeasure("Base", "1"), TomMeasure("A", "[Base] + 1")])]
    )
    p = AtomicPersister(tom)
    _pv, out = preview_apply(
        tom_to_facade(tom),
        "inline_measure",
        {"measure_ref": "Sales[Base]"},
        p,
        evaluator=lambda refs: {"Sales[A]": 2},
        identity="db",
    )
    assert out["success"] is True and out["verification"]["equivalent"] is True
    assert tom.Tables[0].Measures[1].Expression == "(\n1\n) + 1"
