# Shared container: `visualHeader`

> **Layer**: CHROME — lives in `visual.visualContainerObjects`, not `visual.objects`.
> Used by: all visual types.

Documented once and linked from per-type files.

---

## Overview

`visualHeader` controls the icon strip that appears at the top-right of a visual when the user hovers it: the "more options" ellipsis (`…`), focus mode button, drill controls, and filter icon. It is part of `visualContainerObjects` (vCO).

The `visualHeader` container has ~25 properties; the most commonly programmed are `show` (hide the header entirely) and the individual icon visibility flags. This is useful for read-only dashboards where you want a clean look without the interactive hover strip.

---

## Key properties (25-property form)

| Property | Type | Default | Selector-aware? | Notes |
|----------|------|---------|-----------------|-------|
| `show` | `boolean` | `true` | no | Show or hide the entire visual header icon strip. |
| `foreground` | `object` | — | no | Icon foreground color. `{"Solid": {"Color": "#hex"}}`. |
| `background` | `object` | — | no | Header background fill when hovered. |
| `transparency` | `number` | `0D` | no | Header background transparency (0=opaque, 100=transparent). |
| `border` | `boolean` | — | no | Show border around header area. |
| `showTooltipIcon` | `boolean` | `true` | no | Show the tooltip/info icon. |
| `showFocusModeIcon` | `boolean` | `true` | no | Show the focus mode (expand) icon. |
| `showFilterIcon` | `boolean` | `true` | no | Show the active filters icon. |
| `showDrillIcon` | `boolean` | `true` | no | Show the drill up/down controls. |
| `showShareIcon` | `boolean` | — | no | Show the share to Teams icon. |

Additional properties cover pin to dashboard, bookmark, custom options button, and hover styling. The full 25-property list varies slightly by visual type. Discover all properties by applying visual header formatting in Desktop and inspecting the resulting `visualContainerObjects.visualHeader` in `visual.json`.

---

## JSON location

```json
{
  "visual": {
    "visualContainerObjects": {
      "visualHeader": [
        {
          "properties": {
            "show":            { "Literal": { "Value": "false" } }
          }
        }
      ]
    }
  }
}
```

To hide only specific icons (keeping header visible):

```json
{
  "visual": {
    "visualContainerObjects": {
      "visualHeader": [
        {
          "properties": {
            "show":               { "Literal": { "Value": "true" } },
            "showFocusModeIcon":  { "Literal": { "Value": "false" } },
            "showFilterIcon":     { "Literal": { "Value": "false" } }
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
        "visualHeader": [
          {
            "show": false
          }
        ]
      }
    }
  }
}
```

Use this to suppress visual headers globally across all visuals in the theme.

---

## Gotchas

- `show: false` removes all header icons globally — the user cannot drill, focus, or see filters. Only suppress on published, view-only reports.
- The visual header is only visible on hover in editing/view mode; it does not render in exported PDF/PNG unless focus mode is active.
- Hiding the visual header does not hide the title bar — `visualContainerObjects.title.show` controls the title separately.
- Some icons (e.g., share to Teams, pin to dashboard) only appear when the report is published to the Power BI service, not in Desktop.
- The `visualHeaderTooltip` container (documented inline in per-type files) controls the content of the info/tooltip popup that appears when the tooltip icon is clicked — it is separate from `visualHeader`.
