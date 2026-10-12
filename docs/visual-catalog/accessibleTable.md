# accessibleTable

**Type key:** `accessibleTable` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`accessibleTable` is the screen-reader-oriented tabular rendering: the same `Rows` / `Columns` / `Values` shape as a matrix, emitted as a semantic HTML table with proper header associations rather than a canvas-drawn grid. Its formatting surface is deliberately minimal — only `general` — because presentation is delegated to the accessibility layer.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use when a report must satisfy a strict screen-reader/WCAG requirement for tabular data and the visual formatting of `pivotTable` / `tableEx` is not worth the accessibility trade-off. For normal reporting use `pivotTable` (cross-tab) or `tableEx` (flat).
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Rows` | no | yes | Column, Hierarchy |
| `Columns` | no | yes | Column, Hierarchy |
| `Values` | yes | yes | Measure |

## Containers

- **general** — see [./_shared/containers-general.md](./_shared/containers-general.md)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/accessibleTable.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object accessibleTable <object>
powerbi-report-author formatting search accessibleTable <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "accessibleTable",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### Almost no formatting surface

One formatting object (`general`). Do not expect the `pivotTable` object set — style presets, per-level fonts and grid options simply do not exist here. Theme it via the shared VCO chrome instead.

### Not a drop-in replacement for `pivotTable`

Same roles, very different rendering. Swapping the type key on an existing visual silently drops every `visual.objects` entry.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-accessibletable](../GUIDE.md#cli-accessibletable)
- JSON format: [../GUIDE.md#json-accessibletable](../GUIDE.md#json-accessibletable)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/accessibleTable.md`
<!-- /MANUAL -->
