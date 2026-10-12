"""F-20d: culture export_translations / import_translations / translation_coverage.

Pure helpers over a duck-typed TOM graph, then the manager (TOM faked), then the 02 dispatch shell
(classification + dry-run).
"""

from __future__ import annotations

import csv
import io
import types

import pytest

from core.operations import culture_translations as ct
from core.operations.culture_crud_manager import CultureCrudManager
from tests._c_fake_tom import Coll, FakeServer, Obj


# the CLR type name drives the kind (Table / DataColumn / Measure / Hierarchy)
class Table(Obj):
    pass


class DataColumn(Obj):
    pass


class Measure(Obj):
    pass


class Hierarchy(Obj):
    pass


def build_model():
    sales = Table(Name="Sales", Description="Fact table")
    amount = DataColumn(Name="Amount", Description="", DisplayFolder="", Type="Data", Table=sales)
    rownum = DataColumn(
        Name="RowNumber-x", Description="", DisplayFolder="", Type="RowNumber", Table=sales
    )
    total = Measure(
        Name="Total Sales", Description="Sum of sales", DisplayFolder="KPIs", Table=sales
    )
    cal = Hierarchy(Name="Calendar", Description="", DisplayFolder="", Table=sales)
    sales.Columns, sales.Measures, sales.Hierarchies = (
        Coll([amount, rownum]),
        Coll([total]),
        Coll([cal]),
    )
    nl = Obj(
        Name="nl-BE",
        ObjectTranslations=[
            Obj(Object=sales, Property="Caption", Value="Verkoop"),
            Obj(Object=total, Property="Caption", Value="Totale verkoop"),
            Obj(Object=total, Property="DisplayFolder", Value="Kengetallen"),
        ],
    )
    fr = Obj(
        Name="fr-BE",
        ObjectTranslations=[Obj(Object=total, Property="Caption", Value="Ventes totales")],
    )
    model = Obj(Tables=Coll([sales]), Cultures=Coll([nl, fr]))
    model.saves = 0
    return model, sales, total


def test_translatable_items_skip_rownumber_and_empty_properties():
    model, *_ = build_model()
    keys = {k for k, _ in ct.translatable_items(model)}
    assert ("table", "", "Sales", "caption") in keys and (
        "table",
        "",
        "Sales",
        "description",
    ) in keys
    assert ("measure", "Sales", "Total Sales", "display_folder") in keys
    assert ("column", "Sales", "Amount", "caption") in keys
    assert ("column", "Sales", "Amount", "description") not in keys  # empty source
    assert not any(k[2] == "RowNumber-x" for k in keys)


def test_build_rows_all_cultures_and_filtered():
    model, *_ = build_model()
    cultures, rows = ct.build_rows(model)
    assert cultures == ["nl-BE", "fr-BE"]
    by = {(r["object_type"], r["name"], r["property"]): r for r in rows}
    total = by[("measure", "Total Sales", "caption")]
    assert total["translations"] == {"nl-BE": "Totale verkoop", "fr-BE": "Ventes totales"}
    assert by[("column", "Amount", "caption")]["translations"] == {"nl-BE": "", "fr-BE": ""}
    only = ct.build_rows(model, ["nl-BE"], include_untranslated=False)[1]
    assert {r["name"] for r in only} == {"Sales", "Total Sales"} and all(
        r["translations"]["nl-BE"] for r in only
    )


def test_csv_round_trip_and_import_items():
    model, *_ = build_model()
    cultures, rows = ct.build_rows(model, ["nl-BE"])
    text = ct.rows_to_csv(cultures, rows)
    header = next(csv.reader(io.StringIO(text)))
    assert header == ["object_type", "table_name", "name", "property", "source", "nl-BE"]
    parsed = ct.parse_csv("﻿" + text)
    items, counts, rejected = ct.rows_to_items(parsed, "nl-BE")
    assert counts["skipped_empty"] > 0 and not rejected
    assert {
        "object_type": "measure",
        "name": "Total Sales",
        "property": "caption",
        "value": "Totale verkoop",
        "table_name": "Sales",
    } in items
    assert all(i["value"] for i in items)


def test_rows_to_items_accepts_the_bulk_layout_and_rejects_bad_rows():
    items, counts, rejected = ct.rows_to_items(
        [
            {
                "object_type": "measure",
                "table_name": "Sales",
                "name": "Total",
                "property": "caption",
                "value": "X",
            },
            {"object_type": "measure", "name": "NoTable", "property": "caption", "value": "X"},
            {"object_type": "widget", "name": "W", "property": "caption", "value": "X"},
            {"object_type": "table", "name": "Sales", "property": "caption", "value": ""},
            "junk",
        ],
        "nl-BE",
    )
    assert len(items) == 1 and items[0]["table_name"] == "Sales"
    assert counts == {"rows": 5, "skipped_empty": 1, "rejected": 3} and len(rejected) == 3


def test_coverage_report():
    model, *_ = build_model()
    rep = ct.coverage(model, limit=2)
    nl, fr = rep["cultures"]["nl-BE"], rep["cultures"]["fr-BE"]
    assert rep["translatable_properties"] == nl["total"] == fr["total"]
    assert nl["translated"] == 3 and fr["translated"] == 1
    assert nl["coverage_pct"] > fr["coverage_pct"]
    assert nl["by_property"]["caption"]["translated"] == 2
    assert len(nl["missing"]) == 2 and nl["missing_count"] == nl["total"] - 3


