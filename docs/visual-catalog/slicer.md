# slicer

## Overview

<!-- MANUAL: overview -->
`slicer` is the legacy single-field filter control. One column or measure goes into the `Values` role and the visual renders as a list, dropdown, or range picker (controlled by `mode` on the slicer settings). It is the most compact filter control but cannot express hierarchies or rich search out of the box — for those, use `advancedSlicerVisual`.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `slicer` when:

- A **single field** drives the filter (entity, period, currency).
- You need a **small, fixed set of values** and want them visible at a glance (list or tile mode).
- A **date range picker** or **numeric between** picker is required (set `mode` accordingly).

Prefer `advancedSlicerVisual` when: the user needs **search**, **hierarchical selection**, or a large value set. Prefer a `listSlicer` / `textSlicer` when targeting a specific UX. For scenario selectors (Simul 1 / Simul 2 / Actual), a slicer is the default — but the slicer **must be preserved** when deleting/rebuilding content groups on R0101 pages (see CLAUDE.md §"Audit slicers before deleting content groups").
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Values` | yes | yes | Column |

## Containers

- **background** (3 properties) — see [./_shared/containers-background.md](./_shared/containers-background.md)
- **border** (4 properties) — see [./_shared/containers-border.md](./_shared/containers-border.md)
- **divider** (5 properties) — documented inline below
- **dropShadow** (9 properties) — documented inline below
- **general** (7 properties) — see [./_shared/containers-general.md](./_shared/containers-general.md)
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
- **data** (10 properties) — documented inline below
- **date** (8 properties) — documented inline below
- **dateRange** (2 properties) — documented inline below
- **header** (11 properties) — documented inline below
- **items** (12 properties) — documented inline below
- **numericInputStyle** (7 properties) — documented inline below
- **pendingChangesIcon** (8 properties) — documented inline below
- **searchBox** (3 properties) — documented inline below
- **selection** (3 properties) — documented inline below
- **slider** (5 properties) — documented inline below

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

### data

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `endDate` | `string` | — | — | no | End date value for date slicer range. |
| `isInvertedSelectionMode` | `boolean` | — | — | no | Invert selection -- exclude selected items instead of including them. |
| `mode` | `string` | — | — | no | Style |
| `numericEnd` | `number` | — | — | no | End value for numeric range slicer. |
| `numericStart` | `number` | — | — | no | Start value for numeric range slicer. |
| `relativeDuration` | `number` | — | — | no | Duration for relative date slicer (e.g., last 7 days). |
| `relativePeriod` | `string` | — | — | no | Period unit for relative date slicer (days, weeks, months, years). |
| `relativeRange` | `string` | — | — | no | Range type for relative date slicer. |
| `relativeTimePeriod` | `string` | — | — | no | Time period for relative time slicer. |
| `startDate` | `string` | — | — | no | Start date value for date slicer range. |

### date

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `background` | `object` | — | — | no |  |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `hideDatePickerButton` | `boolean` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `textSize` | `number` | — | — | no |  |
| `underline` | `boolean` | — | — | no | Underline |

### dateRange

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `anchorDate` | `string` | — | — | no | Select a date to reference when filtering visuals by a relative range of time. If an anchor date isn't specified, we'll use the current date. |
| `includeToday` | `boolean` | — | — | no | Include today |

### header

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `background` | `object` | — | — | no |  |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `italic` | `boolean` | — | — | no | Italic |
| `outlineStyle` | `integer` | — | — | no | Outline |
| `show` | `boolean` | — | — | no | Show |
| `showRestatement` | `boolean` | — | — | no | Show summary |
| `text` | `string` | — | — | no | The name of the visual |
| `textSize` | `number` | — | — | no |  |
| `underline` | `boolean` | — | — | no | Underline |

### items

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `accessibilityContrastProperties` | `boolean` | — | — | no |  |
| `background` | `object` | — | — | no |  |
| `bold` | `boolean` | — | — | no | Bold |
| `expandCollapseToggleType` | `integer` | — | — | no | Expand/collapse icon |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `italic` | `boolean` | — | — | no | Italic |
| `outlineStyle` | `integer` | — | — | no | Outline |
| `padding` | `number` | — | — | no | Padding |
| `steppedLayoutIndentation` | `number` | — | — | no | Stepped layout indentation |
| `textSize` | `number` | — | — | no |  |
| `underline` | `boolean` | — | — | no | Underline |

### numericInputStyle

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `background` | `object` | — | — | no |  |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `italic` | `boolean` | — | — | no | Italic |
| `textSize` | `number` | — | — | no |  |
| `underline` | `boolean` | — | — | no | Underline |

### pendingChangesIcon

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no |  |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `showTooltip` | `boolean` | — | — | no | Show tooltip |
| `size` | `number` | — | — | no | Size |
| `tooltipLabel` | `string` | — | — | no | Label text |
| `tooltipText` | `string` | — | — | no | Tooltip text |
| `transparency` | `number` | — | — | no | Background color transparency |

### searchBox

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `background` | `object` | — | — | no |  |
| `borderColor` | `object` | — | — | no |  |
| `outlineStyle` | `integer` | — | — | no | Outline |

### selection

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `selectAllCheckboxEnabled` | `boolean` | — | — | no | Show "Select all" as an option in the slicer. This selects all the values in the slicer so you can unselect them one by one to create an "is not" type filter. |
| `singleSelect` | `boolean` | — | — | no | Allow multiple selections in check boxes only when CTRL or Command + click is used |
| `strictSingleSelect` | `boolean` | — | — | no | Force Selection ensures only one item can be chosen at a time. If no item is selected, the first available will be automatically chosen. |

### slider

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no |  |
| `handleBorderColor` | `object` | — | — | no |  |
| `handleFillColor` | `object` | — | — | no |  |
| `secondaryLineColor` | `object` | — | — | no |  |
| `show` | `boolean` | — | — | no | Show |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "slicer",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — LENDINGS - CARD (visual 54392c32590f22b4312d)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = `[Asset Label]` (single-field). `displayName`/`nativeQueryRef` are overridden to `"Choose an asset:"` — this is the user-facing prompt in the slicer; the underlying column name stays `[Asset Label]`.
- **Position:** `(216, 61) 556×73` — wide, short slicer strip at the top of the LENDINGS - CARD page.
- **No vCO title** — this slicer relies purely on its `displayName` prompt for labelling. Setting both `vCO.title.text` AND `displayName` would be redundant (and probably misaligned).
- **Objects:** `data`, `selection`, `general`, `header`, `items` — the standard 5-object slicer stack.
- **Minimal vCO:** only `background`. No border, no visualHeader — the slicer blends into the card chrome.
- **Pattern:** when a slicer is the page's primary "pick a thing" control, lean on `displayName` as the UI label rather than a vCO title block.
<!-- /MANUAL -->

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
  "name": "54392c32590f22b4312d",
  "position": {
    "x": 215.55555555555554,
    "y": 61.111111111111107,
    "z": 2000,
    "height": 73.333333333333329,
    "width": 555.55555555555554,
    "tabOrder": 1000
  },
  "visual": {
    "visualType": "slicer",
    "query": {
      "queryState": {
        "Values": {
          "projections": [
            {
              "field": {
                "Column": {
                  "Expression": {
                    "SourceRef": {
                      "Entity": "d Assetinstrument"
                    }
                  },
                  "Property": "Asset Label"
                }
              },
              "queryRef": "d Assetinstrument.Asset Label",
              "nativeQueryRef": "Choose an asset:",
              "active": true,
              "displayName": "Choose an asset:"
            }
          ]
        }
      }
    },
    "objects": {
      "data": [
        {
          "properties": {
            "mode": {
              "expr": {
                "Literal": {
                  "Value": "'Dropdown'"
                }
              }
            },
            "isInvertedSelectionMode": {
              "expr": {
                "Literal": {
                  "Value": "true"
                }
              }
            }
          }
        }
      ],
      "selection": [
        {
          "properties": {
            "strictSingleSelect": {
              "expr": {
                "Literal": {
                  "Value": "true"
                }
              }
            },
            "selectAllCheckboxEnabled": {
              "expr": {
                "Literal": {
                  "Value": "true"
                }
              }
            },
            "singleSelect": {
              "expr": {
                "Literal": {
                  "Value": "true"
                }
              }
            }
          }
        },
        {
          "properties": {}
        }
      ],
      "general": [
        {
          "properties": {}
        }
      ],
      "header": [
        {
          "properties": {
            "textSize": {
              "expr": {
                "Literal": {
                  "Value": "9D"
                }
              }
            },
            "bold": {
              "expr": {
                "Literal": {
                  "Value": "true"
                }
              }
            }
          }
        }
      ],
      "items": [
        {
          "properties": {
            "textSize": {
              "expr": {
                "Literal": {
                  "Value": "13D"
                }
              }
            },
            "background": {
              "solid": {
                "color": {
                  "expr": {
                    "Literal": {
                      "Value": "'#F0F0F0'"
                    }
                  }
                }
              }
            },
            "bold": {
              "expr": {
                "Literal": {
                  "Value": "false"
                }
              }
            }
          }
        }
      ]
    },
    "visualContainerObjects": {
      "background": [
        {
          "properties": {
            "show": {
              "expr": {
                "Literal": {
                  "Value": "false"
                }
              }
            }
          }
        }
      ]
    },
    "drillFilterOtherVisuals": true
  },
  "parentGroupName": "8c29ae8b97787060c3d4"
}
```

