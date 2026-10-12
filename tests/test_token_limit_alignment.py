"""Token-ceiling alignment with the MCP client's hard output cap.

Claude Code REJECTS an MCP tool result over ~25K tokens — the call fails rather
than the payload being trimmed — so a server ceiling above that turns a
trimmable response into a wasted round trip. These tests pin that alignment and
guard the derived warning/critical thresholds, which were previously hardcoded
absolutes (80000/90000) that could never fire under a 25000 ceiling.
"""

from __future__ import annotations

from core.infrastructure.limits_manager import (
    CLIENT_MAX_OUTPUT_TOKENS,
    LimitsManager,
    TokenLimits,
    init_limits_manager,
)


class TestClientCapAlignment:
    def test_client_cap_constant_is_claude_code_limit(self):
        assert CLIENT_MAX_OUTPUT_TOKENS == 25000

    def test_default_ceiling_does_not_exceed_client_cap(self):
        """The regression that matters: a default above the client cap means
        oversized results fail instead of being truncated."""
        assert TokenLimits().max_result_tokens <= CLIENT_MAX_OUTPUT_TOKENS

    def test_config_default_matches_client_cap(self):
        from core.config.config_manager import config

        lm = init_limits_manager(config)
        assert lm.token.max_result_tokens <= CLIENT_MAX_OUTPUT_TOKENS


class TestDerivedThresholds:
    def test_thresholds_derive_from_default_ceiling(self):
        t = TokenLimits()
        assert t.warning_threshold_tokens == int(t.max_result_tokens * 0.8)
        assert t.critical_threshold_tokens == int(t.max_result_tokens * 0.9)

    def test_thresholds_track_a_custom_ceiling(self):
        t = TokenLimits(max_result_tokens=50000)
        assert t.warning_threshold_tokens == 40000
        assert t.critical_threshold_tokens == 45000

    def test_thresholds_are_ordered_below_the_ceiling(self):
        t = TokenLimits()
        assert 0 < t.warning_threshold_tokens < t.critical_threshold_tokens
        assert t.critical_threshold_tokens < t.max_result_tokens

    def test_explicit_thresholds_are_respected(self):
        t = TokenLimits(max_result_tokens=30000, warning_threshold_tokens=111)
        assert t.warning_threshold_tokens == 111
        # The unset one still derives.
        assert t.critical_threshold_tokens == 27000

    def test_thresholds_actually_fire_within_the_ceiling(self):
        """Under the old hardcoded values none of these would trigger."""
        t = TokenLimits()
        assert t.is_at_warning(t.warning_threshold_tokens)
        assert t.is_at_critical(t.critical_threshold_tokens)
        assert t.is_over_limit(t.max_result_tokens + 1)
        assert not t.is_over_limit(t.warning_threshold_tokens)


class TestLimitsManagerWiring:
    """LimitsManager reads config via dot-path traversal of a nested mapping
    (`_get_config` → `get_by_dot_path`), so fixtures are plain nested dicts."""

    def test_manager_derives_thresholds_from_config_ceiling(self):
        lm = LimitsManager({"query": {"max_result_tokens": 12000}})
        assert lm.token.max_result_tokens == 12000
        # Derived, not the old 80000/90000 constants.
        assert lm.token.warning_threshold_tokens == 9600
        assert lm.token.critical_threshold_tokens == 10800

    def test_manager_falls_back_to_client_cap_not_100k(self):
        lm = LimitsManager({})
        assert lm.token.max_result_tokens == CLIENT_MAX_OUTPUT_TOKENS
        assert lm.token.warning_threshold_tokens == int(CLIENT_MAX_OUTPUT_TOKENS * 0.8)
