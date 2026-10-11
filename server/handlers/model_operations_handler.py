"""
Model Operations Handler
Unified CRUD for tables, columns, measures, relationships, calculation groups,
partitions, hierarchies, perspectives, cultures, named expressions, and OLS rules.
Replaces 5 separate handlers with a single object_type + operation dispatcher.
"""

from typing import Dict, Any, Optional
import logging
from server.registry import ToolDefinition
from core.operations.table_operations import TableOperationsHandler
from core.operations.column_operations import ColumnOperationsHandler
from core.operations.measure_operations import MeasureOperationsHandler
from core.operations.relationship_operations import RelationshipOperationsHandler
from core.operations.calculation_group_operations import CalculationGroupOperationsHandler
from core.operations.partition_crud_manager import PartitionCrudManager
from core.operations.hierarchy_crud_manager import HierarchyCrudManager
from core.operations.perspective_crud_manager import PerspectiveCrudManager
from core.operations.culture_crud_manager import CultureCrudManager
from core.operations.named_expression_crud_manager import NamedExpressionCrudManager
from core.operations.ols_crud_manager import OlsCrudManager
from core.operations.model_refresh_manager import ModelRefreshManager
from core.operations.model_properties_manager import ModelPropertiesManager
from core.operations.rls_role_crud_manager import RlsRoleCrudManager
from core.operations.annotation_manager import AnnotationManager
from core.operations.field_parameter_manager import FieldParameterManager
from core.operations.function_crud_manager import FunctionCrudManager
from core.operations.query_group_crud_manager import QueryGroupCrudManager
from core.operations.calendar_crud_manager import CalendarCrudManager
from core.operations.tom_properties import TomPropertyManager, is_high_impact
from core.operations.journal_manager import WriteJournalManager
from core.autonomous.gating import require_autonomous_mode, Severity, merge_gate_info
from server.tool_schemas import TOOL_SCHEMAS
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

_READ_ONLY_OPS = {
    "list",
    "describe",
    "get",
    "find",
    "sample_data",
    "statistics",
    "distribution",
    "list_items",
    "list_members",
    "get_kpi",
    "get_refresh_policy",
    "get_properties",
    # F-04: TOM property reflection reads; F-20d: translation export / coverage only read the model.
    "list_properties",
    "get_property",
    "export_translations",
    "translation_coverage",
    # MP-01 / RF-01: static reads of role definitions and refresh state.
    "get_effective_permissions",
    "refresh_status",
    "refresh_history",
    # JR-01: journal reads (checkpoint_create only writes a snapshot FILE).
    "history",
    "diff",
    "checkpoint_create",
    # K-03: calc-group migration helpers only emit TMDL / candidates, and the
    # DaxLib search only reads a public catalog — never gate them.
    "detect",
    "scaffold",
    "list_patterns",
    "daxlib_search",
}

# JR-01: these journal ops re-apply images to the live model; preview=true is a dry-run.
_JOURNAL_APPLY_OPS = {"undo", "replay", "checkpoint_restore"}

# C-13: classified by EFFECT. Everything that drops a table, removes a whole
# culture / named expression, installs network-fetched code, or renames a table
# (every DAX expression + optionally the PBIR report) is CATASTROPHIC.
_CATASTROPHIC = {
    ("table", "delete"),
    ("calculation_group", "delete"),
    ("culture", "delete"),
    ("named_expression", "delete"),
    ("function", "daxlib_install"),
    ("table", "rename"),
}

_RENAME_OPS = {"rename"}


