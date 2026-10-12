"""Review G-24: generate_script and the diff text generator emit TMDL that re-parses.

Pre-review, ``generate_measure`` wrote ``measure 'X'`` + ``expression:`` (TOM
"TMDL Format Error"; the repo's own parser found 0 measures) and
``description: "…"``; the text generator put bodies at the property indent.
"""

from __future__ import annotations

from pathlib import Path

from core.tmdl.models import TmdlModel
from core.tmdl.script_generator import ColumnDef, PartitionDef, TmdlScriptGenerator
from core.tmdl.tmdl_text_generator import generate_tmdl_text
from core.tmdl.unified_parser import UnifiedTmdlParser
from core.tmdl.validator import TmdlValidator


def _indent(text: str) -> str:
    return "".join("\t" + ln if ln.strip() else ln for ln in text.splitlines(keepends=True))


def _write_model(tmp_path: Path, tables: dict, rels: str = "") -> Path:
    d = tmp_path / "definition"
    (d / "tables").mkdir(parents=True)
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n", encoding="utf-8")
    (d / "model.tmdl").write_text("model Model\n\tculture: en-US\n", encoding="utf-8")
    for name, text in tables.items():
        (d / "tables" / f"{name}.tmdl").write_text(text, encoding="utf-8")
    if rels:
        (d / "relationships.tmdl").write_text(rels, encoding="utf-8")
    return d


def test_generated_objects_parse_and_validate(tmp_path):
    g = TmdlScriptGenerator()
    table = g.generate_table(
        "Owner's Book",
        [ColumnDef("Key", "int64", summarize_by="none"), ColumnDef("2024 Amount", "double")],
        [PartitionDef("Owner's Book", source_expression="let\n    S = 1\nin\n    S")],
        description="Owners\nsecond line",
    )
    measure = g.generate_measure(
        "Owner's Book",
        "Total 'Q'",
        "VAR x = SUM('Owner''s Book'[2024 Amount])\nRETURN\n    x",
        {
            "formatString": "#,0",
            "description": "The total",
            "isHidden": True,
            "formatStringDefinition": '"#,0"',
        },
    )
    # a measure script is level-0: embed it in the table by indenting one tab
    table = table.rstrip("\n") + "\n\n" + _indent(measure)
    calc = g.generate_from_definition(
        "calc_group",
        {
            "name": "Time Calc",
            "precedence": 5,
            "items": [
                {"name": "Current", "expression": "SELECTEDMEASURE()", "ordinal": 0},
                {
                    "name": "YTD",
                    "expression": "CALCULATE(\n    SELECTEDMEASURE()\n)",
                    "ordinal": 1,
                    "format_string": "SELECTEDMEASUREFORMATSTRING()",
                },
            ],
        },
    )
    rel = g.generate_relationship("Owner's Book", "Key", "Time Calc", "Ordinal")
    d = _write_model(tmp_path, {"Owner's Book": table, "Time Calc": calc}, rel)

    r = TmdlValidator().validate_syntax(str(d))
    assert r.is_valid, [(e.code, e.message) for e in r.errors]
    model = UnifiedTmdlParser(str(d)).parse_full_model()
    owners = next(t for t in model.tables if t.name == "Owner's Book")
    assert owners.description == "Owners\nsecond line"
    m = owners.measures[0]
    assert m.name == "Total 'Q'" and m.is_hidden and m.description == "The total"
    assert m.expression.startswith("VAR x = SUM('Owner''s Book'[2024 Amount])")
    assert m.format_string_definition == '"#,0"'
    assert [c.name for c in owners.columns] == ["Key", "2024 Amount"]
    assert owners.columns[1].source_column == "2024 Amount"
    tc = next(t for t in model.tables if t.name == "Time Calc")
    assert [ci.name for ci in tc.calculation_items] == ["Current", "YTD"]
    assert tc.calculation_items[1].format_string_definition == "SELECTEDMEASUREFORMATSTRING()"
    assert model.relationships[0].from_table == "Owner's Book"


def test_text_generator_bodies_reparse(tmp_path):
    src = (
        "table 'A''s'\n\tmeasure 'M 1' =\n\t\t\tVAR x = 1\n\t\t\tRETURN x\n\t\tformatString: 0\n\n"
        '\t\tformatStringDefinition = "0"\n\n'
        "\tcolumn C = [M 1] * 2\n\t\tdataType: double\n"
    )
    d = _write_model(tmp_path, {"A's": src})
    model = UnifiedTmdlParser(str(d)).parse_full_model()
    text = generate_tmdl_text(model.to_dict())
    body = text.split("table ", 1)[1]
    again = UnifiedTmdlParser()._parse_table_content("table " + body.split("\n\n/// ", 1)[0])
    assert again.name == "A's"
    assert again.measures[0].expression == "VAR x = 1\nRETURN x"
    assert again.measures[0].format_string_definition == '"0"'
    assert again.columns[0].expression == "[M 1] * 2"


def test_role_text_uses_the_header_expression():
    data = TmdlModel().to_dict()
    data["roles"] = [
        {"name": "R", "table_permissions": [{"table": "T", "filter_expression": "T[a] = 1"}]}
    ]
    text = generate_tmdl_text(data)
    assert "\ttablePermission 'T' = T[a] = 1" in text
