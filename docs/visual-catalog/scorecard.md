# scorecard

**Type key:** `scorecard` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`scorecard` embeds a Fabric **metrics scorecard** (goals/metrics from a Fabric metric set) into a report page. It takes **no data roles** — the content comes from the bound scorecard artifact, not from the semantic model's fields — so all authoring happens through `visual.objects` (`scorecard`, `goals`, `columnHeaders`, `detailsPane`, `header`, `visualization`).
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use when the report must surface Fabric goals/metrics with their status, owner and check-in history alongside normal report visuals. For a hand-built KPI grid use `pivotTable` or a row of `cardVisual`s — those bind to the model and are far easier to test.
<!-- /MANUAL -->

## Roles

`scorecard` has **no data roles**. The `queryState` block in the minimal template is empty (`{}`); all content is authored via `visual.objects` and `visual.visualContainerObjects`.

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|

## Containers

- **columnHeaders** — see the generated property surface (Related, below)
- **detailsPane** — see the generated property surface (Related, below)
- **goals** — see the generated property surface (Related, below)
- **header** — see the generated property surface (Related, below)
- **scorecard** — see the generated property surface (Related, below)
- **visualization** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/scorecard.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object scorecard <object>
powerbi-report-author formatting search scorecard <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "scorecard",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### No data roles — `queryState` stays empty

Binding a field is impossible; role pre-flight on this type will reject any `fields` entry. The scorecard artifact reference is the data binding.

### Cannot be debugged with `09_Debug_Operations:visual`

There is no visual DAX query to reconstruct — the content is served by the Fabric metrics service, not the semantic model.

### Environment-coupled

The bound scorecard must exist in the target workspace. A report deployed to another workspace shows an empty/erroring visual.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-scorecard](../GUIDE.md#cli-scorecard)
- JSON format: [../GUIDE.md#json-scorecard](../GUIDE.md#json-scorecard)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/scorecard.md`
<!-- /MANUAL -->
