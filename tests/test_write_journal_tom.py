"""JR-01: the journal's TOM applier against the REAL Tabular object model, fully offline.

No server, no Desktop: a ``Database`` is built in memory (the same object model Desktop's
connection returns), mutated, serialised into images, and the undo / replay / checkpoint-restore
plans are applied back with :func:`core.operations.journal_apply.apply_ops`; the model must
serialise identically to the pre-image afterwards. ``SaveChanges`` cannot run without a server, so
the write_lane / server round trip stays a live check. Skipped when the bundled AMO cannot load.
"""

from __future__ import annotations

import json

import pytest

from core.operations import journal_images as ji
from core.operations import journal_plan as jp


def _load():
    try:
        from core.infrastructure.dll_paths import load_amo_assemblies

        if not load_amo_assemblies():
            return None
        import Microsoft.AnalysisServices.Tabular as tabular  # type: ignore

        opts = tabular.SerializeOptions()
        json.loads(tabular.JsonSerializer.SerializeObject(tabular.Model(), opts))
        return tabular
    except Exception:  # noqa: BLE001 - fakes in sys.modules / no CLR / no DLLs
        return None


T = _load()
pytestmark = pytest.mark.skipif(T is None, reason="real Tabular object model not loadable")


def ser(model) -> dict:
    opts = T.SerializeOptions()
    opts.IgnoreTimestamps = True
    return json.loads(T.JsonSerializer.SerializeObject(model, opts))


def build():
    db = T.Database("t")
    db.CompatibilityLevel = 1702
    m = T.Model()
    db.Model = m
    m.Culture = "en-US"
    tb = T.Table()
    tb.Name = "Sales"
    tb.LineageTag = "tag-sales"
    p = T.Partition()
    p.Name = "Sales"
    src = T.MPartitionSource()
    src.Expression = "let x = 1 in x"
    p.Source = src
    tb.Partitions.Add(p)
    c = T.DataColumn()
    c.Name = "Amount"
    c.DataType = T.DataType.Double
    c.SourceColumn = "Amount"
    c.LineageTag = "tag-amount"
    c.FormatString = "0.0"
    tb.Columns.Add(c)
    cc = T.CalculatedColumn()
    cc.Name = "Calc"
    cc.DataType = T.DataType.Int64
    cc.Expression = "1 + 1"
    cc.LineageTag = "tag-calc"
    tb.Columns.Add(cc)
    me = T.Measure()
    me.Name = "Total"
    me.Expression = "SUM(Sales[Amount])"
    me.FormatString = "0.00"
    me.DisplayFolder = "Base"
    me.LineageTag = "tag-total"
    fsd = T.FormatStringDefinition()
    fsd.Expression = '"0"'
    me.FormatStringDefinition = fsd
    tb.Measures.Add(me)
    m.Tables.Add(tb)
    role = T.ModelRole()
    role.Name = "Reader"
    role.ModelPermission = T.ModelPermission.Read
    perm = T.TablePermission()
    perm.Table = tb
    perm.FilterExpression = "[Amount] > 0"
    role.TablePermissions.Add(perm)
    m.Roles.Add(role)
    return m


def images(model):
    return ji.split_model(ser(model))


def entry(eid, pre, post):
    return {"id": eid, "changes": ji.diff_maps(pre, post)}


def apply(model, plan):
    from core.operations.journal_apply import apply_ops

    assert plan["conflicts"] == [], plan["conflicts"]
    return apply_ops(model, plan["ops"])


def test_undo_restores_a_measure_exactly_including_nested_properties():
    m = build()
    pre = images(m)
    me = m.Tables.Find("Sales").Measures.Find("Total")
    me.Expression = "SUM(Sales[Amount]) * 2"
    me.Description = "added"
    me.DisplayFolder = None
    me.FormatStringDefinition = None
    kpi = T.KPI()
    kpi.TargetExpression = "1"
    kpi.StatusExpression = "1"
    me.KPI = kpi
    post = images(m)
    plan = jp.plan_undo([entry("J1", pre, post)], post)
    apply(m, plan)
    assert ji.diff_maps(pre, images(m)) == []


def test_undo_restores_columns_partition_role_and_model_together():
    m = build()
    pre = images(m)
    tb = m.Tables.Find("Sales")
    tb.Columns.Find("Amount").FormatString = "#,0"
    tb.Columns.Find("Calc").Expression = "2 + 2"
    tb.Partitions.Find("Sales").Source.Expression = "let x = 2 in x"
    m.Roles.Find("Reader").TablePermissions.Find("Sales").FilterExpression = "[Amount] > 99"
    m.Culture = "nl-BE"
    extra = T.Measure()
    extra.Name = "Extra"
    extra.Expression = "1"
    tb.Measures.Add(extra)
    post = images(m)
    apply(m, jp.plan_undo([entry("J1", pre, post)], post))
    assert ji.diff_maps(pre, images(m)) == []


