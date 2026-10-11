"""Role + OLS impersonation: open a temp ADOMD connection with Roles=...
[and optional EffectiveUserName=...] and run a DAX query through it.

Returns rows the impersonated identity would see plus any OLS errors that
fired (objects the role can't access surface as DAX errors).

NOTE — Power BI Desktop limitation: the local AS instance does NOT honor
`EffectiveUserName` and rejects it with "name is not a properly formed
account name". On PBI Desktop you can only impersonate via `Roles=` —
USERPRINCIPALNAME() then returns the live Windows user. To test RLS as a
specific UPN, the model must be hosted on the Power BI Service or SSAS
Tabular (which this MCP doesn't target — local-only). On PBI Desktop,
omit `effective_user_name` to avoid the connection-open error.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.infrastructure.adomd_utils import engine_message

logger = logging.getLogger(__name__)


def _load_adomd():
    try:
        from core.infrastructure.dll_paths import load_adomd_assembly

        if not load_adomd_assembly():
            return None, "ADOMD.NET assembly load failed"
        from Microsoft.AnalysisServices.AdomdClient import AdomdConnection  # type: ignore

        return AdomdConnection, None
    except Exception as e:
        return None, f"ADOMD.NET unavailable: {e}"


class ImpersonationArgError(ValueError):
    """Role / UPN / CustomData value that cannot be put into a connection string."""


_NEEDS_QUOTING = set(";\"'=")


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _quote_conn_value(value: str) -> str:
    """OLE DB connection-string quoting (B-14).

    Values containing ``;``, quotes, ``=`` or edge whitespace are wrapped in
    double quotes with embedded double quotes doubled, so they can neither end
    the property early nor inject another key (``Data Source=...``).
    """
    if value and not (_NEEDS_QUOTING & set(value)) and value.strip() == value:
        return value
    return '"' + value.replace('"', '""') + '"'


def _validate_roles(roles: List[str]) -> List[str]:
    clean: List[str] = []
    for role in roles:
        name = str(role)
        if not name.strip():
            continue
        if _has_control_chars(name):
            raise ImpersonationArgError(f"Role name {name!r} contains control characters")
        if "," in name:
            # The Roles property is a comma-separated list; a comma inside one
            # name would silently split it into two roles.
            raise ImpersonationArgError(
                f"Role name {name!r} contains a comma, which the Roles= list cannot express"
            )
        clean.append(name)
    return clean


def _validate_upn(upn: str) -> str:
    if _has_control_chars(upn) or any(ch in upn for ch in ";\"',="):
        raise ImpersonationArgError(
            f"effective_user_name {upn!r} contains characters not valid in a UPN"
        )
    return upn


def _build_impersonation_conn_str(
    base_conn_str: str,
    roles: List[str],
    effective_user_name: Optional[str] = None,
    custom_data: Optional[str] = None,
) -> str:
    """Append Roles=... [;EffectiveUserName=...][;CustomData=...] safely (B-14).

    Raises :class:`ImpersonationArgError` for values that cannot be expressed.
    """
    parts = [base_conn_str.rstrip(";")]
    clean_roles = _validate_roles(list(roles or []))
    if clean_roles:
        parts.append("Roles=" + _quote_conn_value(",".join(clean_roles)))
    if effective_user_name:
        parts.append("EffectiveUserName=" + _validate_upn(str(effective_user_name)))
    if custom_data:
        value = str(custom_data)
        if _has_control_chars(value):
            raise ImpersonationArgError("custom_data contains control characters")
        parts.append("CustomData=" + _quote_conn_value(value))
    return ";".join(parts)


def run_as_role(
    dax_query: str,
    roles: List[str],
    effective_user_name: Optional[str] = None,
    top_n: int = 1000,
    custom_data: Optional[str] = None,
) -> Dict[str, Any]:
    """Open a temporary ADOMD connection impersonating `roles` (and optional
    `effective_user_name`) and execute the DAX query.

    Returns:
        {
          success, rows, columns, row_count,
          impersonated: {roles, effective_user_name},
          ols_errors: [...]   # DAX errors that fired during impersonation
        }
    """
    from core.infrastructure.connection_state import connection_state

    if not connection_state.is_connected():
        return {
            "success": False,
            "error": "Not connected to Power BI Desktop",
            "error_type": "not_connected",
        }
    if not roles:
        return {
            "success": False,
            "error": "roles is required (at least one role name)",
            "error_type": "invalid_args",
        }

    cmgr = getattr(connection_state, "connection_manager", None)
    if cmgr is None:
        return {
            "success": False,
            "error": "connection_manager not available",
            "error_type": "not_connected",
        }
    base_conn_str = getattr(cmgr, "connection_string", None)
    if not base_conn_str:
        return {
            "success": False,
            "error": "no base connection string on connection_manager",
            "error_type": "not_connected",
        }

    impersonated = {
        "roles": list(roles),
        "effective_user_name": effective_user_name,
    }
    if custom_data:
        impersonated["custom_data"] = custom_data
    try:
        impersonation_conn_str = _build_impersonation_conn_str(
            base_conn_str, roles, effective_user_name, custom_data
        )
    except ImpersonationArgError as e:
        return {"success": False, "error": str(e), "error_type": "invalid_args"}

    AdomdConnection, err = _load_adomd()
    if AdomdConnection is None:
        return {"success": False, "error": err, "error_type": "adomd_unavailable"}

    from core.infrastructure.adomd_utils import apply_command_timeout, read_rows

    conn = None
    stage = "connect"
    try:
        conn = AdomdConnection(impersonation_conn_str)
        conn.Open()
        stage = "query"
        cmd = conn.CreateCommand()
        cmd.CommandText = dax_query
        apply_command_timeout(cmd, 55)
        reader = cmd.ExecuteReader()
        try:
            # B-06: same locale-neutral conversion as the main executor, so an
            # impersonated result compares equal to the unimpersonated one.
            columns, rows, truncated = read_rows(reader, max(1, int(top_n)))
        finally:
            try:
                reader.Close()
            except Exception:
                pass
        return {
            "success": True,
            "rows": rows,
            "columns": columns,
            "row_count": len(rows),
            "impersonated": impersonated,
            "ols_errors": [],
            "truncated": truncated,
        }
    except Exception as e:
        err_msg = engine_message(e)
        lower = err_msg.lower()
        # PBI Desktop rejects EffectiveUserName — detect and tell the caller
        if effective_user_name and "not a properly formed account" in lower:
            return {
                "success": False,
                "error": (
                    "Power BI Desktop does not support EffectiveUserName. "
                    "Retry without effective_user_name (Roles= alone). To test RLS as "
                    "a specific UPN, host the model on Power BI Service or SSAS Tabular."
                ),
                "error_type": "desktop_no_effective_user",
                "underlying_error": err_msg,
                "impersonated": impersonated,
            }
        # Detect OLS-style errors from message body
        ols = bool(
            "permission" in lower
            or "denied" in lower
            or "object-level security" in lower
            or "OLS" in err_msg
        )
        if ols:
            error_type = "ols_blocked"
        elif stage == "connect":
            error_type = "connection_error"
        else:
            error_type = "query_error"
        out = {
            "success": False,
            "error": err_msg,
            "error_type": error_type,
            "impersonated": impersonated,
        }
        if ols:
            out["ols_errors"] = [err_msg]
        return out
    finally:
        if conn is not None:
            try:
                conn.Close()
            except Exception:
                pass
