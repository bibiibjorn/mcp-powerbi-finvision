"""Read-only PBIP preflight: "will Desktop open this, and is it safe to commit" (F-06).

One engine behind ``07_PBIP_Operations validate_report`` and ``preflight``. It
ports the checks that used to live in four independent places outside the
server -- ``pbip/scripts/validate_pbip.py`` (structure), ``validate_tmdl_blocks.py``
(TMDL orphans / duplicates / TOM parse), ``g01_prepush_gate.py`` (JSON
well-formedness + PBIR required fields over a git change set) and the
``dump_visual_state.py --check-in-values`` polarity gate -- into one pure,
side-effect-free module.

Nothing here writes: files are only read, the optional ``powerbi-report-author
validate`` call is the READ-ONLY validator run with ``--no-schema`` (never
``pbir validate --update-schemas``, which rewrites every visual.json).

Finding shape (every finding)::

    {code, severity: error|warning|info, message, file (relative, '/'), pointer
     (JSON pointer or ''), load_blocking, impact, repair_hint, source}

``load_blocking`` is True only for defect classes with evidence that Desktop
refuses the WHOLE report: unparseable JSON, a broken ``definition.pbir`` binding,
a TMDL folder the TOM parser rejects, a resourcePackage item without ``type``, a
dangling visualLink bookmark / navigation / drillthrough target, and the schema
shapes Desktop rejects. Live evidence (R0201 on Desktop 2.157, 2026-10-03)
showed that bookmark refs to deleted visuals, a missing tooltip page and a visual
projecting an entity the model lacks are NOT fatal: they are errors with
``impact`` ``bookmark_partial`` / ``tooltip_missing`` / ``render_error``. ``impact`` says what else a finding costs
(``silent_drop`` / ``render_error`` / ``hygiene``).

``level="load"`` -> ``ok`` = no load-blocking error. ``level="commit"`` -> ``ok``
= no error at all, and the git-staged files are checked too (JSON validity of
every staged .json/.pbir/.platform, local-state files that must not be
committed, staged TMDL structure).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from core.pbip import pbir_model

logger = logging.getLogger(__name__)

GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
PAGE_NAME_RE = re.compile(r"^[\w-]+$")

ERROR, WARNING, INFO = "error", "warning", "info"

#: Visual JSON keys that must exist (g01 gate): absence = Desktop drops the object.
_VISUAL_REQUIRED = ("$schema", "name", "position")
_PAGE_REQUIRED = ("$schema", "name", "displayName", "displayOption")
_PBIR_REQUIRED = ("$schema", "version", "datasetReference")

_LOCAL_STATE_RE = re.compile(r"(^|/)\.pbi/(localSettings\.json|cache\.abf)$", re.I)
_CLI_TIMEOUT_S = 60


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class _Findings:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.items: List[Dict[str, Any]] = []
        self._json: Dict[str, Any] = {}

    def rel(self, path: Any) -> str:
        if path is None:
            return ""
        p = Path(str(path))
        try:
            return p.resolve().relative_to(self.root.resolve()).as_posix()
        except (ValueError, OSError):
            return p.as_posix()

    def add(
        self,
        code: str,
        severity: str,
        message: str,
        file: Any = None,
        pointer: str = "",
        *,
        load_blocking: bool = False,
        impact: str = "hygiene",
        hint: str = "",
        source: str = "preflight",
        cli_severity: Optional[str] = None,
        cause: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a finding.

        ``cause`` names what a CHANGE could have broken in a file it did not touch (a deleted
        table -> slicers still binding it; a deleted visual -> bookmarks still listing it). The
        commit gate counts such a finding as in scope when the cause is among the changed paths.
        Keys: ``table`` (model table name), ``own_visual`` (id of the visual the finding is
        about), ``visuals`` / ``pages`` (ids referenced but missing), ``paths`` (path suffixes).
        """
        item: Dict[str, Any] = {
            "code": code,
            "severity": severity,
            "message": message,
            "file": self.rel(file),
            "pointer": pointer,
            "load_blocking": bool(load_blocking and severity == ERROR),
            "impact": impact,
            "repair_hint": hint,
            "source": source,
        }
        if cli_severity:
            item["cli_severity"] = cli_severity
        if cause:
            item["cause"] = cause
        self.items.append(item)

    def load_json(self, path: Path, code_prefix: str, *, required: bool = True) -> Any:
        """Parse *path*; record missing / unreadable / invalid / BOM findings. None on failure."""
        key = str(path)
        if key in self._json:
            return self._json[key]
        data: Any = None
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            if required:
                self.add(
                    f"{code_prefix}_missing",
                    ERROR,
                    f"{path.name} is missing",
                    path,
                    load_blocking=True,
                    impact="load_fail",
                    hint=f"restore {path.name} (git checkout of the file) or re-save from Desktop",
                )
            self._json[key] = None
            return None
        except OSError as e:
            self.add(f"{code_prefix}_unreadable", ERROR, f"{path.name}: {e}", path)
            self._json[key] = None
            return None
        if raw.startswith(b"\xef\xbb\xbf"):
            self.add(
                f"{code_prefix}_bom",
                WARNING,
                f"{path.name} has a UTF-8 BOM (Desktop writes none; some tools reject it)",
                path,
                hint="re-save without BOM",
            )
            raw = raw[3:]
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self.add(
                f"{code_prefix}_invalid_json",
                ERROR,
                f"{path.name} is not valid JSON: {e}",
                path,
                load_blocking=True,
                impact="load_fail",
                hint="repair the JSON syntax (git diff the file) -- Desktop rejects the whole report",
            )
            data = None
        self._json[key] = data
        return data


def _ptr(*parts: Any) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


# ---------------------------------------------------------------------------
# Small PBIR walkers
# ---------------------------------------------------------------------------


def _literal(node: Any) -> Optional[str]:
    """``{"expr":{"Literal":{"Value":"'x'"}}}`` -> ``x`` (strings only)."""
    if not isinstance(node, dict):
        return None
    lit = (node.get("expr") or {}).get("Literal") if isinstance(node.get("expr"), dict) else None
    value = lit.get("Value") if isinstance(lit, dict) else None
    if isinstance(value, str) and len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return None


def _walk(node: Any, path: Tuple[Any, ...] = ()) -> Iterable[Tuple[Tuple[Any, ...], Any]]:
    stack: List[Tuple[Tuple[Any, ...], Any]] = [(path, node)]
    while stack:
        p, n = stack.pop()
        yield p, n
        if isinstance(n, dict):
            for k, v in n.items():
                if isinstance(v, (dict, list)):
                    stack.append((p + (k,), v))
        elif isinstance(n, list):
            for i, v in enumerate(n):
                if isinstance(v, (dict, list)):
                    stack.append((p + (i,), v))


