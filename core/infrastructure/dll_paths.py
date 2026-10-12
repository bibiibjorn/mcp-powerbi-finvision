"""
Shared DLL path resolution for .NET assemblies (AMO / TOM / ADOMD.NET).

A .NET process binds ONE build of an assembly for its whole lifetime: whoever
calls ``clr.AddReference`` first wins and later references to another copy are
silently ignored. Every loader in this repo therefore goes through
:func:`get_dll_paths`, which picks exactly one folder for the whole process.

Selection (2026-09-22 deep review, B-07 / K-09):

* Candidates: ``$MCP_PBI_AMO_DIR`` (explicit override, always wins when usable),
  the bundled ``lib/dotnet``, DAX Studio, Tabular Editor 2/3, SSMS 18-21.
* A folder is *suitable* when all four assemblies exist AND they target
  ``.NETFramework`` — pythonnet here runs on netfx, and a ``.NETCoreApp`` build
  (e.g. Tabular Editor 3) fails with ``TypeLoadException System.ComponentModel
  .Component``.
* Among suitable folders the one with the highest ``Microsoft.AnalysisServices
  .Tabular`` AssemblyVersion wins (bundled 19.101 beats DAX Studio 19.84).

The previous rule preferred DAX Studio because "the bundled build is the .NET
Core flavour whose trace never raises events". The DLL metadata contradicts that
premise — the bundled build is .NET Framework 4.7.2 and *newer*. The 2026-09-02
"0 events vs 10 events" observation is more plausibly explained by MIXED builds
(query_executor loaded the bundled build, trace_session then AddReference'd DAX
Studio's copy). Now every loader uses the same folder. **Needs live check**: if a
trace on the bundled build still delivers no events, ``04_Run_DAX mode=trace``
reports ``trace_unavailable`` naming the loaded build; pin DAX Studio with
``MCP_PBI_AMO_DIR=C:\\Program Files\\DAX Studio\\bin`` and restart the server.
"""

import logging
import os
import re
import threading
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

# Project root: go up from core/infrastructure/ to project root
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.dirname(_THIS_DIR)
PROJECT_ROOT = os.path.dirname(_CORE_DIR)
DLL_FOLDER = os.path.join(PROJECT_ROOT, "lib", "dotnet")

#: Environment override: a folder holding all four AMO/ADOMD assemblies.
AMO_DIR_ENV = "MCP_PBI_AMO_DIR"


class DllPaths(NamedTuple):
    """Paths to all .NET assemblies used by the server."""

    dll_folder: str
    core_dll: str
    amo_dll: str
    tabular_dll: str
    adomd_dll: str


_AMO_DLL_NAMES = (
    "Microsoft.AnalysisServices.Core.dll",
    "Microsoft.AnalysisServices.dll",
    "Microsoft.AnalysisServices.Tabular.dll",
    "Microsoft.AnalysisServices.AdomdClient.dll",
)

#: Known install locations of AMO/ADOMD client libraries (besides lib/dotnet).
_LOCAL_PROGRAMS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")

KNOWN_AMO_DIRS: Tuple[Tuple[str, str], ...] = (
    # Per-user portable installs (no admin rights needed), junctions to versioned folders.
    ("dax_studio_portable", os.path.join(_LOCAL_PROGRAMS, "DaxStudio", "bin")),
    ("tabular_editor_3_portable", os.path.join(_LOCAL_PROGRAMS, "TabularEditor3")),
    ("dax_studio", r"C:\Program Files\DAX Studio\bin"),
    ("dax_studio_x86", r"C:\Program Files (x86)\DAX Studio\bin"),
    ("tabular_editor_3", r"C:\Program Files\Tabular Editor 3"),
    ("tabular_editor_2", r"C:\Program Files (x86)\Tabular Editor"),
    ("tabular_editor_2_x64", r"C:\Program Files\Tabular Editor"),
    ("ssms_21", r"C:\Program Files\Microsoft SQL Server Management Studio 21\Release\Common7\IDE"),
    ("ssms_20", r"C:\Program Files (x86)\Microsoft SQL Server Management Studio 20\Common7\IDE"),
    ("ssms_19", r"C:\Program Files (x86)\Microsoft SQL Server Management Studio 19\Common7\IDE"),
    ("ssms_18", r"C:\Program Files (x86)\Microsoft SQL Server Management Studio 18\Common7\IDE"),
)

