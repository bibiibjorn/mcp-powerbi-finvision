"""
Uniform ``response_format`` post-processing (concise | detailed).

Pure helpers consumed by ``ToolDispatcher.dispatch`` — same pattern as
``pagination_helpers.paginate``: the dispatcher stays thin and the shrink
logic lives here where it can be unit-tested without a registry.

``detailed`` is today's full shape (the default, a no-op). ``concise``
produces a smaller payload for an agent that is chaining/scanning results
rather than reading them: explanatory fields are dropped, long prose is
clipped, and long lists are capped with an explicit count so the caller can
tell something was dropped and re-request with ``page_size`` if needed.
"""

from typing import Any, Dict, Iterable, List, Optional

__all__ = [
    "normalize_response_format",
    "apply_concise",
    "CONCISE_DROP_KEYS",
    "CONCISE_STRING_LIMIT",
    "CONCISE_LIST_LIMIT",
    "PAGINABLE_KEYS",
    "INLINE_BUDGET_OFF_KEY",
    "DEFAULT_INLINE_SOFT_CHARS",
    "main_list_key",
    "default_response_format",
    "inline_budget_opted_out",
    "soft_inline_budget",
    "concise_analysis_full",
]

# Accepted values for the ``response_format`` argument.
VALID_FORMATS = ("concise", "detailed")

# Verbose / explanatory keys dropped under ``concise``. These are prose or
# duplicative rendering artifacts — never the primary payload of a chaining
# step (rule 1 keeps them if dropping would empty a dict).
CONCISE_DROP_KEYS = frozenset(
    {
        "raw_stdout",
        "raw_stderr",
        "formatted_output",
        "recommendations",
        "suggestions",
        "notes",
        "diagram",
        "mermaid",
        "html",
    }
)

# Keys whose string value is load-bearing for chaining (DAX text) and is
# therefore never clipped, regardless of length. ``dax`` used to be DROPPED
# under concise, which erased 09_Debug's per-slicer filter DAX - the payload of
# the filter-context summary (L-19 / A-19, wave 3).
CONCISE_STRING_KEEP_KEYS = frozenset({"expression", "expr", "dax"})

CONCISE_STRING_LIMIT = 300
CONCISE_LIST_LIMIT = 25

# Mirror middleware._COMPACT_MAX_DEPTH — a hostile/runaway response must not
# blow the Python stack.
_CONCISE_MAX_DEPTH = 64


