"""
Visual Templates for PBIP Report Authoring

Predefined JSON skeletons for each Power BI visual type, extracted from
real working Power BI reports. Each template returns the minimal valid
visual.json structure that Power BI Desktop will accept.

Templates are parameterized — callers provide data bindings, positions, and
formatting overrides on top of the skeleton.
"""

from typing import Any, Callable, Dict, List, Optional

from core.pbip.authoring.id_generator import generate_visual_id
from core.pbip.pbir_model import encode_literal

# Default schema version for generated visuals. ``pbir_executor.write_visual``
# replaces it with the version sibling visuals of the same report already
# carry (H-36), so this only applies to a report without any visual yet.
VISUAL_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json"


def _literal(value: str) -> Dict[str, Any]:
    """Helper: wrap a value in Power BI's Literal expression format."""
    return {"expr": {"Literal": {"Value": value}}}


def _bool_literal(value: bool) -> Dict[str, Any]:
    """Helper: wrap a boolean in Power BI's Literal expression format."""
    return _literal("true" if value else "false")


def _number_literal(value: float) -> Dict[str, Any]:
    """Helper: wrap a number in Power BI's D-suffixed Literal format (no exponent notation)."""
    return _literal(encode_literal(value, "double"))


def _string_literal(value: str) -> Dict[str, Any]:
    """Helper: wrap a string in Power BI's quoted Literal format (``'`` doubled — H-15)."""
    return _literal(encode_literal(str(value), "string"))


def _theme_color(color_id: int, percent: float = 0) -> Dict[str, Any]:
    """Helper: create a ThemeDataColor reference."""
    return {
        "solid": {
            "color": {
                "expr": {
                    "ThemeDataColor": {
                        "ColorId": color_id,
                        "Percent": percent,
                    }
                }
            }
        }
    }


def _hex_color(hex_code: str) -> Dict[str, Any]:
    """Helper: create a literal hex color."""
    return {"solid": {"color": _string_literal(hex_code)}}


def _base_visual(visual_type: str, name: Optional[str] = None) -> Dict[str, Any]:
    """Create the base visual structure common to all types."""
    return {
        "$schema": VISUAL_SCHEMA,
        "name": name or generate_visual_id(),
        "position": {
            "x": 0,
            "y": 0,
            "z": 0,
            "height": 200,
            "width": 300,
            "tabOrder": 0,
        },
        "visual": {
            "visualType": visual_type,
            "drillFilterOtherVisuals": True,
        },
    }


def _with_default_container_objects(visual: Dict[str, Any], title: str = "") -> Dict[str, Any]:
    """Add default visualContainerObjects (title, background, header)."""
    vco: Dict[str, Any] = {}
    if title:
        vco["title"] = [{"properties": {"text": _string_literal(title)}}]
    vco["background"] = [{"properties": {"show": _bool_literal(True)}}]
    vco["visualHeader"] = [{"properties": {"show": _bool_literal(False)}}]
    visual["visual"]["visualContainerObjects"] = vco
    return visual


# --- Template Functions ---


def template_column_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for columnChart (vertical bar chart)."""
    v = _base_visual("columnChart", name)
    v["position"].update({"width": 600, "height": 300})
    v["visual"]["query"] = {
        "queryState": {"Category": {"projections": []}, "Y": {"projections": []}}
    }
    v["visual"]["objects"] = {
        "valueAxis": [{"properties": {"show": _bool_literal(True)}}],
        "categoryAxis": [{"properties": {"show": _bool_literal(True)}}],
        "legend": [{"properties": {"show": _bool_literal(False)}}],
        "labels": [{"properties": {"show": _bool_literal(False)}}],
    }
    return _with_default_container_objects(v)


def template_bar_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for barChart (horizontal bar chart)."""
    v = _base_visual("barChart", name)
    v["position"].update({"width": 600, "height": 300})
    v["visual"]["query"] = {
        "queryState": {"Category": {"projections": []}, "Y": {"projections": []}}
    }
    v["visual"]["objects"] = {
        "valueAxis": [{"properties": {"show": _bool_literal(True)}}],
        "categoryAxis": [{"properties": {"show": _bool_literal(True)}}],
        "legend": [{"properties": {"show": _bool_literal(False)}}],
    }
    return _with_default_container_objects(v)


