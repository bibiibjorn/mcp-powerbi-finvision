"""Review slice C — 03_Batch_Operations / changeset (C-05, C-11, C-12, C-13, C-14,
C-23, C-32, C-36).

The batch engine runs for real on a PascalCase fake TOM model; only the AMO
connect (``get_server_db_model``) and the TOM ``Measure`` class are swapped.
What is asserted is what used to break: the number of SaveChanges, whether a
failure rolled back, whether the changeset staleness hash changes with the model.
"""

from __future__ import annotations

import pytest

from tests._c_fake_tom import FakeMeasure, FakeModel, FakeServer, Obj, all_measures
from tests._c_fake_tom import measure, table


@pytest.fixture
def amo(monkeypatch):
    """Point the batch engine + measure manager at a fake model."""
    import core.infrastructure.connection_state as cs_mod
    import core.operations.batch_operations as bo
    import core.operations.measure_crud_manager as mcm

    holder = {"model": None, "server": FakeServer()}

    def _connect(_cs):
        return holder["server"], None, holder["model"], None

    monkeypatch.setattr(bo, "get_server_db_model", _connect)
    monkeypatch.setattr(mcm, "Measure", FakeMeasure)
    monkeypatch.setattr(mcm, "AMO_AVAILABLE", True)
    cs = cs_mod.connection_state
    monkeypatch.setattr(cs, "is_connected", lambda: True)
    monkeypatch.setattr(cs, "query_executor", None, raising=False)
    monkeypatch.setattr(cs, "measure_crud_manager", mcm.MeasureCRUDManager(None), raising=False)

    def use(model):
        holder["model"] = model
        return model

    return use


def _measures(batch_operation, items, **extra):
    from core.operations.batch_operations import BatchOperationsHandler

    return BatchOperationsHandler()._batch_measures(
        {"batch_operation": batch_operation, "items": items, **extra}
    )


# ---------------------------------------------------------------------------
# C-11 — one SaveChanges per batch; options honoured; update never creates
# ---------------------------------------------------------------------------
def test_c11_failing_item_rolls_the_whole_create_back(amo):
    model = amo(FakeModel(table("Sales")))
    out = _measures(
        "create",
        [
            {"table_name": "Sales", "measure_name": "A", "expression": "1"},
            {"table_name": "Nope", "measure_name": "B", "expression": "2"},
            {"table_name": "Sales", "measure_name": "C", "expression": "3"},
        ],
    )
    assert out["success"] is False
    assert model.saves == 0 and model.undos == 1  # nothing committed
    assert out["rolled_back"] is True and out["committed"] is False
    assert out["not_attempted"] == 1


def test_c11_delete_batch_is_one_save(amo):
    model = amo(FakeModel(table("Sales", measures=[measure(f"M{i}") for i in range(5)])))
    out = _measures("delete", [{"table_name": "Sales", "measure_name": f"M{i}"} for i in range(5)])
    assert out["success"] is True and out["succeeded"] == 5
    assert model.saves == 1
    assert all_measures(model) == []


def test_c11_continue_on_error_commits_the_rest_only_without_transaction(amo):
    model = amo(FakeModel(table("Sales")))
    out = _measures(
        "create",
        [
            {"table_name": "Sales", "measure_name": "A", "expression": "1"},
            {"table_name": "Nope", "measure_name": "B", "expression": "2"},
            {"table_name": "Sales", "measure_name": "C", "expression": "3"},
        ],
        options={"use_transaction": False, "continue_on_error": True},
    )
    assert model.saves == 1 and out["succeeded"] == 2 and out["failed"] == 1
    assert all_measures(model) == ["Sales[A]", "Sales[C]"]


def test_c11_batch_update_never_creates_a_missing_measure(amo):
    model = amo(FakeModel(table("Sales", measures=[measure("Real")])))
    out = _measures("update", [{"table_name": "Sales", "measure_name": "Typo", "expression": "2"}])
    assert out["success"] is False
    assert out["errors"][0]["error_type"] == "measure_not_found"
    assert all_measures(model) == ["Sales[Real]"] and model.saves == 0


