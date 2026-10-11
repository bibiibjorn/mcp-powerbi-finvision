# datePickerSlicer

> **⚠ `visualType` string UNVERIFIED — confirm by dumping a created visual.**
> The date-picker slicer entered preview in June 2026 and went GA in
> August 2026. Its PBIR `visualType` key is **not documented in any source
> available to this repo**: it is absent from `docs/visual-catalog/`, from the
> the internal doc library `General/Reporting/VisualTypes/` encyclopedia (61 types, generated
> from `powerbi-report-author` CLI v0.1.1) and its `_coverage-report.md`, from
> `_visualContainerObjects.md`, from the MS Report Theme Schema v2.155, and
> from the Microsoft report-authoring skill references. It also does not appear
> in the 10-report / 18206-visual PBIR corpus behind the coverage report.
>
> `datePickerSlicer` here is a **placeholder filename**, chosen to match how
> the feature is named in prose. Before authoring one:
> 1. Create the visual by hand in Power BI Desktop.
> 2. Read its `visual.json` (`07_Visual_Operations:list` or the file directly).
> 3. Replace this file's name/type key with the real string, add it to
>    `core/pbip/authoring/visual_roles.py`, and update
>    [INDEX.md](./INDEX.md).
>
> Until then this doc records what IS knowable, and **deliberately does not add
> a `VISUAL_ROLES` entry** — a wrong key in the registry would validate a
> visual that Power BI cannot render. An unknown type is a warning, never a
> block, so leaving it out costs nothing.

## Overview

<!-- MANUAL: overview -->
The **date-picker slicer** is a dedicated calendar-popup slicer: instead of the
classic `slicer` in `'Between'` mode (which renders two inline date fields plus
a range track), it presents a single compact control that opens a month
calendar for picking a date or a date range.

Note what already exists and is NOT this visual:

- The classic `slicer` in `mode` `'Between'` / `'Before'` / `'After'` /
  `'RelativeDatePicker'` — a documented, GA, `visualType: "slicer"` path. Its
  `hideDatePickerButton` property (see `slicer.md` / `filterSlicer.md`) toggles
  the little calendar button inside that legacy control.
- `filterSlicer`, which shares the same `mode` enum.

If the requirement is "let the user choose a date range", the classic `slicer`
in `'Between'` mode is the **verified** option today and should be preferred
over guessing this visual's type key.

### Strongest lead: it may BE `filterSlicer`

`filterSlicer` (modern, documented, `Values` role) already carries the entire
date-picker property surface in `visual.objects` — objects **`calendarButton`**
(icon colour/size, corner radius, stroke), **`date`** (font + `hideDatePickerButton`),
**`dateRange`** (`includeToday`, `anchorDate`), **`dateRangeText`**, **`slider`**,
plus `data.mode` including `Between` / `Before` / `After` / `Relative` /
`RelativeTime` / **`RelativeDatePicker`** and `data.startDate` / `data.endDate`.
That is a purpose-built date control, not a generic list slicer.

**Check `filterSlicer` FIRST** when you dump a real date-picker slicer — the
most likely outcome is that there is no new `visualType` at all and the feature
is a mode of `filterSlicer` (exactly as the "button slicer" turned out to be
`advancedSlicerVisual`, see [buttonSlicer.md](./buttonSlicer.md)). Unverified,
so still do the dump.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use the date-picker slicer when:

- The user needs to choose an **arbitrary date or date range** (not a period
  from a hierarchy) and canvas space is tight — the popup calendar collapses to
  one row of chrome.
- A calendar affordance is genuinely clearer than a Year/Quarter/Month
  dropdown chain.

Prefer a Year/Period dropdown or a button slicer for the common reporting case:
arbitrary-date filtering usually indicates the date dimension is being used for
exploration rather than reporting, and it defeats period-over-period measures.
<!-- /MANUAL -->

## Roles

**Unverified.** By analogy with every other slicer in the family
(`slicer`, `filterSlicer`, `textSlicer`, `listSlicer`, `advancedSlicerVisual`),
the data role is expected to be a single `Values` binding accepting a
Date/DateTime column, possibly with `Tooltips`. Confirm from a dumped visual.

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Values` *(unverified)* | yes | no | Column (Date / DateTime) |

## Containers

**Unverified.** Expect the shared VCO chrome set
(`title`, `subTitle`, `background`, `border`, `padding`, `divider`,
`dropShadow`, `lockAspect`, `stylePreset`, `visualHeader`,
`visualHeaderTooltip`, `visualLink`, `visualTooltip`) — see
[./_shared/containers-title.md](./_shared/containers-title.md) and siblings —
plus slicer-family internals under `visual.objects` (a `date`/`calendar`
formatting object and a `slicerSettings`-style selection object).

## Properties

Not documented. Dump the created visual and, if this becomes a used type,
regenerate the the internal doc library `VisualTypes/<type>.md` entry via
`scripts/gen_visual_type_guides.py` so the full property surface is captured
from CLI metadata rather than hand-written.

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "<UNVERIFIED — dump a created visual>",
    "query": {
      "queryState": {}
    }
  }
}
```

Verified alternative today — classic slicer, `'Between'` mode:

```json
{
  "visual": {
    "visualType": "slicer",
    "objects": {
      "general": [
        { "properties": { "mode": { "expr": { "Literal": { "Value": "'Between'" } } } } }
      ]
    },
    "query": { "queryState": {} }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### Do not guess the `visualType`

A wrong type key produces a visual Desktop silently refuses to render, and the
error surfaces only on open. Dump a real one first. The roles registry
intentionally has **no** entry for this type for the same reason.

### `RelativeDatePicker` is a `mode`, not a visual type

`slicer.mode` and `filterSlicer.mode` both include `RelativeDatePicker` in
their enum. Seeing that string in a report does **not** mean the report uses
the new date-picker visual.

### `hideDatePickerButton` belongs to the classic slicer

Both `slicer` and `filterSlicer` expose `hideDatePickerButton` (bool). It
controls the calendar button of the legacy between-dates control — unrelated to
this visual.

### Height sizing for the classic between-slicer

If you fall back to `slicer` `'Between'`: `h = 60 + top + bottom` padding for
side-by-side date fields, `h = 84 + top + bottom` stacked (the internal doc library
`Microsoft/skills/powerbi-report-authoring/references/slicers.md`).
<!-- /MANUAL -->

## Related

- Verified alternative: [slicer.md](./slicer.md) (`'Between'` mode), [filterSlicer.md](./filterSlicer.md)
- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — returns a warning + this doc path for uncatalogued types
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Backlog item #15 of the 2026-08-21 MCP improvement backlog (private planning notes, not in this repo)
  ("Verify actual visualType strings by dumping created visuals").
<!-- /MANUAL -->
