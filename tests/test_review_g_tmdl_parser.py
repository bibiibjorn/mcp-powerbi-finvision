"""Review G-07 / G-26: unified TMDL parser fidelity (indentation-scoped tree).

Every test below failed on the pre-review line parser. Inputs are synthetic
fragments plus the golden PBIP fixture (``tests/fixtures/pbir_golden``), whose
shapes mirror the Desktop-emitted S01/S02 trees.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tmdl.models import TmdlModel
from core.tmdl.tmdl_tree import parse_text, split_object_ref, tmdl_name
from core.tmdl.unified_parser import UnifiedTmdlParser

GOLDEN_MODEL = (
    Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo" / "MDL-Core.SemanticModel"
)


@pytest.fixture(scope="module")
def golden() -> TmdlModel:
    return UnifiedTmdlParser(str(GOLDEN_MODEL)).parse_full_model()


@pytest.fixture
def parser(tmp_path):
    (tmp_path / "definition").mkdir()
    return UnifiedTmdlParser(str(tmp_path))


def _table(model: TmdlModel, name: str):
    return next(t for t in model.tables if t.name == name)


# ─── golden fixture (the WP4-reported gaps) ─────────────────────────


def test_golden_counts(golden):
    assert len(golden.tables) == 6
    assert sum(len(t.measures) for t in golden.tables) == 6
    assert sum(len(t.columns) for t in golden.tables) == 16
    assert len(golden.relationships) == 2
    assert len(golden.functions) == 1
    assert len(golden.cultures) == 1
    assert golden.parse_issues == []


def test_escaped_apostrophe_table_name(golden):
    names = {t.name for t in golden.tables}
    assert "Owner's Book" in names
    assert "Owner" not in names


def test_is_hidden_after_multiline_expression(golden):
    m = next(m for m in _table(golden, "Sales").measures if m.name == "Amount Guarded")
    assert m.is_hidden is True
    assert "isHidden" not in (m.expression or "")
    assert m.expression.startswith("VAR _base")
    assert m.lineage_tag == "5a0c6f3e-0000-4000-8000-000000000102"


def test_descriptions_captured(golden):
    sales = _table(golden, "Sales")
    assert sales.description == "Synthetic fact table: one row per sales line."
    total = next(m for m in sales.measures if m.name == "Total Amount")
    assert total.description == "Sum of the line amounts."
    fp = _table(golden, "fp Metric")
    col = next(c for c in fp.columns if c.name == "fp Metric")
    assert col.description == "Display column of the field parameter."
    assert golden.roles[0].description.startswith("Readers only see")
    assert golden.functions[0].description.startswith("Divisor for the selected")


def test_calculation_group_keeps_all_items(golden):
    tc = _table(golden, "Time Calc")
    assert tc.is_calculation_group
    assert [ci.name for ci in tc.calculation_items] == ["Current", "YTD", "PY"]
    py = tc.calculation_items[2]
    assert py.format_string_definition == "SELECTEDMEASUREFORMATSTRING()"
    assert "SAMEPERIODLASTYEAR" in py.expression
    assert tc.calculation_items[1].description == "Year to date."
    assert tc.properties.get("calculationGroup.precedence") == "10"


def test_multiline_rls_filter(golden):
    tp = golden.roles[0].table_permissions[0]
    assert tp.table == "Product Line"
    assert tp.filter_expression.startswith("-- multi-line RLS filter")
    assert "'Product Line'[Region]" in tp.filter_expression


def test_culture_info_and_linguistic_metadata(golden):
    c = golden.cultures[0]
    assert c.name == "en-US"
    assert c.linguistic_metadata.lstrip().startswith("{")
    assert '"Language": "en-US"' in c.linguistic_metadata
    assert c.linguistic_content_type == "json"


def test_udf_function_block(golden):
    fn = golden.functions[0]
    assert fn.name == "ScaleFormat"
    assert fn.expression.startswith("(unit: string) =>")
    assert "SWITCH" in fn.expression
    assert fn.lineage_tag == "5a0c6f3e-0000-4000-8000-00000000f001"


def test_extended_property_json_and_group_by(golden):
    fp = _table(golden, "fp Metric")
    fields = next(c for c in fp.columns if c.name == "fp Metric Fields")
    assert '"kind": 2' in fields.extended_properties["ParameterMetadata"]
    assert not any(k.startswith('"') for k in fields.properties)
    display = next(c for c in fp.columns if c.name == "fp Metric")
    assert display.group_by_columns == ["fp Metric Fields"]


def test_table_annotations_not_absorbed_into_partition(golden):
    sales = _table(golden, "Sales")
    assert [a.name for a in sales.annotations] == ["PBI_ResultType"]
    assert "annotation" not in sales.partitions[0].source
    assert sales.partitions[0].type == "m"


def test_relationship_refs_with_bare_and_escaped_tables(golden):
    r0, r1 = golden.relationships
    assert (r0.from_table, r0.from_column_name) == ("Sales", "Product Key")
    assert (r1.to_table, r1.to_column_name) == ("Owner's Book", "Owner Key")


def test_to_dict_carries_new_fields(golden):
    d = golden.to_dict()
    assert d["functions"][0]["name"] == "ScaleFormat"
    assert d["functions"][0]["expression"] == golden.functions[0].expression


# ─── synthetic shapes ───────────────────────────────────────────────


def test_bare_is_hidden_right_after_inline_expression(parser):
    t = parser._parse_table_content("table T\n\tmeasure 'A' = 1\n\t\tisHidden\n")
    assert t.measures[0].expression == "1"
    assert t.measures[0].is_hidden is True


def test_measure_without_expression_is_kept(parser):
    text = "table T\n\tmeasure 'UTL | Blank' \n\t\tdisplayFolder: X\n\tmeasure B = 2\n"
    t = parser._parse_table_content(text)
    assert [m.name for m in t.measures] == ["UTL | Blank", "B"]
    assert t.measures[0].display_folder == "X"
    assert "displayFolder" not in t.properties


def test_escaped_names_in_measure_and_column(parser):
    text = "table 'Client''s Book'\n\tmeasure 'Owner''s Total' = 1\n\tcolumn 'Owner''s Name'\n\t\tdataType: string\n"
    t = parser._parse_table_content(text)
    assert t.name == "Client's Book"
    assert t.measures[0].name == "Owner's Total"
    assert t.columns[0].name == "Owner's Name"


def test_bom_prefixed_table_file(tmp_path):
    d = tmp_path / "definition" / "tables"
    d.mkdir(parents=True)
    (d / "T.tmdl").write_text("﻿table T\n\tmeasure A = 1\n", encoding="utf-8")
    model = UnifiedTmdlParser(str(tmp_path)).parse_full_model()
    assert [t.name for t in model.tables] == ["T"]


def test_role_with_ols_and_inline_filter_containing_colon(parser):
    text = (
        "role R\n\tmodelPermission: read\n\n"
        '\ttablePermission T = T[Code] = "a:b"\n\n'
        "\t\tcolumnPermission Secret\n\t\t\tmetadataPermission: none\n"
    )
    role = parser._parse_role_content(text)
    tp = role.table_permissions[0]
    assert tp.filter_expression == 'T[Code] = "a:b"'
    assert tp.column_permissions[0].column == "Secret"
    assert tp.column_permissions[0].metadata_permission == "none"
    assert role.model_permission == "read"


def test_perspective_header_after_leading_lines(parser):
    text = "/// doc\nperspective P\n\tperspectiveTable Sales\n\t\tperspectiveMeasure Total\n"
    p = parser._parse_perspective_content(text)
    assert p.name == "P"
    assert p.description == "doc"
    assert [(i.object_type, i.name) for i in p.items] == [("table", None), ("measure", "Total")]


def test_multiline_description_and_expression_blank_lines(parser):
    text = (
        "table T\n\t/// line one\n\t/// line two\n\tmeasure M =\n\t\t\tVAR a = 1\n\n"
        "\t\t\tRETURN a\n\t\tformatString: 0\n"
    )
    m = parser._parse_table_content(text).measures[0]
    assert m.description == "line one\nline two"
    assert m.expression == "VAR a = 1\n\nRETURN a"
    assert m.format_string == "0"


def test_backtick_expression(parser):
    text = (
        "table T\n\tmeasure M = ```\n\t\t\tformatString: not a property\n\t\t\t```\n\t\tisHidden\n"
    )
    m = parser._parse_table_content(text).measures[0]
    assert m.expression == "formatString: not a property"
    assert m.is_hidden


def test_culture_translations_tree(parser):
    text = (
        "cultureInfo fr-FR\n\ttranslations\n\t\tmodel Model\n\t\t\ttable Sales\n"
        "\t\t\t\ttranslatedCaption: Ventes\n\t\t\t\tcolumn Amount\n\t\t\t\t\ttranslatedCaption: Montant\n"
    )
    c = parser._parse_culture_content(text)
    assert c.name == "fr-FR"
    assert [(t.object_type, t.name, t.translated_caption) for t in c.translations] == [
        ("table", None, "Ventes"),
        ("column", "Amount", "Montant"),
    ]


def test_no_arg_parser_parse_folder():
    model = UnifiedTmdlParser().parse_folder(str(GOLDEN_MODEL))
    assert len(model.tables) == 6


# ─── tree helpers ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("Sales.'Product Key'", ("Sales", "Product Key")),
        ("'Owner''s Book'.'Owner Key'", ("Owner's Book", "Owner Key")),
        ("f_Aging_Customer.'Company Code'", ("f_Aging_Customer", "Company Code")),
        ("'d Date'.Date", ("d Date", "Date")),
    ],
)
def test_split_object_ref(ref, expected):
    assert split_object_ref(ref) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Sales", "Sales"),
        ("2024 Total", "'2024 Total'"),
        ("O'Brien", "'O''Brien'"),
        ("a\\b", "'a\\b'"),
    ],
)
def test_tmdl_name_quoting(name, expected):
    assert tmdl_name(name) == expected


def test_tree_spans_cover_description_and_children():
    doc = parse_text(
        "table T\n\t/// d\n\tmeasure M = 1\n\t\tisHidden\n\n\tcolumn C\n\t\tdataType: string\n"
    )
    t = doc.roots[0]
    m = t.children[0]
    assert (m.span_start, m.start, m.end) == (1, 2, 4)
    assert t.end == 7
