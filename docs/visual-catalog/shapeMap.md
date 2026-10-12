# shapeMap

## Overview

<!-- MANUAL: overview -->
`shapeMap` is a custom-geography choropleth visual that renders geographic shapes from a **TopoJSON file** uploaded to the report's resources. Unlike `map` and `filledMap`, it does not call Bing or any external geocoding service — all resolution is local. The `Location` column values must match the `id` or `name` properties in the TopoJSON topology. The `ColorSaturation` measure drives a gradient colour fill across matching shapes. The `shape.projectionEnum` controls map projection (Albers, Mercator, etc.) and the `zoom` container lets users pan/zoom or auto-fit.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `shapeMap` when:

- The geography is **non-standard**: custom sales territories, internal zones, postal districts not covered by Bing, floor plans, or any region that doesn't map to a standard country/state polygon.
- **Data residency** prohibits sending location values to external geocoding services (Bing or Azure Maps).
- You have a pre-built **TopoJSON file** (`.json`) that defines the shapes exactly as you need them.
- A **single gradient measure** (ColorSaturation) is sufficient — `shapeMap` does not support a `Legend` series role for multi-colour categorical fills in the way `filledMap` does.

Prefer `filledMap` or `azureMap` when standard country/state geography is fine and you don't have a custom TopoJSON — those visuals geocode automatically. Prefer `azureMap` when you need interactive layers (zoom, pitch, traffic) alongside the choropleth fill.

`shapeMap` is not widely used in R0101 (which uses standard country-level geography), but is the correct choice for private-equity fund structure maps, internal property floor plans, or bespoke regional classifications.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts | Notes |
|------|----------|----------|---------|-------|
| `Location` | yes | no | Column | Values must match the `id` or `name` fields in the uploaded TopoJSON. No external geocoding — the match is purely string-based. |
| `ColorSaturation` | no | no | Measure | Drives the gradient fill across shapes. Without it, all shapes render in `defaultColors.defaultColor`. |

## Containers

- **background** (3 properties) — see [./_shared/containers-background.md](./_shared/containers-background.md)
- **border** (4 properties) — see [./_shared/containers-border.md](./_shared/containers-border.md)
- **divider** (5 properties) — documented inline below
- **dropShadow** (9 properties) — documented inline below
- **general** (1 properties) — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **lockAspect** (1 properties) — documented inline below
- **padding** (4 properties) — see [./_shared/containers-padding.md](./_shared/containers-padding.md)
- **spacing** (5 properties) — documented inline below
- **stylePreset** (1 properties) — documented inline below
- **subTitle** (11 properties) — documented inline below
- **title** (12 properties) — see [./_shared/containers-title.md](./_shared/containers-title.md)
- **visualHeader** (25 properties) — see [./_shared/containers-visualHeader.md](./_shared/containers-visualHeader.md)
- **visualHeaderTooltip** (13 properties) — documented inline below
- **visualLink** (13 properties) — documented inline below
- **visualTooltip** (17 properties) — documented inline below
- **dataPoint** (4 properties) — documented inline below
- **defaultColors** (4 properties) — documented inline below
- **legend** (10 properties) — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **shape** (1 properties) — documented inline below
- **zoom** (3 properties) — documented inline below

## Properties

### background

See [./_shared/containers-background.md](./_shared/containers-background.md).

### border

See [./_shared/containers-border.md](./_shared/containers-border.md).

### divider

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no | Color of the divider line between header and content areas. |
| `ignorePadding` | `boolean` | — | — | no | Ignore padding |
| `show` | `boolean` | — | — | no | Show |
| `style` | `string` | — | — | no | Line style |
| `width` | `number` | — | — | no | Width |

### dropShadow

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `angle` | `number` | — | — | no | Angle |
| `color` | `object` | — | — | no | Color of the drop shadow. Note: drop shadows create accessibility issues for users with vestibular disabilities -- prefer flat layout with background contrast. |
| `position` | `string` | — | — | no | Shadow position |
| `preset` | `string` | — | — | no | Preset |
| `shadowBlur` | `number` | — | — | no | Blur |
| `shadowDistance` | `number` | — | — | no | Distance |
| `shadowSpread` | `number` | — | — | no | Size |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### general

