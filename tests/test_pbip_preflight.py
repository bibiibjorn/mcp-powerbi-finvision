"""F-06: ``07_PBIP_Operations validate_report`` / ``preflight`` (core/pbip/preflight.py).

Read-only "will Desktop open this, is it safe to commit". Every defect class below is a
real Desktop failure class (memory feedback_pbir_load_failure_classes) or a g01 pre-push /
validate_pbip.py check. Synthetic copies of the golden fixture only -- never written in place.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.pbip.preflight import run_preflight
from server.handlers.pbip_operations_handler import handle_pbip_operations

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"


@pytest.fixture()
def proj(tmp_path):
    dst = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, dst)
    return dst


def _rep(p: Path) -> Path:
    return p / "RPT-Sales.Report"


def _defn(p: Path) -> Path:
    return _rep(p) / "definition"


def _run(p: Path, **kw):
    kw.setdefault("tom_check", "off")
    kw.setdefault("report_author", False)
    return run_preflight(str(p / "RPT-Sales.pbip"), **kw)


def _codes(res, severity=None):
    return {f["code"] for f in res["findings"] if severity in (None, f["severity"])}


def _edit(path: Path, fn):
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _hash_tree(root: Path):
    out = {}
    for f in sorted(root.rglob("*")):
        if f.is_file():
            out[str(f.relative_to(root))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


# -- clean / read-only ------------------------------------------------------


def test_golden_report_opens_and_has_no_errors(proj):
    res = _run(proj)
    assert res["success"] and res["ok"] and res["load_ok"] and res["commit_ok"]
    assert res["counts"]["error"] == 0
    assert res["summary"]["reports"][0]["pages"] == 3
    assert res["summary"]["reports"][0]["visuals"] == 13
    assert res["read_only"] is True


def test_preflight_never_writes_even_with_the_cli(proj):
    before = _hash_tree(proj)
    _run(proj, report_author=True, level="commit")
    assert _hash_tree(proj) == before


# -- load-blocking classes --------------------------------------------------


def test_invalid_json_is_load_blocking(proj):
    vis = next(_defn(proj).rglob("vc01cardvisual000001")) / "visual.json"
    vis.write_text('{"name": ', encoding="utf-8")
    res = _run(proj)
    bad = [f for f in res["findings"] if f["code"] == "visual_json_invalid_json"]
    assert bad and bad[0]["load_blocking"] and bad[0]["severity"] == "error"
    assert bad[0]["file"].endswith("vc01cardvisual000001/visual.json")
    assert bad[0]["repair_hint"]
    assert res["ok"] is False and "NOT OPEN" in res["verdict"]


def test_bypath_that_does_not_resolve(proj):
    _edit(
        _rep(proj) / "definition.pbir",
        lambda d: d["datasetReference"]["byPath"].update(path="../Nope.SemanticModel"),
    )
    res = _run(proj)
    f = next(f for f in res["findings"] if f["code"] == "pbir_bypath_missing")
    assert f["load_blocking"] and f["pointer"] == "/datasetReference/byPath/path"


def test_definition_pbir_required_fields(proj):
    _edit(_rep(proj) / "definition.pbir", lambda d: d.pop("version"))
    res = _run(proj)
    assert "pbir_missing_field" in _codes(res, "error")
    assert res["ok"] is False


def test_resource_package_item_without_type(proj):
    def add(d):
        d["resourcePackages"] = [
            {
                "name": "RegisteredResources",
                "type": "RegisteredResources",
                "items": [{"name": "logo.svg", "path": "logo.svg"}],
            }
        ]

    _edit(_defn(proj) / "report.json", add)
    res = _run(proj)
    codes = _codes(res, "error")
    assert "resource_item_no_type" in codes and "resource_missing" in codes
    assert all(f["load_blocking"] for f in res["findings"] if f["code"].startswith("resource_"))


def test_dangling_visual_link_bookmark_and_page(proj):
    button = (
        _defn(proj)
        / "pages"
        / "aa01overview00000001"
        / "visuals"
        / "vb01actionbutton0001"
        / "visual.json"
    )

    def point_away(d):
        props = d["visual"]["visualContainerObjects"]["visualLink"][0]["properties"]
        props["bookmark"]["expr"]["Literal"]["Value"] = "'gone000000000000000'"
        props["navigationSection"] = {"expr": {"Literal": {"Value": "'nopage'"}}}

    _edit(button, point_away)
    res = _run(proj)
    assert {"visuallink_bookmark_missing", "visuallink_page_missing"} <= _codes(res, "error")
    assert res["load_ok"] is False


def test_bookmark_refs_to_deleted_visuals_and_pages(proj):
    bm = _defn(proj) / "bookmarks" / "bm0000000000000000a1.bookmark.json"

    def dangling(d):
        sec = d["explorationState"]["sections"]["aa01overview00000001"]
        sec["visualContainers"]["deleted0000000000001"] = {}
        sec["visualContainerGroups"] = {"grp0": {"children": {"deleted0000000000002": {}}}}
        d["explorationState"]["sections"]["ghostpage"] = {"visualContainers": {}}
        d["options"]["targetVisualNames"].append("deleted0000000000003")

    _edit(bm, dangling)
    res = _run(proj)
    msgs = " ".join(f["message"] for f in res["findings"])
    assert "deleted0000000000001" in msgs and "deleted0000000000002" in msgs
    assert "deleted0000000000003" in msgs and "ghostpage" in msgs
    assert {
        "bookmark_visual_missing",
        "bookmark_section_missing",
        "bookmark_target_visual_missing",
    } <= _codes(res, "error")
    assert res["load_ok"] is False


def test_bookmark_group_children_must_be_strings(proj):
    meta = _defn(proj) / "bookmarks" / "bookmarks.json"
    _edit(
        meta,
        lambda d: d["items"][0].update(
            children=[{"name": "bm0000000000000000a1"}, "bm0000000000000000a2"]
        ),
    )
    res = _run(proj)
    f = next(f for f in res["findings"] if f["code"] == "bookmarks_children_not_strings")
    assert f["load_blocking"] and f["pointer"] == "/items/0/children/0"


def test_missing_model_entity_in_a_live_projection(proj):
    card = (
        _defn(proj)
        / "pages"
        / "aa01overview00000001"
        / "visuals"
        / "vc01cardvisual000001"
        / "visual.json"
    )
    text = card.read_text(encoding="utf-8")
    assert '"Entity": "Sales"' in text
    card.write_text(
        text.replace('"Entity": "Sales"', '"Entity": "No Such Table"'), encoding="utf-8"
    )
    res = _run(proj)
    hit = [f for f in res["findings"] if f["code"] == "visual_binds_missing_field"]
    # Live evidence (R0201, Desktop 2.157, 2026-10-03): the report still opens; the visual
    # renders an error. An error for commit, not a load blocker.
    assert hit and any(f["severity"] == "error" for f in hit)
    assert not any(f["load_blocking"] for f in hit)
    assert all(f["impact"] == "render_error" for f in hit if f["severity"] == "error")
    assert res["by_code"]["visual_binds_missing_field"]["count"] == len(hit)


# -- pages / structure ------------------------------------------------------


def test_pages_json_vs_folders_and_regex(proj):
    pj = _defn(proj) / "pages" / "pages.json"
    _edit(pj, lambda d: d["pageOrder"].extend(["no such page", "ghost000000000000001"]))
    (_defn(proj) / "pages" / "zz99orphan00000000").mkdir()
    (_defn(proj) / "pages" / "zz99orphan00000000" / "page.json").write_text(
        json.dumps({"name": "zz99orphan00000000"}), encoding="utf-8"
    )
    res = _run(proj)
    codes = _codes(res)
    assert {
        "page_name_invalid_chars",
        "page_folder_missing",
        "orphan_page_folder",
        "page_missing_field",
    } <= codes
    # silently dropped pages are errors but do not block the load
    assert res["load_ok"] is True and res["commit_ok"] is False


def test_page_name_must_equal_folder(proj):
    _edit(
        _defn(proj) / "pages" / "ab02details000000002" / "page.json",
        lambda d: d.update(name="other"),
    )
    assert "page_name_mismatch" in _codes(_run(proj), "error")


def test_dangling_interactions_are_flagged(proj):
    page = _defn(proj) / "pages" / "aa01overview00000001" / "page.json"
    _edit(
        page,
        lambda d: d["visualInteractions"].append(
            {"source": "vs01slicerinverted01", "target": "gone0000000000000001", "type": "NoFilter"}
        ),
    )
    res = _run(proj)
    f = next(f for f in res["findings"] if f["code"] == "interaction_dangling")
    assert f["severity"] == "warning" and f["pointer"].endswith("/target")


def test_isHidden_inside_visualgroup_and_null_parent(proj):
    base = _defn(proj) / "pages" / "aa01overview00000001" / "visuals"
    _edit(
        base / "vg01group00000000001" / "visual.json",
        lambda d: d["visualGroup"].update(isHidden=True),
    )
    _edit(base / "vc01cardvisual000001" / "visual.json", lambda d: d.update(parentGroupName=None))
    codes = _codes(_run(proj), "error")
    assert {"ishidden_in_visualgroup", "parentgroup_not_string"} <= codes


def test_platform_guid_and_type(proj):
    (_rep(proj) / ".platform").write_text(
        json.dumps(
            {
                "metadata": {"type": "Dataset", "displayName": "x"},
                "config": {"logicalId": "not-a-guid"},
            }
        ),
        encoding="utf-8",
    )
    codes = _codes(_run(proj), "error")
    assert {"platform_type_mismatch", "platform_bad_guid"} <= codes


# -- polarity ---------------------------------------------------------------


def test_empty_in_values_under_inverted_selection(proj):
    slicer = (
        _defn(proj)
        / "pages"
        / "aa01overview00000001"
        / "visuals"
        / "vs01slicerinverted01"
        / "visual.json"
    )

    def empty(d):
        for flt in d["filterConfig"]["filters"]:
            for w in (flt.get("filter") or {}).get("Where", []):
                node = w["Condition"].get("Not", {}).get("Expression", {}).get("In") or w[
                    "Condition"
                ].get("In")
                if node:
                    node["Values"] = []

    _edit(slicer, empty)
    res = _run(proj)
    f = next(f for f in res["findings"] if f["code"] == "in_values_empty")
    assert "EXCLUDE" in f["message"] and f["impact"] == "render_error"
    assert f["file"].endswith("vs01slicerinverted01/visual.json")


# -- model ------------------------------------------------------------------


def test_tmdl_orphaned_child_property_and_duplicate_annotation(proj):
    defdir = proj / "MDL-Core.SemanticModel" / "definition"
    (defdir / "model.tmdl").write_text(
        (defdir / "model.tmdl").read_text(encoding="utf-8")
        + "\n\tannotation PBI_QueryGroupOrder = 0\n\nannotation A = 1\nannotation A = 2\n",
        encoding="utf-8",
    )
    res = _run(proj)
    codes = _codes(res, "error")
    assert {"tmdl_orphan_property", "tmdl_duplicate_annotation"} <= codes
    assert all(
        f["load_blocking"]
        for f in res["findings"]
        if f["code"].startswith("tmdl_") and f["severity"] == "error"
    )


def test_tom_parse_failure_is_load_blocking_and_unavailable_is_not(proj, monkeypatch):
    import core.tmdl.tom_check as tc

    def boom(folder):
        raise RuntimeError("TMDL objects cannot be merged because both declare the same property")

    monkeypatch.setattr(tc, "_deserialize_override", boom)
    res = _run(proj, tom_check="auto")
    f = next(f for f in res["findings"] if f["code"] == "tom_deserialize_failed")
    assert f["load_blocking"] and f["source"] == "tom_check"

    monkeypatch.setattr(tc, "_deserialize_override", lambda folder: None)
    res = _run(proj, tom_check="auto")
    assert "tom_deserialize_failed" not in _codes(res)
    assert res["summary"]["models"][0]["tom_check"]["ok"] is True

    def unavailable(folder):
        raise RuntimeError("__unavailable__:no assemblies")

    monkeypatch.setattr(tc, "_deserialize_override", unavailable)
    res = _run(proj, tom_check="auto")
    f = next(f for f in res["findings"] if f["code"] == "tom_check_unavailable")
    assert f["severity"] == "info" and "NOT proven" in f["message"]


# -- commit level -----------------------------------------------------------


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_commit_level_checks_staged_files(proj):
    _git(proj, "init", "-q")
    bad = _defn(proj) / "report.json"
    bad.write_text("{ broken", encoding="utf-8")
    (proj / "RPT-Sales.Report" / ".pbi").mkdir()
    (proj / "RPT-Sales.Report" / ".pbi" / "localSettings.json").write_text("{}", encoding="utf-8")
    _git(proj, "add", "-A")
    res = _run(proj, level="commit")
    assert res["git"]["available"] and res["git"]["changed_files"] > 10
    codes = _codes(res, "error")
    assert "staged_local_state" in codes and "report_json_invalid_json" in codes
    assert res["ok"] is False
    staged = [f for f in res["findings"] if f["code"] == "staged_local_state"]
    assert staged and staged[0]["in_staged_scope"] is True


def test_commit_ok_stricter_than_load_ok(proj):
    _edit(
        _defn(proj) / "pages" / "ab02details000000002" / "page.json",
        lambda d: d.update(name="other"),
    )
    load = _run(proj, level="load")
    commit = _run(proj, level="commit")
    assert load["ok"] is True and commit["ok"] is False
    assert commit["git"]["available"] in (True, False)


def test_invalid_level(proj):
    assert _run(proj, level="nope")["success"] is False


# -- handler ----------------------------------------------------------------


def test_handler_ops_validate_report_and_preflight_alias(proj):
    for op in ("validate_report", "preflight"):
        res = handle_pbip_operations(
            {
                "operation": op,
                "pbip_path": str(proj / "RPT-Sales.pbip"),
                "tom_check": "off",
                "report_author_cli": False,
                "level": "load",
            }
        )
        assert res["success"] and res["ok"] and res["operation"] == op


def test_handler_requires_pbip_path_and_valid_level(proj):
    assert handle_pbip_operations({"operation": "validate_report"})["success"] is False
    res = handle_pbip_operations({"operation": "preflight", "pbip_path": str(proj), "level": "x"})
    assert res["success"] is False


def test_not_a_pbip_path(tmp_path):
    res = run_preflight(str(tmp_path / "missing"))
    assert res["success"] is False and res["error_type"] == "not_found"


@pytest.mark.skipif(shutil.which("powerbi-report-author") is None, reason="CLI not installed")
def test_report_author_cli_findings_are_merged_read_only(proj):
    res = _run(proj, report_author=True)
    ra = res["report_author"]["RPT-Sales"]
    assert ra["ran"] is True
    assert any(f["source"] == "powerbi-report-author" for f in res["findings"])


# -- live calibration (R0201 on Desktop 2.157 opens with all of these, 2026-10-03) ----------


def test_bookmark_refs_to_deleted_visuals_do_not_block_the_load(proj):
    bm = _defn(proj) / "bookmarks" / "bm0000000000000000a1.bookmark.json"

    def dangling(d):
        sec = d["explorationState"]["sections"]["aa01overview00000001"]
        sec["visualContainers"]["deleted0000000000001"] = {}
        d["options"]["targetVisualNames"].append("deleted0000000000003")

    _edit(bm, dangling)
    res = _run(proj)
    hits = [
        f
        for f in res["findings"]
        if f["code"] in ("bookmark_visual_missing", "bookmark_target_visual_missing")
    ]
    assert hits and all(f["severity"] == "error" for f in hits)
    assert not any(f["load_blocking"] for f in hits)
    assert all(f["impact"] == "bookmark_partial" for f in hits)
    assert res["load_ok"] is True and res["commit_ok"] is False


def test_missing_tooltip_page_is_not_load_blocking_but_navigation_is(proj):
    button = (
        _defn(proj)
        / "pages"
        / "aa01overview00000001"
        / "visuals"
        / "vb01actionbutton0001"
        / "visual.json"
    )

    def tooltip_only(d):
        vco = d["visual"].setdefault("visualContainerObjects", {})
        vco["visualTooltip"] = [
            {"properties": {"section": {"expr": {"Literal": {"Value": "'gonetooltip'"}}}}}
        ]

    _edit(button, tooltip_only)
    res = _run(proj)
    tip = [f for f in res["findings"] if f["code"] == "visuallink_page_missing"]
    assert tip and not any(f["load_blocking"] for f in tip)
    assert tip[0]["impact"] == "tooltip_missing"

    def nav_away(d):
        props = d["visual"]["visualContainerObjects"]["visualLink"][0]["properties"]
        props["navigationSection"] = {"expr": {"Literal": {"Value": "'nopage'"}}}

    _edit(button, nav_away)
    res = _run(proj)
    nav = [
        f
        for f in res["findings"]
        if f["code"] == "visuallink_page_missing" and f["pointer"].endswith("navigationSection")
    ]
    assert nav and all(f["load_blocking"] for f in nav)


def test_findings_are_capped_inline_and_counted_by_code(proj):
    bm = _defn(proj) / "bookmarks" / "bm0000000000000000a1.bookmark.json"

    def many(d):
        for i in range(30):
            d["options"]["targetVisualNames"].append(f"deleted{i:013d}")

    _edit(bm, many)
    res = _run(proj, max_findings=5)
    assert len(res["findings"]) == 5 and res["findings_truncated"] is True
    assert res["findings_total"] >= 30
    group = res["by_code"]["bookmark_target_visual_missing"]
    assert group["count"] >= 30 and len(group["examples"]) == 3
    one = _run(proj, max_findings=0, examples_per_code=1)
    assert one["findings"] == []
    assert len(one["by_code"]["bookmark_target_visual_missing"]["examples"]) == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_commit_gate_ignores_preexisting_errors_outside_the_change(proj):
    """R0201 carries hundreds of pre-existing (non-fatal) dangling bookmark refs; a commit that
    touches other files must not be blocked by them - only by load blockers anywhere or by
    errors in the files the change touches."""
    bm = _defn(proj) / "bookmarks" / "bm0000000000000000a1.bookmark.json"
    _edit(bm, lambda d: d["options"]["targetVisualNames"].append("deleted0000000000009"))
    _git(proj, "init", "-q")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "baseline with debt")
    page = _defn(proj) / "pages" / "aa01overview00000001" / "page.json"
    _edit(page, lambda d: d.update(displayName=d.get("displayName", "") + " v2"))
    _git(proj, "add", "-A")
    res = _run(proj, level="commit", git_base="HEAD")
    assert res["git"]["available"]
    assert res["counts"]["preexisting_error"] >= 1
    assert res["ok"] is True, res["counts"]


def test_report_author_messages_lose_the_appended_file_path():
    from core.pbip.preflight import _cli_message

    win = 'Unknown property "x": C:' + chr(92) + "repo" + chr(92) + "R.Report" + chr(92) + "v.json"
    assert _cli_message(win) == 'Unknown property "x"'
    assert _cli_message("Bad enum: /home/u/R.Report/visual.json") == "Bad enum"
    assert _cli_message("Ratio 3:4 is fine") == "Ratio 3:4 is fine"


def test_concise_validate_report_inlines_ten_findings_by_default():
    from server.handlers.pbip_operations_handler import _preflight_cap

    assert _preflight_cap({"response_format": "concise"}) == 10
    assert _preflight_cap({}) == 200
    assert _preflight_cap({"response_format": "concise", "max_findings": 3}) == 3