def _as_table_list(value) -> list:
    """C-35: `tables="Sales"` used to be iterated per character."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    try:
        return [t for t in value if t]
    except TypeError:
        return []


def _refresh_threshold() -> int:
    try:
        from core.config.config_manager import config

        return int(config.get("model_write.catastrophic_refresh_tables", 10))
    except Exception:  # noqa: BLE001
        return 10


def _rename_severity(object_type: str, args: Dict[str, Any]):
    """C-13: a rename that rewrites expressions is CATASTROPHIC.

    Table renames (a calculation group IS a table, QG-01) and any rename that
    also rewrites the PBIR report (``pbip_path``) always are. A measure/column
    rename is dry-run first (under LIGHT, the only policy where the distinction
    matters): when the cascade would touch 0 expressions it is only
    DESTRUCTIVE. Any failure to preview is treated as CATASTROPHIC (fail closed).
    """
    if object_type in ("table", "calculation_group") or args.get("pbip_path"):
        return Severity.CATASTROPHIC
    if object_type not in ("measure", "column"):
        return Severity.DESTRUCTIVE
    from core.autonomous.gating import GATE_POLICY_LIGHT, current_policy, is_confirmed

    if current_policy() != GATE_POLICY_LIGHT or is_confirmed(args):
        return Severity.CATASTROPHIC
    try:
        from core.infrastructure.connection_state import connection_state

        if object_type == "measure":
            mgr = connection_state.measure_crud_manager
            pv = mgr.preview_rename(
                args.get("table_name"), args.get("measure_name"), args.get("new_name")
            )
        else:
            mgr = connection_state.column_crud_manager
            pv = mgr.preview_rename(
                args.get("table_name"), args.get("column_name"), args.get("new_name")
            )
    except Exception as e:  # noqa: BLE001 - fail closed
        logger.debug("rename severity preview failed: %s", e)
        return Severity.CATASTROPHIC
    if isinstance(pv, dict) and pv.get("success") and pv.get("change_count") == 0:
        return Severity.DESTRUCTIVE
    return Severity.CATASTROPHIC


# Purely ADDITIVE ops create something new and refuse when it already exists
# (C-12 made create honour if_exists='fail'), so they cannot destroy anything and
# must not carry a "Destructive op" advisory. An overwriting create (if_exists=
# 'update' / upsert) stays destructive.
_ADDITIVE_OPS = {"create", "add_item", "add_field", "add_member", "add_level"}


_DRY_RUN_OPS = {"set_property", "import_translations"}

# F-01: refresh ops preview as a PLAN (what would be requested); everything else that writes
# previews through ``write_preview.run_preview`` (the real manager, commit suppressed, rolled back).
_REFRESH_OPS = {("model", "refresh"), ("table", "refresh"), ("partition", "refresh")}
# Raw verbs the fail-closed composites wrap: a preview reports the raw change + the blast radius.
_COMPOSITE_RAW = {"safe_delete": "delete", "safe_rename": "rename"}


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return value is True


def _any_ref(args: Dict[str, Any]) -> Any:
    """``ref``, else ``Table[Object]`` built from table_name + column_name / measure_name."""
    if args.get("ref"):
        return args["ref"]
    obj = args.get("column_name") or args.get("measure_name")
    if args.get("table_name") and obj:
        return f"{args['table_name']}[{obj}]"
    return args.get("table_name") or None


def _any_kind(ref: Any) -> str:
    """The object kind of an ``object_type=any`` ref (for the per-property gate level)."""
    from core.operations.tom_properties import parse_ref

    kind = parse_ref(ref)[0]
    return "column" if kind == "column_or_measure" else (kind or "")


def _severity_for(object_type: str, operation: str, args: Dict[str, Any]):
    if operation in _READ_ONLY_OPS:
        return None
    if operation in _ADDITIVE_OPS and str(args.get("if_exists") or "fail").lower() != "update":
        return None
    if operation in _RENAME_OPS and args.get("preview") is True:
        return None  # C-41 rename preview is read-only
    if object_type == "model" and operation in _JOURNAL_APPLY_OPS and args.get("preview") is True:
        return None  # JR-01 dry-run
    if operation in _DRY_RUN_OPS and _truthy_flag(args.get("dry_run") or args.get("preview")):
        return None  # F-04 / F-20d: validated, nothing written
    if object_type == "any" and operation == "set_property":
        # F-04: per-property level - query-semantics / security properties are break-glass.
        if is_high_impact(_any_kind(_any_ref(args)), str(args.get("property_name") or "")):
            return Severity.CATASTROPHIC
        return Severity.DESTRUCTIVE
    if object_type == "model" and operation == "set_properties":
        # MP-01: a compatibility-level upgrade is irreversible -> break-glass; the rest is an edit.
        if args.get("compatibility_level") is not None:
            return Severity.CATASTROPHIC
        return Severity.DESTRUCTIVE
    if object_type == "model" and operation == "refresh":
        refresh_type = str(args.get("refresh_type") or "automatic").strip().lower()
        if refresh_type in ("calculate", "indexes"):
            # C-35: the cheap FP/calc-table recalculation; RF-01: an index build reloads no data
            return Severity.DESTRUCTIVE
        tables = _as_table_list(args.get("tables"))
        if not tables or len(tables) >= _refresh_threshold():
            return Severity.CATASTROPHIC
        return Severity.DESTRUCTIVE
    if (object_type, operation) in _CATASTROPHIC:
        return Severity.CATASTROPHIC
    if operation in _RENAME_OPS:
        return _rename_severity(object_type, args)
    return Severity.DESTRUCTIVE


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


def _autopopulate_evidence(
    object_type: str, operation: str, args: Dict[str, Any], severity
) -> Optional[Dict[str, Any]]:
    """Compute blast-radius evidence in-process so a safe CATASTROPHIC op can be
    auto-cleared without the caller supplying anything.

    C-02: a caller-supplied ``evidence`` argument is IGNORED — the evidence is
    always recomputed here from the op's own (canonical, C-19) target. Only
    deletes the graph can vouch for are covered: table/delete and
    calculation_group/delete (a calc group is a table). Skipped when the
    caller confirmed (nothing to decide). A full-model ``model/refresh`` is
    CATASTROPHIC but a dependency graph says nothing about a data reload, so it
    is never auto-cleared.
    """
    # Policy-blind guard: the gate only consults evidence under LIGHT (OFF passes,
    # STRICT blocks, WARN warns — all before reading evidence), so computing the
    # expensive live-model+graph blast radius under any other policy is wasted work.
    from core.autonomous.gating import GATE_POLICY_LIGHT, current_policy, is_confirmed

    if current_policy() != GATE_POLICY_LIGHT:
        return None
    if severity is not Severity.CATASTROPHIC:
        return None
    if is_confirmed(args):
        return None
    if (object_type, operation) not in {("table", "delete"), ("calculation_group", "delete")}:
        return None
    if object_type == "calculation_group":
        table_name = args.get("group_name") or args.get("name")
    else:
        table_name = args.get("table_name")
    if not table_name:
        return None
    from core.autonomous import evidence as _ev

    return _ev.compute_delete_evidence(
        [_ev.table_node_ref(table_name)], pbip_path=args.get("pbip_path")
    )


# Reuse existing singleton handlers
_handlers = {
    "table": TableOperationsHandler(),
    "column": ColumnOperationsHandler(),
    "measure": MeasureOperationsHandler(),
    "relationship": RelationshipOperationsHandler(),
    "calculation_group": CalculationGroupOperationsHandler(),
    "partition": PartitionCrudManager(),
    "hierarchy": HierarchyCrudManager(),
    "perspective": PerspectiveCrudManager(),
    "culture": CultureCrudManager(),
    "named_expression": NamedExpressionCrudManager(),
    "ols_rule": OlsCrudManager(),
    "model": ModelRefreshManager(),
    "role": RlsRoleCrudManager(),
    "annotation": AnnotationManager(),
    "field_parameter": FieldParameterManager(),
    "function": FunctionCrudManager(),
    "query_group": QueryGroupCrudManager(),
    "calendar": CalendarCrudManager(),
    # F-04: TOM property reflection over any resolved object (ref = Table[Col], table:Name, ...).
    "any": TomPropertyManager(),
}

# Model-level property read/write. Shares the object_type='model' namespace with
# ModelRefreshManager but is a separate manager, so it is kept out of _handlers
# (whose keys are the user-facing valid_types list).
_model_properties = ModelPropertiesManager()
# Durable write journal (JR-01): history / diff / undo / replay / checkpoints.
_write_journal = WriteJournalManager()

# Valid operations per object_type (for error messages)
_valid_operations = {
    "table": [
        "list",
        "describe",
        "sample_data",
        "create",
        "update",
        "delete",
        "safe_delete",
        "rename",
        "safe_rename",
        "refresh",
        "generate_calendar",
        "get_refresh_policy",
        "set_refresh_policy",
        "clear_refresh_policy",
        "mark_as_date_table",
    ],
    "column": [
        "list",
        "get",
        "statistics",
        "distribution",
        "create",
        "update",
        "delete",
        "safe_delete",
        "rename",
        "safe_rename",
    ],
    "measure": [
        "list",
        "get",
        "create",
        "update",
        "delete",
        "safe_delete",
        "rename",
        "safe_rename",
        "move",
        "set_format_string_expression",
        "set_detail_rows_expression",
        "set_kpi",
        "clear_kpi",
        "get_kpi",
    ],
    "relationship": [
        "list",
        "get",
        "find",
        "create",
        "update",
        "delete",
        "activate",
        "deactivate",
        "rename",
    ],
    "calculation_group": [
        "list",
        "create",
        "delete",
        "list_items",
        "add_item",
        "update_item",
        "delete_item",
        "set_precedence",
        "update",
        "rename",
        "detect",
        "scaffold",
        "list_patterns",
    ],
    "partition": ["list", "describe", "create", "update", "delete", "set_mode", "refresh"],
    "hierarchy": [
        "list",
        "describe",
        "create",
        "update",
        "delete",
        "add_level",
        "remove_level",
        "rename_level",
    ],
    "perspective": [
        "list",
        "describe",
        "create",
        "delete",
        "add_table",
        "add_measure",
        "add_column",
        "add_hierarchy",
        "remove_table",
        "remove_measure",
        "remove_column",
        "remove_hierarchy",
        "set_members",
    ],
    "culture": [
        "list",
        "describe",
        "create",
        "delete",
        "set_translation",
        "bulk_set_translations",
        "export_translations",
        "import_translations",
        "translation_coverage",
    ],
    "named_expression": ["list", "describe", "create", "update", "delete"],
    "ols_rule": ["list", "set", "remove"],
    "model": [
        "refresh",
        "refresh_status",
        "refresh_history",
        "get_properties",
        "set_properties",
        "set_value_filter_behavior",
        "history",
        "diff",
        "undo",
        "replay",
        "checkpoint_create",
        "checkpoint_restore",
    ],
    "role": [
        "list",
        "create",
        "update",
        "delete",
        "set_table_filter",
        "clear_table_filter",
        "add_member",
        "remove_member",
        "list_members",
        "get_effective_permissions",
    ],
    "annotation": ["list", "get", "set", "delete"],
    "field_parameter": [
        "create",
        "set_parameter_metadata",
        "repair",
        "add_field",
        "remove_field",
        "reorder_fields",
    ],
    "function": [
        "list",
        "get",
        "create",
        "update",
        "delete",
        "daxlib_search",
        "daxlib_install",
    ],
    "query_group": ["list", "create", "update", "delete", "assign", "unassign"],
    "calendar": ["list", "get", "create", "update", "delete"],
    "any": ["list_properties", "get_property", "set_property"],
}


def _list_roles() -> Dict[str, Any]:
    """Delegate role list to the existing RLSManager singleton."""
    from core.infrastructure.connection_state import connection_state

    if connection_state.rls_manager is None:
        return error_envelope("not_connected", "Not connected (rls_manager unavailable)")
    return connection_state.rls_manager.list_roles()


def _dispatch_new_type(manager, object_type: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch operations for new TOM object types."""
    operation = args.get("operation", "list")
    dispatch_map = {
        "partition": {
            "list": lambda: manager.list_partitions(args.get("table_name")),
            "describe": lambda: manager.describe_partition(
                args.get("table_name"), args.get("partition_name")
            ),
            "update": lambda: manager.update_partition_expression(
                args.get("table_name"),
                args.get("partition_name"),
                args.get("expression"),
                refresh_after=bool(args.get("refresh_after", False)),
            ),
            "create": lambda: manager.create_partition(
                args.get("table_name"),
                args.get("partition_name"),
                args.get("expression"),
                mode=args.get("mode", "Import"),
            ),
            "delete": lambda: manager.delete_partition(
                args.get("table_name"), args.get("partition_name")
            ),
            "set_mode": lambda: manager.set_partition_mode(
                args.get("table_name"), args.get("partition_name"), args.get("mode")
            ),
            "refresh": lambda: manager.refresh_partition(
                args.get("table_name"), args.get("partition_name")
            ),
        },
        "hierarchy": {
            "list": lambda: manager.list_hierarchies(args.get("table_name")),
            "describe": lambda: manager.describe_hierarchy(
                args.get("table_name"), args.get("hierarchy_name")
            ),
            "create": lambda: manager.create_hierarchy(
                args.get("table_name"),
                args.get("hierarchy_name"),
                args.get("levels", []),
                description=args.get("description"),
                display_folder=args.get("display_folder"),
                hidden=args.get("hidden"),
            ),
            # C-42: rename / describe / reorder levels in one save.
            "update": lambda: manager.update_hierarchy(
                args.get("table_name"),
                args.get("hierarchy_name"),
                new_name=args.get("new_name"),
                levels=args.get("levels"),
                description=args.get("description"),
                display_folder=args.get("display_folder"),
                hidden=args.get("hidden"),
            ),
            "delete": lambda: manager.delete_hierarchy(
                args.get("table_name"), args.get("hierarchy_name")
            ),
            # MP-01: level surgery in one save each.
            "add_level": lambda: manager.add_level(
                args.get("table_name"),
                args.get("hierarchy_name"),
                level_name=args.get("level_name"),
                column_name=args.get("column_name"),
                ordinal=args.get("ordinal"),
            ),
            "remove_level": lambda: manager.remove_level(
                args.get("table_name"),
                args.get("hierarchy_name"),
                level_name=args.get("level_name"),
            ),
            "rename_level": lambda: manager.rename_level(
                args.get("table_name"),
                args.get("hierarchy_name"),
                level_name=args.get("level_name"),
                new_name=args.get("new_name"),
            ),
        },
        "perspective": {
            "list": lambda: manager.list_perspectives(),
            "describe": lambda: manager.describe_perspective(args.get("perspective_name")),
            "create": lambda: manager.create_perspective(
                args.get("perspective_name"), description=args.get("description")
            ),
            "delete": lambda: manager.delete_perspective(args.get("perspective_name")),
            "add_table": lambda: manager.add_table(
                args.get("perspective_name"), args.get("table_name")
            ),
            "add_measure": lambda: manager.add_measure(
                args.get("perspective_name"),
                args.get("table_name"),
                args.get("measure_name"),
            ),
            "add_column": lambda: manager.add_column(
                args.get("perspective_name"),
                args.get("table_name"),
                args.get("column_name"),
            ),
            "add_hierarchy": lambda: manager.add_hierarchy(
                args.get("perspective_name"),
                args.get("table_name"),
                args.get("hierarchy_name"),
            ),
            "remove_table": lambda: manager.remove_table(
                args.get("perspective_name"), args.get("table_name")
            ),
            "remove_measure": lambda: manager.remove_measure(
                args.get("perspective_name"),
                args.get("table_name"),
                args.get("measure_name"),
            ),
            "remove_column": lambda: manager.remove_column(
                args.get("perspective_name"),
                args.get("table_name"),
                args.get("column_name"),
            ),
            "remove_hierarchy": lambda: manager.remove_hierarchy(
                args.get("perspective_name"),
                args.get("table_name"),
                args.get("hierarchy_name"),
            ),
            # C-42: bulk membership, one SaveChanges.
            "set_members": lambda: manager.set_members(
                args.get("perspective_name"),
                args.get("members"),
                mode=args.get("member_action") or "add",
            ),
        },
        "culture": {
            "list": lambda: manager.list_cultures(),
            "describe": lambda: manager.describe_culture(args.get("culture_name")),
            "create": lambda: manager.create_culture(args.get("culture_name")),
            "delete": lambda: manager.delete_culture(args.get("culture_name")),
            "set_translation": lambda: manager.set_translation(
                args.get("culture_name"),
                args.get("object_type_target"),
                args.get("name"),
                args.get("property_name"),
                args.get("value"),
                table_name=args.get("table_name"),
            ),
            "bulk_set_translations": lambda: manager.bulk_set_translations(
                args.get("culture_name"),
                args.get("translations") or [],
            ),
            # F-20d: CSV / JSON exchange + per-culture coverage.
            "export_translations": lambda: manager.export_translations(
                culture_name=args.get("culture_name"),
                file_format=args.get("file_format") or "json",
                output_path=args.get("output_path"),
                limit=int(args.get("limit") or 500),
            ),
            "import_translations": lambda: manager.import_translations(
                args.get("culture_name"),
                translations=args.get("translations"),
                input_path=args.get("input_path"),
                file_format=args.get("file_format"),
                dry_run=_truthy_flag(args.get("dry_run") or args.get("preview")),
            ),
            "translation_coverage": lambda: manager.translation_coverage(
                culture_name=args.get("culture_name"), limit=int(args.get("limit") or 50)
            ),
        },
        "any": {
            "list_properties": lambda: manager.list_properties(_any_ref(args)),
            "get_property": lambda: manager.get_property(_any_ref(args), args.get("property_name")),
            "set_property": lambda: manager.set_property(
                _any_ref(args),
                args.get("property_name"),
                args.get("value"),
                dry_run=_truthy_flag(args.get("dry_run") or args.get("preview")),
            ),
        },
        "named_expression": {
            "list": lambda: manager.list_expressions(),
            "describe": lambda: manager.describe_expression(args.get("expression_name")),
            "create": lambda: manager.create_expression(
                args.get("expression_name"),
                args.get("expression"),
                description=args.get("description"),
            ),
            "update": lambda: manager.update_expression(
                args.get("expression_name"),
                args.get("expression"),
                description=args.get("description"),
            ),
            "delete": lambda: manager.delete_expression(args.get("expression_name")),
        },
        "ols_rule": {
            "list": lambda: manager.list_ols_rules(args.get("role_name")),
            # C-15: omit column_name for TABLE-level OLS.
            "set": lambda: manager.set_ols_rule(
                args.get("role_name"),
                args.get("table_name"),
                args.get("column_name"),
                args.get("permission", "None"),
            ),
            "remove": lambda: manager.remove_ols_rule(
                args.get("role_name"), args.get("table_name"), args.get("column_name")
            ),
        },
        "model": {
            "refresh": lambda: manager.refresh(
                refresh_type=args.get("refresh_type", "automatic"),
                tables=(
                    _as_table_list(args.get("tables")) if args.get("tables") is not None else None
                ),
            ),
            # Model-level TOM properties live on their own manager.
            "get_properties": lambda: _model_properties.get_properties(),
            "set_value_filter_behavior": lambda: _model_properties.set_value_filter_behavior(
                behavior=args.get("behavior")
            ),
            # MP-01: description / culture / discourage_implicit_measures / compatibility_level.
            "set_properties": lambda: _model_properties.set_properties(
                description=args.get("description"),
                culture=args.get("culture"),
                discourage_implicit_measures=args.get("discourage_implicit_measures"),
                compatibility_level=args.get("compatibility_level"),
                allow_compatibility_upgrade=args.get("allow_compatibility_upgrade", False),
            ),
            # RF-01: what the open model can say about its refreshes.
            "refresh_status": lambda: manager.refresh_status(table_name=args.get("table_name")),
            "refresh_history": lambda: manager.refresh_history(
                since=args.get("since"), limit=args.get("limit")
            ),
            # JR-01 durable write journal.
            "history": lambda: _write_journal.history(args),
            "diff": lambda: _write_journal.diff(args),
            "undo": lambda: _write_journal.undo(args),
            "replay": lambda: _write_journal.replay(args),
            "checkpoint_create": lambda: _write_journal.checkpoint_create(args),
            "checkpoint_restore": lambda: _write_journal.checkpoint_restore(args),
        },
        "role": {
            "list": lambda: _list_roles(),
            "create": lambda: manager.create_role(
                args.get("role_name"),
                model_permission=args.get("permission", "Read"),
                description=args.get("description"),
            ),
            "update": lambda: manager.update_role(
                args.get("role_name"),
                new_name=args.get("new_name"),
                model_permission=args.get("permission"),
                description=args.get("description"),
            ),
            "delete": lambda: manager.delete_role(args.get("role_name")),
            "set_table_filter": lambda: manager.set_table_filter(
                args.get("role_name"), args.get("table_name"), args.get("filter_expression")
            ),
            "clear_table_filter": lambda: manager.clear_table_filter(
                args.get("role_name"), args.get("table_name")
            ),
            "add_member": lambda: manager.add_member(
                args.get("role_name"),
                args.get("member_identifier"),
                member_type=args.get("member_type", "external"),
                tenant_id=args.get("tenant_id"),
            ),
            "remove_member": lambda: manager.remove_member(
                args.get("role_name"), args.get("member_identifier")
            ),
            "list_members": lambda: manager.list_members(args.get("role_name")),
            "get_effective_permissions": lambda: manager.get_effective_permissions(
                role_name=args.get("role_name"), table_name=args.get("table_name")
            ),
        },
        "calendar": {
            "list": lambda: manager.list_calendars(table_name=args.get("table_name")),
            "get": lambda: manager.get_calendar(args.get("calendar_name")),
            "create": lambda: manager.create_calendar(
                args.get("calendar_name"),
                args.get("table_name"),
                args.get("groups") or [],
                time_related_columns=args.get("time_related_columns"),
                description=args.get("description"),
            ),
            "update": lambda: manager.update_calendar(
                args.get("calendar_name"),
                groups=args.get("groups"),
                time_related_columns=args.get("time_related_columns"),
                description=args.get("description"),
                new_name=args.get("new_name"),
            ),
            "delete": lambda: manager.delete_calendar(args.get("calendar_name")),
        },
        "query_group": {
            "list": lambda: manager.list_query_groups(),
            "create": lambda: manager.create_query_group(
                args.get("query_group"), description=args.get("description")
            ),
            "update": lambda: manager.update_query_group(
                args.get("query_group"),
                description=args.get("description"),
                new_name=args.get("new_name"),
            ),
            "delete": lambda: manager.delete_query_group(
                args.get("query_group"), force=bool(args.get("force", False))
            ),
            "assign": lambda: manager.assign(
                args.get("query_group"),
                expression_name=args.get("expression_name"),
                table_name=args.get("table_name"),
                partition_name=args.get("partition_name"),
            ),
            "unassign": lambda: manager.assign(
                args.get("query_group"),
                expression_name=args.get("expression_name"),
                table_name=args.get("table_name"),
                partition_name=args.get("partition_name"),
                unassign=True,
            ),
        },
        "annotation": {
            "list": lambda: manager.list_annotations(args.get("target_type"), args),
            "get": lambda: manager.get_annotation(
                args.get("target_type"), args.get("annotation_name"), args
            ),
            "set": lambda: manager.set_annotation(
                args.get("target_type"),
                args.get("annotation_name"),
                args.get("annotation_value"),
                args,
            ),
            "delete": lambda: manager.delete_annotation(
                args.get("target_type"), args.get("annotation_name"), args
            ),
        },
        "field_parameter": {
            "create": lambda: manager.create_field_parameter(
                table_name=args.get("table_name"),
                fields=args.get("fields"),
                description=args.get("description"),
                hidden=bool(args.get("hidden", False)),
                expression=args.get("expression"),
                extra_columns=args.get("extra_columns"),
                clone_from=args.get("clone_from"),
            ),
            # C-09: both names run the FULL idempotent repair.
            "set_parameter_metadata": lambda: manager.repair_field_parameter(
                table_name=args.get("table_name"),
                fields_column=args.get("column_name"),
            ),
            "repair": lambda: manager.repair_field_parameter(
                table_name=args.get("table_name"),
                fields_column=args.get("column_name"),
            ),
            "add_field": lambda: manager.add_field(
                table_name=args.get("table_name"),
                field=args.get("field"),
            ),
            "remove_field": lambda: manager.remove_field(
                table_name=args.get("table_name"),
                label=args.get("label"),
            ),
            "reorder_fields": lambda: manager.reorder_fields(
                table_name=args.get("table_name"),
                labels_in_order=args.get("labels_in_order") or [],
            ),
        },
        "function": {
            "list": lambda: manager.list_functions(include=args.get("include")),
            "get": lambda: manager.get_function(args.get("function_name")),
            "create": lambda: manager.create_function(
                function_name=args.get("function_name"),
                expression=args.get("expression"),
                parameters=args.get("parameters"),
                description=args.get("description"),
            ),
            "update": lambda: manager.update_function(
                function_name=args.get("function_name"),
                expression=args.get("expression"),
                description=args.get("description"),
            ),
            "delete": lambda: manager.delete_function(args.get("function_name")),
            "daxlib_search": lambda: manager.daxlib_search(
                query=args.get("query"),
                limit=int(args.get("limit", 50)),
            ),
            "daxlib_install": lambda: manager.daxlib_install(
                package_id=args.get("package_id"),
                prefix=args.get("prefix"),
                force=bool(args.get("force", False)),
            ),
        },
    }
    ops = dispatch_map.get(object_type, {})
    handler_fn = ops.get(operation)
    if not handler_fn:
        return error_envelope(
            "invalid_operation",
            f"Unknown operation '{operation}' for {object_type}",
            valid_operations=list(ops.keys()),
        )
    return handler_fn()


