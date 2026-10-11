"""15_DAX_Refactor handler — preview/apply bulk DAX refactor transforms.

Renamed from 15_DAX_Codemods (the implementation modules under
core/codemods/ keep their internal name to avoid churn). The public MCP
tool name now reflects what the tool does: bulk refactoring with
preview/apply + plan_hash staleness check.

2026-09-22 review (E-02, E-06, E-12, E-13, E-22, E-27, A-15):
- the live model is read with ``bypass_cache=True`` and the plan hash covers the
  transform, its args and the connected database;
- rollback is gated like apply and only accepts journals from the journal dir;
- deletes check report usage through ``pbip_path`` (or the connection's PBIP
  folder) — without it a delete is skipped, never guessed;
- semantics-preserving transforms are verified before/after (``verify``);
- the reload hint is gone (live-TOM writes need no reload; it read a
  non-existent ``connection_state.pbip_folder_path`` anyway).
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Dict, List, Optional, Tuple

from core.autonomous.gating import Severity, merge_gate_info, require_autonomous_mode
from core.codemods import transaction
from server.registry import ToolDefinition
from server.tool_schemas import REQUEST_ID_PROPERTY

logger = logging.getLogger(__name__)

_VALID_OPS = {"list_transforms", "preview", "apply", "rollback", "list_journals"}

# Verification evaluates the affected measures in chunks of this size.
_VERIFY_CHUNK = 25
_VERIFY_MAX = 200


def _connection_state():
    from core.infrastructure.connection_state import connection_state

    return connection_state


def _model_or_err(transform: Optional[str] = None):
    cs = _connection_state()
    if not cs.is_connected():
        return None, {
            "success": False,
            "error": "Not connected to Power BI Desktop",
            "error_type": "not_connected",
        }
    from core.infrastructure.live_model_facade import build_live_model

    # E-02: the plan hash and the journal's `before` values must come from a
    # fresh read, never from the query result cache.
    model = build_live_model(bypass_cache=True)
    if model is None:
        return None, {
            "success": False,
            "error": "Live model unavailable",
            "error_type": "not_connected",
        }
    if transform == "extract_udf":
        # F-15: the compatibility level gate and the existing function names come from the live
        # model (and are part of the plan hash), not from the facade.
        from core.dax.udf_support import attach_udf_context

        attach_udf_context(model, getattr(cs, "query_executor", None))
    return model, None


def _identity() -> Tuple[Optional[str], Dict[str, Any]]:
    try:
        info = _connection_state().get_connection_identity() or {}
    except Exception:  # noqa: BLE001
        info = {}
    db = info.get("database")
    return (str(db) if db else None), {"database": db, "port": info.get("port")}


def _pbip_path(args: Dict[str, Any]) -> Optional[str]:
    p = args.get("pbip_path")
    if p:
        return str(p)
    try:
        return _connection_state().get_pbip_folder_path()
    except Exception:  # noqa: BLE001
        return None


def _evaluator(refs: List[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
    """Grand-total values of ``refs`` (fresh, uncached); None when any query fails."""
    from core.dax.dax_names import column_ref, dax_string

    qe = getattr(_connection_state(), "query_executor", None)
    if qe is None:
        return None
    out: Dict[str, Any] = {}
    refs = list(refs)[:_VERIFY_MAX]
    for i in range(0, len(refs), _VERIFY_CHUNK):
        chunk = refs[i : i + _VERIFY_CHUNK]
        cols = ", ".join(f"{dax_string(f'{t}[{m}]')}, {column_ref(t, m)}" for t, m in chunk)
        try:
            res = qe.validate_and_execute_dax(f"EVALUATE ROW({cols})", 0, bypass_cache=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("verification query failed: %s", e)
            return None
        if not isinstance(res, dict) or not res.get("success"):
            return None
        rows = res.get("rows") or []
        if not rows:
            return None
        for k, v in rows[0].items():
            key = k[1:-1] if isinstance(k, str) and k.startswith("[") and k.endswith("]") else k
            out[key] = v
    return out


def _validator(after: str, defines: Dict[Tuple[str, str], str]) -> Optional[str]:
    """Engine parse/bind check of one rewritten body (no grand total computed:
    ``IF(FALSE(), <expr>)``, the closing parens on their own line so a trailing
    comment cannot swallow them). Returns the engine error or None."""
    from core.dax.dax_names import column_ref

    qe = getattr(_connection_state(), "query_executor", None)
    if qe is None:
        return None  # nothing to validate against — the TOM compile check still runs
    define = ""
    if defines:
        parts = [f"MEASURE {column_ref(t, m)} =\n{body}\n" for (t, m), body in defines.items()]
        define = "DEFINE\n" + "\n".join(parts) + "\n"
    query = f'{define}EVALUATE ROW("ok", IF(FALSE(), {after}\n))'
    try:
        res = qe.validate_and_execute_dax(query, 0, bypass_cache=True)
    except Exception as e:  # noqa: BLE001
        return str(e)
    if isinstance(res, dict) and res.get("success") is False:
        return str(res.get("error") or "rejected")
    return None


def _severity(transform: str, args: Dict[str, Any]) -> Severity:
    if transform == "inline_measure" and args.get("delete_after", False):
        return Severity.CATASTROPHIC
    return Severity.DESTRUCTIVE


def _invalid(msg: str) -> Dict[str, Any]:
    return {"success": False, "error": msg, "error_type": "invalid_args"}


def handle_dax_codemods(args: Dict[str, Any]) -> Dict[str, Any]:
    op = args.get("operation")
    if op not in _VALID_OPS:
        return {
            "success": False,
            "error": f"Unknown operation {op!r}",
            "valid_operations": sorted(_VALID_OPS),
            "error_type": "invalid_args",
        }

    if op == "list_transforms":
        out = transaction.list_transforms()
        out["success"] = True
        return out

    if op == "list_journals":
        return transaction.list_journals(int(args.get("limit") or 50))

    if op == "preview":
        transform = args.get("transform")
        if not transform:
            return _invalid("transform is required")
        model, err = _model_or_err(transform)
        if err:
            return err
        identity, _info = _identity()
        return transaction.preview(model, transform, args.get("args") or {}, identity)

    if op == "apply":
        transform = args.get("transform")
        plan_hash = args.get("plan_hash")
        if not transform:
            return _invalid("transform is required")
        if not plan_hash:
            return _invalid("plan_hash is required (run preview first)")
        targs = args.get("args") or {}
        from core.codemods.transforms import registry

        t = registry().get(transform)
        if t is not None and not t.persistable:
            # E-22: refuse before the gate (a >10-move batch used to be gated
            # CATASTROPHIC for an apply that always refuses).
            return transaction.apply(None, transform, targs, plan_hash)
        model, err = _model_or_err(transform)
        if err:
            return err
        gate = require_autonomous_mode(
            op_name=f"15_DAX_Refactor:apply:{transform}",
            args=args,
            severity=_severity(transform, targs),
        )
        if gate is not None and not gate.get("success", True):
            return gate
        identity, info = _identity()
        from core.codemods.report_refs import find_measure_bindings

        result = transaction.apply(
            model,
            transform,
            targs,
            plan_hash,
            identity=identity,
            identity_info=info,
            visual_checker=functools.partial(find_measure_bindings, _pbip_path(args)),
            evaluator=_evaluator,
            verify=args.get("verify"),
            validator=_validator,
        )
        return merge_gate_info(result, gate)

    if op == "rollback":
        journal_path = args.get("journal_path")
        if not journal_path:
            return _invalid("journal_path is required (returned by a prior apply)")
        cs = _connection_state()
        if not cs.is_connected():
            return {
                "success": False,
                "error": "Not connected to Power BI Desktop",
                "error_type": "not_connected",
            }
        # E-13: rollback writes the model — gated like apply.
        gate = require_autonomous_mode(
            op_name="15_DAX_Refactor:rollback",
            args=args,
            severity=Severity.DESTRUCTIVE,
        )
        if gate is not None and not gate.get("success", True):
            return gate
        identity, _info = _identity()
        result = transaction.rollback(
            journal_path, identity=identity, force=bool(args.get("force", False))
        )
        return merge_gate_info(result, gate)

    return {"success": False, "error": f"Unhandled op {op!r}", "error_type": "invalid_args"}


_ARGS_DESCRIPTION = (
    "Transform arguments (see list_transforms args_schema). Every transform accepts "
    "'measures' (['Table[Name]'|'Name']) and 'tables' to limit its scope. "
    "inline_measure: measure_ref, delete_after (default false). extract_measure: "
    "expression, new_name, target_table, replace_in_measures, auto_detect. "
    "replace_pattern: pattern, replacement. swap_table_reference: from_table, to_table, "
    "column_map. use_divide / remove_unused_variables: scope. move_measure_batch "
    "(preview-only): moves [{measure_ref, target_table}]. extract_udf (F-15): measures "
    "(2+ 'Table[Name]' with the same body structure - see 05_DAX_Intelligence find_clones), "
    "function_name, parameter_names, description; needs compatibility level >= 1702. The "
    "plan_hash covers these args: apply must pass the SAME args as preview."
)


def register_dax_codemods_handler(registry):
    registry.register(
        ToolDefinition(
            name="15_DAX_Refactor",
            description=(
                "DAX refactoring codemods: preview -> apply (plan_hash staleness check) -> "
                "rollback (journal_path); list_transforms, list_journals. Transforms: "
                "inline_measure, extract_measure, extract_udf, replace_pattern, "
                "move_measure_batch (preview-only), swap_table_reference, use_divide, "
                "remove_unused_variables. Lexer-based; apply is one TOM transaction and "
                "value-verifies pure refactors, rolling back on a difference."
            ),
            guide_notes=(
                "Bulk DAX refactoring with preview/apply/rollback + plan_hash staleness "
                "check (model state + transform + args + database). Transforms: "
                "inline_measure, extract_measure, extract_udf (copy-pasted measures -> one "
                "DAX UDF + calls; creates the function first, removes it on any failure; "
                "CL >= 1702), replace_pattern, move_measure_batch "
                "(preview-only), swap_table_reference, use_divide, remove_unused_variables. "
                "Rewrites are lexer-based; structurally broken results are rejected. apply "
                "writes a write-ahead undo journal, commits the whole plan in one TOM "
                "transaction and (pure refactors) verifies the affected measures' values, "
                "rolling back on a difference. rollback(journal_path) restores the "
                "pre-apply state if the targets are unchanged since (force=true overrides)."
            ),
            handler=handle_dax_codemods,
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": sorted(_VALID_OPS)},
                    # F-27: idempotency key (deferred in tools/list)
                    "request_id": dict(REQUEST_ID_PROPERTY),
                    "transform": {"type": "string"},
                    "args": {
                        "type": "object",
                        "description": (
                            "Transform arguments (shapes: list_transforms args_schema); all "
                            "accept measures ['Table[Name]'|'Name'] and tables to limit "
                            "scope."
                        ),
                        "x-doc": _ARGS_DESCRIPTION,
                    },
                    "plan_hash": {"type": "string"},
                    "journal_path": {
                        "type": "string",
                        "description": "rollback op: the journal_path returned by a prior apply.",
                    },
                    "pbip_path": {
                        "type": "string",
                        "description": (
                            "apply: PBIP project/report path used to check report visuals "
                            "before a delete (defaults to the connection's PBIP folder)."
                        ),
                    },
                    "verify": {
                        "type": "boolean",
                        "description": (
                            "apply: value-check the affected measures and roll back on a "
                            "difference (default true for pure refactors)."
                        ),
                        "x-doc": (
                            "apply: evaluate the affected measures before/after and roll "
                            "back on a difference. Default: true for semantics-preserving "
                            "transforms (inline/extract/remove_unused_variables), false "
                            "for use_divide / replace_pattern / swap_table_reference."
                        ),
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "rollback: overwrite targets changed since the apply and "
                            "replay journals without a database identity."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "description": "list_journals: max journals returned (newest first).",
                    },
                },
                "required": ["operation"],
            },
            category="model",
            sort_order=1500,
            # WP2 (A-10/L-08): apply/rollback write the live model.
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
    )
