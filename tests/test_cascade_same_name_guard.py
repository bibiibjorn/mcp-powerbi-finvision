"""M4: column-rename cascade must not rewrite same-named measure refs.

DAX uses [name] for BOTH unqualified column refs and measure refs. When the
renamed column shares its name with a measure in the same table, an
unqualified [old_name] inside a same-table measure is a measure reference and
must be left intact. Qualified 'Table'[old_name] is unambiguously the column
and is always rewritten.
"""

from core.operations.rename_cascade import cascade_column_rename


class FakeMeasure:
    def __init__(self, name, expression):
        self.Name = name
        self.Expression = expression
        self.FormatStringDefinition = None
        self.DetailRowsDefinition = None
        self.KPI = None


class FakeColumn:
    def __init__(self, name, expression=None):
        self.Name = name
        self.Expression = expression


class FakeTable:
    def __init__(self, name, measures=None, columns=None):
        self.Name = name
        self.Measures = measures or []
        self.Columns = columns or []


class FakeModel:
    def __init__(self, tables):
        self.Tables = tables
        # Cascade helpers iterate these collections; keep them empty.
        self.Relationships = []
        self.Roles = []


def _build_model():
    foo_measure = FakeMeasure("Foo", "[Foo] + 1")  # unqualified -> measure ref
    bar_measure = FakeMeasure("Bar", "'Sales'[Foo] * 2")  # qualified -> column ref
    sales = FakeTable(
        "Sales",
        measures=[foo_measure, bar_measure],
        columns=[FakeColumn("Foo")],  # the renamed column also exists
    )
    return FakeModel([sales]), foo_measure, bar_measure


def test_unqualified_measure_ref_not_rewritten_on_collision():
    model, foo_measure, bar_measure = _build_model()

    cascade_column_rename(model, "Sales", "Foo", "FooNew")

    # Qualified column ref IS rewritten.
    assert bar_measure.Expression == "'Sales'[FooNew] * 2"
    # Unqualified [Foo] is a measure ref (collision) -> left intact.
    assert foo_measure.Expression == "[Foo] + 1"


def test_unqualified_rewritten_when_no_measure_collision():
    # No measure named "Qty" -> unqualified [Qty] is genuinely the column.
    m = FakeMeasure("Total", "[Qty] + 'Sales'[Qty]")
    sales = FakeTable("Sales", measures=[m], columns=[FakeColumn("Qty")])
    model = FakeModel([sales])

    cascade_column_rename(model, "Sales", "Qty", "QtyNew")

    assert m.Expression == "[QtyNew] + 'Sales'[QtyNew]"
