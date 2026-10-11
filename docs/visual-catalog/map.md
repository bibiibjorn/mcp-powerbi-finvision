# map

## Overview

<!-- MANUAL: overview -->
`map` is the Bing Maps bubble/point visual. One or more columns go into the `Location` role and Power BI geocodes them using Bing (country, region, city, address, postal code). `Latitude` and `Longitude` roles accept numeric columns for precise coordinate-based placement, bypassing geocoding entirely. `Size` scales bubble area proportionally to a measure; `Legend` splits bubbles into colour-coded series. The `heatMap` container activates a density overlay independent of the point layer.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `map` when:

- You need to show **point locations** on a geographic canvas (cities, branches, transactions, portfolios by domicile).
- Bubble **size encodes a measure** (AUM, count, exposure) — the visual comparison is immediate.
- Exact **latitude/longitude** coordinates are available and geocoding ambiguity is unacceptable.
- A **heat-map density overlay** is needed alongside individual data points.

Prefer `filledMap` when the data is regional/country-level and area fill (choropleth) is more readable than bubbles. Prefer `azureMap` when advanced layers (3D bar, heat, traffic, reference polygons) or Azure Maps tile sets are required. Prefer `shapeMap` when the geography is non-standard (custom territories, internal floor plans) and you have a TopoJSON file.

Note: `map` and `filledMap` require Bing Maps geocoding and send location data to Bing servers. If data sovereignty or privacy rules restrict external calls, use `azureMap` with your own Azure Maps subscription or use `shapeMap` with a pre-built TopoJSON.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts | Notes |
|------|----------|----------|---------|-------|
| `Location` | yes | yes | Column | Geocoded via Bing; use country/region/city/postal code columns. Multiple columns stack to disambiguate (e.g. City + Country). |
| `Latitude` | no | no | Column | Numeric decimal degrees. Overrides geocoding for the location's latitude. Pair with `Longitude`. |
| `Longitude` | no | no | Column | Numeric decimal degrees. Overrides geocoding for the location's longitude. |
| `Legend` | no | no | Column | Splits bubbles into colour-coded series (uses `dataColors`). |
| `Size` | no | no | Measure | Scales bubble area proportionally. Without Size, all bubbles render at uniform size. |
| `Color` | no | no | Measure | Not listed in stub but accepted by `map` engine — colours bubbles by measure value via gradient or rules. |

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
- **bubbles** (2 properties) — documented inline below
- **categoryLabels** (10 properties) — documented inline below
- **dataPoint** (5 properties) — documented inline below
- **heatMap** (7 properties) — documented inline below
- **legend** (10 properties) — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **mapControls** (9 properties) — documented inline below
- **mapStyles** (2 properties) — documented inline below

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

### bubbles

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `bubbleSize` | `integer` | — | — | no | Size |
| `markerRangeType` | `string` | — | — | no | Range scaling |

### categoryLabels

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backgroundColor` | `object` | — | — | no |  |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `bold` | `boolean` | — | — | no | Bold |
| `color` | `object` | — | — | no |  |
| `enableBackground` | `boolean` | — | — | no | Show background |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `show` | `boolean` | — | — | no | Show |
| `underline` | `boolean` | — | — | no | Underline |

### dataPoint

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `defaultColor` | `object` | — | — | no | Default fill color for data points when no series coloring is applied. |
| `fill` | `object` | — | — | no | Fill color for data points. Supports conditional formatting via a measure returning theme color tokens ('good', 'bad', 'neutral') for per-bar/per-slice coloring. |
| `fillRule` | `object` | — | — | no | Gradient fill rule for data points (used with conditional formatting gradient scales). |
| `showAllDataPoints` | `boolean` | — | — | no | Show all |
| `transparency` | `number` | — | — | no | Transparency |

### heatMap

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color0` | `object` | — | — | no |  |
| `color100` | `object` | — | — | no |  |
| `color50` | `object` | — | — | no |  |
| `filterRadius` | `number` | — | — | no | Radius |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Background color transparency |
| `unit` | `string` | — | — | no | Units |

### legend

See [./_shared/containers-legend.md](./_shared/containers-legend.md).

### mapControls

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `autoZoom` | `boolean` | — | — | no | Auto zoom |
| `centerLatitude` | `number` | — | — | no |  |
| `centerLongitude` | `number` | — | — | no |  |
| `geocodingCulture` | `string` | — | — | no | Geocoding culture |
| `maxZoom` | `number` | — | — | no | Max zoom level |
| `minZoom` | `number` | — | — | no | Min zoom level |
| `showLassoButton` | `boolean` | — | — | no | Lasso button |
| `showZoomButtons` | `boolean` | — | — | no | Zoom buttons |
| `zoomLevel` | `number` | — | — | no |  |

### mapStyles

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `mapTheme` | `string` | — | — | no | Theme |
| `showLabels` | `boolean` | — | — | no | Show labels |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "map",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->

### Data category must be set on location columns for geocoding to work

Power BI geocodes `Location` columns using their **Data Category** metadata set in the semantic model (City, Country/Region, State or Province, Postal Code, Continent, Place, Latitude, Longitude, Address). Without the correct category, Bing receives raw text and returns inaccurate or zero results. Set data categories in Tabular Editor or Power BI Desktop's column properties — not in the visual's field well. If geocoding quality is poor, switch to `Latitude`/`Longitude` roles using numeric coordinate columns.

### Bing Maps geocoding sends location data externally

Both `map` and `filledMap` transmit the values in the `Location` role to Bing servers for geocoding. This applies inside Power BI Service and Desktop. If data-residency or privacy policies prohibit external calls, use `azureMap` (uses your own Azure Maps key and processes within your tenant) or `shapeMap` (entirely local TopoJSON with no external calls).

### Latitude/Longitude precision — decimal degrees only

The `Latitude` and `Longitude` roles require **decimal-degree numeric** columns (e.g., `48.8566`). DMS format (degrees°minutes′seconds″) does not resolve — convert in Power Query before binding. Precision beyond 5 decimal places is ignored at typical map zoom levels.

### Size role scales area, not radius

Bubble size is proportional to the **measure value's area** (i.e., scaled to value, not square-root-of-value). A point with value 4× another renders with 4× the area (~2× the visual radius). Use `bubbles.markerRangeType` to switch between linear and logarithmic scaling if the data has large outliers that collapse small values visually.

### Heat map and bubble layers are independent overlays

Setting `heatMap.show = true` adds a density layer on top of the point layer; it does not replace it. The heat map renders even when a `Size` measure is bound. If you only want a heat map, hide the point layer via `dataPoint.transparency = 100`.

### Multiple Location columns are concatenated, not hierarchical

Stacking two columns (e.g., City + Country) in the `Location` role causes Power BI to geocode the concatenated label, not the hierarchy. This improves disambiguation (Paris, France vs Paris, Texas) but does not create a drill-down hierarchy — for drill, use `azureMap` which handles the role differently.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-map](../GUIDE.md#cli-map)
- JSON format: [../GUIDE.md#json-map](../GUIDE.md#json-map)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- docs/visual-catalog/filledMap.md — choropleth variant using the same Bing geocoding engine.
- docs/visual-catalog/azureMap.md — Azure Maps replacement with richer layers and no Bing dependency.
- docs/visual-catalog/shapeMap.md — custom TopoJSON maps for non-standard geographies.
- docs/design-principles/chart-selection.md — when a map adds value vs. a simple ranked bar chart.
<!-- /MANUAL -->
