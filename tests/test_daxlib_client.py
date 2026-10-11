"""Tests for daxlib REST client (HTTP mocked)."""

from core.operations import daxlib_client as dc


class _FakeResp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.content = b"x" if body else b""
        self.text = body if isinstance(body, str) else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def _set_get(monkeypatch, body):
    import requests

    dc.clear_catalog_cache()  # C-38: the catalog is cached for 10 minutes
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResp(body))


# ---- search_packages ------------------------------------------------------


def test_search_packages_normalizes_list_response(monkeypatch):
    _set_get(
        monkeypatch,
        [
            {
                "id": "money.formats",
                "name": "Money formats",
                "version": "1.0",
                "description": "EUR/USD helpers",
                "function_count": 3,
            },
            {
                "id": "time.intel",
                "name": "Time intelligence",
                "version": "0.5",
                "description": "YTD/MTD/LY",
                "function_count": 5,
            },
        ],
    )
    res = dc.search_packages()
    assert res["success"] is True
    assert len(res["packages"]) == 2
    assert res["packages"][0]["id"] == "money.formats"


def test_search_packages_filters_by_query(monkeypatch):
    _set_get(
        monkeypatch,
        [
            {"id": "money.formats", "name": "Money", "description": "currency"},
            {"id": "time.intel", "name": "Time", "description": "dates"},
        ],
    )
    res = dc.search_packages(query="curr")
    assert res["success"] is True
    assert len(res["packages"]) == 1
    assert res["packages"][0]["id"] == "money.formats"


def test_search_packages_handles_dict_wrapper(monkeypatch):
    _set_get(
        monkeypatch,
        {"packages": [{"id": "x", "name": "X"}]},
    )
    res = dc.search_packages()
    assert res["success"] is True
    assert len(res["packages"]) == 1


def test_search_packages_unexpected_shape(monkeypatch):
    _set_get(monkeypatch, "not a list or dict-with-packages")
    res = dc.search_packages()
    assert res["success"] is False
    assert res["error_type"] == "daxlib_api_changed"


def test_search_packages_http_error(monkeypatch):
    import requests

    def fake_get(*a, **kw):
        raise RuntimeError("connection refused")

    dc.clear_catalog_cache()
    monkeypatch.setattr(requests, "get", fake_get)
    res = dc.search_packages()
    assert res["success"] is False
    assert res["error_type"] == "daxlib_api_unreachable"


# ---- fetch_package --------------------------------------------------------


def test_fetch_package_normalizes_functions(monkeypatch):
    _set_get(
        monkeypatch,
        {
            "id": "money.formats",
            "name": "Money formats",
            "version": "1.0",
            "functions": [
                {
                    "name": "FormatEUR",
                    "expression": 'FORMAT([Amount], "€#,##0")',
                    "parameters": [{"name": "Amount", "type": "Double"}],
                },
                {"name": "FormatUSD", "expression": 'FORMAT([Amount], "$#,##0")'},
            ],
        },
    )
    res = dc.fetch_package("money.formats")
    assert res["success"] is True
    assert len(res["functions"]) == 2
    assert res["functions"][0]["name"] == "FormatEUR"
    assert res["functions"][0]["parameters"][0]["name"] == "Amount"


def test_fetch_package_skips_malformed_functions(monkeypatch):
    _set_get(
        monkeypatch,
        {
            "name": "x",
            "functions": [
                {"name": "Good", "expression": "1"},
                {"name": "Missing expression"},  # skip
                {"expression": "no name"},  # skip
            ],
        },
    )
    res = dc.fetch_package("x")
    assert res["success"] is True
    assert len(res["functions"]) == 1
    assert res["skipped_malformed"] == 2


def test_fetch_package_empty_id():
    res = dc.fetch_package("")
    assert res["success"] is False
    assert res["error_type"] == "invalid_parameters"


def test_fetch_package_http_error(monkeypatch):
    import requests

    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nope"))
    )
    res = dc.fetch_package("anything")
    assert res["success"] is False
    assert res["error_type"] == "daxlib_api_unreachable"
