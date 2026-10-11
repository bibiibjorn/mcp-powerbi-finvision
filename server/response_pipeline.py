"""Post-dispatch response pipeline: result dict → (JSON text, is_error).

Pure and synchronous so it runs on the dispatcher's WORKER thread (A-14: the
old pipeline ran on the event-loop thread and walked a large result up to 9
times — repr proxies, 4 json.dumps passes, a full compact_keys copy).

Strategy (A-05 / A-17 / L-06):

1. Serialize ONCE (``MCPJsonEncoder``; non-finite floats become ``null`` so the
   text is strict JSON — A-28).
2. ``len(text) <= INLINE_MAX_CHARS`` (450K, under the 500K
   ``anthropic/maxResultSizeChars`` the text is tagged with) → returned verbatim.
   Nothing is sampled, compacted or truncated: payload keys (DAX rows, raw
   visual.json, TMDL text) reach the client byte-for-byte as the handler built
   them.
3. Larger → the FULL text is spilled to ``<repo>/output/<tool>_<ts>.json`` and a
   bounded summary is returned instead: every top-level scalar (success,
   error_type, error, counts, timings …), per-key sizes for containers, a
   preview of each list (first items, re-serialized only for the cut lists),
   plus ``_full_output`` (absolute path), ``_full_output_chars`` and a hint.
   Error results stay errors. ``output/`` is pruned on every spill (K-07).

Key compaction (``success``→``ok`` …) is gone from the pipeline: it rewrote
user payloads and produced a second vocabulary. The envelope is the canonical
``success`` / ``error`` / ``error_type`` (see core.validation.error_response).
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.utilities.json_utils import MCPJsonEncoder
from core.validation.response_format import INLINE_BUDGET_OFF_KEY, main_list_key

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Largest response returned inline (chars). Stays under the 500K
#: ``anthropic/maxResultSizeChars`` _meta cap with headroom.
INLINE_MAX_CHARS = 450_000
#: _meta value attached to every TextContent result.
MAX_RESULT_SIZE_META = 500_000
#: Budget for the inline summary of a spilled result.
SPILL_SUMMARY_MAX_CHARS = 20_000
#: Items previewed per list in a spill summary (before the size budget).
SPILL_PREVIEW_ITEMS = 5
#: Longest string kept verbatim in a spill summary.
SPILL_STRING_MAX = 2_000

#: Retention for spilled responses (K-07): newest N files, none older than D days.
SPILL_MAX_FILES = 50
SPILL_MAX_AGE_DAYS = 7

_spill_lock = threading.Lock()


def spill_dir() -> Path:
    """Directory spilled responses go to (``<repo>/output``)."""
    return PROJECT_ROOT / "output"


@dataclass
class RenderedResult:
    text: str
    is_error: bool
    spilled_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _sanitize_non_finite(value: Any, _depth: int = 0) -> Any:
    """Replace NaN/±Infinity floats by None (JSON has no such literals)."""
    if _depth > 200:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _sanitize_non_finite(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_non_finite(v, _depth + 1) for v in value]
    return value


try:  # P-12: orjson is a pinned dependency; the stdlib path below is the fallback
    import orjson as _orjson

    _ORJSON_OPTIONS = (
        _orjson.OPT_NON_STR_KEYS
        | _orjson.OPT_PASSTHROUGH_DATETIME  # MCPJsonEncoder.default -> isoformat()
        | _orjson.OPT_PASSTHROUGH_DATACLASS  # unserializable marker, as before
    )
    _ORJSON_DEFAULT = MCPJsonEncoder().default
except Exception:  # noqa: BLE001 -- no orjson: stdlib only
    _orjson = None  # type: ignore[assignment]


def _serialize_orjson(result: Any) -> Optional[str]:
    """Compact strict JSON via orjson (3.3x faster on 0.5-5 MB payloads), or ``None``.

    Same document as the stdlib path (NaN/Infinity -> ``null``; ``default`` is
    ``MCPJsonEncoder.default``); only non-ASCII text is emitted as raw UTF-8
    instead of ``\\uXXXX`` escapes (semantically identical JSON). Anything orjson
    refuses (ints beyond 64 bit, exotic keys) returns ``None`` and the caller
    uses the stdlib encoder, so behaviour never gets stricter.
    """
    if _orjson is None:
        return None
    try:
        return _orjson.dumps(result, default=_ORJSON_DEFAULT, option=_ORJSON_OPTIONS).decode(
            "utf-8"
        )
    except Exception:  # noqa: BLE001
        return None


def serialize(result: Any) -> str:
    """Compact strict JSON for ``result`` (one pass on the common path)."""
    text = _serialize_orjson(result)
    if text is not None:
        return text
    try:
        return json.dumps(result, separators=(",", ":"), cls=MCPJsonEncoder, allow_nan=False)
    except ValueError:
        # NaN / Infinity somewhere — sanitize and retry (rare path).
        return json.dumps(_sanitize_non_finite(result), separators=(",", ":"), cls=MCPJsonEncoder)


def is_error_result(result: Any) -> bool:
    """A failed CALL. ``ok`` counts only when ``success`` is absent (the legacy alias): with
    ``success: true`` it is a verdict - validate_report's ``ok: false`` means "the report has
    errors", not "the tool failed" (R0201 live test: a finished commit check came back as isError).
    """
    if not isinstance(result, dict):
        return False
    if "success" in result:
        return result.get("success") is False
    return result.get("ok") is False


# ---------------------------------------------------------------------------
# Spill
# ---------------------------------------------------------------------------


def prune_spill_dir(
    directory: Optional[Path] = None,
    *,
    max_files: int = SPILL_MAX_FILES,
    max_age_days: float = SPILL_MAX_AGE_DAYS,
    now: Optional[float] = None,
) -> List[str]:
    """Delete spilled ``*.json`` files beyond the retention policy.

    Keeps at most ``max_files`` newest files and removes anything older than
    ``max_age_days``. Returns the removed paths. Never raises.
    """
    directory = directory if directory is not None else spill_dir()
    removed: List[str] = []
    try:
        entries = []
        with os.scandir(directory) as it:
            for e in it:
                if e.is_file() and e.name.endswith(".json"):
                    try:
                        entries.append((e.stat().st_mtime, e.path))
                    except OSError:
                        continue
    except OSError:
        return removed
    entries.sort(reverse=True)  # newest first
    cutoff = (now if now is not None else time.time()) - max_age_days * 86400
    for idx, (mtime, path) in enumerate(entries):
        if idx >= max_files or mtime < cutoff:
            try:
                os.remove(path)
                removed.append(path)
            except OSError:
                continue
    return removed


def _write_spill(tool_name: str, text: str, directory: Optional[Path] = None) -> str:
    directory = directory if directory is not None else spill_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in tool_name)[:40]
    path = directory / f"{safe}_{stamp}.json"
    with _spill_lock:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        prune_spill_dir(directory)
    return str(path)


def _preview_value(value: Any) -> Tuple[Any, Optional[Dict[str, Any]]]:
    """Bounded preview of one top-level value + a size descriptor (or None)."""
    if isinstance(value, list):
        return value[:SPILL_PREVIEW_ITEMS], {"type": "list", "count": len(value)}
    if isinstance(value, dict):
        # Keep small scalar members; describe the rest.
        keys = list(value.keys())
        small = {
            k: v
            for k, v in value.items()
            if v is None
            or isinstance(v, (bool, int, float))
            or (isinstance(v, str) and len(v) <= 200)
        }
        return small, {"type": "object", "keys": len(keys), "key_sample": keys[:20]}
    if isinstance(value, str) and len(value) > SPILL_STRING_MAX:
        return value[:SPILL_STRING_MAX] + "... [truncated; full value in _full_output]", {
            "type": "string",
            "chars": len(value),
        }
    return value, None


def build_spill_summary(result: Dict[str, Any], path: str, full_chars: int) -> Dict[str, Any]:
    """Inline summary for a spilled result: all scalars kept, containers previewed."""
    summary: Dict[str, Any] = {}
    sizes: Dict[str, Any] = {}
    previews: Dict[str, Any] = {}
    for key, value in result.items():
        preview, size = _preview_value(value)
        if size is None:
            summary[key] = preview
        else:
            sizes[key] = size
            previews[key] = preview
    if "success" not in summary:
        summary["success"] = not is_error_result(result)
    summary["_spilled"] = True
    summary["_full_output"] = path
    summary["_full_output_chars"] = full_chars
    summary["_sizes"] = sizes
    summary["_note"] = (
        f"Full result ({full_chars:,} chars) exceeded the {INLINE_MAX_CHARS:,}-char inline "
        "cap and was written verbatim (JSON) to _full_output — read that file for the "
        "complete data. _preview holds the first items of each large field; _sizes "
        "their full counts. Opt-in pagination (page_size + next_token) or an op's "
        "export_path also keep responses small."
    )
    # Add previews while the summary stays within budget; shrink per key if not.
    base_len = len(serialize(summary))
    budget = SPILL_SUMMARY_MAX_CHARS - base_len
    kept: Dict[str, Any] = {}
    for key, preview in previews.items():
        text = serialize(preview)
        if len(text) > budget and isinstance(preview, list):
            while preview and len(serialize(preview)) > budget:
                preview = preview[: max(0, len(preview) // 2)]
            text = serialize(preview)
        if len(text) <= budget:
            kept[key] = preview
            budget -= len(text) + len(key) + 6
        else:
            sizes[key]["preview_omitted"] = True
    if kept:
        summary["_preview"] = kept
    return summary


# ---------------------------------------------------------------------------
# Soft inline budget (F-34)
# ---------------------------------------------------------------------------

#: Room kept for the truncation metadata added to a cut result.
_TRUNCATION_META_CHARS = 900


def apply_inline_budget(result: Dict[str, Any], budget_chars: int) -> Optional[Dict[str, Any]]:
    """The first page of ``result``'s main list when the whole result would exceed ``budget_chars``.

    Returns ``None`` when nothing applies: no budget, an error result, a result that is already
    paginated (explicit ``page_size`` / a handler's own ``next_token``), no list, or a list that
    already fits. Otherwise a shallow copy whose list holds as many WHOLE items as fit (at least
    one; no item or string is ever clipped, DAX text included) plus ``truncated_inline: true``,
    ``next_token`` / ``has_more`` / ``paginated_key`` in the dispatcher's own pagination format
    (``<key>:<offset>``, continued with ``page_size`` + ``next_token``) and an ``inline_truncation``
    block with the counts.
    """
    if budget_chars <= 0 or not isinstance(result, dict) or is_error_result(result):
        return None
    if any(k in result for k in ("paginated_key", "next_token", "has_more")):
        return None
    key = main_list_key(result)
    if key is None:
        return None
    items = result[key]
    total = len(items)
    if total <= 1:
        return None
    base = len(serialize({**{k: v for k, v in result.items() if k != key}, key: []}))
    room = budget_chars - base - _TRUNCATION_META_CHARS
    keep, used = 0, 0
    for item in items:
        used += len(serialize(item)) + 1
        if used > room and keep >= 1:
            break
        keep += 1
        if used > room:
            break
    if keep >= total:
        return None
    out = dict(result)
    out[key] = items[:keep]
    out["truncated_inline"] = True
    out["paginated_key"] = key
    out["next_token"] = f"{key}:{keep}"
    out["has_more"] = True
    out["inline_truncation"] = {
        "key": key,
        "shown": keep,
        "total": total,
        "budget_chars": budget_chars,
        "note": (
            f"The full result exceeded the {budget_chars:,}-char inline budget, so '{key}' shows "
            f"its first {keep} of {total} items (none clipped). Continue with page_size={keep} and "
            f"next_token='{key}:{keep}', narrow the request, write it out with export_path where "
            "the op has one, or pass response_format=detailed with full=true for everything."
        ),
    }
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render_result(
    tool_name: str,
    result: Any,
    *,
    inline_max_chars: int = INLINE_MAX_CHARS,
    spill_directory: Optional[Path] = None,
    soft_budget_chars: Optional[int] = None,
) -> RenderedResult:
    """Serialize a dispatcher result for the MCP response.

    Runs on the worker thread. One serialization on the common (inline) path;
    one extra small serialization for the summary when spilling.

    ``soft_budget_chars`` (F-34; ``None``/0 = off) is the soft inline budget below the hard
    ``inline_max_chars``: a success result above it comes back as the first page of its main list
    with ``truncated_inline`` (see :func:`apply_inline_budget`). A result carrying
    :data:`INLINE_BUDGET_OFF_KEY` (the caller opted out) skips it; the marker is never serialized.
    """
    if not isinstance(result, dict):
        result = {"success": True, "result": result}
    budget_off = result.get(INLINE_BUDGET_OFF_KEY) is not None
    if budget_off:
        result = {k: v for k, v in result.items() if k != INLINE_BUDGET_OFF_KEY}
    is_error = is_error_result(result)
    text = serialize(result)
    if (
        soft_budget_chars
        and not budget_off
        and len(text) > min(soft_budget_chars, inline_max_chars)
    ):
        cut = apply_inline_budget(result, min(soft_budget_chars, inline_max_chars))
        if cut is not None:
            result, text = cut, serialize(cut)
    if len(text) <= inline_max_chars:
        return RenderedResult(text=text, is_error=is_error)
    try:
        path = _write_spill(tool_name, text, spill_directory)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to spill large %s output (%d chars): %s", tool_name, len(text), e)
        return RenderedResult(text=text, is_error=is_error)
    summary = build_spill_summary(result, path, len(text))
    return RenderedResult(text=serialize(summary), is_error=is_error, spilled_path=path)
