# Golden PBIP fixture (`GoldenCo`)

A small, fully **synthetic** PBIP project (fake names, no client content) whose
file shapes mirror what the real R0105 / R0201 reports and S01 / S02 models
look like on disk (surveyed read-only on 2026-09-22 for the deep review).
Reuse it in any test that parses, edits or analyses PBIP files — copy it to
`tmp_path` first when a test writes (`shutil.copytree(GOLDEN, tmp_path / "GoldenCo")`).

Byte traits match Desktop: JSON = indent 2, **CRLF, no trailing newline, raw
UTF-8** (`Δ` is not `Δ`); TMDL = tabs, CRLF, trailing newline.
`.gitattributes` (`* -text`) keeps the bytes exact through git.

Accessors that read these shapes: `core/pbip/pbir_model.py`. Pinned by
`tests/test_review_g_pbir_model.py`, `tests/test_review_g_pbip_scanner.py`,
`tests/test_review_a_pbip_cache.py`.

## Layout

```
GoldenCo/
  RPT-Sales.pbip        -> artifacts[].report.path = RPT-Sales.Report
  RPT-Finance.pbip      -> RPT-Finance.Report
  RPT-Remote.pbip       -> RPT-Remote.Report
  MDL-Core.pbip         -> MDL-Core.Report  (DOES NOT EXIST: model-only .pbip)
  RPT-Sales.Report/     definition.pbir byPath ../MDL-Core.SemanticModel
  RPT-Finance.Report/   definition.pbir byPath ../MDL-Core.SemanticModel
  RPT-Remote.Report/    definition.pbir byConnection (no local model)
  MDL-Core.SemanticModel/  definition.pbism + definition/*.tmdl
```

## Shapes and the findings they guard

