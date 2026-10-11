from core.calc_group_migration.detector import detect


class _M:
    def __init__(self, name, table):
        self.name = name
        self.table_name = table


class _T:
    def __init__(self, name, measures):
        self.name = name
        self.measures = measures


class _Mod:
    def __init__(self, tables):
        self.tables = tables


def _model():
    return _Mod(
        [
            _T(
                "Sales",
                [
                    _M("Sales", "Sales"),
                    _M("Sales YTD", "Sales"),
                    _M("Sales MTD", "Sales"),
                    _M("Sales LY", "Sales"),
                    _M("Margin", "Sales"),
                    _M("Margin YTD", "Sales"),
                    _M("Margin MTD", "Sales"),
                    _M("Margin LY", "Sales"),
                    _M("Cost", "Sales"),
                    _M("Cost YTD", "Sales"),
                    _M("Cost MTD", "Sales"),
                    _M("Cost LY", "Sales"),
                ],
            )
        ]
    )


def test_detect_groups_time_intel_pattern():
    res = detect(_model(), patterns=["time_intel"], min_pattern_size=3)
    assert res["candidates"], res
    cand = res["candidates"][0]
    assert cand["pattern_name"] == "time_intel"
    assert set(cand["base_measures"]) >= {"Sales", "Margin", "Cost"}
    assert "YTD" in cand["variant_suffix_set"]


def test_detect_respects_min_pattern_size():
    res = detect(_model(), patterns=["time_intel"], min_pattern_size=10)
    assert res["candidates"] == []


def test_detect_returns_all_patterns_when_unspecified():
    res = detect(_model(), patterns=None, min_pattern_size=3)
    assert any(c["pattern_name"] == "time_intel" for c in res["candidates"])