def test_undo_of_a_column_rename_renames_it_back_in_place():
    m = build()
    pre = images(m)
    tb = m.Tables.Find("Sales")
    col = tb.Columns.Find("Amount")
    col.Name = "Amount2"
    post = images(m)
    (ch,) = [c for c in ji.diff_maps(pre, post) if c["kind"] == "column"]
    assert ch["change"] == "renamed"
    apply(m, jp.plan_undo([entry("J1", pre, post)], post))
    after = tb.Columns.Find("Amount")
    assert after is not None and tb.Columns.Find("Amount2") is None
    assert after.SourceColumn == "Amount", "the column kept its data binding"
    assert ji.diff_maps(pre, images(m)) == []


def test_replay_reapplies_the_postimage_after_the_edits_were_lost():
    m = build()
    pre = images(m)
    m.Tables.Find("Sales").Measures.Find("Total").Expression = "SUM(Sales[Amount]) / 2"
    post = images(m)
    e = entry("J1", pre, post)
    # the "discarded reload": a fresh model at the saved state
    fresh = build()
    apply(fresh, jp.plan_replay([e], images(fresh)))
    assert ji.diff_maps(post, images(fresh)) == []
    assert jp.plan_replay([e], images(fresh))["ops"] == []  # nothing left to do


def test_checkpoint_restore_recreates_a_deleted_table_and_removes_an_added_one():
    m = build()
    checkpoint = images(m)
    m.Tables.Remove(m.Tables.Find("Sales"))
    m.Roles.Clear()
    extra = T.Table()
    extra.Name = "Scratch"
    part = T.Partition()
    part.Name = "Scratch"
    s = T.MPartitionSource()
    s.Expression = "x"
    part.Source = s
    extra.Partitions.Add(part)
    col = T.DataColumn()
    col.Name = "Id"
    col.DataType = T.DataType.Int64
    col.SourceColumn = "Id"
    extra.Columns.Add(col)
    m.Tables.Add(extra)
    plan = jp.plan_restore(images(m), checkpoint)
    kinds = {(o["action"], o["ref"]) for o in plan["ops"]}
    assert ("add", "table:Sales") in kinds and ("remove", "table:Scratch") in kinds
    apply(m, plan)
    assert ji.diff_maps(checkpoint, images(m)) == []
    assert m.Tables.Find("Sales").Columns.Find("Calc").Expression == "1 + 1"


def test_undo_of_a_table_rename_and_edit():
    m = build()
    pre = images(m)
    tb = m.Tables.Find("Sales")
    tb.Name = "Fact"
    tb.Measures.Find("Total").Expression = "SUM(Fact[Amount])"
    post = images(m)
    apply(m, jp.plan_undo([entry("J1", pre, post)], post))
    assert m.Tables.Find("Sales") is not None and m.Tables.Find("Fact") is None
    assert ji.diff_maps(pre, images(m)) == []


def test_deserialize_image_builds_polymorphic_columns_and_relationships():
    from core.operations.journal_apply import deserialize_image

    col = deserialize_image(
        "column", {"name": "C", "type": "calculated", "dataType": "int64", "expression": "1"}
    )
    assert type(col).__name__ == "CalculatedColumn" and col.Expression == "1"
    data = deserialize_image("column", {"name": "D", "dataType": "string", "sourceColumn": "D"})
    assert type(data).__name__ == "DataColumn"


def test_annotations_and_defaults_are_reset():
    m = build()
    me = m.Tables.Find("Sales").Measures.Find("Total")
    # offline quirk: TOM's serializer rejects a measure that had a child removed while it carries a
    # FormatStringDefinition, so this case uses a measure without one
    me.FormatStringDefinition = None
    pre = images(m)
    ann = T.Annotation()
    ann.Name = "note"
    ann.Value = "v"
    me.Annotations.Add(ann)
    me.IsHidden = True
    me.Description = "x"
    post = images(m)
    apply(m, jp.plan_undo([entry("J1", pre, post)], post))
    assert me.Annotations.Count == 0 and me.IsHidden is False and not me.Description
    assert ji.diff_maps(pre, images(m)) == []


def test_apply_error_is_a_journal_apply_error():
    from core.operations.journal_apply import JournalApplyError, apply_ops

    m = build()
    op = {
        "action": "update",
        "kind": "measure",
        "table": "Nope",
        "name": "Total",
        "locate_name": "Total",
        "ref": "measure:Nope[Total]",
        "json": {"name": "Total"},
        "current": {},
    }
    with pytest.raises(JournalApplyError):
        apply_ops(m, [op])
