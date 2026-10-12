"""Shared AMO/TOM helpers for CRUD managers.

Centralizes the server/db/model acquisition pattern so every CRUD manager
doesn't re-implement the same ~30 lines of boilerplate.
"""

import logging
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from core.infrastructure.dll_paths import load_amo_assemblies, load_adomd_assembly

    if load_amo_assemblies():
        from Microsoft.AnalysisServices.Tabular import Server as AMOServer
    else:
        AMOServer = None
    if load_adomd_assembly():
        from Microsoft.AnalysisServices.AdomdClient import AdomdCommand
    else:
        AdomdCommand = None
except Exception as e:
    logger.debug(f"AMO/ADOMD assemblies not loaded: {e}")
    AMOServer = None
    AdomdCommand = None


#: (connection string, connection generation) -> database name. The catalog of a given
#: msmdsrv endpoint cannot change without a reconnect (new connection string and / or
#: a bumped connection generation), so the ``DBSCHEMA_CATALOGS`` round trip that every
#: model op used to pay is made once per connection (P-03).
_DB_NAME_CACHE: Dict[Tuple[str, int], str] = {}
_DB_NAME_LOCK = threading.Lock()
_DB_NAME_CACHE_MAX = 16


def _db_name_key(conn, connection_state) -> Optional[Tuple[str, int]]:
    conn_str = getattr(conn, "ConnectionString", None)
    if not isinstance(conn_str, str) or not conn_str:
        return None
    gen = getattr(connection_state, "connection_generation", 0)
    return (conn_str, gen if isinstance(gen, int) else 0)


def invalidate_db_name_cache() -> None:
    with _DB_NAME_LOCK:
        _DB_NAME_CACHE.clear()


def get_server_db_model(
    connection_state, _fresh: bool = False
) -> Tuple[Any, Any, Any, Optional[str]]:
    """Acquire an AMO server + database + model from the active connection.

    Returns (server, db, model, error). On error, (None, None, None, message).
    Caller is responsible for server.Disconnect() in a finally block.
    """
    if AMOServer is None:
        return None, None, None, "AMO assemblies not available"
    from core.infrastructure import offline_model

    if offline_model.is_active():
        # OFF-01: the in-memory database deserialised from the TMDL folder.
        server, db, model = offline_model.server_db_model()
        return server, db, model, None if model is not None else "offline model unavailable"
    cm = connection_state.connection_manager
    if cm is None:
        return None, None, None, "Not connected"
    try:
        conn = cm.get_connection()
    except Exception as e:
        return None, None, None, f"Failed to get ADOMD connection: {e}"
    if conn is None:
        return None, None, None, "ADOMD connection is None"
    server = AMOServer()
    from core.infrastructure.xmla_target import prepare_amo_server

    prepare_amo_server(server)
    try:
        server.Connect(conn.ConnectionString)
        name_key = _db_name_key(conn, connection_state)
        with _DB_NAME_LOCK:
            db_name = (
                _DB_NAME_CACHE.get(name_key) if (name_key is not None and not _fresh) else None
            )
        used_cached_name = db_name is not None
        # Reader is a CLR COM object — guarantee Close() on every exit
        # path so a Read()/GetValue() throw doesn't leak a native handle
        # back into the connection.
        reader = None
        try:
            if db_name is None and AdomdCommand is not None:
                # B-01 (WP1): serialised on the shared connection's command gate.
                with connection_state.command_gate(purpose="catalog_name"):
                    try:
                        cmd = AdomdCommand(
                            "SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS", conn
                        )
                        reader = cmd.ExecuteReader()
                        if reader.Read():
                            db_name = str(reader.GetValue(0))
                    finally:
                        if reader is not None:
                            try:
                                reader.Close()
                            except Exception:
                                pass
        except Exception:
            db_name = None
        if not db_name and server.Databases.Count > 0:
            db_name = server.Databases[0].Name
        if not db_name:
            try:
                server.Disconnect()
            except Exception:
                pass
            return None, None, None, "Could not determine database name"
        try:
            db = server.Databases.GetByName(db_name)
        except Exception:
            if not used_cached_name:
                raise
            # the remembered name no longer resolves: forget it and look it up again
            with _DB_NAME_LOCK:
                _DB_NAME_CACHE.pop(name_key, None)
            try:
                server.Disconnect()
            except Exception:
                pass
            return get_server_db_model(connection_state, _fresh=True)
        if name_key is not None:
            with _DB_NAME_LOCK:
                if len(_DB_NAME_CACHE) >= _DB_NAME_CACHE_MAX:
                    _DB_NAME_CACHE.clear()
                _DB_NAME_CACHE[name_key] = db_name
        return server, db, db.Model, None
    except Exception as e:
        try:
            server.Disconnect()
        except Exception:
            pass
        return None, None, None, f"AMO connect failed: {e}"


def safe_disconnect(server) -> None:
    """Disconnect an AMO server silently."""
    try:
        server.Disconnect()
    except Exception:
        pass
