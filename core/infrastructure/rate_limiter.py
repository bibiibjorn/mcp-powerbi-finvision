"""
Rate limiting for Power BI MCP Server.
Prevents overwhelming Desktop with rapid queries.
"""

import time
import threading
from collections import deque, defaultdict
from typing import Optional, Dict, Tuple
import logging

logger = logging.getLogger("mcp_powerbi_finvision.rate_limiter")


class RateLimiter:
    """
    Token bucket rate limiter with per-tool and global limits.
    Thread-safe implementation.
    """

    def __init__(self, config: Optional[dict] = None):
        """Initialize rate limiter.

        Config keys: enabled, profile, global_calls_per_second, global_burst,
        tool_limits, tool_bursts. B-23 / A-22 / K-06: ``profile`` now supplies
        the defaults for every key the config does not set explicitly, and
        per-tool keys may use legacy op names (``run_dax``) — they are mapped to
        the REGISTERED tool name (``04_Run_DAX``) that ``allow_request`` actually
        receives, so per-tool limits finally apply.
        """
        raw = dict(config or {})
        profile_name = raw.get("profile")
        base = dict(RATE_LIMIT_PROFILES.get(profile_name, {})) if profile_name else {}
        merged = dict(base)
        merged.update({k: v for k, v in raw.items() if v is not None})
        self.config = merged
        self.profile = profile_name if profile_name in RATE_LIMIT_PROFILES else None

        # Check if rate limiting is enabled
        self.enabled = self.config.get("enabled", True)

        # Global limits
        self.global_rate = float(self.config.get("global_calls_per_second", 10))
        self.global_burst = int(self.config.get("global_burst", 20))
        self.global_tokens = float(self.global_burst)
        self.global_last_update = time.time()

        # Per-tool limits, keyed by REGISTERED tool name.
        self.tool_limits = _normalise_tool_keys(self.config.get("tool_limits", {}) or {})
        self.tool_bursts = _normalise_tool_keys(self.config.get("tool_bursts", {}) or {})
        self.tool_tokens: Dict[str, float] = {}
        self.tool_last_update: Dict[str, float] = {}

        # Request tracking for metrics
        self.request_history = deque(maxlen=1000)
        self.tool_request_counts = defaultdict(int)
        self.tool_throttle_counts = defaultdict(int)

        # Thread safety
        self.lock = threading.RLock()

        status = "enabled" if self.enabled else "disabled"
        logger.info(
            f"Rate limiter {status}: {self.global_rate} calls/sec (burst {self.global_burst}); "
            f"per-tool limits for {sorted(self.tool_limits)}"
        )

    def _refill_tokens(self, current_time: float):
        """Refill token buckets based on elapsed time."""
        # Refill global bucket
        elapsed = current_time - self.global_last_update
        tokens_to_add = elapsed * self.global_rate
        self.global_tokens = min(self.global_burst, self.global_tokens + tokens_to_add)
        self.global_last_update = current_time

    def _refill_tool_tokens(self, tool_name: str, current_time: float):
        """Refill tokens for specific tool."""
        if tool_name not in self.tool_limits:
            return

        rate = self.tool_limits[tool_name]
        burst = self.tool_bursts.get(tool_name, int(rate * 2))

        if tool_name not in self.tool_last_update:
            self.tool_tokens[tool_name] = float(burst)
            self.tool_last_update[tool_name] = current_time
            return

        elapsed = current_time - self.tool_last_update[tool_name]
        tokens_to_add = elapsed * rate
        self.tool_tokens[tool_name] = min(burst, self.tool_tokens[tool_name] + tokens_to_add)
        self.tool_last_update[tool_name] = current_time

    def acquire(
        self, tool_name: str, cost: float = 1.0, timeout: Optional[float] = None
    ) -> Tuple[bool, Optional[float]]:
        """Try to acquire tokens; returns (True, None) on success or (False, wait_seconds) when throttled."""
        start_time = time.time()

        while True:
            sleep_duration = None
            with self.lock:
                current_time = time.time()

                # Refill buckets
                self._refill_tokens(current_time)
                self._refill_tool_tokens(tool_name, current_time)

                # Check global limit
                # P1-11 fix: compute wait_time as the MAX of (global_wait, tool_wait)
                # whenever either bucket is short, so the returned time reflects
                # the actual bottleneck rather than whichever branch ran first.
                global_wait = 0.0
                tool_wait = 0.0
                if self.global_tokens < cost:
                    global_wait = self._calculate_global_wait_time(cost, current_time)
                if tool_name in self.tool_limits and self.tool_tokens.get(tool_name, 0) < cost:
                    tool_wait = self._calculate_wait_time(tool_name, cost, current_time)

                if global_wait == 0.0 and tool_wait == 0.0:
                    # Both buckets have enough tokens — consume and return.
                    self.global_tokens -= cost
                    if tool_name in self.tool_limits:
                        self.tool_tokens[tool_name] -= cost
                    self._record_request(tool_name, current_time)
                    return True, None

                wait_time = max(global_wait, tool_wait)

                # If no timeout or timeout exceeded, return throttled
                if timeout is None or (current_time - start_time) >= timeout:
                    self.tool_throttle_counts[tool_name] += 1
                    logger.warning(f"Rate limit exceeded for {tool_name} (wait: {wait_time:.2f}s)")
                    return False, wait_time

                sleep_duration = min(0.1, wait_time)

            # Sleep outside the lock to avoid blocking other threads
            time.sleep(sleep_duration)

    # --- Convenience API expected by server wrapper ---
    def allow_request(self, tool_name: str, cost: float = 1.0) -> bool:
        """Non-blocking check-and-consume for a request.

        Returns True if tokens were available and consumed; False if throttled.
        """
        # Fast path: If rate limiting is disabled, always allow
        if not self.enabled:
            return True
        acquired, _ = self.acquire(tool_name, cost=cost, timeout=0.0)
        return acquired

    def get_retry_after(self, tool_name: str, cost: float = 1.0) -> float:
        """Estimate seconds until the next request of given cost would pass.

        Does not consume tokens.
        """
        with self.lock:
            now = time.time()
            # Refill to current time without consuming
            self._refill_tokens(now)
            self._refill_tool_tokens(tool_name, now)
            # Compute deficits
            global_deficit = max(0.0, cost - self.global_tokens)
            global_wait = (
                global_deficit / self.global_rate if self.global_rate > 0 else float("inf")
            )
            if tool_name in self.tool_limits:
                tool_deficit = max(0.0, cost - self.tool_tokens.get(tool_name, 0.0))
                tool_rate = float(self.tool_limits.get(tool_name, self.global_rate) or 0.0)
                tool_wait = tool_deficit / tool_rate if tool_rate > 0 else float("inf")
                wait = max(0.0, max(global_wait, tool_wait))
            else:
                wait = max(0.0, global_wait)
            # Bound the value to something reasonable for clients
            try:
                return round(wait, 2)
            except Exception:
                return float(wait)

    def _calculate_wait_time(self, tool_name: str, cost: float, current_time: float) -> float:
        """Calculate seconds to wait for tool-specific tokens."""
        needed = cost - self.tool_tokens.get(tool_name, 0)
        rate = self.tool_limits.get(tool_name, self.global_rate)
        return max(0.0, needed / rate)

    def _calculate_global_wait_time(self, cost: float, current_time: float) -> float:
        """Calculate seconds to wait for global tokens."""
        needed = cost - self.global_tokens
        return max(0.0, needed / self.global_rate)

    def _record_request(self, tool_name: str, timestamp: float):
        """Record request for metrics."""
        self.request_history.append((tool_name, timestamp))
        self.tool_request_counts[tool_name] += 1

    def get_status(self) -> dict:
        """Alias kept for callers of the old AgentPolicy.check_rate_limit surface."""
        stats = self.get_stats()
        stats["enabled"] = self.enabled
        stats["profile"] = self.profile
        stats["tool_limits"] = dict(self.tool_limits)
        return stats

    def get_stats(self) -> dict:
        """Get rate limiter statistics."""
        with self.lock:
            current_time = time.time()
            self._refill_tokens(current_time)

            # Calculate requests in last minute
            recent = [r for r in self.request_history if (current_time - r[1]) <= 60]

            return {
                "global_tokens_available": round(self.global_tokens, 2),
                "global_rate_limit": self.global_rate,
                "global_burst": self.global_burst,
                "requests_last_minute": len(recent),
                "total_requests": sum(self.tool_request_counts.values()),
                "total_throttled": sum(self.tool_throttle_counts.values()),
                "tool_counts": dict(self.tool_request_counts),
                "tool_throttled": dict(self.tool_throttle_counts),
                "tool_tokens": {k: round(v, 2) for k, v in self.tool_tokens.items()},
            }


