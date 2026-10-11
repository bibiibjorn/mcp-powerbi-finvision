from core.codemods.transforms.inline_measure import InlineMeasure


class _M:
    def __init__(self, name, expr, table="T"):
        self.name = name
        self.expression = expr
        self.table_name = table


class _T:
    def __init__(self, name, measures):
        self.name = name
        self.measures = measures
        self.columns = []


class _Mod:
    def __init__(self, tables):
        self.tables = tables
        self.relationships = []


def test_inline_replaces_reference_with_body():
    m = _Mod([_T("T", [_M("Helper", "1+2"), _M("Caller", "[Helper] * 10")])])
    res = InlineMeasure().preview(m, {"measure_ref": "T[Helper]"})
    callers = {e.target for e in res.edits}
    assert "measure:T[Caller]" in callers
    edit = next(e for e in res.edits if e.target == "measure:T[Caller]")
    # E-05: the body sits on its own lines inside the parens (a trailing line
    # comment in the body can never swallow the closing paren).
    assert edit.after == "(\n1+2\n) * 10"
    assert "[Helper]" not in edit.after


def test_inline_warns_when_target_has_dependencies():
    m = _Mod(
        [
            _T(
                "T",
                [
                    _M("Inner", "5"),
                    _M("Helper", "[Inner] + 1"),
                    _M("Caller", "[Helper] * 10"),
                ],
            )
        ]
    )
    res = InlineMeasure().preview(m, {"measure_ref": "T[Helper]"})
    assert any("nested" in w.lower() for w in res.warnings)


def test_inline_with_unknown_measure_returns_empty_with_warning():
    m = _Mod([_T("T", [_M("Caller", "1")])])
    res = InlineMeasure().preview(m, {"measure_ref": "T[Missing]"})
    assert res.edits == []
    assert any("not found" in w.lower() for w in res.warnings)
