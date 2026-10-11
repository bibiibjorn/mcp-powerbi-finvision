# dataQueryVisual

**Type key:** `dataQueryVisual` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`dataQueryVisual` is the raw data-query/inspection visual: a single `Values` role plus `columnOptions`, `general` and — notably — `overrideDataReduction`, which lifts the standard visual row-reduction cap. It is the surface behind data-exploration/"see the rows" experiences rather than a presentation visual.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use for data inspection and export-shaped views where the row cap of a normal table is the blocker. For anything a reader consumes use `tableEx`. Note `04_Run_DAX` with `export_path` is usually a better answer than authoring this visual.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Values` | yes | yes | Column, Measure |

## Containers

- **columnOptions** — see the generated property surface (Related, below)
- **general** — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **overrideDataReduction** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/dataQueryVisual.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object dataQueryVisual <object>
powerbi-report-author formatting search dataQueryVisual <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "dataQueryVisual",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### `overrideDataReduction` is a performance foot-gun

Lifting the reduction cap makes the visual materialize far more rows — expect large SE scans and slow page loads. Profile with `09_Profile:page` after enabling it.

### Not a presentation visual

Minimal formatting surface by design. Do not try to theme it into a report table.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-dataqueryvisual](../GUIDE.md#cli-dataqueryvisual)
- JSON format: [../GUIDE.md#json-dataqueryvisual](../GUIDE.md#json-dataqueryvisual)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/dataQueryVisual.md`
<!-- /MANUAL -->
