"""Visual Catalog tooling.

Builds docs/visual-catalog/ from pbir schema JSON + R0101 visual.json extracts.
Screenshots are captured separately through the existing 12_Autonomous_Workflow
MCP ops; this package only knows where the PNGs should land and how to crop
them.

Public entry points:
  - tools.visual_catalog.build.build_all(report_root, out_dir)
  - tools.visual_catalog.schema.harvest_schema(...)
  - tools.visual_catalog.inventory.harvest_r0101(...)
  - tools.visual_catalog.render.render_type_file(...)
"""

__version__ = "0.1.0"
