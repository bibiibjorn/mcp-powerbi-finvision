"""``response_format: concise`` and lists nested inside dicts.

Finding (spike 2026-09-30): ``17_Desktop_Bridge status`` with ``response_format: concise``
returned every page of every instance. The generic cap (25 per list) does apply to
``instances[].pages``, but the only trace was a top-level ``_concise_capped["pages"]`` that the
LAST instance overwrote - nothing on the instance said its list was cut. A list capped inside a
nested dict now marks its own parent with ``<key>_truncated``.
"""

from __future__ import annotations

import asyncio

from core.validation.response_format import (
    CONCISE_LIST_LIMIT,
    apply_concise,
    apply_response_format,
)
from tests._mcp_harness import make_dispatcher


def _pages(n):
    return [
        {"id": f"p{i}", "displayName": f"Page {i}", "pagePath": "C:/x/pages/" + "p" * 20}
        for i in range(n)
    ]


def _status(n_pages=(40, 30, 3)):
    return {
        "success": True,
        "status": "ready",
        "instances": [{"pid": 100 + i, "pages": _pages(n)} for i, n in enumerate(n_pages)],
    }


def test_nested_pages_are_capped_and_each_instance_says_so():
    out = apply_response_format(_status(), "concise")
    pages = [inst["pages"] for inst in out["instances"]]
    assert [len(p) for p in pages] == [CONCISE_LIST_LIMIT, CONCISE_LIST_LIMIT, 3]
    assert out["instances"][0]["pages_truncated"] == {"shown": 25, "total": 40}
    assert out["instances"][1]["pages_truncated"] == {"shown": 25, "total": 30}
    assert "pages_truncated" not in out["instances"][2]  # 3 pages: nothing was cut
    assert out["_response_format"] == "concise"


def test_detailed_and_default_formats_are_untouched():
    for fmt in ("detailed", None):
        out = apply_response_format(_status(), fmt)
        assert [len(i["pages"]) for i in out["instances"]] == [40, 30, 3]
        assert all("pages_truncated" not in i for i in out["instances"])


def test_top_level_lists_keep_the_existing_marker_only():
    out = apply_concise({"success": True, "rows": list(range(60))})
    assert len(out["rows"]) == 25 and out["_concise_capped"]["rows"] == {"shown": 25, "total": 60}
    assert "rows_truncated" not in out


def test_a_paginated_top_level_list_is_still_never_capped():
    out = apply_response_format(
        {"success": True, "rows": list(range(60))}, "concise", keep_full_keys={"rows"}
    )
    assert len(out["rows"]) == 60


def test_an_existing_truncated_marker_is_not_overwritten():
    src = {"success": True, "instances": [{"pages": _pages(40), "pages_truncated": "mine"}]}
    assert apply_concise(src)["instances"][0]["pages_truncated"] == "mine"


def test_exactly_at_the_limit_is_not_truncated():
    out = apply_concise(_status((25,)))
    assert len(out["instances"][0]["pages"]) == 25 and "pages_truncated" not in out["instances"][0]


def test_through_the_dispatcher_for_17_desktop_bridge_status(monkeypatch):
    from core.autonomous import desktop_bridge as db
    from server.handlers import desktop_bridge_handler as h
    from server.registry import HandlerRegistry

    monkeypatch.setattr(db, "op_status", lambda **k: _status())
    reg = HandlerRegistry()
    h.register_desktop_bridge_handler(reg)
    d = make_dispatcher(reg._handlers["17_Desktop_Bridge"])
    out = asyncio.run(
        d.dispatch_async("17_Desktop_Bridge", {"operation": "status", "response_format": "concise"})
    )
    assert [len(i["pages"]) for i in out["instances"]] == [25, 25, 3]
    assert out["instances"][0]["pages_truncated"]["total"] == 40
    full = asyncio.run(d.dispatch_async("17_Desktop_Bridge", {"operation": "status"}))
    assert [len(i["pages"]) for i in full["instances"]] == [40, 30, 3]
