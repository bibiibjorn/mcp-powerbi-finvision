# animatedNumber

**Type key:** `animatedNumber` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`animatedNumber` displays a single number that counts up/animates into place. Per the CLI metadata dump it exposes **no query roles** and only the `general` formatting object — the value comes from the visual's own configuration rather than a bound `Values` role, which makes it an unusual member of the card family.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Effectively never for analytical reporting. Use `cardVisual` for a bound KPI (full formatting surface, conditional formatting, reference labels) and reserve this type for decorative/landing-page numbers. Confirm the binding mechanism by dumping a created visual before relying on it.
<!-- /MANUAL -->

## Roles

`animatedNumber` has **no data roles**. The `queryState` block in the minimal template is empty (`{}`); all content is authored via `visual.objects` and `visual.visualContainerObjects`.

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|

## Containers

- **general** — see [./_shared/containers-general.md](./_shared/containers-general.md)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/animatedNumber.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object animatedNumber <object>
powerbi-report-author formatting search animatedNumber <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "animatedNumber",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### No query roles in the CLI metadata

`VISUAL_ROLES` therefore records an EMPTY role list rather than guessing a `Values` role. If a dumped visual proves otherwise, add the real role there and update this doc.

### One formatting object

`general` only. Everything else must be done through the shared VCO chrome — there is no `value`/`callout` object like `cardVisual` has.

### Animation is an accessibility concern

Motion-sensitive users are affected by count-up animation; prefer a static `cardVisual` in accessible reports.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-animatednumber](../GUIDE.md#cli-animatednumber)
- JSON format: [../GUIDE.md#json-animatednumber](../GUIDE.md#json-animatednumber)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/animatedNumber.md`
<!-- /MANUAL -->