#: Kept for backward compatibility (imported by older call sites/tests).
TRACE_CAPABLE_DLL_DIRS = tuple(p for k, p in KNOWN_AMO_DIRS if k.startswith("dax_studio"))

_ASSEMBLY_VERSION_KEY = "Assembly Version".encode("utf-16-le") + b"\x00\x00"
_FILE_VERSION_KEY = "FileVersion".encode("utf-16-le") + b"\x00\x00"
_VERSION_VALUE_RE = re.compile(rb"((?:[0-9]\x00|\.\x00)+)")
_TFM_RE = re.compile(rb"\.(NETFramework|NETCoreApp|NETStandard),Version=v([\d.]+)")


def _read_resource_version(blob: bytes, key: bytes) -> Optional[str]:
    idx = blob.find(key)
    if idx < 0:
        return None
    match = _VERSION_VALUE_RE.search(blob[idx + len(key) : idx + len(key) + 96])
    if not match:
        return None
    try:
        return match.group(1).decode("utf-16-le").strip(".") or None
    except UnicodeDecodeError:
        return None


#: P-16: the version resource and the TargetFrameworkAttribute blob sit in the last
#: ~125 KB of the AMO/ADOMD DLLs; the scan reads only this tail first (22 ms vs
#: 53 ms for the 34.5 MB of all candidate folders) and falls back to the whole
#: file when anything it looks for is missing.
_TAIL_BYTES = 384 * 1024


def _scan_assembly_blob(blob: bytes) -> Tuple[Optional[str], Optional[str], Any]:
    return (
        _read_resource_version(blob, _ASSEMBLY_VERSION_KEY),
        _read_resource_version(blob, _FILE_VERSION_KEY),
        _TFM_RE.search(blob),
    )


def inspect_assembly(path: str) -> Dict[str, Any]:
    """Read AssemblyVersion / FileVersion / TargetFramework WITHOUT loading the DLL.

    The Win32 version resource csc emits carries an "Assembly Version" string and
    the TargetFrameworkAttribute blob carries ``.NETFramework,Version=v4.7.2``;
    both are plain byte scans, so this is safe to call before (or instead of)
    touching the CLR.
    """
    info: Dict[str, Any] = {"path": path, "exists": os.path.exists(path)}
    if not info["exists"]:
        return info
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > _TAIL_BYTES:
                fh.seek(size - _TAIL_BYTES)
            blob = fh.read()
            assembly_version, file_version, tfm = _scan_assembly_blob(blob)
            if size > _TAIL_BYTES and (
                assembly_version is None or file_version is None or tfm is None
            ):
                fh.seek(0)  # not in the tail: scan the whole file like before
                assembly_version, file_version, tfm = _scan_assembly_blob(fh.read())
    except OSError as e:
        info["error"] = str(e)
        return info
    info["assembly_version"] = assembly_version
    info["file_version"] = file_version
    if tfm:
        info["target_framework"] = f".{tfm.group(1).decode()},Version=v{tfm.group(2).decode()}"
        info["framework"] = tfm.group(1).decode()
    return info