# ---------------------------------------------------------------------------
# Report-level checks
# ---------------------------------------------------------------------------


def _check_platform(f: _Findings, folder: Path, expected: str) -> None:
    path = folder / ".platform"
    if not path.is_file():
        f.add(
            "platform_missing",
            WARNING,
            f".platform is missing in {folder.name}",
            path,
            hint="Desktop re-creates it on save; Fabric git integration requires it",
        )
        return
    data = f.load_json(path, "platform")
    if not isinstance(data, dict):
        return
    meta = data.get("metadata") or {}
    if meta.get("type") != expected:
        f.add(
            "platform_type_mismatch",
            ERROR,
            f".platform metadata.type is {meta.get('type')!r}, expected {expected!r}",
            path,
            _ptr("metadata", "type"),
            hint=f'set metadata.type to "{expected}"',
        )
    logical = (data.get("config") or {}).get("logicalId")
    if not logical:
        f.add(
            "platform_no_logical_id",
            ERROR,
            ".platform config.logicalId is missing",
            path,
            _ptr("config", "logicalId"),
            hint="add a fresh GUID -- never copy another item's logicalId (Fabric identity clash)",
        )
    elif not GUID_RE.match(str(logical)):
        f.add(
            "platform_bad_guid",
            ERROR,
            f".platform config.logicalId is not a GUID: {logical}",
            path,
            _ptr("config", "logicalId"),
            hint="replace with a valid GUID",
        )


def _check_pbir(f: _Findings, report_dir: Path) -> Optional[Dict[str, Any]]:
    path = report_dir / "definition.pbir"
    data = f.load_json(path, "pbir")
    if not isinstance(data, dict):
        return None
    for key in _PBIR_REQUIRED:
        if key not in data:
            f.add(
                "pbir_missing_field",
                ERROR,
                f"definition.pbir is missing {key!r}",
                path,
                _ptr(key),
                load_blocking=True,
                impact="load_fail",
                hint=f"add {key!r} (copy the shape from another report's definition.pbir)",
            )
    if str(data.get("version")) == "1.0":
        f.add(
            "pbir_legacy_version",
            WARNING,
            "definition.pbir version 1.0 only supports the legacy report format",
            path,
            _ptr("version"),
            hint="open in Desktop and re-save to upgrade to PBIR 4.0",
        )
    ref = data.get("datasetReference") or {}
    if "byPath" in ref:
        rel = (ref.get("byPath") or {}).get("path")
        if not rel:
            f.add(
                "pbir_bypath_empty",
                ERROR,
                "datasetReference.byPath has no path",
                path,
                _ptr("datasetReference", "byPath", "path"),
                load_blocking=True,
                impact="load_fail",
                hint="set byPath.path to the relative folder of the .SemanticModel",
            )
        elif not (report_dir / rel).resolve().is_dir():
            f.add(
                "pbir_bypath_missing",
                ERROR,
                f"datasetReference.byPath.path '{rel}' does not resolve to a folder",
                path,
                _ptr("datasetReference", "byPath", "path"),
                load_blocking=True,
                impact="load_fail",
                hint="fix the relative path (e.g. ../<Model>.SemanticModel) or restore the model folder",
            )
    elif "byConnection" in ref:
        if not (ref.get("byConnection") or {}).get("connectionString"):
            f.add(
                "pbir_byconnection_no_cs",
                ERROR,
                "datasetReference.byConnection has no connectionString",
                path,
                _ptr("datasetReference", "byConnection"),
                load_blocking=True,
                impact="load_fail",
                hint="add the connectionString of the published model",
            )
    elif "datasetReference" in data:
        f.add(
            "pbir_no_dataset_ref",
            ERROR,
            "datasetReference has neither byPath nor byConnection",
            path,
            _ptr("datasetReference"),
            load_blocking=True,
            impact="load_fail",
            hint="bind the report: byPath (local model) or byConnection (published model)",
        )
    return data


def _check_report_json(f: _Findings, report_dir: Path, definition: Path) -> None:
    path = definition / "report.json"
    data = f.load_json(path, "report_json")
    if not isinstance(data, dict):
        return
    if "$schema" not in data:
        f.add("report_json_no_schema", WARNING, "report.json has no $schema", path, "/$schema")
    packages = data.get("resourcePackages") or []
    for pi, pkg in enumerate(packages if isinstance(packages, list) else []):
        if not isinstance(pkg, dict):
            continue
        ptype = pkg.get("type")
        for ii, item in enumerate(pkg.get("items") or []):
            if not isinstance(item, dict):
                continue
            where = _ptr("resourcePackages", pi, "items", ii)
            if not item.get("type"):
                f.add(
                    "resource_item_no_type",
                    ERROR,
                    f"resourcePackages item {item.get('name')!r} has no 'type' "
                    "(Desktop rejects the whole report)",
                    path,
                    where + "/type",
                    load_blocking=True,
                    impact="load_fail",
                    hint='add "type": "Image" (svg/png) or "CustomTheme" (theme json)',
                )
            rel = item.get("path")
            if ptype in ("SharedResources", "RegisteredResources") and rel:
                target = report_dir / "StaticResources" / ptype / rel
                if not target.exists():
                    f.add(
                        "resource_missing",
                        ERROR,
                        f"resourcePackages {ptype} item {item.get('name')!r} points at a "
                        f"missing file: StaticResources/{ptype}/{rel}",
                        path,
                        where + "/path",
                        load_blocking=True,
                        impact="load_fail",
                        hint="restore the file under StaticResources or remove the item",
                    )
    base = (data.get("themeCollection") or {}).get("baseTheme") or {}
    if base.get("type") == "SharedResources" and base.get("name"):
        found = any(
            isinstance(i, dict) and i.get("name") == base["name"]
            for pkg in (packages if isinstance(packages, list) else [])
            if isinstance(pkg, dict) and pkg.get("type") == "SharedResources"
            for i in pkg.get("items") or []
        )
        if not found:
            f.add(
                "base_theme_not_in_packages",
                WARNING,
                f"baseTheme '{base['name']}' has no matching SharedResources item "
                "(built-in themes resolve by name, so Desktop usually still opens it)",
                path,
                _ptr("themeCollection", "baseTheme", "name"),
                hint="re-save from Desktop to register the theme resource",
            )
    custom = (data.get("themeCollection") or {}).get("customTheme") or {}
    if custom.get("name") and custom.get("type") == "RegisteredResources":
        found = any(
            isinstance(i, dict) and i.get("name") == custom["name"]
            for pkg in (packages if isinstance(packages, list) else [])
            if isinstance(pkg, dict) and pkg.get("type") == "RegisteredResources"
            for i in pkg.get("items") or []
        )
        if not found:
            f.add(
                "custom_theme_not_in_packages",
                ERROR,
                f"customTheme '{custom['name']}' has no matching RegisteredResources item",
                path,
                _ptr("themeCollection", "customTheme", "name"),
                load_blocking=True,
                impact="load_fail",
                hint="register the theme in resourcePackages or drop themeCollection.customTheme",
            )


