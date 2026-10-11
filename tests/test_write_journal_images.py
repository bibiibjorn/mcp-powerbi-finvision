"""JR-01: object images, diffing and undo / replay / restore planning (pure Python, no CLR)."""

from __future__ import annotations

import copy

from core.operations import journal_images as ji
from core.operations import journal_plan as jp
from tests import _journal_fakes as jf


def images(tmsl=None):
    return ji.split_model(copy.deepcopy(tmsl if tmsl is not None else jf.BASE_TMSL))


def mutated(fn):
    t = copy.deepcopy(jf.BASE_TMSL)
    fn(t)
    return t


def entry(eid, pre, post):
    return {"id": eid, "changes": ji.diff_maps(images(pre), images(post))}


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------
def test_split_model_makes_one_image_per_restorable_object():
    refs = set(images())
    assert refs == {
        "model",
        "table:Sales",
        "column:Sales[Amount]",
        "column:Sales[Qty]",
        "measure:Sales[Total]",
        "measure:Sales[Count]",
        "partition:Sales[Sales]",
        "role:Reader",
    }
    shell = images()["table:Sales"]["json"]
    assert "columns" not in shell and "measures" not in shell and shell["lineageTag"] == "tag-sales"
    assert images()["model"]["json"] == {"culture": "en-US"}


def test_calculation_items_are_separate_images_and_the_shell_keeps_the_group():
    t = mutated(
        lambda m: m["tables"].append(
            {
                "name": "TI",
                "calculationGroup": {
                    "precedence": 5,
                    "calculationItems": [
                        {"name": "YTD", "expression": "1"},
                        {"name": "MTD", "expression": "2"},
                    ],
                },
            }
        )
    )
    imgs = images(t)
    assert imgs["calcitem:TI[YTD]"]["json"]["expression"] == "1"
    assert imgs["table:TI"]["json"]["calculationGroup"] == {"precedence": 5}


def test_server_timestamps_never_make_a_difference():
    a = images()
    t = copy.deepcopy(jf.BASE_TMSL)
    t["tables"][0]["measures"][0]["modifiedTime"] = "2026-09-30T10:00:00"
    t["tables"][0]["partitions"][0]["refreshedTime"] = "2026-09-30T10:00:00"
    assert ji.diff_maps(a, images(t)) == []


def test_diff_maps_reports_added_removed_modified_and_renamed():
    post = mutated(
        lambda t: (
            t["tables"][0]["measures"].pop(1),
            t["tables"][0]["measures"].append({"name": "New", "expression": "1"}),
            t["tables"][0]["measures"][0].update(expression="2"),
            t["tables"][0]["columns"][0].update(name="Amount2"),
            t.update(culture="nl-BE"),
        )
    )
    got = {c["ref"]: c["change"] for c in ji.diff_maps(images(), images(post))}
    assert got == {
        "model": "modified",
        "measure:Sales[Total]": "modified",
        "measure:Sales[Count]": "removed",
        "measure:Sales[New]": "added",
        "column:Sales[Amount2]": "renamed",
    }


def test_table_rename_moves_untouched_children_without_reporting_them():
    def rename(t):
        t["tables"][0]["name"] = "Fact"
        t["tables"][0]["measures"][0]["expression"] = "SUM(Fact[Amount])"

    got = {c["ref"]: c for c in ji.diff_maps(images(), images(mutated(rename)))}
    assert got["table:Fact"]["change"] == "renamed" and got["table:Fact"]["pre_name"] == "Sales"
    assert got["measure:Fact[Total]"]["change"] == "modified"  # cascaded DAX
    assert "column:Fact[Amount]" not in got and "measure:Fact[Count]" not in got


