"""Model-level SETTINGS validator (integrity scope of 06_Analysis_Operations).

Companion to ``star_schema_validator`` — same pure, dict-driven, unit-testable
shape, but checks model-level TOM *properties* rather than topology.

Why this is not a bpa.json rule: the BPA layer's hand-rolled expression grammar
cannot express this check reliably. Verified against the BPA expression evaluator (``core/analysis/bpa_expression.py``):

- ``ValueFilterBehavior == "Automatic" or ValueFilterBehavior == "Coalesced"``
  evaluates to ``False`` for EVERY input (the ``or`` chaining collapses), and
- ``ValueFilterBehavior != "Independent"`` returns ``True`` when the property is
  ABSENT (older models / older SDK), i.e. it fires a false positive on exactly
  the models that cannot have the setting at all.

Both failure modes are silent, so the check lives here where the semantics are
explicit and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Compatibility level that introduced Model.ValueFilterBehavior.
VALUE_FILTER_BEHAVIOR_MIN_CL = (
    1606  # Model.ValueFilterBehavior: CL 1606+ (Microsoft docs, TOM 19.117)
)

# Behaviours that carry the legacy SUMMARIZECOLUMNS semantics.
_LEGACY_BEHAVIORS = {"automatic", "coalesced"}


@dataclass(frozen=True)
class ModelSettingFinding:
    """A single model-level setting finding (mirrors TopologyFinding)."""

    finding_id: str
    severity: str  # "info" | "low" | "medium" | "high" | "critical"
    object_type: str
    object_name: str
    description: str
    recommendation: str


class ModelSettingsValidator:
    """Detect model-level settings that carry known correctness risk.

    Input dict shape (all keys optional — a missing key means "unknown", which
    is never reported as a violation):

        {
            "value_filter_behavior": "Automatic" | "Independent" | "Coalesced" | None,
            "value_filter_behavior_supported": bool,
            "compatibility_level": int | None,
        }
    """

    def validate(self, model: Dict[str, Any]) -> List[ModelSettingFinding]:
        model = model or {}
        findings: List[ModelSettingFinding] = []
        finding = self._check_value_filter_behavior(model)
        if finding is not None:
            findings.append(finding)
        return findings

    # ── ValueFilterBehavior ────────────────────────────────────────────────

    @staticmethod
    def _check_value_filter_behavior(model: Dict[str, Any]) -> Optional[ModelSettingFinding]:
        """Flag a model still on the legacy value-filter semantics.

        Fails SILENT (returns None) on every "can't know" path so the check never
        fires on a model that is unable to carry the setting:
          - the SDK/model does not expose the property,
          - the compatibility level is below 1600,
          - the value is missing/unrecognised.
        """
        if model.get("value_filter_behavior_supported") is False:
            return None

        level = model.get("compatibility_level")
        try:
            if level is not None and int(level) < VALUE_FILTER_BEHAVIOR_MIN_CL:
                return None
        except (TypeError, ValueError):
            return None

        behavior = model.get("value_filter_behavior")
        if not behavior:
            return None
        key = str(behavior).strip().lower()
        if key not in _LEGACY_BEHAVIORS:
            return None

        if key == "automatic":
            detail = (
                "'Automatic' resolves to 'Independent' on models created 2025+ and to the "
                "legacy 'Coalesced' behaviour on older ones; check which applies before "
                "flipping it (flipping changes results)."
            )
        else:
            detail = "'Coalesced' is the legacy behaviour and is explicitly selected here."

        # 'Automatic' is Independent for models created 2025+ (canon core rule 13) and Coalesced
        # for older ones - the creation date is not in the metadata, so it is a heads-up (low),
        # not a defect; an EXPLICIT 'Coalesced' stays medium (2026-10-03 review D-18).
        return ModelSettingFinding(
            finding_id="VALUE_FILTER_BEHAVIOR_NOT_INDEPENDENT",
            severity="low" if key == "automatic" else "medium",
            object_type="Model",
            object_name="Model",
            description=(
                f"Model.ValueFilterBehavior is '{behavior}'. {detail} Under the legacy "
                "behaviour, SUMMARIZECOLUMNS coalesces multiple value filters that "
                "target the same table, so a visual with several filters on one table "
                "can return surprising (silently wrong) rows."
            ),
            recommendation=(
                "Set it to 'Independent' — the correct default for models authored "
                "2025+ — via 02_Model_Operations {object_type:'model', "
                "operation:'set_value_filter_behavior', behavior:'Independent'}. "
                "This changes query semantics, so re-verify affected measures "
                "afterwards (09_Validate capture_baseline / compare_baseline)."
            ),
        )
