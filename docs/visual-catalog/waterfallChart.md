# waterfallChart

## Overview

<!-- MANUAL: overview -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Breakdown` | no | no | Column |
| `Category` | yes | no | Column |
| `Y` | yes | no | Measure |

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
- **breakdown** (1 properties) — documented inline below
- **categoryAxis** (19 properties) — see [./_shared/containers-categoryAxis.md](./_shared/containers-categoryAxis.md)
- **labels** (14 properties) — documented inline below
- **legend** (10 properties) — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **plotArea** (2 properties) — see [./_shared/containers-plotArea.md](./_shared/containers-plotArea.md)
- **sentimentColors** (4 properties) — documented inline below
- **valueAxis** (32 properties) — see [./_shared/containers-valueAxis.md](./_shared/containers-valueAxis.md)
- **y1AxisReferenceLine** (23 properties) — documented inline below

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

### breakdown

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `maxBreakdowns` | `integer` | — | — | no | The number of individual breakdowns to show (rest grouped into Other) |

### categoryAxis

See [./_shared/containers-categoryAxis.md](./_shared/containers-categoryAxis.md).

### labels

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backgroundColor` | `object` | — | — | no | Background color behind data labels. |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `bold` | `boolean` | — | — | no | Bold |
| `color` | `object` | — | — | no | Font color for data labels. |
| `enableBackground` | `boolean` | — | — | no | Show background |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for data labels. |
| `italic` | `boolean` | — | — | no | Italic |
| `labelDisplayUnits` | `integer` | — | — | no | Display units for data label values (Auto, Thousands, Millions, Billions). |
| `labelOrientation` | `integer` | — | — | no | Orientation |
| `labelPosition` | `string` | — | — | no | Position |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `show` | `boolean` | — | — | no | Show |
| `underline` | `boolean` | — | — | no | Underline |

### legend

See [./_shared/containers-legend.md](./_shared/containers-legend.md).

### plotArea

See [./_shared/containers-plotArea.md](./_shared/containers-plotArea.md).

### sentimentColors

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `decreaseFill` | `object` | — | — | no |  |
| `increaseFill` | `object` | — | — | no |  |
| `otherFill` | `object` | — | — | no |  |
| `totalFill` | `object` | — | — | no |  |

### valueAxis

See [./_shared/containers-valueAxis.md](./_shared/containers-valueAxis.md).

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

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "waterfallChart",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — PORTFOLIO - ASSET MANAGER (visual e9e6524190b3b0dbc0eb)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

### R0101 — STRATEGIC ALLOCATION (visual f82168f249e80838e597)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-waterfallchart](../GUIDE.md#cli-waterfallchart)
- JSON format: [../GUIDE.md#json-waterfallchart](../GUIDE.md#json-waterfallchart)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
TODO: fill in during enrichment.
<!-- /MANUAL -->