def _version_key(version: Optional[str]) -> Tuple[int, ...]:
    if not version:
        return (0,)
    parts: List[int] = []
    for piece in version.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def inspect_folder(folder: str, label: str = "") -> Dict[str, Any]:
    """Suitability report for one candidate folder."""
    report: Dict[str, Any] = {"label": label, "folder": folder, "suitable": False}
    if not folder or not os.path.isdir(folder):
        report["reason"] = "folder not found"
        return report
    assemblies = {name: inspect_assembly(os.path.join(folder, name)) for name in _AMO_DLL_NAMES}
    missing = [n for n, a in assemblies.items() if not a.get("exists")]
    if missing:
        report["reason"] = "missing " + ", ".join(missing)
        return report
    frameworks = {a.get("framework") for a in assemblies.values()}
    tabular = assemblies["Microsoft.AnalysisServices.Tabular.dll"]
    report["version"] = tabular.get("assembly_version")
    report["file_version"] = tabular.get("file_version")
    report["target_framework"] = tabular.get("target_framework")
    report["assemblies"] = {
        n: {
            "assembly_version": a.get("assembly_version"),
            "target_framework": a.get("target_framework"),
        }
        for n, a in assemblies.items()
    }
    non_netfx = sorted(str(f) for f in frameworks if f not in ("NETFramework", None))
    if non_netfx:
        report["reason"] = (
            f"targets {', '.join(non_netfx)}; pythonnet runs on .NET Framework here "
            "(a .NETCoreApp AMO fails with TypeLoadException System.ComponentModel.Component)"
        )
        return report
    report["suitable"] = True
    return report


def _candidate_folders() -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    override = os.environ.get(AMO_DIR_ENV)
    if override:
        candidates.append(("env_override", override))
    candidates.append(("bundled", DLL_FOLDER))
    # Versioned bundles (lib/dotnet/amo-<version>/): newer AMO builds ship side by side
    # so a running server that holds lib/dotnet/*.dll locked never blocks an upgrade.
    try:
        for entry in sorted(os.listdir(DLL_FOLDER)):
            sub = os.path.join(DLL_FOLDER, entry)
            if entry.lower().startswith("amo-") and os.path.isdir(sub):
                candidates.append((f"bundled_{entry[4:]}", sub))
    except OSError:
        pass
    candidates.extend(KNOWN_AMO_DIRS)
    return candidates


_selection_lock = threading.Lock()
_selection: Optional[Dict[str, Any]] = None


def select_amo_folder(refresh: bool = False) -> Dict[str, Any]:
    """Pick the AMO folder for this process (cached; the CLR binds only once anyway).

    Returns ``{folder, label, version, reason, candidates: [...]}``.
    """
    global _selection
    with _selection_lock:
        if _selection is not None and not refresh:
            return _selection
        reports = [inspect_folder(folder, label) for label, folder in _candidate_folders()]
        suitable = [r for r in reports if r.get("suitable")]
        chosen: Optional[Dict[str, Any]] = None
        reason = ""
        override = next((r for r in suitable if r["label"] == "env_override"), None)
        if override is not None:
            chosen, reason = override, f"{AMO_DIR_ENV} override"
        elif suitable:
            chosen = max(suitable, key=lambda r: _version_key(r.get("version")))
            reason = "newest suitable .NET Framework AMO build"
        if chosen is None:
            _selection = {
                "folder": DLL_FOLDER,
                "label": "bundled",
                "version": None,
                "reason": "no suitable AMO folder found; defaulting to bundled lib/dotnet",
                "candidates": reports,
            }
        else:
            _selection = {
                "folder": chosen["folder"],
                "label": chosen["label"],
                "version": chosen.get("version"),
                "reason": reason,
                "candidates": reports,
            }
        logger.info(
            "AMO/ADOMD folder selected: %s (%s, version %s)",
            _selection["folder"],
            _selection["reason"],
            _selection["version"],
        )
        return _selection


def get_trace_capable_dll_folder() -> Optional[str]:
    """Deprecated alias: the folder every loader (incl. traces) now shares."""
    return select_amo_folder().get("folder")