class _ReportIndex:
    """Page / visual / bookmark ids of one report, read once."""

    def __init__(self) -> None:
        self.pages: Dict[str, Path] = {}  # page id (slug) -> folder
        self.visuals: Dict[str, Dict[str, Path]] = {}  # page id -> {visual id: folder}
        self.visual_data: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.bookmarks: Set[str] = set()
        self.bookmark_groups: Set[str] = set()


def _check_pages(f: _Findings, definition: Path, idx: _ReportIndex) -> None:
    pages_dir = definition / "pages"
    pj_path = pages_dir / "pages.json"
    if not pages_dir.is_dir():
        f.add(
            "pages_dir_missing",
            ERROR,
            "definition/pages/ is missing",
            pages_dir,
            impact="silent_drop",
            hint="a report needs at least one page",
        )
        return
    on_disk: Dict[str, Path] = {}
    for child in sorted(pages_dir.iterdir()):
        if child.is_dir():
            slug = child.name[: -len(".Page")] if child.name.endswith(".Page") else child.name
            on_disk[slug] = child
    idx.pages = dict(on_disk)

    pages_json = f.load_json(pj_path, "pages_json")
    order: List[str] = []
    if isinstance(pages_json, dict):
        raw_order = pages_json.get("pageOrder")
        if raw_order is not None and not isinstance(raw_order, list):
            f.add(
                "page_order_not_list",
                ERROR,
                "pageOrder must be an array",
                pj_path,
                "/pageOrder",
                load_blocking=True,
                impact="load_fail",
            )
        else:
            order = [p for p in (raw_order or []) if isinstance(p, str)]
        for key in ("activePageName", "landingPageName"):
            val = pages_json.get(key)
            if val and val not in order:
                f.add(
                    (
                        "active_page_not_in_order"
                        if key == "activePageName"
                        else "landing_page_not_in_order"
                    ),
                    WARNING,
                    f"{key} '{val}' is not in pageOrder",
                    pj_path,
                    _ptr(key),
                    hint=f"point {key} at a page listed in pageOrder",
                )
    for i, name in enumerate(order):
        if not PAGE_NAME_RE.match(name):
            f.add(
                "page_name_invalid_chars",
                ERROR,
                f"page id '{name}' has characters outside [A-Za-z0-9_-]; Desktop silently ignores it",
                pj_path,
                _ptr("pageOrder", i),
                impact="silent_drop",
                hint="rename the page folder + page.json name + pageOrder entry to word characters/hyphens",
            )
        if name not in on_disk:
            f.add(
                "page_folder_missing",
                ERROR,
                f"pageOrder lists '{name}' but no page folder exists",
                pj_path,
                _ptr("pageOrder", i),
                impact="silent_drop",
                hint="remove the entry from pageOrder or restore the page folder",
            )
    for slug, folder in on_disk.items():
        if slug not in order:
            f.add(
                "orphan_page_folder",
                WARNING,
                f"page folder '{folder.name}' is not listed in pageOrder (Desktop ignores it)",
                folder,
                impact="silent_drop",
                hint=f"add '{slug}' to pageOrder or delete the folder",
            )
        page = f.load_json(folder / "page.json", "page_json")
        if not isinstance(page, dict):
            continue
        for key in _PAGE_REQUIRED:
            if key not in page:
                f.add(
                    "page_missing_field",
                    ERROR,
                    f"page.json is missing {key!r}",
                    folder / "page.json",
                    _ptr(key),
                    impact="silent_drop",
                    hint=f"add {key!r} (copy from a sibling page.json)",
                )
        if page.get("name") != slug:
            f.add(
                "page_name_mismatch",
                ERROR,
                f"page.json name '{page.get('name')}' does not match its folder '{folder.name}'",
                folder / "page.json",
                "/name",
                impact="silent_drop",
                hint="make page.json name equal the folder id (case-sensitive)",
            )


def _load_visuals(f: _Findings, idx: _ReportIndex) -> None:
    for slug, pdir in sorted(idx.pages.items()):
        vroot = pdir / "visuals"
        vmap: Dict[str, Path] = {}
        if vroot.is_dir():
            for vdir in sorted(vroot.iterdir()):
                if vdir.is_dir():
                    vmap[vdir.name] = vdir
        idx.visuals[slug] = vmap
        for vid, vdir in vmap.items():
            vpath = vdir / "visual.json"
            data = f.load_json(vpath, "visual_json")
            if not isinstance(data, dict):
                continue
            idx.visual_data[(slug, vid)] = data
            missing = [k for k in _VISUAL_REQUIRED if k not in data]
            if "visual" not in data and "visualGroup" not in data:
                missing.append("visual|visualGroup")
            for key in missing:
                f.add(
                    "visual_missing_field",
                    ERROR,
                    f"visual.json is missing {key!r}",
                    vpath,
                    _ptr(key) if "|" not in key else "",
                    impact="silent_drop",
                    hint=f"add {key!r} (copy the shape from a sibling visual)",
                )
            if data.get("name") not in (None, vid):
                f.add(
                    "visual_name_mismatch",
                    WARNING,
                    f"visual.json name '{data.get('name')}' differs from its folder '{vid}'",
                    vpath,
                    "/name",
                    hint="make the folder and name equal (interactions and bookmarks key on name)",
                )
            group = data.get("visualGroup")
            if isinstance(group, dict) and "isHidden" in group:
                f.add(
                    "ishidden_in_visualgroup",
                    ERROR,
                    "isHidden sits inside visualGroup; it must be a TOP-LEVEL key of visual.json",
                    vpath,
                    _ptr("visualGroup", "isHidden"),
                    impact="render_error",
                    hint="move isHidden next to name/position",
                )
            if "parentGroupName" in data and not isinstance(data["parentGroupName"], str):
                f.add(
                    "parentgroup_not_string",
                    ERROR,
                    "parentGroupName is not a string (a top-level visual must OMIT the key)",
                    vpath,
                    "/parentGroupName",
                    impact="render_error",
                    hint="delete the key; never set it to null",
                )


