"""Tests for 13_Query_Plan evaluate_and_log + benchmark ops."""

from server.handlers import query_plan_handler as qph


def test_evaluate_and_log_requires_dax_query():
    res = qph.handle_query_plan({"operation": "evaluate_and_log"})
    assert res["success"] is False


def test_evaluate_and_log_returns_evaluation_log_field(monkeypatch):
    sentinel = {
        "success": True,
        "capture_id": "cid-eal",
        "logical_plan_raw": "",
        "physical_plan_raw": "",
        "xmsql_queries": [],
        "evaluation_log": [{"raw": '{"input":1,"output":2}', "parsed": {"input": 1, "output": 2}}],
        "se_fe_timings": {"ms": 10},
    }
    monkeypatch.setattr(qph.capturer, "capture", lambda **kw: sentinel)
    res = qph.handle_query_plan(
        {"operation": "evaluate_and_log", "dax_query": 'EVALUATE EVALUATEANDLOG(1, "x")'}
    )
    assert res["success"] is True
    assert "evaluation_log" in res
    assert res["evaluation_log"][0]["parsed"]["output"] == 2


def test_benchmark_requires_dax_query():
    res = qph.handle_query_plan({"operation": "benchmark"})
    assert res["success"] is False


def test_benchmark_summary_shape(monkeypatch):
    sentinel = {
        "success": True,
        "summary": {
            "runs": 5,
            "min_ms": 10.0,
            "max_ms": 20.0,
            "mean_ms": 15.0,
            "median_ms": 15.0,
            "p90_ms": 19.0,
            "p95_ms": 20.0,
            "stdev_ms": 3.5,
        },
        "timings_ms": [10, 13, 15, 18, 20],
        "row_counts": [1, 1, 1, 1, 1],
        "errors": [],
        "cold_cache": True,
        "warmup_runs": 1,
    }
    monkeypatch.setattr(qph.capturer, "benchmark", lambda **kw: sentinel)
    res = qph.handle_query_plan({"operation": "benchmark", "dax_query": "EVALUATE {1}", "runs": 5})
    assert res["success"] is True
    assert res["summary"]["runs"] == 5
    assert res["summary"]["p95_ms"] == 20.0
