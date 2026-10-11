# funnel

## Overview

<!-- MANUAL: overview -->
`funnel` renders a set of stages as horizontal trapezoids that decrease in width from top to bottom, with each stage's width proportional to its `Values` measure. A percentage bar label between stages shows the drop-off ratio from one step to the next.

Two roles, both simple: `Category` (the stage labels, from a column) and `Values` (the numeric measure that sizes each bar). There is no Series or Legend role — funnels are single-series only. The `percentBarLabel` container controls the between-stage drop-off annotation; `labels` controls the data label on each bar itself.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `funnel` when:

- Visualizing **sequential stage drop-off** — sales pipeline, onboarding flow, support ticket resolution.
- The data has a natural **ordered sequence** where each stage has a smaller count/value than the prior one.
- You want the **percentage change between steps** to be visually prominent (built-in via `percentBarLabel`).

Avoid the funnel for unordered categories or when the values do not decrease monotonically — the tapered shape implies a directed flow that misleads if values jump up between stages. For part-to-whole without ordering, prefer `donutChart` or `treemap`.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Category` | yes | no | Column |
| `Values` | yes | no | Measure |

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
- **categoryAxis** (7 properties) — see [./_shared/containers-categoryAxis.md](./_shared/containers-categoryAxis.md)
- **dataPoint** (4 properties) — documented inline below
- **labels** (15 properties) — documented inline below
- **percentBarLabel** (7 properties) — documented inline below

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

### categoryAxis

See [./_shared/containers-categoryAxis.md](./_shared/containers-categoryAxis.md).

### dataPoint

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `defaultColor` | `object` | — | — | no | Default fill color for data points when no series coloring is applied. |
| `fill` | `object` | — | — | no | Fill color for data points. Supports conditional formatting via a measure returning theme color tokens ('good', 'bad', 'neutral') for per-bar/per-slice coloring. |
| `fillRule` | `object` | — | — | no | Gradient fill rule for data points (used with conditional formatting gradient scales). |
| `showAllDataPoints` | `boolean` | — | — | no | Show all |

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
| `funnelLabelStyle` | `string` | — | — | no | Label style |
| `italic` | `boolean` | — | — | no | Italic |
| `labelDisplayUnits` | `integer` | — | — | no | Display units for data label values (Auto, Thousands, Millions, Billions). |
| `labelPosition` | `string` | — | — | no | Position |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `percentageLabelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the percentages |
| `show` | `boolean` | — | — | no | Show |
| `underline` | `boolean` | — | — | no | Underline |

### percentBarLabel

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `bold` | `boolean` | — | — | no | Bold |
| `color` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `show` | `boolean` | — | — | no | Show |
| `underline` | `boolean` | — | — | no | Underline |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "funnel",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->

### Values must decrease for the funnel shape to make sense

Power BI renders funnel bars in the order they appear in the data, not sorted descending automatically. If your values do not naturally decrease, the bars will not form a taper — you get a "jagged funnel" that looks like a bar chart with trapezoidal bars. Sort the category field by `Values` descending in the query or model.

### `percentBarLabel` is between-stage, not per-bar

The `percentBarLabel` container shows the ratio of each stage to the **previous** stage (not to the first stage or the total). If you need cumulative drop-off (first stage = 100%), you must compute it as a model measure and surface it via `labels` instead.

### Single series only

No `Series` or `Legend` role exists. You cannot split a funnel by a dimension into multiple colored segments. For a multi-series comparison of pipelines, use side-by-side bar charts.

### Category role must be a column, not a measure

`Category` only accepts dimension columns. If your stages come from a calculated measure or a parameter, you must materialize them as rows in a table before binding.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-funnel](../GUIDE.md#cli-funnel)
- JSON format: [../GUIDE.md#json-funnel](../GUIDE.md#json-funnel)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Research corpus §5 — role-name table for `funnel` (`Category`, `Values`).
- docs/design-principles/chart-selection.md — when to use funnel vs. bar chart.
<!-- /MANUAL -->
