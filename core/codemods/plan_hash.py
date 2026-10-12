"""Deterministic SHA256 plan hash for codemod staleness checks.

2026-09-22 review E-12: the hash covered model state only, so a caller could
preview ``inline_measure {delete_after:false}`` and apply with ``delete_after``
omitted (then defaulting to true) — same hash, measure deleted. It now covers
``transform`` + canonical JSON of ``args`` + the database identity (a reconnect
to another Desktop instance invalidates the plan) + model state.
``compute_plan_hash(model)`` without the extra arguments keeps the model-only
hash for callers that only want a state fingerprint.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

# Args that do not change WHAT a transform writes (gate / plumbing flags).
_NON_PLAN_ARGS = frozenset({"confirm_destructive", "evidence", "force"})


def canonical_args(args: Optional[Dict[str, Any]]) -> str:
    clean = {k: v for k, v in (args or {}).items() if k not in _NON_PLAN_ARGS}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def _model_parts(model: Any) -> list:
    parts = []
    for tbl in getattr(model, "tables", []) or []:
        tname = getattr(tbl, "name", "")
        for col in getattr(tbl, "columns", []) or []:
            parts.append(f"col\t{tname}\t{getattr(col, 'name', '')}")
        for m in getattr(tbl, "measures", []) or []:
            parts.append(f"meas\t{tname}\t{getattr(m, 'name', '')}\t{getattr(m, 'expression', '')}")
    # UDFs the handler attached (extract_udf): a function created after the preview stales the plan.
    for fn in sorted(str(f).lower() for f in (getattr(model, "udf_names", None) or ())):
        parts.append(f"fn\t{fn}")
    for rel in getattr(model, "relationships", []) or []:
        parts.append(
            "rel\t"
            + "\t".join(
                str(x)
                for x in (
                    getattr(rel, "from_table", ""),
                    getattr(rel, "from_column", ""),
                    getattr(rel, "to_table", ""),
                    getattr(rel, "to_column", ""),
                    getattr(rel, "is_active", True),
                )
            )
        )
    # sorted() is INTENTIONAL: TOM enumeration order is cosmetic, so a no-op
    # re-read must not spuriously invalidate a staged plan. Any real content
    # change still changes the sorted body (see test_plan_hash_content_change_*).
    return sorted(parts)


def compute_plan_hash(
    model: Any,
    transform: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    identity: Optional[str] = None,
) -> str:
    body = "\n".join(_model_parts(model))
    if transform is not None or args is not None or identity is not None:
        header = "\n".join(
            [
                "transform\t" + (transform or ""),
                "args\t" + canonical_args(args),
                "identity\t" + (identity or ""),
            ]
        )
        body = header + "\n--\n" + body
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
