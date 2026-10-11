"""Detect calc-group migration candidates by scanning measure names."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from core.calc_group_migration.patterns import PATTERNS, match_suffix


def _candidate_id(pattern: str, base_set: set) -> str:
    body = pattern + "|" + ",".join(sorted(base_set))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]


def detect(
    model: Any,
    patterns: Optional[List[str]] = None,
    min_pattern_size: int = 3,
) -> Dict[str, Any]:
    """Group measures sharing a base+variant pattern into candidates."""
    pat_names = patterns or list(PATTERNS)
    measures: List[str] = []
    for tbl in getattr(model, "tables", []) or []:
        for m in getattr(tbl, "measures", []) or []:
            n = getattr(m, "name", None)
            if n:
                measures.append(n)
    measure_set = set(measures)
    candidates: List[Dict[str, Any]] = []
    for pat in pat_names:
        groups: Dict[str, set] = {}  # base -> variants
        for name in measures:
            hit = match_suffix(name, pat)
            if hit and hit["base"] in measure_set:
                groups.setdefault(hit["base"], set()).add(hit["variant"])
        if len(groups) < min_pattern_size:
            continue
        variant_set = sorted(set().union(*groups.values()))
        candidates.append(
            {
                "candidate_id": _candidate_id(pat, set(groups)),
                "pattern_name": pat,
                "base_measures": sorted(groups),
                "variant_suffix_set": variant_set,
                "estimated_replacement_count": sum(len(v) for v in groups.values()),
                "dependency_count": 0,  # filled by handler when graph available
            }
        )
    candidates.sort(key=lambda c: -c["estimated_replacement_count"])
    return {"candidates": candidates[:20]}


def detect_structural(
    objects: List[Dict[str, Any]],
    measure_names: Optional[set] = None,
    min_tokens: int = 8,
    min_group_size: int = 2,
) -> Dict[str, Any]:
    """Calc-group candidates found by BODY structure instead of measure-name suffixes (F-15).

    ``objects``: ``[{kind, table, name, expression}]``. A group of measures whose bodies are the
    same up to ONE differing base MEASURE reference is one calculation item applied to those base
    measures (``CALCULATE(SELECTEDMEASURE(), <shared modifiers>)``). Same candidate shape as
    ``detect`` plus the item expression.
    """
    from core.dax.clone_detector import find_clones

    res = find_clones(
        objects,
        min_tokens=min_tokens,
        min_group_size=min_group_size,
        include_fragments=False,
        include_near=False,
        measure_names=measure_names or set(),
    )
    candidates: List[Dict[str, Any]] = []
    for g in res["groups"]:
        prop = g.get("proposal") or {}
        if prop.get("type") != "calc_group_item":
            continue
        cg = prop["calc_group"]
        bases = sorted({str(r["base_measure"]).strip("[]") for r in cg["replaces"]})
        candidates.append(
            {
                "candidate_id": _candidate_id("structural", set(bases)),
                "pattern_name": "structural",
                "base_measures": bases,
                "variant_suffix_set": [],
                "estimated_replacement_count": len(cg["replaces"]),
                "dependency_count": 0,
                "item_expression": cg["item_expression"],
                "replaces": cg["replaces"],
            }
        )
    candidates.sort(key=lambda c: -c["estimated_replacement_count"])
    return {"candidates": candidates[:20]}
