"""Canned INFO.* / DISCOVER_* probes for live VertiPaq + session diagnostics.

Each probe is a named DAX or DMV query that returns a small, structured
result. Wraps the live `connection_state.query_executor` so probes execute
against the currently connected model.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional

from core.infrastructure.connection_state import connection_state


class ProbeNotFoundError(LookupError):
    def __init__(self, message: str, did_you_mean: Optional[List[str]] = None):
        super().__init__(message)
        self.did_you_mean: List[str] = list(did_you_mean or [])


PROBE_REGISTRY: Dict[str, Dict[str, str]] = {
    "vertipaq_columns": {
        "description": (
            "Per-column VertiPaq storage stats (rows, dict size, data size, "
            "encoding, cardinality). Sourced from INFO.STORAGETABLECOLUMNS()."
        ),
        "dax": "EVALUATE INFO.STORAGETABLECOLUMNS()",
    },
    "vertipaq_dictionary": {
        "description": (
            "Per-column dictionary storage details (size, type, count). "
            "Sourced from INFO.DICTIONARYSTORAGES()."
        ),
        "dax": "EVALUATE INFO.DICTIONARYSTORAGES()",
    },
    "calc_dependency": {
        "description": (
            "Runtime calculation dependency walk (object -> referenced object). "
            "Sourced from INFO.CALCDEPENDENCY()."
        ),
        "dax": "EVALUATE INFO.CALCDEPENDENCY()",
    },
    "object_memory_usage": {
        "description": (
            "Per-object memory footprint at the AS engine level. "
            "DMV: $SYSTEM.DISCOVER_OBJECT_MEMORY_USAGE."
        ),
        "dax": "SELECT * FROM $SYSTEM.DISCOVER_OBJECT_MEMORY_USAGE",
    },
    "object_activity": {
        "description": (
            "Per-object query/activity counters. DMV: $SYSTEM.DISCOVER_OBJECT_ACTIVITY."
        ),
        "dax": "SELECT * FROM $SYSTEM.DISCOVER_OBJECT_ACTIVITY",
    },
    "sessions": {
        "description": "Currently active engine sessions. DMV: $SYSTEM.DISCOVER_SESSIONS.",
        "dax": "SELECT * FROM $SYSTEM.DISCOVER_SESSIONS",
    },
    "user_defined_functions": {
        "description": (
            "User-defined DAX functions registered on the model. "
            "Sourced from INFO.USERDEFINEDFUNCTIONS()."
        ),
        "dax": "EVALUATE INFO.USERDEFINEDFUNCTIONS()",
    },
    "refresh_policies": {
        "description": (
            "Refresh policies declared on tables (incremental refresh / hybrid). "
            "Sourced from INFO.REFRESHPOLICIES()."
        ),
        "dax": "EVALUATE INFO.REFRESHPOLICIES()",
    },
}


def list_probes() -> List[Dict[str, str]]:
    return [
        {"name": name, "description": entry["description"], "dax": entry["dax"]}
        for name, entry in PROBE_REGISTRY.items()
    ]


def run_probe(name: str) -> Dict[str, Any]:
    if name not in PROBE_REGISTRY:
        close = difflib.get_close_matches(str(name), list(PROBE_REGISTRY), n=3, cutoff=0.5)
        raise ProbeNotFoundError(
            f"Unknown probe '{name}'. Use operation='list_dmv_probes' to see the probe names.",
            did_you_mean=close,
        )
    qx = connection_state.query_executor
    if qx is None:
        return {"success": False, "error": "Not connected (query_executor unavailable)"}
    dax = PROBE_REGISTRY[name]["dax"]
    try:
        # Dispatch on query kind: DMV ("SELECT ... FROM $SYSTEM...") goes
        # through execute_dmv_query; DAX ("EVALUATE ...") goes through
        # validate_and_execute_dax.
        if dax.lstrip().upper().startswith("SELECT"):
            raw = qx.execute_dmv_query(dax)
        else:
            raw = qx.validate_and_execute_dax(dax, 0)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Probe '{name}' failed: {exc}",
            "probe": name,
            "dax": dax,
        }
    out: Dict[str, Any] = {}
    if isinstance(raw, dict):
        out.update(raw)
    # Metadata wins over executor result keys.
    out["probe"] = name
    out["dax"] = dax
    return out
