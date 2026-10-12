"""Deep review 2026-10-03 (slice N), N-08: the reload classifier fails CLOSED on unknown properties.

Fakes (``SimpleNamespace``) cover the logic; the ``real_tom`` tests build a small synthetic TMDL
folder in a temp dir and run the real TOM deserializer (skipped when the AMO assemblies cannot
be loaded). Nothing here touches a live Desktop or a real project.
"""

from __future__ import annotations

import shutil

import pytest

from core.autonomous import model_change_classifier as mcc
from tests._tom_fakes import base, col, ns, part, table


def diff(live, disk, **kw):
    return mcc.diff_signatures(mcc.extract_signature(live), mcc.extract_signature(disk), **kw)


def verdict(live, disk):
    return mcc.classify(diff(live, disk))["verdict"]


# --- unreviewed properties of import objects are UNSAFE -------------------------------------


@pytest.mark.parametrize(
    "prop,value",
    [
        ("EncodingHint", "Hash"),
        ("IsNullable", False),
        ("SourceProviderType", "Integer"),
        ("StringIndexingBehavior", "None"),
        ("FullTextIndexingBehavior", "None"),
        ("IsKey", True),
        ("SomeFutureTomProperty", "x"),
    ],
)
def test_unreviewed_import_column_property_is_unsafe(prop, value):
    disk = base()
    setattr(disk.Model.Tables[0].Columns[1], prop, value)
    changes = diff(base(), disk)
    assert [c["safe"] for c in changes] == [False]
    assert changes[0]["category"] == "column" and "props" in changes[0]["fields"]
    assert mcc.classify(changes)["verdict"] == "unsafe"


def test_unreviewed_property_on_a_calculated_column_stays_metadata_only():
    live, disk = base(), base()
    for d in (live, disk):
        d.Model.Tables[0].Columns.append(col("Calc", "Calculated", "Double", expr="[Amount]"))
    disk.Model.Tables[0].Columns[-1].EncodingHint = "Hash"
    assert verdict(live, disk) == "metadata_only"


def test_known_metadata_properties_of_import_columns_stay_safe():
    disk = base()
    disk.Model.Tables[0].Columns[1].IsAvailableInMdx = False
    disk.Model.Tables[0].Columns[1].DisplayOrdinal = 3
    changes = diff(base(), disk)
    assert [c["safe"] for c in changes] == [True]
    assert mcc.classify(changes)["verdict"] == "metadata_only"


def test_partition_dataview_and_other_partition_properties_are_unsafe():
    disk = base()
    disk.Model.Tables[0].Partitions[0].DataView = "Sample"
    (c,) = diff(base(), disk)
    assert c["category"] == "partition" and not c["safe"] and "props" in c["fields"]
    disk2 = base()
    disk2.Model.Tables[1].Partitions[0].RetainDataTillForceCalculate = True  # calculated table too
    assert verdict(base(), disk2) == "unsafe"


@pytest.mark.parametrize(
    "prop,value",
    [
        ("DefaultPowerBIDataSourceVersion", "PowerBI_V1"),
        ("SourceQueryCulture", "nl-BE"),
        ("Culture", "nl-BE"),
    ],
)
def test_model_level_unreviewed_property_is_unsafe(prop, value):
    disk = base()
    setattr(disk.Model, prop, value)
    (c,) = diff(base(), disk)
    assert c["category"] == "model" and not c["safe"]
    assert mcc.classify([c])["verdict"] == "unsafe"


def test_model_description_is_metadata_only():
    disk = base()
    disk.Model.Description = "doc"
    assert verdict(base(), disk) == "metadata_only"


def test_unreviewed_table_property_unsafe_but_known_metadata_safe():
    disk = base()
    disk.Model.Tables[0].ExcludeFromModelRefresh = True
    assert verdict(base(), disk) == "unsafe"
    disk2 = base()
    disk2.Model.Tables[0].ShowAsVariationsOnly = True
    assert verdict(base(), disk2) == "metadata_only"


