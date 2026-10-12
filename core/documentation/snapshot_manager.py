"""Snapshot management for Power BI model documentation."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from core.utilities.json_utils import load_json
from .utils import ensure_dir, relationship_id, safe_filename, SNAPSHOT_SUFFIX


def snapshot_from_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """Create a snapshot dictionary from a documentation context.

    Args:
        context: Documentation context dictionary

    Returns:
        Snapshot dictionary with tables, measures, relationships, and metadata
    """
    tables_map: Dict[str, Dict[str, Any]] = {}
    for tbl in context.get("tables", []):
        tables_map[tbl.get("name") or "Unknown"] = {
            "description": tbl.get("description"),
            "hidden": bool(tbl.get("hidden")),
            "columns": [col.get("name") for col in tbl.get("columns", [])],
            "measures": [meas.get("name") for meas in tbl.get("measures", [])],
        }
    measures_map: Dict[str, Dict[str, Any]] = {}
    for meas in context.get("measures", []):
        key = f"{meas.get('table')}::{meas.get('name')}"
        measures_map[key] = {
            "description": meas.get("description"),
            "expression": meas.get("expression"),
            "dependencies": meas.get("dependencies"),
            "hidden": bool(meas.get("hidden")),
        }
    rels = sorted(relationship_id(rel) for rel in context.get("relationships", []))
    roles = {
        r.get("name"): sorted(
            f"{p.get('table')}: {p.get('filter_expression') or ''}"
            for p in r.get("table_permissions", [])
        )
        for r in context.get("roles", []) or []
        if r.get("name")
    }
    return {
        "database_name": context.get("database_name"),
        "generated_at": context.get("generated_at"),
        "summary_counts": (context.get("summary", {}) or {}).get("counts", {}),
        "tables": tables_map,
        "measures": measures_map,
        "relationships": rels,
        "roles": roles,
        "best_practices": context.get("best_practices", {}),
    }


def save_snapshot(
    context: Dict[str, Any], output_dir: Optional[str] = None, path: Optional[str] = None
) -> Dict[str, Any]:
    """Save a model snapshot to disk.

    Args:
        context: Documentation context dictionary
        output_dir: Directory for the legacy ``<db>_documentation_snapshot.json`` name
        path: Exact snapshot path (I-09: ``<doc stem>.snapshot.json`` next to the doc)

    Returns:
        dict: {"success": bool, "snapshot_path": str, "snapshot": dict}
    """
    snapshot = snapshot_from_context(context)
    if path is None:
        out_dir = ensure_dir(output_dir)
        path = os.path.join(out_dir, safe_filename(context.get("database_name"), SNAPSHOT_SUFFIX))
    else:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2)
    return {"success": True, "snapshot_path": path, "snapshot": snapshot}


def snapshot_candidates(
    path: Optional[str],
    output_dir: Optional[str] = None,
    database_name: Optional[str] = None,
) -> List[str]:
    """Where the previous snapshot for *path* (a .docx or .json) may live, in order."""
    from .utils import snapshot_path_for

    candidates: List[str] = []
    if path:
        if str(path).lower().endswith(".json"):
            candidates.append(path)
        else:
            candidates.append(snapshot_path_for(path))
            directory = os.path.dirname(os.path.abspath(path))
            for name in {database_name, "powerbi_model"}:
                if name:
                    candidates.append(os.path.join(directory, safe_filename(name, SNAPSHOT_SUFFIX)))
    out_dir = ensure_dir(output_dir) if (output_dir or not path) else None
    if out_dir:
        for name in (database_name, "powerbi_model"):
            if name:
                candidates.append(os.path.join(out_dir, safe_filename(name, SNAPSHOT_SUFFIX)))
    seen: List[str] = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
    return seen


def load_snapshot(
    path: Optional[str],
    output_dir: Optional[str] = None,
    database_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Load the previous snapshot.

    I-09: *path* may be the existing ``.docx`` (the documented ``input_path``) —
    its sibling ``<stem>.snapshot.json`` is used; the old code tried to parse
    the .docx as JSON and always reported "changes detected" with no change log.
    """
    for candidate in snapshot_candidates(path, output_dir, database_name):
        if os.path.exists(candidate):
            try:
                data = load_json(candidate)
            except Exception:
                continue
            if isinstance(data, dict):
                data.setdefault("_snapshot_path", candidate)
                return data
    return None


