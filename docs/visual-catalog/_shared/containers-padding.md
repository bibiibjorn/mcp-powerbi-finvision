# Shared container: `padding`

> **Layer**: CHROME — lives in `visual.visualContainerObjects`, not `visual.objects`.
> Used by: all visual types.

Documented once and linked from per-type files.

---

## Overview

`padding` sets the inner whitespace between the visual container's chrome border and its content area. It is part of `visualContainerObjects` (vCO). Property count varies by visual type: chart types typically expose 4 directional properties; richer types (like `cardVisual`) expose 8+ with per-side and uniform modes.

---

## Properties (base form — 4-property variant)

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `top` | `number` | `0D` | 0–100 (D literal, pixels) | no | Top padding in pixels. |
| `bottom` | `number` | `0D` | 0–100 (D literal, pixels) | no | Bottom padding in pixels. |
| `left` | `number` | `0D` | 0–100 (D literal, pixels) | no | Left padding in pixels. |
| `right` | `number` | `0D` | 0–100 (D literal, pixels) | no | Right padding in pixels. |

---

## Extended form (8-property variant — `cardVisual` and richer types)

| Property | Type | Default | Selector-aware? | Notes |
|----------|------|---------|-----------------|-------|
| `paddingTop` | `integer` | 0 | no | Top (when individual mode). |
| `paddingBottom` | `integer` | 0 | no | Bottom (when individual mode). |
| `paddingLeft` | `integer` | 0 | no | Left (when individual mode). |
| `paddingRight` | `integer` | 0 | no | Right (when individual mode). |
| `paddingUniform` | `integer` | 0 | no | All sides simultaneously (when uniform mode). |
| `paddingIndividual` | `boolean` | `false` | no | When `true`, use per-side values; when `false`, use `paddingUniform`. |

Check the per-type file's container list to confirm which variant applies.

---

## JSON location (4-property form)

```json
{
  "visual": {
    "visualContainerObjects": {
      "padding": [
        {
          "properties": {
            "top":    { "Literal": { "Value": "8D" } },
            "bottom": { "Literal": { "Value": "8D" } },
            "left":   { "Literal": { "Value": "8D" } },
            "right":  { "Literal": { "Value": "8D" } }
          }
        }
      ]
    }
  }
}
```

---

## JSON location (8-property form)

```json
{
  "visual": {
    "visualContainerObjects": {
      "padding": [
        {
          "properties": {
            "paddingIndividual": { "Literal": { "Value": "false" } },
            "paddingUniform":    { "Literal": { "Value": "8" } }
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
        "padding": [
          { "top": 8, "bottom": 8, "left": 8, "right": 8 }
        ]
      }
    }
  }
}
```

---

## Gotchas

- Padding reduces the available content area inside the visual. Adding 8px padding on all sides on a 120×400 card reduces the effective content zone — factor this into minimum height calculations.
- The 4-property form (`top`/`bottom`/`left`/`right`) and the 8-property form (`paddingTop`/`paddingUniform`/`paddingIndividual`) are **not interchangeable**. The form present depends on the visual type. Using the wrong form's keys in `visual.json` has no effect.
- For `cardVisual`, there is also padding inside the card layout (`visual.objects.layout`) and inside the callout area (`visual.objects.cardCalloutArea.paddingTop` etc.) — those are internal to the visual content, not chrome padding.
- The `spacing` container (on `cardVisual`) controls vertical space between label and value — separate from outer padding.
