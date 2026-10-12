"""Review G-18: PBIP HTML model report (07_PBIP_Operations analyze, output_format=html).

* The model JSON was embedded raw in ``<script>``: a DAX string ``</script>`` broke out.
* ``highlightDAX`` fed unescaped DAX into ``v-html`` (``<img onerror>`` ran).
* Found while fixing: ``static/pbip_vue_app.js`` was the f-string SOURCE (``\\\\[``,
  ``\\\\n``) left over from the phase-1.1 extraction -- a JS SyntaxError, so the
  report's Vue app never mounted.

Tests fail on base ``07fd722``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from core.pbip.html_templates.vue_app import _STATIC
from core.pbip.pbip_html_generator import PbipHtmlGenerator

PAYLOAD = '"</script><script>window.__pwned=1</script>"'


def _doc(expr: str, repo: str = "Repo") -> str:
    model = {
        "tables": [{"name": "T", "measures": [{"name": "M", "expression": expr}], "columns": []}],
        "relationships": [],
    }
    return PbipHtmlGenerator()._build_html_document(model, None, {}, repo, None)


def test_script_breakout_payload_is_neutralised():
    clean, hostile = _doc("1"), _doc(PAYLOAD)
    assert clean.count("</script>") == hostile.count("</script>")
    assert "window.__pwned=1</script>" not in hostile


def test_embedded_json_round_trips():
    html = _doc(PAYLOAD + " ")
    m = re.search(r"const pbipData = (.*?);\n", html, re.S)
    assert m, "data assignment not found"
    data = json.loads(m.group(1))  # "<\/" is a valid JSON escape of "/"
    assert data["model"]["tables"][0]["measures"][0]["expression"] == PAYLOAD + " "
    assert " " not in m.group(1)


def test_repository_name_escaped_in_title():
    assert "<title>&lt;b&gt;X&lt;/b&gt; - PBIP Analysis</title>" in _doc("1", "<b>X</b>")


def test_highlight_dax_escapes_before_marking_up():
    js = _STATIC.read_text(encoding="utf-8")
    body = js[js.index("highlightDAX(expression) {") :]
    body = body[: body.index("return highlighted;")]
    assert body.index("&lt;") < body.index("dax-keyword")


def test_static_js_is_not_fstring_source():
    js = _STATIC.read_text(encoding="utf-8")
    assert "\\\\[" not in js and "\\\\n" not in js and "\\\\b" not in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_static_js_parses(tmp_path):
    js = _STATIC.read_text(encoding="utf-8").replace("__DATA_JSON__", "{}")
    p = tmp_path / "app.js"
    p.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:500]