def test_subset_match_ignores_extra_live_keys_only():
    assert ji.subset_match({"a": 1}, {"a": 1, "lineageTag": "x"})
    assert not ji.subset_match({"a": 1, "b": 2}, {"a": 1})
    assert not ji.subset_match({"a": 1}, {"a": 2})
    assert ji.subset_match(
        {"annotations": [{"name": "n", "value": "1"}]},
        {"annotations": [{"name": "z"}, {"name": "n", "value": "1", "x": 1}]},
    )
    assert ji.same_image({"a": 1}, {"a": 1}) and not ji.same_image({"a": 1}, {"a": 1, "b": 2})


def test_describe_change_is_property_level():
    ch = ji.diff_maps(
        images(), images(mutated(lambda t: t["tables"][0]["measures"][0].update(formatString="0")))
    )[0]
    d = ji.describe_change(ch)
    assert d["properties"] == {"formatString": {"pre": "0.00", "post": "0"}}


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------
def actions(plan):
    return [(o["action"], o["ref"]) for o in plan["ops"]]


def test_undo_of_a_modification_targets_the_preimage():
    post = mutated(lambda t: t["tables"][0]["measures"][0].update(expression="X"))
    plan = jp.plan_undo([entry("J1", jf.BASE_TMSL, post)], images(post))
    assert actions(plan) == [("update", "measure:Sales[Total]")]
    assert plan["ops"][0]["json"]["expression"] == "SUM(Sales[Amount])"
    assert plan["conflicts"] == []


def test_undo_is_skipped_when_live_already_matches_the_preimage():
    post = mutated(lambda t: t["tables"][0]["measures"][0].update(expression="X"))
    plan = jp.plan_undo([entry("J1", jf.BASE_TMSL, post)], images())
    assert plan["ops"] == [] and plan["skipped"][0]["reason"] == "already_matches"


def test_undo_conflicts_when_live_drifted_and_force_overrides():
    post = mutated(lambda t: t["tables"][0]["measures"][0].update(expression="X"))
    drifted = mutated(lambda t: t["tables"][0]["measures"][0].update(expression="Y"))
    e = entry("J1", jf.BASE_TMSL, post)
    plan = jp.plan_undo([e], images(drifted))
    assert plan["ops"] == [] and plan["conflicts"][0]["ref"] == "measure:Sales[Total]"
    forced = jp.plan_undo([e], images(drifted), force=True)
    assert actions(forced) == [("update", "measure:Sales[Total]")] and forced["conflicts"] == []


def test_undo_chain_rename_then_edit_is_reverted_as_a_chain():
    step1 = mutated(lambda t: t["tables"][0]["columns"][0].update(name="Amount2"))
    step2 = copy.deepcopy(step1)
    step2["tables"][0]["columns"][0]["description"] = "d"
    e1 = entry("J1", jf.BASE_TMSL, step1)
    e2 = entry("J2", step1, step2)
    plan = jp.plan_undo([e2, e1], images(step2))
    assert plan["conflicts"] == []
    assert actions(plan) == [
        ("update", "column:Sales[Amount2]"),
        ("update", "column:Sales[Amount2]"),
    ]
    first, second = plan["ops"]
    assert first["json"].get("description") is None and first["json"]["name"] == "Amount2"
    assert second["json"]["name"] == "Amount" and second["locate_name"] == "Amount2"


def test_undo_orders_removals_before_additions_and_drops_children_of_removed_tables():
    post = mutated(
        lambda t: t["tables"].append(
            {
                "name": "New",
                "columns": [{"name": "Id"}],
                "measures": [{"name": "M", "expression": "1"}],
            }
        )
    )
    plan = jp.plan_undo([entry("J1", jf.BASE_TMSL, post)], images(post))
    assert actions(plan) == [("remove", "table:New")]  # its column / measure go with it


