# Visual Catalog — Index

Agent-first reference for every in-scope Power BI visual type.

Every `visualType` key documented here also has a roles entry in
`core/pbip/authoring/visual_roles.py` (`VISUAL_ROLES`) — enforced by
`tests/test_visual_roles_registry.py`, which drives its assertion off this
directory listing. Two rows are deliberately excluded from that rule because
their `visualType` string is a UI name or unverified: `buttonSlicer` (author as
`advancedSlicerVisual`) and the date-picker slicer (dump a created visual to
learn the key). Roles are a guard rail, not a schema — an uncatalogued type
produces a warning, never a blocked create.

| Visual type | R0101 instances | Primary roles | File |
|-------------|-----------------|---------------|------|
| `columnChart` | 26 | `Category`, `Legend`, `SmallMultiples`, `Y` | [columnChart.md](./columnChart.md) |
| `barChart` | 0 | `Category`, `Legend`, `SmallMultiples`, `Y` | [barChart.md](./barChart.md) |
| `clusteredColumnChart` | 0 | `Category`, `Legend`, `SmallMultiples`, `Y` | [clusteredColumnChart.md](./clusteredColumnChart.md) |
| `clusteredBarChart` | 1 | `Category`, `Legend`, `SmallMultiples`, `Y` | [clusteredBarChart.md](./clusteredBarChart.md) |
| `hundredPercentStackedColumnChart` | 0 | `Category`, `Legend`, `Y` | [hundredPercentStackedColumnChart.md](./hundredPercentStackedColumnChart.md) |
| `hundredPercentStackedBarChart` | 0 | `Category`, `Legend`, `Y` | [hundredPercentStackedBarChart.md](./hundredPercentStackedBarChart.md) |
| `lineChart` | 6 | `Category`, `Legend`, `SmallMultiples`, `Y` | [lineChart.md](./lineChart.md) |
| `areaChart` | 0 | `Category`, `Legend`, `SmallMultiples`, `Y` | [areaChart.md](./areaChart.md) |
| `stackedAreaChart` | 0 | `Category`, `Legend`, `SmallMultiples`, `Y` | [stackedAreaChart.md](./stackedAreaChart.md) |
| `hundredPercentStackedAreaChart` | 0 | `Category`, `Legend`, `SmallMultiples`, `Y` | [hundredPercentStackedAreaChart.md](./hundredPercentStackedAreaChart.md) |
| `lineClusteredColumnComboChart` | 28 | `Category`, `Legend`, `Y`, `Y2` | [lineClusteredColumnComboChart.md](./lineClusteredColumnComboChart.md) |
| `lineStackedColumnComboChart` | 4 | `Category`, `Legend`, `Y`, `Y2` | [lineStackedColumnComboChart.md](./lineStackedColumnComboChart.md) |
| `scatterChart` | 0 | `Category`, `Legend`, `Size`, `SmallMultiples`, `Tooltips`, `X`, `Y` | [scatterChart.md](./scatterChart.md) |
| `pieChart` | 0 | `Category`, `Legend`, `Values` | [pieChart.md](./pieChart.md) |
| `donutChart` | 42 | `Category`, `Legend`, `Values` | [donutChart.md](./donutChart.md) |
| `treemap` | 0 | `Category`, `Details`, `Values` | [treemap.md](./treemap.md) |
| `waterfallChart` | 2 | `Breakdown`, `Category`, `Y` | [waterfallChart.md](./waterfallChart.md) |
| `funnel` | 0 | `Category`, `Values` | [funnel.md](./funnel.md) |
| `ribbonChart` | 1 | `Category`, `Legend`, `Y` | [ribbonChart.md](./ribbonChart.md) |
| `realTimeLineChart` | 0 | `Category`, `Series`, `Y` | [realTimeLineChart.md](./realTimeLineChart.md) |
| `heatMap` | 0 | `Category`, `Series`, `Size`, `Tooltips`, `X`, `Y` | [heatMap.md](./heatMap.md) |
| `gauge` | 0 | `MaxValue`, `MinValue`, `TargetValue`, `Value` | [gauge.md](./gauge.md) |
| `kpi` | 0 | `Goal`, `Goals`, `Indicator`, `TrendLine` | [kpi.md](./kpi.md) |
| `card` | 0 | `Values` | [card.md](./card.md) |
| `cardVisual` | 67 | `Data` | [cardVisual.md](./cardVisual.md) |
| `multiRowCard` | 329 | `Values` | [multiRowCard.md](./multiRowCard.md) |
| `animatedNumber` | 0 | — | [animatedNumber.md](./animatedNumber.md) |
| `pivotTable` | 47 | `Columns`, `Rows`, `Values` | [pivotTable.md](./pivotTable.md) |
| `matrix` (legacy → `pivotTable`) | 0 | `Columns`, `Rows`, `Values` | [matrix.md](./matrix.md) |
| `tableEx` | 32 | `Values` | [tableEx.md](./tableEx.md) |
| `table` (legacy → `tableEx`) | 0 | `Values` | [table.md](./table.md) |
| `accessibleTable` | 0 | `Columns`, `Rows`, `Values` | [accessibleTable.md](./accessibleTable.md) |
| `dataQueryVisual` | 0 | `Values` | [dataQueryVisual.md](./dataQueryVisual.md) |
| `map` | 0 | `Legend`, `Location`, `Size` | [map.md](./map.md) |
| `filledMap` | 0 | `Legend`, `Location`, `Values` | [filledMap.md](./filledMap.md) |
| `azureMap` | 0 | `Color`, `Location`, `Size` | [azureMap.md](./azureMap.md) |
| `shapeMap` | 0 | `ColorSaturation`, `Location` | [shapeMap.md](./shapeMap.md) |
| `decompositionTreeVisual` | 2 | `Category`, `Values` | [decompositionTreeVisual.md](./decompositionTreeVisual.md) |
| `qnaVisual` | 0 | — | [qnaVisual.md](./qnaVisual.md) |
| `keyDriversVisual` | 0 | `Analyze`, `Expand`, `Explain` | [keyDriversVisual.md](./keyDriversVisual.md) |
| `aiNarratives` | 0 | — | [aiNarratives.md](./aiNarratives.md) |
| `scorecard` | 0 | — | [scorecard.md](./scorecard.md) |
| `slicer` | 1050 | `Values` | [slicer.md](./slicer.md) |
| `advancedSlicerVisual` | 246 | `Values`, `Label`, `Tooltips` | [advancedSlicerVisual.md](./advancedSlicerVisual.md) |
| `listSlicer` | 0 | `Tooltips`, `Values` | [listSlicer.md](./listSlicer.md) |
| `textSlicer` | 0 | `Values` | [textSlicer.md](./textSlicer.md) |
| `filterSlicer` | 0 | `Values` | [filterSlicer.md](./filterSlicer.md) |
| `buttonSlicer` → **`advancedSlicerVisual`** | 0 | `Values`, `Label`, `Tooltips` | [buttonSlicer.md](./buttonSlicer.md) |
| *date-picker slicer* (⚠ type key unverified) | 0 | `Values`? | [datePickerSlicer.md](./datePickerSlicer.md) |
| `textbox` | 2 | — | [textbox.md](./textbox.md) |
| `shape` | 685 | — | [shape.md](./shape.md) |
| `basicShape` (legacy → `shape`) | 0 | — | [basicShape.md](./basicShape.md) |
| `image` | 395 | — | [image.md](./image.md) |
| `actionButton` | 502 | — | [actionButton.md](./actionButton.md) |
| `bookmarkNavigator` | 386 | — | [bookmarkNavigator.md](./bookmarkNavigator.md) |
| `pageNavigator` | 0 | — | [pageNavigator.md](./pageNavigator.md) |
| `pythonVisual` | 0 | `Values` | [pythonVisual.md](./pythonVisual.md) |
| `scriptVisual` (R) | 0 | `Values` | [scriptVisual.md](./scriptVisual.md) |
| `rdlVisual` | 0 | `parameterValues` | [rdlVisual.md](./rdlVisual.md) |

## Shared references

- [containers-title.md](./_shared/containers-title.md)
- [containers-background.md](./_shared/containers-background.md)
- [containers-border.md](./_shared/containers-border.md)
- [containers-padding.md](./_shared/containers-padding.md)
- [containers-visualHeader.md](./_shared/containers-visualHeader.md)
- [containers-general.md](./_shared/containers-general.md)
- [containers-axes.md](./_shared/containers-axes.md)
- [containers-legend.md](./_shared/containers-legend.md)
- [containers-dataLabels.md](./_shared/containers-dataLabels.md)
- [containers-plotArea.md](./_shared/containers-plotArea.md)
- [conditional-formatting.md](./_shared/conditional-formatting.md)
- [field-binding.md](./_shared/field-binding.md)
- [selectors.md](./_shared/selectors.md)
- [literals.md](./_shared/literals.md)
