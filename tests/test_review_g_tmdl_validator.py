"""Review G-05: the TMDL validator catches what TOM rejects, without false alarms.

The pre-review validator returned ``is_valid=True`` for every broken tree below
and raised TMDL002 on every ``///`` description line.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import core.tmdl.tom_check as tom_check
from core.tmdl.validator import TmdlValidator

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo" / "MDL-Core.SemanticModel"


def _model(tmp_path: Path, tables: dict, extra: dict = None) -> Path:
    d = tmp_path / "definition"
    (d / "tables").mkdir(parents=True)
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n", encoding="utf-8")
    (d / "model.tmdl").write_text("model Model\n\tculture: en-US\n", encoding="utf-8")
    for name, text in tables.items():
        (d / "tables" / f"{name}.tmdl").write_text(text, encoding="utf-8")
    for rel, text in (extra or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def _codes(result):
    return sorted({e.code for e in result.errors}), sorted({w.code for w in result.warnings})


_GOOD = (
    "/// the fact\ntable T\n\tlineageTag: t1\n\n"
    "\t/// total\n\tmeasure M = SUM(T[C])\n\t\tformatString: 0\n\n"
    "\tcolumn C\n\t\tdataType: int64\n\t\tsortByColumn: K\n\n"
    "\tcolumn K\n\t\tdataType: int64\n\n"
    "\tpartition T = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n\t\t\t\t\tS = 1\n\t\t\t\tin\n\t\t\t\t\tS\n"
)


def test_clean_model_and_descriptions_raise_nothing(tmp_path):
    r = TmdlValidator().validate_syntax(str(_model(tmp_path, {"T": _GOOD})))
    assert r.is_valid, [e.message for e in r.errors]
    assert r.warnings == []


def test_golden_fixture_is_clean():
    r = TmdlValidator().validate_syntax(str(GOLDEN))
    assert r.is_valid, [(e.code, e.message) for e in r.errors]
    assert r.warnings == []


def test_orphaned_children_after_deleted_header(tmp_path):
    # `column B` header deleted -> its properties merge into column A
    text = (
        "table T\n\tcolumn A\n\t\tdataType: string\n\t\tlineageTag: a\n"
        "\t\tdataType: int64\n\t\tlineageTag: b\n"
    )
    r = TmdlValidator().validate_syntax(str(_model(tmp_path, {"T": text})))
    assert not r.is_valid
    assert "TMDL018" in _codes(r)[0]


def test_orphaned_properties_directly_under_table(tmp_path):
    text = "table T\n\t\tformatString: 0\n\t\tlineageTag: abc\n\tmeasure M = 1\n"
    r = TmdlValidator().validate_syntax(str(_model(tmp_path, {"T": text})))
    errors = _codes(r)[0]
    assert "TMDL013" in errors and "TMDL014" in errors


def test_unterminated_quote_and_column_zero_object(tmp_path):
    text = "table T\n\tcolumn 'C\n\t\tdataType: string\nmeasure X = 1\n"
    r = TmdlValidator().validate_syntax(str(_model(tmp_path, {"T": text})))
    errors = _codes(r)[0]
    assert "TMDL011" in errors and "TMDL020" in errors


def test_property_after_child_object(tmp_path):
    text = 'table T\n\tmeasure M = 1\n\t\tformatStringDefinition = "0"\n\t\tlineageTag: x\n'
    r = TmdlValidator().validate_syntax(str(_model(tmp_path, {"T": text})))
    assert "TMDL017" in _codes(r)[0]


def test_dangling_structural_references(tmp_path):
    text = "table T\n\tcolumn C\n\t\tdataType: string\n\t\tsortByColumn: Nope\n"
    rels = "relationship r1\n\tfromColumn: T.C\n\ttoColumn: 'Gone'.Key\n"
    r = TmdlValidator().validate_syntax(
        str(_model(tmp_path, {"T": text}, {"relationships.tmdl": rels}))
    )
    errors = _codes(r)[0]
    assert "TMDL030" in errors and "TMDL031" in errors


def test_dangling_query_group_and_role_table(tmp_path):
    extra = {
        "expressions.tmdl": "expression p = 1\n\tqueryGroup: Params\n",
        "roles/R.tmdl": "role R\n\tmodelPermission: read\n\ttablePermission Nope = TRUE()\n",
    }
    r = TmdlValidator().validate_syntax(str(_model(tmp_path, {"T": _GOOD}, extra)))
    errors = _codes(r)[0]
    assert "TMDL035" in errors and "TMDL038" in errors


def test_duplicate_names(tmp_path):
    text = "table T\n\tmeasure M = 1\n\tcolumn m\n\t\tdataType: string\n"
    r = TmdlValidator().validate_syntax(str(_model(tmp_path, {"T": text})))
    assert "TMDL021" in _codes(r)[0]


def test_dax_reference_to_missing_column_is_a_warning(tmp_path):
    text = "table T\n\tmeasure M = SUM(T[Missing]) + SUM('Other'[X])\n\tcolumn C\n\t\tdataType: int64\n"
    r = TmdlValidator().validate_syntax(str(_model(tmp_path, {"T": text})))
    assert r.is_valid  # Desktop opens the project; the measure is in error
    assert _codes(r)[1] == ["TMDL033"]
    assert len(r.warnings) == 2


def test_dax_strings_and_comments_are_not_references(tmp_path):
    text = (
        'table T\n\tmeasure M =\n\t\t\t// T[Nope] in a comment\n\t\t\t"T[Nope] in a string" & SUM(T[C])\n'
        "\tcolumn C\n\t\tdataType: int64\n"
    )
    r = TmdlValidator().validate_syntax(str(_model(tmp_path, {"T": text})))
    assert r.warnings == []


def test_relationships_file_is_checked(tmp_path):
    """The old validator globbed a `relationships/` DIRECTORY; the item is a file."""
    rels = "relationship r1\n\tfromColumn: T.Missing\n\ttoColumn: T.C\n"
    r = TmdlValidator().validate_syntax(
        str(_model(tmp_path, {"T": _GOOD}, {"relationships.tmdl": rels}))
    )
    assert "TMDL030" in _codes(r)[0]


def test_invalid_data_type_still_reported(tmp_path):
    text = "table T\n\tcolumn C\n\t\tdataType: text\n"
    r = TmdlValidator().validate_syntax(str(_model(tmp_path, {"T": text})))
    assert "TMDL001" in _codes(r)[0]


# ─── TOM check (seam; no CLR in unit tests) ─────────────────────────


def test_tom_udf_gate_is_classified_and_rechecked_without_functions(tmp_path, monkeypatch):
    d = tmp_path / "definition"
    shutil.copytree(GOLDEN / "definition", d)
    calls = []

    def fake(folder):
        calls.append(folder)
        if (Path(folder) / "functions.tmdl").exists():
            raise RuntimeError(
                "Compatibility level of 1702 is below the minimal compatibility level of "
                "1000000 needed for function ScaleFormat"
            )

    monkeypatch.setattr(tom_check, "_deserialize_override", fake)
    res = tom_check.tom_deserialize_check(str(d))
    assert res["ran"] and res["ok"] and res["udf_gate"]
    assert len(calls) == 2


def test_tom_real_error_fails_validation(tmp_path, monkeypatch):
    d = _model(tmp_path, {"T": _GOOD})

    def fake(folder):
        raise RuntimeError("TMDL Format Error:\n\tParsing error type - Indentation")

    monkeypatch.setattr(tom_check, "_deserialize_override", fake)
    r = TmdlValidator({"tom_check": "auto"}).validate_syntax(str(d))
    assert not r.is_valid
    assert "TMDL090" in _codes(r)[0]
    assert r.to_dict()["tom_check"]["ok"] is False


def test_tom_unavailable_is_reported_not_failed(tmp_path, monkeypatch):
    d = _model(tmp_path, {"T": _GOOD})

    def fake(folder):
        raise RuntimeError("__unavailable__:no clr")

    monkeypatch.setattr(tom_check, "_deserialize_override", fake)
    r = TmdlValidator({"tom_check": "auto"}).validate_syntax(str(d))
    assert r.is_valid
    assert r.tom_check == {
        "ran": False,
        "ok": None,
        "error": None,
        "udf_gate": False,
        "reason": "no clr",
        "elapsed_ms": r.tom_check["elapsed_ms"],
    }


@pytest.mark.parametrize("required,valid", [("auto", True), ("required", False)])
def test_tom_required_mode(tmp_path, monkeypatch, required, valid):
    d = _model(tmp_path, {"T": _GOOD})
    monkeypatch.setattr(
        tom_check,
        "_deserialize_override",
        lambda f: (_ for _ in ()).throw(RuntimeError("__unavailable__:x")),
    )
    assert TmdlValidator({"tom_check": required}).validate_syntax(str(d)).is_valid is valid
