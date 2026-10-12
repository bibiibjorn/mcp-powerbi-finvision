"""
Centralized constants and limits used across the server.
"""


class QueryLimits:
    DMV_DEFAULT_CAP = 1000
    SAFETY_MAX_ROWS = 10_000
    PREVIEW_SAMPLE_SIZE = 30
    TELEMETRY_BUFFER_SIZE = 200


class CacheLimits:
    MAX_ENTRIES = 1000
    MAX_SIZE_MB = 100
    DEFAULT_TTL_SECONDS = 300
