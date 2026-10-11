"""
Centralized limits manager for MCP-PowerBi-Finvision.

This module provides a single source of truth for all rate limits, token limits,
query limits, and other constraints across the entire server.

Benefits:
- Single location for all limit configuration
- Consistent limit enforcement across all modules
- Easy to adjust limits globally
- Clear visibility of all constraints
"""

import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger("mcp_powerbi_finvision.limits")


@dataclass
class QueryLimits:
    """Limits for query execution and data retrieval."""

    # INFO.* query limits (per-call defaults)
    # Note: 10000 allows fetching all measures/columns for large models
    default_info_limit: int = 10000
    max_info_limit: int = 10000

    # Preview limits
    max_rows_preview: int = 1000
    preview_sample_size: int = 30

    # Query constraints
    max_dax_query_length: int = 50000
    max_query_timeout: int = 60
    default_top_n: int = 1000

    # Pagination
    default_page_size: int = 20
    max_page_size: int = 200

    # Safety limits
    safety_max_rows: int = 10_000

    # Dependency analysis
    default_dependency_depth: int = 2
    max_dependency_depth: int = 5

    # Expression display
    max_expression_display_length: int = 500


#: Claude Code's hard cap on MCP tool output. Exceeding it fails the whole
#: call — the round trip is wasted, not merely trimmed — so our own ceiling must
#: sit at or below it. Overridable client-side via MAX_MCP_OUTPUT_TOKENS, but we
#: must not *assume* an operator raised it.
CLIENT_MAX_OUTPUT_TOKENS = 25000


@dataclass
class TokenLimits:
    """Limits for token usage and response sizes."""

    # Maximum tokens per tool response. Aligned with the client's hard output cap
    # (see CLIENT_MAX_OUTPUT_TOKENS): a result over the client limit is REJECTED,
    # so a higher server ceiling just converts a trimmed response into a failed
    # call. config/default_config.json sets query.max_result_tokens to the same
    # value; this default is the fallback when config loading fails.
    max_result_tokens: int = CLIENT_MAX_OUTPUT_TOKENS

    # Estimated tokens per character (rough approximation)
    chars_per_token: int = 4

    # Warning thresholds, derived from max_result_tokens in __post_init__ so they
    # track the configured ceiling instead of being dead absolutes. (They were
    # pinned at 80000/90000 against a 100000 default, which never fired once the
    # real ceiling dropped to 25000.)
    warning_threshold_tokens: int = 0  # set to 80% of max in __post_init__
    critical_threshold_tokens: int = 0  # set to 90% of max in __post_init__

    # Description table page sizes (token-optimized)
    describe_table_columns_page_size: int = 20
    describe_table_measures_page_size: int = 20
    describe_table_relationships_page_size: int = 50

    def __post_init__(self) -> None:
        # Derive thresholds from the effective ceiling unless a caller set them
        # explicitly (a non-zero value passed in wins).
        if not self.warning_threshold_tokens:
            self.warning_threshold_tokens = int(self.max_result_tokens * 0.8)
        if not self.critical_threshold_tokens:
            self.critical_threshold_tokens = int(self.max_result_tokens * 0.9)

    def is_at_warning(self, tokens: int) -> bool:
        """Check if token count is at warning threshold."""
        return tokens >= self.warning_threshold_tokens

    def is_at_critical(self, tokens: int) -> bool:
        """Check if token count is at critical threshold."""
        return tokens >= self.critical_threshold_tokens

    def is_over_limit(self, tokens: int) -> bool:
        """Check if token count exceeds maximum."""
        return tokens >= self.max_result_tokens


