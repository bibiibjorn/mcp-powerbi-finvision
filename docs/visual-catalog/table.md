# table

**Type key:** `table` · **Status:** legacy / deprecated · **Modern replacement:** `tableEx`

> **DEPRECATED / LEGACY visual type.** Prefer [`tableEx`](./tableEx.md) for new work.
> Documented because existing reports still use it — edit in place, build new visuals with the modern equivalent.

## Overview

<!-- MANUAL: overview -->
`table` is the legacy flat-table visual — one `Values` role holding an ordered list of columns and measures, rendered as a grid with an optional total row. Superseded by `tableEx`, which adds style presets, per-column formatting and image/URL cell rendering.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Edit existing `table` visuals in place; build new flat tables as `tableEx`. The `clustering` object is the only capability `table` has that is worth checking before migrating a visual.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Values` | yes | yes | Column, Measure |

## Containers

- **clustering** — see the generated property surface (Related, below)
- **columnFormatting** — see the generated property surface (Related, below)
- **columnHeaders** — see the generated property surface (Related, below)
- **general** — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **grid** — see the generated property surface (Related, below)
- **total** — see the generated property surface (Related, below)
- **values** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/table.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object table <object>
powerbi-report-author formatting search table <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "table",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### Field ORDER inside `Values` is the column order

There is no separate ordering property — the projection order in `query.queryState.Values.projections[]` is what renders. Reordering columns means reordering that array.

### `tableEx` is the modern key

32 instances of `tableEx` vs `table` in the R0101 corpus. New work goes to [tableEx.md](./tableEx.md).

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-table](../GUIDE.md#cli-table)
- JSON format: [../GUIDE.md#json-table](../GUIDE.md#json-table)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/table.md`
<!-- /MANUAL -->
