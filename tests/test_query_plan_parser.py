"""Tests for core.query_plan.parser."""

from pathlib import Path

from core.query_plan.parser import parse_logical_plan, parse_xmsql


def test_parse_logical_plan_returns_tree():
    text = Path("tests/fixtures/query_plan_sample.txt").read_text(encoding="utf-8")
    tree = parse_logical_plan(text)
    assert tree["operator"] == "ROOT"
    assert any(child["operator"] == "GroupSemiJoin" for child in tree["children"])


def test_parse_logical_plan_captures_depth():
    text = Path("tests/fixtures/query_plan_sample.txt").read_text(encoding="utf-8")
    tree = parse_logical_plan(text)
    gsj = next(c for c in tree["children"] if c["operator"] == "GroupSemiJoin")
    assert any(c["operator"] == "Scan_Vertipaq" for c in gsj["children"])


def test_parse_logical_plan_captures_callback_data_id():
    text = Path("tests/fixtures/query_plan_sample.txt").read_text(encoding="utf-8")
    tree = parse_logical_plan(text)
    sumx = next(c for c in tree["children"] if c["operator"] == "SumX")
    assert any(c["operator"] == "CallbackDataID" for c in sumx["children"])


def test_parse_xmsql_extracts_select_from():
    sample = "SELECT 'Sales'[Year], SUM('Sales'[Amount])\nFROM 'Sales'\nWHERE 'Sales'[Year]=2024"
    parsed = parse_xmsql(sample)
    assert parsed["select_count"] >= 1
    assert "Sales" in parsed["from_tables"]
