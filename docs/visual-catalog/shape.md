# shape

## Overview

<!-- MANUAL: overview -->
`shape` renders a purely geometric element with no data roles. Its content is controlled entirely by `visual.objects`:

- `shape` container: `tileShape` selects the geometry (`'rectangle'`, `'line'`, `'ellipse'`, `'triangle'`, `'arrow'`, `'chevron'`, `'pentagon'`, `'hexagon'`, `'parallelogram'`, `'trapezoid'`, `'diamond'`, `'octagon'`, `'cross'`, `'speechBubble'`, and snip/rounded tab variants). Shape-specific geometry tweaks (slant, curve, arrowhead size, etc.) live in the same container.
- `fill` container: solid fill color and transparency.
- `outline` container: stroke color, weight, transparency.
- `rotation` container: `shapeAngle` (integer, snaps) and `angle` (double, freeform) for combined rotation.
- `text` container: optional text label overlaid on the shape.
- `glow` and `shadow` containers for visual effects.

The most common R0101 use is a `'line'` shape rotated 90° to create a vertical divider between page sections.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `shape` when:

- Adding a **horizontal or vertical divider line** between sections of a page (the dominant R0101 pattern — 685 instances, almost all are `tileShape: 'line'`).
- Adding a **colored rectangle** as a background panel or accent block behind a group of visuals.
- Adding **geometric decorative elements** (chevrons, diamonds) that draw attention to a section header.
- Overlaying a **semi-transparent fill** over a region to create a visual zone without a container group.

Prefer `image` when the divider is a styled PNG asset (e.g., gradient bars). Prefer `textbox` when the shape needs substantial text content. Prefer `actionButton` when the element needs a click action — shapes can carry `visualLink` via vCO but `actionButton` is purpose-built for interactive targets.
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
- **fill** (3 properties) — documented inline below
- **glow** (4 properties) — documented inline below
- **outline** (4 properties) — documented inline below
- **rotation** (3 properties) — documented inline below
- **shadow** (7 properties) — documented inline below
- **shape** (21 properties) — documented inline below
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

### fill

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `fillColor` | `object` | — | — | no |  |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Background color transparency |

### glow

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no |  |
| `shadowBlur` | `number` | — | — | no | Blur |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### outline

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
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
| `linecapType` | `string` | — | — | no | Cap type |
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
| `text` | `string` | — | — | no | Text |
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
    "visualType": "shape",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — PRIVATE FUNDS - DETAIL (visual 0dad7250e03ca2997df0)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
  "name": "0dad7250e03ca2997df0",
  "position": {
    "x": 508.88888888888891,
    "y": 10.8888888888889,
    "z": 3000,
    "height": 187.77777777777777,
    "width": 101.11111111111111,
    "tabOrder": 12000
  },
  "visual": {
    "visualType": "shape",
    "objects": {
      "shape": [
        {
          "properties": {
            "tileShape": {
              "expr": {
                "Literal": {
                  "Value": "'line'"
                }
              }
            }
          }
        }
      ],
      "rotation": [
        {
          "properties": {
            "shapeAngle": {
              "expr": {
                "Literal": {
                  "Value": "0L"
                }
              }
            },
            "angle": {
              "expr": {
                "Literal": {
                  "Value": "90D"
                }
              }
            }
          }
        }
      ],
      "outline": [
        {
          "properties": {
            "lineColor": {
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
            "id": "default"
          }
        }
      ]
    },
    "drillFilterOtherVisuals": true
  },
  "parentGroupName": "60aa86e189dc05d0b800",
  "howCreated": "InsertVisualButton"
}
```

### R0101 — DIR PARTICIPATIONS - DETAIL (visual 60896c744c1026062f7d)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

### R0101 — DIR PARTICIPATIONS - DETAIL (visual fa6643f0b9da51b7685f)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
TODO: fill in during enrichment.
<!-- /MANUAL -->

### R0101 — RECO - CURRENT ACCOUNTS (visual 601a2fa934e0bc75734b)

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
- pbir CLI: [../GUIDE.md#cli-shape](../GUIDE.md#cli-shape)
- JSON format: [../GUIDE.md#json-shape](../GUIDE.md#json-shape)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
TODO: fill in during enrichment.
<!-- /MANUAL -->
