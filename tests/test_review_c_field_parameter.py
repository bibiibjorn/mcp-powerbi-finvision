"""Review slice C — field parameters (C-07, C-08, C-09).

The manager runs against a fake ``Microsoft.AnalysisServices.Tabular``
namespace (plain Python classes with the TOM property names) and a fake model
that counts SaveChanges, so the tests pin the shape Desktop needs and the
number of saves — the two things that broke in the field (a half-shaped FP
table left behind by a two-save create; rows deleted by a regex parser).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests._c_fake_tom import Coll, FakeModel, FakeServer, Obj, table

# ---------------------------------------------------------------------------
# Fake TOM namespace
# ---------------------------------------------------------------------------


class _Named(Obj):
    def __init__(self):
        super().__init__(Name=None, Annotations=Coll(), ExtendedProperties=Coll())


class _Table(_Named):
    def __init__(self):
        super().__init__()
        self.Columns = Coll()
        self.Partitions = Coll()
        self.Measures = Coll()
        self.LineageTag = ""
        self.IsHidden = False
        self.refreshes = []

    def RequestRefresh(self, rtype):
        self.refreshes.append(rtype)


class _Column(_Named):
    def __init__(self):
        super().__init__()
        self.SourceColumn = None
        self.IsHidden = False
        self.IsNameInferred = True
        self.SortByColumn = None
        self.SummarizeBy = "Default"
        self.FormatString = ""
        self.RelatedColumnDetails = None
        self.LineageTag = ""


class _RCD(Obj):
    def __init__(self):
        super().__init__(GroupByColumns=Coll())


def _fake_tom():
    agg = SimpleNamespace(Sum="Sum")
    setattr(agg, "None", "None")
    return SimpleNamespace(
        Table=_Table,
        Partition=lambda: Obj(Name=None, Mode=None, Source=None),
        CalculatedPartitionSource=lambda: Obj(Expression=None),
        CalculatedTableColumn=_Column,
        RelatedColumnDetails=_RCD,
        GroupByColumn=lambda: Obj(GroupingColumn=None),
        JsonExtendedProperty=lambda: Obj(Name=None, Value=None),
        Annotation=lambda: Obj(Name=None, Value=None),
        ModeType=SimpleNamespace(Import="Import"),
        AggregateFunction=agg,
        RefreshType=SimpleNamespace(Calculate="Calculate"),
    )


@pytest.fixture
def fp(monkeypatch):
    import core.operations.field_parameter_manager as fpm
    from core.operations import model_readback

    state = {"model": FakeModel(table("Sales")), "server": FakeServer(), "connects": 0}

    def _connect(self):
        state["connects"] += 1
        return state["server"], None, state["model"]

    monkeypatch.setattr(fpm, "_tom", _fake_tom)
    monkeypatch.setattr(fpm, "AMO_AVAILABLE", True)
    monkeypatch.setattr(fpm.FieldParameterManager, "_get_server_db_model", _connect)
    monkeypatch.setattr(model_readback, "table_exists", lambda name: None)
    monkeypatch.setattr(model_readback, "column_exists", lambda t, c: None)
    return SimpleNamespace(mgr=fpm.FieldParameterManager(), state=state, fpm=fpm)


def _fp_table(model, name):
    return next(t for t in model.Tables if t.Name == name)


def _col(tbl, source):
    return next(c for c in tbl.Columns if c.SourceColumn == source)


# ---------------------------------------------------------------------------
# C-07 — ONE SaveChanges with the complete shape; failures roll back cleanly
# ---------------------------------------------------------------------------
def test_c07_create_is_one_save_with_the_complete_desktop_shape(fp):
    out = fp.mgr.create_field_parameter(
        "fp Metric",
        fields=[
            {"label": "Amount", "reference": "[Total Amount]"},
            {"label": "Margin", "reference": "'Sales'[Margin %]"},
        ],
    )
    model = fp.state["model"]
    assert out["success"] is True, out
    assert model.saves == 1  # one transaction (was create + configure = 2 saves)
    tbl = _fp_table(model, "fp Metric")
    value, fields, order = (_col(tbl, f"[Value{i}]") for i in (1, 2, 3))
    assert (value.Name, fields.Name, order.Name) == (
        "fp Metric",
        "fp Metric Fields",
        "fp Metric Order",
    )
    assert all(c.IsNameInferred is False for c in (value, fields, order))
    assert fields.IsHidden and order.IsHidden and not value.IsHidden
    assert value.SortByColumn is order and fields.SortByColumn is order
    assert [g.GroupingColumn for g in value.RelatedColumnDetails.GroupByColumns] == [fields]
    assert fields.ExtendedProperties["ParameterMetadata"].Value == '{"version":3,"kind":2}'
    assert tbl.Annotations["PBI_Id"].Value
    assert tbl.refreshes == ["Calculate"]
    assert tbl.Partitions[0].Source.Expression.startswith("{")


def test_c07_save_failure_rolls_back_on_a_fresh_connection(fp, monkeypatch):
    from core.operations import model_readback

    model = fp.state["model"]
    model.save_error = RuntimeError("Unexpected column name 'ObjectID.BindingInfo'")
    left_behind = {"exists": True}
    monkeypatch.setattr(model_readback, "table_exists", lambda n: left_behind["exists"])
    out = fp.mgr.create_field_parameter("fp X", fields=[{"label": "A", "reference": "[A]"}])
    assert out["success"] is False and out["error_type"] == "creation_error"
    assert model.undos >= 1
    assert out["rollback"].get("fresh_connection") is True  # rollback on a NEW session
    assert fp.state["connects"] == 2


def test_c07_client_parse_error_after_commit_is_reported_as_applied(fp, monkeypatch):
    from core.operations import model_readback

    model = fp.state["model"]
    model.save_error = RuntimeError("Unexpected column name 'ObjectID.BindingInfo'")
    monkeypatch.setattr(model_readback, "table_exists", lambda n: True)
    monkeypatch.setattr(model_readback, "column_exists", lambda t, c: True)
    out = fp.mgr.create_field_parameter("fp Y", fields=[{"label": "A", "reference": "[A]"}])
    assert out["success"] is True and out.get("applied") is True
    assert "post_write_warning" in out


# ---------------------------------------------------------------------------
# C-08 — the tuple tokenizer understands every row; partial parses never write
# ---------------------------------------------------------------------------
_PROBE = "\n".join(
    [
        "{",
        "    (\"Revenue (EUR)\", NAMEOF('Sales'[Revenue (EUR)]), 0),",
        '    ("Margin", NAMEOF([Margin]), 1),',
        '    ("Flagged", NAMEOF([Flag]), 2, TRUE()),',
        '    ("lower", nameof([Lower]), 3),',
        '    ("Say ""hi""", NAMEOF(\'O\'\'Brien\'[x]]y]), 4)',
        "}",
    ]
)


