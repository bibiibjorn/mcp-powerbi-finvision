"""F-03: select-and-patch engine + BPA FixExpression translation (no live model).

Layers:

* ``bpa_fix`` - the supported FixExpression subset and what falls outside it;
* ``build_plan`` / ``build_bpa_plan`` - pure plans over synthetic TMSL;
* ``PatchEngine`` - preview -> plan_hash -> apply against a fake TOM (one ``commit``, stale plan
  refused, gate honoured, rollback on a failed save).
"""

from __future__ import annotations

import json
import os

import pytest

from core.analysis import patch_engine as pe
from core.analysis.bpa_analyzer import BPAAnalyzer, rule_from_dict
from core.analysis.bpa_fix import DELETE_KEY, translate_fix_expression
from tests._c_fake_tom import Coll, FakeServer, Obj

# ---------------------------------------------------------------------------
# bpa_fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,kind,expected",
    [
        ("IsHidden = true", "column", {"IsHidden": True}),
        ("IsAvailableInMDX = false", "column", {"IsAvailableInMDX": False}),
        ('FormatString = "#,0"', "measure", {"FormatString": "#,0"}),
        (
            'FormatString = "#,0.0%\\u003B-#,0.0%\\u003B#,0.0%"',
            "measure",
            {"FormatString": "#,0.0%;-#,0.0%;#,0.0%"},
        ),
        ('DisplayFolder = "Base Measures"', "measure", {"DisplayFolder": "Base Measures"}),
        ("SummarizeBy = AggregateFunction.None", "column", {"SummarizeBy": "None"}),
        ("Description = it.Table.Name", "measure", {"Description": "{Table.Name}"}),
        ('Description = "set {x}"', "measure", {"Description": "set {{x}}"}),
    ],
)
def test_fix_subset_translates(expr, kind, expected):
    plan = translate_fix_expression(expr, kind)
    assert plan.supported and plan.patch == expected and not plan.delete


def test_fix_delete():
    plan = translate_fix_expression("Delete()", "measure")
    assert plan.supported and plan.delete and plan.as_patch() == {DELETE_KEY: True}


@pytest.mark.parametrize(
    "expr,kind,fragment",
    [
        ("Name = it.Name", "column", "rename"),  # structural: not on the allow-list
        ("DataType = DataType.Decimal", "column", "structural"),
        ("IsKey = true", "column", "structural"),
        ("Partitions[0].Name = it.Name", "table", "single"),
        ("Use COALESCE(expression, 0) instead.", "measure", "single"),  # prose
        (
            "Description = string.Concat(it.Description.ToCharArray().Select(c => c))",
            "measure",
            "right-hand side",
        ),
        ('IsHidden = true; DisplayFolder = "x"', "measure", "several statements"),
        ("", "measure", "no FixExpression"),
    ],
)
def test_fix_outside_subset_is_unsupported_with_reason(expr, kind, fragment):
    plan = translate_fix_expression(expr, kind)
    assert not plan.supported
    assert fragment in (plan.unsupported or "")
    assert plan.expression == expr.strip()


