"""
Connection Manager for PBIXRay MCP Server

Manages Power BI Desktop connections, instance detection, and connection state.

2026-09-22 deep review (slice B):

* B-01 — every command on the shared ``AdomdConnection`` goes through
  :attr:`ConnectionManager.gate` (a :class:`CommandGate`). A connection swap
  takes the same gate so a reconnect waits for (or cancels) in-flight readers.
* B-05 — swap listeners (:meth:`ConnectionManager.add_swap_listener`) let
  ``connection_state`` rebuild every manager bound to the old connection and
  clear the ID maps / result cache on every swap.
* B-12 — :meth:`ConnectionManager.connect_instance` connects to the exact
  instance dict the caller chose (no second detection); instances are ordered
  by msmdsrv start time (newest first), not by random port.
* B-34 — listening ports come from ``psutil.net_connections`` (locale-neutral,
  one syscall); ``netstat -ano`` is only the fallback. The PBIDesktop cmdline
  file is cross-checked against the main window title (``file_path_confidence``).
"""

import datetime as _dt
import logging
import os
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional

from core.infrastructure.adomd_utils import CommandGate, ConnectionBusy, engine_message

logger = logging.getLogger(__name__)

# Try to load ADOMD.NET
ADOMD_AVAILABLE = False
AdomdConnection = None

try:
    from core.infrastructure.dll_paths import load_adomd_assembly

    if load_adomd_assembly():
        from Microsoft.AnalysisServices.AdomdClient import AdomdConnection

        ADOMD_AVAILABLE = True
        logger.info("ADOMD.NET available for connections")
except Exception as e:
    logger.warning(f"ADOMD.NET not available: {e}")


def _get_pbix_info_from_pbi_desktop_proc(parent_proc) -> Dict[str, Any]:
    """Extract PBI file info from an already-identified PBIDesktop.exe process.

    P2-1: shared helper so find_powerbi_instances and connect_to_port parse
    cmdlines with identical logic instead of two independent implementations.

    Returns dict with keys: db_name, file_full_path, pbip_folder_path, file_type.
    All values may be None if the file path cannot be resolved.
    """
    import re

    db_name = file_full_path = pbip_folder_path = file_type = None

    try:
        cmdline = parent_proc.cmdline()
        for arg in cmdline:
            arg_l = arg.lower().rstrip("\"'")
            if arg_l.endswith(".pbip") or arg_l.endswith(".pbix"):
                file_full_path = arg
                db_name = os.path.basename(arg)
                if arg_l.endswith(".pbip"):
                    file_type = "pbip"
                    from core.utilities.pbip_utils import resolve_report_folder_from_pbip

                    resolved = resolve_report_folder_from_pbip(arg)
                    pbip_folder_path = resolved if resolved else os.path.dirname(arg)
                else:
                    file_type = "pbix"
                break

        if db_name is None:
            cmdline_str = " ".join(cmdline)
            match = re.search(r'([^\s"]+\.pbi[xp])', cmdline_str, re.IGNORECASE)
            if match:
                file_full_path = match.group(1)
                db_name = os.path.basename(file_full_path)
                if file_full_path.lower().rstrip("\"'").endswith(".pbip"):
                    file_type = "pbip"
                    from core.utilities.pbip_utils import resolve_report_folder_from_pbip

                    resolved = resolve_report_folder_from_pbip(file_full_path)
                    pbip_folder_path = resolved if resolved else os.path.dirname(file_full_path)
                else:
                    file_type = "pbix"
    except Exception as e:
        logger.debug("_get_pbix_info_from_pbi_desktop_proc failed: %s", e)

    return {
        "db_name": db_name,
        "file_full_path": file_full_path,
        "pbip_folder_path": pbip_folder_path,
        "file_type": file_type,
    }


_DESKTOP_TITLE_SUFFIX = " - power bi desktop"


