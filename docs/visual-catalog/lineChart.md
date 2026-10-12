# lineChart

## Overview

<!-- MANUAL: overview -->
`lineChart` renders one or more measures as lines over a continuous or ordered Category axis — typically a date axis. Multiple measures in the `Y` role become multiple lines; an optional `Legend` role splits a single measure into one line per legend value. The natural fit is trend over time where the direction and inflection matter more than exact values at each point.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `lineChart` when:

- **Trend over time** is the main signal (NAV trajectory, AUM curve, performance index).
- You compare **2–4 series** side-by-side (Actual vs Simulation 1 vs Simulation 2).
- The X axis has a **dense, ordered** domain — too dense for columns, too continuous for bars.

Prefer `columnChart` when the X axis is **few discrete buckets** (3–8 scenarios). Prefer `lineClusteredColumnComboChart` when mixing stock-and-flow (line = NAV, columns = cash flow). Prefer `stackedAreaChart` or `hundredPercentStackedAreaChart` when the sum-to-total composition matters.

Avoid >4 lines on one chart — readers cannot track more. Split into small multiples (`SmallMultiples` role) or use a Deneb sparkline strip.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Category` | yes | no | Column |
| `Legend` | no | no | Column |
| `SmallMultiples` | no | no | Column |
| `Y` | yes | yes | Measure |

## Containers

- **background** (3 properties) — see [./_shared/containers-background.md](./_shared/containers-background.md)
- **border** (4 properties) — see [./_shared/containers-border.md](./_shared/containers-border.md)
- **divider** (5 properties) — documented inline below
- **dropShadow** (9 properties) — documented inline below
- **general** (3 properties) — see [./_shared/containers-general.md](./_shared/containers-general.md)
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
- **annotationTemplate** (1 properties) — documented inline below
- **anomalyDetection** (25 properties) — documented inline below
- **categoryAxis** (35 properties) — see [./_shared/containers-categoryAxis.md](./_shared/containers-categoryAxis.md)
- **dataPoint** (4 properties) — documented inline below
- **error** (30 properties) — documented inline below
- **filters** (1 properties) — documented inline below
- **forecast** (27 properties) — documented inline below
- **labels** (68 properties) — documented inline below
- **layout** (0 properties) — documented inline below
- **legend** (12 properties) — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **lineStyles** (24 properties) — documented inline below
- **markers** (7 properties) — documented inline below
- **plotArea** (2 properties) — see [./_shared/containers-plotArea.md](./_shared/containers-plotArea.md)
- **referenceLine** (23 properties) — documented inline below
- **scalarKey** (1 properties) — documented inline below
- **seriesLabels** (27 properties) — documented inline below
- **smallMultiplesLayout** (17 properties) — documented inline below
- **subheader** (10 properties) — documented inline below
- **trend** (11 properties) — documented inline below
- **valueAxis** (34 properties) — see [./_shared/containers-valueAxis.md](./_shared/containers-valueAxis.md)
- **xAxisReferenceLine** (23 properties) — documented inline below
- **y1AxisReferenceLine** (23 properties) — documented inline below
- **y2Axis** (22 properties) — documented inline below
- **zoom** (15 properties) — documented inline below

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

### annotationTemplate

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `callout` | `object` | — | — | no |  |

### anomalyDetection

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `BatchEnd` | `string` | — | — | no |  |
| `BatchStart` | `string` | — | — | no |  |
| `CategoryValue` | `string` | — | — | no |  |
| `ExpectedHigh` | `number` | — | — | no |  |
| `ExpectedLow` | `number` | — | — | no |  |
| `ExpectedValue` | `number` | — | — | no |  |
| `Value` | `number` | — | — | no |  |
| `confidenceBandColor` | `object` | — | — | no |  |
| `confidenceBandShow` | `boolean` | — | — | no | Show |
| `confidenceBandStyle` | `string` | — | — | no | Choose the style for the range of normal values. Any value outside of the range will be considered an anomaly. |
| `displayName` | `string` | — | — | no | Set anomaly name |
| `isAnomalyHighlighted` | `boolean` | — | — | no |  |
| `markerBorderColor` | `object` | — | — | no |  |
| `markerBorderColorMatchFill` | `boolean` | — | — | no | Match the legend icon color to the color of the line, not the marker |
| `markerBorderShow` | `boolean` | — | — | no | Show |
| `markerBorderTransparency` | `number` | — | — | no | Transparency |
| `markerBorderWidth` | `number` | — | — | no | Width |
| `markerColor` | `object` | — | — | no |  |
| `markerRotation` | `number` | — | — | no | Rotation |
| `markerShape` | `string` | — | — | no | Choose the shape used to point out the anomalies on the visual. |
| `markerShapeSize` | `number` | — | — | no | Size |
| `markerShow` | `boolean` | — | — | no | Show |
| `markerTransparency` | `number` | — | — | no | Transparency |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### categoryAxis

See [./_shared/containers-categoryAxis.md](./_shared/containers-categoryAxis.md).

### dataPoint

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `defaultColor` | `object` | — | — | no | Default fill color for data points when no series coloring is applied. |
| `fill` | `object` | — | — | no | Fill color for data points. Supports conditional formatting via a measure returning theme color tokens ('good', 'bad', 'neutral') for per-bar/per-slice coloring. |
| `showAllDataPoints` | `boolean` | — | — | no | Show all |
| `transparency` | `number` | — | — | no | Area transparency |

### error

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `barBorderColor` | `object` | — | — | no | Border color around error bar markers. |
| `barBorderSize` | `integer` | — | — | no | Border size |
| `barColor` | `object` | — | — | no | Color of the error bar lines. When using error bars as target lines, style these to look like target markers (e.g., a contrasting color). |
| `barMatchSeriesColor` | `boolean` | — | — | no | Match series color |
| `barShow` | `boolean` | — | — | no | Show |
| `barWidth` | `number` | — | — | no | Width |
| `enabled` | `boolean` | — | — | no | Enabled |
| `labelBackground` | `boolean` | — | — | no | Show background |
| `labelBackgroundColor` | `object` | — | — | no | Background fill color behind error bar labels. |
| `labelBackgroundTransparency` | `number` | — | — | no | Background color transparency |
| `labelBold` | `boolean` | — | — | no | Bold |
| `labelColor` | `object` | — | — | no | Font color for error bar value labels. |
| `labelFontFamily` | `string` | — | — | no | Font family |
| `labelFontSize` | `number` | — | — | no | Font size for error bar value labels (6-45pt). |
| `labelFormat` | `string` | — | — | no | Label format |
| `labelItalic` | `boolean` | — | — | no | Italic |
| `labelMatchSeriesColor` | `boolean` | — | — | no | Match series color |
| `labelShow` | `boolean` | — | — | no | Show |
| `labelUnderline` | `boolean` | — | — | no | Underline |
| `markerShape` | `string` | — | — | no | Marker shape |
| `markerShow` | `boolean` | — | — | no | Show |
| `markerSize` | `number` | — | — | no | Size |
| `shadeBandStyle` | `string` | — | — | no | Style |
| `shadeColor` | `object` | — | — | no | Fill color for the shaded band between upper and lower error bounds. |
| `shadeMatchSeriesColor` | `boolean` | — | — | no | Match series color |
| `shadeShow` | `boolean` | — | — | no | Show |
| `shadeTransparency` | `number` | — | — | no | Background color transparency |
| `showMarkerByDefault` | `boolean` | — | — | no | Show error bar markers on data points by default without requiring hover. |
| `tooltipFormat` | `string` | — | — | no | Label format |
| `tooltipShow` | `boolean` | — | — | no | Show in tooltip |

### filters

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `filteredToUniqueValue` | `boolean` | — | — | no |  |

### forecast

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `autoScale` | `boolean` | — | — | no | Automatically adjust the spacing between dashes and dots based on line width. |
| `bandAreaColor` | `object` | — | — | no | Fill color for the forecast confidence band area. |
| `bandAreaMatchColor` | `boolean` | — | — | no | Match the legend icon color to the color of the line, not the marker |
| `bandAreaShow` | `boolean` | — | — | no | Show |
| `bandAreaTransparency` | `number` | — | — | no | Transparency |
| `bandLineAutoScale` | `boolean` | — | — | no | Automatically adjust the spacing between dashes and dots based on line width. |
| `bandLineColor` | `object` | — | — | no | Color of the forecast confidence band border lines. |
| `bandLineDashArray` | `string` | — | — | no | Dash array |
| `bandLineDashCap` | `string` | — | — | no | Dash cap |
| `bandLineMatchColor` | `boolean` | — | — | no | Match the legend icon color to the color of the line, not the marker |
| `bandLinePattern` | `string` | — | — | no | Line style |
| `bandLineShow` | `boolean` | — | — | no | Show |
| `bandLineTransparency` | `number` | — | — | no | Transparency |
| `bandLineWidth` | `number` | — | — | no | Width |
| `dashArray` | `string` | — | — | no | Dash array |
| `dashCap` | `string` | — | — | no | Dash cap |
| `displayName` | `string` | — | — | no | Set forecast name |
| `interpolation` | `string` | — | — | no | Set interpolation type between line data points. |
| `interpolationSmooth` | `string` | — | — | no | Smooth type |
| `interpolationSmoothParam` | `number` | — | — | no | Tension |
| `interpolationStep` | `string` | — | — | no | Step position |
| `lineColor` | `object` | — | — | no | Color of the forecast trend line. |
| `matchSeriesInterpolation` | `boolean` | — | — | no | Match series interpolation |
| `show` | `boolean` | — | — | no | Show |
| `strokeTransparency` | `number` | — | — | no | Transparency |
| `style` | `string` | — | — | no | Line style |
| `width` | `number` | — | — | no | Width |

### labels

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backgroundColor` | `object` | — | — | no | Background color behind data labels. |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `bold` | `boolean` | — | — | no | Bold |
| `color` | `object` | — | — | no | Font color for data labels. |
| `detailBold` | `boolean` | — | — | no | Bold |
| `detailColor` | `object` | — | — | no | Font color for detail line in data labels (when showing multiple values). |
| `detailContentType` | `string` | — | — | no | Content |
| `detailCustomFormatString` | `string` | — | — | no | Enter a custom number format for your callout. |
| `detailFontFamily` | `string` | — | — | no | Font family |
| `detailFontSize` | `number` | — | — | no | Font size for the detail line in data labels. |
| `detailFormatString` | `string` | — | — | no | Format string for detail values in data labels. |
| `detailItalic` | `boolean` | — | — | no | Italic |
| `detailLabelDisplayUnits` | `integer` | — | — | no | Select the units (millions, billions, etc.) |
| `detailLabelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `detailShowBlankAs` | `string` | — | — | no | Show blank as |
| `detailTransparency` | `number` | — | — | no | Transparency |
| `detailUnderline` | `boolean` | — | — | no | Underline |
| `dynamicLabelDetail` | `string | number | integer | boolean` | — | — | no | Field |
| `dynamicLabelTitle` | `string | number | integer | boolean` | — | — | no | Field |
| `dynamicLabelValue` | `string | number | integer | boolean` | — | — | no | Field |
| `enableBackground` | `boolean` | — | — | no | Show background |
| `enableDetailDataLabel` | `boolean` | — | — | no | Show the detail line in data labels. |
| `enableTitleDataLabel` | `boolean` | — | — | no | Show the title/category in data labels. |
| `enableValueDataLabel` | `boolean` | — | — | no | Show the value in data labels. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for data labels. |
| `horizontalAlignment` | `string` | — | — | no | Horizontal alignment of data labels relative to the data point. |
| `italic` | `boolean` | — | — | no | Italic |
| `labelContainerMaxWidth` | `number` | — | — | no | Maximum width |
| `labelContentLayout` | `string` | — | — | no | Layout |
| `labelDensity` | `integer` | — | — | no | Label density |
| `labelDisplayUnits` | `integer` | — | — | no | Select the units (millions, billions, etc.) |
| `labelPosition` | `string` | — | — | no | Position |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `leaderLineAutoScale` | `boolean` | — | — | no | Automatically adjust the spacing between dashes and dots based on line width. |
| `leaderLineColor` | `object` | — | — | no | Color of leader lines connecting data labels to pie/donut slices when labels are pulled away from the chart. |
| `leaderLineDashArray` | `string` | — | — | no | Dash array |
| `leaderLineDashCap` | `string` | — | — | no | Dash cap |
| `leaderLinePattern` | `string` | — | — | no | Line style |
| `leaderLineTransparency` | `number` | — | — | no | Transparency |
| `leaderLineWidth` | `number` | — | — | no | Width |
| `leaderLines` | `boolean` | — | — | no | Show |
| `maximumOffset` | `number` | — | — | no | Maximum offset |
| `minimumOffset` | `number` | — | — | no | Minimum offset |
| `optimizeLabelDisplay` | `boolean` | — | — | no | Optimize label display |
| `show` | `boolean` | — | — | no | Show |
| `showAll` | `boolean` | — | — | no | Customize series |
| `showBlankAs` | `string` | — | — | no | Show blank as |
| `showByDefault` | `boolean` | — | — | no | Show data labels by default without requiring hover. |
| `showDynamicLabels` | `boolean` | — | — | no | Custom label |
| `showSeries` | `boolean` | — | — | no | Show |
| `titleBold` | `boolean` | — | — | no | Bold |
| `titleColor` | `object` | — | — | no | Font color for the title/category portion of data labels. |
| `titleContentType` | `string` | — | — | no | Content |
| `titleCustomFormatString` | `string` | — | — | no | Enter a custom number format for your callout. |
| `titleFontFamily` | `string` | — | — | no | Font family |
| `titleFontSize` | `number` | — | — | no | Font size for the title/category in data labels. |
| `titleFormatString` | `string` | — | — | no | Format string for the title portion of data labels. |
| `titleItalic` | `boolean` | — | — | no | Italic |
| `titleLabelDisplayUnits` | `integer` | — | — | no | Select the units (millions, billions, etc.) |
| `titleLabelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `titleShowBlankAs` | `string` | — | — | no | Show blank as |
| `titleTransparency` | `number` | — | — | no | Transparency |
| `titleUnderline` | `boolean` | — | — | no | Underline |
| `transparency` | `number` | — | — | no | Transparency |
| `underline` | `boolean` | — | — | no | Underline |
| `valueCustomFormatString` | `string` | — | — | no | Enter a custom number format for your callout. |
| `valueFormatString` | `string` | — | — | no | Format string for the value portion of data labels. |

