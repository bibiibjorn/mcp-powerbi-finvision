"""
Offline lookups in the semantic model a PBIR report is bound to (WP9).

The user's reports are THIN: ``<report>/definition.pbir`` binds them to a
shared model through ``datasetReference.byPath`` (``../S01-SemanticModel
.SemanticModel``), so the old "same-stem ``.SemanticModel`` sibling"
assumption never found the model (H-27) and authoring could not tell a
measure from a column (H-18). This module resolves the model with
``pbir_model.read_dataset_reference`` and reads just enough TMDL:

* ``model_index(definition_path)`` -> ``{table: {"measures": {...},
  "columns": {name: dataType}}}`` (cached per model folder + TMDL mtimes);
* ``field_kind(definition_path, table, field)`` -> ``"Measure"`` /
  ``"Column"`` / None;
* ``column_type(definition_path, table, column)`` -> TMDL ``dataType``
  (``int64`` / ``double`` / ``decimal`` / ``string`` / ``dateTime`` /
  ``boolean``) or None.

Pure file reads: never touches a live model / Desktop.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_NAME = r"(?:'(?:[^']|'')+'|[^\s=']+)"
_TABLE_RE = re.compile(r"^table\s+(" + _NAME + r")\s*$", re.MULTILINE)
_MEASURE_RE = re.compile(r"^\tmeasure\s+(" + _NAME + r")\s*=", re.MULTILINE)
_COLUMN_RE = re.compile(r"^\tcolumn\s+(" + _NAME + r")\s*(=.*)?$", re.MULTILINE)
_DATATYPE_RE = re.compile(r"^\t\tdataType:\s*(\w+)", re.MULTILINE)

_CACHE: Dict[str, Tuple[Tuple[Any, ...], Dict[str, Dict[str, Any]]]] = {}
_CACHE_LOCK = threading.Lock()


def _unquote(name: str) -> str:
    name = name.strip()
    if len(name) >= 2 and name.startswith("'") and name.endswith("'"):
        return name[1:-1].replace("''", "'")
    return name


def model_dir_for(definition_path: Any) -> Optional[Path]:
    """The ``.SemanticModel`` folder bound to the report owning *definition_path*."""
    from core.pbip.pbir_model import read_dataset_reference

    ddir = Path(str(definition_path))
    report_dir = ddir.parent if ddir.name == "definition" else ddir
    try:
        ref = read_dataset_reference(report_dir)
    except Exception:  # pragma: no cover - read_dataset_reference never raises
        return None
    return Path(ref["model_dir"]) if ref.get("model_dir") else None


def _parse_table_file(text: str) -> Tuple[Optional[str], Dict[str, Any]]:
    m = _TABLE_RE.search(text)
    if not m:
        return None, {}
    table = _unquote(m.group(1))
    measures = {_unquote(x.group(1)) for x in _MEASURE_RE.finditer(text)}
    columns: Dict[str, Optional[str]] = {}
    col_matches = list(_COLUMN_RE.finditer(text))
    for idx, cm in enumerate(col_matches):
        start = cm.end()
        # The column block ends at the next top-level (one-tab) declaration.
        nxt = re.search(r"^\t\S", text[start:], re.MULTILINE)
        block = text[start : start + nxt.start()] if nxt else text[start:]
        dt = _DATATYPE_RE.search(block)
        columns[_unquote(cm.group(1))] = dt.group(1) if dt else None
    return table, {"measures": measures, "columns": columns}


def model_index(definition_path: Any) -> Dict[str, Dict[str, Any]]:
    """``{table: {"measures": set, "columns": {name: dataType}}}`` (empty if no model)."""
    mdir = model_dir_for(definition_path)
    if mdir is None:
        return {}
    tables_dir = mdir / "definition" / "tables"
    try:
        files = sorted(p for p in tables_dir.iterdir() if p.suffix == ".tmdl")
    except OSError:
        return {}
    sig = tuple((p.name, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p in files)
    key = str(tables_dir)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    index: Dict[str, Dict[str, Any]] = {}
    for p in files:
        try:
            text = p.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        except OSError:
            continue
        table, info = _parse_table_file(text)
        if table:
            index[table] = info
    with _CACHE_LOCK:
        _CACHE[key] = (sig, index)
    return index


def field_kind(definition_path: Any, table: str, field: str) -> Optional[str]:
    """``"Measure"`` / ``"Column"`` for ``table[field]`` in the bound model, else None."""
    info = model_index(definition_path).get(table)
    if not info:
        return None
    if field in info["measures"]:
        return "Measure"
    if field in info["columns"]:
        return "Column"
    return None


def measure_owner(definition_path: Any, measure: str) -> Optional[str]:
    """Table hosting *measure* when exactly one table defines it."""
    owners = [t for t, info in model_index(definition_path).items() if measure in info["measures"]]
    return owners[0] if len(owners) == 1 else None


def column_type(definition_path: Any, table: str, column: str) -> Optional[str]:
    info = model_index(definition_path).get(table)
    if not info:
        return None
    dt = info["columns"].get(column)
    return dt.lower() if isinstance(dt, str) else None


#: TMDL dataType -> ``pbir_model.encode_literal`` kind.
LITERAL_KIND_FOR_DATATYPE = {
    "int64": "int64",
    "double": "double",
    "decimal": "decimal",
    "string": "string",
    "datetime": "datetime",
    "date": "datetime",
    "boolean": "bool",
}
