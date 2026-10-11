"""Persistence layer for authoring operations.

Provides the canonical write-to-disk pattern used across handler files:
  VisualBuilder returns a dict -> write_visual() handles mkdir + save.

Keeping this in one place means changes to the on-disk layout only need
to be made here. Every write goes through ``safe_io`` (dry-run aware,
failures raise, report-root guard — WP9).
"""

import logging
from pathlib import Path
from typing import Any, Dict

from core.pbip.authoring import safe_io

logger = logging.getLogger(__name__)

_TEMPLATE_SCHEMA_MARK = "/visualContainer/"


def _matching_schema(visuals_dir: Path, current: Any) -> Any:
    """H-36: reuse the visualContainer $schema sibling visuals already carry."""
    if not isinstance(current, str) or _TEMPLATE_SCHEMA_MARK not in current:
        return current
    found = safe_io.sibling_schema(visuals_dir, "visual.json", "")
    if not found:
        pages_dir = visuals_dir.parent.parent
        found = safe_io.sibling_schema(pages_dir, "visual.json", "")
    return found or current


def write_visual(
    pages_visuals_dir: Path, visual_dict: Dict[str, Any], *, overwrite: bool = False
) -> Path:
    """Write a visual.json dict into the correct subfolder under pages_visuals_dir.

    Creates the visuals dir and the visual folder if they don't exist.
    Returns the visual folder path.

    Collision guard (mirrors page_builder.py's P1-48): if the visual folder
    already exists and ``overwrite`` is False, raise FileExistsError rather than
    silently overlaying onto an existing visual. IDs are normally random so this
    is defensive, but VisualBuilder accepts a caller-supplied name, so the trap
    is live infrastructure.
    """
    visual_id = visual_dict["name"]
    if not safe_io.is_safe_segment(visual_id):
        raise ValueError(f"invalid visual id {visual_id!r}")
    pages_visuals_dir = Path(pages_visuals_dir)
    visual_folder = pages_visuals_dir / visual_id
    if visual_folder.exists() and not overwrite:
        raise FileExistsError(
            f"Visual folder already exists: {visual_folder}. "
            f"Pass overwrite=True to intentionally replace it."
        )
    if "$schema" in visual_dict:
        visual_dict["$schema"] = _matching_schema(pages_visuals_dir, visual_dict["$schema"])
    safe_io.make_dirs(visual_folder)
    safe_io.write_json(visual_folder / "visual.json", visual_dict)
    logger.debug("Wrote visual %s to %s", visual_id, visual_folder)
    return visual_folder


def delete_visual_files(visual_folder: Path) -> None:
    """Remove a visual folder from disk. No-op if folder doesn't exist.

    Does NOT clean up cross-references (interactions, parentGroupName).
    Call reference_cleanup.strip_dangling_* after bulk deletions.
    """
    if safe_io.remove_path(visual_folder):
        logger.debug("Deleted visual folder %s", visual_folder)