### layout

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|

### legend

See [./_shared/containers-legend.md](./_shared/containers-legend.md).

### lineStyles

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `areaColor` | `object` | — | — | no | Fill color for the area below lines in area charts. |
| `areaMatchStrokeColor` | `boolean` | — | — | no | Match the legend icon color to the color of the line, not the marker |
| `areaShow` | `boolean` | — | — | no | Shade area |
| `interpolationSmooth` | `string` | — | — | no | Smooth type |
| `interpolationSmoothParam` | `number` | — | — | no | Tension |
| `interpolationStep` | `string` | — | — | no | Step position |
| `lineChartType` | `string` | — | — | no | Set interpolation type between line data points. |
| `lineStyle` | `string` | — | — | no | Line style |
| `markerColor` | `object` | — | — | no | Color of data point markers on line charts. |
| `markerShape` | `string` | — | — | no | Type |
| `markerSize` | `number` | — | — | no | Size |
| `segmentAlignment` | `string` | — | — | no | Segment type |
| `segmentGradient` | `boolean` | — | — | no | Gradient |
| `showMarker` | `boolean` | — | — | no | Show |
| `showMarkerByDefault` | `boolean` | — | — | no | Show data point markers on line charts by default. Useful for sparse data where individual points matter. |
| `showSeries` | `boolean` | — | — | no | Customize series |
| `strokeAutoScale` | `boolean` | — | — | no | Automatically adjust the spacing between dashes and dots based on line width. |
| `strokeColor` | `object` | — | — | no | Color of the line stroke. |
| `strokeDashArray` | `string` | — | — | no | Dash array |
| `strokeDashCap` | `string` | — | — | no | Dash cap |
| `strokeLineJoin` | `string` | — | — | no | Join type |
| `strokeShow` | `boolean` | — | — | no | Show |
| `strokeTransparency` | `number` | — | — | no | Transparency |
| `strokeWidth` | `number` | — | — | no | Width |

