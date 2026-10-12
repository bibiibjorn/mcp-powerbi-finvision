"""
Configuration management for PBIXRay MCP Server
"""

import json
import logging
import os
import threading
from typing import Any, Dict, Optional

from core.utilities.json_utils import load_json

logger = logging.getLogger(__name__)


def get_by_dot_path(
    d: Dict[str, Any], key_path: str, default: Any = None, treat_none_as_missing: bool = False
) -> Any:
    """P2-15: canonical dot-notation traversal for plain config dicts.

    Matches ConfigManager.get() semantics by default (returns None if the
    leaf exists and is None). `treat_none_as_missing=True` switches to
    LimitsManager._get_config semantics (coerces missing-or-None to default).
    """
    keys = key_path.split(".")
    value = d
    try:
        for key in keys:
            value = value[key]
    except (KeyError, TypeError):
        return default
    if treat_none_as_missing and value is None:
        return default
    return value


class ConfigManager:
    """
    Manages server configuration with support for default and local overrides.
    """

    def __init__(self, config_dir: Optional[str] = None):
        """
        Initialize configuration manager.

        Args:
            config_dir: Directory containing configuration files
        """
        if config_dir is None:
            # Default to config directory relative to this file
            # This file is in core/config/, so go up two levels to root, then into config/
            script_dir = os.path.dirname(os.path.abspath(__file__))
            core_dir = os.path.dirname(script_dir)  # core/
            root_dir = os.path.dirname(core_dir)  # project root
            config_dir = os.path.join(root_dir, "config")

        self.config_dir = config_dir
        self.config = {}
        self._lock = threading.RLock()
        self._reload_listeners: list = []
        self._load_config()

    def _load_config(self):
        """Load configuration from default and local files.

        Audit T87: load default and local in two separate try blocks so a
        broken local_config.json does not discard the already-loaded
        default. Operators see a clear ERROR log instead of silently
        falling back to bare defaults.
        """
        with self._lock:
            # 1. Default config (or fallback if missing/broken)
            default_config_path = os.path.join(self.config_dir, "default_config.json")
            try:
                if os.path.exists(default_config_path):
                    self.config = load_json(default_config_path)
                    logger.info("Loaded default configuration")
                else:
                    logger.warning(f"Default config not found at {default_config_path}")
                    self.config = self._get_fallback_config()
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"Failed to load default config {default_config_path}: {e}")
                self.config = self._get_fallback_config()

            # 2. Local overrides — failure here MUST NOT discard the default.
            local_config_path = os.path.join(self.config_dir, "local_config.json")
            # the test suite sets MCP_PBI_NO_LOCAL_CONFIG=1: a developer's own overrides (masking
            # switched on, routing, a profile) must not change what the tests see
            skip_local = os.environ.get("MCP_PBI_NO_LOCAL_CONFIG", "").strip() == "1"
            if os.path.exists(local_config_path) and not skip_local:
                try:
                    local_config = load_json(local_config_path)
                    self._merge_config(self.config, local_config)
                    logger.info("Applied local configuration overrides")
                except json.JSONDecodeError as e:
                    logger.error(
                        f"Invalid JSON in local config {local_config_path}: {e}. "
                        "Continuing with default configuration only."
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to apply local config overrides: {e}. "
                        "Continuing with default configuration only."
                    )

    def _merge_config(self, base: Dict[str, Any], override: Dict[str, Any]):
        """Recursively merge override config into base config."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value

    def _get_fallback_config(self) -> Dict[str, Any]:
        """Return minimal fallback configuration (only keys some code reads; see
        ``tests/test_config_keys_have_readers.py``)."""
        return {
            "server": {"log_level": "INFO"},
            "performance": {
                "cache_ttl_seconds": 300,
                "max_query_timeout": 60,
                "default_top_n": 1000,
            },
            "query": {"max_rows_preview": 1000, "max_dax_query_length": 50000},
            "features": {"enable_bpa": True},
        }

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation.

        Args:
            key_path: Dot-separated path to config value (e.g., 'server.timeout_seconds')
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        with self._lock:
            return get_by_dot_path(self.config, key_path, default)

    def get_section(self, section: str) -> Dict[str, Any]:
        """
        Get entire configuration section.

        Args:
            section: Section name (e.g., 'server', 'performance')

        Returns:
            Configuration section dictionary (shallow copy — caller may mutate safely)
        """
        # P1-12 fix: hold the lock for the read AND return a shallow copy so
        # callers don't accidentally mutate shared config state. A concurrent
        # reload() may swap self.config at any time.
        with self._lock:
            return dict(self.config.get(section, {}))

    def is_feature_enabled(self, feature: str) -> bool:
        """
        Check if a feature is enabled.

        Args:
            feature: Feature name

        Returns:
            True if feature is enabled
        """
        return self.get(f"features.{feature}", False)

    def reload(self):
        """Reload configuration from files and notify reload listeners.

        A-29: components that cache config (the dispatcher's security flags and
        timeout table) register via :meth:`add_reload_listener`, so a reload
        actually takes effect.
        """
        self._load_config()
        logger.info("Configuration reloaded")
        with self._lock:
            listeners = list(self._reload_listeners)
        for listener in listeners:
            try:
                listener()
            except Exception as e:  # noqa: BLE001 - a listener must not break reload
                logger.warning(f"Config reload listener {listener!r} failed: {e}")

    def add_reload_listener(self, listener) -> None:
        """Register a zero-arg callable invoked after every :meth:`reload`."""
        with self._lock:
            if listener not in self._reload_listeners:
                self._reload_listeners.append(listener)

    def get_all(self) -> Dict[str, Any]:
        """Get complete configuration dictionary."""
        return self.config.copy()


# Global configuration instance
config = ConfigManager()
