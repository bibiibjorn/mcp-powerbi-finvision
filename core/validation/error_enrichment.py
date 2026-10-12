"""T1-F: uniform error envelope enrichment.

When a handler returns ``{success: False, error_type: <something>}`` the
dispatcher post-processes the result to attach a ``retry_with`` hint and a
``suggestion`` string where the recovery path is well known. The agent can
then self-correct without a second guess-and-check round trip.

Conservative: only attaches hints for a known set of error_type values, and
only when ``retry_with`` / ``suggestion`` are not already present (handler-
supplied wins). Read-only — never mutates ``success`` or ``error``.
"""

from __future__ import annotations

import difflib
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Mapping of error_type -> {suggestion, retry_with (optional)} that we can
# offer the caller. retry_with is the literal tool call the agent should make
# next; suggestion is human-readable context. Both are advisory.
_RECOVERY_MAP: Dict[str, Dict[str, Any]] = {
    "not_connected": {
        "suggestion": (
            "Run 01_Connection operation='ensure' to acquire (or reacquire) "
            "a live connection to Power BI Desktop."
        ),
        "retry_with": {"tool": "01_Connection", "args": {"operation": "ensure"}},
    },
    "no_instances": {
        "suggestion": (
            "No Power BI Desktop instances are running. Open a .pbip / .pbix "
            "and wait for the model to load, then call 01_Connection:ensure."
        ),
    },
    "connection_lost": {
        "suggestion": (
            "The live connection dropped (PBI Desktop restart / port change). "
            "01_Connection:ensure re-acquires the connection in-place."
        ),
        "retry_with": {"tool": "01_Connection", "args": {"operation": "ensure"}},
    },
    "invalid_model_index": {
        "suggestion": (
            "List instances with 01_Connection:detect, then re-call with a "
            "valid model_index (or use ensure with file_path)."
        ),
        "retry_with": {"tool": "01_Connection", "args": {"operation": "detect"}},
    },
    # Duplicate-name on create — T1-D offers a one-shot fix.
    "duplicate_name": {
        "suggestion": (
            "Object already exists. Re-run with if_exists='update' (or "
            "operation='upsert') to update in place."
        ),
    },
    # PBIR reload pending — visual debug ops will see stale data.
    "reload_pending": {
        "suggestion": (
            "PBIR file edits are pending a Desktop reload. Run "
            "17_Desktop_Bridge:reload to reload the running Desktop in place, "
            "then :screenshot to verify, before debugging."
        ),
        "retry_with": {
            "tool": "17_Desktop_Bridge",
            "args": {"operation": "reload"},
        },
    },
    "stale_plan_hash": {
        "suggestion": (
            "The codemod preview is stale because the model changed. Re-run "
            "operation='preview' to get a fresh plan_hash, then apply."
        ),
    },
    # Gate-blocked — point at the break-glass.
    "gate_blocked": {
        "suggestion": (
            "Op blocked by the destructive-op gate. Pass "
            "confirm_destructive=true on this call to authorize it."
        ),
    },
    "confirm_destructive_required": {
        "suggestion": (
            "Op blocked by the destructive-op gate. Pass "
            "confirm_destructive=true on this call to authorize it."
        ),
    },
    "catastrophic_blocked": {
        "suggestion": (
            "Op is CATASTROPHIC. Pass confirm_destructive=true to proceed " "for this call only."
        ),
    },
    # --- P1b high-frequency additions -----------------------------------
    "query_execution_error": {
        "suggestion": (
            "The DAX/INFO query failed to execute. Verify table/column/measure "
            "names against the live model (02_Model_Operations operation='list', "
            "or 04_Query_Operations operation='search_objects')."
        ),
        "retry_with": {"tool": "04_Query_Operations", "args": {"operation": "search_objects"}},
    },
    "measure_not_found": {
        "suggestion": (
            "No such measure. List measures with 02_Model_Operations "
            "(object_type='measure', operation='list') or search with "
            "04_Query_Operations operation='search_objects'."
        ),
    },
    "table_not_found": {
        "suggestion": (
            "No such table. List tables with 02_Model_Operations "
            "(object_type='table', operation='list')."
        ),
    },
    "column_not_found": {
        "suggestion": (
            "No such column. List columns with 02_Model_Operations "
            "(object_type='column', operation='list', table_name=...)."
        ),
    },
    "page_not_found": {
        "suggestion": (
            "The page name/id wasn't found on the running Desktop. Call "
            "17_Desktop_Bridge operation='status' to list current pages "
            "(or operation='resolve_page' to map a display name to its id)."
        ),
        "retry_with": {"tool": "17_Desktop_Bridge", "args": {"operation": "status"}},
    },
    "ambiguous_instance": {
        "suggestion": (
            "Multiple Power BI Desktop instances are open. Re-detect them with "
            "01_Connection operation='detect' and pass an explicit pid / "
            "pbip_path that matches exactly one."
        ),
        "retry_with": {"tool": "01_Connection", "args": {"operation": "detect"}},
    },
    "cli_not_installed": {
        "suggestion": (
            "The powerbi-desktop bridge CLI is not on PATH. A human must run: "
            "npm install -g @microsoft/powerbi-desktop-bridge-cli (needs Node >= 20; "
            "CLI 0.1.x and >= 1.0.0 are supported)."
        ),
        "requires_human": True,
    },
    "unknown_tool": {
        "suggestion": (
            "That tool name isn't registered in this build. Call "
            "10_Show_User_Guide for the live tool inventory (names + params)."
        ),
        "retry_with": {"tool": "10_Show_User_Guide", "args": {}},
    },
    "invalid_role": {
        "suggestion": (
            "One or more field-role bindings are invalid for the visual type. "
            "See the `role_errors` entries (each lists the allowed roles for "
            "that visual type)."
        ),
    },
    # --- WP2 (2026-09-22) protocol-layer error types -------------------
    "timeout_outcome_unknown": {
        "suggestion": (
            "A WRITE timed out and may still be running or have completed. Do NOT "
            "retry blindly: run the verify_with read call first, then decide."
        ),
    },
    "write_in_progress": {
        "suggestion": (
            "The identical write is still running after an earlier timeout. Wait, then "
            "verify the current state (verify_with) before retrying."
        ),
    },
    "desktop_unresponsive": {
        "suggestion": (
            "Earlier live-model calls are still blocked (Desktop busy: refresh, modal "
            "dialog, or frozen). Check Desktop; resource powerbi://state/dispatcher "
            "lists the stuck calls. Offline PBIP tools keep working meanwhile."
        ),
    },
    "read_only_mode": {
        "suggestion": (
            "The server runs with security.read_only_mode=true: only read operations "
            "are allowed. Ask the operator to lift it for writes."
        ),
    },
    "invalid_arguments": {
        "suggestion": (
            "Fix the argument named in `argument` (valid_values lists allowed enum "
            "values); 10_Show_User_Guide with tool=<name> shows every parameter."
        ),
    },
    "invalid_operation": {
        "suggestion": (
            "Unknown operation for this tool. 10_Show_User_Guide with tool=<name> lists "
            "the valid operations."
        ),
    },
    # --- F-24: argument errors point at the generated ``usage`` block ----------
    "missing_argument": {
        "suggestion": (
            "A required argument is missing. `usage` lists the tool's required / optional "
            "arguments and a canonical example_call; 10_Show_User_Guide tool=<name> "
            "operation=<op> shows every argument."
        ),
    },
    "invalid_argument": {
        "suggestion": (
            "An argument value is invalid. Compare it with `usage.example_call` "
            "(and `did_you_mean` / `valid_values` when present)."
        ),
    },
    "trace_runner_unavailable": {
        "suggestion": (
            "The native trace runner (DaxExecutor.exe) isn't built. Re-run "
            "04_Run_DAX with mode='simple' or 'analyze' (no native trace), or "
            "build it: cd core/infrastructure/dax_executor && dotnet build -c Release."
        ),
    },
}

