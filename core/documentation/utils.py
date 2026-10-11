"""Shared utilities for documentation generation."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.dax.dax_reference_parser import DaxReferenceIndex
from core.utilities.type_conversions import safe_bool as _safe_bool

# Constants
DEFAULT_SUBDIR = os.path.join("exports", "docs")
SNAPSHOT_SUFFIX = "documentation_snapshot.json"
GRAPH_SIZE = (11, 8)

# Branding configuration
DEFAULT_BRANDING = {
    "primary_color": (0, 70, 127),  # Dark blue
    "secondary_color": (0, 102, 204),  # Medium blue
    "accent_color": (68, 114, 196),  # Light blue
    "header_bg": (0, 70, 127),  # Dark blue for table headers
    "logo_path": None,  # Path to company logo
    "company_name": None,  # Company name for footer
}


def now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


# core/documentation/utils.py -> repo root (three levels up). I-24: two levels
# landed in ``core/`` so the default went to ``core/exports/docs``, outside the
# ``exports/`` root the path guard advertises.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ensure_dir(path: Optional[str]) -> str:
    """Ensure directory exists, creating it if necessary (relative = repo-relative)."""
    base = path or DEFAULT_SUBDIR
    if not os.path.isabs(base):
        base = os.path.join(REPO_ROOT, base)
    os.makedirs(base, exist_ok=True)
    return base


def resolve_doc_target(output_path: Optional[str]) -> Tuple[str, Optional[str]]:
    """``(directory, file_path_or_None)`` for an ``output_path`` argument.

    I-09: ``output_path="X.docx"`` is a FILE (it used to become a directory
    named ``X.docx``); anything else is a directory.
    """
    if output_path and str(output_path).lower().endswith(".docx"):
        target = output_path if os.path.isabs(output_path) else os.path.join(REPO_ROOT, output_path)
        directory = os.path.dirname(target) or REPO_ROOT
        os.makedirs(directory, exist_ok=True)
        return directory, target
    return ensure_dir(output_path), None


def snapshot_path_for(doc_path: str) -> str:
    """The snapshot that belongs to a generated document: ``<stem>.snapshot.json``."""
    root, _ext = os.path.splitext(doc_path)
    return root + ".snapshot.json"


# XML 1.0 forbids C0 controls except tab/LF/CR (and lone surrogates / U+FFFE /
# U+FFFF); python-docx raises ValueError on them (I-17).
_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def xml_safe(value: Any) -> Any:
    """Strip characters Word/XML cannot store from a string (other types pass)."""
    if isinstance(value, str):
        return _XML_INVALID.sub("", value)
    return value


def sanitize_context(obj: Any) -> Any:
    """Recursively apply :func:`xml_safe` to every string in *obj* (new object)."""
    if isinstance(obj, str):
        return xml_safe(obj)
    if isinstance(obj, dict):
        return {k: sanitize_context(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_context(v) for v in obj]
    return obj


def safe_filename(name: Optional[str], suffix: str) -> str:
    """Create a safe filename from a model name and suffix."""
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "powerbi_model").strip())
    clean = clean.strip("._") or "powerbi_model"
    return f"{clean}_{suffix}"


def pick(row: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Pick first available value from row using multiple key names."""
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
        alt = f"[{key}]"
        if alt in row and row[alt] not in (None, ""):
            return row[alt]
    return default


def to_bool(value: Any) -> bool:
    """Convert various types to boolean."""
    return _safe_bool(value)


def build_reference_index(
    measures: List[Dict[str, Any]], columns: List[Dict[str, Any]]
) -> Optional[DaxReferenceIndex]:
    """Build DAX reference index for dependency analysis."""
    try:
        return DaxReferenceIndex(measures, columns)
    except Exception:
        return None


def format_edge(source: str, targets: List[str], target_type: str) -> Dict[str, Any]:
    """Format dependency edge for documentation."""
    return {
        "source": source,
        "targets": targets,
        "target_type": target_type,
    }


def truncate(text: str, length: int = 400) -> str:
    """Truncate text to specified length with ellipsis."""
    if len(text) <= length:
        return text
    return text[: length - 3] + "..."


def relationship_id(rel: Dict[str, Any]) -> str:
    """Generate unique identifier for a relationship."""
    left = f"{rel.get('from_table')}[{rel.get('from_column')}]"
    right = f"{rel.get('to_table')}[{rel.get('to_column')}]"
    card = rel.get("cardinality") or "?"
    direct = rel.get("direction") or "?"
    active = "Active" if rel.get("is_active") else "Inactive"
    return f"{left} -> {right} | {card} | {direct} | {active}"
