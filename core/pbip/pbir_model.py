"""
PBIR / PBIP shape model -- pure functions, no Desktop, no MCP.

One place that knows WHERE things live in a PBIP project, so the debugger,
authoring, analysis and graph code stop re-deriving (and mis-deriving) it.
Every accessor here was checked against the real R0105 / R0201 / S01 / S02
projects (2026-09-22 deep review) and is pinned by the synthetic golden
fixture in ``tests/fixtures/pbir_golden/`` (see its README for the evidence
behind each shape).

Shapes this module owns (finding IDs in brackets):

* Pointer chain ``.pbip -> artifacts[].report.path -> <report>/definition.pbir
  -> datasetReference.byPath.path`` (thin reports bound to a shared model,
  several reports per model, model-only ``.pbip`` naming a missing report,
  folders with several ``.Report`` dirs) [G-06, H-13].
* ``definition/pages/pages.json`` -- NOT ``definition/pages.json`` [H-03].
* Visual-level filters live in the TOP-LEVEL ``filterConfig.filters`` of
  visual.json, never ``visual.filters`` [F-03, G-09, H-09, H-22].
* Inverted selection: ``filterConfig.filters[].objects.general[].properties
  .isInvertedSelectionMode`` (the common case), or visual-level
  ``visual.objects.data[].properties.isInvertedSelectionMode`` on a few
  slicers; the condition is then ``Not(In ...)`` or absent [F-04, H-10].
* Page interactions key ``visualInteractions`` (not ``interactions``) [H-08].
* Bookmark group ``children`` are plain STRING ids [H-06].
* PBIR literals ``'text'`` (quote doubled), ``12D``, ``12L``, ``12M``,
  ``true``/``false``, ``null``, ``datetime'...'`` [H-04, H-15].
* Advanced-filter ``ComparisonKind`` ints: 0=Equal, 1=GreaterThan,
  2=GreaterThanOrEqual, 3=LessThan, 4=LessThanOrEqual [F-04d, H-11].
* Bookmark targets: action buttons ``visualLink[].properties.bookmark`` and
  navigators ``objects.bookmarks[].properties.bookmarkGroup`` [G-11].
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, NamedTuple, Optional, Tuple, Union

from core.utilities.pbip_utils import find_page_folder, normalize_path

logger = logging.getLogger(__name__)

PathLike = Union[str, "os.PathLike[str]"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGES_JSON_NAME = "pages.json"
PAGE_INTERACTIONS_KEY = "visualInteractions"
LEGACY_PAGE_INTERACTIONS_KEY = "interactions"

#: PBIR ``Comparison.ComparisonKind`` (int in the file). Verified on the real
#: corpus: ``[UTL | Show In Slot B] = 1L`` is kind 0; a range slicer's
#: ``And(Comparison 2 lower, Comparison 4 upper)`` confirms 2 / 4. There is NO
#: NotEqual kind -- write ``Not(Comparison 0)`` instead.
ADVANCED_FILTER_COMPARISON: Dict[str, int] = {
    "Equal": 0,
    "GreaterThan": 1,
    "GreaterThanOrEqual": 2,
    "LessThan": 3,
    "LessThanOrEqual": 4,
}
ADVANCED_FILTER_COMPARISON_NAMES: Dict[int, str] = {
    v: k for k, v in ADVANCED_FILTER_COMPARISON.items()
}

_REPORT_SUFFIX = ".report"
_MODEL_SUFFIXES = (".semanticmodel", ".dataset")
_DISCOVERY_PRUNE = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", ".pbi", ".vs", ".idea"}
)


# ---------------------------------------------------------------------------
# Pointer chain: .pbip -> .Report -> definition.pbir -> .SemanticModel
# ---------------------------------------------------------------------------


def _canon(path: PathLike) -> Path:
    p = Path(normalize_path(os.fspath(path))).expanduser()
    try:
        return p.resolve()
    except OSError:  # pragma: no cover - resolve(strict=False) rarely raises
        return p.absolute()


def _is_report_dir(p: Path) -> bool:
    return p.name.lower().endswith(_REPORT_SUFFIX) and p.is_dir()


def _is_model_dir(p: Path) -> bool:
    return p.name.lower().endswith(_MODEL_SUFFIXES) and p.is_dir()


def _stem(p: Path) -> str:
    name = p.name
    dot = name.rfind(".")
    return name[:dot] if dot > 0 else name


def _load_json_quiet(path: Path) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logger.debug(f"pbir_model: cannot read {path}: {e}")
        return None


def _pbip_artifact_reports(pbip_file: Path) -> Tuple[List[Path], List[str]]:
    """Return (existing report dirs named by artifacts[].report.path, missing paths)."""
    data = _load_json_quiet(pbip_file)
    found: List[Path] = []
    missing: List[str] = []
    if not isinstance(data, dict):
        return found, missing
    for art in data.get("artifacts") or []:
        if not isinstance(art, dict):
            continue
        rel = (art.get("report") or {}).get("path") if isinstance(art.get("report"), dict) else None
        if not rel:
            continue
        cand = _canon(pbip_file.parent / rel)
        if cand.is_dir():
            if cand not in found:
                found.append(cand)
        else:
            missing.append(str(rel))
    return found, missing


def read_dataset_reference(report_dir: PathLike) -> Dict[str, Any]:
    """Resolve the semantic model a report is bound to.

    Reads ``<report>/definition.pbir`` ``datasetReference``:

    * ``byPath.path`` (relative to the report folder) -> ``kind="byPath"``;
      ``model_dir`` is set when the target folder exists, else ``note`` says so.
    * ``byConnection`` (non-null) -> ``kind="byConnection"``: the report is
      bound to a published/remote model, there is NO local model.
    * no ``definition.pbir`` -> falls back to the same-stem sibling
      ``<stem>.SemanticModel`` (Desktop's default layout), ``kind="sibling"``.

    Returns ``{kind, model_dir, raw_path, connection, note}`` (never raises).
    """
    rdir = _canon(report_dir)
    out: Dict[str, Any] = {
        "kind": "none",
        "model_dir": None,
        "raw_path": None,
        "connection": None,
        "note": None,
    }
    pbir = rdir / "definition.pbir"
    if not pbir.is_file():
        sibling = rdir.parent / f"{_stem(rdir)}.SemanticModel"
        if sibling.is_dir():
            out.update(
                kind="sibling",
                model_dir=str(sibling),
                note="no definition.pbir; bound to the same-stem sibling model by convention",
            )
        else:
            out["note"] = "no definition.pbir and no same-stem .SemanticModel sibling"
        return out
    data = _load_json_quiet(pbir)
    ref = data.get("datasetReference") if isinstance(data, dict) else None
    if not isinstance(ref, dict):
        out["note"] = "definition.pbir has no datasetReference"
        return out
    by_path = ref.get("byPath")
    by_conn = ref.get("byConnection")
    if isinstance(by_path, dict) and by_path.get("path"):
        raw = str(by_path["path"])
        target = _canon(rdir / raw)
        out.update(kind="byPath", raw_path=raw)
        if target.is_dir():
            out["model_dir"] = str(target)
        else:
            out["note"] = f"byPath target not found: {raw}"
        return out
    if by_conn:
        out.update(
            kind="byConnection",
            connection=by_conn,
            note="bound to a published model (byConnection) -- no local semantic model",
        )
        return out
    out["note"] = "datasetReference has neither byPath nor byConnection"
    return out


def _model_format(model_dir: Path) -> str:
    definition = model_dir / "definition"
    if definition.is_dir():
        if (definition / "model.tmdl").is_file() or (definition / "database.tmdl").is_file():
            return "tmdl"
        try:
            if any(p.suffix == ".tmdl" for p in definition.iterdir()):
                return "tmdl"
        except OSError:
            pass
    if (model_dir / "model.bim").is_file():
        return "bim"
    return "unknown"


def _pbip_for(
    folder: Path, artifact_dir: Path, cache: Dict[Path, Dict[Path, Path]]
) -> Optional[Path]:
    """The .pbip in *folder* that names *artifact_dir* (report) or shares its stem."""
    if folder not in cache:
        mapping: Dict[Path, Path] = {}
        try:
            pbips = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pbip")
        except OSError:
            pbips = []
        for pb in pbips:
            reports, _missing = _pbip_artifact_reports(pb)
            for r in reports:
                mapping.setdefault(r, pb)
        for pb in pbips:
            # Stem match wins for models (S01-SemanticModel.pbip -> S01-...SemanticModel).
            for suffix in (".SemanticModel", ".Report"):
                cand = _canon(folder / f"{pb.stem}{suffix}")
                if suffix == ".SemanticModel":
                    mapping[cand] = pb
                else:
                    mapping.setdefault(cand, pb)
        cache[folder] = mapping
    return cache[folder].get(artifact_dir)


def _report_entry(report_dir: Path, pbip_cache: Dict[Path, Dict[Path, Path]]) -> Dict[str, Any]:
    ref = read_dataset_reference(report_dir)
    definition = report_dir / "definition"
    pbir = report_dir / "definition.pbir"
    pbip = _pbip_for(report_dir.parent, report_dir, pbip_cache)
    return {
        "name": _stem(report_dir),
        "report_dir": str(report_dir),
        "definition_dir": str(definition) if definition.is_dir() else None,
        "definition_pbir": str(pbir) if pbir.is_file() else None,
        "pbip_file": str(pbip) if pbip else None,
        "dataset_reference": ref["kind"],
        "model_dir": ref["model_dir"],
        "model_path": ref["raw_path"],
        "note": ref["note"],
    }


def _model_entry(model_dir: Path, pbip_cache: Dict[Path, Dict[Path, Path]]) -> Dict[str, Any]:
    definition = model_dir / "definition"
    pbip = _pbip_for(model_dir.parent, model_dir, pbip_cache)
    return {
        "name": _stem(model_dir),
        "model_dir": str(model_dir),
        "definition_dir": str(definition) if definition.is_dir() else None,
        "format": _model_format(model_dir),
        "pbip_file": str(pbip) if pbip and pbip.stem == _stem(model_dir) else None,
        "report_dirs": [],
    }


def _child_report_dirs(folder: Path) -> List[Path]:
    try:
        return sorted(_canon(p) for p in folder.iterdir() if _is_report_dir(p))
    except OSError:
        return []


def discover_artifacts(
    root: PathLike,
    exclude_folders: Optional[Iterable[str]] = None,
    max_depth: int = 8,
) -> Tuple[List[Path], List[Path]]:
    """Find every ``*.Report`` and ``*.SemanticModel`` folder under *root*.

    Does not descend INTO an artifact folder (a stale ``definition.pbir`` or
    ``definition/pages`` inside a ``.SemanticModel`` -- seen in S01 -- is not a
    report) and prunes ``.git``/``node_modules``/... plus *exclude_folders*.
    Returns ``(report_dirs, model_dirs)``, each sorted.
    """
    base = _canon(root)
    prune = set(_DISCOVERY_PRUNE) | set(exclude_folders or ())
    reports: List[Path] = []
    models: List[Path] = []
    stack: List[Tuple[Path, int]] = [(base, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir():
                    continue
            except OSError:
                continue
            name_l = e.name.lower()
            if name_l.endswith(_REPORT_SUFFIX):
                reports.append(_canon(e.path))
            elif name_l.endswith(_MODEL_SUFFIXES):
                models.append(_canon(e.path))
            elif e.name not in prune and depth < max_depth:
                stack.append((Path(e.path), depth + 1))
    return sorted(set(reports)), sorted(set(models))


def find_reports_bound_to_model(
    model_dir: PathLike, search_dirs: Optional[Iterable[PathLike]] = None
) -> List[str]:
    """Report folders whose ``definition.pbir`` binds to *model_dir*.

    Searches the model's parent folder (the PBIP layout keeps thin reports
    beside the model) plus any *search_dirs*. Use this -- never
    ``sorted(glob('*.Report'))[0]`` -- to cascade a model rename into EVERY
    bound report (H-13).
    """
    target = _canon(model_dir)
    folders = [target.parent] + [_canon(d) for d in (search_dirs or ())]
    out: List[str] = []
    for folder in folders:
        for rdir in _child_report_dirs(folder):
            ref = read_dataset_reference(rdir)
            if ref["model_dir"] and _canon(ref["model_dir"]) == target and str(rdir) not in out:
                out.append(str(rdir))
    return out


def _find_artifact_ancestor(p: Path) -> Optional[Path]:
    for anc in [p] + list(p.parents):
        if _is_report_dir(anc) or _is_model_dir(anc):
            return anc
    return None


def resolve_pbip(
    path: PathLike, *, exclude_folders: Optional[Iterable[str]] = None
) -> Dict[str, Any]:
    """Resolve any PBIP-shaped path to its reports and semantic models.

    Accepts a ``.pbip`` file, a ``.Report`` / ``.SemanticModel`` folder, a
    ``definition/`` folder (or any file/folder inside an artifact), or a plain
    folder (searched recursively, see ``discover_artifacts``).

    Returns::

        {
          "input": str, "pbip_file": str|None,
          "target": "report" | "model" | "folder" | "none",
          "reports": [{name, report_dir, definition_dir, definition_pbir,
                       pbip_file, dataset_reference ("byPath"|"byConnection"|
                       "sibling"|"none"), model_dir, model_path, note}],
          "models":  [{name, model_dir, definition_dir, format ("tmdl"|"bim"|
                       "unknown"), pbip_file, report_dirs: [...]}],
          "warnings": [str],
        }

    Rules (all verified on the real G01/G02 layout):

    * ``R0105.pbip`` -> ``artifacts[].report.path`` -> ``R0105.Report`` ->
      ``definition.pbir byPath ../S01-SemanticModel.SemanticModel``
      (target ``report``: that report + its model only).
    * A ``.pbip`` whose stem names a sibling ``.SemanticModel`` and whose
      artifact report has a DIFFERENT stem or is missing (``S01-SemanticModel
      .pbip -> S01-SemanticModel.Report`` [missing]; ``S02-SemanticModel.pbip
      -> R0200-SemanticModel.Report``) is a MODEL project: target ``model``,
      reports = every sibling report bound to that model.
    * A ``.SemanticModel`` folder -> target ``model`` (same report rule).
    * A plain folder -> target ``folder``: every report + model found.
    * ``model["report_dirs"]`` lists the reports in ``reports`` bound to it.
    """
    p = _canon(path)
    res: Dict[str, Any] = {
        "input": str(p),
        "pbip_file": None,
        "target": "none",
        "reports": [],
        "models": [],
        "warnings": [],
    }
    if not p.exists():
        res["warnings"].append(f"path does not exist: {p}")
        return res

    pbip_cache: Dict[Path, Dict[Path, Path]] = {}
    report_dirs: List[Path] = []
    model_dirs: List[Path] = []

    if p.is_file() and p.suffix.lower() == ".pbip":
        res["pbip_file"] = str(p)
        arts, missing = _pbip_artifact_reports(p)
        for m in missing:
            res["warnings"].append(f"{p.name}: artifact report not found: {m}")
        sibling_model = p.parent / f"{p.stem}.SemanticModel"
        same_stem_report = any(_stem(a) == p.stem for a in arts)
        if same_stem_report:
            res["target"] = "report"
            report_dirs = arts
        elif sibling_model.is_dir():
            res["target"] = "model"
            model_dirs = [_canon(sibling_model)]
        elif arts:
            res["target"] = "report"
            report_dirs = arts
        elif (p.parent / f"{p.stem}.Report").is_dir():
            res["target"] = "report"
            report_dirs = [_canon(p.parent / f"{p.stem}.Report")]
        else:
            res["warnings"].append(f"{p.name}: names no existing report or model")
            return res
    else:
        anc = _find_artifact_ancestor(p if p.is_dir() else p.parent)
        if anc is not None and _is_report_dir(anc):
            res["target"] = "report"
            report_dirs = [anc]
        elif anc is not None:
            res["target"] = "model"
            model_dirs = [anc]
        elif p.is_dir():
            res["target"] = "folder"
            report_dirs, model_dirs = discover_artifacts(p, exclude_folders)
        else:
            res["warnings"].append(f"not a PBIP path: {p}")
            return res

    if res["target"] == "model":
        for m in model_dirs:
            for r in find_reports_bound_to_model(m):
                rp = Path(r)
                if rp not in report_dirs:
                    report_dirs.append(rp)

    reports = [_report_entry(r, pbip_cache) for r in report_dirs]
    for r in reports:
        if r["model_dir"]:
            mp = _canon(r["model_dir"])
            if mp not in model_dirs:
                model_dirs.append(mp)
        elif r["note"]:
            res["warnings"].append(f"{r['name']}: {r['note']}")
    models = [_model_entry(m, pbip_cache) for m in model_dirs]
    for m in models:
        mp = _canon(m["model_dir"])
        m["report_dirs"] = [
            r["report_dir"] for r in reports if r["model_dir"] and _canon(r["model_dir"]) == mp
        ]
    res["reports"] = reports
    res["models"] = models
    if res["pbip_file"] is None:
        if res["target"] == "report" and len(reports) == 1:
            res["pbip_file"] = reports[0]["pbip_file"]
        elif res["target"] == "model" and len(models) == 1:
            res["pbip_file"] = models[0]["pbip_file"]
    return res


# ---------------------------------------------------------------------------
# Report definition paths
# ---------------------------------------------------------------------------


def pages_json_path(definition_dir: PathLike) -> Path:
    """``<definition>/pages/pages.json`` -- the ONLY location (H-03)."""
    return Path(definition_dir) / "pages" / PAGES_JSON_NAME


class VisualRecord(NamedTuple):
    """One visual yielded by ``iter_visuals``."""

    page_dir: Path
    visual_dir: Path
    path: Path  # visual.json
    data: Dict[str, Any]


def iter_visuals(
    definition_dir: PathLike,
    page: Optional[str] = None,
    *,
    visual_id: Optional[str] = None,
) -> Iterator[VisualRecord]:
    """Lazily yield the visuals of one page (or every page), loading only what's needed.

    * ``page`` = page folder id (``.Page`` suffix tolerated) or EXACT display
      name (case-insensitive). The page is resolved first, so only that
      page's visual.json files are read (H-30: never load the whole report
      to find one page). Raises ``LookupError`` if the page doesn't exist.
    * ``visual_id`` = exact visual folder id: short-circuits to that single
      visual.json (no other visual is read).
    * Each visual.json is parsed only when the generator reaches it, so a
      caller that ``break``s early pays for what it consumed. Unreadable
      files are skipped with a warning.
    """
    ddir = Path(definition_dir)
    pages_dir = ddir / "pages"
    if page is not None:
        pdir = find_page_folder(ddir, page, allow_unique_startswith=False)
        if pdir is None:
            raise LookupError(f"page not found: {page!r} in {ddir}")
        page_dirs = [pdir]
    else:
        try:
            page_dirs = sorted(p for p in pages_dir.iterdir() if p.is_dir())
        except OSError:
            return
    for pdir in page_dirs:
        vroot = pdir / "visuals"
        if visual_id is not None:
            candidates = [vroot / visual_id]
        else:
            try:
                candidates = sorted(v for v in vroot.iterdir() if v.is_dir())
            except OSError:
                continue
        for vdir in candidates:
            vpath = vdir / "visual.json"
            if not vpath.is_file():
                continue
            data = _load_json_quiet(vpath)
            if not isinstance(data, dict):
                logger.warning(f"iter_visuals: unreadable visual.json skipped: {vpath}")
                continue
            yield VisualRecord(pdir, vdir, vpath, data)


# ---------------------------------------------------------------------------
# Visual-level filters (F-03 / G-09 / H-09 / H-22)
# ---------------------------------------------------------------------------


def get_visual_filters(visual_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Visual-level filters: top-level ``filterConfig.filters`` of visual.json.

    PBIR never uses ``visual.filters`` (0 occurrences in the corpus vs 8,080
    ``filterConfig`` filters); entries found there (written by the old buggy
    engines) are appended as a legacy fallback, de-duplicated by ``name``.
    Returns a NEW list whose elements are the live filter dicts -- edit an
    element in place, or pass a modified list to ``set_visual_filters``.
    """
    out: List[Dict[str, Any]] = []
    fc = visual_json.get("filterConfig")
    if isinstance(fc, dict) and isinstance(fc.get("filters"), list):
        out.extend(f for f in fc["filters"] if isinstance(f, dict))
    visual = visual_json.get("visual")
    legacy = visual.get("filters") if isinstance(visual, dict) else None
    if isinstance(legacy, list):
        names = {f.get("name") for f in out if f.get("name")}
        for f in legacy:
            if isinstance(f, dict) and (not f.get("name") or f.get("name") not in names):
                out.append(f)
    return out