# Reconcile the historical 3-way spelling drift so a caller that learns the
# recovery for one spelling recognizes them all. Maps an emitted error_type to
# the canonical key present in _RECOVERY_MAP above.
_ERROR_TYPE_ALIASES: Dict[str, str] = {
    "plan_stale": "stale_plan_hash",
    "stale_plan": "stale_plan_hash",
    "confirmation_required": "confirm_destructive_required",
}

# Error types for which a "did you mean" candidate list can be derived from a
# same-envelope list of valid names. Maps error_type -> (target_key, list_key).
_DID_YOU_MEAN: Dict[str, tuple] = {
    "measure_not_found": ("measure", "available_measures"),
    "table_not_found": ("table", "available_tables"),
    "column_not_found": ("column", "available_columns"),
}


def _did_you_mean(result: Dict[str, Any], error_type: str) -> Optional[List[str]]:
    """Best fuzzy matches for a not-found target, if the envelope carries a
    candidate list. Returns None when there's no target or no candidate list."""
    spec = _DID_YOU_MEAN.get(error_type)
    if not spec:
        return None
    target_key, list_key = spec
    target = result.get(target_key)
    candidates = result.get(list_key)
    if not target or not isinstance(candidates, list) or not candidates:
        return None
    matches = difflib.get_close_matches(str(target), [str(c) for c in candidates], n=3, cutoff=0.5)
    return matches or None


