"""Tests for the in-scope visual-type inventory."""

from tools.visual_catalog.types import (
    IN_SCOPE_TYPES,
    POINTER_ONLY_TYPES,
    is_in_scope,
    resolver_note_for_unknown,
)


def test_core_chart_types_in_scope():
    for t in ("columnChart", "barChart", "lineChart", "pivotTable", "cardVisual"):
        assert is_in_scope(t), f"{t} should be in scope"


def test_pointer_only_types_not_in_scope():
    for t in ("pythonVisual", "scriptVisual", "rdlVisual"):
        assert not is_in_scope(t)
        assert t in POINTER_ONLY_TYPES


def test_unknown_type_note():
    note = resolver_note_for_unknown("someFutureVisual")
    assert "someFutureVisual" in note
    assert "unknown" in note.lower()


def test_in_scope_types_unique():
    assert len(IN_SCOPE_TYPES) == len(set(IN_SCOPE_TYPES))
    assert all(isinstance(t, str) and t for t in IN_SCOPE_TYPES)