def _check_visual_cross(f: _Findings, idx: _ReportIndex) -> None:
    """Checks that need every visual AND every bookmark id loaded first."""
    for slug, vmap in idx.visuals.items():
        for vid in vmap:
            data = idx.visual_data.get((slug, vid))
            if not data:
                continue
            vpath = vmap[vid] / "visual.json"
            parent = data.get("parentGroupName")
            if isinstance(parent, str) and parent not in vmap:
                f.add(
                    "parentgroup_missing",
                    ERROR,
                    f"parentGroupName '{parent}' is not a visual on this page",
                    vpath,
                    "/parentGroupName",
                    impact="render_error",
                    hint="re-parent the visual or delete parentGroupName",
                    cause={"visuals": [parent]},
                )
            _check_visual_links(f, vpath, data, idx)
            _check_polarity(f, vpath, data)


def _link_entries(data: Dict[str, Any]) -> Iterable[Tuple[str, int, Dict[str, Any]]]:
    visual = data.get("visual") if isinstance(data.get("visual"), dict) else {}
    for container in ("visualContainerObjects", "objects"):
        objs = visual.get(container)
        if not isinstance(objs, dict):
            continue
        for card in ("visualLink", "visualTooltip"):
            for i, entry in enumerate(objs.get(card) or []):
                props = entry.get("properties") if isinstance(entry, dict) else None
                if isinstance(props, dict):
                    yield f"{container}/{card}/{i}", i, props


def _check_visual_links(f: _Findings, vpath: Path, data: Dict[str, Any], idx: _ReportIndex) -> None:
    for where, _i, props in _link_entries(data):
        link_type = _literal(props.get("type"))
        bm = _literal(props.get("bookmark"))
        if bm and (link_type in (None, "Bookmark")) and bm not in idx.bookmarks:
            f.add(
                "visuallink_bookmark_missing",
                ERROR,
                f"visualLink points at bookmark '{bm}' which does not exist in this report",
                vpath,
                f"/visual/{where}/properties/bookmark",
                load_blocking=True,
                impact="load_fail",
                hint="re-point to an existing bookmark or delete the visualLink",
            )
        for key in ("navigationSection", "drillthroughSection", "section"):
            target = _literal(props.get(key))
            if target and target not in idx.pages:
                f.add(
                    "visuallink_page_missing",
                    ERROR,
                    f"{where.split('/')[1]}.{key} points at page '{target}' which does not exist",
                    vpath,
                    f"/visual/{where}/properties/{key}",
                    # Live evidence (R0201, Desktop 2.157, 2026-10-03): a visualTooltip.section
                    # pointing at a deleted page loads fine - the tooltip just does not show.
                    load_blocking=key != "section",
                    impact="load_fail" if key != "section" else "tooltip_missing",
                    hint="re-point to a valid page id (or this page) or delete the link",
                    cause={"pages": [target]},
                )
    # bookmark navigators
    visual = data.get("visual") if isinstance(data.get("visual"), dict) else {}
    for i, entry in enumerate((visual.get("objects") or {}).get("bookmarks") or []):
        props = entry.get("properties") if isinstance(entry, dict) else None
        if not isinstance(props, dict):
            continue
        group = _literal(props.get("bookmarkGroup"))
        if group and group not in idx.bookmark_groups and group not in idx.bookmarks:
            f.add(
                "navigator_group_missing",
                WARNING,
                f"bookmark navigator points at group '{group}' which does not exist",
                vpath,
                f"/visual/objects/bookmarks/{i}/properties/bookmarkGroup",
                hint="point the navigator at an existing bookmark group",
            )


def _check_polarity(f: _Findings, vpath: Path, data: Dict[str, Any]) -> None:
    """Inverted-selection lint (dump_visual_state.py --check-in-values gate).

    Flags every ``In`` (also under ``Not``) whose ``Values`` list is empty and that is not a
    subquery (TopN uses ``In.Table``). Polarity is per FILE: an inverted-selection visual's
    lists are EXCLUDE lists.
    """
    inverted = any(
        pbir_model.is_inverted_selection(flt, data) for flt in pbir_model.get_visual_filters(data)
    ) or "isInvertedSelectionMode" in json.dumps(data.get("filterConfig") or {})
    for path, node in _walk(data):
        inn = node.get("In") if isinstance(node, dict) else None
        if (
            isinstance(inn, dict)
            and "Values" in inn
            and not inn.get("Values")
            and inn.get("Table") is None
        ):
            f.add(
                "in_values_empty",
                ERROR,
                "In.Values is an empty list"
                + (
                    " under isInvertedSelectionMode (an EXCLUDE list: removing entries WIDENS the "
                    "filter, an empty list fails)"
                    if inverted
                    else ""
                )
                + " -> InvalidQueryInExpression; the visual renders an error box",
                vpath,
                "".join(_ptr(p) for p in path) + "/In/Values",
                impact="render_error",
                hint="drop the whole Where clause / filter instead of emptying the list",
            )


def _check_interactions(f: _Findings, idx: _ReportIndex) -> None:
    for slug, pdir in sorted(idx.pages.items()):
        ppath = pdir / "page.json"
        page = f.load_json(ppath, "page_json")
        if not isinstance(page, dict):
            continue
        ids = set(idx.visuals.get(slug, {}))
        for i, it in enumerate(pbir_model.get_page_interactions(page)):
            for end in ("source", "target"):
                if it.get(end) not in ids:
                    f.add(
                        "interaction_dangling",
                        WARNING,
                        f"visualInteractions entry {i} {end} '{it.get(end)}' is not a visual on page '{slug}'",
                        ppath,
                        _ptr("visualInteractions", i, end),
                        hint="delete the entry (it targets a visual that no longer exists)",
                        cause={"visuals": [str(it.get(end))]},
                    )
        if "interactions" in page:
            f.add(
                "interactions_legacy_key",
                WARNING,
                "page.json uses the legacy 'interactions' key; PBIR reads 'visualInteractions'",
                ppath,
                "/interactions",
                hint="rename the key to visualInteractions",
            )


def _container_ids(node: Any) -> Iterable[str]:
    """Every key of a visualContainerGroups dict, recursing into nested ``children`` dicts."""
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "children" and isinstance(val, dict):
                yield from _container_ids(val)
            elif key != "children":
                yield key
                if isinstance(val, dict):
                    yield from _container_ids(val.get("children"))


