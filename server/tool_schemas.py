"""
Tool Input Schemas for Bridged Tools
Defines proper input schemas with required parameters.
"""

TOOL_SCHEMAS = {
    # DAX Intelligence (1 unified tool)
    "dax_intelligence": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": (
                    "DAX to analyse; an exact measure name analyses that measure (near-misses "
                    "are suggested, never substituted)."
                ),
                "x-doc": "DAX expression to analyse. If it EXACTLY names a measure (case-insensitive, optionally as [Name]) that measure's expression is analysed instead; a non-exact name returns suggestions, never a fuzzy substitute. Not required for dependency operations.",
            },
            "measure_name": {
                "type": "string",
                "description": "Analyse (or expand) this model measure by exact, case-insensitive name. Preferred over passing a name in expression.",
            },
            "analysis_mode": {
                "type": "string",
                "enum": ["all", "analyze", "debug", "report"],
                "default": "all",
            },
            "skip_validation": {"type": "boolean", "default": False},
            "output_format": {
                "type": "string",
                "enum": ["friendly", "steps"],
                "default": "friendly",
            },
            "include_optimization": {"type": "boolean", "default": True},
            "include_profiling": {"type": "boolean", "default": True},
            "breakpoints": {
                "type": "array",
                "description": "Char positions for debugging",
                "items": {"type": "integer"},
            },
            "operation": {
                "type": "string",
                "enum": [
                    "analyze",
                    "dependencies",
                    "impact",
                    "export",
                    "list_udfs",
                    "lint_model",
                    "expand_measure",
                    "find_clones",
                    "explain",
                ],
                "description": (
                    "analyze (default) | dependencies | impact | export | list_udfs | "
                    "lint_model | expand_measure | find_clones | explain."
                ),
                "x-doc": "analyze (default when omitted) = expression analysis per analysis_mode. Dependency/model operations ignore analysis_mode. lint_model=analyze every measure in one call (filters: min_severity, rule_ids, tables, max_findings, suppress_rules); expand_measure=emit DEFINE query inlining transitive deps (measure_name or expression=measure name; inline=true returns one CALCULATE-inlined expression).",
            },
            "table": {"type": "string", "description": "Table name (for dependencies/impact)"},
            "measure": {"type": "string", "description": "Measure name (for dependencies/impact)"},
            # F-15 find_clones
            "min_tokens": {
                "type": "integer",
                "default": 12,
                "description": "find_clones: min tokens",
            },
            "min_similarity": {
                "type": "number",
                "default": 0.85,
                "description": "find_clones: near-clone threshold (0-1)",
            },
            "min_group_size": {"type": "integer", "default": 2, "description": "find_clones"},
            "include_fragments": {"type": "boolean", "default": True, "description": "find_clones"},
            "include_near": {"type": "boolean", "default": True, "description": "find_clones"},
            "max_groups": {"type": "integer", "default": 50, "description": "find_clones"},
            # F-33 explain (include_values runs step_variables at a filter context)
            "include_values": {
                "type": "boolean",
                "default": False,
                "description": "explain: also evaluate the VARs (needs a connection)",
            },
            "filters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "explain include_values: DAX filter predicates",
            },
            "page_name": {"type": "string", "description": "explain include_values: page"},
            "visual_id": {"type": "string", "description": "explain include_values: visual id"},
            "visual_name": {"type": "string", "description": "explain include_values: visual"},
            "include_slicers": {"type": "boolean", "description": "explain include_values"},
            "max_rows": {"type": "integer", "description": "explain include_values"},
            "table_name": {"type": "string", "description": "explain include_values: table"},
            "include_diagram": {
                "type": "boolean",
                "description": "Include Mermaid diagram text (for dependencies)",
                "default": True,
            },
            "html": {
                "type": "boolean",
                "description": "dependencies: also write the interactive HTML diagram (slow on large models — recomputes every measure's dependencies). Default false.",
                "default": False,
            },
            "auto_open": {
                "type": "boolean",
                "description": "dependencies with html=true: open the HTML file in a browser. Default false.",
                "default": False,
            },
            "output_path": {"type": "string", "description": "CSV output path (for export)"},
            "min_severity": {
                "type": "string",
                "enum": ["critical", "high", "medium", "low", "info"],
                "description": "lint_model: keep findings at this severity or worse",
            },
            "rule_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "lint_model: keep only these rule ids",
            },
            "tables": {
                "type": "array",
                "items": {"type": "string"},
                "description": "lint_model: only measures hosted in these tables",
            },
            "max_findings": {
                "type": "integer",
                "description": "lint_model: cap on returned findings (default 200; omitted count reported)",
            },
            "suppress_rules": {
                "type": "array",
                "items": {"type": "string"},
                "description": "lint_model: rule ids to drop model-wide (accepted findings). Per measure use a '// lint-disable RULE_ID' comment.",
            },
            "inline": {
                "type": "boolean",
                "description": "expand_measure: inline dependencies as CALCULATE(<body>) instead of DEFINE MEASURE",
                "default": False,
            },
        },
        "required": [],
    },
    # PBIP Dependency Analysis (1 tool)
    # Report Operations (replaces report_info + new report-level ops)
    "report_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "info",
                    "measure_usage",
                    "rename",
                    "rebind",
                    "backup",
                    "restore",
                    "discover_schema",
                    "manage_extension_measures",
                    "audit_interactions",
                ],
                "default": "info",
            },
            "pbip_path": {
                "type": "string",
                "description": "Path to PBIP project or .Report folder",
            },
            "include_visuals": {"type": "boolean", "default": True},
            "include_filters": {"type": "boolean", "default": True},
            "page_name": {"type": "string", "description": "Filter by page name"},
            "summary_only": {"type": "boolean", "default": True},
            "max_visuals_per_page": {"type": "integer", "default": 50},
            "measure_filter": {"type": "string", "description": "Filter by measure name"},
            "output_format": {"type": "string", "enum": ["text", "json"], "default": "text"},
            "export_path": {"type": "string", "description": "CSV export directory"},
            "new_name": {"type": "string", "description": "New report name (rename)"},
            "model_path": {"type": "string", "description": "Semantic model path (rebind)"},
            "model_id": {"type": "string", "description": "Semantic model GUID (rebind)"},
            "message": {"type": "string", "description": "Backup message"},
            "backup_path": {"type": "string", "description": "Backup path (restore)"},
            "visual_type": {"type": "string", "description": "Visual type to discover schema for"},
            "sub_operation": {
                "type": "string",
                "enum": ["list", "add", "update", "delete"],
                "description": "Extension measure sub-op",
            },
            "measure_name": {"type": "string", "description": "Extension measure name"},
            "expression": {"type": "string", "description": "DAX expression"},
            "table_ref": {"type": "string", "description": "Table reference for measure"},
            "data_type": {"type": "string", "default": "double"},
            "format_string": {"type": "string", "description": "Format string"},
            "description": {"type": "string", "description": "Measure description"},
            "force": {
                "type": "boolean",
                "description": "Extension delete: delete even when visuals reference it",
            },
            "dry_run": {"type": "boolean", "description": "Preview any write"},
            "policy": {
                "type": "object",
                "description": "audit_interactions: isolations the report should have {isolate_visuals, isolate_visual_types, isolate}.",
            },
        },
        "required": ["pbip_path"],
    },
    # Page Operations (new consolidated tool)
    "page_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "list",
                    "create",
                    "clone",
                    "rename",
                    "delete",
                    "reorder",
                    "resize",
                    "set_display",
                    "set_background",
                    "set_wallpaper",
                    "set_drillthrough",
                    "set_tooltip",
                    "hide",
                    "show",
                    "set_interaction",
                    "bulk_set_interactions",
                    "list_interactions",
                    "list_filters",
                    "add_filter",
                    "set_filter",
                    "clear_filters",
                    "hide_filter",
                    "unhide_filter",
                    "lock_filter",
                    "unlock_filter",
                ],
                "default": "list",
            },
            "pbip_path": {"type": "string", "description": "Path to PBIP/Report folder"},
            "page_name": {"type": "string", "description": "Page id or display name"},
            "width": {"type": "integer", "description": "Page width in pixels"},
            "height": {"type": "integer", "description": "Page height in pixels"},
            "insert_after": {"type": "string", "description": "Page ID to insert after"},
            "source_page": {"type": "string", "description": "Source page for clone"},
            "new_display_name": {
                "type": "string",
                "description": "New display name (clone, rename)",
            },
            "page_order": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered page IDs/names (reorder)",
            },
            "display_option": {"type": "string", "enum": ["FitToPage", "FitToWidth", "ActualSize"]},
            "color": {"type": "string", "description": "Color hex value (e.g. #E6E6E6)"},
            "transparency": {"type": "number", "description": "Transparency 0-100"},
            "table": {"type": "string", "description": "Table name (drillthrough filter)"},
            "field": {"type": "string", "description": "Field name (drillthrough filter)"},
            "clear": {"type": "boolean", "description": "Clear drillthrough filters"},
            "hidden": {"type": "boolean", "description": "Hide/show page"},
            "enabled": {"type": "boolean", "description": "set_tooltip: false = normal page"},
            "dry_run": {"type": "boolean", "description": "Preview any write"},
            "summary_only": {"type": "boolean", "default": True},
            "source_visual": {"type": "string", "description": "Source visual (interactions)"},
            "target_visual": {"type": "string", "description": "Target visual (interactions)"},
            "interaction_type": {
                "type": "string",
                "enum": [
                    "NoFilter",
                    "DataFilter",
                    "HighlightFilter",
                    "Default",
                    "Filter",
                    "Highlight",
                ],
            },
            "include_visual_info": {"type": "boolean", "default": True},
            "interactions": {
                "type": "array",
                "description": "Bulk interactions [{source, target, type}]",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "type": {"type": "string"},
                    },
                    "required": ["source", "target", "type"],
                },
            },
            "replace_all": {"type": "boolean", "default": False},
            # Filter-specific params (list_filters/add_filter/set_filter/clear_filters/hide_filter/unhide_filter/lock_filter/unlock_filter)
            "level": {
                "type": "string",
                "enum": ["report", "page", "visual", "all"],
                "default": "all",
                "description": "Filter scope level",
            },
            "visual_name": {
                "type": "string",
                "description": "Visual name/ID (filter visual level)",
            },
            "filter_name": {"type": "string", "description": "Filter name/ID (set/hide/lock)"},
            "filter_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "clear_filters: only these filter names",
            },
            "filter_type": {
                "type": "string",
                "enum": ["Categorical", "Advanced", "TopN"],
                "default": "Categorical",
            },
            "values": {
                "type": "array",
                "items": {"type": ["string", "number", "boolean", "null"]},
                "description": "Filter values (never empty: use clear_filters)",
            },
            "polarity": {
                "type": "string",
                "enum": ["include", "exclude"],
                "description": "Categorical: include (In) / exclude (Not In) list",
            },
            "confirm_widen": {
                "type": "boolean",
                "description": "set_filter: allow shrinking an EXCLUDE list",
            },
            "value_type": {
                "type": "string",
                "description": "string|int64|double|decimal|bool|datetime (default: model)",
            },
            "field_type": {"type": "string", "enum": ["Column", "Measure"], "default": "Column"},
            "operator": {
                "type": "string",
                "description": "Advanced: Equal, NotEqual, GreaterThan(OrEqual), "
                "LessThan(OrEqual), IsBlank, IsNotBlank",
            },
            "top_n": {"type": "integer", "description": "TopN count"},
            "top_direction": {"type": "string", "enum": ["Top", "Bottom"], "default": "Top"},
            "by_table": {"type": "string", "description": "TopN ranking table"},
            "by_field": {"type": "string", "description": "TopN ranking field"},
        },
        "required": ["pbip_path"],
    },
    # Visual Operations (expanded - absorbs authoring create/delete + new ops)
    "visual_operations": {
        "type": "object",
        "properties": {
            "pbip_path": {
                "type": "string",
                "description": "Path to PBIP/Report folder. Optional — auto-resolved from the connected instance if omitted.",
            },
            "operation": {
                "type": "string",
                "enum": [
                    "list",
                    "create",
                    "create_group",
                    "delete",
                    "hide",
                    "unhide",
                    "update_position",
                    "update_visual_config",
                    "update_formatting",
                    "align",
                    "add_field",
                    "remove_field",
                    "set_sort",
                    "set_action",
                    "inject_code",
                    "manage_visual_calcs",
                    "configure_slicer",
                    "list_templates",
                    "get_template",
                    "replace_measure",
                    "sync_visual",
                    "sync_column_widths",
                    "sync_formatting",
                    "set_raw_property",
                    "get_raw_property",
                    "delete_raw_property",
                    "audit_pane_collisions",
                    "bulk_rename_pane_names",
                    "clear_default_selection",
                    "set_default_selection",
                    "get_roles_for_type",
                ],
                "default": "list",
            },
            "json_path": {
                "type": "string",
                "description": "Raw ops: path into visual.json, e.g. "
                "visual.objects.title[0].properties.text",
            },
            "value": {
                "description": "JSON value (not a JSON string) for set_raw_property; "
                "wrap_literal=true wraps scalars as PBIR Literal"
            },
            "force_type": {
                "type": "boolean",
                "description": "set_raw_property: allow replacing a node of another type",
            },
            "wrap_literal": {
                "type": "boolean",
                "default": False,
                "description": "set_raw_property: wrap scalars as PBIR Literal",
            },
            "create_missing": {
                "type": "boolean",
                "default": True,
                "description": "set_raw_property: create missing intermediates",
            },
            "allow_null": {
                "type": "boolean",
                "default": False,
                "description": "set_raw_property: allow writing null",
            },
            "display_title": {"type": "string", "description": "Filter by title"},
            "visual_type": {"type": "string", "description": "Filter/set visual type"},
            "visual_name": {"type": "string", "description": "Visual id or exact title"},
            "page_name": {"type": "string", "description": "Page id, exact name or unique prefix"},
            "match_all": {
                "type": "boolean",
                "description": "Write to EVERY matching visual (no selector needed)",
            },
            "allow_multiple": {
                "type": "boolean",
                "description": "Allow a write that matches several visuals",
            },
            "include_hidden": {"type": "boolean", "default": True},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "width": {"type": "number"},
            "height": {"type": "number"},
            "z": {"type": "integer"},
            "dry_run": {"type": "boolean", "default": False},
            "summary_only": {"type": "boolean", "default": True},
            "include": {
                "type": "array",
                "items": {"type": "string"},
                "description": "list extras: objects, vCO, fields, parent_group, all",
            },
            "config_type": {"type": "string", "description": "Config object to modify"},
            "property_name": {"type": "string", "description": "Property to update"},
            "property_value": {"type": ["string", "number", "boolean"]},
            "selector_metadata": {"type": "string", "description": "Per-series selector"},
            "value_type": {
                "type": "string",
                "enum": [
                    "auto",
                    "literal",
                    "boolean",
                    "number",
                    "string",
                    "int",
                    "double",
                    "decimal",
                    "bool",
                    "datetime",
                ],
                "default": "auto",
            },
            "remove_property": {"type": "boolean", "default": False},
            "config_updates": {
                "type": "array",
                "description": "Batch config changes",
                "items": {
                    "type": "object",
                    "properties": {
                        "config_type": {"type": "string"},
                        "property_name": {"type": "string"},
                        "property_value": {"type": ["string", "number", "boolean"]},
                        "selector_metadata": {"type": "string"},
                        "value_type": {"type": "string"},
                        "remove_property": {"type": "boolean"},
                    },
                    "required": ["config_type", "property_name"],
                },
            },
            "title": {"type": "string", "description": "Visual title text (create)"},
            "position": {
                "type": "object",
                "description": "Position {x, y, width, height, z}",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "width": {"type": "number"},
                    "height": {"type": "number"},
                    "z": {"type": "integer"},
                },
            },
            "measures": {
                "type": "array",
                "description": "Measure bindings [{table, measure, bucket?, display_name?}]",
                "items": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "measure": {"type": "string"},
                        "bucket": {"type": "string"},
                        "display_name": {"type": "string"},
                    },
                    "required": ["table", "measure"],
                },
            },
            "columns": {
                "type": "array",
                "description": "Column bindings [{table, column, bucket?, display_name?}]",
                "items": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "column": {"type": "string"},
                        "bucket": {"type": "string"},
                        "display_name": {"type": "string"},
                    },
                    "required": ["table", "column"],
                },
            },
            "parent_group": {"type": "string", "description": "Parent visual group ID"},
            "position_frame": {"type": "string", "enum": ["page", "group"], "default": "page"},
            "group_name": {"type": "string", "description": "Display name for visual group"},
            "formatting": {
                "type": "array",
                "description": "Formatting overrides [{config_type, property_name, value}]",
                "items": {"type": "object"},
            },
            "delete_children": {"type": "boolean", "default": True},
            "visual_id": {"type": "string", "description": "Visual ID (delete/hide: exact id)"},
            "visual_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "delete/hide/unhide several ids",
            },
            "confirm_delete_filter_layer": {
                "type": "boolean",
                "description": "delete a slicer/filtered visual (prefer hide)",
            },
            "visual_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Visual names for alignment",
            },
            "alignment": {
                "type": "string",
                "enum": ["left", "right", "top", "bottom", "center_h", "center_v"],
                "description": "Alignment type",
            },
            "direction": {
                "type": "string",
                "enum": ["horizontal", "vertical"],
                "description": "Distribute direction",
            },
            "table": {"type": "string", "description": "Table name (field binding)"},
            "field": {"type": "string", "description": "Field name (field binding)"},
            "bucket": {"type": "string", "description": "Data role bucket"},
            "field_type": {
                "type": "string",
                "enum": ["Column", "Measure", "Hierarchy"],
                "default": "Column",
            },
            "hierarchy_level": {"type": "string", "description": "add_field Hierarchy level"},
            "display_name": {"type": "string", "description": "Display name override"},
            "sort_field": {"type": "string", "description": "Sort field (Table.Field)"},
            "sort_field_type": {
                "type": "string",
                "enum": ["Column", "Measure"],
                "description": "set_sort: default = binding / model lookup",
            },
            "sort_direction": {"type": "string", "enum": ["Ascending", "Descending"]},
            "action_type": {
                "type": "string",
                "enum": [
                    "PageNavigation",
                    "Bookmark",
                    "Drillthrough",
                    "WebUrl",
                    "Back",
                    "ClearAllSlicers",
                    "DataFunction",
                ],
                "description": "visualLink type. DataFunction = translytical task flow "
                "(item reference only)",
            },
            "action_target": {
                "type": "string",
                "description": "Page id/name, bookmark id/name or URL",
            },
            "data_function_id": {"type": "string", "description": "DataFunction: item id"},
            "data_function_workspace_id": {
                "type": "string",
                "description": "DataFunction: workspace id",
            },
            "data_function_name": {
                "type": "string",
                "description": "DataFunction: not written (no verified PBIR key)",
            },
            "power_fx": {
                "type": "string",
                "description": "DataFunction: not written (no verified PBIR key)",
            },
            "code_type": {
                "type": "string",
                "enum": ["deneb", "python", "r"],
                "description": "Code injection type",
            },
            "code": {"type": "string", "description": "Code/spec content"},
            "provider": {
                "type": "string",
                "enum": ["vega", "vegaLite"],
                "description": "Deneb provider",
            },
            "sub_operation": {
                "type": "string",
                "enum": ["list", "add", "update", "delete"],
                "description": "Visual calc sub-op",
            },
            "calc_name": {"type": "string", "description": "Visual calculation name"},
            "expression": {"type": "string", "description": "DAX expression"},
            "display_name_filter": {"type": "string", "description": "Slicer display name filter"},
            "include_no_title": {
                "type": "boolean",
                "default": True,
                "description": "Slicer audit: count untitled slicers as colliding on '(no title)'",
            },
            "strategy": {
                "type": "string",
                "enum": ["auto", "append_dim", "positional_only"],
                "default": "auto",
                "description": "Slicer bulk_rename strategy",
            },
            "custom_overrides": {
                "type": "object",
                "description": "Slicer bulk_rename: {visual_name: forced_new_title}",
            },
            "values": {
                "type": "array",
                "items": {"type": ["string", "number", "boolean", "null"]},
                "description": "Slicer set_default_selection: literal values",
            },
            "polarity": {
                "type": "string",
                "enum": ["include", "exclude"],
                "description": "set_default_selection: required on an inverted slicer",
            },
            "selection_mode": {
                "type": "string",
                "enum": ["single", "multi"],
                "description": "Slicer set_default_selection: derived from len(values) if omitted",
            },
            "entity": {"type": "string", "description": "Slicer entity filter"},
            "property": {"type": "string", "description": "Slicer property filter"},
            "formatting_target": {
                "type": "string",
                "enum": [
                    "title",
                    "subtitle",
                    "divider",
                    "background",
                    "border",
                    "shadow",
                    "padding",
                    "spacing",
                    "header",
                    "tooltip",
                    "legend",
                    "categoryAxis",
                    "valueAxis",
                    "labels",
                ],
                "description": "Formatting target (update_formatting)",
            },
            "formatting_properties": {
                "type": "object",
                "description": "Properties to set on target {show, text, fontSize, fontColor, ...}",
            },
            "source_entity": {"type": "string", "description": "Source table (replace_measure)"},
            "source_property": {
                "type": "string",
                "description": "Source measure (replace_measure)",
            },
            "target_entity": {"type": "string", "description": "Target table (replace_measure)"},
            "target_property": {
                "type": "string",
                "description": "Target measure (replace_measure)",
            },
            "new_display_name": {
                "type": "string",
                "description": "New display name (replace_measure)",
            },
            "source_visual_name": {"type": "string", "description": "Source visual ID (sync ops)"},
            "source_page": {"type": "string", "description": "Source page (sync ops)"},
            "target_visual_name": {
                "type": "string",
                "description": "Target visual ID (sync_formatting)",
            },
            "target_page": {"type": "string", "description": "Target page (sync_formatting)"},
            "target_display_title": {
                "type": "string",
                "description": "Target visual title filter (sync_visual, sync_column_widths)",
            },
            "target_visual_type": {
                "type": "string",
                "description": "Target visual type filter (sync_visual, sync_column_widths)",
            },
            "target_pages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Limit sync to these pages",
            },
            "sync_position": {
                "type": "boolean",
                "default": True,
                "description": "Also sync position (sync_visual)",
            },
            "sync_children": {
                "type": "boolean",
                "default": True,
                "description": "Sync child visuals of groups (sync_visual)",
            },
            "formatting_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Formatting types to sync",
            },
            "confirm_bulk_sync": {
                "type": "boolean",
                "description": "sync_formatting without target_visual_name",
            },
        },
        "required": [],
    },
    # Filter Operations (new)
    # Bookmark Operations (replaces analyze_bookmarks)
    "bookmark_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "list",
                    "create",
                    "rename",
                    "delete",
                    "set_capture",
                    "set_affected_visuals",
                    "analyze",
                    "get",
                    "update",
                    "create_group",
                    "update_group",
                    "delete_group",
                ],
                "default": "list",
            },
            "pbip_path": {"type": "string", "description": "Path to PBIP/Report folder"},
            "bookmark_id": {"type": "string", "description": "Bookmark ID or display name"},
            "display_name": {
                "type": "string",
                "description": "Bookmark display name (create/rename/update/create_group)",
            },
            "new_name": {"type": "string", "description": "New name (rename)"},
            "page_name": {"type": "string", "description": "Target page (create)"},
            "capture_data": {"type": "boolean", "description": "Capture data state"},
            "capture_display": {"type": "boolean", "description": "Capture display state"},
            "capture_current_page": {"type": "boolean", "description": "Capture current page"},
            "visual_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Affected visual IDs",
            },
            "all_visuals": {"type": "boolean", "description": "Affect all visuals"},
            "group_id": {
                "type": "string",
                "description": "Bookmark group ID (T1-G update/delete group)",
            },
            "bookmark_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Child bookmark IDs (T1-G create/update group)",
            },
            "delete_children": {
                "type": "boolean",
                "default": False,
                "description": "Also delete child bookmarks (T1-G delete_group)",
            },
            "auto_open": {
                "type": "boolean",
                "default": True,
                "description": "Auto-open HTML (analyze)",
            },
            "output_path": {"type": "string", "description": "HTML output path (analyze)"},
            "dry_run": {"type": "boolean", "description": "Preview any write"},
        },
        "required": ["pbip_path"],
    },
    # Theme Operations (replaces analyze_theme_compliance + new theme ops)
    "theme_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "analyze_compliance",
                    "get_theme",
                    "set_colors",
                    "set_formatting",
                    "push_visual",
                    "list_text_classes",
                    "set_font",
                    "list_cf",
                    "add_cf",
                    "remove_cf",
                    "copy_cf",
                    "export_theme",
                    "apply_theme",
                    "import_theme",
                    "clear_visual_overrides",
                ],
                "default": "get_theme",
            },
            "pbip_path": {"type": "string", "description": "Path to PBIP/Report folder"},
            "theme_path": {"type": "string", "description": "Custom theme JSON path"},
            "auto_open": {"type": "boolean", "default": True},
            # A-26: single entry (was declared twice; the second silently won).
            "output_path": {
                "type": "string",
                "description": (
                    "HTML report path (analyze_compliance) or theme JSON path (export_theme)"
                ),
            },
            "colors": {
                "type": "object",
                "description": "Color map {dataColors, background, foreground, good, bad, ...}",
            },
            "visual_type_target": {
                "type": "string",
                "description": "Visual type for formatting defaults",
            },
            "formatting": {"type": "object", "description": "Formatting properties to set"},
            "page_name": {"type": "string", "description": "Page name (push_visual, CF ops)"},
            "visual_name": {"type": "string", "description": "Visual name (push_visual, CF ops)"},
            "text_class": {
                "type": "string",
                "description": "Text class name (title, label, callout, etc.)",
            },
            "font_family": {"type": "string", "description": "Font family name"},
            "font_size": {"type": "number", "description": "Font size"},
            "color": {"type": "string", "description": "Color hex value"},
            "bold": {"type": "boolean"},
            "container": {"type": "string", "description": "CF container (e.g. dataPoint)"},
            "property_name": {"type": "string", "description": "CF property (e.g. fill)"},
            "rule_type": {
                "type": "string",
                "enum": ["color_scale", "rules"],
                "description": "CF rule type",
            },
            "selector": {
                "type": "object",
                "description": "CF selector, e.g. {metadata: 'T.Measure'}",
            },
            "selector_metadata": {"type": "string", "description": "CF selector queryRef"},
            "cf_config": {
                "type": "object",
                "description": "CF rule config {min_color, max_color, mid_color, etc.}",
            },
            "source_page": {"type": "string", "description": "Source page (copy_cf)"},
            "source_visual": {"type": "string", "description": "Source visual (copy_cf)"},
            "target_page": {"type": "string", "description": "Target page (copy_cf)"},
            "target_visual": {"type": "string", "description": "Target visual (copy_cf)"},
            "dry_run": {"type": "boolean", "description": "Preview any write"},
            # T1-H
            "theme": {"type": "object", "description": "Theme JSON object (apply_theme)"},
            "theme_name": {
                "type": "string",
                "description": "Theme display name (apply_theme/import_theme)",
            },
            "source": {
                "type": "string",
                "description": "Path to theme JSON file on disk (import_theme)",
            },
            "scope": {
                "type": "string",
                "default": "all",
                "description": "clear_visual_overrides: all | page:<id> | visual:<page>/<id>",
            },
            "keep_cf": {
                "type": "boolean",
                "default": True,
                "description": "Preserve conditional-formatting structures (clear_visual_overrides)",
            },
        },
        "required": ["pbip_path"],
    },
    # Batch Operations (03_Batch_Operations)
    "batch_operations": {
        "type": "object",
        "properties": {
            "object_type": {
                "type": "string",
                "enum": [
                    "measures",
                    "tables",
                    "columns",
                    "relationships",
                    "changeset",
                    "ti_suite",
                    "patch",
                ],
                "description": (
                    "measures|tables|columns|relationships (with batch_operation + items), "
                    "changeset (declarative delta) or ti_suite (generate time-intelligence "
                    "measures)."
                ),
                "x-doc": "Type of object to operate on (P2-188: renamed from `operation`, which is still accepted as a deprecated alias). Use `changeset` (or just pass `changeset`) for a declarative multi-object atomic delta. Use `ti_suite` to GENERATE a time-intelligence measure suite (YTD/QTD/MTD/WTD/PY/delta) over `base_measures` — returns a reviewable plan by default; pass apply=true to create them through the normal gated measures/create path.",
            },
            "changeset": {
                "type": "object",
                "description": (
                    "Declarative delta by object_type, e.g. {tables:{create:[...]}, "
                    "columns:{delete:[...]}}; applied atomically in dependency order. "
                    "preview=true -> plan_hash."
                ),
                "x-doc": (
                    "Declarative multi-object delta grouped by object_type, e.g. "
                    '{"tables":{"create":[...]},"columns":{"create":[...],"delete":[...]},'
                    '"relationships":{"update":[...]}}. Applied atomically in dependency-safe '
                    "order (create tables→columns→relationships; updates; deletes reversed). "
                    "preview=true returns a plan_hash + op summary WITHOUT writing; apply requires "
                    "that plan_hash back (rejects stale_plan). Measures are NOT supported here — "
                    "use object_type=measures."
                ),
            },
            "preview": {
                "type": "boolean",
                "description": "dry-run any write (alias dry_run): would_change/errors/breaks + plan_hash; saves nothing.",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Alias of preview=true (batch / ti_suite apply).",
            },
            "base_measures": {
                "type": "array",
                "items": {"type": "string"},
                "description": "ti_suite: existing base measure names to build variants from.",
            },
            "date_column": {
                "type": "string",
                "description": "ti_suite: qualified DATE column driving time intelligence, e.g. \"'Date'[Date]\". Must be a real date column — an integer surrogate like [Year Month Nr] is rejected (TI functions require type DATE).",
            },
            "variants": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "YTD",
                        "QTD",
                        "MTD",
                        "WTD",
                        "PY",
                        "PY_YTD",
                        "PM",
                        "DELTA",
                        "DELTA_PCT",
                    ],
                },
                "description": "ti_suite: which variants to generate (default YTD/QTD/MTD/PY/DELTA/DELTA_PCT). DELTA* need PY in the same request. WTD needs calendar-based TI (CL 1701+).",
            },
            "target_table": {
                "type": "string",
                "description": "ti_suite: target table (default: each base measure's home table).",
            },
            "display_folder": {
                "type": "string",
                "description": "ti_suite: display folder for the generated measures.",
            },
            "measures": {
                "type": "array",
                "description": "Deprecated alias of base_measures / items.",
            },
            "pbip_path": {
                "type": "string",
                "description": "PBIP for the delete blast-radius proof (default: Desktop's).",
            },
            "if_exists": {
                "type": "string",
                "enum": ["fail", "update"],
                "description": "measures/create: 'update' = upsert (default 'fail').",
            },
            "name_pattern": {
                "type": "string",
                "description": 'ti_suite: naming template, default "{base} {suffix}". Must contain {base} and {suffix}.',
            },
            "format_string": {
                "type": "string",
                "description": "ti_suite: format string applied to generated measures (percent variants keep their own).",
            },
            "apply": {
                "type": "boolean",
                "description": "ti_suite: false (default) returns the plan only; true creates the measures via the gated measures/create batch path.",
            },
            "plan_hash": {
                "type": "string",
                "description": "Apply a previewed change; refused (stale_preview) if the model moved.",
            },
            "select": {
                "type": "string",
                "description": (
                    "patch: Dynamic-LINQ selection, e.g. "
                    'Measures.Where(Expression.Contains("SUMX") and not IsHidden)'
                ),
            },
            "patch": {
                "type": "object",
                "description": 'patch: {Property: value-template} or {"Delete()": true}',
            },
            "from_bpa": {
                "type": "array",
                "items": {"type": "string"},
                "description": "patch: BPA rule ids whose FixExpression is applied",
            },
            "batch_operation": {
                "type": "string",
                "enum": [
                    "create",
                    "update",
                    "delete",
                    "rename",
                    "move",
                    "move_display_folder",
                    "activate",
                    "deactivate",
                    "refresh",
                ],
                "description": "move_display_folder needs no expression (measures/columns).",
            },
            "items": {
                "type": "array",
                "description": (
                    "Object definitions (max 200 measures/columns, 100 relationships, 30 "
                    "tables)."
                ),
                "x-doc": (
                    "List of object definitions. Max items depends on object type: "
                    "measures/columns: 200, relationships: 100, tables: 30. "
                    "Deletes above 20 require confirm_destructive=true."
                ),
                "minItems": 1,
                "maxItems": 200,
                "items": {"type": "object"},
            },
            "options": {
                "type": "object",
                "properties": {
                    "use_transaction": {
                        "type": "boolean",
                        "default": True,
                        "description": "All-or-nothing (default true)",
                    },
                    "continue_on_error": {
                        "type": "boolean",
                        "default": False,
                        "description": "Keep going after an error (needs use_transaction=false)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "Validate only (no execution)",
                    },
                    "if_exists": {
                        "type": "string",
                        "enum": ["fail", "update"],
                        "description": "measures/create: 'update' = upsert (default 'fail').",
                    },
                    "confirm_destructive": {
                        "type": "boolean",
                        "description": "Confirm a delete of >20 items (strict bool).",
                    },
                },
            },
        },
        # P2-188: `operation` no longer in `required` — caller can pass
        # `object_type` instead (preferred). Handler accepts either.
        # batch_operation/items are required only for the classic per-object-type
        # path. `changeset` and `ti_suite` carry their own inputs, so requiring
        # them here made those ops uncallable through the MCP schema.
        "required": [],
    },
    # Debug Operations (09_Debug_Operations)
    "debug_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
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
                    "debug_loop",
                ],
                "default": "visual",
            },
            "query": {
                "type": "string",
                "description": "Raw DAX query (DEFINE…EVALUATE) to execute (run_dax)",
            },
            "roles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "RLS roles to impersonate (visual / compare / drill / run_dax).",
                "x-doc": (
                    "Optional RLS role names to impersonate while running the "
                    "query (visual / compare / drill / run_dax). When set, opens "
                    "a fresh ADOMD connection with Roles=... so amounts reflect "
                    "what that role would see."
                ),
            },
            "effective_user_name": {
                "type": "string",
                "description": (
                    "Optional UPN to impersonate alongside roles. Power BI "
                    "Desktop does NOT honor this (Service/SSAS only) — on Desktop "
                    "the live Windows user name flows through USERPRINCIPALNAME()."
                ),
            },
            "pbip_path": {"type": "string", "description": "PBIP folder path (set_path)"},
            "page_name": {"type": "string"},
            "visual_id": {"type": "string"},
            "visual_name": {"type": "string"},
            "measure_name": {"type": "string"},
            "measures": {
                "type": "array",
                "items": {"type": "string"},
                "description": "visual: explicit measure names (override auto-discovery).",
                "x-doc": "Explicit measure names to use (visual) — overrides auto-discovery. Use with trace=true to get SE/FE analysis for specific measures with the visual's real filter context.",
            },
            "table_name": {"type": "string", "description": "Table (analyze)"},
            "include_slicers": {"type": "boolean"},
            "execute_query": {
                "type": "boolean",
                "description": "Execute query and return rows (visual)",
            },
            "execute_measure": {
                "type": "boolean",
                "description": "Execute measure (analyze)",
            },
            "trace": {
                "type": "boolean",
                "default": False,
                "description": (
                    "SE/FE trace (visual / run_dax / compare); returns trace_id for optimize. "
                    "Rows: execute_query=true."
                ),
                "x-doc": "SE/FE trace of the visual query (visual) or raw query (run_dax); returns trace_id for optimize. compare: time runs as cold traces. Cold cache; add execute_query=true for rows.",
            },
            "clear_cache": {
                "type": "boolean",
                "default": True,
                "description": "Clear VertiPaq cache before trace run for cold-cache timings (visual/run_dax, requires trace=true)",
            },
            "filters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Manual DAX predicates, applied as KEEPFILTERS(<dax>) around the visual query.",
            },
            "skip_auto_filters": {"type": "boolean"},
            "expression": {
                "type": "string",
                "description": "Raw DAX expression to analyze instead of measure_name (analyze).",
            },
            "tolerance": {
                "type": "number",
                "default": 0.001,
                "description": "Absolute tolerance for the value match (compare).",
            },
            "top_n": {
                "type": "integer",
                "default": 1000,
                "description": "Row cap for the returned result (run_dax).",
            },
            "bypass_cache": {
                "type": "boolean",
                "default": True,
                "description": "run_dax: skip the query-result cache.",
            },
            "trace_id": {
                "type": "string",
                "description": "optimize: the trace to diagnose (from a trace=true call).",
                "x-doc": (
                    "optimize: trace to diagnose (from visual/run_dax trace=true). Omitted: "
                    "last trace only if it timed this measure, else traces ROW([measure])."
                ),
            },
            "runs": {
                "type": "integer",
                "default": 3,
                "description": "compare: timed runs per side, interleaved (1..20).",
            },
            "warmup": {
                "type": "boolean",
                "default": True,
                "description": "compare: one unmeasured run per side first.",
            },
            "reference_query": {
                "type": "string",
                "description": (
                    "visual: Desktop's query (Performance Analyzer 'Copy query'); adds "
                    "reconstruction_check."
                ),
            },
            "from_trace": {
                "type": "boolean",
                "description": "visual: run the live trace's best-matching captured query (fidelity=captured)",
            },
            "pa_export_path": {
                "type": "string",
                "description": "visual: Performance Analyzer export; QueryText read by visual id.",
            },
            "use_reference_query": {
                "type": "boolean",
                "default": True,
                "description": "visual: execute the reference query instead of the reconstruction.",
            },
            "compact": {"type": "boolean"},
            "original_measure": {
                "type": "string",
                "description": "Original measure (compare)",
            },
            "optimized_expression": {
                "type": "string",
                "description": "Optimized DAX (compare)",
            },
            "mode": {
                "type": "string",
                "enum": ["live_swap"],
                "description": (
                    "compare: live_swap = checkpoint, write the candidate into the live model, "
                    "time the visual's real query cold vs baseline, restore + verify."
                ),
            },
            "patch": {
                "type": "array",
                "items": {"type": "object"},
                "description": "live_swap: [{table_name?, measure_name, expression}] candidate bodies.",
            },
            "dry_run": {
                "type": "boolean",
                "description": "live_swap: plan only, write nothing.",
            },
            "equivalence_check": {
                "type": "boolean",
                "default": False,
                "description": (
                    "compare: prove both sides return the same rows before trusting the "
                    "speed-up (comparison.performance_claim_valid)."
                ),
                "x-doc": (
                    "compare: validate SEMANTIC EQUIVALENCE before trusting the "
                    "performance delta. Compares the two result sets as an "
                    "order-insensitive multiset of rows (relative+absolute "
                    "numeric tolerance, BLANK != 0) and reports the verdict under "
                    "comparison.equivalence. Sets "
                    "comparison.performance_claim_valid=false (plus a "
                    "performance_warning) when the results differ — a single "
                    "matching scalar is NOT proof of equivalence."
                ),
            },
            "equivalence_grain": {
                "type": "string",
                "description": (
                    "compare: DAX grain for the equivalence check — a table "
                    "expression or 'Table'[Column] reference to group by (e.g. "
                    "\"'Date'[Year]\"). Both sides are re-evaluated with "
                    'SUMMARIZECOLUMNS(<grain>, "value", ...) so equivalence is '
                    "proven across many rows instead of one total. Omit to "
                    "compare only the scalar (grain='scalar')."
                ),
            },
            "fact_table": {"type": "string", "description": "Fact table (drill)"},
            "limit": {"type": "integer", "description": "Max rows (drill, default: 100)"},
            "variable_name": {
                "type": "string",
                "description": "Variable name (debug_variable)",
            },
            "max_rows": {
                "type": "integer",
                "default": 100,
                "description": "Max rows (debug_variable/step_variables)",
            },
            "total_ms": {
                "type": "number",
                "description": "Total query time ms from visual trace (optimize, required)",
            },
            "fe_ms": {
                "type": "number",
                "description": "Formula Engine time ms (optimize, required)",
            },
            "se_ms": {
                "type": "number",
                "description": "Storage Engine time ms (optimize, required)",
            },
            "se_cpu_ms": {"type": "number", "description": "SE CPU time ms (optimize)"},
            "fe_pct": {
                "type": "number",
                "description": "FE % of total time (optimize, required)",
            },
            "se_pct": {"type": "number", "description": "SE % of total time (optimize)"},
            "se_queries": {
                "type": "integer",
                "description": "Number of SE queries (optimize, required)",
            },
            "se_cache_hits": {"type": "integer", "description": "SE cache hits (optimize)"},
            "se_parallelism": {
                "type": "number",
                "description": "SE parallelism ratio from visual trace (optimize)",
            },
            "se_events": {
                "type": "array",
                "items": {"type": "object"},
                "description": "SE event list from visual trace (optimize, optional for deep SE analysis)",
            },
            "cache_comparison": {
                "type": "boolean",
                "default": False,
                "description": "Run cold+warm cache comparison to confirm datacache effectiveness (optimize, opt-in)",
            },
            "per_measure_trace": {
                "type": "boolean",
                "default": False,
                "description": "Run individual traces per measure to isolate slow measures (optimize, opt-in, slow)",
            },
            "pages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Page names to audit (audit). Default: all pages.",
            },
            "include_data": {
                "type": "boolean",
                "default": False,
                "description": "Include raw row data in audit output (audit). Default: false to save tokens.",
            },
            "skip_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Additional visual types to skip (audit). Extends built-in skip list.",
            },
            "expected": {
                "type": "number",
                "description": "Expected scalar value (debug_loop). Loop exits when actual matches.",
            },
            "max_iterations": {
                "type": "integer",
                "default": 5,
                "description": "Max debug loop iterations (debug_loop).",
            },
            "take_screenshot": {
                "type": "boolean",
                "default": True,
                "description": "Capture PBI window each iteration (debug_loop).",
            },
            "fix_strategy": {
                "type": "string",
                "enum": ["dry_run", "live_tom", "tmdl_edit", "ask_user"],
                "default": "ask_user",
                "description": (
                    "How to apply proposed fixes between iterations "
                    "(debug_loop). 'ask_user' returns proposed_fix and "
                    "exits so the agent can decide; 'live_tom' auto-applies "
                    "via the optimizer/injector for fe_dominated anomalies "
                    "(semantic anomalies defer unless allow_llm_fix=true); "
                    "'tmdl_edit' requires a tmdl_edit_fn callback (Python "
                    "API only); 'dry_run' never applies."
                ),
            },
            "allow_llm_fix": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Opt in to the Tier-2 LLM-in-the-loop DAX rewrite for "
                    "SEMANTIC anomalies (blank/wrong value) under "
                    "fix_strategy='live_tom' (debug_loop). Off by default: "
                    "without it, a semantic anomaly defers with a proposed_fix "
                    "instead of letting an LLM edit the model. The applied "
                    "rewrite is re-verified next iteration and original_dax is "
                    "recorded for manual revert (no auto-revert)."
                ),
            },
            "screenshot_dir": {
                "type": "string",
                "description": "Directory to write per-iteration screenshots (debug_loop). Defaults to a temp folder.",
            },
            "wait_timeout_seconds": {
                "type": "number",
                "default": 180.0,
                "description": "Reopen wait timeout per iteration (debug_loop).",
            },
        },
        "required": ["operation"],
    },
    # Validate Operations (09_Validate)
    "validate_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "cross_visual",
                    "expected_value",
                    "filter_permutation",
                    "capture_baseline",
                    "compare_baseline",
                    "test_put",
                    "test_remove",
                    "test_list",
                    "test_run",
                    "test_export",
                    "tieout",
                    "test_generate",
                    "equivalence",
                ],
                "description": "baselines: baseline_path. test_*: suite_path.",
                "x-doc": (
                    "capture_baseline: snapshot measure values to baseline_path; "
                    "compare_baseline: re-run and diff against it (regression guard "
                    "around model edits / 15_DAX_Refactor). test_*: persisted suites "
                    "(suite_path). tieout: parent == sum(children) per subtotal level of a "
                    "matrix/table visual (page_name + visual_id), mode=page_vs_page "
                    "(page_a, page_b, measure_name) or mode=A_vs_B (measure_a, measure_b, "
                    "by, filters). test_generate: source=page|report|roles|measures writes "
                    "regression tests (preview unless write=true + suite_path). equivalence: "
                    "two DAX expressions over a context grid."
                ),
            },
            "measure_name": {"type": "string"},
            "measures": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Measure names to snapshot (capture_baseline).",
            },
            "baseline_path": {
                "type": "string",
                "description": "JSON baseline file path (capture_baseline / compare_baseline).",
            },
            "page_name": {"type": "string"},
            "pages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "cross_visual: pages to compare (default all)",
            },
            "visual_id": {"type": "string"},
            "visual_name": {"type": "string"},
            "expected_value": {"type": ["number", "string"]},
            "filters": {"type": "array", "items": {"type": "string"}},
            "tolerance": {"type": "number"},
            "max_permutations": {"type": "integer"},
            "suite_path": {
                "type": "string",
                "description": "test_*: JSON suite file (created by test_put; commit it next to the model).",
            },
            "test": {
                "type": "object",
                "description": "test_put: {id, type, ...} (types: see the user guide).",
                "x-doc": (
                    "test_put: {id, type, ...}; type = measure_assertion | dax_assertion | "
                    "regression_snapshot | rls_validation | performance_budget | "
                    "metadata_compliance | rls_audit | tieout."
                ),
            },
            "test_id": {"type": "string", "description": "test_remove: id of the test."},
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "test_run: only these ids.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "test_run: only tests with these tags.",
            },
            "replace": {
                "type": "boolean",
                "description": "test_put: replace a test with the same id (default true).",
            },
            "record": {
                "type": "boolean",
                "description": "test_run: store last_run in the suite file (default true).",
            },
            "output_path": {
                "type": "string",
                "description": "test_export (or test_run): write the results here.",
            },
            "output_format": {
                "type": "string",
                "enum": ["junit", "json"],
                "description": "test_export format (junit).",
            },
            # tieout (F-09)
            "mode": {
                "type": "string",
                "enum": ["visual", "page_vs_page", "A_vs_B"],
                "description": "tieout mode (default visual).",
            },
            "page_a": {"type": "string", "description": "tieout page_vs_page: first page."},
            "page_b": {"type": "string", "description": "tieout page_vs_page: second page."},
            "measure_a": {
                "type": "string",
                "description": "tieout A_vs_B: first measure or DAX expression.",
            },
            "measure_b": {
                "type": "string",
                "description": "tieout A_vs_B: second measure or DAX expression.",
            },
            "by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "tieout A_vs_B: 'T'[C] grain columns (default grand total).",
            },
            "non_additive": {
                "type": "array",
                "items": {"type": "string"},
                "description": "tieout: measures to skip (ratio / semi-additive).",
            },
            "relative_tolerance": {
                "type": "number",
                "description": "tieout: relative tolerance (default 1e-9).",
            },
            "max_rows": {
                "type": "integer",
                "description": "tieout: row cap that replaces the visual's TOPN window (default 5000).",
            },
            # test_generate (F-10)
            "source": {
                "type": "string",
                "enum": ["page", "report", "roles", "measures"],
                "description": "test_generate: what to generate from.",
            },
            "write": {
                "type": "boolean",
                "description": "test_generate: persist into suite_path (default false = preview + diff).",
            },
            "accept_changes": {
                "type": "boolean",
                "description": "test_generate write: overwrite tests whose expectation changed.",
            },
            "include_perf": {
                "type": "boolean",
                "description": "test_generate: add performance_budget tests for heavy visuals.",
            },
            "include_tieout": {
                "type": "boolean",
                "description": "test_generate: add tieout tests for matrix / table visuals (default true).",
            },
            "headroom": {
                "type": "number",
                "description": "test_generate perf: max_ms = median x headroom (default 1.5).",
            },
            "heavy_ms": {
                "type": "number",
                "description": "test_generate perf: only visuals with a median above this (default 500).",
            },
            "snapshot_rows": {
                "type": "integer",
                "description": "test_generate: snapshot full rows up to this many (default 50).",
            },
            "max_visuals": {
                "type": "integer",
                "description": "test_generate: visual cap per run (default 200).",
            },
            "runs": {
                "type": "integer",
                "description": "test_generate perf: timed runs (default 3).",
            },
            "roles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "test_generate: role names (default all roles).",
            },
            "as_user": {
                "type": "string",
                "description": "test_generate: known UPN for the rls_audit tests.",
            },
            # equivalence (F-14)
            "expression_a": {"type": "string", "description": "equivalence: DAX expression A."},
            "expression_b": {"type": "string", "description": "equivalence: DAX expression B."},
            "candidate_expression": {
                "type": "string",
                "description": "equivalence: candidate body compared with measure_name.",
            },
            "grid_columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "equivalence: 'T'[C] columns of the grid (default: page slicers).",
            },
            "date_column": {
                "type": "string",
                "description": "equivalence: 'Date'[Date] column for the date grains.",
            },
            "grains": {
                "type": "array",
                "items": {"type": "string", "enum": ["year", "quarter", "month"]},
                "description": "equivalence: date grains.",
            },
            "max_cells": {
                "type": "integer",
                "description": "equivalence: cell cap (default 200, max 2000).",
            },
            "host_table": {
                "type": "string",
                "description": "equivalence: table hosting the query-scoped DEFINE MEASURE (default: first visible).",
            },
        },
        "required": ["operation"],
    },
    # Profile Operations (09_Profile)
    "profile_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "page",
                    "filter_matrix",
                    "decompose",
                    "contribution",
                    "trend",
                    "root_cause",
                    "export",
                    "perf_analyzer_import",
                    "regress",
                ],
            },
            "page_name": {"type": "string"},
            "visual_id": {"type": "string"},
            "visual_name": {"type": "string"},
            "iterations": {
                "type": "integer",
                "description": "page: runs per visual (3). filter_matrix: runs per combo, fastest kept (1).",
            },
            "record": {
                "type": "boolean",
                "description": "page: append this run to output/perf/<project>.jsonl (perf history)",
            },
            "label": {"type": "string", "description": "record: label of this run"},
            "baseline": {"type": "string", "description": "regress: baseline label / commit"},
            "candidate": {"type": "string", "description": "regress: candidate label / commit"},
            "project": {
                "type": "string",
                "description": "record / regress: perf-history project key",
            },
            "include_slicers": {"type": "boolean"},
            "trace": {
                "type": "boolean",
                "default": False,
                "description": (
                    "page: trace every visual (SE/FE + estimated 'Other' wait); "
                    "perf_analyzer_import: replay the exported queries."
                ),
                "x-doc": (
                    "page: trace every visual query for SE/FE split and estimate "
                    "Performance Analyzer's 'Other' wait via a K-slot queue model. "
                    "perf_analyzer_import: replay each exported QueryText for SE/FE."
                ),
            },
            "schedule": {
                "type": "string",
                "enum": ["lpt", "layout"],
                "default": "lpt",
                "description": (
                    "page trace=true: 'lpt' longest-first (lower bound) or 'layout' "
                    "(tabOrder/position, like a real load)."
                ),
            },
            "warm_pass": {
                "type": "boolean",
                "default": False,
                "description": "page trace=true: extra pass without ClearCache -> warm_page_pass_ms.",
            },
            "clear_cache": {"type": "boolean", "default": True},
            "simulate_concurrency": {
                "type": "integer",
                "default": 6,
                "description": (
                    "Query slots for the wait ESTIMATE. Power BI publishes no "
                    "concurrency limit; 6 matches observed Desktop behaviour."
                ),
            },
            "per_visual_limit": {
                "type": "integer",
                "description": "Profile at most this many data visuals.",
            },
            "file_path": {
                "type": "string",
                "description": (
                    "perf_analyzer_import: PowerBIPerformanceData.json exported from "
                    "Desktop's Performance Analyzer pane."
                ),
            },
            "pbip_path": {
                "type": "string",
                "description": (
                    "perf_analyzer_import: PBIP folder used to resolve visual ids "
                    "to their PBIR names/titles."
                ),
            },
            "filter_columns": {"type": "array", "items": {"type": "string"}},
            "max_combinations": {"type": "integer"},
            "dimension": {"type": "string"},
            "dimensions": {"type": "array", "items": {"type": "string"}},
            "date_column": {"type": "string"},
            "granularity": {
                "type": "string",
                "enum": ["day", "week", "month", "quarter", "year"],
            },
            "baseline_filters": {"type": "array", "items": {"type": "string"}},
            "comparison_filters": {"type": "array", "items": {"type": "string"}},
            "top_n": {"type": "integer"},
            "output_format": {"type": "string", "enum": ["markdown", "json"]},
        },
        "required": ["operation"],
    },
    # Document Operations (09_Document)
    "document_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["page", "report", "measure_lineage", "filter_lineage"],
            },
            "page_name": {"type": "string"},
            "measure_name": {"type": "string"},
            "lightweight": {"type": "boolean"},
            "include_ui_elements": {"type": "boolean"},
            "narrate": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Ask the client LLM (MCP sampling) for a narrative summary; no-op without "
                    "sampling."
                ),
                "x-doc": (
                    "Request a natural-language narrative summary via MCP "
                    "sampling (server asks client to call its LLM). "
                    "Silently no-ops if client doesn't support sampling."
                ),
            },
        },
        "required": ["operation"],
    },
    # Model Operations (02_Model_Operations)
    "model_operations": {
        "type": "object",
        "properties": {
            "object_type": {
                "type": "string",
                "enum": [
                    "table",
                    "column",
                    "measure",
                    "relationship",
                    "calculation_group",
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
                ],
                "description": "Type of model object to operate on",
            },
            "operation": {
                "type": "string",
                "description": (
                    "Depends on object_type (list: 10_Show_User_Guide "
                    "tool=02_Model_Operations). 'upsert' = create + if_exists=update."
                ),
                "x-doc": (
                    "Operation to perform (valid values depend on object_type). "
                    "Use 'upsert' (alias for create + if_exists=update) on measure/"
                    "column/table/relationship to update when the object already exists."
                ),
            },
            "if_exists": {
                "type": "string",
                "enum": ["fail", "update"],
                "description": "create: 'update' = upsert (default 'fail' = already_exists).",
            },
            "preview": {
                "type": "boolean",
                "description": "dry-run any write (alias dry_run): would_change/errors/breaks + plan_hash; saves nothing.",
            },
            "plan_hash": {
                "type": "string",
                "description": "Apply a previewed write: refused (stale_preview) if the model or args changed.",
            },
            "preview_hash": {
                "type": "string",
                "description": (
                    "rename: hash from preview=true; refused (stale_preview) if changed."
                ),
            },
            "include": {
                "type": "array",
                "items": {"type": "string"},
                "description": "measure list: extra fields (expression, format_string, display_folder, description, kpi, all).",
            },
            # Shared identifiers
            "table_name": {"type": "string", "description": "Table name"},
            "column_name": {"type": "string", "description": "Column name"},
            "measure_name": {"type": "string", "description": "Measure name"},
            "relationship_name": {"type": "string", "description": "Relationship name"},
            "group_name": {"type": "string", "description": "Calculation group name"},
            "rolling_window_granularity": {
                "type": "string",
                "description": "table set_refresh_policy: rolling-window grain (Day/Month/Quarter/Year)",
            },
            "rolling_window_periods": {
                "type": "integer",
                "description": "table set_refresh_policy: number of rolling-window periods to keep",
            },
            "incremental_granularity": {
                "type": "string",
                "description": "table set_refresh_policy: incremental-refresh grain (Day/Month/Quarter/Year)",
            },
            "incremental_periods": {
                "type": "integer",
                "description": "table set_refresh_policy: number of incremental periods to refresh",
            },
            "source_expression": {
                "type": "string",
                "description": "table set_refresh_policy: optional M source expression for the policy",
            },
            "item_name": {
                "type": "string",
                "description": "Calculation item name (calc group add_item/update_item/delete_item)",
            },
            "format_string_expression": {
                "type": "string",
                "description": "DAX format string expression for a calculation item",
            },
            "ordinal": {
                "type": "integer",
                "description": "Item ordinal (calc item add/update)",
            },
            "name": {
                "type": "string",
                "description": "Object name (relationship create, auto-generated if omitted)",
            },
            "new_name": {"type": "string", "description": "New name (rename/update)"},
            "pbip_path": {
                "type": "string",
                "description": "rename: .pbip whose bound PBIR report gets the rename cascade (Desktop closed).",
            },
            # Shared properties
            "expression": {"type": "string", "description": "DAX expression"},
            "m_expression": {
                "type": "string",
                "description": ("table create: M source of an import table (else `expression`)."),
            },
            "description": {"type": "string", "description": "Object description"},
            "format_string": {"type": "string", "description": "Format string"},
            "display_folder": {"type": "string", "description": "Display folder path"},
            "hidden": {"type": "boolean", "description": "Hide object"},
            # Table-specific
            "max_rows": {
                "type": "integer",
                "description": "Max rows for sample_data (default 10, max 1000)",
                "default": 10,
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Columns to include (sample_data)",
            },
            "order_by": {"type": "string", "description": "Order by column (sample_data)"},
            "order_direction": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
            "columns_page_size": {
                "type": "integer",
                "description": "Page size for columns (table describe)",
            },
            "measures_page_size": {
                "type": "integer",
                "description": "Page size for measures (table describe)",
            },
            "relationships_page_size": {
                "type": "integer",
                "description": "Page size for relationships (table describe)",
            },
            "start_year": {
                "type": "integer",
                "description": "Start year (generate_calendar, default: current year - 5)",
            },
            "end_year": {
                "type": "integer",
                "description": "End year (generate_calendar, default: current year + 2)",
            },
            "include_fiscal": {
                "type": "boolean",
                "description": "Include fiscal year columns (generate_calendar, default: false)",
            },
            "fiscal_start_month": {
                "type": "integer",
                "description": "Fiscal year start month 1-12 (generate_calendar, default: 7)",
            },
            # Column-specific
            "column_type": {
                "type": "string",
                "enum": ["all", "data", "calculated"],
                "description": "Filter type (column list)",
                "default": "all",
            },
            "data_type": {
                "type": "string",
                "enum": [
                    "String",
                    "Int64",
                    "Double",
                    "Decimal",
                    "Boolean",
                    "DateTime",
                    "Binary",
                    "Variant",
                ],
                "description": "Case-insensitive; omitted = String (calc columns: inferred).",
            },
            "source_column": {
                "type": "string",
                "description": "Source column for data columns (column create)",
            },
            "sort_by_column": {
                "type": "string",
                "description": "Column name to sort this column by (column update)",
            },
            "clear_sort_by_column": {
                "type": "boolean",
                "description": "Remove any existing sort-by-column binding (column update)",
                "default": False,
            },
            "summarize_by": {
                "type": "string",
                "enum": [
                    "default",
                    "none",
                    "sum",
                    "min",
                    "max",
                    "count",
                    "average",
                    "distinctcount",
                ],
                "description": "Default summarization (column update)",
            },
            "data_category": {
                "type": "string",
                "description": "Data category, e.g. 'WebUrl', 'ImageUrl', 'Address' (column update)",
            },
            "is_key": {"type": "boolean", "description": "Mark column as key (column update)"},
            "is_nullable": {
                "type": "boolean",
                "description": "Whether column allows nulls (column update)",
            },
            "top_n": {
                "type": "integer",
                "description": "Top N values (column statistics/distribution, default: 10)",
                "default": 10,
            },
            # Measure-specific
            "new_table": {"type": "string", "description": "Target table (measure move)"},
            # Relationship-specific
            "from_table": {
                "type": "string",
                "description": "Source table (relationship create)",
            },
            "from_column": {
                "type": "string",
                "description": "Source column (relationship create)",
            },
            "to_table": {"type": "string", "description": "Target table (relationship create)"},
            "to_column": {
                "type": "string",
                "description": "Target column (relationship create)",
            },
            "from_cardinality": {"type": "string", "enum": ["One", "Many"], "default": "Many"},
            "to_cardinality": {"type": "string", "enum": ["One", "Many"], "default": "One"},
            "cross_filtering_behavior": {
                "type": "string",
                "enum": ["OneDirection", "BothDirections", "Automatic"],
                "default": "OneDirection",
            },
            "is_active": {"type": "boolean", "description": "Relationship active state"},
            "allow_ambiguous": {
                "type": "boolean",
                "description": ("relationship: allow a 2nd active path (default: ambiguous_path)."),
                "default": False,
            },
            "security_filtering_behavior": {
                "type": "string",
                "enum": ["OneDirection", "BothDirections", "None"],
                "description": "relationship update: RLS filter direction",
            },
            "rely_on_referential_integrity": {
                "type": "boolean",
                "description": "relationship update: assume referential integrity",
            },
            "join_on_date_behavior": {
                "type": "string",
                "enum": ["DatePartOnly", "DateAndTime"],
                "description": "relationship update: how DateTime keys join",
            },
            "active_only": {
                "type": "boolean",
                "description": "Filter active relationships only (relationship list)",
                "default": False,
            },
            # Calculation group-specific
            "items": {
                "type": "array",
                "description": "Calculation items (calc group create)",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "expression": {"type": "string"},
                        "ordinal": {"type": "integer"},
                    },
                },
            },
            "precedence": {"type": "integer", "description": "Calculation group precedence"},
            "patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "calculation_group detect: patterns to scan (default all)",
            },
            "min_pattern_size": {
                "type": "integer",
                "description": "calculation_group detect: min base measures (default 3)",
            },
            "candidate": {
                "type": "object",
                "description": "calculation_group scaffold: a candidate from detect",
            },
            "calc_group_table_name": {
                "type": "string",
                "description": "calculation_group scaffold: calc-group table name",
            },
            "date_column": {
                "type": "string",
                "description": "calculation_group scaffold: date column, e.g. 'Date'[Date]; table mark_as_date_table: the DateTime column.",
            },
            # Pagination (shared)
            # Partition-specific
            "partition_name": {"type": "string", "description": "Partition name"},
            "refresh_after": {
                "type": "boolean",
                "description": "partition update: refresh the partition afterwards (default false = data stale).",
                "default": False,
            },
            "mode": {
                "type": "string",
                "enum": ["Import", "DirectQuery", "Dual"],
                "description": "Partition storage mode (partition create/set_mode)",
                "default": "Import",
            },
            # Hierarchy-specific
            "hierarchy_name": {"type": "string", "description": "Hierarchy name"},
            "levels": {
                "type": "array",
                "description": "Hierarchy levels (create; update: new order of existing levels)",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "column": {"type": "string"}},
                },
            },
            # Field parameter-specific (object_type=field_parameter, operation=create)
            "fields": {
                "type": "array",
                "description": (
                    "Field-parameter rows [{label, reference, order?}], reference e.g. "
                    "\"'Sales'[Amount]\"."
                ),
                "x-doc": "Field parameter rows {label, reference, order?}; reference = NAMEOF argument, e.g. \"'Sales'[Amount]\". Exclusive with `expression`.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "reference": {"type": "string"},
                        "order": {"type": "integer"},
                    },
                    "required": ["label", "reference"],
                },
            },
            "clone_from": {
                "type": "string",
                "description": "field_parameter create: FP table to clone (expression + Value4..N columns). Exclusive with fields/expression.",
            },
            "extra_columns": {
                "type": "array",
                "description": 'Renames for Value4..ValueN columns: [{source: "[Value4]", name, hidden}]. Auto-derived with `clone_from`.',
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": 'Source column ref like "[Value4]"',
                        },
                        "name": {"type": "string", "description": "New display name"},
                        "hidden": {"type": "boolean", "description": "Hide the column"},
                    },
                    "required": ["source", "name"],
                },
            },
            "field": {
                "type": "object",
                "description": (
                    "Single field row to append (field_parameter add_field). "
                    "Shape: {label, reference, order?}."
                ),
                "properties": {
                    "label": {"type": "string"},
                    "reference": {"type": "string"},
                    "order": {"type": "integer"},
                },
                "required": ["label", "reference"],
            },
            "label": {
                "type": "string",
                "description": (
                    "Field-parameter row label to remove (field_parameter remove_field)."
                ),
            },
            "labels_in_order": {
                "type": "array",
                "description": "field_parameter reorder_fields: every existing label exactly once, in order.",
                "items": {"type": "string"},
            },
            # Function (DAX UDF) — object_type=function
            "function_name": {
                "type": "string",
                "description": "DAX UDF name (function get/create/update/delete)",
            },
            "parameters": {
                "type": "array",
                "description": (
                    "DAX UDF parameter list (function create). Each item: "
                    "{name, type?, description?}. type defaults to Variant."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
            "package_id": {
                "type": "string",
                "description": "daxlib package id (function daxlib_install)",
            },
            "prefix": {
                "type": "string",
                "description": "daxlib_install: namespace prefix for installed UDF names (e.g. 'Money').",
            },
            "force": {
                "type": "boolean",
                "description": (
                    "daxlib_install: overwrite colliding UDFs (else name_collision). "
                    "annotation delete: required for PBI_* annotations."
                ),
                "default": False,
            },
            "query": {
                "type": "string",
                "description": (
                    "Substring filter on package id/name/description " "(function daxlib_search)"
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max packages returned (function daxlib_search, default 50)",
                "default": 50,
            },
            # Perspective-specific
            "perspective_name": {"type": "string", "description": "Perspective name"},
            "members": {
                "type": "array",
                "items": {"type": "object"},
                "description": "perspective set_members: [{table_name, measure_name|column_name|hierarchy_name}]",
            },
            "member_action": {"type": "string", "enum": ["add", "remove"], "default": "add"},
            # Culture-specific
            "culture_name": {"type": "string", "description": "Culture name (e.g. 'fr-FR')"},
            "object_type_target": {
                "type": "string",
                "enum": ["table", "column", "measure", "hierarchy"],
                "description": "Target object type (set_translation)",
            },
            "property_name": {
                "type": "string",
                "description": (
                    "set_translation: caption|description|display_folder. object_type=any: the TOM "
                    "property (list_properties shows them), or Annotations[name] / ExtendedProperties[name]."
                ),
            },
            "value": {
                "type": ["string", "boolean", "number"],
                "description": "Translation value; any set_property: the new value (enum names, true/false, ints).",
            },
            "ref": {
                "type": "string",
                "description": (
                    "any: the object - Table[Column|Measure], table:Name, hierarchy:Table[Name], "
                    "relationship:Name, role:Name, expression:Name, calculation_group:Name, model."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": "any set_property / culture import_translations: validate and report, write nothing.",
            },
            "file_format": {
                "type": "string",
                "enum": ["json", "csv"],
                "description": "culture export_translations / import_translations file format.",
            },
            "input_path": {
                "type": "string",
                "description": "culture import_translations: CSV/JSON file to read.",
            },
            "output_path": {
                "type": "string",
                "description": "culture export_translations: write the file here.",
            },
            "calendar_template": {
                "type": "string",
                "enum": ["standard", "iso_week", "445", "454", "544"],
                "description": "table generate_calendar: column set (iso_week adds ISO 8601 weeks; 445/454/544 = 52/53-week retail calendar).",
            },
            "multiple_or_empty_selection_expression": {
                "type": "string",
                "description": "calculation_group update: what applies when several / no items are selected ('' clears; CL 1605+).",
            },
            "no_selection_expression": {
                "type": "string",
                "description": "calculation_group update: what applies when no item is selected ('' clears; CL 1605+).",
            },
            "selection_expression_behavior": {
                "type": "string",
                "enum": ["automatic", "visual", "nonvisual"],
                "description": "calculation_group update: Model.SelectionExpressionBehavior (model-wide, CL 1609+).",
            },
            "translations": {
                "type": "array",
                "description": "Bulk translation items (culture/bulk_set_translations).",
                "items": {
                    "type": "object",
                    "properties": {
                        "object_type": {
                            "type": "string",
                            "enum": ["table", "column", "measure", "hierarchy"],
                        },
                        "name": {"type": "string"},
                        "property": {
                            "type": "string",
                            "enum": ["caption", "description", "display_folder"],
                        },
                        "value": {"type": "string"},
                        "table_name": {"type": "string"},
                    },
                    "required": ["object_type", "name", "property", "value"],
                },
            },
            # Named expression-specific
            "expression_name": {"type": "string", "description": "Named expression name"},
            # OLS / Role-specific
            "role_name": {"type": "string", "description": "Security role name"},
            "permission": {
                "type": "string",
                "description": (
                    "OLS: None|Read|Default; role: " "None|Read|ReadRefresh|Refresh|Administrator."
                ),
                "x-doc": (
                    "Permission (case-insensitive, strict). OLS: None|Read|Default "
                    "(no column_name = table-level). Role: None|Read|ReadRefresh|Refresh|Administrator."
                ),
            },
            "filter_expression": {
                "type": "string",
                "description": "DAX filter expression for RLS table permission (role set_table_filter)",
            },
            "member_identifier": {
                "type": "string",
                "description": "Member UPN / AAD object ID / domain\\\\user (role add_member/remove_member)",
            },
            "member_type": {
                "type": "string",
                "enum": ["external", "windows"],
                "default": "external",
                "description": "Member type for role add_member (external = AAD/UPN, windows = domain\\\\user)",
            },
            "tenant_id": {
                "type": "string",
                "description": "AAD tenant ID for external members (role add_member, optional)",
            },
            # Annotation-specific
            "target_type": {
                "type": "string",
                "enum": [
                    "model",
                    "table",
                    "column",
                    "measure",
                    "partition",
                    "relationship",
                    "role",
                    "named_expression",
                    "hierarchy",
                    "calculation_item",
                    "perspective",
                    "culture",
                ],
                "description": "Which TOM object type to annotate",
            },
            "annotation_name": {"type": "string", "description": "Annotation key"},
            "annotation_value": {
                "type": "string",
                "description": "Annotation value (set). Strings only; serialize JSON if structured.",
            },
            # Refresh-specific (object_type=model or table/partition refresh)
            "refresh_type": {
                "type": "string",
                "enum": [
                    "full",
                    "automatic",
                    "dataOnly",
                    "calculate",
                    "clearValues",
                    "defragment",
                    "indexes",
                ],
                "description": (
                    "calculate = calc tables / field params; indexes = CL 1707+. No tables or "
                    ">= 10 = catastrophic."
                ),
                "x-doc": "Refresh type (model/table refresh). 'automatic' picks the minimal required refresh; 'calculate' recomputes calculated tables/columns (field parameters); 'full' reprocesses everything; 'indexes' builds the explicit string / full-text indexes only (compatibility level 1707+, refused below it). No `tables` or >= 10 tables = CATASTROPHIC (calculate / indexes: destructive).",
                "default": "automatic",
            },
            "tables": {
                "type": "array",
                "items": {"type": "string"},
                "description": "model refresh: tables to refresh (omit = whole model).",
            },
            # Model-level properties (object_type=model)
            "behavior": {
                "type": "string",
                "enum": ["Automatic", "Independent", "Coalesced"],
                "description": "model set_value_filter_behavior (case-insensitive, CL 1600+). 'Independent' is the 2025+ default.",
            },
            # Durable write journal (object_type=model: history/diff/undo/replay/checkpoint_*)
            "entry_id": {
                "type": "string",
                "description": "model diff / undo: journal entry id (J000012) from history.",
            },
            "entry_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "model replay: only these journal entry ids (default: every unflushed entry).",
            },
            "count": {
                "type": "integer",
                "description": "model undo: revert the last N undoable entries (default 1).",
            },
            "since": {
                "type": "string",
                "description": "model history / replay: ISO timestamp or relative (30m, 2h, 1d).",
            },
            "object": {
                "type": "string",
                "description": "model history: keep entries touching a ref containing this text.",
            },
            "unflushed_only": {
                "type": "boolean",
                "description": "model history: only writes not yet flushed by a verified save.",
            },
            "journal": {
                "type": "string",
                "description": "model history/diff/undo/replay: journal name (default: the connected project's).",
            },
            "checkpoint": {
                "type": "string",
                "description": "model checkpoint_create / checkpoint_restore: checkpoint name.",
            },
            # Parity pack (M4): model properties, levels, query groups, indexing
            "culture": {
                "type": "string",
                "description": "model set_properties: model culture name, e.g. 'en-US'.",
            },
            "discourage_implicit_measures": {
                "type": "boolean",
                "description": "model set_properties: hide implicit measures (Model.DiscourageImplicitMeasures).",
            },
            "compatibility_level": {
                "type": "integer",
                "description": "model set_properties: IRREVERSIBLE upgrade; needs allow_compatibility_upgrade + confirm_destructive.",
            },
            "allow_compatibility_upgrade": {
                "type": "boolean",
                "description": "model set_properties: required opt-in for compatibility_level (no downgrades).",
                "default": False,
            },
            "level_name": {
                "type": "string",
                "description": "hierarchy add_level / remove_level / rename_level: the level name.",
            },
            "query_group": {
                "type": "string",
                "description": "query_group ops: the Power Query folder path, e.g. 'Sources\\Staging'.",
            },
            "calendar_name": {
                "type": "string",
                "description": "calendar ops: calendar name (model-wide unique).",
            },
            "groups": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "calendar create/update: [{time_unit, primary_column, associated_columns?}]; "
                    "time_unit = TOM TimeUnit name (Year, Quarter, Month, Week, Date, MonthOfYear, ...)."
                ),
            },
            "time_related_columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "calendar create/update: columns of the ONE time-related group (flags, relative offsets).",
            },
            "string_indexing_behavior": {
                "type": "string",
                "enum": ["auto", "full", "explicit", "off"],
                "description": "column update: string index (String columns, CL 1707+; explicit = built by refresh_type=indexes).",
            },
            "full_text_indexing_behavior": {
                "type": "string",
                "enum": ["full", "explicit", "off"],
                "description": "column update: full-text index for TEXTCONTAINS / TEXTSIMILARITY (String columns, CL 1708+).",
            },
        },
        "required": ["object_type"],
    },
    # PBIP Operations (07_PBIP_Operations)
    "pbip_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
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
                ],
            },
            "pbip_path": {
                "type": "string",
                "description": (
                    ".pbip, .Report / .SemanticModel folder or project folder (a model covers "
                    "every report bound to it)."
                ),
                "x-doc": (
                    "Path to a report .pbip (that report + its model), a model .pbip /"
                    " .SemanticModel folder (the model + every report bound to it), a"
                    " .Report folder, or a project folder (every report + model in it)"
                ),
            },
            "from_ref": {
                "type": "string",
                "description": (
                    "git_diff: start ref (commit / branch / tag). With to_ref (default HEAD)"
                    " = commit-diff mode; omitted = working-tree mode (staged + unstaged +"
                    " untracked vs HEAD). compare_models: read source_path as committed at this ref"
                ),
            },
            "to_ref": {
                "type": "string",
                "description": "git_diff: end ref (default HEAD). compare_models: read target_path as committed at this ref",
            },
            "base_branch": {
                "type": "string",
                "description": "git_diff: PR mode — changes of HEAD since the merge base with this branch",
            },
            "include_reasons": {
                "type": "boolean",
                "description": (
                    "query_unused: also return usage_reasons (why every USED object is used:"
                    " visual / filter / bookmark / RLS / relationship / UDF / measure ...)"
                ),
            },
            "level": {
                "type": "string",
                "enum": ["load", "commit"],
                "description": "validate_report / preflight: load = will Desktop open it; commit = also safe to commit.",
            },
            "report_author_cli": {
                "type": "boolean",
                "description": "validate_report / preflight: also run powerbi-report-author validate when installed (default true).",
            },
            "git_base": {
                "type": "string",
                "description": "validate_report / preflight: git ref; commit level checks the changes since it.",
            },
            "include_unstaged": {
                "type": "boolean",
                "description": "validate_report / preflight: include unstaged changes in the git scope.",
            },
            "max_findings": {
                "type": "integer",
                "minimum": 0,
                "description": "validate_report: inline findings cap (10 concise / 200); by_code counts all",
            },
            "tom_check": {
                "type": "string",
                "enum": ["auto", "off", "required"],
                "description": (
                    "validate_model: also deserialize with the bundled TOM TmdlSerializer"
                    " (default auto = when the CLR is available)"
                ),
            },
            "output_path": {
                "type": "string",
                "description": "Output path",
            },
            "source_path": {
                "type": "string",
                "description": "Source PBIP path (compare_models)",
            },
            "target_path": {
                "type": "string",
                "description": "Target PBIP path (compare_models)",
            },
            "object_name": {
                "type": "string",
                "description": ("Object name (query_dependencies," " e.g. '[Total Sales]')"),
            },
            "direction": {
                "type": "string",
                "enum": ["forward", "reverse", "both"],
            },
            "table": {
                "type": "string",
                "description": "Table filter (query_measures)",
            },
            "display_folder": {
                "type": "string",
                "description": ("Display folder filter (query_measures)"),
            },
            "pattern": {
                "type": "string",
                "description": ("Name pattern regex (query_measures)"),
            },
            "expression_search": {
                "type": "string",
                "description": ("DAX expression regex search" " (query_measures)"),
            },
            "main_item": {
                "type": "string",
                "description": ("Initial selected item in dependency diagram" " (dependency_html)"),
            },
            "auto_open": {
                "type": "boolean",
                "description": "Open the generated HTML in the browser (analyze, dependency_html)",
            },
            "output_format": {
                "type": "string",
                "enum": ["summary", "detailed", "html", "json"],
                "description": (
                    "analyze: html (default) | json | summary | detailed; "
                    "aggregation_analysis: report format."
                ),
                "x-doc": (
                    "analyze: html (default, HTML model report) | json / summary (write the"
                    " analysis JSON, return a summary) | detailed (also return it inline);"
                    " aggregation_analysis: report format"
                ),
            },
            "include_visual_details": {
                "type": "boolean",
                "description": ("Include per-visual details in output" " (aggregation_analysis)"),
            },
            "page_filter": {
                "type": "string",
                "description": ("Filter pages by name substring" " (aggregation_analysis)"),
            },
        },
        "required": ["operation"],
    },
    # Query Operations (04_Query_Operations)
    "query_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
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
                ],
            },
            "measures": {
                "type": "array",
                "items": {"type": "string"},
                "description": "warm_cache: measure names to evaluate",
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "warm_cache: 'Table'[Column] refs to scan",
            },
            "view": {
                "type": "string",
                "enum": [
                    "measures",
                    "tables",
                    "columns",
                    "relationships",
                    "summary",
                    "partitions",
                    "segments",
                    "all",
                ],
                "description": "info_view / vertipaq_analyzer: which view (guide lists them).",
                "x-doc": (
                    "info_view: INFO.VIEW.* target (measures|tables|columns|relationships). "
                    "vertipaq_analyzer: which metrics view to return "
                    "(summary|tables|columns|relationships|partitions|segments|all, default summary)"
                ),
            },
            "include_stats": {
                "type": "boolean",
                "description": (
                    "vertipaq_analyzer: run the DAX COUNTROWS/DISTINCTCOUNT batches for true "
                    "cardinality and referential-integrity checks (default true). Set false to "
                    "stay DMV-only — much cheaper on a large model."
                ),
                "default": True,
            },
            "export_path": {
                "type": "string",
                "description": (
                    "vertipaq_analyzer: write the full metrics to this file (.json or .csv) "
                    "instead of relying on the inline response"
                ),
            },
            "include_table_rollup": {
                "type": "boolean",
                "description": "vertipaq_report: include per-table size rollup (default true)",
                "default": True,
            },
            "include_verdict": {
                "type": "boolean",
                "description": "vertipaq_report: include hot/warn/info verdict per column (default true)",
                "default": True,
            },
            "query": {
                "type": "string",
                "description": "DAX query (impersonate_run, simulate_as_user, test_rls)",
            },
            "roles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Role names to impersonate (impersonate_run; comma-string also accepted)",
            },
            "effective_user_name": {
                "type": "string",
                "description": "Optional UPN to impersonate via EffectiveUserName (impersonate_run; Service/SSAS only)",
            },
            "custom_data": {
                "type": "string",
                "description": "Optional CustomData= value for CUSTOMDATA()-based RLS (impersonate_run)",
            },
            "as_user": {
                "type": "string",
                "description": (
                    "simulate_as_user: UPN substituted for "
                    "USERPRINCIPALNAME()/USERNAME()/USEROBJECTID() in the role filter (no "
                    "model change)."
                ),
                "x-doc": (
                    "UPN to simulate (simulate_as_user). PBI Desktop workaround: "
                    "USERPRINCIPALNAME() / USERNAME() / USEROBJECTID() calls in the "
                    "role's filter expression are rewritten to this literal string at "
                    "query time, then applied via CALCULATETABLE(FILTER('table', ...)). "
                    "No model mutation; works on Desktop where EffectiveUserName fails."
                ),
            },
            "role_name": {
                "type": "string",
                "description": "RLS role to test (test_rls) OR whose filter to apply (simulate_as_user)",
            },
            "probe_identities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "rls_audit: extra identities to probe.",
            },
            "top_n": {
                "type": "integer",
                "description": "Max rows to return (impersonate_run, default 1000)",
                "default": 1000,
            },
            "pattern": {
                "type": "string",
                "description": (
                    "Search text (search_objects): case-insensitive substring via DAX "
                    "SEARCH(); * and ? are stripped — not a regex"
                ),
            },
            "probe": {
                "type": "string",
                "description": "Probe name (dmv_probe). Use list_dmv_probes to enumerate.",
            },
            "types": {
                "type": "array",
                "items": {"type": "string", "enum": ["tables", "columns", "measures"]},
                "description": "Object types (search_objects)",
            },
            "test_table": {
                "type": "string",
                "description": "Table to auto-generate count query for (test_rls)",
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Max results (m_expressions: partitions first, then shared expressions; "
                    "data_sources TOM fallback)"
                ),
            },
            "search_text": {
                "type": "string",
                "description": "Text to search for (search_string)",
            },
            "search_in_expression": {
                "type": "boolean",
                "description": "Search in DAX expressions (search_string)",
                "default": True,
            },
            "search_in_name": {
                "type": "boolean",
                "description": "Search in object names (search_string)",
                "default": True,
            },
        },
        "required": ["operation"],
    },
    # TMDL Operations (02_TMDL_Operations)
    "tmdl_operations": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "export",
                    "find_replace",
                    "bulk_rename",
                    "generate_script",
                    "migrate_measures",
                    "format_dax",
                ],
            },
            "measure_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "format_dax: 'Table[Name]' refs to format (or format_all). Sends DAX to "
                    "daxformatter.com - off until dax_formatter.enabled=true."
                ),
                "x-doc": "Measure refs in 'Table[Name]' form to format (format_dax). Mutually exclusive with format_all. PRIVACY: format_dax sends DAX to the external service daxformatter.com and is OFF until the user sets dax_formatter.enabled=true (config/local_config.json) or MCP_DAX_FORMATTER_ENABLED=1. Only token-equivalent results are written back.",
            },
            "time_budget_s": {
                "type": "number",
                "description": "format_dax: stop sending after this many seconds (default 120); unprocessed refs are returned in 'remaining'.",
            },
            "format_all": {
                "type": "boolean",
                "description": "Format every measure in the live model (format_dax). Default false.",
                "default": False,
            },
            "max_line_length": {
                "type": "integer",
                "description": "Max line length passed to daxformatter.com (format_dax). Default 90.",
                "default": 90,
            },
            "engine": {
                "type": "string",
                "enum": ["daxformatter", "local"],
                "description": "format_dax: daxformatter (external, opt-in) | local (in-process, no network).",
            },
            "skip_space_after_function": {
                "type": "boolean",
                "description": "format_dax engine=local: write SUM( x ) instead of SUM ( x ).",
            },
            "on_error": {
                "type": "string",
                "enum": ["skip", "abort"],
                "description": "Behavior when formatter rejects an expression (format_dax). Default skip.",
                "default": "skip",
            },
            "output_dir": {"type": "string", "description": "Output directory"},
            "tmdl_path": {
                "type": "string",
                "description": (
                    "TMDL definition folder, its .SemanticModel folder, or a model .pbip "
                    "(find_replace, bulk_rename)"
                ),
            },
            "pattern": {"type": "string", "description": "Find pattern (find_replace)"},
            "replacement": {
                "type": "string",
                "description": (
                    "Replacement text (find_replace; with regex=true group references "
                    "such as \\1 or \\g<name> are expanded)"
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "Preview only. Default TRUE for find_replace / bulk_rename / "
                    "migrate_measures; default FALSE for format_dax (live write)."
                ),
            },
            "regex": {"type": "boolean", "description": "Use regex (default: false)"},
            "case_sensitive": {"type": "boolean", "description": "Case sensitive (default: true)"},
            "target": {
                "type": "string",
                "enum": [
                    "measures",
                    "columns",
                    "calculation_items",
                    "functions",
                    "roles",
                    "partitions",
                    "all",
                ],
                "description": "Objects find_replace may touch (default: 'all')",
            },
            "scope": {
                "type": "string",
                "enum": ["expression", "object", "file"],
                "description": (
                    "find_replace: expression (default; '=' bodies, strings/comments skipped) "
                    "| object | file (target='all' only)."
                ),
                "x-doc": (
                    "find_replace scope (default 'expression'): 'expression' = the '=' bodies "
                    "of the targeted objects only (DAX strings/comments skipped); 'object' = "
                    "every line of each targeted object block; 'file' = whole files "
                    "(target='all' only; the pre-2026-09 behaviour)"
                ),
            },
            "include_strings_and_comments": {
                "type": "boolean",
                "description": "find_replace scope='expression': also replace inside DAX strings/comments",
            },
            "cascade_to_reports": {
                "type": ["boolean", "string"],
                "description": (
                    "bulk_rename: also rename PBIR report bindings - true = every bound "
                    ".Report, or a report/.pbip path. Catastrophic under the gate."
                ),
                "x-doc": (
                    "bulk_rename: also rename measure/column bindings in PBIR report JSON. "
                    "true = every .Report bound to this model (definition.pbir); a path "
                    "(.Report folder, .pbip or folder) = the bound reports it resolves to. "
                    "Runs only after the TMDL rename committed; table renames are refused. "
                    "CATASTROPHIC under the gate (pass confirm_destructive)."
                ),
            },
            "renames": {
                "type": "array",
                "description": "Rename operations",
                "items": {
                    "type": "object",
                    "properties": {
                        "object_type": {"type": "string"},
                        "old_name": {"type": "string"},
                        "new_name": {"type": "string"},
                        "table_name": {"type": "string"},
                    },
                    "required": ["old_name", "new_name"],
                },
            },
            "update_references": {
                "type": "boolean",
                "description": (
                    "bulk_rename: rewrite DAX references (default true; structural refs "
                    "always update)."
                ),
                "x-doc": (
                    "bulk_rename: update DAX references (default: true). Structural references "
                    "(relationships, sortByColumn, hierarchy levels, ref table, roles, "
                    "perspectives, translations) are always updated."
                ),
            },
            "definition": {
                "type": "object",
                "description": "generate_script: the object definition to emit as TMDL",
            },
            "object_type": {
                "type": "string",
                "enum": ["table", "measure", "relationship", "calc_group"],
                "description": "generate_script: object type (default: 'table')",
            },
            "source_path": {
                "type": "string",
                "description": "migrate_measures: source table .tmdl file",
            },
            "target_path": {
                "type": "string",
                "description": "migrate_measures: target table .tmdl file (created if missing)",
            },
            "display_folder_filter": {
                "type": "string",
                "description": "migrate_measures: display-folder prefix filter",
            },
            "replace_target": {
                "type": "boolean",
                "description": (
                    "migrate_measures: replace the target table's MEASURES (partitions, columns "
                    "and annotations are always kept; default: false = append)"
                ),
            },
            "skip_duplicates": {
                "type": "boolean",
                "description": "Skip existing measures (default: true)",
            },
        },
        "required": ["operation"],
    },
}

