# pageNavigator

**Type key:** `pageNavigator` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`pageNavigator` renders one button per report page and wires each to page navigation automatically — the page list is derived from the report, not bound as data. It takes **no data roles**. Its object set mirrors `bookmarkNavigator`: `pages` (which pages, ordering, hidden-page handling), `layout` (grid geometry), `shape`, `fill`, `outline`, `text`, `accentBar`, `glow`, `shadow`, `rotation`.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use for a self-maintaining page-level nav bar — new pages appear automatically. Use `bookmarkNavigator` instead when the buttons must switch *views* on one page, and individual `actionButton`s when the targets are heterogeneous (mix of page, bookmark, drillthrough, URL).
<!-- /MANUAL -->

## Roles

`pageNavigator` has **no data roles**. The `queryState` block in the minimal template is empty (`{}`); all content is authored via `visual.objects` and `visual.visualContainerObjects`.

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|

## Containers

- **accentBar** — see the generated property surface (Related, below)
- **fill** — see the generated property surface (Related, below)
- **glow** — see the generated property surface (Related, below)
- **layout** — see the generated property surface (Related, below)
- **outline** — see the generated property surface (Related, below)
- **pages** — see the generated property surface (Related, below)
- **rotation** — see the generated property surface (Related, below)
- **shadow** — see the generated property surface (Related, below)
- **shape** — see the generated property surface (Related, below)
- **text** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/pageNavigator.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object pageNavigator <object>
powerbi-report-author formatting search pageNavigator <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "pageNavigator",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### The page list is implicit — and it grows

Adding a page to the report changes this visual's contents with no edit to `visual.json`. Use `pages` to exclude/limit, and re-screenshot after any page create/clone/delete.

### Hidden pages are governed by `pages`, not page visibility alone

Check the `pages` object before assuming a hidden page is excluded.

### No data roles — `queryState` stays empty

Role pre-flight rejects any `fields` entry on this type.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-pagenavigator](../GUIDE.md#cli-pagenavigator)
- JSON format: [../GUIDE.md#json-pagenavigator](../GUIDE.md#json-pagenavigator)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/pageNavigator.md`
<!-- /MANUAL -->
