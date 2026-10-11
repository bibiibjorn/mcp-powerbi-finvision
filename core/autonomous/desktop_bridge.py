"""
Power BI Desktop Bridge — subprocess wrapper around the Microsoft
``powerbi-desktop`` CLI (``@microsoft/powerbi-desktop-bridge-cli``).

The CLI speaks the undocumented named-pipe RPC that Power BI Desktop exposes
when the "external tool access through secure local APIs" preview feature is on.
We run it as ``node <cli script>`` (never through the npm ``.cmd`` shim, review
J-06) and parse the JSON it returns. The script comes from the installed
package's ``package.json`` ``bin`` entry: ``dist/index.js`` up to CLI 0.1.x,
``dist/cli.js`` from 0.2.0 on (there ``dist/index.js`` is a silent library entry
that prints nothing).

Safety model (review J-01 / J-02 — the user's #1 rule):

* ``reload`` re-reads the PBIP/PBIR definition from disk and DISCARDS unsaved
  in-memory edits. It therefore REFUSES (``error_type: unsaved_changes`` /
  ``dirty_state_unknown``) whenever the target Desktop may hold unsaved work:
  Desktop's own ``hasUnsavedChanges`` flag (always re-read right before the
  reload, explicit pid included — no cached status), a non-``connected``
  instance, or a live-model (TOM) write this server made that no verified save
  has flushed (``core.autonomous.edit_ledger``). The caller must either save
  first (``save_first=true`` / ``operation=save``) — only valid BEFORE disk
  edits — or explicitly pass ``discard_unsaved=true``.
* ``save`` flushes Desktop's memory to disk and would overwrite disk edits this
  server made since the last reload, so it REFUSES with
  ``conflict_pending_disk_and_memory`` while such edits are pending (override:
  ``overwrite_disk_edits=true``). Rule: save BEFORE disk edits; after disk edits
  reload, never save first.

``reload`` semantics: it asks Desktop to re-read the currently open PBIP/PBIR
definition from disk. The CLI's ``reload`` command sends ``reloadModelDefinition:
false`` (verified on 0.1.1 and 1.0.0), so a plain reload does NOT apply TMDL/model
edits. ``reload include_model=true`` (BR-04) runs the CLI *library* through
``tools/bridge_lib.mjs`` and sends ``true`` — after the same unsaved-work gate and a
change classifier (``model_change_classifier``) that refuses anything but
metadata-only differences (partition / M / expression / data-source / import
table or column changes: close Desktop WITHOUT saving and reopen, ``op_open``).
Other model edits go through live TOM (``02_Model_Operations``). The same helper
serves ``screenshot per_visual`` (``report.snapshot.capture/v2`` regions, only when
the live manifest lists it). Use ``manifest`` to probe what the connected build exposes.

Requires Power BI Desktop **2.155.756.0** (June 2026) or newer — older builds
have no bridge endpoint at all and surface as ``not_connected``.

Two envelope shapes:
- ``run_bridge`` returns the low-level ``{"ok": bool, ...}`` shape.
- ``op_*`` return the MCP handler shape ``{"success": bool, ...}``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.autonomous import edit_ledger

logger = logging.getLogger(__name__)

CLI_NAME = "powerbi-desktop"
CLI_PACKAGE = "@microsoft/powerbi-desktop-bridge-cli"
DEFAULT_TIMEOUT = 90.0

# Status pre-flight wait (seconds) when the caller passes no wait_seconds.
DEFAULT_STATUS_WAIT = 30.0

# The Desktop Bridge RPC endpoint ships from the June 2026 build. Older Desktop
# versions have no bridge at all, which otherwise surfaces as a bare
# `not_connected` and reads like a preview-feature/pipe problem.
MIN_DESKTOP_VERSION = "2.155.756.0"
MIN_DESKTOP_HINT = (
    f"The bridge requires Power BI Desktop {MIN_DESKTOP_VERSION} (June 2026) or "
    "newer with the 'external tool access through secure local APIs' preview "
    "feature enabled (on by default from that build)."
)

# `--scale` (screenshot / screenshot-all) multiplies the NATIVE capture
# resolution; the CLI accepts 1..3 and defaults to 2.
SCALE_MIN = 1.0
SCALE_MAX = 3.0

# Instance-selection status results are cached briefly for READ ops
# (screenshot / resolve_page): every op needs a pre-flight `status`, and each
# CLI invocation pays a full Node startup (~1s+). reload/save NEVER use it —
# they always re-read the instance right before acting (J-01).
STATUS_CACHE_TTL = 5.0

# The bridge manifest describes the RPC surface of a *process* — it cannot change
# while that process lives, so it caches far longer than status.
MANIFEST_CACHE_TTL = STATUS_CACHE_TTL * 12  # 60s

# Kept for backward compatibility (was the post-reload page-list retry sleep).
# The CLI builds the page list from pages.json ON DISK (`readPages` in the CLI
# bundle), so a reload never changes it; a missing page is re-checked once
# with a fresh status and no sleep (J-21).
SETTLE_RETRY_SECONDS = 0.0

# reload_and_screenshot: default render settle after the reload (the CLI only
# sleeps 500 ms). Captures right after a reload are often half-rendered (J-21).
DEFAULT_RELOAD_SETTLE_MS = 2000

# render_stable: capture until two consecutive captures hash identically.
RENDER_STABLE_MAX_CAPTURES = 4
RENDER_STABLE_INTERVAL_S = 1.0

# Minimum budget (seconds) left before a reload may START (J-13): starting a
# reload the dispatcher has already given up on would fire it after the caller
# was told the call failed.
MIN_RELOAD_BUDGET_S = 8.0

# Characters cmd.exe interprets even inside quotes (BatBadBut, J-06). Only
# relevant when we are forced to run the npm .cmd shim.
_CMD_UNSAFE_CHARS = frozenset('"%&|<>^!\r\n\x00')
_ARG_FORBIDDEN_CHARS = frozenset("\r\n\x00")

# Bridge CLI 0.2.0 moved the CLI to dist/cli.js; from then on dist/index.js is the
# typed library entry (no commander, prints nothing, exits 0).
_CLI_LIBRARY_ENTRY_SINCE = (0, 2, 0)
# Highest CLI major version this wrapper was verified against (1.0.0).
MAX_TESTED_CLI_MAJOR = 1
# The .js npm's shim runs: "%dp0%\node_modules\@microsoft\powerbi-desktop-bridge-cli\dist\cli.js"
_SHIM_TARGET_RE = re.compile(r"powerbi-desktop-bridge-cli[\\/]+([^\"'\s%]+?\.[cm]?js)\b", re.I)

# The CLI cancels concurrent calls against one PID — serialize per PID. RLock
# so a composite (reload_and_screenshot) can hold it across both steps (J-14).
_PID_LOCKS: Dict[int, threading.RLock] = {}
_PID_LOCKS_GUARD = threading.Lock()


def _pid_lock(pid: int) -> threading.RLock:
    with _PID_LOCKS_GUARD:
        lock = _PID_LOCKS.get(int(pid))
        if lock is None:
            lock = threading.RLock()
            _PID_LOCKS[int(pid)] = lock
        return lock


# Default PBIR page canvas (report units) when page.json omits width/height.
_DEFAULT_PAGE_W = 1280.0
_DEFAULT_PAGE_H = 720.0


# ---------------------------------------------------------------------------
# CLI resolution (J-06) + low-level subprocess runner
# ---------------------------------------------------------------------------
_CLI_CACHE: Dict[str, Any] = {"key": None, "value": None}
_CLI_CACHE_LOCK = threading.Lock()


def _parse_version(text: Any) -> Optional[Tuple[int, int, int]]:
    m = re.match(r"\s*v?(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _read_package(pkg_dir: str) -> Dict[str, Any]:
    """The installed CLI's package.json: ``{exists, name, version, bin, mtime}``. Never raises."""
    info: Dict[str, Any] = {
        "exists": False,
        "name": None,
        "version": None,
        "bin": None,
        "mtime": None,
    }
    path = os.path.join(pkg_dir, "package.json")
    try:
        info["mtime"] = os.stat(path).st_mtime_ns
        info["exists"] = True
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return info
    if isinstance(doc, dict):
        if isinstance(doc.get("name"), str):
            info["name"] = doc["name"]
        if isinstance(doc.get("version"), str):
            info["version"] = doc["version"]
        b = doc.get("bin")
        if isinstance(b, dict):
            b = b.get(CLI_NAME)
        if isinstance(b, str) and b.strip():
            info["bin"] = b.strip()
    return info


def _within(base: str, path: str) -> bool:
    base, path = os.path.abspath(base), os.path.abspath(path)
    try:
        return os.path.commonpath([base, path]) == base
    except ValueError:  # different drives
        return False


def _version_near_script(script: str) -> Optional[str]:
    """Version of the CLI package that owns ``script`` (override path), if it is one."""
    d = os.path.dirname(os.path.abspath(script))
    for _ in range(3):
        pkg = _read_package(d)
        if pkg["name"] == CLI_PACKAGE:
            return pkg["version"]
        d = os.path.dirname(d)
    return None


def _resolve_shim_script(exe: str, pkg_dir: str, pkg: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Find the CLI script behind the npm shim ``exe`` -> ``(script | None, resolved_via)``.

    Order: package.json ``bin`` (what npm itself runs), the ``.js`` target named
    in the shim text, ``dist/cli.js``, then ``dist/index.js`` — the last only
    when it cannot be the silent library entry (CLI < 0.2.0, or no package.json
    at all). The shim-text hit is skipped when it is that library entry. Every
    candidate must be a file inside the package dir.
    """
    ver = _parse_version(pkg["version"])
    library_index = ver is not None and ver >= _CLI_LIBRARY_ENTRY_SINCE
    index_js = os.path.normpath(os.path.join(pkg_dir, "dist", "index.js"))

    def _usable(p: str) -> bool:
        return os.path.isfile(p) and _within(pkg_dir, p)

    if pkg["bin"]:
        cand = os.path.normpath(os.path.join(pkg_dir, pkg["bin"]))
        if _usable(cand):
            return cand, "package_bin"
    try:
        shim_text = Path(exe).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        shim_text = ""
    m = _SHIM_TARGET_RE.search(shim_text)
    if m:
        cand = os.path.normpath(os.path.join(pkg_dir, *re.split(r"[\\/]+", m.group(1))))
        is_library = library_index and os.path.normcase(cand) == os.path.normcase(index_js)
        if _usable(cand) and not is_library:
            return cand, "shim_text"
    cli_js = os.path.join(pkg_dir, "dist", "cli.js")
    if _usable(cli_js):
        return cli_js, "dist_cli"
    if _usable(index_js) and ((ver is not None and not library_index) or not pkg["exists"]):
        return index_js, "dist_index_legacy"
    return None, "shim"


def _resolve_cli() -> Dict[str, Any]:
    """How to run the CLI: ``{"argv0": [...], "mode": "node"|"exe"|"shim"}`` or an error.

    On Windows ``shutil.which`` finds the npm ``powerbi-desktop.CMD`` shim.
    Executing a ``.cmd`` goes through ``cmd.exe`` and Python's ``list2cmdline``
    quoting is NOT cmd-safe: ``"`` breaks out of the quotes and ``%VAR%`` is
    expanded even inside them (probe_cmd_injection: an ``output`` containing
    ``" & echo X & "`` ran ``echo X``). So we resolve the shim to
    ``node.exe <the package's bin script>`` (``dist/index.js`` up to CLI 0.1.x,
    ``dist/cli.js`` from 0.2.0; see ``_resolve_shim_script``) and exec that
    directly (``shell=False``, no cmd.exe). The script can be pinned with
    ``MCP_PBI_BRIDGE_CLI_JS``. Falls back to the shim (``mode: shim``) only when
    the script cannot be found — ``run_bridge`` then refuses any argument
    containing a cmd metacharacter.

    The value also carries ``version`` (the installed package's, or None),
    ``script`` and ``resolved_via``. The cache key includes the package.json
    mtime, so an in-session ``npm i -g`` upgrade is picked up.
    """
    exe = shutil.which(CLI_NAME)
    override = os.environ.get("MCP_PBI_BRIDGE_CLI_JS")
    pkg_dir = (
        os.path.join(os.path.dirname(exe), "node_modules", *CLI_PACKAGE.split("/")) if exe else ""
    )
    pkg = _read_package(pkg_dir) if exe else None
    key = (exe, override, pkg["mtime"] if pkg else None)
    with _CLI_CACHE_LOCK:
        if _CLI_CACHE["key"] == key and _CLI_CACHE["value"] is not None:
            return _CLI_CACHE["value"]

    def _node_for(prefix: Optional[str]) -> Optional[str]:
        if prefix:
            cand = os.path.join(prefix, "node.exe" if os.name == "nt" else "node")
            if os.path.isfile(cand):
                return cand
        return shutil.which("node")

    value: Dict[str, Any]
    if override:
        script = os.path.abspath(override)
        node = _node_for(os.path.dirname(exe) if exe else None)
        if os.path.isfile(script) and node:
            value = {
                "argv0": [node, script],
                "mode": "node",
                "script": script,
                "version": _version_near_script(script),
                "resolved_via": "env_override",
            }
        else:
            value = {
                "error_type": "cli_not_installed",
                "error": (
                    f"MCP_PBI_BRIDGE_CLI_JS={override!r} is not a file or node is not on PATH."
                ),
            }
    elif not exe:
        value = {
            "error_type": "cli_not_installed",
            "error": (
                f"`{CLI_NAME}` not found on PATH. Install with: " f"npm install -g {CLI_PACKAGE}"
            ),
        }
    elif exe.lower().endswith((".cmd", ".bat", ".ps1")):
        prefix = os.path.dirname(exe)
        script, via = _resolve_shim_script(exe, pkg_dir, pkg)
        node = _node_for(prefix)
        if script and node:
            value = {
                "argv0": [node, script],
                "mode": "node",
                "script": script,
                "version": pkg["version"],
                "resolved_via": via,
            }
        else:
            value = {
                "argv0": [exe],
                "mode": "shim",
                "script": None,
                "version": pkg["version"],
                "resolved_via": "shim",
            }
    else:
        value = {
            "argv0": [exe],
            "mode": "exe",
            "script": None,
            "version": None,
            "resolved_via": "exe",
        }
    with _CLI_CACHE_LOCK:
        _CLI_CACHE["key"] = key
        _CLI_CACHE["value"] = value
    return value


def reset_cli_cache() -> None:
    """Forget the resolved CLI command (tests / after an npm upgrade)."""
    with _CLI_CACHE_LOCK:
        _CLI_CACHE["key"] = None
        _CLI_CACHE["value"] = None


def _validate_argv(argv: List[str], mode: str) -> Optional[str]:
    for a in argv:
        s = str(a)
        if any(ch in _ARG_FORBIDDEN_CHARS for ch in s):
            return f"argument {s!r} contains a newline / NUL"
        if mode == "shim" and any(ch in _CMD_UNSAFE_CHARS for ch in s):
            return (
                f"argument {s!r} contains a cmd.exe metacharacter "
                '(one of " % & | < > ^ !) and the CLI could only be reached through '
                "the npm .cmd shim, which cmd.exe would interpret. Set "
                "MCP_PBI_BRIDGE_CLI_JS to the CLI script (the package's `bin` entry, "
                "dist/cli.js from CLI 0.2.0), or use a plain path."
            )
    return None


def run_bridge(argv: List[str], *, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Run ``powerbi-desktop <argv...>`` and return a normalized envelope.

    Returns ``{"ok": True, "data": <parsed json>}`` on success, or
    ``{"ok": False, "error_type": ..., "error": ..., ...}`` on any failure.
    """
    cli = _resolve_cli()
    if "argv0" not in cli:
        return {"ok": False, "error_type": cli["error_type"], "error": cli["error"]}
    bad = _validate_argv(argv, cli["mode"])
    if bad:
        return {"ok": False, "error_type": "unsafe_argument", "error": bad}

    cmd = [*cli["argv0"], *[str(a) for a in argv]]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            shell=False,
            stdin=subprocess.DEVNULL,  # never inherit the MCP server's stdio pipe
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_type": "timeout",
            "error": f"`{CLI_NAME} {argv[0] if argv else ''}` exceeded {timeout}s",
        }
    except Exception as e:  # pragma: no cover - defensive
        return {"ok": False, "error_type": "subprocess_error", "error": str(e)}

    stdout = (proc.stdout or "").strip()
    try:
        data = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error_type": "bad_output",
            "error": f"`{CLI_NAME}` returned non-JSON output",
            "raw_stdout": stdout,
            "raw_stderr": (proc.stderr or "").strip(),
            "returncode": proc.returncode,
        }

    # A non-zero exit is a failure even when stdout parsed as JSON (the CLI
    # emits JSON error envelopes) or was empty — otherwise CLI errors
    # masquerade as success / "not connected" downstream.
    if proc.returncode != 0:
        return {
            "ok": False,
            "error_type": "cli_error",
            "error": f"`{CLI_NAME} {argv[0] if argv else ''}` exited {proc.returncode}",
            "data": data,
            "raw_stderr": (proc.stderr or "").strip(),
            "returncode": proc.returncode,
        }

    # Every CLI command prints JSON. Exit 0 with NOTHING on stdout means we ran
    # something that is not the CLI — e.g. the silent library entry
    # (dist/index.js of CLI >= 0.2.0) — and would otherwise read as "no instances".
    if not stdout:
        return {
            "ok": False,
            "error_type": "cli_no_output",
            "error": f"`{CLI_NAME} {argv[0] if argv else ''}` exited 0 with no output",
            "hint": (
                f"The resolved script ({cli.get('script') or 'the npm shim'}, CLI version "
                f"{cli.get('version') or 'unknown'}) printed nothing; it may be a library entry "
                "instead of the CLI (bridge CLI >= 0.2.0 moved the CLI to dist/cli.js). Point "
                f"MCP_PBI_BRIDGE_CLI_JS at the package's dist/cli.js or reinstall {CLI_PACKAGE}."
            ),
            "bridge_cli_script": cli.get("script"),
            "bridge_cli_version": cli.get("version"),
            "raw_stderr": (proc.stderr or "").strip(),
            "returncode": proc.returncode,
        }

    return {"ok": True, "data": data}


