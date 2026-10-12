"""Routed folders: which Claude Code project folders send their traffic through the gateway.

Routing a folder writes ``ANTHROPIC_BASE_URL`` into ``<folder>/.claude/settings.local.json`` (the
per-user, per-project settings file of Claude Code), plus the settings that close the channels to
Anthropic that IGNORE ``ANTHROPIC_BASE_URL`` (see ``PRIVACY_ENV`` / ``PRIVACY_SETTINGS``), and
nothing else:

* other keys of that file are preserved; an earlier ``ANTHROPIC_BASE_URL`` and an earlier value of
  any privacy key is remembered and restored by ``unroute_folder`` (a key that was absent is
  removed again);
* the file is kept out of git WITHOUT touching tracked files: when ``git check-ignore`` says it is
  not ignored, one line is appended to the repository's ``info/exclude``;
* the registry ``<home>/gateway_folders.json`` records the routed folders (used to start the
  gateway with the MCP server and to undo the routing precisely).

A routed folder is a statement about WHERE the traffic goes, not about the data in it: whether a
session is masked is the per-session mode of the gateway. Since 2026-10-08 a folder is routed ON
DEMAND: the user's typed "masking on" routes the chat's project folder and records the chat on it
(``add_session``), "masking off" drops it again (``drop_session``) and the last one unroutes the
folder (``client_gateway.switch``). The gateway exits by itself when nothing is routed to it.

Unrouting never DELETES ``ANTHROPIC_BASE_URL``: a running Claude Code session re-reads the ``env``
of its settings files when they change, but a REMOVED key stays set in it until a relaunch (Claude
Code env-vars docs), so it would keep talking to a gateway that is about to stop. The key is set to
the direct address instead (the value it had, else the project / user setting, else
``DIRECT_URL``), and the running session follows at once.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from client_gateway.control_client import gateway_url, home, is_gateway_url

REGISTRY_NAME = "gateway_folders.json"
ENV_KEY = "ANTHROPIC_BASE_URL"
DIRECT_URL = "https://api.anthropic.com"
# Claude Code channels to Anthropic that do not follow ANTHROPIC_BASE_URL and so never pass the
# gateway: the /feedback, /bug and /share transcript uploads, the session survey (transcript
# share). Switched off in a routed folder. NOT CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC (alone) and
# NOT DISABLE_TELEMETRY together with ANTHROPIC_BASE_URL: both switch off feature-flag evaluation,
# which took the Artifact tool away in EVERY chat (bisected 2026-10-02 with headless sessions).
# Usage metrics never hold code, prompts or file paths (Claude Code data-usage docs); the two
# feedback flags stop the transcript uploads. In a masked chat the Artifact tools are blocked by
# the hook instead (they publish outside the gateway).
PRIVACY_ENV = {
    "DISABLE_FEEDBACK_COMMAND": "1",
    "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
}
# Top-level (not env) settings: WebFetch otherwise sends the requested HOSTNAME straight to
# api.anthropic.com as a domain-safety check, with the real value the gateway has just restored.
PRIVACY_SETTINGS = {"skipWebFetchPreflight": True}
SETTINGS_REL = (".claude", "settings.local.json")
EXCLUDE_LINE = ".claude/settings.local.json"
_LOCK = threading.Lock()


class FolderError(ValueError):
    """The folder cannot be routed / unrouted (the message never holds file content)."""


# ------------------------------------------------------------------------------ small helpers
def _resolve(path: Any, must_exist: bool = True) -> Path:
    if not isinstance(path, (str, os.PathLike)) or not str(path).strip():
        raise FolderError("folder_path is required")
    folder = Path(os.path.abspath(os.path.expanduser(str(path).strip())))
    if must_exist and not folder.is_dir():
        raise FolderError("folder does not exist or is not a directory")
    return folder


def _key(folder: Path) -> str:
    return os.path.normcase(os.path.normpath(str(folder)))


def settings_path(folder: Path) -> Path:
    return folder.joinpath(*SETTINGS_REL)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_settings(path: Path) -> Tuple[Dict[str, Any], bool, str]:
    """(data, existed, newline). Invalid JSON is an error: a file we cannot parse is never
    overwritten."""
    if not path.is_file():
        return {}, False, "\n"
    raw = path.read_bytes().decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in raw else "\n"
    if not raw.strip():
        return {}, True, newline
    try:
        data = json.loads(raw)
    except ValueError:
        raise FolderError("settings.local.json is not valid JSON; fix or remove it first") from None
    if not isinstance(data, dict):
        raise FolderError("settings.local.json is not a JSON object")
    return data, True, newline


def _apply_privacy(
    data: Dict[str, Any], env: Dict[str, Any], remembered: Dict[str, Any]
) -> Dict[str, Any]:
    """Set the privacy keys; ``remembered`` holds, per key, whether it existed and its value (the
    first route wins, so a second route never records our own value as the user's)."""
    memo = dict(remembered)
    for name, value in PRIVACY_ENV.items():
        memo.setdefault("env." + name, {"had": name in env, "previous": env.get(name)})
        env[name] = value
    for name, value in PRIVACY_SETTINGS.items():
        memo.setdefault("settings." + name, {"had": name in data, "previous": data.get(name)})
        data[name] = value
    return memo


def _restore_privacy(data: Dict[str, Any], env: Dict[str, Any], remembered: Dict[str, Any]) -> bool:
    """Undo ``_apply_privacy`` for the keys that still hold OUR value (a value the user changed
    since is theirs). Returns True when something was restored or removed."""
    changed = False
    for scope, container, ours in (
        ("env.", env, PRIVACY_ENV),
        ("settings.", data, PRIVACY_SETTINGS),
    ):
        for name, value in ours.items():
            note = remembered.get(scope + name)
            if not isinstance(note, dict) or container.get(name) != value:
                continue
            if note.get("had") and note.get("previous") != value:
                container[name] = note.get("previous")
            elif note.get("had"):
                continue  # it was already exactly this: leave it as the user had it
            else:
                del container[name]
            changed = True
    return changed


def _dump(data: Dict[str, Any], newline: str) -> str:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if newline != "\n":
        text = text.replace("\n", newline)
    return text + newline


# ------------------------------------------------------------------------------ registry
def registry_path(base: Optional[Path] = None) -> Path:
    return (base or home()) / REGISTRY_NAME


def _load_registry(base: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(registry_path(base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    folders = data.get("folders") if isinstance(data, dict) else None
    if not isinstance(folders, dict):
        return {}
    return {k: v for k, v in folders.items() if isinstance(v, dict)}


def _save_registry(folders: Dict[str, Dict[str, Any]], base: Optional[Path] = None) -> None:
    _atomic_write(
        registry_path(base), json.dumps({"version": 1, "folders": folders}, indent=2) + "\n"
    )


# ------------------------------------------------------------------------------ git
def _git(folder: Path, *args: str) -> Optional["subprocess.CompletedProcess[str]"]:
    try:
        return subprocess.run(  # noqa: S603,S607 - fixed argv
            ["git", "-C", str(folder), *args],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _is_ignored(folder: Path) -> Optional[bool]:
    # --no-index: judge the ignore rules alone, also for a file git already tracks
    proc = _git(folder, "check-ignore", "-q", "--no-index", "--", EXCLUDE_LINE)
    if proc is None or proc.returncode not in (0, 1):
        return None
    return proc.returncode == 0


def _is_tracked(folder: Path) -> bool:
    proc = _git(folder, "ls-files", "--error-unmatch", "--", EXCLUDE_LINE)
    return proc is not None and proc.returncode == 0


def _ensure_git_ignored(folder: Path) -> Dict[str, Any]:
    """Keep settings.local.json out of git without changing tracked files."""
    top = _git(folder, "rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0:
        return {"git": False}
    out: Dict[str, Any] = {"git": True, "exclude_added": None}
    ignored = _is_ignored(folder)
    if ignored is False:
        pre = _git(folder, "rev-parse", "--show-prefix")
        # info/exclude patterns are relative to the repository root: add the folder's sub-path
        prefix = pre.stdout.strip() if pre is not None and pre.returncode == 0 else ""
        line = prefix + EXCLUDE_LINE
        gp = _git(folder, "rev-parse", "--git-path", "info/exclude")
        if gp is not None and gp.returncode == 0 and gp.stdout.strip():
            target = Path(gp.stdout.strip())
            if not target.is_absolute():
                target = folder / target
            try:
                existing = target.read_text(encoding="utf-8") if target.is_file() else ""
                lines = [x.strip() for x in existing.splitlines()]
                if line not in lines:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    sep = "" if not existing or existing.endswith("\n") else "\n"
                    with open(target, "a", encoding="utf-8", newline="") as handle:
                        handle.write(sep + line + "\n")
                out["exclude_added"] = line
            except OSError:
                pass
        ignored = _is_ignored(folder)
    out["ignored"] = ignored
    if _is_tracked(folder):
        out["tracked"] = True
        out["warning"] = (
            ".claude/settings.local.json is tracked by git: ignoring it has no effect; "
            "run `git rm --cached` on it or it will show up in commits."
        )
    return out


def _loopback(value: str) -> bool:
    try:
        return (urlparse(value.strip()).hostname or "") in ("127.0.0.1", "localhost", "::1")
    except ValueError:
        return False


def _configured_url(path: Path) -> Optional[str]:
    """A remote ``ANTHROPIC_BASE_URL`` in a settings file, else None. A loopback address there is
    left out: it is a gateway (an earlier one, another port), and a session parked on it would
    talk to a process that may be gone. Never raises."""
    try:
        data, _, _ = _read_settings(path)
    except (FolderError, OSError):
        return None
    env = data.get("env")
    value = env.get(ENV_KEY) if isinstance(env, dict) else None
    if isinstance(value, str) and value.strip() and not _loopback(value):
        return value.strip()
    return None


def direct_url(folder: Optional[Path], previous: Any = None) -> str:
    """Where a session goes when it is NOT routed: the value our route replaced, else the
    project's shared settings, else the user settings, else ``DIRECT_URL``."""
    if isinstance(previous, str) and previous.strip() and not is_gateway_url(previous):
        return previous.strip()
    candidates = [folder / ".claude" / "settings.json"] if folder is not None else []
    candidates.append(user_settings_path())
    for path in candidates:
        value = _configured_url(path)
        if value:
            return value
    return DIRECT_URL


# ------------------------------------------------------------------------------ public API
def route_folder(path: Any, base: Optional[Path] = None) -> Dict[str, Any]:
    """Route a folder through the gateway. Idempotent. Raises FolderError."""
    folder = _resolve(path)
    url = gateway_url()
    target = settings_path(folder)
    with _LOCK:
        data, existed, newline = _read_settings(target)
        env = data.get("env")
        if env is None:
            env = {}
        elif not isinstance(env, dict):
            raise FolderError("settings.local.json 'env' is not an object")
        registry = _load_registry(base)
        entry = registry.get(_key(folder), {})
        previous = env.get(ENV_KEY)
        remembered = entry.get("previous_base_url")
        if isinstance(previous, str) and previous != url and not is_gateway_url(previous):
            remembered = previous  # somebody else's value: put it back on unroute
        env[ENV_KEY] = url
        privacy = _apply_privacy(data, env, entry.get("privacy") or {})
        data["env"] = env
        _atomic_write(target, _dump(data, newline))
        git = _ensure_git_ignored(folder)
        registry[_key(folder)] = {
            "path": str(folder),
            "settings": str(target),
            "created_file": bool(entry.get("created_file", not existed)),
            "previous_base_url": remembered,
            "privacy": privacy,
            "exclude_line": git.get("exclude_added") or entry.get("exclude_line"),
            "routed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sessions": list(entry.get("sessions") or []),
        }
        _save_registry(registry, base)
    return {
        "folder": str(folder),
        "settings_file": str(target),
        "base_url": url,
        "replaced_base_url": bool(remembered),
        "privacy": {"env": sorted(PRIVACY_ENV), "settings": sorted(PRIVACY_SETTINGS)},
        "git": git,
    }


def unroute_folder(path: Any, base: Optional[Path] = None) -> Dict[str, Any]:
    """Undo ``route_folder``: removes only our key (restoring one it replaced).

    Raises FolderError."""
    folder = _resolve(path, must_exist=False)
    target = settings_path(folder)
    with _LOCK:
        registry = _load_registry(base)
        entry = registry.get(_key(folder))
        result: Dict[str, Any] = {
            "folder": str(folder),
            "unrouted": False,
            "restored_base_url": False,
            "settings_removed": False,
            "was_registered": entry is not None,
        }
        if target.is_file():
            data, _, newline = _read_settings(target)
            env = data.get("env")
            current = env.get(ENV_KEY) if isinstance(env, dict) else None
            if (
                isinstance(env, dict)
                and ENV_KEY in env
                and (is_gateway_url(current) or entry is not None)
            ):
                previous = (entry or {}).get("previous_base_url")
                result["restored_base_url"] = isinstance(previous, str) and bool(previous)
                # never deleted: a running session keeps a removed key (module docstring)
                env[ENV_KEY] = direct_url(folder, previous)
                result["base_url"] = env[ENV_KEY]
                if entry is not None:
                    result["privacy_restored"] = _restore_privacy(
                        data, env, entry.get("privacy") or {}
                    )
                _atomic_write(target, _dump(data, newline))
                result["unrouted"] = True
            elif ENV_KEY in (env or {}):
                result["note"] = "ANTHROPIC_BASE_URL is not ours; left as it is"
        if entry is not None:
            del registry[_key(folder)]
            _save_registry(registry, base)
    return result


# ------------------------------------------------------------------------------ every chat
ALL_KEY = "*"


def user_settings_path() -> Path:
    """Claude Code's user settings (every project): ``$CLAUDE_CONFIG_DIR`` or ``~/.claude``."""
    root = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(root) if root else Path.home() / ".claude") / "settings.json"


def route_all(base: Optional[Path] = None) -> Dict[str, Any]:
    """Route EVERY Claude Code chat of this user through the gateway (user settings ``env``).
    Idempotent; an earlier base URL and privacy values are remembered. Raises FolderError."""
    url = gateway_url()
    target = user_settings_path()
    with _LOCK:
        data, existed, newline = _read_settings(target)
        env = data.get("env")
        if env is None:
            env = {}
        elif not isinstance(env, dict):
            raise FolderError("settings.json 'env' is not an object")
        registry = _load_registry(base)
        entry = registry.get(ALL_KEY, {})
        previous = env.get(ENV_KEY)
        remembered = entry.get("previous_base_url")
        if isinstance(previous, str) and previous != url and not is_gateway_url(previous):
            remembered = previous
        env[ENV_KEY] = url
        privacy = _apply_privacy(data, env, entry.get("privacy") or {})
        data["env"] = env
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, _dump(data, newline))
        registry[ALL_KEY] = {
            "path": ALL_KEY,
            "settings": str(target),
            "created_file": bool(entry.get("created_file", not existed)),
            "previous_base_url": remembered,
            "privacy": privacy,
            "routed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _save_registry(registry, base)
    return {
        "scope": "every chat",
        "settings_file": str(target),
        "base_url": url,
        "replaced_base_url": bool(remembered),
        "privacy": {"env": sorted(PRIVACY_ENV), "settings": sorted(PRIVACY_SETTINGS)},
    }


def unroute_all(base: Optional[Path] = None) -> Dict[str, Any]:
    """Undo ``route_all``: takes only our keys out of the user settings. Raises FolderError."""
    target = user_settings_path()
    with _LOCK:
        registry = _load_registry(base)
        entry = registry.get(ALL_KEY)
        result: Dict[str, Any] = {
            "scope": "every chat",
            "unrouted": False,
            "restored_base_url": False,
        }
        if target.is_file():
            data, _, newline = _read_settings(target)
            env = data.get("env")
            current = env.get(ENV_KEY) if isinstance(env, dict) else None
            if (
                isinstance(env, dict)
                and ENV_KEY in env
                and (is_gateway_url(current) or entry is not None)
            ):
                previous = (entry or {}).get("previous_base_url")
                result["restored_base_url"] = isinstance(previous, str) and bool(previous)
                # never deleted: a running session keeps a removed key (module docstring)
                ok = isinstance(previous, str) and previous and not is_gateway_url(previous)
                env[ENV_KEY] = previous.strip() if ok else DIRECT_URL
                result["base_url"] = env[ENV_KEY]
                if entry is not None:
                    _restore_privacy(data, env, entry.get("privacy") or {})
                _atomic_write(target, _dump(data, newline))
                result["unrouted"] = True
        if entry is not None:
            registry.pop(ALL_KEY)
            _save_registry(registry, base)
    return result


def routes_all(base: Optional[Path] = None) -> bool:
    """True when the user settings route every chat through the gateway. Never raises."""
    try:
        data, _, _ = _read_settings(user_settings_path())
    except (FolderError, OSError):
        return False
    env = data.get("env")
    return isinstance(env, dict) and is_gateway_url(env.get(ENV_KEY))


def list_folders(base: Optional[Path] = None) -> List[Dict[str, Any]]:
    """The registered folders, each with ``active`` = its settings file still routes to the
    gateway. Never raises."""
    out: List[Dict[str, Any]] = []
    for entry in _load_registry(base).values():
        folder = entry.get("path")
        if not isinstance(folder, str) or not folder or folder == ALL_KEY:
            continue  # "every chat" is reported by routes_all()
        active = False
        try:
            data, _, _ = _read_settings(settings_path(Path(folder)))
            env = data.get("env")
            active = isinstance(env, dict) and is_gateway_url(env.get(ENV_KEY))
        except (FolderError, OSError):
            pass
        out.append({"path": folder, "settings_file": entry.get("settings"), "active": active})
    out.sort(key=lambda x: x["path"].lower())
    return out


# ------------------------------------------------------------------------------ masked chats
def add_session(path: Any, session_id: str, base: Optional[Path] = None) -> int:
    """Record a chat with masking on in a routed folder; returns how many it now holds."""
    folder = _resolve(path, must_exist=False)
    with _LOCK:
        registry = _load_registry(base)
        entry = registry.get(_key(folder))
        if entry is None:
            return 0
        sessions = [s for s in (entry.get("sessions") or []) if isinstance(s, str)]
        if session_id and session_id not in sessions:
            sessions.append(session_id)
        entry["sessions"] = sessions
        _save_registry(registry, base)
        return len(sessions)


def drop_session(path: Any, session_id: Optional[str], base: Optional[Path] = None) -> int:
    """Forget a chat (``None``: every chat) of a folder; returns how many masked chats are left."""
    folder = _resolve(path, must_exist=False)
    with _LOCK:
        registry = _load_registry(base)
        entry = registry.get(_key(folder))
        if entry is None:
            return 0
        sessions = [s for s in (entry.get("sessions") or []) if isinstance(s, str)]
        sessions = [s for s in sessions if session_id is not None and s != session_id]
        entry["sessions"] = sessions
        _save_registry(registry, base)
        return len(sessions)


def routed_folder_of(cwd: Any, base: Optional[Path] = None) -> Optional[Path]:
    """The registered routed folder that holds ``cwd`` (itself or the closest parent). Never
    raises."""
    try:
        here = _resolve(cwd, must_exist=False)
    except FolderError:
        return None
    keys = {k for k, v in _load_registry(base).items() if k != ALL_KEY and isinstance(v, dict)}
    for folder in [here, *here.parents]:
        if _key(folder) in keys:
            return folder
    return None


def anything_routed(base: Optional[Path] = None) -> bool:
    """True while any folder (or every chat) still sends its traffic to the gateway: the gateway
    must keep running. Never raises."""
    try:
        return routes_all(base) or any(f["active"] for f in list_folders(base))
    except Exception:  # noqa: BLE001
        return True  # unsure: keep running
