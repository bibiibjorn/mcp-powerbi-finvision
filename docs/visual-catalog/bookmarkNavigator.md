# bookmarkNavigator

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

## Containers

- **background** (3 properties) — see [./_shared/containers-background.md](./_shared/containers-background.md)
- **border** (4 properties) — see [./_shared/containers-border.md](./_shared/containers-border.md)
- **divider** (5 properties) — documented inline below
- **dropShadow** (9 properties) — documented inline below
- **general** (8 properties) — see [./_shared/containers-general.md](./_shared/containers-general.md)
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
- **accentBar** (6 properties) — documented inline below
- **bookmarks** (5 properties) — documented inline below
- **fill** (4 properties) — documented inline below
- **glow** (5 properties) — documented inline below
- **layout** (4 properties) — documented inline below
- **outline** (5 properties) — documented inline below
- **rotation** (3 properties) — documented inline below
- **shadow** (8 properties) — documented inline below
- **shape** (20 properties) — documented inline below
- **text** (14 properties) — documented inline below

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

### accentBar

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `color` | `object` | — | — | no |  |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |
| `width` | `number` | — | — | no | Width |

### bookmarks

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `allowDeselectionBookmark` | `boolean` | — | — | no | Allow users to deselect buttons in the bookmark navigator |
| `bookmarkGroup` | `string` | — | — | no | Bookmark |
| `deselectionBookmark` | `string` | — | — | no | Launch this bookmark when a user deselects a button in the bookmark navigator |
| `hideDeselectedBookmark` | `boolean` | — | — | no | Hide the deselection bookmark from the bookmark navigator |
| `selectedBookmark` | `string` | — | — | no |  |

### fill

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `fillColor` | `object` | — | — | no |  |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Background color transparency |

### glow

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `color` | `object` | — | — | no |  |
| `shadowBlur` | `number` | — | — | no | Blur |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### layout

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `cellPadding` | `integer` | — | — | no | Padding |
| `columnCount` | `integer` | — | — | no | Columns |
| `orientation` | `integer` | — | — | no | Orientation |
| `rowCount` | `integer` | — | — | no | Rows |

### outline

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `lineColor` | `object` | — | — | no |  |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Set transparency for line color |
| `weight` | `number` | — | — | no | Thickness of the outline in pixels |

### rotation

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `angle` | `number` | — | — | no | Rotation |
| `shapeAngle` | `integer` | — | — | no | Shape rotation |
| `textAngle` | `integer` | — | — | no | Text rotation |

### shadow

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `angle` | `number` | — | — | no | Angle |
| `color` | `object` | — | — | no | Color of the shadow effect. Note: shadows create accessibility issues -- prefer flat layout. |
| `shadowBlur` | `number` | — | — | no | Blur |
| `shadowDistance` | `number` | — | — | no | Distance |
| `shadowPositionPreset` | `string` | — | — | no | Preset |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### shape

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `arrowStemWidth` | `integer` | — | — | no | Stem width |
| `arrowheadSize` | `integer` | — | — | no | Arrowhead size |
| `chevronAngle` | `integer` | — | — | no | Angle |
| `hexagonSlant` | `integer` | — | — | no | Slant |
| `isocelesTriangleTipPosition` | `integer` | — | — | no | Tip position |
| `octagonSnipSize` | `integer` | — | — | no | Size of snips |
| `parallelogramSlant` | `integer` | — | — | no | Slant |
| `rectangleRoundedCurve` | `integer` | — | — | no | Rounded corners |
| `roundEdge` | `integer` | — | — | no | Round edges |
| `speechBubbleHeight` | `integer` | — | — | no | Chat bubble height |
| `speechBubbleTailAngle` | `integer` | — | — | no | Tail angle |
| `speechBubbleTailPosition` | `string` | — | — | no | Tail position |
| `tabCutCornerSnipSizeBottom` | `integer` | — | — | no | Size of bottom snips |
| `tabCutCornerSnipSizeTop` | `integer` | — | — | no | Size of top snips |
| `tabCutCornerSnipSizeTopRight` | `integer` | — | — | no | Size of top right snip |
| `tabRoundCornerBottom` | `integer` | — | — | no | Rounded bottom corners |
| `tabRoundCornerTop` | `integer` | — | — | no | Rounded top corners |
| `tabRoundCornerTopRight` | `integer` | — | — | no | Rounded top right corner |
| `tileShape` | `string` | — | — | no | Shape |
| `trapezoidSlant` | `integer` | — | — | no | Slant |

### text

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `bold` | `boolean` | — | — | no | Bold |
| `bottomMargin` | `integer` | — | — | no | Bottom margin |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `horizontalAlignment` | `string` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `leftMargin` | `integer` | — | — | no | Left margin |
| `rightMargin` | `integer` | — | — | no | Right margin |
| `show` | `boolean` | — | — | no | Show |
| `topMargin` | `integer` | — | — | no | Top margin |
| `underline` | `boolean` | — | — | no | Underline |
| `verticalAlignment` | `string` | — | — | no |  |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "bookmarkNavigator",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — CASH FLOW & AVAILABILITY (visual 197b743a9e56f5123568)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
  "name": "197b743a9e56f5123568",
  "position": {
    "x": 12,
    "y": 170,
    "z": 12500,
    "height": 36,
    "width": 530,
    "tabOrder": 4000
  },
  "visual": {
    "visualType": "bookmarkNavigator",
    "objects": {
      "bookmarks": [
        {
          "properties": {
            "bookmarkGroup": {
              "expr": {
                "Literal": {
                  "Value": "'b2cdd107a60476e7b686'"
                }
              }
            },
            "selectedBookmark": {
              "expr": {
                "Literal": {
                  "Value": "'fb62c580f0aa5964384a'"
                }
              }
            }
          }
        }
      ],
      "outline": [
        {
          "properties": {
            "show": {
              "expr": {
                "Literal": {
                  "Value": "true"
                }
              }
            }
          }
        }
      ],
      "shape": [
        {
          "properties": {
            "roundEdge": {
              "expr": {
                "Literal": {
                  "Value": "7L"
                }
              }
            }
          },
          "selector": {
            "id": "default"
          }
        }
      ],
      "fill": [
        {
          "properties": {
            "fillColor": {
              "solid": {
                "color": {
                  "expr": {
                    "ThemeDataColor": {
                      "ColorId": 2,
                      "Percent": 0.6
                    }
                  }
                }
              }
            }
          },
          "selector": {
            "id": "selected"
          }
        }
      ]
    },
    "drillFilterOtherVisuals": true
  },
  "parentGroupName": "ec2530d94e17151d8356",
  "howCreated": "InsertVisualButton"
}
```

### R0101 — DIR PARTICIPATIONS - DETAIL (visual 26828da190d1ebeae9ed)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

### R0101 — DIR PARTICIPATIONS - DETAIL (visual 5cfd92c9d12d4135265a)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

### R0101 — DIR PARTICIPATIONS - DETAIL (visual 664e38d181637a51a2e2)

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
- pbir CLI: [../GUIDE.md#cli-bookmarknavigator](../GUIDE.md#cli-bookmarknavigator)
- JSON format: [../GUIDE.md#json-bookmarknavigator](../GUIDE.md#json-bookmarknavigator)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
TODO: fill in during enrichment.
<!-- /MANUAL -->
