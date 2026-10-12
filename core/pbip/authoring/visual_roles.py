"""
PBIR Visual Roles Registry

Static mapping of visual types to their valid data-role names. Used for
pre-flight validation before calling `pbir visuals bind` — binding to a role
name that doesn't exist silently produces a blank visual.

Sources (2026-08-21 refresh):
  1. `docs/visual-catalog/<type>.md` — this repo's A-layer reference; the
     `## Roles` table of each per-type file.
  2. The internal doc library's `VisualTypes/<type>.md`
     (`## Query roles` tables, generated from the `powerbi-report-author`
     CLI v0.1.1 metadata dump + reconciled against a 10-report / 18206-visual
     PBIR corpus). This covers types our catalog does not, e.g. `matrix`,
     `table`, `scorecard`, `heatMap`, `filterSlicer`, `pageNavigator`.

Where the two sources disagree on a role name (the CLI dump reports `Series`
and `Rows` for the cartesian family where our catalog reports `Legend` and
`SmallMultiples`), the registry stores the UNION. This is deliberate: the
registry is a guard rail, not a schema — a false negative silently blocks
valid authoring, whereas a false positive is caught by pbir at bind time.

Unknown visual types are a SOFT outcome (a warning), never a hard failure:
Power BI ships new visual types faster than this file is refreshed.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

VISUAL_ROLES: Dict[str, List[str]] = {
    # --- Cards / KPI ---------------------------------------------------
    "cardVisual": ["Data"],
    "card": ["Values"],
    "multiRowCard": ["Values"],
    # CLI metadata reports NO query roles for animatedNumber — its value is not
    # a bound projection. Keep it empty rather than guessing a `Values` role.
    "animatedNumber": [],
    "kpi": ["Indicator", "TrendLine", "Goal", "Goals"],
    "gauge": ["Y", "Value", "MinValue", "MaxValue", "TargetValue", "Tooltips"],
    # --- Cartesian family ---------------------------------------------
    # 'Legend'/'SmallMultiples' come from docs/visual-catalog; 'Series'/'Rows'
    # from the CLI dump (doc library VisualTypes). Both are accepted.
    "lineChart": ["Category", "Y", "Y2", "Legend", "Series", "SmallMultiples", "Rows", "Tooltips"],
    "barChart": ["Category", "Y", "Legend", "Series", "SmallMultiples", "Rows", "Tooltips"],
    "columnChart": ["Category", "Y", "Legend", "Series", "SmallMultiples", "Rows", "Tooltips"],
    "clusteredBarChart": [
        "Category",
        "Y",
        "Legend",
        "Series",
        "SmallMultiples",
        "Rows",
        "Tooltips",
    ],
    "clusteredColumnChart": [
        "Category",
        "Y",
        "Legend",
        "Series",
        "SmallMultiples",
        "Rows",
        "Tooltips",
    ],
    "hundredPercentStackedBarChart": [
        "Category",
        "Y",
        "Legend",
        "Series",
        "SmallMultiples",
        "Rows",
        "Tooltips",
    ],
    "hundredPercentStackedColumnChart": [
        "Category",
        "Y",
        "Legend",
        "Series",
        "SmallMultiples",
        "Rows",
        "Tooltips",
    ],
    "areaChart": [
        "Category",
        "Y",
        "Y2",
        "Legend",
        "Series",
        "SmallMultiples",
        "Rows",
        "Tooltips",
    ],
    "stackedAreaChart": [
        "Category",
        "Y",
        "Legend",
        "Series",
        "SmallMultiples",
        "Rows",
        "Tooltips",
    ],
    "hundredPercentStackedAreaChart": [
        "Category",
        "Y",
        "Legend",
        "Series",
        "SmallMultiples",
        "Rows",
        "Tooltips",
    ],
    "lineClusteredColumnComboChart": [
        "Category",
        "ColumnY",
        "LineY",
        "Legend",
        "Series",
        "Y",
        "Y2",
        "Tooltips",
    ],
    "lineStackedColumnComboChart": [
        "Category",
        "ColumnY",
        "LineY",
        "Legend",
        "Series",
        "Y",
        "Y2",
        "Tooltips",
    ],
    "ribbonChart": ["Category", "Y", "Legend", "Series", "Rows", "Tooltips"],
    "waterfallChart": ["Category", "Breakdown", "Y", "Tooltips"],
    "funnel": ["Category", "Y", "Values", "Tooltips"],
    "realTimeLineChart": ["Category", "Series", "Y"],
    "heatMap": ["Category", "Series", "X", "Y", "Size", "Tooltips"],
    "scatterChart": [
        "X",
        "Y",
        "Category",
        "Size",
        "Legend",
        "Series",
        "SmallMultiples",
        "Tooltips",
    ],
    # --- Part-to-whole ------------------------------------------------
    # 'Y' is the role used by some report templates (notably R0101) for
    # the donut/pie value axis instead of the default 'Values'. Pre-flight
    # is a guard rail, not a guarantee — both roles are accepted and the
    # actual validity is checked by pbir at bind time. (Audit T76.)
    "donutChart": ["Category", "Values", "Y", "Legend", "Series", "Tooltips"],
    "pieChart": ["Category", "Values", "Y", "Legend", "Series", "Tooltips"],
    "treemap": ["Category", "Values", "Details", "Legend", "Tooltips"],
    # --- Tabular ------------------------------------------------------
    "pivotTable": ["Rows", "Columns", "Values"],
    "tableEx": ["Values"],
    "table": ["Values"],
    "matrix": ["Rows", "Columns", "Values"],
    "accessibleTable": ["Rows", "Columns", "Values"],
    # --- Slicers ------------------------------------------------------
    "slicer": ["Values"],
    "advancedSlicerVisual": ["Values", "Label", "Tooltips"],
    "listSlicer": ["Values", "Tooltips"],
    "textSlicer": ["Values"],
    # `buttonSlicer` is a UI/product name, NOT a PBIR visualType — the tile /
    # button slicer is authored as `advancedSlicerVisual` (MS report-authoring
    # reference; `buttonSlicerVisual` is only the rendered DOM class). Kept as a
    # tolerated alias so a caller using the product name isn't blocked; roles
    # mirror advancedSlicerVisual. See docs/visual-catalog/buttonSlicer.md.
    "buttonSlicer": ["Values", "Label", "Tooltips"],
    "filterSlicer": ["Values"],
    # --- Maps ---------------------------------------------------------
    "map": ["Location", "Latitude", "Longitude", "Size", "Values", "Legend", "Series", "Color"],
    "filledMap": ["Location", "Values", "Legend", "Series", "Color"],
    "azureMap": [
        "Location",
        "Latitude",
        "Longitude",
        "Category",
        "X",
        "Y",
        "Legend",
        "Series",
        "Size",
        "Color",
        "Tooltips",
        "PathID",
        "PointOrder",
    ],
    "shapeMap": ["Location", "Category", "Series", "Value", "ColorSaturation", "Tooltips"],
    # --- AI / analytics ----------------------------------------------
    "decompositionTreeVisual": ["Analyze", "Explain By", "Category", "Values"],
    "keyDriversVisual": ["Analyze", "Expand", "Explain", "Target", "ExplainBy", "Details"],
    "scorecard": [],
    # --- Script / custom ----------------------------------------------
    "pythonVisual": ["Values"],
    "scriptVisual": ["Values"],
    "dataQueryVisual": ["Values"],
    "rdlVisual": ["parameterValues"],
    # --- Layout / navigation (no data roles) --------------------------
    "textbox": [],
    "image": [],
    "shape": [],
    "basicShape": [],
    "actionButton": [],
    "bookmarkNavigator": [],
    "pageNavigator": [],
    "aiNarratives": [],
    "qnaVisual": [],
}


#: Types that legitimately take no data roles (buttons, shapes, navigators,
#: self-configuring AI visuals). Used by the registry-shape tests so an empty
#: list is not mistaken for a missing entry.
NO_DATA_ROLE_TYPES = frozenset(
    {
        "textbox",
        "image",
        "shape",
        "basicShape",
        "actionButton",
        "bookmarkNavigator",
        "pageNavigator",
        "aiNarratives",
        "qnaVisual",
        "scorecard",
        "animatedNumber",
    }
)

#: Where to look when a type isn't in the registry.
CATALOG_DOC_HINT = "docs/visual-catalog/{visual_type}.md"


class UnknownVisualType(ValueError):
    """Raised when a visual type is not in VISUAL_ROLES.

    Retained for backward compatibility — `validate_role` still raises it so
    existing callers keep working — but it is NOT an authoring blocker: see
    `validate_bindings`, which downgrades an unknown type to a warning.
    """


class InvalidRole(ValueError):
    """Raised when a role is not valid for the given visual type."""

    def __init__(self, visual_type: str, role: str, allowed: List[str]) -> None:
        self.visual_type = visual_type
        self.role = role
        self.allowed = list(allowed)
        super().__init__(
            f"Role '{role}' is not valid for visual type '{visual_type}'. "
            f"Allowed roles: {', '.join(allowed) if allowed else '(none)'}"
        )


def is_known_visual_type(visual_type: str) -> bool:
    """True when `visual_type` has a roles entry in the registry."""
    return visual_type in VISUAL_ROLES


def unknown_type_warning(visual_type: str) -> str:
    """Human-readable advisory for a visual type we haven't catalogued."""
    return (
        f"Visual type '{visual_type}' is not in the roles registry — role "
        f"pre-flight skipped (pbir validates at bind time). Confirm the role "
        f"names against {CATALOG_DOC_HINT.format(visual_type=visual_type)} or "
        f"07_Visual_Operations:get_roles_for_type."
    )


