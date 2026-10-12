# pythonVisual

**Type key:** `pythonVisual` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`pythonVisual` runs a user Python script (matplotlib/seaborn/plotly-static) against the `Values` projection and renders the resulting image. The only formatting object is `script` — which holds the source. All appearance is decided inside the script.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use when the chart genuinely cannot be expressed natively or as a Deneb spec: statistical plots (violin, ridgeline, dendrogram), sklearn output. Prefer Deneb (`pbi-custom-visuals`) or SVG-via-DAX first — both render without a Python runtime and work in the Service without a personal gateway.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Values` | yes | yes | Column, Measure |

## Containers

- **script** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/pythonVisual.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object pythonVisual <object>
powerbi-report-author formatting search pythonVisual <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "pythonVisual",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### The DataFrame is de-duplicated and row-capped

Power BI passes `Values` as a pandas DataFrame with duplicate rows removed and a hard row cap (150k). Add a unique key column to the projection if row identity matters.

### Script lives in `visual.objects.script` — inject it, don't hand-type

Use `07_Visual_Operations:inject_code` with `code_type='python'`; hand-editing the escaped string in `visual.json` corrupts easily.

### Runtime dependency

Requires a Python runtime on the authoring machine and a personal gateway in the Service. It does not render at all in Power BI Embedded / Publish-to-web.

### Static image only

No cross-filtering out of the visual, no tooltips, no drill.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-pythonvisual](../GUIDE.md#cli-pythonvisual)
- JSON format: [../GUIDE.md#json-pythonvisual](../GUIDE.md#json-pythonvisual)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/pythonVisual.md`
<!-- /MANUAL -->
