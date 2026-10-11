# pivotTable

## Overview

<!-- MANUAL: overview -->
`pivotTable` is the **matrix** visual type (the PBIR token `pivotTable` maps to the "Matrix" UI label). It supports row hierarchies, column hierarchies, and multiple measures in the Values role, with hierarchical stepping, subtotals, and drill paths. It is the default choice for tabular reporting where rows represent an aggregation dimension and columns optionally pivot a second dimension (e.g. months, entities, scenarios).
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `pivotTable` when:

- The report surface is a **structured breakdown**: portfolio × asset class, entity × scenario, currency × period.
- Users expect **subtotals and grand totals** at row-hierarchy boundaries.
- **Drill-down** into a row hierarchy is required (parent → child levels).
- Multiple measures need to be shown side-by-side per row.

Prefer `tableEx` when: no row hierarchy is needed (a flat grid is sufficient), or rows are purely transactional. Prefer a `lineChart` / `barChart` when the primary signal is a trend or comparison rather than precise values. Prefer a Deneb heatmap when columns are highly dense and cell colouring is the main signal.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Columns` | no | yes | Column |
| `Rows` | no | yes | Column |
| `Values` | yes | yes | Measure |

## Containers

- **background** (3 properties) — see [./_shared/containers-background.md](./_shared/containers-background.md)
- **border** (4 properties) — see [./_shared/containers-border.md](./_shared/containers-border.md)
- **divider** (5 properties) — documented inline below
- **dropShadow** (9 properties) — documented inline below
- **general** (2 properties) — see [./_shared/containers-general.md](./_shared/containers-general.md)
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
- **accessibility** (1 properties) — documented inline below
- **annotationTemplate** (1 properties) — documented inline below
- **blankRows** (8 properties) — documented inline below
- **columnFormatting** (10 properties) — documented inline below
- **columnHeaders** (16 properties) — documented inline below
- **columnTotal** (8 properties) — documented inline below
- **columnWidth** (1 properties) — documented inline below
- **grid** (13 properties) — documented inline below
- **rowHeaders** (22 properties) — documented inline below
- **rowTotal** (8 properties) — documented inline below
- **sparklines** (7 properties) — documented inline below
- **subTotals** (18 properties) — documented inline below
- **total** (8 properties) — documented inline below
- **values** (20 properties) — documented inline below

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

### annotationTemplate

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `highlight` | `object` | — | — | no |  |

### blankRows

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `blankRowColor` | `object` | — | — | no |  |
| `blankRowTransparency` | `number` | — | — | no | Transparency |
| `borderColor` | `object` | — | — | no |  |
| `borderPosition` | `string` | — | — | no | Position |
| `borderTransparency` | `number` | — | — | no | Transparency |
| `borderWidth` | `number` | — | — | no | Width |
| `showBlankRows` | `boolean` | — | — | no | Show |
| `showBorder` | `boolean` | — | — | no | Show |

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
| `styleSubtotals` | `boolean` | — | — | no | Apply to subtotals |
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
| `titleAlignment` | `string` | — | — | no | Title alignment |
| `underline` | `boolean` | — | — | no | Underline |
| `urlIcon` | `boolean` | — | — | no | Show an icon instead of the full URL |
| `wordWrap` | `boolean` | — | — | no | Word wrap |

### columnTotal

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `applyToHeaders` | `boolean` | — | — | no | Apply to labels |
| `backColor` | `object` | — | — | no | Background color for column total cells in matrices. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no | Font color for column total text. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for column total text. |
| `italic` | `boolean` | — | — | no | Italic |
| `underline` | `boolean` | — | — | no | Underline |

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

### rowHeaders

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `alignment` | `string` | — | — | no | Alignment |
| `backColor` | `object` | — | — | no | Background color for row header cells in matrices. |
| `bold` | `boolean` | — | — | no | Bold |
| `expandCollapseButtonsColor` | `object` | — | — | no | Color of expand/collapse +/- buttons in matrix row headers. |
| `expandCollapseButtonsSize` | `number` | — | — | no | Size of expand/collapse buttons in matrix row headers. |
| `expandCompositeHierarchy` | `boolean` | — | — | no | Expand composite hierarchy levels in matrix row headers. |
| `fontColor` | `object` | — | — | no | Font color for row header text. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for row header text. |
| `italic` | `boolean` | — | — | no | Italic |
| `legacyStyleDisabled` | `boolean` | — | — | no | Disable legacy styling mode for row headers. |
| `outlineColor` | `object` | — | — | no | Color of the outline around row header cells. |
| `outlineStyle` | `integer` | — | — | no | Outline |
| `outlineWeight` | `number` | — | — | no | Thickness of the outline in pixels |
| `repeatRowHeaders` | `boolean` | — | — | no | Repeat row headers |
| `showExpandCollapseButtons` | `boolean` | — | — | no | +/- icons |
| `stepped` | `boolean` | — | — | no | Render row headers with stepped layout |
| `steppedLayoutIndentation` | `number` | — | — | no | Set the indentation, in pixels, applied to row headers |
| `underline` | `boolean` | — | — | no | Underline |
| `unfrozen` | `boolean` | — | — | no | Allow row headers to scroll horizontally instead of staying frozen/pinned. |
| `urlIcon` | `boolean` | — | — | no | Show an icon instead of the full URL |
| `wordWrap` | `boolean` | — | — | no | Word wrap |

### rowTotal

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `applyToHeaders` | `boolean` | — | — | no | Apply to labels |
| `backColor` | `object` | — | — | no | Background color for row total cells in matrices. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no | Font color for row total text. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for row total text. |
| `italic` | `boolean` | — | — | no | Italic |
| `underline` | `boolean` | — | — | no | Underline |

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

### subTotals

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `applyToHeaders` | `boolean` | — | — | no | Apply to labels |
| `backColor` | `object` | — | — | no | Background color for subtotal row cells in matrices. |
| `bold` | `boolean` | — | — | no | Bold |
| `columnSubtotals` | `boolean` | — | — | no | Show subtotals for all column groups |
| `columnSubtotalsLabel` | `string` | — | — | no | Column subtotals label |
| `fontColor` | `object` | — | — | no | Font color for subtotal text. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for subtotal text. |
| `italic` | `boolean` | — | — | no | Italic |
| `levelSubtotalEnabled` | `boolean` | — | — | no | Enable subtotals at specific hierarchy levels in matrices. |
| `levelSubtotalLabel` | `string` | — | — | no | Custom label text for level subtotals. |
| `perColumnLevel` | `boolean` | — | — | no | Per column level |
| `perRowLevel` | `boolean` | — | — | no | Per row level |
| `rowSubtotals` | `boolean` | — | — | no | Show subtotals for all row groups |
| `rowSubtotalsLabel` | `string` | — | — | no | Row subtotals label |
| `rowSubtotalsPosition` | `string` | — | — | no | Row subtotal position |
| `underline` | `boolean` | — | — | no | Underline |

### total

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `applyToHeaders` | `boolean` | — | — | no | Apply to labels |
| `backColor` | `object` | — | — | no | Background color for total/subtotal row cells. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no | Font color for total/subtotal row text. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for total/subtotal row text. |
| `italic` | `boolean` | — | — | no | Italic |
| `underline` | `boolean` | — | — | no | Underline |

### values

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backColor` | `object` | — | — | no | Background color for data value cells in tables/matrices. |
| `backColorPrimary` | `object` | — | — | no | Primary alternating row background color for banded rows in tables/matrices. |
| `backColorSecondary` | `object` | — | — | no | Secondary alternating row background color for banded rows in tables/matrices. |
| `bandedRowHeaders` | `boolean` | — | — | no | Apply banded row style to the last level of the row group headers, using the colors of the values. |
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
| `valuesOnRow` | `boolean` | — | — | no | Show values in row groups rather than columns |
| `webURL` | `string` | — | — | no | Web URL |
| `wordWrap` | `boolean` | — | — | no | Word wrap |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "pivotTable",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — ANALYSIS - OWNERSHIP SIMULATION V3 CLEAN (visual impact-ranking-pivotTable-969a74)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Rows` = `[Entity Name]` (single-level); `Values` = 5 measures — `[Net Asset Value]`, `[Net Asset Value %Share]`, `[Net Asset Value (Simul Switch)]`, `[Net Asset Value Δ (Simulation)]`, `[Net Asset Value Δ% (Simulation)]`. Two scenario-delta measures carry Unicode Δ — author via the `pbir_add_visuals` MCP op to avoid Windows codepage mangling (see CLAUDE.md §4).
- **Position:** `(20, 160) 917×380` — left half of the ownership-simulation analysis page; paired with a line chart at `(957, 160)` on the right.
- **vCO title:** `"Impact Ranking"` — names the ordering, not the content.
- **Objects:** `values`, `columnHeaders`, `rowHeaders`, `rowTotal`, `columnTotal`, `grid` — the classic full-chrome matrix set.
- **Flat rows + wide measures** is the right shape for this dataset — ranking entities by how much the simulation moved their NAV.
<!-- /MANUAL -->

### R0101 — RECO - INSTRUMENT DATA (visual a2c51b73c818a6e67d35)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Rows` = `[Asset Label]` → `[Asset Instrument Label]` (2-level hierarchy); `Values` = 1 measure — `[F Hiku - Data Completeness]`. Minimal-value matrix, used purely as a completeness audit grid.
- **Position:** `(≈2.6, ≈165.6) 963×314` — coords are sub-pixel from a save-round — normal artefact; treat as `(2, 166) 963×314` for layout math.
- **vCO title:** `"Data Completeness"`.
- **Objects:** `columnHeaders`, `columnFormatting`, `columnWidth`, `rowHeaders`, `subTotals`, `grid`, `general`, `values` — notably `subTotals` is configured (parent-rows roll up); also has `columnFormatting` to drive per-column CF on the completeness score.
- **Full vCO chrome** (`background`, `border`, `dropShadow`, `title`, `padding`, `visualHeader`, `spacing`, `visualTooltip`, `general`) — the RECO page uses a consistent card chrome across all visuals; copy-paste this vCO block when adding sibling matrices.
- **Pattern:** single-measure matrices are rare but justified when the measure is ordinal (completeness %) and the point is to see which rows fall below threshold.
<!-- /MANUAL -->

