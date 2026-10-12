"""Tests for core.dax.dax_formatter_client (network-mocked)."""

from core.dax import dax_formatter_client as dfc


class _FakeResp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.content = (body or "").encode("utf-8") if isinstance(body, str) else b"x"
        self.text = body if isinstance(body, str) else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def test_format_one_returns_formatted(monkeypatch):
    monkeypatch.setattr(dfc, "_DAXFORMATTER_URL", "https://example.invalid/api")

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResp("SUM(<br/>&nbsp;'Sales'[Amount]<br/>)")

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    out = dfc.format_one("SUM('Sales'[Amount])", max_line_length=80)
    assert out["success"] is True
    assert "SUM(" in out["formatted"]
    assert captured["payload"]["MaxLineLength"] == 80


def test_format_one_with_empty_input_returns_empty():
    out = dfc.format_one("   ")
    assert out["success"] is True
    assert out["formatted"] == ""


def test_format_one_handles_plain_text_response(monkeypatch):
    """The canonical Azure endpoint returns plain text (not HTML, not JSON)."""

    def fake_post(url, json=None, timeout=None):
        # Daxformatter returns formatted DAX as plain text with CRLF
        return _FakeResp('EVALUATE\r\nROW ( "x", SUM ( Sales[Amount] ) )\r\n')

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    out = dfc.format_one('EVALUATE ROW("x", SUM(Sales[Amount]))')
    assert out["success"] is True
    assert "EVALUATE" in out["formatted"]
    assert "ROW (" in out["formatted"]


def test_format_many_aggregates_stats(monkeypatch):
    def fake_format_one(expr, **kw):
        if "BAD" in expr:
            return {"success": False, "error": "rejected"}
        # Insert a real character change so it counts as "succeeded"
        return {"success": True, "formatted": "FORMATTED_" + expr, "original": expr}

    monkeypatch.setattr(dfc, "format_one", fake_format_one)
    out = dfc.format_many(
        [
            {"key": "T[a]", "expression": "SUM(T[X])"},
            {"key": "T[b]", "expression": "SUM(T[X]) + BAD"},
            {"key": "T[c]", "expression": "1+1"},
        ],
        delay_s=0,
    )
    assert out["stats"]["total"] == 3
    assert out["stats"]["failed"] == 1
    assert out["stats"]["succeeded"] + out["stats"]["unchanged"] == 2
