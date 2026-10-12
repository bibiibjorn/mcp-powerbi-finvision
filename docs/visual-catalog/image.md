# image

## Overview

<!-- MANUAL: overview -->
`image` displays a static or URL-sourced image with no data roles. Its content is stored in `visual.objects.general[0].properties.imageUrl`, which can reference:

1. A file in the report's `StaticResources/RegisteredResources/` folder via a `ResourcePackageItem` expression (most common in R0101 — icon PNGs, decorative elements).
2. A hard-coded URL string (`sourceUrl` on the `image` objects container).
3. A measure that returns a URL (dynamic image from data — uses `sourceField`).

The `image` container under `visual.objects` controls fit (`fit`: `Normal`, `Fit`, `Fill`, `Center`), corner radius, stroke/border, and image effects (blur/contrast/exposure/saturation). The `imageScaling` container has a single `imageScalingType` property that mirrors the fit option at a higher level.

The visual supports `visualContainerObjects.visualLink` for click actions (bookmark / page navigation / web URL), making it the preferred pattern for clickable icon-buttons sourced from PNG/SVG files.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `image` when:

- Embedding a **company logo, client icon, or decorative PNG/SVG** from `RegisteredResources` (the predominant R0101 use — 395 instances are almost all small icons in group containers on detail pages).
- Adding a **clickable icon-button** with a PNG source — combine with `visualContainerObjects.visualLink` to wire a bookmark or page navigation action.
- Using a **dynamic image from data** where a measure returns an image URL (e.g., client photo URLs stored in a dimension table).
- Placing a **horizontal rule / separator image** (the R0101 "Arrow-Up" pattern — a thin PNG spanning the page width used as a visual divider).

Prefer `actionButton` when you need shaped, multi-state, text-labelled buttons with built-in icon library. Prefer `shape` for pure geometric dividers/lines with no image asset dependency.
<!-- /MANUAL -->

## Roles

`image` has **no data roles**. Image source is configured via `visual.objects.general[0].properties.imageUrl` (for `ResourcePackageItem` or URL references) or `visual.objects.image` sub-properties for the standalone image visual. The `queryState` block is empty (`{}`).

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|

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
- **image** (27 properties) — documented inline below
- **imageScaling** (1 properties) — documented inline below

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

### image

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `altText` | `string` | — | — | no | Alt text |
| `backgroundColor` | `object` | — | — | no |  |
| `backgroundEnabled` | `boolean` | — | — | no | Show background |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `blur` | `integer` | — | — | no | Blur |
| `contrast` | `integer` | — | — | no | Contrast |
| `cornerRadius` | `integer` | — | — | no | Rounded corners |
| `cornerRadiusAdvanced` | `boolean` | — | — | no | Individual corners |
| `cornerRadiusLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `cornerRadiusLeftTop` | `integer` | — | — | no | Top left corner |
| `cornerRadiusRightBottom` | `integer` | — | — | no | Bottom right corner |
| `cornerRadiusRightTop` | `integer` | — | — | no | Top right corner |
| `effects` | `boolean` | — | — | no | Image effects |
| `exposure` | `integer` | — | — | no | Exposure |
| `fit` | `string` | — | — | no | Image fit |
| `height` | `integer` | — | — | no | Image height |
| `saturation` | `integer` | — | — | no | Saturation |
| `sourceField` | `string` | — | — | no | Data |
| `sourceFile` | `object` | — | — | no |  |
| `sourceType` | `string` | — | — | no | Image source |
| `sourceUrl` | `string` | — | — | no | Image URL |
| `strokeColor` | `object` | — | — | no |  |
| `strokePattern` | `string` | — | — | no | Line style |
| `strokeShow` | `boolean` | — | — | no | Show |
| `strokeTransparency` | `number` | — | — | no | Transparency |
| `strokeWidth` | `number` | — | — | no | Width |
| `transparency` | `number` | — | — | no | Transparency |

### imageScaling

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `imageScalingType` | `string` | — | — | no | Scaling |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "image",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — DIR PARTICIPATIONS - DETAIL (visual 3bf67d07d82fca5db83f)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Source**: `ResourcePackageItem` referencing `icon_Family_Grey1964519709981154.png` — a small grey family/household icon.
- **Position**: `(14.3, 3.3) 45×42` px inside a group container (`parentGroupName: "9262df6f05b410f0ba9b"`) — fractional coordinates are group-local after Desktop layout. This is the canonical small-icon-in-cell pattern.
- **No action**: no `visualLink` — purely decorative.
<!-- /MANUAL -->

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
  "name": "3bf67d07d82fca5db83f",
  "position": {
    "x": 14.27157216805119,
    "y": 3.25883441402754,
    "z": 2000,
    "height": 41.5443977407067,
    "width": 45.291925484531113,
    "tabOrder": 0
  },
  "visual": {
    "visualType": "image",
    "objects": {
      "general": [
        {
          "properties": {
            "imageUrl": {
              "expr": {
                "ResourcePackageItem": {
                  "PackageName": "RegisteredResources",
                  "PackageType": 1,
                  "ItemName": "icon_Family_Grey1964519709981154.png"
                }
              }
            }
          }
        }
      ]
    },
    "visualContainerObjects": {
      "title": [
        {
          "properties": {
            "text": {
              "expr": {
                "Literal": {
                  "Value": "'Icon family'"
                }
              }
            }
          }
        }
      ]
    },
    "drillFilterOtherVisuals": true
  },
  "parentGroupName": "9262df6f05b410f0ba9b"
}
```

