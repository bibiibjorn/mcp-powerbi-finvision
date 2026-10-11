"""
Batch Operations Handler
Unified handler for batch operations on model objects
"""

from typing import Dict, Any, List, Optional
import logging
from server.registry import ToolDefinition
from server.progress import emit_progress
from server.tool_schemas import TOOL_SCHEMAS
from core.operations.batch_operations import BatchOperationsHandler
from core.autonomous.gating import require_autonomous_mode, Severity, merge_gate_info

logger = logging.getLogger(__name__)

_MUTATING_BATCH_OPS = {
    "create",
    "update",
    "delete",
    "rename",
    "move",
    "move_display_folder",
    "activate",
    "deactivate",
    "refresh",
}

# Create singleton instance
_batch_ops_handler = BatchOperationsHandler()


def _per_type_cap(object_type: str) -> int:
    return {"tables": 30, "relationships": 100}.get(object_type, 200)


def _check_per_type_cap(object_type: str, items: list) -> Optional[Dict[str, Any]]:
    cap = _per_type_cap(object_type)
    if len(items) > cap:
        return {
            "success": False,
            "error_type": "items_exceeds_cap",
            "error": f"Batch size {len(items)} exceeds per-type cap {cap} for object_type={object_type!r}",
            "hint": f"Split into multiple calls of \u2264{cap} items each.",
        }
    return None


