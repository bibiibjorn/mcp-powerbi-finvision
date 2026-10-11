# tableEx

## Overview

<!-- MANUAL: overview -->
`tableEx` is the modern (enhanced) table visual — the flat grid counterpart to `pivotTable` (matrix). All fields go into a single `Values` role; there is no row or column hierarchy. Each row in the underlying data becomes one row in the grid, with each field binding becoming a column. It is the default pick for "show me the rows" reporting surfaces.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `tableEx` when:

- The surface is **transactional** — one row per trade, position, client, holding.
- There is **no hierarchy** to aggregate over; users want flat rows.
- You need **conditional formatting** on specific columns (data bars, icons, background scales).
- The report acts as a **detail drill** of a higher-level chart or KPI card.

Prefer `pivotTable` whenever: rows aggregate over a hierarchy with subtotals, or columns pivot a second dimension. Prefer a chart (`barChart`, `columnChart`) if the reader mainly needs to compare one measure across a short category list — tables force the eye to scan, which is slower than bar-height comparison.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Values` | yes | yes | Column, Measure |

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
- **accessibility** (2 properties) — documented inline below
- **clustering** (0 properties) — documented inline below
- **columnFormatting** (9 properties) — documented inline below
- **columnHeaders** (14 properties) — documented inline below
- **columnWidth** (1 properties) — documented inline below
- **grid** (13 properties) — documented inline below
- **sparklines** (7 properties) — documented inline below
- **total** (12 properties) — documented inline below
- **values** (18 properties) — documented inline below

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

### accessibility

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `altTextColumns` | `string` | — | — | no | Column with alt text |
| `rowWithReferenceText` | `string` | — | — | no | Refer to row by |

### clustering

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|

### columnFormatting

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `alignment` | `string` | — | — | no | Alignment |
| `backColor` | `object` | — | — | no | Background color for a specific column in tables/matrices (requires field selector). |
| `dataBars` | `object` | — | — | no | Data bars configuration for in-cell bar visualization in tables/matrices. |
| `fontColor` | `object` | — | — | no | Font color for a specific column (requires field selector). Supports conditional formatting. |
| `labelDisplayUnits` | `integer` | — | — | no | Display units for a specific column (Auto, Thousands, Millions, Billions). |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `styleHeader` | `boolean` | — | — | no | Apply to header |
| `styleTotal` | `boolean` | — | — | no | Apply to total |
| `styleValues` | `boolean` | — | — | no | Apply to values |

### columnHeaders

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `alignment` | `string` | — | — | no | Alignment |
| `autoSizeColumnWidth` | `boolean` | — | — | no | Auto-size column width |
| `backColor` | `object` | — | — | no | Background color for column header cells. |
| `bold` | `boolean` | — | — | no | Bold |
| `columnAdjustment` | `string` | — | — | no | Controls the resize behavior for columns without fixed width |
| `fontColor` | `object` | — | — | no | Font color for column header text. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for column header text (6-45pt). |
| `italic` | `boolean` | — | — | no | Italic |
| `outlineColor` | `object` | — | — | no | Color of the outline/border around column headers. |
| `outlineStyle` | `integer` | — | — | no | Outline |
| `outlineWeight` | `number` | — | — | no | Thickness of the outline in pixels |
| `underline` | `boolean` | — | — | no | Underline |
| `wordWrap` | `boolean` | — | — | no | Word wrap |

### columnWidth

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `value` | `number` | — | — | no | Fixed column width in pixels. Only takes effect when columnHeaders.autoSizeColumnWidth is set to false. |

### grid

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `gridHorizontal` | `boolean` | — | — | no | Show/Hide the horizontal gridlines |
| `gridHorizontalColor` | `object` | — | — | no | Color of horizontal gridlines. |
| `gridHorizontalWeight` | `number` | — | — | no | Thickness of the horizontal gridlines in pixels |
| `gridVertical` | `boolean` | — | — | no | Show/Hide the vertical gridlines |
| `gridVerticalColor` | `object` | — | — | no | Color of vertical gridlines. |
| `gridVerticalWeight` | `number` | — | — | no | Thickness of the vertical gridlines in pixels |
| `imageHeight` | `number` | — | — | no | The height of images in pixels |
| `imageWidth` | `number` | — | — | no | The width of images in pixels |
| `outlineColor` | `object` | — | — | no | Color of the outer border/outline around the grid. |
| `outlineStyle` | `integer` | — | — | no | Outline |
| `outlineWeight` | `number` | — | — | no | Thickness of the outline in pixels |
| `rowPadding` | `number` | — | — | no | Padding in pixels applied to top and bottom of every row |
| `textSize` | `number` | — | — | no | Text size |

### sparklines

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `chartType` | `string` | — | — | no | Chart type |
| `dataColor` | `object` | — | — | no | Color of sparkline mini-charts embedded in table/matrix cells. |
| `markerColor` | `object` | — | — | no | Color of data point markers on sparklines. |
| `markerShape` | `string` | — | — | no | Type |
| `markerSize` | `number` | — | — | no | Size |
| `markers` | `integer` | — | — | no | Show these markers |
| `strokeWidth` | `number` | — | — | no | Width |

### total

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backColor` | `object` | — | — | no | Background color for total/subtotal row cells. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no | Font color for total/subtotal row text. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for total/subtotal row text. |
| `italic` | `boolean` | — | — | no | Italic |
| `label` | `string` | — | — | no | Total label |
| `outlineColor` | `object` | — | — | no | Color of the outline around total row cells. |
| `outlineStyle` | `integer` | — | — | no | Outline |
| `outlineWeight` | `number` | — | — | no | Thickness of the outline in pixels |
| `totals` | `boolean` | — | — | no | Totals |
| `underline` | `boolean` | — | — | no | Underline |

