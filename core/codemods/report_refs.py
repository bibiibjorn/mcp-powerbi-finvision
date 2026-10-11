"""Report-side usage of a measure (review E-06: ``affected_visuals`` was always ``[]``).

``find_measure_bindings(pbip_path, table, measure)`` resolves the PBIP path to
every report bound to it (``core.pbip.pbir_model.resolve_pbip``) and scans each
report definition's JSON (visuals, page/report filters, bookmarks, report
extension measures) for:

- a ``{"Measure": {..., "Property": "<name>"}}`` field binding (the Entity may
  be a query alias, so the measure name alone decides — measure names are
  model-scoped);
- a report-extension / visual-calculation ``expression`` that references it.

Returns ``{"checked": bool, "visuals": [...], "files": [...], "reason": str}``;
``checked=False`` when no report could be scanned (callers must then treat the
usage as UNKNOWN and refuse a delete).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.codemods import dax_text


def _walk(node: Any, name_n: str, table: str, hits: List[str], path: str = "") -> None:
    if isinstance(node, dict):
        meas = node.get("Measure")
        if isinstance(meas, dict):
            prop = meas.get("Property")
            if isinstance(prop, str) and dax_text.norm_name(prop) == name_n:
                hits.append(path or "/")
        for k, v in node.items():
            if k.lower() == "expression" and isinstance(v, str):
                if dax_text.measure_ref_sites(v, name_n, table):
                    hits.append(f"{path}/{k}")
                continue
            _walk(v, name_n, table, hits, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, name_n, table, hits, f"{path}[{i}]")


def find_measure_bindings(pbip_path: Optional[str], table: str, measure: str) -> Dict[str, Any]:
    if not pbip_path:
        return {
            "checked": False,
            "visuals": [],
            "files": [],
            "reason": "no PBIP path known — report usage cannot be verified",
        }
    try:
        from core.pbip.pbir_model import resolve_pbip

        res = resolve_pbip(pbip_path)
    except Exception as e:  # noqa: BLE001
        return {"checked": False, "visuals": [], "files": [], "reason": f"resolve failed: {e}"}
    reports = [r for r in res.get("reports") or [] if r.get("definition_dir")]
    if not reports:
        return {
            "checked": False,
            "visuals": [],
            "files": [],
            "reason": f"no report definition found under {pbip_path}",
        }
    name_n = dax_text.norm_name(measure)
    visuals: List[str] = []
    files: List[str] = []
    for r in reports:
        ddir = Path(r["definition_dir"])
        for f in ddir.rglob("*.json"):
            try:
                text = f.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            if measure.lower() not in text.lower():
                continue  # cheap pre-filter (names appear verbatim in PBIR JSON)
            try:
                data = json.loads(text)
            except ValueError:
                continue
            hits: List[str] = []
            _walk(data, name_n, table, hits)
            if not hits:
                continue
            files.append(str(f))
            if f.name == "visual.json":
                visuals.append(f"{r.get('name', '')}:{f.parent.parent.parent.name}/{f.parent.name}")
    return {
        "checked": True,
        "visuals": visuals,
        "files": files,
        "reports_scanned": [r.get("report_dir") for r in reports],
        "reason": "",
    }
