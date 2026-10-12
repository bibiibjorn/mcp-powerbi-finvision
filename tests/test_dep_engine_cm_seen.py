"""Regression: _analyze_column_usage is idempotent across calls (P1).

_cm_seen was lazy-initialized via hasattr() inside the column loop, so on a
SECOND call to _analyze_column_usage on the same engine instance the tracker
held stale data and could drop/duplicate column->referrer entries. It must be
initialized fresh at the top of each call.
"""

import pytest

from core.pbip.pbip_dependency_engine import PbipDependencyEngine, DAX_PARSER_AVAILABLE

pytestmark = pytest.mark.skipif(
    not DAX_PARSER_AVAILABLE, reason="DAX reference parser not available"
)


def _model():
    return {
        "tables": [
            {
                "name": "Sales",
                "measures": [],
                "columns": [
                    {"name": "Amount", "expression": ""},
                    # Calc column referencing another column in the same table.
                    {"name": "AmountDoubled", "expression": "'Sales'[Amount] * 2"},
                ],
            }
        ]
    }


def test_analyze_all_dependencies_idempotent_on_reuse():
    """Running the full analysis twice on the same engine must not drop the
    calc-column referrer. _build_reverse_indices reassigns column_to_measure,
    so a stale _cm_seen (from run 1) would cause run 2 to skip the entry."""
    engine = PbipDependencyEngine(_model())

    engine.analyze_all_dependencies()
    first = {k: sorted(v) for k, v in engine.column_to_measure.items()}

    engine.analyze_all_dependencies()
    second = {k: sorted(v) for k, v in engine.column_to_measure.items()}

    assert first == second, f"second full pass diverged: {first} -> {second}"
    assert "Sales[AmountDoubled]" in engine.column_to_measure.get("Sales[Amount]", [])


def test_analyze_column_usage_no_duplicate_referrers():
    engine = PbipDependencyEngine(_model())
    engine._analyze_column_usage()
    referrers = engine.column_to_measure.get("Sales[Amount]", [])
    assert referrers.count("Sales[AmountDoubled]") == 1