See [./_shared/containers-general.md](./_shared/containers-general.md).

### lockAspect

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `show` | `boolean` | — | — | no | Show |

### padding

See [./_shared/containers-padding.md](./_shared/containers-padding.md).

### spacing

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `customizeSpacing` | `boolean` | — | — | no | Customize spacing |
| `spaceBelowSubTitle` | `number` | — | — | no | Space below subtitle |
| `spaceBelowTitle` | `number` | — | — | no | Space below title |
| `spaceBelowTitleArea` | `number` | — | — | no | Space below title area |
| `verticalSpacing` | `number` | — | — | no | Space between label and value |

### stylePreset

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `name` | `string` | — | — | no | Style |

### subTitle

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `alignment` | `string` | — | — | no | Horizontal alignment of the subtitle text. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no | Font color for the subtitle. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for the subtitle text (6-45pt). |
| `heading` | `string` | — | — | no | Set the heading tag for your visual titles to help with screen reader accessibility. This won't impact the visual styling of your titles. |
| `italic` | `boolean` | — | — | no | Italic |
| `show` | `boolean` | — | — | no | Show |
| `text` | `string` | — | — | no | The name of the visual |
| `titleWrap` | `boolean` | — | — | no | Word wrap |
| `underline` | `boolean` | — | — | no | Underline |

### title

See [./_shared/containers-title.md](./_shared/containers-title.md).

### visualHeader

See [./_shared/containers-visualHeader.md](./_shared/containers-visualHeader.md).

### visualHeaderTooltip

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `background` | `object` | — | — | no | Background color for the visual header tooltip popup. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontFamily` | `string` | — | — | no | Font family for the tooltip |
| `fontSize` | `number` | — | — | no | Font size for visual header tooltip text. |
| `italic` | `boolean` | — | — | no | Italic |
| `section` | `string` | — | — | no | Choose the page you want to use for your tooltip |
| `text` | `string` | — | — | no | Write the custom tooltip that will show up when a report page tooltip hasn't been chosen or isn't supported |
| `themedBackground` | `object` | — | — | no | Theme-aware background color for header tooltips. |
| `themedTitleFontColor` | `object` | — | — | no | Theme-aware font color for header tooltip title. |
| `titleFontColor` | `object` | — | — | no | Font color for header tooltip title text. |
| `transparency` | `number` | — | — | no | Set transparency for the tooltip background |
| `type` | `string` | — | — | no | Type |
| `underline` | `boolean` | — | — | no | Underline |

### visualLink

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `bookmark` | `string` | — | — | no | Choose which bookmark will open when this element is selected. |
| `dataFunction` | `object` | — | — | no |  |
| `disabledTooltip` | `string` | — | — | no | Disabled tooltip |
| `drillthroughSection` | `string` | — | — | no | Select a single destination or use conditional formatting for custom destination logic, such as multi-destination support. |
| `enabledTooltip` | `string` | — | — | no | Enabled tooltip |
| `navigationSection` | `string` | — | — | no | Select a single destination or use conditional formatting for custom destination logic, such as multi-destination support. |
| `show` | `boolean` | — | — | no | Show |
| `showDefaultTooltip` | `boolean` | — | — | no | Show tooltips |
| `suppressDefaultTooltip` | `boolean` | — | — | no | No tooltips |
| `tooltip` | `string` | — | — | no | Write the custom tooltip that will show when users hover over this element. |
| `tooltipPlaceholderText` | `string` | — | — | no | Placeholder for tooltip |
| `type` | `string` | — | — | no | Choose what action will occur when this element is selected. |
| `webUrl` | `string` | — | — | no | Web URL |

### visualTooltip

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `actionFontColor` | `object` | — | — | no | Font color for tooltip action text (e.g., drill-through links). |
| `background` | `object` | — | — | no | Background color for the tooltip popup. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontFamily` | `string` | — | — | no | Font family for the tooltip |
| `fontSize` | `number` | — | — | no | Font size for tooltip text. |
| `italic` | `boolean` | — | — | no | Italic |
| `section` | `string` | — | — | no | Choose the page you want to use for your tooltip |
| `show` | `boolean` | — | — | no | Show |
| `showActionsInTooltips` | `boolean` | — | — | no | Add drill options within tooltips |
| `themedBackground` | `object` | — | — | no | Theme-aware background color for tooltips. |
| `themedTitleFontColor` | `object` | — | — | no | Theme-aware font color for tooltip title. |
| `themedValueFontColor` | `object` | — | — | no | Theme-aware font color for tooltip values. |
| `titleFontColor` | `object` | — | — | no | Font color for tooltip title text. |
| `transparency` | `number` | — | — | no | Set transparency for the tooltip background |
| `type` | `string` | — | — | no | Allows report pages to be used as tooltips for this visual |
| `underline` | `boolean` | — | — | no | Underline |
| `valueFontColor` | `object` | — | — | no | Font color for tooltip value text. |

