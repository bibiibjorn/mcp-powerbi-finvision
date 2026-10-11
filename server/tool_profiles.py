"""Tool profiles: expose only the tools a session needs (PROF-01).

``MCP_PBI_PROFILE`` selects which registered tools ``tools/list`` advertises:

* ``all`` (default) — every tool.
* ``model`` — model / DAX / performance work: drops the five PBIR report-authoring
  tools and ``11_PBIP_Authoring``.
* ``report`` — report authoring + the live Desktop loop: drops the heavy
  model-analysis tools (query plans, DAX codemods, batch CRUD, TMDL automation,
  column-usage mapping, Word docs, Q&A linguistics, BPA analysis).

``MCP_PBI_TOOLS_EXCLUDE`` (comma-separated tool names) removes further tools on
top of any profile. ``01_Connection`` and ``10_Show_User_Guide`` are always kept.
A hidden tool is left out of ``tools/list``; a direct call to it returns
``tool_hidden_by_profile`` — switch profile and restart the server to get it back.
An unknown ``MCP_PBI_PROFILE`` value (a typo) falls back to ``all`` but is logged and
reported as ``warning`` in ``01_Connection status`` -> ``tool_profile`` (N-33).
"""

from __future__ import annotations

import logging
import os
from typing import Dict, FrozenSet, Iterable, List, Optional

logger = logging.getLogger(__name__)
_warned_profiles: set = set()

ALWAYS_KEEP: FrozenSet[str] = frozenset({"01_Connection", "10_Show_User_Guide"})

PROFILE_EXCLUDES: Dict[str, FrozenSet[str]] = {
    "all": frozenset(),
    "model": frozenset(
        {
            "07_Page_Operations",
            "07_Visual_Operations",
            "07_Bookmark_Operations",
            "07_Theme_Operations",
            "07_Report_Operations",
            "11_PBIP_Authoring",
        }
    ),
    "report": frozenset(
        {
            "13_Query_Plan",
            "15_DAX_Refactor",
            "03_Batch_Operations",
            "02_TMDL_Operations",
            "02_QA_Linguistic",
            "05_Column_Usage_Mapping",
            "06_Analysis_Operations",
            "08_Documentation_Word",
        }
    ),
}

PROFILES = tuple(PROFILE_EXCLUDES)


def requested_profile() -> str:
    """The raw ``MCP_PBI_PROFILE`` value (normalised), before validation."""
    return (os.environ.get("MCP_PBI_PROFILE") or "all").strip().lower()


def profile_warning() -> Optional[str]:
    """A message when ``MCP_PBI_PROFILE`` names no known profile, else None."""
    value = requested_profile()
    if value in PROFILE_EXCLUDES:
        return None
    return (
        f"Unknown MCP_PBI_PROFILE={value!r}; falling back to 'all' (every tool exposed). "
        f"Valid profiles: {', '.join(PROFILES)}."
    )


def active_profile() -> str:
    value = requested_profile()
    if value in PROFILE_EXCLUDES:
        return value
    if value not in _warned_profiles:
        _warned_profiles.add(value)
        logger.warning(profile_warning())
    return "all"


def excluded_tools() -> FrozenSet[str]:
    extra = {
        t.strip() for t in (os.environ.get("MCP_PBI_TOOLS_EXCLUDE") or "").split(",") if t.strip()
    }
    return frozenset((PROFILE_EXCLUDES[active_profile()] | extra) - ALWAYS_KEEP)


def filter_tool_names(names: Iterable[str]) -> List[str]:
    hidden = excluded_tools()
    return [n for n in names if n not in hidden]


def is_exposed(name: str) -> bool:
    return name not in excluded_tools()


def describe() -> Dict[str, object]:
    out: Dict[str, object] = {"profile": active_profile(), "hidden_tools": sorted(excluded_tools())}
    warning = profile_warning()
    if warning:
        out["requested_profile"] = requested_profile()
        out["warning"] = warning
    return out
