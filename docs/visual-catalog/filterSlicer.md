# filterSlicer

**Type key:** `filterSlicer` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`filterSlicer` is the modern unified slicer control and the richest of the slicer family. One `Values` role, but a `data.mode` enum that spans the whole spectrum — `VerticalList`, `HorizontalList`, `Dropdown`, `Basic`, `Single`, `Between`, `Before`, `After`, `Relative`, `RelativeTime`, `RelativeDatePicker` — with dedicated formatting objects for each shape: `calendarButton`, `date`, `dateRange`, `dateRangeText`, `slider`, `numericInputStyle`, `relativeText`, `searchBox`, `items`, `selection`, `selectionIcon`, `pendingChangesIcon`, `header`.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use for date and numeric-range filtering — it is the only slicer with a first-class calendar/slider surface (`data.startDate` / `data.endDate`, `dateRange.anchorDate`, `dateRange.includeToday`). For categorical filtering `advancedSlicerVisual` (search/hierarchy) or `listSlicer` remain the defaults; use the classic `slicer` only for legacy parity.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Values` | yes | yes | Column, Hierarchy |

## Containers

- **calendarButton** — see the generated property surface (Related, below)
- **data** — see the generated property surface (Related, below)
- **date** — see the generated property surface (Related, below)
- **dateRange** — see the generated property surface (Related, below)
- **dateRangeText** — see the generated property surface (Related, below)
- **general** — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **header** — see the generated property surface (Related, below)
- **items** — see the generated property surface (Related, below)
- **numericInputStyle** — see the generated property surface (Related, below)
- **pendingChangesIcon** — see the generated property surface (Related, below)
- **relativeText** — see the generated property surface (Related, below)
- **searchBox** — see the generated property surface (Related, below)
- **selection** — see the generated property surface (Related, below)
- **selectionIcon** — see the generated property surface (Related, below)
- **slider** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/filterSlicer.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object filterSlicer <object>
powerbi-report-author formatting search filterSlicer <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "filterSlicer",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### `mode` selects which formatting objects matter

`data.mode` is the master switch. Setting `slider` properties on a `Dropdown`-mode slicer is a silent no-op. Set `mode` FIRST, then format.

### It is the prime suspect for the 2026 date-picker slicer

`calendarButton` + `date.hideDatePickerButton` + `dateRange` + `RelativeDatePicker` mode are already here. Check this type first before assuming a new visualType — see [datePickerSlicer.md](./datePickerSlicer.md).

### `data.isInvertedSelectionMode` is the page's filter layer

As with every slicer, inverted selection and any Advanced filter on `objects.general[].properties.filter` carry real filtering semantics. Dump them before editing or deleting the visual.

### `pendingChangesIcon` implies deferred application

When the slicer is in apply-on-demand mode the visual state and the page filter state diverge until the user applies — slicer state read from PBIR can be stale (the usual `pbip_warning`).

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-filterslicer](../GUIDE.md#cli-filterslicer)
- JSON format: [../GUIDE.md#json-filterslicer](../GUIDE.md#json-filterslicer)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/filterSlicer.md`
<!-- /MANUAL -->
