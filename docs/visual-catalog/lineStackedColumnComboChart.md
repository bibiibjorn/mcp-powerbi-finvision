# lineStackedColumnComboChart

## Overview

<!-- MANUAL: overview -->
`lineStackedColumnComboChart` is the stacked-column variant of the combo chart: columns are **stacked** (segment contributions sum to a total) and a line overlays on the secondary axis. Like its clustered sibling, it uses `ColumnY` and `LineY` as the role names — NOT the generic `Y` used by single-series charts.

The stacked column portion communicates part-to-whole composition per category; the line typically represents a rate, ratio, or target that cuts across all stacks. This combination is well-suited to financial reporting (e.g., stacked revenue by asset class with a total return % line).

Key distinction from `lineClusteredColumnComboChart`: columns stack rather than cluster, so there is no `Series` role — the stacking dimension is implicit from the `Category` hierarchy or the `ColumnY` fields. The `totals` formatting container (which appears in this chart but not in the clustered variant) controls the display of the stacked total above each bar.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `lineStackedColumnComboChart` when:

- Showing **composition + trend** together — e.g., AUM by strategy (stacked) with a total performance line.
- The column series represents **segments that add up to a meaningful whole** and you also need to overlay a rate or target.
- You want to communicate **100%-relative weight is less important than absolute total** — if relative weight matters more, prefer `hundredPercentStackedColumnChart` without a line.

Prefer `lineClusteredColumnComboChart` when the column segments should be compared side-by-side rather than stacked.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Category` | yes | no | Column |
| `ColumnY` | no | yes | Measure |
| `LineY` | no | yes | Measure |

## Containers

- **background** (3 properties) — see [./_shared/containers-background.md](./_shared/containers-background.md)
- **border** (4 properties) — see [./_shared/containers-border.md](./_shared/containers-border.md)
- **divider** (5 properties) — documented inline below
- **dropShadow** (9 properties) — documented inline below
- **general** (5 properties) — see [./_shared/containers-general.md](./_shared/containers-general.md)
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
- **categoryAxis** (37 properties) — see [./_shared/containers-categoryAxis.md](./_shared/containers-categoryAxis.md)
- **dataPoint** (11 properties) — documented inline below
- **error** (30 properties) — documented inline below
- **filters** (1 properties) — documented inline below
- **labels** (58 properties) — documented inline below
- **layout** (3 properties) — documented inline below
- **legend** (12 properties) — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **lineStyles** (21 properties) — documented inline below
- **markers** (7 properties) — documented inline below
- **plotArea** (2 properties) — see [./_shared/containers-plotArea.md](./_shared/containers-plotArea.md)
- **seriesLabels** (27 properties) — documented inline below
- **smallMultiplesLayout** (17 properties) — documented inline below
- **subheader** (10 properties) — documented inline below
- **totals** (13 properties) — documented inline below
- **valueAxis** (57 properties) — see [./_shared/containers-valueAxis.md](./_shared/containers-valueAxis.md)
- **xAxisReferenceLine** (23 properties) — documented inline below
- **y1AxisReferenceLine** (23 properties) — documented inline below
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

### categoryAxis

See [./_shared/containers-categoryAxis.md](./_shared/containers-categoryAxis.md).

### dataPoint

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `borderColor` | `object` | — | — | no | Border/outline color around individual data points (bars, columns, pie slices). |
| `borderColorMatchFill` | `boolean` | — | — | no | Match the border color to the main shape color. |
| `borderOutlineOnly` | `boolean` | — | — | no | Hide inner borders |
| `borderShow` | `boolean` | — | — | no | Border |
| `borderSize` | `number` | — | — | no | Width |
| `borderTransparency` | `number` | — | — | no | Transparency |
| `defaultColor` | `object` | — | — | no | Default fill color for data points when no series coloring is applied. |
| `fill` | `object` | — | — | no | Fill color for data points. Supports conditional formatting via a measure returning theme color tokens ('good', 'bad', 'neutral') for per-bar/per-slice coloring. |
| `fillRule` | `object` | — | — | no | Gradient fill rule for data points (used with conditional formatting gradient scales). |
| `fillTransparency` | `number` | — | — | no | Transparency |
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
| `italic` | `boolean` | — | — | no | Italic |
| `labelContainerMaxWidth` | `number` | — | — | no | Maximum width |
| `labelContentLayout` | `string` | — | — | no | Layout |
| `labelDensity` | `integer` | — | — | no | Label density |
| `labelDisplayUnits` | `integer` | — | — | no | Display units for data label values (Auto, Thousands, Millions, Billions). |
| `labelOrientation` | `integer` | — | — | no | Orientation |
| `labelPosition` | `string` | — | — | no | Position |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
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
| `seriesOrderReversed` | `boolean` | — | — | no | Reverse the series order of your bars or columns. |
| `seriesOrderSorted` | `boolean` | — | — | no | Within each category, dynamically sort series by their data value. |
| `stackedGapSize` | `number` | — | — | no | Space between series |

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
| `showMarker` | `boolean` | — | — | no | Show |
| `showMarkerByDefault` | `boolean` | — | — | no | Show data point markers on line charts by default. Useful for sparse data where individual points matter. |
| `showSeries` | `boolean` | — | — | no | Customize series |
| `strokeAutoScale` | `boolean` | — | — | no | Automatically adjust the spacing between dashes and dots based on line width. |
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

### totals

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
| `labelDisplayUnits` | `integer` | — | — | no |  |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `show` | `boolean` | — | — | no | Show |
| `showPositiveAndNegative` | `boolean` | — | — | no | Show separate labels for the total of the positive values and the total of the negative values |
| `underline` | `boolean` | — | — | no | Underline |

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
    "visualType": "lineStackedColumnComboChart",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — CASH FLOW CURVE (visual 1f8dfa3e30534ab0bfdb)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

### R0101 — PRIVATE FUNDS - DETAIL (visual 5fd7b8540b69705ee099)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

### R0101 — PRIVATE FUNDS - CARD (visual 7c039d150b1502ad4980)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

### R0101 — PRIVATE FUNDS - JCURVE (visual 86e9752ca56b07f70b40)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### Wrong role name (same as the clustered variant)

`ColumnY` and `LineY` are required, not `Y`. This is the #1 error — a blank visual with no error message. See `lineClusteredColumnComboChart.md` for the verification script.

### Stack totals container

The `totals` container (unique to this stacked variant) controls whether a label showing the column total appears above each bar. If `totals.show` is not explicitly set to `false`, a total label appears by default even when `labels.show` is `false`. Set both independently.

### Negative stacks distort the secondary axis

When any `ColumnY` measure returns negative values, the stacked columns extend below zero. If the `LineY` measure is a positive ratio (e.g., 0–100%), the auto-scaled secondary axis will anchor at 0 and the negative stack will push into the plot area visually overlapping the line. Set `y1Axis.start` explicitly to accommodate the negative extent.

### Visual calculations not supported

Same restriction as the clustered variant — visual calculations (`RUNNINGSUM`, etc.) are not available on `lineStackedColumnComboChart`.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-linestackedcolumncombochart](../GUIDE.md#cli-linestackedcolumncombochart)
- JSON format: [../GUIDE.md#json-linestackedcolumncombochart](../GUIDE.md#json-linestackedcolumncombochart)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- `lineClusteredColumnComboChart.md` — clustered variant; identical role names, no `totals` container.
- Research corpus §5 — role-name table confirming `ColumnY`/`LineY` for both combo types.
- docs/design-principles/chart-selection.md — stacked vs. clustered vs. combo decision.
<!-- /MANUAL -->
