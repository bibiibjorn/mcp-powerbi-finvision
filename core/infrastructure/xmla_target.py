"""Read-only XMLA connection target: point the server at a PUBLISHED model (XM-01).

``01_Connection operation=connect_xmla workspace=<ws> model=<model>`` swaps the
shared connection from a Desktop instance to a Service semantic model over the
XMLA endpoint. The DAX / DMV / TOM-read / trace / BPA / dependency tools then
work unchanged against the published model. Every model write is refused (see
``server/dispatch.py``), with no override: production is changed by deploying
the PBIP, never through this lane (the owner's decision, 2026-09-30).

Authentication (the hard-won recipe, memory ``feedback_xmla_connect_recipe``):

* A headless process cannot run ADOMD's own MSAL flow ("interactive
  authentication is not supported"), so the connection carries an access token.
* **The client id decides everything**: the endpoint accepts tokens minted for
  the Analysis Services client (``7f67af8a-…``) and refuses Azure CLI tokens even
  when audience and scope are right.
* The token comes from, in order: ``MCP_PBI_XMLA_TOKEN`` (a raw bearer token), or
  the persistent MSAL cache the Fabric MCP server maintains
  (``FABRIC_XMLA_TOKEN_CACHE`` or ``~/.fabric-mcp/xmla_token_cache.json``; seed it
  once from a console with ``python -m helpers.clients.xmla_token_cache`` in that
  repo). Requires the ``msal`` package for the cache path.
* ADOMD and AMO each have their OWN ``AccessToken`` class; cross-assigning fails.
  Every AMO ``Server`` opened while this target is active must be passed through
  :func:`prepare_amo_server` before ``Connect``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

CLIENT_ID = os.environ.get("FABRIC_XMLA_CLIENT_ID", "7f67af8a-fedc-4b08-8b4e-37c4d127b6cf")
TENANT_ID = os.environ.get("FABRIC_TENANT_ID", "")
SCOPES = ["https://analysis.windows.net/powerbi/api/.default"]
ENDPOINT = "powerbi://api.powerbi.com/v1.0/myorg/"

_lock = threading.RLock()
_active: Optional[Dict[str, Any]] = None
_token: Optional[Tuple[str, float]] = None  # (token, expires_epoch)


class XmlaAuthError(RuntimeError):
    """No usable access token for the XMLA endpoint."""


class ServiceReadOnly(RuntimeError):
    """A model write was attempted while the read-only XMLA target is active (N-01)."""

    error_type = "service_read_only"


def cache_path() -> Path:
    override = os.environ.get("FABRIC_XMLA_TOKEN_CACHE")
    return Path(override) if override else Path.home() / ".fabric-mcp" / "xmla_token_cache.json"


def build_connection_string(workspace: str, model: str) -> str:
    ws = (workspace or "").strip()
    mdl = (model or "").strip()
    if not ws or not mdl:
        raise ValueError("workspace and model are required")
    for bad in (";", '"', "\n", "\r"):
        if bad in ws or bad in mdl:
            raise ValueError('workspace / model names must not contain ; " or newlines')
    return f'Data Source={ENDPOINT}{ws};Initial Catalog="{mdl}";'


def _load_cache_text(path: Path, cache: Any) -> None:
    """Deserialize the MSAL cache, retrying a few times: the Fabric MCP server shares the file and
    a writer that is mid-way (non-atomic) can show a reader a truncated document (N-32)."""
    last: Optional[Exception] = None
    for attempt in range(4):
        try:
            cache.deserialize(path.read_text(encoding="utf-8"))
            return
        except Exception as e:  # noqa: BLE001 - msal raises generic errors on a torn file
            last = e
            time.sleep(0.1 * (attempt + 1))
    raise XmlaAuthError(f"The token cache at {path} could not be read ({last}); re-seed it.")


def _write_cache_atomic(path: Path, text: str) -> None:
    """Replace the cache file atomically so a concurrent reader never sees a partial write."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _pick_account(accounts: list, tenant: str) -> Any:
    """The cached account that belongs to ``tenant`` (home_account_id ``<oid>.<tid>`` / realm);
    the first one when the tenant is unknown or nothing matches."""
    if tenant:
        t = tenant.lower()
        for acct in accounts:
            hid = str(acct.get("home_account_id") or "").lower()
            if str(acct.get("realm") or "").lower() == t or hid.endswith("." + t):
                return acct
    return accounts[0]


def _token_from_cache() -> Tuple[str, float]:
    try:
        import msal  # type: ignore
    except ImportError as e:  # pragma: no cover - environment
        raise XmlaAuthError(
            "The msal package is not installed in this server's venv "
            "(venv/Scripts/pip install msal), or set MCP_PBI_XMLA_TOKEN."
        ) from e
    path = cache_path()
    if not path.exists():
        raise XmlaAuthError(
            f"No XMLA token cache at {path}. Seed it once from a console: in the Fabric MCP "
            "repo run `python -m helpers.clients.xmla_token_cache`, or set MCP_PBI_XMLA_TOKEN."
        )
    cache = msal.SerializableTokenCache()
    _load_cache_text(path, cache)
    tenant = TENANT_ID or _tenant_from_cache(cache)
    authority = f"https://login.microsoftonline.com/{tenant or 'organizations'}"
    app = msal.PublicClientApplication(CLIENT_ID, authority=authority, token_cache=cache)
    accounts = app.get_accounts()
    if not accounts:
        raise XmlaAuthError(f"The token cache at {path} holds no signed-in account; re-seed it.")
    result = app.acquire_token_silent(SCOPES, account=_pick_account(accounts, tenant))
    if cache.has_state_changed:
        try:
            _write_cache_atomic(path, cache.serialize())
        except OSError as e:  # pragma: no cover - best effort
            logger.debug("could not persist refreshed XMLA token cache: %s", e)
    if not result or "access_token" not in result:
        detail = (result or {}).get("error_description") or "silent acquisition failed"
        raise XmlaAuthError(f"Could not refresh the XMLA token: {detail}. Re-seed the cache.")
    return result["access_token"], time.time() + float(result.get("expires_in", 3000))


