"""Regression: inline_measure must not rewrite refs inside string literals (P1).

The ref pattern was applied across the whole expression with re.sub, so a
[Measure] substring appearing inside a "double-quoted string" literal got
replaced with the measure body — corrupting the literal.
"""

from types import SimpleNamespace

from core.codemods.transforms.inline_measure import InlineMeasure


def _model(consumer_expr):
    target = SimpleNamespace(name="Status", expression="SELECTEDVALUE('T'[S])")
    consumer = SimpleNamespace(name="Consumer", expression=consumer_expr)
    tbl = SimpleNamespace(name="T", measures=[target, consumer])
    return SimpleNamespace(tables=[tbl])


def test_ref_inside_string_literal_not_inlined():
    # [Status] appears ONLY inside a string literal — it must be left alone.
    model = _model('IF([Region] = "South [Status]", 1, 0)')
    out = InlineMeasure().preview(model, {"measure_ref": "T[Status]"})
    # No edit should be produced (the only [Status] is inside a string).
    assert (
        out.edits == []
    ), f"string-literal ref was wrongly inlined: {[e.after for e in out.edits]}"


def test_genuine_ref_outside_string_is_inlined():
    model = _model("[Status] + 1")
    out = InlineMeasure().preview(model, {"measure_ref": "T[Status]"})
    assert len(out.edits) == 1
    assert "SELECTEDVALUE('T'[S])" in out.edits[0].after


def test_mixed_ref_inlines_only_real_one():
    model = _model('IF([Region] = "has [Status] text", [Status], 0)')
    out = InlineMeasure().preview(model, {"measure_ref": "T[Status]"})
    assert len(out.edits) == 1
    after = out.edits[0].after
    # The string literal must be preserved verbatim; only the real ref inlined.
    assert '"has [Status] text"' in after, f"string literal corrupted: {after}"
