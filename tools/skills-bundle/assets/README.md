# Power BI + Fabric Skills for Claude Code

A bundle of Claude Code **skills**, **agents**, and a **rule** for working with Power BI
and Microsoft Fabric — DAX, semantic modeling, PBIP/PBIR report authoring, theming,
bookmarks, model/report auditing, query-plan analysis, and Fabric/Service operations.

Drop these into your `~/.claude/` and Claude Code will pick them up automatically.

---

## What's in here

### Skills (`skills/`)
| Skill | What it's for |
|---|---|
| `dax` | Write/review/optimize/debug DAX; SE/FE tracing |
| `powerbi-data-modeling` | Star schema, relationships, storage modes, Direct Lake, M/Power Query |
| `pbip` | PBIP/PBIR/TMDL file format, rename cascades, PBIX extraction |
| `tabular-editor` | TOM C# scripting, BPA rule authoring, TE CLI |
| `pbi-visual-author` | Add/bind/reformat a single visual |
| `pbir-page-builder` | Build/clone/redesign a whole report page |
| `pbi-theme` | Design/audit/enforce a report theme.json |
| `pbi-bookmarks` | Create/update/audit bookmarks + navigator |
| `pbi-custom-visuals` | Deneb (Vega), SVG-via-DAX, Python/R visuals |
| `pbi-lineage` | Impact analysis of a planned model change |
| `pbi-live-iterate` | Edit → reload running Desktop → screenshot loop |
| `pbi-model-audit` | Semantic-model quality/performance/BPA audit |
| `pbi-report-audit` | Report-page design-quality review |
| `pbi-query-plan` | DAX query-plan / xmSQL / CallbackDataID analysis |
| `autonomous-dax-debug` | Autonomously fix a wrong/blank measure until correct |
| `autonomous-pbi-build` | Build a report page end-to-end from a brief |
| `fabric` | Fabric & Power BI Service — workspaces, fab CLI, deploy, refresh |

### Agents (`agents/`)
`FabricAdmin`, `FabricAppDev`, `FabricDataEngineer`, `FabricMigrationEngineer`, `FabricIQ`,
`bpa-expression-helper`, `pbip-validator`, `query-listener`, `semantic-model-auditor`,
`visual-script-reviewer`.

### Rule (`rules/`)
`fabric-cli.md` — `fab` CLI version notes and tool-priority guidance for the `fabric` skill.

---

## Install

### Windows — easiest
**Double-click `Install (double-click me).bat`.** A window opens, installs the skills,
and tells you when it's done. (Don't double-click `install.ps1` directly — Windows opens
it in Notepad instead of running it.)

### Windows — from a terminal
```powershell
cd <unzipped-bundle-folder>
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### macOS / Linux / WSL (bash)
```bash
cd <unzipped-bundle-folder>
./install.sh
```

The installer copies `skills/`, `agents/`, and `rules/` into your `~/.claude/`.
It is **non-destructive**: if you already have a skill/agent of the same name, your
existing copy is backed up to `~/.claude/backups/skills-bundle-import/` first — nothing
is overwritten without a backup.

Restart Claude Code (or start a new session) after installing so the skills load.

---

## A note on MCP servers

Many of these skills drive MCP servers — `powerbi-finvision` (connect to Power BI
Desktop, run DAX, edit models/reports) for the PBI skills, and `ms-fabric-mcp` for the
`fabric` skill and `Fabric*` agents. This bundle assumes those servers are **already set
up** in your environment. The skills are the know-how; the MCP servers are the hands —
without them the skills can still give guidance but won't run live operations.

---

## What was intentionally left out

- **A private documentation library** these skills could optionally cite was removed.
  The skills are fully usable on their own bundled `references/` content — you just won't
  get the extra "canonical reference" deep-dives that pointed at the author's private docs.
- **Client/project-specific tooling** (a particular reporting repo, Teams/DevOps
  automation) was excluded — it wouldn't run on your machine anyway.

A `SANITIZATION-REPORT.txt` in this bundle lists exactly what was stripped.