def _window_titles_by_pid(pids) -> Dict[int, List[str]]:
    """Visible top-level window titles per PID (empty when pywin32 is absent)."""
    wanted = {int(p) for p in pids if p}
    titles: Dict[int, List[str]] = {}
    if not wanted:
        return titles
    try:
        import win32gui
        import win32process
    except ImportError:
        return titles

    def _cb(hwnd, _acc):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid in wanted:
                title = win32gui.GetWindowText(hwnd)
                if title:
                    titles.setdefault(found_pid, []).append(title)
        except Exception:  # noqa: BLE001 — window vanished mid-enumeration
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception as e:  # noqa: BLE001
        logger.debug("EnumWindows failed: %s", e)
    return titles


def _file_name_from_title(title: str) -> Optional[str]:
    """'Sales.pbix - Power BI Desktop' / 'Sales - Power BI Desktop' -> 'Sales[.pbix]'."""
    t = (title or "").strip()
    if not t:
        return None
    low = t.lower()
    if low.endswith(_DESKTOP_TITLE_SUFFIX):
        return t[: -len(_DESKTOP_TITLE_SUFFIX)].strip() or None
    if ".pbix" in low or ".pbip" in low:
        return t.split(" - ")[0].strip() or None
    return None


def file_path_confidence(file_full_path: Optional[str], titles: List[str]) -> str:
    """How far the cmdline file can be trusted (B-34).

    After File→Open in the same Desktop window the process cmdline still names
    the FIRST file. ``high`` = the window title agrees; ``low`` = the title names
    a different file (cmdline is stale); ``unverified`` = no title to compare.
    """
    names = [n for n in (_file_name_from_title(t) for t in titles) if n]
    if not names:
        return "unverified"
    if not file_full_path:
        return "low"
    stem = os.path.splitext(os.path.basename(file_full_path))[0].lower()
    for name in names:
        if os.path.splitext(name)[0].lower() == stem:
            return "high"
    return "low"


def _listening_ports_psutil(msmdsrv_pids) -> Optional[Dict[int, int]]:
    """``{port: pid}`` of LISTEN sockets owned by msmdsrv PIDs, or None on failure."""
    try:
        import psutil

        port_pid: Dict[int, int] = {}
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or conn.pid not in msmdsrv_pids:
                continue
            laddr = getattr(conn, "laddr", None)
            port = getattr(laddr, "port", None) if laddr else None
            if port:
                port_pid[int(port)] = int(conn.pid)
        return port_pid
    except Exception as e:  # noqa: BLE001 — AccessDenied / platform quirks
        logger.debug("psutil.net_connections failed, falling back to netstat: %s", e)
        return None


def _listening_ports_netstat() -> Dict[int, int]:
    """Legacy ``netstat -ano`` parser (English ``LISTENING`` token only)."""
    port_pid_map: Dict[int, int] = {}
    result = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL
    )
    if result.returncode != 0:
        logger.error("netstat command failed")
        return port_pid_map
    saw_any_listening_candidate = False
    for line in result.stdout.splitlines():
        if ("[::]:" in line or "127.0.0.1:" in line) and "TCP" in line.upper():
            saw_any_listening_candidate = True
        if "LISTENING" in line and ("[::]" in line or "127.0.0.1" in line):
            parts = line.split()
            # Proto=0, Local=1, Foreign=2, State=3, PID=4 (5 columns minimum)
            if len(parts) < 5 or parts[3] != "LISTENING":
                continue
            try:
                address = parts[1]
                if "]:" in address:
                    port = int(address.split("]:")[1])
                else:
                    port = int(address.split(":")[-1])
                port_pid_map[port] = int(parts[4])
            except (ValueError, IndexError):
                continue
    if not port_pid_map and saw_any_listening_candidate:
        logger.warning(
            "netstat returned listening TCP rows but none matched the English "
            "'LISTENING' state token; non-English Windows locale suspected."
        )
    return port_pid_map


