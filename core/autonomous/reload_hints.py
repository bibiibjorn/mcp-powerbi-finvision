"""
Reload hints — mark responses from PBIP-file-writing ops so the caller knows
the RUNNING PBI Desktop must be reloaded in place (17_Desktop_Bridge — no
close/reopen) to see the change.

Live-TOM ops (02_Model_Operations measure/table/column CRUD) propagate
immediately and do NOT get the hint.
"""

from __future__ import annotations

from typing import Dict, Any, Set, Tuple

# (tool_name, operation) tuples that write to PBIP files on disk.
# Op names here MUST match the dispatcher keys in the corresponding handler
# (server/handlers/<tool>_handler.py). Mismatches silently skip the hint.
OPS_REQUIRING_RELOAD: Set[Tuple[str, str]] = {
    # Visual operations — all disk mutations
    ("07_Visual_Operations", "create"),
    ("07_Visual_Operations", "create_group"),
    ("07_Visual_Operations", "delete"),
    ("07_Visual_Operations", "update_position"),
    ("07_Visual_Operations", "update_visual_config"),
    ("07_Visual_Operations", "update_formatting"),
    ("07_Visual_Operations", "align"),
    ("07_Visual_Operations", "add_field"),
    ("07_Visual_Operations", "remove_field"),
    ("07_Visual_Operations", "set_sort"),
    ("07_Visual_Operations", "set_action"),
    ("07_Visual_Operations", "inject_code"),
    ("07_Visual_Operations", "manage_visual_calcs"),
    ("07_Visual_Operations", "configure_slicer"),
    ("07_Visual_Operations", "replace_measure"),
    ("07_Visual_Operations", "sync_visual"),
    ("07_Visual_Operations", "sync_column_widths"),
    ("07_Visual_Operations", "sync_formatting"),
    ("07_Visual_Operations", "set_raw_property"),
    ("07_Visual_Operations", "delete_raw_property"),
    ("07_Visual_Operations", "bulk_rename_pane_names"),
    ("07_Visual_Operations", "clear_default_selection"),
    ("07_Visual_Operations", "set_default_selection"),
    # Page operations — all disk mutations (list/list_interactions/list_filters are READ)
    ("07_Page_Operations", "create"),
    ("07_Page_Operations", "clone"),
    ("07_Page_Operations", "delete"),
    ("07_Page_Operations", "reorder"),
    ("07_Page_Operations", "resize"),
    ("07_Page_Operations", "set_display"),
    ("07_Page_Operations", "set_background"),
    ("07_Page_Operations", "set_wallpaper"),
    ("07_Page_Operations", "set_drillthrough"),
    ("07_Page_Operations", "set_tooltip"),
    ("07_Page_Operations", "hide"),
    ("07_Page_Operations", "show"),
    ("07_Page_Operations", "set_interaction"),
    ("07_Page_Operations", "bulk_set_interactions"),
    ("07_Page_Operations", "add_filter"),
    ("07_Page_Operations", "set_filter"),
    ("07_Page_Operations", "clear_filters"),
    ("07_Page_Operations", "hide_filter"),
    ("07_Page_Operations", "unhide_filter"),
    ("07_Page_Operations", "lock_filter"),
    ("07_Page_Operations", "unlock_filter"),
    # Authoring — all disk mutations (list_templates/get_template are READ)
    ("11_PBIP_Authoring", "clone_page"),
    ("11_PBIP_Authoring", "clone_report"),
    ("11_PBIP_Authoring", "create_page"),
    ("11_PBIP_Authoring", "create_visual"),
    ("11_PBIP_Authoring", "create_visual_group"),
    ("11_PBIP_Authoring", "delete_page"),
    ("11_PBIP_Authoring", "delete_visual"),
    ("11_PBIP_Authoring", "pbir_add_visuals"),
    # Theme — all disk mutations (get_theme/list_text_classes/list_cf/analyze_compliance/export_theme are READ)
    ("07_Theme_Operations", "set_colors"),
    ("07_Theme_Operations", "set_formatting"),
    ("07_Theme_Operations", "push_visual"),
    ("07_Theme_Operations", "set_font"),
    ("07_Theme_Operations", "add_cf"),
    ("07_Theme_Operations", "remove_cf"),
    ("07_Theme_Operations", "copy_cf"),
    # T1-H additions:
    ("07_Theme_Operations", "apply_theme"),
    ("07_Theme_Operations", "import_theme"),
    ("07_Theme_Operations", "clear_visual_overrides"),
    # TMDL writes (export + generate_script are READ)
    ("02_TMDL_Operations", "bulk_rename"),
    ("02_TMDL_Operations", "find_replace"),
    ("02_TMDL_Operations", "migrate_measures"),
    # Bookmark writes (list + analyze + get are READ)
    ("07_Bookmark_Operations", "create"),
    ("07_Bookmark_Operations", "rename"),
    ("07_Bookmark_Operations", "delete"),
    ("07_Bookmark_Operations", "set_capture"),
    ("07_Bookmark_Operations", "set_affected_visuals"),
    # T1-G additions:
    ("07_Bookmark_Operations", "update"),
    ("07_Bookmark_Operations", "create_group"),
    ("07_Bookmark_Operations", "update_group"),
    ("07_Bookmark_Operations", "delete_group"),
    # T0-8 fill-ins:
    # QA Linguistic writes mutate linguisticMetadata.json (load + audit are READ)
    ("02_QA_Linguistic", "set_include_in_qa"),
    ("02_QA_Linguistic", "add_synonym"),
    ("02_QA_Linguistic", "remove_synonym"),
    # Report-level disk writes (info / measure_usage / discover_schema / backup are READ;
    # restore is CATASTROPHIC and already gated, but it still mutates disk)
    ("07_Report_Operations", "rename"),
    ("07_Report_Operations", "rebind"),
    ("07_Report_Operations", "restore"),
    ("07_Report_Operations", "manage_extension_measures"),
}

