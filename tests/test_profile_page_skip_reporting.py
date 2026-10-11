"""profile_page must report WHY a visual was not profiled, never drop it silently.

Previously any failure in _profile_single_visual returned None and the visual simply
disappeared from the output, so a crash was indistinguishable from "the page only has
one visual".
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.debug.debug_operations import DebugOperations

VISUALS = [
    {"id": "matrix1", "type": "pivotTable", "friendly_name": "Matrix", "is_slicer": False},
    {"id": "card1", "type": "card", "friendly_name": "Period", "is_slicer": False},
]


def _make_ops(builder, qe):
    ops = DebugOperations.__new__(DebugOperations)
    ops.builder = builder
    ops.qe = qe
    ops.logger = MagicMock()
    return ops


def _ok_exec():
    return {"success": True, "execution_time_ms": 12.0, "rows": [{"a": 1}]}


def test_build_failure_is_reported_not_silently_dropped():
    builder = MagicMock()
    builder.list_visuals.return_value = list(VISUALS)

    def _build(page_name, visual_id, include_slicers):
        if visual_id == "matrix1":
            return None  # e.g. no query could be built
        return SimpleNamespace(
            dax_query='EVALUATE ROW("v", 1)',
            filter_context=SimpleNamespace(all_filters=lambda: []),
        )

    builder.build_visual_query.side_effect = _build
    qe = MagicMock()
    qe.validate_and_execute_dax.return_value = _ok_exec()

    result = _make_ops(builder, qe).profile_page("Matrix", iterations=1)

    assert result["visuals_profiled"] == 1
    assert result["visuals_skipped"] == 1
    assert result["data_visuals_found"] == 2
    skipped = result["skipped"][0]
    assert skipped["visual_id"] == "matrix1"
    assert skipped["reason"] == "query_not_built"


def test_exception_is_reported_with_type_and_message():
    builder = MagicMock()
    builder.list_visuals.return_value = [VISUALS[0]]
    builder.build_visual_query.side_effect = UnboundLocalError("boom")

    result = _make_ops(builder, MagicMock()).profile_page("Matrix", iterations=1)

    assert result["visuals_profiled"] == 0
    assert result["visuals_skipped"] == 1
    skipped = result["skipped"][0]
    assert skipped["reason"] == "exception"
    assert "UnboundLocalError" in skipped["detail"]
    assert "boom" in skipped["detail"]


def test_dax_failure_surfaces_the_engine_error():
    builder = MagicMock()
    builder.list_visuals.return_value = [VISUALS[0]]
    builder.build_visual_query.return_value = SimpleNamespace(
        dax_query="EVALUATE BAD",
        filter_context=SimpleNamespace(all_filters=lambda: []),
    )
    qe = MagicMock()
    qe.validate_and_execute_dax.return_value = {
        "success": False,
        "error": "The column 'X' could not be found",
    }

    result = _make_ops(builder, qe).profile_page("Matrix", iterations=1)

    assert result["visuals_skipped"] == 1
    skipped = result["skipped"][0]
    assert skipped["reason"] == "query_failed"
    assert "could not be found" in skipped["detail"]


def test_all_profiled_reports_empty_skipped():
    builder = MagicMock()
    builder.list_visuals.return_value = list(VISUALS)
    builder.build_visual_query.return_value = SimpleNamespace(
        dax_query='EVALUATE ROW("v", 1)',
        filter_context=SimpleNamespace(all_filters=lambda: []),
    )
    qe = MagicMock()
    qe.validate_and_execute_dax.return_value = _ok_exec()

    result = _make_ops(builder, qe).profile_page("Matrix", iterations=1)

    assert result["visuals_profiled"] == 2
    assert result["visuals_skipped"] == 0
    assert result["skipped"] == []