# ---------------------------------------------------------------------------
# F-24: ``usage`` block + generic ``did_you_mean`` for argument / not-found errors
# ---------------------------------------------------------------------------

# error_types that get a ``usage`` block (the tool's required / optional args + a
# canonical example call), derived from the live registry schemas + TOOL_EXAMPLES.
_USAGE_ERROR_TYPES = frozenset(
    {
        "missing_argument",
        "invalid_argument",
        "invalid_arguments",
        "invalid_input",
        "invalid_operation",
        "unknown_operation",
        "invalid_object_type",
        "not_found",
        "operation_failed",
    }
)

# Operation selectors, in the order they identify "which op is this call".
_SELECTOR_KEYS = ("operation", "object_type", "batch_operation", "mode")
# Protocol-level args never listed as an operation's own optional args.
_PROTOCOL_ARGS = frozenset(
    {"response_format", "page_size", "next_token", "confirm_destructive", "request_id"}
)
_MAX_OPTIONAL = 10
_MAX_REQUIRED = 12
_MAX_EXAMPLE_CHARS = 700
_MAX_DYM_CANDIDATES = 400


def _is_not_found_type(canonical: str) -> bool:
    return canonical == "not_found" or canonical.endswith("_not_found")


def _registry_tool(tool: Optional[str], registry: Any = None):
    """The ToolDefinition for ``tool`` or None (registry unavailable / unknown)."""
    if not isinstance(tool, str) or not tool:
        return None
    try:
        if registry is None:
            from server.registry import get_registry

            registry = get_registry()
        if not registry.has_tool(tool):
            return None
        return registry.get_tool_def(tool)
    except Exception:  # noqa: BLE001 - enrichment must never break an error path
        return None


def _selector_values(tool_def: Any) -> Dict[str, List[str]]:
    """Valid values per selector arg (schema enum, plus 02's per-type operation table)."""
    props = (getattr(tool_def, "input_schema", None) or {}).get("properties") or {}
    out: Dict[str, List[str]] = {}
    for key in _SELECTOR_KEYS:
        enum = (props.get(key) or {}).get("enum") if isinstance(props.get(key), dict) else None
        if enum:
            out[key] = [str(v) for v in enum]
    by_type = getattr(tool_def, "operations_by_type", None) or {}
    if by_type and "operation" not in out:
        ops = sorted({op for names in by_type.values() for op in names})
        if ops:
            out["operation"] = ops
    return out


def _operation_of(tool_def: Any, arguments: Dict[str, Any]) -> Optional[str]:
    props = (getattr(tool_def, "input_schema", None) or {}).get("properties") or {}
    for key in ("operation", "batch_operation", "mode"):
        if key in props and isinstance(arguments.get(key), str):
            return arguments[key]
    return None


def _mentions_op(spec: Any, operation: str) -> bool:
    import re

    if not isinstance(spec, dict):
        return False
    text = " ".join(str(spec.get(k) or "") for k in ("description", "x-doc"))
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(operation)}(?![A-Za-z0-9_])", text) is not None


def _args_named_in(error: Any, props: Dict[str, Any]) -> List[str]:
    """Declared argument names the error message talks about ("X is required")."""
    import re

    text = str(error or "")
    if not re.search(r"required|missing|must|provide|pass ", text, re.IGNORECASE):
        return []
    return [
        p
        for p in props
        if len(p) > 2 and re.search(rf"(?<![A-Za-z0-9_]){re.escape(p)}(?![A-Za-z0-9_])", text)
    ]


