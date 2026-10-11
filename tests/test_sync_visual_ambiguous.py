"""Regression: sync_visual must not SILENTLY pick the first match when the
source is ambiguous.

When source_visual_name/display_title matches multiple visuals and no
source_page disambiguates, the op used to resolve to source_visuals[0] with no
signal — a silent wrong-target sync on duplicate-titled template visuals. It
now returns a structured `ambiguous_match` error listing the candidates,
mirroring the 17_Desktop_Bridge ambiguous_instance convention.
"""

from pathlib import Path

import server.handlers.visual_layout_handler as vlh


def test_sync_visual_ambiguous_source_returns_error(monkeypatch):
    # Two source matches, no source_page → must be an ambiguous_match error,
    # not a silent pick.
    candidates = [
        {
            "visual_name": "v1",
            "page_name": "Page A",
            "file_path": "/x/a/v1/visual.json",
            "display_title": "KPI",
        },
        {
            "visual_name": "v2",
            "page_name": "Page B",
            "file_path": "/x/b/v2/visual.json",
            "display_title": "KPI",
        },
    ]
    monkeypatch.setattr(vlh, "_find_visuals", lambda *a, **k: candidates)
    out = vlh._op_sync_visual({"display_title": "KPI"}, Path("/x"))
    assert out["success"] is False
    assert out["error_type"] == "ambiguous_match"
    # Candidates surfaced so the caller can disambiguate without a list call.
    cand = out.get("candidates") or []
    assert len(cand) == 2
    assert {c.get("page_name") for c in cand} == {"Page A", "Page B"}


def test_sync_visual_single_source_not_ambiguous(monkeypatch):
    # Exactly one match → no ambiguity error (fails later for another reason,
    # but NOT ambiguous_match).
    monkeypatch.setattr(
        vlh,
        "_find_visuals",
        lambda *a, **k: [
            {"visual_name": "v1", "page_name": "P", "file_path": "/x/p/v1/visual.json"}
        ],
    )
    monkeypatch.setattr(vlh, "_load_json_file", lambda p: None)  # force a later benign failure
    out = vlh._op_sync_visual({"display_title": "Solo"}, Path("/x"))
    assert out.get("error_type") != "ambiguous_match"


def test_sync_visual_missing_source_has_error_type(monkeypatch):
    monkeypatch.setattr(vlh, "_find_visuals", lambda *a, **k: [])
    out = vlh._op_sync_visual({"display_title": "Nope"}, Path("/x"))
    assert out["success"] is False
    assert out.get("error_type")