class PowerBIDesktopDetector:
    """Detects running Power BI Desktop instances (msmdsrv.exe listening ports)."""

    @staticmethod
    def find_powerbi_instances() -> List[Dict[str, Any]]:
        """Find running Power BI Desktop instances.

        Returns instance dicts ordered newest-first by msmdsrv start time, so
        index 0 really is the most recently opened model.
        """
        instances: List[Dict[str, Any]] = []
        try:
            import psutil

            msmdsrv: Dict[int, float] = {}
            try:
                for proc in psutil.process_iter(["pid", "name", "create_time"]):
                    name = proc.info.get("name") or ""
                    if name.lower() == "msmdsrv.exe":
                        msmdsrv[proc.info["pid"]] = float(proc.info.get("create_time") or 0.0)
            except Exception as e:
                logger.error(f"psutil enumeration failed: {e}")
                return instances
            if not msmdsrv:
                logger.info("Detected 0 Power BI Desktop instance(s)")
                return instances

            port_pid_map = _listening_ports_psutil(set(msmdsrv))
            if not port_pid_map:
                port_pid_map = {
                    port: pid for port, pid in _listening_ports_netstat().items() if pid in msmdsrv
                }

            pending: List[Dict[str, Any]] = []
            for port, pid in port_pid_map.items():
                db_name = f"Port {port}"
                file_full_path = pbip_folder_path = file_type = None
                ui_pid = pid  # fallback: msmdsrv pid (wrong for UIA, safe for AS queries)
                try:
                    parent = psutil.Process(pid).parent()
                    if parent and "PBIDesktop" in parent.name():
                        ui_pid = parent.pid
                        info = _get_pbix_info_from_pbi_desktop_proc(parent)
                        db_name = info["db_name"] or f"Port {port}"
                        file_full_path = info["file_full_path"]
                        pbip_folder_path = info["pbip_folder_path"]
                        file_type = info["file_type"]
                except Exception as e:
                    logger.error(f"Error getting filename for port {port}: {e}", exc_info=True)

                started = msmdsrv.get(pid) or 0.0
                pending.append(
                    {
                        "port": port,
                        "pid": ui_pid,
                        "msmdsrv_pid": pid,
                        "started_at": (
                            _dt.datetime.fromtimestamp(started).isoformat(timespec="seconds")
                            if started
                            else None
                        ),
                        "_started_ts": started,
                        "database": db_name,
                        "workspace": f"msmdsrv_pid_{pid}",
                        "path": f"localhost:{port}",
                        "connection_string": f"Data Source=localhost:{port}",
                        "display_name": db_name or f"Power BI Desktop (Port {port})",
                        "file_full_path": file_full_path,
                        "pbip_folder_path": pbip_folder_path,
                        "file_type": file_type,
                    }
                )

            titles = _window_titles_by_pid(i["pid"] for i in pending)
            for inst in pending:
                inst_titles = titles.get(inst["pid"], [])
                inst["file_path_confidence"] = file_path_confidence(
                    inst["file_full_path"], inst_titles
                )
                if inst_titles:
                    inst["window_title"] = inst_titles[0]

            # Newest msmdsrv first; port only breaks ties (it is random, not ordered).
            pending.sort(key=lambda x: (x["_started_ts"], x["port"]), reverse=True)
            for inst in pending:
                inst.pop("_started_ts", None)
            instances = pending
            logger.info(f"Detected {len(instances)} Power BI Desktop instance(s)")
        except subprocess.TimeoutExpired:
            logger.error("Detection timed out")
        except Exception as e:
            logger.error(f"Detection error: {e}")
        return instances


SwapListener = Callable[[Optional[str], Optional[str], str], None]