def normalize_response_format(value: Any) -> Optional[str]:
    """Normalize a caller-supplied ``response_format`` value.

    Returns ``"concise"`` / ``"detailed"`` for a recognized value (case
    insensitive, whitespace stripped), or ``None`` when absent or invalid.
    Never raises — an unusable value is simply not a format.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in VALID_FORMATS else None


def _concise_value(
    key: Optional[str],
    value: Any,
    capped: Dict[str, Dict[str, int]],
    depth: int,
    keep_full: frozenset = frozenset(),
) -> Any:
    """Recursively shrink one value. Pure; builds new containers."""
    if depth >= _CONCISE_MAX_DEPTH:
        return value

    if isinstance(value, dict):
        return _concise_dict(value, capped, depth)

    if isinstance(value, list):
        total = len(value)
        # A top-level list the caller explicitly sized with page_size is never
        # capped (A-18: capping it would make rows unreachable behind next_token).
        limit_applies = not (depth == 1 and key in keep_full)
        items = (
            value[:CONCISE_LIST_LIMIT] if (limit_applies and total > CONCISE_LIST_LIMIT) else value
        )
        if limit_applies and total > CONCISE_LIST_LIMIT and key is not None:
            capped[key] = {"shown": len(items), "total": total}
        return [_concise_value(None, item, capped, depth + 1) for item in items]

    if isinstance(value, str):
        if key in CONCISE_STRING_KEEP_KEYS:
            return value
        if len(value) > CONCISE_STRING_LIMIT:
            return value[:CONCISE_STRING_LIMIT] + "…"
        return value

    return value


def _concise_dict(
    source: Dict[Any, Any],
    capped: Dict[str, Dict[str, int]],
    depth: int,
    keep_full: frozenset = frozenset(),
) -> Dict[Any, Any]:
    """Drop verbose keys + shrink the survivors. Never mutates ``source``."""
    out: Dict[Any, Any] = {}
    for key, value in source.items():
        if isinstance(key, str) and key in CONCISE_DROP_KEYS:
            continue
        out[key] = _concise_value(
            key if isinstance(key, str) else None, value, capped, depth + 1, keep_full
        )
        # A list capped INSIDE a nested dict (e.g. instances[].pages) is marked on its own
        # parent as ``<key>_truncated`` - the top-level ``_concise_capped`` map is keyed by
        # name only, so several parents with the same key would overwrite one another.
        if (
            depth >= 1
            and isinstance(key, str)
            and isinstance(value, list)
            and len(value) > CONCISE_LIST_LIMIT
            and f"{key}_truncated" not in source
        ):
            out[f"{key}_truncated"] = {"shown": CONCISE_LIST_LIMIT, "total": len(value)}

    # Rule 1 exception: never drop a key whose value was the ONLY content —
    # if dropping emptied a non-empty dict, keep the original (shrunk) shape.
    if source and not out:
        for key, value in source.items():
            out[key] = _concise_value(
                key if isinstance(key, str) else None, value, capped, depth + 1
            )
    return out


def apply_concise(result: dict, keep_full_keys: Optional[Iterable[str]] = None) -> dict:
    """Return a concise copy of ``result``.

    Rules:
      1. Drop verbose/explanatory keys (:data:`CONCISE_DROP_KEYS`) anywhere in
         the tree — except when dropping would empty a dict that had keys.
      2. Clip string values over 300 chars (``expression``/``expr`` exempt).
      3. Cap lists at the first 25 items, recording ``_concise_capped``
         ``{key: {shown, total}}`` merged at the top level; a list capped inside a
         nested dict also gets ``<key>_truncated: {shown, total}`` on its own parent
         (e.g. ``17_Desktop_Bridge status`` ``instances[].pages`` -> ``pages_truncated``).
      4. Recurse into nested dicts/lists with a depth guard of 64.
      5. Always set ``_response_format: "concise"``.

    Pure: never mutates the input, never raises — on any unexpected shape the
    input is returned unchanged.
    """
    if not isinstance(result, dict):
        return result
    try:
        capped: Dict[str, Dict[str, int]] = {}
        keep = frozenset(k for k in (keep_full_keys or ()) if k)
        out = _concise_dict(result, capped, 0, keep)
        if capped:
            existing = out.get("_concise_capped")
            merged: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
            merged.update(capped)
            out["_concise_capped"] = merged
        out["_response_format"] = "concise"
        return out
    except Exception:  # pragma: no cover - defensive: shrinking must never fail a call
        return result


def apply_response_format(
    result: Any, requested: Any, keep_full_keys: Optional[Iterable[str]] = None
) -> Any:
    """Dispatch-layer entry point for the ``response_format`` control.

    * absent / unrecognized value -> no-op (unrecognized additionally tags
      ``_response_format_ignored`` so the caller can see it was dropped).
    * ``detailed`` -> no-op, records ``_response_format: "detailed"``.
    * ``concise`` -> :func:`apply_concise`, success results only (error
      envelopes stay intact for ``enrich_error_response``). ``keep_full_keys``
      names top-level lists that must not be capped (the paginated list).
    """
    if not isinstance(result, dict):
        return result

    fmt = normalize_response_format(requested)
    if fmt is None:
        if requested is not None:
            out = dict(result)
            out["_response_format_ignored"] = requested
            return out
        return result

    if fmt == "detailed":
        out = dict(result)
        out["_response_format"] = "detailed"
        return out

    # concise — never touch an error envelope.
    if result.get("success") is False or result.get("ok") is False:
        return result
    return apply_concise(result, keep_full_keys)


# ---------------------------------------------------------------------------
# F-34: soft inline budget + list-heavy defaults
# ---------------------------------------------------------------------------

#: Result keys the dispatcher paginates first (server/dispatch.py ``_PAGINABLE_KEYS`` mirrors this;
#: tests/test_wp5b_inline_budget.py pins the two together).
PAGINABLE_KEYS = (
    "measures",
    "columns",
    "tables",
    "relationships",
    "visuals",
    "rows",
    "items",
    "results",
    "objects",
    "violations",
    "findings",
)

#: Private marker the dispatcher puts on a result whose caller opted out of the soft inline budget.
#: ``server.response_pipeline.render_result`` strips it before serializing.
INLINE_BUDGET_OFF_KEY = "_inline_budget_off"

#: Default soft inline budget (chars): ~15k tokens, far below the 450K spill limit.
DEFAULT_INLINE_SOFT_CHARS = 60_000

#: (tool, operation) pairs whose result is a long list of hits/findings: ``response_format``
#: defaults to ``concise`` for them when the caller did not choose one. ``None`` = every operation.
_LIST_HEAVY_DEFAULT_CONCISE: Dict[str, Optional[frozenset]] = {
    "04_Query_Operations": frozenset({"search_objects", "search_string"}),
    "06_Analysis_Operations": frozenset({"full"}),
    "16_Dependency_Graph": frozenset({"find_unused"}),
}


def main_list_key(result: Any, next_token: Any = None) -> Optional[str]:
    """The top-level list the dispatcher paginates: the first of :data:`PAGINABLE_KEYS` that is a
    list, else the largest top-level list (private ``_`` keys excluded), else ``None``."""
    if not isinstance(result, dict):
        return None
    if isinstance(next_token, str) and ":" in next_token:
        key = next_token.split(":", 1)[0]
        return key if isinstance(result.get(key), list) else None
    for key in PAGINABLE_KEYS:
        if isinstance(result.get(key), list):
            return key
    lists: List[tuple] = [
        (len(v), k) for k, v in result.items() if isinstance(v, list) and not str(k).startswith("_")
    ]
    if not lists:
        return None
    lists.sort(reverse=True)
    return lists[0][1]


def default_response_format(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """``"concise"`` for a list-heavy operation when the caller chose no ``response_format``.

    Explicit ``response_format`` (either value), an explicit ``page_size`` and ``full=true`` all win,
    so this only changes what an agent gets when it asked for nothing in particular. Also covers
    ``02_Model_Operations`` ``list`` with ``include=`` (e.g. ``include=expression``).
    """
    if not isinstance(args, dict) or args.get("response_format") is not None:
        return None
    if args.get("page_size") is not None or args.get("full") is True:
        return None
    op = str(args.get("operation") or "").lower()
    if tool_name == "02_Model_Operations":
        return "concise" if op == "list" and args.get("include") else None
    ops = _LIST_HEAVY_DEFAULT_CONCISE.get(tool_name)
    return "concise" if ops is not None and op in ops else None


def inline_budget_opted_out(args: Dict[str, Any]) -> bool:
    """The caller asked for the whole result: an explicit ``page_size`` (they size the page), or
    ``response_format=detailed`` together with ``full=true``."""
    if not isinstance(args, dict):
        return False
    if args.get("page_size") is not None:
        return True
    return normalize_response_format(args.get("response_format")) == "detailed" and (
        args.get("full") is True
    )


def soft_inline_budget() -> int:
    """Soft inline budget in chars from config ``response.inline_soft_chars`` (0 = off)."""
    try:
        from core.config.config_manager import config

        raw = config.get("response.inline_soft_chars", DEFAULT_INLINE_SOFT_CHARS)
        value = int(raw) if raw is not None else DEFAULT_INLINE_SOFT_CHARS
    except Exception:  # noqa: BLE001 - config not initialised (tests): use the default
        return DEFAULT_INLINE_SOFT_CHARS
    return max(value, 0)


# ---------------------------------------------------------------------------
# 06_Analysis_Operations full: concise shape
# ---------------------------------------------------------------------------

#: Per-issue boilerplate added by ``enrich_issue_with_impact``: the same few sentences on every row.
_ISSUE_BOILERPLATE = ("business_impact", "why_it_matters", "consequences")

#: Nested lists of a ``full`` analysis capped (with a count) under ``concise``.
ANALYSIS_NESTED_CAP = 10

#: ``analyses.<section>`` -> the nested row list(s) capped under ``concise``.
_ANALYSIS_ROW_KEYS = {
    "integrity": ("issues",),
    "topology": ("findings",),
    "model_settings": ("findings",),
    "bpa": ("violations",),
    "m_practices": ("issues",),
    "performance": ("issues",),
    "relationships": ("relationships",),
}


def _cap_rows(holder: Dict[str, Any], key: str) -> None:
    rows = holder.get(key)
    if not isinstance(rows, list):
        return
    clean = [
        {k: v for k, v in r.items() if k not in _ISSUE_BOILERPLATE} if isinstance(r, dict) else r
        for r in rows
    ]
    if len(clean) > ANALYSIS_NESTED_CAP:
        holder[f"{key}_truncated"] = {"shown": ANALYSIS_NESTED_CAP, "total": len(clean)}
        clean = clean[:ANALYSIS_NESTED_CAP]
    holder[key] = clean


def concise_analysis_full(result: Any) -> Any:
    """Shrink a ``06_Analysis_Operations full`` result for ``response_format=concise``.

    Drops the per-issue boilerplate keys and caps the nested issue / finding / violation lists (and
    the audit check examples) at :data:`ANALYSIS_NESTED_CAP`, recording ``<key>_truncated``
    ``{shown, total}`` beside each capped list. Pure (copies what it changes); anything that is not
    an analysis result passes through. The generic concise pass runs after this.
    """
    if not isinstance(result, dict) or not isinstance(result.get("analyses"), dict):
        return result
    out = dict(result)
    _cap_rows(out, "issues")
    analyses: Dict[str, Any] = {}
    for name, section in out["analyses"].items():
        if isinstance(section, dict) and name in _ANALYSIS_ROW_KEYS:
            section = dict(section)
            for key in _ANALYSIS_ROW_KEYS[name]:
                _cap_rows(section, key)
        analyses[name] = section
    out["analyses"] = analyses
    audit = out.get("audit")
    if isinstance(audit, dict) and isinstance(audit.get("checks"), list):
        checks = []
        for check in audit["checks"]:
            if isinstance(check, dict) and isinstance(check.get("examples"), list):
                check = dict(check)
                _cap_rows(check, "examples")
            checks.append(check)
        out["audit"] = {**audit, "checks": checks}
    return out
