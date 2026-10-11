from core.codemods.transforms.extract_measure import ExtractMeasure


class _M:
    def __init__(self, name, expr):
        self.name = name
        self.expression = expr
        self.table_name = "T"


class _T:
    def __init__(self, name, ms):
        self.name = name
        self.measures = ms
        self.columns = []


class _Mod:
    def __init__(self, ts):
        self.tables = ts
        self.relationships = []


def test_extract_creates_new_measure_and_replaces_in_caller():
    m = _Mod([_T("T", [_M("A", "SUM(T[X]) * 1"), _M("B", "SUM(T[X]) * 2")])])
    res = ExtractMeasure().preview(
        m,
        {
            "expression": "SUM(T[X])",
            "new_name": "X Total",
            "target_table": "T",
            "auto_detect": True,
        },
    )
    new = next(e for e in res.edits if e.target == "measure:T[X Total]")
    assert "SUM(T[X])" in new.after
    callers = {e.target for e in res.edits if e.target != "measure:T[X Total]"}
    assert "measure:T[A]" in callers
    assert "measure:T[B]" in callers


def test_extract_with_no_matches_returns_no_callers():
    m = _Mod([_T("T", [_M("A", "1+1")])])
    res = ExtractMeasure().preview(
        m,
        {
            "expression": "SUM(T[Y])",
            "new_name": "Y Total",
            "target_table": "T",
            "auto_detect": True,
        },
    )
    callers = [e for e in res.edits if e.target != "measure:T[Y Total]"]
    assert callers == []
