# Shared container: `title`

> **Layer**: CHROME — lives in `visual.visualContainerObjects`, not `visual.objects`.
> Used by: all visual types.

Documented once and linked from per-type files.

---

## Overview

`title` controls the text label rendered above a visual in its chrome bar. It is part of `visualContainerObjects` (vCO) and formats the container frame — **not** the internal rendering of the visual content. Setting a title property affects only the chrome wrapper, never the callout value, axis labels, or chart series.

**Critical distinction**: changing `visualContainerObjects.title.fontSize` does NOT affect a `cardVisual`'s big number — that is `visual.objects.value.fontSize`. The title is purely decorative chrome.

Titles support dynamic (expression-based) text via a DAX measure reference in the `text` property instead of a `Literal`. Configure via the formatting pane `fx` button; inspect the resulting vCO JSON for the binding pattern.

---

## Properties

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `show` | `boolean` | `true` | true / false | no | Show or hide the title bar entirely. |
| `text` | `string` or expression | — | Any string; or `{"Expr": {"Measure": {...}}}` for dynamic title | no | The title text. Null = use visual name. Supports expression-based dynamic title via DAX measure reference. |
| `fontColor` | `object` | Theme `firstLevelElements` | `{"Solid": {"Color": "#hex"}}` | no | Font color of the title text. |
| `background` | `object` | transparent | `{"Solid": {"Color": "#hex"}}` | no | Background fill of the title area. |
| `alignment` | `string` | `'left'` | `'left'` / `'center'` / `'right'` | no | Horizontal alignment of the title text. Encoded as single-quoted string literal: `{"Literal": {"Value": "'center'"}}`. |
| `fontSize` | `number` | `12D` | 8–60 (D literal) | no | Font size in points for title text. Example: `{"Literal": {"Value": "12D"}}`. |
| `fontFamily` | `string` | Theme default | e.g. `'Segoe UI'` | no | Font family name (single-quoted string literal). |
| `bold` | `boolean` | `false` | true / false | no | Bold title text. |
| `italic` | `boolean` | `false` | true / false | no | Italic title text. |
| `underline` | `boolean` | `false` | true / false | no | Underline title text. |
| `titleWrap` | `boolean` | `false` | true / false | no | Word-wrap long title text onto multiple lines. |
| `heading` | `string` | — | `'h1'`–`'h6'` / `'p'` | no | Semantic heading tag for screen-reader accessibility. Does not affect visual styling. |

---

## JSON location

```json
{
  "visual": {
    "visualContainerObjects": {
      "title": [
        {
          "properties": {
            "show":       { "Literal": { "Value": "true" } },
            "text":       { "Literal": { "Value": "'My KPI Title'" } },
            "fontColor":  { "Solid":   { "Color": "#1A1A3E" } },
            "alignment":  { "Literal": { "Value": "'center'" } },
            "fontSize":   { "Literal": { "Value": "12D" } },
            "bold":       { "Literal": { "Value": "true" } }
          }
        }
      ]
    }
  }
}
```

---

## Theme equivalent

In `theme.json` the title card is configured under `visualStyles`:

```json
{
  "visualStyles": {
    "*": {
      "*": {
        "title": [
          {
            "show": true,
            "fontColor": { "solid": { "color": "#1A1A3E" } },
            "fontSize": 12,
            "fontFamily": "Segoe UI",
            "bold": false,
            "alignment": "center"
          }
        ]
      }
    }
  }
}
```

Theme values use plain JSON types (no `Literal`/`Solid` wrappers). Per-visual `visualContainerObjects` overrides theme.

---

## Gotchas

- `text: null` falls back to the visual's object name (the 20-char hex ID or renamed friendly name). Always set an explicit `text` value or it renders the internal ID.
- `show: false` collapses the entire title bar — the visual gets more vertical space, which shifts content up. Re-check card/matrix height after hiding title.
- Expression-based (dynamic) titles reference a DAX measure. The measure must return a `Text` type. Numeric or date return types will not render.
- `alignment` must be a **single-quoted string inside the Value** — `"'center'"`, not `"center"`. The outer double-quotes are JSON; the inner single-quotes are the PBIR string literal delimiter.
- `heading` is accessibility metadata only — it has no visual effect on font size or weight.
- Setting `fontColor` via a `ThemeDataColor` with `selector: {"id": "default"}` may render as white (theme colour 0). Use an explicit hex `Solid` color for reliable title text color.
