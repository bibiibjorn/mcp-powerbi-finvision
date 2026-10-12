# clusteredBarChart

## Overview

<!-- MANUAL: overview -->
`clusteredBarChart` renders multiple measures or a measure split by a series dimension as side-by-side horizontal bars per category. It is the horizontal counterpart of `clusteredColumnChart`. Each category on the Y axis gets a group of bars — one bar per `Legend` value or per measure — extending left-to-right. The grouped layout preserves absolute values for direct comparison across series at each category level, whereas `barChart` with a `Legend` field stacks them.

Roles: `Category` (Y axis dimension), `Y` (one or more measures), `Legend` (optional — splits one measure into one bar per legend value per category), `SmallMultiples` (optional). The role name for the measure is `Y`, not `Values` — wrong role silently blanks the visual.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `clusteredBarChart` when:

- You need to **compare two or more measures side-by-side per category** — e.g., NAV Actual vs NAV Simulated per entity, or Return vs Target per fund.
- **Category labels are long** (entity names, fund names) and need to be read horizontally without truncation.
- You have a **small-to-medium number of categories** (≤15) and **2–4 series** — beyond that, the bar clusters become too dense.

Prefer `barChart` (stacked) when the segments within each category should be read as parts of a total. Prefer `clusteredColumnChart` when categories have short labels and you want the bars to extend upward. Prefer `pivotTable` when users need exact values for each combination rather than a visual comparison.
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
- **categoryAxis** (37 properties) — see [./_shared/containers-categoryAxis.md](./_shared/containers-categoryAxis.md)
- **dataPoint** (9 properties) — documented inline below
- **error** (24 properties) — documented inline below
- **filters** (1 properties) — documented inline below
- **labels** (61 properties) — documented inline below
- **layout** (5 properties) — documented inline below
- **legend** (11 properties) — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **plotArea** (2 properties) — see [./_shared/containers-plotArea.md](./_shared/containers-plotArea.md)
- **referenceLine** (23 properties) — documented inline below
- **smallMultiplesLayout** (17 properties) — documented inline below
- **subheader** (10 properties) — documented inline below
- **trend** (11 properties) — documented inline below
- **valueAxis** (34 properties) — see [./_shared/containers-valueAxis.md](./_shared/containers-valueAxis.md)
- **xAxisReferenceLine** (23 properties) — documented inline below
- **y1AxisReferenceLine** (23 properties) — documented inline below
- **zoom** (11 properties) — documented inline below

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
| `borderShow` | `boolean` | — | — | no | Border |
| `borderSize` | `number` | — | — | no | Width |
| `borderTransparency` | `number` | — | — | no | Transparency |
| `defaultColor` | `object` | — | — | no | Default fill color for data points when no series coloring is applied. |
| `fill` | `object` | — | — | no | Fill color for data points. Supports conditional formatting via a measure returning theme color tokens ('good', 'bad', 'neutral') for per-bar/per-slice coloring. |
| `fillRule` | `object` | — | — | no | Gradient fill rule for data points (used with conditional formatting gradient scales). |
| `fillTransparency` | `number` | — | — | no | Transparency |

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
| `horizontalAlignment` | `string` | — | — | no | Horizontal alignment of data labels relative to the data point. |
| `italic` | `boolean` | — | — | no | Italic |
| `labelContainerMaxWidth` | `number` | — | — | no | Maximum width |
| `labelContentLayout` | `string` | — | — | no | Layout |
| `labelDensity` | `integer` | — | — | no | Label density |
| `labelDisplayUnits` | `integer` | — | — | no | Select the units (millions, billions, etc.) |
| `labelOrientation` | `integer` | — | — | no | Orientation |
| `labelOverflow` | `boolean` | — | — | no | Allow the labels to overflow outside of the shape's boundaries |
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
| `wordWrap` | `boolean` | — | — | no | Word wrap |

