"""Review G-01..G-04 / G-13: TMDL bulk rename + find/replace are transactional and safe.

Each scenario reproduces a probe (`probes/G/p06_bulk_editor.py`) that silently
corrupted or deleted model files on the pre-review editor while reporting
``success: true``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import core.tmdl.bulk_editor as be
from core.tmdl.bulk_editor import TmdlBulkEditor
from core.tmdl.unified_parser import UnifiedTmdlParser
from core.tmdl.validator import TmdlValidator

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"


def _editor() -> TmdlBulkEditor:
    return TmdlBulkEditor({"backup_before_edit": False})


def _model(tmp_path: Path, tables: dict, extra: dict = None, newline: str = "\n") -> Path:
    d = tmp_path / "M.SemanticModel" / "definition"
    (d / "tables").mkdir(parents=True)

    def w(p: Path, text: str) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.replace("\n", newline).encode("utf-8"))

    w(d / "database.tmdl", "database\n\tcompatibilityLevel: 1600\n")
    refs = "".join(f"ref table {n if ' ' not in n else repr(n)}\n" for n in tables)
    w(d / "model.tmdl", "model Model\n\tculture: en-US\n\n" + refs)
    for name, text in tables.items():
        w(d / "tables" / f"{name}.tmdl", text)
    for rel, text in (extra or {}).items():
        w(d / rel, text)
    return d


def _read(d: Path, rel: str) -> str:
    return (d / rel).read_bytes().decode("utf-8")


def _snapshot(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*.tmdl")}


def _parse(d: Path):
    return UnifiedTmdlParser(str(d)).parse_full_model()


SALES = (
    "table Sales\n\tlineageTag: s\n\n"
    "\t/// the [Amount] column total\n"
    "\tmeasure Revenue = SUM(Sales[Amount])\n\t\tformatString: 0\n\n"
    "\tcolumn Amount\n\t\tdataType: double\n\t\tsourceColumn: Amount\n\t\tsortByColumn: Key\n\n"
    "\tcolumn Key\n\t\tdataType: int64\n\t\tsourceColumn: Key\n\n"
    "\tcolumn Doubled = [Amount] * 2\n\t\tdataType: double\n\n"
    "\thierarchy H\n\t\tlevel L\n\t\t\tcolumn: Amount\n\n"
    "\tpartition Sales = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n"
    '\t\t\t\t\tS = Table.FromRows({{1, 2}}, {"Amount", "Key"})\n\t\t\t\tin\n\t\t\t\t\tS\n'
)
METRICS = (
    "table 'm Metrics'\n\tlineageTag: m\n\n"
    "\tmeasure Amount = [Revenue] + 1\n\n"
    "\tmeasure Ratio = DIVIDE([Amount], SUM(Sales[Amount]))\n\t\tdisplayFolder: Amount stuff\n\n"
    '\tmeasure Label = "[Amount] is " & FORMAT([Amount], "0") // [Amount] note\n\n'
    "\tpartition 'm Metrics' = calculated\n\t\tmode: import\n\t\tsource = ROW(\"x\", 1)\n"
)
RELS = "relationship r1\n\tfromColumn: Sales.Key\n\ttoColumn: 'Dim Key'.Key\n"
DIM = "table 'Dim Key'\n\tcolumn Key\n\t\tdataType: int64\n\t\tsourceColumn: Key\n"
ROLE = (
    "role R\n\tmodelPermission: read\n\n\ttablePermission Sales = [Amount] > 0 && Sales[Key] > 0\n"
)


@pytest.fixture
def model(tmp_path):
    return _model(
        tmp_path,
        {"Sales": SALES, "m Metrics": METRICS, "Dim Key": DIM},
        {"relationships.tmdl": RELS, "roles/R.tmdl": ROLE},
    )


# ─── G-01: case-only rename and rename onto an existing table ───────


def test_case_only_table_rename_keeps_the_table(tmp_path):
    d = _model(tmp_path, {"sales x": "table 'sales x'\n\tmeasure A = 1\n"})
    r = _editor().bulk_rename(
        str(d),
        [{"object_type": "table", "old_name": "sales x", "new_name": "Sales X"}],
        dry_run=False,
    )
    assert r.success, r.errors
    names = [p.name for p in (d / "tables").iterdir()]
    assert names == ["Sales X.tmdl"]
    assert _read(d, "tables/Sales X.tmdl").startswith("table 'Sales X'")
    assert "ref table 'Sales X'" in _read(d, "model.tmdl")


def test_rename_onto_existing_table_is_refused_and_nothing_changes(model):
    before = _snapshot(model)
    r = _editor().bulk_rename(
        str(model),
        [{"object_type": "table", "old_name": "Dim Key", "new_name": "sales"}],
        dry_run=False,
    )
    assert not r.success
    assert "already exists" in " ".join(r.errors)
    assert _snapshot(model) == before


# ─── G-02: names that are not simple identifiers ────────────────────


@pytest.mark.parametrize("new", ["2024 Total", "a\\b", "O'Brien \\1 \\g<0>"])
def test_rename_to_digit_backslash_or_quote_name(model, new):
    r = _editor().bulk_rename(
        str(model),
        [{"object_type": "measure", "table_name": "Sales", "old_name": "Revenue", "new_name": new}],
        dry_run=False,
    )
    assert r.success, r.errors
    sales = next(t for t in _parse(model).tables if t.name == "Sales")
    assert new in [m.name for m in sales.measures]
    metrics = next(t for t in _parse(model).tables if t.name == "m Metrics")
    amount = next(m for m in metrics.measures if m.name == "Amount")
    assert amount.expression == f"[{new}] + 1"
    assert TmdlValidator().validate_syntax(str(model)).is_valid


# ─── G-03: references ───────────────────────────────────────────────


def test_measure_rename_leaves_columns_strings_and_comments_alone(model):
    r = _editor().bulk_rename(
        str(model),
        [
            {
                "object_type": "measure",
                "table_name": "m Metrics",
                "old_name": "Amount",
                "new_name": "Net",
            }
        ],
        dry_run=False,
    )
    assert r.success, r.errors
    text = _read(model, "tables/m Metrics.tmdl")
    assert "DIVIDE([Net], SUM(Sales[Amount]))" in text
    assert '"[Amount] is " & FORMAT([Net], "0") // [Amount] note' in text
    assert "displayFolder: Amount stuff" in text
    sales = _read(model, "tables/Sales.tmdl")
    assert "/// the [Amount] column total" in sales and "SUM(Sales[Amount])" in sales


def test_column_rename_updates_structural_and_dax_refs(model):
    r = _editor().bulk_rename(
        str(model),
        [
            {
                "object_type": "column",
                "table_name": "Sales",
                "old_name": "Key",
                "new_name": "Sales Key",
            }
        ],
        dry_run=False,
    )
    assert r.success, r.errors
    assert "fromColumn: Sales.'Sales Key'" in _read(model, "relationships.tmdl")
    sales = _read(model, "tables/Sales.tmdl")
    assert "sortByColumn: 'Sales Key'" in sales
    assert "sourceColumn: Key" in sales  # the source column is NOT renamed
    assert "Sales[Sales Key] > 0" in _read(model, "roles/R.tmdl")
    assert TmdlValidator().validate_syntax(str(model)).is_valid


def test_column_rename_row_context_and_hierarchy(model):
    r = _editor().bulk_rename(
        str(model),
        [
            {
                "object_type": "column",
                "table_name": "Sales",
                "old_name": "Amount",
                "new_name": "Net Amount",
            }
        ],
        dry_run=False,
    )
    assert r.success, r.errors
    sales = _read(model, "tables/Sales.tmdl")
    assert "column Doubled = [Net Amount] * 2" in sales  # calc column, row context
    assert "\t\t\tcolumn: 'Net Amount'" in sales  # hierarchy level
    assert "SUM(Sales[Net Amount])" in sales
    assert "tablePermission Sales = [Net Amount] > 0" in _read(model, "roles/R.tmdl")
    # measure [Amount] in m Metrics is a MEASURE reference, never the column
    assert "measure Amount = [Revenue] + 1" in _read(model, "tables/m Metrics.tmdl")
    assert "SUM(Sales[Net Amount])" in _read(model, "tables/m Metrics.tmdl")


def test_unquoted_measure_and_table_names_are_renamed(model):
    r = _editor().bulk_rename(
        str(model),
        [{"object_type": "table", "old_name": "Sales", "new_name": "Fact Sales"}],
        dry_run=False,
    )
    assert r.success, r.errors
    assert (model / "tables" / "Fact Sales.tmdl").exists()
    assert not (model / "tables" / "Sales.tmdl").exists()
    assert "fromColumn: 'Fact Sales'.Key" in _read(model, "relationships.tmdl")
    assert "ref table 'Fact Sales'" in _read(model, "model.tmdl")
    assert "tablePermission 'Fact Sales' = [Amount] > 0 && 'Fact Sales'[Key] > 0" in _read(
        model, "roles/R.tmdl"
    )
    assert "SUM('Fact Sales'[Amount])" in _read(model, "tables/m Metrics.tmdl")
    assert TmdlValidator().validate_syntax(str(model)).is_valid


def test_missing_object_fails_and_writes_nothing(model):
    before = _snapshot(model)
    r = _editor().bulk_rename(
        str(model),
        [
            {
                "object_type": "measure",
                "table_name": "Sales",
                "old_name": "Revenue",
                "new_name": "Rev",
            },
            {"object_type": "measure", "table_name": "Sales", "old_name": "Nope", "new_name": "X"},
        ],
        dry_run=False,
    )
    assert not r.success
    assert _snapshot(model) == before


def test_collision_with_existing_measure_is_refused(model):
    r = _editor().bulk_rename(
        str(model),
        [
            {
                "object_type": "measure",
                "table_name": "Sales",
                "old_name": "Revenue",
                "new_name": "ratio",
            }
        ],
    )
    assert not r.success and "already exists" in " ".join(r.errors)


def test_dry_run_writes_nothing(model):
    before = _snapshot(model)
    r = _editor().bulk_rename(
        str(model),
        [
            {
                "object_type": "measure",
                "table_name": "Sales",
                "old_name": "Revenue",
                "new_name": "Rev",
            }
        ],
        dry_run=True,
    )
    assert r.success and r.files_modified >= 2 and r.preview
    assert _snapshot(model) == before


def test_validation_regression_aborts_the_write(model, monkeypatch):
    before = _snapshot(model)
    real = be.TmdlBulkEditor._new_validator
    calls = {"n": 0}

    class Broken(TmdlValidator):
        def validate_syntax(self, path):
            calls["n"] += 1
            res = super().validate_syntax(path)
            if calls["n"] == 2:  # the post-edit copy
                from core.tmdl.validator import ValidationError, ValidationSeverity

                res.errors.append(
                    ValidationError(
                        "x.tmdl", 1, 0, ValidationSeverity.ERROR, "TMDL999", "synthetic"
                    )
                )
                res.is_valid = False
            return res

    monkeypatch.setattr(be.TmdlBulkEditor, "_new_validator", lambda self: Broken())
    r = _editor().bulk_rename(
        str(model),
        [
            {
                "object_type": "measure",
                "table_name": "Sales",
                "old_name": "Revenue",
                "new_name": "Rev",
            }
        ],
        dry_run=False,
    )
    assert not r.success
    assert _snapshot(model) == before
    assert real is not None


def test_write_failure_restores_already_written_files(model, monkeypatch):
    before = _snapshot(model)
    import core.utilities.pbip_utils as pu

    real = pu.save_json_minimal
    state = {"n": 0}

    def flaky(path, new, orig, raise_on_error=False):
        state["n"] += 1
        if state["n"] == 2:
            raise PermissionError("locked")
        return real(path, new, orig, raise_on_error=raise_on_error)

    monkeypatch.setattr(pu, "save_json_minimal", flaky)
    r = _editor().bulk_rename(
        str(model),
        [
            {
                "object_type": "measure",
                "table_name": "Sales",
                "old_name": "Revenue",
                "new_name": "Rev",
            }
        ],
        dry_run=False,
    )
    assert not r.success and "restored" in " ".join(r.errors)
    assert _snapshot(model) == before


# ─── G-04: cascade_to_reports ───────────────────────────────────────


@pytest.fixture
def golden(tmp_path):
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return root


def _visual_text(root: Path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in root.rglob("visual.json"))


def test_cascade_true_renames_bound_reports_after_the_tmdl_commit(golden):
    assert "Show In Slot" in _visual_text(golden)
    r = _editor().bulk_rename(
        str(golden / "MDL-Core.SemanticModel"),
        [
            {
                "object_type": "measure",
                "table_name": "m Metrics",
                "old_name": "Show In Slot",
                "new_name": "Slot Gate",
            }
        ],
        dry_run=False,
        cascade_to_reports=True,
    )
    assert r.success, (r.errors, r.cascade)
    assert r.cascade["success"] and r.cascade["reports"]
    text = _visual_text(golden)
    assert "Show In Slot" not in text and "Slot Gate" in text


def test_cascade_path_must_resolve_to_a_bound_report(golden, tmp_path):
    r = _editor().bulk_rename(
        str(golden / "MDL-Core.SemanticModel"),
        [
            {
                "object_type": "measure",
                "table_name": "m Metrics",
                "old_name": "Show In Slot",
                "new_name": "Slot Gate",
            }
        ],
        dry_run=False,
        cascade_to_reports=str(golden / "RPT-Remote.Report"),
    )
    assert not r.success and "no report bound" in " ".join(r.errors)


def test_cascade_refuses_table_renames(golden):
    before = _snapshot(golden / "MDL-Core.SemanticModel" / "definition")
    r = _editor().bulk_rename(
        str(golden / "MDL-Core.SemanticModel"),
        [{"object_type": "table", "old_name": "Sales", "new_name": "Fact"}],
        dry_run=False,
        cascade_to_reports=True,
    )
    assert not r.success
    assert _snapshot(golden / "MDL-Core.SemanticModel" / "definition") == before


def test_cascade_skipped_when_tmdl_fails(golden):
    text = _visual_text(golden)
    r = _editor().bulk_rename(
        str(golden / "MDL-Core.SemanticModel"),
        [
            {
                "object_type": "measure",
                "table_name": "m Metrics",
                "old_name": "Show In Slot",
                "new_name": "Slot Gate",
            },
            {
                "object_type": "measure",
                "table_name": "m Metrics",
                "old_name": "Nope",
                "new_name": "X",
            },
        ],
        dry_run=False,
        cascade_to_reports=True,
    )
    assert not r.success and r.cascade is None
    assert _visual_text(golden) == text


# ─── G-13: scoped find/replace ──────────────────────────────────────


def test_find_replace_columns_does_not_touch_partition_or_source_column(tmp_path):
    d = _model(tmp_path, {"Sales": SALES})
    r = _editor().replace_in_measures(str(d), "Amount", "Amt", target="columns", dry_run=False)
    assert r.success, r.errors
    text = _read(d, "tables/Sales.tmdl")
    assert "column Doubled = [Amt] * 2" in text  # the calc-column expression
    assert "sourceColumn: Amount" in text
    assert '{"Amount", "Key"}' in text  # partition M untouched
    assert "SUM(Sales[Amount])" in text  # a measure body is not a column target


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_find_replace_preserves_line_endings(tmp_path, newline):
    d = _model(tmp_path, {"Sales": SALES}, newline=newline)
    r = _editor().replace_in_measures(str(d), "SUM", "SUMX_", target="measures", dry_run=False)
    assert r.success and r.replacements_made == 1
    raw = (d / "tables" / "Sales.tmdl").read_bytes()
    if newline == "\n":
        assert b"\r\n" not in raw
    else:
        assert raw.count(b"\r\n") == raw.count(b"\n")


def test_find_replace_skips_dax_strings_and_comments(tmp_path):
    d = _model(tmp_path, {"m Metrics": METRICS})
    r = _editor().replace_in_measures(str(d), "[Amount]", "[Net]", dry_run=False)
    assert r.success
    text = _read(d, "tables/m Metrics.tmdl")
    assert '"[Amount] is " & FORMAT([Net], "0") // [Amount] note' in text
    assert "displayFolder: Amount stuff" in text


def test_find_replace_object_and_file_scopes(tmp_path):
    d = _model(tmp_path, {"m Metrics": METRICS})
    r = _editor().replace_in_measures(
        str(d), "Amount stuff", "Other", scope="object", dry_run=False
    )
    assert r.success and "displayFolder: Other" in _read(d, "tables/m Metrics.tmdl")
    bad = _editor().replace_in_measures(str(d), "x", "y", scope="file", target="measures")
    assert not bad.success
    r2 = _editor().replace_in_measures(
        str(d), "lineageTag: m", "lineageTag: n", scope="file", target="all", dry_run=False
    )
    assert r2.success and "lineageTag: n" in _read(d, "tables/m Metrics.tmdl")


def test_find_replace_regex_groups_and_literal_backslashes(tmp_path):
    d = _model(tmp_path, {"m Metrics": METRICS})
    r = _editor().replace_in_measures(
        str(d), r"\[Revenue\]", r"[\g<0>x]", regex=True, dry_run=False
    )
    assert r.success and "[[Revenue]x]" in _read(d, "tables/m Metrics.tmdl")
    r2 = _editor().replace_in_measures(str(d), "ROW", "\\1", target="partitions", dry_run=False)
    assert r2.success, r2.errors
    assert "source = \\1(" in _read(d, "tables/m Metrics.tmdl")
