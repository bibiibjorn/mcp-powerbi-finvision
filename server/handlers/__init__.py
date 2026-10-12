"""
Server Handlers Package — 28 tools registered (v7.20)

Tool inventory:
  00_Client_Mode (Client Guard control), 01_Connection, 02_Model_Operations (incl. calc-group migration via
  object_type='calculation_group' operation='detect'/'scaffold'/'list_patterns'),
  02_TMDL_Operations, 02_QA_Linguistic, 03_Batch_Operations,
  04_Run_DAX, 04_Query_Operations, 05_DAX_Intelligence,
  05_Column_Usage_Mapping, 06_Analysis_Operations, 07_Report_Operations,
  07_PBIP_Operations, 07_Page_Operations, 07_Visual_Operations,
  07_Bookmark_Operations, 07_Theme_Operations, 08_Documentation_Word,
  09_Debug_Operations, 09_Validate, 09_Profile, 09_Document,
  10_Show_User_Guide, 11_PBIP_Authoring, 13_Query_Plan,
  15_DAX_Refactor, 16_Dependency_Graph, 17_Desktop_Bridge

(SVG_Visual_Operations removed in v7.6; 12_Autonomous_Workflow removed in v7.8 —
 live iteration now goes through 17_Desktop_Bridge. The exact registered set is
 asserted at bootstrap and surfaced live by 10_Show_User_Guide.)
"""

from server.handlers.client_mode_handler import register_client_mode_handler
from server.handlers.connection_handler import register_connection_handlers
from server.handlers.model_operations_handler import register_model_operations_handler
from server.handlers.tmdl_handler import register_tmdl_operations_handler
from server.handlers.qa_linguistic_handler import register_qa_linguistic_handler
from server.handlers.batch_operations_handler import register_batch_operations_handler
from server.handlers.query_handler import register_query_handlers
from server.handlers.dax_context_handler import register_dax_handlers
from server.handlers.column_usage_handler import register_column_usage_handler
from server.handlers.analysis_handler import register_analysis_handlers
from server.handlers.report_operations_handler import register_report_operations_handler
from server.handlers.pbip_operations_handler import register_pbip_operations_handler
from server.handlers.page_operations_handler import register_page_operations_handler
from server.handlers.visual_operations_handler import register_visual_operations_handler
from server.handlers.bookmark_operations_handler import register_bookmark_operations_handler
from server.handlers.theme_operations_handler import register_theme_operations_handler
from server.handlers.documentation_handler import register_documentation_handlers
from server.handlers.debug_handler import register_debug_handlers
from server.handlers.user_guide_handler import register_user_guide_handlers
from server.handlers.authoring_handler import register_authoring_handler
from server.handlers.desktop_bridge_handler import register_desktop_bridge_handler
from server.handlers.dependency_graph_handler import register_dependency_graph_handler
from server.handlers.query_plan_handler import register_query_plan_handler
from server.handlers.dax_codemods_handler import register_dax_codemods_handler


def register_all_handlers(registry):
    """Register all handlers with the registry — 28 tools total"""
    register_client_mode_handler(registry)  # 00_Client_Mode
    register_connection_handlers(registry)  # 01_Connection
    register_model_operations_handler(registry)  # 02_Model_Operations
    register_tmdl_operations_handler(registry)  # 02_TMDL_Operations
    register_qa_linguistic_handler(registry)  # 02_QA_Linguistic
    register_batch_operations_handler(registry)  # 03_Batch_Operations
    register_query_handlers(registry)  # 04_Run_DAX + 04_Query_Operations
    register_dax_handlers(registry)  # 05_DAX_Intelligence
    register_column_usage_handler(registry)  # 05_Column_Usage_Mapping
    register_analysis_handlers(registry)  # 06_Analysis_Operations
    register_report_operations_handler(registry)  # 07_Report_Operations
    register_pbip_operations_handler(registry)  # 07_PBIP_Operations
    register_page_operations_handler(registry)  # 07_Page_Operations
    register_visual_operations_handler(registry)  # 07_Visual_Operations
    register_bookmark_operations_handler(registry)  # 07_Bookmark_Operations
    register_theme_operations_handler(registry)  # 07_Theme_Operations
    register_documentation_handlers(registry)  # 08_Documentation_Word
    register_debug_handlers(
        registry
    )  # 09_Debug_Operations + 09_Validate + 09_Profile + 09_Document
    register_user_guide_handlers(registry)  # 10_Show_User_Guide
    register_authoring_handler(registry)  # 11_PBIP_Authoring
    register_desktop_bridge_handler(registry)  # 17_Desktop_Bridge
    register_dependency_graph_handler(registry)  # 16_Dependency_Graph
    register_query_plan_handler(registry)  # 13_Query_Plan
    register_dax_codemods_handler(registry)  # 15_DAX_Refactor

    # P2-184: assert the registered tool count matches the declared
    # inventory. Silent misses (e.g. a handler that validates out via
    # register()'s missing-field guards) previously only surfaced when
    # a client asked for the missing tool. Fail fast at bootstrap instead —
    # a missing tool means callers get "unknown tool" errors that would
    # otherwise only show up at runtime.
    _EXPECTED_TOOL_COUNT = 28
    actual = registry.total_tools()
    if actual != _EXPECTED_TOOL_COUNT:
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        _logger.error(
            "Tool registry mismatch: expected %d tools, got %d. "
            "Check register_all_handlers for silently-skipped registrations.",
            _EXPECTED_TOOL_COUNT,
            actual,
        )
        raise RuntimeError(
            f"Tool registry bootstrap mismatch: expected {_EXPECTED_TOOL_COUNT} "
            f"tools, registered {actual}. Aborting to surface the misconfiguration."
        )


__all__ = ["register_all_handlers"]