@dataclass
class RateLimits:
    """Rate limiting configuration."""

    enabled: bool = True
    profile: str = "balanced"

    # Global limits
    global_calls_per_second: float = 10.0
    global_burst: int = 20

    # Tool-specific limits (calls per second)
    tool_limits: Dict[str, float] = field(
        default_factory=lambda: {
            "run_dax": 5.0,
            "analyze_model_bpa": 1.0,
            "full_analysis": 0.5,
            "analyze_queries_batch": 2.0,
        }
    )

    # Tool-specific burst sizes
    tool_bursts: Dict[str, int] = field(
        default_factory=lambda: {
            "run_dax": 10,
            "analyze_model_bpa": 3,
            "full_analysis": 2,
            "analyze_queries_batch": 5,
        }
    )


@dataclass
class TimeoutLimits:
    """Timeout configuration for all tools."""

    default_timeout: int = 60

    # Per-tool timeouts (seconds)
    tool_timeouts: Dict[str, int] = field(
        default_factory=lambda: {
            "list_tables": 5,
            "list_columns": 5,
            "list_measures": 5,
            "list_relationships": 10,
            "describe_table": 10,
            "preview_table_data": 15,
            "run_dax": 60,
            "analyze_model_bpa": 180,
            "full_analysis": 300,
            "analyze_queries_batch": 180,
            "export_tmdl": 60,
            "generate_documentation": 60,
        }
    )

    def get_timeout(self, tool_name: str) -> int:
        """Get timeout for specific tool."""
        return self.tool_timeouts.get(tool_name, self.default_timeout)


@dataclass
class BpaLimits:
    """Limits for Best Practice Analyzer."""

    max_rules: int = 120
    severity_at_least: str = "WARNING"
    max_tables: int = 60
    max_seconds: int = 60
    per_rule_max_ms: int = 500
    max_columns_per_rule: int = 5000
    max_measures_per_rule: int = 1000
    max_relationships_per_rule: int = 1000
    adaptive_timeouts: bool = True


@dataclass
class CacheLimits:
    """Cache configuration limits."""

    ttl_seconds: int = 300
    max_entries: int = 1000
    max_size_mb: int = 100
    eviction_policy: str = "lru"