### R0101 — DIR PARTICIPATIONS - DETAIL (visual 1c0f5057a39538664f69)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Source**: `icon_Currency_Grey_227236776530563556.png` — currency icon, same grey monochrome style as 3bf67d.
- **Action**: `visualLink` wired to bookmark `ae56f32cce7f0c8bcdea` with `show: false` — action is wired but currently disabled. Pattern: pre-wire actions on icons during build, disable with `show: false` until the corresponding bookmark is confirmed.
- **Position**: `(15.0, 2.0) 39×38` px inside group `059f1ced2ae71070e228`.
<!-- /MANUAL -->

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
  "name": "1c0f5057a39538664f69",
  "position": {
    "x": 15.041208791208792,
    "y": 2,
    "z": 2000,
    "height": 38,
    "width": 39.107142857142854,
    "tabOrder": 1000
  },
  "visual": {
    "visualType": "image",
    "objects": {
      "general": [
        {
          "properties": {
            "imageUrl": {
              "expr": {
                "ResourcePackageItem": {
                  "PackageName": "RegisteredResources",
                  "PackageType": 1,
                  "ItemName": "icon_Currency_Grey_227236776530563556.png"
                }
              }
            }
          }
        }
      ]
    },
    "visualContainerObjects": {
      "title": [
        {
          "properties": {
            "text": {
              "expr": {
                "Literal": {
                  "Value": "'Icon currency'"
                }
              }
            }
          }
        }
      ],
      "visualLink": [
        {
          "properties": {
            "show": {
              "expr": {
                "Literal": {
                  "Value": "false"
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
                  "Value": "'ae56f32cce7f0c8bcdea'"
                }
              }
            }
          }
        }
      ]
    },
    "drillFilterOtherVisuals": true
  },
  "parentGroupName": "059f1ced2ae71070e228",
  "howCreated": "InsertVisualButton"
}
```

### R0101 — DIR PARTICIPATIONS - DETAIL (visual 1ec7f3003ca43328de3a)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Source**: `Arrow-Up9926318053955774.png` — a full-width (1886 px) thin horizontal divider/separator image used as a page section divider.
- **Action**: `visualLink.show = true`, bookmark `5eea31299226bea5d585` — clicking the divider bar navigates back. This combines the decorative separator and the navigation action in a single visual.
- **Position**: `(0, 489.9) 1886×28` px — spans nearly the full page width at a fixed y position inside group `59d7fc5211e48baa2f73`.
- **Pattern**: thin-bar clickable separator — the entire divider line is the hit target. No `parentGroupName` on the action itself; it sits directly in the parent group.
<!-- /MANUAL -->

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
  "name": "1ec7f3003ca43328de3a",
  "position": {
    "x": 0,
    "y": 489.88399071925755,
    "z": 2000,
    "height": 27.563805104408353,
    "width": 1885.6148491879351,
    "tabOrder": 1000
  },
  "visual": {
    "visualType": "image",
    "objects": {
      "general": [
        {
          "properties": {
            "imageUrl": {
              "expr": {
                "ResourcePackageItem": {
                  "PackageName": "RegisteredResources",
                  "PackageType": 1,
                  "ItemName": "Arrow-Up9926318053955774.png"
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
                  "Value": "'Arrow Up'"
                }
              }
            }
          }
        }
      ]
    },
    "drillFilterOtherVisuals": true
  },
  "parentGroupName": "59d7fc5211e48baa2f73"
}
```