def _original_handle_model_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Unified model CRUD dispatcher (gate-free core)."""
    object_type = args.get("object_type")
    handler = _handlers.get(object_type)
    if not handler:
        return error_envelope(
            "invalid_operation",
            f"Unknown object_type: {object_type}",
            valid_types=list(_handlers.keys()),
        )

    operation = args.get("operation")
    valid_ops = _valid_operations.get(object_type, [])
    if operation and operation not in valid_ops:
        return error_envelope(
            "invalid_operation",
            f"Invalid operation '{operation}' for {object_type}",
            valid_operations=valid_ops,
        )

    # Calc-group migration ops (folded from former 14_Calc_Group_Migrator).
    # Read-only — emit TMDL only. Apply via the standard create/add_item ops.
    if object_type == "calculation_group" and operation in {"detect", "scaffold", "list_patterns"}:
        from core.calc_group_migration import detector, patterns, tmdl_scaffold

        if operation == "list_patterns":
            return {
                "success": True,
                "patterns": [
                    {"name": k, "kind": v["kind"], "variants": list(v["variants"])}
                    for k, v in patterns.PATTERNS.items()
                ],
            }
        if operation == "scaffold":
            candidate = args.get("candidate")
            if not candidate:
                return {
                    "success": False,
                    "error": "candidate is required (run detect first)",
                    "error_type": "invalid_args",
                }
            out = tmdl_scaffold.scaffold_calc_group(
                candidate,
                calc_group_table_name=args.get("calc_group_table_name")
                or args.get("group_name")
                or "TimeIntelligence",
                precedence=int(args.get("precedence", 10)),
                date_column=args.get("date_column"),
            )
            out["success"] = True
            return out
        # detect
        from core.infrastructure.live_model_facade import build_live_model

        model = build_live_model()
        if model is None:
            return {
                "success": False,
                "error": "Not connected to Power BI Desktop",
                "error_type": "not_connected",
            }
        out = detector.detect(
            model,
            patterns=args.get("patterns"),
            min_pattern_size=int(args.get("min_pattern_size", 3)),
        )
        if not out["candidates"]:
            return {
                "success": False,
                "error": "no candidate patterns found",
                "error_type": "pattern_not_found",
                "candidates": [],
            }
        out["success"] = True
        return out

    # New TOM types don't have execute() — dispatch directly
    if object_type in (
        "partition",
        "hierarchy",
        "perspective",
        "culture",
        "named_expression",
        "ols_rule",
        "model",
        "role",
        "annotation",
        "field_parameter",
        "function",
        "query_group",
        "calendar",
        "any",
    ) or not hasattr(handler, "execute"):
        # A manager without execute() can only be served by the direct table;
        # 'calendar' was missing here and crashed live (2026-09-30).
        return _dispatch_new_type(handler, object_type, args)

    # Existing types use the execute() pattern
    return handler.execute(args)


def _is_already_exists_error(result: Dict[str, Any]) -> bool:
    """T1-D helper: heuristic check for "already exists" failures.

    The TOM CRUD managers don't share a stable error_type for this case;
    some use 'creation_error' / 'duplicate_*' and some leave error_type
    unset. Fall back to substring match on the error message — clunky but
    the message text "already exists" is canonical across managers.
    """
    if not isinstance(result, dict):
        return False
    if result.get("success") is not False:
        return False
    err = (result.get("error") or "").lower()
    et = (result.get("error_type") or "").lower()
    return "already exists" in err or et in {
        "duplicate",
        "duplicate_name",
        "already_exists",
        "table_exists",
        "column_exists",
    }


_SAFE_COMPOSITE_TYPES = {"table", "measure", "column"}


def _node_ref_for(object_type: str, args: Dict[str, Any]) -> Optional[str]:
    """Build the dependency-graph node id for a safe_* op's target.

    C-19: reads ONLY the canonical keys (``table_name`` + ``<type>_name``) —
    the handler canonicalizes the args once, so the evidence target and the
    executed target are the same object by construction.
    """
    from core.autonomous import evidence as _ev

    if object_type == "table":
        name = args.get("table_name")
        return _ev.table_node_ref(name) if name else None
    table = args.get("table_name")
    name = args.get(f"{object_type}_name")
    if not (table and name):
        return None
    return f"{object_type}:{table}[{name}]"


def _handle_safe_composite(
    object_type: str, operation: str, args: Dict[str, Any]
) -> Dict[str, Any]:
    """safe_delete / safe_rename — prove safety via the graph, then run the raw
    op, or refuse. Fails closed: no proof → no change.

    C-20: the policy gate still runs (STRICT blocks without confirm_destructive;
    the proof is what clears LIGHT), gate info is merged into the result, and
    safe_rename forwards the PBIP path the proof was computed against so the
    PBIR rebinds actually happen.
    """
    if object_type not in _SAFE_COMPOSITE_TYPES:
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": f"{operation} is only supported for {sorted(_SAFE_COMPOSITE_TYPES)}",
        }
    node_ref = _node_ref_for(object_type, args)
    if not node_ref:
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": f"{operation} requires the target identifiers (table_name and, for "
            "measure/column, measure_name/column_name).",
        }

    from core.autonomous import evidence as _ev

    if operation == "safe_rename":
        new_name = args.get("new_name")
        if not new_name:
            return {
                "success": False,
                "error_type": "invalid_args",
                "error": "safe_rename requires new_name.",
            }
        evidence = _ev.compute_rename_evidence(node_ref, new_name, pbip_path=args.get("pbip_path"))
        unsafe_type, verb = "rename_collision", "rename"
    else:
        evidence = _ev.compute_delete_evidence([node_ref], pbip_path=args.get("pbip_path"))
        unsafe_type, verb = "unsafe_delete", "delete"

    if evidence is None:
        return {
            "success": False,
            "error_type": "evidence_unavailable",
            "error": (
                f"{operation} could not compute a blast radius (not connected, no PBIP "
                "path, or the dependency graph could not be built). Refusing to proceed — "
                f"use the raw {verb} op with confirm_destructive=true to override."
            ),
            "node_ref": node_ref,
        }
    br = evidence.get("blast_radius", {})
    if not br.get("all_safe", False):
        return {
            "success": False,
            "error_type": unsafe_type,
            "error": f"{operation} refused: the change is not safe.",
            "node_ref": node_ref,
            "blast_radius": br,
        }
    if not _ev.evidence_is_proof(evidence):
        return {
            "success": False,
            "error_type": "evidence_unproven",
            "error": (
                f"{operation} refused: the dependency graph cannot prove the change safe "
                "(" + "; ".join(evidence.get("unproven_reasons") or ["incomplete graph"]) + ")."
            ),
            "node_ref": node_ref,
            "blast_radius": br,
            "unproven_reasons": evidence.get("unproven_reasons"),
        }

    raw_args = {k: v for k, v in args.items() if k != "evidence"}
    raw_args["operation"] = verb
    pbip_used = (evidence.get("provenance") or {}).get("pbip_path")
    if verb == "rename":
        rebinds = [
            r
            for imp in (br.get("impacts") or {}).values()
            for r in (imp.get("rebinds_needed") or [])
        ]
        if not raw_args.get("pbip_path"):
            if rebinds and not pbip_used:
                return {
                    "success": False,
                    "error_type": "rebind_path_required",
                    "error": (
                        "safe_rename refused: report visuals bind this object "
                        f"({len(rebinds)} rebind(s)) and no PBIP path is known — pass pbip_path."
                    ),
                    "rebinds_needed": rebinds[:25],
                }
            if pbip_used:
                raw_args["pbip_path"] = pbip_used

    # The policy gate runs for safe ops too (C-20): STRICT is never relaxed.
    op_label = f"02_Model_Operations:{object_type}/{operation}"
    gate = require_autonomous_mode(op_label, args, Severity.CATASTROPHIC, evidence=evidence)
    if gate is not None and gate.get("success") is False:
        _attach_blast_radius(gate, evidence)
        return gate

    # Proven safe (and the gate agreed) — run the raw op, confirmation injected.
    raw_args["confirm_destructive"] = True
    result = _original_handle_model_operations(raw_args)
    if isinstance(result, dict):
        result.setdefault("safe_op", operation)
        if result.get("success") is not False:
            result.setdefault("evidence_cleared", True)
        merge_gate_info(result, gate)
    return result


_CANONICAL_TYPES = {"table", "measure", "column"}


def _execute_with_upsert(args: Dict[str, Any], if_exists: Optional[str]) -> Dict[str, Any]:
    """The ungated execute of ``handle_model_operations`` incl. the T1-D create->update retry."""
    result = _original_handle_model_operations(args)
    if (
        args.get("operation") == "create"
        and if_exists == "update"
        and _is_already_exists_error(result)
        and args.get("object_type") in {"measure", "column", "table", "relationship"}
    ):
        retry = _original_handle_model_operations({**args, "operation": "update"})
        if isinstance(retry, dict):
            retry["upserted"] = True
            retry["if_exists_action"] = "update"
        return retry
    return result


def _preview_model_write(
    object_type: str,
    operation: str,
    args: Dict[str, Any],
    received_args: Dict[str, Any],
    if_exists: Optional[str],
) -> Dict[str, Any]:
    """F-01: ``preview=true`` of a write op (never a read op; the caller filters those).

    A preview never reaches the destructive gate (nothing is written) and never saves. Ops that
    already had a dry-run keep it (rename cascade diff + ``preview_hash``, journal undo / replay /
    checkpoint_restore, ``set_property`` / ``import_translations`` validation); refresh ops return a
    plan; everything else runs the real manager through
    :func:`core.operations.write_preview.run_preview` (commit suppressed, then rolled back).
    """
    from core.operations import write_preview

    verb = _COMPOSITE_RAW.get(operation, operation)
    label = f"02_Model_Operations:{object_type}/{verb}"
    args = {**args, "operation": verb}
    if object_type in _CANONICAL_TYPES:
        from core.operations.target_resolution import AmbiguousTarget, canonicalize_args

        try:
            args = canonicalize_args(object_type, args)
        except AmbiguousTarget as e:
            return e.to_error()
    if verb in _RENAME_OPS or (object_type == "model" and verb in _JOURNAL_APPLY_OPS):
        # existing dry-runs read ``preview`` (cascade diff + preview_hash / journal dry-run)
        return _original_handle_model_operations({**args, "preview": True})
    if verb in _DRY_RUN_OPS:
        return _original_handle_model_operations(args)  # reads dry_run / preview itself
    if (object_type, verb) in _REFRESH_OPS:
        return write_preview.refresh_plan(
            "02_Model_Operations", label, {**received_args, "object_type": object_type}
        )
    exec_args = {k: v for k, v in args.items() if k not in ("preview", "dry_run", "plan_hash")}
    return write_preview.run_preview(
        "02_Model_Operations",
        label,
        received_args,
        lambda: _execute_with_upsert(exec_args, if_exists),
    )


def handle_model_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Gate-wrapped dispatcher. Severity computed per-call; read-only ops skip gate.

    T1-D: when ``operation='create'`` is called with ``if_exists='update'``
    (or the convenience alias ``operation='upsert'``), a duplicate-name
    failure is automatically retried as ``operation='update'`` against the
    same args. Default behaviour is unchanged when neither flag is present.
    Supported object types: measure, column, table, relationship.
    """
    object_type = args.get("object_type")
    operation = args.get("operation", "list")

    if not object_type:
        return error_envelope(
            "missing_argument",
            "object_type is required",
            valid_types=list(_handlers.keys()),
        )

    # T1-D: normalize the 'upsert' alias to 'create' + if_exists='update'.
    if_exists = args.get("if_exists")
    if operation == "upsert":
        if_exists = if_exists or "update"
        operation = "create"
        args = {**args, "operation": "create"}

    # K-03: validate (object_type, operation) BEFORE the gate, so a typo is
    # reported as a typo instead of a confirmation demand.
    if object_type not in _handlers:
        return error_envelope(
            "invalid_operation",
            f"Unknown object_type: {object_type}",
            valid_types=list(_handlers.keys()),
        )
    valid_ops = _valid_operations.get(object_type, [])
    if operation not in valid_ops:
        return {
            "success": False,
            "error": f"Invalid operation '{operation}' for {object_type}",
            "error_type": "invalid_operation",
            "valid_operations": valid_ops,
        }

    notices: Dict[str, Any] = {}
    if args.get("evidence") is not None:
        # C-02: caller-supplied evidence never clears the gate.
        notices["deprecated_args"] = [
            "evidence: ignored — the destructive gate computes its own in-process evidence"
        ]
        args = {k: v for k, v in args.items() if k != "evidence"}

    # F-01: one uniform preview=true (alias dry_run=true) for every live-TOM write, and the
    # plan_hash staleness gate for the apply. Both look at the call as received (same stage).
    preview = _truthy_flag(args.get("preview")) or _truthy_flag(args.get("dry_run"))
    received_args = args
    if preview and operation not in _READ_ONLY_OPS:
        return {
            **_preview_model_write(object_type, operation, args, received_args, if_exists),
            **notices,
        }
    elif not preview and args.get("plan_hash"):
        from core.operations import write_preview

        stale = write_preview.check_plan_hash("02_Model_Operations", received_args)
        if stale is not None:
            return {**stale, **notices}
        notices["plan_hash_verified"] = True
        args = {k: v for k, v in args.items() if k != "plan_hash"}

    # C-19: resolve the target ONCE; evidence and executor both read the result.
    if object_type in _CANONICAL_TYPES:
        from core.operations.target_resolution import AmbiguousTarget, canonicalize_args

        try:
            args = canonicalize_args(object_type, args)
        except AmbiguousTarget as e:
            return e.to_error()

    # Composite fail-closed ops: prove safety via the dependency graph BEFORE
    # touching the model. Unlike the raw delete/rename (which merely gate and
    # accept confirm_destructive), a safe_* op refuses unless the graph
    # affirmatively proves it safe.
    if operation in {"safe_delete", "safe_rename"}:
        return {**_handle_safe_composite(object_type, operation, args), **notices}

    severity = _severity_for(object_type, operation, args)
    gate = None
    if severity is not None:
        op_label = f"02_Model_Operations:{object_type}/{operation}"
        evidence = _autopopulate_evidence(object_type, operation, args, severity)
        gate = require_autonomous_mode(op_label, args, severity, evidence=evidence)
        if gate is not None and gate.get("success") is False:
            _attach_blast_radius(gate, evidence)
            gate.update(notices)
            return gate

    result = _original_handle_model_operations(args)

    # T1-D retry. Only kick in on create + if_exists='update' + duplicate-name
    # failure + a supported object_type (where 'update' actually exists).
    if (
        operation == "create"
        and if_exists == "update"
        and _is_already_exists_error(result)
        and object_type in {"measure", "column", "table", "relationship"}
    ):
        retry_args = {**args, "operation": "update"}
        # Re-dispatch — gate already approved (severity for update is <=
        # severity for create on these types, so we don't re-gate).
        retry_result = _original_handle_model_operations(retry_args)
        if isinstance(retry_result, dict):
            retry_result["upserted"] = True
            retry_result["if_exists_action"] = "update"
            retry_result.update(notices)
        merge_gate_info(retry_result, gate)
        return retry_result

    if isinstance(result, dict):
        result.update(notices)
    merge_gate_info(result, gate)
    return result


