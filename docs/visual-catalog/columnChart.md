# columnChart

## Overview

<!-- MANUAL: overview -->
`columnChart` is the vertical-bar chart — categories run along the X axis, measures run up the Y axis. Compared with `barChart` (horizontal bars), column charts read better when categories are few (<=10) and have short labels, or when the X axis represents ordered categories (time buckets, scenarios, ranks). It is Power BI's default type for "a measure grouped by a category" when horizontal space exceeds vertical space.

Note: in PBIR the internal name `columnChart` covers both plain and waterfall column-style charts; a waterfall is authored as a columnChart with measures WF1–WF5 in the `Y` role (R0101 convention).
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `columnChart` when:

- Categories fit in ~10 labels **or fewer** and X labels are short enough to read horizontally.
- The X axis has a **natural order** (time buckets, ranks, scenarios) — columns reinforce the sequence.
- You need **stacking or clustering** across a Legend dimension with few categories (2–4).

Prefer `barChart` (horizontal) when: categories exceed ~10, or labels are long (entity names, fund names). Prefer `lineChart` when the primary signal is trend over continuous time. Prefer `lineClusteredColumnComboChart` when mixing an absolute column (count, revenue) with a rate line (yield, % change).
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
- **dataPoint** (10 properties) — documented inline below
- **error** (24 properties) — documented inline below
- **filters** (1 properties) — documented inline below
- **labels** (61 properties) — documented inline below
- **layout** (5 properties) — documented inline below
- **legend** (11 properties) — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **plotArea** (2 properties) — see [./_shared/containers-plotArea.md](./_shared/containers-plotArea.md)
- **ribbonBands** (9 properties) — documented inline below
- **smallMultiplesLayout** (17 properties) — documented inline below
- **subheader** (10 properties) — documented inline below
- **totals** (13 properties) — documented inline below
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
| `borderOutlineOnly` | `boolean` | — | — | no | Hide inner borders |
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
| `ribbonGapSize` | `number` | — | — | no | Space between ribbons and columns |
| `seriesOrderReversed` | `boolean` | — | — | no | Reverse the series order of your bars or columns. |
| `seriesOrderSorted` | `boolean` | — | — | no | Within each category, dynamically sort series by their data value. |
| `stackedGapExplodes` | `boolean` | — | — | no | Series explosion |
| `stackedGapSize` | `number` | — | — | no | Space between series |

### legend

See [./_shared/containers-legend.md](./_shared/containers-legend.md).

### plotArea

See [./_shared/containers-plotArea.md](./_shared/containers-plotArea.md).

