"""Dispatch tests for 13_Query_Plan."""

from server.handlers import query_plan_handler as qph


def test_unknown_op_errors():
    res = qph.handle_query_plan({"operation": "bogus"})
    assert res["success"] is False


def test_capture_requires_dax_query():
    res = qph.handle_query_plan({"operation": "capture"})
    assert res["success"] is False


def test_parse_with_capture_id_uses_recall(monkeypatch):
    monkeypatch.setattr(
        qph.capturer,
        "recall",
        lambda cid: {
            "capture_id": cid,
            "logical_plan_raw": "Op: x",
            "physical_plan_raw": "",
            "xmsql_queries": [],
        },
    )
    res = qph.handle_query_plan({"operation": "parse", "capture_id": "abc123"})
    assert res["success"] is True
    assert "logical_tree" in res


def test_analyze_with_inline_query_calls_capture(monkeypatch):
    sentinel = {
        "success": True,
        "capture_id": "cid",
        "logical_plan_raw": "Op: x",
        "physical_plan_raw": "",
        "xmsql_queries": [],
        "se_fe_timings": {},
    }
    monkeypatch.setattr(qph.capturer, "capture", lambda **kw: sentinel)
    res = qph.handle_query_plan({"operation": "analyze", "dax_query": 'EVALUATE ROW("x", 1)'})
    assert res["success"] is True
    assert res["verdict"] in {"ok", "warn", "hot"}


def test_list_rules_returns_catalog():
    res = qph.handle_query_plan({"operation": "list_rules"})
    assert res["success"] is True
    assert any(r["id"] == "callback_data_id_count" for r in res["rules"])
