# realTimeLineChart

**Type key:** `realTimeLineChart` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`realTimeLineChart` is the streaming/push-dataset line chart: it holds a rolling time window in the client and repaints as new tuples arrive, rather than re-running a DAX query. Roles are the cartesian core only — `Category` (time), `Series`, `Y` — with a matching reduced formatting surface (`categoryAxis`, `valueAxis`, `dataPoint`, `legend`, `plotArea`).
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use only with a streaming/push dataset or an Eventstream-fed model where sub-minute freshness is the requirement. For any import or DirectQuery model use `lineChart` — it has the full analytics surface (trend lines, forecast, small multiples) that this type lacks.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Category` | yes | no | Column |
| `Series` | no | no | Column |
| `Y` | yes | yes | Measure |

## Containers

- **categoryAxis** — see the generated property surface (Related, below)
- **dataPoint** — see the generated property surface (Related, below)
- **general** — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **legend** — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **plotArea** — see [./_shared/containers-plotArea.md](./_shared/containers-plotArea.md)
- **valueAxis** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/realTimeLineChart.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object realTimeLineChart <object>
powerbi-report-author formatting search realTimeLineChart <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "realTimeLineChart",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### No `SmallMultiples`, no analytics pane

The role set is `Category`/`Series`/`Y` only. Trend lines, forecast, reference lines and small multiples are unavailable — do not port a `lineChart` spec onto this type expecting parity.

### Cannot be debugged through the visual DAX path

`09_Debug_Operations:visual` reconstructs a DAX query from `queryState`; a streaming visual has no equivalent server-side query, so timings/values will not reflect what renders.

### Window length is a dataset property, not a visual property

The retained history is defined by the push/streaming dataset, not by anything in `visual.json`.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-realtimelinechart](../GUIDE.md#cli-realtimelinechart)
- JSON format: [../GUIDE.md#json-realtimelinechart](../GUIDE.md#json-realtimelinechart)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/realTimeLineChart.md`
<!-- /MANUAL -->