def template_line_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for lineChart."""
    v = _base_visual("lineChart", name)
    v["position"].update({"width": 600, "height": 300})
    v["visual"]["query"] = {
        "queryState": {"Category": {"projections": []}, "Y": {"projections": []}}
    }
    v["visual"]["objects"] = {
        "valueAxis": [{"properties": {"show": _bool_literal(True)}}],
        "categoryAxis": [{"properties": {"show": _bool_literal(True)}}],
        "legend": [
            {"properties": {"show": _bool_literal(True), "position": _string_literal("TopRight")}}
        ],
        "labels": [{"properties": {"show": _bool_literal(False)}}],
    }
    return _with_default_container_objects(v)


def template_area_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for areaChart."""
    v = _base_visual("areaChart", name)
    v["position"].update({"width": 600, "height": 300})
    v["visual"]["query"] = {
        "queryState": {"Category": {"projections": []}, "Y": {"projections": []}}
    }
    v["visual"]["objects"] = {
        "valueAxis": [{"properties": {"show": _bool_literal(True)}}],
        "categoryAxis": [{"properties": {"show": _bool_literal(True)}}],
        "legend": [{"properties": {"show": _bool_literal(True)}}],
    }
    return _with_default_container_objects(v)


def template_combo_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for lineClusteredColumnComboChart."""
    v = _base_visual("lineClusteredColumnComboChart", name)
    v["position"].update({"width": 600, "height": 300})
    v["visual"]["query"] = {
        "queryState": {
            "Category": {"projections": []},
            "Y": {"projections": []},
            "Y2": {"projections": []},
        }
    }
    v["visual"]["objects"] = {
        "valueAxis": [{"properties": {"show": _bool_literal(True)}}],
        "categoryAxis": [{"properties": {"show": _bool_literal(True)}}],
        "legend": [{"properties": {"show": _bool_literal(True)}}],
    }
    return _with_default_container_objects(v)


def template_donut_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for donutChart."""
    v = _base_visual("donutChart", name)
    v["position"].update({"width": 300, "height": 300})
    v["visual"]["query"] = {
        "queryState": {"Category": {"projections": []}, "Y": {"projections": []}}
    }
    v["visual"]["objects"] = {
        "legend": [
            {"properties": {"show": _bool_literal(True), "position": _string_literal("Right")}}
        ],
        "labels": [{"properties": {"show": _bool_literal(True)}}],
    }
    return _with_default_container_objects(v)


def template_pie_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for pieChart."""
    v = _base_visual("pieChart", name)
    v["position"].update({"width": 300, "height": 300})
    v["visual"]["query"] = {
        "queryState": {"Category": {"projections": []}, "Y": {"projections": []}}
    }
    v["visual"]["objects"] = {
        "legend": [{"properties": {"show": _bool_literal(True)}}],
        "labels": [{"properties": {"show": _bool_literal(True)}}],
    }
    return _with_default_container_objects(v)


def template_waterfall_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for waterfallChart."""
    v = _base_visual("waterfallChart", name)
    v["position"].update({"width": 600, "height": 300})
    v["visual"]["query"] = {
        "queryState": {"Category": {"projections": []}, "Y": {"projections": []}}
    }
    v["visual"]["objects"] = {
        "valueAxis": [{"properties": {"show": _bool_literal(True)}}],
        "categoryAxis": [{"properties": {"show": _bool_literal(True)}}],
        "labels": [{"properties": {"show": _bool_literal(True)}}],
    }
    return _with_default_container_objects(v)


def template_clustered_column_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for clusteredColumnChart."""
    v = _base_visual("clusteredColumnChart", name)
    v["position"].update({"width": 600, "height": 300})
    v["visual"]["query"] = {
        "queryState": {"Category": {"projections": []}, "Y": {"projections": []}}
    }
    v["visual"]["objects"] = {
        "valueAxis": [{"properties": {"show": _bool_literal(True)}}],
        "categoryAxis": [{"properties": {"show": _bool_literal(True)}}],
        "legend": [{"properties": {"show": _bool_literal(False)}}],
    }
    return _with_default_container_objects(v)


def template_clustered_bar_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for clusteredBarChart."""
    v = _base_visual("clusteredBarChart", name)
    v["position"].update({"width": 600, "height": 300})
    v["visual"]["query"] = {
        "queryState": {"Category": {"projections": []}, "Y": {"projections": []}}
    }
    v["visual"]["objects"] = {
        "valueAxis": [{"properties": {"show": _bool_literal(True)}}],
        "categoryAxis": [{"properties": {"show": _bool_literal(True)}}],
        "legend": [{"properties": {"show": _bool_literal(False)}}],
    }
    return _with_default_container_objects(v)