def _check_bookmarks(f: _Findings, definition: Path, idx: _ReportIndex) -> None:
    bdir = definition / "bookmarks"
    if not bdir.is_dir():
        return
    meta_path = bdir / "bookmarks.json"
    meta = f.load_json(meta_path, "bookmarks_json", required=False) if meta_path.exists() else None
    listed: Set[str] = set()
    if isinstance(meta, dict):
        for i, item in enumerate(meta.get("items") or []):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                f.add(
                    "bookmarks_item_invalid",
                    ERROR,
                    f"bookmarks.json items[{i}] has no string name",
                    meta_path,
                    _ptr("items", i),
                    impact="load_fail",
                )
                continue
            if "children" in item:
                idx.bookmark_groups.add(item["name"])
                kids = item.get("children")
                for k, child in enumerate(kids if isinstance(kids, list) else []):
                    if not isinstance(child, str):
                        f.add(
                            "bookmarks_children_not_strings",
                            ERROR,
                            "bookmarks.json group children must be bare id strings, not objects",
                            meta_path,
                            _ptr("items", i, "children", k),
                            load_blocking=True,
                            impact="load_fail",
                            hint='use "children": ["<bookmark id>", ...]',
                        )
                    else:
                        listed.add(child)
            else:
                listed.add(item["name"])
    files: Dict[str, Path] = {}
    for bfile in sorted(bdir.glob("*.bookmark.json")):
        files[bfile.name[: -len(".bookmark.json")]] = bfile
    idx.bookmarks = set(files) | set(listed)
    for bid in sorted(listed - set(files)):
        f.add(
            "bookmark_file_missing",
            ERROR,
            f"bookmarks.json lists '{bid}' but {bid}.bookmark.json does not exist",
            meta_path,
            impact="silent_drop",
            hint="delete the entry or restore the bookmark file",
            cause={"paths": [f"/bookmarks/{bid}.bookmark.json"]},
        )
    for bid, bfile in sorted(files.items()):
        if meta is not None and bid not in listed:
            f.add(
                "bookmark_not_listed",
                WARNING,
                f"{bfile.name} is not referenced by bookmarks.json (Desktop ignores it)",
                bfile,
                hint="add it to bookmarks.json items or delete the file",
            )
        bm = f.load_json(bfile, "bookmark_json")
        if not isinstance(bm, dict):
            continue
        if bm.get("name") not in (None, bid):
            f.add(
                "bookmark_name_mismatch",
                WARNING,
                f"bookmark name '{bm.get('name')}' differs from file '{bfile.name}'",
                bfile,
                "/name",
            )
        state = bm.get("explorationState") or {}
        active = state.get("activeSection")
        if active and active not in idx.pages:
            f.add(
                "bookmark_active_section_missing",
                ERROR,
                f"explorationState.activeSection '{active}' is not a page",
                bfile,
                "/explorationState/activeSection",
                impact="silent_drop",
                hint="point activeSection at an existing page or delete the bookmark",
                cause={"pages": [str(active)]},
            )
        for sec, sdata in (state.get("sections") or {}).items():
            if sec not in idx.pages:
                f.add(
                    "bookmark_section_missing",
                    ERROR,
                    f"explorationState.sections names page '{sec}' which does not exist",
                    bfile,
                    _ptr("explorationState", "sections", sec),
                    load_blocking=True,
                    impact="load_fail",
                    hint="scrub the section from the bookmark (it snapshots a deleted page)",
                    cause={"pages": [str(sec)]},
                )
                continue
            known = set(idx.visuals.get(sec, {}))
            for key in ("visualContainers", "visualContainerGroups"):
                block = (sdata or {}).get(key)
                if not isinstance(block, dict):
                    continue
                ids = list(_container_ids(block)) if key == "visualContainerGroups" else list(block)
                for vid in ids:
                    if vid not in known:
                        f.add(
                            "bookmark_visual_missing",
                            ERROR,
                            f"bookmark {key} references '{vid}' which is not a visual on page '{sec}'",
                            bfile,
                            _ptr("explorationState", "sections", sec, key, vid),
                            # R0201 opens in Desktop 2.157 with 145 of these: the bookmark
                            # just skips the missing visual.
                            load_blocking=False,
                            impact="bookmark_partial",
                            hint="scrub the id from the bookmark (visuals deleted without scrubbing bookmarks)",
                            cause={"visuals": [str(vid)], "pages": [str(sec)]},
                        )
        targets = (bm.get("options") or {}).get("targetVisualNames")
        if isinstance(targets, list):
            every = {v for vm in idx.visuals.values() for v in vm}
            for ti, t in enumerate(targets):
                if t not in every:
                    f.add(
                        "bookmark_target_visual_missing",
                        ERROR,
                        f"options.targetVisualNames references '{t}' which is not a visual",
                        bfile,
                        _ptr("options", "targetVisualNames", ti),
                        # R0201 opens with 308 of these (live, 2026-10-03).
                        load_blocking=False,
                        impact="bookmark_partial",
                        hint="remove the id from targetVisualNames",
                        cause={"visuals": [str(t)]},
                    )


# ---------------------------------------------------------------------------
# Model-side checks
# ---------------------------------------------------------------------------

_OWNED_BY = {"PBI_QueryGroupOrder": "queryGroup"}


def _tmdl_structure(f: _Findings, defdir: Path) -> None:
    """Orphaned indented properties / queryGroup pairing / duplicate model annotations."""
    for tmdl in sorted(defdir.rglob("*.tmdl")):
        try:
            text = tmdl.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            if not (ln.startswith("\t") or ln.startswith("    ")):
                continue
            for prop, owner in _OWNED_BY.items():
                if prop not in ln:
                    continue
                j = i - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                above = lines[j] if j >= 0 else ""
                if not above.startswith(owner):
                    f.add(
                        "tmdl_orphan_property",
                        ERROR,
                        f"indented '{ln.strip()}' has no '{owner}' header above it "
                        "(a block header was deleted without its body)",
                        tmdl,
                        f"line {i + 1}",
                        load_blocking=True,
                        impact="load_fail",
                        hint=f"restore the '{owner}' header or delete the orphaned child lines",
                    )
        heads = len(re.findall(r"^queryGroup ", text, re.M))
        kids = len(re.findall(r"^[\t ]+annotation PBI_QueryGroupOrder", text, re.M))
        if heads and heads != kids:
            f.add(
                "tmdl_querygroup_mismatch",
                ERROR,
                f"{heads} queryGroup header(s) vs {kids} PBI_QueryGroupOrder annotation(s)",
                tmdl,
                load_blocking=True,
                impact="load_fail",
                hint="every queryGroup needs exactly one order annotation",
            )
        names = [ln.split("=")[0].strip() for ln in lines if ln.startswith("annotation ")]
        dups = sorted({n for n in names if names.count(n) > 1})
        if dups:
            f.add(
                "tmdl_duplicate_annotation",
                ERROR,
                f"duplicate model-level annotation(s): {', '.join(dups)} "
                "('objects cannot be merged' -> DataModelLoadFailed)",
                tmdl,
                load_blocking=True,
                impact="load_fail",
                hint="delete the duplicated annotation block",
            )


