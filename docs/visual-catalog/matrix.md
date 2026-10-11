# matrix

**Type key:** `matrix` · **Status:** legacy / deprecated · **Modern replacement:** `pivotTable`

> **DEPRECATED / LEGACY visual type.** Prefer [`pivotTable`](./pivotTable.md) for new work.
> Documented because existing reports still use it — edit in place, build new visuals with the modern equivalent.

## Overview

<!-- MANUAL: overview -->
`matrix` is the legacy cross-tab visual: fields on `Rows` and `Columns` form a grid, `Values` measures fill the cells, with row/column subtotals and a grand total. It is superseded by `pivotTable`, which is the same conceptual visual with a modern formatting surface (style presets, per-level formatting, richer conditional formatting).
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Edit `matrix` visuals **in place** when they already exist in a report — there is no in-place upgrade path and swapping the type loses formatting. Build every NEW cross-tab as `pivotTable`.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Rows` | no | yes | Column, Hierarchy |
| `Columns` | no | yes | Column, Hierarchy |
| `Values` | yes | yes | Measure |

## Containers

- **columnFormatting** — see the generated property surface (Related, below)
- **columnHeaders** — see the generated property surface (Related, below)
- **general** — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **grid** — see the generated property surface (Related, below)
- **rowHeaders** — see the generated property surface (Related, below)
- **subTotals** — see the generated property surface (Related, below)
- **total** — see the generated property surface (Related, below)
- **values** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/matrix.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object matrix <object>
powerbi-report-author formatting search matrix <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "matrix",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### `pivotTable` is stored as `pivotTable`, not `matrix`

Both exist as distinct `visualType` keys. Do not assume a "matrix" in the Desktop UI is `visualType: "matrix"` on disk — modern reports write `pivotTable`. Check `visual.json` before targeting formatting objects, because the object names differ.

### Column widths are not in this object set

Matrix/table column widths live outside the formatting objects (see `tableEx.md` and the `sync_column_widths` op). The CLI metadata dump does not enumerate them.

### `subTotals` and `total` are separate objects

Row/column subtotals (`subTotals`) and the grand total (`total`) are formatted independently — turning off one does not affect the other.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-matrix](../GUIDE.md#cli-matrix)
- JSON format: [../GUIDE.md#json-matrix](../GUIDE.md#json-matrix)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/matrix.md`
<!-- /MANUAL -->
