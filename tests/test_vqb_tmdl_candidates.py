"""WP-P wave 2 -- offline measure lookup reads each TMDL file once (P-15)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.debug.visual_query_builder import VisualQueryBuilder

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"


@pytest.fixture
def builder(tmp_path):
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return VisualQueryBuilder(str(root / "RPT-Sales.Report")), root


def _reads(monkeypatch):
    counter = {"n": 0}
    real = Path.read_text

    def spy(self, *a, **k):
        if self.suffix == ".tmdl":
            counter["n"] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy)
    return counter


def test_lookup_result_is_unchanged(builder):
    b, _root = builder
    found = b._get_measure_from_tmdl("Total Amount")
    assert found is not None and "SUM(Sales[Amount])" in found.expression
    assert b._get_measure_from_tmdl("total amount").expression == found.expression  # case-insens
    assert b._get_measure_from_tmdl("Does Not Exist") is None
    gate = b._get_measure_from_tmdl("Show In Slot")
    assert gate is not None and "HASONEVALUE" in gate.expression


def test_files_are_read_once_across_lookups_and_only_candidates_are_parsed(builder, monkeypatch):
    b, _root = builder
    n_files = len(list(b._find_semantic_model_path().glob("**/*.tmdl")))
    b._get_measure_from_tmdl("Total Amount")  # warms the text cache
    counter = _reads(monkeypatch)
    for name in ("Total Amount", "Amount Guarded", "Show In Slot", "Nope"):
        b._get_measure_from_tmdl(name)
    # old: up to n_files reads per lookup; now: only candidate files are parsed
    assert counter["n"] <= 8 < 4 * n_files


def test_edited_file_is_reread(builder):
    b, root = builder
    assert "SUM(Sales[Amount])" in b._get_measure_from_tmdl("Total Amount").expression
    table = root / "MDL-Core.SemanticModel" / "definition" / "tables" / "Sales.tmdl"
    table.write_text(
        table.read_text(encoding="utf-8").replace("SUM(Sales[Amount])", "AVERAGE(Sales[Amount])"),
        encoding="utf-8",
    )
    assert "AVERAGE(Sales[Amount])" in b._get_measure_from_tmdl("Total Amount").expression


def test_new_file_and_non_ascii_names_are_found(builder):
    b, root = builder
    assert b._get_measure_from_tmdl("Caf\u00e9 Total") is None
    extra = root / "MDL-Core.SemanticModel" / "definition" / "tables" / "Extra.tmdl"
    extra.write_text("table Extra\n\tmeasure 'Caf\u00e9 Total' = 42\n", encoding="utf-8")
    found = b._get_measure_from_tmdl("Caf\u00e9 Total")
    assert found is not None and found.expression.strip() == "42"


def test_unreadable_file_does_not_hide_other_candidates(builder):
    b, root = builder
    bad = root / "MDL-Core.SemanticModel" / "definition" / "tables" / "Broken.tmdl"
    bad.write_bytes(b"\xff\xfe\x00 not utf8 \xff")
    assert b._get_measure_from_tmdl("Total Amount") is not None
