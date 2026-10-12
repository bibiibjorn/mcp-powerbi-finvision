"""
Agent policy layer for PBIXRay MCP Server — facade over the live orchestrators.

2026-09-22 review (B-31 / K-05 / B-35): only the delegates something actually
calls are kept — ``safe_run_dax`` (04_Run_DAX), the ``analysis_orch`` /
``documentation_orch`` properties (06/08 handlers) and the response-limits
helpers used by ``src/pbixray_server_enhanced.py``. The dead connection /
cache / PBIP / hybrid / intent delegates were removed. Lazy orchestrator
construction is now thread-safe (double-checked under one lock).
"""

import logging
import threading
from typing import Any, Dict, Optional

from core.orchestration.analysis_orchestrator import AnalysisOrchestrator
from core.orchestration.documentation_orchestrator import DocumentationOrchestrator
from core.orchestration.query_orchestrator import QueryOrchestrator
from core.orchestration.query_policy import QueryPolicy

logger = logging.getLogger("mcp_powerbi_finvision")


class AgentPolicy:
    """Facade for the live orchestrator classes."""

    def __init__(self, config, timeout_manager=None, rate_limiter=None, limits_manager=None):
        self.config = config
        self.timeout_manager = timeout_manager
        self.rate_limiter = rate_limiter
        self.limits_manager = limits_manager

        # Lazy-initialized orchestrators (created on demand for faster startup)
        self._query_orch = None
        self._documentation_orch = None
        self._analysis_orch = None
        self._query_policy = None
        self._lazy_lock = threading.RLock()  # re-entrant: query_orch builds query_policy

    def _lazy(self, attr: str, factory):
        value = getattr(self, attr)
        if value is None:
            with self._lazy_lock:
                value = getattr(self, attr)
                if value is None:
                    value = factory()
                    setattr(self, attr, value)
        return value

    @property
    def query_policy(self):
        """Lazy-load query policy"""
        return self._lazy("_query_policy", lambda: QueryPolicy(self.config))

    @property
    def query_orch(self):
        """Lazy-load query orchestrator (wired to the shared QueryPolicy)."""

        def _make():
            orch = QueryOrchestrator(self.config)
            try:
                orch.query_policy = self.query_policy
            except Exception:  # noqa: BLE001 — fallback path inside the orchestrator
                orch.query_policy = None
            return orch

        return self._lazy("_query_orch", _make)

    @property
    def documentation_orch(self):
        """Lazy-load documentation orchestrator"""
        return self._lazy("_documentation_orch", lambda: DocumentationOrchestrator(self.config))

    @property
    def analysis_orch(self):
        """Lazy-load analysis orchestrator"""
        return self._lazy("_analysis_orch", lambda: AnalysisOrchestrator(self.config))

    # ==================== QUERY ====================

    def safe_run_dax(
        self,
        connection_state,
        query: str,
        mode: str = "auto",
        runs: Optional[int] = None,
        max_rows: Optional[int] = None,
        verbose: bool = False,
        bypass_cache: bool = False,
        include_event_counts: bool = False,
        cold: bool = False,
    ) -> Dict[str, Any]:
        """Delegate to QueryOrchestrator."""
        return self.query_orch.safe_run_dax(
            connection_state,
            query,
            mode,
            runs,
            max_rows,
            verbose,
            bypass_cache,
            include_event_counts,
            cold,
        )

    # ==================== RESPONSE LIMITS ====================

    def suggest_optimizations(self, tool_name: str, result: Dict[str, Any]) -> Optional[str]:
        """Suggest optimizations based on tool usage and result size."""
        if not isinstance(result, dict):
            return None

        limits_info = result.get("_limits_info", {})
        token_usage = limits_info.get("token_usage", {})
        level = token_usage.get("level", "ok")

        if level in ["warning", "critical", "over"]:
            suggestions = []
            if tool_name in ["list_tables", "list_columns", "list_measures"]:
                suggestions.append("Use 'limit' parameter to reduce result set size")
            if tool_name == "describe_table":
                suggestions.append(
                    "Use 'include_columns=false' or 'include_measures=false' to reduce details"
                )
            if tool_name == "analyze_model_bpa":
                suggestions.append("Use filtering parameters to reduce result size")
            if tool_name == "export_tmdl":
                suggestions.append("Export to file instead of returning full content")
            if tool_name == "04_Run_DAX":
                suggestions.append("Pass export_path to write the full result to disk")
            if suggestions:
                return " | ".join(suggestions)
        return None