### values

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backColor` | `object` | — | — | no | Background color for data value cells in tables/matrices. |
| `backColorPrimary` | `object` | — | — | no | Primary alternating row background color for banded rows in tables/matrices. |
| `backColorSecondary` | `object` | — | — | no | Secondary alternating row background color for banded rows in tables/matrices. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no | Font color for data values in tables/matrices. |
| `fontColorPrimary` | `object` | — | — | no | Primary font color for alternating rows. |
| `fontColorSecondary` | `object` | — | — | no | Secondary font color for alternating rows. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for data values. |
| `icon` | `object` | — | — | no | Icon indicator for data values (used in KPI and scorecard visuals). |
| `italic` | `boolean` | — | — | no | Italic |
| `outlineColor` | `object` | — | — | no | Color of the outline/border around value cells. |
| `outlineStyle` | `integer` | — | — | no | Outline |
| `outlineWeight` | `number` | — | — | no | Thickness of the outline in pixels |
| `underline` | `boolean` | — | — | no | Underline |
| `urlIcon` | `boolean` | — | — | no | Show an icon instead of the full URL |
| `webURL` | `string` | — | — | no | Web URL |
| `wordWrap` | `boolean` | — | — | no | Word wrap |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "tableEx",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — LENDINGS - OVERVIEW (visual c575758c931ffbfae3f0)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = 13 fields — mix of 4 categorical columns (`Valuation Group`, `Valuation Type`, `Asset Label`, `Asset Currency`) and 9 measures (`Net Asset Value` + 2 %Share variants + `Loan Amount`, `Credit Limit`, `Interest Rate`, `Guarantees`, `Maturity Date`, `Endebted Asset`). All in the single flat `Values` role — no grouping.
- **Position:** `(0, 181) 1880×400` — full-width, spans bottom of the LENDINGS overview page.
- **vCO title:** (none visible in title-text probe, but `title` is in the vCO keys) — minimal title, likely the theme default.
- **Objects:** `columnWidth`, `grid`, `columnHeaders`, `values`, `columnFormatting` — the standard flat-table set, explicit `columnWidth` for per-column width tuning (13 columns on 1880px means ~145px each; width tuning is essential).
- **Full vCO chrome** with `spacing`, `padding`, `general`, `border`, `dropShadow`, `visualTooltip`, `visualHeader` — matches the rest of the LENDINGS page cards.
- **Pattern:** mixing dimensional columns early and measure columns late matches reading order — users scan row context (who / what) then numeric values. Keep this column ordering convention.
<!-- /MANUAL -->

### R0101 — PORTFOLIO - ASSET MANAGER (visual 7f41724514ea6918c738)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = 14 fields — 2 categorical (`Asset Label`, `Bank Asset Manager`) + 12 measures. Same NAV + perf + organic/FX/realised + instrument counts as the instrument-breakdown pivotTable, but **flat** (no row hierarchy).
- **Position:** `(0, ≈453) 1880×498` — full-width, bottom half of the ASSET MANAGER page.
- **vCO title:** `"NAV & Performance by Asset Manager/Asset"` — shared with the pivotTable sibling on the same page. This is the classic "detail table under summary matrix" pattern.
- **Objects:** `values`, `grid`, `columnWidth`, `columnHeaders`, `columnFormatting` — minimal compared to the pivotTable counterpart.
- **Pattern:** same measures, two visual types on one page — the pivotTable aggregates by `[Bank Asset Manager]`, the tableEx shows leaf `[Asset Label]` rows. Users use the matrix for overview + drill to the table for exact rows.
<!-- /MANUAL -->

### R0101 — CURRENCY EXPOSURE (visual c97d3ff54bdecd970312)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = 8 fields — `[Instrument Currency]`, `[Asset Instrument Label]` (2 categorical) + 6 measures mixing NAV, %Share (Inv. Prtf. + Global), Total Performance (Organic / FX effect) and `[00_CurrencyFlow % of FX (Non-Base)]`. Cross-currency breakdown.
- **Position:** `(0, 443) 1107×432` — narrower than the full-width tables above (1107 vs 1880); pairs with another visual on the right.
- **vCO title:** `"Foreign Crcy Exposure by Instrument Labelㅤ"` — Hangul-filler trailing character for title-centring.
- **Objects:** `grid`, `columnHeaders`, `values`, `columnFormatting`, `columnWidth` — standard flat-table set.
- **vCO includes `subTitle`** — rare in R0101; this table has both a title and a subtitle (typically the slicer state summary).
- **Measure naming tip:** `[00_CurrencyFlow % of FX (Non-Base)]` uses the `00_` prefix to keep it first in alphabetical measure lists — this is how R0101 signals "system-use measure, pinned to top".
<!-- /MANUAL -->

### R0101 — LENDINGS - CARD (visual d6a07c0db2e7ddb2fc7d)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = 4 fields — `[Month Year (MMMYY)]`, `[ValTrans Currency]`, `[bsum_Closing Amount in valtrans currency]`, `[bsum_Closing Amount in selected currency]`. Compact 2+2 pattern: 2 dim columns (date + currency) + 2 measures.
- **Position:** `(0, 0) 800×672` — narrow + tall (4:6 aspect). Left-edge panel, the tallest table in the catalog.
- **vCO title:** `"Valuations2"` — informal naming suggests this is a development placeholder; worth renaming to `"Monthly Valuations"` or similar before shipping.
- **Objects:** standard 6 + `total` — grand totals row at the bottom. Different from the no-totals convention of other tableEx visuals.
- **Measure prefix:** `bsum_` suggests "base-sum" — measures pre-summed at the fact-row level. This is a performance pattern for large tables; for smaller aggregations an inline `SUM()` measure is fine.
- **Tall + narrow** = scroll-heavy; users read top-down through months. If you're building something similar, ensure the date column is LEFT-aligned and all measure columns RIGHT-aligned.
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### All fields go into one role — there is no `Columns` / `Rows` split

Unlike `pivotTable`, `tableEx` has a single `Values` role. Every field you want rendered as a column — dimension or measure — goes into that role in the order you want the columns displayed. Trying to create a pseudo-hierarchy by ordering dimension columns first does not produce grouped rows; each data row still shows one line.

### `columnWidth` is essential at 8+ columns

PBI's default column auto-sizing collapses narrow columns and truncates long labels ugly-style. At ≥8 columns on a wide table, set explicit `columnWidth[].properties.value` per column to get predictable rendering. Without it, a resize on one column re-flows every other column unpredictably.

### `columnFormatting` is per-column, selected by `$id`

Conditional formatting (data bars, icons, colour scales, font colours) binds to a `columnFormatting` entry keyed by the column's `$id`, which is the field's `queryRef` string. If you rename a measure and the `queryRef` changes, the columnFormatting entry becomes orphaned and the formatting silently drops — PBI does not warn. After a rename always re-check that every `columnFormatting[].$id` still matches a projection in the `Values` role.

### Total rows toggle is the `total` object, not `rowTotal`

`rowTotal` is a pivotTable-only object. On tableEx, the grand-total row is controlled by a `total` object (with `totalShow`, `totalFontColor`, `totalBackColor` properties inside). Copying an R0101 extract without the `total` block gives you a table with no grand-total row even if you expect one.

### Narrow tables with many rows → consider a matrix with single-row hierarchy

A tall-narrow tableEx (like d6a07c0…'s 800×672) ends up with a vertical scrollbar. If there's a natural grouping (month → currency), a `pivotTable` with a 1-level row hierarchy + subtotals is often easier to scan than a flat tableEx.

### `blankRows` suppression is not on tableEx

The `blankRows` object exists on `pivotTable` only — tableEx has no equivalent. If your measures return BLANK for some rows, those rows still render as empty-cell rows in the table (unlike matrix where you can suppress the whole row). Filter the underlying data at the DAX level if empty rows are unwanted.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-tableex](../GUIDE.md#cli-tableex)
- JSON format: [../GUIDE.md#json-tableex](../GUIDE.md#json-tableex)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- docs/design-principles/density.md — table density budget (rows × columns × characters on a page).
- docs/visual-catalog/pivotTable.md — when rows aggregate over a hierarchy instead of staying flat.
- docs/GUIDE.md#json-tableex — field binding into `Values`, conditional formatting selectors.
<!-- /MANUAL -->
