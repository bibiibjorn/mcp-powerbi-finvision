"""
Visual Builder for PBIP Report Authoring

Fluent API for constructing visual.json dicts from templates with
data bindings, formatting overrides, and positioning.

Usage:
    visual = (VisualBuilder("columnChart")
        .position(x=20, y=140, width=600, height=300)
        .add_measure("m Measure", "NAV Performance", bucket="Values")
        .add_column("s Date", "Month", bucket="Category")
        .set_title("Monthly Performance")
        .build())
"""

from typing import Any, Dict, List, Optional

from core.pbip.authoring.id_generator import generate_visual_id
from core.pbip.authoring.data_binding_builder import (
    build_measure_binding,
    build_column_binding,
    build_hierarchy_binding,
)
from core.pbip.authoring.visual_templates import (
    get_template,
    VISUAL_TYPE_BUCKETS,
    _bool_literal,
    _string_literal,
)


class VisualBuilder:
    """Fluent builder for constructing Power BI visual.json dicts."""

    #: Legacy role names callers use for the new card, remapped to its real role.
    _CARD_ROLE_ALIASES = {"Values": "Data"}

    def __init__(self, visual_type: str, name: Optional[str] = None):
        """Initialize builder with a visual type. name is auto-generated if not provided."""
        self._visual_type = visual_type
        #: Advisory notes collected while building (role remaps etc.).
        self.notices: List[str] = []
        self._name = name or generate_visual_id()
        self._position: Dict[str, Any] = {}
        self._projections: Dict[str, List[Dict[str, Any]]] = {}
        self._formatting: List[Dict[str, Any]] = []
        self._title: Optional[str] = None
        self._parent_group: Optional[str] = None
        self._is_hidden: bool = False
        self._group_name: Optional[str] = None
        self._sort: Optional[Dict[str, Any]] = None
        self._sync_group: Optional[Dict[str, Any]] = None
        self._extra_objects: Dict[str, Any] = {}
        self._slicer_mode: Optional[str] = None
        self._single_select: Optional[bool] = None

    def position(
        self,
        x: float = 0,
        y: float = 0,
        width: float = 300,
        height: float = 200,
        z: int = 0,
        tab_order: int = 0,
    ) -> "VisualBuilder":
        """Set visual position and size."""
        self._position = {
            "x": x,
            "y": y,
            "z": z,
            "height": height,
            "width": width,
            "tabOrder": tab_order,
        }
        return self

    def add_measure(
        self,
        table: str,
        measure: str,
        bucket: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> "VisualBuilder":
        """Add a measure binding. bucket is auto-detected from visual type if not provided."""
        resolved_bucket = self._card_role(bucket) or self._default_values_bucket()
        binding = build_measure_binding(table, measure, display_name)
        self._projections.setdefault(resolved_bucket, []).append(binding)
        return self

    def add_column(
        self,
        table: str,
        column: str,
        bucket: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> "VisualBuilder":
        """Add a column binding. bucket is auto-detected from visual type if not provided."""
        resolved_bucket = self._card_role(bucket) or self._default_category_bucket()
        binding = build_column_binding(table, column, display_name)
        self._projections.setdefault(resolved_bucket, []).append(binding)
        return self

    def add_hierarchy(
        self,
        table: str,
        hierarchy: str,
        level: str,
        bucket: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> "VisualBuilder":
        """Add a hierarchy level binding to the visual."""
        resolved_bucket = bucket or self._default_category_bucket()
        binding = build_hierarchy_binding(table, hierarchy, level, display_name)
        self._projections.setdefault(resolved_bucket, []).append(binding)
        return self

    def set_title(self, text: str) -> "VisualBuilder":
        """Set the visual title."""
        self._title = text
        return self

    def in_group(self, parent_group_name: str) -> "VisualBuilder":
        """Place visual inside a visual group."""
        self._parent_group = parent_group_name
        return self

    def hidden(self, is_hidden: bool = True) -> "VisualBuilder":
        """Set visual visibility."""
        self._is_hidden = is_hidden
        return self

    def set_group_name(self, name: str) -> "VisualBuilder":
        """Set group display name (for visualGroup type)."""
        self._group_name = name
        return self

    def set_formatting(self, config_type: str, property_name: str, value: Any) -> "VisualBuilder":
        """Add a formatting override. config_type: e.g. 'valueAxis', 'labels'; value is auto-formatted."""
        self._formatting.append(
            {
                "config_type": config_type,
                "property_name": property_name,
                "value": value,
            }
        )
        return self

    def set_sort(
        self, table: str, field: str, field_type: str = "Measure", direction: str = "Descending"
    ) -> "VisualBuilder":
        """Set sort definition for the visual."""
        if field_type == "Measure":
            field_ref = {
                "Measure": {
                    "Expression": {"SourceRef": {"Entity": table}},
                    "Property": field,
                }
            }
        else:
            field_ref = {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": table}},
                    "Property": field,
                }
            }
        self._sort = {
            "sort": [{"field": field_ref, "direction": direction}],
            "isDefaultSort": True,
        }
        return self

    def set_sync_group(self, group_name: str) -> "VisualBuilder":
        """Set slicer sync group."""
        self._sync_group = {
            "groupName": group_name,
            "fieldChanges": True,
            "filterChanges": True,
        }
        return self

    def set_slicer_mode(
        self, mode: str = "Dropdown", single_select: bool = False
    ) -> "VisualBuilder":
        """Configure slicer mode and selection behavior."""
        self._slicer_mode = mode
        self._single_select = single_select
        return self

    def set_object(self, config_type: str, properties: Dict[str, Any]) -> "VisualBuilder":
        """Set a complete objects entry (advanced)."""
        self._extra_objects[config_type] = [{"properties": properties}]
        return self

    def build(self) -> Dict[str, Any]:
        """Build the complete visual.json dict ready to write to disk."""
        # Get template
        if self._visual_type in ("visualGroup", "group"):
            return self._build_visual_group()

        template = get_template(self._visual_type, self._name)

        # Apply position
        if self._position:
            template["position"].update(self._position)

        # Apply data bindings
        if self._projections:
            query_state = template.get("visual", {}).get("query", {}).get("queryState", {})
            for bucket_name, projections in self._projections.items():
                if bucket_name in query_state:
                    query_state[bucket_name]["projections"] = projections
                else:
                    query_state[bucket_name] = {"projections": projections}

        # Apply sort
        if self._sort:
            template.setdefault("visual", {}).setdefault("query", {})["sortDefinition"] = self._sort

        # Apply sync group
        if self._sync_group:
            template.setdefault("visual", {})["syncGroup"] = self._sync_group

        # Apply slicer mode
        if self._slicer_mode:
            objects = template.setdefault("visual", {}).setdefault("objects", {})
            objects["data"] = [{"properties": {"mode": _string_literal(self._slicer_mode)}}]
            if self._single_select is not None:
                objects["selection"] = [
                    {"properties": {"strictSingleSelect": _bool_literal(self._single_select)}}
                ]

        # Apply formatting overrides
        for fmt in self._formatting:
            self._apply_formatting(template, fmt)

        # Apply extra objects
        for config_type, obj_list in self._extra_objects.items():
            template.setdefault("visual", {}).setdefault("objects", {})[config_type] = obj_list

        # Apply title
        if self._title:
            vco = template.setdefault("visual", {}).setdefault("visualContainerObjects", {})
            vco["title"] = [{"properties": {"text": _string_literal(self._title)}}]

        # Apply parent group
        if self._parent_group:
            template["parentGroupName"] = self._parent_group

        # Apply hidden
        if self._is_hidden:
            template["isHidden"] = True

        return template

    def _build_visual_group(self) -> Dict[str, Any]:
        """Build a visual group container."""
        from core.pbip.authoring.visual_templates import get_template

        template = get_template("visualGroup", self._name)

        if self._position:
            template["position"].update(self._position)

        if self._group_name:
            template["visualGroup"]["displayName"] = self._group_name

        if self._parent_group:
            template["parentGroupName"] = self._parent_group

        if self._is_hidden:
            # H-25: isHidden is a TOP-LEVEL container key, never inside visualGroup.
            template["isHidden"] = True

        return template

    def _apply_formatting(self, template: Dict[str, Any], fmt: Dict[str, Any]) -> None:
        """Apply a single formatting override to the template."""
        config_type = fmt["config_type"]
        prop_name = fmt["property_name"]
        value = fmt["value"]

        # Format value for Power BI
        formatted = self._format_value(value)

        # P1-56 fix: container-level (chrome) properties belong in
        # visual.visualContainerObjects, NOT visual.objects. Writing `title`,
        # `background`, `border`, `visualHeader`, or `padding` into
        # visual.objects lands them where PBI ignores them, leaving the
        # chrome unstyled. Route via the canonical container set.
        _CHROME_CONTAINERS = {
            "title",
            "subTitle",
            "background",
            "border",
            "visualHeader",
            "padding",
            "divider",
        }

        visual = template.setdefault("visual", {})
        if config_type in _CHROME_CONTAINERS:
            root = visual.setdefault("visualContainerObjects", {})
        else:
            root = visual.setdefault("objects", {})

        config_list = root.setdefault(config_type, [{}])

        # Find or create the properties dict
        if not config_list:
            config_list.append({"properties": {}})
        props = config_list[0].setdefault("properties", {})
        props[prop_name] = formatted

    @staticmethod
    def _format_value(value: Any) -> Any:
        """Format a Python value for Power BI's expression format (one encoder — H-15)."""
        from core.pbip.authoring.formatting import property_value

        return property_value("", value)

    def _card_role(self, bucket: Optional[str]) -> Optional[str]:
        """H-18c: ``card`` / ``cardVisual`` bind to role ``Data``; remap ``Values``."""
        if bucket and self._visual_type in ("card", "cardVisual"):
            mapped = self._CARD_ROLE_ALIASES.get(bucket)
            if mapped:
                self.notices.append(
                    f"role '{bucket}' remapped to '{mapped}' ({self._visual_type} is authored "
                    "as cardVisual, whose value role is Data)"
                )
                return mapped
        return bucket

    def _default_values_bucket(self) -> str:
        """Get the default values bucket for this visual type."""
        buckets = VISUAL_TYPE_BUCKETS.get(self._visual_type, {})
        return buckets.get("values", "Values")

    def _default_category_bucket(self) -> str:
        """Get the default category bucket for this visual type."""
        buckets = VISUAL_TYPE_BUCKETS.get(self._visual_type, {})
        return buckets.get("category", buckets.get("rows", buckets.get("values", "Category")))
