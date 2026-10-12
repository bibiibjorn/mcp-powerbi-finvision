"""Preserve content between `<!-- MANUAL: <slug> -->` fences across regens."""

from __future__ import annotations

import re
from typing import Dict

_FENCE_RE = re.compile(
    r"<!--\s*MANUAL:\s*(?P<slug>[a-z0-9-]+)\s*-->\s*\n?"
    r"(?P<body>.*?)"
    r"\n?\s*<!--\s*/MANUAL\s*-->",
    re.DOTALL,
)

ALLOWED_SLUGS = {"overview", "when-to-use", "gotchas", "related-extras", "per-template-notes"}


class InvalidManualSlugError(ValueError):
    pass


def extract_fences(text: str) -> Dict[str, str]:
    blocks: Dict[str, str] = {}
    for match in _FENCE_RE.finditer(text):
        slug = match.group("slug")
        if slug not in ALLOWED_SLUGS:
            raise InvalidManualSlugError(
                f"Unknown MANUAL slug '{slug}'. Allowed: {sorted(ALLOWED_SLUGS)}"
            )
        blocks[slug] = match.group("body").strip()
    return blocks


def merge_manual(*, regenerated: str, previous: str) -> str:
    previous_blocks = extract_fences(previous) if previous else {}
    if not previous_blocks:
        return regenerated

    def replace(match: re.Match) -> str:
        slug = match.group("slug")
        if slug in previous_blocks:
            return f"<!-- MANUAL: {slug} -->\n{previous_blocks[slug]}\n<!-- /MANUAL -->"
        return match.group(0)

    return _FENCE_RE.sub(replace, regenerated)


def has_manual_blocks(text: str) -> bool:
    return bool(_FENCE_RE.search(text))
