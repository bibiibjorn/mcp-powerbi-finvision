"""Regression: measure cascade must not rewrite same-named COLUMN refs (P1).

When a measure [Revenue] is renamed and a column 'Sales'[Revenue] of the same
name exists, the bare-bracket pattern \\[Revenue\\] also matched the column
reference inside 'Sales'[Revenue], corrupting calc-column/measure expressions
that legitimately referenced the column. Qualified ('Table'[name] / Table[name])
references must be left untouched when old_name is also a column name.
"""

from core.operations.rename_cascade import cascade_measure_rename

# --- minimal fake TOM objects ------------------------------------------------


class _Col:
    def __init__(self, name, expression=None):
        self.Name = name
        self.Expression = expression


class _Measure:
    def __init__(self, name, expression):
        self.Name = name
        self.Expression = expression
        self.FormatStringDefinition = None
        self.DetailRowsDefinition = None
        self.KPI = None


class _Table:
    def __init__(self, name, measures=None, columns=None):
        self.Name = name
        self.Measures = measures or []
        self.Columns = columns or []
        self.Partitions = _EmptyPartitions()
        self.CalculationGroup = None


class _EmptyPartitions:
    Count = 0

    def __iter__(self):
        return iter(())


class _Model:
    def __init__(self, tables):
        self.Tables = tables
        self.Roles = []


def test_measure_rename_does_not_touch_qualified_column_ref():
    # Sales has BOTH a measure [Revenue] and a column [Revenue].
    col = _Col("Revenue")
    # A calc column that references the COLUMN via a qualified ref.
    calc_col = _Col("RevenueDoubled", "'Sales'[Revenue] * 2")
    # A measure that references the MEASURE via a bare ref.
    consumer = _Measure("RevenuePlus", "[Revenue] + 1")
    renamed = _Measure("Revenue", "SUM('Sales'[Amount])")
    sales = _Table("Sales", measures=[renamed, consumer], columns=[col, calc_col])
    model = _Model([sales])

    cascade_measure_rename(model, "Revenue", "Revenue_v2", table_name="Sales")

    # The measure consumer's bare [Revenue] is a measure ref → renamed.
    assert consumer.Expression == "[Revenue_v2] + 1"
    # The calc column's qualified 'Sales'[Revenue] is a COLUMN ref → untouched.
    assert (
        calc_col.Expression == "'Sales'[Revenue] * 2"
    ), f"qualified column ref was wrongly rewritten: {calc_col.Expression}"


def test_measure_rename_normal_case_unaffected():
    """When no same-named column exists, bare refs rename as before."""
    consumer = _Measure("Profit", "[Sales Amount] - [Cost]")
    renamed = _Measure("Sales Amount", "SUM('F'[Amt])")
    t = _Table("F", measures=[renamed, consumer], columns=[])
    model = _Model([t])

    cascade_measure_rename(model, "Sales Amount", "Total Sales", table_name="F")
    assert consumer.Expression == "[Total Sales] - [Cost]"
