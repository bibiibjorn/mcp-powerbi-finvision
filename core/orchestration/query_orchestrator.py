"""DAX query execution orchestration.

B-26 / K-13 (2026-09-22 review): the intent-routing surface that used to live
here (``optimize_variants`` — which raised ``NameError`` on every success —
``decide_and_run``, ``execute_intent``, ``plan_query``,
``auto_analyze_or_preview``) had no caller outside ``core/orchestration`` and
was deleted. ``safe_run_dax`` is the only live entry point (04_Run_DAX).
"""

import logging
from typing import Any, Dict, Optional

from .base_orchestrator import BaseOrchestrator

logger = logging.getLogger(__name__)


class QueryOrchestrator(BaseOrchestrator):
    """Handles DAX query execution workflows."""

    def __init__(self, config, query_policy=None):
        super().__init__(config)
        self.query_policy = query_policy

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
        """Validate, limit, and execute a DAX query. ``mode=analyze`` benchmarks it."""
        from core.validation.error_handler import ErrorHandler

        qp = self.query_policy
        if qp is not None:
            return qp.safe_run_dax(
                connection_state,
                query,
                mode=mode,
                runs=runs,
                max_rows=max_rows,
                verbose=verbose,
                bypass_cache=bypass_cache,
                include_event_counts=include_event_counts,
                cold=cold,
            )
        # Fallback: in rare case policy import failed, use legacy logic
        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()
        qe = connection_state.query_executor
        if not qe:
            return ErrorHandler.handle_manager_unavailable("query_executor")
        return qe.validate_and_execute_dax(query, 0, bypass_cache=bypass_cache)