def set_visual_filters(
    visual_json: Dict[str, Any], filters: Iterable[Dict[str, Any]]
) -> Dict[str, Any]:
    """Write visual-level filters to top-level ``filterConfig.filters``.

    Other ``filterConfig`` keys (``filterSortOrder``...) are kept; an empty
    list removes ``filters`` (and ``filterConfig`` if nothing else is left,
    matching visuals Desktop writes without filters). The invalid legacy
    ``visual.filters`` key is removed. Mutates and returns *visual_json*.
    """
    items = [f for f in filters if isinstance(f, dict)]
    fc = visual_json.get("filterConfig")
    if not isinstance(fc, dict):
        fc = {}
    if items:
        fc["filters"] = items
    else:
        fc.pop("filters", None)
    if fc:
        visual_json["filterConfig"] = fc
    else:
        visual_json.pop("filterConfig", None)
    visual = visual_json.get("visual")
    if isinstance(visual, dict):
        visual.pop("filters", None)
    return visual_json


def get_slicer_selection_filter(visual_json: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A slicer's own persisted selection: ``visual.objects.general[].properties.filter.filter``.

    This is the query filter (``{Version, From, Where}``) Desktop stores for
    the slicer's current selection -- separate from ``filterConfig``. Returns
    the live dict or None.
    """
    visual = visual_json.get("visual")
    objs = visual.get("objects") if isinstance(visual, dict) else None
    for entry in (objs or {}).get("general") or []:
        props = entry.get("properties") if isinstance(entry, dict) else None
        wrapper = props.get("filter") if isinstance(props, dict) else None
        if isinstance(wrapper, dict):
            inner = wrapper.get("filter")
            if isinstance(inner, dict):
                return inner
    return None


# ---------------------------------------------------------------------------
# Inverted selection polarity (F-04 / H-10)
# ---------------------------------------------------------------------------


def _literal_of(prop: Any) -> Optional[str]:
    if not isinstance(prop, dict):
        return None
    lit = (prop.get("expr") or {}).get("Literal") if isinstance(prop.get("expr"), dict) else None
    value = lit.get("Value") if isinstance(lit, dict) else None
    return value if isinstance(value, str) else None


def _query_filter(filter_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The ``{Version, From, Where}`` part of any filter shape."""
    if not isinstance(filter_obj, dict):
        return None
    if "Where" in filter_obj or "From" in filter_obj:
        return filter_obj
    inner = filter_obj.get("filter")
    if isinstance(inner, dict):
        if "Where" in inner or "From" in inner:
            return inner
        nested = inner.get("filter")  # objects.general[].properties.filter wrapper
        if isinstance(nested, dict):
            return nested
    return None


def _conditions(filter_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    q = _query_filter(filter_obj)
    where = q.get("Where") if isinstance(q, dict) else None
    out: List[Dict[str, Any]] = []
    for w in where or []:
        cond = w.get("Condition") if isinstance(w, dict) else None
        if isinstance(cond, dict):
            out.append(cond)
    return out


def _flag_in(entries: Any) -> Optional[bool]:
    for entry in entries or []:
        props = entry.get("properties") if isinstance(entry, dict) else None
        if isinstance(props, dict) and "isInvertedSelectionMode" in props:
            value = _literal_of(props["isInvertedSelectionMode"])
            if value is not None:
                return value.strip().lower() == "true"
    return None


def _is_categorical(filter_obj: Dict[str, Any]) -> bool:
    ftype = filter_obj.get("type")
    if ftype not in (None, "Categorical"):
        return False
    for cond in _conditions(filter_obj):
        key = next(iter(cond), None)
        if key == "Not":
            inner = (cond.get("Not") or {}).get("Expression") or {}
            key = next(iter(inner), None)
        if key != "In":
            return False
    return True


def is_inverted_selection(
    filter_obj: Dict[str, Any], visual_json: Optional[Dict[str, Any]] = None
) -> bool:
    """True when *filter_obj* is in inverted-selection ("select all except") mode.

    Looks at the filter's own ``objects.general[].properties
    .isInvertedSelectionMode`` first (R0105: 194 of 196 cases; R0201: 40 of
    41), then -- for categorical filters only -- the visual-level
    ``visual.objects.data[].properties.isInvertedSelectionMode`` (the rest;
    ``general`` / ``selection`` are checked defensively).
    """
    objects = filter_obj.get("objects") if isinstance(filter_obj, dict) else None
    own = _flag_in((objects or {}).get("general")) if isinstance(objects, dict) else None
    if own is not None:
        return own
    if visual_json is None or not _is_categorical(filter_obj):
        return False
    visual = visual_json.get("visual")
    vobjs = visual.get("objects") if isinstance(visual, dict) else None
    if not isinstance(vobjs, dict):
        return False
    for section in ("data", "general", "selection"):
        flag = _flag_in(vobjs.get(section))
        if flag is not None:
            return flag
    return False


def filter_polarity(
    filter_obj: Dict[str, Any], visual_json: Optional[Dict[str, Any]] = None
) -> str:
    """``"exclude"`` or ``"include"`` for a filterConfig entry or a query filter.

    ``"exclude"`` when the filter is in inverted-selection mode (see
    ``is_inverted_selection``) or any Where condition is ``Not(In ...)``.
    For an exclude filter the ``In.Values`` list names what is EXCLUDED:
    removing an entry WIDENS the filter, and an inverted filter with no
    ``Where`` at all means "exclude nothing" (everything selected). Never
    write ``Values: []`` -- Desktop rejects it (``InvalidQueryInExpression``).
    """
    if is_inverted_selection(filter_obj, visual_json):
        return "exclude"
    for cond in _conditions(filter_obj):
        if "Not" in cond:
            inner = (cond.get("Not") or {}).get("Expression")
            if isinstance(inner, dict) and "In" in inner:
                return "exclude"
    return "include"


def get_filter_in_values(filter_obj: Dict[str, Any]) -> Optional[List[Any]]:
    """The live ``In.Values`` rows of the first ``In`` / ``Not(In)`` condition, or None.

    Read the polarity with ``filter_polarity`` before editing the list.
    TopN filters use ``In.Table`` (a subquery), not ``Values``: None.
    """
    for cond in _conditions(filter_obj):
        node = cond
        if "Not" in node:
            node = (node.get("Not") or {}).get("Expression") or {}
        inn = node.get("In") if isinstance(node, dict) else None
        if isinstance(inn, dict) and isinstance(inn.get("Values"), list):
            return inn["Values"]
    return None


# ---------------------------------------------------------------------------
# Page interactions (H-08)
# ---------------------------------------------------------------------------


def get_page_interactions(page_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``page.json`` ``visualInteractions`` (``{source, target, type}`` items).

    Tolerates the legacy/invalid ``interactions`` key on read (entries
    de-duplicated by ``(source, target)``). Returns a new list.
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for key in (PAGE_INTERACTIONS_KEY, LEGACY_PAGE_INTERACTIONS_KEY):
        items = page_json.get(key)
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            ident = (it.get("source"), it.get("target"))
            if ident in seen:
                continue
            seen.add(ident)
            out.append(it)
    return out


def set_page_interactions(
    page_json: Dict[str, Any], interactions: Iterable[Dict[str, Any]]
) -> Dict[str, Any]:
    """Write ``visualInteractions`` (removing the legacy ``interactions`` key).

    An empty list removes the key. Mutates and returns *page_json*.
    """
    items = [i for i in interactions if isinstance(i, dict)]
    page_json.pop(LEGACY_PAGE_INTERACTIONS_KEY, None)
    if items:
        page_json[PAGE_INTERACTIONS_KEY] = items
    else:
        page_json.pop(PAGE_INTERACTIONS_KEY, None)
    return page_json


# ---------------------------------------------------------------------------
# Bookmarks (H-06 / G-11)
# ---------------------------------------------------------------------------


def bookmark_group_children(item: Dict[str, Any]) -> List[str]:
    """A bookmarks.json group item's ``children`` as a list of bookmark ids.

    Real files store plain strings (R0201: 163, R0105: 334, zero objects);
    legacy ``{"name": id}`` dicts written by the old engine are tolerated.
    """
    out: List[str] = []
    children = item.get("children") if isinstance(item, dict) else None
    for c in children if isinstance(children, list) else []:
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, dict) and isinstance(c.get("name"), str):
            out.append(c["name"])
    return out


def set_bookmark_group_children(item: Dict[str, Any], children: Iterable[str]) -> Dict[str, Any]:
    """Write a group's ``children`` as plain string ids. Mutates and returns *item*."""
    ids: List[str] = []
    for c in children:
        if isinstance(c, str):
            ids.append(c)
        elif isinstance(c, dict) and isinstance(c.get("name"), str):
            ids.append(c["name"])
        else:
            raise TypeError(f"bookmark group child must be a string id, got {c!r}")
    item["children"] = ids
    return item


def bookmark_membership(bookmarks_json: Dict[str, Any]) -> Dict[str, Any]:
    """Index ``bookmarks.json``: ``{"groups": {group: [ids]}, "parent": {id: group|None}}``.

    ``items`` entries with ``children`` are groups; the rest are top-level
    bookmarks (parent None).
    """
    groups: Dict[str, List[str]] = {}
    parent: Dict[str, Optional[str]] = {}
    for item in bookmarks_json.get("items") or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        if "children" in item:
            kids = bookmark_group_children(item)
            groups[item["name"]] = kids
            for k in kids:
                parent[k] = item["name"]
        else:
            parent.setdefault(item["name"], None)
    return {"groups": groups, "parent": parent}


def get_bookmark_references(visual_json: Dict[str, Any]) -> Dict[str, List[str]]:
    """Bookmark ids / groups a visual points at (G-11).

    * Action buttons: ``visual.visualContainerObjects.visualLink[]`` (or
      ``visual.objects.visualLink[]``) ``properties.bookmark`` =
      ``{"expr":{"Literal":{"Value":"'<id>'"}}}`` when ``type`` is
      ``'Bookmark'`` (or absent).
    * Bookmark navigators: ``visual.objects.bookmarks[].properties
      .bookmarkGroup`` (group name) and ``selectedBookmark``.

    Returns ``{"bookmarks": [...], "bookmark_groups": [...],
    "selected_bookmarks": [...]}`` (ids unquoted, de-duplicated, in order).
    """
    out: Dict[str, List[str]] = {"bookmarks": [], "bookmark_groups": [], "selected_bookmarks": []}

    def add(key: str, lit: Optional[str]) -> None:
        if lit is None:
            return
        value, kind = decode_literal(lit)
        ident = value if kind == "string" else lit
        if isinstance(ident, str) and ident and ident not in out[key]:
            out[key].append(ident)

    visual = visual_json.get("visual")
    if not isinstance(visual, dict):
        return out
    for container in (visual.get("visualContainerObjects"), visual.get("objects")):
        if not isinstance(container, dict):
            continue
        for entry in container.get("visualLink") or []:
            props = entry.get("properties") if isinstance(entry, dict) else None
            if not isinstance(props, dict) or "bookmark" not in props:
                continue
            link_type = _literal_of(props.get("type"))
            if link_type is None or decode_literal(link_type)[0] == "Bookmark":
                add("bookmarks", _literal_of(props.get("bookmark")))
    objs = visual.get("objects")
    for entry in (objs.get("bookmarks") if isinstance(objs, dict) else None) or []:
        props = entry.get("properties") if isinstance(entry, dict) else None
        if isinstance(props, dict):
            add("bookmark_groups", _literal_of(props.get("bookmarkGroup")))
            add("selected_bookmarks", _literal_of(props.get("selectedBookmark")))
    return out


# ---------------------------------------------------------------------------
# PBIR literals (H-04 / H-15 / H-11)
# ---------------------------------------------------------------------------

_KIND_ALIASES = {
    "auto": "auto",
    "string": "string",
    "text": "string",
    "str": "string",
    "double": "double",
    "number": "double",
    "float": "double",
    "d": "double",
    "int64": "int64",
    "int": "int64",
    "integer": "int64",
    "long": "int64",
    "whole": "int64",
    "l": "int64",
    "decimal": "decimal",
    "currency": "decimal",
    "fixed": "decimal",
    "m": "decimal",
    "bool": "bool",
    "boolean": "bool",
    "datetime": "datetime",
    "date": "datetime",
    "null": "null",
}

_RE_INT64 = re.compile(r"^-?\d+L$")
_RE_DOUBLE = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?D$")
_RE_DECIMAL = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?M$")
_RE_DATETIME = re.compile(r"^datetime'(.*)'$", re.DOTALL)


def _plain_number(value: Union[int, float, Decimal]) -> str:
    """Render a number without exponent notation (``1e-05`` -> ``0.00001``)."""
    if isinstance(value, bool):  # pragma: no cover - guarded by callers
        raise TypeError("bool is not a number literal")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite number cannot be a PBIR literal: {value!r}")
        if value.is_integer() and abs(value) < 1e16:
            return str(int(value))
        text = repr(value)
        if "e" in text or "E" in text:
            text = format(Decimal(text), "f")
        return text
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"non-finite number cannot be a PBIR literal: {value!r}")
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".") or "0"
        return text
    raise TypeError(f"not a number: {value!r}")


