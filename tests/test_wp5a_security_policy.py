"""F-22 / POL-01: the data-vs-metadata split (``security.allow_data_read``) and the per-model /
per-op policy rules (``security.rules``), evaluated in the dispatcher BEFORE every other gate.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server import op_classification as oc
from server import security_policy as sp
from server.dispatch import ToolDispatcher
from server.registry import HandlerRegistry, ToolDefinition


# ---------------------------------------------------------------------------
# the returns_data axis
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool,args",
    [
        ("04_Run_DAX", {"query": 'EVALUATE ROW("a", 1)'}),
        ("04_Run_DAX", {"query": "x", "mode": "trace"}),
        ("04_Run_DAX", {"mode": "build", "execute": True}),
        ("02_Model_Operations", {"object_type": "table", "operation": "sample_data"}),
        ("02_Model_Operations", {"object_type": "column", "operation": "distribution"}),
        ("04_Query_Operations", {"operation": "impersonate_run"}),
        ("04_Query_Operations", {"operation": "simulate_as_user"}),
        ("09_Debug_Operations", {"operation": "drill"}),
        ("09_Debug_Operations", {}),  # default op = visual
        ("09_Debug_Operations", {"operation": "run_dax"}),
        ("09_Validate", {"operation": "tieout"}),
        ("09_Validate", {"operation": "equivalence"}),
        ("09_Validate", {"operation": "expected_value"}),
        ("09_Profile", {"operation": "decompose"}),
        ("13_Query_Plan", {"operation": "evaluate_and_log"}),
        ("17_Desktop_Bridge", {"operation": "screenshot"}),
        ("17_Desktop_Bridge", {"operation": "reload_and_screenshot"}),
        ("05_DAX_Intelligence", {"operation": "explain", "include_values": True}),
    ],
)
def test_ops_that_return_model_values_are_marked(tool, args):
    assert oc.returns_data(tool, args) is True


@pytest.mark.parametrize(
    "tool,args",
    [
        ("02_Model_Operations", {"object_type": "table", "operation": "list"}),
        ("02_Model_Operations", {"object_type": "measure", "operation": "get"}),
        # aggregate-only: counts, no values
        ("02_Model_Operations", {"object_type": "column", "operation": "statistics"}),
        ("04_Query_Operations", {"operation": "rls_audit"}),
        ("04_Query_Operations", {"operation": "vertipaq_analyzer"}),
        ("04_Run_DAX", {"query": "EVALUATE x", "mode": "validate"}),
        ("04_Run_DAX", {"mode": "build"}),  # returns the generated query text only
        ("05_DAX_Intelligence", {"operation": "explain"}),
        ("09_Debug_Operations", {"operation": "audit"}),
        ("09_Debug_Operations", {"operation": "compare", "mode": "live_swap", "dry_run": True}),
        ("09_Validate", {"operation": "test_list"}),
        ("17_Desktop_Bridge", {"operation": "status"}),
        ("16_Dependency_Graph", {"operation": "build"}),
        ("07_Page_Operations", {"operation": "list"}),
    ],
)
def test_metadata_ops_are_not_marked(tool, args):
    assert oc.returns_data(tool, args) is False


def test_every_op_named_in_the_data_table_exists_in_the_registry():
    from server.handlers import register_all_handlers

    reg = HandlerRegistry()
    register_all_handlers(reg)
    for tool, ops in oc.data_ops().items():
        schema = reg.get_full_schema(tool)
        enum = set((schema["properties"].get("operation") or {}).get("enum") or [])
        for op in ops - {"*"}:
            assert op in enum, (tool, op)


# ---------------------------------------------------------------------------
# a dispatcher with a policy
# ---------------------------------------------------------------------------
def _dispatcher(policy: sp.SecurityPolicy, calls: list):
    reg = HandlerRegistry()
    for name, cat in (
        ("02_Model_Operations", "model"),
        ("03_Batch_Operations", "batch"),
        ("04_Run_DAX", "query"),
        ("09_Debug_Operations", "debug"),
        ("17_Desktop_Bridge", "autonomous"),
    ):
        reg.register(
            ToolDefinition(
                name=name,
                description="t",
                handler=lambda a, n=name: calls.append((n, a)) or {"success": True, "ran": n},
                input_schema={"type": "object", "properties": {}},
                category=cat,
            )
        )
    d = ToolDispatcher()
    d.registry = reg
    d._policy = policy
    d._read_only = False
    d._confirm_destructive = False
    return d


def _names(monkeypatch, *names):
    monkeypatch.setattr(sp, "_current_database_names", lambda: list(names))


def test_default_policy_evaluates_nothing(monkeypatch):
    calls: list = []
    d = _dispatcher(sp.SecurityPolicy(), calls)
    monkeypatch.setattr(
        sp, "_current_database_names", lambda: pytest.fail("resolved a database name")
    )
    assert d.dispatch("04_Run_DAX", {"query": 'EVALUATE ROW("a", 1)'})["success"] is True
    assert (
        d.dispatch("02_Model_Operations", {"object_type": "table", "operation": "sample_data"})[
            "success"
        ]
        is True
    )
    assert sp.SecurityPolicy().active is False


def test_allow_data_read_false_refuses_data_ops_and_keeps_metadata_working():
    calls: list = []
    d = _dispatcher(sp.SecurityPolicy(allow_data_read=False), calls)
    for tool, args in (
        ("04_Run_DAX", {"query": 'EVALUATE ROW("a", 1)'}),
        (
            "02_Model_Operations",
            {"object_type": "table", "operation": "sample_data", "table_name": "T"},
        ),
        (
            "02_Model_Operations",
            {
                "object_type": "column",
                "operation": "distribution",
                "table_name": "T",
                "column_name": "C",
            },
        ),
        ("09_Debug_Operations", {"operation": "drill"}),
        ("17_Desktop_Bridge", {"operation": "screenshot"}),
    ):
        out = d.dispatch(tool, args)
        assert out["success"] is False and out["error_type"] == "data_read_disabled", (
            tool,
            args,
            out,
        )
        assert out["returns_data"] is True
    assert calls == []  # no handler ran
    for tool, args in (
        ("02_Model_Operations", {"object_type": "table", "operation": "list"}),
        (
            "02_Model_Operations",
            {
                "object_type": "column",
                "operation": "statistics",
                "table_name": "T",
                "column_name": "C",
            },
        ),
        (
            "02_Model_Operations",
            {
                "object_type": "measure",
                "operation": "update",
                "table_name": "T",
                "measure_name": "M",
                "expression": "1",
            },
        ),
        ("04_Run_DAX", {"query": "EVALUATE x", "mode": "validate"}),
        ("17_Desktop_Bridge", {"operation": "status"}),
    ):
        assert d.dispatch(tool, args)["success"] is True, (tool, args)


def test_data_refusal_comes_before_the_read_only_gate():
    d = _dispatcher(sp.SecurityPolicy(allow_data_read=False), [])
    d._read_only = True
    out = d.dispatch("04_Run_DAX", {"query": "EVALUATE x"})
    assert out["error_type"] == "data_read_disabled"


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------
PROD_DENY = {
    "id": "no-prod-table-delete",
    "database": "*PROD*",
    "tool": "02_Model_Operations",
    "operation": "table.delete",
    "action": "deny",
    "message": "production tables are never deleted from here",
}


def test_deny_rule_matches_the_database_glob_tool_and_operation(monkeypatch):
    calls: list = []
    d = _dispatcher(sp.SecurityPolicy(rules=[PROD_DENY]), calls)
    delete = {"object_type": "table", "operation": "delete", "table_name": "Sales"}
    _names(monkeypatch, "R01_PROD_Wealth", "C:/x/R01.pbip")
    out = d.dispatch("02_Model_Operations", delete)
    assert out["success"] is False and out["error_type"] == "policy_denied"
    assert out["rule"] == "no-prod-table-delete" and "never deleted" in out["error"]
    assert calls == []
    # another database: allowed
    _names(monkeypatch, "R02_DEV")
    assert d.dispatch("02_Model_Operations", delete)["success"] is True
    # same database, other op / other object type: allowed
    _names(monkeypatch, "R01_PROD_Wealth")
    assert d.dispatch("02_Model_Operations", {**delete, "operation": "update"})["success"] is True
    assert (
        d.dispatch(
            "02_Model_Operations", {**delete, "object_type": "measure", "measure_name": "M"}
        )["success"]
        is True
    )
    # case-insensitive glob
    assert (
        sp.SecurityPolicy(rules=[{**PROD_DENY, "database": "*prod*"}]).evaluate(
            "02_Model_Operations", delete, "x", returns_data=False
        )["error_type"]
        == "policy_denied"
    )


def test_a_deny_rule_cannot_be_bypassed_through_the_composites_or_batches(monkeypatch):
    # no `tool` matcher: the operation spelling is what counts (a rule scoped to one tool stays scoped)
    rule = {k: v for k, v in PROD_DENY.items() if k != "tool"}
    d = _dispatcher(sp.SecurityPolicy(rules=[rule]), [])
    _names(monkeypatch, "PROD")
    bypasses = [
        (
            "02_Model_Operations",
            {"object_type": "table", "operation": "safe_delete", "table_name": "T"},
        ),
        (
            "03_Batch_Operations",
            {"object_type": "tables", "batch_operation": "delete", "items": [{"table_name": "T"}]},
        ),
        (
            "03_Batch_Operations",
            {
                "object_type": "changeset",
                "changeset": {"tables": {"delete": [{"table_name": "T"}]}},
            },
        ),
    ]
    for tool, args in bypasses:
        assert d.dispatch(tool, args)["error_type"] == "policy_denied", (tool, args)
    # a changeset that only CREATES a table is not a table.delete
    ok = d.dispatch(
        "03_Batch_Operations",
        {"object_type": "changeset", "changeset": {"tables": {"create": [{"name": "T"}]}}},
    )
    assert ok["success"] is True
    # ... and a rule scoped to 02 does not govern 03
    scoped = _dispatcher(sp.SecurityPolicy(rules=[PROD_DENY]), [])
    batch = {"object_type": "tables", "batch_operation": "delete", "items": [{"table_name": "T"}]}
    assert scoped.dispatch("03_Batch_Operations", batch)["success"] is True


def test_a_preview_matches_unless_the_rule_allows_it(monkeypatch):
    _names(monkeypatch, "PROD")
    args = {"object_type": "table", "operation": "delete", "table_name": "T", "preview": True}
    d = _dispatcher(sp.SecurityPolicy(rules=[PROD_DENY]), [])
    assert d.dispatch("02_Model_Operations", args)["error_type"] == "policy_denied"
    d = _dispatcher(sp.SecurityPolicy(rules=[{**PROD_DENY, "allow_preview": True}]), [])
    assert d.dispatch("02_Model_Operations", args)["success"] is True
    assert (
        d.dispatch("02_Model_Operations", {**args, "preview": False})["error_type"]
        == "policy_denied"
    )


def test_require_confirm_is_satisfied_by_confirm_destructive(monkeypatch):
    _names(monkeypatch, "R01_PROD")
    rule = {
        "id": "confirm-prod-writes",
        "database": "*PROD*",
        "tool": "03_Batch_Operations",
        "action": "require_confirm",
    }
    d = _dispatcher(sp.SecurityPolicy(rules=[rule]), [])
    args = {
        "object_type": "measures",
        "batch_operation": "update",
        "items": [{"table_name": "T", "measure_name": "M"}],
    }
    out = d.dispatch("03_Batch_Operations", args)
    assert (
        out["error_type"] == "policy_confirmation_required" and out["rule"] == "confirm-prod-writes"
    )
    assert (
        d.dispatch("03_Batch_Operations", {**args, "confirm_destructive": True})["success"] is True
    )
    # "true" (a string) is not the break-glass boolean
    assert (
        d.dispatch("03_Batch_Operations", {**args, "confirm_destructive": "true"})["error_type"]
        == "policy_confirmation_required"
    )


def test_object_pattern_narrows_the_rule(monkeypatch):
    _names(monkeypatch, "PROD")
    rule = {"database": "*", "operation": "*.delete", "object": "Fact*", "action": "deny"}
    d = _dispatcher(sp.SecurityPolicy(rules=[rule]), [])
    assert (
        d.dispatch(
            "02_Model_Operations",
            {"object_type": "table", "operation": "delete", "table_name": "FactSales"},
        )["error_type"]
        == "policy_denied"
    )
    assert (
        d.dispatch(
            "02_Model_Operations",
            {"object_type": "table", "operation": "delete", "table_name": "DimDate"},
        )["success"]
        is True
    )
    # a Table[Column] spelling and batch items are searched too
    assert (
        d.dispatch(
            "02_Model_Operations",
            {
                "object_type": "column",
                "operation": "delete",
                "table_name": "FactSales",
                "column_name": "A",
            },
        )["error_type"]
        == "policy_denied"
    )
    assert (
        d.dispatch(
            "03_Batch_Operations",
            {
                "object_type": "measures",
                "batch_operation": "delete",
                "items": [{"table_name": "FactSales", "measure_name": "M"}],
            },
        )["error_type"]
        == "policy_denied"
    )
    rule2 = {**rule, "object": "FactSales[A*]"}
    d = _dispatcher(sp.SecurityPolicy(rules=[rule2]), [])
    assert (
        d.dispatch(
            "02_Model_Operations",
            {
                "object_type": "column",
                "operation": "delete",
                "table_name": "FactSales",
                "column_name": "Amount",
            },
        )["error_type"]
        == "policy_denied"
    )


def test_a_rule_can_deny_reads_of_a_tool(monkeypatch):
    _names(monkeypatch, "PROD")
    d = _dispatcher(
        sp.SecurityPolicy(rules=[{"database": "PROD", "tool": "04_Run_DAX", "action": "deny"}]), []
    )
    assert d.dispatch("04_Run_DAX", {"query": "EVALUATE x"})["error_type"] == "policy_denied"
    assert (
        d.dispatch("02_Model_Operations", {"object_type": "table", "operation": "list"})["success"]
        is True
    )


def test_unresolvable_database_fails_closed_for_a_database_rule(monkeypatch):
    _names(monkeypatch)  # nothing resolves (not connected / lookup failed)
    d = _dispatcher(sp.SecurityPolicy(rules=[PROD_DENY]), [])
    out = d.dispatch(
        "02_Model_Operations", {"object_type": "table", "operation": "delete", "table_name": "T"}
    )
    assert out["error_type"] == "policy_denied"
    # a rule that names no database never needs the lookup
    monkeypatch.setattr(sp, "_current_database_names", lambda: pytest.fail("looked up"))
    d = _dispatcher(sp.SecurityPolicy(rules=[{"tool": "04_Run_DAX", "action": "deny"}]), [])
    assert d.dispatch("04_Run_DAX", {"query": "x"})["error_type"] == "policy_denied"


def test_first_matching_rule_wins_and_unknown_action_denies(monkeypatch):
    _names(monkeypatch, "PROD")
    rules = [
        {"id": "a", "database": "PROD", "tool": "04_Run_DAX", "action": "require_confirm"},
        {"id": "b", "database": "PROD", "tool": "04_Run_DAX", "action": "deny"},
    ]
    d = _dispatcher(sp.SecurityPolicy(rules=rules), [])
    assert d.dispatch("04_Run_DAX", {"query": "x"})["rule"] == "a"
    assert (
        d.dispatch("04_Run_DAX", {"query": "x", "confirm_destructive": True})["success"] is True
    )  # rule a satisfied: b is not consulted
    d = _dispatcher(
        sp.SecurityPolicy(rules=[{"id": "typo", "tool": "04_Run_DAX", "action": "alow"}]), []
    )
    assert d.dispatch("04_Run_DAX", {"query": "x"})["error_type"] == "policy_denied"


def test_policy_runs_before_the_read_only_and_destructive_gates(monkeypatch):
    _names(monkeypatch, "PROD")
    d = _dispatcher(sp.SecurityPolicy(rules=[PROD_DENY]), [])
    d._read_only = True
    d._confirm_destructive = True
    out = d.dispatch(
        "02_Model_Operations", {"object_type": "table", "operation": "delete", "table_name": "T"}
    )
    assert out["error_type"] == "policy_denied"  # not read_only_mode / confirmation_required


def test_a_failing_rule_fails_closed(monkeypatch):
    def boom():
        raise RuntimeError("lookup exploded")

    monkeypatch.setattr(sp, "_current_database_names", boom)
    d = _dispatcher(sp.SecurityPolicy(rules=[PROD_DENY]), [])
    out = d.dispatch(
        "02_Model_Operations", {"object_type": "table", "operation": "delete", "table_name": "T"}
    )
    assert out["error_type"] == "policy_denied"


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def test_load_policy_reads_the_security_keys():
    cfg = {"security.allow_data_read": False, "security.rules": [PROD_DENY, "not a rule"]}
    pol = sp.load_policy(SimpleNamespace(get=lambda k, d=None: cfg.get(k, d)))
    assert pol.allow_data_read is False and len(pol.rules) == 1 and pol.invalid_rules == 1
    assert sp.load_policy(SimpleNamespace(get=lambda k, d=None: d)).active is False
    off = sp.load_policy(
        SimpleNamespace(get=lambda k, d=None: "false" if k == "security.allow_data_read" else d)
    )
    assert off.allow_data_read is False


def test_dispatcher_picks_the_policy_up_from_config_and_on_reload(monkeypatch):
    import server.dispatch as dispatch_mod

    state = {"security.allow_data_read": True, "security.rules": []}
    real = dispatch_mod.config.get
    monkeypatch.setattr(
        dispatch_mod.config, "get", lambda k, d=None: state[k] if k in state else real(k, d)
    )
    d = ToolDispatcher()
    assert d._policy.active is False
    state["security.allow_data_read"] = False
    d.refresh_security_flags()  # what config.reload() triggers
    assert d._policy.allow_data_read is False
    state["security.allow_data_read"] = True
    state["security.rules"] = [PROD_DENY]
    d.refresh_security_flags()
    assert len(d._policy.rules) == 1


def test_default_config_is_permissive():
    import json
    from pathlib import Path

    cfg = json.loads(
        (Path(__file__).parent.parent / "config" / "default_config.json").read_text(
            encoding="utf-8"
        )
    )
    assert cfg["security"]["allow_data_read"] is True and cfg["security"]["rules"] == []


def test_operation_spellings_and_object_collection():
    s = sp.operation_spellings(
        "02_Model_Operations", {"object_type": "table", "operation": "safe_delete"}
    )
    assert {"delete", "table.delete", "table/delete", "safe_delete"} <= s
    s = sp.operation_spellings(
        "03_Batch_Operations", {"object_type": "tables", "batch_operation": "delete"}
    )
    assert {"tables.delete", "table.delete"} <= s
    s = sp.operation_spellings(
        "03_Batch_Operations", {"changeset": {"columns": {"delete": [{"x": 1}], "create": []}}}
    )
    assert "column.delete" in s and "column.create" not in s
    assert "Sales[Amount]" in sp.named_objects({"table_name": "Sales", "column_name": "Amount"})