#: Legacy v6 op names -> the registered MCP tool names ``allow_request`` sees.
LEGACY_TOOL_NAME_MAP = {
    "run_dax": "04_Run_DAX",
    "run_dax_query": "04_Run_DAX",
    "analyze_model_bpa": "06_Analysis_Operations",
    "full_analysis": "06_Analysis_Operations",
    "analyze_queries_batch": "06_Analysis_Operations",
    "export_tmdl": "02_TMDL_Operations",
    "generate_documentation": "08_Documentation_Word",
    "analyze_query_plan": "13_Query_Plan",
}


def _normalise_tool_keys(mapping: dict) -> Dict[str, float]:
    """Map legacy keys to registered names; an explicit registered key wins.

    When several legacy keys collapse onto one tool, the most restrictive
    (smallest) value is kept.
    """
    out: Dict[str, float] = {}
    legacy: Dict[str, float] = {}
    for key, value in (mapping or {}).items():
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        target = LEGACY_TOOL_NAME_MAP.get(key)
        if target is None:
            out[key] = num
        else:
            legacy[target] = min(num, legacy.get(target, num))
    for target, num in legacy.items():
        out.setdefault(target, num)
    return out


# Default rate limit profiles (keyed by REGISTERED tool names)

RATE_LIMIT_PROFILES = {
    "conservative": {
        "global_calls_per_second": 5,
        "global_burst": 10,
        "tool_limits": {
            "04_Run_DAX": 2,
            "06_Analysis_Operations": 0.2,
        },
        "tool_bursts": {
            "04_Run_DAX": 5,
            "06_Analysis_Operations": 1,
        },
    },
    "balanced": {
        # A local stdio server has one client; an agent fanning out 25 parallel
        # read calls must not be throttled by a 20-token global bucket (A-22).
        "global_calls_per_second": 30,
        "global_burst": 60,
        "tool_limits": {
            "04_Run_DAX": 10,
            "06_Analysis_Operations": 1,
        },
        "tool_bursts": {
            "04_Run_DAX": 20,
            "06_Analysis_Operations": 3,
        },
    },
    "aggressive": {
        "global_calls_per_second": 60,
        "global_burst": 120,
        "tool_limits": {
            "06_Analysis_Operations": 2,
        },
        "tool_bursts": {
            "06_Analysis_Operations": 5,
        },
    },
    "development": {
        "global_calls_per_second": 100,  # Effectively unlimited for dev
        "global_burst": 200,
    },
}