# ---------------------------------------------------------------------------
# Bridge CLI *library* helper (tools/bridge_lib.mjs) — what the CLI cannot do:
# a model-definition reload (BR-04) and region captures (BR-05).
# ---------------------------------------------------------------------------
BRIDGE_LIB_HELPER = Path(__file__).resolve().parents[2] / "tools" / "bridge_lib.mjs"
# The library entry (openSession / reloadModelDefinition) is verified on 1.0.0.
_LIB_MIN_VERSION = (1, 0, 0)


def _bridge_lib_target() -> Dict[str, Any]:
    """Where the helper runs: ``{node, package_dir, version}`` or an error dict."""
    cli = _resolve_cli()
    if "argv0" not in cli:
        return {"error_type": cli["error_type"], "error": cli["error"]}
    if cli.get("mode") != "node" or not cli.get("script"):
        return {
            "error_type": "bridge_lib_unavailable",
            "error": (
                "The bridge CLI library helper needs the CLI resolved to `node <script>` "
                f"(got mode={cli.get('mode')!r}). Install {CLI_PACKAGE} >= 1.0.0 globally or "
                "set MCP_PBI_BRIDGE_CLI_JS to its dist/cli.js."
            ),
        }
    ver = _parse_version(cli.get("version"))
    if ver is None or ver < _LIB_MIN_VERSION:
        return {
            "error_type": "bridge_lib_unavailable",
            "error": (
                f"The bridge CLI library helper needs {CLI_PACKAGE} >= 1.0.0 (installed: "
                f"{cli.get('version') or 'unknown'}). Upgrade with: npm install -g {CLI_PACKAGE}"
            ),
            "bridge_cli_version": cli.get("version"),
        }
    pkg_dir = os.path.dirname(os.path.dirname(cli["script"]))
    if not os.path.isfile(os.path.join(pkg_dir, "dist", "index.js")):
        return {
            "error_type": "bridge_lib_unavailable",
            "error": f"{pkg_dir} has no dist/index.js (the library entry).",
        }
    if not BRIDGE_LIB_HELPER.is_file():
        return {
            "error_type": "bridge_lib_unavailable",
            "error": f"helper script missing: {BRIDGE_LIB_HELPER}",
        }
    return {"node": cli["argv0"][0], "package_dir": pkg_dir, "version": cli.get("version")}