### layout

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `clusteredGapOverlapReverse` | `boolean` | — | — | no | Flip overlap |
| `clusteredGapOverlaps` | `boolean` | — | — | no | Overlap |
| `clusteredGapSize` | `number` | — | — | no | Space between series |
| `seriesOrderReversed` | `boolean` | — | — | no | Reverse the series order of your bars or columns. |
| `seriesOrderSorted` | `boolean` | — | — | no | Within each category, dynamically sort series by their data value. |

### legend

See [./_shared/containers-legend.md](./_shared/containers-legend.md).

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

### zoom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `categoryMax` | `number` | — | — | no |  |
| `categoryMin` | `number` | — | — | no |  |
| `categorySize` | `number` | — | — | no |  |
| `show` | `boolean` | — | — | no | Show |
| `showLabels` | `boolean` | — | — | no | Slider labels |
| `showOnCategoryAxis` | `boolean` | — | — | no | Y axis |
| `showOnValueAxis` | `boolean` | — | — | no | X axis |
| `showTooltip` | `boolean` | — | — | no | Slider tooltips |
| `valueMax` | `number` | — | — | no |  |
| `valueMin` | `number` | — | — | no |  |
| `valueSize` | `number` | — | — | no |  |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "clusteredBarChart",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — ANALYSIS - OWNERSHIP SIMULATION V3 CLEAN (visual by-entity-clusteredBarChart-ed0035)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Category` = entity dimension (one row per entity/fund); `Y` = two or more NAV/ownership measures bound side-by-side. Uses the `Legend` or `Series` role to split a single measure by scenario dimension.
- **Pattern:** ANALYSIS - OWNERSHIP SIMULATION page uses this to compare Actual vs Simulation ownership NAV per entity. Two `Y` measures (or `Y` + `Legend` split) produce the clustered groups.
- **Objects present:** `dataPoint` for per-series colour overrides, `categoryAxis`, `valueAxis`, `legend` — standard axis chart object set. Copy `visual.objects` verbatim from this extract when authoring a sibling chart.
- **Sort:** sorted descending by the primary `Y` measure so the largest entity appears at the top — achieved via `visual.query.sortBy` with `direction: "Descending"`.
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### Role name is `Y` not `Values` — wrong role silently blanks the visual

`clusteredBarChart` uses `Y` as the measure role name. Binding a measure to `Values` (the `card`/`donutChart` role) or any other role name produces a blank visual with no error displayed. Always verify role names from an existing same-type visual.

### Adding `Legend` switches from multi-measure to multi-series — first `Y` wins

If you add a column to the `Legend` role while multiple measures are in `Y`, Power BI keeps only the first `Y` measure and uses the `Legend` values as the series grouping. The remaining `Y` measures are silently ignored. This is correct behaviour but frequently surprises when converting from a multi-measure bar chart.

### Clustered bars grow narrow with many categories × many series

With 10 categories and 4 series, each category group contains 4 narrow bars. Below ~8px per bar, bars become invisible or unclickable. Keep the product of (category count × series count) under ~30 for a chart of typical height (250–350 px). Above that, consider a small-multiples layout or switch to a `pivotTable`.

### `clusteredBarChart` is not the same as `barChart` with `Legend`

`barChart` + `Legend` = stacked bars. `clusteredBarChart` = side-by-side bars. They are different `visualType` values. To convert from stacked to clustered, change the `visualType` string in `visual.json` — the roles are identical so field bindings carry over without change.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-clusteredbarchart](../GUIDE.md#cli-clusteredbarchart)
- JSON format: [../GUIDE.md#json-clusteredbarchart](../GUIDE.md#json-clusteredbarchart)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- barChart.md — stacked (not clustered) horizontal bars; same roles, different `visualType`.
- clusteredColumnChart.md — vertical orientation of the same grouped bar pattern.
- docs/design-principles/chart-selection.md — grouped vs stacked decision.
<!-- /MANUAL -->