# ---------------------------------------------------------------------------
# C-12 — create never overwrites (model-wide), unless if_exists='update'
# ---------------------------------------------------------------------------
def test_c12_create_refuses_a_name_taken_in_another_table(amo):
    hand_written = measure("Sales YTD", "HAND WRITTEN")
    model = amo(FakeModel(table("Sales"), table("KPI", measures=[hand_written])))
    out = _measures(
        "create", [{"table_name": "Sales", "measure_name": "Sales YTD", "expression": "1"}]
    )
    assert out["success"] is False
    assert out["errors"][0]["error_type"] == "already_exists"
    assert hand_written.Expression == "HAND WRITTEN" and model.saves == 0


def test_c12_if_exists_update_turns_create_into_an_upsert(amo):
    existing = measure("Sales YTD", "OLD")
    model = amo(FakeModel(table("Sales", measures=[existing])))
    out = _measures(
        "create",
        [{"table_name": "Sales", "measure_name": "Sales YTD", "expression": "NEW"}],
        options={"if_exists": "update"},
    )
    assert out["success"] is True and existing.Expression == "NEW" and model.saves == 1


# ---------------------------------------------------------------------------
# C-14 — ti_suite apply=true actually creates, on the base measure's home table
# ---------------------------------------------------------------------------
def test_c14_ti_suite_apply_creates_on_the_home_table(amo, monkeypatch):
    import server.handlers.batch_operations_handler as bmod
    from core.autonomous import gating
    from core.operations import model_readback

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    gating.invalidate_policy_cache()
    model = amo(FakeModel(table("Sales", measures=[measure("Revenue", "SUM(Sales[Amt])")])))
    monkeypatch.setattr(
        model_readback, "measure_rows", lambda: [{"name": "Revenue", "table": "Sales"}]
    )
    out = bmod.handle_batch_operations(
        {
            "object_type": "ti_suite",
            "base_measures": ["Revenue"],
            "date_column": "'Date'[Date]",
            "variants": ["YTD", "PY"],
            "apply": True,
        }
    )
    gating.invalidate_policy_cache()
    assert out["success"] is True, out
    assert set(all_measures(model)) == {"Sales[Revenue]", "Sales[Revenue YTD]", "Sales[Revenue PY]"}
    assert model.saves == 1


def test_c14_generated_items_carry_measure_name():
    from core.operations.ti_measure_suite import generate_suite

    plan = generate_suite(["Revenue"], date_column="'Date'[Date]", variants=["YTD"])
    assert plan["measures"][0]["measure_name"] == "Revenue YTD"


# ---------------------------------------------------------------------------
# C-32 — batch rename: model-wide case-insensitive conflicts, no chains
# ---------------------------------------------------------------------------
def test_c32_rename_conflict_is_model_wide_and_case_insensitive(amo):
    model = amo(
        FakeModel(
            table("Sales", measures=[measure("A")]), table("KPI", measures=[measure("Total")])
        )
    )
    out = _measures("rename", [{"table_name": "Sales", "measure_name": "A", "new_name": "total"}])
    assert out["success"] is False
    assert out["errors"][0]["error_type"] == "name_conflict"
    assert model.saves == 0 and all_measures(model) == ["Sales[A]", "KPI[Total]"]


def test_c32_chained_renames_are_rejected_up_front(amo):
    model = amo(FakeModel(table("Sales", measures=[measure("A"), measure("B")])))
    out = _measures(
        "rename",
        [
            {"table_name": "Sales", "measure_name": "A", "new_name": "B2"},
            {"table_name": "Sales", "measure_name": "B2", "new_name": "C"},
        ],
    )
    assert out["success"] is False and out["error_type"] == "invalid_rename_batch"
    assert model.saves == 0


def test_c32_rename_batch_is_all_or_nothing(amo):
    model = amo(FakeModel(table("Sales", measures=[measure("A"), measure("B")])))
    out = _measures(
        "rename",
        [
            {"table_name": "Sales", "measure_name": "A", "new_name": "A2"},
            {"table_name": "Sales", "measure_name": "Missing", "new_name": "X"},
        ],
    )
    assert out["success"] is False and model.saves == 0 and model.undos == 1


# ---------------------------------------------------------------------------
# C-05 — the changeset staleness hash reads the real (PascalCase) TOM model
# ---------------------------------------------------------------------------
def _tom(name, expr):
    return FakeModel(
        table(
            "Sales",
            measures=[measure(name, expr)],
            columns=[Obj(Name="Amount", DataType="Double", Expression=None, IsHidden=False)],
        )
    )


def test_c05_plan_hash_distinguishes_pascalcase_models():
    from core.operations.batch_operations import compute_plan_hash

    assert compute_plan_hash(_tom("M", "1")) != compute_plan_hash(_tom("M", "2"))


