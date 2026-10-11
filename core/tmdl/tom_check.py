"""
Offline TOM deserialization check (review G-05 / G-27).

Runs the bundled ``Microsoft.AnalysisServices.Tabular.TmdlSerializer`` over a
TMDL definition folder — no server, no Desktop, no network — which is exactly
what Desktop does when it opens a PBIP. It is the authoritative "will this
project open" answer; :class:`core.tmdl.validator.TmdlValidator` is the fast
structural pre-check with line numbers.

UDF gate: the TOM client library refuses ``function`` objects below
compatibility level 1000000 ("compatibility level of 1702 is below the minimal
compatibility level of 1000000 needed for ... function X"), even though
Desktop/the service accept them at 1702. That message is NOT a model defect
(it reproduces on an untouched S02 tree with both the bundled TOM 17.0.30 and
TE3's TOM 17.0.17). When it is the only failure the folder is re-checked from
a temporary copy without the function definitions and the result says
``udf_gate: true``.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# TOM's own text spells it "compatability" in the second half; match both.
_UDF_GATE_RE = re.compile(r"compatib\w* level.*below.*(needed|required).*function", re.I | re.S)

_LOCK = threading.Lock()
_SERIALIZER: Any = None
_LOAD_ERROR: Optional[str] = None

# Test seam: replace with a callable(folder) -> None that raises on failure.
_deserialize_override: Optional[Callable[[str], None]] = None


def _load_serializer() -> Any:
    global _SERIALIZER, _LOAD_ERROR
    with _LOCK:
        if _SERIALIZER is not None or _LOAD_ERROR is not None:
            return _SERIALIZER
        try:
            from core.infrastructure.dll_paths import load_amo_assemblies

            if not load_amo_assemblies():
                _LOAD_ERROR = "AMO/TOM assemblies not available"
                return None
            from Microsoft.AnalysisServices.Tabular import TmdlSerializer  # type: ignore

            _SERIALIZER = TmdlSerializer
        except Exception as e:  # noqa: BLE001 - CLR interop
            _LOAD_ERROR = f"{type(e).__name__}: {e}"
        return _SERIALIZER


def _deserialize(folder: str) -> None:
    if _deserialize_override is not None:
        _deserialize_override(folder)
        return
    ser = _load_serializer()
    if ser is None:
        raise RuntimeError(f"__unavailable__:{_LOAD_ERROR}")
    ser.DeserializeDatabaseFromFolder(folder)


def is_udf_gate_message(message: str) -> bool:
    """True for the TOM client-library UDF/compatibility-level gate message."""
    return bool(_UDF_GATE_RE.search(message or ""))


def _first_line(e: BaseException) -> str:
    text = str(e) or type(e).__name__
    lines = [
        ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("at ")
    ]
    return " | ".join(lines[:6])[:600]


def tom_deserialize_check(definition_dir: str) -> Dict[str, Any]:
    """Deserialize *definition_dir* with TOM.

    Returns ``{ran, ok, error, udf_gate, elapsed_ms, reason}``; ``ran`` is
    False (with ``reason``) when the CLR / TOM assemblies cannot be loaded.
    """
    folder = Path(definition_dir)
    if (folder / "definition").is_dir():
        folder = folder / "definition"
    t0 = time.perf_counter()
    out: Dict[str, Any] = {"ran": False, "ok": None, "error": None, "udf_gate": False}
    try:
        _deserialize(str(folder))
        out.update(ran=True, ok=True)
    except Exception as e:  # noqa: BLE001 - CLR exceptions
        msg = _first_line(e)
        if msg.startswith("__unavailable__:"):
            out["reason"] = msg.split(":", 1)[1]
            out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
            return out
        out["ran"] = True
        if is_udf_gate_message(msg):
            out["udf_gate"] = True
            out["udf_gate_message"] = msg
            retry = _retry_without_functions(folder)
            out["ok"] = retry is None
            out["error"] = retry
        else:
            out["ok"] = False
            out["error"] = msg
    out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
    return out


def load_database_from_folder(definition_dir: str) -> Dict[str, Any]:
    """Deserialize *definition_dir* with TOM and RETURN the ``Database`` object.

    Same parser and UDF-gate handling as :func:`tom_deserialize_check` (a folder
    whose only failure is the client-library UDF gate is re-read from a temp
    copy without the function definitions; the result then says
    ``udf_gate: true`` and the returned model has NO functions). Returns
    ``{ran, ok, database, error, udf_gate, reason}``; ``ran`` is False when the
    CLR / TOM assemblies cannot be loaded. Callers own reading what they need
    from ``database`` — nothing here touches a server.
    """
    folder = Path(definition_dir)
    if (folder / "definition").is_dir():
        folder = folder / "definition"
    out: Dict[str, Any] = {
        "ran": False,
        "ok": False,
        "database": None,
        "error": None,
        "udf_gate": False,
        "reason": None,
    }
    ser = _load_serializer()
    if ser is None:
        out["reason"] = _LOAD_ERROR or "AMO/TOM assemblies not available"
        return out
    out["ran"] = True
    try:
        out["database"] = ser.DeserializeDatabaseFromFolder(str(folder))
        out["ok"] = True
        return out
    except Exception as e:  # noqa: BLE001 - CLR exceptions
        msg = _first_line(e)
        if not is_udf_gate_message(msg):
            out["error"] = msg
            return out
    out["udf_gate"] = True
    tmp = Path(tempfile.mkdtemp(prefix="tmdl_tomload_"))
    try:
        copy = tmp / "definition"
        shutil.copytree(folder, copy)
        fn = copy / "functions.tmdl"
        if fn.exists():
            fn.unlink()
        if (copy / "functions").is_dir():
            shutil.rmtree(copy / "functions")
        try:
            out["database"] = ser.DeserializeDatabaseFromFolder(str(copy))
            out["ok"] = True
        except Exception as e:  # noqa: BLE001
            out["error"] = _first_line(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _retry_without_functions(folder: Path) -> Optional[str]:
    tmp = Path(tempfile.mkdtemp(prefix="tmdl_tomcheck_"))
    try:
        copy = tmp / "definition"
        shutil.copytree(folder, copy)
        fn = copy / "functions.tmdl"
        if fn.exists():
            fn.unlink()
        if (copy / "functions").is_dir():
            shutil.rmtree(copy / "functions")
        try:
            _deserialize(str(copy))
            return None
        except Exception as e:  # noqa: BLE001
            msg = _first_line(e)
            return None if is_udf_gate_message(msg) else msg
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
