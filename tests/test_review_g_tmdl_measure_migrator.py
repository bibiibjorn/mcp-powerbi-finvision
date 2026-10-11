"""Review G-14: migrate_measures extracts by indentation span and never destroys the target.

Pre-review (probe p08): the last measure swallowed the following ``column``
block, append mode deleted the target's calculated partition and table
annotations and injected a default ``m`` partition, multi-line ``///``
descriptions lost all but one line, every created table shared one lineageTag.
"""

from __future__ import annotations

from pathlib import Path

from core.tmdl.measure_migrator import TmdlMeasureMigrator
from core.tmdl.unified_parser import UnifiedTmdlParser
from core.tmdl.validator import TmdlValidator

SOURCE = (
    "table Src\n\tlineageTag: src\n\n"
    "\t/// first line\n\t/// second line\n"
    "\tmeasure 'Core Total' = SUM(Src[Key])\n\t\tdisplayFolder: Core\n\t\tlineageTag: m1\n\n"
    "\tmeasure Other = 2\n\t\tdisplayFolder: Misc\n\t\tlineageTag: m2\n\n"
    "\tmeasure 'Core Last' =\n\t\t\tVAR x = 1\n\t\t\tRETURN x\n\t\tdisplayFolder: Core\n"
    "\t\tlineageTag: m3\n\n"
    "\tcolumn Key\n\t\tdataType: int64\n\t\tsourceColumn: Key\n\n"
    "\tpartition Src = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n\t\t\t\t\tS = 1\n\t\t\t\tin\n\t\t\t\t\tS\n"
)
TARGET = (
    "table Tgt\n\tlineageTag: tgt\n\n"
    "\tmeasure Existing = 1\n\t\tlineageTag: e1\n\n"
    "\tcolumn Value\n\t\tdataType: string\n\t\tisNameInferred\n\t\tsourceColumn: [Value]\n\n"
    '\tpartition Tgt = calculated\n\t\tmode: import\n\t\tsource = ROW("x", 1)\n\n'
    "\tannotation keep-me = 1\n"
)


def _one(root: Path, name: str, text: str, newline: str) -> Path:
    d = root / "definition"
    (d / "tables").mkdir(parents=True)
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n", encoding="utf-8")
    (d / "model.tmdl").write_text("model Model\n\tculture: en-US\n", encoding="utf-8")
    (d / "tables" / f"{name}.tmdl").write_bytes(text.replace("\n", newline).encode("utf-8"))
    return d


def _model(tmp_path: Path, newline: str = "\n") -> Path:
    """Target model (``tables/Tgt.tmdl``); the source table lives in ANOTHER model."""
    _one(tmp_path / "src", "Src", SOURCE, newline)
    return _one(tmp_path / "tgt", "Tgt", TARGET, newline)


def _src(d: Path) -> str:
    return str(d.parent.parent / "src" / "definition" / "tables" / "Src.tmdl")


def _mig() -> TmdlMeasureMigrator:
    return TmdlMeasureMigrator({"backup_before_edit": False})


def test_last_measure_does_not_swallow_the_column(tmp_path):
    d = _model(tmp_path)
    ms = _mig().extract_measures(_src(d), "Core")
    assert [m.name for m in ms] == ["Core Total", "Core Last"]
    assert "column Key" not in ms[-1].content
    assert ms[0].content.startswith("\t/// first line\n\t/// second line\n\tmeasure")


def test_append_keeps_target_partition_columns_and_annotations(tmp_path):
    d = _model(tmp_path)
    r = _mig().migrate_measures(
        _src(d), str(d / "tables" / "Tgt.tmdl"), display_folder_filter="Core", dry_run=False
    )
    assert r.success, r.errors
    text = (d / "tables" / "Tgt.tmdl").read_text(encoding="utf-8")
    assert 'source = ROW("x", 1)' in text and "partition Tgt = calculated" in text
    assert "annotation keep-me = 1" in text
    assert "partition 'm Measure'" not in text
    assert "column Key" not in text
    model = UnifiedTmdlParser(str(d)).parse_full_model()
    tgt = next(t for t in model.tables if t.name == "Tgt")
    assert [m.name for m in tgt.measures] == ["Existing", "Core Total", "Core Last"]
    assert tgt.measures[1].description == "first line\nsecond line"
    assert [c.name for c in tgt.columns] == ["Value"]
    assert tgt.measures[1].lineage_tag == "m1"  # another model: lineage kept
    assert TmdlValidator().validate_syntax(str(d)).is_valid


def test_replace_target_replaces_only_measures(tmp_path):
    d = _model(tmp_path)
    r = _mig().migrate_measures(
        _src(d),
        str(d / "tables" / "Tgt.tmdl"),
        display_folder_filter="Misc",
        replace_target=True,
        dry_run=False,
    )
    assert r.success, r.errors
    tgt = next(t for t in UnifiedTmdlParser(str(d)).parse_full_model().tables if t.name == "Tgt")
    assert [m.name for m in tgt.measures] == ["Other"]
    assert tgt.partitions[0].source == 'ROW("x", 1)'
    assert [a.name for a in tgt.annotations] == ["keep-me"]


def test_crlf_target_stays_crlf(tmp_path):
    d = _model(tmp_path, newline="\r\n")
    r = _mig().migrate_measures(
        _src(d), str(d / "tables" / "Tgt.tmdl"), display_folder_filter="Misc", dry_run=False
    )
    assert r.success
    raw = (d / "tables" / "Tgt.tmdl").read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_duplicate_is_skipped(tmp_path):
    d = _model(tmp_path)
    (d / "tables" / "Tgt.tmdl").write_text(TARGET.replace("Existing", "Other"), encoding="utf-8")
    r = _mig().migrate_measures(
        _src(d), str(d / "tables" / "Tgt.tmdl"), display_folder_filter="Misc", dry_run=False
    )
    assert r.success and r.duplicates_skipped == 1 and r.measures_migrated == 0


def test_copy_inside_the_same_model_is_refused_as_duplicate_names(tmp_path):
    d = _model(tmp_path)
    same = d / "tables" / "Src.tmdl"
    same.write_text(SOURCE, encoding="utf-8")
    before = (d / "tables" / "Tgt.tmdl").read_bytes()
    r = _mig().migrate_measures(str(same), str(d / "tables" / "Tgt.tmdl"), "Misc", dry_run=False)
    assert not r.success
    assert "also defined in table" in " ".join(r.errors)
    assert (d / "tables" / "Tgt.tmdl").read_bytes() == before