def get_dll_paths() -> DllPaths:
    """Resolved paths of the ONE AMO/ADOMD build this process uses."""
    folder = select_amo_folder().get("folder") or DLL_FOLDER
    return DllPaths(
        dll_folder=folder,
        core_dll=os.path.join(folder, "Microsoft.AnalysisServices.Core.dll"),
        amo_dll=os.path.join(folder, "Microsoft.AnalysisServices.dll"),
        tabular_dll=os.path.join(folder, "Microsoft.AnalysisServices.Tabular.dll"),
        adomd_dll=os.path.join(folder, "Microsoft.AnalysisServices.AdomdClient.dll"),
    )


def load_amo_assemblies():
    """Load AMO/TOM assemblies into CLR. Returns True if at least one DLL was loaded."""
    try:
        import clr

        paths = get_dll_paths()
        loaded = 0
        for dll in (paths.core_dll, paths.amo_dll, paths.tabular_dll):
            if os.path.exists(dll):
                clr.AddReference(dll)
                loaded += 1
        if loaded == 0:
            logger.warning("No AMO/TOM DLLs found to load")
            return False
        return True
    except Exception as e:
        logger.warning(f"AMO/TOM assemblies not available: {e}")
        return False


def load_adomd_assembly():
    """Load ADOMD.NET assembly into CLR. Returns True if successful."""
    try:
        import clr

        paths = get_dll_paths()
        if os.path.exists(paths.adomd_dll):
            clr.AddReference(paths.adomd_dll)
            return True
        return False
    except Exception as e:
        logger.warning(f"ADOMD.NET assembly not available: {e}")
        return False


def get_trace_runner_path() -> Optional[str]:
    """Get path to native DaxExecutor trace runner exe, or None if not built."""
    base = os.path.join(_THIS_DIR, "dax_executor", "bin", "Release")
    for subdir in ("net8.0-windows/win-x64", "net8.0-windows", "net8.0"):
        path = os.path.join(base, subdir, "DaxExecutor.exe")
        if os.path.exists(path):
            return path
    return None


def get_loaded_amo_assemblies() -> List[Dict[str, Any]]:
    """Microsoft.AnalysisServices* assemblies actually loaded in this CLR."""
    loaded: List[Dict[str, Any]] = []
    try:
        import clr  # noqa: F401
        from System import AppDomain  # type: ignore
    except Exception:
        return loaded
    try:
        for asm in AppDomain.CurrentDomain.GetAssemblies():
            try:
                name = asm.GetName()
                simple = str(name.Name)
                if not simple.startswith("Microsoft.AnalysisServices"):
                    continue
                try:
                    location = str(asm.Location)
                except Exception:  # noqa: BLE001 — dynamic assemblies have no Location
                    location = None
                loaded.append({"name": simple, "version": str(name.Version), "location": location})
            except Exception:  # noqa: BLE001
                continue
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not enumerate loaded assemblies: %s", e)
    return sorted(loaded, key=lambda a: a["name"])


def get_amo_info(include_candidates: bool = False) -> Dict[str, Any]:
    """Selected AMO folder + the assemblies really loaded (for 01_Connection / 17 manifest).

    ``mixed_builds`` is True when two versions of the same assembly name (or
    assemblies from two folders) are loaded — the classic cause of silent trace
    and TOM surface failures.
    """
    selection = select_amo_folder()
    loaded = get_loaded_amo_assemblies()
    folders = {os.path.dirname(a["location"]).lower() for a in loaded if a.get("location")}
    names = [a["name"] for a in loaded]
    info: Dict[str, Any] = {
        "selected_folder": selection.get("folder"),
        "selected_label": selection.get("label"),
        "selected_version": selection.get("version"),
        "selection_reason": selection.get("reason"),
        "loaded": loaded,
        "mixed_builds": len(folders) > 1 or len(names) != len(set(names)),
    }
    if include_candidates:
        info["candidates"] = [
            {
                k: v
                for k, v in c.items()
                if k in ("label", "folder", "suitable", "version", "target_framework", "reason")
            }
            for c in selection.get("candidates", [])
        ]
    return info