def test_any_data_source_or_expression_property_is_unsafe():
    live = base()
    live.Model.DataSources = [ns(Name="DS", Type="Structured", ConnectionString="x")]
    disk = base()
    disk.Model.DataSources = [
        ns(Name="DS", Type="Structured", ConnectionString="x", Description="new")
    ]
    assert verdict(live, disk) == "unsafe"
    disk2 = base()
    disk2.Model.Expressions = [
        ns(Name="p_Now", Kind="M", Expression="DateTime.LocalNow()", Description="d")
    ]
    assert verdict(base(), disk2) == "unsafe"


def test_runtime_bookkeeping_properties_are_ignored():
    disk = base()
    t = disk.Model.Tables[0]
    t.ModifiedTime = "2026-10-03"
    t.LineageTag = "abc"
    t.Columns[1].LineageTag = "def"
    t.Columns[1].Annotations = [ns(Name="x", Value="1")]
    t.Partitions[0].RefreshedTime = "now"
    t.Partitions[0].State = "Ready"
    assert diff(base(), disk) == []


# --- exact M text --------------------------------------------------------------------------


def test_double_space_inside_an_m_string_literal_is_a_change():
    live, disk = base(), base()
    live.Model.Tables[0].Partitions[0].Source.Expression = 'let s = "a b" in s'
    disk.Model.Tables[0].Partitions[0].Source.Expression = 'let s = "a  b" in s'
    (c,) = diff(live, disk)
    assert c["category"] == "partition" and not c["safe"]


def test_line_endings_and_outer_whitespace_of_m_are_not_a_change():
    live, disk = base(), base()
    live.Model.Tables[0].Partitions[0].Source.Expression = "let\n    s = 1\nin\n    s"
    disk.Model.Tables[0].Partitions[0].Source.Expression = "\r\nlet\r\n    s = 1\r\nin\r\n    s\r\n"
    assert diff(live, disk) == []


def test_calculated_table_dax_whitespace_is_still_not_a_change():
    live, disk = base(), base()
    live.Model.Tables[1].Partitions[0].Source.Expression = "CALENDAR(1,\n      2)"
    disk.Model.Tables[1].Partitions[0].Source.Expression = "CALENDAR(1, 2)"
    assert diff(live, disk) == []


# --- calc items and role members are detected (as metadata-only) ---------------------------


def _calc_group(items):
    t = table("CG", parts=[part("CG", "CalculationGroup", "")], cols=[col("Name", "Data")])
    t.CalculationGroup = ns(
        CalculationItems=[ns(Name=n, Expression=e, Ordinal=i) for i, (n, e) in enumerate(items)]
    )
    return t


def test_calculation_item_changes_are_detected_and_safe():
    live, disk = base(), base()
    live.Model.Tables.append(
        _calc_group([("YTD", "CALCULATE(SELECTEDMEASURE(), DATESYTD('Cal'[V]))")])
    )
    disk.Model.Tables.append(
        _calc_group(
            [
                ("YTD", "CALCULATE(SELECTEDMEASURE(), DATESMTD('Cal'[V]))"),
                ("PY", "SELECTEDMEASURE()"),
            ]
        )
    )
    changes = diff(live, disk)
    got = {(c["object"], c["change"]) for c in changes if c["category"] == "calculation_item"}
    assert got == {("CG/calculation_item:YTD", "changed"), ("CG/calculation_item:PY", "added")}
    assert mcc.classify(changes)["verdict"] == "metadata_only"


def test_role_member_changes_are_detected_and_safe():
    live, disk = base(), base()
    live.Model.Roles = [
        ns(Name="R", ModelPermission="Read", TablePermissions=[], Members=[ns(MemberName="a@x.be")])
    ]
    disk.Model.Roles = [
        ns(
            Name="R",
            ModelPermission="Read",
            TablePermissions=[],
            Members=[ns(MemberName="a@x.be"), ns(MemberName="b@x.be")],
        )
    ]
    (c,) = diff(live, disk)
    assert c["category"] == "role" and c["safe"] and c["fields"] == ["members"]


# --- fail closed when a TOM object cannot be read ------------------------------------------


