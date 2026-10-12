"""F-07: RLS audit engine (core/validation/rls_audit.py) against a fake query runner."""

from __future__ import annotations

import re

import pytest

from core.validation import rls_audit as ra

UNFILTERED = {"Customer": 100, "Sales": 1000, "Product": 50}
_IDENT_RE = re.compile(r'\[Owner\] = "((?:[^"]|"")*)"')


def _fake(grants, *, open_for=()):
    """Fake engine. ``grants`` maps identity -> visible rows of Customer (Sales = 10x);
    identities in ``open_for`` see every row (a broken, fail-open filter)."""

    calls = []

    def run(query: str):
        calls.append(query)
        tables = re.findall(r"COUNTROWS\('([^']+)'\)", query)
        if "CALCULATETABLE" not in query:
            return {
                "success": True,
                "rows": [{f"[t{i}]": UNFILTERED[t] for i, t in enumerate(tables)}],
            }
        m = _IDENT_RE.search(query)
        ident = m.group(1).replace('""', '"') if m else None
        if ident in open_for:
            n = UNFILTERED["Customer"]
        else:
            n = grants.get(ident, 0)
        per = {"Customer": n, "Sales": n * 10, "Product": 50}
        return {
            "success": True,
            "rows": [{f"[t{i}]": (per[t] or None) for i, t in enumerate(tables)}],
        }

    run.calls = calls
    return run


ROLE = {
    "name": "Advisor",
    "members_count": 3,
    "table_permissions": [
        {"table": "Customer", "filter_expression": "Customer[Owner] = USERPRINCIPALNAME()"}
    ],
}


def test_probe_set_has_every_identity_class():
    labels = {p["label"]: p for p in ra.build_probes(["Jane@x.com"])}
    assert set(labels) == {"known", "zero_grant", "empty", "case_variant", "b2b_ext"}
    assert labels["empty"]["identity"] == ""
    assert labels["case_variant"]["identity"] == "JANE@X.COM"
    assert "#EXT#" in labels["b2b_ext"]["identity"]
    # no known UPN: only the identities that need none
    assert {p["label"] for p in ra.build_probes([])} == {"zero_grant", "empty"}


def test_empty_identity_is_substituted_not_left_alone():
    out = ra.rewrite_filters_for_identity(ROLE["table_permissions"], "")
    assert out[0]["filter_expression"] == 'Customer[Owner] = ""'
    out = ra.rewrite_filters_for_identity(ROLE["table_permissions"], 'a"b@x.com')
    assert out[0]["filter_expression"] == 'Customer[Owner] = "a""b@x.com"'


def test_healthy_role_has_no_critical_finding():
    run = _fake({"jane@x.com": 7, "JANE@X.COM": 7})
    res = ra.run_rls_audit([ROLE], run, known_users=["jane@x.com"])
    assert res["simulation"] == "filter_rewrite" and res["approximation"]["exact"] is False
    assert res["fail_open"] is False and res["summary"]["critical"] == 0
    known = res["roles"][0]["results"]["known"]["tables"]["Customer"]
    assert known == {"visible": 7, "unfiltered": 100}
    assert res["roles"][0]["results"]["zero_grant"]["tables"]["Customer"]["visible"] == 0
    # B2B form sees nothing while the known identity sees rows -> informational
    assert [f["code"] for f in res["findings"]] == ["b2b_identity_mismatch"]
    ok, msgs = ra.audit_passes(res)
    assert ok and msgs == []


def test_fail_open_for_unknown_identity_is_critical():
    run = _fake({"jane@x.com": 7, "JANE@X.COM": 7}, open_for=("no.such.user@rls-audit.invalid", ""))
    res = ra.run_rls_audit([ROLE], run, known_users=["jane@x.com"])
    codes = {(f["code"], f.get("probe")) for f in res["findings"]}
    assert ("sees_everything", "zero_grant") in codes
    assert ("fail_open", "empty") in codes
    assert res["fail_open"] is True
    ok, msgs = ra.audit_passes(res)
    assert not ok and any("sees_everything" in m for m in msgs)
    # a lower threshold is stricter, an allow-list can waive a code
    assert ra.audit_passes(res, fail_on="warn")[0] is False
    ok2, _ = ra.audit_passes(res, allow=["sees_everything", "fail_open"])
    assert ok2


