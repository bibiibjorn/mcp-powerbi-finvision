"""Per-operation read/write classification for every registered tool.

One table drives every server-side safety and consistency mechanism that used
to key on the coarse, tool-level MCP annotations (A-11 / L-07 / A-31e):

* ``security.read_only_mode`` blocks ``write`` + ``destructive`` ops — per
  operation, so ``01_Connection``, ``02_Model_Operations measure/list`` or
  ``09_Debug_Operations visual`` keep working in read-only mode;
* ``security.confirm_destructive`` asks for ``confirm_destructive=true`` on
  ``destructive`` ops only (never on reads of a destructive-capable tool);
* the dispatcher serialises ``write`` + ``destructive`` ops process-wide and
  bumps ``connection_state``'s model generation after each successful one;
* a timed-out ``write`` / ``destructive`` op is reported as
  ``timeout_outcome_unknown`` (verify before retrying).

Levels
------
``read``
    Does not mutate the semantic model, the PBIP report/model files or the
    running Desktop's state. May write export / artifact files the caller asked
    for (HTML, CSV, .daxx, Word docs, baselines) and may evict engine caches.
``write``
    Creates or updates model / report objects in place.
``destructive``
    Deletes / removes / renames / restores / refreshes, whole-artifact
    overwrites (theme apply/import), and Desktop reload/save (a reload discards
    unsaved in-memory edits; a save overwrites on-disk PBIR edits).

Unknown tools and unknown operations classify as ``write`` (fail safe).

A second, independent axis -- :func:`returns_data` (F-22) -- marks the operations that return model
ROW VALUES (DAX result rows, sample data, value distributions, drill rows, rendered pixels,
exports of results). ``security.allow_data_read=false`` refuses exactly those and leaves every
metadata operation working (``server/security_policy.py``).
``tests/test_review_a_op_classification.py`` pins that every operation in every
tool's schema enum (plus 02_Model_Operations' per-object op table) is covered
explicitly, so a new op cannot silently fall into the default.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Literal, Mapping, Optional

Level = Literal["read", "write", "destructive"]

READ: Level = "read"
WRITE: Level = "write"
DESTRUCTIVE: Level = "destructive"

_WRITE_LEVELS = frozenset({WRITE, DESTRUCTIVE})

# ---------------------------------------------------------------------------
# Default operation when the caller omits it (mirrors each schema's default /
# the handler's fallback). Pinned against the schemas by the test suite.
# ---------------------------------------------------------------------------
DEFAULT_OPERATION: Dict[str, str] = {
    "01_Connection": "detect",
    "02_Model_Operations": "list",
    "05_Column_Usage_Mapping": "get_unused_columns",
    "06_Analysis_Operations": "simple",
    "07_Bookmark_Operations": "list",
    "07_Page_Operations": "list",
    "07_Report_Operations": "info",
    "07_Theme_Operations": "get_theme",
    "07_Visual_Operations": "list",
    "09_Debug_Operations": "visual",
    "09_Validate": "cross_visual",
}

# Tools where every operation is a read.
_ALL_READ_TOOLS = frozenset(
    {
        "01_Connection",
        "04_Run_DAX",
        "04_Query_Operations",
        "05_DAX_Intelligence",
        "05_Column_Usage_Mapping",
        "06_Analysis_Operations",
        "07_PBIP_Operations",
        "08_Documentation_Word",
        "09_Profile",
        "09_Document",
        "10_Show_User_Guide",
        "13_Query_Plan",
        "16_Dependency_Graph",
    }
)

# Explicit op tables for the all-read tools (coverage pin; every value READ).
_ALL_READ_OPS: Dict[str, frozenset] = {
    "01_Connection": frozenset(
        {"detect", "connect", "reconnect", "ensure", "status", "connect_xmla", "connect_folder"}
    ),
    "04_Query_Operations": frozenset(
        {
            "data_sources",
            "m_expressions",
            "search_objects",
            "roles",
            "test_rls",
            "impersonate_run",
            "simulate_as_user",
            "vertipaq_report",
            "vertipaq_analyzer",
            "search_string",
            "dmv_probe",
            "list_dmv_probes",
            "info_view",
            "warm_cache",
            "rls_audit",
        }
    ),
    "05_DAX_Intelligence": frozenset(
        {
            "analyze",
            "dependencies",
            "impact",
            "export",
            "list_udfs",
            "lint_model",
            "expand_measure",
            "find_clones",
            "explain",
        }
    ),
    "05_Column_Usage_Mapping": frozenset(
        {
            "get_unused_columns",
            "get_unused_columns_pbip",
            "get_measures_for_tables",
            "get_columns_for_measure",
            "get_measures_for_column",
            "get_full_mapping",
            "export_to_csv",
            "export_measures",
        }
    ),
    "06_Analysis_Operations": frozenset({"simple", "full", "compare"}),
    "07_PBIP_Operations": frozenset(
        {
            "analyze",
            "validate_model",
            "compare_models",
            "generate_documentation",
            "query_dependencies",
            "query_measures",
            "query_relationships",
            "query_unused",
            "scan_broken_refs",
            "git_diff",
            "dependency_html",
            "aggregation_analysis",
            "validate_report",
            "preflight",
        }
    ),
    "09_Profile": frozenset(
        {
            "page",
            "filter_matrix",
            "decompose",
            "contribution",
            "trend",
            "root_cause",
            "export",
            "perf_analyzer_import",
            "regress",
        }
    ),
    "09_Document": frozenset({"page", "report", "measure_lineage", "filter_lineage"}),
    "13_Query_Plan": frozenset(
        {
            "analyze",
            "benchmark",
            "capture",
            "compare",
            "evaluate_and_log",
            "export_daxx",
            "list_rules",
            "parse",
            "plan_rows",
            "trace_start",
            "trace_fetch",
            "trace_stop",
        }
    ),
    "16_Dependency_Graph": frozenset(
        {
            "batch_impact",
            "build",
            "export",
            "find_cycles",
            "find_unused",
            "get_consumers",
            "get_dependencies",
            "get_downstream",
            "get_impact",
            "get_upstream",
            "query",
            "quick_lineage",
            "cleanup_plan",
        }
    ),
}

# Simple op → level tables for mixed tools.
_OP_TABLES: Dict[str, Dict[str, Level]] = {
    # Model assertions are reads; test_put / test_remove (and test_generate write=true) edit the
    # committed suite JSON file (N-19), so read_only_mode blocks them and the dispatcher serialises
    # them. tieout / equivalence only run queries (DEFINE MEASURE is query-scoped). test_run
    # (sidecar last-run) / test_export only write artifacts the caller asked for, like the
    # other export ops, and stay reads.
    "09_Validate": {
        "cross_visual": READ,
        "expected_value": READ,
        "filter_permutation": READ,
        "capture_baseline": READ,
        "compare_baseline": READ,
        "test_put": WRITE,
        "test_remove": WRITE,
        "test_list": READ,
        "test_run": READ,
        "test_export": READ,
        "tieout": READ,
        "equivalence": READ,
        # preview (default) is a read; write=true edits the suite JSON like test_put (see _classify_table)
        "test_generate": WRITE,
    },
    # Client masking: status / gateway_status only read; on/off/refresh change the masking state,
    # gateway_start/stop run the local gateway, route/unroute_folder write a project's
    # .claude/settings.local.json. Gateway `off` is never an op: only the user's typed command.
    "00_Client_Mode": {
        "status": READ,
        "on": WRITE,
        "off": WRITE,
        "refresh": WRITE,
        "gateway_status": READ,
        "gateway_start": WRITE,
        "gateway_stop": WRITE,
        "route_folder": WRITE,
        "unroute_folder": WRITE,
        "route_all": WRITE,
        "unroute_all": WRITE,
        "setup": READ,  # only returns the command + plan for the user; never installs anything
        "selftest": READ,  # offline replay of the recorded fixtures
    },
    "02_QA_Linguistic": {
        "load": READ,
        "audit": READ,
        "add_synonym": WRITE,
        "set_include_in_qa": WRITE,
        "remove_synonym": DESTRUCTIVE,
    },
    "07_Bookmark_Operations": {
        "list": READ,
        "get": READ,
        "analyze": READ,
        "create": WRITE,
        "rename": WRITE,
        "update": WRITE,
        "set_capture": WRITE,
        "set_affected_visuals": WRITE,
        "create_group": WRITE,
        "update_group": WRITE,
        "delete": DESTRUCTIVE,
        "delete_group": DESTRUCTIVE,
    },
    "07_Page_Operations": {
        "list": READ,
        "list_interactions": READ,
        "list_filters": READ,
        "create": WRITE,
        "clone": WRITE,
        "rename": WRITE,
        "reorder": WRITE,
        "resize": WRITE,
        "set_display": WRITE,
        "set_background": WRITE,
        "set_wallpaper": WRITE,
        "set_drillthrough": WRITE,
        "set_tooltip": WRITE,
        "hide": WRITE,
        "show": WRITE,
        "set_interaction": WRITE,
        "bulk_set_interactions": WRITE,
        "add_filter": WRITE,
        "set_filter": WRITE,
        "hide_filter": WRITE,
        "unhide_filter": WRITE,
        "lock_filter": WRITE,
        "unlock_filter": WRITE,
        "delete": DESTRUCTIVE,
        "clear_filters": DESTRUCTIVE,
    },
    "07_Report_Operations": {
        "info": READ,
        "measure_usage": READ,
        "discover_schema": READ,
        "audit_interactions": READ,
        "rename": WRITE,
        "rebind": WRITE,
        "backup": WRITE,
        "manage_extension_measures": WRITE,  # refined by sub_operation below
        "restore": DESTRUCTIVE,
    },
    "07_Theme_Operations": {
        "analyze_compliance": READ,
        "get_theme": READ,
        "list_text_classes": READ,
        "list_cf": READ,
        "export_theme": READ,
        "set_formatting": WRITE,
        "push_visual": WRITE,
        "set_font": WRITE,
        "add_cf": WRITE,
        "copy_cf": WRITE,
        "set_colors": DESTRUCTIVE,
        "apply_theme": DESTRUCTIVE,
        "import_theme": DESTRUCTIVE,
        "clear_visual_overrides": DESTRUCTIVE,
        "remove_cf": DESTRUCTIVE,
    },
    "07_Visual_Operations": {
        "list": READ,
        "get_raw_property": READ,
        "list_templates": READ,
        "get_template": READ,
        "audit_pane_collisions": READ,
        "get_roles_for_type": READ,
        "create": WRITE,
        "create_group": WRITE,
        # H-25: the non-destructive alternative to deleting a filter-layer
        # visual (top-level isHidden).
        "hide": WRITE,
        "unhide": WRITE,
        "update_position": WRITE,
        "update_visual_config": WRITE,
        "update_formatting": WRITE,
        "align": WRITE,
        "add_field": WRITE,
        "set_sort": WRITE,
        "set_action": WRITE,
        "inject_code": WRITE,
        "manage_visual_calcs": WRITE,  # refined by sub_operation below
        "configure_slicer": WRITE,
        "replace_measure": WRITE,
        "sync_visual": WRITE,
        "sync_column_widths": WRITE,
        "sync_formatting": WRITE,
        "set_raw_property": WRITE,
        "bulk_rename_pane_names": WRITE,
        "clear_default_selection": WRITE,
        "set_default_selection": WRITE,
        "delete": DESTRUCTIVE,
        "delete_raw_property": DESTRUCTIVE,
        "remove_field": DESTRUCTIVE,
    },
    "11_PBIP_Authoring": {
        "list_templates": READ,
        "get_template": READ,
        "pbir_resolve_container": READ,
        "clone_visual": WRITE,
        "clone_page": WRITE,
        "create_page": WRITE,
        "create_visual": WRITE,
        "create_visual_group": WRITE,
        "pbir_add_visuals": WRITE,
        "clone_report": DESTRUCTIVE,
        "delete_page": DESTRUCTIVE,
        "delete_visual": DESTRUCTIVE,
    },
    "15_DAX_Refactor": {
        "list_transforms": READ,
        "list_journals": READ,
        "preview": READ,
        "apply": DESTRUCTIVE,
        "rollback": DESTRUCTIVE,
    },
    "17_Desktop_Bridge": {
        "status": READ,
        "manifest": READ,
        "screenshot": READ,
        "screenshot_all": READ,
        "resolve_page": READ,
        "reload": DESTRUCTIVE,
        "reload_and_screenshot": DESTRUCTIVE,
        "save": DESTRUCTIVE,
        # J-32(2): launches a NEW Desktop on a project (never closes one).
        "open": WRITE,
    },
}

# 02_Model_Operations: per (object_type, operation). Built from the handler's
# _valid_operations table (pinned by the test suite).
_MODEL_READ_OPS = frozenset(
    {
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
        # MP-01 / RF-01: static reads of role definitions and refresh state
        "get_effective_permissions",
        "refresh_status",
        "refresh_history",
        # JR-01: journal reads; checkpoint_create only writes a snapshot file under output/
        "history",
        "diff",
        "checkpoint_create",
        # calc-group migration helpers only emit TMDL / candidates
        "detect",
        "scaffold",
        "list_patterns",
        "daxlib_search",
        # F-04 TOM property reflection reads; F-20d translation export / coverage
        "list_properties",
        "get_property",
        "export_translations",
        "translation_coverage",
    }
)
_MODEL_DESTRUCTIVE_OPS = frozenset(
    {
        "delete",
        "safe_delete",
        "rename",
        "safe_rename",
        "refresh",
        "move",
        "delete_item",
        "remove",
        "remove_table",
        "remove_measure",
        "remove_column",
        "remove_hierarchy",
        "remove_member",
        "remove_field",
        # MP-01: drops a hierarchy level
        "remove_level",
        "clear_kpi",
        "clear_refresh_policy",
        "clear_table_filter",
    }
)
_MODEL_WRITE_OPS = frozenset(
    {
        "create",
        "upsert",
        "update",
        "generate_calendar",
        "set_refresh_policy",
        "set_format_string_expression",
        "set_detail_rows_expression",
        "set_kpi",
        "activate",
        "deactivate",
        "add_item",
        "update_item",
        "set_precedence",
        "set_mode",
        "add_table",
        "add_measure",
        "add_column",
        "add_hierarchy",
        "set_translation",
        "bulk_set_translations",
        "set",
        "set_value_filter_behavior",
        "set_table_filter",
        "add_member",
        "set_parameter_metadata",
        "repair",
        "add_field",
        "reorder_fields",
        "daxlib_install",
        "set_members",
        # JR-01: re-applies journalled post-images (a preview is a read, see _classify_model)
        "replay",
        # M4 parity pack
        "set_properties",
        "mark_as_date_table",
        "add_level",
        "rename_level",
        "assign",
        "unassign",
        # F-04 / F-20d (dry_run=true is a read, see _classify_model)
        "set_property",
        "import_translations",
    }
)
# JR-01: undo / checkpoint_restore revert or remove objects -> destructive (preview = read).
_MODEL_JOURNAL_APPLY_OPS = frozenset({"undo", "replay", "checkpoint_restore"})

_TMDL_DRY_RUN_DEFAULT_TRUE = frozenset({"find_replace", "bulk_rename", "migrate_measures"})
_TMDL_TABLE: Dict[str, Level] = {
    "export": READ,
    "generate_script": READ,
    "find_replace": WRITE,
    "format_dax": WRITE,
    "bulk_rename": DESTRUCTIVE,
    "migrate_measures": DESTRUCTIVE,
}

_BATCH_DESTRUCTIVE = frozenset({"delete", "rename", "move", "refresh"})
_BATCH_WRITE = frozenset({"create", "update", "move_display_folder", "activate", "deactivate"})

# A-06 (WP9): every write op of these PBIR authoring tools runs inside
# ``gate_and_wrap``'s ``safe_io.write_scope``; ``dry_run=true`` intercepts
# every write / delete / copy / move and returns the planned changes, so a
# dry-run call touches nothing and classifies as a READ.
_DRY_RUN_TOOLS = frozenset(
    {
        "07_Bookmark_Operations",
        "07_Page_Operations",
        "07_Report_Operations",
        "07_Theme_Operations",
        "07_Visual_Operations",
        "11_PBIP_Authoring",
    }
)

_SUBOP_LEVELS: Dict[str, Level] = {
    "list": READ,
    "add": WRITE,
    "update": WRITE,
    "delete": DESTRUCTIVE,
}


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _op(tool: str, args: Mapping[str, Any]) -> Optional[str]:
    op = args.get("operation")
    if isinstance(op, str) and op:
        return op
    return DEFAULT_OPERATION.get(tool)


def wants_preview(args: Mapping[str, Any]) -> bool:
    """F-01: ``preview=true`` (alias ``dry_run=true``) at the top level of a call."""
    return _truthy(args.get("preview")) or _truthy(args.get("dry_run"))


def _classify_model(args: Mapping[str, Any]) -> Optional[Level]:
    level = _classify_model_ops(args)
    # F-01: a preview of ANY live-TOM write runs with the save suppressed and rolled back, so it is a
    # READ. The handler guarantees it: a preview is never executed as a real write
    # (``handle_model_operations`` intercepts it before the gate).
    if level in _WRITE_LEVELS and wants_preview(args):
        return READ
    return level


def _classify_model_ops(args: Mapping[str, Any]) -> Optional[Level]:
    op = _op("02_Model_Operations", args) or "list"
    if op in _MODEL_READ_OPS:
        return READ
    # C-41: a rename preview only dry-runs the cascade.
    if op in {"rename", "safe_rename"} and args.get("preview") is True:
        return READ
    # F-04 / F-20d: validate-and-report only.
    if op in {"set_property", "import_translations"} and (
        _truthy(args.get("dry_run")) or _truthy(args.get("preview"))
    ):
        return READ
    if op in _MODEL_JOURNAL_APPLY_OPS and _truthy(args.get("preview")):
        return READ
    if op in {"undo", "checkpoint_restore"}:
        return DESTRUCTIVE
    # MP-01: raising the compatibility level cannot be undone.
    if op == "set_properties" and args.get("compatibility_level") is not None:
        return DESTRUCTIVE
    if op in _MODEL_DESTRUCTIVE_OPS:
        return DESTRUCTIVE
    # C-42: perspective set_members removes memberships with member_action=remove.
    if op == "set_members":
        action = str(args.get("member_action") or "add").lower()
        return DESTRUCTIVE if action == "remove" else WRITE
    if op in _MODEL_WRITE_OPS:
        return WRITE
    return None


def _classify_tmdl(args: Mapping[str, Any]) -> Optional[Level]:
    op = _op("02_TMDL_Operations", args)
    if op is None or op not in _TMDL_TABLE:
        return None
    level = _TMDL_TABLE[op]
    if level == READ:
        return READ
    # find_replace / bulk_rename / migrate_measures preview by default
    # (handler: args.get('dry_run', True)); format_dax writes by default.
    default_dry = op in _TMDL_DRY_RUN_DEFAULT_TRUE
    dry = args.get("dry_run", default_dry) if "dry_run" in args else default_dry
    if _truthy(dry):
        return READ
    return level


def _classify_batch(args: Mapping[str, Any]) -> Optional[Level]:
    level = _classify_batch_ops(args)
    # F-01: ``preview=true`` / ``dry_run=true`` (top level) runs the batch with the save suppressed
    # and rolled back (``handle_batch_operations`` intercepts it): a READ. changeset / patch keep
    # their own preview semantics (classified in ``_classify_batch_ops``).
    if level in _WRITE_LEVELS and wants_preview(args) and not _has_own_preview(args):
        return READ
    return level


def _has_own_preview(args: Mapping[str, Any]) -> bool:
    object_type = args.get("object_type") or args.get("operation")
    return (
        object_type in ("changeset", "patch")
        or args.get("changeset") is not None
        or args.get("from_bpa") is not None
    )


def _classify_batch_ops(args: Mapping[str, Any]) -> Optional[Level]:
    object_type = args.get("object_type") or args.get("operation")
    batch_op = args.get("batch_operation") or ""
    if object_type == "ti_suite" or batch_op == "generate_ti_suite":
        return WRITE if _truthy(args.get("apply")) else READ
    if object_type == "changeset" or args.get("changeset") is not None:
        if _truthy(args.get("preview")):
            return READ
        doc = args.get("changeset") or {}
        has_delete = isinstance(doc, dict) and any(
            isinstance(ops, dict) and ops.get("delete") for ops in doc.values()
        )
        return DESTRUCTIVE if has_delete else WRITE
    if object_type == "patch" or args.get("from_bpa") is not None:
        # F-03: select-and-patch / BPA FixExpression apply; preview is read-only
        return READ if _truthy(args.get("preview")) else DESTRUCTIVE
    options = args.get("options") or {}
    if isinstance(options, dict) and _truthy(options.get("dry_run")):
        return READ
    if batch_op in _BATCH_DESTRUCTIVE:
        return DESTRUCTIVE
    if batch_op in _BATCH_WRITE:
        return WRITE
    return None


def _classify_debug(args: Mapping[str, Any]) -> Optional[Level]:
    op = _op("09_Debug_Operations", args)
    if op == "debug_loop":
        strategy = str(args.get("fix_strategy") or "ask_user")
        return DESTRUCTIVE if strategy in {"live_tom", "tmdl_edit"} else READ
    if op == "compare" and str(args.get("mode") or "").lower() == "live_swap":
        # F-08: writes the candidate into the live model (and restores it) unless it is a dry run.
        return READ if _truthy(args.get("dry_run")) else DESTRUCTIVE
    if op in {
        "visual",
        "compare",
        "drill",
        "analyze",
        "debug_variable",
        "step_variables",
        "run_dax",
        "optimize",
        "audit",
        "set_path",
        "status",
    }:
        return READ
    return None


def _classify_table(tool: str, args: Mapping[str, Any]) -> Optional[Level]:
    table = _OP_TABLES[tool]
    op = _op(tool, args)
    if op is None or op not in table:
        return None
    level = table[op]
    if tool == "07_Report_Operations" and op == "manage_extension_measures":
        level = _SUBOP_LEVELS.get(str(args.get("sub_operation") or ""), WRITE)
    elif tool == "07_Visual_Operations" and op == "manage_visual_calcs":
        level = _SUBOP_LEVELS.get(str(args.get("sub_operation") or "list"), WRITE)
    elif tool == "09_Validate" and op == "test_generate":
        level = WRITE if _truthy(args.get("write")) else READ
    if level != READ and tool in _DRY_RUN_TOOLS and _truthy(args.get("dry_run")):
        return READ
    return level


_SPECIAL: Dict[str, Callable[[Mapping[str, Any]], Optional[Level]]] = {
    "02_Model_Operations": _classify_model,
    "02_TMDL_Operations": _classify_tmdl,
    "03_Batch_Operations": _classify_batch,
    "09_Debug_Operations": _classify_debug,
}


def classify_known(tool: str, args: Optional[Mapping[str, Any]]) -> Optional[Level]:
    """Classify ``(tool, args)`` from the explicit tables; None when not covered."""
    args = args if isinstance(args, Mapping) else {}
    if tool == "09_Profile" and _truthy(args.get("record")) and _op(tool, args) == "page":
        return WRITE  # F-18: record=true appends to output/perf/<project>.jsonl (a history file)
    if tool in _ALL_READ_TOOLS:
        ops = _ALL_READ_OPS.get(tool)
        if ops is None:
            return READ
        op = _op(tool, args)
        if op is None or op in ops:
            return READ
        return None
    special = _SPECIAL.get(tool)
    if special is not None:
        return special(args)
    if tool in _OP_TABLES:
        return _classify_table(tool, args)
    return None


# ---------------------------------------------------------------------------
# F-22: the ``returns_data`` axis. An operation "returns data" when its result can carry VALUES from
# the model's tables (not names, definitions, counts of objects or timings): query result rows,
# sampled / distinct column values, per-slice measure values, rendered visuals, result exports.
# Aggregate-only ops stay metadata: ``02 column statistics`` returns DistinctCount / TotalCount /
# BlankCount and no value; ``04_Query_Operations rls_audit`` returns visible-vs-unfiltered ROW
# COUNTS; ``vertipaq_*`` return sizes and cardinalities. Audited 2026-10-03; pinned by
# tests/test_f22_security_policy.py (every op named here exists in the registry).
# ---------------------------------------------------------------------------
_DATA_OPS: Dict[str, frozenset] = {
    # every op of these tools runs DAX and hands the rows back
    "04_Run_DAX": frozenset({"*"}),
    "04_Query_Operations": frozenset({"impersonate_run", "simulate_as_user", "test_rls"}),
    "09_Debug_Operations": frozenset(
        {
            "visual",
            "compare",
            "drill",
            "analyze",
            "debug_variable",
            "step_variables",
            "run_dax",
            "optimize",
            "debug_loop",
        }
    ),
    "09_Validate": frozenset(
        {
            "cross_visual",
            "expected_value",
            "filter_permutation",
            "capture_baseline",
            "compare_baseline",
            "tieout",
            "equivalence",
            "test_run",
            "test_export",
            "test_generate",
        }
    ),
    "09_Profile": frozenset({"filter_matrix", "decompose", "contribution", "trend", "root_cause"}),
    "13_Query_Plan": frozenset({"evaluate_and_log"}),
    # a screenshot IS the data as rendered
    "17_Desktop_Bridge": frozenset({"screenshot", "screenshot_all", "reload_and_screenshot"}),
}

#: 02_Model_Operations ``(object_type, operation)`` pairs that return column / table values.
_DATA_MODEL_OPS = frozenset({("table", "sample_data"), ("column", "distribution")})


#: Tools whose write previews temporarily mutate the model (commit suppressed, then rolled back).
_PREVIEW_TOOLS = frozenset({"02_Model_Operations", "03_Batch_Operations"})


def is_model_preview(tool: str, args: Optional[Mapping[str, Any]]) -> bool:
    """True for a ``preview=true`` call of a live-TOM WRITE (F-01): classified READ, yet it must run
    exclusively like a write because it edits the in-memory model before rolling it back."""
    if tool not in _PREVIEW_TOOLS or not isinstance(args, Mapping) or not wants_preview(args):
        return False
    stripped = {k: v for k, v in args.items() if k not in ("preview", "dry_run")}
    return classify(tool, stripped) in _WRITE_LEVELS


def returns_data(tool: str, args: Optional[Mapping[str, Any]]) -> bool:
    """True when this call can return values from the model's data (F-22).

    Independent of the read / write level: ``04_Run_DAX`` is a read AND returns data. Decided from
    the canonical (alias-folded) args; an op not named in the tables is metadata-only.
    """
    args = args if isinstance(args, Mapping) else {}
    if tool == "02_Model_Operations":
        op = _op(tool, args) or "list"
        return (str(args.get("object_type") or ""), op) in _DATA_MODEL_OPS
    if tool == "04_Run_DAX":
        mode = str(args.get("mode") or "auto").lower()
        if mode == "validate":
            return False  # engine parse / bind, nothing executed
        if mode == "build" and not _truthy(args.get("execute")):
            return False  # returns the generated query text only
        return True
    if tool == "05_DAX_Intelligence":
        return _truthy(args.get("include_values"))  # explain: VAR values via step_variables
    ops = _DATA_OPS.get(tool)
    if ops is None:
        return False
    op = _op(tool, args)
    if op is None:
        return False
    if tool == "09_Debug_Operations" and op == "compare" and _truthy(args.get("dry_run")):
        return False
    return "*" in ops or op in ops


def data_ops() -> Dict[str, frozenset]:
    """The op-level table (for the coverage pin); arg-level cases are in :func:`returns_data`."""
    return dict(_DATA_OPS)


def classify(tool: str, args: Optional[Mapping[str, Any]]) -> Level:
    """Return ``"read"``, ``"write"`` or ``"destructive"`` for one call.

    Unknown tools / operations → ``"write"`` (fail safe: blocked in read-only
    mode, serialised, reported as outcome-unknown on timeout).
    """
    level = classify_known(tool, args)
    return level if level is not None else WRITE


def is_write(level: str) -> bool:
    """True for ``write`` and ``destructive``."""
    return level in _WRITE_LEVELS


def op_label(tool: str, args: Optional[Mapping[str, Any]]) -> str:
    """Human/diagnostic label ``<tool>:<op>`` (``02_Model_Operations:measure/update``)."""
    args = args if isinstance(args, Mapping) else {}
    if tool == "02_Model_Operations":
        return f"{tool}:{args.get('object_type') or '?'}/{_op(tool, args) or '?'}"
    if tool == "03_Batch_Operations":
        obj = args.get("object_type") or args.get("operation") or "?"
        bop = args.get("batch_operation")
        return f"{tool}:{obj}/{bop}" if bop else f"{tool}:{obj}"
    op = _op(tool, args)
    return f"{tool}:{op}" if op else tool


def tool_levels(tool: str) -> frozenset:
    """Every level any operation of ``tool`` can produce (annotation consistency)."""
    if tool == "09_Profile":
        return frozenset({READ, WRITE})  # page record=true writes the perf history
    if tool in _ALL_READ_TOOLS:
        return frozenset({READ})
    if tool in _OP_TABLES:
        levels = set(_OP_TABLES[tool].values())
        if tool in {"07_Report_Operations", "07_Visual_Operations"}:
            levels |= set(_SUBOP_LEVELS.values())
        return frozenset(levels)
    if tool == "02_Model_Operations":
        return frozenset({READ, WRITE, DESTRUCTIVE})
    if tool == "02_TMDL_Operations":
        return frozenset(set(_TMDL_TABLE.values()))
    if tool == "03_Batch_Operations":
        return frozenset({READ, WRITE, DESTRUCTIVE})
    if tool == "09_Debug_Operations":
        return frozenset({READ, DESTRUCTIVE})
    return frozenset({WRITE})
