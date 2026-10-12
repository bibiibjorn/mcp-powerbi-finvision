"""Fake TOM objects (``SimpleNamespace``) for the model-change classifier tests.

Not a test module. The classifier's extractor is duck-typed, so these stand in for a live
or deserialized ``Database`` - no CLR involved.
"""

from __future__ import annotations

import types

ns = types.SimpleNamespace


# ---------------------------------------------------------------------------
# fake TOM builders
# ---------------------------------------------------------------------------
def part(name="P", source="M", expr="let Source = 1 in Source", mode="Import"):
    return ns(Name=name, SourceType=source, Mode=mode, Source=ns(Expression=expr))


def col(name, ctype="Data", dtype="String", source=None, expr=None, **kw):
    return ns(
        Name=name,
        Type=ctype,
        DataType=dtype,
        SourceColumn=source if source is not None else (name if ctype == "Data" else None),
        Expression=expr,
        FormatString=kw.get("fmt"),
        DisplayFolder=kw.get("folder"),
        Description=kw.get("desc"),
        IsHidden=kw.get("hidden", False),
        SortByColumn=None,
        SummarizeBy="Default",
        DataCategory=None,
    )


def meas(name, expr="1", fmt=None, folder=None, desc=None):
    return ns(
        Name=name,
        Expression=expr,
        FormatString=fmt,
        DisplayFolder=folder,
        Description=desc,
        IsHidden=False,
        FormatStringDefinition=None,
    )


def table(name, parts=None, cols=None, measures=None, desc=None):
    return ns(
        Name=name,
        Partitions=parts if parts is not None else [part(name)],
        Columns=cols if cols is not None else [col("Id")],
        Measures=measures or [],
        Hierarchies=[],
        RefreshPolicy=None,
        Description=desc,
        IsHidden=False,
        DataCategory=None,
    )


def calc_table(name, expr="DATATABLE(...)", cols=None, measures=None):
    return table(
        name,
        parts=[part(name, source="Calculated", expr=expr)],
        cols=cols if cols is not None else [col("V", "CalculatedTableColumn")],
        measures=measures,
    )


def rel(ft, fc, tt, tc, active=True):
    fcol = ns(Name=fc, Table=ns(Name=ft))
    tcol = ns(Name=tc, Table=ns(Name=tt))
    return ns(
        FromColumn=fcol,
        ToColumn=tcol,
        IsActive=active,
        CrossFilteringBehavior="OneDirection",
        FromCardinality="Many",
        ToCardinality="One",
        SecurityFilteringBehavior="OneDirection",
    )


def db(tables=(), rels=(), expressions=(), datasources=(), cl=1702, mode="Import", functions=()):
    model = ns(
        DefaultMode=mode,
        DataSources=list(datasources),
        Expressions=list(expressions),
        Tables=list(tables),
        Relationships=list(rels),
        Roles=[],
        Perspectives=[],
        Cultures=[],
        Functions=list(functions),
    )
    return ns(CompatibilityLevel=cl, Model=model)


def base():
    return db(
        tables=[
            table(
                "Sales",
                cols=[col("Id"), col("Amount", dtype="Double")],
                measures=[meas("Total", "1")],
            ),
            calc_table("Cal", expr="CALENDAR(1,2)"),
        ],
        rels=[rel("Sales", "Id", "Cal", "V")],
        expressions=[ns(Name="p_Now", Kind="M", Expression="DateTime.LocalNow()")],
    )
