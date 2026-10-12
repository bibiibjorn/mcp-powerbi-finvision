"""Base orchestrator class with common functionality."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class BaseOrchestrator:
    """Base class for all orchestrators with common utilities."""

    def __init__(self, config):
        self.config = config

    def _get_preview_limit(self, max_rows: Optional[int]) -> int:
        """Get preview row limit from config or parameter."""
        if isinstance(max_rows, int) and max_rows > 0:
            return max_rows
        return (
            self.config.get("query.max_rows_preview", 1000)
            or self.config.get("performance.default_top_n", 1000)
            or 1000
        )
