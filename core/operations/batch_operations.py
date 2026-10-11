"""
Unified batch operations handler
Handles batch create/update/delete operations for all object types.

Every batch runs on ONE AMO connection with ONE SaveChanges
(:func:`run_atomic_batch`) and honours both options (review C-11):

* ``use_transaction`` (default true) — all-or-nothing: the first failing item
  stops the batch and every in-memory change is undone;
* ``continue_on_error`` (only with ``use_transaction=false``) — failing items
  are skipped and the successful rest is committed; without it the batch stops
  at the first failure and commits the successful prefix.
"""

from typing import Dict, Any, List, Optional, Callable
import hashlib
import logging
from .base_operations import BaseOperationsHandler
from core.infrastructure.connection_state import connection_state
from core.operations.amo_helpers import get_server_db_model
from core.operations.batch_validation import check_blast_radius
from core.operations.write_lane import commit, get_write_lane, safe_undo
from core.validation.error_handler import ErrorHandler
from core.operations.changeset_engine import (
    validate_changeset,
    order_operations,
    count_operations,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Changeset staleness hash (C-05)
# ---------------------------------------------------------------------------


def _iter(coll) -> list:
    try:
        return list(coll or [])
    except TypeError:
        return []


def compute_plan_hash(model: Any) -> str:
    """SHA256 over the TOM model state a changeset can touch.

    C-05: the changeset path used ``core.codemods.plan_hash.compute_plan_hash``,
    which reads the lower-case ``build_live_model()`` facade (``model.tables``)
    — on a real TOM model (``model.Tables``) every lookup hit its default and
    the hash was always ``sha256("")``, so the staleness gate never fired. This
    reads the PascalCase TOM shape (the facade shape is accepted too) and
    raises when it finds no tables at all: a connected model always has tables,
    so zero means the read failed and a constant hash must not be issued.
    """
    tables = _iter(getattr(model, "Tables", None)) or _iter(getattr(model, "tables", None))
    if not tables:
        raise ValueError("plan hash: the model exposes no tables (read failed)")

    def g(obj, *names, default=""):
        for n in names:
            v = getattr(obj, n, None)
            if v is not None:
                return v
        return default

    parts: List[str] = []
    for tbl in tables:
        tname = str(g(tbl, "Name", "name"))
        parts.append(f"tbl\t{tname}\t{g(tbl, 'IsHidden', 'is_hidden', default=False)}")
        for part in _iter(g(tbl, "Partitions", default=None)):
            src = g(part, "Source", default=None)
            parts.append(f"part\t{tname}\t{g(part, 'Name')}\t{g(src, 'Expression')}")
        for col in _iter(g(tbl, "Columns", "columns", default=None)):
            parts.append(
                "col\t"
                + "\t".join(
                    str(x)
                    for x in (
                        tname,
                        g(col, "Name", "name"),
                        g(col, "DataType", "data_type"),
                        g(col, "Expression", "expression"),
                        g(col, "IsHidden", "is_hidden", default=False),
                    )
                )
            )
        for m in _iter(g(tbl, "Measures", "measures", default=None)):
            parts.append(
                f"meas\t{tname}\t{g(m, 'Name', 'name')}\t{g(m, 'Expression', 'expression')}"
            )
    for rel in _iter(getattr(model, "Relationships", None)) or _iter(
        getattr(model, "relationships", None)
    ):
        parts.append(
            "rel\t"
            + "\t".join(
                str(x)
                for x in (
                    g(rel, "Name", "name"),
                    g(g(rel, "FromTable", default=None), "Name", default=g(rel, "from_table")),
                    g(g(rel, "FromColumn", default=None), "Name", default=g(rel, "from_column")),
                    g(g(rel, "ToTable", default=None), "Name", default=g(rel, "to_table")),
                    g(g(rel, "ToColumn", default=None), "Name", default=g(rel, "to_column")),
                    g(rel, "IsActive", "is_active", default=True),
                    g(rel, "CrossFilteringBehavior", default=""),
                )
            )
        )
    body = "\n".join(sorted(parts))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Atomic batch engine
# ---------------------------------------------------------------------------


def _truthy_option(options: Dict[str, Any], key: str, default: bool) -> bool:
    value = options.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _cancel_check() -> None:
    try:
        from server.request_context import raise_if_cancelled
    except Exception:  # noqa: BLE001 - core must not hard-depend on server
        return
    raise_if_cancelled()


def run_atomic_batch(
    items: List[Any],
    options: Dict[str, Any],
    dispatch_fn: Callable[[Any, Any, list], Dict[str, Any]],
    *,
    label: Optional[str] = None,
    skip_save_when: Optional[Callable[[Optional[str]], bool]] = None,
    verify_rename: bool = False,
) -> Dict[str, Any]:
    """One connection, one SaveChanges; ``dispatch_fn(model, item, record)``.

    ``record`` is shared by every item: rename primitives append their
    restorable site records to it, so a rename batch (``verify_rename``) is
    verified after the save and rolled back exactly on new expression errors.
    """
    options = options or {}
    use_transaction = _truthy_option(options, "use_transaction", True)
    continue_on_error = _truthy_option(options, "continue_on_error", False)
    operation = options.get("_operation")
    label = label or f"batch/{operation}"

    server, _db, model, conn_err = get_server_db_model(connection_state)
    if conn_err:
        return {"success": False, "error": conn_err, "error_type": "connection_error"}

    notices: List[str] = []
    if use_transaction and continue_on_error:
        notices.append(
            "continue_on_error is ignored with use_transaction=true (all-or-nothing); "
            "pass use_transaction=false to commit the items that succeed."
        )

    from core.operations import rename_flow

    baseline = rename_flow.snapshot_errors() if verify_rename else None
    record: List[Dict[str, Any]] = []
    results, errors = [], []
    commit_info: Dict[str, Any] = {}
    rename_failure: Optional[Dict[str, Any]] = None
    rollback_failed = False
    committed = False
    try:
        for idx, item in enumerate(items):
            _cancel_check()
            try:
                r = dispatch_fn(model, item, record)
            except Exception as e:  # noqa: BLE001 - per-item failure
                r = {"success": False, "error": str(e), "item": item}
            if not isinstance(r, dict):
                r = {"success": bool(r)}
            r.setdefault("index", idx)
            results.append(r)
            if not r.get("success"):
                errors.append(r)
                if use_transaction or not continue_on_error:
                    break

        if errors and use_transaction:
            try:
                safe_undo(model)
            except Exception as undo_err:
                # The rollback itself failed — the model may be left in a
                # partially-mutated state. Report this truthfully rather
                # than claiming rolled_back=True below.
                rollback_failed = True
                logger.error(f"Batch rollback (UndoLocalChanges) failed: {undo_err}")
        elif skip_save_when and skip_save_when(operation):
            committed = True  # the dispatcher persisted each item itself
        elif any(r.get("success") for r in results):
            # If SaveChanges itself throws (and the read-back cannot prove the
            # change landed), the in-memory writes are rolled back (P0-2).
            try:
                if verify_rename:
                    outcome = rename_flow.finish(
                        model, label=label, baseline=baseline, record=record
                    )
                    if outcome.get("success") is False:
                        rename_failure = outcome
                    else:
                        commit_info = outcome
                else:
                    commit_info = commit(model, label=label)
                committed = rename_failure is None
            except Exception:
                try:
                    safe_undo(model)
                except Exception:
                    pass
                raise
    finally:
        try:
            server.Disconnect()
        except Exception:
            pass

    attempted_rollback = bool(errors and use_transaction)
    result: Dict[str, Any] = {
        "success": len(errors) == 0 and rename_failure is None,
        "operation": operation,
        "total": len(items),
        "succeeded": len([r for r in results if r.get("success")]) if committed else 0,
        "failed": len(errors),
        "not_attempted": len(items) - len(results),
        "rolled_back": (attempted_rollback and not rollback_failed)
        or bool(rename_failure and rename_failure.get("rolled_back")),
        "rollback_failed": rollback_failed,
        "committed": committed,
        "results": results,
        "errors": errors or None,
    }
    if rename_failure is not None:
        result.update(
            {
                k: rename_failure[k]
                for k in ("error", "error_type", "new_errors", "rollback")
                if k in rename_failure
            }
        )
    result.update({k: v for k, v in commit_info.items() if k != "success"})
    if committed:
        health = get_write_lane().check_health()
        if isinstance(health, dict) and health.get("success") is False:
            result["post_write_health"] = health
            result["warning"] = (
                "Power BI Desktop did not answer the post-write health probe — wait for "
                "it to settle before issuing more writes."
            )
    if notices:
        result["notices"] = notices
    return result


# P2-34: declarative required-field spec for batch-measures dry-run. Each
# operation lists the aliases for "name", aliases for "table", and any
# additional required fields. The validator below iterates this uniformly
# so a new operation just adds a spec entry instead of a new elif block.
_NAME_ALIASES = ("measure", "measure_name", "name")
_TABLE_ALIASES = ("table", "table_name")
_BATCH_MEASURE_SPEC: Dict[str, Dict[str, Any]] = {
    "create": {"needs_name": True, "needs_table": True, "extra_required": ("expression",)},
    "update": {"needs_name": True, "needs_table": True, "extra_required": ()},
    "delete": {"needs_name": True, "needs_table": True, "extra_required": ()},
    "rename": {"needs_name": True, "needs_table": True, "extra_required": ("new_name",)},
    "move_display_folder": {
        "needs_name": True,
        "needs_table": True,
        "extra_required": ("display_folder",),
    },
    # `move` accepts either `new_table` OR `target_table` — handled specially below.
    "move": {"needs_name": True, "needs_table": True, "extra_required": ()},
}

# C-36: required fields for the other object types' dry-run AND changeset items.
_OBJECT_SPEC: Dict[str, Dict[str, tuple]] = {
    "tables": {
        "create": (("table_name", "name", "table"),),
        "update": (("table_name", "name", "table"),),
        "delete": (("table_name", "name", "table"),),
        "rename": (("table_name", "name", "table"), ("new_name",)),
        "refresh": (("table_name", "name", "table"),),
    },
    "columns": {
        "create": (("table_name", "table"), ("column_name", "name", "column")),
        "update": (("table_name", "table"), ("column_name", "name", "column")),
        "delete": (("table_name", "table"), ("column_name", "name", "column")),
        "rename": (("table_name", "table"), ("column_name", "name", "column"), ("new_name",)),
        "move_display_folder": (
            ("table_name", "table"),
            ("column_name", "name", "column"),
            ("display_folder",),
        ),
    },
    "relationships": {
        "create": (("from_table",), ("from_column",), ("to_table",), ("to_column",)),
        "update": (("relationship_name", "name"),),
        "delete": (("relationship_name", "name"),),
        "activate": (("relationship_name", "name"),),
        "deactivate": (("relationship_name", "name"),),
        "rename": (("relationship_name", "name"), ("new_name",)),
    },
}

_REL_CREATE_KEYS = (
    "from_table",
    "from_column",
    "to_table",
    "to_column",
    "name",
    "from_cardinality",
    "to_cardinality",
    "cross_filtering_behavior",
    "is_active",
)
_REL_UPDATE_KEYS = (
    "cross_filtering_behavior",
    "is_active",
    "new_name",
    "from_cardinality",
    "to_cardinality",
    "security_filtering_behavior",
    "rely_on_referential_integrity",
    "join_on_date_behavior",
)


def validate_object_items(object_type: str, operation: str, items: List[Any]) -> List[Dict]:
    """Per-item missing-field errors for tables/columns/relationships (C-36)."""
    spec = (_OBJECT_SPEC.get(object_type) or {}).get(operation)
    errors: List[Dict[str, Any]] = []
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            errors.append({"index": idx, "errors": ["item must be an object"]})
            continue
        if spec is None:
            errors.append(
                {"index": idx, "errors": [f"unsupported {object_type} operation: {operation!r}"]}
            )
            continue
        missing = [
            "/".join(aliases)
            for aliases in spec
            if not any(item.get(k) not in (None, "") for k in aliases)
        ]
        if missing:
            errors.append(
                {"index": idx, "errors": [f"missing required field: {m}" for m in missing]}
            )
    return errors


class BatchOperationsHandler(BaseOperationsHandler):
    """Handles batch operations for model objects"""

    def __init__(self):
        super().__init__("batch_operations")

        # Register batch operations by object type
        self.register_operation("measures", self._batch_measures)
        self.register_operation("tables", self._batch_tables)
        self.register_operation("columns", self._batch_columns)
        self.register_operation("relationships", self._batch_relationships)

    def _validate_batch_items(
        self, operation: str, items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """P2-34: unified dry-run validator driven by `_BATCH_MEASURE_SPEC`.

        Returns a list of {index, item, errors} dicts for items that are missing
        one or more required fields. Unknown operations produce one "unknown op"
        error per item so the dry-run surfaces the typo cleanly.
        """
        errors: List[Dict[str, Any]] = []
        spec = _BATCH_MEASURE_SPEC.get(operation)
        for idx, item in enumerate(items):
            item_errors: List[str] = []
            name_value = next((item.get(k) for k in _NAME_ALIASES if item.get(k)), None)
            display_name = name_value or f"item[{idx}]"
            has_table = any(item.get(k) for k in _TABLE_ALIASES)

            if spec is None:
                item_errors.append(f"unknown batch operation: {operation!r}")
            else:
                if spec["needs_name"] and not name_value:
                    item_errors.append("missing required field: name/measure/measure_name")
                if spec["needs_table"] and not has_table:
                    item_errors.append("missing required field: table or table_name")
                for required in spec["extra_required"]:
                    if required == "expression" and item.get("dax_expression"):
                        continue
                    if not item.get(required):
                        item_errors.append(f"missing required field: {required}")
                if (
                    operation == "move"
                    and not item.get("new_table")
                    and not item.get("target_table")
                ):
                    item_errors.append("missing required field: new_table or target_table")

            if item_errors:
                errors.append({"index": idx, "item": display_name, "errors": item_errors})
        return errors

    def _apply_blast_check(
        self, batch_op: str, items: list, args: dict
    ) -> Optional[Dict[str, Any]]:
        """Delegate to check_blast_radius (strict-bool confirmation, C-26)."""
        from core.autonomous.gating import is_confirmed

        options = args.get("options", {}) or {}
        confirm = is_confirmed(args) or options.get("confirm_destructive") is True
        return check_blast_radius(batch_op, items, confirm_destructive=confirm)

    @staticmethod
    def _dry_run_result(object_type: str, operation: str, items: list) -> Dict[str, Any]:
        errors = validate_object_items(object_type, operation, items)
        if errors:
            return {
                "success": False,
                "dry_run": True,
                "message": f"Validation failed for {len(errors)} of {len(items)} item(s)",
                "item_count": len(items),
                "validation_errors": errors,
            }
        return {
            "success": True,
            "dry_run": True,
            "message": f"Validated {len(items)} {object_type} for {operation} operation",
            "item_count": len(items),
            "note": "Field-level validation only; name/existence checks run on apply.",
        }

    def _batch_measures(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Batch operations for measures (one connection, one SaveChanges)."""
        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()

        operation = args.get("batch_operation")  # create, update, delete, etc.
        items = args.get("items", []) or args.get("measures", [])
        options = dict(args.get("options") or {})

        if not operation:
            return {
                "success": False,
                "error": "batch_operation parameter is required (create, update, delete, etc.)",
            }

        if not items:
            return {
                "success": False,
                "error": "items parameter is required (array of measure definitions)",
            }

        blast_err = self._apply_blast_check(operation, items, args)
        if blast_err is not None:
            return blast_err

        if options.get("dry_run", False):
            # P2-34: drive dry-run validation from a declarative spec.
            validation_errors = self._validate_batch_items(operation, items)

            if validation_errors:
                return {
                    "success": False,
                    "dry_run": True,
                    "message": f"Validation failed for {len(validation_errors)} of {len(items)} item(s)",
                    "item_count": len(items),
                    "validation_errors": validation_errors,
                }

            return {
                "success": True,
                "dry_run": True,
                "message": f"Validated {len(items)} measure(s) for {operation} operation",
                "item_count": len(items),
                "would_execute": f"{operation} on {len(items)} measures",
            }

        measure_crud = connection_state.measure_crud_manager
        if not measure_crud:
            return ErrorHandler.handle_manager_unavailable("measure_crud_manager")

        def _t(item):
            return item.get("table_name") or item.get("table")

        def _m(item):
            return item.get("measure_name") or item.get("measure") or item.get("name")

        options["_operation"] = operation

        if operation in ("create", "update"):
            # C-12: create never overwrites unless if_exists='update' (upsert).
            if_exists = options.get("if_exists") or args.get("if_exists")
            mode = (
                "update"
                if operation == "update"
                else ("upsert" if if_exists == "update" else "create")
            )

            def dispatch_fn(model, item, _record):
                return measure_crud._apply_upsert_measure(
                    model,
                    table_name=_t(item),
                    measure_name=_m(item),
                    expression=item.get("expression") or item.get("dax_expression"),
                    description=item.get("description"),
                    format_string=item.get("format_string"),
                    format_string_expression=item.get("format_string_expression"),
                    display_folder=item.get("display_folder"),
                    data_category=item.get("data_category"),
                    hidden=item.get("hidden"),
                    mode=mode,
                )

            return run_atomic_batch(items, options, dispatch_fn, label=f"measures/{operation}")

        if operation == "delete":

            def dispatch_fn(model, item, _record):
                return measure_crud._apply_delete_measure(
                    model, table_name=_t(item), measure_name=_m(item)
                )

            return run_atomic_batch(items, options, dispatch_fn, label="measures/delete")

        if operation == "rename":
            # Single connection, all cascades see each other's changes, one
            # SaveChanges, verified + rolled back on new errors (C-32).
            return measure_crud.batch_rename_measures(items, options)

        if operation == "move_display_folder":
            # P1: single AMO connection for the whole batch via run_atomic_batch
            # + a no-connect _apply_set_display_folder primitive.
            def dispatch_fn(model, item, _record):
                return measure_crud._apply_set_display_folder(
                    model,
                    table_name=_t(item),
                    measure_name=_m(item),
                    display_folder=item.get("display_folder", ""),
                )

            return run_atomic_batch(
                items, options, dispatch_fn, label="measures/move_display_folder"
            )

        if operation == "move":

            def dispatch_fn(model, item, _record):
                return measure_crud._apply_move_measure(
                    model,
                    source_table=_t(item),
                    measure_name=_m(item),
                    target_table=item.get("new_table") or item.get("target_table"),
                )

            return run_atomic_batch(items, options, dispatch_fn, label="measures/move")

        return {
            "success": False,
            "error": f"Unknown batch operation: {operation}",
            "supported_operations": [
                "create",
                "update",
                "delete",
                "rename",
                "move",
                "move_display_folder",
            ],
        }

    def _run_atomic_batch(self, items, options, dispatch_fn, *, skip_save_when=None):
        """Backward-compatible wrapper: ``dispatch_fn(model, item)`` (2 args).

        See :func:`run_atomic_batch` for the semantics.
        """
        return run_atomic_batch(
            items,
            options,
            lambda model, item, _record: dispatch_fn(model, item),
            skip_save_when=skip_save_when,
        )

    # ------------------------------------------------------------------
    # Declarative changeset (apply_changeset) — multi-object atomic delta
    # ------------------------------------------------------------------

    def _changeset_apply_one(
        self, model, object_type: str, op: str, item: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply ONE changeset op against the shared `model` (no save/connect).

        Routes (object_type, op) to the matching manager's no-connect _apply_*
        helper. Only tables/columns/relationships are supported (measures use
        the batch path — see changeset_engine docstring)."""

        def _name(*keys):
            for k in keys:
                v = item.get(k)
                if v:
                    return v
            return None

        if object_type == "tables":
            tc = connection_state.table_crud_manager
            if op == "create":
                return tc._apply_create_table(
                    model,
                    table_name=_name("table", "table_name", "name"),
                    description=item.get("description"),
                    expression=item.get("expression"),
                    hidden=item.get("hidden", False),
                    m_expression=item.get("m_expression"),
                )
            if op == "update":
                return tc._apply_update_table(
                    model,
                    table_name=_name("table", "table_name", "name"),
                    description=item.get("description"),
                    expression=item.get("expression"),
                    hidden=item.get("hidden"),
                    new_name=item.get("new_name"),
                )
            if op == "delete":
                return tc._apply_delete_table(
                    model, table_name=_name("table", "table_name", "name")
                )

        elif object_type == "columns":
            cc = connection_state.column_crud_manager
            # C-36: the changeset carries the same column properties as the
            # classic batch path (description / hidden / folder / format / source).
            if op == "create":
                return cc._apply_create_column(
                    model,
                    table_name=_name("table", "table_name"),
                    column_name=_name("name", "column", "column_name"),
                    expression=item.get("expression"),
                    data_type=item.get("data_type"),
                    description=item.get("description"),
                    hidden=item.get("hidden", False),
                    display_folder=item.get("display_folder"),
                    format_string=item.get("format_string"),
                    source_column=item.get("source_column"),
                )
            if op == "update":
                return cc._apply_update_column(
                    model,
                    table_name=_name("table", "table_name"),
                    column_name=_name("name", "column", "column_name"),
                    expression=item.get("expression"),
                    data_type=item.get("data_type"),
                    new_name=item.get("new_name"),
                    description=item.get("description"),
                    hidden=item.get("hidden"),
                    display_folder=item.get("display_folder"),
                    format_string=item.get("format_string"),
                )
            if op == "delete":
                return cc._apply_delete_column(
                    model,
                    table_name=_name("table", "table_name"),
                    column_name=_name("name", "column", "column_name"),
                )

        elif object_type == "relationships":
            rc = connection_state.relationship_crud_manager
            if op == "create":
                # C-36: forward only known keys (unknown ones raised TypeError).
                return rc._apply_create_relationship(
                    model, **{k: item[k] for k in _REL_CREATE_KEYS if k in item}
                )
            if op == "update":
                return rc._apply_update_relationship(
                    model,
                    relationship_name=_name("name", "relationship_name"),
                    **{k: item[k] for k in _REL_UPDATE_KEYS if k in item},
                )
            if op == "delete":
                return rc._apply_delete_relationship(
                    model,
                    relationship_name=_name("name", "relationship_name"),
                )

        return {"success": False, "error": f"unsupported {object_type}/{op}"}

    def apply_changeset(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """preview or apply a declarative multi-object changeset.

        preview=True: validate + compute plan_hash + summarize op counts/order.
        apply (default): require a matching plan_hash (reject stale), then apply
        the dependency-ordered ops against the model in ONE transaction
        (SaveChanges once; UndoLocalChanges on any failure).
        """
        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()

        doc = args.get("changeset")
        errors = validate_changeset(doc)
        if not errors:
            for ot, ops in doc.items():
                for op_kind, items in (ops or {}).items():
                    for e in validate_object_items(ot, op_kind, items):
                        errors.append(f"{ot}.{op_kind}[{e['index']}]: {'; '.join(e['errors'])}")
        if errors:
            return {
                "success": False,
                "error_type": "invalid_changeset",
                "error": "; ".join(errors[:10]),
                "validation_errors": errors,
            }

        is_preview = bool(args.get("preview"))

        server, _db, model, conn_err = get_server_db_model(connection_state)
        if conn_err:
            return {"success": False, "error": conn_err, "error_type": "connection_error"}

        try:
            try:
                current_hash = compute_plan_hash(model)
            except ValueError as e:
                return {
                    "success": False,
                    "error_type": "plan_hash_unavailable",
                    "error": f"Could not fingerprint the model for the staleness gate: {e}",
                }

            if is_preview:
                ordered = order_operations(doc)
                return {
                    "success": True,
                    "preview": True,
                    "plan_hash": current_hash,
                    "operations": count_operations(doc),
                    "ordered": [{"object_type": ot, "op": op} for ot, op, _item in ordered],
                }

            # apply path — staleness gate
            supplied_hash = args.get("plan_hash")
            if not supplied_hash:
                return {
                    "success": False,
                    "error_type": "plan_hash_required",
                    "error": "plan_hash is required on apply. Call with preview=true first and pass back its plan_hash.",
                }
            if supplied_hash != current_hash:
                return {
                    "success": False,
                    "error_type": "stale_plan",
                    "error": "Model changed since preview (plan_hash mismatch). Re-run preview and retry.",
                    "expected": supplied_hash,
                    "current": current_hash,
                }

            ordered = order_operations(doc)
            applied = 0
            failure = None
            for object_type, op, item in ordered:
                try:
                    res = self._changeset_apply_one(model, object_type, op, item)
                except Exception as e:  # noqa: BLE001 — surface as a failed op
                    res = {"success": False, "error": str(e)}
                if not res.get("success"):
                    failure = {"object_type": object_type, "op": op, "item": item, "result": res}
                    break
                applied += 1

            if failure is not None:
                try:
                    safe_undo(model)
                    rolled_back = True
                except Exception:
                    rolled_back = False
                return {
                    "success": False,
                    "error_type": "changeset_apply_failed",
                    "error": f"Op {failure['object_type']}/{failure['op']} failed; all changes rolled back.",
                    "applied_before_failure": applied,
                    "failure": failure,
                    "rolled_back": rolled_back,
                }

            try:
                commit_info = commit(model, label="changeset/apply")
            except Exception as e:
                try:
                    safe_undo(model)
                except Exception:
                    pass
                return {
                    "success": False,
                    "error_type": "save_failed",
                    "error": str(e),
                    "rolled_back": True,
                }

            try:
                new_hash = compute_plan_hash(model)
            except ValueError:
                new_hash = None
            result = {
                "success": True,
                "applied": applied,
                "operations": count_operations(doc),
                "new_plan_hash": new_hash,
            }
            result.update(commit_info)
            health = get_write_lane().check_health()
            if isinstance(health, dict) and health.get("success") is False:
                result["post_write_health"] = health
            return result
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def _batch_tables(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Batch operations for tables"""
        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()

        table_crud = connection_state.table_crud_manager
        if not table_crud:
            return ErrorHandler.handle_manager_unavailable("table_crud_manager")

        operation = args.get("batch_operation")
        items = args.get("items", [])
        options = dict(args.get("options") or {})

        if not operation:
            return {"success": False, "error": "batch_operation parameter is required"}

        if not items:
            return {"success": False, "error": "items parameter is required"}

        blast_err = self._apply_blast_check(operation, items, args)
        if blast_err is not None:
            return blast_err

        if options.get("dry_run", False):
            return self._dry_run_result("tables", operation, items)

        def _tn(item):
            return item.get("table_name") or item.get("name") or item.get("table")

        # H1/H2: acquire AMO model ONCE, apply all items via run_atomic_batch,
        # SaveChanges ONCE (or UndoLocalChanges on failure under use_transaction).
        def dispatch_fn(model, item, record):
            if operation == "create":
                return table_crud._apply_create_table(
                    model,
                    table_name=_tn(item),
                    description=item.get("description"),
                    expression=item.get("expression"),
                    hidden=item.get("hidden", False),
                    m_expression=item.get("m_expression"),
                )
            elif operation == "update":
                return table_crud._apply_update_table(
                    model,
                    table_name=_tn(item),
                    description=item.get("description"),
                    expression=item.get("expression"),
                    hidden=item.get("hidden"),
                    new_name=item.get("new_name"),
                    record=record,
                )
            elif operation == "delete":
                return table_crud._apply_delete_table(model, table_name=_tn(item))
            elif operation == "rename":
                return table_crud._apply_rename_table(
                    model, table_name=_tn(item), new_name=item.get("new_name"), record=record
                )
            elif operation == "refresh":
                # C-23: request every refresh on the SHARED model and save once
                # (was: one connection + SaveChanges per item, refresh_type ignored).
                return self._apply_refresh_table(
                    model, _tn(item), item.get("refresh_type") or options.get("refresh_type")
                )
            else:
                return {"success": False, "error": f"Unknown operation: {operation}"}

        options["_operation"] = operation
        return run_atomic_batch(
            items,
            options,
            dispatch_fn,
            label=f"tables/{operation}",
            verify_rename=operation in ("rename", "update")
            and any(i.get("new_name") for i in items if isinstance(i, dict)),
        )

    @staticmethod
    def _apply_refresh_table(model, table_name: Optional[str], refresh_type: Optional[str]):
        from core.operations.model_refresh_manager import _resolve_refresh_type

        table = next((t for t in model.Tables if t.Name == table_name), None)
        if table is None:
            return {
                "success": False,
                "error": f"Table '{table_name}' not found",
                "error_type": "table_not_found",
            }
        rtype, err = _resolve_refresh_type(refresh_type or "full")
        if err:
            return {"success": False, "error": err, "error_type": "invalid_parameters"}
        table.RequestRefresh(rtype)
        return {
            "success": True,
            "action": "refresh_requested",
            "table": table_name,
            "refresh_type": refresh_type or "full",
        }

    def _batch_columns(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Batch operations for columns"""
        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()

        column_crud = connection_state.column_crud_manager
        if not column_crud:
            return ErrorHandler.handle_manager_unavailable("column_crud_manager")

        operation = args.get("batch_operation")
        items = args.get("items", [])
        options = dict(args.get("options") or {})

        if not operation:
            return {"success": False, "error": "batch_operation parameter is required"}

        if not items:
            return {"success": False, "error": "items parameter is required"}

        blast_err = self._apply_blast_check(operation, items, args)
        if blast_err is not None:
            return blast_err

        if options.get("dry_run", False):
            return self._dry_run_result("columns", operation, items)

        def _tn(item):
            return item.get("table_name") or item.get("table")

        def _cn(item):
            return item.get("column_name") or item.get("name") or item.get("column")

        # H1/H2: acquire AMO model ONCE, apply all items via run_atomic_batch,
        # SaveChanges ONCE (or UndoLocalChanges on failure under use_transaction).
        def dispatch_fn(model, item, record):
            if operation == "create":
                return column_crud._apply_create_column(
                    model,
                    table_name=_tn(item),
                    column_name=_cn(item),
                    data_type=item.get("data_type"),
                    expression=item.get("expression"),
                    description=item.get("description"),
                    hidden=item.get("hidden", False),
                    display_folder=item.get("display_folder"),
                    format_string=item.get("format_string"),
                    source_column=item.get("source_column"),
                )
            elif operation == "update":
                return column_crud._apply_update_column(
                    model,
                    table_name=_tn(item),
                    column_name=_cn(item),
                    expression=item.get("expression"),
                    description=item.get("description"),
                    hidden=item.get("hidden"),
                    display_folder=item.get("display_folder"),
                    format_string=item.get("format_string"),
                    new_name=item.get("new_name"),
                    data_type=item.get("data_type"),
                    record=record,
                )
            elif operation == "delete":
                return column_crud._apply_delete_column(
                    model, table_name=_tn(item), column_name=_cn(item)
                )
            elif operation == "rename":
                return column_crud._apply_rename_column(
                    model,
                    table_name=_tn(item),
                    column_name=_cn(item),
                    new_name=item.get("new_name"),
                    record=record,
                )
            elif operation == "move_display_folder":
                return column_crud._apply_update_column(
                    model,
                    table_name=_tn(item),
                    column_name=_cn(item),
                    display_folder=item.get("display_folder", ""),
                )
            else:
                return {"success": False, "error": f"Unknown operation: {operation}"}

        options["_operation"] = operation
        return run_atomic_batch(
            items,
            options,
            dispatch_fn,
            label=f"columns/{operation}",
            verify_rename=operation in ("rename", "update")
            and any(i.get("new_name") for i in items if isinstance(i, dict)),
        )

    def _batch_relationships(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Batch operations for relationships"""
        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()

        rel_crud = connection_state.relationship_crud_manager
        if not rel_crud:
            return ErrorHandler.handle_manager_unavailable("relationship_crud_manager")

        operation = args.get("batch_operation")
        items = args.get("items", [])
        options = dict(args.get("options") or {})

        if not operation:
            return {"success": False, "error": "batch_operation parameter is required"}

        if not items:
            return {"success": False, "error": "items parameter is required"}

        blast_err = self._apply_blast_check(operation, items, args)
        if blast_err is not None:
            return blast_err

        if options.get("dry_run", False):
            return self._dry_run_result("relationships", operation, items)

        def _rn(item):
            return item.get("relationship_name") or item.get("name")

        # H1/H2: acquire AMO model ONCE, apply all items via run_atomic_batch,
        # SaveChanges ONCE (or UndoLocalChanges on failure under use_transaction).
        def dispatch_fn(model, item, _record):
            if operation == "create":
                return rel_crud._apply_create_relationship(
                    model,
                    from_table=item.get("from_table"),
                    from_column=item.get("from_column"),
                    to_table=item.get("to_table"),
                    to_column=item.get("to_column"),
                    name=item.get("name"),
                    from_cardinality=item.get("from_cardinality", "Many"),
                    to_cardinality=item.get("to_cardinality", "One"),
                    cross_filtering_behavior=item.get("cross_filtering_behavior", "OneDirection"),
                    is_active=item.get("is_active", True),
                )
            elif operation == "update":
                return rel_crud._apply_update_relationship(
                    model,
                    relationship_name=_rn(item),
                    **{k: item[k] for k in _REL_UPDATE_KEYS if k in item},
                )
            elif operation == "delete":
                return rel_crud._apply_delete_relationship(model, relationship_name=_rn(item))
            elif operation == "activate":
                return rel_crud._apply_activate_relationship(model, relationship_name=_rn(item))
            elif operation == "deactivate":
                return rel_crud._apply_deactivate_relationship(model, relationship_name=_rn(item))
            elif operation == "rename":
                return rel_crud._apply_rename_relationship(
                    model,
                    relationship_name=_rn(item),
                    new_name=item.get("new_name"),
                )
            else:
                return {"success": False, "error": f"Unknown operation: {operation}"}

        options["_operation"] = operation
        return run_atomic_batch(items, options, dispatch_fn, label=f"relationships/{operation}")