### markers

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `borderColor` | `object` | — | — | no | Border color around data point markers on line/scatter charts. |
| `borderColorMatchFill` | `boolean` | — | — | no | Match the border color to the main shape color. |
| `borderShow` | `boolean` | — | — | no | Border |
| `borderTransparency` | `number` | — | — | no | Transparency |
| `borderWidth` | `number` | — | — | no | Width |
| `rotation` | `number` | — | — | no | Rotation |
| `transparency` | `number` | — | — | no | Transparency |

### plotArea

See [./_shared/containers-plotArea.md](./_shared/containers-plotArea.md).

### referenceLine

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `autoScale` | `boolean` | — | — | no | Automatically adjust the spacing between dashes and dots based on line width. |
| `dashArray` | `string` | — | — | no | Dash array |
| `dashCap` | `string` | — | — | no | Dash cap |
| `dataLabelColor` | `object` | — | — | no | Font color for reference line value labels. |
| `dataLabelDecimalPoints` | `number` | — | — | no | Value decimal places |
| `dataLabelDisplayUnits` | `integer` | — | — | no | Display units for reference line labels. |
| `dataLabelHorizontalPosition` | `string` | — | — | no | Set the horizontal position for the reference line data label |
| `dataLabelShow` | `boolean` | — | — | no | Display a data label for the reference line |
| `dataLabelText` | `string` | — | — | no | Text shown in the label |
| `dataLabelVerticalPosition` | `string` | — | — | no | Set the vertical position for the reference line data label |
| `displayName` | `string` | — | — | no | Set reference line name |
| `lineColor` | `object` | — | — | no | Color of the reference line. |
| `position` | `string` | — | — | no | Arrange relative to chart data points |
| `shadeColor` | `object` | — | — | no | Fill color for the shaded area above or below a reference line. |
| `shadeColorMatchStroke` | `boolean` | — | — | no | Match the legend icon color to the color of the line, not the marker |
| `shadeRegion` | `string` | — | — | no | Shade region |
| `shadeShow` | `boolean` | — | — | no | Show |
| `shadeTransparency` | `number` | — | — | no | Shade transparency |
| `show` | `boolean` | — | — | no | Show |
| `style` | `string` | — | — | no | Line style |
| `transparency` | `number` | — | — | no | Transparency |
| `value` | `number` | — | — | no | Set reference line numeric value |
| `width` | `number` | — | — | no | Width |

