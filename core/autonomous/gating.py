"""
Destructive-op gating — policy-driven decorator that blocks destructive handler
calls unless explicitly confirmed.

Four policies, set by MCP_AUTONOMOUS_GATE_POLICY env var (default LIGHT):

    OFF     — no gating at all
    LIGHT   — blocks only catastrophic ops; advisory notice on destructive
    WARN    — logs warning on destructive+, never blocks
    STRICT  — blocks all destructive ops

Break-glass: pass confirm_destructive=true in args to authorize the op.

(Autonomous mode was removed in v7.8 — the only way past a block is now
confirm_destructive=true. The env-var name is retained for compatibility.)
"""

from __future__ import annotations

import enum
import functools
import logging
import os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

GATE_POLICY_OFF = "OFF"
GATE_POLICY_LIGHT = "LIGHT"
GATE_POLICY_WARN = "WARN"
GATE_POLICY_STRICT = "STRICT"

_ALL_POLICIES = {GATE_POLICY_OFF, GATE_POLICY_LIGHT, GATE_POLICY_WARN, GATE_POLICY_STRICT}
_DEFAULT_POLICY = GATE_POLICY_LIGHT


class Severity(enum.Enum):
    DESTRUCTIVE = "destructive"
    CATASTROPHIC = "catastrophic"


_policy_cache: Optional[str] = None
_policy_cache_src: Optional[str] = None


def current_policy() -> str:
    """Read the gate policy from the env var each call; fallback to LIGHT.

    Read live (a dict lookup, negligible next to the CLR ops the gate guards)
    so an operator tightening MCP_AUTONOMOUS_GATE_POLICY mid-session — e.g. to
    STRICT after spotting erratic agent behaviour — takes effect immediately,
    without a process restart. (Previously cached-forever after first read,
    which silently ignored runtime changes.) The raw string is memoized per
    distinct env value only to avoid repeating the validation warning.
    """
    global _policy_cache, _policy_cache_src
    raw = (os.environ.get("MCP_AUTONOMOUS_GATE_POLICY") or _DEFAULT_POLICY).upper()
    if _policy_cache is not None and _policy_cache_src == raw:
        return _policy_cache
    resolved = raw
    if resolved not in _ALL_POLICIES:
        logger.warning(
            "Unknown MCP_AUTONOMOUS_GATE_POLICY=%r; using default %s", raw, _DEFAULT_POLICY
        )
        resolved = _DEFAULT_POLICY
    _policy_cache_src = raw
    _policy_cache = resolved
    return resolved


def invalidate_policy_cache() -> None:
    """Clear the memoized gate policy. Used by tests that monkeypatch the env."""
    global _policy_cache, _policy_cache_src
    _policy_cache = None
    _policy_cache_src = None


def is_confirmed(args: Optional[Dict[str, Any]]) -> bool:
    """The ONE break-glass test (C-26): strict boolean ``True`` only.

    ``confirm_destructive`` is canonical; the legacy ``confirmed`` spelling is
    accepted as an alias. A truthy string such as ``"false"`` never confirms.
    """
    if not isinstance(args, dict):
        return False
    return args.get("confirm_destructive") is True or args.get("confirmed") is True


def _build_block_response(op_name: str, severity: Severity, policy: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error_type": "confirm_destructive_required",
        "error": (
            f"{severity.value.capitalize()} operation {op_name!r} blocked. "
            f"Policy {policy} requires explicit confirmation for this operation."
        ),
        "hint": (
            "Pass confirm_destructive=true to authorize this operation. "
            "(Autonomous mode was removed in v7.8; confirm_destructive is the "
            "break-glass.)"
        ),
        "blocked_op": op_name,
        "severity": severity.value,
        "policy": policy,
    }


def _build_warning(op_name: str, severity: Severity, policy: str) -> Dict[str, Any]:
    return {
        "gate_warning": (
            f"{severity.value.capitalize()} op {op_name!r} ran under policy {policy}. "
            f"Pass confirm_destructive=true to acknowledge."
        )
    }


def _build_advisory_notice(op_name: str, policy: str) -> Dict[str, Any]:
    return {
        "gate_notice": (
            f"Destructive op {op_name!r} ran under policy {policy}. "
            f"Pass confirm_destructive=true to acknowledge catastrophic ops."
        )
    }