def template_stacked_column_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for stackedColumnChart."""
    v = _base_visual("stackedColumnChart", name)
    v["position"].update({"width": 600, "height": 300})
    v["visual"]["query"] = {
        "queryState": {
            "Category": {"projections": []},
            "Y": {"projections": []},
            "Series": {"projections": []},
        }
    }
    v["visual"]["objects"] = {
        "valueAxis": [{"properties": {"show": _bool_literal(True)}}],
        "categoryAxis": [{"properties": {"show": _bool_literal(True)}}],
        "legend": [{"properties": {"show": _bool_literal(True)}}],
    }
    return _with_default_container_objects(v)


def template_ribbon_chart(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for ribbonChart."""
    v = _base_visual("ribbonChart", name)
    v["position"].update({"width": 600, "height": 300})
    v["visual"]["query"] = {
        "queryState": {
            "Category": {"projections": []},
            "Y": {"projections": []},
            "Series": {"projections": []},
        }
    }
    v["visual"]["objects"] = {
        "legend": [{"properties": {"show": _bool_literal(True)}}],
    }
    return _with_default_container_objects(v)


def template_table(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for table visual."""
    v = _base_visual("tableEx", name)
    v["position"].update({"width": 600, "height": 400})
    v["visual"]["query"] = {"queryState": {"Values": {"projections": []}}}
    v["visual"]["objects"] = {
        "values": [{"properties": {"fontSize": _number_literal(10)}}],
        "columnHeaders": [
            {"properties": {"fontSize": _number_literal(10), "bold": _bool_literal(True)}}
        ],
        "total": [{"properties": {"fontSize": _number_literal(10)}}],
        "grid": [{"properties": {"gridVertical": _bool_literal(True)}}],
    }
    return _with_default_container_objects(v)


def template_matrix(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for matrix (pivotTable) visual."""
    v = _base_visual("pivotTable", name)
    v["position"].update({"width": 600, "height": 400})
    v["visual"]["query"] = {
        "queryState": {
            "Rows": {"projections": []},
            "Columns": {"projections": []},
            "Values": {"projections": []},
        }
    }
    v["visual"]["objects"] = {
        "values": [{"properties": {"fontSize": _number_literal(10)}}],
        "columnHeaders": [
            {"properties": {"fontSize": _number_literal(10), "bold": _bool_literal(True)}}
        ],
        "rowHeaders": [{"properties": {"fontSize": _number_literal(10)}}],
        "grid": [{"properties": {"gridVertical": _bool_literal(True)}}],
    }
    return _with_default_container_objects(v)


def template_card(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for new card visual (cardVisual)."""
    v = _base_visual("cardVisual", name)
    v["position"].update({"width": 200, "height": 100})
    v["visual"]["query"] = {"queryState": {"Data": {"projections": []}}}
    v["visual"]["objects"] = {
        "label": [{"properties": {"show": _bool_literal(False)}, "selector": {"id": "default"}}],
        "value": [
            {
                "properties": {
                    "fontSize": _number_literal(14),
                },
                "selector": {"id": "default"},
            }
        ],
        "fillCustom": [{"properties": {"show": _bool_literal(False)}}],
        "divider": [{"properties": {"show": _bool_literal(False)}, "selector": {"id": "default"}}],
        "outline": [{"properties": {"show": _bool_literal(False)}, "selector": {"id": "default"}}],
    }
    return _with_default_container_objects(v)


def template_multi_row_card(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for multiRowCard visual."""
    v = _base_visual("multiRowCard", name)
    v["position"].update({"width": 400, "height": 200})
    v["visual"]["query"] = {"queryState": {"Values": {"projections": []}}}
    v["visual"]["objects"] = {
        "dataLabels": [{"properties": {"fontSize": _number_literal(12)}}],
        "cardTitle": [{"properties": {"fontSize": _number_literal(10)}}],
    }
    return _with_default_container_objects(v)


def template_kpi(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for KPI visual."""
    v = _base_visual("kpi", name)
    v["position"].update({"width": 200, "height": 150})
    v["visual"]["query"] = {
        "queryState": {
            "Value": {"projections": []},
            "TrendAxis": {"projections": []},
            "Goal": {"projections": []},
        }
    }
    return _with_default_container_objects(v)


def template_gauge(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for gauge visual."""
    v = _base_visual("gauge", name)
    v["position"].update({"width": 250, "height": 250})
    v["visual"]["query"] = {
        "queryState": {
            "Value": {"projections": []},
            "MinValue": {"projections": []},
            "MaxValue": {"projections": []},
            "TargetValue": {"projections": []},
        }
    }
    return _with_default_container_objects(v)


def template_slicer(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for slicer visual (dropdown mode)."""
    v = _base_visual("slicer", name)
    v["position"].update({"width": 210, "height": 75})
    v["visual"]["query"] = {"queryState": {"Values": {"projections": []}}}
    # P2-55: slicer header/items textSize now inherits from the theme's
    # textClasses.label instead of being hardcoded. Prior 11D/12D overrides
    # defeated theme-compliance audits by writing a visual-level override on
    # every new slicer.
    v["visual"]["objects"] = {
        "data": [{"properties": {"mode": _string_literal("Dropdown")}}],
        "selection": [{"properties": {"strictSingleSelect": _bool_literal(False)}}],
        "general": [{"properties": {"selfFilterEnabled": _bool_literal(True)}}],
    }
    vco = {
        "background": [{"properties": {"show": _bool_literal(False)}}],
        "visualHeader": [{"properties": {"show": _bool_literal(False)}}],
    }
    v["visual"]["visualContainerObjects"] = vco
    v["visual"]["drillFilterOtherVisuals"] = True
    return v


def template_shape(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for shape visual (rectangle by default)."""
    v = _base_visual("shape", name)
    v["position"].update({"width": 400, "height": 200})
    v["visual"]["objects"] = {
        "shape": [{"properties": {"tileShape": _string_literal("rectangle")}}],
        "fill": [
            {
                "properties": {
                    "fillColor": _theme_color(2, 0.6),
                    "transparency": _number_literal(0),
                },
                "selector": {"id": "default"},
            }
        ],
        "outline": [{"properties": {"show": _bool_literal(False)}}],
    }
    return v


def template_textbox(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for textbox visual.

    ``general[0].properties.paragraphs`` is a plain JSON array of
    ``{textRuns: [{value, textStyle}]}`` (the shape Desktop writes), not a
    Literal-wrapped JSON string.
    """
    v = _base_visual("textbox", name)
    v["position"].update({"width": 300, "height": 50})
    v["visual"]["objects"] = {
        "general": [
            {
                "properties": {
                    "paragraphs": [
                        {
                            "textRuns": [
                                {
                                    "value": "Text here",
                                    "textStyle": {"fontFamily": "Segoe UI", "fontSize": "12pt"},
                                }
                            ]
                        }
                    ]
                }
            }
        ]
    }
    vco = {
        "background": [{"properties": {"show": _bool_literal(False)}}],
        "visualHeader": [{"properties": {"show": _bool_literal(False)}}],
    }
    v["visual"]["visualContainerObjects"] = vco
    return v


def template_action_button(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for actionButton visual."""
    v = _base_visual("actionButton", name)
    v["position"].update({"width": 150, "height": 40})
    v["visual"]["objects"] = {
        "icon": [{"properties": {"shapeType": _string_literal("RightArrow")}}],
        "outline": [{"properties": {"show": _bool_literal(False)}}],
        "fill": [
            {
                "properties": {"fillColor": _theme_color(0, 0)},
                "selector": {"id": "default"},
            }
        ],
        "text": [
            {
                "properties": {
                    "text": _string_literal("Button"),
                    "fontSize": _number_literal(11),
                    "fontColor": _theme_color(1, 0),
                },
                "selector": {"id": "default"},
            }
        ],
    }
    vco = {
        "background": [{"properties": {"show": _bool_literal(False)}}],
        "visualHeader": [{"properties": {"show": _bool_literal(False)}}],
    }
    v["visual"]["visualContainerObjects"] = vco
    return v


def template_image(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for image visual."""
    v = _base_visual("image", name)
    v["position"].update({"width": 200, "height": 200})
    v["visual"]["objects"] = {
        "imageScaling": [{"properties": {"imageScalingType": _string_literal("Fit")}}],
    }
    return v


def template_visual_group(name: Optional[str] = None) -> Dict[str, Any]:
    """Template for a visual group (container for other visuals)."""
    v = _base_visual("group", name)
    v["position"].update({"width": 400, "height": 300})
    # Visual groups have no visual.visualType — they use visualGroup instead.
    # H-25: real groups (733 in R0201) all carry ``groupMode`` and NEVER nest
    # ``isHidden`` inside ``visualGroup`` (it is a top-level container key).
    del v["visual"]
    v["visualGroup"] = {
        "displayName": "Visual Group",
        "groupMode": "ScaleMode",
    }
    return v


def template_button_slicer(visual_id: Optional[str] = None) -> Dict[str, Any]:
    """Template for button slicer visual (GA Oct 2025, schema 2.3.0+)."""
    vid = visual_id or generate_visual_id()
    return {
        "$schema": VISUAL_SCHEMA,
        "name": vid,
        "position": {"x": 0, "y": 0, "width": 300, "height": 60, "z": 0, "tabOrder": 0},
        "visual": {
            "visualType": "buttonSlicer",
            "query": {"queryState": {"Values": {"projections": []}}},
            "objects": {},
            "visualContainerObjects": {},
        },
    }


def template_text_slicer(visual_id: Optional[str] = None) -> Dict[str, Any]:
    """Template for text slicer visual (preview Nov 2024, schema 1.4.0+)."""
    vid = visual_id or generate_visual_id()
    return {
        "$schema": VISUAL_SCHEMA,
        "name": vid,
        "position": {"x": 0, "y": 0, "width": 200, "height": 48, "z": 0, "tabOrder": 0},
        "visual": {
            "visualType": "textSlicer",
            "query": {"queryState": {"Values": {"projections": []}}},
            "objects": {},
            "visualContainerObjects": {},
        },
    }


def template_list_slicer(visual_id: Optional[str] = None) -> Dict[str, Any]:
    """Template for list slicer visual (preview Nov 2024, schema 1.4.0+)."""
    vid = visual_id or generate_visual_id()
    return {
        "$schema": VISUAL_SCHEMA,
        "name": vid,
        "position": {"x": 0, "y": 0, "width": 200, "height": 300, "z": 0, "tabOrder": 0},
        "visual": {
            "visualType": "listSlicer",
            "query": {"queryState": {"Values": {"projections": []}}},
            "objects": {},
            "visualContainerObjects": {},
        },
    }


def template_input_slicer(visual_id: Optional[str] = None) -> Dict[str, Any]:
    """Template for input slicer visual (GA Feb 2026, schema 2.6.0+)."""
    vid = visual_id or generate_visual_id()
    return {
        "$schema": VISUAL_SCHEMA,
        "name": vid,
        "position": {"x": 0, "y": 0, "width": 200, "height": 48, "z": 0, "tabOrder": 0},
        "visual": {
            "visualType": "inputSlicer",
            "query": {"queryState": {"Values": {"projections": []}}},
            "objects": {},
            "visualContainerObjects": {},
        },
    }


# --- Template Registry ---

TEMPLATE_REGISTRY: Dict[str, Callable[[Optional[str]], Dict[str, Any]]] = {
    # Charts
    "columnChart": template_column_chart,
    "barChart": template_bar_chart,
    "lineChart": template_line_chart,
    "areaChart": template_area_chart,
    "lineClusteredColumnComboChart": template_combo_chart,
    "donutChart": template_donut_chart,
    "pieChart": template_pie_chart,
    "waterfallChart": template_waterfall_chart,
    "clusteredColumnChart": template_clustered_column_chart,
    "clusteredBarChart": template_clustered_bar_chart,
    "stackedColumnChart": template_stacked_column_chart,
    "ribbonChart": template_ribbon_chart,
    # Tables
    "table": template_table,
    "tableEx": template_table,
    "matrix": template_matrix,
    "pivotTable": template_matrix,
    # KPI/Cards
    "card": template_card,
    "cardVisual": template_card,
    "multiRowCard": template_multi_row_card,
    "kpi": template_kpi,
    "gauge": template_gauge,
    # Slicers
    "slicer": template_slicer,
    "buttonSlicer": template_button_slicer,
    "textSlicer": template_text_slicer,
    "listSlicer": template_list_slicer,
    "inputSlicer": template_input_slicer,
    # Layout
    "shape": template_shape,
    "textbox": template_textbox,
    "actionButton": template_action_button,
    "image": template_image,
    # Groups
    "visualGroup": template_visual_group,
    "group": template_visual_group,
}

# Bucket name mapping: maps visual types to their expected queryState bucket names
VISUAL_TYPE_BUCKETS: Dict[str, Dict[str, str]] = {
    "columnChart": {"category": "Category", "values": "Y"},
    "barChart": {"category": "Category", "values": "Y"},
    "lineChart": {"category": "Category", "values": "Y"},
    "areaChart": {"category": "Category", "values": "Y"},
    "lineClusteredColumnComboChart": {"category": "Category", "values": "Y", "values2": "Y2"},
    "donutChart": {"category": "Category", "values": "Y"},
    "pieChart": {"category": "Category", "values": "Y"},
    "waterfallChart": {"category": "Category", "values": "Y"},
    "clusteredColumnChart": {"category": "Category", "values": "Y"},
    "clusteredBarChart": {"category": "Category", "values": "Y"},
    "stackedColumnChart": {"category": "Category", "values": "Y", "series": "Series"},
    "ribbonChart": {"category": "Category", "values": "Y", "series": "Series"},
    "table": {"values": "Values"},
    "tableEx": {"values": "Values"},
    "matrix": {"rows": "Rows", "columns": "Columns", "values": "Values"},
    "pivotTable": {"rows": "Rows", "columns": "Columns", "values": "Values"},
    # P0-19 fix: bucket role names must match visual_roles.VISUAL_ROLES.
    # - `card` is authored with the cardVisual template (visualType
    #   "cardVisual"), so it binds to cardVisual's role "Data" (H-18c: binding
    #   to "Values" left the card blank).
    # - `cardVisual` takes role "Data".
    # - `kpi` takes roles "Indicator", "TrendLine", "Goal" (NOT "Value"/"TrendAxis").
    "card": {"values": "Data"},
    "cardVisual": {"values": "Data"},
    "multiRowCard": {"values": "Values"},
    "kpi": {"values": "Indicator", "trend": "TrendLine", "goal": "Goal"},
    "gauge": {"values": "Value", "min": "MinValue", "max": "MaxValue", "target": "TargetValue"},
    "slicer": {"values": "Values"},
}


def get_template(visual_type: str, name: Optional[str] = None) -> Dict[str, Any]:
    """Get a template for the given visual type.

    Args:
        visual_type: Power BI visual type string
        name: Optional visual ID (generated if not provided)

    Returns:
        Complete visual.json dict (deep copy)

    Raises:
        KeyError: If visual_type is not in the registry
    """
    import copy

    template_fn = TEMPLATE_REGISTRY.get(visual_type)
    if not template_fn:
        raise KeyError(
            f"Unknown visual type: '{visual_type}'. "
            f"Available: {', '.join(sorted(set(TEMPLATE_REGISTRY.keys())))}"
        )
    return copy.deepcopy(template_fn(name))


def get_template_catalog() -> List[Dict[str, str]]:
    """Get a catalog of all available templates with descriptions.

    Returns:
        List of {type, category, description, buckets} dicts
    """
    seen = set()
    catalog = []
    categories = {
        "columnChart": "Charts",
        "barChart": "Charts",
        "lineChart": "Charts",
        "areaChart": "Charts",
        "lineClusteredColumnComboChart": "Charts",
        "donutChart": "Charts",
        "pieChart": "Charts",
        "waterfallChart": "Charts",
        "clusteredColumnChart": "Charts",
        "clusteredBarChart": "Charts",
        "stackedColumnChart": "Charts",
        "ribbonChart": "Charts",
        "table": "Tables",
        "tableEx": "Tables",
        "matrix": "Tables",
        "pivotTable": "Tables",
        "card": "KPI/Cards",
        "cardVisual": "KPI/Cards",
        "multiRowCard": "KPI/Cards",
        "kpi": "KPI/Cards",
        "gauge": "KPI/Cards",
        "slicer": "Slicers",
        "shape": "Layout",
        "textbox": "Layout",
        "actionButton": "Layout",
        "image": "Layout",
        "visualGroup": "Groups",
        "group": "Groups",
    }

    for vtype, fn in TEMPLATE_REGISTRY.items():
        if fn in seen:
            continue
        seen.add(fn)
        buckets = VISUAL_TYPE_BUCKETS.get(vtype, {})
        catalog.append(
            {
                "type": vtype,
                "category": categories.get(vtype, "Other"),
                "buckets": buckets,
            }
        )

    return catalog
