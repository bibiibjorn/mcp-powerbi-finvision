# Visual Catalog Tooling

Builds `docs/visual-catalog/` from `pbir schema` output + R0101 `visual.json`
extracts. No CLI — this is a library. Invoke functions directly.

## Prerequisites
- Python 3.13
- `pbir` 0.9.9 on PATH
- `Pillow` (listed in repo `requirements.txt`)
- Source report (any PBIR report) at: `C:\path\to\YourReport.Report`

## Full rebuild

```python
from pathlib import Path
from tools.visual_catalog.build import build_all

build_all(
    report_root=Path(r"C:\path\to\YourReport.Report"),
    out_dir=Path("docs/visual-catalog"),
)
```

This: (1) shells out to `pbir schema ... --json` for every in-scope type,
caching JSON under `_cache/schema/`; (2) copies every R0101 `visual.json` into
`_extracts/`; (3) renders per-type markdown files, `INDEX.md`, and shared
container/convention files under `docs/visual-catalog/`.

## Screenshot pass (agent-driven, through MCP)

After `build_all` runs, a picker-selected subset of R0101 visuals still needs
PNG crops. This runs through the existing MCP:

1. `12_Autonomous_Workflow:enter_mode` with the R0101 `.pbip` path
2. `12_Autonomous_Workflow:reopen` → `wait_ready`
3. For each (page, visual) in `build.list_screenshots_needed(out_dir)`:
   - `navigate_page`
   - wait 3s
   - `screenshot` to a temp PNG
   - call `tools.visual_catalog.crop.crop_visual(...)` to write the final
     `docs/visual-catalog/_extracts/<page>__<id>.png`
4. `exit_mode`
5. Re-run `build_all` so per-type files pick up the new PNGs (manual fences preserved)

## Regeneration safety

`render` preserves content inside `<!-- MANUAL: <slug> -->` / `<!-- /MANUAL -->`
fences. Allowed slugs: `overview`, `when-to-use`, `gotchas`, `related-extras`,
`per-template-notes`. Anything else is a bug.
