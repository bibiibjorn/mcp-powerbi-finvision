"""
Multi-Instance Connection Manager

Manages multiple simultaneous connections to Power BI Desktop instances
for model comparison and cross-instance analysis.
"""

import logging
import threading
from typing import Dict, Any, Optional, List
from core.infrastructure.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)


class MultiInstanceManager:
    """
    Manages multiple connections to Power BI Desktop instances. Thread-safe.

    Enables simultaneous connections to different Power BI Desktop instances
    for model comparison, cross-instance queries, and parallel analysis.
    """

    MAX_INSTANCES = 10  # Prevent unbounded instance accumulation

    def __init__(self):
        """Initialize multi-instance manager."""
        self.instances: Dict[int, ConnectionManager] = {}
        self.active_ports: List[int] = []
        self._lock = threading.RLock()
        #: B-30: every connect_to_instance() takes a reference; the connection is
        #: only closed when the last holder calls disconnect_instance(), so a
        #: concurrent second `compare` no longer loses its connection to the
        #: first call's `finally`.
        self._refcounts: Dict[int, int] = {}
        self._databases: Dict[int, Optional[str]] = {}
        self._connecting: Dict[int, threading.Event] = {}

    def detect_instances(self) -> List[Dict[str, Any]]:
        """Detect available Power BI Desktop instances via a temporary ConnectionManager."""
        try:
            temp_conn = ConnectionManager()
            instances = temp_conn.detect_instances()
            logger.info(f"Detected {len(instances)} Power BI Desktop instance(s)")
            return instances
        except Exception as e:
            logger.error(f"Error detecting Power BI instances: {e}", exc_info=True)
            return []

    def connect_to_instance(self, port: int) -> Dict[str, Any]:
        """Connect to (or take another reference on) the instance on ``port``.

        Reuse is health-checked (a dead cached connection is reconnected) and
        always reports ``database_name``. The slow ADOMD open/psutil scan runs
        OUTSIDE the lock; concurrent first connects to the same port wait for
        the one in flight instead of opening two connections.
        """
        if not isinstance(port, int) or port < 1 or port > 65535:
            return {
                "success": False,
                "port": port,
                "error": f"Invalid port number: {port}. Must be between 1 and 65535.",
            }

        while True:
            with self._lock:
                existing = self.instances.get(port)
                pending = self._connecting.get(port)
                if existing is None and pending is None:
                    if len(self.instances) >= self.MAX_INSTANCES:
                        return {
                            "success": False,
                            "port": port,
                            "error": f"Maximum number of instances ({self.MAX_INSTANCES}) reached. Disconnect an instance first.",
                        }
                    pending = threading.Event()
                    self._connecting[port] = pending
                    break
            if existing is not None:
                return self._reuse(port, existing)
            pending.wait(timeout=60)

        try:
            logger.info(f"Connecting to Power BI Desktop instance on port {port}")
            conn_manager = ConnectionManager()
            result = conn_manager.connect_to_port(port)
            if not result.get("success"):
                return {
                    "success": False,
                    "port": port,
                    "error": result.get("error", "Connection failed"),
                }
            with self._lock:
                self.instances[port] = conn_manager
                self._refcounts[port] = self._refcounts.get(port, 0) + 1
                self._databases[port] = result.get("database_name")
                if port not in self.active_ports:
                    self.active_ports.append(port)
            logger.info(f"Successfully connected to instance on port {port}")
            return {
                "success": True,
                "port": port,
                "status": "connected",
                "database_name": result.get("database_name"),
                "instance_info": dict(result.get("instance") or {}),
            }
        except Exception as e:
            logger.error(f"Failed to connect to port {port}: {e}", exc_info=True)
            return {"success": False, "port": port, "error": str(e)}
        finally:
            with self._lock:
                event = self._connecting.pop(port, None)
            if event is not None:
                event.set()

    def _reuse(self, port: int, conn_manager: ConnectionManager) -> Dict[str, Any]:
        """Health-check a cached connection and take a reference on it."""
        healthy = False
        try:
            healthy = bool(conn_manager.is_connected())
        except Exception:  # noqa: BLE001
            healthy = False
        if not healthy:
            recon = conn_manager.reconnect(max_retries=2, retry_delay=0.25)
            if not recon.get("success"):
                return {
                    "success": False,
                    "port": port,
                    "error": f"Cached connection to port {port} is dead and reconnect failed: "
                    f"{recon.get('error')}",
                    "error_type": "connection_error",
                }
        with self._lock:
            if self.instances.get(port) is not conn_manager:
                # Disconnected by another thread meanwhile; start over.
                return self.connect_to_instance(port)
            self._refcounts[port] = self._refcounts.get(port, 0) + 1
            database_name = self._databases.get(port)
        info = conn_manager.get_instance_info() or {}
        logger.info(f"Reusing connection to instance on port {port}")
        return {
            "success": True,
            "port": port,
            "status": "already_connected",
            "reconnected": not healthy,
            "database_name": database_name or info.get("database"),
            "instance_info": dict(info),
        }

    def disconnect_instance(self, port: int) -> Dict[str, Any]:
        """Release one reference; the connection closes when the last holder releases."""
        with self._lock:
            if port not in self.instances:
                return {"success": False, "port": port, "error": "Instance not connected"}
            remaining = self._refcounts.get(port, 1) - 1
            if remaining > 0:
                self._refcounts[port] = remaining
                return {
                    "success": True,
                    "port": port,
                    "status": "released",
                    "remaining_references": remaining,
                }
            conn_manager = self.instances.pop(port)
            self._refcounts.pop(port, None)
            self._databases.pop(port, None)
            if port in self.active_ports:
                self.active_ports.remove(port)

        try:
            logger.info(f"Disconnecting from instance on port {port}")
            conn_manager.disconnect()
            return {"success": True, "port": port, "status": "disconnected"}
        except Exception as e:
            logger.error(f"Error disconnecting from port {port}: {e}")
            return {"success": False, "port": port, "error": str(e)}

    def get_instance(self, port: int) -> Optional[ConnectionManager]:
        """Return ConnectionManager for port, or None if not connected."""
        with self._lock:
            return self.instances.get(port)

    def get_all_instances(self) -> List[Dict[str, Any]]:
        """Return COPIES of the info dicts of all connected instances (B-30)."""
        with self._lock:
            instances_snapshot = list(self.instances.items())
            refs = dict(self._refcounts)

        instances_info = []
        for port, conn_manager in instances_snapshot:
            try:
                info = dict(conn_manager.get_instance_info() or {})
                if info:
                    info["port"] = port
                    info["connected"] = conn_manager.is_connected()
                    info["references"] = refs.get(port, 0)
                    instances_info.append(info)
            except Exception as e:
                logger.warning(f"Error getting info for port {port}: {e}")

        return instances_info

    def disconnect_all(self) -> Dict[str, Any]:
        """Force-disconnect every instance (all references); returns a summary."""
        with self._lock:
            ports_to_disconnect = list(self.active_ports)
            for port in ports_to_disconnect:
                self._refcounts[port] = 1
        disconnected = []
        failed = []

        for port in ports_to_disconnect:
            result = self.disconnect_instance(port)
            if result.get("success"):
                disconnected.append(port)
            else:
                failed.append({"port": port, "error": result.get("error")})

        return {
            "success": len(failed) == 0,
            "disconnected_count": len(disconnected),
            "failed_count": len(failed),
            "disconnected_ports": disconnected,
            "failed_ports": failed,
        }

    def get_status(self) -> Dict[str, Any]:
        """Return total_instances, active_ports, and instances info."""
        with self._lock:
            total = len(self.instances)
            ports = list(self.active_ports)
        return {
            "total_instances": total,
            "active_ports": ports,
            "instances": self.get_all_instances(),
        }

    def close(self) -> None:
        """Explicit cleanup - preferred over __del__."""
        try:
            self.disconnect_all()
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")


# P1-13: previously defined __del__ that touched self._lock and self.logger
# during interpreter shutdown. Either attribute can already be None by the
# time gc runs the finalizer, causing AttributeError or deadlock. Removed
# __del__ in favor of an atexit hook that runs while the interpreter is
# still healthy.
import atexit as _atexit  # noqa: E402

# Global multi-instance manager
multi_instance_manager = MultiInstanceManager()


def _shutdown_multi_instance_manager() -> None:
    try:
        multi_instance_manager.close()
    except Exception:
        # Interpreter shutdown — best effort, silent.
        pass


_atexit.register(_shutdown_multi_instance_manager)
