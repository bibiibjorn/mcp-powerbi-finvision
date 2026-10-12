"""UDF capability probes for the clone / extract tooling (review F-15).

``15_DAX_Refactor extract_udf`` and ``05_DAX_Intelligence find_clones`` need two facts from the
live model: the compatibility level (UDFs need 1702) and the names of the functions that already
exist. Both come through the query executor (``INFO.MODEL()`` / the UDF DMV read), never through
AMO, and every probe degrades to ``None`` ("unknown") instead of raising.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.dax.clone_detector import MIN_UDF_COMPATIBILITY_LEVEL

logger = logging.getLogger(__name__)

__all__ = [
    "MIN_UDF_COMPATIBILITY_LEVEL",
    "attach_udf_context",
    "read_compatibility_level",
    "read_function_names",
]


def _unbracket(key: Any) -> str:
    k = str(key)
    return k[1:-1] if k.startswith("[") and k.endswith("]") else k


def _catalog_compatibility_level(qe: Any) -> Optional[int]:
    """``$SYSTEM.DBSCHEMA_CATALOGS`` -> COMPATIBILITY_LEVEL of the connected catalog.

    Desktop's ``INFO.MODEL()`` has no CompatibilityLevel column (verified live on Desktop
    2.157, 2026-10-03), so this is the primary source; INFO.MODEL stays a fallback for
    engines that do expose it."""
    run = getattr(qe, "execute_dmv_query", None)
    if not callable(run):
        return None
    try:
        res = run("SELECT [CATALOG_NAME], [COMPATIBILITY_LEVEL] FROM $SYSTEM.DBSCHEMA_CATALOGS")
    except Exception as e:  # noqa: BLE001
        logger.debug("DBSCHEMA_CATALOGS compatibility level probe failed: %s", e)
        return None
    if not isinstance(res, dict) or res.get("success") is False:
        return None
    rows = [r for r in (res.get("rows") or res.get("data") or []) if isinstance(r, dict)]
    levels = []
    for row in rows:
        for k, v in row.items():
            if _unbracket(k).replace("_", "").lower() == "compatibilitylevel":
                try:
                    levels.append(int(v))
                except (TypeError, ValueError):
                    pass
    # One catalog on Desktop; with several, only an unambiguous answer counts.
    return levels[0] if len(set(levels)) == 1 else None


def read_compatibility_level(qe: Any) -> Optional[int]:
    """Compatibility level of the connected model (DBSCHEMA_CATALOGS, then ``INFO.MODEL()``);
    None when it cannot be read."""
    if qe is None:
        return None
    level = _catalog_compatibility_level(qe)
    if level is not None:
        return level
    try:
        res = qe.validate_and_execute_dax("EVALUATE INFO.MODEL()", 0, bypass_cache=True)
    except TypeError:
        try:
            res = qe.validate_and_execute_dax("EVALUATE INFO.MODEL()")
        except Exception:  # noqa: BLE001
            return None
    except Exception as e:  # noqa: BLE001
        logger.debug("compatibility level probe failed: %s", e)
        return None
    if not isinstance(res, dict) or not res.get("success"):
        return None
    for row in res.get("rows") or []:
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            if _unbracket(k).lower() == "compatibilitylevel":
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return None
    return None


def read_function_names(qe: Any) -> Optional[List[str]]:
    """Names of the model's UDFs (DMV read); ``[]`` for a model without UDF support, None when
    the read itself failed."""
    if qe is None or not hasattr(qe, "list_udfs"):
        return None
    try:
        res = qe.list_udfs()
    except Exception as e:  # noqa: BLE001
        logger.debug("UDF name probe failed: %s", e)
        return None
    if not isinstance(res, dict) or not res.get("success"):
        return None
    out: List[str] = []
    for row in res.get("rows") or []:
        if isinstance(row, dict):
            for k, v in row.items():
                if _unbracket(k).lower() == "name" and v:
                    out.append(str(v))
                    break
    return out


def attach_udf_context(model: Any, qe: Any) -> Dict[str, Any]:
    """Set ``model.compatibility_level`` / ``model.udf_names`` (read by the extract_udf transform
    and the plan hash). Returns what was found."""
    cl = read_compatibility_level(qe)
    names = read_function_names(qe)
    for attr, val in (("compatibility_level", cl), ("udf_names", names)):
        try:
            setattr(model, attr, val)
        except Exception:  # noqa: BLE001 - a frozen test double
            pass
    return {"compatibility_level": cl, "udf_names": names}
