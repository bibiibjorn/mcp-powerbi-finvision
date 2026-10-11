# Shared container: `border`

> **Layer**: CHROME — lives in `visual.visualContainerObjects`, not `visual.objects`.
> Used by: all visual types.

Documented once and linked from per-type files.

---

## Overview

`border` draws a stroke around the outer edge of the visual container (the chrome frame). It is part of `visualContainerObjects` (vCO). The number of properties varies by visual type — the base form has 4–5 properties; some types (like `cardVisual`) expose a richer set via `shapeCustomRectangle` for rounded corners.

---

## Properties (base form — universal)

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `show` | `boolean` | `false` | true / false | no | Enable the border. Default off. |
| `color` | `object` | — | `{"Solid": {"Color": "#hex"}}` | no | Border stroke color. |
| `radius` | `number` | `0D` | 0–50 (D literal, pixels) | no | Corner radius — rounds the border corners. 0 = square corners. |
| `style` | `string` | `'solid'` | `'solid'` / `'dashed'` / `'dotted'` | no | Line style. Single-quoted string literal. |
| `width` | `number` | `1D` | 1–10 (D literal, pixels) | no | Border stroke width in pixels. |

Some visual types expose additional border properties: `transparency` for border color transparency, or the full `shapeCustomRectangle` / `rectangleRoundedCurve` family for per-corner radius control. These are documented inline in the per-type file when present.

---

## JSON location

```json
{
  "visual": {
    "visualContainerObjects": {
      "border": [
        {
          "properties": {
            "show":   { "Literal": { "Value": "true" } },
            "color":  { "Solid":   { "Color": "#CCCCCC" } },
            "radius": { "Literal": { "Value": "4D" } },
            "width":  { "Literal": { "Value": "1D" } }
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
        "border": [
          {
            "show": true,
            "color": { "solid": { "color": "#CCCCCC" } },
            "radius": 4,
            "width": 1
          }
        ]
      }
    }
  }
}
```

---

## Gotchas

- `show: false` is the **default** — setting `color` or `width` without `show: true` has no visible effect.
- `radius` is in pixels. Values above ~20 produce visibly rounded capsule shapes; test at the card's actual pixel dimensions.
- For advanced per-corner rounding (different radius per corner), use the `shapeCustomRectangle` container (`rectangleRoundedCurveCustomStyle: true`, then individual `rectangleRoundedCurveLeftTop` etc.). This is type-specific and documented in the per-type file.
- `border` in vCO frames the entire visual chrome. Matrix and table visuals also have an internal `grid` container in `visual.objects` that draws grid lines between rows and columns — that is separate from this border.