### scalarKey

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `min` | `string` | — | — | no |  |

### seriesLabels

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backgroundColor` | `object` | — | — | no |  |
| `backgroundMatchColor` | `boolean` | — | — | no | Match series color |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `bold` | `boolean` | — | — | no | Bold |
| `enableBackground` | `boolean` | — | — | no | Show background |
| `italic` | `boolean` | — | — | no | Italic |
| `leaderLineAutoScale` | `boolean` | — | — | no | Automatically adjust the spacing between dashes and dots based on line width. |
| `leaderLineColor` | `object` | — | — | no |  |
| `leaderLineDashArray` | `string` | — | — | no | Dash array |
| `leaderLineDashCap` | `string` | — | — | no | Dash cap |
| `leaderLinePattern` | `string` | — | — | no | Line style |
| `leaderLineTransparency` | `number` | — | — | no | Transparency |
| `leaderLineWidth` | `number` | — | — | no | Width |
| `leaderLines` | `boolean` | — | — | no | Show |
| `maximumOffset` | `number` | — | — | no | Maximum offset |
| `seriesColor` | `object` | — | — | no |  |
| `seriesFontFamily` | `string` | — | — | no | Series font family |
| `seriesMatchColor` | `boolean` | — | — | no | Match series color |
| `seriesMaximumWidth` | `integer` | — | — | no | Width |
| `seriesPosition` | `string` | — | — | no | Position |
| `seriesTransparency` | `number` | — | — | no | Transparency |
| `seriesWordWrap` | `boolean` | — | — | no | Word wrap |
| `show` | `boolean` | — | — | no | Show for this series |
| `showAll` | `boolean` | — | — | no | Customize series |
| `showByDefault` | `boolean` | — | — | no |  |
| `textSize` | `number` | — | — | no |  |
| `underline` | `boolean` | — | — | no | Underline |

### smallMultiplesLayout

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `advancedPaddingOptions` | `boolean` | — | — | no | Advanced padding options |
| `backgroundColor` | `object` | — | — | no |  |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `columnCount` | `integer` | — | — | no | Columns |
| `columnPaddingInner` | `number` | — | — | no | Inner column padding |
| `columnPaddingOuter` | `number` | — | — | no | Outer column padding |
| `gridLineColor` | `object` | — | — | no |  |
| `gridLineShow` | `boolean` | — | — | no | Show |
| `gridLineStyle` | `string` | — | — | no | Line style |
| `gridLineTransparency` | `number` | — | — | no | Transparency |
| `gridLineType` | `string` | — | — | no | Gridlines to delineate the small multiple visuals |
| `gridLineWidth` | `number` | — | — | no | Width |
| `gridPadding` | `number` | — | — | no | Grid padding |
| `layoutType` | `string` | — | — | no | Grid layout |
| `rowCount` | `integer` | — | — | no | Rows |
| `rowPaddingInner` | `number` | — | — | no | Inner row padding |
| `rowPaddingOuter` | `number` | — | — | no | Outer row padding |

### subheader

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `alignment` | `string` | — | — | no |  |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `titleWrap` | `boolean` | — | — | no | Word wrap |
| `underline` | `boolean` | — | — | no | Underline |

### trend

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `autoScale` | `boolean` | — | — | no | Automatically adjust the spacing between dashes and dots based on line width. |
| `combineSeries` | `boolean` | — | — | no | Show one trend line per series or combine |
| `dashArray` | `string` | — | — | no | Dash array |
| `dashCap` | `string` | — | — | no | Dash cap |
| `displayName` | `string` | — | — | no | Set trend line name |
| `lineColor` | `object` | — | — | no |  |
| `show` | `boolean` | — | — | no | Show |
| `style` | `string` | — | — | no | Line style |
| `transparency` | `number` | — | — | no | Transparency |
| `useHighlightValues` | `boolean` | — | — | no | Use highlight values to calculate trend line |
| `width` | `number` | — | — | no | Width |

### valueAxis

See [./_shared/containers-valueAxis.md](./_shared/containers-valueAxis.md).

### xAxisReferenceLine

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `autoScale` | `boolean` | — | — | no | Automatically adjust the spacing between dashes and dots based on line width. |
| `dashArray` | `string` | — | — | no | Dash array |
| `dashCap` | `string` | — | — | no | Dash cap |
| `dataLabelColor` | `object` | — | — | no |  |
| `dataLabelDecimalPoints` | `number` | — | — | no | Value decimal places |
| `dataLabelDisplayUnits` | `integer` | — | — | no |  |
| `dataLabelHorizontalPosition` | `string` | — | — | no | Set the horizontal position for the reference line data label |
| `dataLabelShow` | `boolean` | — | — | no | Display a data label for the reference line |
| `dataLabelText` | `string` | — | — | no | Text shown in the label |
| `dataLabelVerticalPosition` | `string` | — | — | no | Set the vertical position for the reference line data label |
| `displayName` | `string` | — | — | no | Set reference line name |
| `lineColor` | `object` | — | — | no |  |
| `position` | `string` | — | — | no | Arrange relative to chart data points |
| `shadeColor` | `object` | — | — | no |  |
| `shadeColorMatchStroke` | `boolean` | — | — | no | Match the legend icon color to the color of the line, not the marker |
| `shadeRegion` | `string` | — | — | no | Shade region |
| `shadeShow` | `boolean` | — | — | no | Show |
| `shadeTransparency` | `number` | — | — | no | Shade transparency |
| `show` | `boolean` | — | — | no | Show |
| `style` | `string` | — | — | no | Line style |
| `transparency` | `number` | — | — | no | Transparency |
| `value` | `number | string` | — | — | no | Set reference line numeric or date time value according to x-axis type |
| `width` | `number` | — | — | no | Width |

### y1AxisReferenceLine

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `autoScale` | `boolean` | — | — | no | Automatically adjust the spacing between dashes and dots based on line width. |
| `dashArray` | `string` | — | — | no | Dash array |
| `dashCap` | `string` | — | — | no | Dash cap |
| `dataLabelColor` | `object` | — | — | no |  |
| `dataLabelDecimalPoints` | `number` | — | — | no | Value decimal places |
| `dataLabelDisplayUnits` | `integer` | — | — | no |  |
| `dataLabelHorizontalPosition` | `string` | — | — | no | Set the horizontal position for the reference line data label |
| `dataLabelShow` | `boolean` | — | — | no | Display a data label for the reference line |
| `dataLabelText` | `string` | — | — | no | Text shown in the label |
| `dataLabelVerticalPosition` | `string` | — | — | no | Set the vertical position for the reference line data label |
| `displayName` | `string` | — | — | no | Set reference line name |
| `lineColor` | `object` | — | — | no |  |
| `position` | `string` | — | — | no | Arrange relative to chart data points |
| `shadeColor` | `object` | — | — | no |  |
| `shadeColorMatchStroke` | `boolean` | — | — | no | Match the legend icon color to the color of the line, not the marker |
| `shadeRegion` | `string` | — | — | no | Shade region |
| `shadeShow` | `boolean` | — | — | no | Show |
| `shadeTransparency` | `number` | — | — | no | Shade transparency |
| `show` | `boolean` | — | — | no | Show |
| `style` | `string` | — | — | no | Line style |
| `transparency` | `number` | — | — | no | Transparency |
| `value` | `number` | — | — | no | Set reference line numeric value |
| `width` | `number` | — | — | no | Width |

### y2Axis

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `secAxisStyle` | `string` | — | — | no | Style |
| `secBold` | `boolean` | — | — | no | Bold |
| `secEnd` | `number` | — | — | no | Enter an ending value (optional) |
| `secFontFamily` | `string` | — | — | no | Font family |
| `secFontSize` | `number` | — | — | no |  |
| `secItalic` | `boolean` | — | — | no | Italic |
| `secLabelColor` | `object` | — | — | no |  |
| `secLabelDisplayUnits` | `integer` | — | — | no |  |
| `secLabelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `secLogAxisScale` | `boolean` | — | — | no | Logarithmic scale |
| `secRoundRange` | `boolean` | — | — | no | Round range limits to the nearest multiple. |
| `secShowAxisTitle` | `boolean` | — | — | no | Title for the Y-axis |
| `secStart` | `number` | — | — | no | Enter a starting value (optional) |
| `secTitleBold` | `boolean` | — | — | no | Bold |
| `secTitleColor` | `object` | — | — | no |  |
| `secTitleFontFamily` | `string` | — | — | no | Font family |
| `secTitleFontSize` | `number` | — | — | no |  |
| `secTitleItalic` | `boolean` | — | — | no | Italic |
| `secTitleText` | `string` | — | — | no | Axis title |
| `secTitleUnderline` | `boolean` | — | — | no | Underline |
| `secUnderline` | `boolean` | — | — | no | Underline |
| `show` | `boolean` | — | — | no | Show |

