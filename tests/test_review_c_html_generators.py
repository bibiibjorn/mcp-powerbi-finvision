"""Review C-06 / G-18 remainder: the bookmark, theme-compliance and dependency-diagram HTML
generators embedded names and JSON unescaped (``</script>`` breakout, ``__TOKEN__`` look-alikes
re-substituted by the old ``str.replace`` chain)."""

from __future__ import annotations

import json
import re

from core.pbip.pbip_bookmark_html_generator import _build_html_document as build_bookmark
from core.pbip.pbip_theme_compliance_html_generator import _build_html_document as build_theme
from core.utilities import diagram_html_generator as dhg
from core.utilities.diagram_html_generator import generate_dependency_html

HOSTILE = 'x"</script><script>window.__pwned=1</script><b>&'


def _data_blob(doc: str) -> dict:
    m = re.search(r"const analysisData = (.*?);\n", doc, re.S)
    assert m, "data assignment not found"
    return json.loads(m.group(1))  # "<\/" is a valid JSON escape of "/"


def _check_generator(build):
    clean = build({"bookmarks": [{"displayName": "ok"}]}, "Model")
    hostile = build({"bookmarks": [{"displayName": HOSTILE}]}, HOSTILE)
    # The hostile name must not add a single closing script tag or raw element.
    assert clean.count("</script>") == hostile.count("</script>")
    assert "window.__pwned=1</script>" not in hostile
    assert "<title>x&quot;&lt;/script&gt;" in hostile
    # ... and the data still round-trips exactly.
    assert _data_blob(hostile)["bookmarks"][0]["displayName"] == HOSTILE


def test_bookmark_generator_escapes_name_and_data():
    _check_generator(build_bookmark)


def test_theme_generator_escapes_name_and_data():
    _check_generator(build_theme)


def test_placeholder_lookalike_values_are_not_substituted_again():
    for build in (build_bookmark, build_theme):
        doc = build({"k": "__MODEL_NAME__"}, "__DATA_JSON__")
        # A model named like a placeholder keeps its own text; it does not receive the data blob.
        assert "<title>__DATA_JSON__ - " in doc
        assert _data_blob(doc) == {"k": "__MODEL_NAME__"}


def _diagram(tmp_path, monkeypatch, name, table="Sales", mermaid="graph LR\nA-->B"):
    # The generator writes to <two levels above __file__>/exports: redirect it into tmp_path.
    fake_dir = tmp_path / "core" / "utilities"
    fake_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(dhg, "__file__", str(fake_dir / "diagram_html_generator.py"))
    path = generate_dependency_html(
        mermaid_code=mermaid,
        measure_table=table,
        measure_name=name,
        metadata={"direction": "bidirectional", "depth": 2},
        auto_open=False,
        referenced_measures=[(table, name)],
        all_measures=[{"table": table, "name": name, "expression": "1"}],
        main_item=f"{table}[{name}]",
    )
    assert path and str(tmp_path) in path
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_diagram_generator_escapes_names_json_and_mermaid(tmp_path, monkeypatch):
    clean = _diagram(tmp_path, monkeypatch, "Total", mermaid="graph LR\nA-->B")
    hostile = _diagram(
        tmp_path,
        monkeypatch,
        HOSTILE,
        table="T</script>",
        mermaid='graph LR\nA["' + HOSTILE + '"]-->B',
    )
    assert clean.count("</script>") == hostile.count("</script>")
    assert "window.__pwned=1</script>" not in hostile
    # Visible text is escaped (a ``<b>`` inside a JSON string literal is inert and may stay).
    assert '<span class="measure-name">[x&quot;&lt;/script&gt;' in hostile
    # Mermaid source is HTML-escaped inside <pre class="mermaid"> (Mermaid entity-decodes it).
    assert "A--&gt;B" in clean
    # The SVG download name is a script-safe JS literal, not a raw splice into a string.
    assert "a.download = __DOWNLOAD_NAME_JSON__" not in hostile
    assert re.search(r'a\.download = ".*_dependencies\.svg";', hostile)


def test_diagram_name_that_looks_like_a_placeholder_is_left_alone(tmp_path, monkeypatch):
    doc = _diagram(tmp_path, monkeypatch, "__DIRECTION__")
    assert '<span class="measure-name">[__DIRECTION__]</span>' in doc
