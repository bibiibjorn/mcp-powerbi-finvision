# scriptVisual

**Type key:** `scriptVisual` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`scriptVisual` is the R equivalent of `pythonVisual`: one `Values` role, one `script` object holding the R source, and a static image out. Same runtime/gateway constraints.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use when the requirement is specifically an R package (ggplot2, survival, forecast). Otherwise the same preference order applies: native visual → Deneb → SVG-via-DAX → script visual.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Values` | yes | yes | Column, Measure |

## Containers

- **script** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/scriptVisual.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object scriptVisual <object>
powerbi-report-author formatting search scriptVisual <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "scriptVisual",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### `scriptVisual` = R, `pythonVisual` = Python

The names are not self-evident. Injecting Python into `scriptVisual` fails at render with an opaque error.

### Same de-duplication and row cap as the Python visual

The `dataset` data.frame drops duplicate rows; add a key column when row identity matters.

### Inject via `inject_code` with `code_type='r'`

Do not hand-edit the escaped source in `visual.json`.

### Runtime + gateway dependency, static output

Needs a local R install to author and a personal gateway to refresh in the Service; no cross-filtering out.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-scriptvisual](../GUIDE.md#cli-scriptvisual)
- JSON format: [../GUIDE.md#json-scriptvisual](../GUIDE.md#json-scriptvisual)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/scriptVisual.md`
<!-- /MANUAL -->
