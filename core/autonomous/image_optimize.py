"""Image optimization for Claude vision — resize a screenshot to a model's native
image resolution so it is NOT silently downscaled server-side (which crushes small
text) and so its visual-token cost is controlled.

Claude views images in 28x28-pixel patches, each costing one visual token, so an
image costs ``ceil(width/28) * ceil(height/28)`` tokens. Each model has a native
resolution expressed as a long-edge limit AND a visual-token budget; images over
either limit are downscaled (aspect-preserving) before processing.

Defaults here target the **high-resolution tier** (Claude Opus 4.8 / 4.7 / Fable 5):
max long edge 2576 px, 4784 visual tokens. The *standard* tier (all other models)
is 1568 px / 1568 tokens — pass ``max_edge=1568, max_tokens=1568`` for those.

``resized_size`` is ported verbatim from Anthropic's published reference
implementation (platform.claude.com → Vision → Coordinates and bounding boxes) so
the size we resize to exactly matches the size Claude sees. ``optimize_png`` is a
thin PIL wrapper: downscale-only (never upscale), lossless PNG (no JPEG artifacts
on small text), in place.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
import os
import tempfile
from typing import Dict, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# High-resolution tier (Opus 4.8). Standard tier = 1568 / 1568.
HIGH_RES_MAX_EDGE = 2576
HIGH_RES_MAX_TOKENS = 4784
STANDARD_MAX_EDGE = 1568
STANDARD_MAX_TOKENS = 1568
PATCH = 28

# ``vision_tier`` -> (max_edge, max_tokens). J-30: the target used to be pinned to
# the high-res tier, so standard-tier clients still got server-side downscaling.
VISION_TIERS: Dict[str, Tuple[int, int]] = {
    "high": (HIGH_RES_MAX_EDGE, HIGH_RES_MAX_TOKENS),
    "standard": (STANDARD_MAX_EDGE, STANDARD_MAX_TOKENS),
}


def tier_limits(vision_tier: Optional[str]) -> Tuple[int, int]:
    """``(max_edge, max_tokens)`` for a tier name; raises ValueError for an unknown one."""
    key = (vision_tier or "high").strip().lower()
    if key not in VISION_TIERS:
        raise ValueError(
            f"unknown vision_tier {vision_tier!r}; expected one of {sorted(VISION_TIERS)}"
        )
    return VISION_TIERS[key]


def count_image_tokens(width: int, height: int) -> int:
    """Visual tokens consumed by an image: one token per 28x28 pixel patch."""
    return math.ceil(width / PATCH) * math.ceil(height / PATCH)


def resized_size(
    width: int,
    height: int,
    max_edge: int = HIGH_RES_MAX_EDGE,
    max_tokens: int = HIGH_RES_MAX_TOKENS,
) -> Tuple[int, int]:
    """The size Claude resizes an image to before padding.

    Ported verbatim from Anthropic's reference implementation. Finds the largest
    aspect-preserving size satisfying BOTH the edge limit and the visual-token
    budget. Images that already fit are returned unchanged (never upscaled).
    Defaults are the high-resolution tier; for standard-tier models pass
    ``max_edge=1568, max_tokens=1568``.
    """

    def fits(w: int, h: int) -> bool:
        return (
            math.ceil(w / PATCH) * PATCH <= max_edge
            and math.ceil(h / PATCH) * PATCH <= max_edge
            and count_image_tokens(w, h) <= max_tokens
        )

    if fits(width, height):
        return (width, height)
    if height > width:
        resized_h, resized_w = resized_size(height, width, max_edge, max_tokens)
        return (resized_w, resized_h)

    # Binary search along the long edge for the largest aspect-preserving size
    # that fits.
    aspect_ratio = width / height
    lo, hi = 1, width  # lo always fits; hi never fits
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(mid, max(round(mid / aspect_ratio), 1)):
            lo = mid
        else:
            hi = mid
    return (lo, max(round(lo / aspect_ratio), 1))


def optimize_png(
    path: Union[str, Path],
    *,
    max_edge: int = HIGH_RES_MAX_EDGE,
    max_tokens: int = HIGH_RES_MAX_TOKENS,
    vision_tier: Optional[str] = None,
) -> Dict:
    """Resize a PNG in place to fit a model's native image resolution.

    Downscale-only (never upscales), lossless PNG. Safe no-op when the file is
    missing (so enabling this by default cannot break a screenshot op whose
    capture failed upstream) or already within limits. ``vision_tier``
    (``"high"`` | ``"standard"``) overrides ``max_edge`` / ``max_tokens``. The
    rewrite is atomic: a temp file in the same folder + ``os.replace`` (J-30), so
    a crash mid-write never leaves a truncated PNG.

    Returns ``{original_size, final_size, visual_tokens, resized}`` and, on a
    missing file, ``{skipped: True}``.
    """
    if vision_tier is not None:
        max_edge, max_tokens = tier_limits(vision_tier)
    p = Path(path)
    if not p.exists():
        logger.debug("optimize_png: %s does not exist — skipping", p)
        return {"resized": False, "skipped": True, "reason": "file_not_found"}

    try:
        from PIL import Image
    except Exception as e:  # pragma: no cover - PIL is a project dep
        logger.warning("optimize_png: PIL unavailable (%s) — skipping", e)
        return {"resized": False, "skipped": True, "reason": "pil_unavailable"}

    with Image.open(p) as img:
        orig_w, orig_h = img.size
        new_w, new_h = resized_size(orig_w, orig_h, max_edge, max_tokens)
        if (new_w, new_h) == (orig_w, orig_h):
            return {
                "original_size": (orig_w, orig_h),
                "final_size": (orig_w, orig_h),
                "visual_tokens": count_image_tokens(orig_w, orig_h),
                "resized": False,
            }
        resized = img.resize((new_w, new_h), Image.LANCZOS)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.stem}.", suffix=".png.tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            resized.save(fh, format="PNG")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return {
        "original_size": (orig_w, orig_h),
        "final_size": (new_w, new_h),
        "visual_tokens": count_image_tokens(new_w, new_h),
        "resized": True,
    }


__all__ = [
    "HIGH_RES_MAX_EDGE",
    "HIGH_RES_MAX_TOKENS",
    "STANDARD_MAX_EDGE",
    "STANDARD_MAX_TOKENS",
    "VISION_TIERS",
    "tier_limits",
    "PATCH",
    "count_image_tokens",
    "resized_size",
    "optimize_png",
]