def _check_model(f: _Findings, model: Dict[str, Any], tom_check: str) -> Dict[str, Any]:
    mdir = Path(model["model_dir"])
    summary: Dict[str, Any] = {"model": mdir.name, "format": model.get("format")}
    _check_platform(f, mdir, "SemanticModel")
    pbism = mdir / "definition.pbism"
    data = f.load_json(pbism, "pbism")
    if isinstance(data, dict) and not data.get("version"):
        f.add(
            "pbism_no_version",
            ERROR,
            "definition.pbism has no version",
            pbism,
            "/version",
            load_blocking=True,
            impact="load_fail",
        )
    fmt = model.get("format")
    defdir = mdir / "definition"
    if (defdir / "model.tmdl").exists() and (mdir / "model.bim").exists():
        f.add(
            "both_tmdl_and_bim",
            ERROR,
            "definition/ (TMDL) and model.bim (TMSL) are both present",
            mdir,
            load_blocking=True,
            impact="load_fail",
            hint="delete the unused format",
        )
    if fmt == "bim":
        f.load_json(mdir / "model.bim", "bim")
        f.add(
            "sm_tmsl_format",
            INFO,
            "model.bim (TMSL) is the legacy format; prefer TMDL",
            mdir / "model.bim",
        )
    elif fmt == "tmdl":
        if not (defdir / "model.tmdl").exists():
            f.add(
                "model_tmdl_missing",
                ERROR,
                "definition/model.tmdl is missing",
                defdir / "model.tmdl",
                load_blocking=True,
                impact="load_fail",
            )
        _tmdl_structure(f, defdir)
        if tom_check != "off":
            from core.tmdl.tom_check import tom_deserialize_check

            res = tom_deserialize_check(str(defdir))
            summary["tom_check"] = {k: res.get(k) for k in ("ran", "ok", "udf_gate", "elapsed_ms")}
            if not res.get("ran"):
                f.add(
                    "tom_check_unavailable",
                    INFO,
                    "TOM deserialize check did not run ("
                    + str(res.get("reason") or "assemblies unavailable")
                    + "); the model is NOT proven to load",
                    defdir,
                    source="tom_check",
                )
            elif res.get("ok") is False:
                f.add(
                    "tom_deserialize_failed",
                    ERROR,
                    "Desktop's TMDL parser rejects this model: " + str(res.get("error")),
                    defdir,
                    load_blocking=True,
                    impact="load_fail",
                    hint="fix the TMDL named in the message; never tell the user to reload before this is clean",
                    source="tom_check",
                )
            elif res.get("udf_gate"):
                f.add(
                    "tom_udf_gate",
                    INFO,
                    "TOM client library UDF compatibility-level gate (not a model defect)",
                    defdir,
                    source="tom_check",
                )
    elif fmt == "unknown":
        f.add(
            "sm_no_definition",
            ERROR,
            "semantic model has neither definition/ (TMDL) nor model.bim",
            mdir,
            load_blocking=True,
            impact="load_fail",
        )
    return summary


def _check_broken_refs(f: _Findings, pbip_path: str, report_name: str) -> Dict[str, Any]:
    """Visual bindings to objects the bound model lacks (existing scanner)."""
    from core.pbip.pbip_visual_validator import scan_broken_visual_references

    try:
        scan = scan_broken_visual_references(pbip_path)
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        f.add("broken_refs_scan_failed", INFO, f"field-reference scan failed: {e}", pbip_path)
        return {}
    for ref in scan.get("broken_references") or []:
        if ref.get("report") != report_name:
            continue
        loc = str(ref.get("location") or "")
        fatal = loc.startswith("query")
        cause: Dict[str, Any] = {}
        if ref.get("table"):
            cause["table"] = str(ref["table"])
        if ref.get("visual_id"):
            cause["own_visual"] = str(ref["visual_id"])
        f.add(
            "visual_binds_missing_field",
            ERROR if fatal else WARNING,
            f"{ref.get('visual_type')} '{ref.get('visual_id') or ref.get('page')}' binds "
            f"{ref.get('table')}[{ref.get('field')}]: {ref.get('reason')}",
            Path(pbip_path),
            loc,
            # R0201 opens in Desktop 2.157 with 17 slicers projecting a table the model no
            # longer has: the visual shows an error, the report loads.
            load_blocking=False,
            impact="render_error" if fatal else "hygiene",
            hint=(
                "rebind or remove the field (the visual renders an error until it is fixed)"
                if fatal
                else "formatting / filter leftover Desktop ignores; clean when convenient"
            ),
            cause=cause,
        )
    return scan.get("summary") or {}


# ---------------------------------------------------------------------------
# powerbi-report-author (optional, READ-ONLY)
# ---------------------------------------------------------------------------


#: The report-author CLI appends ": <absolute file path>" (sometimes cut with an ellipsis) to every
#: message; the finding's ``file`` already says where, so the copy only bloats the result.
_TRAILING_PATH_RE = re.compile(r":\s+(?:[A-Za-z]:[\\/]|/)[^\n]*$")


def _cli_message(message: str) -> str:
    return _TRAILING_PATH_RE.sub("", message).rstrip()


