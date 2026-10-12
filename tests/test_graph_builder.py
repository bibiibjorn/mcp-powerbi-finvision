"""Tests for core.graph.builder — model + report graph builder."""

from core.graph.builder import build_graph, build_model_graph, build_report_graph


class _FakeMeasure:
    def __init__(self, name, expression, table_name):
        self.name = name
        self.expression = expression
        self.table_name = table_name


class _FakeColumn:
    def __init__(self, name, table_name):
        self.name = name
        self.table_name = table_name


class _FakeTable:
    def __init__(self, name, columns, measures):
        self.name = name
        self.columns = columns
        self.measures = measures


class _FakeModel:
    def __init__(self, tables):
        self.tables = tables


def _model():
    sales_amt = _FakeColumn("Amount", "Sales")
    sales_qty = _FakeColumn("Qty", "Sales")
    total = _FakeMeasure("Total Sales", "SUM(Sales[Amount])", "Sales")
    avg = _FakeMeasure("Avg Sales", "DIVIDE([Total Sales], COUNTROWS(Sales))", "Sales")
    sales = _FakeTable("Sales", [sales_amt, sales_qty], [total, avg])
    return _FakeModel([sales])


def test_model_graph_includes_measures_columns_tables():
    g = build_model_graph(_model())
    assert "measure:Sales[Total Sales]" in g.nodes
    assert "column:Sales[Amount]" in g.nodes
    assert "table:Sales" in g.nodes


def test_model_graph_measure_references_column():
    g = build_model_graph(_model())
    refs = {e.dst for e in g.outgoing("measure:Sales[Total Sales]") if e.kind == "references"}
    assert "column:Sales[Amount]" in refs


def test_model_graph_measure_references_other_measure():
    g = build_model_graph(_model())
    refs = {e.dst for e in g.outgoing("measure:Sales[Avg Sales]") if e.kind == "references"}
    assert "measure:Sales[Total Sales]" in refs


def test_model_graph_belongs_to_table_edges():
    g = build_model_graph(_model())
    bt = {e.dst for e in g.outgoing("measure:Sales[Total Sales]") if e.kind == "belongs_to"}
    assert "table:Sales" in bt


def test_build_graph_with_scope_model_only_has_no_visuals(tmp_path):
    g = build_graph(model=_model(), pbip_path=None, scope="model")
    assert all(n.kind in {"measure", "column", "table"} for n in g.nodes.values())


def test_build_report_graph_returns_empty_when_path_missing(tmp_path):
    g = build_report_graph(str(tmp_path / "nonexistent"))
    assert len(g.nodes) == 0
