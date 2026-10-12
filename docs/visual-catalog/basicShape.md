# basicShape

**Type key:** `basicShape` · **Status:** legacy / deprecated · **Modern replacement:** `shape`

> **DEPRECATED / LEGACY visual type.** Prefer [`shape`](./shape.md) for new work.
> Documented because existing reports still use it — edit in place, build new visuals with the modern equivalent.

## Overview

<!-- MANUAL: overview -->
`basicShape` is the legacy decorative-shape visual (rectangle, oval, line, arrow) with a minimal surface: `fill`, `line`, `rotation`, `general`. Superseded by `shape`, which carries the full modern shape surface (custom corner radii, per-corner control, shadows, glow, images).
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Edit existing `basicShape` visuals in place. Build every new decorative element as `shape` — 685 instances in the R0101 corpus vs the legacy type.
<!-- /MANUAL -->

## Roles

`basicShape` has **no data roles**. The `queryState` block in the minimal template is empty (`{}`); all content is authored via `visual.objects` and `visual.visualContainerObjects`.

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|

## Containers

- **fill** — see the generated property surface (Related, below)
- **general** — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **line** — see the generated property surface (Related, below)
- **rotation** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/basicShape.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object basicShape <object>
powerbi-report-author formatting search basicShape <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "basicShape",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### No data roles

Purely decorative; `queryState` stays empty and role pre-flight rejects any `fields` entry.

### Do not swap the type key to modernize

`shape` uses different object names. Changing `visualType` in place drops the formatting silently — create a new `shape` and delete the old visual (additive first).

### Shapes stack over data visuals

z-order matters: a full-bleed `basicShape` above a chart swallows clicks. Check `position.z` when a visual stops responding.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-basicshape](../GUIDE.md#cli-basicshape)
- JSON format: [../GUIDE.md#json-basicshape](../GUIDE.md#json-basicshape)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/basicShape.md`
<!-- /MANUAL -->
