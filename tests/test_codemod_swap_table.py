from core.codemods.transforms.swap_table_reference import SwapTableReference


class _M:
    def __init__(self, name, expr):
        self.name = name
        self.expression = expr
        self.table_name = "Other"


class _T:
    def __init__(self, name, ms):
        self.name = name
        self.measures = ms
        self.columns = []


class _Mod:
    def __init__(self):
        self.tables = [
            _T("Other", [_M("A", "SUM(OldTable[X]) + OldTable[Y]"), _M("B", "1+1")]),
        ]
        self.relationships = []


def test_swap_renames_qualified_column_refs():
    res = SwapTableReference().preview(
        _Mod(),
        {"from_table": "OldTable", "to_table": "NewTable"},
    )
    edit = next(e for e in res.edits if e.target == "measure:Other[A]")
    assert "NewTable[X]" in edit.after
    assert "OldTable" not in edit.after


def test_swap_with_no_matches_returns_no_edits():
    res = SwapTableReference().preview(_Mod(), {"from_table": "Missing", "to_table": "X"})
    assert res.edits == []