### R0101 — DIR PARTICIPATIONS - DETAIL (visual 01ac57dcbb86262a9c60)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = `[Month Year (MMMYY)]` + `[Period Date]` — **two columns bound** to the same slicer. The first is the display column (string label), the second is the underlying date used for filter propagation. This is the date-range slicer pattern on R0101.
- **Position:** `(701, ≈0.7) 210×75` — small top-right slicer pinned next to the page header.
- **vCO title:** `"Reporting Month"` — the slicer uses a vCO title (unlike the asset picker on 54392c…).
- **Objects:** standard 5-object stack (`data`, `selection`, `general`, `header`, `items`).
- **Scenario / variance slicer caveat:** R0101 reporting-month slicers live at page level. The scenario slicers (Simul 1 / Simul 2) live inside **content groups** — deleting the group silently deletes the scenario slicer. See CLAUDE.md §"Audit slicers before deleting content groups".
<!-- /MANUAL -->

### R0101 — LENDINGS - CARD (visual 2bd337c27a573ad196bd)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = `[Year Month Nr]` (integer YYYYMM). Numeric slicer — enables slider mode and numeric input.
- **Position:** `(≈594, ≈1) 205×45` — narrow horizontal strip top-right of LENDINGS - CARD page.
- **vCO title:** `"Year Month Nr"` (same as the underlying column name — no renaming via `displayName`).
- **Objects:** extends the standard 5 with `slider`, `numericInputStyle`, `pendingChangesIcon` — these three objects signal **range-picker mode** for numeric data. The slider handles the between-filter; pendingChangesIcon lets the user accept edits without immediately refiring queries.
- **vCO adds `border`** vs 54392c…'s bare-background — tighter visual chrome, consistent with the numeric control being a discrete widget.
<!-- /MANUAL -->

