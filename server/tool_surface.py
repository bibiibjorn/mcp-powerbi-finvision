"""Which arguments each tool advertises in ``tools/list`` (L-10 / K-18).

The big dispatcher tools accept far more arguments than a typical call needs:
02_Model_Operations alone had 114 properties, most of them for rare
object-type/operation pairs (refresh policies, calendar generation,
translations, perspectives, annotations, daxlib, field-parameter surgery...).
Every one was paid for in every session's ``tools/list``.

An argument listed here is DEFERRED: it stays in the tool's registered schema
(so the input validator type-checks it and the dispatcher never reports it as
``unknown_args``), and ``10_Show_User_Guide tool=<name>`` documents it, but it
is left out of the ``tools/list`` payload. Nothing stops working: a caller can
pass a deferred argument exactly as before.

Rule for adding an entry: the argument only serves an operation the tool's
description does not lead with, and the tool description says where to look
(``10_Show_User_Guide tool=<name>``). Arguments used by a TOOL_EXAMPLES call or
by a ``required`` list may never be deferred (pinned by
tests/test_w3_api_tool_surface.py).
"""

from __future__ import annotations

from typing import Dict, FrozenSet

__all__ = ["DEFERRED_ARGS", "deferred_args"]


def _names(text: str) -> FrozenSet[str]:
    return frozenset(text.split())


