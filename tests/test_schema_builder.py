"""Tests for build_model_schema (Phase 2.1 population path).

Builds a ModelSchema from the live model via INFO DMV queries. Mocks at the
query_executor boundary; never touches AMO/CLR. Must return an empty-but-valid
schema (never raise) when the model can't be read, so DAX analysis degrades to
static-only.
"""

from core.dax.analyzer.schema_builder import build_model_schema
from core.dax.analyzer.models import ModelSchema


class _FakeExecutor:
    def __init__(self, by_view):
        self._by_view = by_view

    def execute_info_query(self, view):
        return self._by_view.get(view, {"success": False, "rows": []})


def test_builds_schema_from_info_queries():
    executor = _FakeExecutor(
        {
            "TABLES": {"success": True, "rows": [{"Name": "Sales"}, {"Name": "Product"}]},
            "MEASURES": {"success": True, "rows": [{"Name": "Total Sales"}]},
            "COLUMNS": {"success": True, "rows": [{"Name": "Amount", "Table": "Sales"}]},
            "RELATIONSHIPS": {"success": True, "rows": []},
            "ROLES": {"success": True, "rows": [{"Name": "Region"}]},
        }
    )
    schema = build_model_schema(executor)
    assert isinstance(schema, ModelSchema)
    assert schema.is_measure("Total Sales") is True
    assert schema.is_column("Amount") is True
    assert "sales" in schema.table_names
    assert any(r.get("name") == "Region" or r.get("Name") == "Region" for r in schema.roles)


def test_returns_empty_schema_when_executor_is_none():
    schema = build_model_schema(None)
    assert isinstance(schema, ModelSchema)
    assert schema.measure_names == set()


def test_does_not_raise_when_query_explodes():
    class _Boom:
        def execute_info_query(self, view):
            raise RuntimeError("dmv down")

    schema = build_model_schema(_Boom())
    assert isinstance(schema, ModelSchema)
    assert schema.measure_names == set()