def _exec_bridge_lib(command: str, args: List[str], *, timeout: float) -> Dict[str, Any]:
    """Run ``node tools/bridge_lib.mjs <command> --package-dir D <args...>`` (shell=False).

    The helper prints ONE JSON envelope and exits 0 for every handled outcome; a
    Desktop error path has returned exit 0 with an error envelope, so the
    envelope is parsed and the exit code IGNORED. Same return shape as
    :func:`run_bridge` (``ok`` False + ``data`` = the error envelope, so
    :func:`_cli_err` handles both); a ``partial`` / ``unsupported`` envelope is
    ``ok`` True — the caller inspects ``data["status"]``.
    """
    tgt = _bridge_lib_target()
    if "node" not in tgt:
        return {"ok": False, **{k: v for k, v in tgt.items()}}
    argv = [
        tgt["node"],
        str(BRIDGE_LIB_HELPER),
        command,
        "--package-dir",
        tgt["package_dir"],
        *[str(a) for a in args],
    ]
    bad = _validate_argv(argv[1:], "node")
    if bad:
        return {"ok": False, "error_type": "unsafe_argument", "error": bad}
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        if command == "reload-model":
            # N-30: the helper was killed, not Desktop - the model reload may already be applied
            return {
                "ok": False,
                "error_type": "timeout_outcome_unknown",
                "error": f"bridge library helper `{command}` exceeded {timeout}s; Desktop may "
                "already have applied the model reload - verify before retrying",
            }
        return {
            "ok": False,
            "error_type": "timeout",
            "error": f"bridge library helper `{command}` exceeded {timeout}s",
        }
    except Exception as e:  # pragma: no cover - defensive
        return {"ok": False, "error_type": "subprocess_error", "error": str(e)}
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if not stdout:
        return {
            "ok": False,
            "error_type": "cli_no_output",
            "error": f"bridge library helper `{command}` printed nothing (exit {proc.returncode})",
            "raw_stderr": stderr,
            "returncode": proc.returncode,
        }
    try:
        data = json.loads(stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {
            "ok": False,
            "error_type": "bad_output",
            "error": f"bridge library helper `{command}` returned non-JSON output",
            "raw_stdout": stdout,
            "raw_stderr": stderr,
            "returncode": proc.returncode,
        }
    if not isinstance(data, dict):
        return {
            "ok": False,
            "error_type": "bad_output",
            "error": f"bridge library helper `{command}` returned a non-object envelope",
            "raw_stdout": stdout,
        }
    if data.get("status") == "error":
        return {
            "ok": False,
            "error_type": "cli_error",
            "error": f"bridge library helper `{command}` reported an error",
            "data": data,
            "raw_stderr": stderr,
            "returncode": proc.returncode,
        }
    return {"ok": True, "data": data}


def run_bridge_lib(
    command: str, args: List[str], *, timeout: float = DEFAULT_TIMEOUT
) -> Dict[str, Any]:
    """Public entry for the library helper (tests replace this one; see ``_exec_bridge_lib``)."""
    return _exec_bridge_lib(command, args, timeout=timeout)


# ---------------------------------------------------------------------------
# Instance / page selection
# ---------------------------------------------------------------------------
class BridgeError(Exception):
    """Base for bridge selection errors; carries an error_type for envelopes.

    ``extra`` holds structured context (e.g. candidate instances) merged into
    the error envelope so an agent can self-correct without another round-trip.
    """

    error_type = "bridge_error"

    def __init__(self, message: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.extra: Dict[str, Any] = extra or {}


class BridgeNotConnected(BridgeError):
    """No usable bridge instance.

    Always carries ``min_desktop_version`` + a hint in ``extra``: the most common
    cause of a bare "not connected" is a pre-June-2026 Desktop that has no bridge
    endpoint at all, which is indistinguishable from a pipe/preview-feature
    problem without the version hint.
    """

    error_type = "not_connected"

    def __init__(self, message: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        merged: Dict[str, Any] = {
            "min_desktop_version": MIN_DESKTOP_VERSION,
            "min_desktop_hint": MIN_DESKTOP_HINT,
        }
        merged.update(extra or {})
        super().__init__(f"{message} {MIN_DESKTOP_HINT}", extra=merged)


class BridgeAmbiguous(BridgeError):
    error_type = "ambiguous_instance"


class BridgePathMismatch(BridgeError):
    """``pbip_path`` was given but no running instance has that project open (J-15)."""

    error_type = "instance_path_mismatch"


def _norm(p: Optional[str]) -> str:
    return edit_ledger.norm_path(p)


def _instance_keys(inst: Dict[str, Any]) -> Tuple[set, set, set]:
    """(files, folders, artifact dirs) an instance's open project answers to."""
    cur = inst.get("currentFilePath") or ""
    files, folders, arts = set(), set(), set()
    if cur:
        n = _norm(cur)
        files.add(n)
        folders.add(os.path.dirname(n))
        stem, _ext = os.path.splitext(os.path.basename(n))
        arts.add(os.path.join(os.path.dirname(n), stem + ".report"))
        reports, models = edit_ledger.project_artifacts(cur)
        arts |= reports | models
    if inst.get("reportDir"):
        arts.add(_norm(inst["reportDir"]))
    return files, folders, arts


def _instance_matches_path(inst: Dict[str, Any], pbip_path: str) -> bool:
    """Does *inst* have the project *pbip_path* names open?

    Accepts the ``.pbip`` file, its folder, its ``.Report`` folder or its bound
    ``.SemanticModel`` folder — normalised (absolute, long path, case-folded).
    """
    target = _norm(pbip_path)
    if not target:
        return False
    files, folders, arts = _instance_keys(inst)
    if target in files or target in folders or target in arts:
        return True
    # A path INSIDE the open report / its model (definition/, a visual.json ...).
    # A different .pbip never matches through a shared model: two reports bound
    # to one model are different projects.
    art = edit_ledger.artifact_of(pbip_path)
    return bool(art and art in arts and not target.endswith(".pbip"))


def select_instance(
    status: Dict[str, Any], *, pid: Optional[int], pbip_path: Optional[str]
) -> Dict[str, Any]:
    """Pick one Desktop instance from a ``status`` payload. Never guesses.

    When ``pbip_path`` is given the chosen instance MUST have that project open,
    even when only one Desktop is running (J-15: reloading / saving whatever
    report happens to be open is exactly what the path was meant to prevent).
    """
    instances = status.get("instances") or []
    if not instances:
        raise BridgeNotConnected(
            "No Power BI Desktop bridge instance is connected. Open the PBIP and "
            "enable Desktop preview feature 'external tool access through secure "
            "local APIs'."
        )
    listing = [
        {"pid": i.get("pid"), "currentFilePath": i.get("currentFilePath")} for i in instances
    ]
    if pid is not None:
        for inst in instances:
            if inst.get("pid") == pid:
                if pbip_path and not _instance_matches_path(inst, pbip_path):
                    raise BridgePathMismatch(
                        f"Instance pid={pid} has {inst.get('currentFilePath')!r} open, not "
                        f"{pbip_path!r}.",
                        extra={"instances": listing},
                    )
                return inst
        raise BridgeNotConnected(f"No connected instance with pid={pid}.")
    if pbip_path:
        matches = [i for i in instances if _instance_matches_path(i, pbip_path)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise BridgePathMismatch(
                f"No running Desktop instance has {pbip_path!r} open. Pass the .pbip "
                "file, its folder or its .Report folder of an open project, or `pid`.",
                extra={"instances": listing},
            )
        raise BridgeAmbiguous(
            f"{len(matches)} Desktop instances match {pbip_path!r}. Pass `pid` or the "
            ".pbip file itself.",
            extra={
                "instances": [m for m in listing if any(m["pid"] == i.get("pid") for i in matches)]
            },
        )
    if len(instances) == 1:
        return instances[0]
    raise BridgeAmbiguous(
        "Multiple Desktop instances are open. Pass `pid` or a `pbip_path` that "
        "matches exactly one instance's currentFilePath.",
        extra={"instances": listing},
    )


def resolve_page(instance: Dict[str, Any], page_ref: str) -> Dict[str, Any]:
    """Resolve a page id / display name: ``{page_id, match, ambiguous: [...]}``.

    Exact id, then exact display name, then case-insensitive id / display name.
    Duplicate display names are reported (``ambiguous``), never resolved
    first-wins silently (J-31). ``page_id`` is None when nothing matches or the
    match is ambiguous.
    """
    pages = instance.get("pages") or []
    for pg in pages:
        if pg.get("id") == page_ref:
            return {"page_id": page_ref, "match": "id", "ambiguous": []}
    exact = [pg for pg in pages if pg.get("displayName") == page_ref]
    if len(exact) == 1:
        return {"page_id": exact[0].get("id"), "match": "display_name", "ambiguous": []}
    if len(exact) > 1:
        return {"page_id": None, "match": "ambiguous", "ambiguous": [p.get("id") for p in exact]}
    low = (page_ref or "").casefold()
    ci_id = [pg for pg in pages if str(pg.get("id") or "").casefold() == low]
    if len(ci_id) == 1:
        return {"page_id": ci_id[0].get("id"), "match": "id_case_insensitive", "ambiguous": []}
    ci = [pg for pg in pages if str(pg.get("displayName") or "").casefold() == low]
    if len(ci) == 1:
        return {
            "page_id": ci[0].get("id"),
            "match": "display_name_case_insensitive",
            "ambiguous": [],
        }
    if len(ci) > 1:
        return {"page_id": None, "match": "ambiguous", "ambiguous": [p.get("id") for p in ci]}
    return {"page_id": None, "match": None, "ambiguous": []}


def resolve_page_id(instance: Dict[str, Any], page_ref: str) -> Optional[str]:
    """Map a page display name to its PBIR page id. Pass-through if already an id."""
    return resolve_page(instance, page_ref)["page_id"]


# ---------------------------------------------------------------------------
# Post-capture image processing (resize for Claude's vision tier + per-visual crop)
# ---------------------------------------------------------------------------
def _optimize_output(output: str, vision_tier: str = "high") -> Optional[Dict[str, Any]]:
    """Resize a captured PNG to fit Claude's native image resolution.

    Returns the optimize stats dict, or None if the optimizer is unavailable
    (kept non-fatal so a screenshot never fails because of post-processing).
    """
    try:
        from core.autonomous.image_optimize import optimize_png

        if vision_tier and vision_tier != "high":
            return optimize_png(output, vision_tier=vision_tier)
        return optimize_png(output)
    except Exception as e:  # pragma: no cover - defensive, never fail a capture
        logger.warning("screenshot optimize failed for %s: %s", output, e)
        return None


def _optimize(output: str, vision_tier: str) -> Optional[Dict[str, Any]]:
    # Tests monkeypatch ``_optimize_output`` with a one-argument fake.
    if vision_tier == "high":
        return _optimize_output(output)
    return _optimize_output(output, vision_tier)


def _page_canvas_size(report_root: Path, page_id: str) -> tuple[float, float]:
    """Read a PBIR page's canvas size (report units) from page.json."""
    page_json = report_root / "definition" / "pages" / page_id / "page.json"
    try:
        data = json.loads(page_json.read_text(encoding="utf-8"))
        return (
            float(data.get("width", _DEFAULT_PAGE_W)),
            float(data.get("height", _DEFAULT_PAGE_H)),
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return (_DEFAULT_PAGE_W, _DEFAULT_PAGE_H)


def _abs_position(visual, groups: Dict[str, Any]) -> tuple[float, float]:
    """Resolve a visual's page-absolute (x, y).

    A visual with a ``parentGroupName`` stores GROUP-LOCAL coordinates — the
    page-absolute position is the sum of positions up the parent-group chain.
    ``seen`` guards against pathological parent cycles.
    """
    x, y = visual.position["x"], visual.position["y"]
    gid = visual.parent_group_id
    seen: set = set()
    while gid and gid in groups and gid not in seen:
        seen.add(gid)
        g = groups[gid]
        x += g.position["x"]
        y += g.position["y"]
        gid = g.parent_group_id
    return x, y


def _is_effectively_hidden(visual, groups: Dict[str, Any]) -> bool:
    """True when the visual (or any ancestor group) has ``isHidden``."""
    if visual.is_hidden:
        return True
    gid = visual.parent_group_id
    seen: set = set()
    while gid and gid in groups and gid not in seen:
        seen.add(gid)
        if groups[gid].is_hidden:
            return True
        gid = groups[gid].parent_group_id
    return False


def _page_folder(root: Path, page_id: str) -> Optional[Path]:
    """The page folder of *page_id*: ``pages/<id>`` or the page whose page.json name matches."""
    pages = root / "definition" / "pages"
    direct = pages / page_id
    if (direct / "page.json").is_file() or (direct / "visuals").is_dir():
        return direct
    try:
        for d in pages.iterdir():
            pj = d / "page.json"
            if not pj.is_file():
                continue
            try:
                if json.loads(pj.read_text(encoding="utf-8")).get("name") == page_id:
                    return d
            except (OSError, json.JSONDecodeError):
                continue
    except OSError:
        return None
    return None


def _page_inventory(root: Path, page_id: str) -> Tuple[List[Any], Dict[str, Any], Path]:
    """Visuals + groups of ONE page (J-24: never parse the whole report to crop a page)."""
    from tools.visual_catalog.inventory import _parse_group, _parse_page, _parse_visual

    pdir = _page_folder(root, page_id)
    if pdir is None:
        return [], {}, root / "definition" / "pages" / page_id
    pid, pdisplay = _parse_page(pdir)
    visuals: List[Any] = []
    groups: Dict[str, Any] = {}
    vroot = pdir / "visuals"
    try:
        vdirs = sorted(v for v in vroot.iterdir() if v.is_dir())
    except OSError:
        vdirs = []
    for vdir in vdirs:
        vjson = vdir / "visual.json"
        if not vjson.is_file():
            continue
        inst = _parse_visual(vjson, pid, pdisplay)
        if inst is not None:
            visuals.append(inst)
            continue
        try:
            data = json.loads(vjson.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        grp = _parse_group(data, vdir.name, pid)
        if grp is not None:
            groups[grp.group_id] = grp
    return visuals, groups, pdir


# A v1 page capture is the canvas VIEWPORT of the Desktop window, not the page (live spike
# 2026-09-30: 3437x812 at scale 1 on a wide window, including the filter pane and warning
# banners). A PNG whose aspect ratio is within this tolerance of the page's is taken as
# the page itself (the legacy assumption); anything else is searched for the page.
_PAGE_ASPECT_TOL = 0.02
# Per-channel distance below which a pixel counts as canvas background.
_CANVAS_TOL = 8
# A candidate page rectangle must be at least this fraction non-canvas pixels.
_PAGE_COVER_MIN = 0.90
# Long-axis pixel count of the downsampled image the page search runs on.
_PAGE_SEARCH_PX = 1500


def _canvas_colour(np: Any, a: Any) -> Any:
    """Modal colour of the left edge strip + bottom strip (the canvas margin)."""
    h, w = a.shape[:2]
    strip = np.concatenate(
        [a[:, : max(1, w // 100)].reshape(-1, 3), a[-max(1, h // 50) :, :].reshape(-1, 3)]
    )
    packed = (
        (strip[:, 0].astype(np.int64) << 16) | (strip[:, 1].astype(np.int64) << 8) | strip[:, 2]
    )
    vals, counts = np.unique(packed, return_counts=True)
    top = int(vals[int(counts.argmax())])
    return np.array([(top >> 16) & 255, (top >> 8) & 255, top & 255], dtype=np.int16)


def _detect_page_rect(img: Any, aspect: float) -> Optional[Dict[str, Any]]:
    """Locate the page rectangle inside a viewport PNG, or None.

    The canvas margin colour is sampled from the image itself (it can be a custom
    wallpaper colour); everything that differs from it is "content". The page is the
    LARGEST rectangle with the page's aspect ratio that is >= 90 % content (binary
    search over its height on an integral image). Then the exact extent is refined:
    columns from the content density inside that rectangle, rows from the page height
    implied by the aspect ratio, positioned to avoid full-width bars (warning banners
    are content across the WHOLE width, the page is not). Needs numpy; PIL does the rest.
    """
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with matplotlib (a hard dependency)
        return None
    W, H = img.size
    f = max(1, int(round(max(W, H) / _PAGE_SEARCH_PX)))
    rgb = img.convert("RGB")
    small = rgb.reduce(f) if f > 1 else rgb
    a = np.asarray(small, dtype=np.int16)
    h, w = a.shape[:2]
    if h < 16 or w < 16:
        return None
    canvas = _canvas_colour(np, a)
    mask = np.abs(a - canvas).max(axis=2) > _CANVAS_TOL
    integral = np.zeros((h + 1, w + 1), dtype=np.int64)
    integral[1:, 1:] = mask.cumsum(0).cumsum(1)

    def best_at(hh: int) -> Optional[Tuple[float, float, int, int]]:
        ww = int(round(hh * aspect))
        if ww > w or ww < 8 or hh > h:
            return None
        tot = (
            integral[hh:, ww:]
            - integral[: h - hh + 1, ww:]
            - integral[hh:, : w - ww + 1]
            + integral[: h - hh + 1, : w - ww + 1]
        )
        best = tot.max()
        if best < _PAGE_COVER_MIN * ww * hh:
            return None
        ys, xs = np.nonzero(tot == best)
        return float(xs.mean()), float(ys.mean()), ww, hh

    lo, hi = max(8, h // 8), min(h, int(w / aspect))
    if hi < lo or best_at(lo) is None:
        return None
    found = best_at(lo)
    while lo < hi:  # largest hh that still has a >= 90 % content placement
        mid = (lo + hi + 1) // 2
        cand = best_at(mid)
        if cand is None:
            hi = mid - 1
        else:
            lo, found = mid, cand
    assert found is not None
    x0c, y0c, wwc, hhc = int(round(found[0])), int(round(found[1])), found[2], found[3]

    # Refine. Rows that are content across (almost) the whole width are bars/banners.
    bar = mask.mean(axis=1) >= 0.98
    if bar.mean() >= 0.9:
        bar[:] = False
    win_rows = ~bar[y0c : y0c + hhc]
    sub = mask[y0c : y0c + hhc, x0c : x0c + wwc]
    colfrac = (sub[win_rows] if win_rows.any() else sub).mean(axis=0)
    cols = np.nonzero(colfrac >= 0.5)[0]
    if cols.size == 0:
        return None
    xa, xb = x0c + int(cols[0]), x0c + int(cols[-1]) + 1
    page_w_c = xb - xa
    page_h_c = page_w_c / aspect
    win = max(1, int(round(page_h_c)))
    # Page rows: content across the page's columns and not a full-width bar. The window with
    # the most page rows is the page (sliding it into canvas gap or banner rows loses rows).
    row_ok = ((mask[:, xa:xb].mean(axis=1) >= 0.5) & ~bar).astype(np.int32)
    csum = np.concatenate([[0], np.cumsum(row_ok)])
    last_y = int(math.floor(y0c + hhc - win))
    if last_y < y0c:
        ya = float(y0c)
    else:
        scores = [int(csum[y + win] - csum[y]) for y in range(y0c, last_y + 1)]
        ya = float(y0c + int(np.argmax(scores)))
    x, y = xa * f, ya * f
    pw, ph = page_w_c * f, page_h_c * f
    x, y = max(0.0, x), max(0.0, y)
    pw, ph = min(pw, W - x), min(ph, H - y)
    inside = mask[int(ya) : int(ya + page_h_c), xa:xb]
    return {
        "rect": (x, y, pw, ph),
        "coverage": round(float(inside.mean()) if inside.size else 0.0, 3),
        "downsample": f,
        "canvas_rgb": [int(v) for v in canvas],
    }


def _locate_page_rect(img: Any, page_w: float, page_h: float) -> Dict[str, Any]:
    """Where the page sits inside a v1 capture: ``{method, rect: (x, y, w, h), ...}``.

    ``full_png`` = the PNG aspect matches the page (legacy mapping, unchanged);
    ``detected`` = the page was found inside a wider/taller viewport capture;
    ``assumed_full_png`` = it could NOT be found, the legacy mapping is used anyway and
    the crops may be misaligned (``reason`` says why; the caller surfaces a warning).
    """
    png_w, png_h = img.size
    full = (0.0, 0.0, float(png_w), float(png_h))
    aspect = page_w / page_h if page_h else 0.0
    if aspect <= 0 or png_h <= 0:
        return {"method": "assumed_full_png", "rect": full, "reason": "invalid page size"}
    if abs((png_w / png_h) / aspect - 1.0) <= _PAGE_ASPECT_TOL:
        return {"method": "full_png", "rect": full}
    found = None
    try:
        found = _detect_page_rect(img, aspect)
    except Exception as e:  # noqa: BLE001 - detection must never fail a screenshot
        logger.warning("page rectangle detection failed: %s", e)
    if found is None or found["coverage"] < _PAGE_COVER_MIN - 0.05:
        return {
            "method": "assumed_full_png",
            "rect": full,
            "reason": (
                f"the PNG is {png_w}x{png_h} (aspect {png_w / png_h:.3f}) but the page is "
                f"{page_w:g}x{page_h:g} (aspect {aspect:.3f}) and no page-shaped region of the "
                "canvas could be located"
            ),
        }
    return {"method": "detected", **found}


def _crop_page_visuals(
    *,
    full_page_png: str,
    report_root: str,
    page_id: str,
    crops_dir: str,
    optimize: bool = True,
    vision_tier: str = "high",
    padding: int = 8,
) -> Dict[str, Any]:
    """Split a v1 page capture into one optimized crop per visual on that page.

    Visual bounds come from that page's visual.json files (report units),
    resolved to PAGE-ABSOLUTE coordinates through the parent-group chain
    (children of a visualGroup store group-local coords). The v1 capture is the
    canvas VIEWPORT, not the page, so the page rectangle inside the PNG is located
    first (``_locate_page_rect``) and units are scaled by ``page_rect_px / page_units``
    per axis from that rectangle's origin; crops are clipped to the page rectangle.
    When the PNG already has the page's aspect ratio the whole PNG is the page (the
    original behaviour). Hidden visuals (``isHidden`` on the visual or an ancestor
    group) are skipped. The PNG is decoded ONCE and every crop is cut in memory
    (J-24). Returns ``{crops, skipped, crop_mapping, capture_method: "v1_crop"[,
    warnings]}`` - never raises (per-visual failures are collected, not fatal).
    """
    from PIL import Image

    root = Path(report_root)
    out_dir = Path(crops_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    page_visuals, groups, pdir = _page_inventory(root, page_id)
    page_w, page_h = _page_canvas_size(root, pdir.name)

    crops: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    with Image.open(full_page_png) as img:
        img.load()
        png_w, png_h = img.size
        loc = _locate_page_rect(img, page_w, page_h)
        ox, oy, rw, rh = loc["rect"]
        sx, sy = rw / page_w, rh / page_h
        clip_l, clip_t = int(round(ox)), int(round(oy))
        clip_r, clip_b = int(round(ox + rw)), int(round(oy + rh))
        for v in page_visuals:
            if _is_effectively_hidden(v, groups):
                skipped.append(
                    {
                        "visual_id": v.visual_id,
                        "reason": "hidden (isHidden on visual or ancestor group)",
                    }
                )
                continue
            pos = v.position
            abs_x, abs_y = _abs_position(v, groups)
            x, y = ox + abs_x * sx, oy + abs_y * sy
            w, h = pos["width"] * sx, pos["height"] * sy
            left, top = int(x) - padding, int(y) - padding
            right, bottom = int(x + w) + padding, int(y + h) + padding
            if left >= clip_r or top >= clip_b or right <= clip_l or bottom <= clip_t:
                skipped.append(
                    {
                        "visual_id": v.visual_id,
                        "reason": (f"Visual bbox ({x},{y},{w},{h}) outside page ({png_w}x{png_h})"),
                    }
                )
                continue
            box = (max(clip_l, left), max(clip_t, top), min(clip_r, right), min(clip_b, bottom))
            crop_path = out_dir / f"{v.visual_id}.png"
            try:
                img.crop(box).save(crop_path, format="PNG")
            except OSError as e:
                skipped.append({"visual_id": v.visual_id, "reason": f"crop failed: {e}"})
                continue
            entry: Dict[str, Any] = {
                "visual_id": v.visual_id,
                "visual_type": v.visual_type,
                "crop_path": str(crop_path),
            }
            if optimize:
                stats = _optimize(str(crop_path), vision_tier)
                if stats:
                    entry["visual_tokens"] = stats.get("visual_tokens")
            crops.append(entry)

    mapping: Dict[str, Any] = {
        "method": loc["method"],
        "page_rect_px": [round(ox), round(oy), round(rw), round(rh)],
        "png_size": [png_w, png_h],
        "page_size_units": [page_w, page_h],
    }
    for key in ("coverage", "reason", "downsample", "canvas_rgb"):
        if loc.get(key) is not None:
            mapping[key] = loc[key]
    result: Dict[str, Any] = {
        "crops": crops,
        "skipped": skipped,
        "crop_mapping": mapping,
        "capture_method": "v1_crop",
    }
    if loc["method"] == "assumed_full_png":
        result["warnings"] = [
            "Crop positions assume the PNG is exactly the page, but "
            + str(loc.get("reason"))
            + ". Crops may be misaligned; use report.snapshot.capture/v2 (region) when the "
            "Desktop build lists it, or a window/canvas whose viewport shows only the page."
        ]
    return result


# Native region captures (report.snapshot.capture/v2). The method is flighted per Desktop
# INSTANCE (present on one R0105 instance, absent on another of the same build), so it is
# used only when the live manifest of the target lists it; otherwise the v1 crop runs.
def _region_visuals(
    page_visuals: List[Any], groups: Dict[str, Any], page_w: float, page_h: float
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """``(regions, skipped)`` for a page: page-absolute rects clipped to the page."""
    regions: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for v in page_visuals:
        if _is_effectively_hidden(v, groups):
            skipped.append(
                {
                    "visual_id": v.visual_id,
                    "reason": "hidden (isHidden on visual or ancestor group)",
                }
            )
            continue
        ax, ay = _abs_position(v, groups)
        x0, y0 = max(0.0, ax), max(0.0, ay)
        x1 = min(page_w, ax + v.position["width"])
        y1 = min(page_h, ay + v.position["height"])
        if x1 - x0 < 1 or y1 - y0 < 1:
            skipped.append(
                {"visual_id": v.visual_id, "reason": "visual bbox is outside the page or empty"}
            )
            continue
        regions.append(
            {
                "id": v.visual_id,
                "visual_type": v.visual_type,
                "x": round(x0, 2),
                "y": round(y0, 2),
                "width": round(x1 - x0, 2),
                "height": round(y1 - y0, 2),
            }
        )
    return regions, skipped


def _capture_visual_regions_v2(
    *,
    rpid: int,
    page_id: str,
    report_root: str,
    crops_dir: str,
    scale: Optional[float],
    optimize: bool,
    vision_tier: str,
    wait_seconds: Optional[float],
    timeout: float,
    deadline: Optional[float],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """One native crop per visual via ``report.snapshot.capture/v2``.

    Returns ``(result, None)`` in the same shape as ``_crop_page_visuals`` (with
    ``capture_method: "v2_region"``), or ``(None, reason)`` when v2 cannot be used - the
    caller then falls back to the v1 crop. Exact visual rectangles are requested (no
    padding); pixel size is region x scale. The per-PID lock is the caller's.
    """
    import tempfile

    root = Path(report_root)
    page_visuals, groups, pdir = _page_inventory(root, page_id)
    page_w, page_h = _page_canvas_size(root, pdir.name)
    regions, skipped = _region_visuals(page_visuals, groups, page_w, page_h)
    if not regions:
        return None, "no visible visuals to capture"
    out_dir = Path(crops_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return None, f"crops_dir not writable: {e}"
    use_scale = float(scale) if scale is not None else 2.0
    with tempfile.TemporaryDirectory(prefix="pbi_regions_") as tmp:
        regions_file = Path(tmp) / "regions.json"
        regions_file.write_text(
            json.dumps([{k: r[k] for k in ("id", "x", "y", "width", "height")} for r in regions]),
            encoding="utf-8",
        )
        res = run_bridge_lib(
            "capture-regions",
            [
                "--pid",
                str(rpid),
                "--page",
                page_id,
                "--scale",
                f"{use_scale:g}",
                "--regions-file",
                str(regions_file),
                "--output-dir",
                str(out_dir),
                "--wait-ms",
                str(_wait_ms(wait_seconds, deadline)),
            ],
            timeout=_cli_timeout(max(timeout, 30.0 + 3.0 * len(regions)), deadline),
        )
    if not res.get("ok"):
        return None, str(
            res.get("error") or res.get("error_type") or "bridge library helper failed"
        )
    data = res["data"] if isinstance(res.get("data"), dict) else {}
    if data.get("status") == "unsupported":
        return None, str(
            data.get("reason") or "report.snapshot.capture/v2 not in the live manifest"
        )
    by_id = {c.get("id"): c for c in data.get("captures") or [] if isinstance(c, dict)}
    crops: List[Dict[str, Any]] = []
    for r in regions:
        cap = by_id.get(r["id"])
        if cap is None or not cap.get("path") or not Path(cap["path"]).is_file():
            fail = next(
                (
                    f
                    for f in data.get("failures") or []
                    if isinstance(f, dict) and f.get("id") == r["id"]
                ),
                {},
            )
            msg = (
                (fail.get("error") or {}).get("message")
                if isinstance(fail.get("error"), dict)
                else None
            )
            skipped.append(
                {"visual_id": r["id"], "reason": f"v2 region capture failed: {msg or 'no image'}"}
            )
            continue
        entry: Dict[str, Any] = {
            "visual_id": r["id"],
            "visual_type": r["visual_type"],
            "crop_path": cap["path"],
            "region": {k: r[k] for k in ("x", "y", "width", "height")},
        }
        if optimize:
            stats = _optimize(cap["path"], vision_tier)
            if stats:
                entry["visual_tokens"] = stats.get("visual_tokens")
        crops.append(entry)
    if not crops:
        return None, "every region capture failed"
    return {
        "crops": crops,
        "skipped": skipped,
        "capture_method": "v2_region",
        "scale": use_scale,
    }, None


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------
def _err(error_type: str, error: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error_type": error_type, "error": error, **extra}


# CLI error codes (bridge CLI 1.0.0 envelope) that get their own error_type.
# Others (AMBIGUOUS_OWNING_PBIP, OWNING_PBIP_NOT_FOUND, OWNING_PBIP_UNREADABLE,
# REPORT_PARENT_DIR_UNREADABLE, REPORT_DIR_REQUIRED, ...) pass through as
# ``cli_error_code`` under ``cli_error``.
_CLI_CODE_ERROR_TYPES = {
    "RELOAD_REJECTED": "reload_failed",
    "CONNECTION_LOST": "bridge_connection_lost",
    "AMBIGUOUS_DESKTOP_INSTANCE": "ambiguous_instance",
    "METHOD_NOT_AVAILABLE": "bridge_method_unavailable",
}
_CLI_ERR_PASSTHROUGH = (
    "raw_stderr",
    "raw_stdout",
    "returncode",
    "hint",
    "bridge_cli_script",
    "bridge_cli_version",
)


def _reload_rejected_msg(detail: Any) -> str:
    return (
        f"Desktop rejected the reload (bridge result.success=false): {detail or 'no detail'}. "
        "The canvas still shows the PREVIOUS state — fix the PBIR definition and reload again."
    )


def _cli_err(res: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    """Failure envelope that KEEPS the CLI's structured error (J-16).

    The CLI prints ``{status: "error", error: {code, message, details}}``;
    ``code`` values such as ``REPORT_DIR_REQUIRED`` / ``PAGES_JSON_NOT_FOUND``
    are what an agent needs to self-correct. ``RELOAD_REJECTED`` (CLI >= 1.0.0:
    a rejected reload is an error envelope + non-zero exit), ``CONNECTION_LOST``
    and ``AMBIGUOUS_DESKTOP_INSTANCE`` get their own ``error_type`` (see
    ``_CLI_CODE_ERROR_TYPES``).
    """
    out = _err(res.get("error_type", "cli_error"), res.get("error", "bridge CLI failed"), **extra)
    data = res.get("data")
    if data not in (None, {}):
        out["data"] = data
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            code = err.get("code")
            if code:
                out["cli_error_code"] = code
            if err.get("message"):
                out["cli_error_message"] = err.get("message")
            if err.get("jsonRpcCode") is not None:
                out["cli_jsonrpc_code"] = err["jsonRpcCode"]
            if err.get("bridgeCode"):
                out["cli_bridge_code"] = err["bridgeCode"]
            if code in _CLI_CODE_ERROR_TYPES:
                out["error_type"] = _CLI_CODE_ERROR_TYPES[code]
            details = err.get("details") if isinstance(err.get("details"), dict) else {}
            if code == "RELOAD_REJECTED":
                result = details.get("result")
                detail = None
                if isinstance(result, dict):
                    out["result"] = result
                    detail = result.get("error") or result.get("message")
                out["error"] = _reload_rejected_msg(detail or err.get("message"))
            elif code == "CONNECTION_LOST":
                for k in ("retryable", "retryAfterMs"):
                    if err.get(k) is not None:
                        out[k] = err[k]
    for key in _CLI_ERR_PASSTHROUGH:
        if res.get(key) not in (None, ""):
            out[key] = res[key]
    return out


def _cli_meta(out: Dict[str, Any]) -> Dict[str, Any]:
    """Add ``bridge_cli_version`` / ``bridge_cli_script`` (+ a newer-major warning) to a result."""
    cli = _resolve_cli()
    if "argv0" not in cli:
        return out
    out["bridge_cli_version"] = cli.get("version")
    out["bridge_cli_script"] = cli.get("script")
    ver = _parse_version(cli.get("version"))
    if ver is not None and ver[0] > MAX_TESTED_CLI_MAJOR:
        out.setdefault("warnings", []).append(
            f"Bridge CLI {cli.get('version')} is newer than the highest major this server was "
            f"tested with ({MAX_TESTED_CLI_MAJOR}.x); command output shapes may differ."
        )
    return out


def _pages_error_extra(inst: Dict[str, Any]) -> Dict[str, Any]:
    """CLI >= 1.0.0 puts ``pagesError`` on a connected instance whose page list failed."""
    pe = inst.get("pagesError")
    return {"pages_error": pe} if pe else {}


def _scale_args(scale: Optional[float]) -> tuple[List[str], Optional[Dict[str, Any]]]:
    """Build the ``--scale`` argv fragment. Returns ``(argv, error_envelope)``.

    ``None`` omits the flag entirely so the CLI's own default (2) applies.
    An out-of-range/non-numeric value is REJECTED rather than clamped — silently
    capturing at a different resolution than asked for is worse than an error.
    """
    if scale is None:
        return [], None
    try:
        val = float(scale)
    except (TypeError, ValueError):
        return [], _err(
            "invalid_argument",
            f"scale must be a number between {SCALE_MIN:g} and {SCALE_MAX:g}; got {scale!r}.",
        )
    if val != val or val < SCALE_MIN or val > SCALE_MAX:  # NaN or out of range
        return [], _err(
            "invalid_argument",
            f"scale must be between {SCALE_MIN:g} and {SCALE_MAX:g}; got {scale!r}.",
        )
    # Emit "2" not "2.0" — the CLI parses its own numbers, keep argv tidy.
    text = str(int(val)) if val.is_integer() else str(val)
    return ["--scale", text], None


def _ms_value(name: str, value: Any) -> tuple[Optional[int], Optional[Dict[str, Any]]]:
    if value is None:
        return None, None
    try:
        val = int(value)
    except (TypeError, ValueError):
        return None, _err(
            "invalid_argument",
            f"{name} must be a non-negative integer (milliseconds); got {value!r}.",
        )
    if val < 0:
        return None, _err(
            "invalid_argument",
            f"{name} must be a non-negative integer (milliseconds); got {value!r}.",
        )
    return val, None


def _settle_args(settle_ms: Optional[int]) -> tuple[List[str], Optional[Dict[str, Any]]]:
    """Build the ``--settle <ms>`` argv fragment (screenshot-all only)."""
    val, err = _ms_value("settle_ms", settle_ms)
    if err or val is None:
        return [], err
    return ["--settle", str(val)], None


def _path_arg_error(name: str, value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        return _err("invalid_argument", f"{name} must be a non-empty path string.")
    if any(ch in _ARG_FORBIDDEN_CHARS or ord(ch) < 32 for ch in value):
        return _err("invalid_argument", f"{name} contains control characters: {value!r}.")
    return None


def _remaining(deadline: Optional[float]) -> Optional[float]:
    if deadline is None:
        return None
    return deadline - time.monotonic()


def _cancelled() -> bool:
    try:
        from server.request_context import is_cancelled

        return bool(is_cancelled())
    except Exception:  # noqa: BLE001 - core must not hard-depend on server
        return False


def _budget_error(deadline: Optional[float], what: str, needed: float) -> Optional[Dict[str, Any]]:
    """Refuse to START *what* when the call was cancelled or the budget is spent (J-13)."""
    if _cancelled():
        return _err(
            "cancelled",
            f"The call was cancelled / timed out before {what} started; nothing was done.",
        )
    left = _remaining(deadline)
    if left is not None and left < needed:
        return _err(
            "budget_exhausted",
            f"Only {max(left, 0):.1f}s of the tool budget left — not starting {what} "
            f"(needs ~{needed:.0f}s). Nothing was done; call it again on its own.",
            remaining_seconds=round(max(left, 0.0), 1),
        )
    return None


def _cli_timeout(timeout: float, deadline: Optional[float]) -> float:
    left = _remaining(deadline)
    if left is None:
        return timeout
    return max(1.0, min(timeout, left))


def _wait_argv(wait_seconds: Optional[float], deadline: Optional[float]) -> List[str]:
    """``--wait-seconds`` for reload/screenshot/screenshot-all (J-31).

    Forwarded when the caller gave one, or when the remaining tool budget is
    below the CLI's own 60 s default (J-13); otherwise the CLI default applies.
    """
    left = _remaining(deadline)
    val: Optional[float] = wait_seconds
    if left is not None:
        cap = max(0.0, left - 5.0)
        if val is None and cap < 60.0:
            val = cap
        elif val is not None:
            val = min(val, cap)
    if val is None:
        return []
    return ["--wait-seconds", str(int(max(0.0, val)))]


def _status_wait(wait_seconds: Optional[float]) -> float:
    return float(wait_seconds) if wait_seconds is not None else DEFAULT_STATUS_WAIT


# ---------------------------------------------------------------------------
# Status (+ dirty assessment)
# ---------------------------------------------------------------------------
def op_status(
    *,
    wait_seconds: Optional[float] = DEFAULT_STATUS_WAIT,
    timeout: float = DEFAULT_TIMEOUT,
    pid: Optional[int] = None,
    include_dirty_sources: bool = False,
) -> Dict[str, Any]:
    argv = ["status"]
    if pid is not None:
        argv += ["--pid", str(int(pid))]
    argv += ["--wait-seconds", str(int(_status_wait(wait_seconds)))]
    res = run_bridge(argv, timeout=timeout)
    if not res["ok"]:
        return _cli_err(res)
    data = res["data"]
    out: Dict[str, Any] = {
        "success": True,
        "status": data.get("status"),
        "instances": data.get("instances") or [],
    }
    if data.get("diagnostics"):
        out["diagnostics"] = data["diagnostics"]
    for inst in out["instances"]:
        if isinstance(inst, dict) and inst.get("pagesError"):
            pe = inst["pagesError"]
            msg = pe.get("message") if isinstance(pe, dict) else pe
            out.setdefault("warnings", []).append(
                f"pid {inst.get('pid')}: page list unavailable (pagesError: {msg}); "
                "pages cannot be resolved by name for this instance."
            )
    if include_dirty_sources:
        for inst in out["instances"]:
            if isinstance(inst, dict):
                inst["dirty_sources"] = assess_dirty(inst)
                inst["dirty_state_source"] = inst["dirty_sources"]["dirty_state_source"]
    return _cli_meta(out)


_STATUS_CACHE: Dict[str, Any] = {"t": 0.0, "result": None}
_STATUS_CACHE_LOCK = threading.Lock()


def invalidate_status_cache() -> None:
    """Drop the cached status (called after reload/save; autouse-reset in tests)."""
    with _STATUS_CACHE_LOCK:
        _STATUS_CACHE["t"] = 0.0
        _STATUS_CACHE["result"] = None


def _cached_status(wait_seconds: Optional[float], *, force_refresh: bool = False) -> Dict[str, Any]:
    """`op_status` with a short TTL cache — READ-op instance resolution only.

    The explicit `status` MCP op, reload and save never use the cached value.
    Only successful results are cached.
    """
    if not force_refresh:
        with _STATUS_CACHE_LOCK:
            fresh = (
                _STATUS_CACHE["result"] is not None
                and time.monotonic() - _STATUS_CACHE["t"] < STATUS_CACHE_TTL
            )
            if fresh:
                return _STATUS_CACHE["result"]
    st = op_status(wait_seconds=_status_wait(wait_seconds))
    if st.get("success"):
        with _STATUS_CACHE_LOCK:
            _STATUS_CACHE["t"] = time.monotonic()
            _STATUS_CACHE["result"] = st
    return st


def _resolve_pid_instance(
    pid: Optional[int],
    pbip_path: Optional[str],
    wait_seconds: Optional[float],
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    st = _cached_status(wait_seconds, force_refresh=force_refresh)
    if not st.get("success"):
        return st  # already an error envelope
    try:
        inst = select_instance(
            {"instances": st["instances"], "status": st.get("status")},
            pid=pid,
            pbip_path=pbip_path,
        )
    except BridgeError as e:
        return _err(e.error_type, str(e), **e.extra)
    return {"success": True, "instance": inst}


def _fresh_instance(
    pid: Optional[int], pbip_path: Optional[str], wait_seconds: Optional[float]
) -> Dict[str, Any]:
    """Re-read the target instance NOW (never the cache) — reload/save pre-flight.

    With an explicit pid the status call is scoped to that process and runs
    under its pid lock (the CLI cancels concurrent calls to one PID).
    """
    if pid is not None:
        with _pid_lock(int(pid)):
            st = op_status(wait_seconds=_status_wait(wait_seconds), pid=int(pid))
    else:
        st = op_status(wait_seconds=_status_wait(wait_seconds))
    if st.get("success"):
        with _STATUS_CACHE_LOCK:
            if pid is None:
                _STATUS_CACHE["t"] = time.monotonic()
                _STATUS_CACHE["result"] = st
    else:
        return st
    try:
        inst = select_instance(
            {"instances": st["instances"], "status": st.get("status")},
            pid=int(pid) if pid is not None else None,
            pbip_path=pbip_path,
        )
    except BridgeError as e:
        return _err(e.error_type, str(e), **e.extra)
    return {"success": True, "instance": inst}


def _instance_artifacts(inst: Dict[str, Any]) -> Tuple[set, set]:
    reports, models = edit_ledger.project_artifacts(inst.get("currentFilePath"))
    if inst.get("reportDir"):
        r2, m2 = edit_ledger.project_artifacts(inst["reportDir"])
        reports |= r2
        models |= m2
    return reports, models


def _tree_max_mtime(dirs: set, *, exclude: set) -> float:
    newest = 0.0
    for d in dirs:
        root = Path(d)
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in (".git", ".pbi", "cache")]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                if edit_ledger.norm_path(full) in exclude:
                    continue
                try:
                    newest = max(newest, os.stat(full).st_mtime)
                except OSError:
                    continue
    return newest


def _live_writes_flushed_on_disk(models: set, tom: List[Dict[str, Any]]) -> bool:
    """Was the model tree rewritten AFTER the last pending live write?

    A manual Ctrl+S in Desktop writes the model TMDL files; their mtime then
    postdates the ledger's newest TOM write. Our own disk writes are excluded.
    """
    if not models or not tom:
        return False
    last = max(e["t"] for e in tom)
    own = edit_ledger.recent_own_writes(last - 1.0)
    newest = _tree_max_mtime(models, exclude=own)
    return newest > last + 0.5


# BR-03: Desktop 2.157.627.0 reported hasUnsavedChanges=true for an untouched PBIP and
# the flag did not clear after a successful save/reload. When a save VERIFIED ON DISK
# leaves the flag true on a pid, that pid's flag is recorded as stuck: it is still
# reported (and still gates a reload — the bridge never bypasses its own gate), but
# the caller is told the flag is not evidence of unsaved work and how to proceed.
_FLAG_STUCK: Dict[int, Dict[str, Any]] = {}
_FLAG_STUCK_LOCK = threading.Lock()
_FLAG_STUCK_SUGGESTION = (
    "Desktop's hasUnsavedChanges flag stayed true on this pid after a save verified on DISK "
    "(a known Desktop 2.157.627.0 defect), so the flag is not evidence of unsaved work here. "
    "The bridge does NOT bypass its own gate: if you know nothing else is unsaved, reload with "
    "discard_unsaved=true."
)


def note_flag_stuck(pid: int, *, file: Optional[str] = None) -> None:
    """Record that *pid*'s ``hasUnsavedChanges`` flag survived a disk-verified save."""
    with _FLAG_STUCK_LOCK:
        _FLAG_STUCK[int(pid)] = {"since": time.time(), "file": file}


def clear_flag_stuck(pid: Optional[int] = None) -> None:
    """Forget the stuck marker of one pid (or every pid). Tests / the flag went false."""
    with _FLAG_STUCK_LOCK:
        if pid is None:
            _FLAG_STUCK.clear()
        else:
            _FLAG_STUCK.pop(int(pid), None)


def flag_stuck_info(pid: Any) -> Optional[Dict[str, Any]]:
    try:
        key = int(pid)
    except (TypeError, ValueError):
        return None
    with _FLAG_STUCK_LOCK:
        info = _FLAG_STUCK.get(key)
        return dict(info) if info else None


_REPLAY_HINT = (
    "02_Model_Operations object_type=model operation=replay (preview=true first) re-applies "
    "them after a discarded reload or a crash; operation=history lists them."
)


def _tmdl_fingerprint(definition_dir: Optional[str]) -> Optional[Tuple[Tuple[str, int, int], ...]]:
    """(relative path, size, mtime_ns) of every file under *definition_dir* - cheap change
    detector for the classify -> reload window (N-30). None when the folder is unreadable."""
    if not definition_dir:
        return None
    root = Path(definition_dir)
    try:
        rows = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                st = path.stat()
                rows.append((str(path.relative_to(root)), st.st_size, st.st_mtime_ns))
        return tuple(rows)
    except OSError:
        return None


def _journal_flush(reports: Any, models: Any, *, before: Optional[float] = None) -> None:
    """A save was verified on disk: flush the durable write journal (JR-01). Never raises."""
    try:
        from core.operations import write_journal

        write_journal.mark_flushed(reports, models, before=before)
    except Exception as e:  # noqa: BLE001 - the journal must never break a save / status
        logger.debug("write journal flush failed: %s", e)


def _journal_unflushed(reports: Any, models: Any) -> Dict[str, Any]:
    try:
        from core.operations import write_journal

        return write_journal.unflushed_summary(reports, models)
    except Exception as e:  # noqa: BLE001
        logger.debug("write journal summary failed: %s", e)
        return {"count": 0, "ids": [], "journals": []}


def assess_dirty(inst: Dict[str, Any]) -> Dict[str, Any]:
    """Every signal about whether *inst* holds unsaved in-memory work (J-32 dirty_sources).

    ``verdict``: ``clean`` | ``dirty`` | ``unknown``. ``unknown`` = the bridge
    could not read the app state (instance not ``connected`` or no
    ``hasUnsavedChanges`` boolean) — NEVER treated as clean.
    ``dirty_state_source`` (BR-03) names what decided it: ``bridge_flag`` (Desktop's
    flag alone), ``ledger`` (a live-model write this server made, flag false),
    ``bridge_flag+ledger`` (both agree — also the clean case, where both were
    consulted) or ``unavailable`` (unknown verdict). ``flag_stuck`` marks a pid
    whose flag survived a disk-verified save (see ``note_flag_stuck``).
    """
    bridge_status = inst.get("bridgeStatus")
    flag = inst.get("hasUnsavedChanges")
    reports, models = _instance_artifacts(inst)
    tom = edit_ledger.pending_tom_writes(reports, models)
    flushed = False
    if tom and _live_writes_flushed_on_disk(models, tom):
        flushed = True
        edit_ledger.clear_tom(reports, models)
        _journal_flush(reports, models, before=max(e["t"] for e in tom))
        tom = []
    disk = edit_ledger.pending_disk_edits(reports, models)
    journal = _journal_unflushed(reports, models)
    reasons: List[str] = []
    stuck = flag_stuck_info(inst.get("pid"))
    if stuck and flag is False:
        clear_flag_stuck(inst.get("pid"))  # the flag works again
        stuck = None
    if bridge_status not in (None, "connected"):
        verdict = "unknown"
        source = "unavailable"
        reasons.append(f"bridgeStatus={bridge_status!r} — app state unreadable")
    elif not isinstance(flag, bool):
        verdict = "unknown"
        source = "unavailable"
        reasons.append("hasUnsavedChanges not reported")
    elif flag or tom:
        verdict = "dirty"
        source = "bridge_flag+ledger" if (flag and tom) else ("bridge_flag" if flag else "ledger")
        if flag:
            reasons.append("Desktop reports hasUnsavedChanges=true")
        if tom:
            reasons.append(
                f"{len(tom)} live-model write(s) by this server not yet saved "
                f"(latest: {tom[-1].get('label') or '?'})"
            )
    else:
        verdict = "clean"
        source = "bridge_flag+ledger"
    if journal["count"]:
        reasons.append(
            f"journal_unflushed: {journal['count']} write(s) in the durable journal not yet "
            f"flushed by a verified save ({_REPLAY_HINT})"
        )
    out: Dict[str, Any] = {
        "verdict": verdict,
        "reasons": reasons,
        "dirty_state_source": source,
        "intent_flag": flag,
        "bridge_status": bridge_status,
        "ledger_live_writes": len(tom),
        "ledger_live_write_labels": [e.get("label") for e in tom[-5:]],
        "live_writes_flushed_on_disk": flushed,
        "journal_unflushed": journal["count"],
        "journal_unflushed_ids": journal["ids"],
        "pending_disk_edits": disk,
        "flag_stuck": bool(stuck and flag is True),
        "note": (
            "hasUnsavedChanges comes from the MS CLI as `=== true`, so 'unknown' "
            "arrives as false; the live-write ledger covers this server session only."
        ),
    }
    if out["flag_stuck"]:
        out["flag_stuck_since"] = stuck["since"] if stuck else None
        out["suggestion"] = _FLAG_STUCK_SUGGESTION
        reasons.append("hasUnsavedChanges stayed true after a disk-verified save (flag_stuck)")
    return out


_RELOAD_RULE = (
    "Save BEFORE disk edits (17_Desktop_Bridge save / save_first=true while no disk "
    "edits are pending); AFTER disk edits reload, never save first — a save would "
    "overwrite them with Desktop's in-memory copy."
)


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------
def _wait_ms(wait_seconds: Optional[float], deadline: Optional[float]) -> int:
    """Retry budget for the library helper, mirroring the CLI's ``--wait-seconds`` (60 s default)."""
    argv = _wait_argv(wait_seconds, deadline)
    return (int(argv[1]) if argv else 60) * 1000


def _tmdl_reload_advice(inst: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Best-effort: would ``include_model=true`` accept the pending TMDL edits?

    Runs the change classifier (read-only) so a plain reload with pending TMDL
    edits can say "re-run with include_model=true" only when that would work.
    ``None`` when the classifier could not run at all.
    """
    try:
        from core.autonomous import model_change_classifier as mcc

        project = inst.get("currentFilePath") or inst.get("reportDir")
        if not project:
            return None
        res, _ctx = mcc.classify_for_reload(
            project, project_matches=lambda p: _instance_matches_path(inst, p)
        )
    except Exception as e:  # noqa: BLE001 - advice must never fail a reload
        logger.debug("tmdl reload advice failed: %s", e)
        return None
    if res.get("success"):
        return {"classification": res.get("classification")}
    return {"error_type": res.get("error_type"), "error": res.get("error")}


def op_reload(
    *,
    pid: Optional[int] = None,
    pbip_path: Optional[str] = None,
    wait_seconds: Optional[float] = None,
    timeout: float = DEFAULT_TIMEOUT,
    save_first: bool = False,
    discard_unsaved: bool = False,
    overwrite_disk_edits: bool = False,
    include_model: bool = False,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """In-place reload of a running Desktop: re-read the open PBIP/PBIR
    definition from disk (no close/reopen). Fails CLOSED on unsaved work.

    WHY a plain reload does not pick up model/TMDL edits: the bridge protocol
    method ``file.reload/v1`` takes a ``reloadModelDefinition`` parameter, but the
    ``reload`` command of ``@microsoft/powerbi-desktop-bridge-cli`` always sends
    ``false`` (verified on 0.1.1 ``dist/index.js`` and 1.0.0 ``dist/cli.js``).
    ``include_model=True`` (BR-04) goes through the CLI *library* helper
    (``tools/bridge_lib.mjs``) instead and sends ``true`` - but ONLY after the
    same unsaved-work gate AND a change classifier
    (``core.autonomous.model_change_classifier``) proved every pending
    disk-vs-live model difference is metadata-only; partition / M / expression /
    data-source / import-table changes are refused
    (``model_reload_unsafe_change``: close without saving + reopen). The result
    carries ``model_reload`` with ``applied`` / ``mismatch`` from a post-reload
    re-read of the live model. Otherwise apply model edits via live TOM
    (``02_Model_Operations``) and verify with ``04_Run_DAX``.

    - The target instance is ALWAYS re-read right before the reload (explicit
      pid included, no cached status) and the reload REFUSES when
      ``assess_dirty`` says ``dirty`` (``error_type: unsaved_changes``) or
      ``unknown`` (``dirty_state_unknown``) - unless ``discard_unsaved=True``
      (the caller accepts losing that work; reported as ``discarded_unsaved``).
      The MS reload does NOT refuse on unsaved TOM edits (spike 2026-09-30): this
      gate is the only protection, and ``include_model`` adds no bypass to it.
    - ``save_first=True`` flushes in-memory changes to disk via ``op_save``
      first - which itself refuses while disk edits are pending (J-02).
    - A reload Desktop rejected is a FAILURE (``reload_failed``): CLI 0.x exits 0
      with ``result.success == false`` (J-05); CLI >= 1.0.0 exits non-zero with a
      ``RELOAD_REJECTED`` error envelope (mapped in ``_cli_err``).
    """
    extra: Dict[str, Any] = {}
    early = _budget_error(deadline, "the reload", MIN_RELOAD_BUDGET_S)
    if early:
        return early

    saved_ok = False
    if save_first:
        saved = op_save(
            pid=pid,
            pbip_path=pbip_path,
            wait_seconds=wait_seconds,
            overwrite_disk_edits=overwrite_disk_edits,
            deadline=deadline,
        )
        if not saved.get("success"):
            return saved
        saved_ok = True
        extra["saved"] = bool(saved.get("saved", not saved.get("already_saved")))
        if saved.get("already_saved"):
            extra["already_saved"] = True
        extra["save"] = {
            k: saved.get(k)
            for k in ("verified", "verified_clean", "mechanism", "message")
            if saved.get(k) is not None
        }
        if pid is None and saved.get("pid") is not None:
            pid = saved.get("pid")

    sel = _fresh_instance(pid, pbip_path, wait_seconds)
    if not sel.get("success"):
        return sel
    inst = sel["instance"]
    rpid = int(inst["pid"])
    dirty = assess_dirty(inst)
    extra["dirty_state"] = dirty["verdict"]
    extra["dirty_state_source"] = dirty["dirty_state_source"]
    if dirty["verdict"] != "clean" and not saved_ok:
        if not discard_unsaved:
            et = "unsaved_changes" if dirty["verdict"] == "dirty" else "dirty_state_unknown"
            refusal = _err(
                et,
                (
                    "Refusing to reload: "
                    + ("; ".join(dirty["reasons"]) or dirty["verdict"])
                    + ". A reload re-reads disk and silently DISCARDS unsaved in-memory "
                    "edits (e.g. live-TOM measures). "
                    + _RELOAD_RULE
                    + (
                        f" journal_unflushed: {dirty['journal_unflushed']} - after a discarded "
                        "reload, " + _REPLAY_HINT
                        if dirty.get("journal_unflushed")
                        else ""
                    )
                ),
                pid=rpid,
                dirty_state_source=dirty["dirty_state_source"],
                journal_unflushed=dirty.get("journal_unflushed") or None,
                dirty_sources=dirty,
                hint=(
                    "No disk edits pending -> save first (save_first=true). Disk edits "
                    "pending AND unsaved memory -> no ordering keeps both: reload with "
                    "discard_unsaved=true and redo the live-TOM edits, or save with "
                    "overwrite_disk_edits=true and redo the disk edits."
                    if dirty["pending_disk_edits"]
                    else "Call save_first=true (or operation=save) to keep the in-memory "
                    "changes, or pass discard_unsaved=true to throw them away."
                ),
            )
            if dirty.get("flag_stuck"):
                refusal["flag_stuck"] = True
                refusal["suggestion"] = dirty["suggestion"]
            return refusal
        extra["discarded_unsaved"] = {
            "verdict": dirty["verdict"],
            "reasons": dirty["reasons"],
            "ledger_live_write_labels": dirty["ledger_live_write_labels"],
        }
        if dirty.get("journal_unflushed"):
            extra["discarded_unsaved"]["journal_unflushed"] = dirty["journal_unflushed"]
            extra["discarded_unsaved"]["recover_with"] = _REPLAY_HINT

    late = _budget_error(deadline, "the reload", MIN_RELOAD_BUDGET_S / 2)
    if late:
        return late

    ctx: Optional[Dict[str, Any]] = None
    classification: Dict[str, Any] = {}
    if include_model:
        from core.autonomous import model_change_classifier as mcc

        project = inst.get("currentFilePath") or inst.get("reportDir")
        if not project:
            return _err(
                mcc.ERR_UNCLASSIFIED,
                "Cannot classify the pending model change: the instance reports no open file. "
                "The model reload was NOT attempted.",
                pid=rpid,
            )
        tmdl_fp = _tmdl_fingerprint(mcc.find_model_definition(project)[0])
        classification, ctx = mcc.classify_for_reload(
            project, project_matches=lambda p: _instance_matches_path(inst, p)
        )
        if not classification.get("success"):
            return {
                **classification,
                "pid": rpid,
                "include_model": True,
                "dirty_state": dirty["verdict"],
            }
        # Classification (TOM deserialize + a live read) can be slow: re-check the budget.
        late = _budget_error(deadline, "the model reload", MIN_RELOAD_BUDGET_S / 2)
        if late:
            return late
        tmdl_now = _tmdl_fingerprint(classification.get("definition_dir"))
        if tmdl_fp is not None and tmdl_now != tmdl_fp:
            return _err(
                mcc.ERR_UNCLASSIFIED,
                "The on-disk TMDL changed while the pending model change was being classified, "
                "so the verdict no longer describes what Desktop would load. The model reload "
                "was NOT attempted; run the reload again.",
                pid=rpid,
                include_model=True,
            )
        with _pid_lock(rpid):
            res = run_bridge_lib(
                "reload-model",
                ["--pid", str(rpid), "--wait-ms", str(_wait_ms(wait_seconds, deadline))],
                timeout=_cli_timeout(timeout, deadline),
            )
    else:
        argv = ["reload", "--pid", str(rpid), *_wait_argv(wait_seconds, deadline)]
        with _pid_lock(rpid):
            res = run_bridge(argv, timeout=_cli_timeout(timeout, deadline))
    # The dirty state changes across a reload - drop the cache.
    invalidate_status_cache()
    if not res["ok"]:
        out = _cli_err(res, pid=rpid)
        if include_model:
            out["include_model"] = True
            if out.get("error_type") == "timeout_outcome_unknown" and ctx is not None:
                # N-30: Desktop may have applied the reload before the helper was killed; look
                # at the live model and leave the TMDL ledger alone (nothing proved it applied)
                out["model_reload"] = _finish_model_reload(inst, classification, ctx, {})
                out["model_reload"]["outcome_unknown"] = True
            if out.get("error_type") == "cli_error" and out.get("cli_error_code") == "BRIDGE_ERROR":
                # Desktop refused the definition (e.g. a TMDL parse error: RENDER_FAILED,
                # jsonRpcCode -32505, process exit code 0 in the spike). It applies atomically.
                out["error_type"] = "model_reload_failed"
                out["error"] = (
                    "Desktop rejected the model definition reload: "
                    f"{out.get('cli_error_message') or out.get('error')}. The previous model "
                    "stays loaded (the reload is atomic); fix the TMDL and reload again."
                )
        return out
    data = res["data"] if isinstance(res.get("data"), dict) else {}
    result = data.get("result")
    if isinstance(result, dict) and result.get("success") is False:
        return _err(
            "reload_failed",
            _reload_rejected_msg(result.get("error") or result.get("message")),
            pid=rpid,
            result=result,
            data=data,
        )

    reports, models = _instance_artifacts(inst)
    edit_ledger.clear_disk(reports, models, kinds=("pbir",))
    if discard_unsaved or saved_ok:
        edit_ledger.clear_tom(reports, models)
    tmdl_pending = [e for e in dirty["pending_disk_edits"] if e.get("kind") == "tmdl"]
    envelope: Dict[str, Any] = {"success": True, "pid": rpid, "data": res["data"], **extra}
    if include_model and ctx is not None:
        envelope["include_model"] = True
        model_reload = _finish_model_reload(inst, classification, ctx, data)
        envelope["model_reload"] = model_reload
        if model_reload["verification"] == "mismatch":
            envelope["tmdl_edits_not_applied"] = tmdl_pending or [
                {"kind": "tmdl", "mismatch": model_reload["mismatch"]}
            ]
            envelope["tmdl_warning"] = (
                "The model reload succeeded but the live model still differs from disk for: "
                + "; ".join(model_reload["mismatch"][:5])
                + ". The pending TMDL edits stay in the ledger (a save would overwrite them)."
            )
        else:
            edit_ledger.clear_disk(reports, models, kinds=("tmdl",))
        return envelope
    if tmdl_pending:
        envelope["tmdl_edits_not_applied"] = tmdl_pending
        left = _remaining(deadline)
        advice = _tmdl_reload_advice(inst) if (left is None or left > 15.0) else None
        if advice:
            envelope["model_reload_hint"] = advice
        if advice and advice.get("classification") in ("metadata_only", "unchanged"):
            envelope["tmdl_warning"] = (
                "Pending TMDL disk edits were NOT applied: this reload sent "
                "reloadModelDefinition:false. The change classifier says they are metadata-only, "
                "so re-run with include_model=true to apply them in place (no reopen needed). "
                "Do not save - it would overwrite them."
            )
        else:
            envelope["tmdl_warning"] = (
                "Pending TMDL disk edits were NOT applied: this reload sent "
                "reloadModelDefinition:false. If they are metadata-only (measures, calculated "
                "columns, relationships, formats) try include_model=true, which classifies them "
                "first and refuses partition / M / expression / data-source / import-table "
                "changes; for those close Desktop WITHOUT saving (ask the user) and reopen "
                "(17_Desktop_Bridge operation=open), or redo them via live TOM. "
                "Do not save - it would overwrite them."
            )
    return envelope


_MODEL_RELOAD_FOLLOW_UP = (
    "The Desktop Data pane can stay stale after a model reload (it may still list objects the "
    "engine dropped): verify by DAX (04_Run_DAX, INFO.MEASURES() / INFO.COLUMNS() / "
    "INFO.TABLES()), not by the UI.",
    "A model reload never refreshes imported data.",
)
_CALCULATE_NOTE = (
    "Calculated columns / calculated tables changed: they may need "
    "02_Model_Operations object_type=model operation=refresh refresh_type=calculate "
    "(Desktop can show 'calculated columns need to be manually refreshed'). Processing can "
    "flip hasUnsavedChanges to true."
)


def _finish_model_reload(
    inst: Dict[str, Any], classification: Dict[str, Any], ctx: Dict[str, Any], data: Dict[str, Any]
) -> Dict[str, Any]:
    """Post-reload verification + the ``model_reload`` block of the envelope."""
    from core.autonomous import model_change_classifier as mcc

    try:
        verify = mcc.verify_after_reload(
            ctx["disk_signature"],
            ctx["changes"],
            project_matches=lambda p: _instance_matches_path(inst, p),
            skip_functions=bool(ctx.get("udf_gate")),
        )
    except Exception as e:  # noqa: BLE001 - never turn a successful reload into an error
        verify = {"status": "unavailable", "applied": [], "mismatch": [], "reason": str(e)}
    follow_up = list(_MODEL_RELOAD_FOLLOW_UP)
    if classification.get("calculated_objects_changed"):
        follow_up.insert(0, _CALCULATE_NOTE)
    out: Dict[str, Any] = {
        "classification": classification.get("classification"),
        "change_count": classification.get("change_count"),
        "counts_by_category": classification.get("counts_by_category"),
        "changes": classification.get("changes"),
        "udf_gate": bool(ctx.get("udf_gate")),
        "verification": verify["status"],
        "applied": verify.get("applied", []),
        "mismatch": verify.get("mismatch", []),
        "follow_up": follow_up,
    }
    for key in ("changes_truncated", "applied_truncated", "unexpected_mismatch", "reason"):
        if verify.get(key) is not None:
            out[key] = verify[key]
    if verify["status"] == "unavailable":
        out["verification_note"] = (
            "The live model could not be re-read after the reload ("
            + str(verify.get("reason"))
            + "): verify with 04_Run_DAX before relying on it."
        )
    app_state = data.get("applicationState")
    if isinstance(app_state, dict):
        out["application_state"] = app_state
    return out


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------
def _hash_file(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def op_screenshot(
    *,
    page: str,
    output: str,
    pid: Optional[int] = None,
    pbip_path: Optional[str] = None,
    wait_seconds: Optional[float] = None,
    timeout: float = DEFAULT_TIMEOUT,
    optimize: bool = True,
    per_visual: bool = False,
    report_root: Optional[str] = None,
    crops_dir: Optional[str] = None,
    scale: Optional[float] = None,
    settle_ms: Optional[int] = None,
    render_stable: bool = False,
    vision_tier: str = "high",
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Capture one page to PNG.

    ``scale`` (1..3, CLI default 2) multiplies the NATIVE capture resolution
    BEFORE our own ``optimize`` downscale. ``settle_ms`` waits before the
    capture; ``render_stable`` re-captures until two consecutive captures are
    byte-identical (max ``RENDER_STABLE_MAX_CAPTURES``) — both address the
    half-rendered-canvas problem right after a reload (J-21 / J-32). All values
    are validated up-front: an invalid value errors without spawning the CLI.
    Pages come from the report's pages.json ON DISK (that is what the CLI
    reads), matched by id, display name, then case-insensitively.
    """
    scale_argv, scale_err = _scale_args(scale)
    if scale_err:
        return scale_err
    settle_val, settle_err = _ms_value("settle_ms", settle_ms)
    if settle_err:
        return settle_err
    path_err = _path_arg_error("output", output)
    if path_err:
        return path_err
    sel = _resolve_pid_instance(pid, pbip_path, wait_seconds)
    if not sel.get("success"):
        return sel
    inst = sel["instance"]
    pr = resolve_page(inst, page)
    if not pr["page_id"] and not pr["ambiguous"]:
        # The page may have just been created on disk while the cached status
        # predates it: re-read once (no sleep — pages come from pages.json).
        if SETTLE_RETRY_SECONDS:
            time.sleep(SETTLE_RETRY_SECONDS)
        sel = _resolve_pid_instance(pid, pbip_path, wait_seconds, force_refresh=True)
        if not sel.get("success"):
            return sel
        inst = sel["instance"]
        pr = resolve_page(inst, page)
    if pr["ambiguous"]:
        return _err(
            "ambiguous_page",
            f"Page {page!r} matches {len(pr['ambiguous'])} pages; pass the page id.",
            candidates=pr["ambiguous"],
        )
    page_id = pr["page_id"]
    if not page_id:
        return _err(
            "page_not_found",
            f"Page {page!r} not found on pid={inst['pid']} (pages are read from the "
            "report's pages.json on disk).",
            available=[p.get("displayName") for p in inst.get("pages") or []],
            **_pages_error_extra(inst),
        )
    rpid = int(inst["pid"])
    stable_info: Optional[Dict[str, Any]] = None
    with _pid_lock(rpid):
        if settle_val:
            left = _remaining(deadline)
            delay = settle_val / 1000.0
            if left is not None:
                delay = max(0.0, min(delay, left - 10.0))
            time.sleep(delay)
        argv = [
            "screenshot",
            page_id,
            "--pid",
            str(rpid),
            "--output",
            output,
            *scale_argv,
            *_wait_argv(wait_seconds, deadline),
        ]
        res = run_bridge(argv, timeout=_cli_timeout(timeout, deadline))
        if res["ok"] and render_stable:
            hashes = [_hash_file(output)]
            stable = False
            while len(hashes) < RENDER_STABLE_MAX_CAPTURES:
                if _budget_error(deadline, "another capture", 10.0):
                    break
                time.sleep(RENDER_STABLE_INTERVAL_S)
                again = run_bridge(argv, timeout=_cli_timeout(timeout, deadline))
                if not again["ok"]:
                    break
                res = again
                hashes.append(_hash_file(output))
                if hashes[-1] is not None and hashes[-1] == hashes[-2]:
                    stable = True
                    break
            stable_info = {"captures": len(hashes), "stable": stable}
    if not res["ok"]:
        return _cli_err(res, pid=rpid, page_id=page_id)
    envelope: Dict[str, Any] = {
        "success": True,
        "pid": rpid,
        "page_id": page_id,
        "output": output,
        "data": res["data"],
    }
    if pr["match"] not in ("id", "display_name"):
        envelope["page_match"] = pr["match"]
    if scale is not None:
        envelope["scale"] = scale
    if settle_val:
        envelope["settle_ms"] = settle_val
    if stable_info is not None:
        envelope["render_stable"] = stable_info
    # Per-visual crop MUST run before the page optimize: crops are cut from the
    # NATIVE-resolution capture (each crop is then optimized individually).
    # report_root falls back to the instance's reportDir from the status payload.
    if per_visual:
        effective_root = report_root or inst.get("reportDir")
        if not effective_root:
            envelope["per_visual_error"] = (
                "per_visual requires report_root (the PBIR report folder) — "
                "pass it explicitly; the bridge did not report a reportDir."
            )
        else:
            try:
                crops_path = crops_dir or str(Path(output).with_suffix("")) + "_crops"
                # BR-05: native per-visual regions (report.snapshot.capture/v2) when the live
                # manifest lists them; otherwise the v1 crop of the page capture.
                v2_result: Optional[Dict[str, Any]] = None
                v2_reason: Optional[str] = None
                try:
                    with _pid_lock(rpid):
                        v2_result, v2_reason = _capture_visual_regions_v2(
                            rpid=rpid,
                            page_id=page_id,
                            report_root=effective_root,
                            crops_dir=crops_path,
                            scale=scale,
                            optimize=optimize,
                            vision_tier=vision_tier,
                            wait_seconds=wait_seconds,
                            timeout=timeout,
                            deadline=deadline,
                        )
                except Exception as e:  # noqa: BLE001 - v2 is an optimisation, v1 always works
                    v2_reason = f"v2 region capture crashed: {e}"
                if v2_result is not None:
                    envelope["visual_crops"] = v2_result
                else:
                    envelope["visual_crops"] = _crop_page_visuals(
                        full_page_png=output,
                        report_root=effective_root,
                        page_id=page_id,
                        crops_dir=crops_path,
                        optimize=optimize,
                        vision_tier=vision_tier,
                    )
                    if v2_reason:
                        envelope["visual_crops"]["v2_unavailable_reason"] = v2_reason
            except Exception as e:  # pragma: no cover - defensive
                envelope["per_visual_error"] = str(e)
    # Resize the full-page PNG to Claude's native image resolution (ON by
    # default) so it isn't silently downscaled server-side and its token cost
    # is controlled. Non-fatal: a capture never fails because of optimize.
    if optimize:
        stats = _optimize(output, vision_tier)
        if stats:
            envelope["optimize"] = stats
    return envelope


class _CaptureProgress:
    """Per-page progress for the one-shot ``screenshot-all`` CLI call (F-26).

    The CLI captures every page in a single process, so progress is read off the
    output directory: a daemon thread counts the PNGs that appear and reports
    ``page i of n`` (``n`` = the pages Desktop lists). It runs inside a copy of the
    caller's context so the MCP progress token is visible, and never touches the
    capture itself.
    """

    _POLL_S = 0.5

    def __init__(self, output_dir: str, total: int) -> None:
        import contextvars
        import threading

        self._dir = Path(output_dir)
        self._total = max(int(total), 0)
        self._stop = threading.Event()
        self._seen = 0
        self._ctx = contextvars.copy_context()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="screenshot-all-progress"
        )

    def _count(self) -> int:
        try:
            return sum(1 for _ in self._dir.glob("*.png")) if self._dir.is_dir() else 0
        except OSError:
            return self._seen

    def _poll(self) -> None:
        from core.utilities import progress_ticker

        n = self._count()
        if n > self._seen:
            self._seen = n
            progress_ticker.emit(
                n, self._total or None, f"captured page {n} of {self._total or '?'}"
            )

    def _run(self) -> None:
        while not self._stop.wait(self._POLL_S):
            self._ctx.run(self._poll)

    def __enter__(self) -> "_CaptureProgress":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)


def op_screenshot_all(
    *,
    output_dir: str,
    pid: Optional[int] = None,
    pbip_path: Optional[str] = None,
    wait_seconds: Optional[float] = None,
    timeout: float = DEFAULT_TIMEOUT,
    optimize: bool = True,
    scale: Optional[float] = None,
    settle_ms: Optional[int] = None,
    vision_tier: str = "high",
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Capture every page to ``output_dir``.

    ``scale`` (1..3, CLI default 2) multiplies the NATIVE capture resolution
    BEFORE our own ``optimize`` downscale. ``settle_ms`` is the CLI's own
    pre-capture delay. Both are validated before any CLI spawn. When some pages
    fail the CLI exits 1 with ``status: partial``: that is reported as SUCCESS
    with ``partial: true`` + ``failures`` and the captured PNGs are still
    optimized (J-16).
    """
    scale_argv, scale_err = _scale_args(scale)
    if scale_err:
        return scale_err
    settle_argv, settle_err = _settle_args(settle_ms)
    if settle_err:
        return settle_err
    path_err = _path_arg_error("output_dir", output_dir)
    if path_err:
        return path_err
    sel = _resolve_pid_instance(pid, pbip_path, wait_seconds)
    if not sel.get("success"):
        return sel
    rpid = int(sel["instance"]["pid"])
    total_pages = len(sel["instance"].get("pages") or [])
    with _pid_lock(rpid), _CaptureProgress(output_dir, total_pages):
        res = run_bridge(
            [
                "screenshot-all",
                "--pid",
                str(rpid),
                "--output-dir",
                output_dir,
                *scale_argv,
                *settle_argv,
                *_wait_argv(wait_seconds, deadline),
            ],
            timeout=_cli_timeout(timeout, deadline),
        )
    partial = False
    if not res["ok"]:
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        if data.get("status") == "partial" and data.get("screenshots"):
            partial = True
            res = {"ok": True, "data": data, "raw_stderr": res.get("raw_stderr")}
        else:
            return _cli_err(res, pid=rpid)
    data = res["data"]
    envelope: Dict[str, Any] = {
        "success": True,
        "pid": rpid,
        "output_dir": output_dir,
        "data": data,
    }
    if partial:
        envelope["partial"] = True
        envelope["failures"] = data.get("failures") or []
        envelope["warning"] = (
            f"{len(envelope['failures'])} page(s) failed to capture; "
            f"{len(data.get('screenshots') or [])} captured."
        )
    if scale is not None:
        envelope["scale"] = scale
    if settle_ms is not None:
        envelope["settle_ms"] = settle_ms
    # Resize every captured PNG to Claude's native image resolution (ON by
    # default). Non-fatal: a capture never fails because of optimize.
    if optimize:
        optimized: List[Dict[str, Any]] = []
        shots = [
            s.get("outputPath") for s in (data.get("screenshots") or []) if isinstance(s, dict)
        ]
        files = [Path(p) for p in shots if p]
        if not files:
            out_path = Path(output_dir)
            files = sorted(out_path.glob("*.png")) if out_path.is_dir() else []
        from core.utilities.progress_ticker import Ticker

        ticker = Ticker(len(files), "optimizing page")
        for index, png in enumerate(files):
            if _cancelled():
                # The captures are on disk; only the downscale pass is cut short.
                envelope["cancelled"] = True
                envelope["optimize_skipped"] = [f.name for f in files[index:]]
                break
            stats = _optimize(str(png), vision_tier)
            if stats:
                optimized.append({"file": png.name, **stats})
            ticker.tick(png.name)
        envelope["optimize"] = optimized
    return envelope


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
_SAVE_VERIFY_ATTEMPTS = 3
_SAVE_VERIFY_INTERVAL = 1.0


def _verify_saved_clean(
    pid: Optional[int], pbip_path: Optional[str], wait_seconds: Optional[float]
) -> Optional[bool]:
    """Confirm the save landed via the bridge's own ``hasUnsavedChanges`` flag.

    Returns True (clean), False (still dirty after retries), or None (the
    instance could not be read — verification not possible).
    """
    for attempt in range(_SAVE_VERIFY_ATTEMPTS):
        sel = _resolve_pid_instance(pid, pbip_path, wait_seconds, force_refresh=True)
        if not sel.get("success"):
            return None
        flag = sel["instance"].get("hasUnsavedChanges")
        if not isinstance(flag, bool):
            return None
        if flag is False:
            return True
        if attempt < _SAVE_VERIFY_ATTEMPTS - 1:
            time.sleep(_SAVE_VERIFY_INTERVAL)
    return False


def op_save(
    *,
    pid: Optional[int] = None,
    pbip_path: Optional[str] = None,
    wait_seconds: Optional[float] = None,
    save_timeout: float = 30.0,
    overwrite_disk_edits: bool = False,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Persist the running Desktop's in-memory model + report to disk.

    The MS ``powerbi-desktop`` CLI exposes no ``save`` subcommand (its RPC is
    read/reload/screenshot only), so saving is done via UI automation (UIA
    InvokePattern on the Save control, SendInput Ctrl+S as a fallback) and
    verified ON DISK.

    - Refuses with ``conflict_pending_disk_and_memory`` while this server has
      disk edits for the project that Desktop has not reloaded: a save writes
      Desktop's OLD in-memory copy over them (J-02). ``overwrite_disk_edits``
      overrides.
    - Never reports ``already_saved`` on Desktop's intent flag alone (J-07): the
      fast path needs the flag false AND no pending live-model write in the
      ledger; ``already_saved`` and ``saved`` are reported separately.
    - The disk baseline is taken BEFORE the save is triggered (J-08).
    """
    early = _budget_error(deadline, "the save", 5.0)
    if early:
        return early
    sel = _fresh_instance(pid, pbip_path, wait_seconds)
    if not sel.get("success"):
        return sel
    inst = sel["instance"]
    rpid = int(inst["pid"])
    dirty = assess_dirty(inst)
    reports, models = _instance_artifacts(inst)

    if dirty["pending_disk_edits"] and not overwrite_disk_edits:
        return _err(
            "conflict_pending_disk_and_memory",
            (
                "Refusing to save: this server wrote disk edits for this project that "
                "Desktop has not reloaded yet. A save writes Desktop's in-memory (OLD) "
                "copy over them. " + _RELOAD_RULE
            ),
            pid=rpid,
            pending_disk_edits=dirty["pending_disk_edits"],
            memory_state=dirty["verdict"],
            dirty_sources=dirty,
            hint=(
                "Memory is clean -> reload (17_Desktop_Bridge reload_and_screenshot) to apply "
                "the disk edits; no save needed."
                if dirty["verdict"] == "clean"
                else "Both disk edits and unsaved memory exist -> no ordering keeps both. "
                "Reload with discard_unsaved=true and redo the live-TOM edits, or save "
                "with overwrite_disk_edits=true and redo the disk edits."
            ),
        )

    if dirty["verdict"] == "clean":
        return {
            "success": True,
            "pid": rpid,
            "already_saved": True,
            "saved": False,
            "basis": ["hasUnsavedChanges=false", "no pending live-model writes in the ledger"],
            "message": "Nothing to save — Desktop reports clean and this server made no "
            "unsaved live-model write.",
        }

    try:
        from core.autonomous.ui_automation import PbiDesktopUIAutomation
    except Exception as e:  # pragma: no cover - defensive
        return _err("ui_automation_unavailable", f"Could not import UI automation: {e}")

    # Hand the UI automation the project path so it can verify the save ON DISK
    # (authoritative) instead of by window title (a null signal on PBIP). The
    # file Desktop actually has open wins over the caller's (possibly folder)
    # path, which was only used to pick the instance.
    watch_path = inst.get("currentFilePath") or pbip_path
    required = "model" if dirty["ledger_live_writes"] else None
    uia = PbiDesktopUIAutomation(save_timeout=save_timeout)
    save_triggered_at = time.time()
    with _pid_lock(rpid):
        try:
            result = uia.save(rpid, pbip_path=watch_path, required_tree=required)
        except TypeError:
            # Older/injected implementations without the required_tree kwarg.
            result = uia.save(rpid, pbip_path=watch_path)
    invalidate_status_cache()
    if not result.get("success"):
        return _err(
            result.get("error_type", "save_failed"),
            result.get("error", "save failed"),
            pid=rpid,
            **{k: v for k, v in result.items() if k not in ("success", "error", "error_type")},
        )
    details = {k: v for k, v in result.items() if k != "success"}

    # DISK WINS. If the bytes on disk changed (after the trigger) and settled,
    # the save landed -- regardless of what hasUnsavedChanges says. That flag is
    # Desktop's intent marker, not a receipt.
    if result.get("verified") == "disk":
        edit_ledger.clear_tom(reports, models)
        _journal_flush(reports, models, before=save_triggered_at)
        if overwrite_disk_edits:
            edit_ledger.clear_disk(reports, models, kinds=("pbir", "tmdl"))
        out = {"success": True, "pid": rpid, "saved": True, "verified_clean": True, **details}
        # BR-03: a save that landed on disk must clear Desktop's flag. When it does not, the
        # flag is stuck on this pid (Desktop 2.157.627.0 defect): remember and say so.
        flag_after = _verify_saved_clean(rpid, pbip_path, wait_seconds)
        out["has_unsaved_changes_after_save"] = None if flag_after is None else (not flag_after)
        if flag_after is False:
            note_flag_stuck(rpid, file=inst.get("currentFilePath"))
            out["flag_stuck"] = True
            out["dirty_state_source"] = "ledger"
            out["suggestion"] = _FLAG_STUCK_SUGGESTION
            out["warning"] = (
                "The save is verified on disk but Desktop's hasUnsavedChanges flag is still "
                "true (flag_stuck). " + _FLAG_STUCK_SUGGESTION
            )
        else:
            if flag_after is True:
                clear_flag_stuck(rpid)
            out["dirty_state_source"] = "bridge_flag+ledger"
        return out

    verified = _verify_saved_clean(rpid, pbip_path, wait_seconds)
    if verified is False:
        return _err(
            "save_unverified",
            "The save was triggered but nothing changed on disk and Desktop still "
            "reports hasUnsavedChanges=true — the save did NOT land. "
            "DO NOT RETRY blindly: the bridge protocol has no save method; Save lives "
            "in the native WPF shell. ASK THE USER to press Ctrl+S, then confirm on "
            "DISK (the .SemanticModel/ and .Report/ trees newer than before the save "
            "and stable ~1.5s).",
            pid=rpid,
            **details,
        )
    out = {"success": True, "pid": rpid, "saved": True, "verified_clean": verified, **details}
    out["ledger_not_cleared"] = True
    out["warning"] = (
        "Save not verified on disk (no project tree to watch); live-model writes stay "
        "in the ledger, so a later reload still refuses until the save is confirmed "
        "on disk or discard_unsaved=true is passed."
    )
    return out


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------
def op_reload_and_screenshot(
    *,
    page: str,
    output: str,
    pid: Optional[int] = None,
    pbip_path: Optional[str] = None,
    wait_seconds: Optional[float] = None,
    timeout: float = DEFAULT_TIMEOUT,
    optimize: bool = True,
    per_visual: bool = False,
    report_root: Optional[str] = None,
    crops_dir: Optional[str] = None,
    save_first: bool = False,
    scale: Optional[float] = None,
    settle_ms: Optional[int] = DEFAULT_RELOAD_SETTLE_MS,
    render_stable: bool = False,
    discard_unsaved: bool = False,
    overwrite_disk_edits: bool = False,
    include_model: bool = False,
    vision_tier: str = "high",
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Reload + settle + screenshot in ONE call — the standard verify step
    after a PBIR disk edit.

    The reload refuses on unsaved work exactly like ``op_reload`` (``include_model``
    is passed through: classified model reload, see ``op_reload``); a rejected
    or failed reload never takes a screenshot (it would show the stale canvas).
    The per-PID lock is held across both steps (J-14). ``settle_ms`` (default
    2000) waits for the canvas to render before capturing. The reload envelope
    is attached under ``reload``. All arguments are validated BEFORE the
    reload, so a bad value never reloads Desktop.
    """
    _, scale_err = _scale_args(scale)
    if scale_err:
        return scale_err
    _, settle_err = _ms_value("settle_ms", settle_ms)
    if settle_err:
        return settle_err
    path_err = _path_arg_error("output", output)
    if path_err:
        return path_err

    lock_pid = pid
    if lock_pid is None:
        sel = _resolve_pid_instance(pid, pbip_path, wait_seconds)
        if not sel.get("success"):
            return sel
        lock_pid = int(sel["instance"]["pid"])
    with _pid_lock(int(lock_pid)):
        reload_res = op_reload(
            pid=int(lock_pid),
            pbip_path=pbip_path,
            wait_seconds=wait_seconds,
            timeout=timeout,
            save_first=save_first,
            discard_unsaved=discard_unsaved,
            overwrite_disk_edits=overwrite_disk_edits,
            include_model=include_model,
            deadline=deadline,
        )
        if not reload_res.get("success"):
            return reload_res
        shot = op_screenshot(
            page=page,
            output=output,
            pid=int(lock_pid),
            pbip_path=pbip_path,
            wait_seconds=wait_seconds,
            timeout=timeout,
            optimize=optimize,
            per_visual=per_visual,
            report_root=report_root,
            crops_dir=crops_dir,
            scale=scale,
            settle_ms=settle_ms,
            render_stable=render_stable,
            vision_tier=vision_tier,
            deadline=deadline,
        )
    shot["reload"] = reload_res
    return shot


def op_resolve_page(
    *,
    page: str,
    pid: Optional[int] = None,
    pbip_path: Optional[str] = None,
    wait_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    sel = _resolve_pid_instance(pid, pbip_path, wait_seconds)
    if not sel.get("success"):
        return sel
    inst = sel["instance"]
    pr = resolve_page(inst, page)
    if pr["ambiguous"]:
        return _err(
            "ambiguous_page",
            f"Page {page!r} matches {len(pr['ambiguous'])} pages; pass the page id.",
            candidates=pr["ambiguous"],
        )
    if not pr["page_id"]:
        return _err(
            "page_not_found",
            f"Page {page!r} not found.",
            available=[p.get("displayName") for p in inst.get("pages") or []],
            **_pages_error_extra(inst),
        )
    out = {"success": True, "pid": inst["pid"], "page": page, "page_id": pr["page_id"]}
    if pr["match"] not in ("id", "display_name"):
        out["page_match"] = pr["match"]
    return out


# ---------------------------------------------------------------------------
# open — launch Desktop on a project (J-32(2))
# ---------------------------------------------------------------------------
# `open` polls status this often / at most this long after the CLI returns (BR-08 finding:
# the 1.0.0 CLI returned ANOTHER running instance's pid before the new window was ready).
_OPEN_POLL_INTERVAL_S = 1.0
_OPEN_VERIFY_MAX_S = 120.0


def _find_opened_instance(p: Path, known_pids: set, budget_s: float) -> Optional[Dict[str, Any]]:
    """The NEW connected instance whose ``currentFilePath`` is *p*, polled within *budget_s*."""
    end = time.monotonic() + budget_s
    while True:
        st = op_status(wait_seconds=0)
        if st.get("success"):
            for inst in st.get("instances") or []:
                if (
                    isinstance(inst, dict)
                    and inst.get("bridgeStatus") == "connected"
                    and inst.get("pid") not in known_pids
                    and _instance_matches_path(inst, str(p))
                ):
                    return inst
        if time.monotonic() >= end:
            return None
        time.sleep(_OPEN_POLL_INTERVAL_S)


def op_open(
    *,
    pbip_path: str,
    timeout_seconds: float = 60.0,
    allow_duplicate: bool = False,
    wait_seconds: Optional[float] = 0.0,
) -> Dict[str, Any]:
    """Open a ``.pbip`` / ``.pbix`` in a NEW Power BI Desktop and wait for its bridge.

    Completes the "close WITHOUT saving -> reopen" lane that is the only way
    to apply partition / M / expression TMDL disk edits (a metadata-only edit can
    use ``reload include_model=true``). It never closes anything: refuses when a
    running instance already has this project open (``already_open``) unless
    ``allow_duplicate``.

    The pid the CLI returns is NOT trusted (the 1.0.0 CLI returned another,
    already-connected instance's pid before the new window was ready): after
    the CLI returns, status is polled (within ``timeout_seconds``) until a NEW
    connected instance whose ``currentFilePath`` is the requested file exists;
    only that instance's pid is returned. Otherwise ``pid`` is None,
    ``verified`` is False and the CLI's pid is reported as ``cli_reported_pid``.
    Only a verified open clears the project's ledger entries (the new process
    loaded exactly what is on disk).
    """
    err = _path_arg_error("pbip_path", pbip_path)
    if err:
        return err
    p = Path(pbip_path)
    if p.suffix.lower() not in (".pbip", ".pbix") or not p.is_file():
        return _err(
            "invalid_argument",
            f"open needs an existing .pbip or .pbix file; got {pbip_path!r}.",
        )
    known_pids: set = set()
    st = op_status(wait_seconds=wait_seconds or 0)
    if st.get("success"):
        for inst in st.get("instances") or []:
            if _instance_matches_path(inst, str(p)):
                if not allow_duplicate:
                    return _err(
                        "already_open",
                        f"pid={inst.get('pid')} already has {inst.get('currentFilePath')!r} "
                        "open. Close it first (WITHOUT saving if TMDL disk edits must "
                        "survive — ask the user; never close Desktop unprompted), or pass "
                        "allow_duplicate=true.",
                        pid=inst.get("pid"),
                    )
                known_pids.add(inst.get("pid"))
    try:
        t = float(timeout_seconds)
    except (TypeError, ValueError):
        return _err(
            "invalid_argument", f"timeout_seconds must be a number; got {timeout_seconds!r}."
        )
    res = run_bridge(["open", str(p.resolve()), "--timeout", str(int(max(1.0, t)))], timeout=t + 30)
    invalidate_status_cache()
    if not res["ok"]:
        return _cli_err(res)
    data = res["data"] if isinstance(res.get("data"), dict) else {}
    cli_pid = data.get("pid")
    opened = _find_opened_instance(p, known_pids, min(max(t, 1.0), _OPEN_VERIFY_MAX_S))
    invalidate_status_cache()
    out: Dict[str, Any] = {"success": True, "data": data}
    if opened is not None:
        reports, models = edit_ledger.project_artifacts(str(p))
        edit_ledger.clear_disk(reports, models, kinds=("pbir", "tmdl"))
        edit_ledger.clear_tom(reports, models)
        out.update(
            pid=opened.get("pid"),
            bridge_status=opened.get("bridgeStatus"),
            verified=True,
            current_file_path=opened.get("currentFilePath"),
        )
        if cli_pid is not None and cli_pid != opened.get("pid"):
            out["cli_reported_pid"] = cli_pid
            out["warning"] = (
                f"The CLI reported pid {cli_pid}, which is not the instance that has "
                f"{p.name} open; returning pid {opened.get('pid')} (currentFilePath verified)."
            )
        return out
    out.update(
        pid=None,
        bridge_status=(
            data.get("bridgeStatus") if data.get("bridgeStatus") != "connected" else "unverified"
        ),
        verified=False,
    )
    if cli_pid is not None:
        out["cli_reported_pid"] = cli_pid
    out["warning"] = (
        f"Desktop was launched, but no connected instance with currentFilePath == {p.name} "
        "appeared within the wait budget, so no pid is returned (the CLI's own pid is not "
        "trusted: it can be another running instance's). Call status in a moment and match "
        "by currentFilePath. The edit ledger was NOT cleared."
    )
    return out


# ---------------------------------------------------------------------------
# manifest — capability detection
# ---------------------------------------------------------------------------
_MANIFEST_METHOD_KEYS = ("name", "method")


def extract_manifest_methods(payload: Any) -> List[str]:
    """Best-effort flat list of RPC method names in a bridge manifest payload.

    The manifest shape is undocumented and varies by build, so this is tolerant
    rather than schema-driven: walk the whole structure and collect string values
    under any key named ``name``/``method``, plus plain-string entries of any
    list. Deduped + sorted. Empty list means "nothing recognizable found" — the
    caller must treat that as UNKNOWN, not as "no methods".
    """
    found: set = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if key in _MANIFEST_METHOD_KEYS and isinstance(val, str) and val.strip():
                    found.add(val.strip())
                _walk(val)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, str) and item.strip():
                    found.add(item.strip())
                else:
                    _walk(item)

    _walk(payload)
    return sorted(found)


def op_manifest(
    *,
    pid: Optional[int] = None,
    pbip_path: Optional[str] = None,
    wait_seconds: Optional[float] = 0.0,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Read the Desktop Bridge manifest — what the connected build exposes.

    This is the capability-probe op: the RPC surface differs across Desktop/CLI
    versions, so prefer asking over assuming. ``methods`` is a best-effort flat
    list of method names; when nothing recognizable is found the key is OMITTED
    and ``methods_unavailable=True`` is set instead (an empty list would read as
    a definite "no methods", which we cannot know).

    Explicit ``pid`` takes the fast path (no status pre-flight).
    """
    if pid is not None:
        rpid = int(pid)
    else:
        sel = _resolve_pid_instance(pid, pbip_path, wait_seconds)
        if not sel.get("success"):
            return sel
        rpid = sel["instance"]["pid"]

    argv = ["manifest", "--pid", str(rpid)]
    if wait_seconds and wait_seconds > 0:
        argv += ["--wait-seconds", str(int(wait_seconds))]
    with _pid_lock(rpid):
        res = run_bridge(argv, timeout=timeout)
    if not res["ok"]:
        return _cli_err(res, pid=rpid)

    data = res["data"]
    envelope: Dict[str, Any] = {"success": True, "pid": rpid, "data": data}
    methods = extract_manifest_methods(data)
    if methods:
        envelope["methods"] = methods
    else:
        envelope["methods_unavailable"] = True
    return _cli_meta(envelope)


_MANIFEST_CACHE: Dict[int, Dict[str, Any]] = {}
_MANIFEST_CACHE_LOCK = threading.Lock()


def invalidate_manifest_cache() -> None:
    """Drop all cached manifests (autouse-reset in tests)."""
    with _MANIFEST_CACHE_LOCK:
        _MANIFEST_CACHE.clear()


def _cached_manifest(
    pid: Optional[int], pbip_path: Optional[str], wait_seconds: Optional[float]
) -> Dict[str, Any]:
    """``op_manifest`` with a per-PID TTL cache.

    Purely additive: nothing invalidates it except the TTL / an explicit
    ``invalidate_manifest_cache()`` — a process's RPC surface can't change while
    it lives. Only successful results are cached. When ``pid`` is None the
    resolved pid from the result is used as the cache key.
    """
    now = time.monotonic()
    if pid is not None:
        with _MANIFEST_CACHE_LOCK:
            entry = _MANIFEST_CACHE.get(int(pid))
            if entry and now - entry["t"] < MANIFEST_CACHE_TTL:
                return entry["result"]
    res = op_manifest(pid=pid, pbip_path=pbip_path, wait_seconds=wait_seconds)
    if res.get("success") and res.get("pid") is not None:
        with _MANIFEST_CACHE_LOCK:
            _MANIFEST_CACHE[int(res["pid"])] = {"t": time.monotonic(), "result": res}
    return res


def bridge_supports(
    method: str, *, pid: Optional[int] = None, pbip_path: Optional[str] = None
) -> Optional[bool]:
    """Does the connected bridge expose ``method``?

    Returns True/False when the manifest's method list was discoverable, and
    ``None`` for UNKNOWN — manifest unavailable, unparseable, or the op failed.
    Never guesses: a caller must be able to distinguish "not supported" from
    "couldn't tell".
    """
    res = _cached_manifest(pid, pbip_path, 0.0)
    if not res.get("success"):
        return None
    methods = res.get("methods")
    if not methods:
        return None
    return method in methods


# Re-exported for the rewired debug loop / callers that build their own paths.
__all__ = [
    "CLI_NAME",
    "DEFAULT_TIMEOUT",
    "STATUS_CACHE_TTL",
    "MANIFEST_CACHE_TTL",
    "SETTLE_RETRY_SECONDS",
    "DEFAULT_RELOAD_SETTLE_MS",
    "MIN_DESKTOP_VERSION",
    "MIN_DESKTOP_HINT",
    "SCALE_MIN",
    "SCALE_MAX",
    "run_bridge",
    "run_bridge_lib",
    "reset_cli_cache",
    "note_flag_stuck",
    "clear_flag_stuck",
    "flag_stuck_info",
    "select_instance",
    "resolve_page",
    "resolve_page_id",
    "assess_dirty",
    "invalidate_status_cache",
    "invalidate_manifest_cache",
    "extract_manifest_methods",
    "bridge_supports",
    "op_manifest",
    "op_status",
    "op_reload",
    "op_save",
    "op_open",
    "op_screenshot",
    "op_screenshot_all",
    "op_reload_and_screenshot",
    "op_resolve_page",
    "BridgeError",
    "BridgeNotConnected",
    "BridgeAmbiguous",
    "BridgePathMismatch",
]