def test_c05_plan_hash_refuses_a_model_without_tables():
    from core.operations.batch_operations import compute_plan_hash

    with pytest.raises(ValueError):
        compute_plan_hash(FakeModel())


def test_c05_stale_plan_is_refused(amo):
    from core.operations.batch_operations import BatchOperationsHandler

    model = amo(_tom("M", "1"))
    h = BatchOperationsHandler()
    doc = {"tables": {"update": [{"table_name": "Sales", "description": "x"}]}}
    pv = h.apply_changeset({"changeset": doc, "preview": True})
    assert pv["success"] is True and pv["plan_hash"]
    model.Tables[0].Measures[0].Expression = "2"  # someone edits the model meanwhile
    out = h.apply_changeset({"changeset": doc, "plan_hash": pv["plan_hash"]})
    assert out["success"] is False and out["error_type"] == "stale_plan"
    assert model.saves == 0


# ---------------------------------------------------------------------------
# C-23 — table refresh batch: shared model, per-item refresh_type, ONE save
# ---------------------------------------------------------------------------
def test_c23_table_refresh_batch_requests_on_the_shared_model(amo, monkeypatch):
    import core.infrastructure.connection_state as cs_mod
    import core.operations.model_refresh_manager as mrm
    from core.operations.batch_operations import BatchOperationsHandler

    requested = []

    def _t(name):
        t = table(name)
        t.RequestRefresh = lambda rtype, n=name: requested.append((n, rtype))
        return t

    model = amo(FakeModel(_t("A"), _t("B")))
    monkeypatch.setattr(mrm, "_resolve_refresh_type", lambda rt: (rt.lower(), None))
    monkeypatch.setattr(cs_mod.connection_state, "table_crud_manager", object(), raising=False)
    out = BatchOperationsHandler()._batch_tables(
        {
            "batch_operation": "refresh",
            "items": [{"table_name": "A", "refresh_type": "calculate"}, {"table_name": "B"}],
        }
    )
    assert out["success"] is True, out
    assert requested == [("A", "calculate"), ("B", "full")]
    assert model.saves == 1


# ---------------------------------------------------------------------------
# C-36 — dry-run validates tables/columns/relationships; changeset validated first
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "object_type, operation, item",
    [
        ("tables", "rename", {"table_name": "Sales"}),
        ("columns", "create", {"table_name": "Sales"}),
        ("relationships", "create", {"from_table": "A", "from_column": "k"}),
    ],
)
def test_c36_dry_run_reports_missing_fields(object_type, operation, item):
    from core.operations.batch_operations import BatchOperationsHandler

    out = BatchOperationsHandler._dry_run_result(object_type, operation, [item])
    assert out["success"] is False and out["validation_errors"]


def test_c36_non_dict_changeset_is_an_error_not_a_crash():
    import server.handlers.batch_operations_handler as bmod

    out = bmod.handle_batch_operations({"changeset": ["not", "a", "dict"], "plan_hash": "x"})
    assert out["success"] is False and out["error_type"] == "invalid_changeset"


# ---------------------------------------------------------------------------
# C-13 — batch severity by effect
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "object_type, op, n, extra, expected",
    [
        ("tables", "delete", 1, {}, "CATASTROPHIC"),
        ("measures", "delete", 11, {}, "CATASTROPHIC"),
        ("measures", "delete", 3, {}, "DESTRUCTIVE"),
        ("measures", "rename", 1, {"new_name": "x"}, "CATASTROPHIC"),
        ("tables", "update", 1, {"new_name": "x"}, "CATASTROPHIC"),
        ("tables", "refresh", 10, {}, "CATASTROPHIC"),
        ("tables", "refresh", 2, {}, "DESTRUCTIVE"),
    ],
)
def test_c13_batch_severity(object_type, op, n, extra, expected):
    import server.handlers.batch_operations_handler as bmod

    items = [{"table_name": f"T{i}", "name": f"N{i}", **extra} for i in range(n)]
    assert bmod._batch_severity(object_type, op, items).name == expected


def test_c13_changeset_dropping_one_table_is_catastrophic():
    import server.handlers.batch_operations_handler as bmod

    assert bmod._changeset_severity({"tables": {"delete": [{"table_name": "X"}]}}).name == (
        "CATASTROPHIC"
    )