# 00_Client_Mode: client masking: the local gateway (conversation) and, only when
# client_gateway.mcp_masking is on, this server's own results (masked through the gateway).
TOOL_SCHEMAS["client_mode"] = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": [
                "status",
                "on",
                "off",
                "refresh",
                "gateway_status",
                "gateway_start",
                "gateway_stop",
                "route_folder",
                "unroute_folder",
                "route_all",
                "unroute_all",
                "setup",
                "selftest",
            ],
            "x-doc": (
                "status / gateway_status: is this chat routed, gateway running, this chat's mode, "
                "dictionary COUNTS, routed folders - never a value. "
                "on: the same as the user's typed 'masking on': starts the gateway, masks this "
                "chat and routes its project folder (masking is off by default and the gateway "
                "runs only while a chat has masking on). "
                "off: never through a tool: the USER types 'masking off' in the chat (returns "
                "user_action_required). "
                "refresh: re-read the open Power BI models' client values now. "
                "gateway_start / gateway_stop: start / stop the local gateway (stopping makes "
                "routed chats fail until it runs again). "
                "route_folder / unroute_folder: send a folder's Claude Code traffic through the "
                "gateway (needs folder_path); route_all / unroute_all: every chat. Routing only, "
                "not a statement about the data. "
                "setup: returns the command the USER runs in a terminal to install the routed "
                "masking gateway (plan first, --yes applies; never run by a tool) plus the plan "
                "as far as known. "
                "selftest: replay every recorded wire fixture of every named app through the "
                "current adapters (offline), one line per app."
            ),
        },
        "folder_path": {
            "type": "string",
            "description": "route_folder / unroute_folder: the project folder",
        },
    },
    "required": ["operation"],
}


# F-27: a write call may carry ``request_id`` (dispatch.py replays a recorded outcome for 10 min).
# Declared here so the validator type-checks it; DEFERRED in tool_surface.py so tools/list does not grow.
REQUEST_ID_PROPERTY = {
    "type": "string",
    "maxLength": 128,
    "description": "Idempotency key: re-sending the same write returns the recorded outcome (10 min) instead of re-executing.",
}
for _schema_key in (
    "model_operations",
    "batch_operations",
    "bookmark_operations",
    "page_operations",
    "report_operations",
    "theme_operations",
    "visual_operations",
    "tmdl_operations",
):
    TOOL_SCHEMAS[_schema_key]["properties"].setdefault("request_id", dict(REQUEST_ID_PROPERTY))