# ---------------------------------------------------------------------------
# manager
# ---------------------------------------------------------------------------
@pytest.fixture
def mgr(monkeypatch, tmp_path):
    model, sales, total = build_model()
    import core.operations.amo_helpers as amo

    monkeypatch.setattr(amo, "get_server_db_model", lambda cs: (FakeServer(), Obj(), model, None))
    # path guard: allow the temp dir
    from core.validation import path_guard

    real = path_guard.guard_or_error
    monkeypatch.setattr(
        path_guard,
        "guard_or_error",
        lambda candidate, arg, **kw: real(candidate, arg, extra_roots=[str(tmp_path)], **kw),
    )
    return CultureCrudManager(), model


def test_export_inline_json_and_cap(mgr):
    m, _ = mgr
    out = m.export_translations(culture_name="nl-BE", limit=2)
    assert (
        out["success"]
        and out["cultures"] == ["nl-BE"]
        and len(out["rows"]) == 2
        and out["truncated"] is True
    )
    assert out["row_count"] > 2


def test_export_csv_to_file_then_import_dry_run_and_apply(mgr, tmp_path):
    m, model = mgr
    path = tmp_path / "t.csv"
    out = m.export_translations(culture_name="nl-BE", file_format="csv", output_path=str(path))
    assert out["success"] and out["output_path"] == str(path.resolve()) and path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith(",nl-BE") and len(lines) == out["row_count"] + 1

    # a translator fills one empty cell
    text = path.read_text(encoding="utf-8").replace(
        "column,Sales,Amount,caption,Amount,", "column,Sales,Amount,caption,Amount,Bedrag"
    )
    path.write_text(text, encoding="utf-8")
    dry = m.import_translations("nl-BE", input_path=str(path), dry_run=True)
    assert dry["success"] and dry["dry_run"] and dry["to_apply"] == 4
    assert {
        "object_type": "column",
        "name": "Amount",
        "property": "caption",
        "value": "Bedrag",
        "table_name": "Sales",
    } in dry["preview"]

    calls = []
    m.bulk_set_translations = lambda culture, items: calls.append((culture, items)) or {
        "success": True,
        "applied": len(items),
    }
    done = m.import_translations("nl-BE", input_path=str(path))
    assert done["success"] and done["applied"] == 4 and calls[0][0] == "nl-BE"
    assert done["culture"] == "nl-BE" and done["skipped_empty"] > 0


def test_import_json_rows_and_argument_errors(mgr, tmp_path):
    m, _ = mgr
    both = m.import_translations("nl-BE", translations=[], input_path="x")
    assert both["error_type"] == "invalid_parameters"
    assert m.import_translations("nl-BE")["error_type"] == "invalid_parameters"
    assert m.import_translations("")["error_type"] == "invalid_parameters"
    assert m.import_translations("nl-BE", translations="nope")["error_type"] == "invalid_parameters"
    empty = m.import_translations(
        "nl-BE",
        translations=[
            {"object_type": "table", "name": "Sales", "property": "caption", "value": ""}
        ],
    )
    assert empty["applied"] == 0 and "Nothing to import" in empty["message"]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert m.import_translations("nl-BE", input_path=str(bad))["error_type"] == "invalid_input"


def test_unknown_culture_and_format(mgr):
    m, _ = mgr
    out = m.export_translations(culture_name="xx-XX")
    assert out["error_type"] == "culture_not_found" and out["available_cultures"] == [
        "fr-BE",
        "nl-BE",
    ]
    assert m.export_translations(file_format="xlsx")["error_type"] == "invalid_parameters"
    assert m.translation_coverage(culture_name="xx-XX")["error_type"] == "culture_not_found"


def test_coverage_op_and_no_cultures(mgr, monkeypatch):
    m, model = mgr
    out = m.translation_coverage()
    assert out["success"] and set(out["cultures"]) == {"nl-BE", "fr-BE"}
    model.Cultures = Coll()
    assert m.translation_coverage()["cultures"] == {}


# ---------------------------------------------------------------------------
# dispatch shell
# ---------------------------------------------------------------------------
def test_classification_and_dispatch(monkeypatch):
    from server.handlers import model_operations_handler as h
    from server.op_classification import classify

    cl = lambda op, **kw: classify(  # noqa: E731
        "02_Model_Operations", {"object_type": "culture", "operation": op, **kw}
    )
    assert cl("export_translations") == "read" and cl("translation_coverage") == "read"
    assert (
        cl("import_translations") == "write" and cl("import_translations", dry_run=True) == "read"
    )
    assert h._severity_for("culture", "export_translations", {}) is None
    assert h._severity_for("culture", "import_translations", {"dry_run": True}) is None

    seen = {}
    fake = types.SimpleNamespace(
        export_translations=lambda **kw: seen.setdefault("export", kw) or {"success": True},
        import_translations=lambda *a, **kw: seen.setdefault("import", (a, kw))
        and {"success": True},
        translation_coverage=lambda **kw: seen.setdefault("coverage", kw) and {"success": True},
    )
    monkeypatch.setitem(h._handlers, "culture", fake)
    h.handle_model_operations(
        {
            "object_type": "culture",
            "operation": "export_translations",
            "culture_name": "nl-BE",
            "file_format": "csv",
            "output_path": "o.csv",
            "limit": 5,
        }
    )
    assert seen["export"] == {
        "culture_name": "nl-BE",
        "file_format": "csv",
        "output_path": "o.csv",
        "limit": 5,
    }
    h.handle_model_operations(
        {
            "object_type": "culture",
            "operation": "import_translations",
            "culture_name": "nl-BE",
            "input_path": "i.csv",
            "dry_run": True,
        }
    )
    args, kw = seen["import"]
    assert args == ("nl-BE",) and kw["input_path"] == "i.csv" and kw["dry_run"] is True
