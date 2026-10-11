"""Regression: the top-level unexpected-error handler must unwrap a CLR
exception chain instead of surfacing only the opaque outer message.

A raw pythonnet TargetInvocationException reads "Exception has been thrown by
the target of an invocation" — useless. handle_unexpected_error now walks the
InnerException chain (extract_clr_exception_chain) and surfaces the real root
cause + the full chain so an autonomous caller sees what actually failed.
"""

from core.validation.error_handler import ErrorHandler


class _FakeClrException(Exception):
    """Mimics a pythonnet CLR exception with GetType/Message/InnerException."""

    def __init__(self, type_name, message, inner=None):
        super().__init__(message)
        self._type_name = type_name
        self.Message = message
        self.InnerException = inner
        self.Source = "Microsoft.AnalysisServices.Tabular"
        self.HResult = -2146233088

    def GetType(self):
        return type("T", (), {"FullName": self._type_name})()


def test_unexpected_error_surfaces_clr_root_cause():
    root = _FakeClrException("System.IO.IOException", "The process cannot access the file")
    outer = _FakeClrException(
        "System.Reflection.TargetInvocationException",
        "Exception has been thrown by the target of an invocation.",
        inner=root,
    )
    out = ErrorHandler.handle_unexpected_error("02_Model_Operations", outer)
    assert out["success"] is False
    # The chain must be present and reach the real root cause.
    chain = out.get("clr_chain") or out.get("context", {}).get("clr_chain")
    assert chain, "no clr_chain surfaced"
    messages = " ".join(str(level.get("message", "")) for level in chain)
    assert "cannot access the file" in messages
    # A root-cause summary should point at the innermost cause, not the wrapper.
    root_cause = out.get("clr_root_cause") or out.get("context", {}).get("clr_root_cause")
    assert root_cause and "cannot access the file" in root_cause


def test_plain_python_error_still_handled():
    # Non-CLR exceptions must still produce the normal envelope (no crash).
    out = ErrorHandler.handle_unexpected_error("04_Run_DAX", ValueError("bad value"))
    assert out["success"] is False
    assert out["error_type"] == "unexpected_error"
