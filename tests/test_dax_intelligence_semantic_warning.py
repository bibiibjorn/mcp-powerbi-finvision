"""05_DAX_Intelligence must warn that rewrites are structural, not verified.

Audit finding H5 (2026-05): the tool returns optimized_measure / rewriter_draft
from a purely static analyzer with no signal that the rewrite may change
results. Callers should run a 04_Run_DAX baseline before/after. This pins that
any response carrying a rewrite payload gets a semantic_warning advisory, and
responses without a rewrite do not (no noise).
"""

from __future__ import annotations

from server.handlers.dax_context_handler import _attach_semantic_warning


def test_warning_added_when_optimized_measure_present():
    out = _attach_semantic_warning(
        {"success": True, "optimized_measure": {"rewriter_draft": "SUM(x)"}}
    )
    assert "semantic_warning" in out
    assert "04_Run_DAX" in out["semantic_warning"]


def test_warning_added_when_improvements_have_rewriter_draft():
    out = _attach_semantic_warning(
        {"success": True, "improvements": {"has_improvements": True, "rewriter_draft": "X"}}
    )
    assert "semantic_warning" in out


def test_no_warning_when_no_rewrite_payload():
    out = _attach_semantic_warning({"success": True, "analysis": {"health_score": 100}})
    assert "semantic_warning" not in out


def test_no_warning_on_error_response():
    out = _attach_semantic_warning({"success": False, "error": "boom"})
    assert "semantic_warning" not in out


def test_existing_warning_not_clobbered():
    out = _attach_semantic_warning(
        {
            "success": True,
            "optimized_measure": {"rewriter_draft": "X"},
            "semantic_warning": "custom",
        }
    )
    assert out["semantic_warning"] == "custom"
