"""Transform abstract base — preview/apply contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Edit:
    target: str  # "measure:T[name]" or "file:path:line"
    before: str
    after: str
    note: str = ""


@dataclass
class PreviewResult:
    edits: List[Edit] = field(default_factory=list)
    affected_measures: List[str] = field(default_factory=list)
    affected_visuals: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # Targets the transform deliberately did not rewrite (unsafe site, clash ...).
    skipped: List[str] = field(default_factory=list)
    # Edits dropped by the transaction's structural sanity gate:
    # [{"target", "reason", "after"}] (review E-10 / wave-1 hand-over).
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    # A hard refusal (``{"error_type", "error", ...}``): preview reports it as a failure and apply
    # writes nothing (e.g. ``compatibility_level_too_low`` for extract_udf, F-15).
    refusal: Optional[Dict[str, Any]] = None


class Transform:
    name: str = ""
    description: str = ""
    args_schema: Dict[str, Any] = {}
    # False for transforms whose edits are markers, not measure bodies
    # (move_measure_batch) — apply refuses them; preview says so (E-22).
    persistable: bool = True
    # True when the rewrite must not change any result (pure refactor): apply
    # then compares the affected measures before/after and rolls back on a
    # difference (E-27). use_divide / replace_pattern change semantics on purpose.
    semantics_preserving: bool = True

    def preview(self, model: Any, args: Dict[str, Any]) -> PreviewResult:
        raise NotImplementedError

    def apply(self, model: Any, args: Dict[str, Any], preview: PreviewResult) -> Dict[str, Any]:
        raise NotImplementedError


_REGISTRY: Dict[str, Transform] = {}


def register(transform: Transform) -> Transform:
    _REGISTRY[transform.name] = transform
    return transform


def registry() -> Dict[str, Transform]:
    return dict(_REGISTRY)