def _attach_blast_radius(
    response: Optional[Dict[str, Any]], evidence: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Surface a computed-but-not-clearing blast radius on a gate block so the
    agent sees WHY the delete was blocked (the analysis isn't discarded)."""
    from core.autonomous.evidence import attach_blast_radius

    out = attach_blast_radius(response, evidence)
    if isinstance(out, dict) and isinstance(evidence, dict) and evidence.get("unproven_reasons"):
        out.setdefault("evidence_unproven_reasons", evidence["unproven_reasons"])
    return out


def _table_item_name(item: Any) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    return item.get("table_name") or item.get("name") or item.get("table")


def _autopopulate_changeset_delete_evidence(
    severity, doc: Dict[str, Any], args: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Blast-radius evidence for a CATASTROPHIC changeset apply.

    Collects table + column delete refs from the (canonicalized, C-19)
    changeset — the same keys ``_changeset_apply_one`` executes. Relationship
    deletes have no simple graph node, so their presence means the evidence
    can't fully vouch: return None and keep the hard gate. A caller-supplied
    ``evidence`` argument is never used (C-02).
    """
    # Policy-blind guard: the gate only reads evidence under LIGHT.
    from core.autonomous.gating import GATE_POLICY_LIGHT, current_policy, is_confirmed

    if current_policy() != GATE_POLICY_LIGHT:
        return None
    if severity is not Severity.CATASTROPHIC:
        return None
    if is_confirmed(args):
        return None
    from core.autonomous import evidence as _ev

    refs: list = []
    tbl_deletes = ((doc.get("tables") or {}).get("delete")) or []
    col_deletes = ((doc.get("columns") or {}).get("delete")) or []
    rel_deletes = ((doc.get("relationships") or {}).get("delete")) or []
    if rel_deletes:
        return None  # can't prove relationship-delete safety via the node graph
    for t in tbl_deletes:
        name = _table_item_name(t)
        if name:
            refs.append(_ev.table_node_ref(name))
    refs.extend(_ev.column_node_refs(col_deletes))
    if not refs:
        return None
    # Partial-coverage guard: never vouch for a subset. If any table/column
    # delete couldn't be turned into a ref, fail closed to the normal gate.
    if len(refs) != len(tbl_deletes) + len(col_deletes):
        return None
    return _ev.compute_delete_evidence(refs, pbip_path=args.get("pbip_path"))


def _autopopulate_batch_delete_evidence(
    object_type: str, batch_op: str, severity, items: list, args: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Compute blast-radius evidence for a CATASTROPHIC batch delete so it can
    be auto-cleared when the graph PROVES it safe (C-02: never from args).

    Measures, columns and tables have graph refs the impact query understands;
    a relationship delete falls through (returns None → normal gate).
    """
    # Policy-blind guard: the gate only reads evidence under LIGHT, so skip the
    # expensive blast-radius computation under OFF/WARN/STRICT.
    from core.autonomous.gating import GATE_POLICY_LIGHT, current_policy, is_confirmed

    if current_policy() != GATE_POLICY_LIGHT:
        return None
    if severity is not Severity.CATASTROPHIC or batch_op != "delete":
        return None
    if is_confirmed(args):
        return None
    from core.autonomous import evidence as _ev

    if object_type == "measures":
        refs = _ev.measure_node_refs(items)
    elif object_type == "columns":
        refs = _ev.column_node_refs(items)
    elif object_type == "tables":
        refs = [_ev.table_node_ref(n) for n in (_table_item_name(i) for i in items) if n]
    else:
        return None
    if not refs:
        return None
    # Partial-coverage guard: if any delete item couldn't be turned into a ref
    # (missing table/name), the evidence would only cover a subset — never vouch
    # for a delete of items we never analyzed. Fail closed to the normal gate.
    if len(refs) != len(items):
        return None
    return _ev.compute_delete_evidence(refs, pbip_path=args.get("pbip_path"))


def _refresh_threshold() -> int:
    try:
        from core.config.config_manager import config

        return int(config.get("model_write.catastrophic_refresh_tables", 10))
    except Exception:  # noqa: BLE001
        return 10


def _batch_severity(object_type: str, batch_op: str, items: list):
    """C-13: classify batch ops by EFFECT.

    CATASTROPHIC: any table delete (drops data + everything on it), >10 deletes
    of anything, every rename batch (each rename cascades through all DAX),
    a table-refresh batch covering >= ``model_write.catastrophic_refresh_tables``
    tables. Everything else that mutates is DESTRUCTIVE.
    """
    if batch_op == "delete" and (object_type == "tables" or len(items) > 10):
        return Severity.CATASTROPHIC
    if batch_op == "rename" and object_type in ("tables", "measures", "columns"):
        return Severity.CATASTROPHIC
    if (
        batch_op == "update"
        and object_type in ("tables", "columns")
        and any(isinstance(i, dict) and i.get("new_name") for i in items)
    ):
        return Severity.CATASTROPHIC
    if batch_op == "refresh" and object_type == "tables" and len(items) >= _refresh_threshold():
        return Severity.CATASTROPHIC
    if batch_op in _MUTATING_BATCH_OPS:
        return Severity.DESTRUCTIVE
    return None


def _changeset_severity(doc: Dict[str, Any]):
    """C-13: a changeset dropping ANY table, or with >10 deletes, is CATASTROPHIC."""
    delete_total = sum(
        len((ops or {}).get("delete", []) or []) for ops in doc.values() if isinstance(ops, dict)
    )
    table_deletes = len(((doc.get("tables") or {}).get("delete")) or [])
    if table_deletes or delete_total > 10:
        return Severity.CATASTROPHIC
    return Severity.DESTRUCTIVE


def _verify_date_column_type(date_ref: str) -> Optional[Dict[str, Any]]:
    """Best-effort live check that `date_ref` is a DATE column.

    DAX time-intelligence functions reject a non-DATE argument at QUERY time
    ("A column specified in the call to function 'SAMEPERIODLASTYEAR' is not of
    type DATE"), long after we have handed the caller a plausible-looking plan.
    Integer surrogates like [Year Month Nr] are the common trap — they read like
    a date grain but are Int64 (hit live on R0201, 2026-08-22).

    Returns an error envelope when the column is definitively the wrong type,
    else None. Fails OPEN: if the model can't be reached or the column can't be
    resolved, generation proceeds (the engine remains the authority).
    """
    try:
        import re as _re

        m = _re.match(r"^'([^']*(?:''[^']*)*)'\[(.+)\]$", date_ref)
        if not m:
            return None
        table, column = m.group(1).replace("''", "'"), m.group(2)

        from core.infrastructure.connection_state import connection_state

        if not connection_state.is_connected():
            return None
        qe = connection_state.query_executor
        if qe is None:
            return None
        # ExplicitDataType 9 == DateTime in the tabular metadata enum.
        from core.dax.dax_names import dax_string

        dax = (
            "EVALUATE SELECTCOLUMNS(FILTER(INFO.COLUMNS(), "
            f'[ExplicitName] = {dax_string(column)}), "dt", [ExplicitDataType])'
        )
        res = qe.validate_and_execute_dax(dax, top_n=25)
        if not res.get("success"):
            return None
        types = set()
        for row in res.get("rows") or []:
            for v in row.values():
                try:
                    types.add(int(v))
                except (TypeError, ValueError):
                    pass
        if not types or 9 in types:
            return None
        return {
            "success": False,
            "error_type": "date_column_not_date_type",
            "error": (
                f"{date_ref} is not a DATE column (metadata data type "
                f"{sorted(types)}). DAX time-intelligence functions require a "
                "true date column — an integer surrogate such as "
                "[Year Month Nr] fails at query time."
            ),
            "hint": (
                "Pass the date table's real DATE column (e.g. "
                f"'{table}'[Date] / [End Date] / [Start Date]). List candidates "
                "with 02_Model_Operations object_type='column' operation='list'."
            ),
        }
    except Exception as e:  # noqa: BLE001 - advisory check must never block
        logger.debug("TI suite date-column type check skipped: %s", e)
        return None


def _resolve_home_tables(names: List[str]) -> Dict[str, Optional[str]]:
    """Home table of each base measure (C-14), via a fresh INFO/DMV read-back.

    Unknown / ambiguous names map to None; when the model cannot be read at all
    every name maps to None.
    """
    from core.operations import model_readback

    rows = model_readback.measure_rows() or []
    homes: Dict[str, Optional[str]] = {}
    for n in names:
        tables = {r["table"] for r in rows if str(r.get("name", "")).lower() == str(n).lower()}
        homes[n] = next(iter(tables)) if len(tables) == 1 else None
    return homes


def _handle_ti_suite(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate (and optionally create) a time-intelligence measure suite.

    Generation is pure and read-only — the plan comes back for review. With
    ``apply=true`` the generated definitions are handed to the ordinary
    ``measures``/``create`` batch path, so the destructive-op gate, transaction
    handling and item caps are the existing ones rather than a parallel
    implementation. C-14: every item carries ``measure_name`` + ``table_name``
    (the base measure's home table when ``target_table`` is absent), and C-12:
    the create never overwrites an existing measure of the same name.
    """
    from core.operations.ti_measure_suite import (
        ALL_VARIANTS,
        TiSuiteError,
        generate_suite,
    )

    base_measures = args.get("base_measures") or args.get("measures") or []
    if isinstance(base_measures, str):
        base_measures = [base_measures]
    if not base_measures:
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": "base_measures is required (list of existing measure names).",
        }

    try:
        plan = generate_suite(
            base_measures,
            date_column=args.get("date_column") or "",
            variants=args.get("variants"),
            target_table=args.get("target_table"),
            display_folder=args.get("display_folder"),
            name_pattern=args.get("name_pattern") or "{base} {suffix}",
            format_string=args.get("format_string"),
        )
    except TiSuiteError as e:
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": str(e),
            "known_variants": ALL_VARIANTS,
        }

    if (type_err := _verify_date_column_type(plan["date_column"])) is not None:
        type_err["known_variants"] = ALL_VARIANTS
        return type_err

    if not args.get("apply"):
        plan.update(
            {
                "success": True,
                "applied": False,
                "hint": (
                    "Preview only — no measures were created. Review the DAX, then "
                    "re-call with apply=true (or pass `measures` to "
                    "03_Batch_Operations object_type=measures, operation_kind=create)."
                ),
            }
        )
        return plan

    if not plan["measures"]:
        return {
            "success": False,
            "error_type": "empty_plan",
            "error": "Nothing to apply: the variant selection produced no measures.",
            "warnings": plan.get("warnings"),
        }

    items = [dict(m) for m in plan["measures"]]
    missing = [m for m in items if not m.get("table_name")]
    if missing:
        homes = _resolve_home_tables(sorted({m["base_measure"] for m in missing}))
        unresolved = sorted(b for b, t in homes.items() if not t)
        if unresolved:
            return {
                "success": False,
                "error_type": "home_table_unresolved",
                "error": (
                    "Could not resolve the home table of base measure(s) "
                    f"{unresolved} — pass target_table."
                ),
            }
        for m in missing:
            m["table_name"] = homes[m["base_measure"]]

    # Delegate to the existing gated batch create — same path, same safety.
    create_args = {
        **{k: v for k, v in args.items() if k not in ("base_measures", "variants", "apply")},
        "object_type": "measures",
        "operation": "measures",
        "batch_operation": "create",
        "items": items,
    }
    result = handle_batch_operations(create_args)
    if isinstance(result, dict):
        result.setdefault(
            "ti_suite",
            {
                "count": plan["count"],
                "variants_used": plan["variants_used"],
                "date_column": plan["date_column"],
            },
        )
        if plan.get("warnings"):
            result.setdefault("warnings", plan["warnings"])
        if plan.get("requires"):
            result.setdefault("requires", plan["requires"])
    return result


def _patch_catastrophic_threshold() -> int:
    from core.analysis.patch_engine import PATCH_CATASTROPHIC_OBJECTS

    try:
        from core.config.config_manager import config

        return int(config.get("model_write.catastrophic_patch_objects", PATCH_CATASTROPHIC_OBJECTS))
    except Exception:  # noqa: BLE001
        return PATCH_CATASTROPHIC_OBJECTS


def _patch_severity(plan: Dict[str, Any]):
    """F-03: a patch is DESTRUCTIVE; CATASTROPHIC when it is wide (more than
    ``model_write.catastrophic_patch_objects`` objects), deletes more than 10, or touches a
    query-semantics / security property (``tom_properties.HIGH_IMPACT``)."""
    if (
        plan.get("high_impact")
        or int(plan.get("object_count") or 0) > _patch_catastrophic_threshold()
        or int(plan.get("delete_count") or 0) > 10
    ):
        return Severity.CATASTROPHIC
    return Severity.DESTRUCTIVE


def _patch_bpa_rules(rule_ids: List[str]):
    """``(analyzer, extra_rules)`` covering the live ruleset + Microsoft's BPARules."""
    from core.analysis.bpa_analyzer import BPAAnalyzer
    from core.infrastructure.connection_state import connection_state as _cs

    live = getattr(_cs, "bpa_analyzer", None) or BPAAnalyzer()
    ids = {r.id for r in getattr(live, "rules", []) or []}
    extra: list = []
    if any(r not in ids for r in rule_ids):
        from server.handlers.analysis_handler import _ruleset_analyzer

        extra = [r for r in _ruleset_analyzer("microsoft").rules if r.id not in ids]
    return live, extra


def _handle_patch(args: Dict[str, Any]) -> Dict[str, Any]:
    """``object_type=patch`` (F-03): select objects, patch properties, or apply BPA fixes.

    ``preview=true`` is read-only (never gated) and returns the per-object before/after + the
    ``plan_hash``; apply re-plans against the open model, refuses a stale/missing hash, passes the
    destructive gate (``_patch_severity``) and writes ONE ``write_lane.commit``.
    """
    from core.analysis.patch_engine import PatchEngine
    from core.validation.error_handler import ErrorHandler
    from core.infrastructure.connection_state import connection_state as _cs

    select = args.get("select")
    patch = args.get("patch")
    from_bpa = args.get("from_bpa")
    if isinstance(from_bpa, str):
        from_bpa = [p.strip() for p in from_bpa.split(",") if p.strip()]
    if from_bpa is not None and not isinstance(from_bpa, list):
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": "from_bpa must be a BPA rule id or a list of rule ids.",
        }
    if from_bpa and (select or patch is not None):
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": "from_bpa replaces select/patch (the rule's FixExpression is the patch); "
            "pass one or the other.",
        }
    if not from_bpa and (not select or patch is None):
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": "object_type=patch needs `select` + `patch` (or `from_bpa=[rule ids]`).",
            "example": {
                "select": 'Measures.Where(Expression.Contains("SUMX") and not IsHidden)',
                "patch": {"DisplayFolder": "{Table.Name}/Iterators"},
                "preview": True,
            },
        }
    if not _cs.is_connected():
        return ErrorHandler.handle_not_connected()

    preview = bool(args.get("preview"))
    analyzer = extra = None
    if from_bpa:
        try:
            analyzer, extra = _patch_bpa_rules(from_bpa)
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "error_type": "rules_load_error",
                "error": f"Could not load the BPA rules: {e}",
            }

    held: Dict[str, Any] = {}

    def _gate(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        severity = _patch_severity(plan)
        evidence = None
        kinds = {e["kind"] for e in plan["entries"] if e["delete"]}
        if plan.get("delete_count") and len(kinds) == 1 and kinds <= {"measure", "column"}:
            kind = next(iter(kinds))
            items = [
                {"table_name": e["table"], "name": e["name"]}
                for e in plan["entries"]
                if e["delete"]
            ]
            evidence = _autopopulate_batch_delete_evidence(
                kind + "s", "delete", severity, items, args
            )
        gate = require_autonomous_mode(
            "03_Batch_Operations:patch/apply", args, severity, evidence=evidence
        )
        if gate is not None and gate.get("success") is False:
            _attach_blast_radius(gate, evidence)
            return gate
        held["gate"] = gate
        return None

    emit_progress(1, 3, "Planning patch" if preview else "Planning + validating patch")
    result = PatchEngine().run(
        select=select,
        patch=patch,
        from_bpa=from_bpa,
        preview=preview,
        plan_hash_arg=args.get("plan_hash"),
        gate=None if preview else _gate,
        bpa_analyzer=analyzer,
        bpa_extra_rules=extra,
    )
    emit_progress(3, 3, "Complete")
    if isinstance(result, dict) and result.get("success") and not preview:
        merge_gate_info(result, held.get("gate"))
    return result


def _preview_requested(args: Dict[str, Any]) -> bool:
    """F-01: top-level ``preview`` / ``dry_run`` (``options.dry_run`` stays the validate-only switch)."""
    from server.op_classification import wants_preview

    return wants_preview(args)


def handle_batch_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle batch operations.

    P2-188: prefer the new `object_type` arg name, with `operation` kept as a
    deprecated alias. Adjacent 02_Model_Operations uses `object_type` + `operation`
    semantically; the previous `operation=<object_type>` here was a naming footgun
    for LLM callers.
    """
    from core.autonomous.gating import is_confirmed
    from core.operations.target_resolution import (
        AmbiguousTarget,
        canonicalize_changeset,
        canonicalize_items,
    )

    object_type = args.get("object_type") or args.get("operation")
    batch_op = args.get("batch_operation", "")
    items = args.get("items", [])
    options = args.get("options") or {}
    if args.get("dry_run") is not None and not args.get("preview") and _preview_requested(args):
        args = {**args, "preview": True}  # F-01: dry_run is the alias every preview path reads
    received_args = args  # F-01: the call as received keys the preview's plan_hash

    notices: Dict[str, Any] = {}
    if args.get("evidence") is not None:
        # C-02: caller-supplied evidence never clears the gate.
        notices["deprecated_args"] = [
            "evidence: ignored — the destructive gate computes its own in-process evidence"
        ]
        args = {k: v for k, v in args.items() if k != "evidence"}

    # Time-intelligence suite generation. Read-only by default: it returns a
    # PLAN of measure definitions (never touches the model), so the caller can
    # review the DAX before committing. `apply=true` funnels the same plan
    # through the normal measures/create batch path so gating, transactions and
    # per-type caps all still apply — no second write path.
    if object_type == "ti_suite" or batch_op == "generate_ti_suite":
        return {**_handle_ti_suite(args), **notices}

    # Select-and-patch / BPA FixExpression apply (F-03). preview is read-only; apply is gated.
    if object_type == "patch" or args.get("from_bpa") is not None:
        return {**_handle_patch(args), **notices}

    # Declarative changeset path — a single multi-object-type delta. preview is
    # read-only (never gated); apply is gated DESTRUCTIVE (CATASTROPHIC when it
    # drops a table or contains >10 deletes) and requires a matching plan_hash.
    if object_type == "changeset" or args.get("changeset") is not None:
        if args.get("preview"):
            return _batch_ops_handler.apply_changeset(args)
        doc = args.get("changeset") or {}
        # C-36: validate BEFORE computing severity (a non-dict used to crash).
        from core.operations.changeset_engine import validate_changeset

        errors = validate_changeset(doc)
        if errors:
            return {
                "success": False,
                "error_type": "invalid_changeset",
                "error": "; ".join(errors[:10]),
                "validation_errors": errors,
            }
        try:
            doc = canonicalize_changeset(doc)
        except AmbiguousTarget as e:
            return e.to_error()
        args = {**args, "changeset": doc}
        severity = _changeset_severity(doc)
        evidence = _autopopulate_changeset_delete_evidence(severity, doc, args)
        gate = require_autonomous_mode(
            "03_Batch_Operations:apply_changeset", args, severity, evidence=evidence
        )
        if gate is not None and gate.get("success") is False:
            _attach_blast_radius(gate, evidence)
            gate.update(notices)
            return gate
        result = _batch_ops_handler.apply_changeset(args)
        merge_gate_info(result, gate)
        if isinstance(result, dict):
            result.update(notices)
        return result

    if not object_type:
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": "object_type is required (or its deprecated alias `operation`).",
        }

    # The classic per-object-type path needs batch_operation + items. These used
    # to be enforced by the tool schema's `required`, but `changeset`/`ti_suite`
    # carry their own inputs, so the requirement moved here — otherwise a
    # missing `items` fell through to a connection attempt and surfaced as
    # `not_connected`, which sends the caller chasing the wrong problem.
    if not batch_op:
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": (
                f"batch_operation is required for object_type={object_type!r} "
                f"(one of {sorted(_MUTATING_BATCH_OPS)})."
            ),
        }
    if not items and object_type == "measures" and args.get("measures"):
        items = args.get("measures")  # deprecated alias of `items` for measures
        notices.setdefault("deprecated_args", []).append("measures: use `items`")
    if not items:
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": (
                f"items is required and must be a non-empty list for "
                f"object_type={object_type!r}, batch_operation={batch_op!r}."
            ),
        }
    # C-19: resolve every item's target ONCE — evidence and executor read the
    # same canonical keys; conflicting aliases are rejected.
    try:
        items = canonicalize_items(object_type, items)
    except AmbiguousTarget as e:
        return e.to_error()
    # Ensure inner BatchOperationsHandler.execute — which dispatches on
    # `args['operation']` — sees the resolved value when the caller used
    # `object_type` instead.
    args = {**args, "items": items}
    if "operation" not in args:
        args["operation"] = object_type

    def _done(result):
        if isinstance(result, dict):
            result.update(notices)
        return result

    # F-01: uniform preview=true (alias dry_run=true) for the classic path: the real batch runs with
    # the save suppressed, then rolls back (never gated, never saved); apply with its plan_hash.
    if _preview_requested(args):
        if err := _check_per_type_cap(object_type, items):
            return err
        from core.operations import write_preview

        exec_args = {k: v for k, v in args.items() if k not in ("preview", "dry_run", "plan_hash")}
        emit_progress(1, 2, f"Previewing {batch_op} on {len(items)} {object_type}")
        previewed = write_preview.run_preview(
            "03_Batch_Operations",
            f"03_Batch_Operations:{object_type}/{batch_op}",
            received_args,
            lambda: _batch_ops_handler.execute(exec_args),
        )
        emit_progress(2, 2, "Preview complete")
        return _done(previewed)
    if args.get("plan_hash"):
        from core.operations import write_preview

        stale = write_preview.check_plan_hash("03_Batch_Operations", received_args)
        if stale is not None:
            return _done(stale)
        args = {k: v for k, v in args.items() if k != "plan_hash"}
        notices["plan_hash_verified"] = True

    # Validation-only path — never trips the gate
    if options.get("dry_run"):
        emit_progress(1, 2, f"Validating {len(items)} {object_type} (dry-run)")
        result = _batch_ops_handler.execute(args)
        emit_progress(2, 2, "Dry-run complete")
        return _done(result)

    if err := _check_per_type_cap(object_type, items):
        return err

    emit_progress(1, 4, f"Validating {len(items)} {object_type}")

    # Blast-radius preflight runs BEFORE the autonomous gate so legitimate
    # confirm_destructive flows keep their existing error_type semantics.
    # C-26: strict boolean — confirm_destructive:"false" no longer passes.
    confirm = is_confirmed(args) or options.get("confirm_destructive") is True
    from core.operations.batch_validation import check_blast_radius

    blast_err = check_blast_radius(batch_op, items, confirm_destructive=confirm)
    if blast_err is not None:
        return blast_err

    # Severity classification (C-13)
    severity = _batch_severity(object_type, batch_op, items)
    if severity is None:
        # Non-mutating or unknown batch_op — just execute
        emit_progress(2, 4, f"Executing {batch_op} on {len(items)} item(s)")
        result = _batch_ops_handler.execute(args)
        emit_progress(4, 4, "Complete")
        return _done(result)

    emit_progress(2, 4, f"Gating check ({severity.name.lower()})")
    op_label = f"03_Batch_Operations:{object_type}/{batch_op}"
    evidence = _autopopulate_batch_delete_evidence(object_type, batch_op, severity, items, args)
    gate = require_autonomous_mode(op_label, args, severity, evidence=evidence)
    if gate is not None and gate.get("success") is False:
        _attach_blast_radius(gate, evidence)
        gate.update(notices)
        return gate

    emit_progress(3, 4, f"Executing {batch_op} on {len(items)} item(s)")
    result = _batch_ops_handler.execute(args)
    merge_gate_info(result, gate)
    emit_progress(4, 4, "Complete")
    return _done(result)


def register_batch_operations_handler(registry):
    """Register batch operations handler"""

    tool = ToolDefinition(
        name="03_Batch_Operations",
        description=(
            "BULK live-model CRUD in ONE SaveChanges: object_type measures | tables | columns "
            "| relationships + batch_operation (create, update, delete, rename, move, "
            "move_display_folder, activate, deactivate, refresh) + items=[...]; "
            "options.use_transaction=true (default) is all-or-nothing. object_type=changeset: "
            "declarative multi-object delta, preview=true -> plan_hash -> apply. "
            "object_type=ti_suite: generate YTD/QTD/MTD/PY/delta measures. Deletes >20 items "
            "need confirm_destructive. One object: 02_Model_Operations; offline TMDL: "
            "02_TMDL_Operations."
        ),
        guide_notes=(
            "BULK / batch create-update-delete-rename of many live-model objects "
            "on ONE connection with ONE SaveChanges (options.use_transaction=true, "
            "the default, is all-or-nothing; use_transaction=false + "
            "continue_on_error=true commits the items that succeed). "
            "object_type: measures | tables | columns | relationships (plural), "
            "batch_operation: create | update | delete | rename | move | "
            "move_display_folder | activate | deactivate | refresh, plus "
            "items=[{...}]. Also `changeset`: a declarative multi-object delta "
            "(tables/columns/relationships — NOT measures) applied in "
            "dependency-safe order; preview=true returns a plan_hash you must "
            "pass back to apply. Deleting more than 20 items requires "
            "confirm_destructive=true. Use this for MANY objects; for a single "
            "object use 02_Model_Operations (same live TOM, singular "
            "object_type); for offline TMDL file edits use 02_TMDL_Operations. "
            "PREVIEW (F-01): preview=true (alias dry_run=true) on the classic path runs the "
            "real batch with the save suppressed and rolls it back: would_change[] (before / "
            "after TMSL), errors[], breaks[] and a plan_hash; apply = the same call + "
            "plan_hash (stale_preview if the model moved); options.dry_run only validates the "
            "item fields. "
            "object_type=patch: select objects with a Dynamic-LINQ expression "
            "(select='Measures.Where(Expression.Contains(\"SUMX\") and not IsHidden)') and "
            "patch allow-listed properties with value templates "
            '(patch={"DisplayFolder": "{Table.Name}/Iterators"}; {"Delete()": true} deletes); '
            "preview=true returns per-object before/after + plan_hash, apply needs that "
            "plan_hash and writes ONE journaled commit; from_bpa=[rule ids] applies the "
            "machine-applicable BPA FixExpressions through the same plan."
        ),
        handler=handle_batch_operations,
        input_schema=TOOL_SCHEMAS["batch_operations"],
        category="batch",
        sort_order=30,  # 03 = Batch & Transactions
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )

    registry.register(tool)
    logger.info("Registered batch_operations handler")
