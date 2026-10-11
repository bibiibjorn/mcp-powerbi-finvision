"""2026-09-22 deep review, slice I — 07_PBIP_Operations dependency_html (I-19, I-29).

Drives the real generator (static template + placeholder fill) and the real
handler (path guard) with a synthetic dependency payload whose page / measure
names carry HTML and script-breaking text.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import server.handlers._internal.hybrid_analysis_helpers as helpers
from core.utilities import pbip_dependency_html_generator as gen

_EVIL_PAGE = "Overview</script><script>document.title='PWNED-page'</script>"
_EVIL_MODEL = "<img src=x onerror=alert(1)>"
_QUOTE_KEY = 'Sales[Say "hi"]'


def _payload() -> dict:
    return {
        "measure_to_measure": {"Sales[Total]": [], _QUOTE_KEY: ["Sales[Total]"]},
        "measure_to_measure_reverse": {"Sales[Total]": [_QUOTE_KEY]},
        "measure_to_column": {"Sales[Total]": ["Sales[Amt]"]},
        "column_to_measure": {"Sales[Amt]": ["Sales[Total]"]},
        "visual_dependencies": {
            "v1": {
                "page": _EVIL_PAGE,
                "visual_type": "card",
                "measures": ["Sales[Total]"],
                "columns": [],
            },
            "v2": {
                "page": "Detail",
                "visual_type": "tableEx",
                "measures": ["Sales[Total]", "Sales[Total]"],
                "columns": ["Sales[Amt]"],
            },
        },
        # A value that looks like a placeholder must not be substituted again.
        "summary": {"total_measures": 2, "note": "__ITEM_DEPENDENCIES_JSON__"},
    }


def _script_body(html_text: str) -> str:
    m = re.search(r"<script>(.*?)</script>", html_text, re.S)
    assert m, "no inline <script> block"
    return m.group(1)


def _const(script: str, name: str):
    m = re.search(r"const %s = (.*?);\n" % re.escape(name), script)
    assert m, name
    return json.loads(m.group(1))


@pytest.fixture
def generated(tmp_path):
    out = tmp_path / "dep.html"
    path = gen.generate_pbip_dependency_html(
        _payload(), model_name=_EVIL_MODEL, auto_open=False, output_path=str(out)
    )
    assert path == str(out)
    return out.read_text(encoding="utf-8")


def test_script_blob_cannot_close_the_script_element(generated):
    assert "</script><script>document.title" not in generated
    # Exactly one closing tag: the template's own.
    assert generated.count("</script>") == 1
    script = _script_body(generated)
    deps = _const(script, "itemDependencies")
    pages = {v["page"] for v in deps["Sales[Total]"]["downstream"]["visuals"]}
    assert _EVIL_PAGE in pages  # data round-trips intact


def test_model_name_is_html_escaped(generated):
    assert _EVIL_MODEL not in generated
    assert "&lt;img src=x onerror=alert(1)&gt;" in generated


def test_placeholder_lookalike_in_a_value_is_not_substituted(generated):
    script = _script_body(generated)
    summary = _const(script, "modelSummary")
    assert summary["note"] == "__ITEM_DEPENDENCIES_JSON__"


def test_fill_template_is_single_pass():
    out = gen.fill_template("A=__A__ B=__B__", {"__A__": "__B__", "__B__": "x"})
    assert out == "A=__B__ B=x"


def test_script_json_escapes_comment_open_and_close_tag():
    s = gen.script_json({"k": "</script><!-- x"})
    assert "</" not in s and "<!--" not in s
    assert json.loads(s) == {"k": "</script><!-- x"}


def test_reverse_visual_index_dedupes_per_visual(generated):
    deps = _const(_script_body(generated), "itemDependencies")
    keys = [v["key"] for v in deps["Sales[Total]"]["downstream"]["visuals"]]
    assert sorted(keys) == ["v1", "v2"]  # v2 listed the measure twice
    assert [v["key"] for v in deps["Sales[Amt]"]["downstream"]["visuals"]] == ["v2"]


def test_template_binds_clicks_via_data_attributes():
    tpl = gen._STATIC.read_text(encoding="utf-8")
    # No key is ever interpolated into an inline JS string any more.
    assert not re.search(r"onclick=\"select(Item|Filter)\(", tpl)
    assert "data-select-key" in tpl
    assert "closest('[data-select-key]')" in tpl


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_escape_html_escapes_quotes_for_attribute_context(tmp_path):
    tpl = gen._STATIC.read_text(encoding="utf-8")
    m = re.search(r"function escapeHtml\(text\) \{.*?\n        \}", tpl, re.S)
    assert m, "escapeHtml not found"
    js = tmp_path / "esc.js"
    js.write_text(
        m.group(0) + "\nprocess.stdout.write(escapeHtml(process.argv[2]));\n", encoding="utf-8"
    )
    res = subprocess.run(
        ["node", str(js), _QUOTE_KEY + "<'&>"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout == "Sales[Say &quot;hi&quot;]&lt;&#39;&amp;&gt;"


# ---------------------------------------------------------------- handler


@pytest.fixture
def fake_pbip(tmp_path, monkeypatch):
    root = tmp_path / "Proj"
    (root / "Model.SemanticModel").mkdir(parents=True)
    fake_cache = SimpleNamespace(
        get_or_parse=lambda path: SimpleNamespace(
            report_data=None, dependencies=_payload(), model_data=None
        )
    )
    monkeypatch.setattr(helpers, "pbip_cache", fake_cache)
    opened = []
    monkeypatch.setattr(gen.webbrowser, "open", lambda url: opened.append(url))
    return root, opened


def test_handler_rejects_output_path_outside_allowed_roots(fake_pbip, tmp_path):
    root, _ = fake_pbip
    outside = tmp_path / "elsewhere" / "dep.html"
    res = helpers.handle_generate_pbip_dependency_diagram(
        {"pbip_folder_path": str(root), "output_path": str(outside)}
    )
    assert res["success"] is False
    assert res["error_type"] == "path_traversal_blocked"
    assert not outside.exists()


def test_handler_writes_inside_pbip_and_does_not_open_browser_by_default(fake_pbip):
    root, opened = fake_pbip
    target = root / "exports" / "dep.html"
    res = helpers.handle_generate_pbip_dependency_diagram(
        {"pbip_folder_path": str(root), "output_path": str(target)}
    )
    assert res["success"] is True, res
    assert Path(res["diagram_path"]).resolve() == target.resolve()
    assert target.exists()
    assert res["auto_opened"] is False
    assert opened == []