### R0101 — DIR PARTICIPATIONS - DETAIL (visual 4e5689dae8d48aa47a41)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Source**: `menu8993020983940887.png` — a hamburger/burger menu icon (expand navigation).
- **Action**: `visualLink.show = true`, bookmark `d28b634433687abf1465` — clicking the menu icon expands the side navigation panel.
- **Position**: `(1832.9, 35.2) 33×37` px, top-right corner of the page (`z: 3000`, `tabOrder: -2001`) — negative tabOrder places it below the z-stack of content visuals for keyboard navigation order.
- **Pattern**: top-right menu icon that triggers a navigation-expand bookmark. The negative `tabOrder` is intentional — it de-prioritizes this icon in screen-reader tab order while keeping it visually prominent.
<!-- /MANUAL -->

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
  "name": "4e5689dae8d48aa47a41",
  "position": {
    "x": 1832.8735632183909,
    "y": 35.160394562334247,
    "z": 3000,
    "height": 36.7816091954023,
    "width": 33.333333333333336,
    "tabOrder": -2001
  },
  "visual": {
    "visualType": "image",
    "objects": {
      "general": [
        {
          "properties": {
            "imageUrl": {
              "expr": {
                "ResourcePackageItem": {
                  "PackageName": "RegisteredResources",
                  "PackageType": 1,
                  "ItemName": "menu8993020983940887.png"
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
                  "Value": "'d28b634433687abf1465'"
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
                  "Value": "'Burger shape (Expand)'"
                }
              }
            }
          }
        }
      ]
    },
    "drillFilterOtherVisuals": true
  },
  "parentGroupName": "b57f97812c52c26f0fa2",
  "howCreated": "InsertVisualButton"
}
```

## Gotchas

<!-- MANUAL: gotchas -->

### Image source is in `visual.objects.general`, not in `visual.objects.image`

In R0101 extracts, the image URL is stored in `visual.objects.general[0].properties.imageUrl` using a `ResourcePackageItem` expression — NOT in the `image` container under `visual.objects`. The `image` container (27 properties) controls layout/fit/effects but does not hold the source reference for `ResourcePackageItem`-based images. Confusing these two is the #1 authoring error.

```json
"objects": {
  "general": [
    {
      "properties": {
        "imageUrl": {
          "expr": {
            "ResourcePackageItem": {
              "PackageName": "RegisteredResources",
              "PackageType": 1,
              "ItemName": "icon_Family_Grey1964519709981154.png"
            }
          }
        }
      }
    }
  ]
}
```

### RegisteredResources filenames include a numeric suffix

Files in `StaticResources/RegisteredResources/` are stored with a numeric deduplication suffix appended to the base name (e.g., `icon_Family_Grey1964519709981154.png`). The `ItemName` in the `ResourcePackageItem` expression must match exactly — including the suffix — or the image shows blank. Always copy the `ItemName` from the target file's actual filename on disk.

### Clickable images require `visualContainerObjects.visualLink`, not `visual.objects`

Adding a click action to an image uses the same `visualLink` pattern as `actionButton`, stored under `visual.visualContainerObjects`. Setting `visualLink.show = false` disables the action without removing the wiring (useful for temporarily disabling a navigation link).

### Position precision from group parent

When an `image` visual is inside a container group (`parentGroupName` set), its `position` coordinates in the PBIR file are page-absolute. After assigning `parentGroupName`, subtract the group container's page-absolute x/y to get group-local coordinates. The R0101 detail-page icon images (small 30–45 px square icons in the top-left of group cells) show fractional pixel positions (`x: 14.27`, `y: 3.25`) — this is normal and expected from the Desktop layout engine.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-image](../GUIDE.md#cli-image)
- JSON format: [../GUIDE.md#json-image](../GUIDE.md#json-image)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- See `actionButton.md` for shaped/labelled buttons with built-in icon library (no PNG dependency).
- See `shape.md` for geometric dividers/lines with no image asset.
- `StaticResources/RegisteredResources/` — where PNG/SVG files live; `ItemName` in `ResourcePackageItem` must match the filename on disk exactly including the numeric suffix.
- stream-3-pbir-visuals.md §7.12 — image visual storage overview.
<!-- /MANUAL -->
