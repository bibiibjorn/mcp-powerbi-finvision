"""Tests for core.autonomous.clr_errors.

CLR refresh errors arrive as wrapped pythonnet exceptions whose chain
must be unwrapped to surface the actionable root cause. The chain walker
needs to handle:

  - Pure-Python chains (no GetType / Message / InnerException attrs)
  - .NET-shaped exceptions exposing GetType()/Message/InnerException via attrs
  - Mixed chains and Python's __cause__/__context__ fallback
  - Cycles (depth limit + seen-id guard)
  - Optional Source/HResult/StackTrace metadata
  - Stack trace truncation (first line only, max 400 chars)

format_refresh_error wraps the chain into the response shape that
12_Autonomous_Workflow returns to the LLM.
"""

from __future__ import annotations


from core.autonomous.clr_errors import (
    extract_clr_exception_chain,
    format_refresh_error,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _ClrType:
    """Stand-in for a pythonnet System.Type with FullName."""

    def __init__(self, full_name: str):
        self.FullName = full_name

    def __str__(self):
        return self.FullName


class FakeClrException(Exception):
    """
    Stand-in for a pythonnet-wrapped CLR exception.

    Exposes GetType() / Message / Source / HResult / StackTrace / InnerException
    so the chain walker exercises the .NET branches.
    """

    def __init__(
        self,
        type_name: str,
        message: str,
        inner=None,
        source: str | None = None,
        hresult: int | None = None,
        stack_trace: str | None = None,
    ):
        super().__init__(message)
        self._clr_type = _ClrType(type_name)
        self.Message = message
        self.InnerException = inner
        if source is not None:
            self.Source = source
        if hresult is not None:
            self.HResult = hresult
        if stack_trace is not None:
            self.StackTrace = stack_trace

    def GetType(self):
        return self._clr_type


# ---------------------------------------------------------------------------
# extract_clr_exception_chain
# ---------------------------------------------------------------------------


def test_chain_pure_python_single_level():
    exc = ValueError("plain python")
    chain = extract_clr_exception_chain(exc)
    assert len(chain) == 1
    assert chain[0]["type"] == "ValueError"
    assert chain[0]["message"] == "plain python"


def test_chain_unwraps_clr_inner_exceptions():
    root = FakeClrException(
        "Microsoft.AnalysisServices.OperationException",
        "Column 'X' not found",
    )
    middle = FakeClrException(
        "System.Exception",
        "Refresh of partition failed",
        inner=root,
    )
    top = FakeClrException(
        "System.AggregateException",
        "One or more errors occurred",
        inner=middle,
    )

    chain = extract_clr_exception_chain(top)

    assert len(chain) == 3
    assert chain[0]["type"] == "System.AggregateException"
    assert chain[1]["type"] == "System.Exception"
    assert chain[2]["type"] == "Microsoft.AnalysisServices.OperationException"
    assert chain[2]["message"] == "Column 'X' not found"


def test_chain_captures_source_and_hresult():
    exc = FakeClrException(
        "System.Exception",
        "boom",
        source="Microsoft.AnalysisServices.Tabular",
        hresult=-2147467259,
    )
    chain = extract_clr_exception_chain(exc)
    assert chain[0]["source"] == "Microsoft.AnalysisServices.Tabular"
    assert chain[0]["hresult"] == -2147467259


def test_chain_truncates_long_stack_trace_to_first_line():
    long_first = "at Foo.Bar() " + "x" * 1000
    full_trace = long_first + "\nat Baz.Qux()\nat Quux.Corge()"
    exc = FakeClrException("System.Exception", "boom", stack_trace=full_trace)
    chain = extract_clr_exception_chain(exc)
    assert "stacktrace" in chain[0]
    st = chain[0]["stacktrace"]
    assert "Baz.Qux" not in st  # second line dropped
    assert len(st) <= 400


def test_chain_omits_empty_optional_fields():
    exc = FakeClrException("System.Exception", "boom", source="", hresult=None)
    chain = extract_clr_exception_chain(exc)
    assert "source" not in chain[0]
    assert "hresult" not in chain[0]


def test_chain_respects_max_depth():
    # Build a 10-deep chain; default max_depth=8.
    deepest = FakeClrException("E10", "deepest")
    cur = deepest
    for i in range(9, 0, -1):
        cur = FakeClrException(f"E{i}", f"level {i}", inner=cur)

    chain = extract_clr_exception_chain(cur, max_depth=8)
    assert len(chain) == 8
    # We should NOT have reached the 10th level
    assert chain[-1]["type"] != "E10"


def test_chain_breaks_cycles():
    """If pythonnet wires an exception's InnerException back to itself, the
    walker must exit via the seen-id guard, not loop forever."""
    a = FakeClrException("A", "a")
    b = FakeClrException("B", "b", inner=a)
    a.InnerException = b  # cycle: a -> b -> a -> ...

    chain = extract_clr_exception_chain(a, max_depth=20)
    # Cycle guard should give us exactly 2 unique entries.
    assert len(chain) == 2
    assert {entry["type"] for entry in chain} == {"A", "B"}


def test_chain_falls_back_to_python_cause_when_no_inner():
    root = ValueError("python root")
    try:
        try:
            raise root
        except ValueError as e:
            raise RuntimeError("python wrapper") from e
    except RuntimeError as captured:
        chain = extract_clr_exception_chain(captured)

    assert len(chain) == 2
    assert chain[0]["type"] == "RuntimeError"
    assert chain[1]["type"] == "ValueError"
    assert chain[1]["message"] == "python root"


def test_chain_handles_none_inner_gracefully():
    exc = FakeClrException("System.Exception", "single", inner=None)
    chain = extract_clr_exception_chain(exc)
    assert len(chain) == 1


def test_chain_handles_get_type_raising():
    """GetType() that throws falls back to Python type name."""

    class BrokenClr(Exception):
        Message = "still readable"

        def GetType(self):
            raise RuntimeError("CLR broke")

    chain = extract_clr_exception_chain(BrokenClr("x"))
    assert chain[0]["type"] == "BrokenClr"
    assert chain[0]["message"] == "still readable"


def test_chain_strips_message_whitespace():
    exc = FakeClrException("System.Exception", "  hello  \n")
    chain = extract_clr_exception_chain(exc)
    assert chain[0]["message"] == "hello"


# ---------------------------------------------------------------------------
# format_refresh_error
# ---------------------------------------------------------------------------


def test_format_refresh_error_extracts_root_cause():
    root = FakeClrException(
        "Microsoft.AnalysisServices.OperationException",
        "Column 'X' not found in table 'Sales'",
    )
    middle = FakeClrException("System.Exception", "Partition refresh failed", inner=root)
    top = FakeClrException("System.AggregateException", "Aggregate", inner=middle)

    result = format_refresh_error(top, table="Sales", partition="Sales-2024")

    assert result["success"] is False
    assert result["error_type"] == "refresh_error"
    assert result["error"] == "Aggregate"
    assert result["clr_root_cause"] == "Column 'X' not found in table 'Sales'"
    assert result["clr_root_type"] == "Microsoft.AnalysisServices.OperationException"
    assert result["table"] == "Sales"
    assert result["partition"] == "Sales-2024"
    assert len(result["clr_chain"]) == 3


def test_format_refresh_error_omits_optional_metadata():
    exc = FakeClrException("System.Exception", "boom")
    result = format_refresh_error(exc)
    assert "table" not in result
    assert "partition" not in result
    assert "last_query" not in result


def test_format_refresh_error_truncates_long_query():
    big_q = "EVALUATE " + "X" * 2000
    exc = FakeClrException("System.Exception", "boom")
    result = format_refresh_error(exc, last_query=big_q)
    assert "truncated" in result["last_query"]
    # 800 chars + truncation suffix
    assert result["last_query"].startswith("EVALUATE ")
    assert len(result["last_query"]) < len(big_q)


def test_format_refresh_error_keeps_short_query_intact():
    q = "EVALUATE TOPN(10, Foo)"
    exc = FakeClrException("System.Exception", "boom")
    result = format_refresh_error(exc, last_query=q)
    assert result["last_query"] == q


def test_format_refresh_error_with_pure_python_exception():
    """Plain Python exceptions still produce a sensible chain + root."""
    exc = ValueError("nothing fancy")
    result = format_refresh_error(exc)

    assert result["success"] is False
    assert result["error"] == "nothing fancy"
    assert result["clr_root_cause"] == "nothing fancy"
    assert result["clr_root_type"] == "ValueError"
    assert len(result["clr_chain"]) == 1


def test_format_refresh_error_handles_empty_chain_defensively():
    """If chain extraction returns nothing, we still produce a usable shape."""

    class WeirdExc(BaseException):
        # No Message attr; str() returns ""
        def __str__(self):
            return ""

    exc = WeirdExc()
    result = format_refresh_error(exc)
    assert result["success"] is False
    assert result["error_type"] == "refresh_error"
    # chain has at least the top-level entry
    assert len(result["clr_chain"]) >= 1
