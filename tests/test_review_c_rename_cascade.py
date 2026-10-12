"""Review slice C — rename cascades (C-01, C-04, C-06, C-40, C-41).

Pure fake-TOM tests: PascalCase objects shaped like the real TOM model
(``Tables[].Measures[].Expression``, ``FormatStringDefinition.Expression``,
``Partitions[].Source.Expression`` + ``SourceType``, ``CalculationGroup``,
``Roles[].TablePermissions[].FilterExpression``). Every case is a probe from
``probes/C/probe_rename_cascade.py`` that corrupted DAX / M on the old regex
cascade.
"""

from __future__ import annotations

import pytest

from core.operations.rename_cascade import (
    cascade_column_rename,
    cascade_measure_rename,
    cascade_table_rename,
)


class Coll(list):
    @property
    def Count(self):
        return len(self)

    def Add(self, x):
        self.append(x)

    def Remove(self, x):
        list.remove(self, x)


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def measure(name, expr, fsd=None, kpi=None):
    return Obj(
        Name=name,
        Expression=expr,
        FormatStringDefinition=Obj(Expression=fsd) if fsd else None,
        DetailRowsDefinition=None,
        KPI=kpi,
    )


def table(name, measures=(), columns=(), partitions=(), calc_group=None):
    return Obj(
        Name=name,
        Measures=Coll(measures),
        Columns=Coll(columns),
        Partitions=Coll(partitions),
        CalculationGroup=calc_group,
    )


def column(name, expr=None):
    return Obj(Name=name, Expression=expr)


def model(*tables, roles=()):
    return Obj(Tables=Coll(tables), Roles=Coll(roles), Cultures=Coll())


def calc_partition(expr):
    return Obj(SourceType="PartitionSourceType.Calculated", Source=Obj(Expression=expr))


def m_partition(expr):
    return Obj(SourceType="PartitionSourceType.M", Source=Obj(Expression=expr))


# ---------------------------------------------------------------------------
# C-01 — table rename: quoting, bare refs, literals, every DAX site
# ---------------------------------------------------------------------------
def test_c01_table_rename_quotes_new_name_and_cascades_bare_refs():
    m1 = measure("Fmt", "SELECTEDVALUE(s_Decimal2[Nr], 2)", fsd='REPT("0", s_Decimal2[Nr])')
    m2 = measure("CountRows", "COUNTROWS(s_Decimal2)")
    m3 = measure("AllT", "CALCULATE([Fmt], ALL(s_Decimal2))")
    m4 = measure("Lit", '"label s_Decimal2[Nr] in a string" // s_Decimal2[Nr]')
    mod = model(table("s_Decimal2", columns=[column("Nr")]), table("M", measures=[m1, m2, m3, m4]))
    cascade_table_rename(mod, "s_Decimal2", "s Decimal")
    assert m1.Expression == "SELECTEDVALUE('s Decimal'[Nr], 2)"
    assert m1.FormatStringDefinition.Expression == "REPT(\"0\", 's Decimal'[Nr])"
    assert m2.Expression == "COUNTROWS('s Decimal')"
    assert m3.Expression == "CALCULATE([Fmt], ALL('s Decimal'))"
    # String literals and comments are never rewritten.
    assert m4.Expression == '"label s_Decimal2[Nr] in a string" // s_Decimal2[Nr]'


def test_c01_table_rename_escapes_an_apostrophe_in_the_new_name():
    m5 = measure("X", "SUM('Sales'[Amount])")
    mod = model(table("Sales"), table("M", measures=[m5]))
    cascade_table_rename(mod, "Sales", "O'Brien Sales")
    assert m5.Expression == "SUM('O''Brien Sales'[Amount])"


def test_c01_table_rename_reaches_calc_items_rls_kpi_and_calc_tables():
    kpi = Obj(TargetExpression="SUM(Old[Amt])", StatusExpression=None, TrendExpression=None)
    item = Obj(
        Name="PY", Expression="CALCULATE(SELECTEDMEASURE(), ALL(Old))", FormatStringDefinition=None
    )
    cg = Obj(
        CalculationItems=Coll([item]),
        MultipleOrEmptySelectionExpression=None,
        NoSelectionExpression=None,
    )
    tp = Obj(Table=Obj(Name="Old"), FilterExpression='Old[Region] = "EU"')
    role = Obj(Name="EU", TablePermissions=Coll([tp]))
    calc_col = column("Twice", "Old[Amt] * 2")
    mod = model(
        table(
            "Old",
            columns=[column("Amt"), column("Region"), calc_col],
            measures=[measure("K", "1", kpi=kpi)],
        ),
        table("Time", calc_group=cg),
        table("Copy", partitions=[calc_partition("FILTER(Old, Old[Amt] > 0)")]),
        roles=[role],
    )
    record = []
    updated = cascade_table_rename(mod, "Old", "New Name", record=record)
    assert kpi.TargetExpression == "SUM('New Name'[Amt])"
    assert item.Expression == "CALCULATE(SELECTEDMEASURE(), ALL('New Name'))"
    assert tp.FilterExpression == "'New Name'[Region] = \"EU\""
    assert calc_col.Expression == "'New Name'[Amt] * 2"
    assert mod.Tables[2].Partitions[0].Source.Expression == (
        "FILTER('New Name', 'New Name'[Amt] > 0)"
    )
    kinds = {r["kind"] for r in record}
    assert {"kpi", "calc_item", "rls", "calc_column", "calc_partition"} <= kinds
    assert len(updated) == 5


