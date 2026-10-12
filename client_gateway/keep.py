"""Keep the gateway running for a Power BI MCP that masks without a Claude Code chat (VS Code Copilot).

Masking on demand stops the gateway once no folder is routed to it (``client_gateway.reaper``). A
client without a Claude Code session (VS Code GitHub Copilot, any MCP host without the hooks) has
no folder to route: its ``masking on`` (``00_Client_Mode on``) starts the gateway and leaves a
keep file ``<home>/mcp_keep/<pid>.json`` with the MCP's process id and creation time. The reaper
keeps the gateway while any keep file names a process that is still the same one (a reused process
id is another process); the MCP removes its file when it exits, a stale file is removed by the
next check. Counts only, never a value. Never raises.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

KEEP_DIR = "mcp_keep"


def _create_time(pid: int) -> Optional[float]:
    try:
        import psutil  # noqa: PLC0415

        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 - no such process, access denied, psutil missing
        return None


def _keep_dir(home: Path) -> Path:
    return Path(home) / KEEP_DIR


def register(home: Path, pid: Optional[int] = None) -> bool:
    """Write this process's keep file. False when it could not be written."""
    pid = os.getpid() if pid is None else int(pid)
    created = _create_time(pid)
    try:
        folder = _keep_dir(home)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{pid}.json.tmp"
        tmp.write_text(json.dumps({"pid": pid, "created": created}), encoding="utf-8")
        os.replace(tmp, folder / f"{pid}.json")
        return True
    except OSError:
        return False


def unregister(home: Path, pid: Optional[int] = None) -> None:
    pid = os.getpid() if pid is None else int(pid)
    try:
        (_keep_dir(home) / f"{pid}.json").unlink()
    except OSError:
        pass


def registered(home: Path, pid: Optional[int] = None) -> bool:
    pid = os.getpid() if pid is None else int(pid)
    return (_keep_dir(home) / f"{pid}.json").is_file()


def any_alive(home: Path) -> bool:
    """True while a keep file names a live process (the same one that wrote it). Stale files are
    removed. Unsure (an unreadable folder): True, the gateway keeps running."""
    folder = _keep_dir(home)
    try:
        files = list(folder.glob("*.json"))
    except OSError:
        return True
    alive = False
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = int(data["pid"])
            created = data.get("created")
        except (OSError, ValueError, KeyError, TypeError):
            continue  # being written, or not ours: leave it
        now = _create_time(pid)
        same = now is not None and (
            not isinstance(created, (int, float)) or abs(now - float(created)) < 1.0
        )
        if same:
            alive = True
        else:
            try:
                path.unlink()
            except OSError:
                pass
    return alive
