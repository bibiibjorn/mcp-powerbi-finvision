"""F-15 (2026-10-03 deep review): ``15_DAX_Refactor`` transform ``extract_udf``.

Preview -> plan_hash -> apply through the codemod transaction with fakes: the function is created
FIRST, the measures are rewritten as calls in one save, values are verified, and any failure /
rollback removes the function again. Refuses below the UDF compatibility level.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from core.codemods import transaction as tx
from core.codemods.plan_hash import compute_plan_hash
from core.codemods.transforms import registry

YTD = "CALCULATE([{b}], DATESYTD('Date'[Date]), 'Date'[Year] = 2024)"
BASES = ("Sales", "Cost", "Margin")


def model(cl=1702, udfs=(), extra=()):
    meas = [
        NS(name="Sales", expression="SUM(F[Sales])"),
        NS(name="Cost", expression="SUM(F[Cost])"),
    ]
    meas.append(NS(name="Margin", expression="[Sales] - [Cost]"))
    meas += [NS(name=f"{b} YTD", expression=YTD.format(b=b)) for b in BASES]
    meas += list(extra)
    m = NS(
        tables=[NS(name="M", measures=meas, columns=[])],
        relationships=[],
    )
    if cl is not None:
        m.compatibility_level = cl
    m.udf_names = list(udfs)
    return m


ARGS = {
    "measures": [f"M[{b} YTD]" for b in BASES],
    "function_name": "YtdOf",
    "description": "Year to date of a measure",
}


@pytest.fixture(autouse=True)
def journal_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_CODEMOD_JOURNAL_DIR", str(tmp_path))
    return tmp_path


class Persister:
    """Legacy (non-atomic) measure persister fake that logs into a shared event list."""

    def __init__(self, events, fail=()):
        self.events, self.fail = events, set(fail)

    def upsert_measure(self, *, table_name, measure_name, dax_expression, **kw):
        self.events.append(("measure", measure_name, dax_expression))
        if measure_name in self.fail:
            return {"success": False, "error": "boom"}
        return {"success": True}

    def delete_measure(self, *, table_name, measure_name, **kw):
        self.events.append(("delete_measure", measure_name))
        return {"success": True}


class FnPersister:
    def __init__(self, events, fail_create=False, existing=()):
        self.events, self.fail_create = events, fail_create
        self.functions = {str(n).lower() for n in existing}

    def create_function(self, function_name, expression, parameters=None, description=None):
        self.events.append(("create_function", function_name, expression, description))
        if self.fail_create:
            return {"success": False, "error": "tmsl boom", "error_type": "tmsl_error"}
        self.functions.add(function_name.lower())
        return {"success": True}

    def delete_function(self, function_name):
        self.events.append(("delete_function", function_name))
        if function_name.lower() not in self.functions:
            return {"success": False, "error": "nf", "error_type": "not_found"}
        self.functions.discard(function_name.lower())
        return {"success": True}


def _apply(m, args=ARGS, events=None, persister=None, fp=None, **kw):
    events = events if events is not None else []
    pv = tx.preview(m, "extract_udf", args, "db")
    out = tx.apply(
        m,
        "extract_udf",
        args,
        pv["plan_hash"],
        persister=persister or Persister(events),
        identity="db",
        function_persister=fp or FnPersister(events),
        **kw,
    )
    return pv, out, events


# --- registry / preview --------------------------------------------------------------------------


def test_transform_is_registered_and_listed():
    t = registry()["extract_udf"]
    assert t.persistable and t.semantics_preserving
    names = {x["name"] for x in tx.list_transforms()["transforms"]}
    assert "extract_udf" in names


def test_preview_builds_the_function_and_one_call_per_measure():
    pv = tx.preview(model(), "extract_udf", ARGS, "db")
    assert pv["success"] is True
    targets = [e["target"] for e in pv["edits"]]
    assert targets == ["function:YtdOf"] + [f"measure:M[{b} YTD]" for b in BASES]
    fn = pv["edits"][0]
    assert fn["note"] == "create" and fn["before"] == ""
    assert fn["after"].startswith("(measureRef : MeasureRef) =>")
    assert "DATESYTD ( 'Date'[Date] )" in fn["after"]
    calls = {e["target"]: e["after"] for e in pv["edits"][1:]}
    assert calls["measure:M[Sales YTD]"] == "YtdOf ( [Sales] )"
    assert calls["measure:M[Margin YTD]"] == "YtdOf ( [Margin] )"
    assert pv["rejected_edits"] == []
    assert any("grand total" in w for w in pv["warnings"])
    assert pv["affected_measures"] == [f"measure:M[{b} YTD]" for b in BASES]


def test_preview_keeps_a_leading_comment_block_on_the_call():
    m = model()
    m.tables[0].measures[3].expression = "// YTD of sales\n" + YTD.format(b="Sales")
    pv = tx.preview(m, "extract_udf", ARGS, "db")
    after = {e["target"]: e["after"] for e in pv["edits"]}["measure:M[Sales YTD]"]
    assert after.startswith("// YTD of sales") and after.endswith("YtdOf ( [Sales] )")


@pytest.mark.parametrize(
    "cl,etype",
    [(1601, "compatibility_level_too_low"), (None, "compatibility_level_unknown")],
)
def test_preview_refuses_below_or_without_the_udf_compatibility_level(cl, etype):
    pv = tx.preview(model(cl=cl), "extract_udf", ARGS, "db")
    assert pv["success"] is False and pv["error_type"] == etype
    assert pv["edits"] == [] and pv["required_compatibility_level"] == 1702
    if cl:
        assert "irreversible" in pv["error"]


@pytest.mark.parametrize(
    "mutate,etype",
    [
        (lambda a: {**a, "function_name": "SUMX"}, "invalid_function_name"),
        (lambda a: {**a, "function_name": "9bad"}, "invalid_function_name"),
        (lambda a: {**a, "measures": ["M[Sales YTD]", "M[Nope]"]}, "measure_not_found"),
    ],
)
def test_preview_refuses_bad_names_and_unknown_measures(mutate, etype):
    pv = tx.preview(model(), "extract_udf", mutate(ARGS), "db")
    assert pv["success"] is False and pv["error_type"] == etype


def test_preview_refuses_an_existing_function():
    pv = tx.preview(model(udfs=["ytdof"]), "extract_udf", ARGS, "db")
    assert pv["success"] is False and pv["error_type"] == "already_exists"


def test_preview_without_edits_for_different_structures_and_identical_bodies():
    m = model()
    m.tables[0].measures[4].expression = "CALCULATE([Cost], DATESMTD('Date'[Date]))"
    pv = tx.preview(m, "extract_udf", ARGS, "db")
    assert pv["success"] is True and pv["edits"] == []
    assert any("do not share one structure" in w for w in pv["warnings"])

    same = model()
    for mm in same.tables[0].measures[3:]:
        mm.expression = YTD.format(b="Sales")
    pv = tx.preview(same, "extract_udf", ARGS, "db")
    assert pv["edits"] == [] and any("identical" in w for w in pv["warnings"])


def test_preview_with_too_few_measures():
    pv = tx.preview(model(), "extract_udf", {**ARGS, "measures": ["M[Sales YTD]"]}, "db")
    assert pv["edits"] == [] and pv["warnings"]


def test_plan_hash_changes_when_a_function_appears():
    a = compute_plan_hash(model(udfs=[]), "extract_udf", ARGS, "db")
    b = compute_plan_hash(model(udfs=["Other"]), "extract_udf", ARGS, "db")
    assert a != b


# --- apply ---------------------------------------------------------------------------------------


def test_apply_creates_the_function_first_then_writes_the_calls():
    m = model()
    values = iter([{"M[Sales YTD]": 1}, {"M[Sales YTD]": 1}])
    pv, out, events = _apply(m, evaluator=lambda refs: next(values))
    assert out["success"] is True and out["committed"] is True
    kinds = [e[0] for e in events]
    assert kinds[0] == "create_function" and kinds.count("measure") == 3
    assert events[0][1] == "YtdOf" and events[0][3] == "Year to date of a measure"
    written = {e[1]: e[2] for e in events if e[0] == "measure"}
    assert written["Cost YTD"] == "YtdOf ( [Cost] )"
    assert out["functions_created"] == ["YtdOf"]
    journal = json.loads(open(out["journal_path"], encoding="utf-8").read())
    assert journal["functions_created"] == ["YtdOf"]
    assert [e["target"] for e in journal["edits"]][0] == "function:YtdOf"


def test_apply_refusal_writes_nothing():
    m = model(cl=1500)
    pv = tx.preview(m, "extract_udf", ARGS, "db")
    events = []
    out = tx.apply(
        m,
        "extract_udf",
        ARGS,
        pv["plan_hash"],
        persister=Persister(events),
        identity="db",
        function_persister=FnPersister(events),
    )
    assert out["success"] is False and out["error_type"] == "compatibility_level_too_low"
    assert events == []


def test_function_create_failure_aborts_before_any_measure_write():
    events = []
    pv, out, _ = _apply(model(), events=events, fp=FnPersister(events, fail_create=True))
    assert out["success"] is False and out["error_type"] == "tmsl_error"
    assert [e[0] for e in events] == ["create_function"]  # no measure touched, nothing to clean


class AtomicFail:
    """Atomic persister fake: the single save fails, nothing is committed."""

    def __init__(self, events):
        self.events = events

    def apply_measure_changes(self, upserts, deletes=None):
        self.events.append(("atomic_save", [u["measure_name"] for u in upserts]))
        return {"success": False, "committed": False, "errors": [{"error": "compile error"}]}


def test_failed_measure_write_removes_the_created_function():
    events = []
    pv, out, _ = _apply(model(), events=events, persister=AtomicFail(events))
    assert out["success"] is False
    assert [e[0] for e in events] == ["create_function", "atomic_save", "delete_function"]
    assert out["function_cleanup"] == [{"function": "YtdOf", "deleted": True}]
    assert out["functions_created"] == []


def test_partial_legacy_write_keeps_the_function_the_written_measures_call():
    events = []
    pv, out, _ = _apply(model(), events=events, persister=Persister(events, fail=["Cost YTD"]))
    assert out["success"] is False and out["committed"] is True
    assert ("delete_function", "YtdOf") not in events  # written measures still call it
    assert out["functions_created"] == ["YtdOf"]


def test_engine_rejecting_a_call_removes_the_function():
    events = []
    out = _apply(
        model(),
        events=events,
        validator=lambda after, defines: (
            "Failed to resolve name 'YtdOf'" if "Cost" in after else None
        ),
    )[1]
    # one measure rejected -> dropped; the rest still commit and keep the function
    assert out["rejected_edits"] and out["committed"] is True
    events2 = []
    out2 = _apply(model(), events=events2, validator=lambda after, defines: "boom")[1]
    assert out2.get("committed") in (None, False)
    assert ("delete_function", "YtdOf") in events2


def test_value_difference_rolls_back_the_measures_and_the_function():
    events = []
    values = iter([{"M[Sales YTD]": 1.0}, {"M[Sales YTD]": 2.0}])
    pv, out, _ = _apply(model(), events=events, evaluator=lambda refs: next(values))
    assert out["success"] is False and out["error_type"] == "semantic_change_detected"
    # the originals were written back and the function deleted again
    restored = [e for e in events if e[0] == "measure"][3:]
    assert {e[1] for e in restored} == {"Sales YTD", "Cost YTD", "Margin YTD"}
    assert ("delete_function", "YtdOf") in events
    assert events.index(("delete_function", "YtdOf")) > events.index(restored[-1])


def test_rollback_restores_measures_then_deletes_the_function():
    events = []
    pv, out, _ = _apply(model(), events=events)
    assert out["success"] is True
    rb_events = []
    fp = FnPersister(rb_events, existing=["YtdOf"])
    rb = tx.rollback(
        out["journal_path"],
        persister=Persister(rb_events),
        identity="db",
        function_persister=fp,
    )
    assert rb["success"] is True
    assert "function:YtdOf (deleted)" in rb["restored"]
    assert [e[0] for e in rb_events][-1] == "delete_function"
    assert [e[2] for e in rb_events if e[0] == "measure"][0] == YTD.format(b="Sales")


def test_handler_attaches_the_compatibility_level_and_functions(monkeypatch):
    from unittest.mock import MagicMock

    import server.handlers.dax_codemods_handler as h
    from core.infrastructure import live_model_facade

    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()
    qe.validate_and_execute_dax.return_value = {
        "success": True,
        "rows": [{"[CompatibilityLevel]": 1500}],
    }
    qe.list_udfs.return_value = {"success": True, "rows": [{"[Name]": "Existing"}]}
    cs.query_executor = qe
    cs.get_connection_identity.return_value = {"database": "db", "port": 1}
    monkeypatch.setattr(h, "_connection_state", lambda: cs)
    fake = model(cl=None)
    monkeypatch.setattr(live_model_facade, "build_live_model", lambda **kw: fake)
    res = h.handle_dax_codemods({"operation": "preview", "transform": "extract_udf", "args": ARGS})
    assert res["success"] is False and res["error_type"] == "compatibility_level_too_low"
    assert fake.compatibility_level == 1500 and fake.udf_names == ["Existing"]


def test_compatibility_level_comes_from_dbschema_catalogs_when_info_model_has_none():
    """Live Desktop: INFO.MODEL() has no CompatibilityLevel column; DBSCHEMA_CATALOGS has it
    (found by the 2026-10-03 live test on R0201 - find_clones reported compatibility_level null
    and extract_udf would have refused every model)."""
    from core.dax.udf_support import read_compatibility_level

    class _QE:
        def execute_dmv_query(self, q):
            assert "DBSCHEMA_CATALOGS" in q
            return {"success": True, "rows": [{"CATALOG_NAME": "x", "COMPATIBILITY_LEVEL": 1702}]}

        def validate_and_execute_dax(self, *a, **k):  # INFO.MODEL without the column
            return {"success": True, "rows": [{"[Name]": "Model", "[Version]": 4}]}

    assert read_compatibility_level(_QE()) == 1702


def test_compatibility_level_falls_back_to_info_model_and_ignores_ambiguous_catalogs():
    from core.dax.udf_support import read_compatibility_level

    class _QE:
        def execute_dmv_query(self, q):
            return {
                "success": True,
                "rows": [{"COMPATIBILITY_LEVEL": 1600}, {"COMPATIBILITY_LEVEL": 1702}],
            }

        def validate_and_execute_dax(self, *a, **k):
            return {"success": True, "rows": [{"[CompatibilityLevel]": 1702}]}

    assert read_compatibility_level(_QE()) == 1702
