"""F-01 on the REAL TOM + the offline TMDL lane (the bundled AMO/TOM does the (de)serialisation, so
these skip when the assemblies cannot be loaded; Desktop detection is stubbed).

A preview of a real manager (``measure_crud_manager`` / ``table_crud_manager``) must report the
before / after TMSL, leave the folder byte-identical and the in-memory model rolled back; the apply
with the returned ``plan_hash`` then writes through the lane.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.infrastructure import offline_model, xmla_target
from core.operations import write_lane
from core.tmdl import tom_check
from server.handlers import model_operations_handler as moh
from server.handlers.connection_handler import handle_connection

pytestmark = pytest.mark.skipif(
    tom_check._load_serializer() is None, reason="bundled AMO/TOM assemblies not available"
)

TABLE = (
    "table {n}\n\n\tcolumn C1\n\t\tdataType: string\n\t\tsourceColumn: C1\n\n"
    "\tmeasure Total = COUNTROWS ( {n} )\n\t\tformatString: 0\n\n"
    "\tpartition {n} = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n"
    '\t\t\t\t    S = #table({{"C1"}}, {{}})\n\t\t\t\tin\n\t\t\t\t    S\n'
)


def _write_project(root: Path) -> Path:
    d = root / "M.SemanticModel" / "definition"
    (d / "tables").mkdir(parents=True)
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1601\n", encoding="utf-8")
    (d / "model.tmdl").write_text(
        "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\nref table A\nref table B\n",
        encoding="utf-8",
    )
    for n in "AB":
        (d / "tables" / f"{n}.tmdl").write_text(TABLE.format(n=n), encoding="utf-8")
    return d


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(offline_model, "_desktop_has_open", lambda d: None)
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    xmla_target.deactivate()
    d = _write_project(tmp_path)
    assert handle_connection({"operation": "connect_folder", "pbip_path": str(d)})["success"]
    offline_model.persist("normalise")  # byte-identical to the serializer's own output
    offline_model.disconnect()
    assert handle_connection({"operation": "connect_folder", "pbip_path": str(d)})["success"]
    yield d
    offline_model.disconnect()


def _snapshot(d: Path):
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()}


def _model():
    return offline_model.database().Model


def test_preview_of_a_measure_update_create_delete_leaves_folder_and_model_untouched(lane):
    before = _snapshot(lane)
    persisted = write_lane.get_write_lane().saves
    base = {"object_type": "measure", "table_name": "A", "preview": True}

    upd = moh.handle_model_operations(
        {**base, "operation": "update", "measure_name": "Total", "expression": "1"}
    )
    assert upd["success"] is True and upd["would_succeed"] is True, upd
    (change,) = upd["would_change"]
    assert (change["ref"], change["change"]) == ("measure:A[Total]", "modified")
    assert (
        "COUNTROWS" in change["before"]["expression"]
        and change["after"]["expression"].strip() == "1"
    )

    new = moh.handle_model_operations(
        {**base, "operation": "create", "measure_name": "Fresh", "expression": "2"}
    )
    assert [(c["ref"], c["change"]) for c in new["would_change"]] == [("measure:A[Fresh]", "added")]

    gone = moh.handle_model_operations({**base, "operation": "delete", "measure_name": "Total"})
    assert [(c["ref"], c["change"]) for c in gone["would_change"]] == [
        ("measure:A[Total]", "removed")
    ]
    assert gone["breaks_status"] == "unavailable_offline" and gone["breaks"] == []

    tbl = moh.handle_model_operations(
        {"object_type": "table", "operation": "delete", "table_name": "B", "preview": True}
    )
    refs = {c["ref"] for c in tbl["would_change"]}
    assert {"table:B", "column:B[C1]", "measure:B[Total]"} <= refs

    clash = moh.handle_model_operations(
        {**base, "operation": "create", "measure_name": "Total", "expression": "3"}
    )
    assert clash["success"] is False and clash["would_change"] == [] and "plan_hash" not in clash

    assert _snapshot(lane) == before  # not one byte written
    assert write_lane.get_write_lane().saves == persisted  # no save went through the lane
    assert _model().Tables.Count == 2  # the in-memory delete was rolled back (folder re-read)
    assert "Total" in {m.Name for m in _model().Tables["A"].Measures}
    assert "Fresh" not in {m.Name for m in _model().Tables["A"].Measures}


def test_apply_with_the_previewed_plan_hash_writes_through_the_lane(lane):
    before = _snapshot(lane)
    args = {
        "object_type": "measure",
        "operation": "create",
        "table_name": "A",
        "measure_name": "Fresh",
        "expression": "2",
    }
    preview = moh.handle_model_operations({**args, "preview": True})
    assert preview["success"] is True and preview["plan_hash"]
    applied = moh.handle_model_operations({**args, "plan_hash": preview["plan_hash"]})
    assert applied["success"] is True and applied["plan_hash_verified"] is True
    assert applied.get("offline_write") is True
    assert _snapshot(lane) != before
    assert "Fresh" in (lane / "tables" / "A.tmdl").read_text(encoding="utf-8")


def test_a_preview_after_the_model_moved_gives_a_new_hash_and_the_old_one_is_stale(lane):
    from core.infrastructure.connection_state import connection_state

    args = {
        "object_type": "measure",
        "operation": "update",
        "table_name": "A",
        "measure_name": "Total",
        "expression": "5",
    }
    first = moh.handle_model_operations({**args, "preview": True})
    connection_state.bump_model_generation("test: another write landed")
    stale = moh.handle_model_operations({**args, "plan_hash": first["plan_hash"]})
    assert stale["success"] is False and stale["error_type"] == "stale_preview"
    second = moh.handle_model_operations({**args, "preview": True})
    assert second["plan_hash"] != first["plan_hash"]
