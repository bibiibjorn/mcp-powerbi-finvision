r"""Path-traversal guard for handler-supplied file paths.

The MCP trust model here is "the user is the client" — but paths escaping the
intended workspace can still cause unpleasant surprises (accidental overwrites,
writes into `C:\Windows`, exfiltration of `%USERPROFILE%`). This module provides
a small, composable guard that handlers use at the boundary BEFORE passing a
path to ``open()`` / ``Path(...).write_text()`` / downstream library code.

Three allowed-root strategies, in priority order:

  1. **Explicit roots** declared by the MCP client via ``roots/list`` (read from
     ``server.roots.list_root_paths()``). This is the "correct" answer when
     available.
  2. **Workspace roots** — the PBIP folder path on ``ConnectionState``, plus
     the repo's own ``exports/``, ``output/``, and ``logs/`` dirs (legitimate
     server-owned write targets).
  3. **User profile / temp** — when #1 and #2 both come back empty, fall back
     to allowing ``%USERPROFILE%`` and ``%TEMP%``. A no-connection, no-roots
     tool call is inherently under-specified and an ACL this loose is better
     than a crash.

Strategy #1 and #2 are always preferred; #3 only kicks in if both are empty.
Callers can force strict mode by passing ``allow_profile_fallback=False``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PathGuardViolation(ValueError):
    """Raised when a path would escape the allowed roots."""


@dataclass(frozen=True)
class GuardResult:
    resolved: Path
    matched_root: Path


def _resolved_root_list(*, allow_profile_fallback: bool = True) -> List[Path]:
    """Collect the active allowed-root set from all three strategies."""
    roots: List[Path] = []

    # 1. Client-declared roots (MCP roots/list). Best signal when available.
    try:
        from server.roots import list_root_paths

        for raw in list_root_paths():
            try:
                p = Path(raw).resolve()
                if p.exists() and p.is_dir():
                    roots.append(p)
            except OSError:
                continue
    except Exception as e:  # noqa: BLE001
        logger.debug("client roots lookup failed: %s", e)

    # 2. Server-internal workspace. Connection's PBIP folder + repo-owned
    #    write targets. These paths exist for sure if we've connected.
    try:
        from core.infrastructure.connection_state import connection_state

        info = connection_state.get_pbip_info() or {}
        for key in ("pbip_folder_path", "file_full_path"):
            v = info.get(key)
            if not v:
                continue
            try:
                p = Path(v).resolve()
                # If file_full_path points at a .pbip FILE, the workspace is
                # the parent folder. If it's a folder, use it as-is.
                if p.exists() and p.is_file():
                    p = p.parent
                if p.exists() and p.is_dir():
                    roots.append(p)
            except OSError:
                continue
    except Exception as e:  # noqa: BLE001
        logger.debug("connection pbip lookup failed: %s", e)

    # Offline lane (01_Connection connect_folder): the project folder that holds the .pbip, the
    # .SemanticModel and the .Report is the workspace (F-11: a suite next to the model).
    try:
        from core.infrastructure import offline_model

        info = offline_model.info() if offline_model.is_active() else None
        if info and info.get("definition_folder"):
            definition = Path(info["definition_folder"])
            project = (
                definition.parent.parent
                if definition.name.lower() == "definition"
                else definition.parent
            )
            if project.exists() and project.is_dir():
                roots.append(project.resolve())
    except Exception as e:  # noqa: BLE001
        logger.debug("offline project root lookup failed: %s", e)

    # Headless CLI / CI: explicit extra roots (``python -m src.cli --root DIR``).
    for raw in (os.environ.get("FINVISION_ALLOWED_ROOTS") or "").split(os.pathsep):
        if raw.strip():
            try:
                p = Path(raw.strip()).resolve()
                if p.exists() and p.is_dir():
                    roots.append(p)
            except OSError:
                continue

    # Repo-owned write dirs — always writable by the server itself.
    try:
        repo_root = Path(__file__).resolve().parents[2]
        for sub in ("exports", "output", "logs"):
            target = repo_root / sub
            if target.exists() and target.is_dir():
                roots.append(target)
    except Exception:  # noqa: BLE001
        pass

    # 3. Fallback: user profile + temp. Only if nothing else landed.
    if not roots and allow_profile_fallback:
        for env_var in ("USERPROFILE", "HOME", "TEMP", "TMP"):
            v = os.environ.get(env_var)
            if v:
                try:
                    p = Path(v).resolve()
                    if p.exists() and p.is_dir():
                        roots.append(p)
                except OSError:
                    continue

    # Deduplicate while preserving order.
    seen = set()
    unique: List[Path] = []
    for r in roots:
        key = str(r).lower()  # Windows case-insensitive
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _is_inside(child: Path, parent: Path) -> bool:
    """Return True if ``child`` is ``parent`` or a descendant.

    Uses ``Path.relative_to`` under the hood. Case-insensitive on Windows
    by comparing lower-cased strings.
    """
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        pass
    # Windows case-insensitive fallback
    if os.name == "nt":
        try:
            c_str = str(child).lower()
            p_str = str(parent).lower()
            return c_str == p_str or c_str.startswith(p_str + os.sep)
        except Exception:  # noqa: BLE001
            return False
    return False


def check_path(
    candidate: str,
    *,
    extra_roots: Iterable[str] = (),
    allow_profile_fallback: bool = True,
    must_exist: bool = False,
    create_parents: bool = False,
) -> GuardResult:
    """Validate ``candidate`` resolves inside an allowed root.

    Args:
        candidate: the user-supplied path string (absolute or relative).
        extra_roots: additional allowed roots beyond the ambient set (e.g. a
            specific ``pbip_path`` the caller just verified).
        allow_profile_fallback: if False, disallow the user-profile fallback
            and error out if no explicit root is available.
        must_exist: if True, also require the path to exist before returning.
        create_parents: if True, create the parent directory on-demand if it
            doesn't yet exist (only inside an allowed root).

    Returns:
        GuardResult(resolved, matched_root) on success.

    Raises:
        PathGuardViolation if ``candidate`` resolves outside every allowed
            root, or if ``must_exist`` is True and the resolved path doesn't
            exist.
    """
    if not candidate:
        raise PathGuardViolation("empty path supplied")

    try:
        resolved = Path(candidate).expanduser().resolve()
    except (OSError, ValueError) as e:
        raise PathGuardViolation(f"path could not be resolved: {e}") from e

    roots: List[Path] = list(
        _resolved_root_list(
            allow_profile_fallback=allow_profile_fallback,
        )
    )
    for extra in extra_roots:
        if not extra:
            continue
        try:
            p = Path(extra).expanduser().resolve()
            if p.exists():
                # If extra points at a file, use its parent as the root.
                if p.is_file():
                    p = p.parent
                roots.append(p)
        except OSError:
            continue

    if not roots:
        raise PathGuardViolation(
            "no allowed roots are known for this request — "
            "pass extra_roots=[pbip_path] or declare workspace roots/list "
            "on the MCP client"
        )

    matched: Optional[Path] = None
    for r in roots:
        if _is_inside(resolved, r):
            matched = r
            break
    if matched is None:
        raise PathGuardViolation(
            f"path {resolved!s} escapes every allowed root "
            f"({', '.join(str(r) for r in roots[:4])}"
            f"{' …' if len(roots) > 4 else ''})"
        )

    if must_exist and not resolved.exists():
        raise PathGuardViolation(f"path does not exist: {resolved!s}")

    if create_parents:
        parent = resolved if resolved.is_dir() else resolved.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise PathGuardViolation(f"could not create parent directory {parent!s}: {e}") from e

    return GuardResult(resolved=resolved, matched_root=matched)


def guard_or_error(
    candidate: Optional[str],
    arg_name: str,
    *,
    extra_roots: Iterable[str] = (),
    **check_kwargs,
) -> Tuple[Optional[Path], Optional[dict]]:
    """Handler-friendly variant: returns ``(path, None)`` on success or
    ``(None, error_dict)`` on violation. No exceptions raised.

    Idiomatic usage at a handler boundary::

        path, err = guard_or_error(args.get("output_path"), "output_path",
                                    extra_roots=[args.get("pbip_path")])
        if err:
            return err
        # …path is a safe absolute Path…
    """
    if candidate is None:
        return None, None
    try:
        result = check_path(candidate, extra_roots=extra_roots, **check_kwargs)
        return result.resolved, None
    except PathGuardViolation as e:
        return None, {
            "success": False,
            "error_type": "path_traversal_blocked",
            "error": f"{arg_name}: {e}",
            "hint": (
                "Supply a path inside the connected PBIP workspace, inside the "
                "server repo's exports/output/logs directories, or inside a "
                "workspace root declared via MCP roots/list."
            ),
        }