class ConnectionManager:
    """Manages the active Power BI Desktop connection (establish, reuse, health, swap)."""

    def __init__(self):
        self.active_connection = None
        self.active_instance = None
        self.connection_string = None
        # Re-entrant: connect() / reconnect() may call other guarded methods
        # (e.g. is_connected) while still holding the lock.
        self._lock = threading.RLock()
        #: B-01: serialises every ADOMD command on ``active_connection``.
        self.gate = CommandGate()
        self._swap_listeners: List[SwapListener] = []

    # -- command gate -----------------------------------------------------
    def command(self, timeout: Optional[float] = None, purpose: str = ""):
        """Context manager serialising one command on the shared connection."""
        return self.gate.hold(timeout=timeout, purpose=purpose)

    def cancel_active_queries(self) -> int:
        """``Cancel()`` every in-flight command on this connection."""
        return self.gate.cancel_all()

    # -- swap -------------------------------------------------------------
    def add_swap_listener(self, listener: SwapListener) -> None:
        """Register ``listener(old_conn_str, new_conn_str, reason)``; idempotent."""
        with self._lock:
            if listener not in self._swap_listeners:
                self._swap_listeners.append(listener)

    def _do_swap(self, new_connection, new_instance, new_connection_string) -> None:
        # P1-8: close the old connection INSIDE the lock so is_connected() /
        # get_connection() never observe a disposed CLR object.
        with self._lock:
            old = self.active_connection
            self.active_connection = new_connection
            self.active_instance = new_instance
            self.connection_string = new_connection_string
            if old is not None and old is not new_connection:
                try:
                    old.Close()
                except Exception:
                    pass

    def _swap_connection(
        self,
        new_connection,
        new_instance: Optional[Dict[str, Any]],
        new_connection_string: Optional[str],
        *,
        reason: str = "connect",
        gate_timeout: Optional[float] = None,
        force: bool = True,
    ) -> None:
        """Atomically replace the active connection, then notify swap listeners.

        Takes the command gate so a reconnect waits for in-flight readers. When
        the gate stays busy, in-flight commands are cancelled; with
        ``force=True`` the swap then proceeds regardless (a connection change
        must never deadlock behind a hung reader — the reader only loses a
        connection that is being replaced anyway).
        """
        old_target = self.connection_string
        wait = 15.0 if gate_timeout is None else gate_timeout
        try:
            with self.gate.hold(timeout=wait, purpose=f"swap:{reason}"):
                self._do_swap(new_connection, new_instance, new_connection_string)
        except ConnectionBusy:
            if not force:
                raise
            self.gate.cancel_all()
            try:
                with self.gate.hold(timeout=10.0, purpose=f"swap:{reason}"):
                    self._do_swap(new_connection, new_instance, new_connection_string)
            except ConnectionBusy:
                logger.warning("Swapping connection while a command is still in flight")
                self._do_swap(new_connection, new_instance, new_connection_string)

        with self._lock:
            listeners = list(self._swap_listeners)
        for listener in listeners:
            try:
                listener(old_target, new_connection_string, reason)
            except Exception as e:  # noqa: BLE001 — a listener must never break a connect
                logger.warning("Connection swap listener failed: %s", e, exc_info=True)

    # -- detection / connect ---------------------------------------------
    def detect_instances(self) -> List[Dict[str, Any]]:
        """Detect available Power BI Desktop instances via PowerBIDesktopDetector."""
        return PowerBIDesktopDetector().find_powerbi_instances()

    def connect_instance(self, instance: Dict[str, Any]) -> Dict[str, Any]:
        """Connect to exactly this instance dict (from :meth:`detect_instances`).

        B-12: the handler picks an instance on ONE detection and passes it here,
        so the model it connects to is the one whose ``pbip_info`` it reports.
        """
        if not ADOMD_AVAILABLE:
            return {
                "success": False,
                "error": "ADOMD.NET not available",
                "error_type": "adomd_unavailable",
            }
        conn_str = (instance or {}).get("connection_string")
        if not conn_str:
            return {
                "success": False,
                "error": "Instance has no connection_string",
                "error_type": "invalid_instance",
            }
        new_connection = None
        try:
            # Open BEFORE swapping so a failed open leaves the current connection intact.
            new_connection = AdomdConnection(conn_str)
            new_connection.Open()
            msmdsrv_pid = instance.get("msmdsrv_pid")
            if msmdsrv_pid:
                try:
                    import psutil

                    if not psutil.pid_exists(int(msmdsrv_pid)):
                        raise RuntimeError(
                            f"msmdsrv PID {msmdsrv_pid} for port {instance.get('port')} "
                            "exited after detection"
                        )
                except ImportError:
                    pass
            self._swap_connection(new_connection, dict(instance), conn_str, reason="connect")
            logger.info(f"Connected to Power BI Desktop on port {instance.get('port')}")
            return {
                "success": True,
                "instance": dict(instance),
                "connection_string": conn_str,
                "message": f"Successfully connected to Power BI Desktop on port {instance.get('port')}",
            }
        except Exception as e:
            if new_connection is not None and self.active_connection is not new_connection:
                try:
                    new_connection.Close()
                except Exception:
                    pass
            error_msg = engine_message(e)
            logger.error(f"Connection error: {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "error_type": "connection_error",
                "suggestions": [
                    "Verify Power BI Desktop is running with model loaded",
                    "Run 01_Connection detect again — the instance may have closed",
                ],
            }

    def connect_xmla(self, workspace: str, model: str) -> Dict[str, Any]:
        """Connect READ-ONLY to a published semantic model over the XMLA endpoint (XM-01).

        Opens the new connection before swapping, so a failed sign-in leaves the
        current (Desktop) connection intact. See ``core/infrastructure/xmla_target``.
        """
        from core.infrastructure import xmla_target

        if not ADOMD_AVAILABLE:
            return {
                "success": False,
                "error": "ADOMD.NET not available",
                "error_type": "adomd_unavailable",
            }
        try:
            conn_str = xmla_target.build_connection_string(workspace, model)
        except ValueError as e:
            return {"success": False, "error": str(e), "error_type": "invalid_parameters"}
        new_connection = None
        try:
            new_connection = xmla_target.open_adomd(conn_str)
        except xmla_target.XmlaAuthError as e:
            return {"success": False, "error": str(e), "error_type": "xmla_auth_unavailable"}
        except Exception as e:  # noqa: BLE001 — CLR interop
            if new_connection is not None:
                try:
                    new_connection.Close()
                except Exception:
                    pass
            msg = engine_message(e)
            hint = (
                "Check in order: the AMO/ADOMD build (.NET Framework), the token's client id "
                "(Analysis Services client, not Azure CLI), then XMLA Read on the capacity and "
                "your workspace role."
            )
            return {
                "success": False,
                "error": msg,
                "error_type": "xmla_connect_failed",
                "suggestion": hint,
            }
        instance = {
            "kind": "xmla",
            "port": None,
            "pid": None,
            "workspace": workspace,
            "database": model,
            "display_name": f"{workspace} / {model} (Service, read-only)",
            "connection_string": conn_str,
            "read_only": True,
        }
        self.add_swap_listener(xmla_target.on_connection_swap)
        self._swap_connection(new_connection, instance, conn_str, reason="connect_xmla")
        info = xmla_target.activate(workspace, model, conn_str)
        return {
            "success": True,
            "instance": instance,
            "xmla_target": info,
            "message": (
                f"Connected read-only to '{model}' in workspace '{workspace}'. Every model write is "
                "refused. Reconnect to Desktop with 01_Connection connect / ensure."
            ),
        }

    def connect(self, model_index: int = 0) -> Dict[str, Any]:
        """Connect to Power BI Desktop instance by index (0 = most recently started)."""
        if not ADOMD_AVAILABLE:
            return {
                "success": False,
                "error": "ADOMD.NET not available",
                "error_type": "adomd_unavailable",
            }
        instances = self.detect_instances()
        if not instances:
            return {
                "success": False,
                "error": "No Power BI Desktop instances detected",
                "error_type": "no_instances",
                "suggestions": [
                    "Ensure Power BI Desktop is running with .pbix file open",
                    "Verify msmdsrv.exe process is running",
                ],
            }
        if model_index < 0 or model_index >= len(instances):
            return {
                "success": False,
                "error": f"Invalid model index {model_index}, only {len(instances)} instance(s) found",
                "error_type": "invalid_index",
                "available_instances": len(instances),
            }
        result = self.connect_instance(instances[model_index])
        if result.get("success"):
            result["model_index"] = model_index
            result["total_instances"] = len(instances)
        return result

    def connect_to_port(self, port: int) -> Dict[str, Any]:
        """Connect directly to a Power BI Desktop instance on a specific port."""
        if not ADOMD_AVAILABLE:
            return {"success": False, "error": "ADOMD.NET not available"}

        conn_str = f"Data Source=localhost:{port}"
        new_connection = None
        try:
            new_connection = AdomdConnection(conn_str)
            new_connection.Open()

            db_name = None
            permission_limited = False
            try:
                import psutil

                msmdsrv_pid = None
                for proc in psutil.process_iter(["pid", "name"]):
                    try:
                        if proc.info["name"] == "msmdsrv.exe":
                            process = psutil.Process(proc.info["pid"])
                            for conn in process.net_connections():
                                if hasattr(conn, "laddr") and conn.laddr.port == port:
                                    msmdsrv_pid = proc.info["pid"]
                                    break
                            if msmdsrv_pid:
                                break
                    except psutil.AccessDenied:
                        permission_limited = True
                        continue
                    except psutil.NoSuchProcess:
                        continue

                if msmdsrv_pid:
                    parent = psutil.Process(msmdsrv_pid).parent()
                    while parent:
                        try:
                            if "PBIDesktop" in parent.name():
                                info = _get_pbix_info_from_pbi_desktop_proc(parent)
                                db_name = info["db_name"]
                                if not db_name:
                                    # Window-title fallback (B-27: also PBIP titles).
                                    for title in _window_titles_by_pid([parent.pid]).get(
                                        parent.pid, []
                                    ):
                                        name = _file_name_from_title(title)
                                        if name:
                                            db_name = name
                                            break
                                break
                            parent = parent.parent()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            break
            except Exception as e:
                logger.debug(f"Could not get .pbix filename from process tree: {e}")

            if not db_name:
                with self.gate.hold(purpose="catalog_name"):
                    cmd = new_connection.CreateCommand()
                    cmd.CommandText = "SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS"
                    reader = cmd.ExecuteReader()
                    try:
                        if reader.Read():
                            db_name = str(reader.GetValue(0))
                    finally:
                        try:
                            reader.Close()
                        except Exception:
                            pass

            instance_info = {
                "port": port,
                "database": db_name,
                "connection_string": conn_str,
                "display_name": f"Power BI Desktop (Port {port})",
            }
            if permission_limited:
                instance_info["_permission_limited"] = True
            self._swap_connection(new_connection, instance_info, conn_str, reason="connect")
            logger.info(f"Connected to Power BI Desktop on port {port}")
            return {
                "success": True,
                "port": port,
                "database_name": db_name,
                "connection_string": conn_str,
                "instance": dict(instance_info),
                "message": f"Successfully connected to port {port}",
            }
        except Exception as e:
            if new_connection is not None and self.active_connection is not new_connection:
                try:
                    new_connection.Close()
                except Exception:
                    pass
            logger.error(f"Failed to connect to port {port}: {e}")
            return {
                "success": False,
                "port": port,
                "error": engine_message(e),
                "error_type": "connection_failed",
            }

    # -- accessors --------------------------------------------------------
    def get_connection(self):
        """Active ADOMD connection or None."""
        with self._lock:
            return self.active_connection

    def get_instance_info(self) -> Optional[Dict[str, Any]]:
        """A COPY of the current instance dict, or None if not connected."""
        with self._lock:
            return dict(self.active_instance) if self.active_instance else None

    def is_connected(self) -> bool:
        """True if there's an open active connection (client-side State only)."""
        with self._lock:
            if not self.active_connection:
                return False
            try:
                return self.active_connection.State.ToString() == "Open"
            except Exception:
                return False

    def disconnect(self):
        """Disconnect from current instance."""
        with self._lock:
            if self.active_connection:
                try:
                    self.active_connection.Close()
                    logger.info("Disconnected from Power BI Desktop")
                except Exception:
                    pass
                finally:
                    old_conn_str = self.connection_string
                    self.active_connection = None
                    self.active_instance = None
                    self.connection_string = None
                    # N-09: dropping the link also leaves the read-only XMLA target (its swap
                    # listener is not called for a plain disconnect).
                    try:
                        from core.infrastructure import xmla_target

                        xmla_target.on_connection_swap(old_conn_str, None, "disconnect")
                    except Exception as e:  # noqa: BLE001
                        logger.debug("xmla target deactivation failed: %s", e)

    def reconnect(
        self,
        max_retries: int = 3,
        retry_delay: float = 0.5,
        *,
        gate_timeout: Optional[float] = None,
        force: bool = True,
        reason: str = "reconnect",
    ) -> Dict[str, Any]:
        """Reconnect to the last connected instance with exponential backoff."""
        import time

        with self._lock:
            conn_str_snapshot = self.connection_string
            saved_instance = dict(self.active_instance) if self.active_instance else None
        if not conn_str_snapshot:
            return {
                "success": False,
                "error": "No previous connection to reconnect to",
                "error_type": "no_previous_connection",
            }
        if not ADOMD_AVAILABLE:
            return {
                "success": False,
                "error": "ADOMD.NET not available",
                "error_type": "adomd_unavailable",
            }

        attempts = max(1, int(max_retries))
        last_error = None
        for attempt in range(attempts):
            new_connection = None
            try:
                if (saved_instance or {}).get("kind") == "xmla":
                    from core.infrastructure import xmla_target

                    new_connection = xmla_target.open_adomd(conn_str_snapshot)
                else:
                    new_connection = AdomdConnection(conn_str_snapshot)
                    new_connection.Open()
                if new_connection.State.ToString() == "Open":
                    self._swap_connection(
                        new_connection,
                        saved_instance,
                        conn_str_snapshot,
                        reason=reason,
                        gate_timeout=gate_timeout,
                        force=force,
                    )
                    logger.info(f"Reconnected successfully on attempt {attempt + 1}")
                    return {
                        "success": True,
                        "reconnected": True,
                        "attempt": attempt + 1,
                        "instance": saved_instance,
                        "message": f"Successfully reconnected on attempt {attempt + 1}",
                    }
            except ConnectionBusy as busy:
                if new_connection is not None:
                    try:
                        new_connection.Close()
                    except Exception:
                        pass
                return {
                    "success": False,
                    "error": str(busy),
                    "error_type": "busy",
                    "retry_after": busy.retry_after,
                }
            except Exception as e:
                last_error = engine_message(e)
                logger.warning(f"Reconnection attempt {attempt + 1} failed: {e}")
                if attempt < attempts - 1 and retry_delay > 0:
                    time.sleep(retry_delay * (2**attempt))

        logger.error(f"Failed to reconnect after {attempts} attempts")
        return {
            "success": False,
            "error": f"Failed to reconnect after {attempts} attempts"
            + (f": {last_error}" if last_error else ""),
            "error_type": "reconnection_failed",
            "attempts": attempts,
        }

    def ensure_connected(self, max_retries: int = 3) -> Dict[str, Any]:
        """Ensure connection is open, attempting reconnection if needed."""
        if self.is_connected():
            return {"connected": True, "reconnected": False}
        if self.connection_string:
            logger.info("Connection lost, attempting to reconnect...")
            result = self.reconnect(max_retries=max_retries)
            if result.get("success"):
                return {"connected": True, "reconnected": True}
            return {
                "connected": False,
                "reconnected": False,
                "error": result.get("error"),
                "error_type": result.get("error_type"),
            }
        return {
            "connected": False,
            "reconnected": False,
            "error": "No active or previous connection",
            "error_type": "no_connection",
        }