DEFERRED_ARGS: Dict[str, FrozenSet[str]] = {
    "02_Model_Operations": _names(
        # table set_refresh_policy / describe paging / generate_calendar
        "rolling_window_granularity rolling_window_periods incremental_granularity "
        "incremental_periods source_expression columns_page_size measures_page_size "
        "relationships_page_size start_year end_year include_fiscal fiscal_start_month "
        # column list / update / statistics / sample_data details
        "column_type clear_sort_by_column data_category is_key is_nullable top_n order_by "
        "order_direction "
        # relationship details
        "allow_ambiguous security_filtering_behavior rely_on_referential_integrity "
        "join_on_date_behavior active_only "
        # calculation_group detect / scaffold, calc-item details
        "patterns min_pattern_size candidate calc_group_table_name date_column "
        "format_string_expression ordinal "
        # partition / hierarchy / field-parameter surgery
        "partition_name refresh_after mode hierarchy_name levels clone_from extra_columns "
        "field label labels_in_order "
        # daxlib / perspective / culture / named expression / role members / annotation
        "package_id prefix force query limit perspective_name members member_action "
        "culture_name object_type_target property_name value translations expression_name "
        "member_identifier member_type tenant_id target_type annotation_name annotation_value "
        # model properties / import-table source
        "behavior m_expression "
        # model write journal (history / diff / undo / replay / checkpoints)
        "entry_id entry_ids count since object unflushed_only journal checkpoint "
        # parity pack: model set_properties, hierarchy levels, query groups, Sep-2026 indexing
        "culture discourage_implicit_measures compatibility_level allow_compatibility_upgrade "
        "level_name query_group string_indexing_behavior full_text_indexing_behavior "
        # CAL-01 calendars
        "calendar_name groups time_related_columns "
        # F-04 any set_property, F-20c/d/e: selection expressions, translation files, calendar templates
        "parameters items preview_hash include cross_filtering_behavior "
        "ref dry_run plan_hash file_format input_path output_path calendar_template "
        "multiple_or_empty_selection_expression no_selection_expression "
        "selection_expression_behavior "
        # F-27 idempotency key (dispatch replays a recorded write outcome)
        "request_id"
    ),
    "02_TMDL_Operations": _names(
        "time_budget_s max_line_length on_error include_strings_and_comments "
        "display_folder_filter replace_target skip_duplicates case_sensitive request_id "
        "engine skip_space_after_function"
    ),
    "03_Batch_Operations": _names(
        # ti_suite + the deprecated `measures` alias
        "base_measures date_column variants target_table display_folder name_pattern "
        "format_string apply measures request_id dry_run select patch from_bpa"
    ),
    # F-20a: 04_Run_DAX mode=build query-builder inputs
    "04_Run_DAX": _names("measures group_by filters order_by execute queries stop_on_error"),
    "04_Query_Operations": _names(
        "include_table_rollup include_verdict include_stats custom_data test_table "
        "search_in_expression search_in_name types export_path probe_identities"
    ),
    "05_Column_Usage_Mapping": _names(
        "include_reasons group_by include_dax force_refresh output_path"
    ),
    "05_DAX_Intelligence": _names(
        "skip_validation include_optimization include_profiling breakpoints include_diagram "
        "html auto_open output_path min_severity rule_ids tables max_findings suppress_rules "
        "inline min_tokens min_similarity min_group_size include_fragments include_near "
        "max_groups include_values filters page_name visual_id visual_name include_slicers "
        "max_rows table_name"
    ),
    "06_Analysis_Operations": _names(
        "table measure_name max_results active_only max_seconds rules_path "
        "include_vertipaq issues_max generate_json output_path "
        # F-17 compare old/new targets; audit include_runtime
        "old new report include_runtime"
    ),
    "07_Bookmark_Operations": _names(
        "group_id bookmark_ids delete_children auto_open output_path request_id"
    ),
    "07_PBIP_Operations": _names(
        "from_ref to_ref base_branch include_reasons tom_check display_folder pattern "
        "expression_search main_item include_visual_details page_filter "
        # F-06 validate_report / preflight
        "level report_author_cli git_base include_unstaged max_findings"
    ),
    "07_Page_Operations": _names(
        "insert_after page_order display_option transparency clear enabled "
        "include_visual_info interactions replace_all filter_names confirm_widen value_type "
        "operator top_n top_direction by_table by_field request_id"
    ),
    "07_Report_Operations": _names(
        "max_visuals_per_page output_format export_path model_id message sub_operation "
        "measure_name expression table_ref data_type format_string description force request_id "
        # F-12 audit_interactions
        "policy"
    ),
    "07_Theme_Operations": _names(
        "selector_metadata source_page source_visual target_page target_visual keep_cf "
        "scope source theme_name request_id"
    ),
    "07_Visual_Operations": _names(
        # raw-op fine print, batch config, group / align, field details
        "force_type create_missing allow_null config_updates position_frame formatting "
        "delete_children group_name visual_names alignment direction hierarchy_level "
        "sort_field_type "
        # set_action DataFunction, inject_code, visual calcs
        "data_function_id data_function_workspace_id data_function_name power_fx code_type "
        "code provider sub_operation calc_name expression "
        # slicer audit / rename / default-selection details
        "display_name_filter include_no_title strategy custom_overrides selection_mode "
        "entity property "
        # replace_measure + sync_* ops
        "source_entity source_property target_entity target_property new_display_name "
        "source_visual_name source_page target_visual_name target_page target_display_title "
        "target_visual_type target_pages sync_position sync_children formatting_types "
        "confirm_bulk_sync request_id"
    ),
    "09_Debug_Operations": _names(
        # optimize: the flat timing block (trace_id supersedes it) + opt-ins
        "total_ms fe_ms se_ms se_cpu_ms fe_pct se_pct se_queries se_cache_hits "
        "se_parallelism se_events cache_comparison per_measure_trace "
        # debug_loop
        "expected max_iterations take_screenshot fix_strategy allow_llm_fix screenshot_dir "
        "wait_timeout_seconds "
        # drill / debug_variable / compare / visual fine print / audit
        "fact_table limit variable_name max_rows equivalence_grain runs warmup "
        "reference_query pa_export_path use_reference_query compact skip_types "
        "skip_auto_filters tolerance execute_measure effective_user_name "
        # F-08 compare mode=live_swap; F-02 visual from_trace
        "mode patch dry_run from_trace"
    ),
    "09_Profile": _names(
        "schedule warm_pass simulate_concurrency per_visual_limit filter_columns "
        "max_combinations dimensions baseline_filters comparison_filters granularity "
        "date_column top_n output_format record label baseline candidate project"
    ),
    "09_Validate": _names(
        "max_permutations suite_path test test_id ids tags replace record output_path output_format "
        # tieout / test_generate / equivalence (F-09, F-10, F-14)
        "mode page_a page_b measure_a measure_b by non_additive relative_tolerance max_rows "
        "source write accept_changes include_perf include_tieout headroom heavy_ms snapshot_rows "
        "max_visuals runs roles as_user expression_a expression_b candidate_expression "
        "grid_columns date_column grains max_cells host_table"
    ),
    "01_Connection": _names("workspace model pbip_path allow_open_in_desktop confirm_destructive"),
    # F-27 idempotency key on the writer tools that declare their schema inline
    "02_QA_Linguistic": _names("request_id"),
    "15_DAX_Refactor": _names("request_id"),
    "11_PBIP_Authoring": _names(
        "target_path new_report_name insert_after position_frame group_name formatting "
        "delete_children container_x container_y timeout request_id "
        # clone_visual
        "target_page offset group_mode inherit_interactions interaction_map copy_bookmark_refs"
    ),
    "13_Query_Plan": _names(
        "include_xmsql cold_cache warmup max_duration_s max_events since include_text slowest_first label "
        "match pbip_path page_name"
    ),
    "16_Dependency_Graph": _names(
        "all_bound_reports cache cascade allow_model_only kinds jsonpath output_path "
        "transitive max_stages"
    ),
    "17_Desktop_Bridge": _names(
        "vision_tier report_root crops_dir dirty_sources allow_duplicate timeout_seconds "
        "wait_seconds timeout save_timeout optimize output_dir include_model request_id"
    ),
}


def deferred_args(tool_name: str) -> FrozenSet[str]:
    """Arguments of ``tool_name`` that tools/list does not advertise."""
    return DEFERRED_ARGS.get(tool_name, frozenset())
