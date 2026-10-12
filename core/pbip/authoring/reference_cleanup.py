"""Cross-reference cleanup utilities for PBIP report files.

Extracted from CloneEngine so callers outside the clone flow can use them
independently (e.g. after bulk visual deletion without a full clone).

Key name (H-08, verified on 41 real pages): page.json stores interactions
under ``visualInteractions`` (``{source, target, type}``); ZERO real pages
use ``interactions``. The legacy key written by older versions of this tool
is still read (and removed on write) through ``pbir_model``.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Set

from core.pbip import pbir_model
from core.pbip.authoring import safe_io

logger = logging.getLogger(__name__)


def strip_dangling_interactions(page_json: Dict[str, Any], live_ids: Set[str]) -> Dict[str, Any]:
    """Remove interaction entries whose source or target is not in live_ids.

    Uses live_ids semantics: keeps entries where BOTH source and target are
    present. Mutates and returns page_json.
    """
    interactions = pbir_model.get_page_interactions(page_json)
    has_legacy = pbir_model.LEGACY_PAGE_INTERACTIONS_KEY in page_json
    if not interactions and not has_legacy:
        return page_json
    kept = [i for i in interactions if i.get("source") in live_ids and i.get("target") in live_ids]
    # Only rewrite when something actually changed — preserves dict order /
    # avoids dirtying git diffs on re-save when nothing was dangling.
    if len(kept) != len(interactions) or has_legacy:
        pbir_model.set_page_interactions(page_json, kept)
    return page_json


def strip_dangling_visual_refs(visuals_dir: Path, live_ids: Set[str]) -> int:
    """Clear parentGroupName on any visual whose group ID is not in live_ids.

    Returns the number of visuals patched.
    """
    patched = 0
    visuals_dir = Path(visuals_dir)
    if not visuals_dir.exists():
        return patched
    for visual_folder in sorted(visuals_dir.iterdir()):
        if not visual_folder.is_dir():
            continue
        vj_path = visual_folder / "visual.json"
        data = safe_io.read_json(vj_path)
        if not isinstance(data, dict):
            continue
        parent = data.get("parentGroupName")
        if parent and parent not in live_ids:

            def _mutate(d: Dict[str, Any]) -> None:
                d.pop("parentGroupName", None)

            safe_io.update_json(vj_path, _mutate)
            patched += 1
    return patched
