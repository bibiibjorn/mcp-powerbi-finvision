"""Pick ≤4 R0101 instances per visual type for catalog templates.

Priority: (1) force-includes, (2) minimal, (3) CF-configured, (4) distinct
role patterns, (5) distinctive chrome, (6) fill by Jaccard-diversity.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from tools.visual_catalog.inventory import VisualInstance

# Hand-edit this dict to force-include or force-exclude specific instances.
# Schema: {visual_type: {"include": [{"page": "<display>", "visual_id": "<hex>"}], "exclude": [...]}}
PICKER_OVERRIDES: Dict[str, Dict] = {}


def _is_excluded(inst: VisualInstance, overrides: Optional[Dict]) -> bool:
    if not overrides:
        return False
    for rule in overrides.get("exclude", []) or []:
        if rule.get("page") == inst.page_display_name and rule.get("visual_id") == inst.visual_id:
            return True
    return False


def _force_includes(
    instances: List[VisualInstance], overrides: Optional[Dict]
) -> List[VisualInstance]:
    if not overrides:
        return []
    forced: List[VisualInstance] = []
    for rule in overrides.get("include", []) or []:
        match = next(
            (
                i
                for i in instances
                if i.page_display_name == rule.get("page") and i.visual_id == rule.get("visual_id")
            ),
            None,
        )
        if match is not None:
            forced.append(match)
    return forced


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return 1.0 - len(sa & sb) / len(sa | sb)


def _chrome_score(inst: VisualInstance) -> int:
    return len({"title", "background", "border"} & set(inst.configured_containers))


def pick_templates(
    instances: List[VisualInstance],
    cap: int = 4,
    overrides: Optional[Dict] = None,
) -> List[VisualInstance]:
    eligible = [i for i in instances if not _is_excluded(i, overrides)]
    if not eligible:
        return []

    picked: List[VisualInstance] = []
    picked_ids: set[str] = set()

    def add(inst: VisualInstance) -> None:
        if inst.visual_id in picked_ids or len(picked) >= cap:
            return
        picked.append(inst)
        picked_ids.add(inst.visual_id)

    for forced in _force_includes(eligible, overrides):
        add(forced)

    if len(picked) < cap:
        add(min(eligible, key=lambda i: len(i.configured_containers)))

    if len(picked) < cap:
        for i in eligible:
            if i.cf_present:
                add(i)

    if len(picked) < cap:
        seen = {tuple(sorted(p.role_bindings.items())) for p in picked}
        for i in eligible:
            pat = tuple(sorted(i.role_bindings.items()))
            if pat not in seen:
                add(i)
                seen.add(pat)

    if len(picked) < cap:
        candidates = sorted(
            (i for i in eligible if i.visual_id not in picked_ids),
            key=_chrome_score,
            reverse=True,
        )
        for i in candidates:
            if _chrome_score(i) == 0:
                break
            add(i)

    while len(picked) < cap:
        remaining = [i for i in eligible if i.visual_id not in picked_ids]
        if not remaining:
            break
        best = max(
            remaining,
            key=lambda cand: (
                min(_jaccard(cand.configured_containers, p.configured_containers) for p in picked)
                if picked
                else 1.0
            ),
        )
        add(best)

    return picked[:cap]