def compute_diff(previous: Optional[Dict[str, Any]], current: Dict[str, Any]) -> Dict[str, Any]:
    """Compute differences between two snapshots.

    Args:
        previous: Previous snapshot dictionary (or None for initial snapshot)
        current: Current snapshot dictionary

    Returns:
        dict: {
            "changes_detected": bool,
            "tables": {"added": [], "removed": [], "updated": []},
            "measures": {"added": [], "removed": [], "updated": []},
            "relationships": {"added": [], "removed": [], "updated": []},
            "best_practices": {"previous": int, "current": int}
        }
    """
    diff = {
        "changes_detected": False,
        "tables": {},
        "measures": {},
        "relationships": {},
        "roles": {},
        "best_practices": None,
    }
    if not previous:
        diff["changes_detected"] = True
        diff["baseline_missing"] = True
        return diff

    # Tables
    prev_tables = previous.get("tables", {})
    curr_tables = current.get("tables", {})
    added_tables = sorted(set(curr_tables.keys()) - set(prev_tables.keys()))
    removed_tables = sorted(set(prev_tables.keys()) - set(curr_tables.keys()))
    updated_tables = []
    for tbl in set(curr_tables.keys()) & set(prev_tables.keys()):
        prev_entry = prev_tables.get(tbl, {})
        curr_entry = curr_tables.get(tbl, {})
        if (
            prev_entry.get("description") != curr_entry.get("description")
            or set(prev_entry.get("columns", [])) != set(curr_entry.get("columns", []))
            or set(prev_entry.get("measures", [])) != set(curr_entry.get("measures", []))
        ):
            updated_tables.append(tbl)
    if added_tables or removed_tables or updated_tables:
        diff["changes_detected"] = True
        diff["tables"] = {
            "added": added_tables,
            "removed": removed_tables,
            "updated": updated_tables,
        }

    # Measures
    prev_measures = previous.get("measures", {})
    curr_measures = current.get("measures", {})
    added_measures = sorted(set(curr_measures.keys()) - set(prev_measures.keys()))
    removed_measures = sorted(set(prev_measures.keys()) - set(curr_measures.keys()))
    updated_measures = []
    for name in set(curr_measures.keys()) & set(prev_measures.keys()):
        prev_entry = prev_measures.get(name, {})
        curr_entry = curr_measures.get(name, {})
        if (
            prev_entry.get("expression") != curr_entry.get("expression")
            or prev_entry.get("description") != curr_entry.get("description")
            or prev_entry.get("dependencies") != curr_entry.get("dependencies")
        ):
            updated_measures.append(name)
    if added_measures or removed_measures or updated_measures:
        diff["changes_detected"] = True
        diff["measures"] = {
            "added": added_measures,
            "removed": removed_measures,
            "updated": updated_measures,
        }

    # Relationships
    prev_rels = set(previous.get("relationships", []))
    curr_rels = set(current.get("relationships", []))
    added_rels = sorted(curr_rels - prev_rels)
    removed_rels = sorted(prev_rels - curr_rels)
    if added_rels or removed_rels:
        diff["changes_detected"] = True
        diff["relationships"] = {
            "added": added_rels,
            "removed": removed_rels,
            "updated": [],
        }

    # Roles (RLS filters)
    prev_roles = previous.get("roles", {}) or {}
    curr_roles = current.get("roles", {}) or {}
    added_roles = sorted(set(curr_roles) - set(prev_roles))
    removed_roles = sorted(set(prev_roles) - set(curr_roles))
    updated_roles = sorted(
        r for r in set(curr_roles) & set(prev_roles) if curr_roles[r] != prev_roles[r]
    )
    if added_roles or removed_roles or updated_roles:
        diff["changes_detected"] = True
        diff["roles"] = {"added": added_roles, "removed": removed_roles, "updated": updated_roles}

    # Best practices delta
    prev_bp = previous.get("best_practices", {}) or {}
    curr_bp = current.get("best_practices", {}) or {}
    if prev_bp.get("total_issues") != curr_bp.get("total_issues"):
        diff["changes_detected"] = True
        diff["best_practices"] = {
            "previous": prev_bp.get("total_issues"),
            "current": curr_bp.get("total_issues"),
        }

    return diff