| Shape | Where in the fixture | Real-world evidence | Findings |
|---|---|---|---|
| Thin reports bound to a shared model through `definition.pbir` `datasetReference.byPath.path` (relative, `../X.SemanticModel`); report stem ≠ model stem | `RPT-*.Report/definition.pbir` | every G01/G02 report (`R0105 → ../S01-SemanticModel.SemanticModel`) | G-06, H-13, G-10 |
| Several reports sharing one model; a folder with several `.Report` dirs | `RPT-Sales`, `RPT-Finance` | G01: 6 reports on S01; G02: 4 on S02 | H-13, A-13 |
| Model-only `.pbip` naming a missing `.Report` → falls back to the same-stem `.SemanticModel` | `MDL-Core.pbip` | `S01-SemanticModel.pbip → S01-SemanticModel.Report` (missing); `S02-SemanticModel.pbip → R0200-SemanticModel.Report` | G-06 |
| `byConnection` report (no local model) | `RPT-Remote.Report` | Desktop "live connection" shape | G-06 |
| `definition/pages/pages.json` (`pageOrder`, `activePageName`, `landingPageName`) — never `definition/pages.json` | `RPT-Sales.Report/definition/pages/pages.json` | both reports | H-03 |
| `page.json` string enums: `displayOption: "FitToPage"`, `visibility: "HiddenInViewMode"`, `type: "Drillthrough"` / `"Tooltip"`, `pageBinding{name,type,parameters[{name,boundFilter,fieldExpr}]}`, drillthrough filter `howCreated: "Drillthrough"` | pages `ab02details…`, `ac03tooltip…` | R0201 (22 FitToPage, 2 HiddenInViewMode) | H-04 |
| Page interactions under `visualInteractions` (`{source,target,type}`) | page `aa01overview…` | 41 real pages, 0 with `interactions` | H-08 |
| Visual-level filters at the TOP-LEVEL `filterConfig.filters` (never `visual.filters`), incl. `filterSortOrder` | every filtered visual | R0105: 513 visuals / 0 `visual.filters` | F-03, G-09, H-09, H-22 |
| Slicer with **filter-level** inverted selection: `filterConfig.filters[].objects.general[].properties.isInvertedSelectionMode = true` + `Not(In …)` condition; the slicer's own selection in `visual.objects.general[].properties.filter.filter` | `vs01slicerinverted01` | R0105 194/196, R0201 40/41 | F-04, H-10 |
| Slicer with **visual-level** inverted flag `visual.objects.data[].properties.isInvertedSelectionMode` (filter entry has no own flag) | `vs02slicervisinv0002` (`advancedSlicerVisual`) | R0105 2, R0201 1 | F-04, H-10 |
| Inverted filter with NO `filter`/`Where` = "exclude nothing" (all selected) | `vt01tableinverted001` (`tableEx`) | R0105 13, R0201 36 | H-10 |
| Matrix with a MEASURE visibility-gate filter (`Advanced`, `Comparison` kind 0, `1L`), a range `And(Comparison 2 …, Comparison 4 …)`, a Categorical `In` with an apostrophe literal (`'O''Brien Isles'`), a not-blank `Not(Comparison 0 null)`, and a field-only measure filter (no `filter`) | `vm01matrixfilters001` (`pivotTable`) | R0201 `UTL \| Show In Slot B = 1L`; R0105 range slicer `2 … / 4 …` | F-03, G-09, H-11, H-22 |
| TopN filter = `In` over `Table: {SourceRef: subquery}`, `From[{Type: 2, Expression.Subquery…Top}]` | `vd01donuttopn0000001` | R0105 12 TopN filters | F-04c, H-11d |
| RelativeDate filter (`Between` with `DateSpan`/`DateAdd`/`Now`) — **standard Desktop shape, NOT observed in the corpus** | `vl01linereldate00001` | none (0 in R0101/R0105/R0201) | F-04c, H-11d |
| `cardVisual` value role = `Data`; legacy `card` role = `Values`; card font on `visual.objects.value` + `layout.calloutSize` | `vc01cardvisual000001`, `vc02cardlegacy000001` | R0201 134 cardVisual / 61 card | CLAUDE.md §8 |
| Group container (`visualGroup{displayName, groupMode: "ScaleMode"}`, page-absolute position) + child with `parentGroupName` and GROUP-LOCAL position | `vg01group…`, `vg02groupchild000001` | R0201 574 groups | H-19, H-31 |
| Literals: `'text'` (quote doubled), `12D`, `12L`, `true`, `null`, font-family literal `'''Segoe UI'', …'`, raw non-ASCII measure name `Net Δ Value` | across visuals | corpus literal census (84k str, 82k D, 25k L) | H-04, H-15 |
| Action button → bookmark: `visual.visualContainerObjects.visualLink[].properties{type:'Bookmark', bookmark:'<id>'}` | `vb01actionbutton0001` | R0105 793 `bookmark` keys | G-11 |
| Bookmark navigator → group: `visual.objects.bookmarks[].properties{bookmarkGroup, selectedBookmark}` | `vb02bookmarknav00001` | R0201 54 navigators | G-11 |
| `bookmarks/bookmarks.json` `items[]`: group with `children` as **plain strings** + a top-level `{name}` item; `*.bookmark.json` with `explorationState.activeSection` | `RPT-Sales.Report/definition/bookmarks/` | R0201 163 / R0105 334 string children, 0 objects | H-06 |
| TMDL `///` descriptions on tables, measures, columns, roles, functions | all `tables/*.tmdl` | S01/S02 | (parser WPs) |
| Measure with `isHidden` AFTER a multi-line expression | `Sales[Amount Guarded]` | S02 `m_Measures` | (parser WPs) |
| `formatStringDefinition = <UDF call>` + `changedProperty = IsHidden` | `Sales[Amount Dynamic]`, `m Metrics[Net Δ Value]` | S02 `formatStringDefinition = FSD_Shared ()` | (parser WPs) |
| Calculation group with 3 items (incl. multi-line item + item-level `formatStringDefinition`) | `tables/Time Calc.tmdl` | S02 `c_Monthly_vs_Cumulative` | (parser WPs) |
| Culture file starting with `cultureInfo` + `linguisticMetadata` JSON + `contentType: json` | `cultures/en-US.tmdl` | S02 `cultures/en-US.tmdl` | (parser WPs) |
| UDF `function` block (lambda form, `///` description, lineageTag) | `functions.tmdl` | S02 `functions.tmdl` | (parser WPs) |
| Multi-line RLS `tablePermission` filter | `roles/Region Reader.tmdl` | S02 `roles/RLS.tmdl` | (parser WPs) |
| Field parameter: `relatedColumnDetails / groupByColumn`, `extendedProperty ParameterMetadata`, `NAMEOF` partition | `tables/fp Metric.tmdl` | S02 `dfp_Period` | (parser/graph WPs) |
| Table name with a SPACE (`'Product Line'`) and with an APOSTROPHE (`'Owner''s Book'`, file `Owner's Book.tmdl`) | `tables/` | S01/S02 naming | (parser WPs) |
| Unused control objects: `m Metrics[Orphan Metric]`, `'Product Line'[Unused Code]` | `tables/` | — | unused-analysis tests |

## Known parser gaps this fixture exposes (other WPs)

As of this commit the offline TMDL analyzer (`TmdlModelAnalyzer`) parses the
fixture but gets several shapes wrong — they are left for the parser work
packages and deliberately NOT asserted here:
`'Owner''s Book'` is read as `Owner`; `isHidden` after a multi-line
expression is lost; `///` descriptions come back `None`; the 3-item
calculation group yields 1 item; the multi-line RLS filter expression is
`None`; the visual-level measure filters (`Show In Slot`, `Amount Dynamic`)
are reported as unused measures (G-09).

## Regenerating

The files are static. If a shape changes, edit the files directly and keep the
Desktop byte traits (CRLF, no trailing newline for JSON).