def _shrink(value: Any) -> Any:
    """Copy of an example with long strings / lists shortened (payload cap)."""
    if isinstance(value, str):
        return value if len(value) <= 80 else value[:77] + "..."
    if isinstance(value, list):
        return [_shrink(v) for v in value[:2]]
    if isinstance(value, dict):
        return {k: _shrink(v) for k, v in value.items()}
    return value


def _pick_example(tool_def: Any, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The TOOL_EXAMPLES entry closest to the call (matching operation/object_type)."""
    try:
        examples = tool_def.resolved_examples() or []
    except Exception:  # noqa: BLE001
        examples = []
    best: Optional[Dict[str, Any]] = None
    best_score = 0
    for ex in examples:
        if not isinstance(ex, dict):
            continue
        score = 0
        mismatch = False
        for key in _SELECTOR_KEYS:
            if key in arguments and key in ex:
                if arguments[key] == ex[key]:
                    score += 2 if key == "operation" else 1
                elif key in ("operation", "batch_operation"):
                    mismatch = True
        if mismatch or score == 0:
            continue
        if score > best_score:
            best, best_score = ex, score
    return dict(best) if best is not None else None


def _build_usage(
    tool: str, tool_def: Any, arguments: Dict[str, Any], result: Dict[str, Any]
) -> Dict[str, Any]:
    schema = getattr(tool_def, "input_schema", None) or {}
    props = schema.get("properties") or {}
    operation = _operation_of(tool_def, arguments)
    required: List[str] = [r for r in (schema.get("required") or []) if isinstance(r, str)]
    for name in _args_named_in(result.get("error"), props):
        if name not in required and name not in _SELECTOR_KEYS and name not in _PROTOCOL_ARGS:
            required.append(name)
    for key in ("missing", "missing_arguments", "missing_args"):
        extra = result.get(key)
        if isinstance(extra, list):
            required += [str(x) for x in extra if str(x) not in required]
    required = required[:_MAX_REQUIRED]

    skip = set(required) | _PROTOCOL_ARGS | set(_SELECTOR_KEYS)
    optional: List[str] = []
    if operation:
        optional = [
            n for n, spec in props.items() if n not in skip and _mentions_op(spec, operation)
        ]
    if not optional:
        optional = [n for n in props if n not in skip]
    optional = optional[:_MAX_OPTIONAL]

    example = _pick_example(tool_def, arguments)
    if example is None:
        example = {k: arguments[k] for k in _SELECTOR_KEYS if isinstance(arguments.get(k), str)}
        for name in required:
            if name not in example:
                example[name] = f"<{name}>"
    example = _shrink(example)
    import json as _json

    try:
        if len(_json.dumps(example, default=str)) > _MAX_EXAMPLE_CHARS:
            example = {k: example[k] for k in example if k in _SELECTOR_KEYS or k in required}
    except (TypeError, ValueError):
        example = {}
    usage: Dict[str, Any] = {
        "tool": tool,
        "operation": operation,
        "required": required,
        "optional_top": optional,
        "example_call": {"tool": tool, "args": example},
        "more": f"10_Show_User_Guide tool={tool}"
        + (f" operation={operation}" if operation else ""),
    }
    return usage


def _fuzzy(target: Any, candidates: Any) -> Optional[List[str]]:
    if not isinstance(target, str) or not target or not isinstance(candidates, list):
        return None
    names = [str(c) for c in candidates[:_MAX_DYM_CANDIDATES] if isinstance(c, (str, int))]
    if not names:
        return None
    lowered = {n.lower(): n for n in names}
    hit = lowered.get(target.lower())
    if hit is not None and hit != target:
        return [hit]  # same name, different case
    matches = difflib.get_close_matches(target, names, n=3, cutoff=0.5)
    return [m for m in matches if m != target] or None


def _generic_did_you_mean(
    result: Dict[str, Any],
    canonical: str,
    tool_def: Any,
    arguments: Dict[str, Any],
) -> Optional[List[str]]:
    """did_you_mean for a bad operation / enum value / not-found name (F-24)."""
    # 1. an enum-style argument the envelope names (invalid_arguments / invalid_operation).
    bad = result.get("argument")
    valid = result.get("valid_values")
    if isinstance(bad, str) and isinstance(valid, list):
        dym = _fuzzy(arguments.get(bad.split(".")[-1]), valid)
        if dym:
            return dym
    # 2. an operation / object_type that is not in the tool's own value set.
    if tool_def is not None and (
        canonical in ("invalid_operation", "unknown_operation", "invalid_object_type")
        or "valid_values" in result
    ):
        for key, values in _selector_values(tool_def).items():
            value = arguments.get(key)
            if isinstance(value, str) and value not in values:
                dym = _fuzzy(value, values)
                if dym:
                    return dym
        if isinstance(valid, list):
            for key in _SELECTOR_KEYS:
                dym = _fuzzy(arguments.get(key), valid)
                if dym:
                    return dym
    # 3. a not-found name, against whatever candidate list the envelope carries.
    if _is_not_found_type(canonical):
        lists = [
            v
            for k, v in result.items()
            if isinstance(v, list)
            and (k.startswith("available") or k in ("valid_values", "candidates", "suggestions"))
        ]
        targets: List[Any] = [
            result.get(k)
            for k in ("target", "name", "measure", "table", "column", "page", "visual")
        ]
        targets += [v for k, v in arguments.items() if isinstance(v, str) and k.endswith("_name")]
        targets += [arguments.get(k) for k in ("page", "visual", "name", "table", "measure")]
        for target in targets:
            for cands in lists:
                dym = _fuzzy(target, cands)
                if dym:
                    return dym
    return None


def enrich_error_response(
    result: Any,
    *,
    tool: Optional[str] = None,
    arguments: Optional[Dict[str, Any]] = None,
    registry: Any = None,
) -> Any:
    """Attach a retry_with/suggestion hint to a failed result if known.

    F-24: when ``tool`` (and the call's ``arguments``) are known — the dispatcher and
    the protocol layer pass them — argument / not-found / unknown-operation failures
    also get ``usage`` (``{tool, operation, required[], optional_top[], example_call}``,
    derived from the registry schema + TOOL_EXAMPLES, lists capped) and, for a bad
    operation, enum value or not-found name, ``did_you_mean``.

    Returns the same object (mutated in place when applicable) for chaining.
    Non-dict inputs and successful results pass through untouched.
    """
    if not isinstance(result, dict):
        return result
    if result.get("success") is not False:
        return result
    error_type = result.get("error_type")
    if not error_type:
        return result
    # Normalize drift spellings to the canonical recovery key.
    canonical = str(_ERROR_TYPE_ALIASES.get(error_type, error_type))

    # did-you-mean can attach even without a _RECOVERY_MAP entry, as long as
    # the not-found envelope carries a candidate list.
    dym = _did_you_mean(result, canonical)
    if dym and "did_you_mean" not in result:
        result["did_you_mean"] = dym

    if tool is None and isinstance(result.get("tool"), str):
        tool = result["tool"]
    args = arguments if isinstance(arguments, dict) else {}
    if tool is not None and (canonical in _USAGE_ERROR_TYPES or _is_not_found_type(canonical)):
        try:
            tool_def = _registry_tool(tool, registry)
            if tool_def is not None:
                if "did_you_mean" not in result:
                    gdym = _generic_did_you_mean(result, canonical, tool_def, args)
                    if gdym:
                        result["did_you_mean"] = gdym
                if "usage" not in result:
                    result["usage"] = _build_usage(tool, tool_def, args, result)
        except Exception as e:  # noqa: BLE001 - enrichment is advisory
            logger.debug("usage enrichment skipped for %s: %s", tool, e)
    elif canonical == "unknown_tool" and "did_you_mean" not in result:
        names = result.get("available_tools")
        bad_tool = tool or result.get("error", "").replace("Unknown tool:", "").strip()
        dym = _fuzzy(bad_tool, names)
        if dym:
            result["did_you_mean"] = dym

    recovery = _RECOVERY_MAP.get(canonical)
    if not recovery:
        return result
    # Handler-supplied values win.
    if "suggestion" in recovery and "suggestion" not in result:
        result["suggestion"] = recovery["suggestion"]
    if "retry_with" in recovery and "retry_with" not in result:
        result["retry_with"] = recovery["retry_with"]
    if "requires_human" in recovery and "requires_human" not in result:
        result["requires_human"] = recovery["requires_human"]
    return result
