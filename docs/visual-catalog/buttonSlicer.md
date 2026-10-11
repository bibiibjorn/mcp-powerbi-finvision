# buttonSlicer

> **⚠ `buttonSlicer` is a UI / product name, NOT a PBIR `visualType` string.**
> Microsoft's own report-authoring reference is explicit: *"Rules applied to
> ButtonSlicer (default for **tile slicer** requests): 1. visualType is
> **`advancedSlicerVisual`**."* The rendered DOM carries the class
> `buttonSlicerVisual`, which is what CDP/UI-testing selectors match on — that
> class is **not** the `visual.visualType` value.
>
> **When authoring: write `visualType: "advancedSlicerVisual"`** and switch it to
> tile/button presentation via `visual.objects` (see Gotchas). This file exists
> because the GA-late-2025 "button slicer" is discoverable by that name; the
> roles registry tolerates `buttonSlicer` as an alias so a caller using the
> product name is not blocked, but the file written to disk must use
> `advancedSlicerVisual`.
>
> Sources: the internal doc library `Microsoft/skills/powerbi-report-authoring/references/slicers.md`
> (authoring rules), `General/Reporting/UI-Testing-CDP.md` (DOM class).
> Canonical property surface: [advancedSlicerVisual.md](./advancedSlicerVisual.md).

## Overview

<!-- MANUAL: overview -->
The **button slicer** (a.k.a. *tile slicer*) renders each distinct value of its
`Values` column as a clickable tile/button laid out in a grid or a single row,
instead of as a checkbox list or a dropdown. It went GA in late 2025 as a
first-class presentation mode of `advancedSlicerVisual`.

Because it is a presentation mode rather than its own visual type, its whole
property surface is `advancedSlicerVisual`'s: `layout` (rows / columns / tile
size / spacing), `shapeCustomRectangle` (tile shape and corner rounding),
`fillCustom` / `outline` / `accentBar` (per-state tile styling), `actionState`
(the "Advanced" toggles that unlock per-state formatting) and `selection`
(single vs multi select, force selection).
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use a button slicer when:

- The value set is **small and stable** (2–8 values) — period granularity
  (M / Q / S / Y), horizon (1Y / 2Y / 3Y / HTD), report variant
  (Standard / Detailed), currency mode (FX / No FX).
- The choice must be **visible at a glance without opening a dropdown** — the
  selected state is a filled tile, readable from across a room.
- You want a **segmented-control look** rather than a filter widget.

Avoid it when: the value set is large or unbounded (tiles overflow — use
`advancedSlicerVisual` in list/search mode), the values are hierarchical
(tiles are flat), or the slicer sits in a narrow rail (tiles need width).
<!-- /MANUAL -->

## Roles

Identical to [advancedSlicerVisual](./advancedSlicerVisual.md).

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Values` | yes | no | Column |
| `Label` | no | no | Measure, Aggregation |
| `Tooltips` | no | yes | Measure, Aggregation |

## Containers

See [advancedSlicerVisual.md § Containers](./advancedSlicerVisual.md#containers)
— the container set is the same object list; only the values differ.

The tile-specific ones worth knowing:

- **layout** — grid geometry (`rowCount`, `columnCount`, `orientation`,
  `fixedWidth` / `fixedHeight`, `showFixedSize`, `cellPadding`, `style`)
- **shapeCustomRectangle** — `tileShape`, `rectangleRoundedCurve*`
- **fillCustom** / **outline** / **accentBar** — per-state tile chrome
- **actionState** — `IsButtonsAdvanced` etc.; must be on for per-state formatting
- **selection** — `singleSelect`, `strictSingleSelect`, `selectAllCheckboxEnabled`

## Properties

See [advancedSlicerVisual.md § Properties](./advancedSlicerVisual.md#properties).
Full generated surface: the internal doc library
`General/Reporting/VisualTypes/advancedSlicerVisual.md`.

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "advancedSlicerVisual",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### `visualType` must be `advancedSlicerVisual`

Writing `"visualType": "buttonSlicer"` produces a visual Power BI Desktop
cannot render. There is no such type key. This is the single most likely
authoring error for this visual.

### Tile presentation lives in `visual.objects`, not `visualContainerObjects`

The button/tile look is `visual.objects.layout` + `shapeCustomRectangle` +
`fillCustom` — internal rendering. Setting `visualContainerObjects.title` etc.
only touches the chrome and will not turn a list slicer into tiles. Copy
`visual.objects` verbatim from a working tile slicer.

### Per-state formatting needs `actionState` turned on first

`fillCustom` / `outline` / `accentBar` accept selector-scoped state variants
(default / hover / selected / disabled), but the state selectors are ignored
until the matching `actionState.Is*Advanced` flag is true.

### `buttonSlicerVisual` is the DOM class, not the type

UI tests (`pbi_set_slicer {kind:"button"}`, `[role="button"].buttonSlicerVisual`,
state via `aria-pressed`) match on the rendered class. Do not reverse-engineer
a `visualType` from a CDP selector.

### Tile overflow is silent

Too many values for the configured `rowCount`/`columnCount` clips rather than
scrolls by default — check `layout.overFlowStyle` / `overFlowDirection` and
verify with a screenshot after authoring.
<!-- /MANUAL -->

## Related

- Canonical type doc: [advancedSlicerVisual.md](./advancedSlicerVisual.md)
- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-advancedslicervisual](../GUIDE.md#cli-advancedslicervisual)
- JSON format: [../GUIDE.md#json-advancedslicervisual](../GUIDE.md#json-advancedslicervisual)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- UI testing: the internal doc library `General/Reporting/UI-Testing-CDP.md` (button-slicer selectors)
<!-- /MANUAL -->
