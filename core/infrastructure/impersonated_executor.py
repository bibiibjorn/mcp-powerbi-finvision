"""Drop-in executor wrapper that runs each query under a role-impersonated
ADOMD connection.

Used by 09_Debug_Operations sub-handlers (visual, compare, drill, run_dax)
when the caller passes `roles=[...]` and (optionally on Service/SSAS only)
`effective_user_name=...`. Returns the same dict shape as
QueryExecutor.validate_and_execute_dax so existing call sites can swap in
this wrapper without further changes.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class ImpersonatedExecutor:
    """Quacks like QueryExecutor.validate_and_execute_dax for impersonation use.

    Each `validate_and_execute_dax` call opens a fresh ADOMD connection with
    Roles=... [+ EffectiveUserName=...] in the conn string, runs the query,
    and returns the result. No connection is reused across calls — the
    impersonation cost (~10-50ms per call) is acceptable for debug flows
    that issue one or two queries; bulk callers should call run_as_role
    directly.
    """

    def __init__(
        self,
        roles: List[str],
        effective_user_name: Optional[str] = None,
        custom_data: Optional[str] = None,
    ):
        self.roles = list(roles)
        self.effective_user_name = effective_user_name
        self.custom_data = custom_data

    def validate_and_execute_dax(
        self,
        query: str,
        top_n: int = 0,
        bypass_cache: bool = True,
        *,
        max_rows: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Match QueryExecutor signature. bypass_cache is ignored here —
        impersonated calls always bypass the live executor's cache.
        """
        from core.infrastructure.role_impersonation import run_as_role

        start = time.time()
        # run_as_role caps rows via its own top_n. Translate top_n=0 (executor
        # convention for "no cap") to a generous default of 10000.
        effective_top = int(top_n) if top_n and top_n > 0 else 10000
        if max_rows is not None and int(max_rows) > 0:
            effective_top = int(max_rows)
        res = run_as_role(
            dax_query=query,
            roles=self.roles,
            effective_user_name=self.effective_user_name,
            top_n=effective_top,
            **({"custom_data": self.custom_data} if self.custom_data else {}),
        )
        elapsed_ms = round((time.time() - start) * 1000, 2)
        if not res.get("success"):
            return {
                "success": False,
                "error": res.get("error", "impersonated query failed"),
                "error_type": res.get("error_type", "query_error"),
                "execution_time_ms": elapsed_ms,
                "ms": elapsed_ms,
                "impersonated": res.get("impersonated"),
            }
        return {
            "success": True,
            "rows": res.get("rows", []),
            "columns": res.get("columns", []),
            "row_count": res.get("row_count", 0),
            "execution_time_ms": elapsed_ms,
            "ms": elapsed_ms,
            "truncated": res.get("truncated", False),
            "impersonated": res.get("impersonated"),
            "cache": {"hit": False, "age_s": None, "bypassed": True},
        }


def get_executor_for_args(args: Dict[str, Any]):
    """Return the appropriate executor for the given debug-op args.

    If `roles` is present and non-empty, returns an ImpersonatedExecutor.
    Otherwise returns the live connection_state.query_executor.
    """
    from core.infrastructure.connection_state import connection_state

    roles = args.get("roles") or []
    if isinstance(roles, str):
        roles = [r.strip() for r in roles.split(",") if r.strip()]
    if roles:
        return ImpersonatedExecutor(
            roles=list(roles),
            effective_user_name=args.get("effective_user_name"),
            custom_data=args.get("custom_data"),
        )
    return getattr(connection_state, "query_executor", None)
