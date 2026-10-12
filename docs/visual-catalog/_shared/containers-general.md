# Shared container: `general`

> **Layer**: INTERNAL — lives in `visual.objects`, not `visual.visualContainerObjects`.
> Used by: most visual types (charts, matrix, table, card, slicer, etc.).

Documented once and linked from per-type files.

---

## Overview

`general` is a catch-all `visual.objects` container that holds visual-type-level settings that do not belong to a more specific formatting card. Common uses include `keepLayerOrder`, `responsive`, `formatString`, and tooltip/interaction settings that apply to the visual as a whole rather than to a specific formatting sub-card.

The exact set of properties varies significantly by visual type. The 3-property base form is `keepLayerOrder`, `responsive`, and `outSpace`. Check the per-type file for the exact count and any additional properties.

---

## Common properties

| Property | Type | Default | Selector-aware? | Notes |
|----------|------|---------|-----------------|-------|
| `keepLayerOrder` | `boolean` | `false` | no | When `true`, the rendering engine respects z-order of series layers instead of auto-arranging. Relevant for overlapping series (e.g., line + shaded area combos). |
| `responsive` | `boolean` | `true` | no | When `true`, the visual re-renders to fit its container at different sizes (small-multiples, narrow viewports). When `false`, fixed layout is used. |
| `outSpace` | `number` | — | no | Outer spacing adjustment (pixels). Used on some chart types to control the space outside the plot area. |

Some visual types expose additional `general` properties:
- `formatString` — override the default number format for all series.
- `altText` / `altTextSetting` — accessibility alt-text for screen readers.
- `wordWrap` — word-wrap text in axis labels or cell content (applies to some table/matrix and axis contexts).
- `iFrameTitle` — iframe accessibility title for embedded scenarios.

Discover the exact set for a given type by applying a "General" formatting option in Desktop and inspecting `visual.objects.general` in the saved `visual.json`.

---

## JSON location

```json
{
  "visual": {
    "objects": {
      "general": [
        {
          "properties": {
            "keepLayerOrder": { "Literal": { "Value": "false" } },
            "responsive":     { "Literal": { "Value": "true" } }
          }
        }
      ]
    }
  }
}
```

---

## Theme equivalent

```json
{
  "visualStyles": {
    "*": {
      "*": {
        "general": [
          {
            "responsive": true
          }
        ]
      }
    }
  }
}
```

---

## Gotchas

- `general` is in `visual.objects` (internal), **not** `visualContainerObjects` (chrome) — a common mix-up.
- The `general` container in the theme applies to the visual's internal settings, not the outer chrome frame.
- `keepLayerOrder` is rarely needed; leave at default unless you have overlapping series rendering artifacts.
- `responsive: false` can cause visuals to clip at small sizes (e.g., in small multiples or narrow dashboards). Only disable responsiveness when you need pixel-perfect fixed layouts.
- `altText` for accessibility is set here (for some visual types), but the visual header tooltip's alt-text is in `visualContainerObjects.visualHeaderTooltip.text`. The two are separate.