# J-02: the old hint said "If Desktop has unsaved in-memory changes to keep, pass
# save_first=true". By the time this hint arrives the disk edit EXISTS, and a
# save now makes Desktop write its OLD in-memory copy over it. The only valid
# order is: save BEFORE disk edits; after disk edits reload, never save first.
_HINT = (
    "Disk edit written; the RUNNING Desktop still shows the old version. Reload it in "
    "place with 17_Desktop_Bridge operation=reload_and_screenshot — ONCE after the whole "
    "batch of disk edits, not after each one. Do NOT save first: a save now would "
    "overwrite this edit with Desktop's in-memory copy (the bridge save refuses while "
    "disk edits are pending). Rule: save BEFORE disk edits; after disk edits reload, "
    "never save first. If the reload refuses because Desktop also holds unsaved "
    "in-memory (live-TOM) edits, no ordering keeps both: reload with discard_unsaved=true "
    "and redo the live-TOM edits, or save with overwrite_disk_edits=true and redo the "
    "disk edits."
)

_CHAIN = [
    "17_Desktop_Bridge:reload_and_screenshot",
]

# TMDL disk edits: a PLAIN bridge reload cannot apply them - the MS CLI's ``reload`` sends
# ``reloadModelDefinition: false`` (verified on 0.1.1 and 1.0.0) - and a save destroys them.
# ``17_Desktop_Bridge reload include_model=true`` (BR-04) CAN apply them in place, but only
# metadata-only ones (measures, calculated columns, relationships, formats, descriptions...):
# it classifies the disk-vs-live difference first and refuses partition / M / expression /
# data-source / import-table changes (``model_reload_unsafe_change``). The classification
# happens at reload time, not at write time, so this hint states the rule, not a verdict.
_TMDL_TOOLS = frozenset({"02_TMDL_Operations"})
_TMDL_HINT = (
    "TMDL written on disk; the RUNNING Desktop's model does NOT see it, and a PLAIN bridge "
    "reload cannot apply it (the MS CLI's reload sends reloadModelDefinition:false). NEVER save "
    "Desktop now - a save overwrites this TMDL edit with the in-memory model. If the edit is "
    "metadata-only (measures, calculated columns, relationships, formats, descriptions) apply it "
    "in place with 17_Desktop_Bridge operation=reload_and_screenshot include_model=true: a "
    "classifier checks it first and refuses partition / M / expression / data-source / "
    "import-table changes with model_reload_unsafe_change. For those (or if it refuses) ask the "
    "user to close Power BI Desktop WITHOUT saving (never close it unprompted), then reopen "
    "the project with 17_Desktop_Bridge operation=open (pbip_path=<the .pbip>). Alternative: "
    "revert the file edit and make the same change via live TOM (02_Model_Operations), which "
    "needs no reload."
)
_TMDL_CHAIN = [
    "17_Desktop_Bridge:reload_and_screenshot include_model=true (metadata-only TMDL edits)",
    "otherwise ask the user: close Power BI Desktop WITHOUT saving",
    "17_Desktop_Bridge:open",
]