def register_model_operations_handler(registry):
    """Register unified model operations handler"""
    tool = ToolDefinition(
        name="02_Model_Operations",
        description=(
            "LIVE TOM CRUD on the open model: object_type + operation. Types: table, column, "
            "measure, relationship, calculation_group, partition, hierarchy, perspective, "
            "culture (+ translations), named_expression, ols_rule, role, "
            "annotation, query_group, any (list / get / set_property: the TOM long "
            "tail by ref), field_parameter (create / repair / add / remove / reorder fields), "
            "function (UDFs, daxlib), model (get_properties, set_properties, "
            "set_value_filter_behavior, refresh, refresh_status, write journal: history / "
            "diff / undo / replay / checkpoints). Common ops: list, get, describe, create (if_exists='update' = upsert), "
            "update, delete, rename (cascades every DAX ref, verified; "
            "preview=true dry-run), safe_delete / safe_rename (fail-closed: refuse unless the "
            "dependency graph proves zero blast radius). Live immediately - verify with "
            "04_Run_DAX; offline TMDL edits: 02_TMDL_Operations."
        ),
        guide_notes="LIVE TOM CRUD for tables, columns, measures, relationships, calculation groups, partitions, hierarchies, perspectives, cultures, named expressions, OLS rules, RLS roles (incl. table filters + members), and field parameters (object_type='field_parameter', operation='create' — pass `table_name` + `fields=[{label, reference, order?}]`, OR pass `expression` for a raw table-constructor body like `{ (label, NAMEOF(ref), n, ...), ... }` when cloning an existing FP with extra metadata columns, OR pass `clone_from` to clone an existing FP table; combine with `extra_columns` to rename/hide Value4..ValueN. Use operation='repair' (alias 'set_parameter_metadata') to apply the complete FP shape (column names, hidden flags, sort-by, groupBy, ParameterMetadata, annotations) idempotently to an existing or half-built FP — pass `table_name`. Use operation='add_field' to append a row (`field={label, reference, order?}`), 'remove_field' to drop a row by `label` and renumber remaining 0..N-1, or 'reorder_fields' to reassign orders 0..N-1 according to `labels_in_order` (must be a permutation of existing labels)). For table/measure/column, operation='safe_delete' and 'safe_rename' are FAIL-CLOSED composites: they compute a dependency-graph blast radius in-process and only proceed when it proves the change safe (safe_rename also needs new_name), else they refuse (error_type unsafe_delete / rename_collision / evidence_unavailable / evidence_unproven) and never touch the model — use the raw delete/rename with confirm_destructive=true to override. For object_type='model', operation='get_properties' reads model-level TOM properties (value_filter_behavior as an enum NAME, compatibility_level, culture, default_mode) and operation='set_value_filter_behavior' writes Model.ValueFilterBehavior via `behavior` (Automatic|Independent|Coalesced, case-insensitive; requires compatibility level 1600+ — 'Independent' is correct for models authored 2025+, while 'Automatic' resolves to the legacy 'Coalesced' behaviour that makes SUMMARIZECOLUMNS surprising when several filters hit one table). Renames cascade every DAX reference (lexer-based, M partitions untouched), are verified after save and rolled back on new errors; pass preview=true for a dry-run diff + preview_hash. Requires an active connection to PBI Desktop (01_Connection); changes propagate immediately, no reload needed. For offline PBIP file edits use 02_TMDL_Operations instead. DURABLE WRITE JOURNAL (object_type='model'): every live write is appended to output/journal/<project>.jsonl with the pre- and post-image of each touched object, survives a server restart and is marked flushed by a disk-verified 17_Desktop_Bridge save. operation='history' lists entries (since, object, unflushed_only, limit; newest first), 'diff' shows one entry (entry_id) pre vs post, 'undo' re-applies pre-images in reverse in ONE save (count=N or entry_id; refuses when a touched object no longer matches the entry's post-image unless force=true + confirm_destructive; preview=true dry-run), 'replay' re-applies unflushed entries after a discarded reload or a crash (preview=true first; entries already in the model are skipped, conflicts are reported), 'checkpoint_create' (checkpoint=<name>) snapshots the whole model to output/journal/checkpoints and 'checkpoint_restore' (preview=true first) makes the live model equal it. undo, replay and checkpoint_restore are themselves journaled; removing a table/culture/named expression needs confirm_destructive. PARITY PACK (M4): object_type='model' operation='set_properties' sets `description`, `culture`, `discourage_implicit_measures` and - only with `allow_compatibility_upgrade=true` + `confirm_destructive=true` (CATASTROPHIC, IRREVERSIBLE, downgrades refused) - `compatibility_level`; 'refresh_status' reads each partition's state / RefreshedTime and 'refresh_history' lists the refreshes this server requested (Desktop keeps no refresh log); refresh_type='indexes' builds explicit string / full-text indexes (CL 1707+, refused below with guidance, never auto-upgrades). object_type='table' operation='mark_as_date_table' (`table_name` + `date_column`: DateTime, unique, whole days, contiguous - validated by one DAX query before anything is written). object_type='hierarchy' add_level / remove_level / rename_level (`level_name`, `column_name`, `ordinal`, `new_name`). object_type='role' operation='get_effective_permissions' (`role_name` optional; tables, RLS filters, OLS per role). object_type='query_group' list / create / update / delete / assign / unassign (`query_group` = the Power Query folder path; assign takes `expression_name` OR `table_name` [+ `partition_name`]; delete of a used group needs force=true). object_type='relationship' operation='rename'. object_type='calculation_group' operation='update' (`description`, `precedence`, `hidden`) and 'rename' (= table rename: cascade, preview, CATASTROPHIC). column update accepts `string_indexing_behavior` (auto|full|explicit|off, CL 1707+) and `full_text_indexing_behavior` (full|explicit|off, CL 1708+; needed by TEXTCONTAINS / TEXTSIMILARITY) on String columns; feature_unsupported_by_client if the AMO build lacks the property. TOM PROPERTY LONG TAIL (F-04): object_type='any' with `ref` (Table[Column|Measure], table:Name, hierarchy:Table[Name], relationship:Name, role:Name, expression:Name, calculation_group:Name, model) - operation='list_properties' (every scalar property with type, value, settable flag, enum values, min compatibility level), 'get_property' and 'set_property' (`property_name`, `value`, `dry_run=true` validates and reports previous -> value). Settable = an allow-list per object kind (e.g. column SummarizeBy / IsAvailableInMDX / SortByColumn / DataCategory / DisplayFolder / FormatString, table DataCategory, measure FormatString, relationship RelyOnReferentialIntegrity, model SelectionExpressionBehavior); everything else is refused with denied_property + the reason (name -> use rename, expression -> use update, lineage tags, structural). `Annotations[name]` and `ExtendedProperties[name]` work on every object that has them. Values are coerced (true/false, integers, enum member names case-insensitive, column / measure refs, '' clears a ref); a compatibility level below the property's minimum is refused (compatibility_level_too_low, with the required level) before anything is saved. One journaled save per call; query-semantics / security properties (ValueFilterBehavior, SelectionExpressionBehavior, relationship filter behaviours) are CATASTROPHIC and need confirm_destructive. CALCULATION GROUPS (F-20c): operation='update' also takes `multiple_or_empty_selection_expression` and `no_selection_expression` (what applies when several / no items are selected; '' clears; CL 1605+) and `selection_expression_behavior` (automatic|visual|nonvisual; MODEL-wide Model.SelectionExpressionBehavior, CL 1609+). TRANSLATIONS (F-20d): object_type='culture' operation='export_translations' (`culture_name` optional = all cultures, `file_format` json|csv, `output_path` writes the file, else rows inline capped by `limit`; every translatable caption / description / display folder with its source value), 'import_translations' (`culture_name`, `translations` rows OR `input_path` CSV/JSON in the export layout; empty values never blank a translation; `dry_run=true` previews; one journaled save) and 'translation_coverage' (per culture: translated / translatable, by property, first `limit` missing). CALENDAR TEMPLATES (F-20e): table generate_calendar takes `calendar_template` standard | iso_week (ISOYear / ISOWeek / ISOYearWeek) | 445 | 454 | 544 (52/53-week retail calendar: FiscalYear, FiscalYearStart, FiscalWeek, FiscalQuarter, FiscalPeriod; fiscal year = first Monday on/after the 1st of `fiscal_start_month`, default 1). PREVIEW ANY WRITE (F-01): `preview=true` (alias `dry_run=true`) on any write op runs the real manager with the save suppressed and rolls it back - nothing is saved: `would_change[]` (per object before / after TMSL), `errors[]`, `breaks[]` (dependents of anything it would remove, from the dependency graph) and a `plan_hash`; apply = the same call without `preview` + `plan_hash=<hash>` (refused `stale_preview` if the model generation moved or the args differ). A preview cannot evaluate DAX (`not_validated`); refresh ops preview as a plan (`would_refresh`). `request_id` makes a retried write idempotent. Specify object_type + operation.",
        handler=handle_model_operations,
        input_schema=TOOL_SCHEMAS["model_operations"],
        operations_by_type=_valid_operations,
        category="model",
        sort_order=20,
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    registry.register(tool)
    logger.info("Registered unified model_operations handler")
