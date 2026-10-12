# donutChart

## Overview

<!-- MANUAL: overview -->
`donutChart` is the ring variant of a pie chart — a single categorical field drives slice allocation, a single measure drives slice size. The centre hole allows an overlaid total or KPI text, which is the main reason to prefer it over `pieChart`. Both chart types share the same weakness: humans are poor at comparing angular areas, so donuts should be reserved for at-a-glance part-of-whole framing, not precise comparison.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `donutChart` when:

- Showing a **composition of a single total** (asset allocation, fee breakdown, currency exposure) where 3–6 categories dominate.
- You want a **centre label** for the total (e.g. "€128 M NAV" in the ring's hole).
- The audience needs to grasp "roughly half is X, a third is Y" at a glance, not compare slices precisely.

Prefer a `barChart` or `columnChart` whenever precise comparison matters, or when there are more than ~6 categories. Prefer a **waffle/grid** (Deneb) for exact part-of-whole with many categories. Never use a donut for trend over time.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Category` | yes | no | Column |
| `Legend` | no | no | Column |
| `Values` | yes | no | Measure |

> **R0101 variant:** some report templates (notably R0101) bind the value
> measure to a role called `Y` instead of `Values`. Both role names are
> accepted by `pbir bind`; inspect an existing same-type visual on the
> target report before authoring a new one.

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
- **annotationTemplate** (1 properties) — documented inline below
- **dataPoint** (9 properties) — documented inline below
- **labels** (14 properties) — documented inline below
- **legend** (10 properties) — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **slices** (2 properties) — documented inline below

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
| `fillTransparency` | `number` | — | — | no | Transparency |

### labels

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `background` | `string` | — | — | no | Background |
| `bold` | `boolean` | — | — | no | Bold |
| `color` | `object` | — | — | no | Font color for data labels. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for data labels. |
| `italic` | `boolean` | — | — | no | Italic |
| `labelDisplayUnits` | `integer` | — | — | no | Display units for data label values (Auto, Thousands, Millions, Billions). |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `labelStyle` | `string` | — | — | no | Label style |
| `overflow` | `boolean` | — | — | no | Overflow text |
| `percentageLabelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the percentages |
| `position` | `string` | — | — | no | Label position |
| `show` | `boolean` | — | — | no | Show |
| `underline` | `boolean` | — | — | no | Underline |

### legend

See [./_shared/containers-legend.md](./_shared/containers-legend.md).

### slices

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `innerRadiusRatio` | `integer` | — | — | no | Inner radius |
| `startAngle` | `integer` | — | — | no | Start angle |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "donutChart",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — PORFTOLIO - INSTRUMENT BREAKDOWN (visual 3e394d240063a3362b92)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Category` = `[Asset Class]`; `Y` = `[Net Asset Value]`. Note the role is `Y`, NOT `Values` (see Gotchas).
- **Position:** `(475, 121) 458×316` — top-middle section of the breakdown page.
- **vCO title:** `"Instrument Breakdown by Asset Class"` with a long trailing run of thin-space + regular-space characters — this is a layout hack to visually centre the short title across the chart's legend area. Don't trim the trailing spaces.
- **Minimal objects:** only `labels` + `slices` — this is the stripped-down donut; no `legend` or `dataPoint` overrides.
- **Minimal vCO:** only `title` + `border`. No background or dropShadow — the donut sits on the page canvas directly.
- **Extract is only 237 lines** — this is the cleanest template for a standalone donut on R0101. Use it as the base when creating a new one.
<!-- /MANUAL -->

### R0101 — DIR PARTICIPATIONS - DETAIL (visual 2069adf85e7ba6a735a1)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Category` = `[Share Type]`; `Y` = `[Net Asset Value - Multi-asset/instrument currency behaviour]`.
- **Position:** `(≈0.8, 0) 460×370` — left column top donut; paired with the geography donut below at `(0, 390)` and the vintage donut on PRIVATE FUNDS.
- **Full chrome:** `legend`, `dataPoint`, `labels` objects + full vCO stack (`title`, `background`, `dropShadow`, `border`, `padding`, `visualHeader`, `visualTooltip`) — theme-compliant card rendering, heavier than the breakdown page's minimal donut.
- **Measure pattern:** the "Multi-asset/instrument currency behaviour" suffix signals a currency-aware NAV that switches denominators based on the selected currency context — this matters for the donut because percentage allocation depends on the chosen denominator.
- **Pair with b8e4917…:** sibling donut on the same page stacking share-type (top) over geography (bottom) at matching 460×370 footprint. Keep both sizes identical.
<!-- /MANUAL -->

### R0101 — DIR PARTICIPATIONS - DETAIL (visual b8e4917a6f03f2137dde)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Category` = `[Geography]`; `Y` = same currency-aware NAV measure as 2069adf…. Pair with share-type donut above.
- **Position:** `(0, 390) 460×370` — directly below 2069adf… at matching footprint; exact 20 px gap (390 - 0 - 370).
- **Identical objects + vCO stack** to 2069adf… — copy-paste from its sibling when authoring.
- **Pattern:** on DIR PARTICIPATIONS - DETAIL the two donuts form a 2-up allocation strip (share-type / geography). Any additional allocation donut should go on a separate page section — 3 donuts in a column starts to feel redundant and the reader stops comparing.
<!-- /MANUAL -->

### R0101 — PRIVATE FUNDS - DETAIL (visual 5d3da0296c29295c0e48)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Category` = `[Vintage]` (from `d Fund Vintage`); `Y` = same currency-aware NAV measure.
- **Position:** `(0, 0) 460×307` — slightly shorter height (307 vs 370) compared to the DIR PARTICIPATIONS donuts — this is the first donut of the PRIVATE FUNDS detail page.
- **Same full-chrome stack** as the DIR PARTICIPATIONS pair.
- **Vintage is an ordered category** (2018, 2019, 2020 …) — a donut loses the ordering, so this is a design choice to emphasise composition over time-ordering. If the user needs to see year-over-year fund build-up, a stacked column chart is the better pick.
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### Role name: `Y` in R0101, `Values` in generic docs

Generic Power BI documentation lists `Values` as the donut's measure role. R0101 donuts use **`Y`**. Role names diverge across reports and PBI versions; always verify against an existing same-type visual before binding a new donut. One-liner:

```python
import json, pathlib
for d in pathlib.Path('Report/definition/pages').rglob('visual.json'):
    data = json.loads(d.read_text(encoding='utf8'))
    if data.get('visual', {}).get('visualType') == 'donutChart':
        print(d.parent.parent.name, list(data['visual']['query']['queryState'].keys()))
        break
```

A mismatched role yields a silently-blank visual — no error, just nothing rendered.

### Donut is rarely the best choice for share-of-total

Per `design-principles/chart-selection.md`, bar is the default for share-of-total comparisons (Cleveland-McGill #1 position on common scale beats donut's #4 angle). Donut is allowed when all three hold: ≤ 3-4 slices **AND** page needs visual variety **AND** precision doesn't matter. Otherwise use a bar chart.

### Per-slice colour via `dataPoint`

Per-category colours go under `visual.objects.dataPoint[0].properties.fill` with one entry per category selector. Avoid manually mapping colours unless the theme's `dataColors` cycle doesn't match the business semantics (e.g. you need red for "Loss" and green for "Gain" specifically).
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-donutchart](../GUIDE.md#cli-donutchart)
- JSON format: [../GUIDE.md#json-donutchart](../GUIDE.md#json-donutchart)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- docs/design-principles/chart-selection.md — part-of-whole decision tree (donut vs bar vs waffle).
- docs/design-principles/color.md — donut palette constraints (3–6 categories, colour-blind-safe pairs).
- **Role gotcha:** R0101 donuts bind to `Y` (not `Values`); always verify against an existing same-type visual before authoring a new one — see CLAUDE.md §"PBIR Role Names — Always Verify From Existing Visuals".
<!-- /MANUAL -->
