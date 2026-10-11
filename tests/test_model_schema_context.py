"""Tests for the schema-aware AnalysisContext foundation (Phase 2.1).

ModelSchema carries live model metadata (measure/column/table names,
relationships, roles) so DAX rules can reason about topology and resolve
measure-vs-column ambiguity. AnalysisContext.schema is optional; rules must
degrade gracefully when it is None.
"""

from core.dax.analyzer.models import AnalysisContext, ModelSchema


def _schema():
    return ModelSchema.from_model(
        {
            "tables": [
                {"name": "Sales"},
                {"name": "Date"},
                {"name": "Product"},
            ],
            "measures": [
                {"name": "Total Sales", "table": "Sales"},
                {"name": "Margin %", "table": "Sales"},
            ],
            "columns": [
                {"name": "Amount", "table": "Sales"},
                {"name": "Color", "table": "Product"},
            ],
            "relationships": [
                {
                    "fromTable": "Sales",
                    "fromColumn": "ProductKey",
                    "toTable": "Product",
                    "toColumn": "ProductKey",
                    "fromCardinality": "Many",
                    "toCardinality": "One",
                    "crossFilteringBehavior": "OneDirection",
                    "isActive": True,
                },
                {
                    "fromTable": "Sales",
                    "fromColumn": "AccountKey",
                    "toTable": "Account",
                    "toColumn": "AccountKey",
                    "fromCardinality": "Many",
                    "toCardinality": "Many",
                    "crossFilteringBehavior": "BothDirections",
                    "isActive": True,
                },
            ],
            "roles": [
                {"name": "Region", "table_filters": {"Sales": '[Region] = "North"'}},
            ],
        }
    )


# ── Name resolution ─────────────────────────────────────────────────────────


def test_is_measure_true_for_measure_name():
    s = _schema()
    assert s.is_measure("Total Sales") is True
    assert s.is_measure("Margin %") is True


def test_is_measure_false_for_column_name():
    s = _schema()
    assert s.is_measure("Amount") is False


def test_is_column_true_for_column_name():
    s = _schema()
    assert s.is_column("Amount") is True
    assert s.is_column("Color") is True


def test_is_column_false_for_measure_name():
    s = _schema()
    assert s.is_column("Total Sales") is False


def test_name_resolution_is_case_insensitive():
    s = _schema()
    assert s.is_measure("total sales") is True
    assert s.is_column("amount") is True


def test_table_names_collected():
    s = _schema()
    assert s.table_names == {"sales", "date", "product"}


# ── Relationships ───────────────────────────────────────────────────────────


def test_has_limited_relationship_detects_m2m_and_bidirectional():
    s = _schema()
    # The Sales<->Account edge is Many/Many + bidirectional => limited.
    assert s.has_limited_relationships() is True


def test_clean_one_to_many_only_is_not_limited():
    s = ModelSchema.from_model(
        {
            "tables": [{"name": "Sales"}, {"name": "Product"}],
            "relationships": [
                {
                    "fromTable": "Sales",
                    "toTable": "Product",
                    "fromCardinality": "Many",
                    "toCardinality": "One",
                    "crossFilteringBehavior": "OneDirection",
                    "isActive": True,
                },
            ],
        }
    )
    assert s.has_limited_relationships() is False


# ── Roles ───────────────────────────────────────────────────────────────────


def test_roles_exposed():
    s = _schema()
    assert any(r.get("name") == "Region" for r in s.roles)


# ── Graceful degradation ────────────────────────────────────────────────────


def test_empty_model_builds_empty_schema():
    s = ModelSchema.from_model({})
    assert s.measure_names == set()
    assert s.is_measure("anything") is False
    assert s.has_limited_relationships() is False


def test_context_schema_defaults_to_none():
    ctx = AnalysisContext()
    assert ctx.schema is None