def test_every_shipped_microsoft_fix_is_classified():
    """Each real FixExpression either translates or is reported with a reason - never a crash."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "core", "bpa.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rules = data if isinstance(data, list) else data["rules"]
    seen = {"ok": 0, "unsupported": 0}
    for r in rules:
        if not r.get("FixExpression"):
            continue
        plan = translate_fix_expression(r["FixExpression"], "column")
        seen["ok" if plan.supported else "unsupported"] += 1
        assert plan.supported or plan.unsupported
    assert seen["ok"] >= 8  # IsHidden / IsAvailableInMDX / Delete() / FormatString families


# ---------------------------------------------------------------------------
# synthetic model (TMSL) + matching fake TOM
# ---------------------------------------------------------------------------


def _tmsl() -> dict:
    return {
        "name": "M",
        "tables": [
            {
                "name": "Sales",
                "columns": [
                    {"name": "Amount", "dataType": "decimal", "summarizeBy": "sum"},
                    {"name": "ProductKey", "dataType": "int64", "isHidden": False},
                ],
                "measures": [
                    {"name": "Sales Iter", "expression": "SUMX ( Sales, Sales[Amount] )"},
                    {"name": "Sales Iter 2", "expression": "SUMX ( Sales, 1 )", "isHidden": True},
                    {
                        "name": "Sales Total",
                        "expression": "SUM ( Sales[Amount] )",
                        "displayFolder": "Base",
                    },
                ],
            },
            {
                "name": "Product",
                "columns": [{"name": "ProductKey", "dataType": "int64"}],
                "measures": [{"name": "Prod Iter", "expression": "SUMX ( Product, 1 )"}],
            },
        ],
        "relationships": [
            {
                "name": "r1",
                "fromTable": "Sales",
                "fromColumn": "ProductKey",
                "toTable": "Product",
                "toColumn": "ProductKey",
                "crossFilteringBehavior": "bothDirections",
            }
        ],
    }


def _measure(name, expr, **kw):
    base = dict(
        Name=name,
        Expression=expr,
        Description="",
        FormatString="",
        DisplayFolder="",
        IsHidden=False,
        DataCategory="",
    )
    base.update(kw)
    return Obj(**base)


def _column(name, **kw):
    base = dict(
        Name=name,
        Description="",
        IsHidden=False,
        DisplayFolder="",
        FormatString="",
        DataCategory="",
        SummarizeBy="Default",
        IsAvailableInMDX=True,
        SortByColumn=None,
        Alignment="Default",
        TableDetailPosition=-1,
        IsDefaultLabel=False,
        IsDefaultImage=False,
    )
    base.update(kw)
    c = Obj(**base)
    c.__tom_kinds__ = {"SummarizeBy": ("enum", ["Default", "Sum", "None"])}
    return c


class FakeTom:
    def __init__(self):
        self.Tables = Coll()
        self.Relationships = Coll()
        self.Roles = Coll()
        self.Perspectives = Coll()
        self.Expressions = Coll()
        self.DataSources = Coll()
        self.saves = 0
        sales = Obj(
            Name="Sales",
            Description="",
            IsHidden=False,
            Measures=Coll(
                [
                    _measure("Sales Iter", "SUMX ( Sales, Sales[Amount] )"),
                    _measure("Sales Iter 2", "SUMX ( Sales, 1 )", IsHidden=True),
                    _measure("Sales Total", "SUM ( Sales[Amount] )", DisplayFolder="Base"),
                ]
            ),
            Columns=Coll([_column("Amount", SummarizeBy="Sum"), _column("ProductKey")]),
            Hierarchies=Coll(),
            Partitions=Coll(),
        )
        product = Obj(
            Name="Product",
            Description="",
            IsHidden=False,
            Measures=Coll([_measure("Prod Iter", "SUMX ( Product, 1 )")]),
            Columns=Coll([_column("ProductKey")]),
            Hierarchies=Coll(),
            Partitions=Coll(),
        )
        self.Tables.extend([sales, product])
        rel = Obj(
            Name="r1",
            RelyOnReferentialIntegrity=False,
            JoinOnDateBehavior="DateAndTime",
            CrossFilteringBehavior="BothDirections",
            SecurityFilteringBehavior="OneDirection",
        )
        rel.__tom_kinds__ = {
            "CrossFilteringBehavior": ("enum", ["OneDirection", "BothDirections", "Automatic"]),
            "SecurityFilteringBehavior": ("enum", ["OneDirection", "BothDirections"]),
            "JoinOnDateBehavior": ("enum", ["DateAndTime", "DatePartOnly"]),
        }
        self.Relationships.append(rel)
        for t in (sales, product):
            for m in t.Measures:
                m.Table = t


@pytest.fixture
def lane(monkeypatch):
    """Engine wired to a fake TOM; ``commit`` / ``safe_undo`` recorded."""
    tom = FakeTom()
    server = FakeServer()
    calls = {"commits": [], "undo": 0}
    monkeypatch.setattr(
        pe.PatchEngine, "_open", lambda self: (server, Obj(CompatibilityLevel=1702), tom, None)
    )
    monkeypatch.setattr(pe, "serialize_model", lambda model: _tmsl())

    def fake_commit(model, *, label, journal_meta=None, **kw):
        calls["commits"].append((label, journal_meta))
        tom.saves += 1
        return {"saved": True}

    monkeypatch.setattr(pe, "_lane_commit", fake_commit)
    monkeypatch.setattr(pe, "safe_undo", lambda model: calls.__setitem__("undo", calls["undo"] + 1))
    return tom, server, calls


# ---------------------------------------------------------------------------
# select + plan (pure)
# ---------------------------------------------------------------------------


def test_select_contains_and_not_hidden():
    plan = pe.build_plan(
        _tmsl(),
        'Measures.Where(Expression.Contains("SUMX") and not IsHidden)',
        {"DisplayFolder": "{Table.Name}\\Iterators"},
    )
    names = {(e["table"], e["name"]) for e in plan["entries"]}
    assert names == {("Sales", "Sales Iter"), ("Product", "Prod Iter")}  # hidden one excluded
    ch = {e["name"]: e["changes"][0] for e in plan["entries"]}
    assert ch["Sales Iter"]["after"] == "Sales\\Iterators"
    assert ch["Prod Iter"]["after"] == "Product\\Iterators"
    assert ch["Sales Iter"]["before"] == "" and ch["Sales Iter"]["action"] == "set"
    assert plan["errors"] == [] and plan["object_count"] == 2 and plan["plan_hash"]


def test_plan_hash_is_stable_and_sensitive():
    sel = 'Measures.Where(Expression.Contains("SUMX"))'
    a = pe.build_plan(_tmsl(), sel, {"IsHidden": True})["plan_hash"]
    assert a == pe.build_plan(_tmsl(), sel, {"IsHidden": True})["plan_hash"]
    assert a != pe.build_plan(_tmsl(), sel, {"IsHidden": False})["plan_hash"]
    drifted = _tmsl()
    drifted["tables"][0]["measures"][0]["isHidden"] = True  # model moved since preview
    assert a != pe.build_plan(drifted, sel, {"IsHidden": True})["plan_hash"]


def test_noop_detected_for_values_already_set():
    plan = pe.build_plan(
        _tmsl(), 'Measures.Where(Name == "Sales Total")', {"DisplayFolder": "Base"}
    )
    assert plan["noop_count"] == 1 and plan["set_count"] == 0
    # enum spelled TOM-style vs TMSL lowerCamel is the same member
    plan = pe.build_plan(_tmsl(), 'Columns.Where(Name == "Amount")', {"SummarizeBy": "Sum"})
    assert plan["noop_count"] == 1


def test_string_case_change_is_a_real_change():
    plan = pe.build_plan(
        _tmsl(), 'Measures.Where(Name == "Sales Total")', {"DisplayFolder": "base"}
    )
    assert plan["set_count"] == 1


@pytest.mark.parametrize("prop", ["Name", "Expression", "DataType", "IsKey", "Bogus"])
def test_properties_outside_the_allow_list_are_refused(prop):
    plan = pe.build_plan(_tmsl(), "Measures", {prop: "x"})
    assert plan["errors"] and plan["errors"][0]["error_type"] == "denied_property"
    assert "settable_properties" in plan["errors"][0]


@pytest.mark.parametrize(
    "select,etype",
    [
        ("", "invalid_parameters"),
        ("Frobnicators.Where(IsHidden)", "invalid_select"),
        ("Measures.Count()", "invalid_select"),
        ("Measures.Where(NoSuchProp == 1)", "invalid_select"),
        ("Measures.Where(", "invalid_select"),
    ],
)
def test_bad_selects(select, etype):
    with pytest.raises(pe.PatchError) as exc:
        pe.build_plan(_tmsl(), select, {"IsHidden": True})
    assert exc.value.envelope["error_type"] == etype


def test_bad_patch_shapes():
    for bad in (
        None,
        {},
        {"IsHidden": [1]},
        {"Delete()": True, "IsHidden": True},
        {"Delete()": False},
    ):
        with pytest.raises(pe.PatchError):
            pe.build_plan(_tmsl(), "Measures", bad)


def test_cap_on_selected_objects():
    with pytest.raises(pe.PatchError) as exc:
        pe.build_plan(_tmsl(), "Measures", {"IsHidden": True}, max_objects=2)
    assert exc.value.envelope["error_type"] == "too_many_objects"


def test_unknown_template_member_is_an_error_not_a_silent_blank():
    plan = pe.build_plan(_tmsl(), "Measures", {"Description": "{Nope}"})
    assert plan["errors"][0]["error_type"] == "invalid_template"


def test_template_escaping_and_enum_members():
    node = pe.ModelView(_tmsl()).all_columns[0]
    assert pe.render_template("{{literal}} {Name} {Table.Name} {SummarizeBy}", node) == (
        "{literal} Amount Sales Sum"
    )


def test_delete_plan_and_non_deletable_kinds():
    plan = pe.build_plan(_tmsl(), "Measures.Where(IsHidden)", {"Delete()": True})
    assert plan["delete_count"] == 1 and plan["entries"][0]["delete"]
    plan = pe.build_plan(_tmsl(), "Tables", {"Delete()": True})
    assert {e["error_type"] for e in plan["errors"]} == {"object_not_deletable"}


def test_relationship_high_impact_flag():
    plan = pe.build_plan(_tmsl(), "Relationships", {"CrossFilteringBehavior": "OneDirection"})
    assert (
        plan["high_impact"] is True
        and plan["entries"][0]["changes"][0]["before"] == "BothDirections"
    )


# ---------------------------------------------------------------------------
# engine against a fake TOM
# ---------------------------------------------------------------------------

SEL = 'Measures.Where(Expression.Contains("SUMX") and not IsHidden)'


def _preview(**kw):
    return pe.PatchEngine().run(
        select=SEL, patch={"DisplayFolder": "{Table.Name}\\Iterators"}, **kw
    )


def test_preview_lists_before_after_and_does_not_write(lane):
    tom, server, calls = lane
    res = _preview()
    assert res["success"] and res["preview"] and res["plan_hash"]
    assert res["object_count"] == 2 and res["operations"]["set"] == 2
    row = next(r for r in res["changes"] if r["object"].endswith("Sales Iter"))
    assert row["before"] == "" and row["after"] == "Sales\\Iterators"
    assert calls["commits"] == [] and tom.Tables[0].Measures[0].DisplayFolder == ""
    assert server.disconnected == 1


def test_apply_writes_everything_in_one_commit_with_journal_meta(lane):
    tom, _server, calls = lane
    h = _preview()["plan_hash"]
    res = pe.PatchEngine().run(
        select=SEL,
        patch={"DisplayFolder": "{Table.Name}\\Iterators"},
        preview=False,
        plan_hash_arg=h,
    )
    assert res["success"] and res["applied"] == 2 and res["properties_set"] == 2
    assert tom.Tables[0].Measures[0].DisplayFolder == "Sales\\Iterators"
    assert tom.Tables[1].Measures[0].DisplayFolder == "Product\\Iterators"
    assert tom.Tables[0].Measures[1].DisplayFolder == ""  # hidden: not selected
    assert len(calls["commits"]) == 1
    label, meta = calls["commits"][0]
    assert label == "patch/apply" and meta["patch_plan_hash"] == h


def test_apply_requires_and_checks_plan_hash(lane):
    tom, _s, calls = lane
    kw = dict(select=SEL, patch={"DisplayFolder": "x"}, preview=False)
    assert pe.PatchEngine().run(**kw)["error_type"] == "plan_hash_required"
    stale = pe.PatchEngine().run(plan_hash_arg="0" * 64, **kw)
    assert stale["error_type"] == "stale_plan" and stale["expected"] == "0" * 64
    assert calls["commits"] == [] and tom.Tables[0].Measures[0].DisplayFolder == ""


def test_apply_refuses_when_model_moved_after_preview(lane, monkeypatch):
    h = _preview()["plan_hash"]
    moved = _tmsl()
    moved["tables"][0]["measures"][0]["displayFolder"] = "Someone else"
    monkeypatch.setattr(pe, "serialize_model", lambda model: moved)
    res = pe.PatchEngine().run(
        select=SEL,
        patch={"DisplayFolder": "{Table.Name}\\Iterators"},
        preview=False,
        plan_hash_arg=h,
    )
    assert res["error_type"] == "stale_plan"


def test_gate_can_block_before_any_write(lane):
    tom, _s, calls = lane
    h = _preview()["plan_hash"]
    seen = {}

    def gate(plan):
        seen["objects"] = plan["object_count"]
        return {"success": False, "error_type": "destructive_gate", "error": "blocked"}

    res = pe.PatchEngine().run(
        select=SEL,
        patch={"DisplayFolder": "{Table.Name}\\Iterators"},
        preview=False,
        plan_hash_arg=h,
        gate=gate,
    )
    assert res["error_type"] == "destructive_gate" and seen["objects"] == 2
    assert calls["commits"] == [] and tom.Tables[0].Measures[0].DisplayFolder == ""


def test_failed_commit_rolls_back_and_reports(lane, monkeypatch):
    tom, _s, calls = lane
    h = _preview()["plan_hash"]

    def boom(model, **kw):
        raise RuntimeError("save failed")

    monkeypatch.setattr(pe, "_lane_commit", boom)
    res = pe.PatchEngine().run(
        select=SEL,
        patch={"DisplayFolder": "{Table.Name}\\Iterators"},
        preview=False,
        plan_hash_arg=h,
    )
    assert res["success"] is False and res["rolled_back"] is True and calls["undo"] == 1


def test_invalid_value_for_the_tom_type_writes_nothing(lane):
    tom, _s, calls = lane
    res = pe.PatchEngine().run(select="Columns", patch={"SummarizeBy": "Bogus"}, preview=True)
    assert res["error_type"] == "patch_invalid" and "Bogus" in json.dumps(res["errors"])
    assert calls["commits"] == []


def test_boolean_patch_and_noop_apply_does_not_commit(lane):
    tom, _s, calls = lane
    res = pe.PatchEngine().run(
        select='Measures.Where(Name == "Sales Iter 2")', patch={"IsHidden": True}
    )
    assert res["operations"] == {"set": 0, "noop": 1, "delete": 0}
    done = pe.PatchEngine().run(
        select='Measures.Where(Name == "Sales Iter 2")',
        patch={"IsHidden": True},
        preview=False,
        plan_hash_arg=res["plan_hash"],
    )
    assert done["success"] and done["applied"] == 0 and calls["commits"] == []
    res = pe.PatchEngine().run(
        select='Measures.Where(Name == "Sales Iter")', patch={"IsHidden": "true"}
    )
    h = res["plan_hash"]
    pe.PatchEngine().run(
        select='Measures.Where(Name == "Sales Iter")',
        patch={"IsHidden": "true"},
        preview=False,
        plan_hash_arg=h,
    )
    assert tom.Tables[0].Measures[0].IsHidden is True


def test_delete_removes_from_the_parent_collection(lane):
    tom, _s, calls = lane
    sel = "Measures.Where(IsHidden)"
    pv = pe.PatchEngine().run(select=sel, patch={"Delete()": True})
    assert pv["operations"]["delete"] == 1 and pv["changes"][0]["action"] == "delete"
    res = pe.PatchEngine().run(
        select=sel, patch={"Delete()": True}, preview=False, plan_hash_arg=pv["plan_hash"]
    )
    assert res["success"] and res["objects_deleted"] == 1
    assert [m.Name for m in tom.Tables[0].Measures] == ["Sales Iter", "Sales Total"]
    assert len(calls["commits"]) == 1


def test_not_found_in_open_model_is_reported(lane, monkeypatch):
    tom, _s, _c = lane
    tom.Tables[0].Measures.remove(tom.Tables[0].Measures[0])  # TMSL still lists it
    res = pe.PatchEngine().run(select=SEL, patch={"IsHidden": True})
    assert res["error_type"] == "patch_invalid" and res["errors"][0]["error_type"] == "not_found"


# ---------------------------------------------------------------------------
# BPA FixExpression path
# ---------------------------------------------------------------------------


def _rules():
    hide, _ = rule_from_dict(
        {
            "ID": "T_HIDE_ITER",
            "Name": "hide iterators",
            "Severity": 1,
            "Scope": "Measure",
            "Expression": 'Expression.Contains("SUMX")',
            "FixExpression": "IsHidden = true",
        }
    )
    prose, _ = rule_from_dict(
        {
            "ID": "T_PROSE",
            "Name": "prose",
            "Severity": 1,
            "Scope": "Measure",
            "Expression": 'Name.Contains("Total")',
            "FixExpression": "Rename the measure to follow your convention.",
        }
    )
    computed, _ = rule_from_dict(
        {
            "ID": "T_COMPUTED",
            "Name": "computed",
            "Severity": 1,
            "Scope": "Measure",
            "Expression": 'Name.Contains("Total")',
            "FixExpression": "Description = Name.ToUpper()",
        }
    )
    return [hide, prose, computed]


def test_violation_dict_carries_fix_available_only_when_machine_applicable():
    analyzer = BPAAnalyzer()
    res = analyzer.run(_tmsl(), {"rule_ids": ["T_HIDE_ITER", "T_PROSE"]}, extra_rules=_rules())
    by_rule = {}
    for v in res.violations:
        by_rule.setdefault(v.rule_id, []).append(v.to_dict())
    assert all(d.get("fix_available") is True for d in by_rule["T_HIDE_ITER"])
    assert all("fix_available" not in d for d in by_rule["T_PROSE"])
    assert by_rule["T_HIDE_ITER"][0]["fix_expression"] == "IsHidden = true"


def test_from_bpa_plan_translates_supported_and_reports_the_rest():
    analyzer = BPAAnalyzer()
    plan = pe.build_bpa_plan(_tmsl(), ["T_HIDE_ITER", "T_PROSE", "T_COMPUTED"], analyzer, _rules())
    assert plan["errors"] == []
    assert {e["name"] for e in plan["entries"]} == {"Sales Iter", "Sales Iter 2", "Prod Iter"}
    assert all(e["changes"][0]["property"] == "IsHidden" for e in plan["entries"])
    unsupported = {u["rule_id"]: u for u in plan["unsupported"]}
    assert set(unsupported) == {"T_PROSE", "T_COMPUTED"}
    assert unsupported["T_PROSE"]["fix_expression"].startswith("Rename the measure")
    assert "Name.ToUpper" in unsupported["T_COMPUTED"]["fix_expression"]


def test_from_bpa_unknown_rule_id():
    with pytest.raises(pe.PatchError) as exc:
        pe.build_bpa_plan(_tmsl(), ["NOPE"], BPAAnalyzer(), _rules())
    assert exc.value.envelope["error_type"] == "unknown_rule"


def test_from_bpa_apply_goes_through_the_same_plan_hash_commit_path(lane):
    tom, _s, calls = lane
    analyzer = BPAAnalyzer()
    kw = dict(from_bpa=["T_HIDE_ITER", "T_PROSE"], bpa_analyzer=analyzer, bpa_extra_rules=_rules())
    pv = pe.PatchEngine().run(**kw)
    assert pv["success"] and pv["mode"] == "bpa" and pv["operations"]["set"] == 2  # 3rd is a noop
    assert [u["rule_id"] for u in pv["fix_unsupported"]] == ["T_PROSE"]
    res = pe.PatchEngine().run(preview=False, plan_hash_arg=pv["plan_hash"], **kw)
    assert res["success"] and len(calls["commits"]) == 1
    assert tom.Tables[0].Measures[0].IsHidden is True and tom.Tables[1].Measures[0].IsHidden is True
    assert res["fix_unsupported"][0]["rule_id"] == "T_PROSE"


def test_from_bpa_all_delete_fix_and_kind_check():
    rule, _ = rule_from_dict(
        {
            "ID": "T_DEL",
            "Name": "del hidden measures",
            "Severity": 1,
            "Scope": "Measure",
            "Expression": "IsHidden",
            "FixExpression": "Delete()",
        }
    )
    plan = pe.build_bpa_plan(_tmsl(), ["T_DEL"], BPAAnalyzer(), [rule])
    assert plan["delete_count"] == 1 and plan["entries"][0]["name"] == "Sales Iter 2"