### zoom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `categoryMax` | `number` | — | — | no |  |
| `categoryMin` | `number` | — | — | no |  |
| `categorySize` | `number` | — | — | no |  |
| `show` | `boolean` | — | — | no | Show |
| `showLabels` | `boolean` | — | — | no | Slider labels |
| `showOnCategoryAxis` | `boolean` | — | — | no | X axis |
| `showOnValueAxis` | `boolean` | — | — | no | Y axis |
| `showOnValueSecAxis` | `boolean` | — | — | no | Secondary Y axis |
| `showTooltip` | `boolean` | — | — | no | Slider tooltips |
| `valueMax` | `number` | — | — | no |  |
| `valueMin` | `number` | — | — | no |  |
| `valueSecMax` | `number` | — | — | no |  |
| `valueSecMin` | `number` | — | — | no |  |
| `valueSecSize` | `number` | — | — | no |  |
| `valueSize` | `number` | — | — | no |  |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "lineChart",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — ANALYSIS - OWNERSHIP SIMULATION V3 CLEAN (visual nav-trajectory-â-actual-vs-sim-lineChart-a57f9f)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Category` = `[Month Year (MMMYY)]` (string time-bucket, not date); `Y` = 2 measures — `[NAV-Timeseries (Actuals)]` and `[NAV-Timeseries (Simul Switch)]`. Two-line trajectory for before/after comparison.
- **Position:** `(957, 160) 917×380` — right half of the ownership-simulation page; paired 1:1 with the `impact-ranking-pivotTable-969a74` matrix at `(20, 160) 917×380`.
- **vCO title:** `"NAV Trajectory — Actual vs Simul 1"` — the em-dash is intentional typography, preserve it.
- **Objects:** `legend`, `categoryAxis`, `valueAxis`, `dataPoint`, `lineStyles` — the minimal chart-rendering set plus `lineStyles` for dashed-vs-solid differentiation between actual/simulation series.
- **Minimal vCO:** only `title`, `subTitle`, `background` — theme defaults fill in the rest.
- **Pattern:** this is R0101's canonical "before/after comparison" line chart. When cloning, keep the measure pair structure (one actual, one simulated) rather than adding a third line.
<!-- /MANUAL -->

### R0101 — ANALYSIS - BENCHMARK (visual 46f402073e867942686d)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Category` = `[Month Year (MMMYY)]`; `Y` = 2 measures — `[Return (%) Axis]` and `[Return (%) Axis (bench)]`. `Tooltips` role is bound to `[Return (%) Axis - Asset vs Benchmark]` for hover detail. Asset return vs benchmark return, overlaid.
- **Position:** `(0, 0) 1271×369` — full-width top panel of the benchmark analysis page.
- **Heavy object stack:** `valueAxis`, `categoryAxis`, `lineStyles`, `dataPoint`, `legend`, `labels`, `zoom`, `general`, `error`, `seriesLabels`, `markers`, `y2Axis` — notable additions vs the trajectory chart: `zoom` (X-axis zoom slider), `error` (error bars), `seriesLabels` (per-line inline labels), `markers` (data point dots), `y2Axis` (secondary Y axis, though only 2 measures — y2 is configured for future use).
- **Full vCO chrome** including `subTitle`, `divider`, `spacing`, `padding`, `visualTooltip` — the benchmark page's card chrome is the heaviest in R0101.
- **Pattern:** asset-vs-benchmark line overlays in institutional reporting benefit from markers + seriesLabels so the reader can identify each line without relying on the legend.
<!-- /MANUAL -->

