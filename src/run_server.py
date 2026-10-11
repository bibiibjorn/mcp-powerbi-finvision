#!/usr/bin/env python3
"""
Wrapper script to run MCP-PowerBi-Finvision server with correct PYTHONPATH.
This ensures bundled dependencies in venv/Lib/site-packages are found.

K-12: the venv's native wheels (pydantic_core, orjson, pywin32, clr_loader …)
are built for ONE Python minor version. When this script is started by a
different interpreter (e.g. the ``py -3`` launcher defaulting to a newer
Python), it re-launches itself with the venv's own python.exe instead of
failing on the first native import.
"""

import os
import subprocess
import sys
import site
import importlib.util
from pathlib import Path

# Get the repository root (parent of src/ where this script is located)
script_dir = Path(__file__).parent.parent.absolute()

_REEXEC_MARKER = "MCP_PBI_RUN_SERVER_REEXEC"


def _venv_python() -> Path:
    win = script_dir / "venv" / "Scripts" / "python.exe"
    return win if win.exists() else script_dir / "venv" / "bin" / "python"


def _venv_version() -> "tuple[int, int] | None":
    """(major, minor) recorded in venv/pyvenv.cfg, or None."""
    cfg = script_dir / "venv" / "pyvenv.cfg"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip().lower() in ("version", "version_info"):
                parts = value.strip().split(".")
                return int(parts[0]), int(parts[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _maybe_reexec() -> None:
    wanted = _venv_version()
    if wanted is None or wanted == sys.version_info[:2] or os.environ.get(_REEXEC_MARKER):
        return
    venv_py = _venv_python()
    if not venv_py.exists():
        print(
            f"WARNING: venv was built for Python {wanted[0]}.{wanted[1]} but this is "
            f"{sys.version_info[0]}.{sys.version_info[1]}; native modules may fail to import.",
            file=sys.stderr,
        )
        return
    env = dict(os.environ, **{_REEXEC_MARKER: "1"})
    # Child inherits stdin/stdout, so the MCP stdio stream passes straight through.
    sys.exit(
        subprocess.call([str(venv_py), str(Path(__file__).absolute()), *sys.argv[1:]], env=env)
    )


if __name__ == "__main__":
    _maybe_reexec()

# Add bundled dependencies to Python path using site.addsitedir
# This properly handles .pth files and native extensions (.pyd files)
# Support both Windows and Unix venv layouts
site_packages_win = script_dir / "venv" / "Lib" / "site-packages"
site_packages_unix = script_dir / "venv" / "lib"

if site_packages_win.exists():
    site.addsitedir(str(site_packages_win))
elif site_packages_unix.exists():
    for child in site_packages_unix.iterdir():
        sp = child / "site-packages"
        if sp.exists():
            site.addsitedir(str(sp))
            break

# Add parent directory to path so imports work
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

# Vendored pbi-perf-core: fallback when the venv's editable install points elsewhere
# (e.g. a .mcpb bundle built on another machine).
perf_core_dir = script_dir / "pbi-perf-core"
if perf_core_dir.exists() and str(perf_core_dir) not in sys.path:
    sys.path.append(str(perf_core_dir))

# Import and run the server module using importlib (safe alternative to exec())
server_module_path = script_dir / "src" / "pbixray_server_enhanced.py"

if not server_module_path.exists():
    print(f"ERROR: Server module not found: {server_module_path}", file=sys.stderr)
    sys.exit(1)

spec = importlib.util.spec_from_file_location("__main__", str(server_module_path))
module = importlib.util.module_from_spec(spec)
sys.modules["pbixray_server_enhanced"] = module
spec.loader.exec_module(module)
