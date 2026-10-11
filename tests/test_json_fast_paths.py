"""WP-P wave 2 -- JSON fast paths (P-12): orjson response serializer + C-encoder exports."""

from __future__ import annotations

import dataclasses
import datetime as dt
import decimal
import io
import json
import uuid

import pytest

import server.response_pipeline as rp
from core.utilities.json_utils import MCPJsonEncoder
from server.handlers.query_handler import _write_json_rows

needs_orjson = pytest.mark.skipif(rp._orjson is None, reason="orjson not installed")


def _stdlib(result):
    return json.dumps(rp._sanitize_non_finite(result), separators=(",", ":"), cls=MCPJsonEncoder)


@dataclasses.dataclass
class _DC:
    a: int = 1


SAMPLES = [
    {"success": True, "rows": [{"a": 1, "b": 2.5, "c": "x", "d": None, "e": True}]},
    {"when": dt.datetime(2026, 10, 3, 12, 30, 5, 123456), "day": dt.date(2026, 10, 3)},
    {"aware": dt.datetime(2026, 10, 3, 12, 0, tzinfo=dt.timezone.utc)},
    {"dec": decimal.Decimal("1234.50"), "id": uuid.UUID("12345678-1234-1234-1234-123456789abc")},
    {"set": {3, 1, 2}, "bytes": b"abcd", "tuple": (1, 2, 3)},
    {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf"), "ok": 1.5},
    {1: "int key", True: "bool key", None: "none key", 2.5: "float key"},  # noqa: F601
    {"text": "café € \U0001f600 Δ", "quote": 'He said "hi"\n\t\\'},
    {"nested": {"a": [{"b": [1, 2, {"c": None}]}]}, "empty": {}, "list": []},
    {"dc": _DC(), "obj": object},
]


@needs_orjson
@pytest.mark.parametrize("sample", SAMPLES)
def test_orjson_document_equals_the_stdlib_document(sample):
    fast = rp._serialize_orjson(sample)
    assert fast is not None
    assert json.loads(fast) == json.loads(_stdlib(sample))


@needs_orjson
def test_ascii_data_is_byte_identical_to_the_stdlib_compact_text():
    sample = {"success": True, "rows": [{"i": i, "f": i / 7, "s": f"v{i}"} for i in range(200)]}
    assert rp._serialize_orjson(sample) == _stdlib(sample)


@needs_orjson
def test_non_ascii_is_raw_utf8_but_the_same_json():
    text = rp.serialize({"t": "café \U0001f600"})
    assert "\\u" not in text and json.loads(text) == {"t": "café \U0001f600"}


@needs_orjson
def test_what_orjson_refuses_falls_back_to_the_stdlib_encoder():
    big = {"n": 2**80}  # beyond orjson's 64-bit range
    assert rp._serialize_orjson(big) is None
    assert json.loads(rp.serialize(big)) == {"n": 2**80}
    weird = {(1, 2): "tuple key"}  # TypeError for both encoders, as before
    with pytest.raises(TypeError):
        rp.serialize(weird)


def test_serialize_works_without_orjson(monkeypatch):
    monkeypatch.setattr(rp, "_orjson", None)
    out = rp.serialize({"nan": float("nan"), "d": dt.date(2026, 1, 2), "s": "café"})
    assert json.loads(out) == {"nan": None, "d": "2026-01-02", "s": "café"}
    assert "\\u00e9" in out  # the stdlib path keeps its ASCII escapes


@pytest.mark.parametrize("n", [0, 1, 4999, 5000, 5001, 12345])
def test_json_export_text_is_identical_to_json_dump(n):
    rows = [
        {"[a]": i, "[b]": f"café {i}", "[c]": dt.date(2026, 1, 1) if i % 2 else None, "[d]": i / 3}
        for i in range(n)
    ]
    expected = io.StringIO()
    json.dump(rows, expected, ensure_ascii=False, default=str)
    got = io.StringIO()
    _write_json_rows(got, rows)
    assert got.getvalue() == expected.getvalue()
