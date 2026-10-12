"""BR-04: the change classifier behind ``17_Desktop_Bridge reload include_model=true``.

Pure logic + fake TOM objects (``SimpleNamespace``) - no CLR, no Desktop. The extractor
is duck-typed, so the same code that reads a live ``Database`` is exercised here.
The real-TOM extraction was checked by hand against the S02 TMDL folder (see the
work-package report); the live-vs-disk equality on a running Desktop is NOT covered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.autonomous import model_change_classifier as mcc

from tests._tom_fakes import (
    base,
    calc_table,
    col,
    db,
    meas,
    ns,
    part,
    rel,
    table,
)


def diff(live, disk, **kw):
    return mcc.diff_signatures(mcc.extract_signature(live), mcc.extract_signature(disk), **kw)


def verdict(live, disk):
    return mcc.classify(diff(live, disk))["verdict"]


def test_identical_models_are_unchanged():
    assert verdict(base(), base()) == "unchanged"
    assert diff(base(), base()) == []


# ---------------------------------------------------------------------------
# ALLOWED: metadata only
# ---------------------------------------------------------------------------
def test_measure_add_change_remove_are_metadata_only():
    disk = base()
    sales = disk.Model.Tables[0]
    sales.Measures = [meas("Total", "2"), meas("New", "3")]  # changed + added
    live = base()
    live.Model.Tables[0].Measures = [meas("Total", "1"), meas("Old", "9")]  # 'Old' only live
    changes = diff(live, disk)
    got = {(c["object"], c["change"]) for c in changes if c["category"] == "measure"}
    assert got == {("Sales[Total]", "changed"), ("Sales[New]", "added"), ("Sales[Old]", "removed")}
    assert all(c["safe"] for c in changes)
    assert mcc.classify(changes)["verdict"] == "metadata_only"


def test_format_string_display_folder_description_are_metadata_only():
    disk = base()
    disk.Model.Tables[0].Measures = [meas("Total", "1", fmt="0.00", folder="KPI", desc="d")]
    disk.Model.Tables[0].Description = "table doc"
    changes = diff(base(), disk)
    assert {c["category"] for c in changes} == {"measure", "table_properties"}
    assert mcc.classify(changes)["verdict"] == "metadata_only"


def test_calculated_column_add_change_remove_are_allowed():
    live = base()
    disk = base()
    disk.Model.Tables[0].Columns.append(col("Calc", "Calculated", "Double", expr="[Amount]*2"))
    assert verdict(live, disk) == "metadata_only"
    assert mcc.classify(diff(live, disk))["calculated_objects_changed"] is True
    live2 = base()
    live2.Model.Tables[0].Columns.append(col("Calc", "Calculated", "Double", expr="[Amount]"))
    assert verdict(live2, disk) == "metadata_only"  # expression changed
    assert verdict(disk, live) == "metadata_only"  # calc column removed


def test_calculated_table_dax_and_new_or_removed_calculated_tables_are_allowed():
    disk = base()
    disk.Model.Tables[1].Partitions = [part("Cal", "Calculated", "CALENDAR(1,3)")]
    changes = diff(base(), disk)
    assert [c["category"] for c in changes] == ["partition"] and changes[0]["safe"]
    disk2 = base()
    disk2.Model.Tables.append(calc_table("New calc", 'ROW("a",1)'))
    assert verdict(base(), disk2) == "metadata_only"
    assert verdict(disk2, base()) == "metadata_only"
    cg = table(
        "Calc group", parts=[part("Calc group", "CalculationGroup", "")], cols=[col("Name", "Data")]
    )
    disk3 = base()
    disk3.Model.Tables.append(cg)
    assert verdict(base(), disk3) == "metadata_only"  # a calc-group table holds no imported data


def test_relationship_change_is_allowed():
    disk = base()
    disk.Model.Relationships = [rel("Sales", "Id", "Cal", "V", active=False)]
    (c,) = diff(base(), disk)
    assert c["category"] == "relationship" and c["safe"] and c["change"] == "changed"
    disk.Model.Relationships = []
    assert verdict(base(), disk) == "metadata_only"


def test_import_column_metadata_change_is_allowed_but_not_its_source():
    disk = base()
    disk.Model.Tables[0].Columns[1] = col("Amount", dtype="Double", fmt="#,0", hidden=True)
    assert verdict(base(), disk) == "metadata_only"
    disk2 = base()
    disk2.Model.Tables[0].Columns[1] = col("Amount", dtype="Double", source="AmountRaw")
    assert verdict(base(), disk2) == "unsafe"


def test_functions_are_metadata_and_skipped_under_the_udf_gate():
    disk = base()
    disk.Model.Functions = [ns(Name="F", Expression="(x) => x")]
    assert verdict(base(), disk) == "metadata_only"
    assert diff(base(), disk, skip_functions=True) == []


def test_partition_mode_default_normalises_to_the_model_default():
    disk = base()
    disk.Model.Tables[0].Partitions[0].Mode = "Default"  # TMDL without an explicit mode
    assert diff(base(), disk) == []


def test_whitespace_only_expression_differences_are_not_changes():
    disk = base()
    disk.Model.Expressions = [ns(Name="p_Now", Kind="M", Expression="  DateTime.LocalNow()\r\n")]
    disk.Model.Tables[0].Measures = [meas("Total", "\n\t1  ")]
    assert diff(base(), disk) == []


def test_hidden_rownumber_column_is_ignored():
    live = base()
    live.Model.Tables[0].Columns.append(col("RowNumber-2662979B", "RowNumber", "Int64"))
    assert diff(live, base()) == []


# ---------------------------------------------------------------------------
# REFUSED
# ---------------------------------------------------------------------------
def test_partition_source_change_is_refused():
    disk = base()
    disk.Model.Tables[0].Partitions[0].Source.Expression = "let Source = 2 in Source"
    (c,) = diff(base(), disk)
    assert c["category"] == "partition" and not c["safe"]
    assert verdict(base(), disk) == "unsafe"


@pytest.mark.parametrize("new_mode", ["DirectQuery", "Dual"])
def test_storage_mode_change_is_refused(new_mode):
    disk = base()
    disk.Model.Tables[0].Partitions[0].Mode = new_mode
    assert verdict(base(), disk) == "unsafe"


def test_partition_added_or_removed_on_an_import_table_is_refused():
    disk = base()
    disk.Model.Tables[0].Partitions.append(part("P2"))
    assert verdict(base(), disk) == "unsafe"
    assert verdict(disk, base()) == "unsafe"


def test_named_expression_and_parameter_changes_are_refused():
    disk = base()
    disk.Model.Expressions = [ns(Name="p_Now", Kind="M", Expression="DateTime.FixedLocalNow()")]
    assert verdict(base(), disk) == "unsafe"
    disk.Model.Expressions = [
        ns(Name="p_Now", Kind="M", Expression="1"),
        ns(Name="p_New", Kind="M", Expression="2"),
    ]
    assert verdict(base(), disk) == "unsafe"
    disk.Model.Expressions = []
    assert verdict(base(), disk) == "unsafe"


def test_data_source_change_is_refused():
    disk = base()
    disk.Model.DataSources = [ns(Name="DS", Type="Structured", ConnectionString="x")]
    assert verdict(base(), disk) == "unsafe"


def test_new_or_removed_import_table_is_refused():
    disk = base()
    disk.Model.Tables.append(table("Fresh"))
    (c,) = [c for c in diff(base(), disk) if c["category"] == "table"]
    assert not c["safe"] and c["change"] == "added"
    assert verdict(disk, base()) == "unsafe"  # removing an import table drops loaded data


def test_import_column_added_or_removed_is_refused():
    disk = base()
    disk.Model.Tables[0].Columns.append(col("Extra"))
    assert verdict(base(), disk) == "unsafe"
    assert verdict(disk, base()) == "unsafe"


def test_column_type_change_between_data_and_calculated_is_refused():
    disk = base()
    disk.Model.Tables[0].Columns[1] = col("Amount", "Calculated", "Double", expr="1")
    assert verdict(base(), disk) == "unsafe"


def test_table_switching_between_calculated_and_import_is_refused():
    disk = base()
    disk.Model.Tables[1] = table("Cal")  # was a calculated table
    assert verdict(base(), disk) == "unsafe"


def test_refresh_policy_default_mode_and_compat_level_changes_are_refused():
    disk = base()
    disk.Model.Tables[0].RefreshPolicy = ns(PolicyType="Basic", RollingWindowGranularity="Year")
    assert verdict(base(), disk) == "unsafe"
    assert (
        verdict(
            base(),
            db(
                tables=base().Model.Tables,
                rels=base().Model.Relationships,
                expressions=base().Model.Expressions,
                mode="DirectQuery",
            ),
        )
        == "unsafe"
    )
    other = base()
    other.CompatibilityLevel = 1600
    assert verdict(base(), other) == "unsafe"


def test_mixed_change_set_is_unsafe_if_any_change_is():
    disk = base()
    disk.Model.Tables[0].Measures = [meas("Total", "2")]
    disk.Model.Tables[0].Partitions[0].Source.Expression = "changed"
    changes = diff(base(), disk)
    assert {c["safe"] for c in changes} == {True, False}
    v = mcc.classify(changes)
    assert v["verdict"] == "unsafe" and v["unsafe_count"] == 1


# ---------------------------------------------------------------------------
# classify_for_reload orchestration (I/O seams patched)
# ---------------------------------------------------------------------------
def _project(tmp_path: Path, fmt_tmdl=True) -> Path:
    root = tmp_path / "Proj"
    rep = root / "Proj.Report"
    model = root / "Model.SemanticModel"
    (rep).mkdir(parents=True)
    (model / "definition").mkdir(parents=True)
    (root / "Proj.pbip").write_text(
        json.dumps({"artifacts": [{"report": {"path": "Proj.Report"}}]}), encoding="utf-8"
    )
    (rep / "definition.pbir").write_text(
        json.dumps({"datasetReference": {"byPath": {"path": "../Model.SemanticModel"}}}),
        encoding="utf-8",
    )
    (model / "definition.pbism").write_text("{}", encoding="utf-8")
    if fmt_tmdl:
        (model / "definition" / "model.tmdl").write_text("model Model\n", encoding="utf-8")
    return root / "Proj.pbip"


def _seams(
    monkeypatch, disk_db=None, live_db=None, disk_result=None, live_result=None, udf_gate=False
):
    monkeypatch.setattr(
        mcc,
        "_read_disk_signature",
        lambda d: disk_result
        or {"ok": True, "signature": mcc.extract_signature(disk_db), "udf_gate": udf_gate},
    )
    monkeypatch.setattr(
        mcc,
        "_read_live_signature",
        lambda pm=None: live_result or {"ok": True, "signature": mcc.extract_signature(live_db)},
    )


def test_find_model_definition_resolves_the_tmdl_folder(tmp_path):
    pbip = _project(tmp_path)
    ddir, problem = mcc.find_model_definition(str(pbip))
    assert problem is None and Path(ddir).name == "definition"


def test_find_model_definition_rejects_bim_and_unbound_reports(tmp_path):
    pbip = _project(tmp_path, fmt_tmdl=False)
    ddir, problem = mcc.find_model_definition(str(pbip))
    assert ddir is None and problem


def test_classify_for_reload_metadata_only_returns_context(tmp_path, monkeypatch):
    disk = base()
    disk.Model.Tables[0].Measures = [meas("Total", "2")]
    _seams(monkeypatch, disk_db=disk, live_db=base())
    res, ctx = mcc.classify_for_reload(str(_project(tmp_path)))
    assert res["success"] is True and res["classification"] == "metadata_only"
    assert res["change_count"] == 1 and ctx["changes"] and ctx["disk_signature"]


def test_classify_for_reload_refuses_unsafe_with_guidance(tmp_path, monkeypatch):
    disk = base()
    disk.Model.Tables[0].Partitions[0].Source.Expression = "changed"
    _seams(monkeypatch, disk_db=disk, live_db=base())
    res, ctx = mcc.classify_for_reload(str(_project(tmp_path)))
    assert ctx is None and res["success"] is False
    assert res["error_type"] == "model_reload_unsafe_change"
    assert "WITHOUT saving" in res["guidance"] and "operation=open" in res["guidance"]
    assert res["unsafe_changes"][0]["category"] == "partition"


def test_classify_for_reload_tmdl_invalid_carries_the_parser_message(tmp_path, monkeypatch):
    _seams(
        monkeypatch,
        live_db=base(),
        disk_result={"ok": False, "error": "Line 12: unexpected property 'foo'"},
    )
    res, ctx = mcc.classify_for_reload(str(_project(tmp_path)))
    assert ctx is None and res["error_type"] == "tmdl_invalid"
    assert "unexpected property 'foo'" in res["error"] and res["parser_message"]


def test_classify_for_reload_fails_closed_without_a_live_model(tmp_path, monkeypatch):
    _seams(monkeypatch, disk_db=base(), live_result={"ok": False, "reason": "not connected"})
    res, ctx = mcc.classify_for_reload(str(_project(tmp_path)))
    assert ctx is None and res["error_type"] == "model_change_unclassified"
    assert "not connected" in res["error"] and "NOT attempted" in res["error"]


def test_classify_for_reload_fails_closed_when_tom_is_unavailable(tmp_path, monkeypatch):
    _seams(
        monkeypatch,
        live_db=base(),
        disk_result={"ok": False, "unavailable": True, "reason": "no AMO"},
    )
    res, _ = mcc.classify_for_reload(str(_project(tmp_path)))
    assert res["error_type"] == "model_change_unclassified"


def test_classify_for_reload_fails_closed_without_a_definition_folder(tmp_path):
    res, ctx = mcc.classify_for_reload(str(tmp_path / "nope.pbip"))
    assert ctx is None and res["error_type"] == "model_change_unclassified"


def test_udf_gate_skips_functions_in_the_comparison(tmp_path, monkeypatch):
    live = base()
    live.Model.Functions = [ns(Name="F", Expression="(x) => x")]
    _seams(monkeypatch, disk_db=base(), live_db=live, udf_gate=True)
    res, ctx = mcc.classify_for_reload(str(_project(tmp_path)))
    assert res["success"] and res["classification"] == "unchanged" and res["udf_gate"] is True


def test_change_list_is_capped_but_counts_are_complete(tmp_path, monkeypatch):
    disk = base()
    disk.Model.Tables[0].Measures = [meas(f"M{i}", str(i)) for i in range(150)]
    _seams(monkeypatch, disk_db=disk, live_db=base())
    res, ctx = mcc.classify_for_reload(str(_project(tmp_path)))
    assert res["change_count"] == 151  # 150 added + 'Total' removed
    assert len(res["changes"]) == mcc.MAX_CHANGES_REPORTED
    assert res["changes_truncated"]["total"] == 151 and len(ctx["changes"]) == 151


# ---------------------------------------------------------------------------
# the live-connection identity check (real _read_live_signature, fake connection)
# ---------------------------------------------------------------------------
def _fake_connection(monkeypatch, *, connected=True, pbip=None, db_obj=None):
    import core.infrastructure.connection_state as cs_mod
    import core.operations.amo_helpers as amo

    state = ns(
        is_connected=lambda: connected,
        get_pbip_info=lambda: {"file_full_path": pbip, "pbip_folder_path": None},
    )
    monkeypatch.setattr(cs_mod, "connection_state", state)
    server = ns(Disconnect=lambda: None)
    monkeypatch.setattr(
        amo,
        "get_server_db_model",
        lambda cs: (server, db_obj, db_obj.Model if db_obj else None, None),
    )
    return state


def test_live_reader_requires_a_matching_project(monkeypatch):
    # conftest replaced the reader for hermeticity; import the original from source.
    import importlib.util

    spec = importlib.util.spec_from_file_location("_mcc_fresh", mcc.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)

    _fake_connection(monkeypatch, connected=False)
    assert fresh._read_live_signature(lambda p: True)["ok"] is False

    _fake_connection(monkeypatch, connected=True, pbip=None, db_obj=base())
    r = fresh._read_live_signature(lambda p: True)
    assert r["ok"] is False and "cannot verify" in r["reason"]

    _fake_connection(monkeypatch, connected=True, pbip="C:/other/Other.pbip", db_obj=base())
    r = fresh._read_live_signature(lambda p: False)
    assert r["ok"] is False and "different project" in r["reason"]

    _fake_connection(monkeypatch, connected=True, pbip="C:/x/Mine.pbip", db_obj=base())
    r = fresh._read_live_signature(lambda p: p.endswith("Mine.pbip"))
    assert r["ok"] is True and "Sales" in r["signature"]["tables"]


# ---------------------------------------------------------------------------
# verify_after_reload
# ---------------------------------------------------------------------------
def _changes_for(live, disk):
    return diff(live, disk)


def test_verify_after_reload_reports_applied_and_mismatch(monkeypatch):
    live, disk = base(), base()
    disk.Model.Tables[0].Measures = [meas("Total", "2"), meas("New", "3")]
    changes = _changes_for(live, disk)
    disk_sig = mcc.extract_signature(disk)

    # after the reload the engine has 'Total' updated but still lacks 'New'
    after = base()
    after.Model.Tables[0].Measures = [meas("Total", "2")]
    monkeypatch.setattr(
        mcc,
        "_read_live_signature",
        lambda pm=None: {"ok": True, "signature": mcc.extract_signature(after)},
    )
    out = mcc.verify_after_reload(disk_sig, changes, attempts=2, interval_s=0)
    assert out["status"] == "mismatch"
    assert out["applied"] == ["measure Sales[Total] changed"]
    assert out["mismatch"] == ["measure Sales[New] added"]

    monkeypatch.setattr(
        mcc, "_read_live_signature", lambda pm=None: {"ok": True, "signature": disk_sig}
    )
    out = mcc.verify_after_reload(disk_sig, changes, attempts=2, interval_s=0)
    assert out["status"] == "verified" and len(out["applied"]) == 2 and out["mismatch"] == []


def test_verify_after_reload_unavailable_is_never_reported_as_verified(monkeypatch):
    monkeypatch.setattr(
        mcc, "_read_live_signature", lambda pm=None: {"ok": False, "reason": "connection dropped"}
    )
    out = mcc.verify_after_reload({}, [], attempts=2, interval_s=0)
    assert out["status"] == "unavailable" and out["reason"] == "connection dropped"


def test_verify_after_reload_retries_until_the_model_settles(monkeypatch):
    live, disk = base(), base()
    disk.Model.Tables[0].Measures = [meas("Total", "2")]
    changes = _changes_for(live, disk)
    disk_sig = mcc.extract_signature(disk)
    seq = iter([mcc.extract_signature(live), disk_sig])
    monkeypatch.setattr(
        mcc, "_read_live_signature", lambda pm=None: {"ok": True, "signature": next(seq)}
    )
    out = mcc.verify_after_reload(disk_sig, changes, attempts=3, interval_s=0)
    assert out["status"] == "verified"