def validate_role(visual_type: str, role: str) -> None:
    """Raise InvalidRole if `role` is not valid for `visual_type`.

    Raises UnknownVisualType if the visual type is not in the registry.
    Role matching is case-sensitive (PBIR role names are case-sensitive).

    Callers that must not block on an uncatalogued type should use
    `validate_bindings` / `validate_bindings_ex` instead, which treat an
    unknown type as a warning.
    """
    if visual_type not in VISUAL_ROLES:
        raise UnknownVisualType(
            f"Unknown visual type '{visual_type}'. Known types: "
            f"{', '.join(sorted(VISUAL_ROLES.keys()))}"
        )
    allowed = VISUAL_ROLES[visual_type]
    if role not in allowed:
        raise InvalidRole(visual_type, role, allowed)


def validate_bindings_ex(visual_type: str, roles: List[str]) -> Tuple[bool, List[str], List[str]]:
    """Validate a list of role names for a visual type.

    Returns (ok, errors, warnings). Does not raise.

    An unrecognized `visual_type` yields ok=True with one entry in `warnings`
    — a guard rail must not block authoring on a type we simply haven't
    catalogued yet (Power BI ships new visual types continuously). Only
    genuinely-invalid roles on a KNOWN type land in `errors`.
    """
    errors: List[str] = []
    warnings: List[str] = []
    if visual_type not in VISUAL_ROLES:
        warnings.append(unknown_type_warning(visual_type))
        return True, errors, warnings
    allowed = VISUAL_ROLES[visual_type]
    for role in roles:
        if role not in allowed:
            errors.append(
                f"Role '{role}' invalid for '{visual_type}'. "
                f"Allowed: {', '.join(allowed) if allowed else '(none)'}"
            )
    return len(errors) == 0, errors, warnings


def validate_bindings(visual_type: str, roles: List[str]) -> Tuple[bool, List[str]]:
    """Backward-compatible wrapper over `validate_bindings_ex`.

    Keeps the historical `(ok, errors)` shape so existing callers are
    unchanged. Warnings (currently only "unknown visual type") are dropped
    here — call `validate_bindings_ex` when you need them.
    """
    ok, errors, _warnings = validate_bindings_ex(visual_type, roles)
    return ok, errors