### R0101 — PORFTOLIO - INSTRUMENT BREAKDOWN (visual 9c3e638a9a273a697eb0)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Rows` = `[Bank Asset Manager]` → `[Asset Label]` (2-level); `Values` = **17 measures** — NAV + %Share + NAV Growth + 3 performance attribution measures (organic, FX effect, total) + instrument counts + non-FO-currency NAV + cash % + top-10 share.
- **Position:** `(0, 0) 1877×499` — full-width matrix filling the top half of the page.
- **vCO title:** `"NAV & Performance by Asset Manager/Asset"` — descriptive, names both the hierarchy and the measure theme.
- **Objects:** adds `blankRows` + `columnFormatting` + `columnWidth` on top of the standard set — the 17-column width needs explicit per-column width tuning, and blank-row suppression is on to hide the leaf where the parent hierarchy level has no data.
- **Full chrome** + `rowTotal` and `columnTotal` — grand totals visible at bottom and right.
- **Density note:** 17 measures × 2-level hierarchy on 1877px wide is near the density ceiling for pivotTable. Any wider/denser and it belongs in a `tableEx` with column conditional formatting instead.
<!-- /MANUAL -->

### R0101 — PORFTOLIO - INSTRUMENT BREAKDOWN (visual a932b7e74d1812022c76)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Rows` = `[Asset Instrument Label]` (flat, single-level); `Values` = 11 measures ending with `SparklineData([F Hiku - Time Specific - Sparkline_[d Date.Date]])` — an embedded sparkline via the `SparklineData()` DAX function. Closing Price + NAV + growth/performance + popularity + avg NAV per portfolio + sparkline.
- **Position:** `(0, 450) 1880×500` — bottom half of the instrument-breakdown page; sibling to the 9c3e638… matrix above at y=0.
- **vCO title:** `"NAV & Performance by Asset Manager/Asset"` — shared with the upper matrix (both matrices on this page share the same title chrome).
- **Objects:** full standard set plus `total` + `subTotals` + `rowTotal` — three levels of aggregation on a flat-row matrix, driven by the row grouping in measures, not the Rows hierarchy.
- **Sparkline column:** demonstrates the inline sparkline pattern — the `SparklineData(measure_[date_col])` wrapper in the query is how pivotTable renders mini line charts per row. For other visuals use an SVG-by-measure pattern instead.
- **Pair with 9c3e638…:** these two matrices share title + chrome and form the top/bottom halves of INSTRUMENT BREAKDOWN. Keep them structurally consistent.
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### Column headers require BOTH `backColor` AND `fontColor`

