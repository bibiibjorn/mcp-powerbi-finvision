# heatMap

**Type key:** `heatMap` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`heatMap` renders density/intensity over a map surface: `Category` / `Series` locate the points, `Size` (or `Y`) drives intensity, and the `heatMap` object controls radius, intensity and the colour gradient. It shares the Azure-Maps-style plumbing (`mapControls`, `mapStyles`, `bubbles`) with `azureMap`.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use for geographic density where individual points are not the story (client concentration, transaction density). For per-location values prefer `filledMap` (choropleth) or `azureMap` bubbles — a heat map cannot be read quantitatively.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Category` | yes | no | Column |
| `Series` | no | no | Column |
| `X` | no | no | Column, Measure |
| `Y` | no | yes | Measure |
| `Size` | no | no | Measure |
| `Tooltips` | no | yes | Measure, Aggregation |

## Containers

- **bubbles** — see the generated property surface (Related, below)
- **categoryLabels** — see the generated property surface (Related, below)
- **dataPoint** — see the generated property surface (Related, below)
- **general** — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **heatMap** — see the generated property surface (Related, below)
- **legend** — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **mapControls** — see the generated property surface (Related, below)
- **mapStyles** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/heatMap.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object heatMap <object>
powerbi-report-author formatting search heatMap <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "heatMap",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### Requires a map-capable tenant setting

Like `azureMap`, the underlying map service must be enabled in the tenant or the visual renders blank with no error in `visual.json`.

### Intensity is not a legend-readable scale

The colour ramp is relative to the visible data extent, so the same colour means different values at different zoom levels. Never put a heat map next to a number the reader is expected to reconcile.

### `mapStyles` / `mapControls` are chrome, `heatMap` is the encoding

Radius/intensity/gradient live under `visual.objects.heatMap`. Changing `mapStyles` only repaints the basemap.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-heatmap](../GUIDE.md#cli-heatmap)
- JSON format: [../GUIDE.md#json-heatmap](../GUIDE.md#json-heatmap)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/heatMap.md`
<!-- /MANUAL -->
