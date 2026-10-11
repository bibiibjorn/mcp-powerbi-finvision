# rdlVisual

**Type key:** `rdlVisual` · **Status:** modern

## Overview

<!-- MANUAL: overview -->
`rdlVisual` embeds a **paginated (RDL) report** inside a Power BI report page. Its data binding is not a normal field projection: the single role is `parameterValues`, which maps report-page state onto the paginated report's parameters. Formatting objects are all about the embed: `parameterMapping`, `reportInfo`, `toolbar`, `export`, `autoFilter`.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use to surface a pixel-perfect / printable artifact (statement, invoice, regulatory table) inside an interactive report, driven by the reader's current selections. Do not use it to work around a table-formatting limitation — the embed is slow and does not cross-filter.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `parameterValues` | no | yes | Column, Measure |

## Containers

- **autoFilter** — see the generated property surface (Related, below)
- **export** — see the generated property surface (Related, below)
- **parameterMapping** — see the generated property surface (Related, below)
- **reportInfo** — see the generated property surface (Related, below)
- **toolbar** — see the generated property surface (Related, below)

Plus the shared VCO chrome (`title`, `subTitle`, `background`, `border`, `padding`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`, `dropShadow`, `lockAspect`, `stylePreset`) — see [./_shared/containers-title.md](./_shared/containers-title.md) and siblings.

## Properties

Per-property types, enums and selectors for every object above are in the generated encyclopedia entry (the internal doc library `General/Reporting/VisualTypes/rdlVisual.md`, from `powerbi-report-author` CLI metadata). Live check:

```
powerbi-report-author formatting describe-object rdlVisual <object>
powerbi-report-author formatting search rdlVisual <regex>
```

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "rdlVisual",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->
### The role is `parameterValues`, not `Values`

This is the only visual type in the registry with that role name. Binding to `Values` produces an unparameterized embed.

### `parameterMapping` must match the RDL's parameter names exactly

A mismatch fails silently to the parameter default, so the embed shows plausible-but-wrong data. Verify against the paginated report's parameter list.

### One-way filtering

Report state flows in via parameters; nothing flows back out. The embed cannot cross-filter the page.

### Workspace-coupled and capacity-coupled

The referenced paginated report must exist in the target workspace and paginated workloads must be enabled on the capacity.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- MCP op: `07_Visual_Operations:get_roles_for_type` — canonical role list
- pbir CLI: [../GUIDE.md#cli-rdlvisual](../GUIDE.md#cli-rdlvisual)
- JSON format: [../GUIDE.md#json-rdlvisual](../GUIDE.md#json-rdlvisual)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- Full generated property surface: the internal doc library `General/Reporting/VisualTypes/rdlVisual.md`
<!-- /MANUAL -->
