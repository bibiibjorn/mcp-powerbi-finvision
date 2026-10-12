"""Regression: a field-parameter create that fails mid-configuration must roll
back the half-built table, not leave it live in the model.

create_field_parameter commits the calculated table early (SaveChanges) and
then configures GroupByColumn / ParameterMetadata / annotations. If a later
step fails it used to return an error with "Table left in place for
inspection" — leaving a broken table permanently live and a retry blocked by
`table_exists`. The `_rollback_fp_table` helper removes the table and saves so
the model returns to a clean state; failure paths now report `rolled_back`.
"""

from core.operations.field_parameter_manager import FieldParameterManager


class _NamedColl:
    def __init__(self, items=None):
        self._items = list(items or [])

    def __iter__(self):
        return iter(self._items)

    def Remove(self, item):
        self._items.remove(item)

    def __getitem__(self, key):
        for it in self._items:
            if getattr(it, "Name", None) == key:
                return it
        raise KeyError(key)


class _Table:
    def __init__(self, name):
        self.Name = name


class _Model:
    def __init__(self, tables):
        self.Tables = _NamedColl(tables)
        self.save_count = 0

    def SaveChanges(self):
        self.save_count += 1


def test_rollback_removes_table_and_saves():
    tbl = _Table("MyFP")
    model = _Model([tbl])
    mgr = FieldParameterManager()
    ok = mgr._rollback_fp_table(model, "MyFP")
    assert ok is True
    assert list(model.Tables) == []
    assert model.save_count == 1


def test_rollback_missing_table_is_noop_success():
    model = _Model([])
    mgr = FieldParameterManager()
    # Nothing to remove — treated as already-clean, not an error.
    assert mgr._rollback_fp_table(model, "Nope") is True
    assert model.save_count == 0


def test_rollback_swallows_save_failure():
    class _BadModel(_Model):
        def SaveChanges(self):
            raise RuntimeError("save blew up during rollback")

    tbl = _Table("MyFP")
    model = _BadModel([tbl])
    mgr = FieldParameterManager()
    # Must not raise; returns False so the caller can report rollback_failed.
    assert mgr._rollback_fp_table(model, "MyFP") is False
