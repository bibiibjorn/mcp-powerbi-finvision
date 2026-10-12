from core.codemods.transforms.replace_pattern import ReplacePattern


class _M:
    def __init__(self, name, expr):
        self.name = name
        self.expression = expr
        self.table_name = "T"


class _T:
    def __init__(self, ms):
        self.name = "T"
        self.measures = ms
        self.columns = []


class _Mod:
    def __init__(self):
        self.tables = [
            _T(
                [
                    _M("A", "CALCULATE([X], FILTER(ALL(T), T[Y]>0))"),
                    _M("B", "CALCULATE([X], FILTER(ALL(T), T[Z]>0))"),
                    _M("C", "1+1"),
                ]
            )
        ]
        self.relationships = []


def test_replace_pattern_rewrites_matching_expressions():
    res = ReplacePattern().preview(
        _Mod(),
        {
            "pattern": r"FILTER\(ALL\((?P<tbl>\w+)\),\s*(?P<cond>[^)]+)\)",
            "replacement": r"REMOVEFILTERS(\g<tbl>), \g<cond>",
            "scope": ["measures"],
        },
    )
    targets = {e.target for e in res.edits}
    assert "measure:T[A]" in targets
    assert "measure:T[B]" in targets
    assert "measure:T[C]" not in targets


def test_replace_pattern_with_invalid_regex_returns_warning():
    res = ReplacePattern().preview(_Mod(), {"pattern": "([invalid", "replacement": "x"})
    assert any("regex" in w.lower() for w in res.warnings)
