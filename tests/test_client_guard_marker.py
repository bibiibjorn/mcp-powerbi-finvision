"""The Power BI chat marker: a per-install id, stamped on every MCP result and found by the gateway."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from core.client_guard import marker as mk

OBJ = {"_fv": "PLACEHOLDER", "rows": [{"name": "Zorblax Holdings NV", "n": 3}]}


def _stamped(home) -> tuple[str, dict]:
    m = mk.marker(home)
    return m, {**OBJ, "_fv": m}


def test_install_id_is_16_lowercase_hex_and_stable(tmp_path):
    first = mk.install_id(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    assert mk.install_id(tmp_path) == first
    assert (tmp_path / "install.id").read_text(encoding="ascii").strip() == first


def test_install_id_creates_a_missing_home(tmp_path):
    home = tmp_path / "a" / "b"
    ident = mk.install_id(home)
    assert (home / "install.id").is_file()
    assert re.fullmatch(r"[0-9a-f]{16}", ident)


def test_install_id_is_per_home(tmp_path):
    assert mk.install_id(tmp_path / "one") != mk.install_id(tmp_path / "two")


def test_install_id_keeps_an_existing_valid_file(tmp_path):
    (tmp_path / "install.id").write_text("0123456789abcdef\n", encoding="ascii")
    assert mk.install_id(tmp_path) == "0123456789abcdef"


def test_install_id_replaces_a_garbage_file(tmp_path):
    (tmp_path / "install.id").write_text("not an id", encoding="ascii")
    ident = mk.install_id(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{16}", ident)
    assert mk.install_id(tmp_path) == ident


def test_install_id_create_is_atomic_under_a_race(tmp_path):
    home = tmp_path / "fresh"
    with ThreadPoolExecutor(max_workers=16) as pool:
        ids = list(pool.map(lambda _i: mk.install_id(home), range(48)))
    assert len(set(ids)) == 1
    assert (home / "install.id").read_text(encoding="ascii").strip() == ids[0]
    assert not list(home.glob("*.tmp"))


def test_marker_format_and_field(tmp_path):
    m = mk.marker(tmp_path)
    assert m == "fvpbi-" + mk.install_id(tmp_path)
    assert mk.MARKER_FIELD == "_fv"


def test_has_marker_on_plain_bytes_and_text(tmp_path):
    m = mk.marker(tmp_path)
    text = json.dumps({"_fv": m, "x": 1})
    assert mk.has_marker(text, m)
    assert mk.has_marker(text.encode("utf-8"), m)
    assert not mk.has_marker('{"x": 1}', m)
    assert not mk.has_marker(b"", m)


def test_has_marker_in_json_escaped_and_double_escaped_payloads(tmp_path):
    m, stamped = _stamped(tmp_path)
    once = json.dumps(json.dumps(stamped))  # a tool_result: JSON inside a JSON string
    twice = json.dumps(once)  # ... inside another one (history replayed in a request)
    assert '\\"_fv\\"' in once and '\\\\\\"_fv' in twice
    for payload in (once, twice):
        assert mk.has_marker(payload, m)
        assert mk.has_marker(payload.encode("utf-8"), m)
    unicode_escaped = json.dumps({"name": "Zoë", "_fv": m}, ensure_ascii=True)
    assert mk.has_marker(unicode_escaped.encode("ascii"), m)


def test_no_false_positive_on_another_installs_marker(tmp_path):
    mine = mk.marker(tmp_path / "mine")
    theirs = mk.marker(tmp_path / "theirs")
    payload = json.dumps({"_fv": theirs})
    assert not mk.has_marker(payload, mine)
    assert not mk.has_marker(payload.encode(), mine)
    assert not mk.has_marker(json.dumps(payload), mine)
    assert mk.has_marker(payload, theirs)


def test_no_false_positive_on_the_bare_prefix_or_stripped_form(tmp_path):
    m = mk.marker(tmp_path)
    assert not mk.has_marker('{"_fv":"fvpbi"}', m)
    assert not mk.has_marker("fvpbi-", m)


def test_strip_marker_keeps_json_valid_and_replaces_with_fvpbi(tmp_path):
    m, stamped = _stamped(tmp_path)
    out = mk.strip_marker(json.dumps(stamped), m)
    assert isinstance(out, str)
    assert m not in out
    assert json.loads(out)["_fv"] == "fvpbi"
    assert json.loads(out)["rows"] == OBJ["rows"]


def test_strip_marker_on_bytes_returns_bytes(tmp_path):
    m, stamped = _stamped(tmp_path)
    out = mk.strip_marker(json.dumps(stamped).encode("utf-8"), m)
    assert isinstance(out, bytes)
    assert m.encode() not in out
    assert json.loads(out)["_fv"] == "fvpbi"


def test_strip_marker_inside_escaped_payloads_keeps_every_layer_valid(tmp_path):
    m, stamped = _stamped(tmp_path)
    once = json.dumps(json.dumps(stamped))
    twice = json.dumps(once)
    out_once = mk.strip_marker(once, m)
    out_twice = mk.strip_marker(twice, m)
    assert m not in out_once and m not in out_twice
    assert json.loads(json.loads(out_once))["_fv"] == "fvpbi"
    assert json.loads(json.loads(json.loads(out_twice)))["_fv"] == "fvpbi"


def test_strip_marker_replaces_every_occurrence(tmp_path):
    m = mk.marker(tmp_path)
    out = mk.strip_marker(f"a {m} b {m} c", m)
    assert out == "a fvpbi b fvpbi c"


def test_strip_marker_without_the_marker_is_identity(tmp_path):
    m = mk.marker(tmp_path)
    data = b'{"x": "\\u00e9"}'
    assert mk.strip_marker(data, m) == data
    other = mk.marker(tmp_path / "other")
    foreign = json.dumps({"_fv": other})
    assert mk.strip_marker(foreign, m) == foreign


def test_strip_then_has_marker_is_false(tmp_path):
    m, stamped = _stamped(tmp_path)
    assert not mk.has_marker(mk.strip_marker(json.dumps(stamped), m), m)
