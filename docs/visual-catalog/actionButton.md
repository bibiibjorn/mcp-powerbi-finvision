# actionButton

## Overview

<!-- MANUAL: overview -->
`actionButton` is a fully configurable click-target with no data roles. Its content — label text, icon, fill color, outline, shape, and glow — is stored entirely in `visual.objects`. The action it triggers is stored in `visual.visualContainerObjects.visualLink`, where `type` selects the action category (`'Bookmark'`, `'PageNavigation'`, `'Drillthrough'`, `'WebURL'`, `'Back'`) and sibling properties (`bookmark`, `navigationSection`, `drillthroughSection`, `webUrl`) supply the destination.

It supports multiple button states (default / hover / pressed / disabled) that can each carry different fill and text colors — set via `selector: {id: "default"}`, `{id: "hover"}`, etc. on the relevant `visual.objects` container entries.

The `icon` container selects from a library of built-in shapes (`shapeType` values such as `'bookmarks'`, `'arrow'`, `'information'`, `'arrowRight'`, etc.) and controls size and placement alongside the button label.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `actionButton` when you need:

- **Bookmark navigation**: toggling between views, showing/hiding content groups, or acting as a "tab" that switches page state.
- **Page navigation**: a styled "Go to page X" button that is more visually branded than a plain hyperlink.
- **Drillthrough back button**: the standard `action.type = "Back"` pattern on a drillthrough detail page.
- **Web URL launcher**: opening an external URL from within the report (e.g., linking to a client portal or document).

Prefer `image` (with a `visualLink`) for clickable icons sourced from PNG/SVG files in `RegisteredResources`. Prefer `bookmarkNavigator` when you need a row of buttons that automatically list and highlight the current bookmark in a group — `actionButton` is for a single, explicitly-wired click target, not a dynamic list.

Note: `actionButton` instances that serve purely as invisible click-targets (full-canvas, `isHidden: true`) are the R0101 pattern for overlay navigation — they intercept clicks over a page region and jump to a bookmark.
<!-- /MANUAL -->

## Roles

`actionButton` has **no data roles**. The `queryState` block in the minimal template is empty (`{}`). All content is authored via `visual.objects` (button appearance) and `visual.visualContainerObjects.visualLink` (action wiring).

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
- **fill** (5 properties) — documented inline below
- **glow** (5 properties) — documented inline below
- **icon** (15 properties) — documented inline below
- **outline** (5 properties) — documented inline below
- **rotation** (3 properties) — documented inline below
- **shadow** (8 properties) — documented inline below
- **shape** (20 properties) — documented inline below
- **text** (15 properties) — documented inline below

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
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `fillColor` | `object` | — | — | no |  |
| `image` | `object` | — | — | no |  |
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

### icon

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `bottomMargin` | `integer` | — | — | no | Bottom margin |
| `horizontalAlignment` | `string` | — | — | no |  |
| `iconSize` | `number` | — | — | no | Icon size |
| `image` | `object` | — | — | no |  |
| `leftMargin` | `integer` | — | — | no | Left margin |
| `lineColor` | `object` | — | — | no |  |
| `lineTransparency` | `number` | — | — | no | Set transparency for line color |
| `lineWeight` | `integer` | — | — | no | Weight |
| `placement` | `string` | — | — | no | Icon placement |
| `rightMargin` | `integer` | — | — | no | Right margin |
| `shapeType` | `string` | — | — | no | Icon |
| `show` | `boolean` | — | — | no | Show |
| `topMargin` | `integer` | — | — | no | Top margin |
| `verticalAlignment` | `string` | — | — | no |  |

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
    "visualType": "actionButton",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — DIR PARTICIPATIONS - DETAIL (visual e2678b00679a7080e459)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Action**: `visualLink.type = 'Bookmark'` targeting bookmark `5eea31299226bea5d585`.
