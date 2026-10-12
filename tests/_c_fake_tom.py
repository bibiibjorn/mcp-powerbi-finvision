"""Shared PascalCase fake-TOM objects for the review-C regression tests.

Shaped like the real ``Microsoft.AnalysisServices.Tabular`` model as pythonnet
exposes it (``Tables``, ``Measures``, ``Columns``, ``Partitions``, collections
with ``Add`` / ``Remove`` / ``Count``, ``SaveChanges`` / ``UndoLocalChanges`` on
the model). Nothing here touches CLR.
"""

from __future__ import annotations

from typing import Any, List


class Coll(list):
    """A TOM collection: iterable + Add/Remove/Count + name indexer via find()."""

    @property
    def Count(self):
        return len(self)

    def __getitem__(self, key):
        if isinstance(key, str):  # TOM name indexer: coll["Name"]
            hit = self.Find(key)
            if hit is None:
                raise KeyError(key)
            return hit
        return list.__getitem__(self, key)

    def Add(self, x):
        self.append(x)

    def Remove(self, x):
        list.remove(self, x)

    def Find(self, name):
        return next((o for o in self if getattr(o, "Name", None) == name), None)

    def ContainsName(self, name):
        return self.Find(name) is not None


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Obj({getattr(self, 'Name', '?')})"


class FakeMeasure(Obj):
    """Stands in for ``Tabular.Measure()`` (constructed without args)."""

    def __init__(self, **kw):
        base = dict(
            Name=None,
            Expression=None,
            Description="",
            FormatString="",
            DisplayFolder="",
            IsHidden=False,
            DataCategory="",
            FormatStringDefinition=None,
            DetailRowsDefinition=None,
            KPI=None,
            LineageTag="",
            SourceLineageTag="",
            Annotations=Coll(),
            ExtendedProperties=Coll(),
        )
        base.update(kw)
        super().__init__(**base)


def measure(name, expr="1", **kw) -> FakeMeasure:
    return FakeMeasure(Name=name, Expression=expr, **kw)


def column(name, expr=None, **kw) -> Obj:
    return Obj(Name=name, Expression=expr, **kw)


def table(name, measures=(), columns=(), partitions=(), calc_group=None, **kw) -> Obj:
    return Obj(
        Name=name,
        Measures=Coll(measures),
        Columns=Coll(columns),
        Partitions=Coll(partitions),
        CalculationGroup=calc_group,
        Annotations=Coll(),
        **kw,
    )


class FakeModel:
    """A TOM ``Model``: counts SaveChanges / UndoLocalChanges."""

    def __init__(self, *tables, roles=(), relationships=(), cultures=(), perspectives=()):
        self.Tables = Coll(tables)
        self.Roles = Coll(roles)
        self.Relationships = Coll(relationships)
        self.Cultures = Coll(cultures)
        self.Perspectives = Coll(perspectives)
        self.saves = 0
        self.undos = 0
        self.save_error: Any = None

    def SaveChanges(self):
        if self.save_error is not None:
            raise self.save_error
        self.saves += 1

    def UndoLocalChanges(self):
        self.undos += 1


class FakeServer:
    def __init__(self):
        self.disconnected = 0

    def Disconnect(self):
        self.disconnected += 1


def all_measures(model: FakeModel) -> List[str]:
    return [f"{t.Name}[{m.Name}]" for t in model.Tables for m in t.Measures]
