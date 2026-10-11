"""F-11: headless CLI (src/cli.py). Subprocess tests use offline ops on tests/fixtures/pbir_golden only
(no Desktop, no network); the orchestration (args, exit codes, redaction, connect order) is tested in
process against a fake dispatcher."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "fixtures" / "pbir_golden" / "GoldenCo"
PY = sys.executable

sys.path.insert(0, str(ROOT))
from src import cli  # noqa: E402


def run(*args, stdin=None, env_extra=None, timeout=120):
    env = dict(os.environ)
    env.pop("ANTHROPIC_BASE_URL", None)
    env.update(env_extra or {})
    return subprocess.run(
        [PY, "-m", "src.cli", *args],
        cwd=str(ROOT),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        env=env,
    )


# --------------------------------------------------------------------------- subprocess contract
def test_list_tools_json_comes_from_the_registry():
    p = run("--list-tools")
    assert p.returncode == 0, p.stderr
    tools = {t["tool"]: t for t in json.loads(p.stdout)}
    assert len(tools) == 28
    assert {"tieout", "test_generate", "equivalence", "test_run"} <= set(
        tools["09_Validate"]["operations"]
    )
    assert "rls_audit" in tools["04_Query_Operations"]["operations"]


def test_offline_read_op_prints_only_json_and_exits_0():
    p = run(
        "07_Page_Operations",
        json.dumps({"operation": "list", "pbip_path": str(GOLDEN / "RPT-Sales.Report")}),
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)  # stdout is ONLY the result
    assert out["success"] is True and out["count"] == 3


def test_tool_operation_shorthand_and_args_file(tmp_path):
    f = tmp_path / "args.json"
    f.write_text(json.dumps({"pbip_path": str(GOLDEN)}), encoding="utf-8-sig")  # BOM tolerated
    p = run("07_PBIP_Operations:scan_broken_refs", "--file", str(f))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["broken_references"] == []


def test_args_from_stdin():
    p = run(
        "07_PBIP_Operations:scan_broken_refs",
        "--file",
        "-",
        stdin=json.dumps({"pbip_path": str(GOLDEN)}),
    )
    assert p.returncode == 0 and json.loads(p.stdout)["success"] is True


def test_tool_error_exits_1_with_the_envelope():
    p = run(
        "07_Page_Operations",
        json.dumps({"operation": "list", "pbip_path": str(ROOT / "no" / "such")}),
    )
    assert p.returncode == 1
    out = json.loads(p.stdout)
    assert out["success"] is False and out["error_type"] == "not_found"


@pytest.mark.parametrize(
    "argv",
    [
        [],  # no tool
        ["99_Nope", "{}"],  # unknown tool
        ["07_Page_Operations", "{not json"],  # bad json
        ["07_Page_Operations", "[1,2]"],  # not an object
        ["07_Page_Operations", "{}", "--file", "x.json"],  # both inline and --file
        ["07_Page_Operations", "--file", "does-not-exist.json"],
        ["07_Page_Operations:list", '{"operation":"info"}'],  # operation conflict
        ["07_Page_Operations", "{}", "--format", "xml"],
    ],
)
def test_usage_errors_exit_2_and_print_nothing_on_stdout(argv):
    p = run(*argv)
    assert p.returncode == 2, (argv, p.stdout, p.stderr)
    assert p.stdout == "" and "error" in p.stderr.lower()


def test_connect_folder_runs_first_and_offline_lane_is_explicit(tmp_path):
    copy = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, copy)
    suite = copy / "t.suite.json"
    suite.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "t",
                "tests": [{"id": "a", "type": "measure_assertion", "measure": "M", "expected": 1}],
            }
        ),
        encoding="utf-8",
    )
    base = ["--connect-folder", str(copy / "MDL-Core.pbip")]
    listed = run(*base, "09_Validate:test_list", json.dumps({"suite_path": str(suite)}))
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert (
        json.loads(listed.stdout)["test_count"] == 1
    )  # the project folder is an allowed root offline
    ran = run(*base, "09_Validate:test_run", json.dumps({"suite_path": str(suite)}))
    assert ran.returncode == 1
    assert json.loads(ran.stdout)["error_type"] == "no_dax_engine"


def test_failed_connect_folder_reports_and_exits_1(tmp_path):
    p = run("--connect-folder", str(tmp_path / "missing.pbip"), "09_Validate:test_list", "{}")
    assert p.returncode == 1 and json.loads(p.stdout)["success"] is False


def test_text_format_and_version():
    p = run(
        "--format",
        "text",
        "07_Page_Operations",
        json.dumps({"operation": "list", "pbip_path": str(GOLDEN / "RPT-Sales.Report")}),
    )
    assert p.returncode == 0 and "success: True" in p.stdout and "count: 3" in p.stdout
    v = run("--version")
    assert v.returncode == 0 and v.stdout.strip().count(".") >= 2


def test_help_lists_tools_from_the_registry():
    p = run("--help")
    assert p.returncode == 0 and "09_Validate" in p.stdout and "--connect-folder" in p.stdout


# --------------------------------------------------------------------------- in-process
class FakeDispatcher:
    def __init__(self, results):
        self.calls = []
        self.results = results

    def dispatch(self, tool, args):
        self.calls.append((tool, dict(args)))
        r = self.results.get(tool)
        return r(args) if callable(r) else (r if r is not None else {"success": True})


class FakeRegistry:
    def __init__(self, names):
        self.names = names

    def has_tool(self, n):
        return n in self.names

    def get_all_tools(self):
        from types import SimpleNamespace

        return [SimpleNamespace(name=n, input_schema={}, description=f"{n} d") for n in self.names]


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def install(results):
        d = FakeDispatcher(results)
        holder["d"] = d
        monkeypatch.setattr(
            cli,
            "bootstrap",
            lambda: (FakeRegistry({"09_Validate", "01_Connection", "07_Page_Operations"}), d),
        )
        return d

    return install


def test_exit_code_prefers_the_result_exit_code():
    assert cli.exit_code_for({"success": True, "exit_code": 1}) == 1  # failed test_run
    assert cli.exit_code_for({"success": True, "exit_code": 0}) == 0
    assert cli.exit_code_for({"success": False}) == 1
    assert cli.exit_code_for({"success": True}) == 0
    assert (
        cli.exit_code_for({"exit_code": True, "success": True}) == 0
    )  # a bool is not an exit code


def test_main_dispatches_and_maps_exit_codes(fake, capsys):
    d = fake({"09_Validate": {"success": True, "exit_code": 1, "passed": 0}})
    assert cli.main(["09_Validate:test_run", '{"suite_path":"s.json"}']) == 1
    assert d.calls == [("09_Validate", {"suite_path": "s.json", "operation": "test_run"})]
    assert json.loads(capsys.readouterr().out)["passed"] == 0


def test_connect_failure_stops_before_the_tool(fake, capsys):
    d = fake({"01_Connection": {"success": False, "error": "nope", "error_type": "tmdl_invalid"}})
    assert cli.main(["--connect-folder", "M.pbip", "09_Validate:test_list", "{}"]) == 1
    assert [c[0] for c in d.calls] == ["01_Connection"]
    assert d.calls[0][1] == {"operation": "connect_folder", "pbip_path": "M.pbip"}
    assert json.loads(capsys.readouterr().out)["error_type"] == "tmdl_invalid"


def test_connect_xmla_then_tool(fake):
    d = fake({})
    assert cli.main(["--connect-xmla", "WS", "Model", "09_Validate:test_list", "{}"]) == 0
    assert d.calls[0] == (
        "01_Connection",
        {"operation": "connect_xmla", "workspace": "WS", "model": "Model"},
    )
    assert d.calls[1][0] == "09_Validate"


def test_unknown_tool_is_usage_error(fake, capsys):
    fake({})
    assert cli.main(["99_Nope", "{}"]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "unknown tool" in cap.err


def test_stray_prints_never_reach_stdout(fake, capsys):
    def noisy(args):
        print("library chatter")
        return {"success": True}

    fake({"09_Validate": noisy})
    assert cli.main(["09_Validate", '{"operation":"test_list"}']) == 0
    cap = capsys.readouterr()
    assert "library chatter" not in cap.out and "library chatter" in cap.err
    json.loads(cap.out)


def test_secrets_are_redacted(monkeypatch):
    monkeypatch.setenv("MCP_PBI_XMLA_TOKEN", "eyJhbGciOiJSUzI1NiIs.secretpayload")
    out = cli.redact(
        {
            "success": True,
            "connection_string": "Data Source=powerbi://x;Password=hunter2",
            "xmla": {"access_token": "abc", "workspace": "WS"},
            "note": "failed with token eyJhbGciOiJSUzI1NiIs.secretpayload in header",
            "list": [{"api_key": "k", "n": 1}],
            "empty_password": "",
        }
    )
    text = json.dumps(out)
    assert (
        "hunter2" not in text
        and "secretpayload" not in text
        and '"abc"' not in text
        and '"k"' not in text
    )
    assert out["xmla"]["workspace"] == "WS" and out["list"][0]["n"] == 1
    assert out["empty_password"] == ""  # nothing to hide


def test_render_text_shapes_errors_and_nested_values():
    txt = cli.render_result(
        {"success": False, "error_type": "x", "error": "boom", "rows": [{"a": 1}], "n": 2}, "text"
    )
    assert txt.splitlines()[0] == "ERROR [x]: boom" and 'rows: [{"a": 1}]' in txt and "n: 2" in txt


def test_parse_arguments_rules():
    p = cli.build_parser()
    ns = p.parse_args(["09_Validate:test_run", '{"ids":["a"]}'])
    assert cli.parse_arguments(ns) == ("09_Validate", {"ids": ["a"], "operation": "test_run"}, None)
    ns = p.parse_args(["09_Validate"])
    assert cli.parse_arguments(ns) == ("09_Validate", {}, None)
    ns = p.parse_args(["09_Validate:a", '{"operation":"b"}'])
    assert cli.parse_arguments(ns)[2].startswith("operation conflict")


def test_root_flag_extends_the_path_guard(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FINVISION_ALLOWED_ROOTS", "")  # monkeypatch restores whatever main() sets
    fake({})
    assert cli.main(["--root", str(tmp_path), "09_Validate", '{"operation":"test_list"}']) == 0
    from core.validation import path_guard

    assert tmp_path.resolve() in path_guard._resolved_root_list()


def test_dispatcher_and_cli_see_the_same_tools():
    """The CLI help/listing is generated from the registry (no hand-kept copy)."""
    from server.handlers import register_all_handlers
    from server.registry import HandlerRegistry

    reg = HandlerRegistry()
    register_all_handlers(reg)
    names = {s["tool"] for s in cli.tool_summaries(reg)}
    assert names == {t.name for t in reg.get_all_tools()}