### ribbonBands

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `borderColor` | `object` | — | — | no |  |
| `borderColorMatchFill` | `boolean` | — | — | no | Match the border color to the main shape color. |
| `borderShow` | `boolean` | — | — | no | Border |
| `borderSize` | `number` | — | — | no | Width |
| `borderTransparency` | `number` | — | — | no | Transparency |
| `fillColor` | `object` | — | — | no |  |
| `fillMatchColor` | `boolean` | — | — | no | Change the color of ribbons to match the color of the series. |
| `fillTransparency` | `number` | — | — | no | Transparency |
| `show` | `boolean` | — | — | no | Show |

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
| `showOnCategoryAxis` | `boolean` | — | — | no | X axis |
| `showOnValueAxis` | `boolean` | — | — | no | Y axis |
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
    "visualType": "columnChart",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — CURRENCY EXPOSURE (visual 2fedce9c6a32bda874a0)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Category` = `[Month Year (MMMYY)]` (from the `s Family Period ACT (timeseries)` switcher); `Series` (legend) = `[Instrument Currency]`; `Y` = `[NAV Non FO Curr. - Over Time]`. **This is the only columnChart in the 4-extract set that actually uses its Series role** — stacked columns by currency across time.
- **Position:** `(≈6, 525) 747×342` — bottom-left panel on the currency-exposure page.
- **vCO title:** `"NAV Exposure over timeㅤ"` — trailing Hangul-filler (U+3164) is a spacing hack. Don't normalise.
- **Objects:** `categoryAxis`, `labels`, `legend`, `valueAxis`, `dataPoint` — full chart-rendering suite. The `legend` is configured because Series is bound.
- **Full vCO chrome** matches the cross-page card convention.
- **Pattern:** time on X + a categorical legend = time-bucket stacked columns. Prefer this over a stacked area chart when the value jumps are meaningful (monthly FX shifts, not smooth trends).
<!-- /MANUAL -->

### R0101 — DIR COMMODITIES - OVERVIEW (visual 2ae69e69c62ca20c7583)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **This is a waterfall chart stored as `columnChart`.** Detect by the measure names: `Y` = `[WF1-Base]`, `[WF5-Return Base]`, `[WF2-Blank]`, `[WF3-Negative]`, `[WF4-Positive]` — the five WF* measures that drive PBI's native waterfall layout.
- **Role binding:** `Category` = `[Bucket]` (from `s Waterfall Bucket`); `Y` = the 5 WF* measures in canonical order (base, return-base, blank spacer, negative, positive).
- **Position:** `(0, 1) 930×317` — full-width waterfall panel.
- **Objects:** `valueAxis`, `categoryAxis`, `legend`, `general`, `dataPoint`, `ribbonBands`, `labels` — the `ribbonBands` object is the distinctive waterfall-specific rendering hint.
- **Full vCO chrome.** No overrides unique to this visual.
- **Measure order is load-bearing:** WF1-Base must come first, WF2-Blank is the transparent spacer, WF3/WF4 are negative/positive bars, WF5-Return Base is the closing balance. Swap order = break rendering.
- **Shared pattern with `d83ad29…` and `defd0b68…`:** three waterfall charts on R0101 with identical 930×317 footprint and identical WF measure set — only the slicer context (commodities, project dev, DIR participations) changes the data.
<!-- /MANUAL -->

### R0101 — PROJECT DEVELOPMENT - OVERVIEW (visual d83ad29131a553d8b6b9)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Second instance of the WF1–WF5 waterfall pattern.** Same `Category` = `[Bucket]`, same 5-measure `Y` set, same footprint (930×317) as 2ae69e69….
- **Position:** `(0, ≈1) 930×317`.
- **Near-zero drift:** all three waterfalls on R0101 share the entire objects/vCO block verbatim. When copying between pages use `pbir_add_visuals` with the extract JSON as-is and only change `position` if needed.
<!-- /MANUAL -->

### R0101 — DIR PARTICIPATIONS - OVERVIEW (visual defd0b68f70828d38e66)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Third instance of the WF1–WF5 waterfall pattern.** Identical to 2ae69e69… and d83ad291… — same `Category`/`Y`/position/objects.
- **Pattern:** R0101's three overview pages (DIR COMMODITIES, PROJECT DEVELOPMENT, DIR PARTICIPATIONS) each host one waterfall chart rendered identically; the filter/slicer context is what differentiates the data, not the visual configuration. This is why the per-visual extracts are near-duplicates.
- **Do not drift these:** any schema change to one of the three should be applied to all three in a single edit pass to keep the pages consistent.
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### Waterfalls live under `columnChart`, not a dedicated type

There is no `waterfallChart` visual type in R0101's PBIR. What looks like a waterfall in the UI is a `columnChart` with 5 specially-named measures (`WF1-Base`, `WF2-Blank`, `WF3-Negative`, `WF4-Positive`, `WF5-Return Base`) bound to `Y` in that canonical order, plus a `Bucket` dimension on `Category`. When authoring a new waterfall, start from an existing extract — do NOT try to build it from a blank columnChart template.

### Measure order is load-bearing for waterfalls

The 5 WF* measures must appear in the `Y` role in the order: `WF1-Base`, `WF5-Return Base`, `WF2-Blank`, `WF3-Negative`, `WF4-Positive` (the order observed in every R0101 extract). The internal `ribbonBands` rendering assumes this exact sequence. Swapping even two of them produces blank bars where coloured ones should be.

### Waterfall category must come from the `s Waterfall Bucket` switcher

The `Bucket` column on `s Waterfall Bucket` defines the waterfall stages (Opening, FX, Return, Closing, etc.). Binding a different categorical column to `Category` while keeping the WF1–WF5 measures produces misaligned colours because the measure-to-bar mapping is driven by row position from the bucket switcher's contract, not a join.

### Native stacking requires a `Series` binding

If you want stacked columns (see the CURRENCY EXPOSURE extract), bind a categorical column to `Series` (legend role). Without it, the columns are drawn one-measure-per-bar, not stacked — a common authoring mistake when converting a column chart into a stacked-time-series.

### `Category` as date-typed column → PBI renders a continuous axis

Binding a real date column to `Category` flips PBI's axis mode from "categorical" to "continuous date", which changes x-axis rendering (evenly spaced vs date-proportional spacing) and tick density. If you need evenly-spaced month-buckets, use a string field like `[Month Year (MMMYY)]` from the time-series switcher, as the WATERFALL and CURRENCY EXPOSURE extracts do.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-columnchart](../GUIDE.md#cli-columnchart)
- JSON format: [../GUIDE.md#json-columnchart](../GUIDE.md#json-columnchart)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- docs/design-principles/chart-selection.md — column vs bar vs line decision points.
- docs/design-principles/density.md — category count budget (>10 categories ⇒ switch to barChart).
- **Waterfall gotcha:** R0101 stores waterfall charts as `columnChart` with WF1–WF5 measures in `Y`. Detect by measure-name pattern, not visual type.
<!-- /MANUAL -->