- **Icon**: `shapeType = 'bookmarks'` on `selector: {id: "default"}`; a second entry with `show: false` (no selector) hides the icon in all other states — effectively icon-only for the default view, blank otherwise.
- **Position**: `(0, 0) 1920×1080` — full-canvas invisible overlay. `isHidden: true` keeps it non-visible but click-active.
- **Pattern**: invisible overlay action button — clicking anywhere on the page triggers the bookmark. Used in R0101 to navigate from a detail page back to the main view.
<!-- /MANUAL -->

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
  "name": "e2678b00679a7080e459",
  "position": {
    "x": 0,
    "y": 0,
    "z": 2000,
    "height": 1080,
    "width": 1920,
    "tabOrder": 3000
  },
  "visual": {
    "visualType": "actionButton",
    "objects": {
      "icon": [
        {
          "properties": {
            "shapeType": {
              "expr": {
                "Literal": {
                  "Value": "'bookmarks'"
                }
              }
            }
          },
          "selector": {
            "id": "default"
          }
        },
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
    "visualContainerObjects": {
      "visualLink": [
        {
          "properties": {
            "show": {
              "expr": {
                "Literal": {
                  "Value": "true"
                }
              }
            },
            "type": {
              "expr": {
                "Literal": {
                  "Value": "'Bookmark'"
                }
              }
            },
            "bookmark": {
              "expr": {
                "Literal": {
                  "Value": "'5eea31299226bea5d585'"
                }
              }
            }
          }
        }
      ],
      "title": [
        {
          "properties": {
            "text": {
              "expr": {
                "Literal": {
                  "Value": "'Default Bookmark'"
                }
              }
            }
          }
        }
      ]
    },
    "drillFilterOtherVisuals": true
  },
  "isHidden": true,
  "howCreated": "InsertVisualButton"
}
```

### R0101 — ANALYSIS - BENCHMARK (visual 655ad452951e21d7be3b)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Action**: Bookmark navigation (full multi-state button) — styled with explicit fill, text, outline, shape per state (default / hover / pressed / disabled).
- **Position**: Small button (308 px wide, not full-canvas) — a visible, styled navigation tab on the ANALYSIS - BENCHMARK page.
- **Pattern**: Visible tab-style button, contrasting with the invisible overlay button in the e2678b template. These 474-line extracts are the canonical reference for a fully-styled multi-state button.
<!-- /MANUAL -->

### R0101 — ANALYSIS - BENCHMARK (visual c8b4587975ef46f1bfe8)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Action**: Bookmark navigation, sibling to 655ad45… — together they form a two-button tab row on the ANALYSIS - BENCHMARK page (e.g. switching between two benchmark views).
- **Pattern**: When authoring a pair of tab buttons, copy this and 655ad45… as a pair; do not hand-craft the multi-state `fill`/`text`/`outline` stacks — they are identical except for the target bookmark ID and the label text.
<!-- /MANUAL -->

### R0101 — NAVIGATOR (visual 024a1d08e3b603234603)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Action**: PageNavigation — navigates to a specific report page rather than toggling a bookmark. Uses `navigationSection` instead of `bookmark` in `visualLink`.
- **Pattern**: NAVIGATOR page uses a grid of `actionButton` visuals for main report navigation. At 308 lines each, they carry per-state fill colors matching the report's primary color palette.
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### Action type is set in `visualContainerObjects.visualLink`, not in `visual.objects`

The `visualLink` container lives under `visual.visualContainerObjects`, not `visual.objects`. A common mistake is looking for `type` inside `objects` and not finding it. The full pattern:

```json
"visualContainerObjects": {
  "visualLink": [
    {
      "properties": {
        "show":     {"expr": {"Literal": {"Value": "true"}}},
        "type":     {"expr": {"Literal": {"Value": "'Bookmark'"}}},
        "bookmark": {"expr": {"Literal": {"Value": "'<bookmarkId>'"}}}
      }
    }
  ]
}
```

Supported `type` values: `'Bookmark'`, `'PageNavigation'` (use `navigationSection`), `'Drillthrough'` (use `drillthroughSection`), `'WebURL'` (use `webUrl`), `'Back'` (no additional property needed).

### State selectors on `icon` and `fill`

Per-state formatting uses `selector: {id: "<state>"}` entries in the `icon` and `fill` container arrays. Valid states: `"default"`, `"hover"`, `"pressed"`, `"disabled"`. Omitting the selector means the property applies to all states. The R0101 pattern for a disabled icon uses `show: false` on `icon` with no selector (hides the icon for all states).

### Full-canvas invisible overlay buttons

In R0101, `actionButton` visuals at `(0, 0) 1920×1080` with `isHidden: true` act as invisible click overlays that trigger bookmarks. The `isHidden` flag hides the button visually but keeps it interactive. This pattern is brittle if bookmark IDs change — verify the `bookmark` value in `visualLink` after any bookmark reorganization.

### `howCreated: "InsertVisualButton"` is cosmetic

PBIR persists `howCreated: "InsertVisualButton"` on action buttons created via the Insert > Button menu. This is metadata only and has no effect on rendering or behavior.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-actionbutton](../GUIDE.md#cli-actionbutton)
- JSON format: [../GUIDE.md#json-actionbutton](../GUIDE.md#json-actionbutton)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- See `bookmarkNavigator.md` for the dynamic multi-button pattern (auto-lists a bookmark group).
- See `image.md` for clickable PNG/SVG icons sourced from `RegisteredResources`.
- CLAUDE.md §11.3 — Back button pattern for drillthrough pages.
- stream-3-pbir-visuals.md §11.3 — Back button / drillthrough return flow.
<!-- /MANUAL -->