### dataPoint

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `defaultColor` | `object` | — | — | no | Default fill color for data points when no series coloring is applied. |
| `fill` | `object` | — | — | no | Fill color for data points. Supports conditional formatting via a measure returning theme color tokens ('good', 'bad', 'neutral') for per-bar/per-slice coloring. |
| `fillRule` | `object` | — | — | no | Gradient fill rule for data points (used with conditional formatting gradient scales). |
| `showAllDataPoints` | `boolean` | — | — | no | Show all |

### defaultColors

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `borderColor` | `object` | — | — | no |  |
| `borderThickness` | `number` | — | — | no | Border thickness |
| `defaultColor` | `object` | — | — | no |  |
| `defaultShow` | `boolean` | — | — | no | Show shapes with no data |

### legend

See [./_shared/containers-legend.md](./_shared/containers-legend.md).

### shape

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `projectionEnum` | `string` | — | — | no | Projection |

### zoom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `autoZoom` | `boolean` | — | — | no | Zoom in on shapes with available data |
| `manualZoom` | `boolean` | — | — | no | Allow user to zoom and pan |
| `selectionZoom` | `boolean` | — | — | no | Zoom in on selected shapes |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "shapeMap",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->

### Location values must exactly match TopoJSON feature IDs — case-sensitive

`shapeMap` resolves `Location` column values against the `id` (or `name`) property of each TopoJSON feature. The match is **case-sensitive and exact** — `"Germany"` does not match `"germany"` or `"DE"`. If your data uses ISO codes and the TopoJSON uses full names (or vice versa), add a mapping column in Power Query before binding. Unmatched shapes render in `defaultColors.defaultColor`; they do not error.

### TopoJSON file must be uploaded to the report's Static Resources

The map file is stored in `StaticResources/RegisteredResources/` inside the `.Report` folder (same location as theme JSON). Uploading via Power BI Desktop's Insert > Shape Map > Format pane > Map settings handles this automatically. When deploying via PBIR/Git, ensure the TopoJSON file is committed alongside the report — it is not a semantic model asset.

### TopoJSON format only — GeoJSON is not accepted

`shapeMap` requires **TopoJSON** (`.json` with a `type: "Topology"` root), not standard GeoJSON (`.geojson`). Convert GeoJSON to TopoJSON using the `topojson-server` CLI or geo.tools before uploading.

### Only a single gradient measure — no categorical multi-colour series

`shapeMap` has one data role (`ColorSaturation`) and no `Legend` role. You cannot colour shapes categorically (e.g., red/green/blue by region tier) using the built-in role system. Workaround: use a DAX measure that returns a colour string and apply it via `dataPoint.fill` conditional formatting — but this requires manual JSON editing.

### `zoom.autoZoom` and `zoom.selectionZoom` interact

With `autoZoom = true`, the map re-fits whenever slicer context changes the visible shapes. With `selectionZoom = true`, clicking a shape zooms to it. Running both simultaneously can cause jarring double-zoom transitions in dashboards with active slicers — set only one.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-shapemap](../GUIDE.md#cli-shapemap)
- JSON format: [../GUIDE.md#json-shapemap](../GUIDE.md#json-shapemap)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- docs/visual-catalog/filledMap.md — standard Bing choropleth when custom TopoJSON is not needed.
- docs/visual-catalog/azureMap.md — Azure Maps filled-polygon layer for standard geographies with richer interactivity.
- docs/visual-catalog/map.md — point/bubble map for location-level data.
- docs/design-principles/chart-selection.md — map selection guidance.
<!-- /MANUAL -->