class LimitsManager:
    """Centralized manager for all server limits (query, token, rate, timeout, BPA, cache). Single source of truth."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize limits manager from config dict (from ConfigManager)."""
        self.config = config or {}

        # Initialize all limit categories
        self.query = self._init_query_limits()
        self.token = self._init_token_limits()
        self.rate = self._init_rate_limits()
        self.timeout = self._init_timeout_limits()
        self.bpa = self._init_bpa_limits()
        self.cache = self._init_cache_limits()

        logger.info(
            f"LimitsManager initialized: query_limit={self.query.default_info_limit}, "
            f"token_limit={self.token.max_result_tokens}, "
            f"rate={self.rate.global_calls_per_second}/s"
        )

    def _init_query_limits(self) -> QueryLimits:
        """Initialize query limits from config."""
        return QueryLimits(
            default_info_limit=self._get_config("query.default_info_limit", 10000),
            max_info_limit=self._get_config("query.max_info_limit", 10000),
            max_rows_preview=self._get_config("query.max_rows_preview", 1000),
            preview_sample_size=self._get_config("query.preview_sample_size", 30),
            max_dax_query_length=self._get_config("query.max_dax_query_length", 50000),
            max_query_timeout=self._get_config("performance.max_query_timeout", 60),
            default_top_n=self._get_config("performance.default_top_n", 1000),
            default_page_size=self._get_config("query.default_page_size", 20),
            max_page_size=self._get_config("query.max_page_size", 200),
            safety_max_rows=10_000,
            default_dependency_depth=self._get_config("query.default_dependency_depth", 2),
            max_dependency_depth=self._get_config("query.max_dependency_depth", 5),
            max_expression_display_length=self._get_config(
                "query.max_expression_display_length", 500
            ),
        )

    def _init_token_limits(self) -> TokenLimits:
        """Initialize token limits from config."""
        defaults = self._get_config("query.describe_table_defaults", {})
        # Thresholds are left at 0 so TokenLimits.__post_init__ derives them
        # (80%/90%) from whatever ceiling config resolves to. Hardcoding them
        # here pinned 80000/90000 against a 25000 ceiling, so neither ever fired.
        return TokenLimits(
            max_result_tokens=self._get_config("query.max_result_tokens", CLIENT_MAX_OUTPUT_TOKENS),
            chars_per_token=4,
            warning_threshold_tokens=0,
            critical_threshold_tokens=0,
            describe_table_columns_page_size=defaults.get("columns_page_size", 20),
            describe_table_measures_page_size=defaults.get("measures_page_size", 20),
            describe_table_relationships_page_size=defaults.get("relationships_page_size", 50),
        )

    def _init_rate_limits(self) -> RateLimits:
        """Initialize rate limits from config."""
        return RateLimits(
            enabled=self._get_config("rate_limiting.enabled", True),
            profile=self._get_config("rate_limiting.profile", "balanced"),
            global_calls_per_second=float(
                self._get_config("rate_limiting.global_calls_per_second", 10)
            ),
            global_burst=int(self._get_config("rate_limiting.global_burst", 20)),
            tool_limits=self._get_config("rate_limiting.tool_limits", {}),
            tool_bursts=self._get_config("rate_limiting.tool_bursts", {}),
        )

    def _init_timeout_limits(self) -> TimeoutLimits:
        """Initialize timeout limits from config."""
        return TimeoutLimits(
            default_timeout=self._get_config("server.timeout_seconds", 60),
            tool_timeouts=self._get_config("tool_timeouts", {}),
        )

    def _init_bpa_limits(self) -> BpaLimits:
        """Initialize BPA limits from config."""
        return BpaLimits(
            max_rules=self._get_config("bpa.max_rules", 120),
            severity_at_least=self._get_config("bpa.severity_at_least", "WARNING"),
            max_tables=self._get_config("bpa.max_tables", 60),
            max_seconds=self._get_config("bpa.max_seconds", 60),
            per_rule_max_ms=self._get_config("bpa.per_rule_max_ms", 500),
            max_columns_per_rule=self._get_config("bpa.max_columns_per_rule", 5000),
            max_measures_per_rule=self._get_config("bpa.max_measures_per_rule", 1000),
            max_relationships_per_rule=self._get_config("bpa.max_relationships_per_rule", 1000),
            adaptive_timeouts=self._get_config("bpa.adaptive_timeouts", True),
        )

    def _init_cache_limits(self) -> CacheLimits:
        """Initialize cache limits from config."""
        return CacheLimits(
            ttl_seconds=self._get_config("performance.cache_ttl_seconds", 300),
            max_entries=self._get_config("performance.cache_max_entries", 1000),
            max_size_mb=self._get_config("performance.cache_max_size_mb", 100),
            eviction_policy=self._get_config("performance.cache_eviction_policy", "lru"),
        )

    def _get_config(self, key: str, default: Any) -> Any:
        """Get configuration value with dot notation support.

        P2-15: delegates to `core.config.config_manager.get_by_dot_path` so
        there is a single canonical dot-traversal implementation. Preserves
        the previous "None at leaf → default" behavior via
        treat_none_as_missing=True.
        """
        if not self.config:
            return default
        from core.config.config_manager import get_by_dot_path

        return get_by_dot_path(self.config, key, default, treat_none_as_missing=True)


# Global singleton (initialized by server on startup)
_limits_manager: Optional[LimitsManager] = None


def init_limits_manager(config: Dict[str, Any]) -> LimitsManager:
    """Initialize the global limits manager."""
    global _limits_manager
    _limits_manager = LimitsManager(config)
    return _limits_manager


def get_limits() -> LimitsManager:
    """Get the global limits manager instance."""
    if _limits_manager is None:
        raise RuntimeError("LimitsManager not initialized. Call init_limits_manager() first.")
    return _limits_manager
