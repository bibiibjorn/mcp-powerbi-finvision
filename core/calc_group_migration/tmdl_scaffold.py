"""Emit TMDL for a proposed calc group from a detector candidate.

C-30: the scaffold now emits TMDL the TOM deserializer accepts — every
``calculationItem`` uses the ``name = expression`` form (multi-line bodies
indented two levels deeper), names that are not plain identifiers are quoted
(``'YoY%'``), and the calc-group ``Name`` / ``Ordinal`` columns plus the
``calculationGroup`` partition are included. ``next_steps`` describe ONE
``02_Model_Operations calculation_group/create`` call with the ``group_name``
and ``items`` the handler actually reads.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.tmdl.tmdl_tree import tmdl_name

_DEFAULT_DATE = "'Date'[Date]"

_TIME_INTEL_BODIES = {
    "YTD": "CALCULATE(SELECTEDMEASURE(), DATESYTD({d}))",
    "MTD": "CALCULATE(SELECTEDMEASURE(), DATESMTD({d}))",
    "QTD": "CALCULATE(SELECTEDMEASURE(), DATESQTD({d}))",
    "LY": "CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR({d}))",
    "LM": "CALCULATE(SELECTEDMEASURE(), DATEADD({d}, -1, MONTH))",
    "PYTD": "CALCULATE(SELECTEDMEASURE(), DATESYTD(SAMEPERIODLASTYEAR({d})))",
    "YoY%": (
        "DIVIDE(SELECTEDMEASURE() - "
        "CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR({d})), "
        "CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR({d})))"
    ),
    "MoM%": (
        "DIVIDE(SELECTEDMEASURE() - "
        "CALCULATE(SELECTEDMEASURE(), DATEADD({d}, -1, MONTH)), "
        "CALCULATE(SELECTEDMEASURE(), DATEADD({d}, -1, MONTH)))"
    ),
}

_PERCENT_VARIANTS = {"YoY%", "MoM%"}


def _body_for(pattern_name: str, variant: str, date_column: str) -> str:
    if pattern_name == "time_intel":
        tpl = _TIME_INTEL_BODIES.get(variant)
        if tpl:
            return tpl.format(d=date_column)
        return "SELECTEDMEASURE()"
    # A line comment would swallow nothing here (the body is on its own line),
    # but keep scaffolds comment-free so they paste into any editor.
    return "SELECTEDMEASURE()"


def _item_tmdl(name: str, body: str, *, format_string: Optional[str] = None) -> List[str]:
    lines = [f"\t\tcalculationItem {tmdl_name(name)} ="]
    for body_line in body.splitlines() or [body]:
        lines.append(f"\t\t\t\t{body_line}")
    if format_string:
        lines.append("")
        lines.append(f"\t\t\tformatStringDefinition = {format_string}")
    return lines


def scaffold_calc_group(
    candidate: Dict[str, Any],
    calc_group_table_name: str = "TimeIntelligence",
    precedence: int = 10,
    date_column: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a TMDL string (and the create-call items) for the proposed calc group."""
    pattern = candidate.get("pattern_name", "custom")
    variants = candidate.get("variant_suffix_set", [])
    bases = candidate.get("base_measures", [])
    date_ref = date_column or _DEFAULT_DATE

    items: List[Dict[str, Any]] = []
    body_lines: List[str] = []
    for ordinal, v in enumerate(variants):
        body = _body_for(pattern, v, date_ref)
        fmt = '"0.0%"' if v in _PERCENT_VARIANTS else None
        item: Dict[str, Any] = {"name": v, "expression": body, "ordinal": ordinal}
        if fmt:
            item["format_string_expression"] = fmt
        items.append(item)
        body_lines.append("")
        body_lines.extend(_item_tmdl(v, body, format_string=fmt))

    tname = tmdl_name(calc_group_table_name)
    lines = [
        f"table {tname}",
        "",
        "\tcalculationGroup",
        f"\t\tprecedence: {int(precedence)}",
        *body_lines,
        "",
        "\tcolumn Name",
        "\t\tdataType: string",
        "\t\tsummarizeBy: none",
        "\t\tsourceColumn: Name",
        "\t\tsortByColumn: Ordinal",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
        "",
        "\tcolumn Ordinal",
        "\t\tdataType: int64",
        "\t\tformatString: 0",
        "\t\tsummarizeBy: sum",
        "\t\tsourceColumn: Ordinal",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
        "",
        f"\tpartition {tname} = calculationGroup",
        "\t\tmode: import",
        "",
    ]
    tmdl = "\n".join(lines)

    notes = [
        f"Pattern: {pattern}; covers {len(variants)} variants across {len(bases)} base measures.",
        "Existing measures are NOT deleted by the scaffold — they remain alongside the calc group.",
        "The model must have discourageImplicitMeasures on; calculation_group/create sets it.",
    ]
    if pattern == "time_intel" and not date_column:
        notes.append(f"Bodies reference {_DEFAULT_DATE}; pass date_column to use your date table.")
    create_args = {
        "object_type": "calculation_group",
        "operation": "create",
        "group_name": calc_group_table_name,
        "precedence": int(precedence),
        "items": items,
    }
    next_steps = [
        (
            "Review the TMDL above, then create the whole group in ONE call: "
            "02_Model_Operations " + json.dumps(create_args, ensure_ascii=False)
        ),
        (
            "Then add a slicer bound to the calc-group column on the report pages "
            "where you want the variant choice."
        ),
    ]

    return {
        "tmdl": tmdl,
        "calc_group_items": items,
        "create_args": create_args,
        "notes": notes,
        "next_steps": next_steps,
    }
