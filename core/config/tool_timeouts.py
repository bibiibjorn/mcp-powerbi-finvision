"""
Tool-specific timeout configuration and enforcement.
"""

from typing import Dict, List, Optional
import logging

logger = logging.getLogger("mcp_powerbi_finvision.tool_timeouts")


class ToolTimeoutManager:
    """Manages per-tool timeout settings, keyed by REGISTERED tool name.

    K-06: the legacy v6 table (``run_dax``, ``full_analysis`` …, 47 names) and
    the matching config keys never matched what the dispatcher passes
    (``04_Run_DAX``), so none of them ever applied. They were removed; config
    ``tool_timeouts`` is now keyed by registered names (unknown keys are
    reported by :meth:`unknown_keys` and logged at startup).
    """

    # Registered MCP tool names (NN_Tool_Name) → timeout budget in seconds.
    #
    # server/dispatch.py calls get_timeout() with the REGISTERED tool name
    # ('06_Analysis_Operations', '04_Run_DAX').
    #
    # Multi-op dispatchers (most 02_*/07_* tools) get their SLOWEST operation's
    # budget: a timeout too generous merely delays recovery, but a timeout too
    # short abandons a legitimate write mid-flight (outcome unknown).
    _REGISTERED_TOOL_TIMEOUTS = {
        # CORE
        "01_Connection": 15,  # detect/connect/reconnect
        "10_Show_User_Guide": 10,
        # MODEL — live TOM CRUD + refresh (model/refresh full can be very slow)
        "02_Model_Operations": 300,
        # TMDL — export/find_replace/bulk_rename + format_dax format_all is slow
        "02_TMDL_Operations": 180,
        "15_DAX_Refactor": 120,
        # BATCH — transactional bulk CRUD across up to 200 items
        "03_Batch_Operations": 180,
        # QUERY
        "04_Run_DAX": 60,
        "04_Query_Operations": 60,  # includes DMV probes + warm_cache
        # DAX
        "05_DAX_Intelligence": 120,  # analyze/lint_model over every measure
        "05_Column_Usage_Mapping": 60,
        # ANALYSIS — BPA + performance + integrity (full_analysis == 300)
        "06_Analysis_Operations": 300,
        "16_Dependency_Graph": 60,
        # PBIP — offline disk analysis / authoring
        "02_QA_Linguistic": 30,
        "07_Report_Operations": 60,
        "07_PBIP_Operations": 300,  # full offline analyze + HTML generation
        "07_Page_Operations": 60,
        "07_Visual_Operations": 60,
        "07_Bookmark_Operations": 60,
        "07_Theme_Operations": 60,
        "11_PBIP_Authoring": 120,  # pbir CLI subprocess (default 120s timeout)
        # DOCS — Word generation is the slowest single op (300)
        "08_Documentation_Word": 300,
        # DEBUG
        "09_Debug_Operations": 180,  # debug_loop iterates + traces
        "09_Validate": 120,  # filter_permutation runs many queries
        "09_Profile": 180,  # multi-iteration page profiling
        "09_Document": 120,
        "13_Query_Plan": 120,  # benchmark runs up to 50 cold/warm passes
        "17_Desktop_Bridge": 120,  # reload + settle + screenshot chain
        "00_Client_Mode": 150,  # `on` / `refresh` read every client column of the model
    }

    # Kept as an alias for callers that introspect the defaults.
    DEFAULT_TIMEOUTS = _REGISTERED_TOOL_TIMEOUTS

    def __init__(self, custom_timeouts: Optional[Dict[str, int]] = None):
        """
        Initialize timeout manager.

        Args:
            custom_timeouts: Optional dict of registered tool name -> timeout_seconds
        """
        self.timeouts: Dict[str, int] = dict(self._REGISTERED_TOOL_TIMEOUTS)
        self._custom: Dict[str, int] = {}
        for name, value in (custom_timeouts or {}).items():
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                logger.warning("Ignoring non-numeric tool_timeouts[%r]=%r", name, value)
                continue
            if seconds <= 0:
                logger.warning("Ignoring non-positive tool_timeouts[%r]=%r", name, value)
                continue
            self._custom[name] = seconds
        self.timeouts.update(self._custom)

        logger.info(f"Timeout manager initialized with {len(self.timeouts)} tool configs")

    def unknown_keys(self) -> List[str]:
        """Configured keys that are not registered tool names (ignored)."""
        return sorted(k for k in self._custom if k not in self._REGISTERED_TOOL_TIMEOUTS)

    def get_timeout(self, tool_name: str, default: int = 60) -> float:
        """
        Get timeout for a tool.

        Args:
            tool_name: Registered tool name ('04_Run_DAX')
            default: Default timeout if tool not configured

        Returns:
            Timeout in seconds
        """
        timeout = self.timeouts.get(tool_name)
        if timeout is not None:
            return timeout
        return default