def test_undo_of_a_table_delete_folds_children_into_one_table_add():
    pre = copy.deepcopy(jf.BASE_TMSL)
    post = mutated(lambda t: t["tables"].clear())
    plan = jp.plan_undo([entry("J1", pre, post)], images(post))
    assert actions(plan) == [("add", "table:Sales")]
    table_add = next(o for o in plan["ops"] if o["ref"] == "table:Sales")
    assert {m["name"] for m in table_add["json"]["measures"]} == {"Total", "Count"}
    assert {c["name"] for c in table_add["json"]["columns"]} == {"Amount", "Qty"}
    assert table_add["json"]["partitions"][0]["name"] == "Sales"
    assert table_add["needs_refresh"] is True
    assert all(o["kind"] not in ji.CHILD_KINDS for o in plan["ops"])


def test_replay_applies_missing_changes_and_skips_applied_ones():
    post = mutated(lambda t: t["tables"][0]["measures"][0].update(expression="X"))
    e = entry("J1", jf.BASE_TMSL, post)
    assert actions(jp.plan_replay([e], images())) == [("update", "measure:Sales[Total]")]
    done = jp.plan_replay([e], images(post))
    assert done["ops"] == [] and done["skipped"][0]["reason"] == "already_applied"


def test_replay_conflicts_when_live_is_neither_pre_nor_post():
    post = mutated(lambda t: t["tables"][0]["measures"][0].update(expression="X"))
    other = mutated(lambda t: t["tables"][0]["measures"][0].update(expression="Y"))
    plan = jp.plan_replay([entry("J1", jf.BASE_TMSL, post)], images(other))
    assert plan["ops"] == [] and len(plan["conflicts"]) == 1


def test_replay_chains_dependent_entries():
    s1 = mutated(lambda t: t["tables"][0]["measures"][0].update(expression="A"))
    s2 = copy.deepcopy(s1)
    s2["tables"][0]["measures"][0]["expression"] = "B"
    plan = jp.plan_replay([entry("J1", jf.BASE_TMSL, s1), entry("J2", s1, s2)], images())
    assert [o["json"]["expression"] for o in plan["ops"]] == ["A", "B"] and plan["conflicts"] == []


def test_replay_renames_a_table_before_locating_its_children():
    def rename(t):
        t["tables"][0]["name"] = "Fact"
        t["tables"][0]["measures"][0]["expression"] = "SUM(Fact[Amount])"

    plan = jp.plan_replay([entry("J1", jf.BASE_TMSL, mutated(rename))], images())
    assert actions(plan)[0] == ("update", "table:Fact")
    assert plan["ops"][0]["locate_name"] == "Sales"


def test_undo_renames_a_table_back_after_its_children():
    def rename(t):
        t["tables"][0]["name"] = "Fact"

    post = mutated(rename)
    plan = jp.plan_undo([entry("J1", jf.BASE_TMSL, post)], images(post))
    assert actions(plan) == [("update", "table:Fact")]
    assert plan["ops"][0]["json"]["name"] == "Sales" and plan["ops"][0]["locate_name"] == "Fact"


def test_restore_plan_updates_adds_and_removes():
    target = images()
    live = mutated(
        lambda t: (
            t["tables"][0]["measures"][0].update(expression="X"),
            t["tables"][0]["measures"].append({"name": "Junk", "expression": "1"}),
            t["tables"][0]["measures"].pop(1),
        )
    )
    plan = jp.plan_restore(images(live), target)
    assert sorted(actions(plan)) == sorted(
        [
            ("remove", "measure:Sales[Junk]"),
            ("update", "measure:Sales[Total]"),
            ("add", "measure:Sales[Count]"),
        ]
    )
    assert jp.plan_restore(target, target)["ops"] == []


def test_summarize_plan_flags_gated_removals_and_caps_output():
    post = mutated(lambda t: t["tables"].append({"name": "New"}))
    plan = jp.plan_undo([entry("J1", jf.BASE_TMSL, post)], images(post))
    s = jp.summarize_plan(plan)
    assert s["removes_gated_objects"] == ["table:New"] and s["counts"] == {"remove": 1}
    assert jp.needs_removal_confirmation(plan) == ["table:New"]
