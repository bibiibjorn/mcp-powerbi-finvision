"""
Page Builder for PBIP Report Authoring

Creates PBIP page folder structures with page.json, visual folders,
and updates pages.json ordering.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.pbip.authoring import safe_io
from core.pbip.authoring.id_generator import generate_visual_id

logger = logging.getLogger(__name__)

# Default schema version for generated pages; the version sibling pages of the
# same report carry wins (H-36 — real reports are on page/2.1.0).
PAGE_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json"

#: PBIR ``displayOption`` string enum (H-04: never the legacy PBIX integers).
DISPLAY_OPTIONS = ("FitToPage", "FitToWidth", "ActualSize")


class PageBuilder:
    """Builder for creating PBIP report pages with visuals."""

    def __init__(
        self,
        definition_path: Path,
        display_name: str,
        page_id: Optional[str] = None,
    ):
        """Initialize the page builder.

        Args:
            definition_path: Path to the report's definition/ folder
            display_name: Display name for the new page
            page_id: Optional page ID (auto-generated if not provided)
        """
        self._definition_path = Path(definition_path)
        self._display_name = display_name
        self._page_id = page_id or generate_visual_id()
        self._width = 1280
        self._height = 720
        self._display_option = "FitToPage"  # H-04: PBIR string enum
        self._visuals: List[Dict[str, Any]] = []
        self._filters: List[Dict[str, Any]] = []
        self._interactions: List[Dict[str, Any]] = []
        self._background_color: Optional[str] = None

    def set_dimensions(self, width: int, height: int) -> "PageBuilder":
        """Set page dimensions.

        Common sizes:
        - 1280 x 720: Default (16:9)
        - 1920 x 1080: Full HD (16:9)
        - 1366 x 768: Common laptop (16:9)
        - 1024 x 768: 4:3 aspect
        """
        self._width = width
        self._height = height
        return self

    def add_visual(self, visual_dict: Dict[str, Any]) -> "PageBuilder":
        """Add a visual to the page.

        Args:
            visual_dict: Complete visual.json dict (from VisualBuilder.build())
        """
        self._visuals.append(visual_dict)
        return self

    def add_filter(self, filter_dict: Dict[str, Any]) -> "PageBuilder":
        """Add a page-level filter."""
        self._filters.append(filter_dict)
        return self

    def add_interaction(
        self, source_visual: str, target_visual: str, interaction_type: str = "Filter"
    ) -> "PageBuilder":
        """Add a visual interaction rule.

        Args:
            source_visual: Source visual ID
            target_visual: Target visual ID
            interaction_type: "NoFilter", "DataFilter" or "HighlightFilter"
                ("Filter" / "Highlight" accepted as aliases)
        """
        aliases = {"Filter": "DataFilter", "Highlight": "HighlightFilter"}
        self._interactions.append(
            {
                "source": source_visual,
                "target": target_visual,
                "type": aliases.get(interaction_type, interaction_type),
            }
        )
        return self

    def set_background_color(self, hex_color: str) -> "PageBuilder":
        """Set page background color."""
        self._background_color = hex_color
        return self

    @property
    def page_id(self) -> str:
        """Get the page ID."""
        return self._page_id

    def build(self, insert_after: Optional[str] = None, overwrite: bool = False) -> Dict[str, Any]:
        """Create the page folder structure and write files.

        Args:
            insert_after: Page ID to insert this page after (default: end)
            overwrite: When False (default) and a page folder with the same
                ID already exists, raise FileExistsError rather than silently
                overlaying onto the existing page. P1-48 fix.

        Returns:
            Dict with page_id, display_name, visual_count, path
        """
        if not safe_io.is_safe_segment(self._page_id):
            raise ValueError(f"invalid page id {self._page_id!r}")
        page_folder = self._definition_path / "pages" / self._page_id
        # P1-48: explicit collision detection. Previously `mkdir(exist_ok=True)`
        # + blind writes silently overlaid onto an existing page, and
        # overwrote page.json + added visual subfolders beside existing
        # visuals. Fail-fast unless the caller explicitly opts in.
        if page_folder.exists() and not overwrite:
            raise FileExistsError(
                f"Page folder already exists: {page_folder}. "
                f"Pass overwrite=True to intentionally replace it."
            )
        safe_io.make_dirs(page_folder)

        page_json = self._build_page_json()
        safe_io.write_json(page_folder / "page.json", page_json)

        if self._visuals:
            from core.pbip.authoring.pbir_executor import write_visual

            for visual_dict in self._visuals:
                visual_dict.setdefault("name", generate_visual_id())
                write_visual(page_folder / "visuals", visual_dict, overwrite=overwrite)

        # H-03: register in definition/pages/pages.json (the ONLY registry).
        self._update_pages_json(insert_after)

        return {
            "success": True,
            "page_id": self._page_id,
            "display_name": self._display_name,
            "visual_count": len(self._visuals),
            "path": str(page_folder),
            **({"dry_run": True} if safe_io.dry_run_active() else {}),
        }

    def _build_page_json(self) -> Dict[str, Any]:
        """Build the page.json content (real PBIR shape)."""
        page_data: Dict[str, Any] = {
            "$schema": safe_io.sibling_schema(
                self._definition_path / "pages", "page.json", PAGE_SCHEMA
            ),
            "name": self._page_id,
            "displayName": self._display_name,
            "displayOption": self._display_option,
            "height": self._height,
            "width": self._width,
        }
        if self._filters:
            page_data["filterConfig"] = {"filters": self._filters}
        if self._interactions:
            # H-08: Desktop reads visualInteractions (never "interactions").
            page_data["visualInteractions"] = self._interactions
        if self._background_color:
            from core.pbip.pbir_model import encode_literal

            page_data["objects"] = {
                "background": [
                    {
                        "properties": {
                            "color": {
                                "solid": {
                                    "color": {
                                        "expr": {
                                            "Literal": {
                                                "Value": encode_literal(
                                                    self._background_color, "string"
                                                )
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                ]
            }
        return page_data

    def _update_pages_json(self, insert_after: Optional[str] = None) -> None:
        """Add this page to definition/pages/pages.json ordering (H-03)."""
        from core.pbip.authoring.clone_engine import update_pages_json

        update_pages_json(self._definition_path, add=self._page_id, insert_after=insert_after)
