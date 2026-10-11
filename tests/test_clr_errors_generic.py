"""T0-5: tests for the generalized format_clr_error()."""

from __future__ import annotations

from core.autonomous.clr_errors import format_clr_error, format_refresh_error


class FakeInner(Exception):
    """Stand-in for a CLR InnerException (no .Message / .GetType — falls to str())."""

    def __init__(self, msg: str) -> None:
        super().__init__(msg)


class FakeClr(Exception):
    """Stand-in for a CLR exception with a single inner level."""

    def __init__(self, msg: str, inner: Exception | None = None) -> None:
        super().__init__(msg)
        self._inner = inner
        self.Source = "Microsoft.AnalysisServices.Tabular"
        self.HResult = -2146232832

    @property
    def InnerException(self) -> Exception | None:  # noqa: N802 (CLR-style name)
        return self._inner

    @property
    def Message(self) -> str:  # noqa: N802
        return str(self.args[0]) if self.args else ""


def test_format_clr_error_unwraps_inner_chain() -> None:
    inner = FakeInner("root cause: invalid DAX")
    top = FakeClr("One or more errors occurred", inner=inner)
    out = format_clr_error(top, error_type="kpi_error")
    assert out["success"] is False
    assert out["error_type"] == "kpi_error"
    assert out["error"] == "One or more errors occurred"
    assert out["clr_root_cause"] == "root cause: invalid DAX"
    # Chain has at least 2 levels.
    assert len(out["clr_chain"]) >= 2


def test_format_clr_error_passes_through_context() -> None:
    exc = FakeClr("nope")
    out = format_clr_error(exc, error_type="culture_create_error", culture="en-US", attempt=2)
    assert out["culture"] == "en-US"
    assert out["attempt"] == 2


def test_format_clr_error_drops_none_context_values() -> None:
    exc = FakeClr("nope")
    out = format_clr_error(exc, error_type="x", culture="en-US", partition=None)
    assert "culture" in out
    assert "partition" not in out  # None dropped.


def test_format_clr_error_truncates_last_query() -> None:
    exc = FakeClr("syntax error")
    huge = "X" * 2000
    out = format_clr_error(exc, error_type="dax_error", last_query=huge)
    assert "last_query" in out
    assert "truncated" in out["last_query"]
    assert len(out["last_query"]) < 1000


def test_format_refresh_error_still_works() -> None:
    """Back-compat: format_refresh_error keeps its signature and shape."""
    exc = FakeClr("refresh blew up")
    out = format_refresh_error(exc, table="Sales", partition="2024", last_query="EVALUATE 1")
    assert out["success"] is False
    assert out["error_type"] == "refresh_error"
    assert out["table"] == "Sales"
    assert out["partition"] == "2024"
    assert out["last_query"] == "EVALUATE 1"