### R0101 — LENDINGS - CARD (visual 797f84048723a617be49)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = `[Transaction Type]` — categorical string column.
- **Position:** `(0, 0) 360×70` — left-edge slicer, top of page.
- **vCO title:** `"Transaction Type"` (direct column name).
- **Objects:** extends the standard 5 with `numericInputStyle` + `slider` even though the bound column is categorical — this is a style-override inheritance quirk in R0101 (these objects are present but have no effect for a string column).
- **vCO includes `border`** — same chrome treatment as 2bd337…, consistent across LENDINGS - CARD slicers.
- **Pattern note:** when rebuilding this page, three slicers sit in a row at the top (asset picker 54392c…, year-month 2bd337…, transaction-type 797f84…). Preserve all three — the page's queries assume each slicer's filter contribution.
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### Scenario/simulation slicers inside content groups get deleted with the group

The Actual / Simul 1 / Simul 2 scenario slicer on R0101 analysis pages lives **inside a content group**, not at page level. Deleting the group to rebuild the content area (via `pbir rm` or `delete_visual` on the group) silently removes the scenario slicer. The page still loads, but every measure that depends on the scenario filter (anything in the `Δ (Simulation)` / Simul Switch family) returns BLANK.

**Rule:** before deleting a content group, list all slicers on the page. Any slicer whose parentGroupName matches the group being deleted must be recreated (with a new GUID) at page level or inside the rebuilt group. See CLAUDE.md §"Audit slicers before deleting content groups" for the audit query.

### `displayName` vs vCO `title` — pick one, not both

Slicers carry TWO candidate labels: `projections[].displayName` (shown as a prompt inside the slicer: "Choose an asset:") and `visualContainerObjects.title.text` (a card title over the slicer). Setting both is redundant and usually visually awkward — the user sees two overlapping labels. The R0101 convention is:

- `displayName` only → compact, prompt-style slicer (see 54392c…'s "Choose an asset:")
- `vCO.title.text` only → card-style slicer with a heading (see 2bd337…'s "Year Month Nr")

### Numeric slicers keep their slider even when bound to a string column

Objects like `slider`, `numericInputStyle`, `pendingChangesIcon` appear in slicer extracts whose `Values` binding is a categorical string (see 797f84…'s Transaction Type). PBI stores the config even when it's not in effect, so copying from a categorical slicer may carry over dead numeric-mode objects. Harmless at render time, but strip them if you need the extract to be minimal.

### Multi-column slicer bindings are legal but rare

The `Reporting Month` slicer on 01ac57… binds **two columns** to `Values` — a display label and an underlying date. This works for date slicers where you want a pretty-printed label (`MMMYY`) but filter on the raw date. Do NOT use this pattern for unrelated columns — the filter semantics become undefined.

### Role name: `Values` (not `Field`)

Some older PBI docs and third-party tutorials call the slicer role `Field`. In PBIR the role is always `Values`. Binding to `Field` writes a dead projection that PBI silently ignores and the slicer renders empty.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-slicer](../GUIDE.md#cli-slicer)
- JSON format: [../GUIDE.md#json-slicer](../GUIDE.md#json-slicer)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- docs/design-principles/interactivity.md — slicer placement, syncSlicers, and cross-page filter conventions.
- docs/visual-catalog/advancedSlicerVisual.md — escalation path for search/hierarchy.
- CLAUDE.md §"pbir CLI — Safety Rules" → "Audit slicers before deleting content groups" — scenario/simulation slicers live inside content groups on R0101 and are silently deleted with the group.
<!-- /MANUAL -->