A common failure mode is setting `columnHeaders[0].properties.backColor` to a dark theme colour without also setting `fontColor`. The default column-header font is dark grey, so a dark header strip renders invisible dark-on-dark text. Setting only `fontColor` produces the mirrored failure: white text on a light theme-default header, also invisible.

Always set both together and verify against the worst-case theme background. A working baseline:

```json
"columnHeaders": [{
  "properties": {
    "fontSize": {"expr": {"Literal": {"Value": "11D"}}},
    "bold": {"expr": {"Literal": {"Value": "true"}}},
    "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#FFFFFF'"}}}}},
    "backColor": {"solid": {"color": {"expr": {"ThemeDataColor": {"ColorId": 0, "Percent": 0}}}}},
    "alignment": {"expr": {"Literal": {"Value": "'Center'"}}},
    "outlineStyle": {"expr": {"Literal": {"Value": "4D"}}},
    "outlineWeight": {"expr": {"Literal": {"Value": "1D"}}}
  }
}]
```

### Totals are shown by default — hide them explicitly when not wanted

`rowTotal[0].properties.rowSubtotals` and `columnTotal[0].properties.columnSubtotals` default to showing subtotals. Explicitly set both to `"false"` to get a clean breakdown matrix without trailing total rows/columns.

### Row/values styling

The three standard container blocks (`columnHeaders`, `rowHeaders`, `values`) each need coherent `outlineStyle`, `outlineWeight`, and `fontSize`. Grid styling lives in `grid[0].properties` — `gridHorizontal: "true"`, `gridVertical: "false"` produces a horizontal-ruled table that reads best for financial data.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-pivottable](../GUIDE.md#cli-pivottable)
- JSON format: [../GUIDE.md#json-pivottable](../GUIDE.md#json-pivottable)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- docs/design-principles/density.md — matrix density and column-count budgets.
- docs/design-principles/chart-selection.md — when to pick pivotTable over tableEx or bar/line charts.
- docs/GUIDE.md#json-pivottable — field binding (`Rows` / `Columns` / `Values`) and subtotal configuration.
<!-- /MANUAL -->