def test_fail_closed_for_everyone_and_case_mismatch():
    run = _fake({"JANE@X.COM": 2})  # the known identity matches nobody; the variant does
    res = ra.run_rls_audit([ROLE], run, known_users=["jane@x.com"])
    codes = {f["code"] for f in res["findings"]}
    assert {"fail_closed_for_everyone", "case_sensitivity_mismatch"} <= codes


def test_member_and_static_filter_findings():
    role = {
        "name": "Static",
        "members_count": 0,
        "table_permissions": [{"table": "Customer", "filter_expression": "TRUE()"}],
    }
    res = ra.run_rls_audit([role], _fake({}), known_users=[])
    codes = {f["code"] for f in res["findings"]}
    assert {"role_without_members", "filter_allows_all", "static_filter"} <= codes
    assert res["known_identity_missing"]
    # static filters are never reported as fail-open (every identity sees the same rows by design)
    assert "fail_open" not in codes


def test_unknown_role_and_role_subset():
    res = ra.run_rls_audit([ROLE], _fake({}), role_names=["Advisor", "Nope"], known_users=[])
    assert res["unknown_roles"] == ["Nope"] and res["roles_audited"] == 1
    ok, msgs = ra.audit_passes(res)
    assert not ok and any("Nope" in m for m in msgs)


def test_query_error_is_reported_not_swallowed():
    def run(q):
        return {"success": False, "error": "boom"}

    res = ra.run_rls_audit([ROLE], run, known_users=["jane@x.com"])
    assert res["roles"][0]["error"].startswith("unfiltered counts failed")
    assert ra.audit_passes(res)[0] is False


def _rel(frm, to, bidi=False, fc="Many", tc="One", active=True):
    return {
        "fromTable": frm,
        "toTable": to,
        "fromCardinality": fc,
        "toCardinality": tc,
        "crossFilteringBehavior": "BothDirections" if bidi else "OneDirection",
        "isActive": active,
    }


def test_relationship_paths_single_direction_vs_bidirectional():
    rels = [
        _rel("Sales", "Customer"),  # Customer filters Sales
        _rel("Sales", "Product", bidi=True),  # Sales filters Product (leak) because bidirectional
        _rel("Sales", "Date", active=False),  # inactive: ignored
    ]
    out = ra.analyze_relationship_paths(["Customer"], rels)
    assert out["reached_single_direction"] == ["Sales"]
    assert out["reached_only_via_bidirectional"] == ["Product"]
    assert {"from": "Sales", "to": "Product"} in out["bidirectional_hops"]


def test_bidirectional_leak_flagged_in_audit_and_counts_reached_tables():
    rels = [_rel("Sales", "Customer"), _rel("Sales", "Product", bidi=True)]
    run = _fake({"jane@x.com": 7})
    res = ra.run_rls_audit([ROLE], run, known_users=["jane@x.com"], relationships=rels)
    assert "bidirectional_leak_path" in {f["code"] for f in res["findings"]}
    role = res["roles"][0]
    assert role["relationship_analysis"]["reached_only_via_bidirectional"] == ["Product"]
    assert "Product" in role["unfiltered"]


def test_audit_from_connection_not_connected(monkeypatch):
    from unittest.mock import MagicMock

    import core.infrastructure.connection_state as cs_mod

    cs = MagicMock()
    cs.is_connected.return_value = False
    monkeypatch.setattr(cs_mod, "connection_state", cs)
    assert ra.audit_from_connection({})["error_type"] == "not_connected"


def test_audit_from_connection_uses_manager_and_executor(monkeypatch):
    from unittest.mock import MagicMock

    import core.infrastructure.connection_state as cs_mod

    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.rls_manager.list_roles.return_value = {"success": True, "roles": [ROLE]}
    run = _fake({"jane@x.com": 7, "JANE@X.COM": 7})
    cs.query_executor.validate_and_execute_dax.side_effect = lambda q, **k: run(q)
    monkeypatch.setattr(cs_mod, "connection_state", cs)
    monkeypatch.setattr(ra, "_fetch_relationships", lambda: None)
    res = ra.audit_from_connection({"as_user": "jane@x.com", "roles": ["Advisor"]})
    assert res["success"] and res["roles_audited"] == 1 and res["fail_open"] is False


@pytest.mark.parametrize(
    "raw,expected", [("a@x.com, b@x.com", ["a@x.com", "b@x.com"]), (None, []), (["a"], ["a"])]
)
def test_split_users(raw, expected):
    assert ra._split_users(raw) == expected
