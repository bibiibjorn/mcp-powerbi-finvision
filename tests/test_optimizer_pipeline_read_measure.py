"""Audit C2: optimizer pipeline must read measure DAX via execute_dmv_query."""

from unittest.mock import MagicMock


def _make_pipeline_with_executor(qx):
    from core.dax.optimizer.pipeline import OptimizationPipeline

    cs = MagicMock()
    cs.query_executor = qx
    return OptimizationPipeline(connection_state=cs)


def test_read_measure_dax_uses_dmv_and_data_key():
    qx = MagicMock()
    qx.execute_dmv_query.return_value = {
        "success": True,
        "data": [{"Expression": "SUM(Sales[Amount])"}],
        "row_count": 1,
    }
    pipe = _make_pipeline_with_executor(qx)

    expr = pipe._read_measure_dax("Total Sales", "Sales")

    assert expr == "SUM(Sales[Amount])"
    qx.execute_dmv_query.assert_called_once()
    assert "TMSCHEMA_MEASURES" in qx.execute_dmv_query.call_args[0][0]


def test_read_measure_dax_escapes_single_quote():
    qx = MagicMock()
    qx.execute_dmv_query.return_value = {"success": True, "data": [], "row_count": 0}
    pipe = _make_pipeline_with_executor(qx)

    pipe._read_measure_dax("O'Brien Sales", "Sales")

    sent = qx.execute_dmv_query.call_args[0][0]
    assert "O''Brien Sales" in sent


def test_read_measure_dax_returns_none_on_failure():
    qx = MagicMock()
    qx.execute_dmv_query.return_value = {"success": False, "error": "boom"}
    pipe = _make_pipeline_with_executor(qx)
    assert pipe._read_measure_dax("X", "T") is None


def test_read_measure_dax_no_connection_returns_none():
    from core.dax.optimizer.pipeline import OptimizationPipeline

    pipe = OptimizationPipeline(connection_state=None)
    assert pipe._read_measure_dax("X", "T") is None