# ---------------------------------------------------------------------------
# C-06 — column rename never matches a longer table name
# ---------------------------------------------------------------------------
def test_c06_column_rename_leaves_other_tables_with_the_same_suffix_alone():
    other = measure("Other", "SUM(MySales[Amount]) + SUM(FactSales[Amount])")
    mine = measure("Mine", "SUM(Sales[Amount]) + SUM('Sales'[Amount])")
    mod = model(
        table("Sales", columns=[column("Amount")]),
        table("MySales", columns=[column("Amount")]),
        table("FactSales", columns=[column("Amount")]),
        table("M", measures=[other, mine]),
    )
    cascade_column_rename(mod, "Sales", "Amount", "Net Amount")
    assert mine.Expression == "SUM(Sales[Net Amount]) + SUM('Sales'[Net Amount])"
    assert other.Expression == "SUM(MySales[Amount]) + SUM(FactSales[Amount])"


# ---------------------------------------------------------------------------
# C-04 — measure rename: never Power Query M, ]] escaping, literal backslash
# ---------------------------------------------------------------------------
def test_c04_measure_rename_never_touches_power_query_m_partitions():
    m_src = (
        'let Source = Excel.Workbook(File.Contents("x.xlsx")), '
        'Add = Table.AddColumn(Source, "V2", each [Value] * 2) in Add'
    )
    imp = table("Sales Import", partitions=[m_partition(m_src)])
    calc = table("Calc", partitions=[calc_partition('ROW("v", [Value])')])
    mv = measure("Value", "1")
    mod = model(imp, calc, table("M", measures=[mv]))
    mv.Name = "Value EUR"
    updated = cascade_measure_rename(mod, "Value", "Value EUR", table_name="M")
    assert imp.Partitions[0].Source.Expression == m_src
    assert calc.Partitions[0].Source.Expression == 'ROW("v", [Value EUR])'
    assert updated == ["'Calc' (calc table / field parameter)"]


@pytest.mark.parametrize(
    "old, new, before, after",
    [
        ("Sales [EUR]", "Revenue [EUR]", "[Sales [EUR]]] * 2", "[Revenue [EUR]]] * 2"),
        ("Sales", "Sales [EUR]", "[Sales] * 2", "[Sales [EUR]]] * 2"),
        ("Sales [EUR]", "Revenue", "[Sales [EUR]]] * 2", "[Revenue] * 2"),
    ],
)
def test_c04_measure_rename_escapes_closing_brackets(old, new, before, after):
    target = measure(old, "1")
    use = measure("Use", before)
    mod = model(table("T", measures=[target, use]))
    target.Name = new
    cascade_measure_rename(mod, old, new, table_name="T")
    assert use.Expression == after


def test_c04_backslash_in_new_name_is_literal():
    a = measure("A", "1")
    b = measure("B", "[A] + 1")
    mod = model(table("T", measures=[a, b]))
    a.Name = r"A\1 B"
    cascade_measure_rename(mod, "A", r"A\1 B", table_name="T")
    assert b.Expression == r"[A\1 B] + 1"


# ---------------------------------------------------------------------------
# C-41 — preview: records every site, mutates nothing, hash tracks the diff
# ---------------------------------------------------------------------------
def test_c41_preview_records_the_diff_without_mutating():
    from core.operations import rename_flow

    use = measure("Use", "COUNTROWS(Old)")
    mod = model(table("Old"), table("M", measures=[use]))
    record = []
    updated = cascade_table_rename(mod, "Old", "New", record=record, apply=False)
    assert use.Expression == "COUNTROWS(Old)"  # untouched
    assert updated and record[0]["before"] == "COUNTROWS(Old)"
    assert record[0]["after"] == "COUNTROWS('New')"
    payload = rename_flow.preview_payload("table", "Old", "New", record)
    assert payload["preview"] is True and payload["change_count"] == 1
    assert "_restore" not in payload["expression_changes"][0]  # JSON-safe

    # The hash is stable for the same diff and changes when the model changes.
    record2 = []
    cascade_table_rename(mod, "Old", "New", record=record2, apply=False)
    assert rename_flow.preview_hash(record2) == payload["preview_hash"]
    use.Expression = "COUNTROWS(Old) + 1"
    record3 = []
    cascade_table_rename(mod, "Old", "New", record=record3, apply=False)
    assert rename_flow.preview_hash(record3) != payload["preview_hash"]