def _to_number(value: Any) -> Union[int, float, Decimal]:
    if isinstance(value, bool):
        raise TypeError("bool is not a number literal")
    if isinstance(value, (int, float, Decimal)):
        return value
    if isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except InvalidOperation as e:
            raise ValueError(f"not a number: {value!r}") from e
        return int(d) if d == d.to_integral_value() and "." not in value else d
    raise TypeError(f"not a number: {value!r}")


def encode_literal(value: Any, kind: str = "auto") -> str:
    """Encode a Python value as a PBIR ``Literal.Value`` string.

    ``kind``: ``string`` (``'O''Neil'`` -- quotes doubled), ``double``
    (``12D``), ``int64`` (``12L``: REQUIRED for Int64 filter columns),
    ``decimal`` (``12M``), ``bool`` (``true``/``false``), ``datetime``
    (``datetime'2024-01-31T00:00:00'``), ``null``, or ``auto``: None -> null,
    bool -> bool, int/float/Decimal -> double (the formatting-property
    default, e.g. ``fontSize 14D``), datetime/date -> datetime, str -> string
    (ALWAYS quoted -- a str is never emitted bare). Numbers never use
    exponent notation. Raises ValueError/TypeError on a value that doesn't
    fit the kind.
    """
    k = _KIND_ALIASES.get(str(kind).lower())
    if k is None:
        raise ValueError(f"unknown literal kind: {kind!r}")
    if k == "auto":
        if value is None:
            k = "null"
        elif isinstance(value, bool):
            k = "bool"
        elif isinstance(value, (int, float, Decimal)):
            k = "double"
        elif isinstance(value, (_dt.datetime, _dt.date)):
            k = "datetime"
        elif isinstance(value, str):
            k = "string"
        else:
            raise TypeError(f"cannot encode {type(value).__name__} as a PBIR literal")
    if k == "null":
        return "null"
    if k == "string":
        text = value if isinstance(value, str) else str(value)
        return "'" + text.replace("'", "''") + "'"
    if k == "bool":
        if isinstance(value, str):
            if value.strip().lower() not in ("true", "false"):
                raise ValueError(f"not a boolean: {value!r}")
            return value.strip().lower()
        return "true" if bool(value) else "false"
    if k == "double":
        return _plain_number(_to_number(value)) + "D"
    if k == "decimal":
        num = _to_number(value)
        return _plain_number(Decimal(repr(num)) if isinstance(num, float) else num) + "M"
    if k == "int64":
        num = _to_number(value)
        if isinstance(num, float):
            if not num.is_integer():
                raise ValueError(f"int64 literal needs an integral value: {value!r}")
            num = int(num)
        elif isinstance(num, Decimal):
            if num != num.to_integral_value():
                raise ValueError(f"int64 literal needs an integral value: {value!r}")
            num = int(num)
        return f"{num}L"
    # datetime
    if isinstance(value, _dt.datetime):
        dt = value.replace(tzinfo=None)
    elif isinstance(value, _dt.date):
        dt = _dt.datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        dt = _dt.datetime.fromisoformat(value.strip())
    else:
        raise TypeError(f"not a datetime: {value!r}")
    return (
        "datetime'"
        + dt.isoformat(timespec="seconds" if not dt.microsecond else "milliseconds")
        + "'"
    )