def test_disk_signature_failure_is_unclassified_not_a_crash(monkeypatch):
    import importlib.util

    import core.tmdl.tom_check as tc

    # conftest replaces the reader for hermeticity; load the original from source.
    spec = importlib.util.spec_from_file_location("_mcc_fresh_n08", mcc.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    monkeypatch.setattr(
        tc, "load_database_from_folder", lambda d: {"ran": True, "ok": True, "database": object()}
    )
    monkeypatch.setattr(
        fresh, "extract_signature", lambda db: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    res = fresh._read_disk_signature("x")
    assert res["ok"] is False and res.get("unavailable") and "boom" in res["reason"]


# --- real TOM (synthetic TMDL) ---------------------------------------------------------------

_MODEL = """\
model Model
\tculture: en-US
\tdefaultPowerBIDataSourceVersion: powerBI_V3
\tsourceQueryCulture: en-US

ref table Sales
"""

_SALES = """\
table Sales
\tlineageTag: 11111111-1111-1111-1111-111111111111

\tmeasure Total = SUM(Sales[Amount])
\t\tformatString: 0

\tcolumn Id
\t\tdataType: int64
\t\tsourceColumn: Id
\t\tsummarizeBy: none

\tcolumn Amount
\t\tdataType: double
\t\tsourceColumn: Amount
\t\tencodingHint: hash
\t\tisNullable: false

\tpartition Sales = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t    Source = #table({"Id","Amount"}, {})
\t\t\tin
\t\t\t    Source
"""


def _write(root, files):
    for rel_path, text in files.items():
        f = root / rel_path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")


def _tom_signature(folder):
    from core.tmdl.tom_check import load_database_from_folder

    res = load_database_from_folder(str(folder))
    if not res.get("ran"):
        pytest.skip(f"TOM not available: {res.get('reason')}")
    assert res.get("ok"), res.get("error")
    return mcc.extract_signature(res["database"])


@pytest.fixture
def real_tom(tmp_path):
    def build(name, model=_MODEL, sales=_SALES, edits=()):
        root = tmp_path / name
        if root.exists():
            shutil.rmtree(root)
        _write(
            root,
            {
                "database.tmdl": "database Synth\n\tcompatibilityLevel: 1702\n",
                "model.tmdl": model,
                "tables/Sales.tmdl": sales,
            },
        )
        for rel_path, old, new in edits:
            f = root / rel_path
            text = f.read_text(encoding="utf-8")
            assert old in text, old
            f.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        return _tom_signature(root)

    return build


def test_real_tom_control_is_unchanged_and_runtime_noise_is_ignored(real_tom):
    assert mcc.diff_signatures(real_tom("a"), real_tom("b")) == []


@pytest.mark.parametrize(
    "label,edit,category",
    [
        (
            "EncodingHint",
            ("tables/Sales.tmdl", "encodingHint: hash", "encodingHint: value"),
            "column",
        ),
        ("IsNullable", ("tables/Sales.tmdl", "isNullable: false", "isNullable: true"), "column"),
        (
            "DataView",
            ("tables/Sales.tmdl", "mode: import", "mode: import\n\t\tdataView: sample"),
            "partition",
        ),
        ("DefaultPowerBIDataSourceVersion", ("model.tmdl", "powerBI_V3", "powerBI_V1"), "model"),
        (
            "SourceQueryCulture",
            ("model.tmdl", "sourceQueryCulture: en-US", "sourceQueryCulture: nl-BE"),
            "model",
        ),
        (
            "M double space",
            ("tables/Sales.tmdl", '{"Id","Amount"}', '{"Id",  "Amount"}'),
            "partition",
        ),
    ],
)
def test_real_tom_unseen_property_changes_are_unsafe(real_tom, label, edit, category):
    changes = mcc.diff_signatures(real_tom("live"), real_tom("disk", edits=[edit]))
    assert changes, label
    assert {c["category"] for c in changes} == {category}, (label, changes)
    assert mcc.classify(changes)["verdict"] == "unsafe", label


def test_real_tom_measure_change_is_still_metadata_only(real_tom):
    edit = ("tables/Sales.tmdl", "SUM(Sales[Amount])", "SUM(Sales[Amount]) + 1")
    changes = mcc.diff_signatures(real_tom("live"), real_tom("disk", edits=[edit]))
    assert mcc.classify(changes)["verdict"] == "metadata_only"
    assert [c["category"] for c in changes] == ["measure"]
