"""Client for daxlib.org — DAX UDF package catalog.

daxlib.org hosts community-contributed reusable DAX UDF packages.
Tabular Editor 3.24+ has built-in support; this module mirrors that
pattern from Python so the agent can browse + install packages
without leaving the MCP loop.

Endpoints (best-effort, defensive — daxlib's API surface is young):
  GET https://daxlib.org/api/packages           -> list catalog
  GET https://daxlib.org/api/packages/{id}      -> full package definition

If the endpoints move or change shape, the client returns a structured
error_type='daxlib_api_changed' so callers can degrade gracefully.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

_BASE_URL = "https://daxlib.org"
_TIMEOUT_S = 15
# C-38: the catalog was re-downloaded on every search; cache it briefly.
_CATALOG_TTL_S = 600.0
_catalog_cache: Dict[str, Any] = {"at": 0.0, "body": None}
_catalog_lock = threading.Lock()


def _catalog_body() -> Dict[str, Any]:
    now = time.monotonic()
    with _catalog_lock:
        if _catalog_cache["body"] is not None and now - _catalog_cache["at"] < _CATALOG_TTL_S:
            return {"_body": _catalog_cache["body"], "_cached": True}
    res = _http_get(f"{_BASE_URL}/api/packages")
    if "_body" in res:
        with _catalog_lock:
            _catalog_cache["body"] = res["_body"]
            _catalog_cache["at"] = time.monotonic()
    return res


def clear_catalog_cache() -> None:
    with _catalog_lock:
        _catalog_cache["body"] = None
        _catalog_cache["at"] = 0.0


def _http_get(url: str) -> Dict[str, Any]:
    """Issue a GET, return parsed JSON or a structured error."""
    try:
        import requests
    except Exception as e:
        return {"_http_error": f"requests unavailable: {e}"}
    try:
        resp = requests.get(url, timeout=_TIMEOUT_S, headers={"Accept": "application/json"})
        resp.raise_for_status()
    except Exception as e:
        return {"_http_error": f"GET {url} failed: {e}"}
    try:
        body = resp.json() if resp.content else None
    except Exception as e:
        return {
            "_http_error": f"could not parse JSON from {url}: {e}",
            "_body_excerpt": resp.text[:500],
        }
    if body is None:
        return {"_http_error": f"empty body from {url}"}
    return {"_body": body}


def _normalize_package_summary(pkg: Any) -> Optional[Dict[str, Any]]:
    """daxlib's catalog response shape isn't fully stable; map best-effort."""
    if not isinstance(pkg, dict):
        return None
    out = {
        "id": pkg.get("id") or pkg.get("Id") or pkg.get("name") or pkg.get("Name"),
        "name": pkg.get("name") or pkg.get("Name") or pkg.get("title") or pkg.get("Title"),
        "version": pkg.get("version") or pkg.get("Version"),
        "description": pkg.get("description") or pkg.get("Description") or "",
        "author": pkg.get("author") or pkg.get("Author") or "",
        "function_count": (
            pkg.get("function_count") or pkg.get("functionCount") or len(pkg.get("functions") or [])
        ),
    }
    if not out["id"] or not out["name"]:
        return None
    return out


def _normalize_function(fn: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(fn, dict):
        return None
    name = fn.get("name") or fn.get("Name")
    expression = fn.get("expression") or fn.get("Expression") or fn.get("body") or fn.get("Body")
    if not name or not expression:
        return None
    params: List[Dict[str, str]] = []
    raw_params = fn.get("parameters") or fn.get("Parameters") or []
    for p in raw_params:
        if not isinstance(p, dict):
            continue
        pname = p.get("name") or p.get("Name")
        if not pname:
            continue
        params.append(
            {
                "name": pname,
                "type": p.get("type") or p.get("Type") or "Variant",
                "description": p.get("description") or p.get("Description") or "",
            }
        )
    return {
        "name": name,
        "expression": expression,
        "description": fn.get("description") or fn.get("Description") or "",
        "parameters": params,
    }


def search_packages(query: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    """List daxlib packages, optionally filtered by `query` (substring match
    on id/name/description)."""
    res = _catalog_body()
    if "_http_error" in res:
        return {
            "success": False,
            "error": res["_http_error"],
            "error_type": "daxlib_api_unreachable",
        }
    body = res["_body"]
    raw_packages = (
        body
        if isinstance(body, list)
        else (body.get("packages") if isinstance(body, dict) else None)
    )
    if not isinstance(raw_packages, list):
        return {
            "success": False,
            "error": "unexpected daxlib catalog shape",
            "error_type": "daxlib_api_changed",
            "body_excerpt": str(body)[:500],
        }
    packages = []
    for raw in raw_packages:
        norm = _normalize_package_summary(raw)
        if norm:
            packages.append(norm)
    if query:
        q = query.lower()
        packages = [
            p
            for p in packages
            if q in (p.get("id") or "").lower()
            or q in (p.get("name") or "").lower()
            or q in (p.get("description") or "").lower()
        ]
    truncated = len(packages) > limit
    return {
        "success": True,
        "packages": packages[:limit],
        "total_matched": len(packages),
        "truncated": truncated,
    }


def fetch_package(package_id: str) -> Dict[str, Any]:
    """Fetch a single package by id, normalize its functions list."""
    if not package_id:
        return {
            "success": False,
            "error": "package_id is required",
            "error_type": "invalid_parameters",
        }
    # C-38: the id is URL-quoted (a "/" or "?" can no longer change the request path).
    res = _http_get(f"{_BASE_URL}/api/packages/{quote(str(package_id), safe='')}")
    if "_http_error" in res:
        return {
            "success": False,
            "error": res["_http_error"],
            "error_type": "daxlib_api_unreachable",
        }
    body = res["_body"]
    if not isinstance(body, dict):
        return {
            "success": False,
            "error": "unexpected package shape",
            "error_type": "daxlib_api_changed",
            "body_excerpt": str(body)[:500],
        }
    raw_functions = body.get("functions") or body.get("Functions") or []
    if not isinstance(raw_functions, list):
        return {
            "success": False,
            "error": "package has no functions[] array",
            "error_type": "daxlib_api_changed",
        }
    functions: List[Dict[str, Any]] = []
    skipped = 0
    for raw in raw_functions:
        norm = _normalize_function(raw)
        if norm:
            functions.append(norm)
        else:
            skipped += 1
    return {
        "success": True,
        "package_id": package_id,
        "version": body.get("version") or body.get("Version"),
        "name": body.get("name") or body.get("Name") or package_id,
        "functions": functions,
        "skipped_malformed": skipped,
    }