def test_c08_parser_keeps_every_row_and_round_trips():
    from core.operations.field_parameter_manager import _serialize_fp_rows, parse_fp_rows

    rows, count = parse_fp_rows(_PROBE)
    assert count == 5 and len(rows) == 5
    assert [r["label"] for r in rows] == ["Revenue (EUR)", "Margin", "Flagged", "lower", 'Say "hi"']
    assert rows[0]["nameof"] == "NAMEOF('Sales'[Revenue (EUR)])"
    assert rows[2]["extras"] == ["TRUE()"]
    again, again_count = parse_fp_rows(_serialize_fp_rows(rows))
    assert again == rows and again_count == 5


def _fp_with_partition(fp, expr):
    part = Obj(Name="p", Source=Obj(Expression=expr))
    tbl = table("fp Metric", partitions=[part])
    tbl.RequestRefresh = lambda rtype: None
    fp.state["model"] = FakeModel(tbl)
    return part


def test_c08_unparseable_row_refuses_to_rewrite(fp):
    part = _fp_with_partition(
        fp, '{\n    ("A", NAMEOF([A]), 0),\n    ("B", SELECTEDVALUE(x[y]), 1)\n}'
    )
    before = part.Source.Expression
    out = fp.mgr.add_field("fp Metric", {"label": "C", "reference": "[C]"})
    assert out["success"] is False and out["error_type"] == "fp_parse_incomplete"
    assert part.Source.Expression == before and fp.state["model"].saves == 0


def test_c08_add_field_keeps_the_tuple_arity(fp):
    part = _fp_with_partition(fp, '{\n    ("A", NAMEOF([A]), 0, "cat")\n}')
    out = fp.mgr.add_field("fp Metric", {"label": "B", "reference": "[B]"})
    assert out["success"] is True
    assert '("B", NAMEOF([B]), 1, BLANK())' in part.Source.Expression
    assert fp.state["model"].saves == 1


# ---------------------------------------------------------------------------
# C-09 — repair a half-built FP idempotently (columns found by SourceColumn)
# ---------------------------------------------------------------------------
def _half_built(fp):
    tbl = _Table()
    tbl.Name = "fp Half"
    for i in (1, 2, 3):
        c = _Column()
        c.Name = f"Value{i}"
        c.SourceColumn = f"[Value{i}]"
        tbl.Columns.Add(c)
    tbl.Partitions.Add(Obj(Name="p", Source=Obj(Expression='{\n    ("A", NAMEOF([A]), 0)\n}')))
    ann = Obj(Name="PBI_Id", Value="keep-me")
    tbl.Annotations.Add(ann)
    fp.state["model"] = FakeModel(tbl)
    return tbl


def test_c09_repair_finishes_a_half_built_field_parameter(fp):
    tbl = _half_built(fp)
    out = fp.mgr.repair_field_parameter("fp Half")
    assert out["success"] is True and out["action"] == "repaired", out
    value, fields, order = (_col(tbl, f"[Value{i}]") for i in (1, 2, 3))
    assert (value.Name, fields.Name, order.Name) == ("fp Half", "fp Half Fields", "fp Half Order")
    assert fields.IsHidden and order.IsHidden and value.SortByColumn is order
    assert [g.GroupingColumn for g in value.RelatedColumnDetails.GroupByColumns] == [fields]
    assert fields.ExtendedProperties["ParameterMetadata"]
    assert tbl.Annotations["PBI_Id"].Value == "keep-me"  # PBI_Id is stable
    assert fp.state["model"].saves == 1


def test_c09_repair_is_idempotent_and_old_name_still_works(fp):
    _half_built(fp)
    fp.mgr.repair_field_parameter("fp Half")
    again = fp.mgr.set_parameter_metadata("fp Half")  # legacy op name
    assert again["success"] is True and again["action"] == "noop"
    assert fp.state["model"].saves == 1


def test_c09_data_driven_expression_is_accepted(fp):
    out = fp.mgr.create_field_parameter(
        "fp Dyn", expression='SELECTCOLUMNS(Metrics, "L", Metrics[Label], "F", Metrics[F], "O", 1)'
    )
    assert out["success"] is True and out.get("notes")
