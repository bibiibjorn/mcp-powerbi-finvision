# Shared container: `background`

> **Layer**: CHROME — lives in `visual.visualContainerObjects`, not `visual.objects`.
> Used by: all visual types.

Documented once and linked from per-type files.

---

## Overview

`background` controls the fill color and optional image behind the entire visual container chrome. It is part of `visualContainerObjects` (vCO) — it wraps the whole visual from the outside.

**Do not confuse with internal background properties**: chart `plotArea` background (`visual.objects.plotArea`), `cardVisual` cell background (`visual.objects.layout.backgroundFillColor`), or matrix row backgrounds — those are `visual.objects` and documented in the per-type or plotArea shared file.

---

## Properties

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `show` | `boolean` | `false` | true / false | no | Enable the background fill. When `false` (default) the container is transparent. |
| `fillColor` | `object` | — | `{"Solid": {"Color": "#hex"}}` | no | The fill color applied when `show` is `true`. |
| `transparency` | `number` | `0D` | 0–100 (D literal) | no | 0 = fully opaque; 100 = fully transparent. Inverse of alpha. |

Some visual types also expose image-related background properties (e.g., `backgroundImage`, `backgroundImageType`, `backgroundImageFit`, `backgroundImageTransparency`, `backgroundImageShow`). These are documented inline in the per-type file when present; they are not universal.

---

## JSON location

```json
{
  "visual": {
    "visualContainerObjects": {
      "background": [
        {
          "properties": {
            "show":         { "Literal": { "Value": "true" } },
            "fillColor":    { "Solid":   { "Color": "#F5F5F5" } },
            "transparency": { "Literal": { "Value": "0D" } }
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
        "background": [
          {
            "show": true,
            "fillColor": { "solid": { "color": "#F5F5F5" } },
            "transparency": 0
          }
        ]
      }
    }
  }
}
```

Theme values use plain JSON types (no `Literal`/`Solid` wrappers).

---

## Gotchas

- `show: false` is the **default**. Setting `fillColor` without `show: true` has no visible effect.
- `transparency` is **inverted from alpha**: 0 = opaque, 100 = invisible.
- For `cardVisual`, `visual.objects.layout.backgroundFillColor` / `backgroundShow` controls the background of individual card cells inside the callout area — separate from this vCO background which frames the whole visual.
- When both vCO `background` and `plotArea` (internal) background are set, they stack: vCO is outermost, `plotArea` is inside the chart area only.