### R0101 — ANALYSIS - BENCHMARK (visual b004bcacded306eeac01)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Second instance of the benchmark pattern** — same `Category`, same 2-measure `Y`, same `Tooltips` binding as 46f402…, but on a different section of the BENCHMARK page.
- **Position:** `(≈0.7, ≈0.9) 1272×369` — effectively `(0, 0) 1272×369` after rounding; the sub-pixel offsets are save-artefacts.
- **Identical object + vCO stack** to 46f402….
- **Pair pattern:** R0101 stacks two benchmark line charts vertically on the same page to compare different time windows (YTD vs 1Y, for example). Author them together and keep the objects block in lock-step.
<!-- /MANUAL -->

### R0101 — PORFTOLIO - INSTRUMENT BREAKDOWN (visual e904a3f22c0a00226c35)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Category` = `[Year Month Nr]` (integer YYYYMM bucket, NOT a date); `Y` = 1 measure — `[NAV-Timeseries - PV1]`. Single-series sparkline-ish line chart.
- **Position:** `(≈3, ≈111) 453×194` — compact 453×194 footprint; this is a small-multiples companion chart, not a full panel.
- **Minimal object stack:** `categoryAxis`, `valueAxis`, `lineStyles`, `dataPoint`, `labels`, `seriesLabels` — no legend (only 1 series), no zoom, no error bars.
- **Minimal vCO:** only `title`, `background`, `padding` — compact card chrome.
- **Pattern:** when a line chart is <500px wide and shows a single measure, drop the legend + markers + zoom objects to reduce visual noise. This extract is the baseline for "small-multiples line in a grid of 4–6".
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### Legacy `Y2` usage in combo-chart conversions

