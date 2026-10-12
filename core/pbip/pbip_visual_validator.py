"""
PBIP Visual Reference Validator

Scans a PBIP report's visual.json (and page / report filter) field references
and cross-checks them against the semantic model. Reports references to
objects that do not exist — typically left behind by renames or deletions.

Review G-10 (2026-09-22):
* the model comes from the report's OWN model (``definition.pbir`` byPath,
  via :func:`core.pbip.pbir_model.resolve_pbip`) parsed with the unified
  TMDL parser — the regex slow path stored bare measure names only, so every
  ``Table[Measure]`` reference was "broken" (3,210 false hits on R0105);
* a table-qualified column must exist in THAT table (the bare-name fallback
  hid mis-qualified references);
* every report the path resolves to is scanned (a folder with several
  ``.Report`` folders used to scan whichever ``iterdir`` returned first);
* references are found with :func:`core.pbip.pbir_field_refs.extract_field_refs`
  (``SourceRef.Source`` aliases, hierarchy levels, auto date/time variations).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.pbip.pbir_field_refs import FieldRef, extract_field_refs

logger = logging.getLogger(__name__)


def _n(name: Any) -> str:
    return " ".join(str(name or "").lower().split())


class _ModelNames:
    def __init__(self, model: Dict[str, Any]) -> None:
        self.tables: Dict[str, str] = {}
        self.columns: Dict[Tuple[str, str], str] = {}
        self.measures: Dict[Tuple[str, str], str] = {}
        self.measure_home: Dict[str, str] = {}
        self.hier_levels: Dict[Tuple[str, str], set] = {}
        for t in model.get("tables", []) or []:
            tn = t.get("name", "")
            if not tn:
                continue
            tk = _n(tn)
            self.tables[tk] = tn
            for c in t.get("columns", []) or []:
                if c.get("name"):
                    self.columns[(tk, _n(c["name"]))] = c["name"]
            for m in t.get("measures", []) or []:
                if m.get("name"):
                    self.measures[(tk, _n(m["name"]))] = m["name"]
                    self.measure_home.setdefault(_n(m["name"]), tn)
            for h in t.get("hierarchies", []) or []:
                self.hier_levels[(tk, _n(h.get("name")))] = {
                    _n(lv.get("name")) for lv in h.get("levels", []) or []
                }

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "known_measures": len(self.measures),
            "known_columns": len(self.columns),
            "known_tables": len(self.tables),
        }

    def check(self, ref: FieldRef) -> Tuple[bool, str]:
        """``(is_broken, reason)``."""
        if not ref.table:
            return True, f"'{ref.name}' has an unresolved SourceRef (no Entity / unknown alias)"
        tk = _n(ref.table)
        if tk not in self.tables:
            return True, f"Table '{ref.table}' not found in model"
        key = (tk, _n(ref.name))
        if ref.kind == "measure":
            if key in self.measures:
                return False, ""
            if key in self.columns:
                return True, f"'{ref.table}'[{ref.name}] is a column, bound as a measure"
            home = self.measure_home.get(_n(ref.name))
            if home:
                return True, f"Measure '{ref.name}' lives in table '{home}', not '{ref.table}'"
            return True, f"Measure '{ref.table}[{ref.name}]' not found in model"
        if ref.kind == "column":
            if key in self.columns:
                return False, ""
            if key in self.measures:
                return True, f"'{ref.table}'[{ref.name}] is a measure, bound as a column"
            return True, f"Column '{ref.table}[{ref.name}]' not found in model"
        if ref.kind in ("hierarchy", "hierarchy_level"):
            if ref.variation_column:
                if (tk, _n(ref.variation_column)) in self.columns:
                    return False, ""
                return (
                    True,
                    f"Column '{ref.table}[{ref.variation_column}]' (date hierarchy) not found",
                )
            levels = self.hier_levels.get(key)
            if levels is None:
                return True, f"Hierarchy '{ref.table}'.'{ref.name}' not found in model"
            if ref.kind == "hierarchy_level" and _n(ref.level) not in levels:
                return (
                    True,
                    f"Level '{ref.level}' not found in hierarchy '{ref.table}'.'{ref.name}'",
                )
            return False, ""
        # selector metadata: a column or a measure of that table
        if key in self.columns or key in self.measures:
            return False, ""
        return True, f"Field '{ref.table}[{ref.name}]' (selector) not found as measure or column"


def _load_model(model_dir: Optional[str]) -> Optional[Dict[str, Any]]:
    if not model_dir:
        return None
    try:
        from core.tmdl.unified_parser import UnifiedTmdlParser

        return UnifiedTmdlParser(model_dir).parse_full_model().to_dict()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not parse model %s: %s", model_dir, e)
        return None


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read %s: %s", path, e)
        return None


def scan_broken_visual_references(
    pbip_path: str, tmdl_model: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Scan every visual (plus page / report filters) of the report(s) *pbip_path*
    resolves to for field references that do not exist in the bound model.

    Args:
        pbip_path: ``.pbip`` file, ``.Report`` / ``definition`` folder, a model
            (every report bound to it) or a folder (every report in it).
        tmdl_model: Optional pre-parsed model dict. Used only when every
            scanned report binds to the same model.
    """
    from core.pbip.pbir_model import resolve_pbip

    res = resolve_pbip(pbip_path)
    reports = [r for r in res.get("reports") or [] if r.get("definition_dir")]
    if not reports:
        return {
            "success": False,
            "error": f"Could not find a report for: {pbip_path}",
            "warnings": res.get("warnings", []),
        }

    model_dirs = {r.get("model_dir") for r in reports}
    models: Dict[Optional[str], Optional[_ModelNames]] = {}
    for md in model_dirs:
        data = (
            tmdl_model
            if (tmdl_model and tmdl_model.get("tables") and len(model_dirs) == 1)
            else _load_model(md)
        )
        models[md] = _ModelNames(data) if data else None

    broken_refs: List[Dict[str, Any]] = []
    total_visuals = 0
    total_refs_checked = 0
    scanned: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []

    for rep in reports:
        names = models.get(rep.get("model_dir"))
        if names is None:
            skipped.append(
                {
                    "report": rep["name"],
                    "reason": rep.get("note") or "no local semantic model (byConnection?)",
                }
            )
            continue
        definition = Path(rep["definition_dir"])
        rep_visuals = 0

        def record(page: str, vid: str, title: str, vtype: str, refs: List[FieldRef]) -> None:
            nonlocal total_refs_checked
            seen = set()
            for ref in refs:
                k = (ref.kind, ref.table, ref.name, ref.level, ref.variation_column)
                if k in seen:
                    continue
                seen.add(k)
                total_refs_checked += 1
                bad, reason = names.check(ref)
                if bad:
                    broken_refs.append(
                        {
                            "report": rep["name"],
                            "page": page,
                            "visual_id": vid,
                            "visual_title": title,
                            "visual_type": vtype,
                            "table": ref.table,
                            "field": (
                                ref.name
                                if ref.kind != "hierarchy_level"
                                else f"{ref.name}.{ref.level}"
                            ),
                            "ref_type": ref.kind,
                            "location": ref.location,
                            "reason": reason,
                        }
                    )

        report_json = (
            _read_json(definition / "report.json")
            if (definition / "report.json").exists()
            else None
        )
        if report_json:
            record(
                "(report)",
                "",
                "",
                "report filters",
                extract_field_refs(
                    report_json.get("filterConfig") or {}, base_path=("filterConfig",)
                ),
            )
        pages_dir = definition / "pages"
        if pages_dir.is_dir():
            for page_folder in sorted(pages_dir.iterdir()):
                if not page_folder.is_dir():
                    continue
                page_json = (
                    _read_json(page_folder / "page.json")
                    if (page_folder / "page.json").exists()
                    else None
                )
                page_name = (page_json or {}).get("displayName", page_folder.name)
                if page_json:
                    record(
                        page_name,
                        "",
                        "",
                        "page filters",
                        extract_field_refs(
                            page_json.get("filterConfig") or {}, base_path=("filterConfig",)
                        ),
                    )
                visuals_dir = page_folder / "visuals"
                if not visuals_dir.is_dir():
                    continue
                for visual_folder in sorted(visuals_dir.iterdir()):
                    vj = visual_folder / "visual.json"
                    if not vj.is_file():
                        continue
                    data = _read_json(vj)
                    if data is None:
                        continue
                    total_visuals += 1
                    rep_visuals += 1
                    record(
                        page_name,
                        visual_folder.name,
                        _extract_visual_title(data),
                        (data.get("visual") or {}).get("visualType", "unknown"),
                        extract_field_refs(data),
                    )
        scanned.append(
            {"report": rep["name"], "visuals": rep_visuals, "model": rep.get("model_dir")}
        )

    affected_pages = {(r["report"], r["page"]) for r in broken_refs}
    affected_visuals = {(r["report"], r["visual_id"]) for r in broken_refs if r["visual_id"]}
    first = next((m for m in models.values() if m is not None), None)
    return {
        "success": True,
        "broken_references": broken_refs,
        "reports_scanned": scanned,
        "reports_skipped": skipped,
        "summary": {
            "total_visuals_scanned": total_visuals,
            "total_references_checked": total_refs_checked,
            "broken_count": len(broken_refs),
            "affected_pages": len(affected_pages),
            "affected_visuals": len(affected_visuals),
            "reports_scanned": len(scanned),
            # 'objects'/'vco'/'selector' hits are formatting leftovers (reference
            # labels, CF rules) Desktop ignores; 'query'/'filter'/'sort' break data
            "by_location": _by_location(broken_refs),
        },
        "model_stats": first.stats if first else {},
        "warnings": res.get("warnings", []),
    }


def _by_location(refs: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in refs:
        loc = str(r.get("location") or "other").split(":", 1)[0]
        out[loc] = out.get(loc, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _extract_visual_title(visual_data: dict) -> str:
    """Display title from visual.json."""
    try:
        vc = visual_data.get("visual", {}).get("visualContainerObjects", {})
        title_list = vc.get("title", [])
        if not title_list:
            return ""
        text = title_list[0].get("properties", {}).get("text", {})
        if (
            isinstance(text, dict)
            and isinstance(text.get("expr"), dict)
            and "Literal" in text["expr"]
        ):
            return text["expr"]["Literal"].get("Value", "").strip("'\"")
        return ""
    except (KeyError, TypeError, AttributeError, IndexError):
        return ""


def _extract_field_references(visual_data: dict) -> List[Dict[str, str]]:
    """All field references of a visual.json (legacy dict shape)."""
    out: List[Dict[str, str]] = []
    seen = set()
    for r in extract_field_refs(visual_data):
        k = (r.table, r.name, r.kind)
        if k not in seen:
            seen.add(k)
            out.append({"table": r.table, "field": r.name, "type": r.kind})
    return out