def _run_report_author(f: _Findings, report_dir: Path) -> Dict[str, Any]:
    exe = shutil.which("powerbi-report-author")
    if not exe:
        return {"ran": False, "reason": "powerbi-report-author not on PATH"}
    # --no-schema: offline (no network fetch of JSON schemas); NEVER pbir validate --update-schemas.
    cmd = [exe, "validate", str(report_dir), "--no-schema", "--format", "json"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT_S,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,  # never inherit the MCP server's stdio pipe
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ran": False, "reason": f"{type(e).__name__}: {e}"}
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        return {"ran": False, "reason": "unparseable CLI output", "exit": proc.returncode}
    data = payload.get("data") if isinstance(payload, dict) else None
    diagnostics = (data or {}).get("diagnostics") or {}
    n = 0
    for code, block in diagnostics.items():
        # Advisory only: the CLI reports hundreds of 'errors' on reports Desktop opens fine
        # (R0201: 1,000+ on 2026-10-03). The fatal classes it knows (resourcePackage item
        # type, isHidden inside visualGroup) have their own preflight checks.
        cli_sev = (block or {}).get("severity") or "warning"
        for item in (block or {}).get("items") or []:
            n += 1
            f.add(
                f"report_author:{code}",
                WARNING,
                _cli_message(str(item.get("message") or code)),
                item.get("file") or report_dir,
                "/" + str(item["path"]).strip("/") if item.get("path") else "",
                source="powerbi-report-author",
                cli_severity=cli_sev,
                hint="advisory: this CLI is noisy on legacy-schema reports (theme-name / outspacePane "
                "findings are known false positives)",
            )
    return {"ran": True, "diagnostics": n, "result": (data or {}).get("result")}


# ---------------------------------------------------------------------------
# git scope (level=commit)
# ---------------------------------------------------------------------------


def _git(args: List[str], cwd: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-c", "core.fsmonitor=false"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            # Inside the MCP server stdin is the protocol pipe: a child must not inherit it, and
            # git must never prompt (credential / pager) - either can stall the call to its timeout.
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _git_scope(
    f: _Findings, root: Path, base: Optional[str], include_unstaged: bool
) -> Dict[str, Any]:
    top = _git(["rev-parse", "--show-toplevel"], root)
    if not top:
        return {"available": False, "reason": "not a git repository (or git missing)"}
    repo = Path(top.strip())
    # Deleted files stay in the list (no --diff-filter) and --no-renames reports BOTH sides of a
    # rename: a deleted table / visual is exactly what makes other files' references dangle.
    # quotepath=false keeps non-ASCII paths readable instead of octal-quoted.
    base_args = ["-c", "core.quotepath=false", "diff", "--name-only", "--no-renames"]
    files: Set[str] = set((_git(base_args + ["--cached"], repo) or "").split("\n"))
    if include_unstaged:
        files |= set((_git(base_args, repo) or "").split("\n"))
    if base:
        files |= set((_git(base_args + [f"{base}...HEAD"], repo) or "").split("\n"))
    files = {x.strip() for x in files if x.strip()}
    json_checked = 0
    tmdl_dirs: Set[Path] = set()
    for rel in sorted(files):
        full = repo / rel
        norm = rel.replace("\\", "/")
        if _LOCAL_STATE_RE.search(norm):
            f.add(
                "staged_local_state",
                ERROR,
                f"{rel} is machine-local Desktop state and must not be committed",
                full,
                hint="git rm --cached the file and add '**/.pbi/localSettings.json' / '**/.pbi/cache.abf' to .gitignore",
                source="git",
            )
        if norm.endswith(".tmdl"):
            tmdl_dirs.add(full.parent)
        if not full.is_file():
            continue
        if norm.endswith((".json", ".pbir", ".platform", ".pbip", ".pbism")):
            json_checked += 1
            f.load_json(full, "staged_json")
    # Contamination scan (g01 gate #4): a visual referencing another page's section id.
    contam = 0
    for rel in sorted(files):
        norm = rel.replace("\\", "/")
        if not (norm.endswith("visual.json") and "/pages/" in norm and "/visuals/" in norm):
            continue
        full = repo / rel
        data = f.load_json(full, "staged_visual", required=False) if full.is_file() else None
        if not isinstance(data, dict):
            continue
        owner = norm.split("/pages/")[1].split("/")[0]
        foreign = {
            n["section"]
            for _p, n in _walk(data)
            if isinstance(n, dict) and isinstance(n.get("section"), str) and n["section"] != owner
        }
        if foreign:
            contam += 1
            f.add(
                "cross_page_section_ref",
                WARNING,
                f"visual references other page section(s) {sorted(foreign)} (legit for "
                "drillthrough / smart narrative; verify it is intentional)",
                full,
                source="git",
            )
    return {
        "available": True,
        "repo": str(repo),
        "changed_files": len(files),
        "json_files_checked": json_checked,
        "cross_page_warnings": contam,
        # Only the echoed list is capped; the scope itself is built from the full set.
        "files": sorted(files)[:200],
        "files_total": len(files),
        "_changed": files,
    }


def _norm_name(name: str) -> str:
    """Table name -> comparable token (TMDL file names sanitise characters the name allows)."""
    return re.sub(r"[^0-9a-z]", "", str(name).lower())


def _table_stems(model_dirs: Iterable[Any]) -> Set[str]:
    """Normalised file stems of every ``definition/tables/*.tmdl`` that exists on disk."""
    stems: Set[str] = set()
    for md in model_dirs:
        if not md:
            continue
        tdir = Path(str(md)) / "definition" / "tables"
        if tdir.is_dir():
            stems |= {_norm_name(p.stem) for p in tdir.glob("*.tmdl")}
    return stems


def _scope_reason(
    item: Dict[str, Any], changed: Set[str], blob: str, table_stems: Set[str]
) -> Optional[str]:
    """Why *item* belongs to this change: ``changed_file`` / ``caused_by_change`` / None."""
    file = item.get("file") or ""
    if file and any(c.endswith(file) or file.endswith(c) for c in changed):
        return "changed_file"
    cause = item.get("cause")
    if not isinstance(cause, dict):
        return None
    own = cause.get("own_visual")
    if own and f"/visuals/{own}/" in blob:
        return "changed_file"
    for vid in cause.get("visuals") or []:
        if f"/visuals/{vid}/" in blob:
            return "caused_by_change"
    for pid in cause.get("pages") or []:
        if f"/pages/{pid}/" in blob:
            return "caused_by_change"
    for suffix in cause.get("paths") or []:
        if any(("/" + c).endswith(suffix) for c in changed):
            return "caused_by_change"
    table = cause.get("table")
    if table:
        tn = _norm_name(table)
        if any(
            "/tables/" in "/" + c and _norm_name(Path(c).stem) == tn and c.endswith(".tmdl")
            for c in changed
        ):
            return "caused_by_change"
        # The table has no TMDL file we can map to (deleted, or a TMSL model): any model-file
        # change could be what removed it, so stay conservative.
        if tn not in table_stems and any(c.endswith((".tmdl", ".bim")) for c in changed):
            return "caused_by_change"
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_preflight(
    pbip_path: str,
    level: str = "load",
    *,
    tom_check: str = "auto",
    report_author: bool = True,
    git_base: Optional[str] = None,
    include_unstaged: bool = False,
    max_findings: Optional[int] = 200,
    examples_per_code: int = 3,
) -> Dict[str, Any]:
    """Validate the report(s) / model(s) *pbip_path* resolves to. Never writes.

    ``findings`` holds the first *max_findings* (load-blocking first, then errors) and
    ``by_code`` the per-code counts with examples; ``None`` returns every finding.

    Returns ``{success, level, ok, load_ok, commit_ok, findings, summary, ...}``.
    ``ok`` follows *level* (``load``: no load-blocking error; ``commit``: no error).
    """
    level = (level or "load").lower()
    if level not in ("load", "commit"):
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": f"level must be 'load' or 'commit', got {level!r}",
        }
    res = pbir_model.resolve_pbip(pbip_path)
    reports = res.get("reports") or []
    models = res.get("models") or []
    if not reports and not models:
        return {
            "success": False,
            "error_type": "not_found",
            "error": f"no report or semantic model found for {pbip_path}",
            "warnings": res.get("warnings", []),
        }

    inp = Path(res["input"])
    anchor = Path(reports[0]["report_dir"]) if reports else Path(models[0]["model_dir"])
    root = (
        inp
        if inp.is_dir() and not pbir_model._is_report_dir(inp) and not pbir_model._is_model_dir(inp)
        else anchor.parent
    )
    f = _Findings(root)
    summary: Dict[str, Any] = {"reports": [], "models": []}

    for w in res.get("warnings") or []:
        f.add("resolve_warning", WARNING, str(w), pbip_path)

    for pbip_file in {r.get("pbip_file") for r in reports if r.get("pbip_file")}:
        data = f.load_json(Path(pbip_file), "pbip")
        if isinstance(data, dict):
            for art in data.get("artifacts") or []:
                rp = ((art or {}).get("report") or {}).get("path")
                if rp and not (Path(pbip_file).parent / rp).is_dir():
                    f.add(
                        "pbip_report_path_missing",
                        ERROR,
                        f".pbip artifacts[].report.path '{rp}' does not resolve",
                        pbip_file,
                        load_blocking=True,
                        impact="load_fail",
                        hint="fix the path or restore the .Report folder",
                    )

    ra_state: Dict[str, Any] = {}
    for rep in reports:
        rdir = Path(rep["report_dir"])
        rsum: Dict[str, Any] = {
            "report": rep["name"],
            "dataset_reference": rep.get("dataset_reference"),
        }
        _check_platform(f, rdir, "Report")
        _check_pbir(f, rdir)
        definition = Path(rep["definition_dir"]) if rep.get("definition_dir") else None
        legacy = rdir / "report.json"
        if definition is None:
            if legacy.exists():
                f.add(
                    "report_legacy_format",
                    WARNING,
                    "report.json at the root (no definition/) is the legacy PBIR-Legacy format",
                    legacy,
                    hint="open in Desktop and re-save to migrate to PBIR",
                )
            else:
                f.add(
                    "report_no_definition",
                    ERROR,
                    "report folder has no definition/",
                    rdir,
                    load_blocking=True,
                    impact="load_fail",
                )
            summary["reports"].append(rsum)
            continue
        idx = _ReportIndex()
        _check_report_json(f, rdir, definition)
        f.load_json(definition / "version.json", "version_json", required=False)
        _check_pages(f, definition, idx)
        _load_visuals(f, idx)
        _check_bookmarks(f, definition, idx)
        _check_visual_cross(f, idx)
        _check_interactions(f, idx)
        rsum.update(
            pages=len(idx.pages),
            visuals=sum(len(v) for v in idx.visuals.values()),
            bookmarks=len(idx.bookmarks),
        )
        if rep.get("model_dir"):
            rsum["broken_refs"] = _check_broken_refs(
                f, rep.get("pbip_file") or str(rdir), rep["name"]
            )
        if report_author:
            ra_state[rep["name"]] = _run_report_author(f, rdir)
        summary["reports"].append(rsum)

    for model in models:
        summary["models"].append(_check_model(f, model, tom_check))

    git_info: Optional[Dict[str, Any]] = None
    if level == "commit":
        git_info = _git_scope(f, root, git_base, include_unstaged)
        changed = set(git_info.pop("_changed", None) or ())
        if git_info.get("available"):
            blob = "\n".join("/" + c.replace("\\", "/") for c in changed) + "\n"
            stems = _table_stems(
                [r.get("model_dir") for r in reports] + [m.get("model_dir") for m in models]
            )
            for item in f.items:
                reason = _scope_reason(item, changed, blob, stems)
                item["in_staged_scope"] = reason is not None
                item["in_scope_reason"] = reason

    errors = [x for x in f.items if x["severity"] == ERROR]
    load_errors = [x for x in errors if x["load_blocking"]]
    load_ok = not load_errors
    scoped = git_info is not None and bool(git_info.get("available"))
    if scoped:
        # Pre-existing debt outside the changed files must not block every commit: gate on
        # load blockers anywhere plus errors in the files this change touches.
        gating = [x for x in errors if x["load_blocking"] or x.get("in_staged_scope")]
    else:
        gating = errors
    commit_ok = not gating
    counts = {
        "error": len(errors),
        "error_in_scope": len(gating) if scoped else None,
        "preexisting_error": (len(errors) - len(gating)) if scoped else None,
        "warning": sum(1 for x in f.items if x["severity"] == WARNING),
        "info": sum(1 for x in f.items if x["severity"] == INFO),
        "load_blocking": len(load_errors),
    }
    by_code: Dict[str, Dict[str, Any]] = {}
    sev_rank = {ERROR: 0, WARNING: 1, INFO: 2}
    for x in f.items:
        g = by_code.setdefault(
            x["code"],
            {
                "count": 0,
                "severity": x["severity"],
                "by_severity": {},
                "load_blocking": False,
                "impact": x.get("impact"),
                "repair_hint": x.get("repair_hint"),
                "examples": [],
            },
        )
        g["count"] += 1
        # A code can mix severities (visual_binds_missing_field, report_author:*): report the
        # most severe and the split, not whichever finding came first.
        g["by_severity"][x["severity"]] = g["by_severity"].get(x["severity"], 0) + 1
        if sev_rank.get(x["severity"], 3) < sev_rank.get(g["severity"], 3):
            g["severity"] = x["severity"]
        g["load_blocking"] = g["load_blocking"] or bool(x["load_blocking"])
        if len(g["examples"]) < examples_per_code:
            g["examples"].append(
                {"file": x["file"], "pointer": x["pointer"], "message": x["message"]}
            )
    rank = {ERROR: 0, WARNING: 1, INFO: 2}
    ordered = sorted(
        f.items, key=lambda x: (not x["load_blocking"], rank.get(x["severity"], 3), x["code"])
    )
    cap = max(0, int(max_findings)) if max_findings is not None else len(ordered)
    out: Dict[str, Any] = {
        "success": True,
        "level": level,
        "ok": load_ok if level == "load" else commit_ok,
        "load_ok": load_ok,
        "commit_ok": commit_ok,
        "counts": counts,
        "by_code": dict(
            sorted(by_code.items(), key=lambda kv: (not kv[1]["load_blocking"], -kv[1]["count"]))
        ),
        "findings": ordered[:cap],
        "findings_total": len(ordered),
        "findings_truncated": len(ordered) > cap,
        "summary": summary,
        "read_only": True,
        "report_author": ra_state or None,
    }
    if git_info is not None:
        out["git"] = git_info
    if res.get("warnings"):
        out["warnings"] = res["warnings"]
    if not load_ok:
        out["verdict"] = "DESKTOP WILL NOT OPEN THIS: fix the load_blocking findings first"
    elif not commit_ok:
        out["verdict"] = "opens, but errors remain (not safe to commit)"
    elif errors:
        # Scoped commit gate: the errors are outside this change, so it is not "clean".
        out["verdict"] = f"clean for this change; {len(errors)} pre-existing error(s) elsewhere"
    else:
        out["verdict"] = "clean"
    return out


__all__ = ["run_preflight"]
