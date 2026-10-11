# gauge

## Overview

<!-- MANUAL: overview -->
`gauge` renders a semicircular arc (speedometer-style) where a needle or filled arc indicates the current `Value` relative to a range defined by `MinValue` and `MaxValue`, with an optional `TargetValue` tick mark on the arc. The callout value area below the arc shows the current value formatted as a number.

Note: the research corpus lists the primary role as `Y` (gauge in the role table) — the stub lists it as `Value`. Always verify from an existing gauge in your report, as older reports may have been saved with `Y` as the primary role name. The PBIR role name from the corpus is `Y`, `MinValue`, `MaxValue`, `TargetValue`.

The gauge conveys a single scalar measure and its position within a min-max band. It is one of the simplest visuals from a data-binding standpoint (single required role) but one of the most commonly misused — it consumes significant canvas space to convey very little information.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `gauge` when:

- You have a **single KPI with a well-defined target range** — e.g., portfolio risk score between 0 and 100, with a target of 75.
- The consumer needs to see **"how far along"** a metric is within a fixed scale — completion rate, utilization rate, health score.
- The visual is in a **dashboard header or a dedicated summary area** where a single number with spatial context is more intuitive than a bare card.

Avoid the gauge when: the min/max range is not meaningful or stable (the arc shape implies a fixed scale); when you have more than one metric to compare (use cards instead); or when canvas real estate is tight (a gauge needs at least 200 × 200 px to be readable). For multiple KPIs in a row, a row of `cardVisual` items with conditional icons is more space-efficient.
<!-- /MANUAL -->

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
- **axis** (3 properties) — documented inline below
- **calloutValue** (10 properties) — documented inline below
- **dataPoint** (2 properties) — documented inline below
- **labels** (9 properties) — documented inline below
- **target** (9 properties) — documented inline below

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

### axis

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `max` | `number` | — | — | no | Max |
| `min` | `number` | — | — | no | Min |
| `target` | `number` | — | — | no | Target |

### calloutValue

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `bold` | `boolean` | — | — | no | Bold |
| `color` | `object` | — | — | no |  |
| `dynamicLabelValue` | `string | number | integer | boolean` | — | — | no | Field |
| `fontFamily` | `string` | — | — | no | Font family |
| `italic` | `boolean` | — | — | no | Italic |
| `labelDisplayUnits` | `integer` | — | — | no |  |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `show` | `boolean` | — | — | no | Show |
| `showDynamicLabels` | `boolean` | — | — | no | Custom label |
| `underline` | `boolean` | — | — | no | Underline |

### dataPoint

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `fill` | `object` | — | — | no | Fill color for data points. Supports conditional formatting via a measure returning theme color tokens ('good', 'bad', 'neutral') for per-bar/per-slice coloring. |
| `target` | `object` | — | — | no | Target value for data points (used in bullet charts and KPI visuals). |

### labels

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `bold` | `boolean` | — | — | no | Bold |
| `color` | `object` | — | — | no | Font color for data labels. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for data labels. |
| `italic` | `boolean` | — | — | no | Italic |
| `labelDisplayUnits` | `integer` | — | — | no | Display units for data label values (Auto, Thousands, Millions, Billions). |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `show` | `boolean` | — | — | no | Show |
| `underline` | `boolean` | — | — | no | Underline |

### target

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `bold` | `boolean` | — | — | no | Bold |
| `color` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `labelDisplayUnits` | `integer` | — | — | no |  |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `show` | `boolean` | — | — | no | Show |
| `underline` | `boolean` | — | — | no | Underline |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "gauge",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->

### Primary role name ambiguity (`Y` vs `Value`)

The research corpus (stream-3, §5) lists the gauge primary role as `Y` with sub-roles `MinValue`, `MaxValue`, `TargetValue`. The stub file above shows `Value`. The actual role name depends on the report's schema version and how the PBIR was generated. Always verify from an existing gauge in your specific report before binding:

```python
import json, pathlib
for d in pathlib.Path('Report/definition/pages').rglob('visual.json'):
    data = json.loads(d.read_text(encoding='utf8'))
    if data.get('visual',{}).get('visualType') == 'gauge':
        print(list(data['visual']['query']['queryState'].keys()))
        break
```

### Default min/max is 0 to 2× the current value

If `MinValue` and `MaxValue` are not bound, Power BI defaults the gauge arc to 0–(2×Value). This means the needle always points to approximately the midpoint regardless of how "good" or "bad" the value is. Always bind explicit `MinValue` and `MaxValue` for any production gauge.

### `calloutValue` container controls the numeric display, not `value`

Unlike `cardVisual` which uses `value` and `layout.calloutSize`, the gauge uses a `calloutValue` container for the number below the arc. Do not confuse with `cardVisual` formatting patterns — they are different containers.

### Space cost is high

A gauge requires roughly 200 × 200 px minimum to be readable. At smaller sizes the arc becomes thin and the needle/tick marks overlap. For space-constrained layouts, prefer a `cardVisual` with a conditional icon or an SVG bullet chart.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-gauge](../GUIDE.md#cli-gauge)
- JSON format: [../GUIDE.md#json-gauge](../GUIDE.md#json-gauge)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Research corpus §5 — gauge roles (`Y`, `MinValue`, `MaxValue`, `TargetValue`).
- docs/design-principles/cards-and-kpis.md — gauge vs. card design tradeoffs.
- `kpi.md` — for KPI with trend line; often preferable to gauge when time-series context is available.
<!-- /MANUAL -->