# ---------------------------------------------------------------------------
# C-40 — verify after save; new errors roll the rename back exactly
# ---------------------------------------------------------------------------
class _SavingModel:
    def __init__(self, inner):
        self.inner = inner
        self.saves = 0

    def SaveChanges(self):
        self.saves += 1


def test_c40_new_expression_errors_roll_the_rename_back(monkeypatch):
    from core.operations import model_readback, rename_flow

    use = measure("Use", "COUNTROWS(Old)")
    tbl = table("Old")
    mod = model(tbl, table("M", measures=[use]))
    record = [rename_flow.name_record(tbl, "Old", "New", "table 'Old'")]
    tbl.Name = "New"
    cascade_table_rename(mod, "Old", "New", record=record)
    assert use.Expression == "COUNTROWS('New')"

    states = iter([{"measure:9": "measure M[Use]: cannot find table 'New'"}, {}])
    monkeypatch.setattr(model_readback, "expression_errors", lambda: next(states))
    saver = _SavingModel(mod)
    out = rename_flow.finish(saver, label="table/rename", baseline={}, record=record)
    assert out["success"] is False and out["error_type"] == "rename_introduced_errors"
    assert out["rolled_back"] is True and saver.saves == 2  # rename + revert
    assert tbl.Name == "Old" and use.Expression == "COUNTROWS(Old)"
    assert out["rollback"]["verified_clean"] is True


def test_c40_clean_rename_reports_verification_passed(monkeypatch):
    from core.operations import model_readback, rename_flow

    monkeypatch.setattr(model_readback, "expression_errors", lambda: {"x:1": "old error"})
    saver = _SavingModel(None)
    out = rename_flow.finish(
        saver, label="measure/rename", baseline={"x:1": "old error"}, record=[]
    )
    assert out.get("verification") == "passed" and saver.saves == 1


# ---------------------------------------------------------------------------
# C-41 / C-40 through the live manager: preview_hash staleness + verified apply
# ---------------------------------------------------------------------------
@pytest.fixture
def measure_mgr(monkeypatch):
    import core.operations.measure_crud_manager as mcm
    from core.operations import model_readback
    from tests._c_fake_tom import FakeModel, FakeServer
    from tests._c_fake_tom import measure as fmeasure
    from tests._c_fake_tom import table as ftable

    target = fmeasure("Rev", "1")
    user = fmeasure("Use", "[Rev] * 2")
    model = FakeModel(ftable("Sales", measures=[target, user]))
    monkeypatch.setattr(mcm, "AMO_AVAILABLE", True)
    mgr = mcm.MeasureCRUDManager(None)
    monkeypatch.setattr(mgr, "_get_server_db_model", lambda: (FakeServer(), None, model))
    monkeypatch.setattr(model_readback, "expression_errors", lambda: {})
    monkeypatch.setattr(model_readback, "udf_rows", lambda: [])
    monkeypatch.setattr(model_readback, "find_measure", lambda t, n: {"expression": "1"})
    return mgr, model, target, user


def test_c41_stale_preview_hash_refuses_the_rename(measure_mgr):
    mgr, model, target, user = measure_mgr
    pv = mgr.rename_measure("Sales", "Rev", "Revenue", preview=True)
    assert pv["preview"] is True and pv["change_count"] == 1 and model.saves == 0
    user.Expression = "[Rev] * 3"  # the model changes after the preview
    out = mgr.rename_measure("Sales", "Rev", "Revenue", preview_hash=pv["preview_hash"])
    assert out["success"] is False and out["error_type"] == "stale_preview"
    assert target.Name == "Rev" and model.saves == 0


def test_c41_matching_preview_hash_applies_and_verifies(measure_mgr):
    mgr, model, target, user = measure_mgr
    pv = mgr.rename_measure("Sales", "Rev", "Revenue", preview=True)
    out = mgr.rename_measure("Sales", "Rev", "Revenue", preview_hash=pv["preview_hash"])
    assert out["success"] is True and out.get("verification") == "passed"
    assert target.Name == "Revenue" and user.Expression == "[Revenue] * 2"
    assert model.saves == 1
