# filledMap

## Overview

<!-- MANUAL: overview -->
`filledMap` is the Bing Maps choropleth visual. A `Location` column containing country, region, state, or postal-code values is geocoded by Bing and the matching geographic shapes are filled with colour derived from the `Values` measure or the `Legend` category. Unlike `map`, which renders bubbles at point locations, `filledMap` colours entire geographic polygons — countries, provinces, postcode areas — making it ideal for spatial distribution of aggregated metrics. The `stroke` container controls the border between adjacent shapes; `dataPoint.fillRule` drives gradient colour scales.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `filledMap` when:

- The data is **inherently regional** and area fill communicates density better than bubbles (asset distribution by country, AUM by domicile, tax residency breakdown).
- You want a **gradient colour scale** across regions (lightest = lowest, darkest = highest).
- The geographic level is country, state/province, or postal code — levels Bing can reliably resolve.

Prefer `map` (bubble) when individual point locations matter more than area fill, or when a `Size` measure encodes the primary metric. Prefer `azureMap` when you need the filled polygon layer alongside other Azure Maps layers (heat, 3D bar, reference). Prefer `shapeMap` when the geography is non-standard (custom territories, sales regions, internal zones) and you have a TopoJSON file — `shapeMap` does not depend on Bing at all.

Avoid `filledMap` for data at city level or below: Bing's polygon coverage is inconsistent for sub-state geographies and many cities resolve to single points, not area polygons.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts | Notes |
|------|----------|----------|---------|-------|
| `Location` | yes | yes | Column | Geocoded via Bing to country/region/state polygons. Data category must be set (Country/Region, State or Province, Postal Code). |
| `Legend` | no | no | Column | Splits regions into discrete colour series instead of a gradient scale. |
| `Values` | no | no | Measure | Drives the gradient colour fill across regions. Without Values, all regions render in the `defaultColor`. |

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
- **categoryLabels** (1 properties) — documented inline below
- **dataPoint** (5 properties) — documented inline below
- **labels** (4 properties) — documented inline below
- **legend** (10 properties) — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **mapControls** (9 properties) — documented inline below
- **mapStyles** (2 properties) — documented inline below
- **stroke** (3 properties) — documented inline below

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

### categoryLabels

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `show` | `boolean` | — | — | no | Show |

### dataPoint

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `defaultColor` | `object` | — | — | no | Default fill color for data points when no series coloring is applied. |
| `fill` | `object` | — | — | no | Fill color for data points. Supports conditional formatting via a measure returning theme color tokens ('good', 'bad', 'neutral') for per-bar/per-slice coloring. |
| `fillRule` | `object` | — | — | no | Gradient fill rule for data points (used with conditional formatting gradient scales). |
| `showAllDataPoints` | `boolean` | — | — | no | Show all |
| `transparency` | `number` | — | — | no | Transparency |

### labels

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no | Font color for data labels. |
| `labelDisplayUnits` | `integer` | — | — | no | Display units for data label values (Auto, Thousands, Millions, Billions). |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `show` | `boolean` | — | — | no | Show |

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

### stroke

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `show` | `boolean` | — | — | no | Show |
| `strokeColor` | `object` | — | — | no |  |
| `strokeWidth` | `number` | — | — | no | Width |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "filledMap",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->

### Data category is mandatory for choropleth to resolve correctly

Bing geocoding for `filledMap` depends on the **Data Category** set in the semantic model (Country/Region, State or Province, Postal Code). Without it, Bing treats values as free text and may resolve them to points rather than polygons, producing an empty map. Set categories in Tabular Editor or Power BI Desktop model view, not in the visual field well.

### Unmapped regions render blank — they do not show a zero

Regions that exist in the data but cannot be geocoded (e.g., misspelled country names, unofficial territory names) are silently dropped — they do not render even in the `defaultColor`. Check the visual's "Data with no match" tooltip indicator to audit geocoding failures. Use a consistent ISO 3166-1 alpha-2 or alpha-3 country code column for reliable results.

### `Legend` and `Values` are mutually exclusive

Binding both `Legend` (categorical colour) and `Values` (gradient fill) simultaneously is not meaningful — Power BI will honour one and ignore the other. Use `Legend` for categorical series membership; use `Values` for a continuous gradient scale. Do not bind both.

### Bing Maps sends location data to external servers

Same as `map`: `filledMap` sends `Location` column values to Bing servers. Subject to the same data-residency caveats. Use `shapeMap` or `azureMap` when external transmission is not permitted.

### Postal-code resolution is region-dependent

Postal code polygon coverage varies by country — strong in US/UK/Germany, weak or absent in many emerging markets. Test geocoding resolution before designing a postal-code choropleth for unfamiliar territories.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-filledmap](../GUIDE.md#cli-filledmap)
- JSON format: [../GUIDE.md#json-filledmap](../GUIDE.md#json-filledmap)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- docs/visual-catalog/map.md — point/bubble variant with Size encoding and Latitude/Longitude roles.
- docs/visual-catalog/azureMap.md — filledMap layer available inside Azure Maps alongside other layers.
- docs/visual-catalog/shapeMap.md — custom TopoJSON choropleth with no Bing dependency.
- docs/design-principles/chart-selection.md — when choropleth adds signal vs. introducing misleading area-bias.
<!-- /MANUAL -->
