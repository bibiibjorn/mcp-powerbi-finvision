"""F-17: ``06_Analysis_Operations compare old=<target> new=<target>``.

Targets (port | xmla | pbip_path | git_ref + pbip_path) all become TMSL and go through ONE engine
(normalize_tmsl + ModelDiffer); the result splits "changes that affect results" from cosmetic ones.
The XMLA loader is not live-verified: only its argument handling is tested. The git tests use a
throwaway repo; the end-to-end test also needs the TOM TMDL parser (skipped when not loadable).
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from core.comparison import compare_targets as ct
from core.comparison.model_comparison_orchestrator import ModelComparisonOrchestrator
from core.comparison.model_diff_engine import ModelDiffer
from core.comparison.tmsl_normalizer import normalize_tmsl


# ---------------------------------------------------------------------------
# parse_target
# ---------------------------------------------------------------------------
def test_parse_target_shapes():
    assert ct.parse_target({"port": 51234}, "old") == ("port", {"port": 51234})
    assert ct.parse_target(51234, "old") == ("port", {"port": 51234})
    assert ct.parse_target("51234", "new") == ("port", {"port": 51234})
    assert ct.parse_target({"pbip_path": "C:/r/R.pbip"}, "new") == (
        "pbip",
        {"pbip_path": "C:/r/R.pbip"},
    )
    assert ct.parse_target({"git_ref": "HEAD", "pbip_path": "C:/r/R.pbip"}, "old") == (
        "git",
        {"git_ref": "HEAD", "pbip_path": "C:/r/R.pbip"},
    )
    assert ct.parse_target({"xmla": {"workspace": "W", "model": "M"}}, "new") == (
        "xmla",
        {"workspace": "W", "model": "M"},
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        "not a port",
        {},
        {"port": 0},
        {"port": 70000},
        {"port": "x"},
        {"git_ref": "HEAD"},
        {"git_ref": "--output=x", "pbip_path": "p"},
        {"xmla": {"workspace": "W"}},
        {"port": 1, "pbip_path": "p"},
    ],
)
def test_parse_target_rejects(value):
    with pytest.raises(ct.TargetError):
        ct.parse_target(value, "old")


# ---------------------------------------------------------------------------
# classification on a real differ result
# ---------------------------------------------------------------------------
def tmsl(
    expr="SUM(Sales[Amount])", desc="d", fmt="0", hidden=False, extra_col=False, rel="OneDirection"
):
    cols = [
        {
            "name": "Amount",
            "dataType": "double",
            "sourceColumn": "Amount",
            "description": desc,
            "isHidden": hidden,
        }
    ]
    if extra_col:
        cols.append({"name": "Qty", "dataType": "int64", "sourceColumn": "Qty"})
    return {
        "name": "m",
        "culture": "en-US",
        "tables": [
            {
                "name": "Sales",
                "columns": cols,
                "measures": [
                    {
                        "name": "Total",
                        "expression": expr,
                        "formatString": fmt,
                        "description": desc,
                        "displayFolder": "A",
                    }
                ],
                "partitions": [
                    {"name": "p", "source": {"type": "m", "expression": "let x=1 in x"}}
                ],
            },
            {
                "name": "Date",
                "columns": [{"name": "Date", "dataType": "dateTime", "sourceColumn": "Date"}],
            },
        ],
        "relationships": [
            {
                "name": "r1",
                "fromTable": "Sales",
                "fromColumn": "Amount",
                "toTable": "Date",
                "toColumn": "Date",
                "crossFilteringBehavior": rel,
            }
        ],
    }


def diff(a, b):
    return ModelDiffer(
        normalize_tmsl(a, name="A", compatibility_level=1702),
        normalize_tmsl(b, name="B", compatibility_level=1702),
    ).compare()


def test_identical_models_have_no_changes():
    out = ct.classify_changes(diff(tmsl(), tmsl()))
    assert out["affects_results_count"] == 0 and out["cosmetic_count"] == 0


def test_metadata_only_changes_are_cosmetic():
    out = ct.classify_changes(diff(tmsl(), tmsl(desc="new text", fmt="0.0", hidden=True)))
    assert out["affects_results_count"] == 0
    fields = {f for c in out["cosmetic"] for f in c.get("fields", [])}
    assert {"description", "format_string", "is_hidden"} <= fields


def test_formatting_only_dax_is_cosmetic_but_a_real_change_affects_results():
    cosmetic = ct.classify_changes(diff(tmsl(), tmsl(expr="SUM( Sales[Amount] )")))
    assert cosmetic["affects_results_count"] == 0 and any(
        c["fields"] == ["expression"] for c in cosmetic["cosmetic"]
    )
    real = ct.classify_changes(diff(tmsl(), tmsl(expr="AVERAGE(Sales[Amount])")))
    assert [
        (c["category"], c["object"], c["change"], c["fields"]) for c in real["affects_results"]
    ] == [("measures", "Sales.Total", "modified", ["expression"])]


def test_added_columns_relationship_changes_and_mixed_fields():
    out = ct.classify_changes(diff(tmsl(), tmsl(desc="x", extra_col=True, rel="BothDirections")))
    kinds = {(c["category"], c["change"]) for c in out["affects_results"]}
    assert ("columns", "added") in kinds and ("relationships", "modified") in kinds
    assert out["cosmetic_count"] >= 1  # the description changes ride along as cosmetic
    assert all("description" not in c.get("fields", []) for c in out["affects_results"])


def test_cap_marks_truncation():
    out = ct.classify_changes(diff(tmsl(), tmsl(extra_col=True)), limit=0)
    assert (
        out["truncated"] is True
        and out["affects_results"] == []
        and out["affects_results_count"] >= 1
    )


# ---------------------------------------------------------------------------
# orchestrator.compare_targets with an injected loader
# ---------------------------------------------------------------------------
def fake_loader(models):
    def load(kind, spec):
        m = models[spec["pbip_path"]]
        return {
            "model": m,
            "compatibility_level": 1702,
            "label": Path(spec["pbip_path"]).stem,
            "source": dict(spec),
        }

    return load


def test_compare_targets_result_and_html(tmp_path):
    loader = fake_loader(
        {"old.pbip": tmsl(), "new.pbip": tmsl(expr="AVERAGE(Sales[Amount])", desc="doc")}
    )
    out = ModelComparisonOrchestrator().compare_targets(
        {"pbip_path": "old.pbip"},
        {"pbip_path": "new.pbip"},
        output_path=str(tmp_path / "d.html"),
        generate_json=True,
        loader=loader,
    )
    assert out["success"], out
    assert (
        out["summary"]["affects_results"] == 1 and out["summary"]["cosmetic"] == 2
    )  # measure + column description
    assert out["summary"]["old"] == "old" and out["summary"]["new"] == "new"
    assert out["sources"]["old"] == {"pbip_path": "old.pbip"}
    assert Path(out["reports"]["html"]).exists() and Path(out["reports"]["json"]).exists()
    assert out["affects_results"][0]["fields"] == ["expression"]


def test_compare_targets_without_report_and_with_errors():
    loader = fake_loader({"a": tmsl(), "b": tmsl()})
    out = ModelComparisonOrchestrator().compare_targets(
        {"pbip_path": "a"}, {"pbip_path": "b"}, loader=loader, report=False
    )
    assert out["success"] and "reports" not in out and out["summary"]["total_changes"] == 0
    bad = ModelComparisonOrchestrator().compare_targets(
        {"pbip_path": "a"}, {"git_ref": "HEAD"}, loader=loader
    )
    assert bad["success"] is False and bad["error_type"] == "invalid_input"

    def boom(kind, spec):
        raise ct.TargetError("no such project", "not_found")

    nf = ModelComparisonOrchestrator().compare_targets(
        {"pbip_path": "a"}, {"pbip_path": "b"}, loader=boom
    )
    assert nf["error_type"] == "not_found"


def test_a_port_side_is_exported_from_its_instance_and_released(monkeypatch):
    from core.comparison import model_comparison_orchestrator as mod

    calls = {"connect": [], "disconnect": []}
    monkeypatch.setattr(
        mod.multi_instance_manager,
        "connect_to_instance",
        lambda port: calls["connect"].append(port)
        or {"success": True, "instance_info": {"display_name": f"Desktop{port}"}},
    )
    monkeypatch.setattr(
        mod.multi_instance_manager,
        "disconnect_instance",
        lambda port: calls["disconnect"].append(port),
    )
    orch = ModelComparisonOrchestrator()
    monkeypatch.setattr(
        orch,
        "_export",
        lambda port: {
            "model": tmsl(desc=str(port)),
            "compatibility_level": 1702,
            "database_name": "x",
        },
    )
    out = orch.compare_targets(
        {"port": 5001}, {"pbip_path": "b"}, loader=fake_loader({"b": tmsl()}), report=False
    )
    assert (
        out["success"]
        and out["summary"]["old"] == "Desktop5001"
        and out["sources"]["old"] == {"port": 5001}
    )
    assert calls["connect"] == [5001] and calls["disconnect"] == [5001]


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------
def test_handler_dispatches_targets_and_keeps_the_two_port_default(monkeypatch):
    from server.handlers._internal import comparison_helpers as ch

    seen = {}
    monkeypatch.setattr(
        ModelComparisonOrchestrator,
        "compare_targets",
        lambda self, old, new, **kw: seen.update(old=old, new=new, kw=kw)
        or {
            "success": True,
            "summary": {"affects_results": 2, "cosmetic": 1, "old": "a", "new": "b"},
        },
    )
    out = ch.handle_compare_pbi_models(
        {"old": {"port": 1}, "new": {"pbip_path": "p"}, "report": False}
    )
    assert out["success"] and seen["old"] == {"port": 1} and seen["kw"]["report"] is False
    assert "2 change(s) can affect results" in out["workflow"]["summary"]
    assert ch.handle_compare_targets({"old": {"port": 1}})["error_type"] == "invalid_input"
    assert (
        ch.handle_compare_targets({"old": {"port": 1}, "new": {"port": 2}, "old_port": 1})[
            "error_type"
        ]
        == "invalid_input"
    )
    # no old/new: the existing two-open-models path (needs a connection) still answers first
    monkeypatch.setattr(ch.connection_state, "is_connected", lambda: False)
    assert ch.handle_compare_pbi_models({})["error_type"] == "not_connected"


def test_old_new_are_declared_and_deferred():
    from server.handlers import register_all_handlers
    from server.registry import get_registry

    reg = get_registry()
    if reg.total_tools() == 0:
        register_all_handlers(reg)
    props = reg.get_full_schema("06_Analysis_Operations")["properties"]
    assert {"old", "new", "report"} <= set(props)
    assert not {"old", "new"} & set(reg.get_emitted_schema("06_Analysis_Operations")["properties"])


def test_ruleset_all_drops_microsoft_rules_a_curated_rule_supersedes(monkeypatch):
    """Hand-off from the DAX WP: `superseded_rule_ids` is applied before the Microsoft rules are added."""
    from core.analysis import bpa_analyzer
    from server.handlers import analysis_handler as ah

    local, twin = next(iter(bpa_analyzer.RULE_SUPERSEDES.items()))
    twin_id = next(iter(twin))
    fake_rules = [type("R", (), {"id": local})()]
    monkeypatch.setattr(
        ah.connection_state, "bpa_analyzer", type("A", (), {"rules": fake_rules})(), raising=False
    )
    ms = [type("R", (), {"id": twin_id})(), type("R", (), {"id": "MS_ONLY"})()]
    monkeypatch.setattr(ah, "_ruleset_analyzer", lambda name: type("A", (), {"rules": ms})())
    monkeypatch.setattr(ah.connection_state, "is_connected", lambda: True)
    captured = {}

    class Orch:
        def comprehensive_analysis(self, state, **kw):
            captured.update(kw)
            return {"success": True, "issues": []}

    monkeypatch.setattr(
        ah.connection_state,
        "agent_policy",
        type("P", (), {"analysis_orch": Orch()})(),
        raising=False,
    )
    ah.handle_full_analysis({"ruleset": "all"})
    extra = [r.id for r in captured.get("extra_rules") or []]
    assert extra == ["MS_ONLY"], extra


# ---------------------------------------------------------------------------
# git extraction (throwaway repo) + end to end with the real TMDL parser
# ---------------------------------------------------------------------------
pytestmark_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

TMDL_TABLE = textwrap.dedent("""\
    table Sales
    \tlineageTag: 11111111-1111-1111-1111-111111111111

    {desc}\tmeasure Total = {expr}
    \t\tformatString: 0

    \tcolumn Amount
    \t\tdataType: double
    \t\tlineageTag: 22222222-2222-2222-2222-222222222222
    \t\tsummarizeBy: sum
    \t\tsourceColumn: Amount

    \tpartition Sales = m
    \t\tmode: import
    \t\tsource = let x = 1 in x
    """)


def write_project(root: Path, expr: str, desc: str = "", name: str = "Proj") -> Path:
    defn = root / f"{name}.SemanticModel" / "definition"
    (defn / "tables").mkdir(parents=True, exist_ok=True)
    (defn / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1702\n", encoding="utf-8")
    (defn / "model.tmdl").write_text(
        "model Model\n\tculture: en-US\n\nref table Sales\n", encoding="utf-8"
    )
    d = f"\t/// {desc}\n" if desc else ""
    (defn / "tables" / "Sales.tmdl").write_text(
        TMDL_TABLE.format(expr=expr, desc=d), encoding="utf-8"
    )
    return root / f"{name}.SemanticModel"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    model = write_project(root, "SUM(Sales[Amount])")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root, model


def test_git_extraction_is_read_only_and_reads_the_committed_files(repo, tmp_path):
    root, model = repo
    write_project(root, "AVERAGE(Sales[Amount])")  # working tree now differs from HEAD
    before = git(root, "status", "--porcelain")
    dest = tmp_path / "out" / "definition"
    folder, label = ct._extract_git_definition("HEAD", str(model), dest)
    assert label == "Proj" and folder == dest
    assert "SUM(Sales[Amount])" in (dest / "tables" / "Sales.tmdl").read_text(encoding="utf-8")
    assert git(root, "status", "--porcelain") == before  # nothing checked out, nothing staged
    other = write_project(root, "1", name="Other")  # on disk but never committed
    with pytest.raises(ct.TargetError, match="does not exist at HEAD"):
        ct._extract_git_definition("HEAD", str(other), tmp_path / "x")
    with pytest.raises(ct.TargetError, match="git ls-tree"):
        ct._extract_git_definition("no-such-ref", str(model), tmp_path / "y")


def _tom_available():
    try:
        from core.infrastructure.dll_paths import load_amo_assemblies

        return bool(load_amo_assemblies())
    except Exception:  # noqa: BLE001
        return False


def test_end_to_end_git_head_vs_working_tree(repo, tmp_path):
    if not _tom_available():
        pytest.skip("TOM assemblies not loadable here")
    root, model = repo
    write_project(root, "AVERAGE(Sales[Amount])", desc="documented")
    out = ModelComparisonOrchestrator().compare_targets(
        {"git_ref": "HEAD", "pbip_path": str(model)},
        {"pbip_path": str(model)},
        report=False,
    )
    assert out["success"], out
    assert out["summary"]["old"] == "Proj@HEAD" and out["summary"]["new"] == "Proj"
    assert out["summary"]["affects_results"] == 1 and out["summary"]["cosmetic"] == 1
    assert out["affects_results"][0]["object"].endswith("Total") and out["affects_results"][0][
        "fields"
    ] == ["expression"]
    assert out["cosmetic"][0]["fields"] == ["description"]
    # the same commit against itself is clean
    same = ModelComparisonOrchestrator().compare_targets(
        {"git_ref": "HEAD", "pbip_path": str(model)},
        {"git_ref": "HEAD", "pbip_path": str(model)},
        report=False,
    )
    assert same["summary"]["total_changes"] == 0


def test_missing_pbip_is_not_found():
    with pytest.raises(ct.TargetError) as exc:
        ct.load_target("pbip", {"pbip_path": "C:/definitely/not/here.pbip"})
    assert exc.value.error_type == "not_found"