def _blast_radius_details(
    args: Dict[str, Any], evidence: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Summarize what the user would be authorizing, for the confirm prompt.

    Best-effort and purely presentational: pulls the blast radius / affected
    object count out of the evidence (or args) that the handlers already compute,
    so the human sees *what* the op touches rather than just its name. Never
    raises — a missing key just means a shorter prompt.
    """
    details: Dict[str, Any] = {}
    # C-02: only handler-computed evidence — never the caller's `evidence` arg.
    ev = evidence
    if isinstance(ev, dict):
        br = ev.get("blast_radius")
        if isinstance(br, dict):
            unsafe = br.get("unsafe_nodes")
            if unsafe:
                details["unsafe_nodes"] = unsafe
            breaks = br.get("breaks")
            if breaks:
                details["breaks"] = breaks
            impacts = br.get("impacts")
            if isinstance(impacts, (list, dict)):
                details["impacted_objects"] = len(impacts)
        diff = ev.get("baseline_diff")
        if isinstance(diff, dict) and diff.get("changed"):
            details["baseline_changed"] = diff["changed"]
    items = args.get("items")
    if isinstance(items, list):
        details["item_count"] = len(items)
    for key in ("table_name", "name", "measure_name", "column_name", "pbip_path"):
        value = args.get(key)
        if isinstance(value, str) and value:
            details[key] = value
    return details


def _try_elicit_confirmation(
    op_name: str,
    args: Dict[str, Any],
    severity: Severity,
    policy: str,
    evidence: Optional[Dict[str, Any]],
) -> Optional[bool]:
    """Ask the user to confirm interactively. True/False/None (see server.elicitation).

    `core/` must not hard-depend on `server/`, so the import is lazy and any
    ImportError degrades to None — i.e. exactly the pre-elicitation behaviour.
    """
    try:
        from server.elicitation import elicit_confirmation
    except ImportError:  # pragma: no cover - defensive
        return None
    try:
        return elicit_confirmation(
            message=(
                f"Power BI MCP is about to run a {severity.value} operation that gate "
                f"policy {policy} blocks without confirmation."
            ),
            op_name=op_name,
            severity=severity.value,
            details=_blast_radius_details(args, evidence),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("gate elicitation failed for %s: %s", op_name, e)
        return None


def _build_user_confirmed_notice(op_name: str, severity: Severity, policy: str) -> Dict[str, Any]:
    """Advisory returned when the user confirmed the op via elicitation.

    Deliberately advisory-shaped (no `success: False`) so merge_gate_info folds
    it into the handler result instead of short-circuiting as a block.
    """
    notice = _build_advisory_notice(op_name, policy)
    notice["gate_notice"] = (
        f"{severity.value.capitalize()} op {op_name!r} was confirmed interactively by the "
        f"user in response to a gate prompt (policy {policy})."
    )
    notice["user_confirmed"] = True
    return notice


def _build_user_declined_block(op_name: str, severity: Severity, policy: str) -> Dict[str, Any]:
    """Block returned when the user explicitly declined the confirm prompt.

    error_type stays `confirm_destructive_required` — callers and tests match on
    it, and the programmatic break-glass is still the way past this gate.
    """
    block = _build_block_response(op_name, severity, policy)
    block["error"] = (
        f"{severity.value.capitalize()} operation {op_name!r} blocked: the user declined "
        f"the confirmation prompt."
    )
    block["user_declined"] = True
    return block


def _evidence_proves_safe(evidence: Optional[Dict[str, Any]]) -> bool:
    """True when `evidence` demonstrates an op is safe to auto-clear.

    C-02/C-03: only evidence computed IN-PROCESS by ``core.autonomous.evidence``
    for the op's own targets counts, and only when its proof conditions held
    (``proven`` — complete report side, no unresolved graph nodes, fresh for the
    current model etag). The blast radius must then be affirmatively safe:
      - batch shape: {"all_safe": bool, "unsafe_nodes": [...]}
      - single shape: {"safe": bool, "breaks": [...]}
    and, when present, a baseline diff ({"changed": [...]}) must be empty.
    """
    if not isinstance(evidence, dict):
        return False
    try:
        from core.autonomous.evidence import evidence_is_proof
    except Exception:  # noqa: BLE001
        return False
    if not evidence_is_proof(evidence):
        return False
    br = evidence.get("blast_radius")
    if not isinstance(br, dict):
        return False
    # batch shape wins if present; else single-node shape.
    if "all_safe" in br:
        blast_safe = bool(br.get("all_safe")) and not br.get("unsafe_nodes")
    elif "safe" in br:
        blast_safe = bool(br.get("safe"))
    else:
        return False
    if not blast_safe:
        return False
    # If a baseline diff is supplied, any changed value forbids the downgrade.
    diff = evidence.get("baseline_diff")
    if isinstance(diff, dict) and diff.get("changed"):
        return False
    return True


def _gated_block(
    op_name: str,
    args: Dict[str, Any],
    severity: Severity,
    policy: str,
    evidence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Produce the response for a path that would otherwise hard-block.

    Offers the user an interactive confirmation first (elicitation), which turns
    the old "block → agent retries with confirm_destructive=true" round trip into
    a single in-flight prompt. Fail-safe by construction:

        confirmed (True)  → advisory, op proceeds
        declined  (False) → block, flagged user_declined
        no answer (None)  → the exact pre-elicitation block response
    """
    answer = _try_elicit_confirmation(op_name, args, severity, policy, evidence)
    if answer is True:
        return _build_user_confirmed_notice(op_name, severity, policy)
    if answer is False:
        return _build_user_declined_block(op_name, severity, policy)
    return _build_block_response(op_name, severity, policy)


def require_autonomous_mode(
    op_name: str,
    args: Dict[str, Any],
    severity: Severity,
    evidence: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Check gate policy. Returns None to pass, or a dict to merge/override.

    Return semantics:
      - None               → pass through normally
      - {"success":False}  → BLOCK: return this dict as the handler response
      - {"gate_warning":..} or {"gate_notice":..} → pass through, caller merges into response

    Evidence-aware downgrade (LIGHT only): if the handler-computed `evidence`
    (never a caller-supplied ``args['evidence']`` — C-02) proves the op is safe — zero blast radius via 16_Dependency_Graph, and no
    baseline regression when a compare_baseline diff is supplied — a
    CATASTROPHIC op is auto-cleared to a DESTRUCTIVE advisory instead of a hard
    block. STRICT is never relaxed by evidence (it's the unsupervised-safe
    tier); no-evidence calls behave exactly as before.

    Interactive confirmation: on the paths that would hard-block (STRICT at any
    severity, LIGHT × CATASTROPHIC not cleared by evidence) the gate first asks
    the user directly via MCP elicitation. Confirmed → advisory with
    `user_confirmed`; declined → block with `user_declined` (error_type
    unchanged); no answer / no client support / MCP_GATE_ELICITATION=0 → the
    exact pre-elicitation block response. Fail-safe: absent elicitation nothing
    about this function's behaviour changes.
    """
    # Break-glass: explicit per-call confirmation (strict bool, C-26)
    if is_confirmed(args):
        return None

    policy = current_policy()

    if policy == GATE_POLICY_OFF:
        return None

    if policy == GATE_POLICY_STRICT:
        return _gated_block(op_name, args, severity, policy, evidence)

    if policy == GATE_POLICY_WARN:
        return _build_warning(op_name, severity, policy)

    # LIGHT
    if severity is Severity.CATASTROPHIC:
        # C-02: args["evidence"] is NEVER consulted — only the evidence the
        # handler computed in-process for the targets it will actually touch.
        ev = evidence
        if _evidence_proves_safe(ev):
            notice = _build_advisory_notice(op_name, policy)
            notice["gate_notice"] = (
                f"Catastrophic op {op_name!r} auto-cleared to advisory under {policy}: "
                "in-process evidence shows zero blast radius"
                + (
                    " and no baseline regression"
                    if isinstance(ev, dict) and ev.get("baseline_diff")
                    else ""
                )
                + "."
            )
            notice["evidence_cleared"] = True
            return notice
        return _gated_block(op_name, args, severity, policy, evidence)
    # LIGHT × destructive → advisory
    return _build_advisory_notice(op_name, policy)


_GATE_INFO_KEYS = (
    "gate_warning",
    "gate_notice",
    "blocked_op",
    "severity",
    "policy",
    "evidence_cleared",
    "user_confirmed",
    "user_declined",
)


def merge_gate_info(result: Dict[str, Any], gate: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge advisory/warning gate fields into a handler result without ever
    overwriting `success`, `error`, `error_type`, or any handler-owned key.

    Only `gate_warning`, `gate_notice`, and metadata keys (`blocked_op`,
    `severity`, `policy`) are copied. Existing `gate_*` entries are preserved
    using setdefault so nested calls do not stomp earlier notices.
    """
    if not isinstance(result, dict) or not isinstance(gate, dict):
        return result
    # Never merge a block response into a result — that's the caller's job
    # (it should short-circuit before calling here).
    if gate.get("success") is False:
        return result
    for key in _GATE_INFO_KEYS:
        if key in gate:
            result.setdefault(key, gate[key])
    return result


def autonomous_gated(op_name: str, severity: Severity) -> Callable:
    """Decorator that gates a handler function via the autonomous mode policy."""

    def _decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def _wrapped(args: Dict[str, Any], *a, **kw) -> Dict[str, Any]:
            gate = require_autonomous_mode(op_name, args or {}, severity)
            if gate is not None and gate.get("success") is False:
                return gate
            result = fn(args, *a, **kw)
            if not isinstance(result, dict):
                result = {"success": True, "result": result}
            return merge_gate_info(result, gate)

        return _wrapped

    return _decorator