# Response keys that may carry the written project / file path.
_RESPONSE_PATH_KEYS = (
    "pbip_path",
    "tmdl_path",
    "report_path",
    "definition_path",
    "visual_path",
    "page_path",
    "file_path",
    "path",
)


def is_reload_required(tool_name: str, operation: str) -> bool:
    """True if the (tool, op) pair writes to PBIP files on disk."""
    return (tool_name, operation) in OPS_REQUIRING_RELOAD


def _record_in_ledger(response: Dict[str, Any], tool_name: str, operation: str) -> None:
    """Best-effort feed of the edit ledger from paths the response names.

    The primary feed is ``pbip_utils``' write listener (every PBIR write) plus
    the dispatcher hook (args); this catches writers that bypass both.
    """
    try:
        from core.autonomous import edit_ledger

        kind = "tmdl" if tool_name in _TMDL_TOOLS else "pbir"
        for key in _RESPONSE_PATH_KEYS:
            val = response.get(key)
            if isinstance(val, str) and val.strip():
                edit_ledger.record_disk_write(val, kind=kind, source=f"{tool_name}:{operation}")
    except Exception:  # noqa: BLE001 - a hint must never fail the op
        pass


def attach_reload_hint(
    response: Dict[str, Any],
    tool_name: str,
    operation: str,
) -> Dict[str, Any]:
    """
    Mutate `response` in place to add reload-required metadata if applicable.
    No-op on failed responses (success=False) or non-reload-requiring ops.

    PBIR ops get ``reload_op_chain = [17_Desktop_Bridge:reload_and_screenshot]``;
    TMDL ops get ``reload_applies: false`` (a PLAIN reload) + ``reopen_required: true``
    (unless the edit is metadata-only) and a chain that leads with
    ``reload_and_screenshot include_model=true`` and falls back to the
    close-without-saving -> ``open`` path. ``include_model_reload: true`` marks that the
    classified model reload is available.

    Returns the same dict for chaining.
    """
    if not isinstance(response, dict):
        return response
    if response.get("success") is False:
        return response
    if not is_reload_required(tool_name, operation):
        return response

    _record_in_ledger(response, tool_name, operation)
    response["reload_required"] = True
    if tool_name in _TMDL_TOOLS:
        response["reload_applies"] = False
        response["reopen_required"] = True
        response["include_model_reload"] = True
        response["reload_hint"] = _TMDL_HINT
        response["reload_op_chain"] = list(_TMDL_CHAIN)
    else:
        response["reload_hint"] = _HINT
        response["reload_op_chain"] = list(_CHAIN)
    response["save_forbidden_until_reload"] = True

    return response
