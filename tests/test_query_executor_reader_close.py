"""Regression: _execute_dax_reader must close the reader exactly once (P0-1).

Double Close() on the CLR data reader can corrupt COM state. The finally
block is the single owner of the close; the happy path must not also close.
"""

from core.infrastructure.query_executor import OptimizedQueryExecutor


class _FakeReader:
    def __init__(self, n_rows=0):
        self.close_calls = 0
        self.FieldCount = 0
        self._remaining = n_rows

    def Read(self):
        if self._remaining > 0:
            self._remaining -= 1
            return True
        return False

    def GetName(self, i):  # pragma: no cover - FieldCount is 0
        raise AssertionError("no columns expected")

    def GetValue(self, i):  # pragma: no cover - no columns
        raise AssertionError("no columns expected")

    def Close(self):
        self.close_calls += 1


class _FakeCommand:
    def __init__(self, reader):
        self._reader = reader

    def ExecuteReader(self):
        return self._reader


def test_reader_closed_exactly_once_on_success():
    qe = OptimizedQueryExecutor.__new__(OptimizedQueryExecutor)  # bypass __init__/CLR
    reader = _FakeReader(n_rows=0)
    cmd = _FakeCommand(reader)

    columns, rows, _ms = qe._execute_dax_reader(cmd)

    assert rows == []
    assert reader.close_calls == 1, f"expected exactly one Close(), got {reader.close_calls}"


def test_reader_closed_exactly_once_with_rows():
    qe = OptimizedQueryExecutor.__new__(OptimizedQueryExecutor)
    reader = _FakeReader(n_rows=3)
    cmd = _FakeCommand(reader)

    _columns, rows, _ms = qe._execute_dax_reader(cmd)

    # FieldCount is 0 so rows carry empty dicts, but the count still exercises
    # the read loop + the single-close contract.
    assert len(rows) == 3
    assert reader.close_calls == 1, f"expected exactly one Close(), got {reader.close_calls}"