def decode_literal(text: Any) -> Tuple[Any, str]:
    """Decode a PBIR ``Literal.Value`` string -> ``(value, kind)``.

    ``'O''Neil'`` -> ``("O'Neil", "string")``, ``12D`` -> ``(12.0, "double")``,
    ``12L`` -> ``(12, "int64")``, ``1.5M`` -> ``(Decimal("1.5"), "decimal")``,
    ``true`` -> ``(True, "bool")``, ``null`` -> ``(None, "null")``,
    ``datetime'2024-01-31T00:00:00'`` -> ``(datetime, "datetime")``. Anything
    else -> ``(text, "unknown")``. ``encode_literal(*decode_literal(s))``
    round-trips every shape seen in the corpus.
    """
    if not isinstance(text, str):
        return text, "unknown"
    s = text.strip()
    if len(s) >= 2 and s.startswith("'") and s.endswith("'"):
        return s[1:-1].replace("''", "'"), "string"
    low = s.lower()
    if low == "null":
        return None, "null"
    if low in ("true", "false"):
        return low == "true", "bool"
    if _RE_INT64.match(s):
        return int(s[:-1]), "int64"
    if _RE_DOUBLE.match(s):
        return float(s[:-1]), "double"
    if _RE_DECIMAL.match(s):
        return Decimal(s[:-1]), "decimal"
    m = _RE_DATETIME.match(s)
    if m:
        try:
            return _dt.datetime.fromisoformat(m.group(1)), "datetime"
        except ValueError:
            return m.group(1), "datetime"
    return text, "unknown"
