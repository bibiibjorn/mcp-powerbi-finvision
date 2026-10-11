"""Crop a full-page PNG to a visual's bounding box."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


class OutOfBoundsError(ValueError):
    pass


def crop_visual(
    *,
    full_page_png: Path,
    x: float,
    y: float,
    width: float,
    height: float,
    output_png: Path,
    padding: int = 8,
) -> None:
    with Image.open(full_page_png) as img:
        page_w, page_h = img.size
        left = int(x) - padding
        top = int(y) - padding
        right = int(x + width) + padding
        bottom = int(y + height) + padding

        if left >= page_w or top >= page_h or right <= 0 or bottom <= 0:
            raise OutOfBoundsError(
                f"Visual bbox ({x},{y},{width},{height}) outside page ({page_w}x{page_h})"
            )

        left, top = max(0, left), max(0, top)
        right, bottom = min(page_w, right), min(page_h, bottom)

        output_png.parent.mkdir(parents=True, exist_ok=True)
        img.crop((left, top, right, bottom)).save(output_png, format="PNG")
