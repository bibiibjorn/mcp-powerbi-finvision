"""
PBIP Report Authoring Package

Provides tools for creating, cloning PBIP report pages and visuals.

Modules:
    id_generator        - Generate page/visual/bookmark IDs
    data_binding_builder - Build queryState projections from (table, field) tuples
    visual_templates    - Predefined JSON skeletons for each visual type
    visual_builder      - Fluent API for constructing visual.json dicts
    page_builder        - Create page folder structure with visuals
    clone_engine        - Deep-clone pages/reports with ID regeneration
    pbir_executor       - Canonical write_visual / delete_visual_files persistence helpers
    reference_cleanup   - strip_dangling_interactions / strip_dangling_visual_refs
"""

from core.pbip.authoring.id_generator import generate_visual_id, generate_guid
from core.pbip.authoring.clone_engine import CloneEngine
from core.pbip.authoring.visual_builder import VisualBuilder
from core.pbip.authoring.page_builder import PageBuilder
from core.pbip.authoring.pbir_executor import write_visual, delete_visual_files
from core.pbip.authoring.reference_cleanup import (
    strip_dangling_interactions,
    strip_dangling_visual_refs,
)

__all__ = [
    "generate_visual_id",
    "generate_guid",
    "CloneEngine",
    "VisualBuilder",
    "PageBuilder",
    "write_visual",
    "delete_visual_files",
    "strip_dangling_interactions",
    "strip_dangling_visual_refs",
]
