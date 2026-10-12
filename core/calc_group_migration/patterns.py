"""Pattern catalog for calc-group migration detector."""

from __future__ import annotations

from typing import Any, Dict, Optional

PATTERNS: Dict[str, Dict[str, Any]] = {
    "time_intel": {
        "kind": "suffix",
        "variants": ["YTD", "MTD", "QTD", "LY", "LM", "PYTD", "YoY%", "MoM%"],
    },
    "scenario": {
        "kind": "suffix",
        "variants": ["Actual", "Budget", "Forecast", "Variance"],
    },
    "currency": {
        "kind": "prefix_or_suffix",
        "variants": ["EUR", "USD", "GBP", "JPY", "FX"],
    },
    "granularity": {
        "kind": "suffix",
        "variants": ["Daily", "Monthly", "Yearly", "Weekly"],
    },
}


def match_suffix(measure_name: str, pattern_name: str) -> Optional[Dict[str, str]]:
    cfg = PATTERNS.get(pattern_name)
    if not cfg:
        return None
    name = (measure_name or "").strip()
    for v in cfg["variants"]:
        if cfg["kind"] in {"suffix", "prefix_or_suffix"} and name.endswith(" " + v):
            return {"base": name[: -(len(v) + 1)].strip(), "variant": v}
        if cfg["kind"] == "prefix_or_suffix" and name.startswith(v + " "):
            return {"base": name[len(v) + 1 :].strip(), "variant": v}
    return None
