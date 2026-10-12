"""06 compare disk TMDL vs live Desktop: inferred data types are not changes (R0201 live test,
2026-10-03: 226 columns reported 'modified data_type' between S02 on disk and the open model)."""

from core.comparison.model_diff_engine import ModelDiffer


def _model(columns):
    return {
        "model_name": "m",
        "tables": [{"name": "T", "columns": columns, "measures": [], "partitions": []}],
        "relationships": [],
    }


def _col(name, data_type, kind="Calculated"):
    return {
        "name": name,
        "kind": kind,
        "data_type": data_type,
        "is_calculated": kind == "Calculated",
        "expression": "1",
    }


def _column_changes(old_cols, new_cols):
    differ = ModelDiffer(_model(old_cols), _model(new_cols))
    out = {}
    for c1 in old_cols:
        c2 = next(c for c in new_cols if c["name"] == c1["name"])
        out[c1["name"]] = differ._column_changes(c1, c2, "T")
    return out


def test_inferred_data_type_on_one_side_is_not_a_change():
    changes = _column_changes(
        [_col("Calc", "Unknown"), _col("Real", "Int64", kind="Data")],
        [_col("Calc", "String"), _col("Real", "String", kind="Data")],
    )
    assert "data_type" not in changes["Calc"]
    assert changes["Real"]["data_type"] == {"from": "Int64", "to": "String"}