def _jwt_expiry(token: str) -> Optional[float]:
    """The ``exp`` claim of a JWT, or None when the token is opaque / unparsable."""
    try:
        import base64
        import json

        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload.encode("ascii"))).get("exp")
        return float(exp) if exp is not None else None
    except Exception:  # noqa: BLE001 - opaque tokens are fine
        return None


def _tenant_from_cache(cache: Any) -> str:
    try:
        for acct in cache.find(cache.CredentialType.ACCOUNT):
            realm = acct.get("realm")
            if realm and realm not in ("organizations", "common"):
                return realm
    except Exception:  # pragma: no cover - msal internals vary
        pass
    return ""


def get_token(*, force_refresh: bool = False) -> Tuple[str, float]:
    """(access_token, expires_epoch); cached until 5 minutes before expiry.

    ``MCP_PBI_XMLA_TOKEN`` wins while it is usable; an env token whose JWT ``exp`` has passed (or
    any ``force_refresh``) falls through to the MSAL cache instead of shadowing it (N-32).
    """
    global _token
    with _lock:
        env = os.environ.get("MCP_PBI_XMLA_TOKEN")
        env_error: Optional[str] = None
        if env:
            env = env.strip()
            exp = _jwt_expiry(env)
            if not force_refresh and (exp is None or exp - time.time() > 60):
                return env, exp if exp is not None else time.time() + 3000
            env_error = (
                "MCP_PBI_XMLA_TOKEN has expired"
                if not force_refresh
                else "MCP_PBI_XMLA_TOKEN was refused"
            )
        if not force_refresh and not env and _token and _token[1] - time.time() > 300:
            return _token
        try:
            _token = _token_from_cache()
        except XmlaAuthError as e:
            if env_error:
                raise XmlaAuthError(f"{env_error} and the token cache is unusable: {e}") from e
            raise
        return _token


def _adomd_token(token: str, expires: float):
    from System import DateTimeOffset  # type: ignore
    from Microsoft.AnalysisServices.AdomdClient import AccessToken  # type: ignore

    return AccessToken(token, DateTimeOffset.FromUnixTimeSeconds(int(expires)), "")


def _amo_token(token: str, expires: float):
    from System import DateTimeOffset  # type: ignore
    from Microsoft.AnalysisServices import AccessToken  # type: ignore

    return AccessToken(token, DateTimeOffset.FromUnixTimeSeconds(int(expires)), "")


def open_adomd(connection_string: str):
    """Open an ADOMD connection to the XMLA endpoint with the current token."""
    from Microsoft.AnalysisServices.AdomdClient import AdomdConnection  # type: ignore

    token, expires = get_token()
    conn = AdomdConnection(connection_string)
    conn.AccessToken = _adomd_token(token, expires)
    conn.Open()
    return conn


def is_active() -> bool:
    with _lock:
        return _active is not None


def active() -> Optional[Dict[str, Any]]:
    return dict(_active) if _active else None


def activate(workspace: str, model: str, connection_string: str) -> Dict[str, Any]:
    global _active
    with _lock:
        _active = {
            "kind": "xmla",
            "workspace": workspace,
            "model": model,
            "connection_string": connection_string,
            "read_only": True,
            "connected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        return dict(_active)


def deactivate() -> None:
    global _active
    with _lock:
        _active = None


def on_connection_swap(
    old_conn_str: Optional[str], new_conn_str: Optional[str], reason: str
) -> None:
    """Swap listener: leaving the XMLA target (e.g. back to Desktop) clears it."""
    if _active and (not new_conn_str or ENDPOINT not in new_conn_str):
        deactivate()


def prepare_amo_server(server: Any) -> Any:
    """Give an AMO ``Server`` the token when the XMLA target is active (no-op otherwise)."""
    if _active is None or server is None:
        return server
    try:
        token, expires = get_token()
        server.AccessToken = _amo_token(token, expires)
    except Exception as e:  # surfaced by the Connect that follows
        logger.warning("Could not set the AMO access token for XMLA: %s", e)
    return server


def trace_options() -> Dict[str, Any]:
    """``TraceSession`` kwargs for the current target (remote + token on XMLA)."""
    if _active is None:
        return {"local": True}
    token, expires = get_token()
    return {"local": False, "access_token": token, "access_token_expires": expires}


def read_only_message(what: str) -> str:
    """The refusal text shared by the dispatcher gate and the write-lane guard."""
    target = _active or {}
    return (
        f"{what} would WRITE to the published model '{target.get('model')}' in workspace "
        f"'{target.get('workspace')}'. The XMLA target is read-only."
    ).strip()


def refuse_write(
    tool: str, operation: Optional[str], args: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Error envelope for any write while the published-model target is active (no override)."""
    if _active is None:
        return None
    return {
        "success": False,
        "error_type": "service_read_only",
        "error": (
            f"{tool} {operation or ''} would WRITE to the published model "
            f"'{_active['model']}' in workspace '{_active['workspace']}'. The XMLA target is "
            "read-only."
        ).strip(),
        "suggestion": (
            "Author the change on the PBIP (Desktop, or offline with 01_Connection "
            "connect_folder) and deploy it; 01_Connection ensure returns to Desktop."
        ),
        "xmla_target": active(),
    }
