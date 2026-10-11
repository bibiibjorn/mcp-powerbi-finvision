"""Location of the masking home: the gateway's control token, order key, registry and log."""

from __future__ import annotations

import os
from pathlib import Path


def home_dir() -> Path:
    """``$FINVISION_MASKING_HOME``, else ``%LOCALAPPDATA%/FinvisionMasking``."""
    override = os.environ.get("FINVISION_MASKING_HOME")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "FinvisionMasking"