Some R0101 charts that read as pure line charts are actually `lineClusteredColumnComboChart` under the hood, with both series bound to `Y2` (the line axis) and no column series. When copying patterns from an "existing line chart in R0101", check the `visual.visualType` first:

- `"visualType": "lineChart"` — plain line chart, bind measures to `Y`
- `"visualType": "lineClusteredColumnComboChart"` with only `Y2` used — legacy pattern, prefer `lineChart` in new work

For fresh line charts, always use `lineChart` with measures in the `Y` role.

### Dual-axis implies combo chart, not line chart

If the design calls for two series with different scales (e.g. NAV in millions and yield %), that is a `lineClusteredColumnComboChart` case. A pure `lineChart` cannot host a secondary axis — attempting to squeeze two scale-incompatible series onto one axis compresses one of them into a flat line.

### Trend context comes from category ordering

Line charts read left-to-right as time. Ensure the `Category` role is a sortable temporal column (date, year-month). Binding a categorical dimension (e.g. entity name) to `Category` produces a line that connects unrelated points — technically a chart, semantically meaningless.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-linechart](../GUIDE.md#cli-linechart)
- JSON format: [../GUIDE.md#json-linechart](../GUIDE.md#json-linechart)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- docs/design-principles/chart-selection.md — trend visualization decision tree.
- docs/design-principles/density.md — max-4-lines rule and small-multiples escalation.
- docs/visual-catalog/lineClusteredColumnComboChart.md — when to escalate to the combo chart for stock-and-flow.
<!-- /MANUAL -->
