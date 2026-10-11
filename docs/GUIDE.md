---
title: MCP-PowerBi-Finvision Guide
subtitle: User guide, client masking, tool reference, architecture, PBIR reference and agentic-stack index
author: Finvision
version: 7.21.0
---

> **Canonical source:** This `.md` file is the single source of truth for the server's documentation. The `GUIDE.pdf` in the same folder is a point-in-time export (`python build/md_to_pdf.py`) and may be out of date — always read the `.md` for current information. The tool and operation inventory is also served by the server itself (`10_Show_User_Guide`, resources `powerbi://docs/user-guide`, `powerbi://docs/tool-reference` and `powerbi://docs/tools/{tool}`), generated from the live tool registry: if it ever disagrees with this guide, the server is right.

# MCP-PowerBi-Finvision — Guide

One document, six Parts. **Part I** is the user guide (install, connect, every tool family with examples, workflows, troubleshooting). **Part II** covers client masking. **Part III** is the exhaustive tool reference (every operation and argument) and **Part IV** the module-level architecture; both are for developers and agents. **Part V** is the PBIR reference (the file format and the `pbir` CLI) and **Part VI** the index of the agentic stack around this server. The per-visual-type reference is separate, in [docs/visual-catalog/](visual-catalog/INDEX.md), and the design reasoning in [docs/design-principles/](design-principles/INDEX.md). `AGENTS.md` in the repo root is the compact project index.

## Table of contents

- [Part I — User guide](#part-i)
  - [1. What this server does](#1-what-this-server-does)
  - [2. Installation](#2-installation)
  - [3. Connecting to Power BI](#3-connecting-to-power-bi)
  - [4. Core concepts](#4-core-concepts)
  - [5. The tools](#5-the-tools)
  - [6. Common workflows](#6-common-workflows)
  - [7. Live report iteration and the Desktop Bridge](#7-live-report-iteration-and-the-desktop-bridge)
  - [8. Safety and gating](#8-safety-and-gating)
  - [9. Troubleshooting](#9-troubleshooting)
  - [10. Quick reference card](#10-quick-reference-card)
  - [Further reading](#further-reading)
- [Part II — Client masking](#part-ii)
  - [Masking on demand (Claude Code)](#masking-on-demand-claude-code)
  - [What is covered](#what-is-covered)
  - [Install](#install)
  - [Profiles](#profiles)
  - [Known limits](#known-limits)
  - [Remove](#remove)
  - [Troubleshooting](#troubleshooting)
  - [Best-practice review (2026-10-08)](#best-practice-review-2026-10-08)
- [Part III — Tool reference (every operation)](#part-iii)
  - [III.0 Protocol-level behaviour (every tool)](#iii0-protocol-level-behaviour-every-tool)
  - [III.0a `00_Client_Mode` — CORE](#iii0a-00_client_mode--core)
  - [III.1 `01_Connection` — CORE](#iii1-01_connection--core)
  - [III.2 `02_Model_Operations` — MODEL](#iii2-02_model_operations--model)
  - [III.3 `02_TMDL_Operations` — MODEL](#iii3-02_tmdl_operations--model)
  - [III.4 `02_QA_Linguistic` — PBIP](#iii4-02_qa_linguistic--pbip)
  - [III.5 `03_Batch_Operations` — BATCH](#iii5-03_batch_operations--batch)
  - [III.6 `04_Run_DAX` — QUERY](#iii6-04_run_dax--query)
  - [III.7 `04_Query_Operations` — QUERY](#iii7-04_query_operations--query)
  - [III.8 `05_DAX_Intelligence` — DAX](#iii8-05_dax_intelligence--dax)
  - [III.9 `05_Column_Usage_Mapping` — DAX](#iii9-05_column_usage_mapping--dax)
  - [III.10 `06_Analysis_Operations` — ANALYSIS](#iii10-06_analysis_operations--analysis)
  - [III.11 `07_Report_Operations` — PBIP](#iii11-07_report_operations--pbip)
  - [III.12 `07_PBIP_Operations` — PBIP](#iii12-07_pbip_operations--pbip)
  - [III.13 `07_Page_Operations` — PBIP](#iii13-07_page_operations--pbip)
  - [III.14 `07_Visual_Operations` — PBIP](#iii14-07_visual_operations--pbip)
  - [III.15 `07_Bookmark_Operations` — PBIP](#iii15-07_bookmark_operations--pbip)
  - [III.16 `07_Theme_Operations` — PBIP](#iii16-07_theme_operations--pbip)
  - [III.17 `08_Documentation_Word` — DOCS](#iii17-08_documentation_word--docs)
  - [III.18 `09_Debug_Operations` — DEBUG](#iii18-09_debug_operations--debug)
  - [III.19 `09_Validate` — DEBUG](#iii19-09_validate--debug)
  - [III.20 `09_Profile` — DEBUG](#iii20-09_profile--debug)
  - [III.21 `09_Document` — DEBUG](#iii21-09_document--debug)
  - [III.22 `10_Show_User_Guide` — CORE](#iii22-10_show_user_guide--core)
  - [III.23 `11_PBIP_Authoring` — PBIP](#iii23-11_pbip_authoring--pbip)
  - [III.24 `17_Desktop_Bridge` — DEBUG](#iii24-17_desktop_bridge--debug)
  - [III.25 `13_Query_Plan` — DEBUG](#iii25-13_query_plan--debug)
  - [III.26 `15_DAX_Refactor` — MODEL](#iii26-15_dax_refactor--model)
  - [III.27 `16_Dependency_Graph` — ANALYSIS](#iii27-16_dependency_graph--analysis)
  - [III.28 Ops added after the last full doc sync (verify in handler before relying)](#iii28-ops-added-after-the-last-full-doc-sync-verify-in-handler-before-relying)
  - [III.29 Headless CLI — `python -m src.cli` (F-11)](#iii29-headless-cli--python--m-srccli-f-11)
- [Part IV — Architecture](#part-iv)
  - [IV.1 Bootstrap & execution flow](#iv1-bootstrap--execution-flow)
  - [IV.2 `server/registry.py`](#iv2-serverregistrypy)
  - [IV.3 `server/dispatch.py` + `server/op_classification.py` + `server/request_context.py`](#iv3-serverdispatchpy--serverop_classificationpy--serverrequest_contextpy)
  - [IV.4 `server/response_pipeline.py`, `server/middleware.py`, `server/mcp_app.py`](#iv4-serverresponse_pipelinepy-servermiddlewarepy-servermcp_apppy)
  - [IV.5 `server/pbip_cache.py`](#iv5-serverpbip_cachepy)
  - [IV.6 `server/handlers/_authoring_utils.py`](#iv6-serverhandlers_authoring_utilspy)
  - [IV.7 Singletons & shared state](#iv7-singletons--shared-state)
  - [IV.8 `core/infrastructure/`](#iv8-coreinfrastructure)
  - [IV.9 `core/operations/` — CRUD managers](#iv9-coreoperations--crud-managers)
  - [IV.10 `core/dax/`](#iv10-coredax)
  - [IV.10b Other `core/` packages (not detailed above)](#iv10b-other-core-packages-not-detailed-above)
  - [IV.11 `core/tmdl/`](#iv11-coretmdl)
  - [IV.12 `core/pbip/`](#iv12-corepbip)
  - [IV.13 `core/analysis/`](#iv13-coreanalysis)
  - [IV.14 `core/comparison/`](#iv14-corecomparison)
  - [IV.15 `core/utilities/`](#iv15-coreutilities)
  - [IV.16 `core/autonomous/`](#iv16-coreautonomous)
  - [IV.17 Configuration](#iv17-configuration)
  - [IV.18 Environment variables](#iv18-environment-variables)
  - [IV.19 Build & packaging](#iv19-build--packaging)
  - [IV.20 Test infrastructure](#iv20-test-infrastructure)
- [Part V — PBIR reference](#part-v)
  - [PBIR JSON reference](#pbir-json-reference)
    - [1. File tree](#1-file-tree)
    - [2. `visual.json` — exhaustive top-level keys](#2-visualjson--exhaustive-top-level-keys)
    - [3. `visual.objects` vs `visualContainerObjects` — the CHROME vs INTERNAL distinction](#3-visualobjects-vs-visualcontainerobjects--the-chrome-vs-internal-distinction)
    - [4. Field binding syntax](#4-field-binding-syntax)
    - [5. Roles per visual type](#5-roles-per-visual-type)
    - [6. Literal value encoding](#6-literal-value-encoding)
    - [7. Conditional formatting structure](#7-conditional-formatting-structure)
    - [8. Selector metadata](#8-selector-metadata)
    - [9. Group visuals](#9-group-visuals)
    - [10. Known gotchas (PBIR file format)](#10-known-gotchas-pbir-file-format)
    - [Appendix A — Schema URLs (as of R0101, PBIR 2.0.0)](#appendix-a--schema-urls-as-of-r0101-pbir-200)
    - [Appendix B — Discovery cheat sheet](#appendix-b--discovery-cheat-sheet)
  - [pbir CLI reference (0.9.9, revalidated against 0.9.25)](#pbir-cli-reference-099-revalidated-against-0925)
    - [Changes in 0.9.25](#changes-in-0925)
    - [1. Global flags](#1-global-flags)
    - [2. Path syntax and selectors (mini-language)](#2-path-syntax-and-selectors-mini-language)
    - [3. Property setter path DSL](#3-property-setter-path-dsl)
    - [4. Command tree (exhaustive)](#4-command-tree-exhaustive)
    - [5. Visual type catalog (every type + roles)](#5-visual-type-catalog-every-type--roles)
    - [6. Container catalog per visual type](#6-container-catalog-per-visual-type)
    - [7. Entity types and containers](#7-entity-types-and-containers)
    - [8. Conditional formatting (CF) command surface](#8-conditional-formatting-cf-command-surface)
    - [9. Batch spec v2 schema](#9-batch-spec-v2-schema)
    - [10. Known gotchas (pbir CLI)](#10-known-gotchas-pbir-cli)
- [Part VI — Agentic stack index](#part-vi)
  - [1. The Golden Path](#1-the-golden-path)
  - [2. Autonomous Lifecycle Rules](#2-autonomous-lifecycle-rules)
  - [3. Cheat-Sheet Index](#3-cheat-sheet-index)
  - [4. Reference Doc Index](#4-reference-doc-index)
  - [5. MCP Op Quick-Reference](#5-mcp-op-quick-reference)
  - [6. Coordinate System — always resolve containers before hardcoding](#6-coordinate-system--always-resolve-containers-before-hardcoding)
  - [7. Visual Type Quick Lookup](#7-visual-type-quick-lookup)
  - [Maintenance](#maintenance)

<a id="part-i"></a>
# Part I — User guide

A complete user guide to the **MCP-PowerBi-Finvision** server (**28 tools**): a Model Context Protocol server that lets an AI agent (e.g. Claude) drive Power BI Desktop directly. The tools cover DAX, live model authoring, PBIP/PBIR report editing, debugging, and live report iteration through the Power BI Desktop Bridge — see §4 (Core concepts) for how they fit together.

This guide is for **end users** — people who use a Claude-style chat client and want their AI assistant to actually *do things* with Power BI: run DAX, refactor measures, fix slow visuals, build pages, audit reports.

For exhaustive technical detail (every tool, operation and argument) see [Part III](#part-iii); for the module-level deep dive see [Part IV](#part-iv). **AGENTS.md** in the repo root is the compact project index.

---

## 1. What this server does

The MCP-PowerBi-Finvision server connects an AI agent to a running Power BI Desktop instance and exposes structured operations the agent can call. Concretely, the agent can:

- **Read the model:** list tables, columns, measures, relationships, hierarchies, perspectives, RLS roles, partitions, calc groups, DAX user-defined functions
- **Run DAX:** execute queries with a result cache, capture full SE/FE timings and engine execution metrics with an in-process trace, and capture and analyze query plans (logical, physical, xmSQL)
- **Modify the model live:** create/update/delete measures, columns, tables, relationships — changes propagate immediately via TOM; `safe_delete` / `safe_rename` check the dependency graph first
- **Edit TMDL:** export the live model to TMDL files, find/replace, bulk rename, migrate measures between projects, format DAX (opt-in, via daxformatter.com)
- **Edit PBIP reports:** create pages, add visuals, bind fields, set filters, change themes, manage bookmarks, configure conditional formatting
- **Debug visuals:** combine PBIP layout with live data — execute the exact DAX a visual would generate, check why a card is blank, drill to row-level detail, compare original vs optimized measures
- **Audit and validate:** run best-practice analysis (a curated rule set of about 72 rules, plus Microsoft's Tabular Editor rule set on request), find unused columns, detect broken references, profile pages, check measures for cross-visual consistency, lint every measure's DAX at once
- **Refactor DAX safely:** preview/apply codemods with a staleness hash and a rollback journal; query a structured dependency graph of the model **and** the report
- **Iterate on a live report:** edit the report files on disk, then reload the running Power BI Desktop in place and take a screenshot — all through `17_Desktop_Bridge`, with no close/reopen for report edits

**What it does NOT do:** publish to the Power BI Service (use Fabric CLI for that), run on Mac/Linux (Windows-only), modify .pbix files (use .pbip projects), or close and reopen Power BI Desktop on its own — the server never closes Desktop, and it refuses to reload one that may hold unsaved work.

---

## 2. Installation

### 2.1 Requirements

| Requirement | Version |
|-------------|---------|
| OS | Windows 10/11 (64-bit) |
| Power BI Desktop | Latest |
| .NET Framework | 4.7.2 or higher |
| Python | 3.10 or higher (3.12+ recommended; the setup script installs 3.13 when missing) |
| Node.js + the Desktop Bridge CLI | Only for `17_Desktop_Bridge` (§2.5). CLI `0.x` and `1.0.0` are supported |

### 2.2 Install from package (`.mcpb`)

If your distribution ships a `.mcpb` bundle:

1. Download the `.mcpb`.
2. Open Claude Desktop → **Settings → MCP Servers → Install from file**.
3. Select the downloaded `.mcpb` file.
4. Restart Claude Desktop.

### 2.3 Install from source

The repo-root script **`MCP Server Setup.bat`** does the whole developer setup in nine steps: it checks prerequisites, finds (or installs) Python 3.12+, picks up `uv` when available, selects the clone location, creates the virtual environment (a re-run reuses it) and installs the requirements plus the optional masking engines, verifies the critical dependencies, registers the server in Claude Desktop, Claude Code and VS Code (GitHub Copilot agent mode, `code --add-mcp`), and sets up client masking on demand. `setup-dev.ps1` runs the same script from PowerShell.

Manually:

```bash
git clone https://github.com/bibiibjorn/mcp-powerbi-finvision.git MCP-PowerBi-Finvision
cd MCP-PowerBi-Finvision
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Then add this to your MCP client config (`%APPDATA%\Claude\claude_desktop_config.json` for Claude Desktop):

```json
{
  "mcpServers": {
    "mcp-powerbi-finvision": {
      "command": "C:/path/to/MCP-PowerBi-Finvision/venv/Scripts/python.exe",
      "args": ["C:/path/to/MCP-PowerBi-Finvision/src/run_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

Point `command` at the **venv** Python: the system Python does not have the server's dependencies. Restart your MCP client.

### 2.4 Verify the install

Ask your agent:

> "Detect running Power BI Desktop instances."

The agent should call `01_Connection { operation: "detect" }` and return the list of running instances with their PIDs and ports. If you get an error like "Power BI Desktop not found," start Power BI Desktop first. To see what the installed build offers, ask for the user guide: `10_Show_User_Guide` returns a compact index of all 28 tools.

### 2.5 Enable the Desktop Bridge (for live report reload)

`17_Desktop_Bridge` wraps Microsoft's Power BI Desktop Bridge CLI, which talks to a running Desktop over a local named pipe. It is optional, and needed only for "reload the report I just edited" and screenshots.

1. In Power BI Desktop enable **File → Options → Security → Desktop Bridge → "Enable external tool access to Power BI Desktop through secure local APIs"** (on by default; some builds list it under **Preview features** instead). An administrator can force it off with the registry policy `HKLM\SOFTWARE\Policies\Microsoft\Power BI Desktop`, value `DesktopNamedPipeBridge` (`1` or `0`).
2. Install the CLI: `npm install -g @microsoft/powerbi-desktop-bridge-cli`.
3. Check it: `17_Desktop_Bridge { operation: "status" }` lists the running instances and reports `bridge_cli_version`.

**CLI versions.** Version `0.x` and the first stable **`1.0.0`** both work. `1.0.0` moved the CLI entry point to `dist/cli.js` and made `dist/index.js` a silent library; the server therefore resolves the script from the installed package (`MCP_PBI_BRIDGE_CLI_JS` overrides it) and never runs `dist/index.js` on a CLI from 0.2.0 on. A CLI major newer than the tested one produces a warning in `status`; if a run exits successfully but prints nothing you get `cli_no_output` (§9.12).

---

## 3. Connecting to Power BI

### 3.1 Detect and connect

You always start with `01_Connection`:

```
01_Connection { operation: "detect" }
```

Returns something like:
```json
{
  "instances": [
    { "pid": 12345, "port": 51234, "file_full_path": "C:/.../MyReport.pbip", "model_index": 0 }
  ]
}
```

Instances are listed **newest first**. `file_path_confidence` (`high`, `low`, `unverified`) tells you how sure the server is about which file an instance has open.

Then connect:
```
01_Connection { operation: "connect", model_index: 0 }
```

Or let the server decide with the idempotent form: it reuses a healthy connection and otherwise connects to the instance you name (`prefer_pid`, `file_path`, or `model_index`):

```
01_Connection { operation: "ensure" }
```

`01_Connection { operation: "status" }` reports the current connection (port, database, generations) and whether a call is busy.

Most tools require an active connection. The session-level state (active connection, PBIP folder path) is shared across tool calls — you don't need to reconnect for every operation. All calls on the shared connection run one at a time; a caller that cannot get its turn within about 30 seconds receives `error_type: "busy"` with `retry_after`.

### 3.2 Multiple Power BI instances

If you have several PBI Desktop instances open, `detect` lists them all. Connect to the one you want by `model_index`:

```
01_Connection { operation: "connect", model_index: 2 }
```

To switch, just call `connect` again with a different `model_index`. Switching instances clears the cached results, so answers from the previous instance are never replayed; if you ever need a guaranteed round trip to the engine, pass `bypass_cache: true` to `04_Run_DAX` (there is no need to rename DAX variables any more).

Verify which instance you're connected to via the `database` field in the connection result.

### 3.3 Reconnect after a crash or port change

If Power BI Desktop's analysis-services port changes (after a save or refresh), reconnect in place:

```
01_Connection { operation: "reconnect" }
```

This re-acquires ADOMD/TOM with a server round-trip health probe, without losing the session's PBIP folder path. A tool call that finds the connection broken also reconnects once by itself.

### 3.4 Other targets: a published model (read-only) or a PBIP folder (offline)

Besides a running Desktop, `01_Connection` can target two more things. `01_Connection { operation: "status" }` always says which one is active in `target` (`desktop`, `xmla` or `offline`).

**A published model over XMLA — read-only.**

```
01_Connection { operation: "connect_xmla", workspace: "Finance", model: "S02-SemanticModel" }
```

Reads, DAX queries, query plans and traces run against the Service copy of the model. Every tool call that would change the model (`02_Model_Operations`, `03_Batch_Operations`, `15_DAX_Refactor` writes) is refused with `error_type: "service_read_only"`: this lane is for inspecting and measuring production, never for editing it. The token comes from `MCP_PBI_XMLA_TOKEN` or the MSAL token cache (`~/.fabric-mcp/xmla_token_cache.json`, override the path with `FABRIC_XMLA_TOKEN_CACHE`); without one you get `xmla_auth_required`. The workspace needs XMLA read enabled (Premium / Fabric capacity). `connect` or `ensure` takes you back to Desktop.

**A PBIP semantic model folder — offline, no Desktop.**

```
01_Connection { operation: "connect_folder", pbip_path: "C:/repo/Model.SemanticModel" }
```

`pbip_path` may be the `.pbip` file, the `.SemanticModel` folder or its `definition/` folder. The TMDL is loaded into an in-memory TOM database with Microsoft's own serializer, so `02_Model_Operations` reads and writes work as usual, and **every committed write is serialized straight back to the TMDL files**: only the `.tmdl` files that changed are rewritten, the result is re-read with the TOM parser, and the previous files are restored when it no longer loads. There is no engine: `04_Run_DAX`, query plans, traces and refreshes need a live target. The folder is refused (`project_open_in_desktop`) while Desktop has the same project open, because Desktop's next save would overwrite the offline edits; `allow_open_in_desktop: true` overrides that for reads. A model with DAX user-defined functions opens read-only, because the parser build may not round-trip them.

---

## 4. Core concepts

### 4.1 Live TOM vs offline PBIP

There are two modes of operation:

**Live (TOM) — `02_Model_Operations` and friends:**
- Power BI Desktop must be **open** with your model loaded.
- Changes propagate **immediately** — no file save, no reload.
- Best for: model authoring, measure CRUD, RLS, calc groups, perspectives.
- The changes exist in the running model's memory until you **save in Desktop**. Reloading Desktop from disk before that discards them (§7.2).
- Does **not** work on visual/page/theme files (those live in `.Report/definition/`, not the live AS instance).

**Offline (PBIP) — `07_*_Operations`, `11_PBIP_Authoring`, `02_QA_Linguistic`:**
- Operates directly on `.pbip` project files on disk.
- Power BI Desktop can **stay open**: after a batch of edits, `17_Desktop_Bridge` `reload_and_screenshot` loads the new report definition into the running Desktop in place (§7). Just never let Desktop *save* over pending disk edits — a save writes Desktop's old in-memory copy over them.
- Edits to the **semantic model's TMDL files** are the exception: the bridge reload does not apply them (§7.4).
- Best for: report authoring, visual creation, theme edits, bookmark management, Q&A schema.
- Returns `reload_required: true` so the agent knows the next step is a reload.

Some operations bridge both — for example `02_Model_Operations measure/rename` accepts an optional `pbip_path` and cascades the rename through PBIR JSON files (live model + on-disk references stay in sync).

### 4.2 Tool naming convention

Tools follow `NN_Category_Name` where `NN` is a category-order number:

- `01_*` — Connection
- `02_*` — Model authoring (live TOM, TMDL files, Q&A schema)
- `03_*` — Batch operations
- `04_*` — DAX execution and metadata queries
- `05_*` — DAX and column-usage analysis
- `06_*` — Whole-model analysis
- `07_*` — PBIP report editing
- `08_*` — Documentation
- `09_*` — Debugging, validation, profiling
- `10_*` — User guide
- `11_*` — PBIP authoring
- `13_*` — Query plans
- `15_*` — DAX refactoring (codemods)
- `16_*` — Dependency graph
- `17_*` — Desktop Bridge (live reload + screenshots)

There is no `12_*`, `14_*` or `SVG_*` tool: the old autonomous close/reopen lifecycle tool (number 12) was removed in 7.8 and the SVG template tool in 7.6, and calc-group migration lives in `02_Model_Operations` (`calculation_group` `detect` / `scaffold` / `list_patterns`).

### 4.3 Operations and parameters

Most tools have an `operation` parameter that selects the action. For example, `02_Model_Operations` exposes 16 object types and dozens of operations per type:

```
02_Model_Operations { object_type: "measure", operation: "create", table_name: "Sales", name: "Total", expression: "SUM(Sales[Amount])" }
```

If you forget the operation, the agent will get a list of valid choices in the error response.

**Canonical argument names.** Each concept has one canonical name: `query` (DAX text), `pbip_path`, `page_name` (page id **or** display name), `visual_name` (id or exact title) / `visual_id`, `table_name` / `measure_name`, `output_format`, `bypass_cache`, `confirm_destructive`. Older spellings (`dax_query`, `page`, `table`, `format`, `no_cache`, `confirmed`, …) still work, but the result then carries `deprecated_args`. This guide uses the canonical names. Arguments that only serve rare operations are left out of the tool list the model sees but remain valid; `10_Show_User_Guide { tool: "<name>" }` lists all of them (add `operation: "<op>"` to narrow to one operation).

### 4.4 Reload hints

When you edit PBIP files on disk, the response includes:

```json
{ "reload_required": true,
  "reload_op_chain": ["17_Desktop_Bridge:reload_and_screenshot"] }
```

This is how the agent knows the next step: reload the running Power BI Desktop in place and look at the result. For edits to the semantic model's TMDL files the answer is different — the result says `reload_applies: false` and `reopen_required: true` with the chain "close Desktop **without saving** (ask the user), then `17_Desktop_Bridge open`". Live-TOM ops don't set the flag — they propagate immediately.

### 4.5 Destructive operations

Some operations are flagged "destructive" (bulk delete, refresh) or "catastrophic" (full-model refresh, delete >10 items, restore from backup). The gate policy controls what happens:

- **`LIGHT` (default):** ordinary destructive ops pass with an advisory note; catastrophic ops are blocked unless you confirm.
- **`STRICT`:** all destructive ops blocked unless confirmed.

You can pass `confirm_destructive: true` in any single call as a break-glass override. See [§8 Safety and gating](#8-safety-and-gating).

### 4.6 Results, paging and timeouts

- **Envelope.** Every result has `success`; a failure adds `error_type` and `error` (plus `suggestion`, `retry_with` or `valid_values` where known).
- **Size control.** Every tool accepts `response_format` (`concise` drops prose and caps lists at 25; `detailed` is the default) and `page_size` + `next_token` to page the result's main list (`paginated_key` names it). A result above 450K characters is written to a file under `output/` and a summary with `_full_output` is returned.
- **Timeouts.** `timeout` means the call ran too long (a read, or a write that never started). `timeout_outcome_unknown` means a **write** may still be running: verify with the named check before retrying — an identical re-issue returns `write_in_progress`.

### 4.7 Tool profiles

The full tool list costs the agent's context about 75K characters. A session that only does one kind of work can advertise fewer tools. Set the environment variable before the server starts:

- `MCP_PBI_PROFILE=all` (default) — every tool.
- `MCP_PBI_PROFILE=model` — model, DAX and performance work: hides the five PBIR report-authoring tools (`07_Page_Operations`, `07_Visual_Operations`, `07_Bookmark_Operations`, `07_Theme_Operations`, `07_Report_Operations`) and `11_PBIP_Authoring`.
- `MCP_PBI_PROFILE=report` — report authoring and the live Desktop loop: hides `13_Query_Plan`, `15_DAX_Refactor`, `03_Batch_Operations`, `02_TMDL_Operations`, `02_QA_Linguistic`, `05_Column_Usage_Mapping`, `06_Analysis_Operations` and `08_Documentation_Word`.
- `MCP_PBI_TOOLS_EXCLUDE=08_Documentation_Word,02_QA_Linguistic` — hide more tools on top of any profile.

`01_Connection` and `10_Show_User_Guide` are always kept. A call to a hidden tool returns `error_type: "tool_hidden_by_profile"`; change the variable and restart the server to get it back. `01_Connection status` reports the active profile in `tool_profile`.

---

## 5. The tools

This section lists every tool family, what it does, when to use it, and a worked example. The authoritative, always-current list is `10_Show_User_Guide` (generated from the live registry); the exhaustive per-argument reference is [Part III](#part-iii). Examples use the canonical argument names (§4.3).

### 5.1 `01_Connection` — detect and connect

**Category:** CORE. Detect running Power BI Desktop instances and connect to one.

**Operations:**
- `detect` — list running PBI instances (newest first)
- `connect` — connect to one (by `model_index`)
- `reconnect` — re-acquire connection after a restart or port change
- `ensure` — idempotent: reuse a healthy connection, otherwise connect (`prefer_pid`, `file_path` or `model_index`)
- `status` — connection identity (`port`, `database`, generations), busy state, PBIP info, the active `target` and `tool_profile`; `probe: true` adds a health round trip
- `connect_xmla` — connect **read-only** to a published model (`workspace`, `model`); every model write is refused (§3.4)
- `connect_folder` — load a PBIP semantic model folder offline (`pbip_path`); writes go straight to the TMDL files (§3.4)

**Example:**
```
01_Connection { operation: "detect" }
01_Connection { operation: "connect", model_index: 0 }
01_Connection { operation: "reconnect" }
01_Connection { operation: "ensure" }
01_Connection { operation: "status", probe: true }
01_Connection { operation: "connect_xmla", workspace: "Finance", model: "S02-SemanticModel" }
01_Connection { operation: "connect_folder", pbip_path: "C:/repo/Model.SemanticModel" }
```

**Tip:** call `detect` whenever you've restarted PBI; ports change. The agent often calls `ensure` automatically before model operations.

---

### 5.2 `02_Model_Operations` — live TOM CRUD

**Category:** MODEL. The single biggest tool — covers tables, columns, measures, relationships, calc groups, partitions, hierarchies, perspectives, cultures, named expressions, OLS, RLS roles, annotations, field parameters, DAX user-defined functions, query groups, calendars and model-level operations.

Every call needs `object_type` + `operation`. PBI Desktop must be **open**. A typo in either is rejected as `invalid_operation` before anything runs, with the valid values listed.

##### Operations by object_type

| `object_type` | Operations |
|---------------|-----------|
| `table` | `list`, `describe`, `sample_data`, `create`, `update`, `delete`, `safe_delete`, `rename`, `safe_rename`, `refresh`, `generate_calendar`, `get_refresh_policy`, `set_refresh_policy`, `clear_refresh_policy`, `mark_as_date_table` |
| `column` | `list`, `get`, `statistics`, `distribution`, `create`, `update`, `delete`, `safe_delete`, `rename`, `safe_rename` |
| `measure` | `list`, `get`, `create`, `update`, `delete`, `safe_delete`, `rename`, `safe_rename`, `move`, `set_format_string_expression`, `set_detail_rows_expression`, `set_kpi`, `clear_kpi`, `get_kpi` |
| `relationship` | `list`, `get`, `find`, `create`, `update`, `delete`, `activate`, `deactivate`, `rename` |
| `calculation_group` | `list`, `create`, `delete`, `list_items`, `add_item`, `update_item`, `delete_item`, `set_precedence`, `update`, `rename`, `detect`, `scaffold`, `list_patterns` |
| `partition` | `list`, `describe`, `create`, `update`, `delete`, `set_mode`, `refresh` |
| `hierarchy` | `list`, `describe`, `create`, `update`, `delete`, `add_level`, `remove_level`, `rename_level` |
| `perspective` | `list`, `describe`, `create`, `delete`, `add_table`, `add_measure`, `add_column`, `add_hierarchy`, `remove_table`, `remove_measure`, `remove_column`, `remove_hierarchy`, `set_members` |
| `culture` | `list`, `describe`, `create`, `delete`, `set_translation`, `bulk_set_translations`, `export_translations`, `import_translations`, `translation_coverage` |
| `named_expression` | `list`, `describe`, `create`, `update`, `delete` |
| `ols_rule` | `list`, `set`, `remove` |
| `model` | `refresh`, `refresh_status`, `refresh_history`, `get_properties`, `set_properties`, `set_value_filter_behavior`, `history`, `diff`, `undo`, `replay`, `checkpoint_create`, `checkpoint_restore` |
| `role` | `list`, `create`, `update`, `delete`, `set_table_filter`, `clear_table_filter`, `add_member`, `remove_member`, `list_members`, `get_effective_permissions` |
| `annotation` | `list`, `get`, `set`, `delete` |
| `field_parameter` | `create`, `set_parameter_metadata`, `repair`, `add_field`, `remove_field`, `reorder_fields` |
| `function` | `list`, `get`, `create`, `update`, `delete`, `daxlib_search`, `daxlib_install` |
| `query_group` | `list`, `create`, `update`, `delete`, `assign`, `unassign` |
| `calendar` | `list`, `get`, `create`, `update`, `delete` |
| `any` | `list_properties`, `get_property`, `set_property` |

Behaviours worth knowing before you start:

- **`create` never overwrites.** A name that already exists is `error_type: "already_exists"` (measure names are unique model-wide). Pass `if_exists: "update"` (or use the operation `upsert`) to update an existing object instead. `update` never creates.
- **`safe_delete` / `safe_rename`** (tables, columns, measures) consult the dependency graph of the model **and** the report first and refuse when anything still depends on the object (`unsafe_delete`, `rename_collision`, `evidence_unproven`). Prefer them over plain `delete` / `rename`.
- **Renames cascade.** A rename rewrites every DAX object that references the old name (measures, calc columns and items, RLS filters, KPIs, format strings, detail rows), re-reads the object errors afterwards and **rolls back** if the rename introduced a new one (`rename_introduced_errors`). `preview: true` shows the exact diff and a `preview_hash` without writing. With `pbip_path` the rename also cascades through the report's PBIR files.
- **Enum values are strict** (case-insensitive): an unknown `data_type`, cardinality or permission is `invalid_parameters`, never a silent default.
- If SaveChanges reports an error but a read-back proves the change landed, you get `success: true` with a `post_write_warning` — do not retry.
- **Every live write is journaled** (`output/journal/<project>.jsonl`, next to the server, never in a repo): the object before and after, per write. `02_Model_Operations` with `object_type: "model"` reads it (`history`, `diff`) and uses it to `undo` a write, to `replay` edits a reload or crash threw away, and to `checkpoint_create` / `checkpoint_restore` a named snapshot (§5.2.16).

#### 5.2.1 Tables

```
02_Model_Operations { object_type: "table", operation: "list" }
02_Model_Operations { object_type: "table", operation: "describe", table_name: "Sales" }
02_Model_Operations { object_type: "table", operation: "sample_data", table_name: "Sales", max_rows: 50 }
02_Model_Operations { object_type: "table", operation: "create", table_name: "DimDate",
                      expression: "CALENDAR(DATE(2020,1,1), DATE(2030,12,31))" }
02_Model_Operations { object_type: "table", operation: "rename",
                      table_name: "Sales", new_name: "Fact Sales" }
02_Model_Operations { object_type: "table", operation: "delete", table_name: "Junk" }
02_Model_Operations { object_type: "table", operation: "refresh", table_name: "Sales" }
02_Model_Operations { object_type: "table", operation: "generate_calendar",
                      start_year: 2020, end_year: 2030 }
02_Model_Operations { object_type: "table", operation: "generate_calendar",
                      start_year: 2020, end_year: 2030, calendar_template: "445" }
```

`generate_calendar` takes `calendar_template`: `standard` (default), `iso_week` (adds `ISOYear`, `ISOWeek`, `ISOYearWeek`) or `445` / `454` / `544`, a 52/53-week retail calendar (`FiscalYear`, `FiscalYearStart`, `FiscalWeek`, `FiscalQuarter`, `FiscalPeriod`; a fiscal year starts on the first Monday on or after the 1st of `fiscal_start_month`, default 1, and 13-week quarters split into the three period lengths of the template; week 53 stays in the last period). Check the generated table with `04_Run_DAX` before building relationships on it.

`create` needs `expression` (DAX, a calculated table) or `m_expression` (Power Query, an import table); a table without either is refused. `refresh` with a `refresh_type` reprocesses one table.

#### 5.2.2 Columns

```
02_Model_Operations { object_type: "column", operation: "list", table_name: "Sales" }
02_Model_Operations { object_type: "column", operation: "statistics",
                      table_name: "Sales", column_name: "Amount" }
02_Model_Operations { object_type: "column", operation: "distribution",
                      table_name: "Sales", column_name: "Region", top_n: 20 }
02_Model_Operations { object_type: "column", operation: "create",
                      table_name: "Sales", column_name: "Margin",
                      data_type: "Decimal", expression: "Sales[Amount] - Sales[Cost]" }
02_Model_Operations { object_type: "column", operation: "rename",
                      table_name: "Sales", column_name: "Amount", new_name: "Net Amount" }
```

#### 5.2.3 Measures

```
02_Model_Operations { object_type: "measure", operation: "list" }
02_Model_Operations { object_type: "measure", operation: "create",
                      table_name: "Sales", measure_name: "Total Sales",
                      expression: "SUM(Sales[Amount])",
                      format_string: "#,##0;-#,##0" }
02_Model_Operations { object_type: "measure", operation: "update",
                      table_name: "Sales", measure_name: "Total Sales",
                      expression: "SUMX(Sales, Sales[Quantity] * Sales[Price])" }
02_Model_Operations { object_type: "measure", operation: "rename",
                      table_name: "Sales", measure_name: "Total Sales", new_name: "Sales Total",
                      preview: true }
02_Model_Operations { object_type: "measure", operation: "rename",
                      table_name: "Sales", measure_name: "Total Sales", new_name: "Sales Total",
                      pbip_path: "C:/path/MyReport.pbip", confirm_destructive: true }
```

The first `rename` is a dry run (`preview: true`): it lists every expression that would change and returns a `preview_hash`. The last form **cascades the rename through PBIR JSON references** — visual.json, bookmarks, filters all updated. Result includes `pbir_cascade: { files_modified, replacements, errors }` and propagates `reload_required: true`. A rename that rewrites at least one expression, or that carries `pbip_path`, is a catastrophic operation, so it needs `confirm_destructive: true`.

**Dynamic format string** (CL 1606+):
```
02_Model_Operations { object_type: "measure", operation: "set_format_string_expression",
                      table_name: "Sales", measure_name: "Total Sales",
                      expression: "IF([Total Sales] >= 1000000, \"€#,##0.0,,\"\"M\"\"\", \"€#,##0\")" }
```

Pass `expression: ""` (empty string) or `null` to clear the dynamic format string.

#### 5.2.4 Relationships

```
02_Model_Operations { object_type: "relationship", operation: "list" }
02_Model_Operations { object_type: "relationship", operation: "find",
                      from_table: "Sales", to_table: "DimDate" }
02_Model_Operations { object_type: "relationship", operation: "create",
                      from_table: "Sales", from_column: "DateKey",
                      to_table: "DimDate", to_column: "DateKey",
                      cross_filtering_behavior: "OneDirection", is_active: true }
02_Model_Operations { object_type: "relationship", operation: "deactivate",
                      relationship_name: "abc123" }
```

`create` refuses an exact duplicate (`duplicate_relationship`) and a second *active* path between the same two tables (`ambiguous_path`; `allow_ambiguous: true` overrides). `update` also takes the cardinalities, `security_filtering_behavior`, `rely_on_referential_integrity` and `join_on_date_behavior`.

#### 5.2.5 Calculation groups

```
02_Model_Operations { object_type: "calculation_group", operation: "list_patterns" }
02_Model_Operations { object_type: "calculation_group", operation: "detect" }
02_Model_Operations { object_type: "calculation_group", operation: "scaffold",
                      candidate: { ... },
                      calc_group_table_name: "Time Intelligence", date_column: "'Date'[Date]" }
02_Model_Operations { object_type: "calculation_group", operation: "create",
                      name: "Time Intelligence", precedence: 100 }
02_Model_Operations { object_type: "calculation_group", operation: "add_item",
                      group_name: "Time Intelligence", item_name: "YTD",
                      expression: "CALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))" }
02_Model_Operations { object_type: "calculation_group", operation: "set_precedence",
                      group_name: "Time Intelligence", precedence: 200 }
```

`update` also sets what a calculation group returns when several or no items are selected: `multiple_or_empty_selection_expression` and `no_selection_expression` (an empty string clears one; compatibility level 1605+), and `selection_expression_behavior` (`automatic`, `visual` or `nonvisual`; this is `Model.SelectionExpressionBehavior`, a MODEL-wide setting, compatibility level 1609+). A model below the required level gets `compatibility_level_too_low` with the level it needs and nothing is written.

`detect` finds patterns in your existing measures (`PY`, `YoY`, `YTD`, `MTD`, scenario, currency, granularity) and suggests calc groups as candidates. `scaffold` takes one candidate and returns ready-to-paste TMDL plus a single-call `create_args`. Both, and `list_patterns`, are read-only and never gated. `list` shows each group's precedence, items, ordinals and format strings; `list_items`, `add_item`, `update_item`, `delete_item` and `set_precedence` edit an existing group.

#### 5.2.6 Partitions

```
02_Model_Operations { object_type: "partition", operation: "list", table_name: "Sales" }
02_Model_Operations { object_type: "partition", operation: "set_mode",
                      table_name: "Sales", partition_name: "Default", mode: "Import" }
```

Modes: `Import`, `DirectQuery`, `Dual`.

#### 5.2.7 Perspectives

```
02_Model_Operations { object_type: "perspective", operation: "create", perspective_name: "Sales View" }
02_Model_Operations { object_type: "perspective", operation: "add_table",
                      perspective_name: "Sales View", table_name: "Sales" }
02_Model_Operations { object_type: "perspective", operation: "add_measure",
                      perspective_name: "Sales View", table_name: "Sales", measure_name: "Total Sales" }
```

Adding a measure/column auto-adds the parent table to the perspective if missing.

#### 5.2.8 Cultures (translations)

```
02_Model_Operations { object_type: "culture", operation: "create", culture_name: "fr-FR" }
02_Model_Operations { object_type: "culture", operation: "set_translation",
                      culture_name: "fr-FR", object_type_target: "measure",
                      table_name: "Sales", name: "Total Sales",
                      property_name: "caption", value: "Ventes Totales" }
02_Model_Operations { object_type: "culture", operation: "bulk_set_translations",
                      culture_name: "fr-FR",
                      translations: [
                        { object_type: "measure", table_name: "Sales", name: "Total Sales",
                          property: "caption", value: "Ventes Totales" },
                        { object_type: "table", name: "Sales",
                          property: "caption", value: "Ventes" }
                      ] }
```

The translated property is `caption`, `description` or `display_folder`. The bulk form does one `SaveChanges()` for the whole batch — much faster than per-item — and reports per-item failures in `errors`.

```
02_Model_Operations { object_type: "culture", operation: "export_translations",
                      culture_name: "nl-BE", file_format: "csv", output_path: "C:/exports/nl-BE.csv" }
02_Model_Operations { object_type: "culture", operation: "import_translations",
                      culture_name: "nl-BE", input_path: "C:/exports/nl-BE.csv", dry_run: true }
02_Model_Operations { object_type: "culture", operation: "translation_coverage" }
```

`export_translations` lists every translatable caption, description and display folder with its source value and the translation of each culture (all cultures unless `culture_name` is given); without `output_path` the rows come back inline, capped by `limit`. `import_translations` reads the same layout (`translations` rows, or `input_path` with a CSV or JSON file), applies one culture, never blanks an existing translation with an empty value, and writes in one journaled save; `dry_run: true` reports what would be set. `translation_coverage` gives, per culture, how many translatable properties are translated, by property, and the first `limit` missing ones.

#### 5.2.9 RLS roles

```
02_Model_Operations { object_type: "role", operation: "create", role_name: "EU_Only" }
02_Model_Operations { object_type: "role", operation: "set_table_filter",
                      role_name: "EU_Only", table_name: "Sales",
                      filter_expression: "Sales[Region] = \"EU\"" }
02_Model_Operations { object_type: "role", operation: "add_member",
                      role_name: "EU_Only", member_identifier: "user@example.com" }
```

Test the role later with `04_Query_Operations` `test_rls`, or run a query under it with `04_Run_DAX` `roles: ["EU_Only"]` (§6.8).

#### 5.2.10 Field parameters

```
02_Model_Operations { object_type: "field_parameter", operation: "create",
                      table_name: "Parameters", name: "Measure Choice",
                      fields: [
                        { label: "Sales", reference: "[Total Sales]", order: 0 },
                        { label: "Profit", reference: "[Total Profit]", order: 1 }
                      ] }
02_Model_Operations { object_type: "field_parameter", operation: "add_field",
                      table_name: "Measure Choice",
                      field: { label: "Margin", reference: "[Total Margin]", order: 2 } }
02_Model_Operations { object_type: "field_parameter", operation: "reorder_fields",
                      table_name: "Measure Choice",
                      labels_in_order: ["Profit", "Margin", "Sales"] }
02_Model_Operations { object_type: "field_parameter", operation: "remove_field",
                      table_name: "Measure Choice", label: "Margin" }
02_Model_Operations { object_type: "field_parameter", operation: "repair",
                      table_name: "Measure Choice" }
```

`create` builds the whole field-parameter table (columns, hidden flags, sort-by, parameter metadata) in one save and rolls back on failure; `repair` (alias `set_parameter_metadata`) re-applies that complete shape idempotently to an existing or half-built field parameter. `reorder_fields` requires you list every existing label exactly — missing or extra labels return an explicit error.

#### 5.2.11 More measure operations

```
02_Model_Operations { object_type: "measure", operation: "move",
                      table_name: "Sales", measure_name: "Total Sales", new_table: "_Measures" }
02_Model_Operations { object_type: "measure", operation: "safe_rename",
                      table_name: "Sales", measure_name: "Total Sales", new_name: "Sales Total",
                      pbip_path: "C:/path/MyReport.pbip" }
02_Model_Operations { object_type: "measure", operation: "safe_delete",
                      table_name: "Sales", measure_name: "Old Measure" }
02_Model_Operations { object_type: "measure", operation: "set_detail_rows_expression",
                      table_name: "Sales", measure_name: "Total Sales",
                      expression: "SELECTCOLUMNS(Sales, \"Order\", Sales[OrderID])" }
02_Model_Operations { object_type: "measure", operation: "get_kpi",
                      table_name: "Sales", measure_name: "Total Sales" }
```

`move` keeps the measure's lineage tag, KPI, detail rows, translations and perspective membership, and returns a `pbir_followup` because report visuals still name the old home table (rebind them with `07_Report_Operations`). `set_kpi` needs at least one of a target, status or trend expression; `clear_kpi` removes the KPI; `10_Show_User_Guide { tool: "02_Model_Operations", operation: "set_kpi" }` lists the exact arguments. `set_detail_rows_expression` sets (or, with an empty expression, clears) the measure's "detail rows" query.

#### 5.2.12 Hierarchies, named expressions and object-level security

```
02_Model_Operations { object_type: "hierarchy", operation: "list", table_name: "DimDate" }
02_Model_Operations { object_type: "hierarchy", operation: "describe",
                      table_name: "DimDate", hierarchy_name: "Calendar" }

02_Model_Operations { object_type: "named_expression", operation: "list" }
02_Model_Operations { object_type: "named_expression", operation: "create",
                      expression_name: "SourceFolder", expression: "\"C:\\Data\"" }

02_Model_Operations { object_type: "ols_rule", operation: "set",
                      role_name: "EU_Only", table_name: "Customer",
                      column_name: "Salary", permission: "None" }
```

`hierarchy` `update` renames a hierarchy, changes its display folder or hidden flag, or reorders its levels in one save (a rename returns a `pbir_followup`). `ols_rule` `set` without `column_name` applies table-level security; `permission` is `None`, `Read` or `Default`. Dropping a `named_expression` is gated as catastrophic.

#### 5.2.13 Annotations

```
02_Model_Operations { object_type: "annotation", operation: "set",
                      target_type: "measure", table_name: "Sales", measure_name: "Total Sales",
                      annotation_name: "owner", annotation_value: "finance" }
02_Model_Operations { object_type: "annotation", operation: "list",
                      target_type: "table", table_name: "Sales" }
```

`target_type` picks the kind of object that carries the annotation (model, table, column, measure, partition, relationship, role, named expression, hierarchy, calculation item, perspective or culture). Annotations whose key starts with `PBI_` belong to Power BI itself; deleting one needs `force: true`.

#### 5.2.14 Model-level operations, refresh and calendars

```
02_Model_Operations { object_type: "model", operation: "get_properties" }
02_Model_Operations { object_type: "model", operation: "refresh",
                      refresh_type: "calculate", tables: ["Parameters"] }
02_Model_Operations { object_type: "model", operation: "set_value_filter_behavior",
                      behavior: "Independent" }

02_Model_Operations { object_type: "table", operation: "generate_calendar",
                      start_year: 2020, end_year: 2030, include_fiscal: true, fiscal_start_month: 7 }
02_Model_Operations { object_type: "table", operation: "get_refresh_policy", table_name: "Sales" }
```

`refresh_type` is `full`, `automatic` (the minimal refresh that is needed), `dataOnly`, `calculate`, `clearValues`, `defragment` or `indexes` (builds the explicit string / full-text indexes only; needs compatibility level 1707 - see §5.2.17). A refresh without `tables`, or covering ten or more tables, is **catastrophic** and needs `confirm_destructive: true`; `calculate` and `indexes` are only destructive. Table refresh policies (`set_refresh_policy` / `clear_refresh_policy`) take rolling-window and incremental grain + period arguments.

#### 5.2.15 DAX user-defined functions (UDFs)

```
02_Model_Operations { object_type: "function", operation: "list" }
02_Model_Operations { object_type: "function", operation: "create",
                      function_name: "AddVat",
                      parameters: [{ name: "amount" }],
                      expression: "amount * 1.21" }
02_Model_Operations { object_type: "function", operation: "daxlib_search", query: "date" }
02_Model_Operations { object_type: "function", operation: "daxlib_install",
                      package_id: "daxlib.example", prefix: "Money",
                      confirm_destructive: true }
```

Functions are read from the engine's DMVs (Desktop's object model does not expose them) and written as TMSL `createOrReplace` / `delete`, then verified by reading back (`verified`, or `write_not_visible`). `daxlib_search` browses the DaxLib package registry; `daxlib_install` validates every body first, installs the whole package in one transaction and is catastrophic (`force: true` overwrites colliding names). List them across the model with `05_DAX_Intelligence { operation: "list_udfs" }`.

---

#### 5.2.16 Write journal, undo, replay and checkpoints

Live model edits exist only in Desktop's memory until Desktop saves. A bridge reload (including `include_model: true`, which re-applies the on-disk TMDL) or a crash throws them away. The server therefore writes every live write to a **durable journal** — one line per write in `output/journal/<project>.jsonl` — holding the full before and after image of every object the write touched (a measure with its expression, format string, format-string expression, display folder, description, hidden flag and KPI; the same for columns, tables, calculation items, relationships, roles, hierarchies, UDFs and so on). It records `02_Model_Operations`, `03_Batch_Operations`, `15_DAX_Refactor apply`, UDF writes, and undo / replay themselves. Secrets in the arguments are redacted; a journal problem never fails your write — you get a `journal_warning` on that write's result instead.

Entries are **unflushed** until a disk-verified `17_Desktop_Bridge save` (or a Ctrl+S the bridge detects) proves they reached disk. `17_Desktop_Bridge status` shows the number as `journal_unflushed` in `dirty_sources`, and a refused reload names it.

```
02_Model_Operations { object_type: "model", operation: "history" }
02_Model_Operations { object_type: "model", operation: "history",
                      since: "2h", object: "Revenue", unflushed_only: true }
02_Model_Operations { object_type: "model", operation: "diff", entry_id: "J000012" }

02_Model_Operations { object_type: "model", operation: "undo", preview: true }
02_Model_Operations { object_type: "model", operation: "undo", count: 3 }
02_Model_Operations { object_type: "model", operation: "undo", entry_id: "J000012" }

02_Model_Operations { object_type: "model", operation: "replay", preview: true }
02_Model_Operations { object_type: "model", operation: "replay" }

02_Model_Operations { object_type: "model", operation: "checkpoint_create", checkpoint: "before-refactor" }
02_Model_Operations { object_type: "model", operation: "checkpoint_restore",
                      checkpoint: "before-refactor", preview: true }
02_Model_Operations { object_type: "model", operation: "checkpoint_restore",
                      checkpoint: "before-refactor", confirm_destructive: true }
```

- **`history`** lists entries newest first (`id`, time, tool and operation, touched objects, `flushed`). Filters: `since` (an ISO time or `30m` / `2h` / `1d`), `object` (text contained in an object reference such as `measure:Sales[Total]`), `unflushed_only`, `limit`. It also lists the checkpoints and works without a live connection (it then shows the newest journal).
- **`diff`** shows one entry property by property, before and after; long expressions come with a line diff.
- **`undo`** reverts the last `count` writes (default 1) or one `entry_id`, by re-applying the saved before-images in reverse order in **one** save. It first checks that each touched object still looks the way that write left it; if it does not (you or a later write changed it) the result is `journal_conflict` and nothing changes. `force: true` overrides that and, like any undo that would remove a table, culture or named expression, needs `confirm_destructive: true`. `preview: true` shows what would change without changing it. An undo is itself journaled, so it can be undone. Restoring a deleted table, column or partition re-creates it **empty** (`needs_refresh`) — refresh it afterwards.
- **`replay`** re-applies the writes that are still unflushed, oldest first, after a discarded reload or a crash: run `preview: true`, then the real call. Writes already in the model are skipped; a change whose object is now something else is reported under `conflicts` and left alone. Data is not touched — replay only re-creates metadata.
- **`checkpoint_create`** snapshots the whole model (including UDFs) to `output/journal/checkpoints/<name>.json` (`force: true` overwrites). **`checkpoint_restore`** compares the checkpoint with the live model and changes, adds and removes objects in one save so the model equals the checkpoint — preview first.

Verify the result with `04_Run_DAX`, not with a reload (a reload re-reads disk).

---

#### 5.2.17 Model properties, date tables, hierarchy levels, query groups, refresh state and text indexes

**Model properties** - `model` `set_properties` changes several model-level settings in ONE save.

```
02_Model_Operations { object_type: "model", operation: "set_properties",
                      description: "Group reporting model", culture: "en-US",
                      discourage_implicit_measures: true }
02_Model_Operations { object_type: "model", operation: "set_properties",
                      compatibility_level: 1707, allow_compatibility_upgrade: true,
                      confirm_destructive: true }
```

`culture` must look like a culture name (`en-US`). `compatibility_level` is different in kind: raising it is **irreversible** (the model can then only be opened by Desktop and Service builds that support the new level), so it needs `allow_compatibility_upgrade: true` **and** `confirm_destructive: true` (catastrophic), it can never lower the level (`compatibility_downgrade_refused`), and no other operation ever upgrades it silently. Read the current level with `get_properties`.

**Date table** - `table` `mark_as_date_table` does what "Mark as date table" does in Desktop, after checking the data.

```
02_Model_Operations { object_type: "table", operation: "mark_as_date_table",
                      table_name: "DimDate", date_column: "Date" }
```

The column must be `DateTime`; its values must be unique, non-blank, whole days and contiguous (one DAX query checks it). If anything fails the result lists the `problems` and nothing is written.

**Hierarchy levels** - add, remove or rename one level without rebuilding the hierarchy.

```
02_Model_Operations { object_type: "hierarchy", operation: "add_level",
                      table_name: "DimDate", hierarchy_name: "Calendar",
                      level_name: "Quarter", column_name: "Quarter", ordinal: 1 }
02_Model_Operations { object_type: "hierarchy", operation: "rename_level",
                      table_name: "DimDate", hierarchy_name: "Calendar",
                      level_name: "Quarter", new_name: "Qtr" }
02_Model_Operations { object_type: "hierarchy", operation: "remove_level",
                      table_name: "DimDate", hierarchy_name: "Calendar", level_name: "Qtr" }
```

`ordinal` is the 0-based position (default: at the end; later levels shift). The last level of a hierarchy cannot be removed (delete the hierarchy). A level rename returns a `pbir_followup`: report visuals bind levels by name.

**Role permissions** - `role` `get_effective_permissions` is a read-only summary per role: its model permission, every table permission with the RLS filter, table-level and column-level object security, and the tables the role has no entry for.

```
02_Model_Operations { object_type: "role", operation: "get_effective_permissions", role_name: "EU_Only" }
```

**Query groups** - the folders of the Power Query editor. A group is named by its folder path; it only organises named expressions and table partitions.

```
02_Model_Operations { object_type: "query_group", operation: "list" }
02_Model_Operations { object_type: "query_group", operation: "create",
                      query_group: "Sources\\Staging", description: "Raw loads" }
02_Model_Operations { object_type: "query_group", operation: "assign",
                      query_group: "Sources\\Staging", expression_name: "SourceFolder" }
02_Model_Operations { object_type: "query_group", operation: "assign",
                      query_group: "Sources\\Staging", table_name: "Sales", partition_name: "Sales" }
02_Model_Operations { object_type: "query_group", operation: "unassign", expression_name: "SourceFolder" }
02_Model_Operations { object_type: "query_group", operation: "update",
                      query_group: "Sources\\Staging", new_name: "Sources\\Raw" }
02_Model_Operations { object_type: "query_group", operation: "delete",
                      query_group: "Sources\\Raw", force: true }
```

`assign` and `unassign` take exactly one target: `expression_name`, or `table_name` (+ `partition_name` when the table has several partitions). `delete` refuses a group that still has members (`query_group_in_use`) unless `force: true`, which unassigns them first.

**Renames and calculation-group updates.**

```
02_Model_Operations { object_type: "relationship", operation: "rename",
                      relationship_name: "abc123", new_name: "Sales to Date" }
02_Model_Operations { object_type: "calculation_group", operation: "update",
                      group_name: "Time Intelligence", description: "Period logic",
                      precedence: 20, hidden: false }
02_Model_Operations { object_type: "calculation_group", operation: "rename",
                      group_name: "Time Intelligence", new_name: "Time Calc", preview: true }
```

No DAX or report refers to a relationship by name, so its rename has no cascade. A calculation group *is* a table: `rename` is a table rename (every DAX reference cascaded and verified, `preview` / `preview_hash` / `pbip_path` as for tables, catastrophic like any table rename). `update` refuses a `precedence` another group already uses.

**Refresh state.** Desktop keeps no refresh log, so these two are deliberately modest.

```
02_Model_Operations { object_type: "model", operation: "refresh_status" }
02_Model_Operations { object_type: "model", operation: "refresh_status", table_name: "Sales" }
02_Model_Operations { object_type: "model", operation: "refresh_history", since: "1d", limit: 10 }
```

`refresh_status` reads each partition's `State`, `RefreshedTime` and `ModifiedTime` from the open model (plus a per-table roll-up and the partitions that are not `Ready`). `refresh_history` lists the refreshes **this server** requested (from the write journal, §5.2.16): time, scope and `refresh_type`. What Desktop cannot provide: start/end time, duration, row counts or error text of a refresh, and any refresh started from Desktop's own UI or by another tool. For a published model use the Service's refresh history.

**String and full-text indexes (September 2026).** Two column settings speed up text search; neither changes results.

```
02_Model_Operations { object_type: "column", operation: "update",
                      table_name: "Hotels", column_name: "NAME_HOTEL",
                      string_indexing_behavior: "full" }
02_Model_Operations { object_type: "column", operation: "update",
                      table_name: "Hotels", column_name: "DESCRIPTION",
                      full_text_indexing_behavior: "explicit" }
02_Model_Operations { object_type: "model", operation: "refresh",
                      refresh_type: "indexes", tables: ["Hotels"] }
```

- `string_indexing_behavior`: `auto` (default, built lazily in memory), `full` (built at refresh and stored), `explicit` (built only by a `refresh_type: "indexes"` refresh), `off`. Needs compatibility level **1707**.
- `full_text_indexing_behavior`: `full`, `explicit`, `off` (there is no `auto`). Needs level **1708** and is required by the DAX functions `TEXTCONTAINS` / `TEXTSIMILARITY`.
- String columns only. If the model's level is too low the call is refused (`compatibility_level_too_low`) and tells you how to raise it - it never raises the level itself. If the loaded AMO client does not know the property you get `feature_unsupported_by_client`.
- `refresh_type: "indexes"` is refused below level 1707 the same way. `column` `get` shows the current values.
- Best-practice rules `TEXTCONTAINS_REQUIRES_FULLTEXT_INDEX` (error), `APPROXIMATEDISTINCTCOUNT_IN_MEASURE` (warning: about 1.6 percent off, never for exact or financial counts) and `STRING_INDEX_ON_LOW_VALUE_COLUMN` (info) in `06_Analysis_Operations` `full` check the related mistakes.

#### 5.2.18 Calendars (calendar-based time intelligence)

A calendar maps the columns of a date table to time units, so the engine's calendar-based time intelligence (week-based and retail calendars included) knows which column is the year, the month, the week.

```
02_Model_Operations { object_type: "calendar", operation: "list", table_name: "Date" }
02_Model_Operations { object_type: "calendar", operation: "get", calendar_name: "Fiscal" }
02_Model_Operations { object_type: "calendar", operation: "create",
                      table_name: "Date", calendar_name: "Fiscal",
                      groups: [
                        { time_unit: "Year", primary_column: "Fiscal Year" },
                        { time_unit: "Quarter", primary_column: "Fiscal Quarter" },
                        { time_unit: "Month", primary_column: "Fiscal Month",
                          associated_columns: ["Fiscal Month Name"] },
                        { time_unit: "Date", primary_column: "Date" }
                      ],
                      time_related_columns: ["Is Working Day"] }
02_Model_Operations { object_type: "calendar", operation: "update",
                      calendar_name: "Fiscal", new_name: "Fiscal 4-4-5" }
02_Model_Operations { object_type: "calendar", operation: "delete", calendar_name: "Fiscal 4-4-5" }
```

`time_unit` takes the exact engine names: `Year`, `Semester`, `Quarter`, `Month`, `Week`, `Date` and the `…OfYear` / `…OfSemester` / `…OfQuarter` / `…OfMonth` / `DayOfWeek` forms (never plurals and never `Day`; a near miss gets a "did you mean"). The definition is checked before anything is written: calendar names are unique in the model, every column belongs to the host table and is used once, a time unit appears once, and a column maps to the same time unit in every calendar. `update` with `groups` replaces the whole mapping. Calendars need a recent compatibility level; the server never raises it for you (`model set_properties` does that, deliberately, and the change cannot be undone).

### 5.3 `02_TMDL_Operations` — TMDL automation

**Category:** MODEL. Export, find/replace, bulk rename, generate scripts, migrate measures, format DAX. `export` and `format_dax` work on the live model; the others edit TMDL files on disk, and a running Desktop only sees those edits after the model-reload rules of §7.4.

```
02_TMDL_Operations { operation: "export", output_dir: "C:/exports/MyModel" }

02_TMDL_Operations { operation: "find_replace",
                     tmdl_path: "C:/MyReport.SemanticModel/definition",
                     pattern: "old_table_name", replacement: "new_table_name",
                     regex: false, dry_run: true }

02_TMDL_Operations { operation: "bulk_rename",
                     tmdl_path: "C:/MyReport.SemanticModel/definition",
                     renames: [
                       { old_name: "Sales", new_name: "FactSales", object_type: "table" },
                       { old_name: "Total", new_name: "Total Sales", object_type: "measure",
                         table_name: "FactSales" }
                     ],
                     update_references: true, dry_run: false }

02_TMDL_Operations { operation: "migrate_measures",
                     source_path: "C:/Source.SemanticModel/definition",
                     target_path: "C:/Target.SemanticModel/definition",
                     skip_duplicates: true }

02_TMDL_Operations { operation: "format_dax", format_all: true,
                     max_line_length: 100, on_error: "skip" }
```

`generate_script` produces a migration/batch script from TMDL (`tmdl_path`, `source_path`, `target_path`) and writes nothing to the model.

`format_dax` posts each measure to daxformatter.com and writes the formatted DAX back via TOM. **It is opt-in:** because measure DAX is sent to a third-party service, the operation returns `error_type: "external_formatter_disabled"` until you set `"dax_formatter": {"enabled": true}` in `config/local_config.json` (or the environment variable `MCP_DAX_FORMATTER_ENABLED=1`). A formatted text is written back only when it is token-for-token equivalent to the original, and the run stops at `time_budget_s` (default 120) and returns the unprocessed measures in `remaining`. `find_replace`, `bulk_rename` and `migrate_measures` accept `dry_run: true` — always preview first.

#### 5.2.19 The TOM property long tail (`object_type: "any"`)

For a property no dedicated operation exposes, address the object with `ref` (`Table[Column]`, `Table[Measure]`, `table:Name`, `hierarchy:Table[Name]`, `relationship:Name`, `role:Name`, `expression:Name`, `calculation_group:Name`, `model`).

```
02_Model_Operations { object_type: "any", operation: "list_properties", ref: "Sales[Amount]" }
02_Model_Operations { object_type: "any", operation: "get_property", ref: "Sales[Amount]", property_name: "SummarizeBy" }
02_Model_Operations { object_type: "any", operation: "set_property", ref: "Sales[Amount]",
                      property_name: "IsAvailableInMDX", value: false, dry_run: true }
02_Model_Operations { object_type: "any", operation: "set_property", ref: "Sales[Amount]",
                      property_name: "Annotations[Owner]", value: "Finance" }
```

`list_properties` shows every scalar property with its type, value, whether it is settable, the valid enum names and the minimum compatibility level. Reading is open; **writing is an allow-list per object kind** (for example column `SummarizeBy`, `IsAvailableInMDX`, `SortByColumn`, `DataCategory`, `DisplayFolder`, `FormatString`; table `DataCategory`; relationship `RelyOnReferentialIntegrity`; model `SelectionExpressionBehavior`). Everything else is refused with `denied_property` and the reason (rename with `rename`, change expressions with `update`, lineage tags, structural properties). `Annotations[name]` and `ExtendedProperties[name]` work on every object that has them. Values are coerced to the property type (booleans, integers, enum names in any case, column and measure references; an empty string clears a reference). A compatibility level below the property's minimum is `compatibility_level_too_low` with the required level, before anything is saved. Each write is one journaled save (so `undo` works); `dry_run: true` validates and reports `previous` and `value` without writing. Properties that change query semantics or security (`ValueFilterBehavior`, `SelectionExpressionBehavior`, relationship filter behaviours) need `confirm_destructive` like other break-glass edits.

---

### 5.4 `02_QA_Linguistic` — Q&A synonyms (offline)

**Category:** PBIP. Edit the Q&A linguistic schema directly on disk: the `linguisticMetadata` block inside `<Model>.SemanticModel/definition/cultures/<culture>.tmdl` (TMDL projects), `model.bim`, or the legacy `linguisticMetadata.json`. **PBI Desktop must be closed** for writes — this is a model-side file, so the TMDL rules of §7.4 decide how a running Desktop gets to see the change. `pbip_path` may be the `.pbip`, its folder or the `.SemanticModel` folder; `culture` defaults to the model's culture.

```
02_QA_Linguistic { operation: "load",
                   pbip_path: "C:/MyReport.pbip", culture: "en-US" }

02_QA_Linguistic { operation: "audit",
                   pbip_path: "C:/MyReport.pbip", culture: "en-US" }

02_QA_Linguistic { operation: "set_include_in_qa",
                   pbip_path: "C:/MyReport.pbip", culture: "en-US",
                   entity: "Sales.Total Sales", include: true }

02_QA_Linguistic { operation: "add_synonym",
                   pbip_path: "C:/MyReport.pbip", culture: "en-US",
                   entity: "Sales.Total Sales",
                   synonym: "revenue", state: "Authored" }

02_QA_Linguistic { operation: "remove_synonym",
                   pbip_path: "C:/MyReport.pbip", culture: "en-US",
                   entity: "Sales.Total Sales", synonym: "revenue" }
```

Write operations accept `dry_run: true` (nothing is written; the result lists `would_change`). Audit returns `entities_without_extra_synonyms`, `entities_excluded_from_qa`, `entities_without_binding`.

---

### 5.5 `03_Batch_Operations` — bulk transactional CRUD

**Category:** BATCH. Bulk changes on ONE connection with ONE `SaveChanges` per call — 3–5x faster than serial calls. Every call names an `object_type` and a `batch_operation`:

| `object_type` | `batch_operation` values |
|---------------|--------------------------|
| `measures` | `create`, `update`, `delete`, `rename`, `move`, `move_display_folder` |
| `tables` | `create`, `update`, `delete`, `rename`, `refresh` |
| `columns` | `create`, `update`, `delete`, `rename`, `move_display_folder` |
| `relationships` | `create`, `update`, `delete`, `rename`, `activate`, `deactivate` |
| `changeset` | declarative multi-object delta (no `batch_operation`) |
| `ti_suite` | generate time-intelligence measures (no `batch_operation`) |

```
03_Batch_Operations { object_type: "measures", batch_operation: "create",
                      options: { use_transaction: true },
                      items: [
                        { table: "Sales", name: "Total Sales",
                          expression: "SUM(Sales[Amount])" },
                        { table: "Sales", name: "Total Profit",
                          expression: "[Total Sales] - SUMX(Sales, Sales[Cost])" }
                      ] }

03_Batch_Operations { object_type: "measures", batch_operation: "delete",
                      items: [{ table: "Sales", name: "Old Measure 1" },
                              { table: "Sales", name: "Old Measure 2" }],
                      confirm_destructive: true }

03_Batch_Operations { object_type: "measures", batch_operation: "move_display_folder",
                      items: [
                        { table: "Sales", name: "Total Sales",
                          display_folder: "Revenue / Totals" }
                      ] }
```

Defaults and limits: `options.use_transaction` defaults to `true` (atomic, all-or-nothing — the first failing item stops the batch and everything is undone). Maximum items per call: 200 measures or columns, 100 relationships, 30 tables. Set `use_transaction: false` + `continue_on_error: true` (both inside `options`) for fail-tolerant batching. `create` never overwrites (`already_exists`) unless `options.if_exists` is `"update"`. `dry_run` validates the required fields per item without writing. Results report `succeeded`, `failed`, `not_attempted`, `committed`, `rolled_back` and a per-item list.

**Declarative changeset.** Describe the whole delta once, preview it, then apply exactly that plan:

```
03_Batch_Operations { object_type: "changeset", preview: true,
                      changeset: { tables: { create: [ ... ] }, columns: { delete: [ ... ] } } }

03_Batch_Operations { object_type: "changeset", plan_hash: "<hash from the preview>",
                      changeset: { ... } }
```

`preview: true` returns a `plan_hash` and an operation summary; the apply call must pass that hash back and is rejected as `stale_plan` if the model changed in between. Measures are excluded from changesets (use `object_type: "measures"`).

**Time-intelligence suite.** Generate YTD / QTD / MTD / PY / delta variants of existing measures. The default is a reviewable plan; `apply: true` creates them:

```
03_Batch_Operations { object_type: "ti_suite",
                      base_measures: ["Total Sales", "Total Profit"],
                      date_column: "'Date'[Date]", variants: ["YTD", "PY"] }
```

Severity: `delete` with more than 10 items, every table delete, every rename batch, and a table refresh covering ten or more tables are **catastrophic** — they require `confirm_destructive: true`. Deletes of more than 20 items always need it. Under `LIGHT`, a mass delete clears automatically only when the dependency graph **proves** nothing uses the objects (report side included).

---

**Preview any write (F-01).** `preview: true` (alias `dry_run: true`) on a `02_Model_Operations` write or a `03_Batch_Operations` call runs the real operation against the model with the save suppressed, then rolls it back: nothing is saved and the model does not change. You get `would_change[]` (per object: before / after TMSL), `errors[]` (what the engine would refuse), `breaks[]` (report and model objects that depend on something the call would remove) and a `plan_hash`. Pass the `plan_hash` back with the same call (without `preview`) to apply it; if the model changed in between the call is refused with `stale_preview`. A preview cannot evaluate DAX (`validation.dax_expressions: not_validated`); refresh operations preview as a plan only.

**`patch`** (`object_type: "patch"`): select objects with a Dynamic-LINQ expression (`select`) and set properties on all of them (`patch`), or apply the Best Practice Analyzer fixes of the rules in `from_bpa`. Always run it with `preview: true` first; the apply needs the `plan_hash` the preview returned.

### 5.6 `04_Run_DAX` — execute DAX

**Category:** QUERY. Single-op tool — **the** DAX executor: `09_Debug_Operations run_dax`, `04_Query_Operations impersonate_run` and `13_Query_Plan evaluate_and_log` all forward to it, so roles, tracing, export and caching behave the same everywhere.

```
04_Run_DAX { query: "EVALUATE TOPN(10, 'Sales', [Total Sales], DESC)" }

04_Run_DAX { query: "EVALUATE SUMMARIZECOLUMNS('Date'[Year], \"Sales\", [Total Sales])",
            mode: "trace", clear_cache: true }

04_Run_DAX { query: "EVALUATE 'Sales'", roles: ["EU_Only"] }

04_Run_DAX { query: "EVALUATE 'Sales'", export_path: "C:/exports/sales.csv" }

04_Run_DAX { query: "EVALUATE SUMMARIZECOLUMNS('Date'[Year], \"Sales\", [Total Sales])",
            mode: "validate" }
```

The query must start with `EVALUATE` or `DEFINE` (leading comments are fine).

**Modes:**
- `auto` (default) and `simple` — run the query and return a row preview, no trace (a bare `EVALUATE <table>` is wrapped in `TOPN`)
- `trace` — full SE/FE timings, engine `execution_metrics` (CPU, peak memory, queue wait), the rows of the *same* traced run, and a `trace_id` other tools can reuse
- `analyze` — a real N-run benchmark (`runs`, default 3; `cold: true` clears the cache before every run) with average / min / max / median / deviation
- `evaluate_and_log` — the `EVALUATEANDLOG` events of one traced run
- `validate` — the engine parses and binds the query **without executing it**: `{ valid, errors[], elapsed_ms }`. See below.
- `build` — writes the query for you: `measures` (names or `{ name, expression }`), `group_by` (`"Table[Column]"`), `filters` (`"Date[Year] = 2025"`, `{ column, operator, values }` or a DAX table expression) and `order_by` become one `SUMMARIZECOLUMNS` query, returned as `query`; `execute: true` also runs it. No `query` argument is needed:

```
04_Run_DAX { mode: "build", measures: ["Total Sales"], group_by: ["Date[Year]"],
             filters: ["Product[Color] in {\"Red\", \"Blue\"}"], order_by: ["[Total Sales] desc"], execute: true }
```

**Useful arguments:** `top_n` (rows returned, default 100), `clear_cache` (trace mode only), `export_path` (a `.csv` with BOM or `.json`, streamed to disk without the row cap), `roles` (run under RLS role(s); `effective_user_name` needs a Service/SSAS connection — on Desktop use `04_Query_Operations simulate_as_user`), `bypass_cache`.

**Validate (parse / bind only).** `mode: "validate"` is the cheap way to prove new DAX before writing it into the model or running it. Each `EVALUATE <expr>` is moved into an unused branch of a trivial `ROW` query, so the engine resolves every table, column, measure and function name and checks types and syntax, but never evaluates the expression: a heavy query validates as fast as a light one, and a 10-second timeout stops it if the engine does evaluate something (`error_type: "timeout"` - unknown, not valid). An invalid query is a normal result, not a failure: `valid: false` and `errors[]` holding the engine's own text, with `Query (line, col)` positions translated back to your text (the untranslated text is in `raw_errors`). `success: false` only means the validation could not run (no connection, timeout, no `EVALUATE`). Limits: `ORDER BY` / `START AT` clauses are not part of the check (the result lists them under `not_validated`), so a name used only there is not verified; run-time errors (division by zero, circular references found during evaluation) are not found; a query without a top-level `EVALUATE` (a DMV `SELECT`) is refused. Nothing is cached or written. The resource `powerbi://docs/dax-query-rules` lists the rules that keep queries valid.

**Result cache.** Results are cached per connection and *model generation*: writes, connection changes and Desktop-side edits invalidate them, and results over 1,000 rows are never cached. Every result carries `cache: { hit, age_s }` and a `connection` block. Pass `bypass_cache: true` when you need a guaranteed server round trip. Values come back as native JSON types (numbers, booleans, ISO dates), not culture-formatted strings.

---

### 5.7 `04_Query_Operations` — metadata, RLS, DMV probes

**Category:** QUERY. Model metadata and introspection on the live model.

**Operations:** `data_sources`, `m_expressions`, `search_objects`, `search_string`, `info_view`, `dmv_probe`, `list_dmv_probes`, `vertipaq_report`, `vertipaq_analyzer`, `warm_cache`, and for RLS `test_rls`, `impersonate_run`, `simulate_as_user`, `rls_audit`. (`roles` still works but is deprecated — use `02_Model_Operations` with `object_type: "role"`.)

```
04_Query_Operations { operation: "data_sources" }
04_Query_Operations { operation: "m_expressions" }

04_Query_Operations { operation: "search_objects",
                      pattern: "sales", types: ["measures", "columns"] }

04_Query_Operations { operation: "search_string",
                      search_text: "TREATAS",
                      search_in_expression: true, search_in_name: false }

04_Query_Operations { operation: "info_view", view: "measures", top_n: 200 }

04_Query_Operations { operation: "test_rls",
                      role_name: "EU_Only",
                      test_table: "Sales" }

04_Query_Operations { operation: "impersonate_run",
                      roles: ["EU_Only"], query: "EVALUATE 'Sales'",
                      effective_user_name: "user@example.com" }

04_Query_Operations { operation: "simulate_as_user",
                      role_name: "EU_Only", as_user: "user@example.com",
                      query: "EVALUATE 'Sales'" }
```

`search_objects` is a case-insensitive substring match (not a regex). `data_sources` falls back to the TOM partition sources and shared M expressions when the model has no legacy data sources (Power Query models); `m_expressions` lists partition M sources and shared expressions/parameters. `impersonate_run` forwards to `04_Run_DAX` with `roles`; it is for Service/SSAS connections. `simulate_as_user` is the Desktop workaround that rewrites `USERPRINCIPALNAME()` to a literal. `info_view` reads the `INFO.VIEW.*` functions (measures, tables, columns, relationships) and falls back to the DMVs on older compatibility levels.

**RLS audit.** `rls_audit` checks every role for fail-open filters: it applies each role's filters as a set of probe identities (your own `as_user`, a user with no grant, the empty string, a casing variant, a B2B guest `#EXT#` form) and reports, per filtered table, the rows each identity sees against the unfiltered row count. An identity with no grant that sees rows is `fail_open` (critical).

```
04_Query_Operations { operation: "rls_audit", as_user: "jane@contoso.com" }
04_Query_Operations { operation: "rls_audit", roles: ["EU_Only"], as_user: "jane@contoso.com",
                      probe_identities: ["svc-account@contoso.com"] }
```

The result is a simulation (`simulation: "filter_rewrite"`): Power BI Desktop rejects `EffectiveUserName`, so the filters are rewritten, which is approximate (see `approximation.known_gaps`). The same engine runs as the `rls_audit` test type in a `09_Validate` suite.

**Memory and cache diagnostics.**

```
04_Query_Operations { operation: "vertipaq_report" }
04_Query_Operations { operation: "vertipaq_analyzer", view: "summary" }
04_Query_Operations { operation: "vertipaq_analyzer", view: "columns",
                      include_stats: false, export_path: "C:/exports/vpa.json" }
04_Query_Operations { operation: "warm_cache", measures: ["Total Sales"] }
```

`vertipaq_report` lists the top columns by RAM with a hot / warn / info verdict. `vertipaq_analyzer` has VertiPaq Analyzer parity (model size, cardinality, encoding, segments, partitions, relationships, findings); `view` is `summary` (default), `tables`, `columns`, `relationships`, `partitions`, `segments` or `all`, and `include_stats: false` stays DMV-only, which is much cheaper on a large model. `warm_cache` warms the VertiPaq cache without clearing it, so a warm benchmark starts from a comparable state.

**DMV probes** (no live connection needed for `list_dmv_probes`):

```
04_Query_Operations { operation: "list_dmv_probes" }
04_Query_Operations { operation: "dmv_probe", probe: "vertipaq_columns" }
04_Query_Operations { operation: "dmv_probe", probe: "calc_dependency" }
04_Query_Operations { operation: "dmv_probe", probe: "object_memory_usage" }
04_Query_Operations { operation: "dmv_probe", probe: "sessions" }
```

Available probes: `vertipaq_columns`, `vertipaq_dictionary`, `calc_dependency`, `object_memory_usage`, `object_activity`, `sessions`, `user_defined_functions`, `refresh_policies`.

---

### 5.8 `05_DAX_Intelligence` — DAX expression analysis

**Category:** DAX. Static analysis: context transitions, anti-patterns, optimization, dependency trees, impact analysis.

```
05_DAX_Intelligence { expression: "[Total Sales]", analysis_mode: "all" }

05_DAX_Intelligence { expression: "Sales[Margin %]", analysis_mode: "debug",
                      include_optimization: true, include_profiling: true }

05_DAX_Intelligence { operation: "dependencies",
                      table: "Sales", measure: "Total Sales", include_diagram: true }

05_DAX_Intelligence { operation: "impact",
                      table: "Sales", measure: "Total Sales" }

05_DAX_Intelligence { operation: "list_udfs" }

05_DAX_Intelligence { operation: "lint_model", min_severity: "medium", max_findings: 100 }

05_DAX_Intelligence { operation: "expand_measure", measure_name: "Margin %" }

05_DAX_Intelligence { operation: "export", output_path: "C:/audit.csv" }

05_DAX_Intelligence { operation: "find_clones", min_similarity: 0.9 }

05_DAX_Intelligence { operation: "explain", measure_name: "Margin %" }
```

`find_clones` groups copy-pasted DAX (exact, structural, near and fragment clones) and proposes a UDF signature or a calc-group item; `15_DAX_Refactor` transform `extract_udf` applies the UDF route with preview, value verification and rollback. `explain` returns the measure's structure — call tree, filter modifiers in order, context transitions, iterators, VARs, calc-group and RLS notes — and with `include_values: true` the VAR values at a filter context.

`analysis_mode` (for the default `analyze` operation):
- `all` (default) — full analysis with insights
- `analyze` — context analysis only
- `debug` — step through the expression
- `report` — generate readable report

`expression` may also be just a measure's name (`[Total Sales]`, or use `measure_name`): the analysis then runs on that measure's model expression. A near-miss name returns `measure_not_found` with suggestions — it is never silently replaced by a fuzzy match.

**Whole-model lint.** `lint_model` analyses **every** measure (and UDF) in one call and returns the findings worst-first, with a per-measure health score, a per-rule tally, and an `auto_fixable` / `fix_transform` hint that points at `15_DAX_Refactor` (§5.25). Filter with `min_severity`, `rule_ids`, `tables`, `max_findings` (default 200) and `suppress_rules`. Accept a finding by putting `// lint-disable RULE_ID` in the measure.

**Inlined view.** `expand_measure` emits a runnable `DEFINE … MEASURE … EVALUATE` script that inlines the measure's transitive measure dependencies and the UDFs it calls (`inline: true` returns one `CALCULATE`-inlined expression) — paste it into DAX Studio to profile a measure chain.

`export` writes ALL measures to a CSV; `dependencies` with `html: true` writes an HTML file.

---

### 5.9 `05_Column_Usage_Mapping` — column ↔ measure mapping

**Category:** DAX.

```
05_Column_Usage_Mapping { operation: "get_unused_columns",
                          tables: ["Sales", "Customer"] }

05_Column_Usage_Mapping { operation: "get_unused_columns_pbip",
                          pbip_path: "C:/MyReport.pbip" }

05_Column_Usage_Mapping { operation: "get_measures_for_tables",
                          tables: ["Sales"], group_by: "column" }

05_Column_Usage_Mapping { operation: "get_columns_for_measure",
                          table: "Sales", measure: "Total Sales" }

05_Column_Usage_Mapping { operation: "get_measures_for_column",
                          table: "Sales", column: "Amount" }

05_Column_Usage_Mapping { operation: "get_full_mapping", include_dax: false }

05_Column_Usage_Mapping { operation: "export_to_csv",
                          output_path: "C:/exports", group_by: "table" }

05_Column_Usage_Mapping { operation: "export_measures",
                          output_path: "C:/exports/measures.csv" }
```

`get_measures_for_tables` groups its answer by `table`, `column`, `measure` or `flat`.

**Always prefer `get_unused_columns_pbip` (offline)** — the live method has known false positives.

---

### 5.10 `06_Analysis_Operations` — model-wide analysis

**Category:** ANALYSIS.

```
06_Analysis_Operations { operation: "simple", mode: "all" }
06_Analysis_Operations { operation: "simple", mode: "measure", measure_name: "Total Sales" }

06_Analysis_Operations { operation: "full", scope: "all", depth: "balanced",
                         max_seconds: 60 }

06_Analysis_Operations { operation: "compare",
                         old_port: 51234, new_port: 51235 }

06_Analysis_Operations { operation: "compare",
                         old: { git_ref: "HEAD", pbip_path: "C:/repo/Sales.pbip" },
                         new: { pbip_path: "C:/repo/Sales.pbip" }, report: false }
```

`simple` is fast (2–5s), `full` runs BPA + perf + integrity (10–180s). `simple` `mode` values: `all`, `tables`, `stats`, `measures`, `measure`, `columns`, `relationships`, `roles`, `database`, `calculation_groups`, `storage`.

`full` builds ONE model snapshot and feeds it to everything: best-practice rules (a curated set of about 75), star-schema topology findings (fact-to-fact, snowflake dimensions, disconnected tables, missing date table), Power Query practices and integrity checks, returned as a single severity-ranked `issues` list (`issues_max` caps it). `ruleset` is `default`, `microsoft` (Microsoft's Tabular Editor BPARules) or `all`; `rules_path` adds a Tabular Editor rule file for that call only; `include_vertipaq: true` reads the storage statistics first so the cardinality and row-count rules can evaluate. A rule the engine cannot evaluate is reported as *unsupported* in `coverage`, never as a silent pass. Three rules cover the September 2026 text features: `TEXTCONTAINS_REQUIRES_FULLTEXT_INDEX` (error: a measure or calculated column searches a column that has no `fullTextIndexingBehavior` of `full` / `explicit`), `APPROXIMATEDISTINCTCOUNT_IN_MEASURE` (warning: about 1.6 percent error, never for exact or financial counts) and `STRING_INDEX_ON_LOW_VALUE_COLUMN` (info: a persisted string index on a key, relationship or low-cardinality column).

`compare` also takes `old` / `new` targets: `{ port }`, `{ pbip_path }` (a `.pbip`, `.SemanticModel` or `definition` folder), `{ git_ref, pbip_path }` (the project as committed at that ref, read with `git show` into a temp folder - nothing is checked out) or `{ xmla: { workspace, model } }` (a published model, read-only; this variant has not been verified against a live workspace). All of them go through the same engine, and the result separates `affects_results` (changed DAX, relationships, roles, sources, added / removed / renamed objects) from `cosmetic` (formatting-only DAX, descriptions, display folders, format strings, hidden flags, annotations), so it works as a deploy pre-check. `report: false` skips the HTML.

`compare` diffs two open models. With exactly two Desktop instances open, omit both ports and the older instance is treated as "old"; the report (HTML, optionally JSON) lists renames and moves (matched by lineage tag) separately from real changes, and whitespace/comment-only DAX edits as cosmetic.

---

### 5.11 `07_Report_Operations` — report-level structure

**Category:** PBIP.

```
07_Report_Operations { operation: "info",
                       pbip_path: "C:/MyReport.pbip",
                       include_visuals: true, summary_only: true }

07_Report_Operations { operation: "measure_usage",
                       pbip_path: "C:/MyReport.pbip",
                       output_format: "json",
                       export_path: "C:/measure_usage.csv" }

07_Report_Operations { operation: "rename",
                       pbip_path: "C:/MyReport.pbip", new_name: "MyReport_v2" }

07_Report_Operations { operation: "rebind",
                       pbip_path: "C:/MyReport.pbip",
                       model_path: "C:/SharedModel.SemanticModel" }

07_Report_Operations { operation: "backup",
                       pbip_path: "C:/MyReport.pbip",
                       message: "Pre-refactor snapshot" }

07_Report_Operations { operation: "restore",
                       pbip_path: "C:/MyReport.pbip",
                       backup_path: "C:/MyReport.Report.backup_20260930_101500",
                       confirm_destructive: true }

07_Report_Operations { operation: "discover_schema",
                       pbip_path: "C:/MyReport.pbip", visual_type: "columnChart" }

07_Report_Operations { operation: "manage_extension_measures",
                       pbip_path: "C:/MyReport.pbip",
                       sub_operation: "add",
                       measure_name: "_ReportTotal",
                       expression: "SUM(Sales[Amount])",
                       table_ref: "Sales",
                       data_type: "double",
                       format_string: "#,##0" }
```

`backup` copies the `.Report` folder to a sibling `<name>.Report.backup_<timestamp>`; `restore` puts one back and is a **catastrophic** operation. `manage_extension_measures` edits report-level measures (`reportExtensions.json`); its `sub_operation` is `list`, `add`, `update` or `delete` (a delete is refused while a visual still references the measure, unless `force: true`). `audit_interactions` is a read-only slicer audit: for every slicer it lists the visuals its selection can reach through the model, the NoFilter rules that block a visual the slicer depends on (wrong) or sit on a visual it cannot affect (redundant), the missing ones for a `policy` you pass (`isolate_visuals`, `isolate_visual_types`, `isolate`), and a `suite_plan` of what a UI test must and must not see change. It never removes a rule.

**Every write of the report-editing tools (`07_Report_Operations`, `07_Page_Operations`, `07_Visual_Operations`, `07_Bookmark_Operations`, `07_Theme_Operations`, `11_PBIP_Authoring`) works the same way:** `dry_run: true` previews the write (nothing touches disk; `would_change` lists the files); writes to one report are locked so parallel calls cannot lose updates and are confined to the report folder; a failed write is `write_failed`, a partial one still reports `reload_required` and `files_modified`; a folder with several `.Report` folders is `ambiguous_report` (pass the `.pbip` or `.Report`); and if `17_Desktop_Bridge` last saw the report open with unsaved changes, the result carries `desktop_state` and, under `STRICT`, blocks the write unless you confirm.

---

### 5.12 `07_PBIP_Operations` — offline PBIP analysis

**Category:** PBIP. No live connection needed.

```
07_PBIP_Operations { operation: "analyze",
                     pbip_path: "C:/MyReport.pbip",
                     output_format: "html", auto_open: true }

07_PBIP_Operations { operation: "validate_model",
                     pbip_path: "C:/MyReport.pbip" }

07_PBIP_Operations { operation: "compare_models",
                     source_path: "C:/v1.SemanticModel",
                     target_path: "C:/v2.SemanticModel" }

07_PBIP_Operations { operation: "query_dependencies",
                     pbip_path: "C:/MyReport.pbip",
                     object_name: "[Total Sales]", direction: "both" }

07_PBIP_Operations { operation: "query_measures",
                     pbip_path: "C:/MyReport.pbip", pattern: "margin" }

07_PBIP_Operations { operation: "query_relationships",
                     pbip_path: "C:/MyReport.pbip" }

07_PBIP_Operations { operation: "query_unused",
                     pbip_path: "C:/MyReport.pbip" }

07_PBIP_Operations { operation: "scan_broken_refs",
                     pbip_path: "C:/MyReport.pbip" }

07_PBIP_Operations { operation: "git_diff",
                     pbip_path: "C:/MyReport.pbip" }

07_PBIP_Operations { operation: "dependency_html",
                     pbip_path: "C:/MyReport.pbip",
                     auto_open: true, main_item: "[Total Sales]" }

07_PBIP_Operations { operation: "aggregation_analysis",
                     pbip_path: "C:/MyReport.pbip",
                     output_format: "html" }
```

`validate_report` (alias `preflight`) answers "will Desktop open this, and is it safe to commit" without writing anything: `level: "load"` (default) fails only on defects that make Desktop reject the report; `level: "commit"` also checks the git change set. Each finding carries `severity`, `file`, a JSON `pointer`, `load_blocking` and a `repair_hint`.

**HTML model report = `analyze` with `output_format: "html"` + `auto_open: true`.** Don't confuse with `dependency_html` (interactive dependency browser) or `generate_documentation` (JSON only).

`git_diff` returns `details: List[Dict]` — one entry per modified `.tmdl` file with `{file, measures: {added, modified, removed}, columns, relationships, roles}`.

`query_measures` searches measures by name, display folder or DAX text (`pattern`, `display_folder`, `expression_search`); `query_relationships` lists every relationship; `query_unused` finds unused columns and measures across all reports bound to the model (results are cached); `aggregation_analysis` scores data visuals only (slicers, text boxes, images, shapes, buttons and groups are skipped) and `page_filter` (a case-insensitive part of a page name) is applied before scoring.

---

### 5.13 `07_Page_Operations` — pages, filters, interactions

**Category:** PBIP. Disk writes: Desktop may stay open — reload with `17_Desktop_Bridge` afterwards (§7). Pages resolve by folder id, exact display name, or a **unique** prefix (never a substring; an ambiguous name is `ambiguous_page` with the candidates).

```
07_Page_Operations { operation: "list", pbip_path: "C:/MyReport.pbip" }

07_Page_Operations { operation: "create",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Executive Summary",
                     width: 1280, height: 720,
                     insert_after: "Overview" }

07_Page_Operations { operation: "clone",
                     pbip_path: "C:/MyReport.pbip",
                     source_page: "Overview",
                     new_display_name: "Overview - Backup" }

07_Page_Operations { operation: "rename",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Overview",
                     new_display_name: "Executive Overview" }

07_Page_Operations { operation: "set_background",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Executive Summary",
                     color: "#F5F5F5", transparency: 0 }

07_Page_Operations { operation: "set_drillthrough",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Customer Detail",
                     table: "Customer", field: "CustomerKey" }

07_Page_Operations { operation: "set_tooltip",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Tooltip - Sales", enabled: true, width: 320, height: 240 }

07_Page_Operations { operation: "reorder",
                     pbip_path: "C:/MyReport.pbip",
                     page_order: ["Overview", "Executive Summary", "Customer Detail"] }

07_Page_Operations { operation: "resize",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Overview", width: 1920, height: 1080 }

07_Page_Operations { operation: "set_display",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Overview", display_option: "FitToPage" }

07_Page_Operations { operation: "set_wallpaper",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Overview", color: "#EEEEEE" }

07_Page_Operations { operation: "hide",
                     pbip_path: "C:/MyReport.pbip", page_name: "Customer Detail" }

07_Page_Operations { operation: "delete",
                     pbip_path: "C:/MyReport.pbip", page_name: "Old Page",
                     confirm_destructive: true }

07_Page_Operations { operation: "set_interaction",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Overview",
                     source_visual: "Slicer.Region",
                     target_visual: "ColumnChart.Sales",
                     interaction_type: "Highlight" }

07_Page_Operations { operation: "add_filter",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Overview",
                     table: "Sales", field: "Region",
                     filter_type: "Categorical", values: ["EU", "US"] }

07_Page_Operations { operation: "add_filter",
                     pbip_path: "C:/MyReport.pbip",
                     page_name: "Overview",
                     table: "Sales", field: "Total Sales",
                     filter_type: "TopN", top_n: 10, top_direction: "Top" }

07_Page_Operations { operation: "list_filters",
                     pbip_path: "C:/MyReport.pbip", level: "all" }

07_Page_Operations { operation: "set_filter",
                     pbip_path: "C:/MyReport.pbip",
                     filter_name: "Region", level: "page", page_name: "Overview",
                     values: ["EU"] }

07_Page_Operations { operation: "clear_filters",
                     pbip_path: "C:/MyReport.pbip", level: "page", page_name: "Overview" }

07_Page_Operations { operation: "list_interactions",
                     pbip_path: "C:/MyReport.pbip", page_name: "Overview" }

07_Page_Operations { operation: "bulk_set_interactions",
                     pbip_path: "C:/MyReport.pbip", page_name: "Overview",
                     interactions: [ ... ], replace_all: false }
```

Filter types: `Categorical`, `Advanced` and `TopN` (a `RelativeDate` filter is refused — author it in Desktop). `level` is `report`, `page` or `visual`. `lock_filter` / `unlock_filter` and `hide_filter` / `unhide_filter` control user interaction. Interaction types are `NoFilter`, `DataFilter` and `HighlightFilter` (the short forms `None`, `Filter` and `Highlight` are accepted).

**Exclude lists.** A filter can be an *exclude* list (the slicer's "inverted selection" / a `Not In` filter): `list_filters` reports each filter's `polarity` (`include` or `exclude`). Removing a value from an exclude list *widens* the filter, and an empty list is refused (`empty_in_list`), so `set_filter` on an exclude-mode filter needs `polarity` (or `confirm_widen: true`) — read the polarity before you edit a filter. A slicer's own selection is changed with `set_default_selection` (§5.14.12), not `set_filter`.

`hide` / `show` toggle a page's visibility. Deleting a page (`delete`, catastrophic) repoints `pageOrder`, the active and landing page, removes bookmarks that captured it and strips navigator entries; prefer `hide` when in doubt.

---

### 5.14 `07_Visual_Operations` — visual CRUD and formatting

**Category:** PBIP. The richest tool by operation count. Internal dispatch to three sub-handlers (layout, config, creation). Disk writes: Desktop may stay open — reload with `17_Desktop_Bridge` afterwards (§7).

`visual_name` and `visual_id` accept the visual's folder id or its **exact** title, in every operation; `page_name` accepts a page id, exact display name or unique prefix. A write with no page/visual selector is refused (`target_required`) unless `match_all: true`, and a write that matches several visuals is refused (`multiple_matches`, with the list) unless `allow_multiple: true`. Positions in results are page-absolute even inside groups.

| Group | Operations |
|-------|-----------|
| Inspect / layout | `list`, `get_roles_for_type`, `update_position`, `align`, `replace_measure`, `sync_visual`, `sync_column_widths`, `sync_formatting` |
| Create / delete | `create`, `create_group`, `delete`, `hide`, `unhide`, `list_templates`, `get_template` |
| Data and behaviour | `add_field`, `remove_field`, `set_sort`, `set_action`, `inject_code`, `manage_visual_calcs`, `configure_slicer`, `set_default_selection`, `clear_default_selection`, `audit_pane_collisions`, `bulk_rename_pane_names` |
| Formatting | `update_formatting`, `update_visual_config`, `set_raw_property`, `get_raw_property`, `delete_raw_property` |

#### 5.14.1 List and inspect

```
07_Visual_Operations { operation: "list",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview" }

07_Visual_Operations { operation: "list",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_type: "card" }
```

#### 5.14.2 Create a visual

```
07_Visual_Operations { operation: "create",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview",
                       visual_type: "card",
                       title: "Total Sales",
                       position: { x: 16, y: 16, width: 220, height: 100, z: 1000 },
                       measures: [{ table: "Sales", measure: "Total Sales" }] }
```

Field roles default per visual type and are validated; `measures` / `columns` accept `{table, measure|column, bucket?}` objects or `Table.Field` / `'Table'[Field]` strings, and `position` is page-absolute (add `position_frame: "group"` for group-local coordinates with `parent_group`). `get_roles_for_type` returns the valid roles of a visual type.

Common visual types: `card`, `cardVisual`, `columnChart`, `barChart`, `lineChart`, `pieChart`, `donutChart`, `tableEx`, `pivotTable` (matrix), `slicer`, `advancedSlicerVisual`, `kpi`, `lineClusteredColumnComboChart`.

**Tip:** for bulk creation, use `11_PBIP_Authoring:pbir_add_visuals` instead — it's UTF-8 safe and handles container parenting.

#### 5.14.3 Position and align

```
07_Visual_Operations { operation: "update_position",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Sales by Region",
                       position: { x: 240, y: 16, width: 600, height: 380 } }

07_Visual_Operations { operation: "align",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview",
                       visual_names: ["Card1", "Card2", "Card3", "Card4"],
                       alignment: "top" }
```

#### 5.14.4 Bind data fields

```
07_Visual_Operations { operation: "add_field",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Sales by Region",
                       table: "Sales", field: "Region",
                       bucket: "Category", field_type: "Column" }

07_Visual_Operations { operation: "add_field",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Sales by Region",
                       table: "Sales", field: "Total Sales",
                       bucket: "Y", field_type: "Measure" }

07_Visual_Operations { operation: "remove_field",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Sales by Region",
                       bucket: "Y" }
```

**Warning:** bucket names vary by visual type. Always check an existing same-type visual via `list` first — wrong bucket = silently blank visual. Canonical roles per visual type are in `docs/visual-catalog/<type>.md`.

#### 5.14.5 Replace measures

```
07_Visual_Operations { operation: "replace_measure",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Sales by Region",
                       source_entity: "Sales", source_property: "Total Sales",
                       target_entity: "Sales", target_property: "Sales Total",
                       new_display_name: "Sales Total" }
```

#### 5.14.6 Format

```
07_Visual_Operations { operation: "update_formatting",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Sales by Region",
                       formatting_target: "title",
                       formatting_properties: {
                         text: "Sales by Region (€)",
                         fontSize: 16, bold: true } }

07_Visual_Operations { operation: "update_formatting",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Total Sales",
                       formatting_target: "value",
                       formatting_properties: { fontSize: 14 } }
```

**Critical formatting concept:** there are two formatting layers in `visual.json`:

- `visual.visualContainerObjects` (vCO) — the **chrome** (title, background, border, header). NOT visual content.
- `visual.objects` — the **internal rendering** (chart axes, card value font, matrix header colors). This is what makes the visual look good.

Setting vCO `title.fontSize = 28` does **nothing** to a card's huge number — that's `visual.objects.value[0].properties.fontSize`. **Rule:** copy `visual.objects` from a working same-type visual.

#### 5.14.7 cardVisual font compounding

`cardVisual` has TWO font multipliers that compound: `value.fontSize` AND `layout.calloutSize` multiply at render time, so small changes have big effects and two large values overflow the card every time.

Conservative sizing for a 440×130 card (4 cards × 440 px wide):

- `value.fontSize = 14D`, `layout.calloutSize = 8D` → ~16pt render, always fits
- `value.fontSize = 16D`, `layout.calloutSize = 10D` → ~20pt render, comfortable
- `value.fontSize = 18D`, `layout.calloutSize = 14D` → ~26pt render, overflows a 130×440 card
- Anything above → test first, usually overflows

**Always screenshot and verify card sizing before declaring done** (edit → `17_Desktop_Bridge reload_and_screenshot` → verify): this is a repeated failure mode.

#### 5.14.8 Sort and actions

```
07_Visual_Operations { operation: "set_sort",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Sales by Region",
                       sort_field: "Sales.Total Sales",
                       sort_direction: "Descending" }

07_Visual_Operations { operation: "set_action",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "DrillButton",
                       action_type: "PageNavigation",
                       action_target: "Customer Detail" }
```

#### 5.14.9 Inject Deneb / Python / R

```
07_Visual_Operations { operation: "inject_code",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "DenebChart1",
                       code_type: "deneb", provider: "vegaLite",
                       code: "{ \"$schema\": \"...\", \"mark\": \"bar\", ... }" }
```

#### 5.14.10 Visual calculations

```
07_Visual_Operations { operation: "manage_visual_calcs",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Trend",
                       sub_operation: "add",
                       calc_name: "Running Total",
                       expression: "RUNNINGSUM([Total Sales])" }
```

#### 5.14.11 Sync visuals

```
07_Visual_Operations { operation: "sync_visual",
                       pbip_path: "C:/MyReport.pbip",
                       source_page: "Overview",
                       source_visual_name: "KPI Card",
                       target_pages: ["Sales Detail", "Customer Detail"],
                       sync_position: true, sync_children: true }

07_Visual_Operations { operation: "sync_column_widths",
                       pbip_path: "C:/MyReport.pbip",
                       source_page: "Overview",
                       source_visual_name: "Master Table",
                       target_pages: ["Detail Page 1", "Detail Page 2"],
                       target_visual_type: "tableEx" }
```

#### 5.14.12 Slicer pane management

```
07_Visual_Operations { operation: "audit_pane_collisions",
                       pbip_path: "C:/MyReport.pbip" }

07_Visual_Operations { operation: "bulk_rename_pane_names",
                       pbip_path: "C:/MyReport.pbip",
                       strategy: "auto" }

07_Visual_Operations { operation: "set_default_selection",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview",
                       display_name: "Region Slicer",
                       entity: "Sales", property: "Region",
                       values: ["EU"], selection_mode: "single" }

07_Visual_Operations { operation: "clear_default_selection",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", display_name: "Region Slicer",
                       entity: "Sales", property: "Region" }

07_Visual_Operations { operation: "configure_slicer",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", display_name: "Region Slicer",
                       dry_run: true }
```

`configure_slicer` turns a slicer into a single-select "All" (an inverted selection with nothing excluded); several matches only preview unless `allow_multiple: true`, and every slicer's prior selection and `filterConfig` come back as `prior_state`. On an **inverted** slicer `set_default_selection` requires `polarity`: `exclude` means the listed values are *excluded*, `include` writes a positive list. Empty `values` are refused. Read the slicer first (`get_raw_property` on `filterConfig`) before you change one.

#### 5.14.13 Raw JSON property access (escape hatch)

```
07_Visual_Operations { operation: "set_raw_property",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "MyVisual",
                       json_path: "visual.objects.title.properties.fontSize",
                       value: "16D", wrap_literal: false }

07_Visual_Operations { operation: "get_raw_property",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "MyVisual",
                       json_path: "visual.objects.title" }
```

`wrap_literal` controls whether scalar values get wrapped in PBIR's `{ "expr": { "Literal": { "Value": "..." } } }` shape. `delete_raw_property` removes a JSON node and is **catastrophic**. A type change needs `force_type: true`.

#### 5.14.14 Hide, delete, group, config and formatting sync

```
07_Visual_Operations { operation: "hide",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Region Slicer" }

07_Visual_Operations { operation: "unhide",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Region Slicer" }

07_Visual_Operations { operation: "delete",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_id: "a1b2c3d4e5f6a7b8c9d0" }

07_Visual_Operations { operation: "create_group",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", group_name: "KPI Row",
                       position: { x: 16, y: 16, width: 1248, height: 130 } }

07_Visual_Operations { operation: "update_visual_config",
                       pbip_path: "C:/MyReport.pbip",
                       page_name: "Overview", visual_name: "Sales by Region",
                       config_type: "legend", property_name: "show", property_value: "false",
                       value_type: "boolean" }

07_Visual_Operations { operation: "sync_formatting",
                       pbip_path: "C:/MyReport.pbip",
                       source_page: "Overview", source_visual_name: "Master Table",
                       target_page: "Detail", target_visual_name: "Detail Table" }

07_Visual_Operations { operation: "list_templates", pbip_path: "C:/MyReport.pbip" }
```

`delete` takes exact visual ids. Deleting a slicer, or any visual that carries filters, returns a dump of its filter layer and needs `confirm_delete_filter_layer: true`: a slicer's `filterConfig` is the page's filter layer, and removing it can blank other visuals. **Prefer `hide`** — it sets `isHidden` at the top level and keeps the filter layer intact; a deleted group's children keep their page position unless `delete_children` says otherwise. Bookmark targets and interactions are scrubbed on delete. `sync_formatting` copies `visual.objects` and the chrome (`visualContainerObjects`) to the target; without `target_visual_name` it needs `confirm_bulk_sync: true`.

---

### 5.15 `07_Bookmark_Operations` — bookmark CRUD

**Category:** PBIP.

```
07_Bookmark_Operations { operation: "list",
                         pbip_path: "C:/MyReport.pbip" }

07_Bookmark_Operations { operation: "create",
                         pbip_path: "C:/MyReport.pbip",
                         display_name: "Q4 View",
                         page_name: "Overview",
                         capture_data: true, capture_display: true,
                         all_visuals: true }

07_Bookmark_Operations { operation: "set_affected_visuals",
                         pbip_path: "C:/MyReport.pbip",
                         bookmark_id: "abc-123",
                         visual_ids: ["v1", "v2", "v3"] }

07_Bookmark_Operations { operation: "get",
                         pbip_path: "C:/MyReport.pbip", bookmark_id: "Q4 View" }

07_Bookmark_Operations { operation: "rename",
                         pbip_path: "C:/MyReport.pbip",
                         bookmark_id: "Q4 View", new_name: "Q4 2026" }

07_Bookmark_Operations { operation: "update",
                         pbip_path: "C:/MyReport.pbip",
                         bookmark_id: "Q4 2026", capture_data: false }

07_Bookmark_Operations { operation: "set_capture",
                         pbip_path: "C:/MyReport.pbip",
                         bookmark_id: "Q4 2026",
                         capture_data: true, capture_display: true, capture_current_page: false }

07_Bookmark_Operations { operation: "create_group",
                         pbip_path: "C:/MyReport.pbip",
                         display_name: "Quarterly views",
                         bookmark_ids: ["abc-123", "def-456"] }

07_Bookmark_Operations { operation: "update_group",
                         pbip_path: "C:/MyReport.pbip",
                         group_id: "grp-1", bookmark_ids: ["abc-123"] }

07_Bookmark_Operations { operation: "delete_group",
                         pbip_path: "C:/MyReport.pbip",
                         group_id: "grp-1", delete_children: false }

07_Bookmark_Operations { operation: "delete",
                         pbip_path: "C:/MyReport.pbip", bookmark_id: "Q4 2026" }

07_Bookmark_Operations { operation: "analyze",
                         pbip_path: "C:/MyReport.pbip",
                         auto_open: true }
```

A bookmark is addressed by its id or its display name. `set_capture` and `update` change what a bookmark captures (data state, display state, current page); `set_affected_visuals` limits which visuals it applies to. A bookmark belongs to at most one group; `delete_group` keeps the members as top-level bookmarks unless `delete_children: true`. `analyze` produces an interactive HTML report showing every bookmark, what it captures, and what it affects.

---

### 5.16 `07_Theme_Operations` — themes and conditional formatting

**Category:** PBIP.

```
07_Theme_Operations { operation: "analyze_compliance",
                      pbip_path: "C:/MyReport.pbip", auto_open: true }

07_Theme_Operations { operation: "get_theme",
                      pbip_path: "C:/MyReport.pbip" }

07_Theme_Operations { operation: "set_colors",
                      pbip_path: "C:/MyReport.pbip",
                      colors: {
                        dataColors: ["#0077C8", "#FF6F00", "#5C3D9E"],
                        background: "#FFFFFF",
                        foreground: "#1A1A1A" } }

07_Theme_Operations { operation: "set_font",
                      pbip_path: "C:/MyReport.pbip",
                      text_class: "title",
                      font_family: "Segoe UI", font_size: 14, bold: true }

07_Theme_Operations { operation: "add_cf",
                      pbip_path: "C:/MyReport.pbip",
                      page_name: "Overview", visual_name: "Sales Matrix",
                      container: "dataPoint", property_name: "fillColor",
                      rule_type: "color_scale",
                      cf_config: {
                        table: "Sales", field: "Total Sales",
                        min_color: "#FFFFFF", max_color: "#0077C8" } }

07_Theme_Operations { operation: "copy_cf",
                      pbip_path: "C:/MyReport.pbip",
                      source_page: "Overview", source_visual: "Sales Matrix",
                      target_page: "Detail", target_visual: "Detail Matrix" }

07_Theme_Operations { operation: "list_cf",
                      pbip_path: "C:/MyReport.pbip",
                      page_name: "Overview", visual_name: "Sales Matrix" }

07_Theme_Operations { operation: "remove_cf",
                      pbip_path: "C:/MyReport.pbip",
                      page_name: "Overview", visual_name: "Sales Matrix",
                      container: "dataPoint", property_name: "fillColor" }

07_Theme_Operations { operation: "list_text_classes", pbip_path: "C:/MyReport.pbip" }

07_Theme_Operations { operation: "set_formatting",
                      pbip_path: "C:/MyReport.pbip",
                      visual_type_target: "card",
                      formatting: { value: { fontSize: 14 } } }

07_Theme_Operations { operation: "push_visual",
                      pbip_path: "C:/MyReport.pbip",
                      page_name: "Overview", visual_name: "Total Sales" }

07_Theme_Operations { operation: "export_theme", pbip_path: "C:/MyReport.pbip",
                      output_path: "C:/themes/current.json" }

07_Theme_Operations { operation: "import_theme", pbip_path: "C:/MyReport.pbip",
                      source: "C:/themes/brand.json", theme_name: "Brand" }

07_Theme_Operations { operation: "apply_theme", pbip_path: "C:/MyReport.pbip",
                      theme: { name: "Brand", dataColors: ["#0077C8", "#FF6F00"] } }

07_Theme_Operations { operation: "clear_visual_overrides", pbip_path: "C:/MyReport.pbip",
                      scope: "page:Overview", keep_cf: true }
```

CF rule types that can be *written*: `color_scale` and `rules` (categorical cases). `data_bars` and `icons` are refused because no verified file shape exists — author one in Desktop, then `copy_cf` it. `set_formatting` sets visual-type defaults in the theme and `push_visual` promotes one visual's literal formatting to its type's default. `set_colors`, `apply_theme`, `import_theme` and `clear_visual_overrides` are **catastrophic** (they touch the whole report). `clear_visual_overrides` removes plain formatting overrides so the theme applies; it never touches content (slicer selections, text-box paragraphs, Deneb specs, images, button text), selector-scoped entries, or — with `keep_cf: true` — conditional formatting, and it returns counts of what it kept.

---

### 5.17 `08_Documentation_Word` — Word doc generation

**Category:** DOCS.

```
08_Documentation_Word { mode: "generate", output_path: "C:/docs/Model.docx" }

08_Documentation_Word { mode: "update",
                        input_path: "C:/docs/Model.docx",
                        output_path: "C:/docs/Model_v2.docx" }
```

`output_path` may be a `.docx` file or a folder (a timestamped name is chosen). `generate` writes a `<name>.snapshot.json` next to the document; `update` finds that snapshot from `input_path`, diffs the live model against it, adds a change log and rewrites both files. The document covers tables, columns, measures, relationships, calculation groups, RLS roles and filters, Power Query sources, UDFs, field parameters and the best-practice findings of `06_Analysis_Operations` `full`. A document that Word has open returns a structured error instead of a partial write.

---

### 5.18 `09_Debug_Operations` — the visual debugger

**Category:** DEBUG. **The single most important debugging tool.** Combines PBIP layout with live model data — runs the exact DAX a visual would generate, with full slicer/filter context.

**Always use `09_Debug_Operations` (operation `visual` or `run_dax`) for visual debugging — never `07_Report_Operations`.**

PBIP path persists across calls (set once via `set_path`):

```
09_Debug_Operations { operation: "set_path", pbip_path: "C:/MyReport.pbip" }
09_Debug_Operations { operation: "status" }
```

#### 5.18.1 Debug a visual

```
09_Debug_Operations { operation: "visual",
                      page_name: "Overview",
                      visual_name: "Sales by Region",
                      execute_query: true, trace: true,
                      include_slicers: true, clear_cache: true }
```

Returns: filter context, generated DAX, rows, SE/FE timings, any errors.

The visual's query is rebuilt the way Power BI emits it: include lists become `TREATAS`, while exclude lists (inverted selection), comparisons, between, blank, TopN, relative-date and And/Or filters become `FILTER(KEEPFILTERS(...))`; visual-, page- and report-level filters on the same column are *intersected*; measure (value) filters, table grand totals and matrix subtotals are reproduced. Anything that could not be translated is listed in `unsupported_filters` and the result says `fidelity: "partial"` — nothing is dropped silently. For ground truth, paste Desktop's own query (Performance Analyzer → "Copy query") as `reference_query`, or point `pa_export_path` at a Performance Analyzer export: the result then carries `reconstruction_check` (what differs) and the Desktop query is what executes. Manual `filters` are applied verbatim as `KEEPFILTERS(<dax>)`.

Slicer state is read from the PBIP on disk, so an unsaved selection in Desktop is invisible to the debugger — check `hasUnsavedChanges` with `17_Desktop_Bridge status`, or pass the current values via `filters`. Every query in the `09_*` tools bypasses the result cache.

#### 5.18.2 Compare original vs optimized measure

```
09_Debug_Operations { operation: "compare",
                      page_name: "Overview", visual_id: "v1",
                      original_measure: "[Total Sales]",
                      optimized_expression: "SUMX(Sales, Sales[Amount])",
                      trace: true }
```

Side-by-side timings + result equality. `compare` warms up, then alternates the two versions (A B, B A, …) for `runs` rounds (default 3) and keeps the fastest run per side, so background noise does not decide the winner.

That standalone comparison evaluates the candidate outside the model, so dependent measures and the visual's other measures never run. For the real measure chain use `mode: "live_swap"`:

```
09_Debug_Operations { operation: "compare", mode: "live_swap",
                      page_name: "Overview", visual_id: "v1",
                      original_measure: "Total Sales",
                      optimized_expression: "SUMX(Sales, Sales[Amount])",
                      runs: 3, dry_run: true }
```

It checkpoints the live model, writes the candidate body (or a `patch` list of `{ table_name?, measure_name, expression }`) into it, times the visual's own query cold, interleaved with the baseline, compares the rows at the visual's grain, then always restores the model and proves it (`restore_verified`; a failed proof is the `restore_not_verified` error and names the checkpoint to restore by hand). The verdict is `faster`, `slower`, `noise`, `values_differ` or `inconclusive`, with `delta_pct` and the `min_detectable_effect_pct` it had to beat. `dry_run: true` plans without writing anything. It is a destructive-class call (`confirm_destructive` under STRICT), needs a live Desktop model, and does not refuse because the model has unsaved changes: the checkpoint contains them and the restore is verified against it.

#### 5.18.3 Drill to detail

```
09_Debug_Operations { operation: "drill",
                      page_name: "Overview", visual_id: "v1",
                      fact_table: "Sales",
                      filters: [
                        { table: "Sales", field: "Region", values: ["EU"] }
                      ],
                      limit: 100 }
```

#### 5.18.4 Step through variables

```
09_Debug_Operations { operation: "step_variables",
                      measure_name: "Total Sales",
                      max_rows: 100 }

09_Debug_Operations { operation: "debug_variable",
                      variable_name: "_filtered",
                      max_rows: 100 }
```

#### 5.18.5 Optimize from timings

```
09_Debug_Operations { operation: "optimize",
                      total_ms: 4500, fe_ms: 800, se_ms: 3700,
                      fe_pct: 18, se_pct: 82,
                      se_queries: 12 }
```

Returns concrete recommendations based on the timing profile. Instead of typing timings, pass the `trace_id` that a traced `visual` or `run_dax` call returned (`09_Debug_Operations { operation: "optimize", measure_name: "Total Sales", trace_id: "<id>" }`); without either, the tool traces the measure itself.

#### 5.18.5b Analyze a measure and run raw DAX

```
09_Debug_Operations { operation: "analyze",
                      measure_name: "Total Sales", execute_measure: true }

09_Debug_Operations { operation: "run_dax",
                      query: "EVALUATE ROW(\"v\", [Total Sales])", trace: true }
```

`analyze` inspects a measure's DAX (optionally in a visual's filter context). `run_dax` is a thin alias of `04_Run_DAX` — same executor, same options — with the traced rows under `result` and the timings under `se_fe_trace`.

#### 5.18.6 Audit a whole page

```
09_Debug_Operations { operation: "audit",
                      page_name: "Overview",
                      include_data: false, compact: true }

09_Debug_Operations { operation: "audit",
                      page_name: ["Overview", "Detail", "Trends"],
                      compact: true }
```

#### 5.18.7 Closed-loop debug

```
09_Debug_Operations { operation: "debug_loop",
                      page_name: "Overview",
                      visual_id: "v1",
                      measure_name: "Total Sales",
                      expected: 10000000,
                      max_iterations: 5,
                      fix_strategy: "ask_user",
                      take_screenshot: true,
                      screenshot_dir: "C:/debug/screenshots" }
```

Iterates until the measure equals expected (within tolerance) or max iterations hit. `fix_strategy` is `ask_user`, `live_tom` (edits the live model; needs your confirmation under the `WARN` / `STRICT` gate), `tmdl_edit` (edits files, then stops with `final_status: awaiting_reopen` — the TMDL rules of §7.4 apply) or `dry_run`. Its reloads use the same refusing bridge reload as §5.27.

---

### 5.19 `09_Validate` — validation suite

**Category:** DEBUG.

```
09_Validate { operation: "cross_visual",
              measure_name: "Total Sales",
              pages: ["Overview", "Detail", "Summary"],
              tolerance: 0.01 }

09_Validate { operation: "expected_value",
              measure_name: "Total Sales",
              expected_value: 10000000,
              page_name: "Overview", visual_name: "TotalCard",
              tolerance: 1.0 }

09_Validate { operation: "filter_permutation",
              measure_name: "Total Sales",
              filters: [
                { table: "Sales", field: "Region", values: ["EU", "US", "APAC"] },
                { table: "Date", field: "Year", values: [2024, 2025] }
              ],
              max_permutations: 50 }

09_Validate { operation: "capture_baseline",
              measures: ["Total Sales", "Total Profit"],
              baseline_path: "C:/baselines/before_refactor.json" }

09_Validate { operation: "compare_baseline",
              baseline_path: "C:/baselines/before_refactor.json", tolerance: 0.001 }
```

`cross_visual` checks that a measure renders identically across multiple visuals (catches subtle filter-context bugs). What is compared is the *measure* evaluated over what each visual shows — not the first column of the visual query.

`capture_baseline` / `compare_baseline` are a **regression guard around model edits**: snapshot the scalar value of a list of measures before a refactor (`15_DAX_Refactor`, a rename, a batch edit), then re-run after and see which changed. Both always bypass the result cache, so a compare inside the cache lifetime cannot replay the baseline; a measure that errored at capture time is reported, and NaN/BLANK versus a number counts as a change.

`tieout` answers "the subtotal is off but the rows are fine": for a matrix or table visual it runs the visual's real query with its subtotal rows and checks that every parent equals the sum of its children, naming the first level and measure that breaks. `page_vs_page` compares the same measure on two pages, `A_vs_B` two measures that must agree (for example cash-flow EBITDA against P&L EBITDA). Ratio and semi-additive measures break additivity legitimately; list them in `non_additive`.

```
09_Validate { operation: "tieout", page_name: "P&L", visual_id: "a1b2c3", non_additive: ["Margin %"] }
09_Validate { operation: "tieout", mode: "A_vs_B", measure_a: "CF EBITDA", measure_b: "PL EBITDA",
              by: ["'Date'[Year]"] }
09_Validate { operation: "equivalence", measure_name: "Revenue",
              candidate_expression: "SUMX('Sales', 'Sales'[Qty] * 'Sales'[Price])",
              grid_columns: ["'Region'[Name]"], date_column: "'Date'[Date]", grains: ["year"] }
```

`equivalence` evaluates two DAX expressions side by side over a grid of contexts (total, each grid column, date grains, explicit `filters`), without writing the model, and lists the cells that differ. Equal results on the sampled cells are not a formal proof.

#### 5.19.1 Persisted test suites

A test suite is one JSON file you keep next to the model (for example `tests/model.suite.json`, committed with it) and re-run after every change, by hand or in CI. The runner only reads the model.

```
09_Validate { operation: "test_put", suite_path: "C:/repo/tests/model.suite.json",
              test: { id: "rev-2025", type: "measure_assertion", measure: "[Revenue]",
                      filters: ["'Date'[Year] = 2025"], expected: 1234567.89, tolerance: 0.5 } }
09_Validate { operation: "test_list", suite_path: "C:/repo/tests/model.suite.json" }
09_Validate { operation: "test_remove", suite_path: "C:/repo/tests/model.suite.json", test_id: "rev-2025" }
09_Validate { operation: "test_run", suite_path: "C:/repo/tests/model.suite.json", tags: ["smoke"] }
09_Validate { operation: "test_export", suite_path: "C:/repo/tests/model.suite.json",
              output_path: "C:/repo/tests/results.xml", output_format: "junit" }
```

Test types:

| `type` | Passes when |
|---|---|
| `measure_assertion` | `measure` (with optional DAX `filters`) equals `expected` within `tolerance`; `expected: null` asserts BLANK |
| `dax_assertion` | the `query` result meets `expect`: `scalar`, `row_count`, `min_rows`, `max_rows`, `columns`, `rows` (order-insensitive unless `ordered: true`) or `error` (the query must fail with that text) |
| `rls_validation` | as `dax_assertion`, but run under `role` (and optionally `effective_user_name`) |
| `regression_snapshot` | a `capture_baseline` file (`baseline_path`) still matches |
| `performance_budget` | the median of `runs` cache-bypassed runs is at most `max_ms` (wall clock: a regression budget, not an SE/FE measurement) |
| `metadata_compliance` | `rule` holds: `no_measure_errors`, `measures_exist` (`measures`), `measure_name_regex` (`pattern`) or `descriptions_required` (`scope`: measures or tables) |
| `rls_audit` | the `04_Query_Operations rls_audit` engine finds nothing at or above `fail_on` (default `critical`, i.e. fail-open) for `roles` (default all) with the known UPN `as_user`; `allow` lists finding codes to waive |
| `tieout` | a `tieout` (`mode`: visual with `page_name` + `visual_id`, `A_vs_B` with `measure_a` + `measure_b`, or `page_vs_page`) ties; an inconclusive run fails |

`test_put` adds a test, or replaces the one with the same `id` (`replace: false` refuses instead), and creates the file when it is missing. `test_run` runs the suite (`ids` or `tags` pick a subset; `skip: true` on a test skips it) and returns per-test results with the reason for every failure; `record: false` leaves the suite's last-run record untouched. `test_export` runs it and writes the result as JUnit XML (`output_format: "junit"`, which CI systems read) or JSON.

`test_generate` writes the suite for you instead of by hand: `source: "page"` (`page_name`), `"report"`, `"roles"` or `"measures"` (`measures`). For a page it creates a snapshot `dax_assertion` per data visual (with the reconstruction `fidelity`), a `tieout` per matrix or table, a check that each slicer's saved selection still exists, a grand-total `measure_assertion` per card measure and, with `include_perf`, a `performance_budget` per heavy visual; roles get an `rls_audit`. It previews by default and shows a diff against the suite; `write: true` with `suite_path` saves it. Tests are keyed by stable ids, so running it again updates them; a changed expectation, or a hand-written test with the same id, is held back until you pass `accept_changes: true`. Generated snapshot tests assert today's numbers (bugs included), which is why they carry `provenance: "snapshot"`.

---

### 5.20 `09_Profile` — performance profiling

**Category:** DEBUG.

```
09_Profile { operation: "page",
             page_name: "Overview",
             iterations: 3,
             max_combinations: 15 }

09_Profile { operation: "filter_matrix",
             page_name: "Overview", visual_id: "v1",
             iterations: 5,
             filter_columns: ["Region", "Year"] }

09_Profile { operation: "decompose",
             visual_name: "Total Sales", dimension: "Region", top_n: 10 }

09_Profile { operation: "contribution",
             visual_name: "Total Sales", dimension: "Product Category" }

09_Profile { operation: "trend",
             page_name: "Overview", date_column: "Date.Date",
             granularity: "month" }

09_Profile { operation: "root_cause",
             page_name: "Overview", visual_id: "v1",
             baseline_filters: [{ table: "Date", field: "Year", values: [2024] }],
             comparison_filters: [{ table: "Date", field: "Year", values: [2025] }] }

09_Profile { operation: "export",
             page_name: "Overview", output_format: "markdown" }

09_Profile { operation: "page",
             page_name: "Overview", trace: true, iterations: 3 }

09_Profile { operation: "perf_analyzer_import",
             file_path: "C:/exports/PowerBIPerformanceData.json", trace: false }
```

**Performance Analyzer parity.** `page` with `trace: true` runs every visual query of the page in ONE trace session and returns a table shaped like Desktop's Performance Analyzer pane (`visual`, `type`, `dax_ms`, `other_wait_est_ms`, `total_est_ms`, `se_pct`, `callbacks`, `peak_mem_kb`) plus a page estimate. A warm-up run is discarded, `iterations` measured runs follow and the fastest is kept. `other_wait_est_ms` is an **estimate** from a queue model (`simulate_concurrency`, default 6) — Power BI publishes no concurrency limit. For the **measured** "Other" time, export the pane from Desktop and read it with `perf_analyzer_import`. The batch stops at the tool's time budget (`runtime` reports what did not run) and never keeps running in the background after a timeout.

---

**Perf history and `regress`.** `page` with `record: true` appends the run to the project's perf history (`output/perf/<project>.jsonl`, with `label` and `project`); `regress` compares a recorded `candidate` run with a `baseline` (a label, a git sha prefix or a date) and refuses to call a difference a regression while it is inside the noise of the measurements (`inconclusive`). Profile with `iterations` of 6 or more before comparing.

### 5.21 `09_Document` — narrative documentation

**Category:** DEBUG.

```
09_Document { operation: "page", page_name: "Overview",
              lightweight: true, narrate: false }

09_Document { operation: "report", lightweight: true }

09_Document { operation: "measure_lineage", measure_name: "Total Sales" }

09_Document { operation: "filter_lineage", page_name: "Overview" }
```

`filter_lineage` shows which filters affect which visuals on the page.

`narrate: true` uses MCP sampling to generate prose (requires the agent's LLM).

---

### 5.22 `10_Show_User_Guide` — show the embedded user guide

**Category:** CORE. Returns text only (no connection needed). The tool inventory is generated from the live registry, so it always matches the build you are running.

```
10_Show_User_Guide { topic: "index" }
10_Show_User_Guide { tool: "02_Model_Operations" }
10_Show_User_Guide { tool: "07_Visual_Operations", operation: "add_field" }
10_Show_User_Guide { topic: "workflows" }
```

| Call | Returns |
|------|---------|
| no arguments / `topic: "index"` | Compact index: quick start, one line per tool (title + operations), the common workflows |
| `tool: "<name>"` | That tool's **full** argument list (including the rare-operation arguments the tool list omits), its long usage notes, and its section of [Part III](#part-iii) |
| `tool: "<name>", operation: "<op>"` | Only the arguments that concern that operation; an unknown op is `invalid_operation` with the valid values |
| `topic: "all"` | The complete guide (every tool's parameters; about 70K characters) |
| `topic: "workflows"` / `topic: "tips"` | Just that section |

---

### 5.23 `11_PBIP_Authoring` — page/visual authoring

**Category:** PBIP. Page and visual creation, cloning, deletion and bulk direct-write visual creation (no external process). Disk writes: Desktop may stay open — reload with `17_Desktop_Bridge` afterwards (§7).

**Operations:** `create_page`, `clone_page`, `clone_visual`, `clone_report`, `delete_page`, `create_visual`, `create_visual_group`, `delete_visual`, `pbir_add_visuals`, `pbir_resolve_container`, `list_templates`, `get_template`. `create_visual` and `delete_visual` are the same implementation as `07_Visual_Operations` `create` / `delete` (same severity, same filter-layer guard). `clone_visual` copies one visual or group, on the same page or onto another (`target_page`), with new ids, the group and coordinate handling (`group_mode`, `parent_group`) and its filters; pass `inherit_interactions: true` or the clone follows none of the source's interaction rules (a cloned slicer would filter everything).

```
11_PBIP_Authoring { operation: "create_page",
                    pbip_path: "C:/MyReport.pbip",
                    page_name: "Sales Detail",
                    width: 1280, height: 720 }

11_PBIP_Authoring { operation: "clone_page",
                    pbip_path: "C:/MyReport.pbip",
                    source_page: "Overview",
                    new_display_name: "Overview - Backup" }

11_PBIP_Authoring { operation: "delete_page",
                    pbip_path: "C:/MyReport.pbip",
                    page_name: "Old Page" }

11_PBIP_Authoring { operation: "create_visual_group",
                    pbip_path: "C:/MyReport.pbip",
                    page_name: "Sales Detail",
                    group_name: "KPI Row",
                    position: { x: 16, y: 16, width: 1248, height: 130 } }

11_PBIP_Authoring { operation: "pbir_add_visuals",
                    pbip_path: "C:/MyReport.pbip",
                    page_name: "Sales Detail",
                    container_display_name: "KPI Row",
                    visuals: [
                      { visual_type: "card", x: 16, y: 16,
                        width: 220, height: 100,
                        title: "Total Sales",
                        fields: { Data: "Sales[Total Sales]" } },
                      { visual_type: "card", x: 240, y: 16,
                        width: 220, height: 100,
                        title: "Total Profit",
                        fields: { Data: "Sales[Total Profit]" } }
                    ] }

11_PBIP_Authoring { operation: "pbir_resolve_container",
                    pbip_path: "C:/MyReport.pbip",
                    page_name: "Sales Detail",
                    container_display_name: "KPI Row" }
```

**Why prefer `pbir_add_visuals` over `07_Visual_Operations:create`:**
1. **UTF-8 safe** — measure names containing `Δ`, `∆`, `€` survive Windows encoding (uses `json.dumps(ensure_ascii=True)`). Raw shell pbir mangles non-ASCII.
2. **Container parenting** — pass `container_display_name` and the visuals get the right `parentGroupName` plus group-local coordinate translation.
3. **Role pre-flight** — validates field bindings before issuing pbir commands.
4. **Bulk** — one round-trip for many visuals.

---

### 5.24 `13_Query_Plan` — DAX query plans

**Category:** DEBUG. Capture, parse, analyze, compare DAX query plans (logical, physical, xmSQL) and benchmark queries. Plans are real: the AMO trace runs in-process, so no external build step is needed (`runner_lane` in the result says `in_process` or the fallback `exe`, which returns no plans or execution metrics).

```
13_Query_Plan { operation: "capture",
                query: "EVALUATE SUMMARIZECOLUMNS('Date'[Year], \"Sales\", [Total Sales])",
                include_xmsql: true, clear_cache: true }
```

`capture` returns a **summary**, never the plan text (a real plan reaches 10,000+ rows): a headline (total / FE / SE time, SE CPU, parallelism, SE queries and cache hits, DirectQuery time, result rows), engine `execution_metrics`, every result set, the plan shape, an operator census, the top `#Records` rows and heuristic findings. The full plans are written to `exports/perf/<capture_id>/` as `ServerTimings.json`, `QueryPlan.json` and a `capture.daxx` you can open in DAX Studio. Every capture id is unique per run, so a before/after pair of the same query never overwrites the first. Then:

```
13_Query_Plan { operation: "plan_rows", capture_id: "<id>", plan: "physical",
                offset: 0, limit: 100 }

13_Query_Plan { operation: "parse", capture_id: "<id>" }

13_Query_Plan { operation: "analyze", capture_id: "<id>", include_xmsql: true }

13_Query_Plan { operation: "compare",
                capture_id_a: "<before>", capture_id_b: "<after>" }

13_Query_Plan { operation: "export_daxx", capture_id: "<id>" }

13_Query_Plan { operation: "list_rules" }

13_Query_Plan { operation: "evaluate_and_log",
                query: "DEFINE VAR _x = EVALUATEANDLOG(SUM(Sales[Amount]), \"sum\") EVALUATE { _x }" }

13_Query_Plan { operation: "benchmark",
                query: "EVALUATE …",
                runs: 5, cold_cache: true, warmup: 1 }
```

`plan_rows` pages the captured plan rows (`physical` or `logical`); `export_daxx` rewrites the DAX Studio export; `compare` gives the timing delta **plus a structural plan diff** (row counts, operator histogram, operators added or removed — what the rewrite actually stopped doing). The verdict is `ok`, `warn` or `hot`.

#### 5.24.1 Live trace while you use the report

`capture` traces one query you write. To see what the **report** sends — the queries of every visual while you click slicers, change pages or drill — keep a trace running:

```
13_Query_Plan { operation: "trace_start", max_duration_s: 300, label: "slicer test" }
13_Query_Plan { operation: "trace_fetch", since: 0, slowest_first: true }
13_Query_Plan { operation: "trace_stop", include_text: true }
```

Start the trace, use the report in Desktop, then fetch. `trace_fetch` returns the queries captured so far, one entry per query with its duration, SE / FE split, SE query count and cache hits; `since` takes the `next_since` of the previous fetch, so each fetch only returns the queries that are new. A fetch takes about two seconds, because the engine hands trace events over in batches and the server waits for the latest one. `include_text: true` adds the DAX text (cut to 4,000 characters). Queries the server itself sent are left out. `trace_stop` ends the trace and returns the final list. A trace stops by itself after `max_duration_s` (default 300, at most 3600) or `max_events` (default 200,000), so a forgotten trace does not keep costing Desktop; one live trace runs at a time.

`benchmark` runs the query several times and reports the **fastest valid run as `baseline_ms`** (the mean is dragged by background noise), the `spread_pct`, and a warning when the spread exceeds 20% — the runs disagree and the baseline is not yet trustworthy. `cold_cache: true` really clears the engine cache and `cold_cache_honoured` tells you if it worked. Two richer forms: passing `cold_runs` / `warm_runs` gives per-run server timings, and passing `compare_dax_query` runs an interleaved A/B where every run is cold and traced.

---

### 5.25 `15_DAX_Refactor` — DAX refactoring

**Category:** MODEL. Preview and apply bulk DAX refactor transforms on the live model, with a staleness hash, a write-ahead undo journal and rollback.

**Operations:** `list_transforms`, `preview`, `apply`, `rollback`, `list_journals`.

```
15_DAX_Refactor { operation: "list_transforms" }

15_DAX_Refactor { operation: "preview",
                  transform: "inline_measure",
                  args: { measure_ref: "Sales[Helper]" } }
```

`preview` is a dry run: it returns `{ plan_hash, ... }` with the full before/after and a unified diff per edit, plus `rejected_edits` (rewrites that failed the structural check and will never be written) and warnings. Then:

```
15_DAX_Refactor { operation: "apply",
                  transform: "inline_measure",
                  plan_hash: "<hash from the preview>",
                  args: { measure_ref: "Sales[Helper]", delete_after: false },
                  pbip_path: "C:/MyReport.pbip" }

15_DAX_Refactor { operation: "list_journals", limit: 10 }

15_DAX_Refactor { operation: "rollback", journal_path: "<path from list_journals>" }
```

`plan_hash` covers the transform, its arguments, the connected database and the model state: apply with different arguments, or on a model that changed since the preview, is rejected (`plan_stale`) — call `preview` again. The whole plan is committed in ONE save; if a rewritten measure no longer compiles, or (with `verify`, on by default for `inline_measure` / `extract_measure` / `remove_unused_variables`) a measure's grand-total value changes, the apply is rolled back automatically. Every apply first writes a journal; `rollback` restores the pre-apply state from it (it refuses a journal from another database or one whose targets changed since — `force: true` overrides).

Available transforms (arguments go in `args`; every transform also accepts `measures` and `tables` scope filters):
- `inline_measure` (`measure_ref`, `delete_after`) — replace references to a measure with its body. `delete_after` defaults to `false`; the measure is deleted only when nothing still references it, in the model or the report
- `extract_measure` (`expression`, `new_name`, `target_table`) — lift a sub-expression into a new measure
- `replace_pattern` (`pattern`, `replacement`) — regex find/replace across measure DAX (the result must still parse)
- `swap_table_reference` (`from_table`, `to_table`, `column_map`) — point measures at a different table
- `use_divide` — rewrite a safe `a / b` as `DIVIDE(a, b)` (a semantic change: errors and infinity become BLANK)
- `remove_unused_variables` — drop `VAR`s that are never used
- `move_measure_batch` (`moves`) — **preview only**: `apply` refuses with `transform_not_persistable`; move measures with `02_Model_Operations` `measure`/`move` or `03_Batch_Operations` `measures`/`move`

`apply` is gated as **catastrophic** when `inline_measure` has `delete_after: true`. Findings from `05_DAX_Intelligence` `lint_model` name the transform and arguments that fix them.

---

### 5.26 `16_Dependency_Graph` — structured dependency queries

**Category:** ANALYSIS. Build a graph of model + report dependencies, then query it.

```
16_Dependency_Graph { operation: "build",
                      scope: "both",
                      pbip_path: "C:/MyReport.pbip",
                      cache: true }
```

Returns `{ graph_id, summary }`. Then:

```
16_Dependency_Graph { operation: "get_consumers",
                      graph_id: "g-abc",
                      node_ref: "Sales[Total Sales]",
                      depth: -1 }

16_Dependency_Graph { operation: "get_dependencies",
                      graph_id: "g-abc",
                      node_ref: "Sales[Region]" }

16_Dependency_Graph { operation: "find_unused",
                      graph_id: "g-abc",
                      kinds: ["column", "measure"] }

16_Dependency_Graph { operation: "find_cycles",
                      graph_id: "g-abc" }

16_Dependency_Graph { operation: "get_impact",
                      graph_id: "g-abc",
                      node_ref: "Sales[Region]",
                      change: "delete" }

16_Dependency_Graph { operation: "batch_impact",
                      graph_id: "g-abc",
                      node_refs: ["Sales[Region]", "Sales[Amount]"],
                      change: "delete" }

16_Dependency_Graph { operation: "quick_lineage",
                      node_ref: "Sales[Total Sales]", direction: "both", scope: "both" }

16_Dependency_Graph { operation: "export",
                      graph_id: "g-abc", output_format: "mermaid",
                      output_path: "C:/exports/graph.mmd" }

16_Dependency_Graph { operation: "query",
                      graph_id: "g-abc",
                      jsonpath: "$.nodes[?(@.kind==\"measure\")]" }
```

What is in the graph: the **model side** covers tables (calculated tables, field parameters, calc groups), columns, measures (expression, dynamic format string, detail rows), calculation items, RLS table permissions, relationships, hierarchies and DAX UDFs; the **report side** covers pages, visuals, their field bindings and conditional formats, visual/page/report filters, slicer selections, drillthrough, bookmarks and the buttons that navigate to them, and report-level extension measures. Bindings to objects the model lacks are reported as `dangling_refs`. `node_ref` accepts `Sales[Total Sales]`, `[Total Sales]` or a bare table name, case-insensitively.

`get_consumers` returns what **depends on** the node (the objects that break when it goes); `get_dependencies` returns what the node **depends on** (its inputs). The older names `get_upstream` / `get_downstream` (and `direction: "upstream"` / `"downstream"` of `quick_lineage`) still work and answer with a notice. `find_unused` lists objects with no consumer and **refuses** on a graph without the report side (`report_side_unavailable`) unless you pass `allow_model_only: true` — without the report side it cannot know whether a visual uses the object. `get_impact` and `batch_impact` answer "what breaks if this is deleted, renamed or modified"; an unknown node is **never** reported safe. `quick_lineage` builds (or reuses) a graph and answers in one call. `export` writes `json`, `dot` or `mermaid`. `find_unused` with `transitive: true` also lists what is used only by unused objects (and ignores hidden visuals no bookmark ever reveals), in `stage`s; `cleanup_plan` turns that into a staged deletion plan with per-stage evidence, `remove_manually` objects and a `plan_hash`. It only plans — it never deletes anything.

Graphs are cached (LRU of 8, 5-minute lifetime) and keyed by scope, path, the model's etag and a signature of the report files, so a change to either invalidates them; a result says `stale: true` once the model has moved since the build. Other reports bound to the same model are listed as `other_bound_reports` — scan them with `all_bound_reports: true`. The same graph is the evidence the destructive-operation gate uses (§8.2).

---

### 5.27 `17_Desktop_Bridge` — reload and screenshot a running Desktop

**Category:** DEBUG. Wraps Microsoft's Power BI Desktop Bridge CLI (setup: §2.5), which talks to a **running** Power BI Desktop over a local named pipe. This is the **standard live-iteration path**: edit the report files → `reload_and_screenshot` **once** → review → iterate. Desktop stays open, and the server never closes it.

| Operation | What it does |
|-----------|--------------|
| `status` | Running Desktop instances: `pid`, `bridgeStatus`, `currentFilePath`, `hasUnsavedChanges`, the pages (read from `pages.json` on disk) and a `dirty_sources` verdict (`clean`, `dirty`, `unknown`) with the reasons. Also reports `bridge_cli_version` |
| `reload` | Reload the **report** definition in place. Refuses when Desktop may hold unsaved work. With `include_model: true` it also applies **metadata-only** TMDL edits from disk, after a change classifier has proved the change safe and followed by a verification read-back (§7.4) |
| `reload_and_screenshot` | Reload, wait for the render to settle (`settle_ms`, default 2000), then screenshot — in one call. A failed reload never takes a screenshot |
| `screenshot` | Capture one page to a PNG, sized for vision models; `per_visual: true` also crops every visual of that page |
| `screenshot_all` | Capture every page into a directory; a partial capture is `success: true, partial: true` with the failures listed |
| `resolve_page` | Page display name → page id (ambiguous names are reported) |
| `save` | Flush Desktop's in-memory model and report to disk through UI automation, and verify on disk that it happened. Refuses while unreloaded disk edits exist |
| `manifest` | The RPC methods the connected Desktop exposes |
| `open` | Launch a **new** Desktop on a `.pbip` and wait for its bridge — the reopen half of "close without saving → reopen" (§7.4). Refuses `already_open` when an instance already has the project open |

```
17_Desktop_Bridge { operation: "status" }

17_Desktop_Bridge { operation: "reload_and_screenshot",
                    page_name: "Overview", output: "C:/shots/overview.png", scale: 2 }

17_Desktop_Bridge { operation: "screenshot", page_name: "Overview",
                    output: "C:/shots/overview.png", per_visual: true }

17_Desktop_Bridge { operation: "screenshot_all", output_dir: "C:/shots" }

17_Desktop_Bridge { operation: "save", pbip_path: "C:/MyReport.pbip" }

17_Desktop_Bridge { operation: "open", pbip_path: "C:/MyReport.pbip" }
```

**Which instance?** An explicit `pid` wins; otherwise `pbip_path` selects the instance that has that project open (the `.pbip`, its folder, the `.Report` or the bound `.SemanticModel` folder) — even when only one Desktop is running (`instance_path_mismatch` if it is a different project). With neither, the single running instance is used, and several instances give `ambiguous_instance` with the candidates. Calls to one instance are serialised.

**The safety rules the tool enforces** (details in §7.2):

- `reload` / `reload_and_screenshot` **refuse** with `unsaved_changes` or `dirty_state_unknown` when Desktop may hold unsaved in-memory work — a reload re-reads the files and silently discards it. The instance is re-read right before the reload. Ways forward: `save_first: true` (only while no disk edits are pending) or `discard_unsaved: true` (you accept losing them).
- `save` **refuses** with `conflict_pending_disk_and_memory` while this server has written disk edits that Desktop has not reloaded — a save would write Desktop's old copy over them. Override: `overwrite_disk_edits: true`.
- A reload that Desktop rejects is `reload_failed` (the canvas still shows the old state, so no screenshot is taken).
- The reload applies the **report** definition only. Edits to the semantic model's TMDL files report `tmdl_edits_not_applied` / `reopen_required` (§7.4).

---

### 5.28 `00_Client_Mode` — client masking gateway

**Category:** CORE. A small local **gateway** (`http://127.0.0.1:8890`) sits between Claude Code and Anthropic for *routed* chats. Before anything leaves your machine it replaces every client value with a stable pseudonym such as `CUS_48213`: in your own prompt, in every tool result (all MCP servers, files, command output), in the history and in pasted screenshots. It puts the real values back in the answer, so **you and the tools work with real names while Anthropic only sees pseudonyms**. Amounts stay real. This server does not mask anything itself: its results travel inside the request and the gateway masks them.

| Operation | What it does |
|-----------|--------------|
| `status` / `gateway_status` | Is this chat routed, is the gateway running, this chat's mode, dictionary counts per prefix, routed folders, whether every chat is routed — counts only |
| `on` | Masking on for this chat (masking is **off by default**; a chat is masked from the moment it is switched on) |
| `off` | Refused: only you can switch masking off, by typing `masking off` in the chat |
| `refresh` | Re-read the client values of the models open in Power BI Desktop now |
| `gateway_start` / `gateway_stop` | Start / stop the gateway (stopping makes routed chats fail until it runs again; refused inside a routed chat) |
| `route_folder` / `unroute_folder` | Send the chats of one folder through the gateway (`folder_path`), or undo it (undo refused inside a routed chat) |
| `route_all` / `unroute_all` | Send **every** chat through the gateway (your Claude Code user settings), or undo it (undo refused inside a routed chat) |
| `setup` | The command **you** run in a terminal to install the routed masking gateway for the desktop apps (never run by a tool: it puts a certificate in your store); the bare command is a plan, `--yes` applies, see [Part II](#part-ii) |
| `selftest` | Replay every recorded wire fixture of every named app through the current adapters, offline: one PASS / FAIL line per app |

```
00_Client_Mode { operation: "route_all" }
00_Client_Mode { operation: "status" }
00_Client_Mode { operation: "route_folder", folder_path: "C:/path/to/project" }
```

- **Routing says where the traffic goes, not what the data is.** The same repository can hold dev data or client data. Start a new chat after routing; the gateway starts by itself with the chat (and with the MCP server), and a dead gateway is restarted by your next message.
- **Per-chat mode, off by default** (`client_gateway.default_mode`). A chat is masked only after you type `masking on`. While a chat is off a **safety net** stops a request whose NEWEST message (prompt, attached file, tool result) holds known client data (a dictionary value, a name the recognizer finds, a screenshot with a hit) and tells you to type `masking on` (or `masking off` to send it on purpose); it masks nothing and does not look at earlier messages or the system prompt. Type one of these in the chat; they are answered on your machine and never reach the AI:
  - `masking off` (or `client mode off`): this chat only, client values reach Anthropic as they are (dev data). Everything earlier in that chat is then sent unmasked as well, so switch it off at the start of a dev chat;
  - `masking on`: masked from the next message; the whole earlier conversation is read once for names (the first request after it can ask you to send again while that finishes: what was checked is kept);
  - `masking dictionary`: like `on`, but names that no model holds are masked only when the name recognizer is ready, instead of the request being refused while it is not (needs the control token, like `off`);
  - `masking status`: the mode, how many values the dictionary holds, and a warning when masking is on but the dictionary is empty or the remembered-value store cannot be read.
  The AI can switch a chat on but not off; the hook blocks the actions that would switch the gateway off, stop it, take a chat out of it or read its token (a speed bump against a careless model, **not a security boundary**: the token is a file of your own Windows account).
- **What is masked.** The values of the models open in Power BI Desktop: customers, vendors, persons, e-mails, IBANs, companies and entities, profit centers and groups, GL account names and other dimension values. Ordinary words, phrases of ordinary words and values without a letter (amounts, band labels) from generic dimensions and GL accounts are only masked as a whole cell, never in running text; names of customers, vendors and persons are masked everywhere, also inside file names and identifiers, and so are the other values of those tables (addresses, invoice numbers) and a value that is both a name and a dimension value. Period labels (`OCT 25`, `Q3 2025`, `FY24`) are never masked. With no model open and nothing read yet, there is nothing to mask and the chat works normally; a model that opens while you work is read before the next masked request is sent.
- **Data from models the gateway never read** (a published model, a Fabric query, a closed PBIP, a table pasted from Excel): the text cells of every data table in the new content (data-tool results as JSON rows, tab-separated or Markdown tables you paste) are added to the dictionary before the request is masked, with the same rules as a model column. Numbers, dates, periods and code stay readable; metadata queries (`INFO.`, `$SYSTEM`, `TMSCHEMA`) are skipped.
- **Names in no model** are found by the local name recognition (NER), also in files you attach with `@`, IDE selections and table cells. In a masked chat it checks ALL new text or the request is not sent (`ner_incomplete`: still starting, or a very long paste; retry; a model that failed to load is retried after 30 s, doubling). Names it finds are remembered (encrypted, `python -m client_gateway forget` deletes them) so they stay masked after a restart. The model starts when a chat is switched on (or when the safety net first sees prose) and ends after 20 idle minutes while no chat is masked. A client named by part of its name ("Lambert" for "Lambert Bouwwerken NV") is masked too; a part that is an everyday word only inside a sentence.
- **Screenshots.** Client names are read locally (Windows OCR and RapidOCR, full page and 2x tiles) and painted over with their code; an uncertain reading is painted with a neutral `client` label (never a code that could belong to someone else), and text an engine found but could not read reliably (4+ letters, or nothing read in a word-sized box) with `unreadable`. Numbers and short labels stay readable. The painted image is read again: whatever is still readable is painted too, and if a value stays readable the image is withheld. About 6–20 s once per new screenshot (cached).
- **Sources the dictionary does not cover are blocked while masking is on:** Fabric data queries and `01_Connection connect_xmla`. **claude.ai connectors** (tools named `mcp__claude_ai_*`) run on Anthropic's side and bypass the gateway, so they are blocked too. Type `masking off` for dev data.
- **Channels that ignore the gateway are switched off** by the routing (and put back by the undo): `skipWebFetchPreflight`, `DISABLE_FEEDBACK_COMMAND`, `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY`.
- **Remote Control is not available** in routed chats (Claude Code does not offer it when a custom base URL is set).
- **Light on Power BI Desktop.** No timer: the gateway checks the open models only when a chat sends a request and the last check is over a minute old (in the background; the request does not wait), at "masking on", and when a masked request finds a model it has not read. No chat active: no query at all. It reads all client columns only for a new or changed model (or hourly while a chat is active), paced, at below-normal priority.

---

## 6. Common workflows

### 6.1 Refactor a measure with PBIR cascade

You renamed `[Total Sales]` to `[Sales Total]` and want every visual, bookmark, and filter that references it to follow.

```
01_Connection { operation: "connect", model_index: 0 }

02_Model_Operations { object_type: "measure", operation: "rename",
                      table_name: "Sales", measure_name: "Total Sales", new_name: "Sales Total",
                      pbip_path: "C:/MyReport.pbip", preview: true }
```

The preview lists every DAX expression that would change and returns a `preview_hash`. Happy with it? Run the same call without `preview`, add `confirm_destructive: true` (a rename with a `pbip_path` is catastrophic) and pass the hash back as `preview_hash` — the call refuses (`stale_preview`) if the diff changed in between.

Result:
```json
{ "success": true,
  "pbir_cascade": { "files_modified": 14, "replacements": 27, "errors": [] },
  "reload_required": true,
  "reload_op_chain": ["17_Desktop_Bridge:reload_and_screenshot"] }
```

The cascade is parent-kind aware — column rename of `Foo` does NOT touch `field.Measure.Property == "Foo"`. The rename is verified after saving and rolled back if it introduced a new expression error. If you would rather have the server check for consumers first, use `operation: "safe_rename"` (§5.2.11). The model change is live at once, while the report files changed on disk — that combination (an unsaved live-model edit plus unreloaded disk edits) is exactly the "both pending" case of §7.2. The bridge will refuse to reload or save until you choose which side to keep (`unsaved_changes` / `conflict_pending_disk_and_memory`), so read §7.2 before you finish with a reload.

### 6.2 Find and remove unused columns

```
05_Column_Usage_Mapping { operation: "get_unused_columns_pbip",
                          pbip_path: "C:/MyReport.pbip" }
```

Review the list. Then:

```
03_Batch_Operations { object_type: "columns", batch_operation: "delete",
                      items: [
                        { table: "Sales", column: "_Junk1" },
                        { table: "Sales", column: "_Junk2" }
                      ],
                      options: { use_transaction: true },
                      confirm_destructive: true }
```

To have the server prove the columns are unused in the model **and** every bound report first, use `02_Model_Operations` `safe_delete` per column, or `16_Dependency_Graph` `find_unused` (§6.9).

### 6.3 Debug a slow visual

```
09_Debug_Operations { operation: "set_path", pbip_path: "C:/MyReport.pbip" }

09_Debug_Operations { operation: "visual",
                      page_name: "Overview", visual_name: "Sales Matrix",
                      execute_query: true, trace: true, clear_cache: true }
```

Look at the `se_ms` / `fe_ms` split:
- High SE% → too many storage-engine queries; consider aggregations or remove cross-filter
- High FE% → expensive iterators; check for `SUMX` over large tables, nested CALCULATEs

If you have a hypothesis:
```
09_Debug_Operations { operation: "compare",
                      page_name: "Overview", visual_id: "...",
                      original_measure: "[Total Sales]",
                      optimized_expression: "SUMX(Sales, Sales[Quantity] * Sales[Price])",
                      trace: true }
```

For the deep view:
```
13_Query_Plan { operation: "capture", query: "<DAX from debug>" }
13_Query_Plan { operation: "analyze", capture_id: "<id>" }
```

When you have a rewrite, measure it against the original with `13_Query_Plan` `benchmark` (fastest run wins, watch `spread_pct`), then guard against regressions with `09_Validate` `capture_baseline` / `compare_baseline` (§5.19).

### 6.4 Build a new page from scratch

Power BI Desktop stays open. If you made live model edits first (new measures for the page), **save them in Desktop before the file edits** (§7.2) — check with `17_Desktop_Bridge { operation: "status" }`:

```
17_Desktop_Bridge { operation: "status" }

11_PBIP_Authoring { operation: "create_page",
                    pbip_path: "C:/MyReport.pbip",
                    page_name: "Sales Detail",
                    width: 1280, height: 720 }

11_PBIP_Authoring { operation: "create_visual_group",
                    pbip_path: "C:/MyReport.pbip",
                    page_name: "Sales Detail",
                    group_name: "KPI Row",
                    position: { x: 16, y: 16, width: 1248, height: 130 } }

11_PBIP_Authoring { operation: "pbir_add_visuals",
                    pbip_path: "C:/MyReport.pbip",
                    page_name: "Sales Detail",
                    container_display_name: "KPI Row",
                    visuals: [
                      { visual_type: "card", x: 0, y: 0, width: 220, height: 100,
                        title: "Total Sales",
                        fields: { Data: "Sales[Total Sales]" } },
                      { visual_type: "card", x: 240, y: 0, width: 220, height: 100,
                        title: "Total Profit",
                        fields: { Data: "Sales[Total Profit]" } }
                    ] }

17_Desktop_Bridge { operation: "reload_and_screenshot",
                    page_name: "Sales Detail", output: "C:/check.png" }
```

Look at the screenshot, fix what is off (edit → reload again), and check the numbers with `04_Run_DAX` or `09_Validate` `expected_value`. Nothing is closed or reopened.

### 6.5 Optimize DAX with codemods

```
15_DAX_Refactor { operation: "list_transforms" }

15_DAX_Refactor { operation: "preview",
                  transform: "replace_pattern",
                  args: { pattern: "\\bIF\\s*\\(\\s*ISBLANK\\s*\\(([^)]+)\\)\\s*,\\s*0,\\s*\\1\\)",
                          replacement: "COALESCE($1, 0)" } }
```

Review the diff (and any `rejected_edits`). Then:

```
09_Validate { operation: "capture_baseline",
              measures: ["Total Sales"], baseline_path: "C:/baselines/pre.json" }

15_DAX_Refactor { operation: "apply",
                  transform: "replace_pattern",
                  plan_hash: "<hash from the preview>",
                  args: { pattern: "...", replacement: "COALESCE($1, 0)" } }

09_Validate { operation: "compare_baseline", baseline_path: "C:/baselines/pre.json" }
```

The `apply` is atomic and journaled: `15_DAX_Refactor { operation: "rollback", journal_path: "..." }` undoes it. To find what to fix in the first place, run `05_DAX_Intelligence` `lint_model`.

### 6.6 Audit a report against design principles

```
07_PBIP_Operations { operation: "analyze",
                     pbip_path: "C:/MyReport.pbip",
                     output_format: "html", auto_open: true }

07_Theme_Operations { operation: "analyze_compliance",
                      pbip_path: "C:/MyReport.pbip", auto_open: true }

07_Bookmark_Operations { operation: "analyze",
                         pbip_path: "C:/MyReport.pbip", auto_open: true }
```

Three HTML reports — model health, theme compliance, bookmark integrity.

### 6.7 Bulk-update measure DAX with formatter

```
02_TMDL_Operations { operation: "format_dax", format_all: true,
                     max_line_length: 100, on_error: "skip" }
```

Or just specific measures:

```
02_TMDL_Operations { operation: "format_dax",
                     measure_refs: ["Sales[Total Sales]", "Sales[Total Profit]"] }
```

Each measure is round-tripped through daxformatter.com and written back via TOM. Live model is updated in place. **This is opt-in** — measure DAX leaves your machine, so the call returns `external_formatter_disabled` until `"dax_formatter": {"enabled": true}` is set in `config/local_config.json` (or `MCP_DAX_FORMATTER_ENABLED=1`). A formatted measure is written back only if it is token-for-token equivalent to the original.

### 6.8 RLS testing

Define a role:
```
02_Model_Operations { object_type: "role", operation: "create", role_name: "EU_Only" }
02_Model_Operations { object_type: "role", operation: "set_table_filter",
                      role_name: "EU_Only", table_name: "Sales",
                      filter_expression: "Sales[Region] = \"EU\"" }
```

Test with role context:
```
04_Run_DAX { query: "EVALUATE SUMMARIZECOLUMNS('Sales'[Region], \"Total\", [Total Sales])",
             roles: ["EU_Only"] }
```

`04_Query_Operations { operation: "impersonate_run", roles: [...], query: "..." }` is the same call. On Desktop, `effective_user_name` is not supported; use the workaround that rewrites `USERPRINCIPALNAME()`:
```
04_Query_Operations { operation: "simulate_as_user",
                      role_name: "EU_Only",
                      as_user: "user@example.com",
                      query: "..." }
```

### 6.9 Dependency-aware impact analysis

```
16_Dependency_Graph { operation: "build", scope: "both",
                      pbip_path: "C:/MyReport.pbip" }

16_Dependency_Graph { operation: "get_impact",
                      graph_id: "g-...",
                      node_ref: "Sales[Region]",
                      change: "delete" }
```

Returns the full set of measures, columns, visuals, and bookmarks that will break if you delete the column. For several objects at once use `batch_impact` with `node_refs`; for a one-shot answer use `quick_lineage`. `find_unused` is the safe way to build a delete list — it only trusts a graph that includes the report side.

### 6.10 Speed up a slow measure end to end

```
05_DAX_Intelligence { operation: "lint_model", min_severity: "medium" }
04_Run_DAX { query: "EVALUATE ROW(\"v\", [Slow Measure])", mode: "trace" }
13_Query_Plan { operation: "capture", query: "EVALUATE ROW(\"v\", [Slow Measure])" }
15_DAX_Refactor { operation: "preview", transform: "inline_measure", args: { measure_ref: "Sales[Helper]" } }
13_Query_Plan { operation: "benchmark", query: "EVALUATE ROW(\"v\", [Slow Measure])", runs: 7 }
```

`lint_model` finds the candidates, the trace and plan show where the time goes (storage engine vs formula engine, callbacks, cache consumers), the codemod preview shows a fix, and `benchmark` proves it. Guard the numbers with `09_Validate` `capture_baseline` / `compare_baseline` before you apply.

### 6.11 Write and prove a DAX query

Ask the client for the prompt `create-dax-query` (argument `request`), or do it by hand:

1. Read the resource `powerbi://docs/dax-query-rules` (one page of rules that keep `EVALUATE` queries valid) and list the tables and measures so every name is real.
2. Draft the query, then `04_Run_DAX { query: "...", mode: "validate" }`. Repeat until `valid` is true; the engine's error text says what to fix. Nothing is executed.
3. Run it once with `04_Run_DAX { query: "..." }` for the numbers.

More prompts: `run-dax-with-metrics` (cold run with a trace: `mode: "trace"`, `bypass_cache: true`, every returned field shown as is), `analyze-dax-query` (the same run, then a review of the metrics) and `connect-desktop-by-name` (find the Desktop instance that has a given file open, then `01_Connection { operation: "ensure", file_path: "..." }`). Together with the older workflow prompts (`debug-visual`, `analyze-model`, `create-measure`, `document-report`, `optimize-slow-measure`, `live-iterate`, `safe-rename`) that makes eleven; the model and DAX work lives in this server, the rendered canvas and interaction tests in the separate `pbi-webview2` server.

---

## 7. Live report iteration and the Desktop Bridge

Earlier versions of this server had an "autonomous mode" that closed and reopened Power BI Desktop for every report edit. That tool was removed in 7.8. Report edits now reload **in place** through `17_Desktop_Bridge`, Desktop stays open, and the server never closes it on its own. What remains of the old mode is the destructive-operation gate (§8).

### 7.1 The standard loop

```
edit the report files          (07_*, 11_PBIP_Authoring, 07_Theme_Operations — any number of edits)
→ 17_Desktop_Bridge reload_and_screenshot     ONCE, after the batch
→ look at the screenshot
→ iterate
→ verify numbers with 04_Run_DAX / 09_Validate — the screenshot proves layout, not values
```

Batch your edits: every reload repaints the whole report, so one reload per batch is faster and safer than one per edit.

### 7.2 Save and reload: which order?

Power BI Desktop holds the model and the report in memory. A **reload** re-reads the files on disk; a **save** writes Desktop's memory to disk. Each destroys what the other one protects:

| You did | Then | Because |
|---------|------|---------|
| Live model edits (`02_*`, `03_*`, `15_*`) — unsaved in Desktop | **Save first** (`17_Desktop_Bridge` `save`, or ask the user to press Ctrl+S), then edit files | A reload re-reads the disk and silently **discards** unsaved in-memory model edits |
| Edited files on disk | **Reload**, never save first | A save writes Desktop's *old* in-memory copy over your disk edits |
| Both | No order keeps both | Reload with `discard_unsaved: true` and redo the model edits, or save with `overwrite_disk_edits: true` and redo the file edits |

The bridge enforces this. `reload` refuses when Desktop reports unsaved changes **or** when this server made live-model writes that no verified save has flushed since (`unsaved_changes`), and when the state cannot be determined (`dirty_state_unknown`); `save` refuses while unreloaded disk edits exist (`conflict_pending_disk_and_memory`). The error carries `dirty_sources` explaining why.

`17_Desktop_Bridge status` also reports `journal_unflushed`: live writes recorded in the write journal (§5.2.16) that no verified save has flushed; if a reload discards them, `replay` re-applies them.

Never close Desktop to "make a reload work": ask the user, who may have unsaved work.

### 7.3 What a reload applies — and what it does not

By default, `reload` / `reload_and_screenshot` re-read the **report** definition only (pages, visuals, themes, bookmarks), because the CLI's reload sends `reloadModelDefinition: false`. With **`include_model: true`** they also apply **metadata-only** semantic-model (TMDL) edits from disk (§7.4). Model changes made through `02_Model_Operations` are live already, and you verify them with `04_Run_DAX` — never by reloading. Reloading never refreshes data.

### 7.4 Model (TMDL) edits on disk

Sometimes you must change the model's files directly (TMDL). What works depends on the change; the results below were verified against Power BI Desktop 2.157 (26.08) on a scratch project.

| Change on disk | How to apply it |
|----------------|-----------------|
| **Metadata only** — a measure body, a new measure or calculated column, relationships, descriptions, format strings | **`17_Desktop_Bridge { operation: "reload", pbip_path: "…", include_model: true }`** applies it to the running Desktop. The server first runs the unsaved-changes gate, then a **change classifier** that compares the TMDL on disk with the live model. It refuses anything unsafe (`model_reload_unsafe_change`), invalid TMDL (`tmdl_invalid`, nothing reloaded) or a model it cannot identify (`model_change_unclassified`). After the reload it re-reads the model and reports `model_reload.applied` / `mismatch`. A new calculated column was populated without a refresh in the spike; run a `calculate` refresh if its values look stale. Imported data is retained. Fallback when the MCP is unavailable: Microsoft's `powerbi-report-author preview "<.Report folder>" --host desktop --reload-with-model` (see the preflight below) |
| **Partitions, Power Query (M) sources, shared expressions, data sources, new or changed import columns** | **Do not use a model reload.** In the spike the reload emptied the changed table, and the follow-up refresh hung and left the Desktop host permanently unresponsive ("Host is not ready to accept operations"). Close Desktop **without saving** (ask the user), then `17_Desktop_Bridge open` |

`powerbi-report-author` is Microsoft's report-authoring CLI (npm package `@microsoft/powerbi-report-authoring-cli`, public preview). `--status` and `--reload-with-model` need version 0.4.0 or later; the 0.1.x releases do not have them.

**Preflight for a model reload (all must hold):**

1. Finish and validate **all** file edits first.
2. `powerbi-report-author preview "<.Report folder>" --status` must show `details.hasUnsavedChanges` **explicitly `false`**. `null` means "unknown" — never treat it as false — and any `true` means save first (§7.2).
3. `17_Desktop_Bridge status` must show no live-model writes from this session that were not saved (`dirty_sources`). The Microsoft CLI does **not** refuse a model reload when unsaved live edits exist; it reloads anyway and the unsaved measures are silently thrown away. The unsaved-changes gate is entirely your job.
4. Run the reload **once**.

**After the reload:**

- Read the result envelope, not the exit code: an invalid TMDL file returns an error envelope (`RENDER_FAILED`, with the parser message and line) but the process can still exit `0`. The previous model stays intact.
- Verify with DAX (`04_Run_DAX`). Do **not** trust the Data pane: after a model reload it can keep listing objects the engine has already dropped, and the canvas can show cached values until refreshed. Banners such as "calculated columns need to be manually refreshed" can appear.
- Screenshot every affected page.

### 7.5 Choosing the lane

| You want to change | Use | Then |
|--------------------|-----|------|
| A measure, column, relationship, role, … in the model | `02_Model_Operations` / `03_Batch_Operations` (live TOM) | Verify with `04_Run_DAX`; **save in Desktop** before any file edit or reload |
| Pages, visuals, themes, bookmarks | `07_*` / `11_PBIP_Authoring` (files) | `17_Desktop_Bridge reload_and_screenshot` |
| Model files (TMDL) with metadata-only changes | Files + `--reload-with-model` (§7.4) | Verify with `04_Run_DAX` |
| Model files with partition / M / source changes | Files | Close without saving → `17_Desktop_Bridge open` |

### 7.6 Several Desktop instances

`17_Desktop_Bridge` selects the instance by `pid` or `pbip_path` (§5.27). After `open`, confirm `currentFilePath` in `status`: the CLI can hand back the pid of an already-connected instance before the new window is ready, so never trust the returned pid alone.

### 7.7 Bridge CLI notes

- **CLI 1.0.0 is supported** (as are 0.x). `status` and `manifest` report `bridge_cli_version` and `bridge_cli_script`. The server resolves the CLI script from the installed package, so the 1.0.0 layout change (entry point `dist/cli.js`) needs no configuration.
- A reload that Desktop rejects is `reload_failed`: CLI 1.0.0 exits non-zero with a `RELOAD_REJECTED` envelope; CLI 0.x exited 0 with `success: false`. Both surface the same way.
- Other CLI error codes: `CONNECTION_LOST` → `bridge_connection_lost` (with `retryable` / `retryAfterMs`), `AMBIGUOUS_DESKTOP_INSTANCE` → `ambiguous_instance`; codes such as `AMBIGUOUS_OWNING_PBIP` and `OWNING_PBIP_NOT_FOUND` pass through as `cli_error_code`.
- A run that exits 0 with empty output is `cli_no_output` (§9.12).
- Reload and save refuse to *start* when less than about 8 seconds of the 120-second tool budget remain (`budget_exhausted`).

---

## 8. Safety and gating

### 8.1 The gate policy

The environment variable `MCP_AUTONOMOUS_GATE_POLICY` (the name is historical — there is no autonomous mode any more) controls how destructive ops are handled:

| Value | Behavior |
|-------|----------|
| `OFF` | No gating |
| `LIGHT` (default) | Only **catastrophic** ops blocked unless confirmed. Others pass with `gate_notice`. |
| `WARN` | Nothing blocked. All destructive ops emit `gate_warning`. |
| `STRICT` | All destructive ops blocked unless `confirm_destructive: true`. **Recommended for unsupervised agents.** |

Independently, the config setting `security.read_only_mode` blocks every write and destructive **operation** (not whole tools): reads keep working.

### 8.2 Severity levels

- **Read-only:** list, get, describe, sample, statistics, detect, scaffold, a rename with `preview: true` — never gated.
- **Destructive:** ordinary writes (single measure update, single visual edit). Pass under LIGHT, blocked under STRICT.
- **Catastrophic:** mass deletes >10 items, table / calculation-group / culture / named-expression deletes, table renames, any rename with `pbip_path`, a measure or column rename that rewrites at least one expression, `daxlib_install`, report `restore` and `clone_report`, page delete, whole-report theme changes (`set_colors`, `apply_theme`, `import_theme`, `clear_visual_overrides`), `delete_raw_property`, a model refresh without `tables` or covering ten or more tables, and `inline_measure` with `delete_after: true`. Blocked under LIGHT unless you confirm.

**Evidence-aware gate (LIGHT only).** A catastrophic **delete** clears automatically, with `evidence_cleared: true`, only when the server's own dependency graph (model **and** every bound report) proves that nothing uses the object. Any missing piece — no connection, no PBIP path, an unknown or ambiguous target, a graph without the report side, a model that changed since the graph was built — means "not proven", and the normal block applies with `blast_radius` and `evidence_unproven_reasons` explaining why. A caller-supplied `evidence` argument is ignored. `STRICT` is never relaxed, and renames and model refreshes never auto-clear.

### 8.3 Break-glass

For a single call:
```
03_Batch_Operations { ..., confirm_destructive: true }
```

This bypasses the gate **for that one call only**. Subsequent calls are gated normally. `confirm_destructive` must be the JSON boolean `true` — the string `"false"` never confirms.

### 8.4 Reload-required hint

PBIP-disk-write ops return `reload_required: true` plus a `reload_op_chain` so the agent (or you) knows what to do next:

```json
{ "...": "...",
  "reload_required": true,
  "reload_op_chain": ["17_Desktop_Bridge:reload_and_screenshot"] }
```

Reload **once** after the whole batch of edits, and never save first (§7.2). Edits to the model's TMDL files get a different hint (`reload_applies: false`, `reopen_required: true`; §7.4). Live-TOM ops (model CRUD) don't set the flag — they propagate immediately. A cached `17_Desktop_Bridge` status that shows the report open with unsaved changes is returned on write results as `desktop_state`, and under `STRICT` it blocks the write unless you confirm.

---

## 9. Troubleshooting

### 9.1 "Power BI Desktop not found"

Open Power BI Desktop with a model loaded. Then:
```
01_Connection { operation: "detect" }
```

If the model is still loading, wait a few seconds and retry — `detect` only finds instances with a fully-loaded analysis-services port.

### 9.2 "TREATAS error: column mismatch"

Two TREATAS filters for the same slicer table = two **separate** slicer visuals on the page (not both applied to one). The debug handler reads slicer state from the PBIP — it may be stale if PBI Desktop has unsaved slicer state. Check `pbip_warning` in the response.

Workaround: pass the current values as `filters`, or paste Desktop's own query as `reference_query` (§5.18.1), or construct the filters by hand via `09_Debug_Operations` `run_dax`.

### 9.3 "Card visual shows nothing / says 'Fields need to be fixed'"

Three common causes:

**1. Wrong bucket.** Card with field parameters may bind to `Data` (cardVisual) or `Values` (card). Check an existing same-type visual.

**2. Measure name mangled by Unicode.** If your measure name contains `Δ`, `∆`, `€`, raw shell pbir mangles it to `Î"`. Use `11_PBIP_Authoring` `pbir_add_visuals` instead — it's UTF-8 safe (mechanism and byte-level recovery: [Part VI §2.2](#22-prefer-mcp-op-over-raw-pbir-shell--encoding-matters)).

**3. Slicer dependency missing.** Δ / Simul measures depend on the variance slicer being set. Inject the slicer filter in the debug query, or use the no-dependency variant (e.g. `Net Asset Value Δ Simul 1` instead of `Net Asset Value Δ (Simulation)`).

### 9.4 "Card text is huge / overflows the card"

`cardVisual` has TWO font multipliers that compound (`value.fontSize` × `layout.calloutSize`); the conservative sizes are in [§5.14.7](#5147-cardvisual-font-compounding). After ANY card font change: edit → `17_Desktop_Bridge reload_and_screenshot` → verify.

### 9.5 "PBI crashed after `pbir cp`"

Always run `python3 ~/bin/pbir_fix_copied_page.py "Report.Report/definition/pages/<new_id>"` after `pbir cp`. There's a known mismatch bug — without the fix script, PBI throws `VisualRelationshipService.getRelationship TypeError`.

Better yet: use `11_PBIP_Authoring` `clone_page` (no fix script needed; visual ids and interactions are remapped and the page is registered in `pages.json`).

### 9.6 "Thousands of files changed in git after a single edit"

Likely cause: someone ran `pbir validate` on a report whose visuals are at an older schema version. Older pbir CLI versions silently rewrote every `visual.json` and `page.json` to the current schema (e.g. 2.4.0 → 2.7.0). From pbir 0.9.25 the mass `$schema` rewrite is behind the opt-in flag `--update-schemas` (see [Changes in 0.9.25](#changes-in-0925)), so plain `pbir validate` is a read-only check — but if you are on an older CLI, or used that flag, the rewrite happens.

Recovery:
```bash
git checkout -- "Report.Report/definition/pages/"
```

Only do that when you know the changed files contain nothing but the schema rewrite (uncommitted work in the same folder is lost). Scope validation to what you need: `pbir validate "Report.Report/SinglePage.Page"`.

### 9.7 "Reload refused: `unsaved_changes`, `dirty_state_unknown` or `conflict_pending_disk_and_memory`"

The bridge is protecting work that a reload or save would destroy (§7.2). Read `dirty_sources` in the error — it says whether Desktop reports unsaved changes, whether this server made live-model writes that were never saved, or whether disk edits are pending.

- **`unsaved_changes` / `dirty_state_unknown` on reload** → the model in Desktop's memory is newer than the disk. If there are **no** disk edits pending, `save_first: true` (or `17_Desktop_Bridge save`) then reload. If there are, no order keeps both: `discard_unsaved: true` (redo the live-model edits afterwards) or save with `overwrite_disk_edits: true` (redo the file edits).
- **`conflict_pending_disk_and_memory` on save** → you have disk edits Desktop has not loaded; reload instead of saving.
- `dirty_state_unknown` also appears when the instance is not connected to the bridge or does not report the flag — run `17_Desktop_Bridge status` and wait for `connected`.
- After a reload you had to force (`discard_unsaved: true`), the edits are recoverable from the write journal: see §9.18.

### 9.8 "Different DAX results between two PBI instances"

Query results are cached per connection *and* model generation, and a connection change clears the cache, so an answer from instance A is never replayed for instance B. If you still suspect a stale answer, force a round trip:

```
04_Run_DAX { query: "EVALUATE { [Total Sales] }", bypass_cache: true }
```

Verify the connected instance via the `database` field in the connection result (`01_Connection { operation: "status" }`) — with several instances open, connect explicitly by `model_index`, or `ensure` with `prefer_pid`.

### 9.9 "Agent says HTML report won't generate"

Use `07_PBIP_Operations` with the right operation:
- ✅ `analyze` with `output_format: "html"` and `auto_open: true` — full PBIP HTML model report
- ✅ `dependency_html` — interactive dependency browser (different thing)
- ❌ `generate_documentation` — produces JSON, NOT HTML

### 9.10 "I made changes but PBI doesn't see them"

It depends on what you changed:

- **Report files (pages, visuals, themes, bookmarks):** run `17_Desktop_Bridge reload_and_screenshot`. Desktop does not watch the files by itself.
- **Live model edits (`02_Model_Operations`, `03_Batch_Operations`, `15_DAX_Refactor`):** they show immediately in the running Desktop. If you cannot see one, verify with `04_Run_DAX`; do not reload. They exist only in memory until you **save in Desktop** — a reload from disk would throw them away (§7.2).
- **Model files (TMDL):** the bridge reload does not apply them. Metadata-only edits can be applied with the model-aware reload of §7.4; partition / M / source changes need "close without saving → `17_Desktop_Bridge open`".
- **You saved in Desktop after editing files:** Desktop wrote its older in-memory copy over your disk edits. Save *before* disk edits, reload *after* them (§7.2).

Prefer `02_Model_Operations` (live TOM) for model changes — they propagate without any reload. Edit TMDL only for schema-level things TOM does not expose.

### 9.11 "Format string changes don't apply"

For dynamic format strings (CL 1606+) on standalone measures, use:
```
02_Model_Operations { object_type: "measure", operation: "set_format_string_expression",
                      table_name: "Sales", measure_name: "Total Sales",
                      expression: "..." }
```

Plain `format_string` on `update`/`create` sets the **static** format string. The dynamic format string is a separate property (`Measure.FormatStringDefinition.Expression`).

### 9.12 `cli_no_output`

The Desktop Bridge CLI exited successfully but printed nothing, so the server had no answer to read. The usual cause is the wrong script: a CLI from 0.2.0 on (including 1.0.0) moved its entry point to `dist/cli.js`, and `dist/index.js` is now a silent library. The server picks the right script from the installed package, so check:

1. `17_Desktop_Bridge { operation: "status" }` → `bridge_cli_version` and `bridge_cli_script` (the script that was run).
2. Make sure you run the current server build (7.15.0 and later resolve the 1.0.0 layout; older builds ran `dist/index.js` and would report this).
3. As a last resort point `MCP_PBI_BRIDGE_CLI_JS` at the CLI's `dist/cli.js`.

### 9.13 `reload_failed` / `RELOAD_REJECTED`

Desktop refused the reload, so the canvas still shows the old state and no screenshot was taken. CLI 1.0.0 reports this as a non-zero exit with a `RELOAD_REJECTED` error envelope; CLI 0.x exited 0 with `result.success = false` — the server maps both to `reload_failed`. The error keeps the CLI's `cli_error_code`, `cli_error_message` and `raw_stderr`.

- Look at the message: a malformed report file (for example a broken `pages.json`, which `status` reports as `pagesError` on the instance) is the most common cause. Fix the file and reload again.
- If Desktop is busy or showing a dialog, close the dialog (in the Desktop window, not by killing it) and retry.
- `CONNECTION_LOST` (`bridge_connection_lost`, with `retryable`) means the pipe dropped — re-run `status`, then retry.

### 9.14 "Host is not ready to accept operations"

Desktop's bridge host is up but not accepting operations (JSON-RPC error `-32502`). Usually Desktop is still loading the file, is showing a modal dialog, or is in the middle of a refresh. Wait a few seconds (`17_Desktop_Bridge status` with `wait_seconds`), dismiss any dialog and retry. If it **never** clears, the host is stuck: this was observed after a model reload of a changed partition / M source followed by a refresh that hung. The instance then has to be closed **without saving** (ask the user; anything unsaved in it is lost) and reopened with `17_Desktop_Bridge open`. To avoid it, do not use a model reload for partition, M or data-source changes (§7.4).

### 9.15 The Data pane still shows a measure after a model reload

After a model reload (§7.4) Desktop's **Data pane can be stale**: it may keep listing objects the engine has already dropped (for example a measure that was only in memory and was discarded), and the canvas can show cached values until refreshed. Banners such as "One or more calculated columns need to be manually refreshed" or "Some of the tables have incomplete or no data" can appear too. Trust the engine, not the pane: check with `04_Run_DAX` (or `02_Model_Operations` `measure`/`list`), then refresh or reopen Desktop if the pane needs to catch up.

### 9.16 `timeout_outcome_unknown`

A **write** ran out of time but may still be running or may already have landed. Do not retry blindly: verify with the check the result names (`verify_with`), for example listing the object or running a DAX query, and only then decide. An identical re-issue while the first is still running returns `write_in_progress`. `desktop_unresponsive` means earlier calls are still blocked — wait for them.

### 9.17 Wrong Desktop targeted

`ambiguous_instance` means several Desktop instances are running and neither `pid` nor `pbip_path` was given — pass one. `instance_path_mismatch` means the instance you named does not have that project open. After `17_Desktop_Bridge open`, confirm `currentFilePath` with `status` before trusting the returned pid (§7.6).

---

### 9.18 "My live edits vanished after a reload or a crash"

A reload re-reads the disk, so any model edit that was never saved is gone; Desktop closing without saving does the same. The write journal still has them.

1. `02_Model_Operations { object_type: "model", operation: "history", unflushed_only: true }` — the writes that never reached disk.
2. `02_Model_Operations { object_type: "model", operation: "replay", preview: true }` — what would be re-applied, what is already in the model (`skipped`), and what conflicts.
3. `02_Model_Operations { object_type: "model", operation: "replay" }`, then verify with `04_Run_DAX` and **save** (`17_Desktop_Bridge save`) so the edits are flushed.

If `history` is empty, the journal was off (`model_write.journal_enabled`) or the connection points at a different project than the one you edited (`history journal: "<name>"` opens another journal; `other_journals` lists them). A write listed with `degraded` had no readable before-image: it is visible but cannot be undone or replayed.

### 9.19 `service_read_only`, `xmla_auth_required` or `project_open_in_desktop`

- **`service_read_only`** — you are connected to a published model with `connect_xmla`, which never writes. Make the change in the PBIP (in Desktop, or offline with `connect_folder`) and publish it the usual way.
- **`xmla_auth_required`** — no token was found. Set `MCP_PBI_XMLA_TOKEN`, or sign in once with the Fabric tooling that fills the MSAL cache (`~/.fabric-mcp/xmla_token_cache.json`). Treat that cache file like a password: it holds a refresh token.
- **`project_open_in_desktop`** — `connect_folder` points at a project that Desktop has open. Close it there first (save or discard your Desktop edits), or pass `allow_open_in_desktop: true` when you only read.

### 9.20 A tool is missing from the list

A tool profile is hiding it (§4.7): `01_Connection { operation: "status" }` shows `tool_profile`. Unset `MCP_PBI_PROFILE` / `MCP_PBI_TOOLS_EXCLUDE` and restart the server.

## 10. Quick reference card

### 10.1 Connect

```
01_Connection { operation: "detect" }
01_Connection { operation: "connect", model_index: 0 }
01_Connection { operation: "ensure" }
01_Connection { operation: "connect_xmla", workspace: "Finance", model: "S02-SemanticModel" }
01_Connection { operation: "connect_folder", pbip_path: "C:/repo/Model.SemanticModel" }
```

### 10.2 Read the model

```
02_Model_Operations { object_type: "table",   operation: "list" }
02_Model_Operations { object_type: "measure", operation: "list" }
02_Model_Operations { object_type: "relationship", operation: "list" }
```

### 10.3 Run DAX

```
04_Run_DAX { query: "EVALUATE …" }
04_Run_DAX { query: "EVALUATE …", mode: "trace", clear_cache: true }
04_Run_DAX { query: "EVALUATE …", bypass_cache: true }
04_Run_DAX { query: "EVALUATE …", mode: "validate" }
```

### 10.4 Edit model

```
02_Model_Operations { object_type: "measure", operation: "create",
                      table_name: "...", measure_name: "...", expression: "..." }
03_Batch_Operations { object_type: "measures", batch_operation: "create",
                      items: [...] }
```

### 10.5 Edit report (Desktop can stay open)

```
07_Page_Operations  { operation: "create", pbip_path: "...", page_name: "..." }
07_Visual_Operations { operation: "create", pbip_path: "...",
                       page_name: "...", visual_type: "...", position: {...} }
11_PBIP_Authoring   { operation: "pbir_add_visuals", pbip_path: "...",
                      page_name: "...", visuals: [...] }
17_Desktop_Bridge   { operation: "reload_and_screenshot",
                      page_name: "...", output: "..." }
```

### 10.6 Debug

```
09_Debug_Operations { operation: "set_path", pbip_path: "..." }
09_Debug_Operations { operation: "visual",
                      page_name: "...", visual_name: "...", trace: true }
```

### 10.7 Live iteration

```
17_Desktop_Bridge { operation: "status" }
   … save live model edits in Desktop FIRST (§7.2), then edit the report files …
17_Desktop_Bridge { operation: "reload_and_screenshot",
                    page_name: "...", output: "..." }
```

### 10.8 Safety

- Default policy: `LIGHT` (catastrophic blocked, ordinary destructive passes)
- Break-glass: `confirm_destructive: true` (single call)
- Strict mode: `MCP_AUTONOMOUS_GATE_POLICY=STRICT`
- Reload re-reads disk and drops unsaved model edits; a save overwrites disk edits (§7.2)

---

## Further reading

- [Part III — Tool reference](#part-iii) — every tool, operation and argument (the exhaustive reference)
- [Part IV — Architecture](#part-iv) — module-level deep dive of the server
- **AGENTS.md** in the repo root — compact project index, tool map and operating rules
- [Part VI — Agentic stack index](#part-vi) — top-level cross-layer index
- **docs/visual-catalog/** — per-visual-type reference (roles, containers, properties, gotchas; start at `INDEX.md`)
- **docs/design-principles/** — design reasoning + self-audit checklists (7 topics)
- [Part V — PBIR reference](#part-v) — the PBIR JSON format and every pbir 0.9.9 command and gotcha, with what changed by 0.9.25

<a id="part-ii"></a>
# Part II — Client masking

The Power BI MCP can hide client names from the AI vendor. In a chat where you switch masking on, every
client value (customer, vendor, person, company, account, IBAN, e-mail, phone) is replaced by a pseudonym such
as `CUS_10234` before it leaves your PC, and you read the real names again in the answers. The vendor only
ever sees pseudonyms. You use your normal login (claude.ai, ChatGPT account, Copilot subscription); no API key
is involved.

Deeper detail: [Part III](#part-iii) (`00_Client_Mode`), [Part IV](#part-iv) (client gateway) and the
[best-practice review](#best-practice-review-2026-10-08) below. The design notes and hard rules R1-R10 are kept
outside this repository.

## Masking on demand (Claude Code)

Nothing runs until you ask for it. Type, as your **whole message**:

| You type | What happens |
|----------|--------------|
| `masking on` | The masking gateway starts (the first start reads the client dictionary: a few seconds, up to about 30 s for a very large one), this chat is switched on, and the chat's project folder is routed through the gateway (`ANTHROPIC_BASE_URL` in its `.claude/settings.local.json`, kept out of git). Claude Code picks that up within a few seconds; from your next message on the chat is masked. A message you send before Claude Code has switched over is held ("send it again in a few seconds"), never sent unmasked. |
| `masking off` | Every chat of this project folder is switched off and the folder goes straight to Anthropic again. The gateway stops by itself about half a minute later, unless another folder still has masking on. |
| `masking status` | Whether this chat is masked and whether the gateway runs, and, since the gateway started: requests masked, requests blocked (nothing sent), messages that went on before name recognition finished. |

- The commands are answered on your PC; the AI never sees them. Only you can switch masking off: the model, a
  tool result or a file never can (the MCP tool `00_Client_Mode` can switch it on, never off).
- Without `masking on` there is no gateway, no routing and no masking: your chats talk to Anthropic directly.
- Power BI MCP results are masked only in a chat with masking on (the MCP asks the gateway about its own chat).
- Other chats in the same folder while one has masking on go through the gateway unchanged (not masked).
- A chat you closed with masking on leaves its folder routed and the gateway running: type `masking off` in
  any chat of that folder, or run `python -m client_gateway masking off --folder <path>` in a terminal.
- Claude Code's own instructions (the system prompt) are masked too, except their first short identity lines
  ("You are Claude Code, Anthropic's official CLI for Claude." and a billing line): Anthropic checks those for a
  subscription login and refuses a rewritten one (HTTP 429 on every request). The longer instructions after them,
  with your project folder and memory path, are masked like everything else - what you type, CLAUDE.md, files,
  tool results, Power BI data.

## What is covered

| App | What is masked / restored | Notes |
|-----|---------------------------|-------|
| Claude Code (CLI, VS Code extension) | typed text, files, tool results, screenshots | on demand: `masking on` / `masking off` (above) |
| VS Code GitHub Copilot | chat and responses API: typed text, tool results, images; replies restored | only with the desktop-app routing (`setup --desktop-apps`, below) |
| ChatGPT in the browser (logged in or out) | typed text, uploads; replies restored | only with the desktop-app routing; MCP tools inside ChatGPT need OpenAI's own connector setup (not automated) |
| Claude Desktop | Power BI results are masked by the MCP itself | the conversation traffic is pending its wire capture: typed text is not masked |

Not covered: Firefox, the ChatGPT desktop and mobile apps, developer API traffic (`api.openai.com`,
`api.anthropic.com` used by scripts and SDKs).

## Install

Run `MCP Server Setup.bat`. Its last step runs `python -m client_gateway setup --yes`, which:

1. adds the masking hooks to your Claude Code user settings (`~/.claude/settings.json`, a backup first, other
   hooks untouched): they answer `masking on|off|status` and guard the gateway;
2. sets `client_gateway.mcp_masking=true` and `client_gateway.routing=false` in this install's
   `config/local_config.json` (other keys kept, a backup first);
3. moves an older install that routed every chat through the gateway back to direct (that was the old default).

No certificate, no proxy setting, no service, scheduled task or Run key. By hand, from the repo:

```text
python -m client_gateway setup                     # the PLAN: reads the situation, changes nothing
python -m client_gateway setup --yes               # apply (Claude Code, masking on demand)
python -m client_gateway setup --yes --register    # also register the Power BI MCP in your AI apps
python -m client_gateway setup --status            # what is installed (read-only)
```

### Copilot and ChatGPT (opt-in: `--desktop-apps`)

Copilot and ChatGPT have no hook that could start the gateway when you type `masking on`, so for them the
gateway has to run whenever the Power BI MCP runs. `setup --yes --desktop-apps` adds, in this order, and stops
without changing anything when a check fails:

1. **Managed PC check.** Intune enrollment or Defender for Endpoint: clear the certificate with IT first, then add
   `--managed-ok`.
2. **Proxy check.** Proxy settings locked by policy, or "Automatically detect settings" (WPAD) that finds a PAC
   script or cannot be probed: routing is refused, the company proxy owns it.
3. **Certificate.** A local CA that can only vouch for the AI hosts (`claude.ai`, `chatgpt.com`, `openai.com`,
   `oaiusercontent.com`, `githubcopilot.com`; every IP address excluded; key encrypted with your Windows login),
   installed in `Cert:\CurrentUser\Root`. Windows asks you to confirm.
4. **Config.** `client_gateway.routing=true`: the gateway then starts with the Power BI MCP and ends with the last
   one; while it runs a per-user PAC sends only the AI hosts through it and everything else exactly as before.

In those apps `masking on` / `masking off` typed in the chat switch the chat (the gateway reads your newest
message only).

## Profiles

`client_guard.profile` (config) or `FINVISION_MASKING_PROFILE` (environment, wins):

- `generic` (default): prefixes `CUS` `VEN` `PER` `ORG` `ACC` `USR` (and `VAL` for any other client value), e-mail /
  IBAN / phone shapes, no company naming conventions, name recognition off until you enable it.
- `finvision`: the Finvision behaviour: `FAM` `GLA` `PCC` `ENT` `DIM` `CLI` prefixes, the S01/S02 safe tables, the
  Belgian shapes, name recognition on. Existing pseudonyms stay stable.

Product and vendor names of the profile's allow list (Anthropic, Claude, Microsoft, Power BI ...) and the terms in
`<masking home>/allow_terms.txt` are never masked, even when a model holds them as a vendor.

## Known limits

- **Context is not masked.** Amounts, dates, cities, roles and events go out as they are (the AI needs them
  for the analysis). Pseudonymised data is still personal data (EDPB): a strong model can sometimes work out
  who a client is from that context. Masking lowers the risk; the vendor agreement still applies.
- **New codes every 90 days.** The pseudonym key is replaced at a gateway start once it is older than
  `client_guard.key_rotation_days` (90; 0 = never), or now with `python -m client_gateway rotate-key` (while no
  gateway runs), so the vendor cannot link one client's conversations over a long time. Codes from before
  (in an old chat) then stay codes; they are never shown as another client.

- **Name recognition** checks the new text of a message for at most 8 seconds, then the message goes on; names
  in the dictionary (the models read, names remembered from earlier chats, your client list) are always masked.
  A name in no model and not recognized in time can pass once.
- **The system prompt's identity lines** (above) are sent unchanged; a client value there is only counted
  (`system_values_passed` in `status`).
- **Copilot compaction** (desktop routing): Copilot identifies a chat by its first typed turn; when it compacts a
  chat and drops that turn the chat key changes.
- **Claude Desktop** conversation traffic: pending its capture.
- Files on disk (exports, spilled results) keep real values; an agent that reads one sends it in the next request,
  which the gateway masks in a chat with masking on.

## Remove

```text
python -m client_gateway masking off --folder <path>   # a folder that still has masking on
python -m client_gateway setup --remove --yes          # undo exactly what setup recorded
```

It removes the hooks setup added (a hook you changed since stays), sets `routing` / `mcp_masking` to false, and
with `--desktop-apps` installs also restores the proxy backup exactly and removes our certificate by its recorded
thumbprint. Every backup is kept. Setup records what it did in `<masking home>/setup_record.json`; the home is
`%LOCALAPPDATA%\FinvisionMasking`. It refuses while a gateway runs.

## Troubleshooting

- `masking status` in the chat, `00_Client_Mode status` (gateway running, mode, dictionary counts, `warnings`)
  and `python -m client_gateway setup --status` (hooks, flags, registrations, coverage).
- The gateway log `<masking home>/gateway.log` holds counts and status codes only, never content: a masked
  request answered `429` means the request was refused by the vendor (see the system prompt note above).
- `00_Client_Mode selftest` or `python tools/masking_lab/lab.py selftest`: replays every recorded wire fixture of every
  app through the current code, offline, one PASS / FAIL line per app. Run it after an app or MCP update.
- `warnings` codes: `routing_inactive:<reason>` (desktop routing only: `ca_missing`, `ca_not_trusted`,
  `ca_needs_rotation`, `refused_locked`, `refused_autodetect`, `refused_autodetect_unknown`, `frontend_*`),
  `dictionary_empty`, `store_error:*`, `token_key_ephemeral`, `marker_unavailable`, `rehydration_unrecognised` (a
  reply format the gateway did not know: pseudonyms visible, nothing leaked), `untested_app_version:<app>`,
  `profile_unknown`, `shape_pack_unknown`, `ner_unavailable`, `ner_load_failed`.

## Best-practice review (2026-10-08)

What the literature and the main tools recommend for hiding personal and client data from an AI vendor, what
the Finvision gateway does for each point, and what is still open. The user-facing description is the rest of
this Part; the design notes and hard rules R1-R10 are kept outside this repository.

### Sources

- OWASP Top 10 for LLM Applications 2025, [LLM02 Sensitive Information Disclosure](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/)
- EDPB [Guidelines 01/2025 on pseudonymisation](https://www.hunton.com/insights/publications/edpb-advises-on-pseudonymisation-for-gdpr-compliance) (summary; [Stibbe](https://stibbe.com/publications-and-insights/key-takeaways-and-insights-from-the-edpb-pseudonymisation-guidelines))
- ENISA, [Pseudonymisation techniques and best practices](https://www.enisa.europa.eu/publications/deploying-pseudonymisation-techniques) (2019) and [Advanced techniques and use cases](https://www.enisa.europa.eu/sites/default/files/publications/ENISA%20Report%20-%20Data%20Pseudonymisation%20-%20Advanced%20Techniques%20and%20Use%20Cases.pdf) (2021)
- Microsoft Presidio [FAQ](https://microsoft.github.io/presidio/faq/), LangChain [reversible anonymization](https://python.langchain.com/docs/guides/privacy/presidio_data_anonymization/reversible)
- Unicode [TR39 security mechanisms](https://www.unicode.org/reports/tr39/) (confusables), [Mimecast: keystrokes that defeat a privacy filter](https://www.mimecast.com/blog/5-keystrokes-that-defeat-a-privacy-filter/)
- CAMP, [cumulative masking for multi-turn conversations](https://arxiv.org/abs/2604.16521) (2026)
- Staab et al., [Beyond Memorization: privacy via inference](https://alphaxiv.org/abs/2310.07298) (ICLR 2024); Lermen et al., [Large-scale online deanonymization with LLMs](https://arxiv.org/pdf/2602.16800) (2026)
- Deußer et al., [On the impact of anonymization on LLM performance](https://arxiv.org/pdf/2609.11335) (2026)
- [GLiNER2-PII](https://arxiv.org/pdf/2605.09973) (2026)
- Vendor and practitioner write-ups (claims not independently verified): [Grepture mask-and-restore](https://grepture.com/blog/mask-and-restore-reversible-redaction-llm), [LLM-Shield-Proxy](https://pypi.org/project/llm-shield-proxy/), [MLflow PII redaction](https://mlflow.org/articles/pii-redaction-llm/), [DeepInspect fail-open vs fail-closed](https://www.deepinspect.ai/blog/ai-gateway-fail-open-vs-fail-closed), [hoop.dev PII redaction for MCP gateways](https://hoop.dev/blog/getting-pii-redaction-right-for-mcp-gateways/)
- Claude Code subscription check on the system prompt (community findings, no Anthropic documentation): [hermes-agent #53212](https://github.com/NousResearch/hermes-agent/issues/53212), [peaproxy #29](https://github.com/ks1686/peaproxy/pull/29), [claude-code #87420](https://github.com/anthropics/claude-code/issues/87420)

### Checklist

| # | Practice | Status | Where |
|---|----------|--------|-------|
| 1 | Mask at a gateway inside your own perimeter, before the vendor (and its logs) sees anything | Done. The gateway runs on the PC, only while a chat has `masking on` | `client_gateway/` |
| 2 | Reversible, typed, consistent pseudonyms; restore only the tokens you issued | Done. `CUS_10234`-style codes, one per value; an unknown code-shaped string is left alone and counted (`unknown_tokens`), so a code the model invents never turns into a real name | `core/client_guard/engine.py` `unmask_text` |
| 3 | Keyed pseudonyms, not plain hashes (dictionary attacks on low-entropy names) | Done. HMAC-SHA256 with a 32-byte key kept on the PC | `core/client_guard/tokens.py` |
| 4 | Keep the map local and protected; short lifetime | Done. In memory while the gateway runs; the remembered dictionary is DPAPI-encrypted for the Windows user, deleted with `forget` | `client_gateway/store.py` |
| 5 | Layered detection: patterns with checksums, then NER, then the rest | Done, and stronger than pattern + NER: the main layer is the dictionary read from the client's own data (the open Power BI models, remembered values, the client list). Shapes with checksums (IBAN MOD-97, Belgian national and enterprise numbers, e-mail, phone). Local NER (GLiNER) only for names no model holds | `detector.py`, `shapes.py`, `ner.py` |
| 6 | Normalise before detecting: NFKC, invisible characters, whitespace, diacritics | Done (NFKC, casefold, format characters, every whitespace kind, diacritics) | `engine.normalise` |
| 7 | Fold cross-script look-alikes (Cyrillic "о" for Latin "o"); NFKC does not | **Added 2026-10-08**: the Unicode TR39 confusables table (generated from Unicode's own file, not a hand list) is applied on the matching copy | `data/confusables_latin.txt`, `tools/masking_lab/gen_confusables.py` |
| 8 | Typos and variants the model or the user writes | Done. Fuzzy matching, name parts, initials, "Last, First", slugs (`zorvex-holding`), URL encoding | `fuzzy.py`, engine |
| 9 | Keep state across turns (stateless per-message scanners miss names spread over a conversation, CAMP) | Done. One dictionary for every chat; the whole history is checked the first time a chat is masked; earlier turns are replayed byte-identical | `masking.py`, `memo.py` |
| 10 | Streaming restore that survives a code split across chunks | Done. A hold-back keeps an incomplete code (`CUS_4`) until the next chunk | `client_gateway/sse.py` |
| 11 | Scan every part of the request, not only the user's text | Done for messages, tool definitions and tool results. **Since 2026-10-08 also the system prompt**, except its first short identity lines (see below) | `masking.py` `_system` |
| 12 | Tool results masked before the model sees them | Done. The MCP masks its own results, the gateway masks every other tool result | `server/masking_layer.py`, `masking.py` |
| 13 | Tool-call arguments that go to third parties | **Partly.** In a masked chat the claude.ai connectors and the Fabric data tools are blocked. Other tools run locally with the real names (that is what makes a DAX query or a file search work); a WebFetch URL or a `curl` the model builds can therefore carry a real name to that website. See open decision A | `hooks/client_masking_hook.py` |
| 14 | Logs and telemetry without content | Done. The gateway log holds counts and fixed labels only | `client_gateway/log.py` |
| 15 | Decide fail-open vs fail-closed per component, and make degradation visible | Masking error, residual leak, unknown request shape: closed (nothing is sent). Gateway down while routed: closed (Claude Code cannot reach it; no fallback to direct). A message sent before Claude Code switched over: held. Name recognition slower than 8 s: open (the message goes on, counted `ner_partial`; dictionary names are always masked). Counters are in `status`; see open decision D | `masking.py`, `server.py` |
| 16 | Test against evasion and regressions | Done. Leak matrix (every spelling x every request position), recorded wire fixtures replayed offline (`selftest`), E2E with a real Claude Code session. Look-alike spellings added 2026-10-08 | `tests/test_client_gateway_leak_matrix.py`, `tools/masking_lab/` |
| 17 | Policy in configuration, not code | Mostly. Thresholds, NER model, profiles, allow terms (`allow_terms.txt`) are configuration; the bundled word lists are data with generators (`gen_common_words.py`, `gen_confusables.py`) | `config/default_config.json`, `core/client_guard/data/` |
| 18 | Unique identifiers per entity keep the model useful (Deußer 2026) | Done. Each client gets its own code, with a type prefix | engine |
| 19 | Limit linkability: rotate the pseudonym key (ENISA) | **Added 2026-10-08**: a new key every 90 days (`client_guard.key_rotation_days`, 0 = never), at a gateway start, or now with `python -m client_gateway rotate-key`. Each key generation numbers in its own range, so an old code is never restored to another client | `core/client_guard/tokens.py` |
| 20 | Make degraded masking visible | **Added 2026-10-08**: `masking status` shows, for the current gateway run, masked and blocked requests, messages that went on before name recognition finished, and client values in the identity lines | `client_gateway/switch.py`, hook |

### The system prompt (changed 2026-10-08)

Claude Code's system field is `[billing line, identity sentence, instructions]`. With a subscription login,
Anthropic checks the identity sentence ("You are Claude Code, Anthropic's official CLI for Claude.");
when it was rewritten, every request got HTTP 429 (2026-10-08, and the same finding in the community issues
above). Until today the whole field was forwarded unchanged, so the instructions block (project folder,
memory path, MCP server instructions) could carry a client name out. Now the leading short blocks (at most
400 characters each) are kept byte-identical and everything from the first long block on is masked. The rule
is structural (position and length), not a copy of Anthropic's wording, so a reworded identity sentence
keeps working. Verified live: a project folder named after a client reached the model as its code, answer
200. If Anthropic ever checks more of the prompt, the symptom is a 429 without rate-limit headers on every
masked request; the log shows it (`masked: true, status: 429`).

### Decisions

Taken 2026-10-08: **B** key rotation (done, 90 days), **C** accepted as a known limit, **D** done, **E**
evaluated and acted on (below). **A** skipped by the owner (a web request the model makes can carry a
real client name to that website; Anthropic still only sees the code).

**A. Real names in web requests the model makes.** Best practice is to block or confirm a call to a third
party whose arguments hold client data. Option: in a masked chat the PreToolUse hook asks the gateway whether
a WebFetch URL (or a network command) holds a dictionary value and, if so, asks you before it runs. Cost: an
occasional confirmation prompt.

**B. Linkability over time (ENISA).** A client keeps the same code in every chat, for months. The vendor
cannot read the name, but it can link everything said about `CUS_10234` across conversations (amounts,
cities, events), and that profile is what re-identification attacks use. Done: a new key every 90 days
(item 19). After a rotation a chat that still holds old codes (an old Power BI result) shows them as codes;
the client's name in the chat text gets the new code.

**C. Context still identifies (EDPB, Staab 2024, Lermen 2026)** - accepted. Pseudonymised data is still personal data.
Amounts, dates, cities, a unique role or event pass unmasked, and a strong model can sometimes guess who a
client is from that context. Masking lowers the risk, it does not anonymise: the vendor agreement (no
training, retention) still matters. Options, each with a cost for the analysis: generalise places, round
amounts in free text.

**D. Make degraded masking visible in the chat** - done (item 20).

**E. Name recognition model** - evaluated 2026-10-08 (`tools/masking_eval/ner_models.py`, two synthetic
NL / FR / EN sets: 107 names, plus products, roles, places and ordinary words that must not be found).

| Model | Basic set (74) | Hard set (33) | Time per message | Library |
|-------|----------------|---------------|------------------|---------|
| fastino/gliner2-privacy-filter-PII-multi (GLiNER2-PII) | 73 | 29 | 120-470 ms | `gliner2` (new dependency) |
| **urchade/gliner_multi_pii-v1** (new default) | 72 | 29 | 120-190 ms | `gliner` (as before) |
| E3-JSI/gliner-multi-pii-domains-v1 (old default) | 71 | 25 | 100-155 ms | `gliner` |
| knowledgator/gliner-pii-base-v1.0 | 68 | 25 | 80-100 ms | `gliner` |
| fastino/GLiNER2-Guardrails-PII-Multi (earlier filter) | 68 | 25 | 125-135 ms | `gliner2` |
| nvidia/gliner-PII (earlier filter) | 66 | - | 480 ms | `gliner`, NVIDIA licence |

Every model had 0 false hits after the gateway's filter (raw, without it, 4 to 27 per set: "klant", "family
office", "Power BI"). The biggest gain was in that filter, not the model: with the old filter the old
default found 69 + 23 names, now 71 + 25. Three changes: a lower-case lead is cut off a span ("familie De
Smet" -> "De Smet"); a span of ordinary words is kept when the model is at least 90 % sure
(`client_guard.ner.confident`) and it is a name of two or more words ("Silver Lake Farms", "Carter Family
Trust") or a surname with its particle ("De Wit") - one bare ordinary word never ("Noord" in "while Noord
stayed flat" scored 0.99 as a person, and a recognized name is remembered); a name on its own line (an
e-mail signature) now reaches the model. The worker process now passes the model's score on (it did not,
so a score rule could never have worked). `tools/ner_bench.py` through the real worker (ONNX) with the new
default: 100 of 100 unknown names, 0 false hits on clean text, 80 ms for a short prompt, 0.6 s for a pasted
e-mail. Default switched to `urchade/gliner_multi_pii-v1`: same library, 101 of 107 against 96, Apache-2.0
(the old default is a fine-tune of it). GLiNER2-PII found one name more but needs a new library and was up
to three times slower on a long e-mail; it would also need its own path in the worker, which exports
`gliner` models to ONNX. The sets are small and synthetic: they rank the models, they do not measure
real-world recall.

<!-- tool-reference:start -->
<a id="part-iii"></a>
# Part III — Tool reference (every operation)

> Exhaustive per-tool op reference. **Update this Part in the same commit when a tool/op changes.** Registry truth: `server/handlers/__init__.py:register_all_handlers()`. This Part sits between the
> `tool-reference` markers below: `server/resources.py` (resources `powerbi://docs/tool-reference` and
> `powerbi://docs/tools/{tool}`, `10_Show_User_Guide tool=<name>`) and the drift tests read only that region.

Each section: tool name, category, file, one-line purpose, then full op table. Destructive ops marked. **CATASTROPHIC** = always blocked under LIGHT gate; requires `confirm_destructive=true`.

## III.0 Protocol-level behaviour (every tool)

- **Injected args:** `response_format` (`concise`|`detailed`; concise drops prose keys, clips long strings except DAX text `expression`/`expr`/`dax`, caps lists at 25), `page_size` + `next_token` (opt-in pagination of the result's main list: first of measures/columns/tables/relationships/visuals/rows/items/results/objects/violations/findings, else the largest top-level list; `paginated_key` reports which, `null` when none). `confirm_destructive` is declared only on destructive-capable tools. Since wave 3 (L-10) these carry no per-tool description in `tools/list`; the session instructions explain them once.
- **Argument vocabulary (wave 3, L-11 / A-32):** every schema declares ONE canonical name per concept; `server/arg_aliases.py` folds the legacy spellings in **before input validation** (a legacy name satisfies a `required` canonical one) and the result carries `deprecated_args: {legacy: "renamed to <canonical>"}`. An alias and its canonical name with different values → `error_type: "conflicting_arguments"` (nothing runs). Accepted-but-meaningless legacy args are dropped and listed in `ignored_legacy_args`.

  | Concept | Canonical | Legacy spellings still accepted |
  |---|---|---|
  | DAX query text | `query` | `dax_query` (04_Run_DAX, 04_Query, 09_Debug, 13), `test_query` (04_Query test_rls) |
  | Project locator | `pbip_path` | `pbip_folder_path` (07_PBIP), `report_path` (11) |
  | One page | `page_name` = page id or display name | `page_id` (07_Page, 07_Visual, 11, 17), `page` (09_Debug, 11, 17), `page_path` (11: `.../pages/<id>` -> id + `pbip_path`) |
  | Several pages | `pages` | `page_names` (09_Validate) |
  | Visual | `visual_name` = id or exact title; `visual_id` = exact id | `visual` -> `visual_id` (09_Debug) |
  | Object refs | `table_name` / `measure_name` | `table` (02), `measure` (09_Debug) |
  | Output format | `output_format` | `format` (09_Profile, 16) |
  | Result cache | `bypass_cache` | `no_cache` (04_Run_DAX) |
  | Break-glass | `confirm_destructive` | `confirmed` (every tool) |
  | Other | `object_type` (03) <- `operation`; `display_name` (07_Bookmark) <- `name`; `json_path` (07_Visual) <- `property_path`; `container_display_name` (11) <- `container_name` | |

  Shape transforms: `16_Dependency_Graph` called without `operation` but with `object_type`/`object_name`/`table_name`/`include_report_layer` (the pbi-lineage skill's form) becomes `quick_lineage` on `node_ref` (`Table[Name]`) with `scope` both|model. Ignored legacy args: `04_Run_DAX return_execution_metrics` (trace always returns them), `13 plan_type` (capture records both plans), `08 output_format`, `09_Document output_format`, `07_Bookmark recapture` (offline edit - no live state to re-snapshot).
- **Rare-op arguments (wave 3, L-10):** `server/tool_surface.py DEFERRED_ARGS` lists per tool the arguments that only serve rare operations (refresh policies, calendar generation, translations, perspectives, annotations, daxlib, field-parameter surgery, sync ops, debug_loop, ...). They are left out of `tools/list` but remain in the registered schema - validated, never `unknown_args`, callable exactly as before. `10_Show_User_Guide tool=<name>` lists every argument (rare-op ones marked, long-form `x-doc` text, the tool's `guide_notes`; `02_Model_Operations` also its operations per object_type); `operation=<op>` narrows it to one operation. `tools/list` went from 128.6k to 73.3k chars.
- **Envelope:** every result has `success`; failures carry `error_type` + `error` (+ `suggestion`, `retry_with`, `valid_values` where known). Invalid arguments → `invalid_arguments` (+ `valid_values` for enums). Undeclared args are passed to the handler and listed in `unknown_args`; the notice also carries `did_you_mean_args` {arg: closest declared arg} (a dict, distinct from the list-valued `did_you_mean` of error enrichment / `dmv_probe`) when one is close (`dispatch._did_you_mean_args`; live test 2026-10-03). Engine errors no longer carry the appended .NET stack frames (`adomd_utils.engine_message`, applied centrally in `core/validation/error_response.py`; live test 2026-10-03).
- **Large results:** ≤ 450K chars inline verbatim (no key compaction); larger → full JSON written to `output/` and a summary returned (`_full_output`, `_sizes`, `_preview`; also readable as resource `powerbi://export/latest`). `02_TMDL/05_Column_Usage/06/07_PBIP/16` spill above `max_result_tokens × 4` chars.
- **Timeouts:** `timeout` (read, or not executed: `executed:false`) vs `timeout_outcome_unknown` (a write that may still be running — verify with `verify_with` before retrying; an identical re-issue returns `write_in_progress`). `desktop_unresponsive` = earlier calls are still blocked.
- **Safety modes (config `security`):** `read_only_mode` blocks write/destructive OPERATIONS (not whole tools); `confirm_destructive` requires `confirm_destructive=true` on destructive operations. Classification: `server/op_classification.py`.
- **Data vs metadata, per-model policy rules (2026-10-03 review F-22 / POL-01; `server/security_policy.py`, evaluated in `ToolDispatcher.dispatch` BEFORE `read_only_mode` / `confirm_destructive` / the destructive-op gate; both default permissive, nothing is evaluated while `allow_data_read` is true and `rules` is empty):**
  - **`security.allow_data_read`** (default `true`). `false` keeps every METADATA operation working (names, definitions, expressions, relationships, counts, BPA, graph, PBIR authoring) and refuses the operations that return MODEL ROW VALUES with `error_type: data_read_disabled` (`returns_data: true`). The second classification axis `op_classification.returns_data(tool, args)` names them (audited 2026-10-03, pinned by `tests/test_wp5a_security_policy.py`): every `04_Run_DAX` mode that executes (not `mode=validate`, not `mode=build` without `execute`), `02_Model_Operations` `table sample_data` / `column distribution`, `04_Query_Operations` `impersonate_run` / `simulate_as_user` / `test_rls`, `09_Debug_Operations` `visual` / `compare` / `drill` / `analyze` / `debug_variable` / `step_variables` / `run_dax` / `optimize` / `debug_loop`, `09_Validate` `cross_visual` / `expected_value` / `filter_permutation` / `capture_baseline` / `compare_baseline` / `tieout` / `equivalence` / `test_run` / `test_export` / `test_generate`, `09_Profile` `filter_matrix` / `decompose` / `contribution` / `trend` / `root_cause`, `13_Query_Plan evaluate_and_log`, `05_DAX_Intelligence explain include_values=true`, and `17_Desktop_Bridge` `screenshot` / `screenshot_all` / `reload_and_screenshot` (a screenshot is the data as rendered). **Aggregate-only operations stay available:** `02 column statistics` (DistinctCount / TotalCount / BlankCount, no value), `04_Query_Operations rls_audit` (visible vs unfiltered ROW COUNTS), VertiPaq sizes and cardinalities. `15_DAX_Refactor apply` runs grand-total equivalence checks (an equal / not-equal verdict, values only on a mismatch) and is not marked.
  - **`security.rules`** (default `[]`): a list of `{id?, database?, tool?, operation?, object_type?, object?, action, message?, allow_preview?}`. Every present matcher must match, absent = any; string matchers are case-insensitive globs (`*`, `?`; `[` `]` are literal) or a list of them. `database` matches any name the connected model is known by (database / catalog name, PBIP project name and path, the offline folder, the XMLA workspace/model); when none can be resolved a rule that names a database MATCHES (fail closed). `operation` matches `<op>`, `<object_type>.<op>` and `<object_type>/<op>`: `table.delete` also catches `safe_delete`, `03_Batch_Operations tables/delete` (plural folded to singular) and a changeset that deletes a table. `object` matches any table / column / measure / role ... name the call names (also `Table[Column]` and every batch / changeset item). `action`: `deny` -> `policy_denied`; `require_confirm` -> `policy_confirmation_required` unless the call carries the boolean `confirm_destructive=true`; a missing / unknown action is `deny`. The first matching rule decides; a rule that raises while evaluating counts as a match. A `preview=true` / `dry_run=true` call matches like the real one unless the rule sets `allow_preview: true`. Example: `{"id": "no-prod-table-delete", "database": "*PROD*", "operation": "table.delete", "action": "deny", "message": "..."}`.
- **Uniform write preview + `plan_hash` (F-01):** `preview=true` (alias `dry_run=true`) on every live-TOM write of `02_Model_Operations` and `03_Batch_Operations`, see §III.2 *Preview any write*. A preview is classified READ (allowed in `read_only_mode`, no generation bump, no edit-ledger note) but runs exclusively (process-wide write lock + the offline lane's write scope) because it edits the in-memory model before rolling it back.
- **Resources:** `powerbi://docs/user-guide`, `powerbi://docs/tool-reference`, `powerbi://docs/dax-query-rules` (M4/PRM-01: our own paraphrase of the rules an EVALUATE query must follow; `server/dax_query_rules.py`), `powerbi://docs/tools/{tool}`, `powerbi://docs/visual-catalog/{type}`, `powerbi://docs/design-principles/{topic}`, `powerbi://metadata/bpa-rules`, `powerbi://state/{connection,roots,dispatcher}`. **Prompts:** debug-visual, analyze-model, create-measure, document-report, optimize-slow-measure, live-iterate, safe-rename, create-dax-query (draft, `04_Run_DAX mode=validate`, return only the DAX), run-dax-with-metrics (`mode=trace` + `bypass_cache`, fields shown as returned), analyze-dax-query (same run + metric review), connect-desktop-by-name (`01_Connection detect` then `ensure file_path=`). **Tool titles (M4/TS-01):** every tool in `tools/list` carries a top-level `title` (from `server/registry.py TOOL_TITLES`, also kept in `annotations.title`). The session `instructions` state the lane split: model/DAX/authoring here, rendered canvas + interaction tests in `pbi-webview2`.

**Protocol additions (2026-10-03 review, wave 2):**
- **Errors carry usage.** `missing_argument` / `invalid_argument` / `not_found` / unknown-operation / `operation_failed` results get `usage {tool, operation, required[], optional_top[] (<=10), example_call, more}` derived from the registry, and `did_you_mean` (difflib) for a bad operation, enum value, tool or not-found name. Schema-validation errors also list `missing_arguments`.
- **Idempotent writes.** Any write may pass `request_id` (string, <=128 chars). The dispatcher records the outcome for `server.idempotency_ttl_s` (600 s; 0 disables), also when the worker finishes after the caller timed out. A retry with the same tool + arguments + `request_id` returns the recorded result with `replayed: true`, or `write_in_progress` while the first call still runs; a recorded failure is replayed only while the model generation is unchanged. `timeout_outcome_unknown` results carry `safe_retry`.
- **Write pacing + health (live single writes).** More than `model_write.pace_max_saves` (3) saves inside `pace_window_s` (10 s) sleep `pace_pause_s` (0.5 s) first. After each save the result carries `post_write_health` (`ok` / `errors_introduced` / `unverified`), `post_write_errors [{object, message}]` (objects newly in error since the previous save; baseline reused for `post_write_baseline_ttl_s`), `writes_last_60s` and `paced_ms`. Switch off with `model_write.pace_single_writes` / `post_write_health`. In-loop batch saves keep their own pacing.
- **Serialization.** Results are serialized with orjson (raw UTF-8; NaN/Infinity -> null; stdlib fallback for anything orjson refuses).
- **Resources + completion.** Templates `powerbi://model/measure/{table}/{name}` (DAX, format, dependencies), `powerbi://model/schema`, `powerbi://report/pages`, `powerbi://report/page/{page}/visuals`, `powerbi://capture/{capture_id}` (server/live_resources.py; `{available:false, reason}` when nothing is connected). `completion/complete` suggests table, measure, page, visual, capture and tool values.

## III.0a `00_Client_Mode` — CORE

Files: `server/handlers/client_mode_handler.py`, `client_gateway/` (control client, folders, switch, reaper, server); schema `TOOL_SCHEMAS["client_mode"]`. Control surface of the **client-masking gateway** for Claude Code. **Masking on demand** (owner rule 2026-10-08, `client_gateway/switch.py`): the gateway runs ONLY while a chat has masking on. The user's typed `masking on` (the UserPromptSubmit hook, any chat) starts it, switches the chat on and routes the chat's project folder; `masking off` switches the folder's chats off and unroutes it, and the gateway then exits by itself (`client_gateway/reaper.py`). Nothing else starts it (the MCP does not; only the opt-in desktop-app routing keeps the old MCP-bound lifecycle). With the MCP masking layer on (`client_gateway.mcp_masking` or `FINVISION_MCP_MASKING=1`) this server masks its own results through the gateway, but only while ITS chat has masking on (below).

| Operation | Purpose | Class |
|-----------|---------|-------|
| `status` / `gateway_status` | `routed_session`, `running`, `version`, `session_mode` (this chat, from `CLAUDE_CODE_SESSION_ID`), `dictionary` {total, by_prefix} (counts), `routed_folders`, `every_chat_routed`, `note`. Never a value. | read |
| `on` | The same as the typed `masking on` (`switch.masking_on`): starts the gateway, mode `on` for this chat (`CLAUDE_CODE_SESSION_ID`), routes this chat's project folder (`CLAUDE_PROJECT_DIR`): `{masking, folder, gateway_started}`. Without those variables: `no_session` (the user types `masking on`); a gateway that does not start: `masking_on_failed`, nothing routed. | write |
| `off` | Always `user_action_required`: the user types `masking off` in the chat (a UserPromptSubmit hook switches the mode with the control token, which only the hook and the CLI read). | write |
| `refresh` | Asks the gateway to re-read its dictionary now. | write |
| `gateway_start` | Starts the gateway detached (`<repo>/venv/Scripts/pythonw.exe -m client_gateway start`) and waits up to 8 s for its health answer. | write |
| `gateway_stop` | Stops the gateway; routed chats fail until it runs again. Refused in a routed chat (`user_action_required`). | write |
| `route_folder` / `unroute_folder` | `folder_path`: route / unroute one folder (`.claude/settings.local.json`, kept out of git through `.git/info/exclude` without touching tracked files). Unroute refused in a routed chat. | write |
| `route_all` / `unroute_all` | Route / unroute every chat (`env` of the user settings, `$CLAUDE_CONFIG_DIR` or `~/.claude/settings.json`); `route_all` also starts the gateway. Unroute refused in a routed chat. | write |
| `setup` | Returns the command a HUMAN runs in a terminal (`"<repo>/venv/Scripts/python.exe" -m client_gateway setup`, plus `then.apply` / `register_clients` / `managed_pc` / `status` / `undo`), the `steps` and a `plan` built without reading the system settings (CA, config flags, client configs). It never runs setup and never builds a real backend: a certificate install must be a human action. | read |
| `selftest` | Runs `tools/masking_lab/selftest.py` in a windowless child process (R10; own temp masking home in the child's env, 60 s limit, `selftest_timeout` on expiry; the server's `os.environ` is never patched; offline replay of every recorded wire fixture through the current adapters, R9): `{passed, exit_code, apps[{app, verdict, line, problems}], unrouted, lines}`. One line per named app (`claude-code`, `copilot`, `chatgpt`); a failure names the fixture, never a value. Serialised by a lock (the run points the environment at a temp home for a moment). `selftest_unavailable` when `tools/masking_lab` or `tests/` is not in the install. | read |

**Gateway** (`client_gateway/`, `127.0.0.1:8890`, control endpoints `/__gateway/health|status|mode|session|refresh`):
- **Routing** writes `ANTHROPIC_BASE_URL` plus the settings that close the channels ignoring it (`skipWebFetchPreflight`, `DISABLE_FEEDBACK_COMMAND`, `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY`); earlier values are remembered and restored by the undo. The claude.ai login keeps working (no credential variable is set). Remote Control is unavailable in routed chats (Claude Code rule). A RUNNING Claude Code session re-reads the `env` of its settings files a few seconds after they change (Claude Code env-vars docs; verified 2026-10-08 with a live session), but a REMOVED key stays set until a relaunch: unrouting therefore WRITES the direct address (the value the route replaced, else a remote base URL in the project / user settings, else `https://api.anthropic.com`; a loopback value there is never used) instead of deleting the key. The registry entry of a routed folder records the masked chats (`folders.add_session` / `drop_session`).
- **Reaper** (`reaper.py`): a gateway with nothing routed to it (`folders.anything_routed`: no active folder, no every-chat route) and no request running or seen for `GRACE_S` (20 s) exits (`stopped_unused` in the log). Not with desktop routing on; off with `FINVISION_GATEWAY_KEEP=1` (set by `tests/conftest.py`) or `client_gateway.stop_when_unused=false`.
- **Requests** (`masking.py`): the top-level `system` field: its leading short text blocks (<= `SYSTEM_IDENTITY_MAX` = 400 characters each: Claude Code's billing line and identity sentence) are forwarded byte-identical and never refuse (a subscription login is checked on them: a rewritten identity sentence got HTTP 429 on every request, 2026-10-08; a client value there is counted, `system_values_passed`); the first longer block and everything after it (instructions, project folder, memory path, MCP server instructions) is masked with the developer-text rule (`system_blocks_masked`; live E2E 2026-10-08: 200, the model saw the pseudonym in the memory path); product / vendor names of the profile allow list are never dictionary values. Every other string leaf except thinking blocks, signatures and structural enums is masked (`core/client_guard/engine.py`), byte-identically on every turn (remask memo + cache: prompt caching and preserved thinking keep working); the residual-leak check runs per string. Pseudonym numbers come from a keyed HMAC (`core/client_guard/tokens.py`); the key rotates at a gateway start once older than `client_guard.key_rotation_days` (default 90, 0 = never) or by `python -m client_gateway rotate-key` (refused while a gateway runs); generation g numbers in its own range (0: 10000..99999, g = 1..9: g00000..g99999, a full range overflows to 1000000+), so an older generation's code is unknown, never another client. Name recognition: default model `urchade/gliner_multi_pii-v1` (`core/config/ner_defaults.py`, config `client_guard.ner.model`; compare models with `tools/masking_eval/ner_models.py`); a span of ordinary words is kept from `client_guard.ner.confident` (0.9) on, a lower-case lead is cut (`ner.name_part`). Matching folds Unicode TR39 look-alikes (`core/client_guard/data/confusables_latin.txt`, `tools/masking_lab/gen_confusables.py`). Tool definitions are not whole-cell matched (keywords such as enum values stay). A failure blocks that one request with a 400 in Anthropic's error format and `x-should-retry: false`; nothing is sent.
- **Responses** (`sse.py`): streamed text and tool_use input are rehydrated with a hold-back for tokens split across deltas; thinking, signatures, pings and unknown events pass through unchanged.
- **Dictionary** (`provider.py`, `core/client_guard/detector.py`, policy `client_guard.gateway_policy`, default `dimensions`): the values of the models open in Power BI Desktop, add-only, codes from a keyed hash (`order.key` in the masking home) so they are stable across chats. Generic dimension values and GL names that are everyday words (EN/NL frequency list, `core/client_guard/data/`) or have no letter are matched as a whole cell only and never inside a word; identity values match everywhere, and so do the dimension values of party tables (`ValueBatch.strong`) and a generic value that also arrives as an identity value (the engine strengthens it, token kept; identity batches are added first). Period labels are no dimension values, and period-table words give no identity prefix. Caches key on `SessionMap.revision`. No timer: a cycle (one fingerprint query per model) runs at start, on "masking on" / a masked request that finds an unread model (`verify_instances`), and on any request when the last cycle is older than `poll_s` (`notice()`, background); a failed cycle is retried while a chat is active. Full detection for a new or changed model, or hourly while a chat is active (`ACTIVE_WINDOW_S`), paced; the gateway runs at below-normal priority. No model open and nothing read: ready, nothing to mask.
- **Profiles** (`core/client_guard/profiles.py`): everything organisation-specific is data. `generic` (default): prefixes `CUS`/`VEN`/`PER`/`ORG`/`ACC`/`USR` and `VAL` for any other client value, no naming-convention safe tables (date tables = DataCategory Time, calculation groups, field-parameter and measure tables are skipped as structure), software-only allow list, shapes e-mail / IBAN / phone (country packs off), name recognition off unless `client_guard.ner.enabled`. `finvision`: today's `FAM`/`GLA`/`PCC`/`ENT`/`DIM`/`CLI` prefixes, S01/S02 safe tables, company allow terms, Belgian pack (`be`), NER on. Select with env `FINVISION_MASKING_PROFILE`, else `client_guard.profile`; an unknown name = `generic` + warning `profile_unknown`; `client_guard.shape_packs` adds packs (unknown = `shape_pack_unknown`). Other `warnings()` codes: `ner_unavailable`, `ner_load_failed`, `token_key_ephemeral`. Pinned by `tests/test_client_guard_profiles_golden.py` (recorded at the pre-profile base).
- **Tables** (`rows.py`): text cells of data tables in the new content (data-tool JSON rows: `Table[Column]` keys of DAX results, every key of SQL/grid results; pasted TSV / Markdown tables) become `DIM` (`VAL` under `generic`) values before masking (stat `table_values_learned`); numbers, dates, periods, code and metadata queries are skipped.
- **NER** (`core/client_guard/ner.py`): the NEW text of a masked request gets at most `client_guard.ner.max_s` (8 s; 5 s wait for a loading model, `wait_s`), what finished is used and the request goes on (`masking.recognize_new_text`, counted `ner_partial`): a masked chat is never held or refused by name recognition (the dictionary and the leak checks still mask every known value). MCP results (they carry the PBI marker) are left out. The earlier conversation of a chat masked for the first time is scanned best effort, one history scan at a time, and always yields the worker to new text (`background_priority`). Parts of identity values that are everyday words are `proper` entries: masked only inside a sentence (`_inside_sentence`).
- **Images** (`images.py`): Windows OCR + RapidOCR (`FINVISION_GATEWAY_OCR_ENGINES`), several passes (original, inverted, upscaled, 2x tiles); exact readings are painted with their code, inexact ones with `client`, RapidOCR lines read below `LOW_CONFIDENCE` that could hold a name with `unreadable`; the painted image is read again (`VERIFY_ROUNDS`), and an image that still shows a value is withheld (`client text still readable after redaction`).
- **Hooks** (`hooks/client_masking_hook.py`, standard library + optional psutil, run by path; events `session`, `prompt`, `pretool`): `prompt` answers the typed `masking on|off|status` (and `client mode ...`) in ANY chat through `client_gateway.switch` (imported lazily; a failure blocks the command, it never reaches the model); after `masking on` it waits `SETTLE_S` (4 s) so Claude Code re-reads its settings. In a routed chat `prompt` also restarts a dead gateway, and HOLDS a message of a masked chat whose process has not switched to the gateway yet (`_pending_guard`: its env still has the direct URL; the message would leave unmasked). `session` ensures the gateway runs in a routed folder and tells the model and the user the chat's mode; `pretool` blocks claude.ai connectors and uncovered sources (Fabric data queries, `connect_xmla`) while the mode is on, and always the ACTIONS that would switch the gateway off, stop it, unroute a chat or read its token (mentions in code work pass). Timeouts: `prompt` and `session` 120 s (a first start loads the dictionary: about 25 s for 200k values), `pretool` 30 s.
- **MCP start**: `src/pbixray_server_enhanced.py` starts the gateway only when desktop-app routing is on (`client_gateway.routing`, opt-in); for Claude Code the typed `masking on` alone starts it (never blocks, never raises; skipped under pytest or with `FINVISION_GATEWAY_AUTOSTART=0`).
- **Authority routes** (token required, `X-Gateway-Token`; body limit 64 MiB for these routes only): `POST /__gateway/mask` `{texts, whole}`, `/__gateway/unmask` `{texts}`, `/__gateway/mask_image` `{png_b64}`. The gateway owns the one `SessionMap`, so the MCP and the conversation make the same pseudonyms. `mask` / `mask_image` wait for the dictionary (`client_gateway.authority_wait_s`, default 30) and fail closed: `503 {ok:false, error: not_ready|mask_failed}`, never a partial list; `unmask` never waits (unknown tokens stay). Counters `authority_mask_calls|authority_unmask_calls|authority_failures`. Client side: `client_gateway/control_client.py` (`mask_texts`, `unmask_texts`, `mask_image`; `None` = unreachable or non-200).
- **MCP masking layer** (`server/masking_layer.py`, wired in `server/mcp_app.py`; **off by default**: `client_gateway.mcp_masking` or `FINVISION_MCP_MASKING=1`, read per call; off = identity, no gateway call). With the flag on it masks only while masking is ON for this MCP's own chat (`CLAUDE_CODE_SESSION_ID` asked from the gateway, cached 2 s; without a session id: while a gateway runs); no gateway running = off = results as they are. When it masks, it covers every tool result (string leaves and dict keys in ONE call, images through OCR redaction), `read_resource` contents, forwarded log messages, argument completions (`completion/complete` values) and the `resources/list` / `resources/templates/list` entries (`name`, `title`, `description`, never the uri); unmasks every argument before dispatch; stamps the marker `"_fv": "fvpbi-<install id>"` (first member of a top-level object) on every result. Fail closed while masking is on: a gateway that cannot mask (not ready, an error) = a `masking_unavailable` error envelope instead of data (an empty completion, an empty listing, a dropped log message). Exempt tools (`00_Client_Mode`, `10_Show_User_Guide`, config `client_gateway.mcp_masking_exempt`) carry the marker but are not masked.
- **PBI-chat rule** (`client_gateway.pbi_chats_only`, default **true**; `GatewayState.request_mode`): the gateway masks only a chat that carried this install's marker (`core/client_guard/marker.py`, install id in `<masking home>/install.id`); every other request is forwarded **byte-identical** and its reply is not rehydrated (`passthrough_requests`). The chat stays a PBI chat for good (sticky, persisted in `session_modes.json` next to the per-chat modes, 7 days renewed on use, carried by `start --replace`: compaction can drop the marked tool results). Order: the chat's own `off` -> off (in a PBI chat: the pseudonyms in the history become real values again and the marker is stripped, reply not rehydrated, `pbi_unmasked_requests`); own `on`, PBI chat or global mode `on` -> masked; otherwise passthrough. A request with no chat id is masked when its body carries the marker, a chat has its own `on`, or a PBI chat was active in the last 15 minutes. The marker is stripped from everything that leaves. The unmasked guard does not run under this rule (nothing is blocked because masking is off); `pbi_chats_only=false` restores the old behaviour exactly. `status` adds `pbi_chats_only`, `pbi_chats` and per-chat `pbi` (counts and 8-character id prefixes only), counter `pbi_chat_requests`.
- **Masking lab** (`tools/masking_lab/lab.py start|stop|status --dir <lab dir>`): runs a separate gateway (port 18890, own home seeded with synthetic names, no provider, no autostart) for live checks; refuses port 8890, the real masking home and any path outside the lab dir. `start --frontend-port N` also runs the TLS front end of the gateway on `127.0.0.1:N` (lab CA in the lab home, trusted only by a test window started with `--ignore-certificate-errors-spki-list=<spki>`; `status` prints the SPKI); refused for 8890, the configured production port and the lab port. `lab.py selftest` = the offline replay below.
- **Front end + adapters** (`client_gateway/frontend/`, `client_gateway/adapters/`): the desktop apps' traffic for the routed AI hosts (`claude.ai`, `chatgpt.com`, `openai.com`, `oaiusercontent.com`, `githubcopilot.com`) is decrypted by an in-process mitmproxy on `client_gateway.frontend_port` (8891, loopback only; every other CONNECT is tunnelled undecrypted, upstream TLS verified against the Windows roots + certifi, chained through the user's static proxy). One adapter per wire format (`registry.adapter_for`): `openai_chat` + `openai_responses` (Copilot, split-safe SSE), `chatgpt_web` (logged in and out, `prepare` partial text, uploads); shared helpers in `adapters/common.py`; named-app labels in `adapters/appinfo.py` (`TESTED_APPS`). Claude Desktop has no adapter yet (pending its capture). Typed `masking on|off` is read from the newest user-authored text only (R4).
- **Desktop routing** (`client_gateway/routing/`, config `client_gateway.routing`, default off): a name-constrained local CA (`ca.py`: only the routed domains, IPs excluded, key under DPAPI), the per-user `CurrentUser\Root` store (`certstore.py`), the per-user HKCU Internet Settings with a one-time exact backup (`winproxy.py`), and a PAC served by the gateway (`GET /proxy.pac`, `pac.py`): routed hosts to the front end (`; DIRECT` behind it only while no chat is masked), everything else exactly as the previous decision (direct / static proxy with its bypass list / previous PAC embedded); locked settings and WPAD refuse routing (`status` `routing_inactive:<reason>`). Real system access exists only in the three guarded backends (`certstore.py`, `winproxy.py`, `managed.py` = a read-only Intune / Defender probe): each constructor calls `guard.require_real_system` first (refused under pytest and without `FINVISION_MASKING_REAL_SYSTEM=1`); `backends.real_backends()` is the one place they are built (`setup` and the routed `serve`).
- **Lifecycle** (`lifecycle.py`, R1): the Power BI MCP attaches its pid (`POST /__gateway/attach|detach`, `control_client.attach_pid`); a 2 s watchdog drops dead pids, and with none left the proxy rule is removed (backup restored), the front end stops and the gateway exits; a gateway nothing attached to within 60 s exits too; `cleanup_stale_rule` restores a crash leftover (our PAC URL, nobody answering) at every gateway and MCP start. No service, scheduled task or Run key.
- **R9 update-proofing**: `leakscan.residual_hits` scans the whole outgoing body of a masked request for any known identity value (plain, JSON-escaped, URL-encoded) and blocks a hit as `residual_leak`; a strict shape check per adapter blocks a masked chat whose request format changed (`format_changed`); an unknown reply shape passes with pseudonyms visible and is counted (`rehydration_unrecognised`); app / major-version labels not in `TESTED_APPS` show as `untested_app_version:<space>`; `warnings()` lists every weak state.
- **Self-test** (`tools/masking_lab/selftest.py`, `00_Client_Mode selftest`, `lab.py selftest`): replays every sanitized wire fixture (`tests/fixtures/masking/wire/`) of `claude-code` (a small synthetic request/stream), `copilot` and `chatgpt` through the current adapters: masked out, rehydrated back, leak scan; offline, seconds, also run in the suite (`pytest -m selftest`).
- **Setup** (`client_gateway/setup_cmd.py`, `python -m client_gateway setup`, human-run): the bare command is a PLAN (reads through read-only wrappers; nothing changes), `--yes` applies, `--remove --yes` undoes, `--status` is read-only (adds `claude code hooks: installed / missing`), `--register` adds the MCP to Claude Desktop / VS Code (`servers`) / Claude Code (`~/.claude.json`). **Default `--yes` = Claude Code masking on demand** (no system backends needed): the three masking hooks in the Claude Code user settings (`SessionStart` 120 s, `UserPromptSubmit` 120 s, `PreToolUse` 30 s; idempotent, a lower timeout is raised, other hooks untouched, backup first, each added group recorded), `client_gateway.mcp_masking=true` + `client_gateway.routing=false` in `config/local_config.json` (merge, backup), and an old every-chat route is moved back to direct (`folders.unroute_all`). **`--desktop-apps`** (opt-in, Copilot / ChatGPT) = the routed flow: managed check (`--managed-ok`), proxy check (locked / WPAD refuse), CA create + install, `routing=true`, plus the hooks. Registrations: backup each, never over an existing entry. Everything is recorded in `<masking home>/setup_record.json`; remove undoes exactly that (our CA by recorded thumbprint, proxy backup restored only when the rule in force is ours, config keys false, unchanged registrations) and keeps every backup; it refuses while a gateway runs. `--yes` builds the real backends with `FINVISION_MASKING_REAL_SYSTEM=1` set only while they are constructed. User-facing description: [Part II](#part-ii).

## III.1 `01_Connection` — CORE

File: `server/handlers/connection_handler.py`. Detect running Power BI Desktop instances, connect, or reacquire connection after port change.

| Operation | Purpose | Required args | Destructive |
|-----------|---------|---------------|-------------|
| `detect` | List running PBI Desktop instances, **newest msmdsrv first** (`port`, `pid` = PBIDesktop UI pid, `msmdsrv_pid`, `started_at`, `file_full_path`, `file_path_confidence` = `high`\|`low`\|`unverified` — `low` means the window title names a different file than the process cmdline, e.g. after File→Open) + `amo` (selected/loaded AMO/ADOMD build) | — | No |
| `connect` | Connect to exactly the instance at `model_index` of ONE detection (no second detect) | `model_index` (int, default 0) | No |
| `reconnect` | Re-acquire ADOMD/TOM in place after restart/port change; server round-trip health probe | `model_index?` | No |
| `ensure` | Idempotent: reuse when a server round-trip probe (DBSCHEMA_CATALOGS, never the result cache) succeeds, else pick `prefer_pid` > `file_path` > previous file > `model_index` and connect | `prefer_pid?`, `file_path?`, `model_index?` | No |
| `status` | Connection identity (`port`, `database`, `connection_generation`, `model_generation`, `amo_version`), `command_gate` (busy/holder/active commands), PBIP info, `amo` incl. every candidate folder and why it was (not) suitable; **7.16** `target` (`desktop`\|`xmla`\|`offline`), `xmla_target`, `offline`, `tool_profile` | `probe?` (adds a health round-trip) | No |
| `connect_xmla` | **7.16 (XM-01)** READ-ONLY connection to a published model: `Data Source=powerbi://api.powerbi.com/v1.0/myorg/<workspace>;Initial Catalog="<model>"` (`;` `"` and newlines refused). Token: `MCP_PBI_XMLA_TOKEN`, else the MSAL cache `FABRIC_XMLA_TOKEN_CACHE` / `~/.fabric-mcp/xmla_token_cache.json` (Analysis Services client id; `xmla_auth_required` without one). ADOMD and AMO get their own `AccessToken`; traces run remote. `dispatch._security_gate` refuses every WRITE-classified op of `02_Model_Operations` / `03_Batch_Operations` / `15_DAX_Refactor` with `service_read_only` (`xmla_target.refuse_write`). `connect` / `ensure` leave the lane. | `workspace`, `model` | No |
| `connect_folder` | **7.16 (OFF-01)** Offline model: `pbip_path` (.pbip / .SemanticModel / definition/) → `tom_check.load_database_from_folder` → an in-memory TOM database served by `amo_helpers.get_server_db_model`. `write_lane.commit` then calls `offline_model.persist`: serialise to a temp folder (TmdlSerializer), back up, sync only changed/added/removed `.tmdl` files, re-deserialize with TOM; a failed re-read restores the backup and raises. Refuses `project_open_in_desktop` (override `allow_open_in_desktop=true`); a model with UDFs opens read-only. No engine: DAX / traces / refresh return `not_connected`. Round trip proven byte-identical on S02 (103/103 files). | `pbip_path`, `allow_open_in_desktop?` | No |

**Connection semantics (2026-09-22 review, WP1):**
- All ADOMD commands on the shared connection are serialised by a per-connection **command gate**. A caller that cannot get the gate within ~30 s receives `error_type: "busy"` + `retry_after` (and `busy_with`) instead of corrupting the XMLA stream.
- Transport/stream failures (`Root element is missing`, `BufferedStream`, forcibly closed, connection not open) → the connection is rebuilt and READ queries are retried once (`connection_recovered: true`). Any tool call finding the connection Broken/Closed auto-reconnects once (throttled ~2 s) instead of returning `not_connected` until a manual `ensure`.
- Every connection swap (connect / reconnect / auto-recovery) rebuilds all managers bound to the old connection and clears the result cache, table/column ID maps and INFO.VIEW capability flag; switching to a *different* instance also clears PBIP info and cached traces.
- AMO/ADOMD build: the newest suitable **.NET Framework** build among `$MCP_PBI_AMO_DIR` (override, always wins), bundled `lib/dotnet`, DAX Studio, Tabular Editor 2, SSMS 18–21 is loaded for the whole process (one folder for every loader, traces included). `.NETCoreApp` builds (Tabular Editor 3) are rejected. **Needs live check:** confirm a trace on the selected build delivers events (`04_Run_DAX mode=trace` → `events_received > 0`); otherwise pin DAX Studio via `MCP_PBI_AMO_DIR`.

**Offline / XMLA lanes (2026-10-03 review, wave 1):**
- XMLA (`connect_xmla`) is read-only by an ALLOW-list in the dispatcher: on a service target every write-classified call is refused (`service_read_only`) unless the tool only writes local files (07_*, 11_*, 17_*, 00_*, 02_QA_Linguistic, 09_Validate suite files). `02_TMDL_Operations format_dax` and `09_Debug_Operations debug_loop` live-TOM fixes are refused explicitly, and `write_lane.commit` itself raises `ServiceReadOnly`, so a new writer cannot slip through. `<ClearCache>` is never sent to a service target (traces report `cache not cleared (service target)`); `13_Query_Plan trace_start` on XMLA returns `trace_unsupported_on_service`.
- `connect_folder` leaves an active XMLA target. Offline `persist` stages every file as `*.mcp_new`, swaps them in with `os.replace`, deletes last, and restores from the backup on any failure (leftovers reported). Only files whose content changed are rewritten, and each keeps its own line endings.
- Every `*.tmdl` file is fingerprinted at connect and after each persist: a folder edited outside the server since then → `offline_folder_changed` (nothing written; the folder is re-read so a retry works).
- A failed offline write rolls back by re-reading the folder (`write_lane.safe_undo`; `UndoLocalChanges` throws on a disconnected model). Reads and writes on the offline model are serialised by a reader/writer lock; a write that cannot take it within 300 s returns `busy` (nothing written).
- `allow_open_in_desktop=true` (writing a folder Desktop has open) also needs `confirm_destructive=true`; Desktop detection that errors fails closed.
- `model set_properties compatibility_level` is checked against the loaded TOM build's supported levels (`compatibility_level_unknown`), on both lanes.

## III.2 `02_Model_Operations` — MODEL

File: `server/handlers/model_operations_handler.py`. **Live TOM CRUD dispatcher.** Every call requires `object_type` + `operation`. PBI Desktop must be **open** — these are live model writes that propagate immediately (no PBIP reload needed).

### Object types and their operations

| `object_type` | Operations |
|---------------|-----------|
| `table` | `list`, `describe`, `sample_data`, `create`, `update`, `delete`, `safe_delete`, `rename`, `safe_rename`, `refresh`, `generate_calendar` (F-20e: `calendar_template` = `standard` \| `iso_week` \| `445` \| `454` \| `544`), `get_refresh_policy` (read-only), `set_refresh_policy`, `clear_refresh_policy`, `mark_as_date_table` (M4: `table_name` + `date_column`; validated first, see *Parity pack* below) |
| `column` | `list`, `get`, `statistics`, `distribution`, `create`, `update`, `delete`, `safe_delete`, `rename`, `safe_rename` |
| `measure` | `list`, `get`, `create`, `update`, `delete`, `safe_delete`, `rename`, `safe_rename`, `move`, `set_format_string_expression`, `set_detail_rows_expression`, `set_kpi`, `clear_kpi`, `get_kpi` |
| `relationship` | `list`, `get`, `find`, `create`, `update`, `delete`, `activate`, `deactivate`, `rename` (M4: `relationship_name` + `new_name`; no cascade - DAX never names a relationship) |
| `calculation_group` | `list`, `create`, `delete`, `list_items`, `add_item`, `update_item`, `delete_item`, `set_precedence`, `update` (M4: `description`, `precedence` (refused when another group has it), `hidden`; F-20c: `multiple_or_empty_selection_expression` / `no_selection_expression` (`''` clears; CL 1605+), `selection_expression_behavior` = `automatic`\|`visual`\|`nonvisual` (MODEL-wide `Model.SelectionExpressionBehavior`, CL 1609+); below the level = `compatibility_level_too_low` + `required_compatibility_level`, nothing written), `rename` (M4: `group_name` + `new_name` = a TABLE rename: full cascade, `preview`/`preview_hash`/`pbip_path`, CATASTROPHIC), `detect`, `scaffold`, `list_patterns` |
| `partition` | `list`, `describe`, `create`, `update`, `delete`, `set_mode`, `refresh` |
| `hierarchy` | `list`, `describe`, `create`, `update` (`new_name`, `description`, `display_folder`, `hidden`, `levels` = new order of the existing levels; one save; a rename returns `pbir_followup`), `delete`, `add_level` (M4: `level_name?`, `column_name`, `ordinal?` 0-based), `remove_level` (`level_name`; never the last level), `rename_level` (`level_name`, `new_name`; `pbir_followup`) |
| `perspective` | `list`, `describe`, `create`, `delete`, `add_table`, `add_measure`, `add_column`, `add_hierarchy`, `remove_table`, `remove_measure`, `remove_column`, `remove_hierarchy`, `set_members` (bulk: `members=[{table_name, measure_name\|column_name\|hierarchy_name}]`, `member_action` add\|remove; validated first, ONE save) |
| `culture` | `list`, `describe`, `create`, `delete`, `set_translation`, `bulk_set_translations`, `export_translations` (F-20d: `culture_name?` = all, `file_format` json\|csv, `output_path?` else rows inline capped by `limit`), `import_translations` (`culture_name`, `translations` rows OR `input_path`; empty values never blank; `dry_run`; one journaled save), `translation_coverage` (`culture_name?`, `limit?`) |
| `named_expression` | `list`, `describe`, `create`, `update`, `delete` |
| `ols_rule` | `list`, `set` (omit `column_name` for TABLE-level OLS), `remove` |
| `model` | `refresh` (param `refresh_type`: full\|automatic\|dataOnly\|calculate\|clearValues\|defragment\|indexes (CL 1707+)), `refresh_status`, `refresh_history`, `get_properties`, `set_properties` (M4), `set_value_filter_behavior`, and the durable write journal (JR-01): `history`, `diff`, `undo`, `replay`, `checkpoint_create`, `checkpoint_restore` (see *Durable write journal* below) |
| `any` (F-04) | `list_properties` (`ref`: every scalar property with `type`, `value`, `settable`, enum `values`, `min_compatibility_level`, `reason` when not settable; `annotations` / `extended_properties`), `get_property` (`ref`, `property_name`), `set_property` (`ref`, `property_name`, `value`, `dry_run?`). `ref` = `Table[Column\|Measure]`, `table:Name`, `measure:T[M]`, `column:T[C]`, `hierarchy:T[H]`, `partition:T[P]`, `relationship:Name`, `role:Name`, `perspective:Name`, `culture:Name`, `expression:Name`, `calculation_group:Name`, `model`; or `table_name` + `column_name`\|`measure_name`. See *TOM property reflection* below |
| `role` | `list`, `create`, `update`, `delete`, `set_table_filter`, `clear_table_filter`, `add_member`, `remove_member`, `list_members`, `get_effective_permissions` (M4, read-only: per role the model permission, per-table RLS filter + table/column OLS, unrestricted tables) |
| `annotation` | `list`, `get`, `set`, `delete` (param `target_type`: model\|table\|column\|measure\|partition\|relationship\|role\|named_expression\|hierarchy\|calculation_item (`group_name`+`item_name`)\|perspective\|culture; UDFs are not reachable through AMO; `PBI_*` annotations need `force=true` to delete) |
| `field_parameter` | `create`, `repair` (alias `set_parameter_metadata`), `add_field`, `remove_field`, `reorder_fields` |
| `function` | `list`, `get`, `create`, `update`, `delete`, `daxlib_search`, `daxlib_install` — DAX user-defined function (UDF) CRUD + DaxLib registry search/install |
| `query_group` | `list`, `create`, `update`, `delete`, `assign`, `unassign` — Power Query folders (`Model.QueryGroups`), see *Parity pack* below |
| `calendar` (7.16, CAL-01) | `list` (`table_name?`), `get`, `create`, `update`, `delete` — calendar metadata on a date table (`calendar_name`, `table_name`, `groups`, `time_related_columns`), see *Calendars* below |

### Preview any write (F-01, 2026-10-03; `core/operations/write_preview.py`)

`preview=true` (alias `dry_run=true`) on any write op (create / update / delete / safe_* / rename / set_* / refresh / calendar / field_parameter / ...) answers WITHOUT saving. The REAL manager runs against the real model inside `write_lane.preview_scope`: `write_lane.commit` is suppressed (`SaveChanges` is never called, on the live lane and on the offline TMDL lane; the journal, pacing and health probe do not run), the "after" TMSL image is captured instead, the committed model supplies the "before" image, `journal_images.diff_maps` reports added / removed / renamed / modified per object, and the in-memory edits are rolled back with `write_lane.safe_undo` (live: `UndoLocalChanges`; offline: the folder is re-read). Result:

| key | meaning |
|---|---|
| `success`, `would_succeed`, `preview: true`, `applied: false`, `committed: false`, `rolled_back` | a manager refusal (name conflict, not found, ...) is `success: false` with the manager's `error_type`, `errors[]` and no `plan_hash` |
| `would_change[]` | per touched object `{ref, kind, change, renamed_from?, before, after, properties{pre,post,diff}}` (TMSL, volatile timestamps stripped; first 100, `would_change_truncated` = total), `would_change_counts` |
| `errors[]` | what the engine / manager would refuse (`{error_type, error}`) |
| `breaks[]`, `breaks_status` | for removed tables / columns / measures: `{object, breaks[], unknown_nodes[]}` from the dependency graph (read-only, `core.autonomous.evidence`); `breaks_status` = `checked` \| `checked_not_proven` \| `nothing_removed` \| `unavailable` (no graph) \| `unavailable_offline` (offline lane has no live graph) - never silently empty |
| `validation` | `dax_expressions: not_validated`: a preview cannot evaluate DAX, the engine binds expressions at save |
| `plan_hash`, `model_generation`, `apply_with` | hash of (tool, effective arguments, model generation) |
| `result` | the manager's own result minus the post-write bookkeeping |

**Apply:** repeat the call WITHOUT `preview` and pass `plan_hash=<hash>`. It is refused with `error_type: stale_preview` (`executed: false`, `current_plan_hash`) when the model generation moved (any write, a swap, a Desktop-side edit) or the arguments differ; otherwise it runs normally (the destructive gate still applies; `plan_hash_verified: true`). Existing dry-runs keep their vocabulary: `rename` -> cascade diff + `preview_hash` (`dry_run` is an alias), `model undo` / `replay` / `checkpoint_restore`, `any set_property` and `culture import_translations` validation. `safe_delete` / `safe_rename` preview as the raw verb (the graph's blast radius is `breaks[]`). **Refresh** (`model` / `table` / `partition` `refresh`) previews as a plan: `would_refresh {refresh_type, scope, tables[{table, partitions[]}], table_count}`, `table_not_found` for unknown names; nothing is requested. Read ops ignore `preview`. The preview never reaches the destructive gate. `03_Batch_Operations`: top-level `preview` / `dry_run` on the classic `object_type` + `batch_operation` + `items` path does the same (the per-type item cap still applies; `options.dry_run` stays the validate-fields-only switch; `changeset` and `patch` keep their own `preview` + `plan_hash`; `ti_suite` + `apply=true` + `preview=true` previews the create).

### Target resolution (2026-09-22 review C-19)

For table/measure/column ops the target is resolved ONCE and both the destructive gate's evidence and the executor read the same canonical args: `table_name` (aliases `table`, `name` for tables, `source_table` for measure/move), `measure_name` (aliases `measure`, `name`), `column_name` (aliases `column`, `name`). Two aliases with **different** values → `error_type: "ambiguous_target"` (nothing runs).

### Destructive gate (review C-02 / C-03 / C-13 / C-20 / K-03)

- **Read-only ops are never gated** — including `calculation_group/detect|scaffold|list_patterns`, `function/daxlib_search` and any `rename` with `preview=true`. `(object_type, operation)` is validated **before** the gate, so a typo returns `invalid_operation` even under STRICT.
- **CATASTROPHIC** (blocked under LIGHT without `confirm_destructive=true`, or a proof): `table/delete`, `calculation_group/delete`, `culture/delete`, `named_expression/delete`, `function/daxlib_install`, `table/rename`, any rename with `pbip_path`, a `measure`/`column` rename whose dry-run cascade touches ≥1 expression (0 expressions → DESTRUCTIVE; a failed dry-run → CATASTROPHIC), `model/refresh` without `tables` or with ≥ `model_write.catastrophic_refresh_tables` (default 10) tables. `model/refresh refresh_type=calculate|indexes` is DESTRUCTIVE. M4: `model/set_properties` with `compatibility_level` and `calculation_group/rename` are CATASTROPHIC (`calculation_group/rename preview=true` is a read).
- **Evidence** is computed in-process only (`core/autonomous/evidence.py`); a caller-supplied `evidence` argument is **ignored** (`deprecated_args` notice). It counts as proof only when every `get_impact` result carries `report_side_available: true`, `unknown_nodes: []`, `graph_scope: "both"` and the current `model_etag` (not `stale`), and the graph (built via `core.graph.service.get_or_build_graph(scope="both", all_bound_reports=True)`) has `model_side_complete: true` and no unscanned `other_bound_reports`; a missing field = not proven. Blocks carry `blast_radius` + `evidence_unproven_reasons`.
- **`safe_delete` / `safe_rename`** (table/measure/column): FAIL-CLOSED composites. They refuse with `unsafe_delete` / `rename_collision` (blast radius), `evidence_unavailable` (no graph / no PBIP path) or `evidence_unproven` (reasons); they still run the policy gate (STRICT blocks without `confirm_destructive`) and merge gate info. `safe_rename` forwards the PBIP path the proof was computed against (refuses with `rebind_path_required` when visuals bind the object and no path is known).
- `confirm_destructive` must be the boolean `true` (`"false"` never confirms); the legacy `confirmed` is an alias.

### Parity pack (M4: MP-01, QG-01, RF-01, Sep-2026 model support)

New args (all deferred = not in `tools/list`, listed by `10_Show_User_Guide tool=02_Model_Operations`): `culture`, `discourage_implicit_measures`, `compatibility_level`, `allow_compatibility_upgrade`, `level_name`, `query_group`, `string_indexing_behavior`, `full_text_indexing_behavior`; `date_column` and `limit`/`since` are reused.

- **`model/set_properties`** (`core/operations/model_properties_manager.py`): `description`, `culture` (culture-name shape checked), `discourage_implicit_measures`, `compatibility_level`. One `commit`. `compatibility_level` is an IRREVERSIBLE upgrade: needs `allow_compatibility_upgrade=true` (else `compatibility_upgrade_not_allowed`) AND `confirm_destructive=true` (CATASTROPHIC), a lower value is `compatibility_downgrade_refused`, an equal value is a no-op, the result carries a `warning`. No other operation ever raises the level. Needs a live check: the CL write via `Database.CompatibilityLevel` + `SaveChanges` against Desktop.
- **`table/mark_as_date_table`** (`table_crud_manager.mark_as_date_table`): column must exist and be `DateTime`; no other column may already be `IsKey`; ONE DAX query (`check_date_column`) proves rows == distinct values, no blanks, no time-of-day, distinct days == max-min+1 (unique + contiguous). Failure = `invalid_date_column` with `problems[]` and `stats`, nothing written. Success sets `Table.DataCategory='Time'` + `Column.IsKey=true`. An uncalculated/unloaded table fails validation with `date_validation_failed` (refresh first).
- **`hierarchy/add_level|remove_level|rename_level`** (`hierarchy_crud_manager.py`): one `commit` each; ordinals kept 0..n-1; duplicate level name = `name_conflict`; removing the last level = `last_level`.
- **`role/get_effective_permissions`** (`rls_role_crud_manager.py`): read-only static summary; `role_name?` (default all roles), `table_name?` narrows `tables[]`. It does not evaluate member overlap - use `04_Query_Operations test_rls` for actual rows.
- **`query_group/*`** (`core/operations/query_group_crud_manager.py`): a group is named by its folder path (`query_group`, e.g. `Sources\Staging`; TOM derives `QueryGroup.Name` from `Folder`). `create` (`description?`), `update` (`description?`, `new_name?` = folder rename), `delete` (`force=true` unassigns members first, else `query_group_in_use`), `assign`/`unassign` take exactly one target: `expression_name` OR `table_name` [+ `partition_name`, required when the table has several partitions]. `feature_unsupported_by_client` when the loaded TOM lacks `Model.QueryGroups`. Needs a live check: a folder rename with members assigned.
- **`calculation_group/update|rename`** and **`relationship/rename`**: see the table. `calculation_group/rename` delegates to `table_crud.rename_table`; `relationship/rename` with `preview=true` returns a no-op description and never writes.
- **`model/refresh_status`** (`table_name?`) reads every partition's `State`, `Mode`, `RefreshedTime`, `ModifiedTime` from TOM + a per-table roll-up and `not_ready_partitions`. **`model/refresh_history`** (`since?`, `limit?` default 20) lists the refreshes this server requested (write-journal entries whose operation is `model|table|partition/refresh`) with scope and `refresh_type`. Both carry `limits`: Desktop has NO refresh log (no duration, row counts, error text, no UI-started refreshes); Service history needs the REST/XMLA lane.
- **`model/refresh refresh_type=indexes`** (also `table/refresh`): builds explicit string / full-text indexes; refused below compatibility level 1707 (`compatibility_level_too_low`, `suggestion` names the deliberate `set_properties` upgrade). Needs a live check on a 1707 model.
- **`column/update string_indexing_behavior|full_text_indexing_behavior`** (`core/operations/column_indexing.py`): values auto\|full\|explicit\|off (string, CL 1707+) and full\|explicit\|off (full-text, CL 1708+, no `auto`); String columns only (`invalid_column_type`); refused with `compatibility_level_too_low` (never auto-upgrades) or `feature_unsupported_by_client` (needs `Microsoft.AnalysisServices.Tabular` 19.117+; the bundled build has both properties and `IndexingBehavior`). `column/get` returns `string_indexing_behavior` / `full_text_indexing_behavior` when the client exposes them. Not wired into `03_Batch_Operations`.
- **BPA (06 `full`, `config/bpa_rules_comprehensive.json`)**: `TEXTCONTAINS_REQUIRES_FULLTEXT_INDEX` (error), `APPROXIMATEDISTINCTCOUNT_IN_MEASURE` (warning), `STRING_INDEX_ON_LOW_VALUE_COLUMN` (info; low-cardinality branch needs `include_vertipaq=true`). `core/analysis/bpa_model.py` exposes `StringIndexingBehavior` / `FullTextIndexingBehavior` on columns.

### Write path (review C-10 / C-22 / C-27 / C-43)

Every SaveChanges goes through `core/operations/write_lane.py`: serialised, journaled (JR-01, see *Durable write journal*), followed by a model-generation bump (DAX cache, id maps). When SaveChanges raises but a fresh read-back proves the change landed (old-AMO response-parse errors), the op returns **success with `post_write_warning` + `client_error`** — do not retry. Batch/changeset results carry `post_write_health` when Desktop failed the post-write probe.

### Durable write journal (JR-01, `object_type='model'`)

Live TOM edits exist only in Desktop's memory until Desktop saves, so a reload (including `17_Desktop_Bridge reload include_model=true`, which discards unsaved TOM edits) or a crash loses them, and `edit_ledger` only refuses the reload. The journal (`core/operations/write_journal.py`) makes that loss recoverable.

- **Recording.** `write_lane.commit` - the one choke point of every live-TOM `SaveChanges` (02, 03, 15, the DAX injector, UDF TOM writes, undo/replay themselves) - serialises the mutated model (POST image), reads the committed model (PRE image: the previous commit's image when Desktop's `ModifiedTime` proves nothing changed, else a fresh server read - the pending save is not in it yet), diffs them (`core/operations/journal_images.py`) and appends ONE JSON line to `output/journal/<project>.jsonl` (append + fsync; git-ignored via `output/`). The file is keyed by the connected PBIP path (the database name when unknown) so it survives a Desktop restart. Line fields: `id` (`J000012`), `seq`, `ts` (UTC ISO), `session`, `tool`, `operation`, `args` (sanitised), `refs`, `changes[]` (`ref`, `kind`, `change` = added\|removed\|modified\|renamed, full TMSL `pre` and `post` image of the object), `result`, `model_generation`, `flushed:false`, `meta` (undo/replay/restore bookkeeping), `degraded` (pre-image could not be read: listed but not undoable), `noop` (saved but no metadata changed, e.g. a data refresh). Refs: `measure:Sales[Total]`, `column:Sales[Amount]`, `table:Sales`, `partition:`, `hierarchy:`, `calcitem:Group[Item]`, `relationship:<name>`, `role:`, `perspective:`, `culture:`, `expression:`, `datasource:`, `function:`, `model` (scalar model properties). A rename is one `renamed` change (matched by `lineageTag`), so undoing a column rename renames it back instead of dropping the data.
- **Never fails your write.** A journal failure (disk full, unreadable pre-image) is logged and surfaces as `journal_warning` on the write's result. Args are sanitised (secret-looking keys, `Password=`-style fragments and bearer tokens redacted; text capped at 2000 chars); images never hold credentials (TOM `SerializeOptions` omits restricted information). Switch off with `model_write.journal_enabled=false`; folder override `model_write.journal_dir` or env `MCP_PBI_JOURNAL_DIR`.
- **Flushing.** A disk-verified `17_Desktop_Bridge save` (or a manual Ctrl+S the bridge detects) appends a `flush` record (`up_to_seq`); entries at or below it are `flushed`. A discarded reload or `open` does NOT flush - those entries are what `replay` restores. `17_Desktop_Bridge status` `dirty_sources` carries `journal_unflushed: N` (+ ids), and a refused reload names it and points at `replay`.
- **`history`** - `since` (ISO or `30m`/`2h`/`1d`), `object` (substring of a ref), `unflushed_only`, `limit` (50), `journal` (name; default = the connected project's, else the newest file); newest first; returns `journal_unflushed`, `checkpoints[]`, `other_journals[]`. Works without a live connection.
- **`diff`** - `entry_id`: property-level pre vs post per change (long expressions get a unified diff).
- **`undo`** - `count` (last N undoable entries, default 1; entries made by undo/replay/restore are skipped) or `entry_id`. Re-applies pre-images in reverse order in ONE SaveChanges (in-place property writes for data-bearing kinds, replace for roles/perspectives/cultures/hierarchies/data sources/UDFs). It first checks that each touched object still looks like the entry's post-image (server-added keys are ignored): otherwise `journal_conflict` and nothing changes; `force=true` overrides and, like any plan that removes a table/culture/named expression, needs `confirm_destructive=true`. `preview=true` is a dry run (`changes`, `conflicts`, `skipped`, `needs_refresh`, `can_apply`, `requires_confirm_destructive`). Classified DESTRUCTIVE (a preview is a read). Restoring a deleted table/column/partition re-creates it unloaded (`needs_refresh`).
- **`replay`** - re-applies the post-images of unflushed entries oldest first (optionally `entry_ids`, `since`) after a discarded reload or crash. Entries already in the model are skipped (`already_applied`); a change whose object is neither the entry's pre- nor post-image is a `conflict` (reported, not applied; `force=true` overrides). One SaveChanges. WRITE (a preview is a read).
- **`checkpoint_create`** (`checkpoint=<name>`, `force` to overwrite) - snapshots the whole model (TMSL + the UDFs read from the DMV) to `output/journal/checkpoints/<name>.json`. **`checkpoint_restore`** - diffs the checkpoint against the live model and applies changed/added/removed objects in ONE SaveChanges; DESTRUCTIVE (preview first). UDFs are only touched when the checkpoint captured them.
- UDF changes are applied through the UDF lane (`function/*`, separate write, journaled there); UDF TMSL writes (`server.Execute`) are journaled with the DMV row as pre-image.
- Needs a live check: exactness of `undo`/`replay`/`checkpoint_restore` is proven offline against the real TOM object model, but the SaveChanges round trip against Desktop, `ModifiedTime` behaviour after a save (cache hit vs re-read) and removing a child object (KPI, annotation) from a measure that carries a `FormatStringDefinition` (the offline serializer rejects it) were not exercised.

### Notable details

- **Rename cascade (C-01/C-04/C-06):** lexer-driven (`core.dax.dax_rename`): bare table refs (`COUNTROWS(T)`) are cascaded, the new table name is always emitted quoted (`'O''Brien Sales'`), `]` in names is escaped, `MySales[Amount]` never matches `Sales[Amount]`, string literals / comments are untouched, **Power Query M partitions are never touched** (only calculated partitions), `NAMEOF([m])` is qualified as `NAMEOF('Home'[m])`. Also rewrites calc items (+ format strings), calc-group selection expressions, RLS filters, KPIs, detail rows, format-string definitions and JSON linguistic metadata; UDF bodies (not reachable via AMO) and XML linguistic metadata are reported under `untouched`.
- **Verify + rollback (C-40):** after SaveChanges the rename re-reads object errors (`TMSCHEMA_*` `ErrorMessage` via the gated executor); any NEW error rolls the rename + cascade back exactly and returns `error_type: "rename_introduced_errors"` with `new_errors`. Results carry `verification: passed|unavailable`.
- **Rename preview (C-41):** `rename … preview=true` returns `expression_changes` (path/kind/before/after), `untouched`, `change_count` and a `preview_hash` without writing (with `pbip_path`, a PBIR dry-run `pbir_preview` for measures). Passing `preview_hash` back on the real call refuses (`stale_preview`) when the diff changed.
- **Cascade rename to PBIR:** `table/rename`, `column/rename`, `measure/rename` accept optional `pbip_path`; after the live rename `apply_pbir_cascade()` rewrites PBIR JSON references. Result includes `pbir_cascade` and `reload_required=true`.
- **`measure/create` is a real create (C-12):** a name that exists anywhere in the model → `error_type: "already_exists"`; `if_exists='update'` / `operation='upsert'` retries as update. `measure/update` never creates (`measure_not_found`). Both are verified by read-back.
- **`measure/move` (C-31):** keeps LineageTag, extended properties, annotations, KPI, detail rows, format-string definition, culture translations and perspective membership (`preserved`); returns `pbir_followup` (report visuals still name the old home table — rebind with `07_Report_Operations`).
- **`table/create`:** `expression` (DAX, calculated table) or `m_expression` (Power Query, import table) is required — empty-M tables are refused (C-24). `table/update expression=` only applies to calculated partitions.
- **Enums are strict + case-insensitive (C-25):** `data_type` (String/Int64/Double/Decimal/Boolean/DateTime/Binary/Variant), relationship cardinality / cross-filter, role permission — an unknown value is `invalid_parameters`, never a silent default. A calculated column without `data_type` keeps the inferred type.
- **Relationships (C-42):** create refuses an exact duplicate (`duplicate_relationship`) and a second ACTIVE path between the same two tables (`ambiguous_path`, override `allow_ambiguous=true`); `update` also takes `from_cardinality`, `to_cardinality`, `security_filtering_behavior` (OneDirection\|BothDirections\|None), `rely_on_referential_integrity`, `join_on_date_behavior` (DatePartOnly\|DateAndTime); `activate` runs the ambiguity check.
- **`measure/set_format_string_expression`:** sets/clears `Measure.FormatStringDefinition.Expression` (CL 1606+). `expression=None`/empty clears.
- **`measure/set_detail_rows_expression`:** sets/clears `Measure.DetailRowsDefinition.Expression` (CL 1400+).
- **`measure/set_kpi` / `clear_kpi` / `get_kpi`:** `set_kpi` requires one of `target_expression`, `status_expression`, `trend_expression`; default graphics are filled in.
- **`culture/bulk_set_translations`:** many translations under a single `SaveChanges()`.
- **TOM property reflection (F-04, `core/operations/tom_properties.py`):** reads are open (CLR reflection over the resolved object; DateTime values are returned ISO-8601, never in the process culture's format - live test 2026-10-03); writes are an ALLOW-LIST per kind (`ALLOWED`: e.g. column `SummarizeBy`, `IsAvailableInMDX`, `SortByColumn` (a reference by name; `''` clears), `DataCategory`, `DisplayFolder`, `FormatString`, `Alignment`; table `DataCategory`, `IsHidden`, `Description`; measure `FormatString`, `DisplayFolder`, `Description`, `IsHidden`; relationship `RelyOnReferentialIntegrity`, `JoinOnDateBehavior`, `CrossFilteringBehavior`, `SecurityFilteringBehavior`; model `Description`, `DiscourageImplicitMeasures`, `DiscourageReportMeasures`, `ValueFilterBehavior`, `SelectionExpressionBehavior`, `DefaultMeasure`); any other property = `denied_property` + reason (`Name` -> rename op, `Expression` -> update op, lineage tags, structural, runtime state). Pseudo-properties `Annotations[name]` / `ExtendedProperties[name]` (string) on every object that has them. Values are coerced to the CLR type (bool strings, ints, enum names case-insensitive with `valid_values` on a miss). Compatibility: `MIN_CL` pre-checks the measured minimum (`ValueFilterBehavior` 1606, `SelectionExpressionBehavior` 1609) and TOM's own `CompatibilityViolationException` (raised on assignment) is reported as `compatibility_level_too_low` + `required_compatibility_level`. One `write_lane.commit` per call (journaled, undoable); `dry_run` (or `preview`) reports `previous` -> `value` and classifies as a read. Gate: DESTRUCTIVE, CATASTROPHIC for `HIGH_IMPACT` (model `ValueFilterBehavior` / `SelectionExpressionBehavior`, relationship `CrossFilteringBehavior` / `SecurityFilteringBehavior` / `JoinOnDateBehavior`). Tests: `tests/test_fm_tom_properties.py` (includes the real in-memory TOM when loadable).
- **Field parameters (C-07/C-08/C-09):** `create` builds the WHOLE table in ONE SaveChanges with explicit `CalculatedTableColumn`s (`[Value1..3]` → `<t>`, `<t> Fields`, `<t> Order`, hidden flags, sort-by, SummarizeBy, `RelatedColumnDetails.GroupByColumns`, ParameterMetadata, lineage tags, annotations) + a Calculate refresh; a failure is rolled back on a fresh connection. `expression` may also be a data-driven table expression. `repair` (= `set_parameter_metadata`) applies that complete shape idempotently to an existing / half-built FP (columns found by `SourceColumn`, existing `PBI_Id` kept); returns `repaired: [...]` or `action: noop`. `add_field` / `remove_field` / `reorder_fields` use a real tuple tokenizer and refuse (`fp_parse_incomplete`) when any row cannot be parsed.
- **Calculation groups (C-16/C-30):** `list` reads the TMSCHEMA DMVs (precedence, item expressions, ordinals, format strings); `list_items` returns the items. `create` builds `Name`/`Ordinal` columns bound to the engine source columns and sets `discourageImplicitMeasures`. `scaffold` emits TMDL that deserializes with TOM (quoted names, `calculationItem X =`, columns + `calculationGroup` partition; `date_column` optional) and a single-call `create_args`.
- **UDFs (C-18 / FG-11):** reads via the DMV (`TMSCHEMA_FUNCTIONS` / `INFO.USERDEFINEDFUNCTIONS`, AMO `Model.Functions` is empty on Desktop); writes are TMSL `createOrReplace` / `delete` executed server-side, verified by a DMV read-back (`verified`, `write_not_visible`). `parameters` build a `(p: TYPE, …) =>` header only when the body has no `=>`. `daxlib_install` validates every body first and installs in one TMSL `sequence`.
- **`model/refresh`:** `tables` may be a string or a list; all listed tables missing → `table_not_found` (no save).
- Calc-group `set_precedence` updates `CalculationGroup.Precedence` post-create.
- **Calendars (7.16, CAL-01)** `object_type=calendar`: `list` (`table_name?`), `get`, `create`, `update`, `delete` (`calendar_name`, `table_name`, `groups=[{time_unit, primary_column, associated_columns?}]`, `time_related_columns?`, `description?`, `new_name?`). File `core/operations/calendar_crud_manager.py`. `validate_groups` refuses BEFORE the write: non-TOM `TimeUnit` names (did-you-mean), a unit twice, a column not in the host table or used twice, a column mapped to a different unit in another calendar, a duplicate calendar name. Groups are `TimeUnitColumnAssociation` (`TimeUnit`, `PrimaryColumn`, `AssociatedColumns`); `time_related_columns` form the single `TimeRelatedColumnGroup`. `update` with `groups` replaces the mapping. Writes go through `write_lane.commit`; a failed save runs `UndoLocalChanges`. Never raises the compatibility level.

**Journal behaviour (2026-10-03 review, wave 1):**
- `undo` / `replay` merge per property (three-way: entry pre, entry post, live). Only keys the entry changed are touched; a removal (unhide, clear displayFolder / description) counts as a change; a key that also changed live since the entry is a conflict listed per key (`conflicts[].keys`) and left alone unless `force=true`.
- When the connected model has no journal of its own, `history` / `diff` show the newest journal with a `note`, but `undo` / `replay` refuse with `journal_project_mismatch` unless `journal=<name>` is passed explicitly. `checkpoint_restore` refuses a checkpoint recorded for another project unless `journal=<the checkpoint's journal>` is passed.
- `checkpoint_restore` that removes columns or partitions, or more than 10 objects, needs `confirm_destructive=true` (preview reports `removes_gated_objects`, `requires_confirm_destructive`).
- Journal files: a torn last line is repaired before the next append (`journal_warning` lists unreadable lines); appends take a cross-process `<key>.lock`; retention drops only flushed entries (`model_write.journal_max_bytes`, default 64 MB; `model_write.journal_retention_days`, default 30); each entry records `capture {pre_source, ms}`. `journal=<name>` must be a plain name (`invalid_parameters` otherwise).
- A Desktop save only flushes entries whose project resolves to the saved project.

## III.3 `02_TMDL_Operations` — MODEL

File: `server/handlers/tmdl_handler.py` (editors in `core/tmdl/bulk_editor.py`, `measure_migrator.py`, `script_generator.py`). TMDL file automation: export, find/replace, bulk rename, script generation, measure migration, DAX formatting. Everything except `export` and `format_dax` works on TMDL **files** (offline, Desktop closed or reopened afterwards: writes return `reload_required`); every path is passed through `guard_or_error` (path guard).

| Operation | Purpose | Args (required in **bold**) | Gate |
|-----------|---------|-----------------------------|------|
| `export` | Export the LIVE model's structure (tables, columns, measures with DAX, relationships, roles) to ONE JSON file (`format: "TMDL"` but not `.tmdl` files). Needs a connection. **`output_dir` is used as the output FILE path** (parent folders are created; a directory path fails): pass a `.json` file path, or omit it for `exports/tmdl_exports/<db>_tmdl_<ts>.json` | `output_dir?` | none (read; no reload hint) |
| `find_replace` | Find/replace inside the targeted objects of a TMDL model | **`tmdl_path`** (definition folder, `.SemanticModel` folder or `.pbip`), **`pattern`**, **`replacement`**, `dry_run` (default **true**), `regex` (default false; pattern <= 4096 chars, compiled up front; `\1` / `\g<name>` expand), `case_sensitive` (default true), `target` (`measures`\|`columns`\|`calculation_items`\|`functions`\|`roles`\|`partitions`\|`all`; handler default **`all`**), `scope` (`expression` default: the `=` bodies, DAX strings and comments skipped \| `object`: every line of each targeted object block \| `file`: whole files, only with `target='all'`), `include_strings_and_comments` (default false) | DESTRUCTIVE |
| `bulk_rename` | Rename measures / columns / tables and every reference to them, in memory, then one commit | **`tmdl_path`**, **`renames`** = `[{old_name, new_name, object_type? (default measure), table_name? (measures/columns)}]`, `update_references` (default true = DAX references; structural refs - relationships, sortBy, hierarchy levels, `ref table`, roles, perspectives, translations - always update), `dry_run` (default **true**), `cascade_to_reports` (`true` = every bound `.Report`, or a report/`.pbip` path; PBIR bindings are renamed only for renames that succeeded and only after the TMDL commit; **table renames are refused with a cascade**) | DESTRUCTIVE; **CATASTROPHIC when `cascade_to_reports` is truthy** (needs `confirm_destructive=true`) |
| `generate_script` | Pure transform: emit TMDL text from a definition dict (no file touched) | **`definition`** (object), `object_type` (`table`\|`measure`\|`relationship`\|`calc_group`; default `table`). Definition keys: table `{name, columns[], partitions?, description?}`, measure `{table, name, expression, properties?}`, relationship `{from_table, from_column, to_table, to_column, ...}` | none |
| `migrate_measures` | Copy measures from one table `.tmdl` file into another (target created if missing) | **`source_path`**, **`target_path`**, `display_folder_filter?`, `replace_target` (default false), `skip_duplicates` (default true), `dry_run` (default **true**) | DESTRUCTIVE |
| `format_dax` | Format DAX and write it back through LIVE TOM. `engine`: `daxformatter` (default; posts to daxformatter.com, opt-in) or `local` (F-23: in-process, no network, works without the opt-in) | `measure_refs=['Table[Name]', ...]` OR `format_all=true`, `engine?` (`daxformatter`\|`local`), `skip_space_after_function?` (local only: `SUM( x )`), `max_line_length` (default 90), `dry_run` (default **false**), `on_error` (`skip`\|`abort`), `time_budget_s?` (default 120; service engine only) | DESTRUCTIVE (live model; refused on an XMLA target unless `dry_run`) |

Outputs (`success` plus):

- `export`: `format`, `database_name`, `export_file`, `export_timestamp`, `statistics {tables, relationships, roles, total_measures, total_columns}`, `file_size_mb`, `message`, `note`.
- `find_replace`: `matches_found`, `replacements_made`, `files_modified`, `backup_path`, `errors[]`, `warnings[]`, `preview`, `scope`, `target`, `dry_run`. A bad `target`/`scope`, or `scope='file'` with a `target` other than `all`, returns `success: false` with the message in `errors`.
- `bulk_rename`: `objects_renamed`, `references_updated`, `files_modified`, `backup_path`, `errors[]`, `warnings[]`, `skipped[]`, `details[]`, `dry_run`, `preview?`, `cascade?` (report-side result).
- `generate_script`: `script` (TMDL text).
- `migrate_measures`: `measures_found`, `measures_migrated`, `duplicates_skipped`, `backup_path`, `errors[]`, `warnings[]`, `measures[]` (first 50).
- `format_dax`: `dry_run`, `engine`, `formatted[{ref, before, after, written, note?, error?}]`, `external_service` (`null` for `engine=local`), `stats {written, skipped_unchanged, rejected_not_equivalent, apply_errors, ...}`, `remaining?` + `partial: true`. With `dry_run=true` the call is a READ and carries no destructive-op gate notice (live test 2026-10-03).

Errors: `missing_argument`, `invalid_argument`, `not_found`, `invalid_operation`, `pattern_too_long`, `invalid_regex`, `operation_failed`, `external_formatter_disabled` (format_dax opt-in), plus the gate envelope. Backups: the file editors write a backup (`backup_path`) before a real write; the editors are constructed without config, so the backup folder is the code default `./backups/tmdl` relative to the server's working directory (the old `tmdl.*` config section was removed in 2026-10 because nothing read it).

`format_dax` posts to canonical Azure URL — was 301 → body lost; canonical URL fix landed in commit `e9dc355`.
**`engine=local` (2026-10-03 review F-23).** `core/dax/local_formatter.py`: a deterministic, offline formatter built on the repo lexer, SQLBI long form (`FUNC ( a, b )`, one argument per line when the call exceeds `max_line_length`, `VAR x =` / `RETURN` blocks on their own lines, lambdas `(x : T) =>`, `DEFINE` / `EVALUATE` / `ORDER BY`). Built-in functions and keywords are upper-cased, a user-defined function / VAR / table keeps its spelling. Comments (`//`, `--`, `/* */`, `///`) and string literals are carried byte-exact (a trailing comment stays at the end of its line, one blank line between statements is kept); `format(format(x)) == format(x)`. Every result is checked with the same `token_equivalent` guard as the service plus "the comment sequence is unchanged"; a text that cannot be laid out (unbalanced brackets, unterminated string) or fails the guard comes back as a per-measure `error` and is never written. It needs no `dax_formatter.enabled` and never touches the network; the default `engine` stays `daxformatter` (still opt-in, and its refusal now suggests `engine='local'`). The result shape is the service's (`formatted[]`, `stats`); `time_budget_s` is not used by the local engine.

**Opt-in since 2026-09-22 (review D-20):** measure DAX is sent to a third-party service, so the op returns `error_type: "external_formatter_disabled"` until the user sets `"dax_formatter": {"enabled": true}` in `config/local_config.json` or `MCP_DAX_FORMATTER_ENABLED=1` (this changes the previous default; `engine=local` is exempt). Formatted text is written back only when it is token-equivalent to the original (`stats.rejected_not_equivalent`); CRLF/LF differences are not "changes"; work stops at `time_budget_s` and unprocessed refs come back in `remaining` (`partial: true`).

## III.4 `02_QA_Linguistic` — PBIP

File: `server/handlers/qa_linguistic_handler.py`. Q&A linguistic schema CRUD on PBIP semantic models. **Offline disk operations — PBI Desktop must be closed for writes.** TMDL projects keep the schema inline in `<Model>.SemanticModel/definition/cultures/<culture>.tmdl` (`linguisticMetadata =` JSON block + `contentType: json`) — that block is read and rewritten in place (indentation, line endings and BOM preserved, atomic write). `model.bim` cultures and the legacy `cultures/<culture>/linguisticMetadata.json` layout are also supported (review C-17 / G-23). `pbip_path` may be a `.pbip`, its folder, or the `.SemanticModel` folder; a folder with several models returns an ambiguity error listing them. `culture` defaults to the model's `culture:` (fallback `en-US`); entity names match case-insensitively.

| Operation | Purpose | Required args | Destructive |
|-----------|---------|---------------|-------------|
| `load` | Load the linguistic schema (`storage`: tmdl\|bim\|json, `culture`) | `pbip_path`, `culture?` | No |
| `audit` | Audit entities for missing synonyms/QnA flags/bindings | `pbip_path`, `culture?` | No |
| `set_include_in_qa` | Toggle IncludeInQnA flag on entity | `pbip_path`, `culture?`, `entity`, `include` | Yes |
| `add_synonym` | Add synonym to entity | `pbip_path`, `culture?`, `entity`, `synonym`, `state` (default `'Authored'`) | Yes |
| `remove_synonym` | Remove synonym from entity | `pbip_path`, `culture?`, `entity`, `synonym` | Yes |

Audit returns `entities_without_extra_synonyms`, `entities_excluded_from_qa`, `entities_without_binding`.

`dry_run=true` on a write op returns `{dry_run: true, would_change: {...}}` and writes nothing (wave 3, A-15: the flag used to be ignored while the gate skipped dry runs - a "dry run" wrote the file ungated).

## III.5 `03_Batch_Operations` — BATCH

File: `server/handlers/batch_operations_handler.py`. Bulk CRUD on ONE AMO connection with ONE SaveChanges per call (review C-11).

Single op `batch_operation` dispatches by `(object_type, operation_kind)`:

| `object_type` | Operations | Notes |
|---------------|-----------|-------|
| `measures` | `create`, `update`, `delete`, `rename`, `move`, `move_display_folder` | `create` never overwrites (`already_exists`) unless `options.if_exists='update'`; `update` never creates; items accept `name`/`measure`/`measure_name` + `table`/`table_name` |
| `tables` | `create`, `update`, `delete`, `rename`, `refresh` | `refresh` honours per-item `refresh_type` (default `full`) and saves once (C-23); `create` needs `expression` or `m_expression` |
| `columns` | `create`, `update`, `delete`, `rename`, `move_display_folder` | |
| `relationships` | `create`, `update`, `delete`, `rename`, `activate`, `deactivate` | `update` items may carry the C-42 relationship properties |

**Preview (F-01):** top-level `preview=true` (alias `dry_run=true`) previews the classic path without saving: `would_change[]`, `errors[]`, `breaks[]`, `plan_hash` (apply = the same call + `plan_hash`, `stale_preview` when the model moved); see §III.2 *Preview any write*. `options.dry_run` remains the validate-fields-only check.

Common args: `object_type`, `batch_operation`, `items` (per-type cap: measures/columns 200, relationships 100, tables 30). Options: `use_transaction` (default `true` — all-or-nothing: the first failing item stops the batch and every change is undone), `continue_on_error` (only with `use_transaction=false`: failing items are skipped, the rest is committed; without it a non-transactional batch commits the successful prefix), `dry_run` (per-type required-field validation), `confirm_destructive` (required for delete >20 items — strict boolean). Results: `succeeded`, `failed`, `not_attempted`, `committed`, `rolled_back`, per-item `results`. Item targets are canonicalized once (conflicting aliases → `ambiguous_target`, C-19); a caller-supplied `evidence` is ignored.

Rename batches (and table/column `update` items with `new_name`) are verified after the save and rolled back exactly on new expression errors (`rename_introduced_errors`); measure rename batches reject chains / duplicate targets up front (`invalid_rename_batch`) and check conflicts model-wide, case-insensitively (C-32).

Severity (C-13): CATASTROPHIC for any `tables/delete`, >10 deletes of anything, every `rename` batch of tables/measures/columns, table `update` renames, and `tables/refresh` with ≥ `model_write.catastrophic_refresh_tables` (default 10) items; other mutating batches are DESTRUCTIVE. Mass deletes of measures/columns/tables can auto-clear under LIGHT only with PROVEN in-process evidence (see §III.2).

**`ti_suite`** (object_type `ti_suite`): generates YTD/QTD/MTD/WTD/PY/… variants over `base_measures` (alias `measures`); preview by default, `apply=true` creates them through the measures/create path. Items carry `measure_name` + `table_name` (the base measure's home table resolved via DMV when `target_table` is absent; unresolvable → `home_table_unresolved`, C-14).

**`apply_changeset`** (declarative multi-object delta): pass `changeset` (grouped by object_type — `tables`/`columns`/`relationships`, each with `create`/`update`/`delete` lists; **measures excluded** — use `object_type=measures`). The document and every item's required fields are validated first (C-36). `preview=true` → `plan_hash` + op summary, no write. The plan hash fingerprints the TOM model itself (tables, partitions, columns, measures, relationships — C-05; it was a constant before); apply requires that `plan_hash` back (rejects `stale_plan`, and `plan_hash_unavailable` when the model could not be read). Ops are applied in dependency-safe order against ONE model (SaveChanges once, UndoLocalChanges on any failure). Column items carry description/hidden/display_folder/format_string/source_column; relationship items only known keys. Gated DESTRUCTIVE; CATASTROPHIC when it drops a table or has >10 deletes.

**`patch`** (F-03: select-and-patch + BPA FixExpression; `core/analysis/patch_engine.py`, `core/analysis/bpa_fix.py`). `object_type=patch` with `select` + `patch`, or `from_bpa`:
- `select` = a Dynamic-LINQ expression over the model in the BPA dialect, starting with a collection (`Measures`, `Columns`, `Tables`, `Hierarchies`, `Partitions`, `Relationships`, `Roles`, `Perspectives`, `Expressions`, `DataSources`, `CalculationItems`), e.g. `Measures.Where(Expression.Contains("SUMX") and not IsHidden)`. It is evaluated by `bpa_expression` over the TMSL snapshot of the open model; it must yield objects (not `.Count()`); at most 500 (`too_many_objects`).
- `patch` = `{Property: value-template}`. Properties are the allow-list of `core/operations/tom_properties.ALLOWED` (Description, IsHidden, DisplayFolder, FormatString, DataCategory, SummarizeBy, ... per kind; names / expressions / data types / keys are `denied_property`). String values are templates: `{Name}`, `{Table.Name}`, any scalar member of the object; `{{` `}}` are literal braces. `{"Delete()": true}` deletes the selection (measures, columns, hierarchies, relationships, roles, perspectives, expressions, data sources; cannot be combined with assignments).
- `preview=true` (read-only, never gated) returns per-object `changes` (`before` -> `after`, `action` set/noop/delete; first 200 inline), `operations`, `object_count` and a `plan_hash` = SHA-256 over the plan content (select, patch, every object's before/after). Apply (`preview` absent) REQUIRES the `plan_hash`: the plan is recomputed against the current model and a different hash is `stale_plan`; a missing one `plan_hash_required`. Every value is coerced against the real TOM object first, so one bad value (`patch_invalid`) writes nothing; then every `setattr`/`Remove` and ONE `write_lane.commit` (journaled with the plan hash; a failed save is `safe_undo`-ed, `rolled_back: true`).
- Gate: DESTRUCTIVE; CATASTROPHIC when more than `model_write.catastrophic_patch_objects` (default 50) objects, more than 10 deletes, or a `HIGH_IMPACT` property (`CrossFilteringBehavior`, `SecurityFilteringBehavior`, ...). A homogeneous measure/column delete uses the same in-process blast-radius evidence as the batch delete.
- `from_bpa=[rule ids]` (or one id / comma list; exclusive with `select`/`patch`): the rules run over the same snapshot (live ruleset + Microsoft's) and each violation's `FixExpression` is translated by `bpa_fix`. **Supported subset:** `Delete()`; `<Property> = "<string>"` (C# `;` escapes decoded); `= true|false|<number>|null`; `= <EnumType>.<Member>` (`SummarizeBy = AggregateFunction.None`); `= it.<Member>[.<Member>]` (a `{Member}` template, e.g. `it.Table.Name`); the property must be on the allow-list for the object kind. Everything else (prose advice, `string.Concat(...)` lambdas, `Partitions[0].Name = ...`, several statements, structural properties such as `Name` / `DataType` / `IsKey`) is returned under `fix_unsupported` as `{rule_id, fix_expression, reason}` and never guessed; two rules setting one property differently, or one deleting an object another patches, is `conflicting_fixes`.
- `06 full` flags `fix_available: true` on a finding (and its flattened issue) only when its FixExpression is machine-applicable in this sense; prose fixes keep their text in `fix_expression`.

Anti-pattern (avoid): per-item `model.SaveChanges()`. Resolve culture/perspective/etc. once at top, save once at end.

## III.6 `04_Run_DAX` — QUERY

File: `server/handlers/query_handler.py`. Single-op tool: execute DAX with auto-limits and optional trace. **The one DAX executor (wave 3, L-12):** `query_handler.execute_dax(request, source=...)` implements every mode; `09_Debug_Operations run_dax`, `04_Query_Operations impersonate_run` and `13_Query_Plan evaluate_and_log` are thin forwarders to it (`executed_via: "04_Run_DAX"`), so roles, trace+rows, export, cache policy and the `trace_id` store behave identically everywhere.

| Param | Description |
|-------|-------------|
| `query` | DAX (must start with `EVALUATE` / `DEFINE`; leading `//`, `--`, `/* */` comments are fine). Legacy `dax_query` accepted (`deprecated_args`). |
| `top_n` | int, default 100 — rows returned inline; the reader stops there and `truncated`/`has_more` say whether more rows exist. With `export_path`: max rows written (default 1,000,000). `mode=trace`: rows kept from the traced run (default 100). |
| `mode` | `auto` \| `simple` (preview) \| `analyze` \| `trace` \| `evaluate_and_log` (DAXEvaluationLog events of one traced run: `capture_id`, `evaluation_log`, `eval_log_subscribed`, `se_fe_timings`) \| `validate` (M4/PV-01: parse + bind only, see below) \| `build` (F-20a: `measures` + `group_by` + `filters` + `order_by` -> one `SUMMARIZECOLUMNS` query, returned as `query`; no `query` argument needed; `execute=true` also runs it and adds `built_query` / `built_from`; `core/debug/dax_query_builder.py`), default `auto` |
| `bypass_cache` | bool, default `false` (legacy `no_cache`) — skip the server-side result cache and always execute |
| `runs` | `analyze`: timed runs (default 3, max 20) |
| `cold` | `analyze`: clear the VertiPaq cache before every timed run (default `false` = 1 untimed warm-up, then warm runs) |
| `clear_cache` | bool, default `true` — trace mode only |
| `queries` | **Batch mode (S-30).** Array (max 20) of DAX strings or `{id, query}` objects, run SEQUENTIALLY on the one connection through the same single-query path (`execute_dax`), so `top_n` / `bypass_cache` / `mode` (`auto` \| `simple` \| `validate`) / `roles` apply to every entry. Result: `{mode: "batch", query_count, executed, succeeded, failed, results: [{id, index, success, ...the single-query result}], not_run?}`; any failed query makes `success: false` + `error_type: "batch_partial_failure"` with every result still present. Per-query progress; a cancelled call returns what ran (`cancelled`, `partial`, `not_run`). Refused (`invalid_args`): `queries` + `query`, > 20 entries, an empty / duplicate-id entry, `export_path` / `runs` / `cold`, `mode` trace / analyze / evaluate_and_log / build. The JSONL file / hash / resume variant of S-30 (`queries_path`, `output_path`, `hash_only`, `timeout_s`, `skip_slow_signature`) is NOT built yet. |
| `stop_on_error` | batch mode: stop after the first failed query (`stopped_early`, the rest in `not_run`); default `false` = keep going |
| `roles` | array (or comma string) of RLS roles — the query runs on a temporary `Roles=` connection (**honoured**; not combinable with `mode=trace`/`analyze` or `export_path` → `unsupported_arg`) |
| `effective_user_name` | UPN (`EffectiveUserName=`; Service/SSAS only — Desktop rejects it with `desktop_no_effective_user`; use `04_Query_Operations simulate_as_user`). Requires `roles`. |
| `custom_data` | `CustomData=` value for `CUSTOMDATA()` RLS. Requires `roles`. |
| `export_path` | `.csv` (UTF-8 **with BOM**) or `.json` — streams the result to disk (no 10k reader cap, atomic temp+rename) and returns `{exported, export_path (absolute), format, row_count, total_rows, truncated, columns, sample, warning?}`. Refused with `error_type: "read_only_mode"` when `security.read_only_mode` is on. |

**Every result** carries top-level `cache: {hit, age_s, bypassed?}` and `connection: {port, database, connection_generation, model_generation, amo_version}`. Values are native JSON types (numbers, bools, ISO-8601 dates) — never culture-formatted strings (before 2026-09-22 Decimals came back as e.g. `"1234,56"` and every cell was a string).

**Result cache** (`core/infrastructure/query_executor.py`): key = (connection string, database, connection generation, **model generation**, prepared query, top_n, row cap), TTL `performance.cache_ttl_seconds`. The model generation is bumped by every write op (`connection_state.bump_model_generation`), every connection swap, and by an external model-version probe (MDSCHEMA_CUBES `LAST_SCHEMA_UPDATE`/`LAST_DATA_UPDATE` + TMSCHEMA_MODEL `ModifiedTime`, at most one probe per ~2 s) that catches Desktop-side edits/refreshes. When the model version cannot be verified nothing is cached. A hit reports `execution_time_ms: 0` and the original timing as `cached_execution_time_ms`. Results over 1,000 rows are never cached. **No more "rename a VAR to bust the cache"** — pass `bypass_cache=true` when you need a guaranteed server round-trip. Needs live check: that a Desktop UI measure edit moves `LAST_SCHEMA_UPDATE`/`ModifiedTime` (otherwise such edits are only caught after a write op, a swap or the TTL).

**`mode='validate'`** (PV-01, `core/dax/dax_validate.py` + `OptimizedQueryExecutor.validate_dax`) asks the engine to parse and bind the query without executing it. Returns `{success, valid, errors[], elapsed_ms, statements, strategy, not_validated?, raw_errors?}`; an invalid query is `success: true, valid: false` with the engine's own text in `errors[]` (positions `Query (line, col)` translated back to the caller's text; untranslated text in `raw_errors`); `success: false` = validation could not run (`invalid_args` no top-level EVALUATE, `connection_not_open`, `timeout`, `validate_failed`, `adomd_not_available`). Never cached, never impersonated (`roles` ignored). **Design:** the ADOMD `Prepare()` candidate was rejected because for an XMLA/tabular target it is not known to send anything to the server (a silent "valid" would be worse than no answer); `TOPN(0, ...)` was rejected because the engine may still evaluate the inner table. Chosen: every top-level `EVALUATE <expr>` becomes `VAR __mcp_validate_N = <expr>` in the query's DEFINE block (the caller's own DEFINE is kept) and the executed statement is `EVALUATE ROW("__mcp_validate", IF(FALSE(), COUNTROWS(__mcp_validate_1) + ..., 0))`: names/types/syntax are resolved when the engine compiles the script, the dead branch is never evaluated. 10 s command timeout (`VALIDATE_TIMEOUT_S`) is the backstop; a timeout is reported as unknown, never as valid. **Limits:** `ORDER BY` / `START AT` are dropped from the rewrite (listed in `not_validated`); run-time errors are not found; DMV `SELECT` and bare expressions are refused. **Needs a live check** (no Desktop was available): that the engine binds an unused-branch VAR (i.e. an unknown column inside it IS reported), that the dead `IF` branch costs nothing on a heavy query, and the exact error text/positions.

**`mode='analyze'`** is a real N-run benchmark (cache always bypassed): `benchmark: {runs, cold, run_ms[], wall_clock{avg_ms,min_ms,max_ms,median_ms,stddev_ms,cv_pct}, reader_ms{...}, first_run_ms (warm-up, cold=false), cache_cleared (cold=true), timing_source}` plus the last run's rows. Client wall-clock only — use `13_Query_Plan benchmark` for FE/SE server timings per run.

**TOPN preview wrapper:** only a single, unordered `EVALUATE <table>` is wrapped in `TOPN(top_n, …)`. Queries with a top-level `ORDER BY` / `START AT`, several `EVALUATE`s, `DEFINE`, or an existing outer `TOPN` are left untouched (the reader cap bounds their rows) — they used to fail with a DAX syntax error.

**Timeouts / cancellation:** the ADOMD `CommandTimeout` is the dispatcher budget minus 5 s (55 s for 04_Run_DAX), so the engine-side timeout fires first and the worker releases the connection. `connection_state.cancel_active_queries()` cancels in-flight commands (used by the dispatcher on its timeout path); a cancelled query reports `error_type: "cancelled"`.

**`mode='trace'` response keys** — `runner`, `runner_lane`, `performance`, `se_queries_lookup`, `se_events`, `cache_cleared`, `summary`, plus:

| Key | Shape | Notes |
|-----|-------|-------|
| `rows` / `columns` / `row_count` / `truncated` | first result set of the SAME traced run | **2026-09-22 (L-19)** — first `top_n` rows (default 100, max 100k). With `export_path` the rows are written to disk instead (`export: {exported, export_path, row_count, total_rows, truncated}` + `sample`). The exe lane cannot return rows: `rows_note` + `ignored_args: ["top_n", "export_path"]`. |
| `trace_id` | str | **2026-09-22 (L-20)** — handle of this trace in `connection_state` (`get_trace_result(trace_id)`); pass it to consumers such as `09_Debug optimize` instead of relying on "the last trace in the process". |
| `trace_started` / `events_received` / `correlation` | bool / int / `{method, activity_id, foreign_dropped, unattributed_dropped, contaminated}` | **2026-09-22 (B-08/B-10)** — our QueryBegin/End is identified by OUR SessionID (fallback: identical query text; `ambiguous` → nothing guessed); only events with our ActivityID are kept, ActivityID-less events only when the capture is uncontaminated. |
| `se_analysis` | see 5.18 | **v7.14.2** — the same `SeEventAnalyzer` block `09_Debug_Operations` returns (`callbacks`, `datacache`, `timing_distribution`, `fusion_opportunities`, `join_analysis`, `row_ratio_analysis`, `with_clauses`). Omitted when there are no SE events; a failure inside the analyzer is swallowed and never breaks the trace response. |
| `result_sets` | `[{columns, row_count, truncated?}]` | **v7.15.0** — one entry per EVALUATE. Before 7.15.0 only the first statement was reported, so the rest were invisible. |
| `execution_metrics` | `{}` | **v7.15.0** — parsed `ExecutionMetrics` (136): `duration_ms`, `total_cpu_ms`, `fe_cpu_ms`, `se_cpu_ms`, `execution_delay_ms` (thread-pool queue wait), `peak_memory_kb`, `result_rows`, `query_dialect`. **Fires on Desktop** (verified 2026-09-02). Since 2026-09-22 the payload whose JSON `activityID` is ours wins (not "the longest from any session"). `{}` on the exe lane. |
| `runner_lane` | `"in_process"` \| `"exe"` | **v7.15.0** — which trace lane produced the numbers. `exe` means no plans, no execution metrics, no rows. |

Trace errors are structured: `error_type: "busy"` (+`retry_after`) when another trace is running (traces are serialised process-wide — each one clears the engine cache and sees every session's events), `"trace_unavailable"` + `reason: "trace_no_events"` + `amo` when the trace delivered no event at all (instead of success-with-zeros). The local trace-start wait is 5 s; settling ends early once our QueryEnd + ExecutionMetrics arrived and our activity is quiet for 300 ms. Stale-trace cleanup drops only `MCP_Trace*` traces whose session is no longer in `DISCOVER_SESSIONS`.

`performance` additionally carries **v7.15.0** `se_net_parallel_ms`, `total_cpu_ms`, `dq_ms` and `parallel_se_detected` alongside the existing keys, which are unchanged.

The same `result_sets` / `execution_metrics` / `runner_lane` keys are returned by `09_Debug_Operations` `run_dax` (it forwards to `execute_dax` mode=trace and splits the answer into `result` rows + `se_fe_trace`). Trace results stored for `optimize` carry `source` (`04_Run_DAX`, `09_Debug_Operations:run_dax`, ...).

`NativeTraceRunner.execute_with_trace()` (`core/infrastructure/query_trace.py`, new kwarg `keep_rows`) additionally returns two keys consumed by plan/waterfall analysis:

| Key | Shape | Notes |
|-----|-------|-------|
| `timeline` | `List[{line, class, subclass, duration_ms, cpu_ms, rows, kb, start_offset_ms, end_offset_ms}]` | **v7.14.2** — the FULL ordered waterfall including the synthetic `class: "FE"` gap rows the C# runner computes (previously discarded). `se_events` is unchanged and stays SE-only. Read a large FE segment as "look at the SE query immediately before it". Offsets are `None` when the running `DaxExecutor.exe` predates the `Timeline` field. |
| `agg_rewrites` | `List[{status, text, subclass}]` | **v7.14.2** — `AggregateTableRewriteQuery` events (subscribed since v7.x but never surfaced). `status` = `matched` \| `nomatch` \| `attempted`, classified from `EventSubclass` in both integer (`1`/`2`) and string (`MatchFound`/`NoMatch`) forms via `core/query_plan/capturer.py::_classify_agg_subclass`. Capped at 50; `text` truncated to 500 chars. Empty list on an exe build that does not emit `AggRewrites`. |

**`mode=validate` (2026-10-03):** a second `DEFINE` block is no longer swallowed - validation stops there and the remainder is reported under `not_validated`.

## III.7 `04_Query_Operations` — QUERY

File: `server/handlers/query_handler.py`. Query metadata, search objects, RLS, DMV probes.

| Operation | Purpose | Required args |
|-----------|---------|---------------|
| `data_sources` | List data source connections: INFO.DATASOURCES, falling back to TOM (partition sources + M shared expressions) when empty — Power Query models have no legacy DataSources (`fallback_from`) | `limit?` |
| `m_expressions` | Partition M sources (`kind: "partition"`) **and** shared expressions / parameters from INFO.EXPRESSIONS (`kind: "shared_expression"`); `limit` is pushed into the INFO query's TOPN | `limit?`, `next_token?` |
| `search_objects` | Search tables/columns/measures by name. **Case-insensitive substring** via DAX `SEARCH()` (`*`/`?` stripped — not a regex). Columns match on `ExplicitName`/`InferredName`; per-type failures are reported in `errors` + `partial: true` instead of being silently skipped | `pattern`, `types?`, `page_size?`, `next_token?` |
| `roles` | List RLS roles (deprecated — prefer `02_Model_Operations object_type=role`) | — |
| `test_rls` | Test RLS role filtering | `role_name`, `query?` (legacy `test_query`) OR `test_table?` |
| `impersonate_run` | Execute DAX as specific role(s) on a temporary `Roles=` connection. Role names / UPN / CustomData are quoted per OLE DB rules; a comma inside a role name, control characters or `; " ' , =` in a UPN → `invalid_args`. Errors: `connection_error` \| `query_error` \| `ols_blocked` (+`ols_errors`) \| `desktop_no_effective_user`. Values are native types (same conversion as 04_Run_DAX). | `roles`, `query` (legacy `dax_query`), `effective_user_name?`, `custom_data?`, `top_n?` (default 1000). Forwards to `04_Run_DAX roles=[...]` (`executed_via`). |
| `simulate_as_user` | Simulate user in filter expression (Desktop workaround) | `role_name`, `as_user`, `query` (legacy `dax_query`) |
| `rls_audit` | **F-07** For every role (or `roles`) and each probe identity — the known UPN(s) `as_user` (comma list), a zero-grant user, the empty string, a casing variant, a B2B `#EXT#` form, plus `probe_identities` — run the role filters through the `simulate_as_user` rewrite and report per RLS-filtered table (and every table reached from it over active relationships) the visible vs unfiltered row count. Findings (`severity` critical\|warn\|info): `fail_open` / `sees_everything` (an identity with no grant sees rows), `fail_closed_for_everyone`, `static_filter`, `filter_allows_all`, `case_sensitivity_mismatch`, `b2b_identity_mismatch`, `role_without_members`, `bidirectional_leak_path` (tables reached from a filtered table only through bidirectional relationships). Always `simulation: "filter_rewrite"` + `approximation.known_gaps` (Desktop rejects `EffectiveUserName`; ALL/REMOVEFILTERS inside measures can remove the simulated filter). Read-only; engine `core/validation/rls_audit.py`; also the `rls_audit` test type of `09_Validate` suites. `ols_audit` is not built. | `as_user?` (without a known UPN the case / B2B / fail-closed checks are skipped: `known_identity_missing`), `roles?`, `probe_identities?`, `custom_data?` |
| `search_string` | Search in DAX expressions or object names (apostrophes now match — only `"` is escaped in the literal) | `search_text`, `search_in_expression?` (default `true`), `search_in_name?` (default `true`) |
| `vertipaq_report` | Top columns by RAM with a hot/warn/info verdict (DMV only; a projection of the same extractor as `vertipaq_analyzer`). Returns `model_total_mb`, `columns[{table, column, cardinality, data_size_mb, dict_size_mb, hier_size_mb, total_size_mb, encoding, segments, rle_efficiency, verdict}]`, `column_count_total/returned`, `truncated`, `stats {hot, warn, info}`, `table_rollup[{table, total_mb, column_count}]`; `error_type: dmv_unavailable` when the VertiPaq DMVs return nothing | `top_n?` (default 50), `include_table_rollup?` (default true), `include_verdict?` (default true) |
| `vertipaq_analyzer` | Full VertiPaq Analyzer parity (model size, tables, columns, relationships, partitions, segments, findings). Returns `view`, `summary`, `findings`, `stats_included`, `stats_queries`; `invalid_args` on an unknown view | `view?` (`summary` default \| `tables` \| `columns` \| `relationships` \| `partitions` \| `segments` \| `all`), `include_stats?` (default true), `top_n?` (default 50), `export_path?` (CSV; refused in `read_only_mode`) |
| `dmv_probe` | Execute named DMV probe; an unknown `probe` returns `error_type: probe_not_found` with `did_you_mean` and `valid_values` (live test 2026-10-03) | `probe` |
| `list_dmv_probes` | List available DMV probes — **no live connection required** | — |
| `info_view` | INFO.VIEW.MEASURES/TABLES/COLUMNS/RELATIONSHIPS friendly metadata (`view` is allow-listed) | `view`, `top_n?` (default 1000) |
| `warm_cache` | Warm VertiPaq cache (no clear) before a warm benchmark. Every query bypasses the result cache; measure names are `]`-escaped (`Table[Measure]` / `[Measure]` accepted); columns are touched with `ROW("n", COUNTROWS(DISTINCT(col)))` instead of marshalling the values | `measures?` and/or `columns?` ('Table'[Col]) |

`vertipaq_analyzer export_path` is refused in `read_only_mode`.

DMV probes (8): `vertipaq_columns`, `vertipaq_dictionary`, `calc_dependency`, `object_memory_usage`, `object_activity`, `sessions`, `user_defined_functions`, `refresh_policies`. `list_dmv_probes` is gated BEFORE `is_connected()` check so it works disconnected. `run_probe` soft-fails on executor exceptions. `execute_dmv_query` results carry both `data` and `rows` (same list) with native value types (string columns are no longer coerced to numbers when they look numeric).

`info_view` wraps the `INFO.VIEW.*` DAX functions (CL 1604+, Power BI Desktop ≥ April 2024). On older models the wrapper falls back to the equivalent TMSCHEMA / MDSCHEMA DMV (`source: "DMV_FALLBACK"`). Capability is cached per-connection on `connection_state._info_view_supported`; every connection swap resets it so a different model re-probes.

## III.8 `05_DAX_Intelligence` — DAX

File: `server/handlers/dax_context_handler.py`. DAX expression analysis: context transitions, anti-patterns, VertiPaq, call tree, optimization, dependencies, impact, export. Never writes the model; `export` writes a CSV and `dependencies` with `html=true` writes an HTML file (tool annotation `readOnlyHint: false` since 2026-09-22).

| Operation | Purpose | Required args |
|-----------|---------|---------------|
| `analyze` (default; `operation` omitted or `"analyze"`) | Comprehensive static DAX analysis | `expression` (DAX) **or** `measure_name`, `analysis_mode` (`all`\|`analyze`\|`debug`\|`report`, default `'all'`), `skip_validation?`, `include_optimization?`, `include_profiling?`, `breakpoints?` |
| `dependencies` | Extract dependencies | `table` + `measure`, `include_diagram?` (Mermaid text, default `true`), `html?` (default `false`), `auto_open?` (default `false`) |
| `impact` | Measure impact analysis | `table` + `measure`, `include_diagram?` |
| `export` | Export ALL measures to CSV (via `05_Column_Usage_Mapping`) | `output_path` |
| `list_udfs` | List user-defined functions in model | — |
| `lint_model` | Analyze EVERY measure (and UDF) in one call; findings worst-first with `auto_fixable`/`fix_transform` (→ `15_DAX_Refactor`) | — ; filters `min_severity?`, `rule_ids?`, `tables?`, `max_findings?` (default 200), `suppress_rules?` |
| `expand_measure` | Emit `DEFINE [FUNCTION …] MEASURE … EVALUATE` inlining transitive measure deps and called UDFs (DAX Studio parity) | `measure_name` or `expression` (measure name); `inline?` (one `CALCULATE`-inlined expression) |
| `find_clones` | Copy-pasted DAX across measures (+ calculation items / calculated columns when the TMSL snapshot is available) by normalised token structure; groups with a UDF signature or calc-group-item proposal (F-15) | — ; `min_tokens?` (12), `min_similarity?` (0.85, near clones), `min_group_size?` (2), `include_fragments?` (true), `include_near?` (true), `max_groups?` (50), `tables?` |
| `explain` | Structured explanation of a measure: call tree, filter modifiers in order, context transitions, iterators, VARs, calc-group / RLS notes, dependencies (F-33; no prose). UDF and table names keep the casing as written; a VAR defined by a UDF call but used as a table (iterator row source, first argument of `FILTER`/`COUNTROWS`) is typed `table` (live test 2026-10-03) | `measure_name` or `expression`; `include_values?` (+ `filters?`, `page_name?` + `visual_id?`/`visual_name?`, `max_rows?`) |

**Measure resolution (2026-09-22, review D-04).** `measure_name`, or an `expression` that *exactly* names a measure (case-insensitive, `[Name]` allowed), analyses that measure's model expression (`resolved_measure`/`measure_info` in the response). Anything with DAX structure is analysed verbatim. A near-miss name returns `error_type: "measure_not_found"` with `suggestions` — it is never silently replaced by a fuzzy match (the old behaviour analysed unrelated measures for inputs like `SUM(Sales[Amount])`).

**Validation (review D-14).** The engine checks the expression inside `EVALUATE ROW("Result", IF(FALSE(), <expr>\n))` (scalar) or `EVALUATE TOPN(0, <expr>\n)` (table) — cheap, and a trailing `// comment` cannot swallow the closing paren. `DEFINE`/`EVALUATE` queries pass through unchanged. Invalid DAX returns `success: false, error_type: "invalid_dax"`.

**Rules.** One engine (`core/dax/analyzer`): JSON rules in `core/dax/knowledge/rules/*.json` + Python structural rules, one canonical rule per anti-pattern (`supersedes` in JSON). Advice follows current SQLBI guidance (constant denominators use `/`; `FILTER(ALL(col))`, per-measure `CROSSFILTER`/`USERELATIONSHIP`, `IF(ISBLANK(x), BLANK(), …)` are not flagged). CB001–CB011 are the callback rules (CB007–CB011 restored). Accept a finding with a `// lint-disable RULE_ID` (or `lint-disable-all`) comment in the measure; the ids are reported under `suppressed`. `lint_model` returns `per_measure` (health score, finding count), `by_rule`, `total_findings` (distinct rows)/`total_occurrences` (rows x `occurrences`)/`returned_findings`/`omitted_findings`, sorted by severity then rule frequency, with the model schema applied (same verdicts as `analyze`). Identical (object, rule, title) findings collapse into one row with `occurrences`; `by_rule` counts the findings left after `min_severity`, and `by_rule_all_severities` appears only when `min_severity` filtered something (live test 2026-10-03). `auto_fixable` maps `MAINT_UNUSED_VAR → remove_unused_variables`, `CORR_DIVISION_WITHOUT_DIVIDE → use_divide`.

**`find_clones` (2026-10-03 review F-15; `core/dax/clone_detector.py`).** Expressions are lexed and normalised to *units*: string / number / datetime literals, column references (`Sales[Amount]` is ONE unit), bare `[Name]` references, table names and free identifiers become placeholders; VAR names are numbered by declaration order (a renamed VAR is not a difference); function names, keywords, operators and punctuation stay literal; comments and whitespace are ignored. Same unit-key sequence = clones. Result `groups[]`, each with `scope` (`measure` = whole bodies | `fragment` = the same call sub-tree inside different objects), `match` (`exact_duplicate` | `structural` | `near` with `similarity` and `differences`), `members[]`, `parameters[]` (`name`, `kind` literal/column/table/measure, `mode` `val`|`expr`, `type_hint`, `occurrences`, the value in each member), `proposal`, `extractable_by_transform`, `recommended` (3+ copies, the canon's UDF threshold), plus top-level `by_match`, `total_groups`, `omitted_groups?` and `udf {compatibility_level, required_compatibility_level: 1702, supported, existing_functions}` (the compatibility level is read from `DBSCHEMA_CATALOGS`, because Desktop's `INFO.MODEL` lacks it; live test 2026-10-03). `proposal.type`: `udf` (`udf.signature`, the full lambda `udf.expression`, one `calls[]` entry per member, `suggested_function_name`), `calc_group_item` when the ONLY difference is one base measure (`calc_group.item_expression` with `SELECTEDMEASURE()`, `replaces[]`; the UDF stays as `alternative`) or `null` (identical bodies; near clones: align them first). Parameter modes follow the canon: a literal is a `val` scalar; a column / table is the typed reference (`ColumnRef` / `TableRef`, lazy by definition); a differing measure is `val` only when every use sits in the caller's own context (no CALCULATE / iterator frame, not in a conditional branch), otherwise `MeasureRef` (`expr`: it must be evaluated at its site inside the body). `fragment` groups, calc items and calculated columns are reported but not rewritten by `extract_udf` (`extractable_by_transform: false`). `calc_group_migration.detector.detect_structural` reuses the same engine for calc-group candidates found by body structure instead of name suffix.

**`explain` (F-33; `core/dax/explain.py`).** Structure, not prose - the agent narrates. `call_tree` (every call with `kind` + per-argument `role`: CALCULATE expression / filter modifier, iterator row source / per-row expression, IF condition / then / else, DIVIDE numerator / denominator / alternate ..., and the references each argument touches), `filter_modifiers[]` in source order (ALL / ALLEXCEPT / REMOVEFILTERS / KEEPFILTERS / USERELATIONSHIP / CROSSFILTER / TREATAS / boolean predicate / FILTER / time intelligence, with `effect`, `columns`), `context_transitions[]` (explicit CALCULATE with `in_row_context` + the iterator providing it; implicit measure-reference transitions), `iterators[]`, `variables[]` (`definition`, `depends_on_*`, `used_in[]` with the call frames and `conditional`, `used_by`, `unused`, `eager_evaluation_note` when every direct use is in a conditional branch), `calculation_groups` (SELECTEDMEASURE family in the text + the model's groups from TMSL), `rls` (roles from TMSL, secured tables read directly), `dependencies` (measures, columns, tables, `udf_calls` with parameter modes) and `summary`. `include_values=true` evaluates the measure's VARs through the existing `09_Debug_Operations step_variables` (filters / visual context as there; it runs queries, so it is opt-in) and merges `variables[].value` + `variable_values`; a failure is reported as `variable_values_error`, never raised. Needs a model measure for values; a bare expression is explained statically.

## III.9 `05_Column_Usage_Mapping` — DAX

File: `server/handlers/column_usage_handler.py` (live analyzer: `core/analysis/column_usage_analyzer.py`). Column <-> measure usage analysis. Read-only tool (`readOnlyHint`); the two export ops write files under a path-guarded `output_path`.

All ops except `get_unused_columns_pbip` need a live connection. Common args: `force_refresh` (rebuild the cached mapping, default false), `include_dax` (add DAX text to rows, default **false** for size). Handler defaults when an arg is omitted: `operation` = `get_measures_for_tables` (the registered schema shows `get_unused_columns`; the handler default wins), `group_by` = `table` (the schema shows `flat`).

| Operation | Purpose | Args (required in **bold**) | Output |
|-----------|---------|-----------------------------|--------|
| `get_unused_columns` | Columns no MEASURE references (live model only: `scope: "model-only"`, report bindings are not counted) | `tables?`, `force_refresh?` | `unused_columns`, `unused_by_table`, `used_by_measures[_by_table]`, `used_by_relationships_only[_by_table]`, `used_by_sort_by[_by_table]`, `used_by_field_params[_by_table]`, `used_by_rls[_by_table]`, `summary {total_columns_analyzed, unused, tables_with_unused, ...}`, `scope`, `scope_note`, `tables_filter`, `formatted_output` |
| `get_unused_columns_pbip` | Offline, report-aware unused columns AND measures from a PBIP project | **`pbip_path`** (report `.pbip` = that report + its model; model `.pbip` / `.SemanticModel` / folder = every bound report), `report_paths?` (explicit `.Report` folders: parsed fresh, unused only if unused in ALL of them; omitted = served from the shared PBIP cache), `tables?` (filters columns only), `include_reasons?` | `unused_columns[]`, `unused_columns_count`, `unused_by_table`, `unused_measures[]`, `unused_measures_count`, `reports_analyzed`, `reports_failed[]`, `total_reports`, `total_columns`, `total_measures`, `complete`, `scope` (`model+reports` \| `model-only`), `tables_filter`, `warning?` (PARTIAL result: a report failed to parse or none could be analyzed), `usage_reasons?`, `formatted_output` |
| `get_measures_for_tables` | Measures that use columns of the given tables | **`tables`** (list), `group_by` (`table` default \| `column` \| `measure` \| `flat`) | `tables_requested`, `group_by`, `results` (or `measures` for `flat`), `summary`, `formatted_output` (for `table`/`flat`) |
| `get_columns_for_measure` | Columns a measure uses | **`table`**, **`measure`** | `measure {table, measure}`, `columns[]`, `column_count`, `formatted_output` |
| `get_measures_for_column` | Measures that use a column | **`table`**, **`column`** | `column {table, column}`, `measures[]`, `measure_count`, `formatted_output` |
| `get_full_mapping` | Complete bidirectional mapping | `include_dax?` | `column_to_measures`, `measure_to_columns`, `statistics` |
| `export_to_csv` | Write the mapping to two CSV files | `tables?`, `output_path?` (a DIRECTORY; default `exports/`), `include_dax?` | `column_to_measures_file`, `measure_to_columns_file`, `statistics {column_to_measures_rows, measure_to_columns_rows, tables_filter}`, `message` |
| `export_measures` | Write every measure (Table, Measure_Name, Display_Folder, DAX_Expression) to one CSV | `output_path?` (a DIRECTORY; default `exports/`) | `file_path` (`all_dax_measures_<ts>.csv`), `statistics {measures_exported}`, `message` |

Errors: `missing_argument` (`tables`, `table`+`measure`, `table`+`column`, `pbip_path`), `invalid_operation` (lists `valid_operations`), not-connected / manager-unavailable envelopes, `unexpected_error`.

**Always use `get_unused_columns_pbip` (offline) for unused-column analysis** - the live method does not see report bindings, so a column used only by a visual, filter or bookmark is listed as unused. `get_unused_columns_pbip` without `report_paths` and `07_PBIP_Operations query_unused` share the same cached analysis; pass `report_paths` only to scope to a subset of reports.

**Repeat calls (2026-10-03, P-02):** `get_unused_columns_pbip` without `report_paths` is served from the shared PBIP parse cache (repeat calls ~10 ms; output identical to a fresh parse). Explicit `report_paths` still parse fresh. Across several reports `usage_reasons` is capped at 20 per key.

## III.10 `06_Analysis_Operations` — ANALYSIS

File: `server/handlers/analysis_handler.py`. Model-wide analysis.

| Operation | Purpose | Required args |
|-----------|---------|---------------|
| `simple` | Quick model overview + insights (2–5s) | `mode` (`all`\|`tables`\|`stats`\|`measures`\|`measure`\|`columns`\|`relationships`\|`roles`\|`database`\|`calculation_groups`\|`storage`, default `'all'`), `table?` (required with `measure_name` for `mode=measure`), `measure_name?`, `max_results?`, `active_only?` |
| `full` | BPA + performance/integrity + topology + M practices (10–180s) | `scope` (`all`\|`best_practices`\|`performance`\|`integrity`, default `'all'`), `depth` (`fast`\|`balanced`\|`deep`), `include_bpa?`, `include_performance?`, `include_integrity?`, `max_seconds?` (5–300; bounds the WHOLE call, every depth), `ruleset?` (`default`\|`microsoft`\|`all`\|`audit`), `rules_path?`, `include_vertipaq?`, `include_runtime?` (audit: CR10 RI queries), `issues_max?` (default 500) |
| `compare` | Diff two models: the two open Desktop instances (`old_port?`, `new_port?`), or (F-17) any two targets `old` / `new` = `{port}` \| `{xmla:{workspace,model}}` (read-only, not live-verified) \| `{pbip_path}` \| `{git_ref, pbip_path}` (`git show` into a temp folder, never a checkout). ONE engine (`tmsl_normalizer` + `ModelDiffer`); the result splits `affects_results` from `cosmetic` (formatting-only DAX, descriptions, display folders, format strings, hidden flags, annotations) with counts in `summary`; `report=false` skips the HTML | `old_port?`, `new_port?`, `old?`, `new?`, `output_path?`, `generate_json?`, `report?` |

**`full`.** One TMSL snapshot feeds BPA, topology, M practices and relationships. BPA is a real Tabular Editor Dynamic-LINQ evaluator (`core/analysis/bpa_expression.py` over the TOM-like `core/analysis/bpa_model.py` view); a rule whose expression/regex/scope it cannot evaluate is reported as *unsupported* in `coverage` (never a silent pass). `ruleset`: `default` = the curated ~75 rules (M4 added `TEXTCONTAINS_REQUIRES_FULLTEXT_INDEX`, `APPROXIMATEDISTINCTCOUNT_IN_MEASURE`, `STRING_INDEX_ON_LOW_VALUE_COLUMN`) (`config/bpa_rules_comprehensive.json` + built-ins), `microsoft` = Microsoft's Tabular Editor BPARules (`core/bpa.json`), `all` = both (dedup by ID). `rules_path` (path-guarded; Tabular Editor array OR `{rules:[…]}`) adds rules **for this call only** — the response carries `external_rules: {added, skipped_duplicate_ids, invalid, unsupported}` (+ `external_rules_merged` = added). `include_vertipaq=true` reads the storage DMVs first so the `Vertipaq_*` cardinality/row-count rules evaluate. The response has a flattened, severity-ranked `issues` list (BPA + topology + settings + integrity + M; capped by `issues_max`, `issues_total` exact) enriched with business impact, plus `ruleset`.

The **topology** analysis runs under the integrity scope (`core/analysis/star_schema_validator.py`): findings `FACT_TO_FACT`, `SNOWFLAKE_DIMENSION`, `DISCONNECTED_TABLE` (field-parameter, what-if, measure, calc-group and auto-date tables are exempt), `MISSING_DATE_TABLE`, and (F-05) `AMBIGUOUS_RELATIONSHIP_PATH`: two distinct ACTIVE filter-propagation paths between two tables (a relationship filters from its ONE side to its MANY side, and back when bidirectional; the classic multi-fact star has none, a bidirectional fact link creates one; the search is bounded and caps at 10 findings). Fact detection = many-side source that is never a one-side target.

**`ruleset=audit`** (F-05; `core/analysis/audit_taxonomy.py`, `audit_rules.py`, `config/audit_taxonomy.json`). Runs the `all` ruleset plus 12 taxonomy-only rules (`AUDIT_BIDI_ON_RLS_PATH` CR1, `AUDIT_COLUMN_DATATYPE_UNKNOWN` CR3, `AUDIT_RLS_ON_FACT_TABLE` CR5, `AUDIT_RLS_INACTIVE_RELATIONSHIP` CR6, `AUDIT_USERELATIONSHIP_IN_RLS_FILTER` CR8, `AUDIT_COMPOSITE_KEY_RELATIONSHIP` CR9, `AUDIT_CALCITEM_NO_SELECTEDMEASURE` DES12, `AUDIT_UDF_UNUSED` / `AUDIT_UDF_DUPLICATE_BODY` DES13, `AUDIT_AI_IMPLICIT_MEASURES` AI1, `AUDIT_AI_DUPLICATE_FIELD_NAMES` AI5, `AUDIT_AI_CRYPTIC_NAMES` AI6). `config/audit_taxonomy.json` maps all 76 checks (CR1-10, MS1-9, DR1-3, DAX1-11, QP1-7, DOC1-6, DES1-10, DL1-8, AI1-9, plus the proposed extensions DES11 ambiguity, DES12 calc-group design, DES13 UDF hygiene, flagged `extension` until they are added to `audit-categories.md`) to existing rule ids and topology finding ids, so earlier rules (curated, Microsoft, the D-28 modern rules) are mapped, never duplicated. Every BPA violation, topology finding and flattened issue gets `audit_id` (+ `audit_ids` when several checks share the rule) and `audit_citation` = `audit-categories.md:<ID>`; the result's `audit` section is a verdict table: `fail` (with `examples`), `pass` (the mapped rules RAN and found nothing), `not_evaluated` (a mapped rule did not run - never a pass), `requires_live` (QP1-QP7 need a trace; AI9 a service setting; CR10 needs `include_runtime=true`), `manual` (no machine check, with `hand_off`), `engine_enforced` (CR2). `include_runtime=true` runs one `EVALUATE ROW(COUNTROWS(FILTER(Fact, NOT ISBLANK(fk) && ISBLANK(RELATED(dim[pk])))))` per active many-to-one relationship (max 40; a failed probe is a note, never counted clean) and reports CR10 orphans. Not detected: measures that re-implement a UDF instead of calling it (manual), DL1-DL8 (Fabric item metadata, not in the TMSL).

**`compare`.** Omit both ports with **exactly two** instances open to auto-pick them (the older instance = old); with more than two it returns `requires_input: true` plus the instance list, with fewer than two `insufficient_instances`, and a single port is an `invalid_input` error. Labels are the Desktop file names. The diff runs over the full normalised TMSL (tables, columns, measures, relationships, partitions/M, roles/RLS, perspectives, calc items, format strings): objects match by `lineageTag` first (true renames and cross-table moves), DAX compares token-wise so whitespace/comment-only edits are listed as *cosmetic* and not counted, and `total_changes` counts objects. The HTML report goes to `output_path` or `<repo>/exports/model_diffs/model_diff_<ts>.html` (`generate_json=true` writes the JSON next to it). Only connections this call opened are released.

**Live test 2026-10-03 (R0201).** `response_format=concise` on `full` drops the per-issue boilerplate keys and caps nested lists at 10 with a `<key>_truncated` marker (`core/validation/response_format.py` `concise_analysis_full`). `compare` ignores a column `data_type` that only one side states (TMDL omits it for calculated columns; `core/comparison/model_diff_engine.py`). The CR4 disconnected-table check skips tables that DAX reads (`star_schema_validator.py` + `info_normalize.dax_referenced_tables`). Audit check MS8 (unused visible columns) is `manual` with a hand-off to `05_Column_Usage_Mapping get_unused_columns_pbip`, because both unused-column BPA rules only match hidden columns (`config/audit_taxonomy.json`).

## III.11 `07_Report_Operations` — PBIP

File: `server/handlers/report_operations_handler.py`. Report-level structure, measure usage, rebind.

All write ops of this tool run through `gate_and_wrap` (`server/handlers/_authoring_utils.py`, WP9): **`dry_run=true` previews ANY write** (nothing touches disk, `would_change` lists the planned file changes, classified READ); writes are locked per report (parallel calls cannot lose updates) and confined to the report folder; a failed write is `error_type: write_failed`, a partial write keeps `reload_required` + `files_modified`; a folder with several `.Report` folders is `ambiguous_report` (pass the `.pbip` / `.Report`); a cached `17_Desktop_Bridge` status showing the report open with unsaved changes is returned as `desktop_state` and, under STRICT, blocks the write unless `confirm_destructive=true`.

| Operation | Purpose | Required args | Destructive |
|-----------|---------|---------------|-------------|
| `info` | Report structure (visual filters read from the top-level `filterConfig`) | `pbip_path`, `include_visuals?`, `include_filters?`, `page_name?`, `summary_only?`, `max_visuals_per_page?` | No |
| `measure_usage` | Measure usage per page — includes measures used ONLY as visual-level filters (visibility gates) | `pbip_path`, `page_name?`, `measure_filter?`, `output_format` (text\|json), `export_path?` (CSV) | No |
| `rename` | Rename the `.Report` folder + its `.pbip`, rewrite `artifacts[].report.path` in every `.pbip` pointing at it, `.platform` displayName | `pbip_path`, `new_name` | Yes |
| `rebind` | Rewrite `<report>/definition.pbir`: `model_path` = `byPath` relative to the report folder (must resolve to a folder); `model_id` = `byConnection` in the file's schema shape | `pbip_path`, `model_path` OR `model_id` | Yes |
| `backup` | Copy the `.Report` folder to a sibling `<name>.Report.backup_<ts>` | `pbip_path`, `message?` | No |
| `restore` | Restore from a backup folder (the sibling backup folder is an allowed root) | `pbip_path`, `backup_path` | Yes (CATASTROPHIC) |
| `discover_schema` | Discover visual field schema | `pbip_path`, `visual_type` | No |
| `audit_interactions` | **F-12.** Read-only, offline slicer reach / NoFilter audit + UI-suite plan: per slicer which data visuals its selection can reach through the model, which NoFilter rules are wrong / redundant / missing, the isolations a `policy` asks for, and what a UI test must / must not see change | `pbip_path`, `page_name?` (page id or name), `policy?` (see below) | No (READ) |
| `manage_extension_measures` | `reportExtensions.json` CRUD (`name: extension`, TOM `dataType` casing; delete refuses while a visual references the measure unless `force=true`) | `pbip_path`, `sub_operation` (list\|add\|update\|delete), `measure_name?`, `expression?`, `table_ref?`, `data_type?`, `format_string?`, `description?`, `force?` | Yes |

**`audit_interactions`** (`core/pbip/interaction_audit.py`; port of `pbi-perf-workflow/scripts/slicer_interactions.py`, now running on the dependency graph instead of three exported JSON files). The model comes from the offline TMDL parse of the report's bound model (`snapshot_from_model_data`); without a local model (`byConnection`) only the declared rules and dangling entries are reported (`model_available: false`, `model_note`) and no reach claim is made.

- `reach(S)` = the slicer's source tables + every table a filter on them propagates to over relationships (one to many; both ways for 1:1, many:many and bi-directional; inactive relationships included because a measure can `USERELATIONSHIP` them). A field-parameter slicer is a data slicer over every candidate column's table; a calculation-group slicer reaches every visual that shows a measure.
- `read(visual)` = tables of the columns it projects / formats by + for each measure shown the tables its value DAX reads (graph: measure + UDF bodies, transitively, incl. bare `Table` arguments passed to UDFs that `CALCDEPENDENCY` misses) and its own dynamic format string's reads; a filter-only measure contributes its value reads; a field-parameter table expands to its candidate columns / measures; a calculation-group table to its items. Report-level (extension) measures are scanned like model measures. `needed = read AND reach non-empty`. `REMOVEFILTERS`/`ALL` neutralisation is not modelled (such a pair counts as reached, so a needed rule is never called redundant).
- Per slicer: `needed[]` (`visual`, `title`, `type`, `via` tables, `rule`), `no_effect[]`, `no_filter_rules[]` with a `verdict`: `no_effect` (NoFilter on a pair the slicer cannot affect: the perf-correct rule, avoids a pointless re-query), `blocks_dependency` (NoFilter on a pair it CAN affect: the visual silently ignores a slicer it depends on; listed in `wrong_no_filter` + `review`), `intentional_isolation` (blocks, but the policy asks for it), `unknown` (no model); plus `redundant_no_filter[]`, `missing_no_filter[]` (no-effect pairs without a rule), `missing_isolation[]` (policy pairs without a rule). Per page: `dangling_interactions[]` and `suggested {add_no_filter[], remove_dangling[], review_rules[]}`. **Rules are never auto-removed**: a reachable NoFilter is only flagged for review (it may be a deliberate isolation).
- `policy` = `{"isolate_visuals": ["<id|title>"], "isolate_visual_types": ["card"], "isolate": [{"slicer": "<id|title|*>", "visual": "<id|title|type:<visualType>|*>"}]}`.
- Compact by default (live test 2026-10-03; `_compact_interaction_audit`): dangling rules stay listed, each `suggested` sub-list is capped at 25 (`<key>_count` holds the total) and the suite plan keeps `{visual, reason}` per pair (title / type dropped); `full=true` returns every needed / no_effect / missing pair.
- Top-level `suite_plan[]` (one row per slicer): `click`, `must_change[]` (positive controls: reached, no NoFilter) and `must_not_change[]` (`reason` `model_unreachable` or `declared_isolation`), the input of the invariance suite. The reusable engine functions are `audit_interactions(definition_dir, snapshot, *, page, policy)` and `build_invariance_plan(audit)` (returns the same flat rows).

## III.12 `07_PBIP_Operations` — PBIP

File: `server/handlers/pbip_operations_handler.py` (+ `_internal/hybrid_analysis_helpers.py`, `aggregation_helpers.py`). Offline PBIP analysis (no live connection needed; reads go through the shared `PbipModelCache`, see §IV.5). `pbip_path` may be a report `.pbip` (that report + its `byPath` model), a model `.pbip` / `.SemanticModel` folder or a project folder (a model covers every report bound to it). Writes only report files under `exports/` (or a path-guarded `output_path`), never into the PBIP project.

| Operation | Purpose | Args (required in **bold**) | Output (besides `success`) |
|-----------|---------|-----------------------------|----------------------------|
| `analyze` | Full offline analysis (BPA + lineage + quality). **The HTML model report.** | **`pbip_path`**, `output_format` (`html` default \| `json` \| `summary` \| `detailed`), `output_path?` (a DIRECTORY, default `exports/`; relative paths resolve against the repo root), `auto_open?` (html only) | html: `html_report`, `opened_in_browser`, `summary`, `message`. json/summary/detailed: `json_report` (`<project>_analysis.json`), `summary` (+ `analysis` for `detailed`), `output_format` |
| `validate_model` | TMDL structure / duplicate / reference validation | **`pbip_path`**, `tom_check` (`auto` default = also run the bundled TOM `TmdlSerializer` when the CLR is up \| `off` \| `required`) | `definition_path`, `validation_result {is_valid, errors[], warnings[], error_count, warning_count, tom_check}` (rows `{file, line, code, message, severity}`), `note` (a `compatibility level 1702 ... UDF` message is the TOM client's gate, not a model defect). Memoised per TMDL tree. |
| `validate_report` (alias `preflight`) | **F-06.** One read-only op answering "will Desktop open this, and is it safe to commit" (`core/pbip/preflight.py`) | **`pbip_path`**, `level` (`load` default \| `commit`), `tom_check` (`auto` default \| `off`), `report_author_cli?` (default true when `powerbi-report-author` is on PATH), `git_base?`, `include_unstaged?`, `max_findings?` | `ok`, `load_ok`, `commit_ok`, `verdict`, `counts {error, warning, info, load_blocking}`, `findings[{code, severity, message, file, pointer, load_blocking, impact, repair_hint, source}]`, `summary {reports[], models[]}`, `git?` (commit level), `report_author?` |
| `compare_models` | Semantic diff of two PBIP models (offline). **F-17:** runs the ONE compare engine (`core/comparison/compare_targets` -> `normalize_tmsl` + `ModelDiffer`, shared with `06 compare`); needs the TOM client library to deserialise the TMDL. `from_ref` / `to_ref` read that side as committed at a git ref (`git show`, nothing is checked out): `from_ref=HEAD` with the same path on both sides = "what changed since the last commit" | **`source_path`**, **`target_path`**, `from_ref?`, `to_ref?` | `comparison` (the differ's result + `has_changes`: `tables{modified[{changes{measures, columns, calculation_items, ...}}]}`, `roles`, `functions`, `model_properties`, ...), `summary` (`total_changes`, `affects_results`, `cosmetic`, `changes_by_category`), `affects_results[]` / `cosmetic[]` (`{category, object, change, fields}`: DAX / relationship / role / source / structure changes vs descriptions, display folders, formats, hidden flags; an unrecognised change shape counts as affecting results, never dropped), `sources`, `warnings` (e.g. the TOM client drops DAX UDFs below compatibility level 1702: function changes are then not compared). `TmdlSemanticDiff` no longer backs any tool op; it only feeds the HTML report's "TMDL changes view" |
| `generate_documentation` | Model documentation as **Markdown** | **`pbip_path`**, `output_path?` (a FILE; default `exports/<project>_documentation.md`) | `file`, `message`; without `output_path` also `summary` (first 500 chars) and `length_chars` |
| `query_dependencies` | Dependencies of one object (or the summary maps when `object_name` is omitted) | **`pbip_path`**, `object_name?` (`[Measure]` or `Table[Name]`; an ambiguous bare name returns `error_type: ambiguous` + `candidates`), `direction` (`forward`\|`reverse`\|`both`, default `both`) | with `object_name`: `object`, `object_type`, `depends_on_measures`, `depends_on_columns`, `referenced_by`, `referenced_by_field_parameters`, `used_by` (reasons). Without: `summary`, `measure_to_measure`, `measure_to_column` |
| `query_measures` | List/search measures | **`pbip_path`**, `table?` (exact, case-insensitive), `display_folder?` (prefix), `pattern?` (regex on the name, falls back to substring), `expression_search?` (regex on the DAX) | `total`, `measures[{table, name, expression, display_folder, format_string, is_hidden, description}]` |
| `query_relationships` | List relationships with quality flags | **`pbip_path`** | `total`, `relationships[]`, `quality {many_to_many_count, bidirectional_count, inactive_count, issues[]}` |
| `query_unused` | Unused columns/measures/functions across ALL reports of the project (cached) | **`pbip_path`**, `include_reasons?` | `unused_columns[]`, `unused_columns_count`, `unused_measures[]`, `unused_measures_count`, `unused_functions`, `coverage`, `reports_analyzed`, `usage_reasons?`. For a subset of reports use `05_Column_Usage_Mapping get_unused_columns_pbip` with `report_paths` |
| `scan_broken_refs` | visual.json field references that no longer exist in the model | **`pbip_path`** | `broken_references[{report, page, visual_id, visual_title, visual_type, table, field, ref_type, location, reason}]`, `reports_scanned`, `reports_skipped`, `summary {total_visuals_scanned, total_references_checked, broken_count, affected_pages, affected_visuals, by_location}`, `model_stats`, `warnings`. `objects`/`vco`/`selector` hits are formatting leftovers Desktop ignores; `query`/`filter`/`sort` break data. Memoised per tree |
| `git_diff` | Semantic git diff of TMDL/report changes, scoped to the named report/model | **`pbip_path`**; mode by args: `base_branch` (PR summary) \| `from_ref` [+ `to_ref`, default `HEAD`] (commit diff) \| neither (working changes: staged + unstaged + untracked) | categorised changes, `pathspecs`, `details[]` (see below), `from_ref`/`to_ref` in commit mode |
| `dependency_html` | Interactive dependency browser (HTML) | **`pbip_path`**, `output_path?` (path-guarded), `auto_open?` (default **false**), `main_item?` | `diagram_path`, `model_name`, `initial_selection`, `auto_opened`, `summary {visuals, pages, measures, columns, field_parameters, unused_measures, unused_columns}`; `error_type: generation_error` on failure |
| `aggregation_analysis` | Summarisation/aggregation patterns of the report visuals | **`pbip_path`**, `output_format` (`summary` default \| `detailed` \| `html` \| `json`), `output_path?` (path-guarded), `page_filter?`, `include_visual_details?` (default true) | html: `format`, `output_path`, `summary`. json: `data` (+ `output_path` when saved). summary/detailed: `report` (text). Errors: `missing_pbip_path`, `pbip_path_not_found` |

Errors: `missing_argument` (every op that needs `pbip_path`, and `source_path`+`target_path` for `compare_models`), `not_found`, `invalid_operation` (lists the valid ops), `operation_failed`, `manager_unavailable`, plus the path-guard envelope for `output_path`.

**HTML model report = `analyze` with `output_format='html'` + `auto_open=true`. NOT `dependency_html` (different thing) and NOT `generate_documentation` (Markdown, not HTML).**

**`validate_report` / `preflight` (F-06).** Replaces four outside implementations (`pbip/scripts/validate_pbip.py`, `validate_tmdl_blocks.py`, `~/.claude/hooks/g01_prepush_gate.py`, the `dump_visual_state.py --check-in-values` gate). Never writes: files are only read and the CLI call is the READ-ONLY `powerbi-report-author validate <report> --no-schema --format json` (never `pbir validate --update-schemas`, which rewrites every visual.json; tests assert the tree hash is unchanged). Checks: JSON well-formedness (+BOM), `definition.pbir` required fields + `byPath`/`byConnection` binding, `.platform` type/GUID, `report.json` resource packages (item without `type`, missing file, custom theme), pages (`pages.json` order vs folders, page-name regex `^[\w-]+$`, orphan folders, `page.json` required fields and name = folder), visuals (required fields, `isHidden` inside `visualGroup`, `parentGroupName` null/missing), `visualLink` / tooltip targets (bookmark and page must exist), bookmark navigators, `bookmarks.json` (group `children` are bare strings, listed vs files), bookmarks (`activeSection`, `sections`, `visualContainers` / nested `visualContainerGroups`, `options.targetVisualNames` against real pages/visuals), `visualInteractions` pointing at missing visuals, inverted-selection polarity (an empty `In.Values`, also under `Not`, fails with `InvalidQueryInExpression`), field bindings against the bound model (`scan_broken_visual_references`; a missing entity in a live `query` projection is load-blocking), and for the model: TMDL orphaned child properties / queryGroup pairing / duplicate model-level annotations + the TOM deserialize (`core/tmdl/tom_check`; unavailable = an `info` finding "NOT proven to load"). `load_blocking: true` is set only for defect classes with evidence that Desktop rejects the WHOLE report (memory `feedback_pbir_load_failure_classes`: dangling visualLink / bookmark container refs, resourcePackage item without `type`, a missing entity in a live projection; plus unparseable JSON, a broken binding, a TOM parse failure); `impact` is `load_fail` | `silent_drop` | `render_error` | `hygiene`. `level=load`: `ok` = no load-blocking error. `level=commit`: `ok` = no error at all, and the git change set (staged by default; `include_unstaged`, `git_base` = `base...HEAD` like the g01 gate) is checked too: JSON validity of every changed `.json/.pbir/.platform/.pbip/.pbism`, machine-local `.pbi/localSettings.json` / `cache.abf` staged (error), cross-page `section` references (warning), `in_staged_scope` + `in_scope_reason` (`changed_file` | `caused_by_change`) on each finding: an error a change CAUSES in files it did not touch (visuals still binding a deleted/changed table's TMDL, bookmarks / interactions / links naming a deleted visual or page folder) counts as in scope via the finding's `cause`; the scope is built from the full `git diff --name-only --no-renames` set (deleted files included), `git.files` is only the first 200 of `git.files_total`; `by_code[code]` carries the most severe `severity` plus `by_severity`; a commit that passes with errors elsewhere says so in `verdict` (`clean for this change; N pre-existing error(s) elsewhere`). **Live test 2026-10-03 (R0201).** `load_blocking` is now limited to navigation / bookmark `visualLink` targets and the structural classes (unparseable JSON, broken binding, resourcePackage item without `type`, TOM parse failure); dangling bookmark visual refs, a missing-entity binding and a missing tooltip page are errors with `impact` `bookmark_partial` / `render_error` / `tooltip_missing`, not load-blocking (Desktop 2.157 opens such a report). The result adds `by_code` {count, severity, load_blocking, impact, repair_hint, examples} per finding code, `findings` capped at `max_findings` (default 10 with `response_format=concise`, else 200), `findings_total` and `findings_truncated`; examples per code: 1 concise, 3 otherwise. report-author CLI findings are warnings (`cli_severity` keeps the CLI's own severity) and their messages no longer repeat the absolute file path. At `level=commit` with a git scope only load-blockers or errors in the changed files gate (`error_in_scope`, `preexisting_error` counts). Not ported: the page-tab highlight drift check (`pbi-bookmarks/scripts/check_tab_selection.py`, still run by the g01 gate).

`aggregation_analysis` scores data visuals only (slicers of every type, text boxes, images, shapes, buttons, navigators and visual groups are skipped), lists pages in `pages.json` `pageOrder`, and applies `page_filter` (case-insensitive substring of the page name) **before** scoring, so totals and `optimization_score` cover only the matching pages. It writes only the requested artefact: `html` → `output_path` or `<repo>/exports/aggregation_analysis/<model>_Aggregation_<ts>.html`; `json` → `output_path`, else inline `data` only (no HTML side-file).

`dependency_html` embeds names script-safely (`</` and `<!--` escaped, model name HTML-escaped, clicks bound through escaped `data-*` attributes), so page/visual/measure names cannot inject markup.

`git_diff` populates `details: List[Dict]` — one entry per modified `.tmdl` file with `{file, measures: {added, modified, removed}, columns, relationships, roles}`. Modified = name appears on both `+` and `-` diff lines.

## III.13 `07_Page_Operations` — PBIP

File: `server/handlers/page_operations_handler.py` (+ `_internal/filter_helpers.py`, `core/pbip/page_operations_engine.py`, `core/pbip/filter_engine.py`). Page CRUD, filters, drillthrough, tooltips, interactions. Disk writes — reload via `17_Desktop_Bridge` afterwards.

All write ops of this tool run through `gate_and_wrap` (`server/handlers/_authoring_utils.py`, WP9): **`dry_run=true` previews ANY write** (nothing touches disk, `would_change` lists the planned file changes, classified READ); writes are locked per report (parallel calls cannot lose updates) and confined to the report folder; a failed write is `error_type: write_failed`, a partial write keeps `reload_required` + `files_modified`; a folder with several `.Report` folders is `ambiguous_report` (pass the `.pbip` / `.Report`); a cached `17_Desktop_Bridge` status showing the report open with unsaved changes is returned as `desktop_state` and, under STRICT, blocks the write unless `confirm_destructive=true`. Pages resolve by folder id, exact display name, or a UNIQUE prefix (never a substring; ambiguity = `ambiguous_page` + candidates).

Real PBIR shapes written (WP9): the page registry is `definition/pages/pages.json`; `displayOption` / `visibility` (`HiddenInViewMode`) / `type` (`Tooltip` / `Drillthrough`) are STRING enums; drillthrough writes `pageBinding{type: Drillthrough, parameters[{boundFilter}]}` + a `Categorical` filter with `howCreated: Drillthrough`; background = `objects.background`, wallpaper = `objects.outspace`; interactions = `visualInteractions` with types `NoFilter` / `DataFilter` / `HighlightFilter` (aliases `Filter`, `Highlight`, `None`).

| Operation | Purpose | Key args | Destructive |
|-----------|---------|----------|-------------|
| `list` | List pages (`is_hidden`, `page_type`, `page_binding`, filter / interaction counts with `summary_only=false`) | `pbip_path`, `summary_only?` | No |
| `create` | Create page, registered in `pages.json` | `pbip_path`, `page_name`, `width?` (1280), `height?` (720), `insert_after?` | Yes |
| `clone` | Clone page (staged; visual ids + `visualInteractions` remapped) | `pbip_path`, `source_page`, `new_display_name?`, `insert_after?` | Yes |
| `delete` | Delete page; `pageOrder` / `activePageName` / `landingPageName` repointed, bookmarks that captured it removed, navigator entries stripped, dangling nav/tooltip refs reported | `pbip_path`, `page_name` | Yes (CATASTROPHIC) |
| `rename` | Set a page's display name (S-33; id and folder stay; duplicate display names refused, unchanged = no-op) | `pbip_path`, `page_name`, `new_display_name` | Yes |
| `reorder` | Reorder pages | `pbip_path`, `page_order` (array of ids / names) | Yes |
| `resize` | Resize page | `pbip_path`, `page_name`, `width`, `height` | Yes |
| `set_display` | Display option | `pbip_path`, `page_name`, `display_option` (FitToPage\|FitToWidth\|ActualSize) | Yes |
| `set_background` | Page background | `pbip_path`, `page_name`, `color`, `transparency?` | Yes |
| `set_wallpaper` | Canvas wallpaper (`objects.outspace`) | `pbip_path`, `page_name`, `color?`, `transparency?` | Yes |
| `set_drillthrough` | Make a drillthrough target | `pbip_path`, `page_name`, `table`, `field`, `clear?`, `hidden?` | Yes |
| `set_tooltip` | Tooltip page (`enabled=false` reverts to a normal page) | `pbip_path`, `page_name`, `enabled?`, `width?`, `height?` | Yes |
| `hide` / `show` | Toggle visibility | `pbip_path`, `page_name` | Yes |
| `set_interaction` | One interaction (visuals by id or exact title; an ambiguous title is an error) | `pbip_path`, `page_name`, `source_visual`, `target_visual`, `interaction_type` | Yes |
| `bulk_set_interactions` | Several interactions | `pbip_path`, `page_name`, `interactions`, `replace_all?` | Yes |
| `list_interactions` | List interactions | `pbip_path`, `page_name` | No |
| `list_filters` | List filters with `polarity` (include\|exclude) + `is_inverted` | `pbip_path`, `page_name?`, `level` (report\|page\|visual\|all), `visual_name?` | No |
| `add_filter` | Add filter (visual level = top-level `filterConfig`) | `pbip_path`, `level`, `page_name`, `visual_name?`, `table`, `field`, `filter_type` (Categorical\|Advanced\|TopN), `values?`, `operator?` (Equal\|NotEqual\|GreaterThan(OrEqual)\|LessThan(OrEqual)\|IsBlank\|IsNotBlank), `polarity?`, `field_type?`, `value_type?`, `top_n?`, `top_direction?` (Top\|Bottom), `by_table?`, `by_field?` | Yes |
| `set_filter` | Replace a Categorical filter's values. **Exclude-mode filters** (inverted selection / `Not In`) need `polarity` (exclude keeps the mode, include switches to a positive list) or `confirm_widen=true`; `values: []` is refused (`empty_in_list`); a slicer's own filter is refused (`use_set_default_selection`) | `pbip_path`, `filter_name`, `values`, `level?`, `page_name?`, `polarity?`, `confirm_widen?`, `value_type?` | Yes |
| `clear_filters` | Remove filters (returns the removed definitions + their polarity) | `pbip_path`, `level`, `page_name?`, `visual_name?`, `filter_names?` | Yes |
| `hide_filter`/`unhide_filter` | Top-level `isHiddenInViewMode` | `pbip_path`, `filter_name`, `level?`, `page_name?` | Yes |
| `lock_filter`/`unlock_filter` | Top-level `isLockedInViewMode` | `pbip_path`, `filter_name`, `level?`, `page_name?` | Yes |

Literals: strings quoted with `'` doubled, `L` for Int64 columns / `D` for doubles (column type read from the bound model's TMDL via `definition.pbir`), never exponent notation. Advanced `ComparisonKind`: 0 Equal, 1 GreaterThan, 2 GreaterThanOrEqual, 3 LessThan, 4 LessThanOrEqual; NotEqual = `Not(Comparison 0)`. TopN = `In` over a `Top` subquery (`From[].Type = 2`, `OrderBy.Direction` 2 = Top / 1 = Bottom). `RelativeDate` is refused (no verified sample — author it in Desktop).

**`rename` (2026-10-03, S-33):** `07_Page_Operations operation=rename page_name=<id or name> new_display_name=<text>` changes the page's display name (the page id and folder stay); duplicate display names are refused, an unchanged name is a no-op, `dry_run=true` previews.

## III.14 `07_Visual_Operations` — PBIP

File: `server/handlers/visual_operations_handler.py` — thin dispatch shell routing to:

- `visual_layout_handler.py` — `list`, `update_position`, `replace_measure`, `sync_*`, `align`, `get_roles_for_type`.
- `visual_config_handler.py` — `update_visual_config`, `update_formatting`, `configure_slicer`, `add_field`, `remove_field`, `set_sort`, `set_action`, `inject_code`, `manage_visual_calcs`, `set_raw_property`, `get_raw_property`, `delete_raw_property`, `clear_default_selection`, `set_default_selection`, `audit_pane_collisions`, `bulk_rename_pane_names`.
- `visual_creation_handler.py` — `create`, `create_group`, `delete`, `hide`, `unhide`, `list_templates`, `get_template`. **Single source of truth** — `11_PBIP_Authoring` delegates `create_visual`/`delete_visual` here; `delete` is rated by `delete_severity` in BOTH tools (DESTRUCTIVE for one visual, CATASTROPHIC for several / a group with children).

Target resolution (`core/pbip/authoring/targets.py`, WP9 — H-16 / L-02 / L-05): `visual_name` / `visual_id` = visual folder id OR exact title (case-insensitive) in EVERY op; `page_name` / `page_id` = page id, exact display name or unique prefix (writes never substring-match). A write with no page / visual / title selector is refused (`target_required`) unless `match_all=true`; a write matching several visuals is refused (`multiple_matches`, with the match list) unless `allow_multiple=true`; single-visual ops (raw / sort / action / code) always need exactly one. Positions in results are page-absolute (group offsets summed).

All write ops of this tool run through `gate_and_wrap` (`server/handlers/_authoring_utils.py`, WP9): **`dry_run=true` previews ANY write** (nothing touches disk, `would_change` lists the planned file changes, classified READ); writes are locked per report (parallel calls cannot lose updates) and confined to the report folder; a failed write is `error_type: write_failed`, a partial write keeps `reload_required` + `files_modified`; a folder with several `.Report` folders is `ambiguous_report` (pass the `.pbip` / `.Report`); a cached `17_Desktop_Bridge` status showing the report open with unsaved changes is returned as `desktop_state` and, under STRICT, blocks the write unless `confirm_destructive=true`.

| Operation | Purpose | Key args | Destructive |
|-----------|---------|----------|-------------|
| `list` | List visuals | `pbip_path`, `page_name?`, `visual_type?`, `display_title?`, `include_hidden?`, `summary_only?`, `include?` (objects\|vCO\|fields\|parent_group\|all) | No |
| `create` | Create visual (roles default per type and are validated; `measures` / `columns` accept `{table, measure\|column, bucket?}` objects or `Table.Field` / `'Table'[Field]` strings; `parent_group` must exist; `position` is page-absolute unless `position_frame=group`) | `pbip_path`, `page_name`, `visual_type`, `title?`, `position?`, `measures?`, `columns?`, `parent_group?`, `position_frame?` | Yes |
| `create_group` | Group container (`visualGroup{displayName, groupMode: ScaleMode}`) | `pbip_path`, `page_name`, `group_name`, `position?` | Yes |
| `delete` | Delete visual(s) by EXACT id. A slicer / visual carrying filters returns its filter-layer dump and needs `confirm_delete_filter_layer=true` (prefer `hide`); children of a deleted group keep their page position when `delete_children=false`; bookmark `targetVisualNames` + interactions are scrubbed | `pbip_path`, `page_name`/`page_id`, `visual_id` or `visual_ids`, `delete_children?`, `confirm_delete_filter_layer?` | Yes |
| `hide` / `unhide` | Top-level `isHidden` (the non-destructive alternative to delete; filter layer kept) | `pbip_path`, `page_name`, `visual_name` or `visual_ids` | Yes |
| `update_position` | Move / resize (page-absolute, converted to group-local) | target selectors, `x?`, `y?`, `width?`, `height?`, `z?` | Yes |
| `update_visual_config` | `visual.objects.<config_type>[].properties` (never overwrites a selector-scoped entry) | targets, `config_type`, `property_name`, `property_value`, `selector_metadata?`, `value_type?`, `remove_property?` or `config_updates[]` | Yes |
| `update_formatting` | Chrome (`visual.visualContainerObjects`: title/subtitle/background/border/...) or rendering cards | targets, `formatting_target`, `formatting_properties` | Yes |
| `align` | Align / distribute in the page-absolute frame | `pbip_path`, `page_name`, `visual_names`, `alignment` or `direction` | Yes |
| `add_field` | Bind a field (`field_type=Hierarchy` + `hierarchy_level` supported) | targets, `table`, `field`, `bucket`, `field_type?`, `display_name?`, `hierarchy_level?` | Yes |
| `remove_field` | Remove a field + its sort entry and per-field formatting | targets, `table`, `field`, `bucket?` | Yes |
| `set_sort` | `visual.query.sortDefinition.sort[{field, direction}]` (Measure / Column from the binding or model; `sort_field_type` overrides) | single target, `sort_field` (Table.Field), `sort_direction`, `sort_field_type?` | Yes |
| `set_action` | `visual.visualContainerObjects.visualLink[0].properties{show, type, navigationSection\|drillthroughSection\|bookmark\|webUrl}`; page / bookmark targets accept names (resolved to ids). `DataFunction` writes `dataFunction{kind: ItemLocation, byReference{itemId, workspaceId}}`; `data_function_name` / `power_fx` have no verified key and come back as `unwritten_args` | single target, `action_type` (PageNavigation\|Bookmark\|Drillthrough\|WebUrl\|Back\|ClearAllSlicers\|DataFunction), `action_target?`, `data_function_id?`, `data_function_workspace_id?` | Yes |
| `inject_code` | Deneb → `visual.objects.vega[0].properties{jsonSpec, provider}`; Python / R → `objects.script` | single target, `code_type` (deneb\|python\|r), `code`, `provider?` | Yes |
| `manage_visual_calcs` | Visual calculations as `NativeVisualCalculation` projections in `queryState` (NEEDS LIVE CHECK) | single target, `sub_operation` (list\|add\|update\|delete), `calc_name?`, `expression?` | Yes (delete) |
| `configure_slicer` | Single-select "All" (inverted, nothing excluded). Needs `page_name` + a selector; several matches only preview unless `allow_multiple=true`; every slicer's prior selection / filterConfig is returned (`prior_state`) | `pbip_path`, `page_name`, `visual_name?` / `display_name?` / `entity?` / `property?`, `allow_multiple?`, `dry_run?` | Yes |
| `list_templates`/`get_template` | Visual templates | `template_id?` | No |
| `replace_measure` | Retarget a measure: projections + sortDefinition + visual filters + `selector.metadata` (per-measure formatting / CF) | targets, `source_entity`, `source_property`, `target_entity`, `target_property`, `new_display_name?` | Yes |
| `sync_visual` / `sync_column_widths` | Copy from a source visual to matching targets | `source_page`, `source_visual_name`, `target_pages?`, `target_display_title?`, `target_visual_type?`, `sync_position?`, `sync_children?` | Yes |
| `sync_formatting` | Copy `visual.objects` + `visual.visualContainerObjects` (content cards skipped); without `target_visual_name` needs `confirm_bulk_sync=true` | source/target page + visual, `formatting_types?`, `confirm_bulk_sync?` | Yes |
| `set_raw_property` | Direct JSON write. `value` is a JSON value (a JSON STRING of an object/array for an object/array node is parsed); a type change needs `force_type=true`; a missing intermediate with `create_missing=false` is an error; unclosed `[` = `invalid_json_path` | single target, `json_path`, `value`, `wrap_literal?`, `create_missing?`, `allow_null?`, `force_type?` | Yes |
| `get_raw_property` | Read raw JSON | single target, `json_path` | No |
| `delete_raw_property` | Delete a JSON node | single target, `json_path` | Yes (CATASTROPHIC) |
| `audit_pane_collisions` | Slicer title collisions per page | `pbip_path`, `page_name?`, `display_name_filter?`, `entity?`, `property?`, `include_no_title?` | No |
| `bulk_rename_pane_names` | Rename colliding / misleading slicer titles (parse-mutate-serialise; keyed by page + visual; `custom_overrides` keys `<page_id>/<visual_name>` — a bare id present on several pages is `ambiguous_override`) | `pbip_path`, `page_name?`, `strategy` (auto\|append_dim\|positional_only), `custom_overrides?` | Yes |
| `clear_default_selection` | Remove a slicer's persisted selection (general.filter + its filterConfig condition) | `pbip_path`, `page_name`, `visual_name?` or `display_name?` + `entity` + `property` | Yes |
| `set_default_selection` | Set slicer values in general.filter AND its filterConfig entry. On an INVERTED slicer `polarity` is required (exclude = values are excluded; include = positive list); empty `values` refused; literal type from the bound model | same + `values`, `polarity?`, `selection_mode?`, `value_type?` | Yes |
| `get_roles_for_type` | Valid data roles for a visual type | `visual_type` | No |

### Critical PBIR JSON distinction

Two formatting layers in `visual.json`:

- **`visual.visualContainerObjects`** (vCO) = CHROME (title text, background, border, header, padding). NOT visual content. Always under `visual.` — never top-level.
- **`visual.objects`** = INTERNAL rendering (chart axes, card value font, matrix header colors, slicer pill formatting). This is what makes the visual look good.

Setting vCO `title.fontSize = 28` does NOTHING to a cardVisual's huge number — that's `visual.objects.value[0].properties.fontSize`. **Rule: copy `visual.objects` verbatim from a working same-type visual.**

### `cardVisual` font compounding gotcha

`value.fontSize` AND `layout.calloutSize` multiply at render time. For 4 cards × 440 px wide: `value.fontSize = 14D`, `layout.calloutSize = 8D` → ~16pt render, fits. `18D + 14D` overflows a 130×440 card.

## III.15 `07_Bookmark_Operations` — PBIP

File: `server/handlers/bookmark_operations_handler.py` → `core/pbip/bookmark_engine.py`. `bookmarks/bookmarks.json` group `children` are plain STRING ids (legacy `{name}` objects are read and normalised); the index is updated before a file is removed; capture choices are Desktop's `options.suppressData` / `suppressDisplay` / `suppressActiveSection`; affected visuals = `options.targetVisualNames` + `applyOnlyToTargetVisuals`.

All write ops of this tool run through `gate_and_wrap` (`server/handlers/_authoring_utils.py`, WP9): **`dry_run=true` previews ANY write** (nothing touches disk, `would_change` lists the planned file changes, classified READ); writes are locked per report (parallel calls cannot lose updates) and confined to the report folder; a failed write is `error_type: write_failed`, a partial write keeps `reload_required` + `files_modified`; a folder with several `.Report` folders is `ambiguous_report` (pass the `.pbip` / `.Report`); a cached `17_Desktop_Bridge` status showing the report open with unsaved changes is returned as `desktop_state` and, under STRICT, blocks the write unless `confirm_destructive=true`.

| Operation | Purpose | Key args | Destructive |
|-----------|---------|----------|-------------|
| `list` / `get` | List / read bookmarks | `pbip_path`, `bookmark_id?` (id or display name) | No |
| `create` | Create bookmark (registered as an index item) | `pbip_path`, `display_name`, `page_name?`, `capture_data?`, `capture_display?`, `capture_current_page?`, `visual_ids?`, `all_visuals?` | Yes |
| `rename` / `update` | Rename / update capture flags | `pbip_path`, `bookmark_id`, `new_name` / `display_name?`, `capture_*?` | Yes |
| `delete` | Delete bookmark (index first, then file; group children cleaned) | `pbip_path`, `bookmark_id` | Yes |
| `set_capture` | Capture settings (`suppress*` keys) | `pbip_path`, `bookmark_id`, `capture_data?`, `capture_display?`, `capture_current_page?` | Yes |
| `set_affected_visuals` | `targetVisualNames` scope | `pbip_path`, `bookmark_id`, `visual_ids?`, `all_visuals?` | Yes |
| `create_group` / `update_group` | Group with string children (a bookmark belongs to one group) | `pbip_path`, `display_name` / `group_id`, `bookmark_ids` | Yes |
| `delete_group` | Remove group (children kept as top-level items unless `delete_children=true`) | `pbip_path`, `group_id`, `delete_children?` | Yes |
| `analyze` | Interactive HTML bookmark analysis | `pbip_path`, `output_path?`, `auto_open?` | No |

## III.16 `07_Theme_Operations` — PBIP

File: `server/handlers/theme_operations_handler.py` → `core/pbip/theme_engine.py`, `core/pbip/conditional_formatting_engine.py`.

All write ops of this tool run through `gate_and_wrap` (`server/handlers/_authoring_utils.py`, WP9): **`dry_run=true` previews ANY write** (nothing touches disk, `would_change` lists the planned file changes, classified READ); writes are locked per report (parallel calls cannot lose updates) and confined to the report folder; a failed write is `error_type: write_failed`, a partial write keeps `reload_required` + `files_modified`; a folder with several `.Report` folders is `ambiguous_report` (pass the `.pbip` / `.Report`); a cached `17_Desktop_Bridge` status showing the report open with unsaved changes is returned as `desktop_state` and, under STRICT, blocks the write unless `confirm_destructive=true`.

Theme `visualStyles.<type>.*.<card>` entries are ARRAYS of plain values (`[{"fontSize": 14}]`); `set_formatting` accepts `{card: {prop: v}}` or `{card: [{prop: v}]}`, `push_visual` unwraps literals (`14D` → 14, `'x'` → x, literal colours → `{solid: {color}}`) and skips CF / measure-driven / content properties (`skipped_non_literal`).

| Operation | Purpose | Key args | Destructive |
|-----------|---------|----------|-------------|
| `analyze_compliance` | Audit theme compliance (HTML) | `pbip_path`, `auto_open?`, `output_path?` | No |
| `get_theme` / `export_theme` | Read / export theme JSON | `pbip_path`, `output_path?` | No |
| `set_colors` | Update theme colors | `pbip_path`, `colors` | Yes (CATASTROPHIC) |
| `set_formatting` | Visual-type formatting defaults | `pbip_path`, `visual_type_target`, `formatting` | Yes |
| `push_visual` | Push one visual's literal formatting as its type's defaults | `pbip_path`, `page_name`, `visual_name` | Yes |
| `list_text_classes` / `set_font` | Text classes | `pbip_path`, `text_class`, `font_family?`, `font_size?`, `color?`, `bold?` | Yes (set) |
| `apply_theme` / `import_theme` | Replace the custom theme (object / file on disk) | `pbip_path`, `theme` / `source`, `theme_name?` | Yes (CATASTROPHIC) |
| `clear_visual_overrides` | Remove plain formatting overrides so the theme applies. NEVER touches content cards (`general` slicer selection / textbox paragraphs, `vega`, `data`, `selection`, images, button text), selector-scoped entries, and (`keep_cf=true`) conditional formatting; returns `kept` counts | `pbip_path`, `scope?` (all\|page:<page>\|visual:<page>/<visual>), `keep_cf?` | Yes (CATASTROPHIC) |
| `list_cf` | List CF rules | `pbip_path`, `page_name`, `visual_name`, `container?` | No |
| `add_cf` | Add CF in the real shapes: `color_scale` → `solid.color.expr.FillRule{Input, FillRule.linearGradient2\|3{min, mid?, max}}` with Literal colours; `rules` → `Conditional.Cases[{Condition, Value}]`. Placed at `selector` / `selector_metadata` (`{data: [dataViewWildcard], metadata}`); `data_bars` / `icons` refused (no verified shape — author in Desktop, then `copy_cf`) | `pbip_path`, `page_name`, `visual_name`, `container`, `property_name`, `rule_type` (color_scale\|rules), `cf_config` ({table, field, min_color, max_color, mid_color?, rules[]}), `selector?`, `selector_metadata?` | Yes |
| `remove_cf` | Remove a CF property | same identification + `selector_metadata?` | Yes |
| `copy_cf` | Copy CF between visuals | source/target page + visual, `container`, `property_name`, `selector_metadata?` | Yes |

## III.17 `08_Documentation_Word` — DOCS

File: `server/handlers/documentation_handler.py`. Word `.docx` documentation from live model.

| `mode` | Purpose | Required args |
|--------|---------|---------------|
| `generate` | Create new doc | `output_path?` (a `.docx` file path, or a folder → timestamped name; default `<repo>/exports/docs`), `include_hidden?` |
| `update` | Refresh an existing doc in place with a change log | `input_path` (the `.docx`, or its snapshot `.json`), `output_path?` (default = `input_path`), `snapshot_path?`, `include_hidden?` |

Sections: tables, columns, measures, relationships, calculation groups/items, RLS roles + filters, Power Query partitions/shared expressions, UDFs, field parameters, and the real BPA findings (same engine as `06 full`). `generate` writes `<stem>.snapshot.json` next to the document; `update` finds that snapshot from `input_path` (or `snapshot_path`), diffs the live model against it, adds a change log and rewrites the document and snapshot. Text is XML-sanitised (control characters stripped); a document locked by Word returns a structured error instead of a partial write.

## III.18 `09_Debug_Operations` — DEBUG

File: `server/handlers/debug_handler.py`. **The visual debugger** combining PBIP layout with live model data. **ALWAYS use this (operation `visual` or `run_dax`) — never `07_Report_Operations` for debugging.** PBIP path persists across calls (set once via `set_path`).

| Operation | Purpose | Key args |
|-----------|---------|----------|
| `visual` | Debug specific visual | `page_name?`, `visual_id?` OR `visual_name?`, `measure_name?`, `execute_query?` (default `true`), `trace?`, `clear_cache?`, `include_slicers?`, `filters?`, `skip_auto_filters?`, `reference_query?`, `pa_export_path?`, `from_trace?`, `use_reference_query?` (default `true`), `roles?`, `effective_user_name?` |
| `compare` (`mode=live_swap`, F-08) | A/B on the visual's REAL measure chain: `checkpoint_create` -> write the candidate (`original_measure` + `optimized_expression` [+ `table_name`], or `patch=[{table_name?, measure_name, expression}]`) into the live model -> cold traced runs of the visual query (`page_name` + `visual_id`/`visual_name`, or `reference_query`) interleaved with the baseline (`runs` 1-10, order A B \| B A ..., one journaled write per side change) -> rows compared at the visual's grain (`tolerance`) -> ALWAYS restore (`checkpoint_restore` preview first, pre-image write-back as fallback) and verify: `restore_verified` / `restore_method`; a restore that cannot be proven = `restore_not_verified` naming the checkpoint. Verdict `faster` \| `slower` \| `noise` \| `values_differ` \| `inconclusive` with `delta_ms`, `delta_pct`, `min_detectable_effect_pct` (max of both spreads and 5%), `baseline` / `candidate` {`best_ms`, `spread_pct`, `runs_ms`, stats}. `dry_run=true` plans only (a read). Classified DESTRUCTIVE otherwise (`confirm_destructive` under STRICT); live Desktop only (XMLA / offline refused); does not refuse on `hasUnsavedChanges`. `core/debug/live_swap.py`, `server/handlers/_debug/live_swap.py`; LIVE CHECK still needed | `mode=live_swap`, candidate, visual or `reference_query`, `runs?`, `tolerance?`, `dry_run?`. A candidate body that references the measure it replaces is refused with `circular_candidate` before any write (live test 2026-10-03) |
| `compare` | Original vs optimized measure | `original_measure`, `optimized_expression`, `page_name?` + `visual_id?` (visual filter context), `filters?`, `runs?` (default 3, 1–20), `warmup?` (default `true`), `trace?` (time each run as a cold traced run), `tolerance?` (0.001), `equivalence_check?`, `equivalence_grain?`, `include_slicers?` |
| `drill` | Drill to detail | `page_name`, `visual_id`, `fact_table`, `filters?`, `limit?` (default 100) |
| `analyze` | Analyze measure DAX | `measure_name` OR `expression`, `table_name?`, `page_name?` + `visual_id?` (filter context), `execute_measure?` |
| `debug_variable` | Debug intermediate variable value | `measure_name`, `variable_name`, `table_name?`, `page_name?` + `visual_id?`, `filters?`, `max_rows?` (default 100) |
| `step_variables` | Step through variable definitions | `measure_name`, `table_name?`, `page_name?` + `visual_id?`, `filters?`, `max_rows?` |
| `run_dax` | Raw DAX execution - a thin alias of `04_Run_DAX` (same executor; `executed_via`). trace=true returns the rows of the SAME traced run under `result` and the timings (+ `trace_id`) under `se_fe_trace`; with `roles` the timings are traced without impersonation (`trace_identity_note`) | `query`, `trace?`, `clear_cache?`, `top_n?` (default 1000), `bypass_cache?` (default `true`), `roles?` |
| `optimize` | Recommend optimizations from timing | `measure_name`, then ONE timing source: `trace_id` (from `visual`/`run_dax` `trace=true`), or explicit `total_ms`, `fe_ms`, `se_ms`, `fe_pct`, `se_pct`, `se_queries` (+ `se_cpu_ms`, `se_cache_hits`, `se_parallelism`, `se_events`); optional `cache_comparison`, `per_measure_trace` |
| `audit` | Full-page visual audit | `page_name?` (array for batch; omit to discover), `include_data?`, `skip_types?`, `compact?` |
| `debug_loop` | Closed-loop debug with iteration | `page_name`, `visual_id`, `measure`, `expected`, `max_iterations?` (default 5), `fix_strategy` (ask_user\|live_tom\|tmdl_edit\|dry_run), `take_screenshot?`, `screenshot_dir?`, `wait_timeout_seconds?` |
| `set_path` | Set PBIP folder path | `pbip_path` |
| `status` | Get debugger status | — |

**`se_analysis` block** (attached by `_build_trace_response` whenever a traced call produces SE events; also returned by `04_Run_DAX mode='trace'` since v7.14.2): `callbacks`, `datacache`, `timing_distribution`, `fusion_opportunities`, `join_analysis`, `row_ratio_analysis`, and — **new in v7.14.2** — `with_clauses`: `List[{line, expressions: [{expr_name, callback_type, arguments}]}]`, the parsed xmSQL `WITH $Expr := ( Callback(...) )` declarations per SE event. Only events that actually declare a WITH expression are listed, so it is `[]` for a callback-free trace; capped at 10 events × 10 expressions.

**Reconstruction fidelity (2026-09-22 review, F-02…F-06, F-12, F-19).** The visual query is
rebuilt the way Power BI emits it: `TREATAS` only for plain include lists; NOT-IN, inverted
selections (`isInvertedSelectionMode` on the filter or the visual, = an EXCLUDE list), comparison,
between, blank, TopN (`In.Table` subquery), RelativeDate and And/Or filters become
`FILTER(KEEPFILTERS(VALUES(col)), <predicate>)`; visual-level filters are read from the TOP-LEVEL
`filterConfig`; filters on the same column from different levels are INTERSECTED (separate
arguments), not unioned; measure (value) filters keep the visual's grouping
(`__ValueFilterDM<n>`); every `queryState` role is scanned (`cardVisual` `Data`, combo `Y2`,
Deneb `dataset`); tables get one `ROLLUPGROUP` grand total, matrices one subtotal flag per row
level, and `sortDefinition` drives the TOPN window. Interactions set to NoFilter are honoured.
Anything that could not be translated is listed in `unsupported_filters` and the response says
`fidelity: "partial"` — nothing is dropped silently.

**Manual `filters`** are applied VERBATIM as `KEEPFILTERS(<dax>)` around the visual query (any
auto filter on the same column is overridden and reported in `auto_filters_overridden`); the
rebuilt query keeps the rollup, row limit, IGNORE companions and sort order.

**Desktop's own query as ground truth (F-39).** Pass `reference_query` (Performance Analyzer →
"Copy query") or `pa_export_path` (a `PowerBIPerformanceData.json` export; the visual's QueryText
is read by visual id). The response gains `reconstruction_check` (group-by / filter columns /
negations / TREATAS values / fields that differ) and, with `use_reference_query` (default true,
ignored when manual `filters` are given), the Desktop query is what executes
(`executed_query_source: "reference"`).

**`from_trace=true` (F-02).** Instead of pasting a query, take the best-matching query the RUNNING
`13_Query_Plan trace_start` buffer captured for this visual (the trace keeps running; drive the
visual / switch to its page first). Matching is `core/debug/trace_visual_match.py`: the engine's
ApplicationContext VisualId when the event carries one (`confidence: exact`), else query shape
(group-by columns, measures, filter tables vs the reconstructions of the SAME page's visuals);
only `exact` / `high` / `medium` matches are used, the latest capture wins, and a tie between
different visuals is refused. The executed query is the captured one: `fidelity: "captured"`,
`executed_query_source: "trace"`, `trace_capture {seq, request_id, total_ms, match, captures_for_visual}`;
`reconstruction_check` still lists where our reconstruction diverged. With no trace / no confident
capture the reconstruction runs unchanged and `reconstruction_check.error` says why. An explicit
`reference_query` / `pa_export_path` wins over `from_trace`.

**Cache + timing honesty (F-01, F-07, F-22, F-26).** Every 09_* query bypasses the executor's
result cache (`run_dax` opt-out: `bypass_cache=false`); responses carry a `cache` provenance block.
`compare` runs a warm-up, then `runs` INTERLEAVED rounds (A B, B A, …) and keeps the fastest per
side (`comparison.timing`). Traced `visual` / `run_dax` calls return a `trace_id`; `optimize`
uses it (or the last trace only when it provably timed THIS measure and nothing else was traced
meanwhile), otherwise it traces `ROW([measure])` itself (`timing_source: "fresh_trace_row"`).
`step_variables` / `debug_variable` evaluate each VAR chain INSIDE the filter context
(`CALCULATE(VAR … RETURN <var>, filters)`); VARs are parsed with the DAX lexer (nested VAR
blocks and strings are not top-level); `execution_time_ms` is cumulative, `delta_ms` per step.

**Freshness.** The builder cache is keyed on the report path and re-checks the definition folder
signature (newest mtime + file count) at most every 2 s; PBIR writes through `pbip_utils` and
`debug_loop` fixes bust it (any path form: `.pbip`, `.Report`, a file inside). `pbip_state`
(verbose) is informational — unsaved Desktop selections are invisible on disk; check
`17_Desktop_Bridge status` (`hasUnsavedChanges`) or pass current values via `filters`.

`debug_loop` with `fix_strategy=live_tom` hands its gate authorisation (the loop's own
destructive-op gate: `confirm_destructive`, or an elicitation-confirmed STRICT block) to the
gated `MeasureApplier`, so a confirmed loop is not re-blocked per iteration.

Debug handler returns `"success": True` when called directly. Waterfall charts stored as `columnChart` in PBIP — detect by WF1-WF5 measure name pattern.

## III.19 `09_Validate` — DEBUG

File: `server/handlers/debug_handler.py`. Cross-visual + expected-value + filter-permutation validation.

| Operation | Purpose | Required args |
|-----------|---------|---------------|
| `cross_visual` | Check measure consistency across visuals | `measure_name`, `pages?` (legacy `page_names`), `tolerance?` |
| `expected_value` | Check a visual's value against an expected value | `page_name`, `visual_id?` OR `visual_name?`, `expected_value`, `filters?`, `tolerance?` |
| `filter_permutation` | Re-evaluate a visual per slicer-value combination | `page_name`, `visual_id?` OR `visual_name?`, `max_permutations?` (20) |
| `capture_baseline` | Snapshot measure scalars to a JSON baseline file | `measures`, `baseline_path` |
| `compare_baseline` | Re-run + diff measures vs the baseline within tolerance (regression guard around model edits / `15_DAX_Refactor`) | `baseline_path`, `tolerance?` |
| `test_put` | **7.16 (TEST-01)** Add / replace one test in a JSON suite (file created when missing) | `suite_path`, `test`, `replace?` (true) |
| `test_remove` | Remove a test by id | `suite_path`, `test_id` |
| `test_list` | List a suite's tests + last-run record | `suite_path` |
| `test_run` | Run the suite (read-only); per-test pass/fail/error + reason | `suite_path`, `ids?`, `tags?`, `record?` (true) |
| `test_export` | Run + write JUnit XML or JSON | `suite_path`, `output_path`, `output_format?` (`junit`\|`json`) |
| `tieout` | **F-09** Additivity / tie-out audit (`core/validation/tieout.py`). `mode=visual` (default): run a matrix / table visual's REAL query (`visual_query_builder`, its per-type TOPN window lifted to `max_rows`, default 5000) with the `IsGrandTotalRowTotal` / `IsDM<n>Total` subtotal rows and check parent == sum(children) at every level within `tolerance` (absolute, default 0.01) / `relative_tolerance`; returns `ties`, `first_break` {level, level_name, measure, parent_key, parent_value, children_sum, difference}, `breaks`, `per_measure` (+`cause_hint`: ratio / distinct count / average / semi-additive from the measure expression), `levels_checked` / `levels_skipped`, `fidelity`. `non_additive` measures are skipped and listed. No checkable level or a row-cap hit → `ties: null` + `inconclusive` (never green). `mode=page_vs_page`: the same measure's grand total on two pages (visuals paired by title, or the single visual of each page). `mode=A_vs_B`: two measures / expressions that must agree, at the grand total or per `by` grain (`filters` applied with CALCULATE). Semi-additive measures break additivity legitimately: pass them in `non_additive` (measure annotations are not read). | visual: `page_name`, `visual_id` or `visual_name`, `measures?`, `non_additive?`, `tolerance?`, `relative_tolerance?`, `max_rows?`; page_vs_page: `page_a`, `page_b`, `measure_name`; A_vs_B: `measure_a`, `measure_b`, `by?`, `filters?`. Measures whose returned values are all text go to `skipped_non_numeric`; a parent with no child row (all children BLANK, or a formula line the next level does not break down) is not a break and is counted in `per_measure.<m>.parents_without_children`; when every break comes from a measure whose expression marks it non-additive, `breaks_only_in_non_additive_by_expression: true` + `non_additive_by_expression` (`ties` stays false - pass them in `non_additive`) (live test 2026-10-03) |
| `test_generate` | **F-10** Generate regression tests into a suite (`core/validation/test_generator.py`). `source=page` (`page_name`): per data visual a `dax_assertion` snapshot of the reconstructed query (`expect.rows` up to `snapshot_rows` (50) rows else `row_count` + `columns`; `fidelity` + notes + the slicer base recorded), per matrix / table visual a `tieout` (`include_tieout`, default true), per slicer with a saved selection a "selected members still exist" `dax_assertion`, per card / KPI measure a grand-total `measure_assertion`, optional `performance_budget` per heavy visual (`include_perf`: median of `runs` x `headroom` (1.5), only above `heavy_ms` (500)); `source=report`: every page + one `rls_audit` per model role; `source=roles`: `rls_audit` per role (`roles?`, `as_user?`); `source=measures` (`measures`): grand-total assertions. Ids are stable (`snap.<page>.<visual>`, `tie.…`, `slicer.…`, `perf.…`, `kpi.<measure>`, `rls.<role>`), so re-generating updates. Tests carry `generated: true`, `generator`, `provenance` (`snapshot` = today's numbers, bugs included; `invariant` / `audit` = a property), `source`. **Preview by default** (full `tests` + `diff` {added, unchanged, changed (old/new expectation), hand_written_conflicts, stale}); `write=true` + `suite_path` persists in ONE atomic write (`test_suites.put_tests`, same validation as `test_put`); a changed expectation or a hand-written test with the same id is held back (`held_for_review`) unless `accept_changes=true`; stale generated tests are reported, never deleted. Read when previewing, WRITE with `write=true` (`op_classification`). | `source`, `suite_path?` (required with `write`), `write?`, `accept_changes?`, `page_name?`, `measures?`, `roles?`, `as_user?`, `include_tieout?`, `include_perf?`, `runs?`, `headroom?`, `heavy_ms?`, `snapshot_rows?`, `max_visuals?`, `tolerance?`. PBIR literals in slicer values are decoded before use (live test 2026-10-03) |
| `equivalence` | **F-14** Two DAX expressions over a deterministic context grid (`core/validation/equivalence_grid.py`). Both sides are `DEFINE MEASURE`d on `host_table` INSIDE the query (`__eqA` / `__eqB`; the model is never written) and compared cell by cell within `tolerance` (absolute, default 1e-6; BLANK vs 0 differs). Grid = the grand total, each explicit `filters` scenario, every `grid_columns` column (default: the `page_name`'s slicer columns) with its subtotal row, pairs among the first three columns, and `grains` (`year`\|`quarter`\|`month`) of `date_column`; cells come from `SUMMARIZECOLUMNS`, no literal is built from data. Capped by `max_cells` (200, max 2000) split per group. Returns `equivalent` (true\|false\|null = nothing evaluated), `cells_compared`, `cells_differing`, `differing_cells`, `hash_a` / `hash_b`, `coverage`, `errors` (an error on ONE side counts as a difference), `proof: "sampled_grid"` (not a formal proof). | `expression_a` + `expression_b` OR `measure_name` + `candidate_expression`; `grid_columns?`, `page_name?`, `date_column?`, `grains?`, `filters?`, `max_cells?`, `host_table?`, `tolerance?`. A measure name is always bracketed; the note says when no value could be compared; a grid group that returned no non-blank cell is listed in `empty_groups` and named in the note (live test 2026-10-03) |

Suites (`core/validation/test_suites.py`): test types `measure_assertion`, `dax_assertion`, `regression_snapshot`, `rls_validation`, `performance_budget`, `metadata_compliance` (rules `no_measure_errors`, `measures_exist`, `measure_name_regex`, `descriptions_required`), plus **`rls_audit`** (`roles?`, `as_user?`, `fail_on?` (default `critical`), `allow?` codes — fails on findings at or above `fail_on`) and **`tieout`** (`mode`, `page_name` + `visual_id`, …; passes only on a definite tie). Both reuse the engines of `04_Query_Operations rls_audit` / `09_Validate tieout` through `run_suite(rls_audit=…, tieout=…)`. Queries run through `validate_and_execute_dax(bypass_cache=True, max_rows=10000)`; RLS tests through `run_as_role`; snapshots reuse `compare_baseline`. `performance_budget` = median wall clock of `runs` (default 3) — a budget, not an SE/FE measurement.

Baseline ops live in `server/handlers/_debug/tools.py` and talk to the executor directly (no visual layer): `capture_baseline` evaluates `EVALUATE ROW("v",[M])` per measure (names escaped; `Table[Measure]` accepted) and ALWAYS bypasses the result cache (F-01: a compare inside the cache TTL used to replay the baseline); the file (v2) stores `results`, `errors`, `captured_at`, `model_generation`. `compare_baseline` reports `{changed, unchanged, differences, cache_hits (always empty), baseline_captured_at}`, iterates the baseline's `measures` (a measure that errored at capture is reported), and treats NaN/BLANK vs a number as a change.

The value of `cross_visual` / `expected_value` / `filter_permutation` is the MEASURE evaluated over what the visual shows (`ROW("Value", CALCULATE([M], <visual filters>))`, value filters kept over the visual's grouping) — not the first column of the visual query (a group-by label, or BLANK on the grand-total row, F-15). `filter_permutation` REPLACES the slicer's own selection with the permuted value and types the literal from the model column (`2024`, not `"2024"`); every other filter stays applied. Responses carry `fidelity` / `unsupported_filters` / `partial_fidelity_visuals`.

**Suites (2026-10-03 review, wave 1):** `test_put` / `test_remove` are WRITE ops (they edit the suite JSON; the model is never written). `suite_path` and `output_path` must be under an allowed root (PBIP folder, `exports/`, `output/`, `logs/`). `test_run` stores the last run in `<suite>.last-run.json` (the suite file is not rewritten). Zero executed tests (unknown ids/tags, empty suite, all skipped) → `no_tests_ran`, `exit_code: 1`, `success: false`; a missing suite → `not_found`. `ids` / `tags` accept a list, a string or a comma-separated string. `expect` keys are strict (unknown or empty → error); row cells compare numerically within the test tolerance (`1` == `1.0`, NaN == NaN) and the message names the first differing row; a truncated result cannot assert an exact row count. JUnit is built with `xml.etree` (always valid XML).

## III.20 `09_Profile` — DEBUG

File: `server/handlers/debug_handler.py`. Page-level perf profiling.

| Operation | Purpose | Required args |
|-----------|---------|---------------|
| `page` | Profile all visuals on page | `page_name`, `iterations?` (default 3), `include_slicers?`, `trace?`, `clear_cache?`, `simulate_concurrency?` (default 6), `per_visual_limit?`, `schedule?` (`lpt`\|`layout`, trace only), `warm_pass?` (trace only) |
| `filter_matrix` | Profile visual with filter matrix | `page_name`, `visual_id?` OR `visual_name?`, `filter_columns?` (auto-detect), `max_combinations?` (default 15), `iterations?` (runs per combination, fastest kept, default 1), `include_slicers?` |
| `decompose` | Decompose measure by dimension | `visual_id?` OR `visual_name?`, `dimension`, `top_n?` |
| `contribution` | Contribution analysis | `visual_id?` OR `visual_name?`, `dimension` |
| `trend` | Trend analysis | `page_name`, `date_column`, `granularity` (day\|week\|month\|quarter\|year) |
| `root_cause` | Root cause analysis | `page_name`, `visual_id`, `baseline_filters`, `comparison_filters` |
| `export` | Export profiling results | `page_name`, `output_format` (markdown\|json; legacy `format`) |
| `perf_analyzer_import` | Parse a `PowerBIPerformanceData.json` exported from Desktop's Performance Analyzer pane | `file_path`, `trace?`, `pbip_path?` |
| `regress` | **F-18.** Compare the latest recorded run with a baseline from the perf history; REFUSES a verdict inside the noise band | `baseline` (a run `label`, a git sha prefix or `YYYY-MM-DD`), `candidate?` (default: the latest run), `page_name?`, `project?` |

**Perf history (F-18).** `page record=true` (+ `label?`, `project?`) appends one run to
`output/perf/<project>.jsonl` (project = the PBIP folder name): per visual AND per measure-in-visual
(`attribution: visual_total`, a measure's cost is not separable) the raw `runs_ms`, `n`, `median_ms`,
`mad_ms`, `sigma_ms`, min/max, `git_sha` / `git_dirty` (read-only `git rev-parse` / `status` of the PBIP
repo), `model_generation`, `session_id`, `warmup_runs` (1 for `trace=true`, else 0) and the environment
(cache state, timing source, platform, background CPU when psutil is present); the result gains
`recorded {run_id, history_file, records_written, warnings}`. The newest `perf_history.max_records` records
(default 5000) are kept per file. `record` is refused in `read_only_mode`. A cancelled profile is never
recorded. `regress` ports `~/.claude/scripts/pbi-telemetry/benchmark.py`: >= 6 measured runs per side
(else `insufficient_data`), a verdict only when `noise / |delta| <= 0.15` where noise = max(sigma, 1.4826 x
MAD, IQR / 1.349), and a halved bar (0.075) when baseline and candidate come from different server sessions
(`session_match: false`). Result: `overall` (`regressed` \| `improved` \| `inconclusive` \|
`insufficient_data`), per-key `verdict` / `delta_ms` / `delta_pct` / `reason`, `only_in_baseline` /
`only_in_candidate`, caveats (never interleaved; model generation differs). Profile with `iterations >= 6`
and `trace=true` for a verdict-grade run. `page` / `filter_matrix` also report per-visual / per-combination
progress and, when the call is cancelled, return the finished part with `cancelled: true, partial: true`.

**Performance Analyzer parity.** `page` with `trace=true` routes every visual query through
`NativeTraceRunner` (one trace session for the whole page) and returns the pane-shaped table:
`visual, type, dax_ms, other_wait_est_ms, total_est_ms, se_pct, callbacks, peak_mem_kb`, plus
`page_estimate` (`est_page_ms`, `critical_path_ms`, `total_wait_ms`, `what_if`) and `skipped[]`.
Baseline protocol: one warm-up run is discarded, then `iterations` measured runs, fastest kept.
`other_wait_est_ms` is an **estimate** from a K-slot queue model (`simulate_concurrency`, default
6) — Power BI publishes no concurrency limit. For the **measured** Other, export the pane from
Desktop and read it with `perf_analyzer_import`, which recomputes it as the lifecycle residual
(`lifecycle − DAX − DirectQuery − Render − Parameters`) exactly as Power BI does. Desktop's
export is a FLAT `events[]` list linked by `id`/`parentId` with ISO timestamps; it is normalised
to the nested shape before parsing (F-31 — needs one real export to confirm).

**Traced page runs (F-14, F-33, F-35).** All passes run in ONE trace session and the batch
stops at the tool budget (tool timeout − 15 s) or on cancellation — no background ClearCache +
queries after a timeout; `runtime` reports `estimated_s`, `budget_s`, `runs_not_executed`, and a
visual with no completed run is skipped as `time_budget_exhausted`. `schedule="lpt"` (default,
longest first) is an optimistic LOWER BOUND on page time; `schedule="layout"` dispatches in page
layout order (tabOrder, then position) like a real page load. `warm_pass=true` adds one
back-to-back page pass with no ClearCache between visuals (`warm_page_pass_ms` per visual).
Non-traced `page` / `filter_matrix` / `trend` / `decompose` bypass the result cache, so
iterations 2..N are real runs. `trend` returns one row per PERIOD (GROUPBY over date ranges — weeks
never merge across years) in the visual's DATA filter context.

## III.21 `09_Document` — DEBUG

File: `server/handlers/debug_handler.py`. Generate docs from PBIP + live model.

| Operation | Purpose | Required args |
|-----------|---------|---------------|
| `page` | Document a page | `page_name`, `lightweight?` (default `true`), `include_ui_elements?`, `narrate?` (uses MCP sampling) |
| `report` | Document entire report | `lightweight?`, `include_ui_elements?`, `narrate?` |
| `measure_lineage` | Trace measure dependencies | `measure_name`, `narrate?` |
| `filter_lineage` | Trace filter flow | `page_name`, `narrate?` |

## III.22 `10_Show_User_Guide` — CORE

File: `server/handlers/user_guide_handler.py`. Returns the user guide generated from the live registry.

| Arg | Effect |
|-----|--------|
| (none) / `topic="index"` | Compact index: quick start, one line per tool (title + operations), workflows (~8K chars) |
| `tool="<name>"` | That tool's FULL parameter list (incl. the rare-op args `tools/list` omits, marked; long-form `x-doc` text), its `guide_notes` (the long description), `02_Model_Operations`' operations per object_type, + its Part III section |
| `tool="<name>", operation="<op>"` | Only the arguments that concern that operation (selectors, required args, args whose docs name the op); unknown op -> `invalid_operation` + `valid_values` |
| `topic="all"` | The full guide (every tool's parameters, ~70K chars) |
| `topic="workflows"` / `"tips"` | Just that section |

## III.23 `11_PBIP_Authoring` — PBIP

File: `server/handlers/authoring_handler.py`. Page/visual create + clone + delete + bulk direct-write visual creation.

All write ops of this tool run through `gate_and_wrap` (`server/handlers/_authoring_utils.py`, WP9): **`dry_run=true` previews ANY write** (nothing touches disk, `would_change` lists the planned file changes, classified READ); writes are locked per report (parallel calls cannot lose updates) and confined to the report folder; a failed write is `error_type: write_failed`, a partial write keeps `reload_required` + `files_modified`; a folder with several `.Report` folders is `ambiguous_report` (pass the `.pbip` / `.Report`); a cached `17_Desktop_Bridge` status showing the report open with unsaved changes is returned as `desktop_state` and, under STRICT, blocks the write unless `confirm_destructive=true`.

| Operation | Purpose | Key args | Destructive |
|-----------|---------|----------|-------------|
| `clone_page` | Clone page (staged next to the definition folder, moved in only when complete; visual ids + `visualInteractions` remapped; registered in `pages/pages.json`) | `pbip_path`, `source_page`, `new_display_name?`, `insert_after?` | Yes |
| `clone_report` | Copy a whole `.Report` (page / visual ids KEPT so navigation / bookmarks stay valid; new `.platform` logicalId; `definition.pbir` byPath re-based) | `pbip_path`, `target_path`, `new_report_name?` | Yes (CATASTROPHIC) |
| `clone_visual` | **F-16.** Copy ONE visual or group (same or another page) with fresh ids (`core/pbip/authoring/visual_clone.py`): visual name, `parentGroupName` links inside a cloned group, `filterConfig` filter names regenerated and ids hidden inside base64 blobs of the COPIED folder (`visual.json` + `mobile.json`) remapped; parent handling by `group_mode` (`keep` same page \| `clone_group` clones the empty ancestor groups onto the target page \| `top_level` drops the parent; `auto` = keep on the same page else top_level) or `parent_group` (re-parent under an existing target-page group); group-local and page-absolute coordinates converted; z/tabOrder go on top of the target page. `inherit_interactions=true` copies every `visualInteractions` entry of the source in BOTH directions (cross-page: pass `interaction_map {source visual id: target visual id}`); without it the result says `interactions_not_inherited: n` (`feedback_cloned_visuals_lose_interactions`: a cloned slicer then filters everything). `copy_bookmark_refs=true` adds the clone next to the source in every bookmark that captured it (`visualContainers`, `visualContainerGroups`, `options.targetVisualNames`; the target page's section must already exist). `dry_run=true` previews; a real write carries the reload hint | `pbip_path`, `source_page`, `visual_id` (or `visual_name` = exact title), `target_page?`, `offset? {x,y}` (default 20/20 same page, 0/0 other), `position? {x,y,width,height}` + `position_frame?` (`page`\|`group`), `group_mode?`, `parent_group?`, `inherit_interactions?`, `interaction_map?`, `copy_bookmark_refs?` | Yes |
| `create_page` | Blank page (`displayOption: FitToPage`, sibling `$schema`) | `pbip_path`, `page_name`, `width?`, `height?`, `insert_after?` | Yes |
| `create_visual` | Single visual (delegates to `visual_creation_handler`) | `pbip_path`, `page_name`, `visual_type`, `title?`, `position`, `measures?`, `columns?`, `parent_group?`, `position_frame?` | Yes |
| `create_visual_group` | Group container | `pbip_path`, `page_name`, `group_name`, `position` | Yes |
| `delete_page` | Delete page (see `07_Page_Operations:delete`) | `pbip_path`, `page_name` OR `page_id` | Yes (CATASTROPHIC) |
| `delete_visual` | Delete visual (same implementation + severity as `07_Visual_Operations:delete`; filter-layer guard) | `pbip_path`, `page_id`/`page_name`, `visual_id` or `visual_ids`, `delete_children?`, `confirm_delete_filter_layer?` | Yes |
| `pbir_add_visuals` | Bulk direct-write create (no subprocess). Field refs `Table.Field` split at the FIRST dot (or `'Table'[Field]`); Measure vs Column from the report's bound model (`definition.pbir` byPath TMDL) unless `field_types:{role: Measure\|Column}`; unknown fields reported in `field_warnings`; `card` binds cardVisual's `Data` role; x/y are page-absolute and the container's page-absolute offset is COMPUTED (nested groups summed) when `container_x`/`container_y` are omitted; a container display name must be unique (`ambiguous_container`); each visual is written once, fully assembled | `pbip_path`, `page`, `visuals` ([{visual_type, x, y, width, height, title, fields, field_types?, objects?, visualContainerObjects?}]), `container_group_id?`, `container_display_name?`, `container_x?`, `container_y?` | Yes |
| `pbir_resolve_container` | Group id + PAGE-ABSOLUTE coords by displayName (`local` = raw position) | `pbip_path`, `page_name` (legacy `page`), `container_display_name` (legacy `container_name`) | No |
| `list_templates`/`get_template` | Templates | `template_id` | No |

### Encoding

Written files keep the byte traits of the file they replace (EOL, BOM, trailing newline, raw UTF-8 vs `\uXXXX` escapes); new files follow the report's sibling files, including their `$schema` versions.

### `pbir cp` mismatch bug

If you use the external pbir CLI's `cp`, the copied page/visual GUIDs still need regenerating (Desktop otherwise crashes with `VisualRelationshipService.getRelationship TypeError`; the old `~/bin/pbir_fix_copied_page.py` helper is gone). `clone_page` handles this itself — prefer it.

## III.24 `17_Desktop_Bridge` — DEBUG

File: `server/handlers/desktop_bridge_handler.py`. Wrapper around the Microsoft `powerbi-desktop` CLI (`@microsoft/powerbi-desktop-bridge-cli`), which talks to a **running** Power BI Desktop over the secure local named-pipe RPC (enable Desktop preview feature *"external tool access through secure local APIs"*). Core wrapper: `core/autonomous/desktop_bridge.py`; pending-state ledger: `core/autonomous/edit_ledger.py`. Annotated `destructiveHint: true`; per-op classification: `reload`/`reload_and_screenshot`/`save` = destructive, `open` = write, the rest read. Never closes Desktop. `inline_image=true` on `screenshot` / `reload_and_screenshot` also returns the page PNG as MCP image content.

This is the **standard live-iteration path**: edit PBIR (a batch of edits) → `reload_and_screenshot` ONCE → review → iterate. No close/reopen.

**The rule the tool enforces (2026-09-22 review, J-01/J-02): save BEFORE disk edits; after disk edits reload, never save first.**

- `reload` / `reload_and_screenshot` **refuse** when the target Desktop may hold unsaved in-memory work: `error_type: unsaved_changes` (Desktop reports `hasUnsavedChanges=true`, or this server made a live-model/TOM write — 02/03/15/09 debug_loop — that no disk-verified save flushed) or `dirty_state_unknown` (instance not `connected` / no flag). The instance is **always re-read right before the reload** (`status --pid`, explicit pid included — no cached status). The error carries `dirty_sources` and a hint. Ways forward: `save_first=true` (only while no disk edits are pending) or `discard_unsaved=true` (accept losing them; reported as `discarded_unsaved`). The CLI emits `hasUnsavedChanges` as `=== true`, so "unknown" arrives as `false` — the live-write ledger is what catches TOM edits; a manual Ctrl+S is detected when the `.SemanticModel` tree was rewritten after the last ledger entry.
- **Journal (JR-01):** `status` `dirty_sources` and a refused reload carry `journal_unflushed: N` (writes in the durable journal not flushed by a verified save) and name `02_Model_Operations object_type=model operation=replay` as the way back after a discarded reload; a verified `save` marks them flushed, `reload discard_unsaved=true` reports `discarded_unsaved.journal_unflushed` + `recover_with`.
- `save` **refuses** with `conflict_pending_disk_and_memory` while this server has disk edits for the project that Desktop has not reloaded (fed by every `pbip_utils` write + the dispatcher for TMDL ops) — a save would write Desktop's OLD in-memory copy over them. Override: `overwrite_disk_edits=true`. Memory clean + edits pending → just reload. Both pending → no ordering keeps both: reload with `discard_unsaved=true` and redo the TOM edits, or save with `overwrite_disk_edits=true` and redo the disk edits.
- A reload Desktop rejected (CLI 0.x: exit 0 with `result.success=false`; CLI >= 1.0.0: non-zero exit with a `RELOAD_REJECTED` error envelope) is `reload_failed` (the canvas still shows the old state; no screenshot is taken).
- A **plain** bridge reload cannot apply TMDL (the CLI's `reload` sends `reloadModelDefinition:false`; verified on 0.1.1 and 1.0.0): TMDL disk ops return `reload_applies:false`, `reopen_required:true`, `include_model_reload:true` and the chain "`reload_and_screenshot include_model=true` (metadata-only edits) → otherwise close Desktop WITHOUT saving (ask the user) → `17_Desktop_Bridge open`"; a plain reload with pending TMDL edits reports `tmdl_edits_not_applied` and, when the change classifier says the edits are metadata-only, `model_reload_hint` + a warning to re-run with `include_model=true`.
- **`reload` / `reload_and_screenshot` with `include_model=true` (BR-04, default false)** re-applies the on-disk TMDL to the running model (`file.reload/v1` with `reloadModelDefinition:true`, sent through the bridge CLI *library* by `tools/bridge_lib.mjs`; needs CLI >= 1.0.0). Order: (1) the **same fail-closed unsaved gate** as any reload — the MS reload itself does not refuse on unsaved TOM edits (spike 2026-09-30: a live-only measure was silently discarded), so this gate is the only protection and `include_model` adds no bypass beyond `save_first` / `discard_unsaved`; (2) the **change classifier** (`core/autonomous/model_change_classifier.py`) deserializes the on-disk `definition/` folder with the bundled TOM `TmdlSerializer` and diffs it against the live model read over the existing connection (which must be the target instance's project — else `model_change_unclassified`). *Allowed* (`metadata_only`): measures, calculated columns, calculated tables' DAX and new/removed calculated or calculation-group tables, relationships, roles, perspectives, cultures, hierarchies, UDFs, descriptions, format strings, display folders, hidden flags, import-column property changes. *Refused* with `error_type: model_reload_unsafe_change` (+ `unsafe_changes[]`, guidance "close Desktop WITHOUT saving and reopen"): any partition change (source / mode / query), named expression / M parameter, data source, storage mode, refresh policy, model default mode or compatibility level, a new/removed import (storage-backed) table, a new/removed/retyped import (`sourceColumn`) column. TMDL that does not deserialize is `tmdl_invalid` (+ `parser_message`); a classifier that cannot run (no connection, wrong project, TOM unavailable) is `model_change_unclassified` — nothing is reloaded in any of these cases (fail closed). (3) The reload; Desktop's error envelope is parsed, never the exit code (an invalid-TMDL reload returned exit 0 with `RENDER_FAILED` / `-32505`): `model_reload_failed` (the previous model stays loaded — the reload is atomic), `reload_failed` (`RELOAD_REJECTED`), `bridge_method_unavailable` (no `file.reload/v1`). (4) A post-reload re-read of the live model: `model_reload` = `{classification, changes[], verification: verified|mismatch|unavailable, applied[], mismatch[], follow_up[]}`. A `mismatch` keeps the TMDL ledger entries (a save would overwrite them). A model reload never refreshes imported data; changed calculated columns/tables may need `02_Model_Operations object_type=model operation=refresh refresh_type=calculate`; the Desktop **Data pane can stay stale** after a model reload, so verify with DAX (`INFO.MEASURES()` / `INFO.COLUMNS()` / `INFO.TABLES()`), not the UI. Live basis: FACTS §E (metadata-only reload applied in ~2 s with import data retained; a partition/M change emptied the table and left the host permanently "not ready").
- **`dirty_state_source`** (status `dirty_sources`, reload/save results): `bridge_flag` | `ledger` | `bridge_flag+ledger` (also the clean case) | `unavailable`. **`flag_stuck`** (BR-03): when a save verified ON DISK leaves `hasUnsavedChanges=true` on a pid (Desktop 2.157.627.0 defect), that pid is marked stuck for this server session; `assess_dirty` / reload refusals then carry `flag_stuck:true` + a `suggestion` (reload with `discard_unsaved=true` if nothing else is unsaved). The gate is never bypassed automatically; the marker clears when the flag reads false.

| Operation | Purpose | Key args | Level |
|-----------|---------|----------|-------|
| `status` | Running Desktop instances + pages (pid, bridgeStatus, currentFilePath, reportDir, hasUnsavedChanges, pages[] — read from pages.json ON DISK) + per-instance `dirty_sources` {verdict clean\|dirty\|unknown, dirty_state_source, flag_stuck, reasons, intent_flag, ledger_live_writes, pending_disk_edits}; with `response_format: concise` a nested `pages` list is capped at 25 and its instance gets `pages_truncated {shown, total}` | `pid?`, `wait_seconds?` (30), `dirty_sources?` (true) | read |
| `reload` | In-place reload (report/PBIR; plus the TMDL model definition with `include_model=true`, classified as above). Fail-closed as above. | `pid?`/`pbip_path?`, `save_first?`, `discard_unsaved?`, `overwrite_disk_edits?` (with save_first), `include_model?` (false; rare-op arg), `wait_seconds?` (forwarded as `--wait-seconds`), `timeout?` | destructive |
| `save` | Flush in-memory model + report via UI automation (UIA Invoke on the Save control; SendInput Ctrl+S only after re-checking foreground + no held modifier; no global type_keys floor). **Verified on disk**: baseline taken BEFORE the trigger, only post-trigger changes in the project's own trees count (the `.SemanticModel` tree when TOM writes are pending), this server's own writes are ignored, tree must be stable 1.5 s. Clean fast path only when the flag is false AND the ledger has no pending live write → `already_saved:true, saved:false`. | `pid?`/`pbip_path?`, `save_timeout?` (30), `overwrite_disk_edits?` | destructive |
| `screenshot` | Capture one page to PNG, optimized to the vision tier. `per_visual=true` crops every visual of THAT page (the page folder only is read; the PNG is decoded once). Page by id, display name, then case-insensitively; duplicate display names → `ambiguous_page`. **`per_visual` (BR-05):** uses `report.snapshot.capture/v2` with a `region` per visual (exact page-absolute rectangle, group offsets resolved, clipped to the page; `visual_crops.capture_method: "v2_region"`, pixel size = region × scale) **only when the target's live manifest lists the method** — Desktop flights it per instance, not per build — via `tools/bridge_lib.mjs`; otherwise (or on any failure) the v1 crop runs (`capture_method: "v1_crop"`, `v2_unavailable_reason` says why). A v1 capture is the canvas *viewport* (filter pane + banners included), not the page, so the v1 crop first **locates the page rectangle** in the PNG (`crop_mapping`: `full_png` = PNG aspect matches the page, unchanged mapping; `detected` = found by canvas-colour + aspect-locked search; `assumed_full_png` = not found, legacy mapping with a `warnings` entry). | `page` (alias `page_id`/`page_name`), `output`, `settle_ms?` (0), `render_stable?`, `scale?` (1–3), `optimize?`, `vision_tier?` (`high`\|`standard`), `per_visual?`, `report_root?`, `crops_dir?`, `pid?`/`pbip_path?` | read |
| `screenshot_all` | Every page to a directory; a partial capture (CLI exit 1, `status: partial`) is `success:true, partial:true, failures[]` and the captured PNGs are still optimized | `output_dir`, `scale?`, `settle_ms?`, `optimize?`, `vision_tier?` | read |
| `reload_and_screenshot` | **Reload + render settle + screenshot in one call**, per-PID lock held across both; refuses exactly like `reload`; a failed reload never screenshots | `page`, `output`, `settle_ms?` (**default 2000**), `render_stable?`, `save_first?`, `discard_unsaved?`, `include_model?` + all `screenshot` args | destructive |
| `resolve_page` | Display-name → page id (case-insensitive fallback, ambiguity reported) | `page_name` (legacy `page` / `page_id`), `pid?`/`pbip_path?` | read |
| `manifest` | RPC surface the connected Desktop exposes (`methods`, or `methods_unavailable`) | `pid?`, `wait_seconds?` (0) | read |
| `open` | Launch a NEW Desktop on a `.pbip`/`.pbix` and wait for its bridge (the reopen half of "close without saving → reopen" after TMDL edits). Refuses `already_open` when a running instance has the project open (`allow_duplicate` overrides). **The pid the CLI returns is not trusted** (the 1.0.0 CLI returned another running instance's pid before the new window was ready): status is polled (within `timeout_seconds`) until a NEW `connected` instance whose `currentFilePath` is the requested file exists; only its pid is returned (`verified:true`, `cli_reported_pid` when it differed). Otherwise `pid:null`, `verified:false`, a warning, and the ledger is NOT cleared. Clears the project's ledger only on a verified open. | `pbip_path`, `timeout_seconds?` (60), `allow_duplicate?` | write |

Page argument (wave 3, L-11): the canonical name is `page_name` (page id or display name); `page` and `page_id` still work and are reported in `deprecated_args`.

Instance selection (`select_instance`): explicit `pid` → that instance (and, with `pbip_path`, it must have that project open); `pbip_path` → the instance whose open project matches — the `.pbip` file, its folder, its `.Report` or bound `.SemanticModel` folder, normalised (absolute, 8.3 long path, case-folded, `/` or `\`) — **even with a single Desktop running** (`instance_path_mismatch` otherwise); no pid/path → the single instance, else `ambiguous_instance` with the candidates. Errors keep the CLI's structured error (`cli_error_code`, `cli_error_message`, `raw_stderr`). An invalid `pid` is `invalid_argument` (never silently dropped); `wait_seconds: null` means "default".

Execution: the CLI is run as `node <script>` with `shell=False`, where `<script>` comes from the installed package (resolution order: `MCP_PBI_BRIDGE_CLI_JS` → `package.json` `bin` → the target named in the npm shim text → `dist/cli.js` → `dist/index.js`, the last only for CLI < 0.2.0 or no package.json; from CLI 0.2.0 `dist/index.js` is a silent library entry and is never used) — never through the npm `.cmd` shim, which `cmd.exe` would interpret (`"`, `%VAR%`, `&`, `|` in an `output` path were command injection, J-06); override the script with `MCP_PBI_BRIDGE_CLI_JS`. When only the shim exists, arguments with cmd metacharacters are refused (`unsafe_argument`). `status` and `manifest` report `bridge_cli_version` / `bridge_cli_script` (plus a `warnings` entry when the CLI major is newer than the tested 1.x; `status` also warns on an instance's `pagesError`). A run that exits 0 with empty stdout is `cli_no_output` (wrong script, e.g. a library entry). CLI error codes map to `error_type`: `RELOAD_REJECTED` → `reload_failed`, `CONNECTION_LOST` → `bridge_connection_lost` (+ `retryable` / `retryAfterMs`), `AMBIGUOUS_DESKTOP_INSTANCE` → `ambiguous_instance`; any other code (e.g. `AMBIGUOUS_OWNING_PBIP`, `OWNING_PBIP_NOT_FOUND`) passes through as `cli_error_code`. **Library helper (BR-04/BR-05):** what the CLI cannot do — a model-definition reload and `report.snapshot.capture/v2` region captures — runs `node tools/bridge_lib.mjs <reload-model|capture-regions> --package-dir <pkg>` (`shell=False`, same node/package as the CLI; needs CLI >= 1.0.0, else `bridge_lib_unavailable`). It prints ONE JSON envelope (`ok` / `partial` / `unsupported` / `error`) and exits 0 for every handled outcome — the envelope is parsed, the exit code ignored. Calls are serialized per PID (the CLI cancels parallel calls to one PID); the dispatcher also serializes every write/destructive op. Budget: reload/save refuse to START (`budget_exhausted` / `cancelled`) once the call was cancelled or less than ~8 s of the 120 s tool budget is left, and the CLI's `--wait-seconds` is capped to the remaining budget.

Model changes go through live TOM (`02_Model_Operations`, instant) and verify via `04_Run_DAX` / `09_Validate`. `09_Debug_Operations debug_loop` uses the same refusing reload; its `fix_strategy=live_tom` passes the destructive gate (`allow_llm_fix` needs `confirm_destructive=true` under WARN/STRICT) and `tmdl_edit` stops with `final_status: awaiting_reopen`.

CLR errors keep `clr_root_cause`, `clr_root_type`, `clr_chain` (`core/autonomous/clr_errors.py`); an `AggregateException` with several causes also lists every one (`clr_chain[].inner_exceptions`, `clr_root_causes`).

**Model reload classifier + reload timeouts (2026-10-03):** `include_model=true` compares the FULL serialized object (model, data sources, named expressions, tables, partitions, import columns); only reviewed metadata / runtime-bookkeeping properties are exempt, so any other changed property (e.g. `EncodingHint`, `IsNullable`, partition `DataView`, `DefaultPowerBIDataSourceVersion`, M source text) makes the change unsafe (`model_reload_unsafe_change`); a serialization failure is `model_change_unclassified`. Calc items and role members are detected as metadata-only. The TMDL tree is fingerprinted at classification and re-checked just before the reload (`model_change_unclassified` if it moved). A `reload-model` helper timeout is `timeout_outcome_unknown` (verify by DAX, never blind-retry).

## III.25 `13_Query_Plan` — DEBUG

File: `server/handlers/query_plan_handler.py`. Capture/parse/analyze DAX query plans (logical/physical/xmSQL) with heuristic verdict (`ok|warn|hot`).

**v7.14.2 — capture rewired to the native trace runner.** Previously this tool imported a
`core.infrastructure.trace_session` module that **has never existed in this repo**; the
`except Exception` fell through to a `_NullTrace` whose `events()` returned `[]`, so every capture
returned `success: true` with **empty plans and zero counters** — a false all-clear. Capture now
goes through `core.infrastructure.query_trace.NativeTraceRunner` (the same lane as `04_Run_DAX` /
`09_Debug_Operations`): a real AMO trace subscription, correct interval-union SE duration, and real
plan text (the C# runner now subscribes the `DAXQueryPlan` event class). If the runner is
unavailable the tool now returns `success: false` / `error_type: "trace_unavailable"` rather than
an empty success.

**v7.15.0 — plans are real; the exe is now only a fallback.** The rebuild warning above is
obsolete. `core/infrastructure/trace_session.py` runs the AMO trace **in-process via pythonnet**,
so there is no build step and no stale binary: it subscribes `DAXQueryPlan` (112) and
`ExecutionMetrics` (136) directly, drains every result set, and hands the events to
`pbi_perf_core` for DAX Studio-parity aggregation. `DaxExecutor.exe` is kept only for machines
where `import clr` fails, and on that lane the plan/metrics keys come back empty (never missing).
`runner_lane` in the response says which one ran (`in_process` | `exe`).

Implementation notes that are easy to get wrong: `Trace.Events` collides with `Component.Events`
under pythonnet (fetch it off the CLR type), event columns must be intersected with what
`$SYSTEM.DISCOVER_TRACE_EVENT_CATEGORIES` advertises per event (QueryBegin has no Duration;
ExecutionMetrics has no SessionID), and the engine delivers events in a batch **~2.3 s after the
query returns**, so `run()` waits out a settle window (default 3000 ms, extended while plan/metrics
events keep arriving). On a local Desktop no server-side filter is used — SE events carry no
SessionID — so events are correlated by the ActivityID of our own QueryBegin/QueryEnd.

> **Plans are file-backed, never inlined.** A real plan reaches 10k+ rows / 12 MB. `capture`
> returns a summary and writes `exports/perf/<capture_id>/{ServerTimings.json, QueryPlan.json,
> capture.daxx}`; use `plan_rows` to page the rows, or open the `.daxx` in DAX Studio itself.

**Cache clearing is now real.** `clear_cache` used to run `EVALUATE {1}` — a throwaway constant
query that clears nothing, so every "cold" capture and every `benchmark(cold_cache=true)` silently
produced warm-cache numbers. It now issues the `ClearCache` XMLA batch
(`QueryExecutor.clear_vertipaq_cache()`). `capture` returns **`cache_cleared`** and `benchmark`
returns **`cold_cache_honoured`** so a failed clear is visible instead of silently invalidating the
baseline.

| Operation | Purpose | Required args |
|-----------|---------|---------------|
| `capture` | Capture plan for DAX → **summary + file exports** | `query` (legacy `dax_query`), `include_xmsql?` (default `true`), `clear_cache?` (default `true`) |
| `plan_rows` | Page the captured plan rows | `capture_id`, `plan?` (`physical`\|`logical`, default `physical`), `offset?` (0), `limit?` (100, clamped 1–500), `min_records?` |
| `export_daxx` | Re-write the DAX Studio exports for a capture | `capture_id` |
| `parse` | Parse captured plan | `capture_id` |
| `analyze` | Analyze captured plan | `capture_id`, `include_xmsql?` |
| `compare` | Timing delta **+ structural plan diff** | `capture_id_a`, `capture_id_b` (must differ) |
| `list_rules` | List heuristic rules | — |
| `evaluate_and_log` | Capture EVALUATEANDLOG events - forwards to `04_Run_DAX mode=evaluate_and_log` | `query` (with `EVALUATEANDLOG(...)` wrapping), `clear_cache?` |
| `benchmark` | Multi-run cold/warm cache benchmark | `query`, `runs?` (default 5, clamped 1–50), `cold_cache?` (default `true`), `warmup?` (default 1, 0–5) |
| `benchmark` (traced) | N cold then M warm runs **with per-run server timings** | `query`, `cold_runs?` (3, 0–20), `warm_runs?` (3, 0–20), `warmup?` (1, 0–5) — passing either `cold_runs`/`warm_runs` selects this lane |
| `benchmark` (A/B) | Interleaved A/B, every run cold + traced | `query` (A), `compare_dax_query` (B), `runs?` (5, 1–20), `warmup?` (1) |

**`capture` response (v7.15.0).** A summary, never the plan text: `headline` (total/fe/se/fe_pct/
se_pct/se_cpu/total_cpu/se_net_parallel/se_queries/se_cache_hits/dq_ms/parallel_se_detected/
result_rows), `execution_metrics` (peak memory, FE/SE CPU split, `executionDelayMs` queue wait,
result rows), `result_sets[]` (**every** EVALUATE, not just the first), `plan_shape`
(physical/logical row counts, `max_records`, `cache_consumers` — `Cache:` operators only — vs
`se_query_count`, `highlighted_rows` — DAX Studio's highlight count incl. `VertipaqResult:`
producers, F-23 —, `callback_scans`), `operator_census` (top 20), `top_records` (top 10 `#Records` rows with
RowNumber/Level), `findings[]`, and `exports` with the three file paths.

**`compare` response.** `timing_delta` plus `physical_plan_diff` / `logical_plan_diff`, each with
row-count / max-records / highlighted deltas, an operator histogram delta, and added/removed
operators — what the rewrite actually stopped doing, which is what generalises beyond one run.

`benchmark.summary` adds **`baseline_ms`** (the fastest valid run — the baseline protocol compares
fastest, not mean, because the mean is dragged by background noise), **`spread_pct`**, and a
**`warning`** when spread >20% (runs disagree; the baseline is not yet trustworthy).

**2026-09-22 review (F-08…F-11, F-14, F-22…F-24).** Capture ids are UNIQUE per run
(`<query hash>-<UTC>-<n>`, plus `query_hash` / `captured_at`), so a before/after capture of the
same query never overwrites the first; `compare` rejects `a == b` and reports `same_query`. The
verdict no longer calls every plan "hot": nesting is measured in operator levels (info-only above
40) and a bare `CrossApply` is not a finding. `evaluate_and_log` subscribes event 135 when the
trace STARTS and returns `eval_log_subscribed` (a warning when false: an empty log then proves
nothing). `capture.daxx` carries `[Content_Types].xml` (OPC) so DAX Studio's packaging API sees
its parts (needs one open-in-DAX-Studio check). The plan analysis is recomputed with the LARGEST
result set. `benchmark` lanes stop at the tool budget (`runs_not_executed`); the traced lane adds
`cold_cache_honoured` (false when any "cold" run could not clear the cache), a `summary`
(`baseline_ms` fastest / `spread_pct`) per phase and `stddev_kind: "population"`. Raw plans of
stored captures are spilled to the export folder (the in-memory store keeps a slim copy);
`plan_rows` / `compare` re-read them and cache at most 4 parsed plans.

`capture` and `analyze` outputs include extended trace fields: `dq_query_count` + `dq_total_ms` (DirectQueryEnd events), `se_cache_match_count` / `se_cache_removal_count` (`VertiPaqSEQueryCacheMatch`/`Removal` events), `agg_rewrites: List[{status, agg, subclass}]` (`AggregateTableRewriteQuery` classified as `matched`/`nomatch`/`attempted` — accepts both string and integer `EventSubclass` values), and `callback_count` (CallbackDataID substring detection across xmSQL chunks). Three new heuristic rules consume these counters: `callback_in_xmsql`, `dq_query_in_plan`, `agg_rewrite_no_match`.

**Live trace (7.16, TRC-01)** — `core/infrastructure/live_trace.py`:

| Operation | Purpose | Args |
|-----------|---------|------|
| `trace_start` | Start ONE long-lived trace on the current Desktop (local, unfiltered: it sees the report's own visual queries). A published (XMLA) target is refused with `trace_unsupported_on_service`: the service trace is filtered to this server's own session, so it can never see a report's queries | `max_duration_s?` (300, cap 3600), `max_events?` (200,000), `label?` |
| `trace_fetch` | Queries captured so far, one per RequestID (commands on one connection share an ActivityID), with duration, SE/FE split, SE query count, cache hits via `pbi_perf_core.server_timings`; DMV / internal traffic dropped. Before reading it flushes the engine's trace buffer (released in ~2 s batches) with a DMV ping carrying a unique negative `SESSION_SPID` and waits for that ping's QueryEnd (cap 3 s) | `since?` (the previous `next_since`), `include_text?` (4,000-char cut), `slowest_first?`, `match?` (`visuals`, F-02), `pbip_path?`, `page_name?` |
| `trace_stop` | Stop and return the final list | same as fetch |

Auto-stop on `threading.Timer` or the event cap, so a forgotten trace never keeps the engine subscribed.

**`match=visuals` (F-02, `trace_fetch` / `trace_stop`).** Tags each captured query with the PBIR visual that sent it
(`core/debug/trace_visual_match.py`; PBIP = `pbip_path` or the `09_Debug_Operations set_path` project; `page_name` limits
the index). Tier 1: the engine's `ApplicationContext` `VisualId` when the event carries one (parsed from
`application_context` / `request_properties` / `request_parameters`; the row shows `application_context`) =
`confidence: exact`. **Unverified:** whether Desktop fills it locally, and `trace_session` does not map the column yet,
so today every match is tier 2. Tier 2: query shape - group-by columns (0.50), measures / value fields (0.35), filter
tables (0.15), weighted Jaccard of the captured DAX against each visual's reconstructed query (`VisualQueryBuilder`);
`confidence` high (score >= 0.95, margin >= 0.10) / medium (>= 0.80, >= 0.05) / low; below 0.60 nothing is assigned. A
tie between different visuals (< 0.02 apart, e.g. a card pasted on two pages) is `ambiguous: true, match: null` and
never auto-picked. Rows gain `visual_match {match {visual_id, page, visual_name, score, confidence, method}, candidates[3],
ambiguous, reason?}`; the result gains `visual_matches {queries, matched, ambiguous, unmatched, visuals_indexed,
visuals_unbuildable, by_visual[{visual_id, page, queries, total_ms, max_ms}]}`. Without a PBIP the rows come back
untouched with `visual_matches.error_type = pbip_path_required`. The rows are the real per-visual timings of one page
switch; `09_Debug_Operations visual from_trace=true` runs the best-matching captured query instead of the reconstruction.

**Per-item progress + cancel (F-26, WP-5B).** `09_Profile page` / `filter_matrix`, `05 lint_model`, `16 build`, `09_Validate test_run`
and `17 screenshot_all` emit per-item progress (`core/utilities/progress_ticker.py`) and honour the call's cancel flag. A
cancelled profile / lint / suite returns the finished part (`cancelled: true, partial: true`; a cancelled suite is never
`all_passed`). A cancelled graph `build` returns `error_type: cancelled` and caches nothing (a half-built graph must not be
mistaken for evidence). `screenshot_all` reports `page i of n` while the CLI writes the PNGs (a watcher counts them) and
while optimizing; a cancel stops the optimize pass (`cancelled`, `optimize_skipped`).

**Soft inline budget (F-34, `server/response_pipeline.py`).** Below the 450K spill, `render_result(soft_budget_chars=...)`
(config `response.inline_soft_chars`, default 60,000, `0` = off) returns a success result whose serialized size exceeds the
budget as the first page of its main list (the dispatcher's own pagination key choice) with `truncated_inline: true`,
`paginated_key`, `next_token` (`<key>:<n>`), `has_more` and `inline_truncation {key, shown, total, budget_chars, note}`; whole
items only, no string or DAX text is ever clipped. Continue with `page_size` + `next_token`. Opt out: an explicit `page_size`,
or `response_format=detailed` together with `full=true`. Results that are errors, already paginated, or have no list are left alone.

**Live trace (2026-10-03):** the session buffer is bounded (oldest dropped and counted) and a 5 s pump enforces `max_events`; `events_dropped` is reported by `trace_fetch`, `trace_stop` and status. `trace_stop` flushes, closes and de-registers in one locked step, auto-stop closes only the session it owns, and a concurrent `trace_start` can no longer orphan a trace that holds the lock. On an XMLA target `trace_start` returns `trace_unsupported_on_service`.

**Benchmark primary metric (2026-10-03, S-31):** `benchmark` / `benchmark_traced` groups carry `primary_metric` + `primary_ms` = the engine's `execution_metrics.duration_ms` (falls back to `total_ms` when a run lacks it); the A/B verdict uses it. Benchmarks emit per-run progress notifications.

## III.26 `15_DAX_Refactor` — MODEL

File: `server/handlers/dax_codemods_handler.py` (file kept; tool renamed); engine in `core/codemods/`. Preview/apply bulk DAX refactor transforms with a `plan_hash` staleness check, a write-ahead undo journal and rollback.

| Operation | Purpose | Required args | Destructive |
|-----------|---------|---------------|-------------|
| `list_transforms` | Available transforms + `args_schema`, `persistable`, `semantics_preserving` | — | No |
| `preview` | Dry-run on the live model: FULL `before`/`after` + unified `diff` per edit, `rejected_edits` (failed the structural gate), `skipped`, `warnings`, `persistable`, `delete_candidate` | `transform`, `args` | No |
| `apply` | Apply the previewed plan | `transform`, `plan_hash` (from preview), the SAME `args`, `pbip_path?`, `verify?` | Yes (CATASTROPHIC if `inline_measure` + `delete_after=true`) |
| `rollback` | Restore the pre-apply state from a journal | `journal_path`, `force?` | Yes (DESTRUCTIVE, gated like apply) |
| `list_journals` | Recent journals (newest first) with status / database | `limit?` (50) | No |

**2026-09-22 review (WP6) — behaviour:**
- **Rewrites are lexer-based and exact or refused.** `inline_measure` substitutes `CALCULATE(<body>)` at sites in a row context / CALCULATE filter argument (a measure reference has an implicit context transition) and `(<body>)` elsewhere, body on its own lines (comment-safe); matches `[M]` and `Home[M]` case-insensitively, `]]` names too; skips callers whose VAR names clash with the body's. `use_divide` refuses `&&`/`||`/`==`/`IN`/VAR blocks at top level and keeps trailing comments outside the call (it is a SEMANTIC change: errors/Infinity become BLANK). `remove_unused_variables` is VAR/RETURN-nesting aware (drops `RETURN` with the last VAR, no pass cap). `swap_table_reference` swaps bare/quoted table refs too and leaves `Old[Measure]` alone (measures only). `extract_measure` refuses an existing measure/column name, a missing `target_table`, naked-column bodies, and rebinding inside row context / filter args / partial operands.
- Every transform accepts `measures: ['T[M]'|'M']` and `tables: [...]` scope filters (`lint_model` findings carry `fix_args: {measures: [...]}`).
- Every `after` is re-lexed (balanced parens, no swallowed tokens, no new `, *` / `,)` patterns); failures are `rejected_edits`, never written. `apply` additionally asks the engine to bind each body (`EVALUATE ROW("ok", IF(FALSE(), <after>))`, created measures via `DEFINE MEASURE`) before writing.
- **`plan_hash` = transform + canonical args + connected database + model state**, computed from reads that bypass the DAX result cache. Apply with different args (e.g. `delete_after` omitted) → `plan_stale`.
- **One atomic write:** the whole plan (upserts + delete) is committed in ONE TOM session / ONE `SaveChanges` through the connection's ADOMD-bound `DAXInjector` (`apply_measure_changes`); each measure must still have the previewed body (`plan_stale` otherwise); a body that no longer compiles after the save reverts the whole plan (`invalid_dax`).
- **`delete_after` defaults to `false`.** A delete happens only when every rewrite committed, nothing was rejected/skipped, the in-memory model has no remaining reference (measures, format-string / detail-rows / KPI expressions, calc columns, calc tables, calc items, RLS filters, UDFs) and the report scan (`pbip_path` or the connection's PBIP folder) finds no binding; otherwise `delete_skipped` explains why and `affected_visuals` lists the bindings.
- **Verification (`verify`, default true for `inline_measure` / `extract_measure` / `remove_unused_variables`):** the rewritten measures are evaluated at the grand total before and after; a difference rolls the apply back automatically (`error_type: semantic_change_detected`). Grand total only — per-slice differences are not covered.
- **Journal** (`%TEMP%/mcp-pbi-codemods`, override `MCP_CODEMOD_JOURNAL_DIR`, pruned after 30 days) is written BEFORE the first write (`status: pending → committed | failed | rolled_back`) with the database identity, every edit's before/after and full snapshots of deleted measures (format string, dynamic format string, folder, description, data category, hidden, detail rows).
- **`rollback`** accepts only journals inside the journal directory, refuses another database (`identity_mismatch`) or a journal without identity (`identity_unverified`, pass `force=true`), refuses targets changed since the apply (`rollback_conflict`, `force=true` overwrites), DELETES measures the apply created, recreates deleted measures with their metadata, and reports every failure.
- **`extract_udf` (2026-10-03 review F-15):** args `measures` (2+ `'Table[Name]'` whose bodies share ONE token structure - the `extractable_by_transform` groups of `05_DAX_Intelligence find_clones`), `function_name`, `parameter_names?`, `description?`. The differing literals / references become the parameters (`val` / `expr` per the canon, see §III.8); each measure body becomes `FnName ( args )` (a leading comment block is kept). Edits: `function:<Name>` (note `create`) + one `measure:T[M]` per measure. **Refuses** (preview `success: false`, apply writes nothing): compatibility level below 1702 (`compatibility_level_too_low`) or unreadable (`compatibility_level_unknown`) - the op NEVER raises the level; an existing function (`already_exists`), an invalid / built-in function name (`invalid_function_name`), unknown measures (`measure_not_found`); different structures or identical bodies give no edits and a warning. The transaction **creates the function first** (an uncalled UDF is inert; the engine can only bind the call sites once it exists), validates and writes the measures in the usual single save, verifies values at the grand total, and **deletes the function again when the apply does not commit** (failed save, rejected body, `semantic_change_detected` rollback; `function_cleanup` lists it); `rollback` restores the measures and then deletes the function. A partial non-atomic write keeps the function (the written measures call it). The journal records `functions_created`; the plan hash covers the model's function names. Verification is grand total only: sweep the real grains before relying on the rewrite, and never call the new function from a row context (a UDF body gets no automatic context transition).
- **`move_measure_batch` is PREVIEW-ONLY** (`persistable: false`): `apply` refuses with `error_type: transform_not_persistable` before any gate; use `02_Model_Operations measure/move` or `03_Batch_Operations measures/move`.
- Live-TOM writes need no reload — the old `reload_required` hint (which read a non-existent attribute) is gone.

## III.27 `16_Dependency_Graph` — ANALYSIS

File: `server/handlers/dependency_graph_handler.py`; core: `core/graph/{model_source,report_source,builder,queries,service,cache}.py`. Structured queryable graph of model + report dependencies (2026-09-22 review, J-03/J-04/J-11/J-18/J-19/J-20/J-22).

- **Model side** (live `INFO.*`, cache bypassed): tables (calculated tables, field parameters via `NAMEOF`, calc groups), columns (calculated expressions, sort-by), measures (expression + dynamic format-string definition + detail rows), calculation items (+ format string), RLS table permissions, relationships, hierarchies/levels, DAX UDFs (+ calls). References are parsed with the lexer-based `scan_references` (comments/strings ignored, `''`/`]]` escapes, case-insensitive resolution); unresolvable refs are kept in the node's `attrs.unresolved_refs`. A failed core INFO query → `model_side_complete: false`.
- **Report side** (PBIR via `core.pbip.pbir_model`): report → pages → visuals; visual projections and conditional-format/object bindings (`binds`), visual/page/report filters, slicer selections and drillthrough bindings (`filters`), bookmarks (captured filters, `captures` visuals, groups) and the buttons/navigators pointing at them (`navigates`), report-level extension measures. Bindings to objects the model lacks → `dangling_refs`. Other reports bound to the same model are listed in `other_bound_reports` (scan them with `all_bound_reports=true`).
- **Node ids**: `measure:T[M]`, `column:T[C]`, `table:T`, `calc_group_item:T[I]`, `rls_role:R`, `hierarchy:T[H]`, `function:F`, `relationship:T[c]->U[d]`, `report:R`, `page:R/<id>`, `visual:R/<page>/<visual>`, `bookmark:R/<id>`. `node_ref` also accepts bare `T[M]` / `'T'[M]` / `[M]` / `T`, case-insensitively.
- **Hidden visuals (F-13)**: visual nodes carry `attrs.hidden` (top-level `isHidden`) and `attrs.parent_group`; a bookmark's `captures` edge carries `attrs.reveals` (`false` when the bookmark captures the element with `display.mode: hidden`, or an `isHidden` group entry). A visual is dead when it or an ancestor group is hidden and no bookmark reveals it (`core.graph.queries.dead_visuals`).
- **Cache**: LRU (8, TTL 300 s) keyed by `(scope, normalised path, model_etag, report value-signature, all_bound_reports)` — deletions and mtime-preserving copies invalidate; `pbip_path` defaults to the connected model's PBIP path. Results report `stale: true` once the model generation moved since the build.

| Operation | Purpose | Required args |
|-----------|---------|---------------|
| `build` | Build graph (cached) → `graph_id`, counts, `node_kinds`, `graph_scope`, `built_at`, `model_etag`, `report_side_available`, `model_side_complete`, `reports`, `other_bound_reports`, warnings | `scope` (model\|report\|both, default `both`), `pbip_path?`, `all_bound_reports?`, `cache?` |
| `get_consumers` | **S-32.** What DEPENDS ON the node (measures, columns, visuals that use it) = the nodes that break when it goes. Result keys `consumers` (= `ancestors`) + `by_depth`. Legacy name `get_upstream` (reads backwards: it walks the incoming edges) is still accepted, rewritten with a `deprecated_args` notice | `graph_id`, `node_ref`, `depth?` (-1) |
| `get_dependencies` | **S-32.** What the node DEPENDS ON (its inputs). Result keys `dependencies` (= `descendants`) + `by_depth`. Legacy name `get_downstream` still accepted (notice) | `graph_id`, `node_ref`, `depth?` |
| `find_unused` | Objects with **no consumer** (any edge but containment). **Refuses** (`report_side_unavailable`) on a graph without the report side unless `allow_model_only=true`. **`transitive=true` (F-13)**: the dead set to a fixed point, "used only by unused" (mark and sweep from the live roots: measures / columns / calc items / UDFs / hierarchies reached by nothing live, incl. dead reference cycles); hidden visuals never revealed by a bookmark do not count as consumers. Entries get `stage` (peeling round), `reason` (`no_consumers`\|`only_hidden_visuals`\|`only_used_by_unused`\|`unused_cycle`), `consumers`, `hidden_visuals`; plus `dead_visuals`, `rounds`, `unused_cycles` | `graph_id`, `kinds?` (measure, column), `transitive?`. Entries a model delete cannot remove alone carry `removable: false` + `not_removable_reason` (live test 2026-10-03; `core/graph/queries.py`) |
| `cleanup_plan` | **F-13.** Staged deletion plan from the transitive dead set. **Plans only, never deletes.** Needs the report side. Per stage: `nodes`, `documents` (`changeset` = `apply_changeset` columns delete + `apply_changeset_args` with `preview: true`; `measure_batch` = `03_Batch_Operations object_type=measures batch_operation=delete` because measures are not supported by the changeset engine; `other_deletes` for calc items / UDFs / hierarchies via `02_Model_Operations`), `evidence` (`batch_impact`-style `impacts`, `unsafe_nodes`, `all_safe` + the gate meta, computed AS IF the earlier stages were applied: `assumes_removed`), `remove_manually` (calculated-table / field-parameter / calc-group columns, report-level extension measures: `07_Report_Operations manage_extension_measures delete`), `requires_prerequisite_visuals`. Top level: `plan_hash` (fingerprint of model etag + every stage's refs; NOT the TOM hash `apply_changeset` requires, run its `preview=true` for that), `prerequisite_visuals` (hidden, never revealed; a visual binding a deleted field makes Desktop reject the report, so remove them yourself with `11_PBIP_Authoring delete_visual` first), `deferred` (dead nodes whose dead consumer kind is not in `kinds`), `unused_cycles`, `warnings` (other bound reports not scanned, incomplete model side, truncation), `how_to_apply` | `graph_id`, `kinds?` (default measure, column), `max_stages?` (10), `full?`. Compact by default: staged nodes' per-node impact records are reduced (safe nodes counted, unsafe ones keep their full record); `full=true` returns every per-node impact record (live test 2026-10-03; `_compact_cleanup_evidence`) |
| `find_cycles` | Reference / UDF-call cycles (iterative, O(V+E)) | `graph_id` |
| `get_impact` | What breaks if this changes | `graph_id`, `node_ref`, `change` (delete\|rename\|modify), `new_name?`, `cascade?` |
| `batch_impact` | Blast radius for many refs in ONE call | `graph_id`, `node_refs[]`, `change?`, `new_name?`, `cascade?` → `{impacts, unsafe_nodes, all_safe, unknown_nodes, …}` |
| `quick_lineage` | build (or reuse) + consumers/dependencies/impact in one call (result keys `upstream` = consumers, `downstream` = dependencies) | `node_ref`, `direction?` (consumers\|dependencies\|both\|impact; legacy `upstream` / `downstream` still accepted with a notice), `scope?`, `pbip_path?` |
| `export` | Serialize a graph (DOT ids escaped); `output_path` goes through the path guard | `graph_id`, `output_format?` (json\|dot\|mermaid; legacy `format`), `output_path?` |
| `query` | JSONPath escape hatch | `graph_id`, `jsonpath` |

Every `get_impact` / `batch_impact` result carries **`unknown_nodes`, `report_side_available`, `model_etag`, `built_at`, `graph_scope`** (+ `stale`) — the fields the evidence-aware gate consumes. An unknown node is **never safe** (`safe:false`, listed in `unknown_nodes`; `all_safe` is false). A model-object impact on a graph without the report side carries a `warning` (visual consumers were not counted). Renames: case-insensitive collision check (measures model-wide + columns of the same table; columns vs columns/measures of their table); `safe:false` while `rebinds_needed` is non-empty unless `cascade=true` (the op rewrites every consumer).

## III.28 Ops added after the last full doc sync (verify in handler before relying)

The op tables above (§III.1–§III.27) predate several `feat(T1-*)` additions. These ops are live in their handlers but not yet folded into the tables above — grep the handler's dispatch table to confirm exact args:

- `01_Connection:ensure` — idempotent detect→connect→reconnect-on-port-drift in one call (`connection_handler.py:handle_ensure`).
- `16_Dependency_Graph:quick_lineage` — now documented in §III.27.
- `07_Theme_Operations`: `apply_theme`, `export_theme`, `import_theme`, `clear_visual_overrides`.
- `07_Bookmark_Operations`: `get`, `update`, `create_group`, `update_group`, `delete_group`.
- `07_Visual_Operations:get_roles_for_type` — return the role/bucket names for a visual type.
- `02_Model_Operations`: `upsert` alias + `if_exists` arg on `create`.
- Cross-cutting: `feat(T1-E)` adds `selectors` on list ops (measure + visual); `feat(T1-F)` introduced a uniform error envelope with `retry_with` / `suggestion` hint fields across handlers.

- **7.16 tool profiles (PROF-01)** — `server/tool_profiles.py`: `MCP_PBI_PROFILE` = `all` (default) \| `model` (drops the five PBIR `07_*` report tools + `11_PBIP_Authoring`) \| `report` (drops `13`, `15`, `03`, `02_TMDL`, `02_QA`, `05_Column_Usage`, `06`, `08`); `MCP_PBI_TOOLS_EXCLUDE` adds names. `01_Connection` and `10_Show_User_Guide` are always kept. `mcp_app.list_tools` filters; `call_tool` on a hidden tool returns `tool_hidden_by_profile`.

## III.29 Headless CLI — `python -m src.cli` (F-11)

File: `src/cli.py`. Runs any registered tool in-process through `ToolDispatcher.dispatch` (same argument normalisation, security gates, write serialisation and envelope as the MCP server) without an MCP client, so CI, `g01_prepush_gate.py` and the skills' scripts call one engine instead of reimplementing it. It bootstraps the registry and dispatcher like `pbixray_server_enhanced.py` but starts no MCP app, no stdio loop, no client gateway and no file logging.

```
python -m src.cli --list-tools [--format json|text]          # generated from the registry
python -m src.cli <tool>[:<operation>] ['<json-args>'] [--file args.json|-] [--format json|text]
python -m src.cli --connect-folder <pbip> <tool> '<json>'     # 01_Connection connect_folder first (offline lane)
python -m src.cli --connect-xmla <workspace> <model> <tool> '<json>'   # read-only published model first
python -m src.cli --root <dir> ...                            # extra directory for the file-path guard (repeatable)
```

| Exit code | Meaning |
|---|---|
| `0` | ok (`success` not false and no non-zero result `exit_code`) |
| `1` | the tool reported an error (`success: false`), or the result carries a non-zero `exit_code` (a failed `09_Validate test_run` suite, `no_tests_ran`) |
| `2` | usage error: bad / non-object JSON, unknown tool, missing tool, both inline args and `--file`, operation conflict (`tool:op` vs the JSON) |

stdout carries ONLY the result (JSON by default; `--format text` prints `ERROR [type]: ...` plus `key: value` lines); logs and anything a library prints go to stderr. Secrets are redacted from the output: values under keys matching token / secret / password / api key / authorization / connection string / credential, and the value of `MCP_PBI_XMLA_TOKEN` wherever it appears. `-h` appends the tool list from the registry; `--list-tools` shows each tool's `operation` enum.

Lanes: the PBIP file tools (`07_*`, `11_PBIP_Authoring`, `02_TMDL_Operations`, `02_QA_Linguistic`) and the suite-file ops `09_Validate test_list|test_put|test_remove` need no connection. `--connect-folder` makes the PBIP project folder an allowed path-guard root (a suite next to the model works) but the offline lane has **no DAX engine**: `09_Validate test_run|test_export|tieout|equivalence` answer `no_dax_engine`. Use `--connect-xmla` (needs `MCP_PBI_XMLA_TOKEN` or the MSAL cache) or a running Desktop for those. `path_guard` also honours `FINVISION_ALLOWED_ROOTS` (`os.pathsep`-separated), which `--root` sets.

Packaging: the console script is a hand-off (`python/pyproject.toml [project.scripts] finvision = "src.cli:main"`); until then use `python -m src.cli` from the repo root with the venv interpreter. Tests: `tests/test_cli_headless.py` (subprocess on `tests/fixtures/pbir_golden`, offline ops only).

> **`SVG_Visual_Operations` was removed in v7.6** (was unused). `core/svg/` is dead.
<!-- tool-reference:end -->

<a id="part-iv"></a>
# Part IV — Architecture

> Module-level detail for `server/` and `core/`. **Update this Part in the same commit when the architecture changes.**

## IV.1 Bootstrap & execution flow

`src/run_server.py`:
- Re-launches itself with `venv/Scripts/python.exe` when started by a Python whose minor version differs from `venv/pyvenv.cfg` (the venv's native wheels are single-version; K-12). The `.mcpb` manifest launches the venv python directly.
- Sets `sys.path` via `site.addsitedir()` for bundled `venv/Lib/site-packages` (Windows) or `venv/lib/*/site-packages` (Unix)
- Dynamically imports `src/pbixray_server_enhanced.py` via `importlib.util.spec_from_file_location()`

`src/pbixray_server_enhanced.py` (bootstrap only):
1. `server/logging_setup.configure_logging()` — stderr at `config.server.log_level`; `logs/pbixray.log` = `RotatingFileHandler` (10 MB × 3) behind a `MemoryHandler` (flush on WARNING / 64 records) + a 2 s background flush so INFO survives a killed stdio process.
2. Creates `ConnectionManager`, `RateLimiter`, `AgentPolicy`; warns about `tool_timeouts` keys that are not registered tool names.
3. `register_all_handlers(registry)` (asserts 28 tools — fails fast), `ToolDispatcher`, `seed_resources()`, log forwarder.
4. `server/mcp_app.create_server(registry, dispatcher, version=__version__)` builds the MCP app (serverInfo.version = ours; instructions generated from the registry) and pre-builds the tool list + validators.
5. `cli()` → `asyncio.run(main())` → stdio server.

**Client masking** (`00_Client_Mode`): a local gateway on the Claude Code <-> Anthropic connection (`client_gateway/`, `127.0.0.1:8890`, entered through `ANTHROPIC_BASE_URL` per folder or for every chat). **Masking on demand (2026-10-08):** the gateway runs only while a chat has masking on. `client_gateway/switch.py` is what the typed `masking on|off` does (called by the hook, the CLI `python -m client_gateway masking ...` and `00_Client_Mode on`): on = start the gateway, mode on, route the chat's project folder and record the chat on it (`folders.add_session`); off = the folder's chats off, unroute (the direct URL is WRITTEN: a running Claude Code session re-reads its settings' `env` but keeps a removed key). `client_gateway/reaper.py` stops a gateway nothing is routed to. The hook holds a masked chat's message until its process has switched (`_pending_guard`). The MCP never starts the gateway (desktop routing excepted) and `server/masking_layer.enabled()` = the flag AND this MCP's own chat on (`_chat_masked`). It masks every request (prompt, tool results, history, screenshots via OCR) and rehydrates the streamed answer; masking is a per-chat mode, off by default (an "unmasked guard" stops a request whose newest message holds known client data), switched by the user typing `masking on|off|status|dictionary` (`hooks/client_masking_hook.py`). Shared pieces in `core/client_guard/`: `engine.py` (pseudonyms + match rules), `detector.py` (which model columns hold client values), `common_words.py` + `data/` (word lists), `home.py` (masking folder). The handler only controls the gateway, and the MCP start ensures the gateway runs when routing is set. The gateway is also the single **masking authority** (the `mask` / `unmask` / `mask_image` control routes, control token, `503` fail-closed; one machine-wide `SessionMap`), which `server/masking_layer.py` calls to mask this server's own results, resources, log messages, completions and resource listings and to unmask tool arguments (**off by default**: `client_gateway.mcp_masking` / `FINVISION_MCP_MASKING=1`; results carry the marker `_fv: fvpbi-<install id>` from `core/client_guard/marker.py`). With `client_gateway.pbi_chats_only` (default true) the gateway masks only chats that carried that marker (sticky, persisted in `session_modes.json`; requests with no chat id follow a PBI chat active in the last 15 minutes) and forwards every other request byte-identical, never blocking it (`GatewayState.request_mode`). `tools/masking_lab/lab.py` runs an isolated lab gateway on port 18890 for live checks. **TLS front end (phase 3a, lab only, off unless `serve --frontend-port N`):** `client_gateway/frontend/` runs mitmproxy 12.2.3 in-process (`runner.py`: own thread + asyncio loop, `127.0.0.1` only, `allow_hosts` = the routed AI hosts so every other CONNECT is tunnelled undecrypted, upstream TLS verified, CA in `<masking home>/frontend` whose SPKI hash the status shows for `--ignore-certificate-errors-spki-list`) and `addon.py` applies the wire adapters (`client_gateway/adapters/`) per flow with the shared `GatewayState` (`frontend_mode`: chat id `<adapter>:<key>`, marker, typed `masking on|off`; unknown bodies of a masked chat are blocked with a fixed text, SSE replies are rehydrated through `flow.response.stream`, outgoing WebSocket frames holding a client value are dropped). Nothing system-wide is installed by the front end itself; the desktop routing, the lifecycle and `setup` (below) own every system change.

**Front end, phase 3a.2 (lab only; R2 first, R9, Copilot `/responses`):** *R2*: no time window and no adapter-wide state makes a request of another chat masked or blocked any more (`GatewayState.frontend_mode`: a request with no chat key is masked only by its own marker; a WebSocket is checked only when its URL names a masked chat or the global mode is on; an upload that names no chat is only touched under the global mode). `FrontendAddon.decide` settles the mode in its own guarded step (failure: only a request that carries the install marker is blocked, everything else is forwarded untouched), `apply` carries it out (an error after the mode is known blocks a masked flow only). An unknown body is blocked only when THIS flow belongs to a masked chat (marker, a masked chat id in URL or body, the Copilot chat key, or the turn id). *Copilot identity* (`adapters/copilot.py`): no header carries a chat id, so the chat key is `c` + 16 hex of the SHA-256 of the FIRST typed turn (the `<userRequest>` block of the first user message that has one, whitespace normalised), the same for `/chat/completions` and `/responses` (chat space `copilot`, `adapters.base.chat_space_of`); the side calls of a turn (title, intent, summary: no first turn of their own) follow their chat through `x-interaction-id` (`frontend_note_turn` / `frontend_turn_chat`, memory only, 15 minutes). Known limits: two chats that open with exactly the same text share a key; a side call sent BEFORE the main call of its turn is untouched (it holds only that turn's typed text, in a chat that has not used Power BI yet); when Copilot compacts a chat and drops its first turn the key changes (the chat stays masked while a marked tool result is in the history; the captures hold no summary turn to detect); a body with no `<userRequest>` block has no key. `adapters/openai_responses.py` masks `input[]` text parts, `reasoning.summary`, `function_call.arguments` (parsed, keys and leaves), `function_call_output.output`, images; ids, `call_id`, `encrypted_content`, `tools` and the config keys pass verbatim (check-only); an `input_file` / `input_audio` part or another body shape fails closed (`unsupported_format`, counted as `format_changed`); the SSE reply is rehydrated split-safe per field (`response.output_text|refusal|reasoning_summary_text|reasoning_text.delta`, `response.function_call_arguments.delta`), every whole-object / whole-text event is unmasked as a tree, and the tail held at a field end leaves as one extra delta event in front of the event that ends the field. *R9*: after the adapter, `leakscan.residual_hits` scans the WHOLE outgoing body (raw text, joined JSON strings, URL-decoded forms) with `SessionMap.identity_hits` and blocks a hit as `residual_leak` (counter `residual_leak_blocked`; this includes values the adapters forward verbatim, such as a tool description; the Claude Code path is unchanged: its walk already checks every leaf and key); a masked reply in which the rehydrator knew no event shape bumps `rehydration_unrecognised` (also a `status` warning); `adapters/appinfo.py` records app and major-version labels per chat space (`status.frontend_apps`), a label that is not in `TESTED_APPS` shows as `untested_app_version:<space>` while masking continues. *Pseudonyms are not masked twice*: a string that carried the PBI marker is an MCP result the authority already pseudonymised, so `MaskContext.text` masks it with `premasked=True` (`SessionMap.mask_text`: a string shaped like one of our pseudonyms stays). Before, the front end gave the model a SECOND pseudonym for each MCP pseudonym; the model echoed it and the rehydrator turned it into the FIRST pseudonym (`tokens=` was true, the app still showed `CUS_61729`, and the next turn masked that visible pseudonym as a new value): the root cause of the lab session's Copilot display bug, found by replaying the recorded stream (`tests/test_copilot_display_replay.py`; the stream conversion itself was correct). The Claude Code request path (`masking.mask_request`) still masks a pseudonym-shaped string in outgoing text as a value of its own; with `client_gateway.mcp_masking` on it has the same double masking for MCP results (not changed here: `server/` and the production path are out of scope for this phase).

**Routed masking gateway, phases 3b-4b (desktop apps; rules R1 / R7 / R8 / R9 / R10).** *Front end + adapters*: `client_gateway/frontend/{runner,addon}.py` run mitmproxy in the gateway process; `client_gateway/adapters/` has one adapter per wire format (`openai_chat`, `openai_responses`, `chatgpt_web`; shared helpers in `common.py`, stream codecs in `streams.py`, typed `masking on|off` parsing in `control.py`, app labels in `appinfo.py`), selected by `registry.adapter_for`; Claude Desktop has no adapter yet. *Routing* (`client_gateway/routing/`): `ca.py` (name-constrained CA, DPAPI key, in-memory mitmproxy `CertStore`), `certstore.py` (`CAInstaller` + the `CertStoreBackend` interface, fake `MemoryCertStore`, real `WindowsCertStore`), `winproxy.py` (`InternetSettings` with the one-time backup, fake `MemoryInternetSettings`, real `WindowsInternetSettings`), `pac.py` (previous-decision classifier + PAC generator; policy-locked settings refuse, WPAD refuses unless `wpad.py` proved it direct), `upstream.py` (upstream trust bundle: Windows roots + certifi minus our CA; chain through a static proxy), `managed.py` (read-only Intune / Defender probe), `wpad.py` (read-only WinHTTP WPAD probe: `found` / `none` / `unknown`, fake `MemoryWpadProbe`; `none` lets `RoutingControl` / setup route with `AutoDetect` turned off, restored from the backup), `guard.py` (R8: a real backend is refused unless `FINVISION_MASKING_REAL_SYSTEM=1`, and always under pytest), `backends.py` (the `Backends` bundle; `real_backends()` is the single place the real ones are built). `tests/test_routing_guard.py` pins where real system calls may live. *Lifecycle* (`client_gateway/lifecycle.py`): `RoutingControl` turns the rule on only after the CA is trusted, the previous decision is known, the PAC is published and the front end listens, and restores the backup exactly when it turns it off; `Lifecycle` watches the attached Power BI MCP pids and ends the gateway (and the rule) with the last one; `cleanup_stale_rule` runs at every start. *Profiles* (`core/client_guard/profiles.py`): `generic` / `finvision` as data. *R9*: `leakscan.py` (residual scan of the whole outgoing body), per-adapter shape checks, `GatewayState.warnings()`. *Self-test*: `tools/masking_lab/selftest.py` (replay of every recorded fixture per named app; `00_Client_Mode selftest` runs it in a windowless child process, `lab.py selftest`). *Setup*: `client_gateway/setup_cmd.py` (`python -m client_gateway setup`): `read_facts` (read-only), `plan_lines`, `_apply_install` / `_apply_remove` through the guarded backends, `read_only()` wrappers for plan and status, `setup_record.json` as the undo record, injectable `SetupPaths` (config file, client configs) so tests never touch real files; `open_backends()` builds the real backends with the permission variable set only for the moment of construction. User-facing description: [Part II](#part-ii).

Tool call lifecycle:
```
MCP tools/call → mcp_app.call_tool (validate_input=False)
  → cached jsonschema validator (registry.get_input_validator) → invalid_arguments envelope on failure
  → rate limit
  → ToolDispatcher.dispatch_and_render_async
      → bounded pool (live / offline) via contextvars.copy_context().run
        (SERVER_LOOP + CANCEL_EVENT + CALL_LABEL published in server/request_context.py)
      → dispatch(): normalise args (confirmed → confirm_destructive, unknown_args notice)
                    → per-OPERATION read-only / confirm gates (server/op_classification.py)
                    → handler (writes under the process-wide write lock; model generation bumped)
                    → canonical error envelope → pagination → response_format → enrichment
      → response_pipeline.render_result (serialize once; spill > 450K to output/)
  → CallToolResult(content=[TextContent(+_meta maxResultSizeChars)], isError)
```

## IV.2 `server/registry.py`

- `ToolCategory` enum (11) — the allowed `ToolDefinition.category` values (`AUTONOMOUS` has no tools since v7.8). The v6 deferred-loading / discovery API (`CATEGORY_TOOLS`, `CATEGORY_INFO`, `load_category`, `get_discovery_info` …) was removed — no callers.
- `TOOL_TITLES` — display titles for every registered tool (pinned by a test).
- `TOOL_EXAMPLES` — canonical calls, emitted ONCE in the description (not at the schema root) and validated with jsonschema by the suite.
- Emitted schema = handler schema + `INJECTED_PROPERTIES` (`response_format`, `page_size`, `next_token` on every tool) + `TOOL_EXTRA_PROPERTIES` (per-tool app-level args, e.g. `17_Desktop_Bridge.inline_image`) + `confirm_destructive` **only** when `annotations.destructiveHint is True`.
- `get_all_tools_as_mcp()` is built once per registry state (cache dropped on `register()`); `get_input_validator(name)` caches one validator per tool (`check_schema` once); `declared_properties(name)`; `prebuild()`.

Handler registration template:
```python
def register_foo_handler(registry):
    registry.register(ToolDefinition(
        name="02_Foo_Operations",
        description="...",
        handler=handle_foo,
        input_schema={...},
        category="model",
        sort_order=200,
        annotations={"readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": False, "openWorldHint": True},  # all four, explicit
    ))
```
New operations must be added to `server/op_classification.py` (the suite fails on an unclassified op).

## IV.3 `server/dispatch.py` + `server/op_classification.py` + `server/request_context.py`

- `classify(tool, args) → "read" | "write" | "destructive"` for every tool × operation (02 per `object_type/operation`, 03 per `batch_operation`/changeset/ti_suite, dry-run aware only where the handler honours it). Unknown → `write`.
- Security gates (cached at init, refreshed by a `config.reload()` listener): `read_only_mode` blocks `write`+`destructive` per op (connect / list / debug reads keep working); `confirm_destructive` asks for `confirm_destructive=true` on `destructive` ops only. Legacy `confirmed` is folded into `confirm_destructive` with a `deprecated_args` notice.
- Args not declared in the emitted schema are passed through AND reported as `unknown_args`.
- Pools: `live` (8) and `offline` (4, PBIP/disk tools) `ThreadPoolExecutor`s; handlers run in a copied context so `request_ctx` and `SERVER_LOOP` are visible (progress / elicitation / roots / sampling / log forwarding bridges).
- Writes hold a process-wide `RLock`; a queued write re-checks its cancel event while waiting. After a successful write: `connection_state.bump_model_generation("<tool>:<op>")` (getattr-guarded).
- Timeouts (`core/config/tool_timeouts.py`, registered names): not started → `timeout` + `executed:false`; started read → `timeout` (retryable); started write → `timeout_outcome_unknown` + `may_still_be_running`, `operation_id`, `verify_with` (a VALID read per tool / `object_type` — e.g. `02_Model_Operations model/refresh` → `04_Run_DAX EVALUATE INFO.PARTITIONS()`; `03_Batch_Operations` → the `02` `list` of its object type; pinned by `tests/test_verify_with.py`). Timed-out/cancelled calls set the cancel event (`request_context.is_cancelled()` / `raise_if_cancelled()`) and call `connection_state.cancel_active_queries()` for live tools. Orphans are tracked: an identical write is refused (`write_in_progress`); ≥ `server.max_stuck_calls` stuck calls in a pool → new calls fail fast (`desktop_unresponsive` / `server_busy`). `get_stats()["stuck_calls"]` (resource `powerbi://state/dispatcher`).
- Pagination: `page_size` paginates the first `_PAGINABLE_KEYS` list present, else the largest top-level list; `paginated_key` reported (`null` + note when there is no list); a stale token is `success:false, error_type: stale_pagination_token`. `response_format=concise` never caps the paginated list.
- **P2-18:** large/sensitive arg values redacted in confirmation envelopes.

## IV.4 `server/response_pipeline.py`, `server/middleware.py`, `server/mcp_app.py`

- `render_result(tool, result)` runs on the worker thread: one strict-JSON serialization (NaN/Inf → null); ≤ 450K chars inline verbatim (no key compaction, no sampling, no truncation); larger → full JSON spilled to `output/<tool>_<ts>.json` (retention: newest 50, ≤ 7 days) + a ≤ 20K-char summary (all scalars, `_sizes`, `_preview`, `_full_output`). `HIGH_TOKEN_TOOLS` spill at `max_result_tokens × 4` chars instead of being hard-blocked. Spilled files are registered as `powerbi://export/*` resources.
- Canonical envelope: `core/validation/error_response.py` (`error_envelope`, `normalize_error_envelope` — folds `ok/err/err_type`, infers `error_type`).
- `middleware.py`: only `compact_response` is left (empty/null removal for the debug handlers; it no longer renames keys). The unapplied `compact_keys` / `expand_keys` / `truncate_if_needed` / `estimate_tokens` utilities were removed as dead code (deep review C-04).
- `mcp_app.py`: resources (`powerbi://docs/user-guide` (generated), `…/tool-reference`, `…/metadata/bpa-rules`, `…/state/{connection,roots,dispatcher}`) + templates (`powerbi://docs/tools/{tool}`, `…/visual-catalog/{type}`, `…/design-principles/{topic}`); prompts (`server/prompts.py`, structured steps validated against the schemas); `logging/setLevel` (declares the logging capability); opt-in `ImageContent` for `17_Desktop_Bridge inline_image=true`.

## IV.5 `server/pbip_cache.py`

- `PbipProject` typed dataclass: `project_info, model_data, typed_model, report_data, dependencies` + dict-style `__getitem__()` for backward compat
- `PbipModelCache` thread-safe LRU (default max 3 entries)
- **Scope (2026-09-22, G-06/H-13/A-13):** the path is resolved via `core.pbip.pbir_model.resolve_pbip` (`.pbip → artifacts[].report.path → definition.pbir byPath → .SemanticModel`). `.pbip`/`.Report` = that report + its model; `.SemanticModel` / model-only `.pbip` = the model + every sibling report bound to it; folder = its single model + bound reports (several models → `ValueError` "Ambiguous"). One entry per scope (`PbipScope.key`).
- **Staleness (A-12/G-17):** value signature per tree `(files, dirs, bytes, max file mtime_ns, max dir mtime_ns)` over the model + report `definition/` folders (os.scandir; prunes `StaticResources`/`CustomVisuals`/`.pbi`) + pointer files (`.pbip`, `definition.pbir`, `definition.pbism`) — deletes and mtime-preserving copies invalidate. Pre-parse signature stored, post-parse signature memoised, so an edit during the parse forces a re-parse.
- **Cost (G-16):** signature memoised for `signature_ttl_s` (default 5 s): a hit inside the window does no I/O. `pbip_utils` writes call `invalidate(path)` through `register_write_listener` (registered at import), so our own edits are never served stale; external edits (Desktop, git) are seen after ≤ TTL.
- **In-flight de-dup (A-35):** concurrent callers for the same scope + signature share one parse.
- `invalidate(path)` accepts an entry path, any file/folder inside a cached tree, or a parent folder (path-boundary match). WSL `/mnt/c/...` and Git-Bash `/c/...` normalised (T91/G-17). `normalize_pbip_path` keeps its legacy mapping (`.pbip` → parent folder); pass the ORIGINAL path to `get_or_parse` for report scoping.
- `get_or_parse(pbip_path) → PbipProject`

## IV.6 `server/handlers/_authoring_utils.py`

- `gate_and_wrap(args, operation, tool_name, dispatch_fn, pbip_path_or_defn, *, read_only_ops, catastrophic_ops, skip_reload_hint_on_error=True) → Dict`
  - Calls `require_autonomous_mode()` (skipped if op in `read_only_ops`)
  - Severity = CATASTROPHIC if op in `catastrophic_ops`, else DESTRUCTIVE
  - Returns gate failure early; merges info via `merge_gate_info()`
  - Attaches `reload_required` via `attach_reload_hint()` (skipped on error if flag set — P1-135)
- `coerce_num(value, key, default) → float` — safe MCP arg coercion

**P1-120 / P0-50..54:** Two gating patterns coexist (verify which a handler uses before "following the pattern"):
- **PBIR / disk-write handlers** (page, visual, theme, bookmark, report, qa, authoring) route through `gate_and_wrap()`.
- **Live-TOM / DAX write handlers** (`02_Model_Operations`, `02_TMDL_Operations`, `03_Batch_Operations`, `15_DAX_Refactor`) gate inline via `require_autonomous_mode()` + `merge_gate_info()` directly — they do NOT use `gate_and_wrap()`.
Both deliver consistent gating + reload hints; the split is by write surface (disk vs live model).

## IV.7 Singletons & shared state

### `connection_state` (`core/infrastructure/connection_state.py`)

- Singleton with `threading.RLock` (`_init_lock`)
- Lazy-loads 15+ managers on first connection: `QueryExecutor`, `BpaAnalyzer`, `DependencyAnalyzer`, all CRUD managers, `DAXInjector`, etc.
- Tracks `_is_connected`, PBIP folder path, query history (rolling 200 max), table ID↔name mapping cache (TTL 600s default)

## IV.8 `core/infrastructure/`

| Module | Class / purpose |
|--------|-----------------|
| `connection_state.py` | `ConnectionState` singleton, lazy manager init |
| `connection_manager.py` | Detect PBI instances (msmdsrv process scan via `psutil`, listening ports from `psutil.net_connections` with a netstat fallback), create TOM/ADOMD connections |
| `dll_paths.py` | `get_dll_paths()` → `DllPaths(dll_folder, core_dll, amo_dll, tabular_dll, adomd_dll)`; `load_amo_assemblies() → bool`; `load_adomd_assembly() → bool`; `get_trace_runner_path() → Optional[str]` (DaxExecutor.exe) |
| `query_executor.py` | DAX execution via ADOMD, timeouts, caching, traces. `OptimizedQueryExecutor` inherits two mixins split out of it (C-17, no behaviour change): `executor_catalog.py` (INFO.* / describe / search / list reads) and `executor_tom.py` (TOM enumerations). Methods that read the patched module globals `ADOMD_AVAILABLE` / `AMO_AVAILABLE` / `AdomdCommand` stay in `query_executor.py` |
| `rate_limiter.py` | Token bucket; per-tool limits in `default_config.json` |
| `limits_manager.py` | `init_limits_manager(config)` — memory/execution bounds |
| `multi_instance_manager.py` | Multiple concurrent PBI Desktop instances |
| `role_impersonation.py` | EffectiveUserName wrapping for RLS testing |
| `impersonated_executor.py` | Execute under role impersonation |
| `live_model_facade.py` | TOM facade for live model |
| `adomd_utils.py` | Shared ADOMD plumbing: per-connection command gate, value conversion |
| `trace_session.py` | In-process AMO trace session (pythonnet) for DAX Studio-parity server timings and plans; replaced the `DaxExecutor.exe` subprocess as the primary lane |
| `live_trace.py` | Long-lived trace session: capture every query while visuals are driven (`13_Query_Plan trace_start/trace_fetch/trace_stop`) |
| `query_trace.py` | Query trace runner for SE/FE timing; falls back to `DaxExecutor.exe` (timings only, no plans) |
| `xmla_target.py` | Read-only XMLA target for a PUBLISHED model (`01_Connection connect_xmla`; token from `MCP_PBI_XMLA_TOKEN` or the MSAL cache) |
| `offline_model.py` | Offline TMDL-folder lane: edit a PBIP semantic model through the real TOM serializer (`01_Connection connect_folder`) |

DLLs: one AMO/ADOMD build is bound per process, chosen by `dll_paths.select_amo_folder` (`MCP_PBI_AMO_DIR`, else the newest suitable of `lib/dotnet/amo-<version>/`, the legacy `lib/dotnet/` root copy, DAX Studio, Tabular Editor, SSMS; see `lib/dotnet/README.md`):
- AMO: `Microsoft.AnalysisServices.Core.dll`, `Microsoft.AnalysisServices.dll`, `Microsoft.AnalysisServices.Tabular.dll`
- ADOMD: `Microsoft.AnalysisServices.AdomdClient.dll`
- Fallback trace runner (C#): `core/infrastructure/dax_executor/bin/Release/net8.0-windows/win-x64/DaxExecutor.exe`. Only the Release runtime set under `bin/` is tracked; `obj/`, `*.pdb` and the localized `System.CommandLine` resource folders are build output and git-ignored.

Broad `except Exception` blocks in infrastructure code are intentional for CLR interop stability.

## IV.9 `core/operations/` — CRUD managers

| Class | File | Notes |
|-------|------|-------|
| `TableCRUDManager` | `table_crud_manager.py` | uppercase CRUD; helper `_get_server_db_model()` |
| `ColumnCRUDManager` | `column_crud_manager.py` | sort_by_column binding |
| `MeasureCRUDManager` | `measure_crud_manager.py` | format string expression |
| `RelationshipCRUDManager` | `relationship_crud_manager.py` | cross-filter behavior |
| `HierarchyCrudManager` | `hierarchy_crud_manager.py` | |
| `PartitionCrudManager` | `partition_crud_manager.py` | |
| `CalculationGroupManager` | `calculation_group_manager.py` | **no `Crud` suffix**; helper `_connect_amo_server_db()` returning `(server, db, Tabular)` |
| `FieldParameterManager` | `field_parameter_manager.py` | FP table CRUD + field row mutations |
| `RlsRoleCrudManager` | `rls_role_crud_manager.py` | |
| `RLSManager` | `rls_manager.py` | table filter + permissions |
| `AnnotationManager` | `annotation_manager.py` | object metadata |
| `CultureCrudManager` | `culture_crud_manager.py` | |
| `PerspectiveCrudManager` | `perspective_crud_manager.py` | |
| `NamedExpressionCrudManager` | `named_expression_crud_manager.py` | |
| `OlsCrudManager` | `ols_crud_manager.py` | |
| `QaLinguisticManager` | `qa_linguistic_manager.py` | synonyms + IncludeInQnA |
| `BaseOperations` | `base_operations.py` | abstract base, common TOM error handling + retry |
| `BatchOperationsHandler` | `batch_operations.py` | `run_atomic_batch()` — ONE connection + ONE SaveChanges per batch (`use_transaction` / `continue_on_error` honoured), changeset apply + TOM plan hash |
| `ModelRefreshManager` | `model_refresh_manager.py` | refresh orchestration |
| (cascade functions) | `rename_cascade.py` | lexer-driven DAX rename cascade (`core.dax.dax_rename`), calculated partitions only, restorable site records |
| (verify + rollback) | `rename_flow.py` | error snapshot → commit → new-error diff → exact rollback; rename preview hash |
| `ModelWriteLane` | `write_lane.py` | the ONE SaveChanges choke point: serialised, verify-after-write on client errors, loop pacing + health probe, model-generation bump 
| `write_preview` (F-01) | `write_preview.py` | uniform `preview=true`: runs the real manager inside `write_lane.preview_scope` (commit suppressed, images captured), diffs before / after TMSL with `journal_images`, rolls back with `safe_undo`; `plan_hash` binds (tool, args, model generation) -> `stale_preview` on apply; refresh previews as a plan |
| Write journal (JR-01) | `write_journal.py`, `journal_images.py`, `journal_plan.py`, `journal_apply.py`, `journal_manager.py` | `commit` journals every landed save (pre/post TMSL image per touched object) to `output/journal/<project>.jsonl`; `02_Model_Operations model history/diff/undo/replay/checkpoint_*` read it and re-apply images (pure planner + TOM reflection applier, ONE SaveChanges); a verified save appends a `flush` record; the dispatcher labels commits via `write_journal.call_scope` |
| (read-back) | `model_readback.py` | DMV/INFO read-back via the gated executor (object errors, measure/table/column existence, UDF rows) |
| (target resolution) | `target_resolution.py` | canonical table/measure/column identifiers for gate evidence AND executor |
| `FunctionCrudManager` | `function_crud_manager.py` | UDFs: DMV reads, TMSL writes (`Server.Execute`) |

**Naming convention warning — THREE styles coexist; verify by grep before referencing:**
- **`CRUD`** (uppercase): `MeasureCRUDManager`, `ColumnCRUDManager`, `TableCRUDManager`, `RelationshipCRUDManager` — helper `_get_server_db_model()` → `(server, db, model)`.
- **`Crud`** (mixed): `CultureCrudManager`, `PartitionCrudManager`, `RlsRoleCrudManager`, `OlsCrudManager`, `NamedExpressionCrudManager`, `HierarchyCrudManager`, `PerspectiveCrudManager`, `FunctionCrudManager`.
- **no suffix**: `CalculationGroupManager`, `RLSManager`, `AnnotationManager`, `FieldParameterManager`, `ModelRefreshManager`, `QaLinguisticManager` — `CalculationGroupManager` uses `_connect_amo_server_db()` → `(server, db, Tabular)`.

The CRUD managers above (Table/Column/Relationship) also expose `_apply_*(model, *, ...)` keyword-only mutate helpers (no save/disconnect) so `03_Batch_Operations` can run a single-connection atomic batch via `run_atomic_batch` (acquire once → apply all → SaveChanges once, UndoLocalChanges on failure). `MeasureCRUDManager` has the same primitives (`_apply_upsert_measure` / `_apply_delete_measure` / `_apply_rename_measure` / `_apply_move_measure`).

## IV.10 `core/dax/`

- `context_analyzer.py` — `ContextAnalyzer`
- `context_debugger.py` — step through DAX with variable tracking
- `call_tree_builder.py` — `CallTreeBuilder`
- `analysis_pipeline.py` — multi-analysis orchestration
- `dax_reference_parser.py` — `DaxReferenceIndex`, `parse_dax_references()`
- `dax_injector.py` — inject DAX into TOM
- `dax_formatter_client.py` — daxformatter.com (canonical Azure URL)
- `local_formatter.py` — deterministic offline formatter behind `format_dax engine=local` (lexer -> comment-attached atoms -> tree -> width-driven layout; guarded by `token_equivalent` + comment-sequence check; F-23)
- `clone_detector.py` / `udf_support.py` — normalised-token clone groups + UDF / calc-group-item proposals behind `05 find_clones` and `15 extract_udf`; UDF compatibility-level / function-name probes (F-15)
- `explain.py` — structured measure explanation behind `05 explain` (call tree, filter modifiers, transitions, VARs; F-33)
- `dax_validator.py` — syntax validation
- `dax_utilities.py` — name normalize, bracket/unbracket, escape M
- `code_rewriter.py` — pattern-match DAX rewrite engine
- `rls_rewriter.py` — wrap CALCULATE + role filter for RLS testing
- `dax_best_practices.py`, `dax_rules_engine.py`
- `vertipaq_analyzer.py`, `se_event_analyzer.py`, `vertipaq_storage_report.py`
- `calculation_group_analyzer.py`
- `callback_detector.py`
- `dmv_probes.py`
- `knowledge/function_db.py` — function DB (286 fns incl. visual-calc/calendar-TI/INFO.VIEW.*/TABLEOF; new `visual_calculation` category)
- `tokenizer/` — DAX tokenization
- `analyzer/rules/` — live Python rules consumed by `DaxUnifiedAnalyzer` (NOT BPA). Includes schema-aware model rules: `DirectMeasureReferenceRule` (uses `schema.classify_ref`), `AllCrossFilteredRule` (PY_ALL_VS_ALLCROSSFILTERED), `RlsInteractionRule` (PY_RLS_BIDIRECTIONAL_INTERACTION).
- `analyzer/models.py` — `AnalysisContext` (now carries optional `schema: ModelSchema`) + `ModelSchema` (measure/column/table name sets, relationships, roles; `is_measure`/`is_column`/`classify_ref`/`has_limited_relationships`).
- `analyzer/schema_builder.py` — `build_model_schema(query_executor)` builds `ModelSchema` from INFO DMV queries; never raises (empty schema on failure → static-only degrade). Populated in `05_DAX_Intelligence` (analysis_mode=all).
- `optimizer/` — optimization strategies (note: `optimizer/pipeline.py` reads measure DAX via `execute_dmv_query`)

> **`core/dax/code_rewriter.py` is a low-level rewrite engine and is NOT the `15_DAX_Refactor` backing.** The five `15_DAX_Refactor` transforms live in the SEPARATE top-level package **`core/codemods/`** (`plan_hash.py`, `transaction.py`, `transforms/`).

## IV.10b Other `core/` packages (not detailed above)

These top-level `core/` packages are live and used but only summarized here; read the package for detail:
`core/debug/` (backs `09_*` — `visual_query_builder` + its `visual_query_types` / `visual_report_reader` / `visual_measure_lookup` parts, `debug_operations` + `debug_types` / `analysis_ops` / `document_ops`), `core/orchestration/` (analysis/query/documentation orchestrators + agent_policy; `AnalysisOrchestrator` inherits `orchestrator_simple` / `orchestrator_report`), `core/graph/` (backs `16_Dependency_Graph`), `core/query_plan/` (backs `13_Query_Plan`), `core/codemods/` (backs `15_DAX_Refactor`), `core/calc_group_migration/` (used by model handler calc-group detect/scaffold), `core/model/` (dependency_analyzer, model_exporter), `core/execution/` (dmv_helper), `core/performance/` (used by connection_state), `core/client_guard/` (client-masking engine/detector used by the gateway and `00_Client_Mode`), `core/aggregation/` (backs aggregation_analysis), `core/documentation/` (Word: data_collector, word_generator, snapshot_manager), `core/validation/` (pagination_helpers, error_handler). (`core/svg/` was removed in v7.6.)

### Modules not described in the tables above (checked against the tree 2026-10-03)

| Module | Purpose |
|--------|---------|
| `server/arg_aliases.py` | Canonical argument vocabulary + legacy-alias normaliser |
| `server/arg_coercion.py` | Lenient type coercion of tool arguments before schema validation |
| `server/tool_schemas.py` / `tool_surface.py` / `tool_profiles.py` | Input schemas; which args each tool advertises in `tools/list` (`DEFERRED_ARGS`); tool profiles (`MCP_PBI_PROFILE=model\|report`) |
| `server/resources.py` / `dax_query_rules.py` | MCP resources (`powerbi://docs/...`) and the DAX query-rules resource text |
| `server/elicitation.py` / `progress.py` / `roots.py` / `sampling.py` | MCP elicitation, progress, roots and sampling bridges for sync handlers on worker threads |
| `server/log_forward.py` / `logging_setup.py` | Python logging forwarded to the client as `LoggingMessageNotification`; process log setup (stderr level from `server.log_level`, rotating file) |
| `core/autonomous/edit_ledger.py` | What this server changed that Desktop has not seen yet (gates `reload`/`save`) |
| `core/analysis/model_settings_validator.py` | Model-level settings checks (integrity scope of `06_Analysis_Operations`) |
| `core/pbip/pbir_field_refs.py` / `report_format.py` | Field references in PBIR JSON; PBIR vs legacy report-format detection |
| `core/tmdl/tmdl_tree.py` / `tom_check.py` | Indentation-scoped TMDL document tree; offline TOM deserialization check |
| `core/pbip/pbip_{bookmark,theme_compliance}_html_generator.py`, `core/utilities/diagram_html_generator.py`, `core/utilities/pbip_dependency_html_generator.py` | HTML report generators; all embed names/JSON through `script_json` + `html.escape` + single-pass `fill_template` (C-06) |

## IV.11 `core/tmdl/`

**Single canonical parser:** `core/tmdl/unified_parser.py` (`UnifiedTmdlParser`/`UnifiedParser`) produces typed dataclasses from `core/tmdl/models.py` (`TmdlModel`, `TmdlTable`, `TmdlColumn`, `TmdlMeasure`, `TmdlPartition`, `TmdlHierarchy`, `TmdlCalculationItem`, `TmdlAnnotation`, `TmdlHierarchyLevel`).

The old `core/tmdl/tmdl_parser.py` facade was removed (dead); `core/pbip/pbip_model_analyzer.py` is a thin facade delegating to the unified parser.

Other modules:
- `tmdl_text_generator.py` — generate TMDL text from models
- `tmdl_semantic_diff.py` — diff two TMDL states
- `script_generator.py` — C# scripts for model updates
- `measure_migrator.py` — migrate measures between tables
- `bulk_editor.py` — batch TMDL mutations
- `validator.py` — syntax/reference/naming validation

## IV.12 `core/pbip/`

PBIP analysis pipeline: `PbipProjectScanner` → `TmdlModelAnalyzer` → `PbipDependencyEngine` → `PbipProject`. Multi-report merging intersects unused columns/measures and prefixes page names for disambiguation.

| Module | Purpose |
|--------|---------|
| `pbip_project_scanner.py` | `PbipProjectScanner.scan_repository(path)` — reports + models linked through `definition.pbir` `byPath` (thin reports, shared models, model-only `.pbip`, byConnection); accepts a folder, `.pbip`, `.Report`, `.SemanticModel` or `definition/` path |
| `pbir_model.py` | Pure PBIR/PBIP shape accessors (2026-09-22): `resolve_pbip`, `read_dataset_reference`, `find_reports_bound_to_model`, `pages_json_path`, `iter_visuals`, `get/set_visual_filters` (top-level `filterConfig`), `filter_polarity`, `get/set_page_interactions` (`visualInteractions`), `bookmark_group_children` (string ids), `get_bookmark_references`, `encode/decode_literal`, `ADVANCED_FILTER_COMPARISON`. Golden fixture: `tests/fixtures/pbir_golden/` |
| `pbip_model_analyzer.py` | `PbipModelAnalyzer` — parse model.bim |
| `pbip_dependency_engine.py` | `PbipDependencyEngine` — measure→measure, measure→column, column→measure, visual deps. **P1-49:** `column_to_measure` dict mixes column→measure AND column→calc-column referrers (deferred refactor) |
| `pbip_enhanced_analyzer.py` | Higher-level analyzer combining model + deps |
| `report_operations_engine.py` | `ReportOperationsEngine` — rename, rebind |
| `pbip_report_analyzer.py` | `PbipReportAnalyzer` — parse report.json |
| `authoring/id_generator.py` | Unique visual/page IDs |
| `authoring/pbir_add_visuals.py` | Bulk visual creation via pbir CLI subprocess + coord translation + role pre-flight |
| `authoring/pbir_executor.py` | `write_visual` / `delete_visual_files` canonical persistence |
| `authoring/reference_cleanup.py` | `strip_dangling_interactions` / `strip_dangling_visual_refs` |
| `filter_engine.py` | `FilterEngine` — page/visual filter CRUD |
| `bookmark_engine.py` | `BookmarkEngine` |
| `theme_engine.py` | `ThemeEngine` |
| `conditional_formatting_engine.py` | `ConditionalFormattingEngine` |
| `page_operations_engine.py` | `PageOperationsEngine` |
| `field_binding_engine.py` | `FieldBindingEngine` |
| `visual_alignment_engine.py` | `VisualAlignmentEngine` |
| `visual_calculations_engine.py` | `VisualCalculationsEngine` |
| `extension_measures_engine.py` | `ExtensionMeasuresEngine` |
| `pbip_visual_validator.py` | Validates visual definitions |
| `pbip_theme_compliance_analyzer.py` | Theme JSON compliance |
| `pbip_html_generator.py`, `pbip_bookmark_html_generator.py` | HTML reports |
| `pbip_bookmark_analyzer.py` | `PbipBookmarkAnalyzer` |
| `pbip_git_analyzer.py` | Git diff analysis |
| `cascade_rename.py` | `CascadeRenameEngine` post-rename PBIR rewrite |

## IV.13 `core/analysis/`

- `bpa_analyzer.py` — `BPAAnalyzer.run(tmsl, cfg=None, *, extra_rules=None) -> BPARunResult`: a PURE run (no per-run state on the shared instance; `violations`, `notes`, `coverage`) over one TMSL snapshot, per-rule time budget, bounded per-run equality index. Rule sets (`RULESET_FILES`): the live default is `config/bpa_rules_comprehensive.json` + built-ins (~72 rules, `ruleset=default`); `core/bpa.json` is Microsoft's Tabular Editor BPARules (`ruleset=microsoft`, or `all` for both). `load_rules_file(path, existing_ids)` loads an external TE file per call and reports `added / skipped_duplicate_ids / invalid / unsupported`.
- `bpa_expression.py` — tokenizer + recursive-descent parser + evaluator for the Tabular Editor Dynamic-LINQ subset (`compile_expression`, `Evaluator`, `compile_regex` = .NET regex → Python incl. inline `(?i)` and C#-doubled backslashes). Anything it cannot evaluate makes the rule *unsupported* (reported in coverage), never a silent never-match.
- `bpa_model.py` — `ModelView(tmsl_model)`: TOM-like object graph over TMSL (all TE scopes: tables, data/calculated columns, measures, hierarchies, partitions, relationships, calculation items, KPIs, roles, table permissions, perspectives, expressions…) with TMSL defaults (e.g. `fromCardinality=Many`) and `DaxText.sanitized` (comments/strings blanked for regex rules).
- `info_normalize.py` — INFO.* / TMSL enum and column normalisation shared by `06 simple`, topology and the Word collector.
- `column_usage_analyzer.py` — `ColumnUsageAnalyzer`
- `m_practices.py` — M (Power Query) best-practice checks. **12 rules (M001–M012)**; per-expression detection extracted to the pure `detect_m_issues(name, expr)` helper (unit-testable). M005 volatile-fn fold-break, M006 filter-after-merge, M007 hardcoded secret, M008 per-row lookup in AddColumn, M009 Table.Sort, M010 Csv/Excel absolute path, M011 NativeQuery without EnableFolding, M012 Table.Combine.
- `star_schema_validator.py` — `StarSchemaValidator.validate(model)` → topology findings (fact-to-fact, snowflake, disconnected, missing date table). Pure; wired into `06_Analysis_Operations:full`.

## IV.14 `core/comparison/`

- `tmsl_normalizer.py` — `normalize_tmsl(model, name=None, compatibility_level=None)`: the full TMSL of each model in one comparable shape (the compare no longer exports TMDL to temp files)
- `model_diff_engine.py` — `ModelDiffer` deep diffs of two normalised models: `lineageTag`-first matching (renames, cross-table moves), token-wise DAX compare (`dax_equivalent(a, b) -> (equivalent, cosmetic)`; cosmetic edits listed, not counted), object-level `total_changes`
- `model_comparison_orchestrator.py`
- `model_diff_report.py` — delta summary, item-level diffs
- `model_narrative.py` — prose narrative

## IV.15 `core/utilities/`

- `json_utils.py` — `MCPJsonEncoder`: datetime→ISO8601, Decimal→float, UUID→str, set→sorted list, bytes→`<bytes:N>`, unserializable→`<unserializable:ClassName>`
- `mermaid_utils.py`
- `diagram_html_generator.py` — Mermaid + D3 wrap
- `pbip_dependency_html_generator.py`
- `pbip_utils.py` — path normalization (WSL `/mnt/c/...`, Git-Bash `/c/...` → Windows); safe PBIR writes: `save_json_file` / `save_json_minimal` (unique `mkstemp` temp + fsync + `os.replace` retried on WinError 5/32, original BOM/CRLF/trailing-newline/indent/raw-UTF-8 traits preserved), `file_lock(path)` (process-wide re-entrant per-path lock), `update_json_file(path, mutate_fn)` (locked read-modify-write), `register_write_listener` / `notify_path_written` (PBIP cache invalidation hook)
- `business_impact.py`
- `type_conversions.py` — CLR ↔ Python types

## IV.16 `core/autonomous/`

Trimmed in v7.8 — the close/reopen/save/snapshot/audit lifecycle modules and the `12_Autonomous_Workflow` tool were deleted. What remains is the live-iteration + shared-gating surface:

| Module | Purpose |
|--------|---------|
| `desktop_bridge.py` | Subprocess wrapper around the MS `powerbi-desktop` CLI: `run_bridge`, `select_instance` (PID selection), `resolve_page_id`, and `op_status`/`op_reload`/`op_save`/`op_screenshot`/`op_screenshot_all`/`op_reload_and_screenshot`/`op_resolve_page`. Per-PID locks, ~5 s status TTL cache (`invalidate_status_cache`), post-reload settle retry, per-visual crops (group-aware absolute bounds via `tools/visual_catalog/{inventory,crop}`; the v1 crop locates the page rectangle inside the viewport capture first, and `report.snapshot.capture/v2` region crops are used when the live manifest lists them), `run_bridge_lib` (the `tools/bridge_lib.mjs` node helper over the CLI library: model reload + region capture), `reload include_model`, `flag_stuck` bookkeeping (BR-03). Backs `17_Desktop_Bridge` + the debug loop. |
| `model_change_classifier.py` | BR-04: `extract_signature` (TOM `Database` → plain dict), `diff_signatures` (PURE: every live-vs-disk difference tagged `safe` = metadata-only), `classify_for_reload` (disk `TmdlSerializer` read + live read → `metadata_only` / `unchanged` / refusal `model_reload_unsafe_change` / `tmdl_invalid` / `model_change_unclassified`), `verify_after_reload` (`applied` / `mismatch`). Gates `reload include_model=true`. |
| `ui_automation.py` | `PbiDesktopUIAutomation` — pywinauto Ctrl+S save for `op_save` (the MS CLI has no save subcommand). Window ranking handles modern suffix-less titles; refuses to send the global keystroke unless the PBI window is confirmed foreground (`focus_failed`). |
| `image_optimize.py` | `optimize_png`/`resized_size` — downscale screenshots to Claude's high-res vision tier (2576 px / 4784 visual tokens; Anthropic reference algorithm) so PNGs aren't crushed server-side. Used by the bridge screenshot ops. |
| `gating.py` | `require_autonomous_mode(op_name, args, severity, evidence=None) → Optional[Dict]`. `Severity` enum: DESTRUCTIVE, CATASTROPHIC. Policy-only (no mode); break-glass `is_confirmed(args)` (strict bool `confirm_destructive`, alias `confirmed`); block `error_type: confirm_destructive_required`. Only the handler-computed `evidence` kwarg is consulted (never `args['evidence']`, review C-02), and only when `evidence.proven` + `provenance.source == 'in_process'` (see `evidence.py`). `current_policy()` re-reads the env each call; `invalidate_policy_cache()` for tests. |
| `reload_hints.py` | `attach_reload_hint(result, tool_name, operation)`; `OPS_REQUIRING_RELOAD: Set[Tuple[str, str]]`. Hint/chain point at `17_Desktop_Bridge:reload` + `:screenshot`; TMDL hints lead with `reload_and_screenshot include_model=true` (metadata-only edits) and fall back to close-without-saving + `open`. |
| `clr_errors.py` | `extract_clr_exception_chain(exc, max_depth=8)` + `format_refresh_error` — unwrap `System.AggregateException` + `InnerException`. Imported by 6 CRUD managers + `model_refresh_manager`. |
| `loops/debug_loop.py` | Closed-loop DAX debug: debug → fix → bridge reload → screenshot → verify (no close/reopen). Backs `09_Debug_Operations:debug_loop`. **v7.14.1:** `live_tom` resolves the measure's owning table via `_resolve_measure_table` (parses `Table[Name]` or looks up a bare name via `enumerate_measures_tom`; defers if unresolvable — never applies with an empty table name), runs the optimizer compute-only (`dry_run=True`) with `MeasureApplier` as the single write path, and records `original_dax` + `revert_hint` on every applied fix (no auto-revert; the next loop iteration re-verifies). SEMANTIC anomalies (blank/wrong value) escalate to the Tier-2 LLM-in-the-loop path (`_default_semantic_llm_apply` via `server.sampling.sample_text` + `DaxValidator` syntax gate) **only when `allow_llm_fix=true` is passed** (`DebugLoopConfig.allow_llm_fix`, exposed as a debug_loop arg; default false → deferred marker with the proposal, pre-v7.14 behaviour). `fe_dominated` always takes the deterministic optimizer path. |

## IV.17 Configuration

`config/default_config.json` (override via `config/local_config.json`, gitignored). Key sections:

| Section | Keys |
|---------|------|
| `server` | `log_level` (stderr level), `live_workers`=8, `offline_workers`=4, `max_stuck_calls`=4 (dispatcher pools) |
| `performance` | cache_ttl=600s, cache_max_entries=2000, max_query_timeout=60s, command_timeout_seconds=55, table_mapping_cache_ttl=600s, reference_index_ttl=30, `perf_export_max_captures`=30 / `perf_export_max_mb`=500 (retention of `exports/perf/`, pruned on every capture; `0` disables a limit) |
| `query` | max_rows_preview=500, max_dax_query_length=50000, default_page_size=10, max_page_size=200, max_result_tokens=25000, preview_sample_size=30 |
| `model_write` | live-TOM write lane: loop_pause_s, health_check_in_loops, health_timeout_s, verify_renames, catastrophic_refresh_tables=10, journal_enabled, journal_max_bytes |
| `features` | `enable_bpa` |
| `bpa` | max_rules=120, max_tables=60, max_seconds=45, per_rule_max_ms=400 (rules run serially with adaptive timeouts) |
| `rate_limiting` | profile=`"balanced"`, global_calls_per_second=30 (burst 60), per-tool limits (`04_Run_DAX`: 10, `06_Analysis_Operations`: 1) |
| `security` | read_only_mode=false, confirm_destructive=false, allow_data_read=true, rules=[] (F-22 / POL-01: `server/security_policy.py`) |
| `tool_timeouts` | per-tool overrides keyed by REGISTERED tool name (`"04_Run_DAX": 60`, `"06_Analysis_Operations": 300`, …); unknown keys are logged and ignored |
| `debug` | pbip_freshness_minutes=5, vqb_cache_ttl_seconds=300, filter/variable truncation, max_measures_shown, max_query_result_rows, audit_max_rows_per_visual=20 |
| `client_gateway` / `client_guard` | `client_gateway.port`=8890, `default_mode`, `mcp_masking` (false; `setup` turns it on), `pbi_chats_only` (true), `authority_wait_s` (30), `routing` (false; `setup --desktop-apps` turns it on), `stop_when_unused` (true: the reaper), `frontend_port` (8891), `client_guard.ner.max_s` (8) / `wait_s` (5), `client_guard.gateway_policy`, `client_guard.profile` (`generic` default; env `FINVISION_MASKING_PROFILE` wins; `finvision` = the pre-profile behaviour), `client_guard.shape_packs` (e.g. `["be"]`) (the gateway reads further keys with call-site defaults) |

Config singleton: `from core.config.config_manager import config` — RLock-protected (dispatcher caches `_read_only`, `_confirm_destructive` at init to avoid hot-path contention — Audit T93; `config.reload()` notifies listeners registered with `add_reload_listener`, which refreshes those cached flags). `load_json` tolerates a UTF-8 BOM.

## IV.18 Environment variables

| Var | Values | Purpose |
|-----|--------|---------|
| `MCP_AUTONOMOUS_GATE_POLICY` | `OFF` \| `LIGHT` (default) \| `WARN` \| `STRICT` | Autonomous gate policy (see §7.1) |
| `PYTHONIOENCODING` | `utf-8` | Set in `manifest.json` MCP env |

## IV.19 Build & packaging

- `manifest.json` (v0.2): entry `src/run_server.py`, command `${__dirname}/venv/Scripts/python.exe ${__dirname}/src/run_server.py`, env `PYTHONIOENCODING=utf-8`, version 7.16.0, 28 tools declared. The server version is `src/__version__.py` = `manifest.json` = `python/pyproject.toml` (pinned by `tests/test_requirements_pyproject_agree.py`); git commit labels `v7.17`-`v7.19` were internal milestones, not releases.
- Dependencies: `requirements.txt` mirrors `python/pyproject.toml` (core + `gateway` extra); optional gateway engines (rapidocr, GLiNER) are in `requirements-optional.txt` (`gateway-ocr` / `gateway-ner` extras).
- **No automated `.mcpb` packaging script exists yet** (the previously-documented `build\package.bat` was never present). Dev-env provisioning is `"MCP Server Setup.bat"` at the repo root; `.mcpbignore` lists bundle exclusions for when packaging is added (it keeps `tools/bridge_lib.mjs`, the `include_model=true` reload helper).
- Bundled venv: `venv/Lib/site-packages/` (Windows) or `venv/lib/*/site-packages/` (Unix)

## IV.20 Test infrastructure

- `run_tests.py` — canonical entrypoint. Flags: `--fast` (skip `@pytest.mark.slow`), `--verbose`, `--cov` (HTML + terminal), `--file <basename>` (basename match with disambiguation), `--marker EXPR`, `-- <pytest passthrough>`. Exits with pytest exit code.
- `tests/conftest.py` — prepends repo root to `sys.path`; auto-use fixture `_reset_gate_policy_cache()` invalidates policy cache before/after every test.
- Fresh worktrees have no editable install; conftest's `sys.path` insert is critical.

### Test discipline

- **One-file-per-feature/module**: every new core engine, MCP op, or handler dispatch path ships its own `test_<thing>.py` in the same change.
- **Test the dispatch shell**, not just the manager. Mock at the manager boundary via `monkeypatch.setitem(_handlers, ...)` with `MagicMock`.
- **Skip live AMO/CLR/network.** Mock at the boundary or test pure decision logic with dict inputs.
- **CLR exception unwinding:** build a `FakeClrException` class with `GetType()`/`Message`/`InnerException`/`Source`/`HResult`/`StackTrace` attrs.
- **Filesystem fixtures:** build a minimal repo/project under `tmp_path`; don't depend on shared static fixtures unless they already exist.
- **TOM lazy imports** inside method bodies: pattern is `monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", fake_tom)` AND chain `Microsoft` + `Microsoft.AnalysisServices` parent modules (Python's `import x.y.z as Z` resolves by attribute lookup on the parent).
- Confirm baseline-green BEFORE adding/modifying tests.
- **Consolidation:** merge two test files only if they target the same dispatch shell or extraction helper, both <100 lines, with overlapping fixtures. Keep separate when files mirror distinct production modules.

### Coverage gap workflow

When asked to "review the test suite":
1. Run the suite, confirm green.
2. List handlers/core modules without dedicated `test_*.py`: `for f in core/*/*.py; do bn=$(basename "$f" .py); grep -rql "$bn" tests/ || echo "MISSING: $f"; done`
3. Cross-reference with `git log --since="3 months ago" -- core/ server/` — recently-changed code without tests is the highest-priority gap.
4. Propose 3–5 specific files; act on user's go-ahead. Prioritize: (a) recently-patched modules with documented bugs, (b) handler dispatchers, (c) bulk-op invariants.

---

<a id="part-v"></a>
# Part V — PBIR reference

Two halves. First the **PBIR JSON reference** (the on-disk file format: `visual.json` keys, chrome vs internal objects, field binding, literal encoding, selectors, groups), then the **pbir CLI reference** (every command, flag, visual type, container, conditional-formatting structure, batch spec v2 and the gotchas; documented at 0.9.9 and revalidated against 0.9.25, see [Changes in 0.9.25](#changes-in-0925)).

The per-type pages of [docs/visual-catalog/](visual-catalog/INDEX.md) link here with stable per-type anchors: `#cli-<visualType lowercased>` (row of the CLI visual type catalog, [§5](#5-visual-type-catalog-every-type--roles)) and `#json-<visualType lowercased>` (row of the JSON roles-per-type table, [§5](#5-roles-per-visual-type)).

## PBIR JSON reference

Comprehensive reference for the Power BI Enhanced Report (PBIR) JSON schema, focused on authoring visuals programmatically.

All examples are real lines from the **R0101 Wealth Reporting** reference project unless noted otherwise. Verified against `pbir 0.9.9` (`pbir schema describe`) and the `pbip:pbir-format` skill (marketplace v0.26.0).

---

### 1. File tree

#### 1.1 The PBIP project

```
<project root>/
├── <report>.pbip                     # Tiny pointer file (thick report launcher)
├── <report>.Report/                  # The PBIR report
│   ├── .platform                     # Fabric Git integration metadata
│   ├── definition.pbir               # REQUIRED. Semantic-model binding
│   ├── StaticResources/              # Themes, images, custom-visual bundles
│   └── definition/                   # The actual report content
│       ├── version.json              # PBIR format version
│       ├── report.json               # Report-level config (theme, filters, settings)
│       ├── reportExtensions.json     # Optional: thin-report DAX + visual calcs
│       ├── pages/
│       │   ├── pages.json            # pageOrder[] + activePageName
│       │   └── <page id>/            # hex ID, NOT the display name
│       │       ├── page.json         # Page config
│       │       └── visuals/
│       │           └── <visual id>/  # hex ID
│       │               ├── visual.json
│       │               └── mobile.json    # Optional
│       └── bookmarks/                # Optional
│           ├── bookmarks.json
│           └── <id>.bookmark.json
└── <model>.SemanticModel/            # TMDL model (see `pbip:tmdl` skill)
    └── definition/
        ├── model.tmdl
        ├── database.tmdl
        └── tables/<table>.tmdl
```

#### 1.2 `.pbip` — Desktop launcher

Tiny file that tells Power BI Desktop what to open. Not part of the definition.

#### 1.3 `definition.pbir` — Semantic-model binding

R0101 (`definition.pbir`):

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
  "version": "4.0",
  "datasetReference": {
    "byPath": { "path": "../S01-SemanticModel.SemanticModel" }
  }
}
```

Two forms:

| Form | Use case | Shape |
|---|---|---|
| `byPath` | Thick report (PBIP) — local sibling model | `{"byPath": {"path": "../Model.SemanticModel"}}` |
| `byConnection` | Thin report — Fabric/Service model | `{"byConnection": {"connectionString": "Data Source=powerbi://..."}}` |

#### 1.4 `version.json` — PBIR format version

R0101: `{"version": "2.0.0"}`. Required. Do not hand-edit — use `pbir schema upgrade`.

#### 1.5 `pages/pages.json` — Page index

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
  "pageOrder": ["b81ad76bede761849700", "e3e0acb98bc3c532921b", ...],
  "activePageName": "a06a057745f21fa41f3d"
}
```

The order in `pageOrder` == the order in the Desktop page-tab bar. A page folder on disk that is **not** listed is invisible in Desktop but still loaded (lint risk; clean up after page deletions).

#### 1.6 Directory names are hex IDs, not display names

Power BI Desktop generates 20-hex-char folder names (`b81ad76bede761849700`). The display name lives inside `page.json` at `displayName`. `pbir` CLI accepts glob patterns via path DSL with `.Page` / `.Visual` TYPE suffixes (e.g. `Report.Report/b81ad76bede761849700.Page/4b6de49b2a35d1e8e81f.Visual`) — these suffixes are **NOT** part of the folder name on disk.

#### 1.7 `SemanticModel/definition/` — TMDL

The model side is TMDL text format. Out of scope here — see the `pbip:tmdl` skill. The only cross-cutting concern: measure/column/table names referenced from PBIR `Entity` and `Property` strings must match the TMDL exactly.

---

### 2. `visual.json` — exhaustive top-level keys

Schema: `visualContainer/2.7.0/schema.json`. Real example root (R0101 `definition/pages/fee7097e74e40a801acf/visuals/4b6de49b2a35d1e8e81f/visual.json`):

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
  "name": "4b6de49b2a35d1e8e81f",
  "position": { "x": 0, "y": 0, "z": 0, "height": 160, "width": 930, "tabOrder": 2000 },
  "visual": {
    "visualType": "cardVisual",
    "query": { "queryState": { ... }, "sortDefinition": { ... } },
    "objects": { ... },
    "visualContainerObjects": { ... },
    "drillFilterOtherVisuals": true
  },
  "parentGroupName": "14e6514ef98f56ec0d3f"
}
```

| Key | Type | Purpose | When set |
|---|---|---|---|
| `$schema` | string | Schema URL (`visualContainer/2.7.0`) | Always. Do not omit. |
| `name` | string | Visual ID (20-char hex, must equal folder name) | Always |
| `position` | object | Page-absolute coords: `x, y, z, height, width, tabOrder` | Always |
| `visual` | object | Data visual (see below) | Exclusive with `visualGroup` |
| `visualGroup` | object | Container group (see §9) | Exclusive with `visual` |
| `parentGroupName` | string | ID of the `visualGroup` this belongs to | Only when nested inside a group |
| `filterConfig` | object | Visual-level filters (field + filter expression) | Optional; see §2.5 |
| `isHidden` | boolean | Hide the visual at design time (shows in bookmarks) | Default absent == visible. Observed in R0101 `pages/a06a057745f21fa41f3d/visuals/e3d2b10178453244b705/visual.json`: `"isHidden": true` |
| `howCreated` | string | `"User"`, `"InsertVisualButton"`, `"PasteAsValue"`, ... | Set by Desktop on creation |

#### 2.1 `position`

- `x, y` — page-absolute pixels (page is sized by `page.json.width` / `height`). Children of a group are ALSO page-absolute on disk, but presented LOCAL to the group in Desktop; pbir CLI reports group-local positions — mind the translation.
- `z` — stacking order (higher == on top). In R0101 values range 0..15012.
- `tabOrder` — keyboard-focus order in published report. Multiples of 1000 by convention.
- Values are doubles; `x: 527.14285714285711` is normal Desktop output.

#### 2.2 `visual.visualType`

One of 49 strings. From `pbir schema types`:

```
actionButton, advancedSlicerVisual, aiNarratives, areaChart, azureMap,
barChart, bookmarkNavigator, card, cardVisual, clusteredBarChart,
clusteredColumnChart, columnChart, decompositionTreeVisual, donutChart,
filledMap, filter, funnel, gauge, group, hundredPercentStackedAreaChart,
hundredPercentStackedBarChart, hundredPercentStackedColumnChart, image,
keyDriversVisual, kpi, lineChart, lineClusteredColumnComboChart,
lineStackedColumnComboChart, listSlicer, map, multiRowCard, pieChart,
pivotTable, pythonVisual, qnaVisual, rdlVisual, ribbonChart, scatterChart,
scorecard, scriptVisual, shape, shapeMap, slicer, stackedAreaChart,
tableEx, textSlicer, textbox, treemap, waterfallChart
```

Gotchas: **`pivotTable`** is the matrix. **`cardVisual`** is the new multi-row callout card; **`card`** is the legacy single-value card. **`columnChart`** covers waterfalls and some 3rd-party column visuals in PBIR output.

#### 2.3 `visual.query` — field bindings

```json
"query": {
  "queryState": {
    "<roleName>": {
      "projections": [ { ...binding... }, ... ],
      "fieldParameters": [ ... ]   // optional, field-parameter driven
    },
    ...
  },
  "sortDefinition": { "sort": [ ... ], "isDefaultSort": true }
}
```

See §4 for binding syntax and §5 for role names per visual type.

#### 2.4 `visual.objects` vs `visual.visualContainerObjects`

**The single most important distinction in PBIR.** See §3.

#### 2.5 `filterConfig` — visual-level filters

Real R0101 example (`pages/fee7097e74e40a801acf/visuals/ca63bd4c65345eb4808d/visual.json`):

```json
"filterConfig": {
  "filters": [
    {
      "name": "18ffa5de076e2d445d7a",
      "field": {
        "Column": {
          "Expression": { "SourceRef": { "Entity": "s Reporting Currency" } },
          "Property": "Reporting Currency"
        }
      },
      "type": "Categorical",
      "objects": {
        "general": [
          { "properties": { "isInvertedSelectionMode": { "expr": { "Literal": { "Value": "true" } } } } }
        ]
      }
    }
  ]
}
```

`filter_config.shell` properties (`pbir schema describe filter_config.shell`): `name, displayName, howCreated {Auto,User,Drill,...}, isHiddenInViewMode, isLockedInViewMode, ordinal, restatement, type {Categorical,Range,Advanced,...}`.

An **omitted** `filterConfig` means "no visual-level filters". An **empty** `{"filters":[]}` behaves identically at render time but is noise; pbir CLI writes the omitted form. Don't write empty `{}`.

#### 2.6 `drillFilterOtherVisuals`

Boolean on `visual`. When true (Desktop default), drill actions filter neighbouring visuals. R0101 ships with true on almost every visual.

#### 2.7 `syncGroup` — sync slicers across pages

R0101 (`pages/a06a057745f21fa41f3d/visuals/f58bc3f389e52cf48c1e/visual.json`):

```json
"syncGroup": {
  "groupName": "Family Name",
  "fieldChanges": true,
  "filterChanges": true
}
```

`groupName` is a free-form string; all slicers with the same name sync. `fieldChanges: true` syncs the bound field (same slicer on different pages); `filterChanges: true` syncs the selection state.

#### 2.8 Annotations

Optional `"annotations": [{"name": "...", "value": "..."}]` on report, page, or visual. Stored for deployment scripts / external tooling; Desktop ignores unknown keys.

---

### 3. `visual.objects` vs `visualContainerObjects` — the CHROME vs INTERNAL distinction

**Rule:** if you want to change what the data-rendering looks like (the number size of a card, the colour of bars, the matrix header background), write `visual.objects`. If you want to change the frame around the visual (title, white-box background, border, drop shadow), write `visual.visualContainerObjects`.

#### 3.1 `visualContainerObjects` (vCO) — the chrome

Always lives at `visual.visualContainerObjects`. These containers wrap **every** visual type identically.

| Container | Controls |
|---|---|
| `title` | The title bar above the visual (text, fontSize, heading, fontColor, bold, background, alignment) |
| `subTitle` | Subtitle row |
| `background` | The container's fill colour + transparency |
| `border` | Container outline (show, color, width, radius) |
| `visualHeader` | The `...` menu strip (show, border, showFilterRestatementButton, etc.) |
| `padding` | Gap between chrome and inner content (left/right/top/bottom) |
| `divider` | Line between sections in e.g. multiRowCard chrome |
| `dropShadow` | Drop-shadow on the container |
| `visualLink`, `visualTooltip`, `visualHeaderTooltip` | Interactivity chrome |

R0101 cardVisual (`4b6de49b2a35d1e8e81f/visual.json`) shows a typical set:

```json
"visualContainerObjects": {
  "title": [{ "properties": {
      "show":  { "expr": { "Literal": { "Value": "false" } } },
      "text":  { "expr": { "Literal": { "Value": "'Net Asset Value'" } } }
  }}],
  "padding":   [{ "properties": { "right": {...0D}, "left": {...0D}, "top": {...0D}, "bottom": {...0D} } }],
  "background":[{ "properties": { "show": {...false}, "color": {"solid": {"color": {"expr": {"ThemeDataColor": {"ColorId": 0, "Percent": 0}}}}} } }],
  "border":    [{ "properties": { "show": {...false}, "radius": {...5D}, "color": {"solid": {"color": {"expr": {"Literal": {"Value": "'#F3F2F1'"}}}}}, "width": {...2D} } }],
  "visualHeader":[{ "properties": { "show": {...false}, ... } }]
}
```

Setting `visualContainerObjects.title.fontSize = 28` does **nothing** to a cardVisual's huge number readout — that number is controlled by `visual.objects.value[0].properties.fontSize`.

#### 3.2 `visual.objects` — internal rendering

Per-visual-type. Each visual type exposes its own containers; discover them via `pbir schema containers <visualType>`. Examples:

- `cardVisual`: `value, referenceLabel, referenceLabelValue, referenceLabelTitle, referenceLabelDetail, referenceLabelLayout, label, layout, spacing, divider, shadowCustom, shapeCustomRectangle, outline, fillCustom, image, padding, overFlow, cardCalloutArea, ...` (43 containers)
- `pivotTable`: `columnHeaders, rowHeaders, values, total, subTotals, grid, columnWidth, selection, ...`
- `barChart` / `columnChart` / `lineChart`: `categoryAxis, valueAxis, legend, dataPoint, labels, plotArea, lineStyles, markers, y1AxisReferenceLine, trend, forecast, ...`
- `donutChart` / `pieChart`: `dataPoint, legend, labels, detailLabels, centerLabel`
- `slicer`: `data {Dropdown/Vertical/...}, selection, general, items, header, slicerHeader, pills`
- `waterfallChart`: `sentimentColors, categoryAxis, valueAxis, legend, breakdown, dataLabels`
- `kpi`: `indicator, trendline, goals, dateLabel, calloutValue`
- `textbox`: `general.paragraphs[].textRuns[].textStyle` (NOT the literal-wrapped form — see §3.5)

Concrete R0101 examples:

**cardVisual internal number (`4b6de49b2a35d1e8e81f`):**

```json
"value": [
  {
    "properties": {
      "fontSize":    {"expr": {"Literal": {"Value": "22D"}}},
      "bold":        {"expr": {"Literal": {"Value": "true"}}},
      "horizontalAlignment": {"expr": {"Literal": {"Value": "'center'"}}},
      "labelDisplayUnits":   {"expr": {"Literal": {"Value": "1D"}}},
      "fontColor":   {"solid": {"color": {"expr": {"ThemeDataColor": {"ColorId": 2, "Percent": 0}}}}}
    },
    "selector": {"metadata": "_measures.pv311. NAV Period"}
  }
]
```

**donutChart per-category dataPoint (`797bc009a517f7f74722`):**

```json
"dataPoint": [
  {
    "properties": {
      "fill": {"solid": {"color": {"expr": {"ThemeDataColor": {"ColorId": 9, "Percent": 0}}}}}
    },
    "selector": {
      "data": [{"scopeId": {"Comparison": {"ComparisonKind": 0,
        "Left":  {"Column": {"Expression": {"SourceRef": {"Entity": "d_Currency Exposure"}}, "Property": "Currency ID"}},
        "Right": {"Literal": {"Value": "'GBP'"}}
      }}}]
    }
  }
]
```

**waterfallChart categoryAxis (`f82168f249e80838e597`):**

```json
"categoryAxis": [{
  "properties": {
    "show":     {"expr": {"Literal": {"Value": "true"}}},
    "fontSize": {"expr": {"Literal": {"Value": "10D"}}},
    "labelColor": {"solid": {"color": {"expr": {"ThemeDataColor": {"ColorId": 1, "Percent": 0}}}}}
  }
}]
```

#### 3.3 Container shape — always a list of property-bags

Every `objects.<container>` and `visualContainerObjects.<container>` value is a **list** of objects, each with `{properties, selector?}`. Multiple entries = multiple selector scopes (default, per-data-point, per-measure, etc.). See §8 for selector semantics.

#### 3.4 Copy `visual.objects` verbatim from an existing working visual

The R0101 cardVisual has ~900 lines of `objects`. You cannot hand-author this. Find an existing same-`visualType` visual, clone its full `objects` tree, swap only the bound measure/column references in `selector.metadata` and in any `expr.Measure.Property`.

#### 3.5 Textbox is the special case

`textbox.general.paragraphs` uses a flat text-run model **not** wrapped in `{"expr": {"Literal": ...}}`. See the real R0101 textbox in `pages/c83e9eec024415017a59/visuals/45676d67e070b23b48c0/visual.json`:

```json
"visual": {
  "visualType": "textbox",
  "objects": {
    "general": [{
      "properties": {
        "paragraphs": [{
          "textRuns": [{
            "value": "Analysis",
            "textStyle": {
              "fontWeight": "bold",
              "fontFamily": "Segoe (Bold)",
              "fontSize": "24pt",
              "color": "#666666"
            }
          }],
          "horizontalTextAlignment": "center"
        }]
      }
    }]
  }
}
```

Note: `fontSize` is a quoted string with unit (`"24pt"`), NOT `24D`, and `color` is a plain hex string.

---

### 4. Field binding syntax

Every projection takes this shape:

```json
{
  "field":          { ...field reference... },
  "queryRef":       "<Entity>.<Property>",
  "nativeQueryRef": "<display alias or Property>",
  "active":         true,
  "displayName":    "Optional Display Name"
}
```

#### 4.1 Column vs Measure vs HierarchyLevel

**Measure** (R0101 cardVisual):

```json
"field": {
  "Measure": {
    "Expression": { "SourceRef": { "Entity": "m Measure" } },
    "Property": "Net Asset Value"
  }
}
```

**Column** (R0101 donutChart Category):

```json
"field": {
  "Column": {
    "Expression": { "SourceRef": { "Entity": "d Asset Class" } },
    "Property": "Asset Class"
  }
}
```

**Hierarchy level** (dates, rare in R0101):

```json
"field": {
  "HierarchyLevel": {
    "Expression": {
      "Hierarchy": {
        "Expression": { "SourceRef": { "Entity": "d Date" } },
        "Hierarchy": "Date Hierarchy"
      }
    },
    "Level": "Month"
  }
}
```

**Thin-report extension measure** — note the `"Schema": "extension"` marker:

```json
"field": {
  "Measure": {
    "Expression": { "SourceRef": { "Schema": "extension", "Entity": "_Formatting" } },
    "Property": "Line Color"
  }
}
```

#### 4.2 Projection metadata

- `queryRef` — canonical `"Entity.Property"` string. Must match the field. PBI uses this to key per-projection formatting selectors.
- `nativeQueryRef` — the name used when the measure/column is referenced in DAX output. Usually just `Property` but can differ if the field is renamed in the visual (via `displayName`).
- `active` — on multi-projection rows (e.g. `pivotTable` drill levels), only the active projection renders. Example from R0101 pivotTable (`84d3755adbd2dc785897`): two `Rows` projections, `Valuation Group` is `active: true`, `Asset Label` is `active: false` (drilled-down level).
- `displayName` — override the field label in the visual.

#### 4.3 `fieldParameters` — field-parameter-driven projections

R0101 donutChart Category (`797bc009a517f7f74722`):

```json
"fieldParameters": [{
  "parameterExpr": {
    "Column": {
      "Expression": { "SourceRef": { "Entity": "sf Slicer 2" } },
      "Property": "sf Slicer 2"
    }
  },
  "index": 0,
  "length": 1
}]
```

The `projections[]` array is populated at query time from the field-parameter table at the indicated slice.

#### 4.4 `sortDefinition`

```json
"sortDefinition": {
  "sort": [{
    "field": {
      "Measure": {
        "Expression": { "SourceRef": { "Entity": "m Measure" } },
        "Property": "Net Asset Value"
      }
    },
    "direction": "Descending"
  }],
  "isDefaultSort": true
}
```

`direction` is `"Ascending"` or `"Descending"`. `isDefaultSort: true` means "this is the visual's default sort; user interaction can override".

---

### 5. Roles per visual type

From `pbir schema roles <type>`. **Always verify by opening an existing same-type visual in the report** — some reports ship with legacy roles (e.g. R0101 uses `Y` for donutChart values where the modern schema says `Values`).

| visualType | Roles (required marked *) |
|---|---|
| <a id="json-card"></a>`card` | `Values`* (Column, Measure; multiple) |
| <a id="json-cardvisual"></a>`cardVisual` | `Data`* (Column, Measure; multiple) |
| <a id="json-slicer"></a><a id="json-advancedslicervisual"></a><a id="json-textslicer"></a><a id="json-listslicer"></a>`slicer`, `advancedSlicerVisual`, `textSlicer`, `listSlicer` | `Values`* (Column; multiple) |
| <a id="json-linechart"></a><a id="json-barchart"></a><a id="json-columnchart"></a><a id="json-clusteredbarchart"></a><a id="json-clusteredcolumnchart"></a><a id="json-areachart"></a><a id="json-stackedareachart"></a>`lineChart`, `barChart`, `columnChart`, `clusteredBarChart`, `clusteredColumnChart`, `areaChart`, `stackedAreaChart` | `Category`*, `Y`*, `Legend`, `SmallMultiples` |
| <a id="json-hundredpercentstackedbarchart"></a><a id="json-hundredpercentstackedcolumnchart"></a><a id="json-hundredpercentstackedareachart"></a>`hundredPercentStackedBarChart`, `hundredPercentStackedColumnChart`, `hundredPercentStackedAreaChart` | same as above |
| <a id="json-lineclusteredcolumncombochart"></a><a id="json-linestackedcolumncombochart"></a>`lineClusteredColumnComboChart`, `lineStackedColumnComboChart` | `Category`*, `Y` (ColumnY), `Y2` (LineY), `Legend` |
| <a id="json-donutchart"></a><a id="json-piechart"></a>`donutChart`, `pieChart` | `Category`*, `Values`*, `Legend` |
| <a id="json-pivottable"></a>`pivotTable` (matrix) | `Rows`, `Columns`, `Values`* |
| <a id="json-tableex"></a>`tableEx` | `Values`* (Column, Measure) |
| <a id="json-kpi"></a>`kpi` | `Indicator`*, `TrendLine`, `Goal`, `Goals` |
| <a id="json-waterfallchart"></a>`waterfallChart` | `Category`*, `Y`*, `Breakdown` |
| <a id="json-ribbonchart"></a>`ribbonChart` | (same as column chart pattern) |
| <a id="json-scatterchart"></a>`scatterChart` | `Category`, `X`, `Y`, `Size`, `Play Axis` |
| <a id="json-treemap"></a>`treemap` | `Group`*, `Values`* |
| <a id="json-funnel"></a><a id="json-gauge"></a><a id="json-map"></a><a id="json-filledmap"></a><a id="json-shapemap"></a><a id="json-azuremap"></a>`funnel`, `gauge`, `map`, `filledMap`, `shapeMap`, `azureMap` | visual-specific |
| <a id="json-decompositiontreevisual"></a><a id="json-keydriversvisual"></a><a id="json-qnavisual"></a>`decompositionTreeVisual`, `keyDriversVisual`, `qnaVisual` | AI visuals — discover via `pbir schema roles` |
| <a id="json-image"></a><a id="json-textbox"></a><a id="json-shape"></a><a id="json-actionbutton"></a><a id="json-bookmarknavigator"></a>`image`, `textbox`, `shape`, `actionButton`, `bookmarkNavigator` | no data roles |

Types the [visual catalog](visual-catalog/INDEX.md) documents that the table above does not list (roles from the catalog pages; the pbir CLI catalog in [§5 of the CLI part](#5-visual-type-catalog-every-type--roles) has the same set, plus the `matrix` / `table` legacy aliases):

| visualType | Roles (required marked *) |
|---|---|
| <a id="json-multirowcard"></a>`multiRowCard` | `Values`* (Column, Measure; multiple) |
| <a id="json-scorecard"></a>`scorecard` | `Values`* (Measure; multiple), `TargetValue` (Measure), `Date` (Column) |
| <a id="json-pythonvisual"></a>`pythonVisual`, <a id="json-scriptvisual"></a>`scriptVisual` (R) | `Values` |
| <a id="json-rdlvisual"></a>`rdlVisual` | paginated-report embed; no data roles listed |
| <a id="json-ainarratives"></a>`aiNarratives`, <a id="json-pagenavigator"></a>`pageNavigator`, <a id="json-animatednumber"></a>`animatedNumber` | no data roles |
| <a id="json-basicshape"></a>`basicShape` (legacy; use `shape`) | no data roles |
| <a id="json-accessibletable"></a>`accessibleTable` | `Rows`, `Columns` (Column, Hierarchy; multiple), `Values`* (Measure; multiple) |
| <a id="json-matrix"></a>`matrix` (legacy; use `pivotTable`) | `Rows`, `Columns` (Column, Hierarchy; multiple), `Values`* (Measure; multiple) |
| <a id="json-table"></a>`table` (legacy; use `tableEx`) | `Values`* (Column, Measure; multiple) |
| <a id="json-dataqueryvisual"></a>`dataQueryVisual` | `Values`* (Column, Measure; multiple) |
| <a id="json-filterslicer"></a>`filterSlicer` | `Values`* (Column, Hierarchy; multiple) |
| <a id="json-heatmap"></a>`heatMap` | `Category`*, `Series`, `X`, `Y` (Measure; multiple), `Size`, `Tooltips` |
| <a id="json-realtimelinechart"></a>`realTimeLineChart` | `Category`*, `Series`, `Y`* (Measure; multiple) |

**Wrong role name = silently blank visual.** Binding `Values` on a donutChart in a report that uses `Y` will render nothing.

---

### 6. Literal value encoding

PBIR wraps almost every scalar in `{"expr": {"Literal": {"Value": "<raw>"}}}`. The `<raw>` string is the DAX/AS literal notation, NOT a JSON-native type.

#### 6.1 Encoding rules (verified from R0101)

| Source type | Raw form | Real example |
|---|---|---|
| String | `"'quoted'"` (single-quotes **inside** the JSON string) | `"Value": "'Net Asset Value'"` (R0101 `4b6de49b2a35d1e8e81f` line 603) |
| String (hex colour) | `"'#RRGGBB'"` | `"Value": "'#0C850C'"` (R0101 `4b6de49b2a35d1e8e81f` line 1545) |
| Double | `"<n>D"` | `"Value": "22D"` (cardVisual fontSize, line 691), `"Value": "45D"` (backgroundTransparency, line 209) |
| Long integer | `"<n>L"` | `"Value": "11L"` (cellPadding, line 534), `"Value": "0L"` (verticalSpacing, line 661), `"Value": "223L"` (outspacePane width, page.json line 182) |
| Boolean | `"true"` / `"false"` (lowercase, unquoted *inside* the JSON string) | `"Value": "true"` (line 203), `"Value": "false"` (line 444) |
| Null | `"null"` | `"Value": "null"` (page.json filter line 160) |

**Do NOT confuse** `"Value": "true"` (PBIR boolean literal — five characters t-r-u-e) with `"Value": true` (JSON boolean). PBIR is the former — a JSON string whose content is the keyword `true`. Same for numbers: `"22D"` is a string of three characters.

#### 6.2 Colour encoding (three variants)

**Solid hex** — unconditional colour:

```json
"fontColor": {
  "solid": { "color": { "expr": { "Literal": { "Value": "'#F3F2F1'" } } } }
}
```

**Theme colour** — indirect via theme slot (R0101 cardVisual line 722):

```json
"fontColor": {
  "solid": { "color": { "expr": { "ThemeDataColor": { "ColorId": 2, "Percent": 0 } } } }
}
```

`ColorId` is the theme slot (0 == background, 1 == foreground, 2 == primary accent, 3.. == accent ramp). `Percent` shades: -1.0 = black, 0 = base, +1.0 = white (R0101 uses `-0.2` for subtle dark variants on dividers, `0.4` for lighter accent fills).

**Measure-driven** — colour from a DAX measure (extension or model measure returning hex):

```json
"fill": {
  "solid": { "color": { "expr": {
    "Measure": {
      "Expression": { "SourceRef": { "Schema": "extension", "Entity": "_Formatting" } },
      "Property": "Bar Color"
    }
  }}}
}
```

**Conditional cases** — inline if/else ladder (R0101 cardVisual `referenceLabelValue` line 1519):

```json
"valueFontColor": {
  "solid": { "color": { "expr": {
    "Conditional": {
      "Cases": [
        {
          "Condition": { "Comparison": {
            "ComparisonKind": 2,
            "Left":  { "Measure": { "Expression": { "SourceRef": { "Entity": "_measures" } }, "Property": "pv108. Performance" } },
            "Right": { "Literal": { "Value": "0D" } }
          }},
          "Value": { "Literal": { "Value": "'#0C850C'" } }
        },
        {
          "Condition": { "Comparison": {
            "ComparisonKind": 3,
            "Left":  { "Measure": { "Expression": { "SourceRef": { "Entity": "_measures" } }, "Property": "pv108. Performance" } },
            "Right": { "Literal": { "Value": "0D" } }
          }},
          "Value": { "Literal": { "Value": "'#D71C1F'" } }
        }
      ]
    }
  }}}
}
```

`ComparisonKind`: 0=`=`, 1=`<>`, 2=`>`, 3=`<`, 4=`>=`, 5=`<=`.

---

### 7. Conditional formatting structure

Three families — pick by use case.

#### 7.1 Rules-based (the `Conditional` / `Cases` tree)

See §6.2 above. Stored inline in the property value. Verbose, UI-generated. R0101 uses this heavily for red/green variance colours on cardVisual callout labels.

#### 7.2 FillRule gradient (no DAX)

From `pbir schema describe barChart.dataPoint` → `fill` supports conditional formatting. Pattern from the `pbir-format` skill reference (no live example in R0101 — scan a different report if you need one on disk):

**Three-stop explicit bounds:**

```json
"fill": {
  "FillRule": {
    "linearGradient3": {
      "min": { "color": {"Literal": {"Value": "'#D64550'"}}, "value": {"Literal": {"Value": "-1D"}} },
      "mid": { "color": {"Literal": {"Value": "'#F1F1F1'"}}, "value": {"Literal": {"Value": "0D"}}  },
      "max": { "color": {"Literal": {"Value": "'#118DFF'"}}, "value": {"Literal": {"Value": "1D"}}  },
      "nullColoringStrategy": { "strategy": {"Literal": {"Value": "'asZero'"}} }
    }
  }
}
```

Use `linearGradient2` for two-stop (no `mid`). Omit the `value` fields for data-driven scaling (min/max span observed data range).

#### 7.3 Measure-based

Replace the property's `Literal` expression with a `Measure` expression — see §6.2 "Measure-driven". Combine with `"selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}` to evaluate per data point.

#### 7.4 `pbir` CLI CF helpers

```bash
pbir get "<Visual>.dataPoint.fill.cf"                  # inspect
pbir set "<Visual>.dataPoint.fill.cf.gradient.min.color" --value bad
pbir visuals cf "<Visual>" --gradient --field "Table.Measure" --on dataPoint.fill
```

The CLI hides the `FillRule` / `Conditional` wrapper; it mutates the underlying structure.

---

### 8. Selector metadata

Each entry in an `objects.<container>` or `visualContainerObjects.<container>` array can carry a `selector` that scopes the properties. No selector = applies to the container as a whole.

#### 8.1 Selector variants (observed in R0101 cardVisual `4b6de49b2a35d1e8e81f`)

**Default selector** — explicit "all other cases" fallback:

```json
"selector": { "id": "default" }
```

**Per-measure selector** — scope by field metadata key (`Entity.Property`):

```json
"selector": { "metadata": "_measures.pv311. NAV Period" }
```

**Per-projection-instance selector** — scope by projection slot within a multi-projection role. `id` is a field-instance GUID generated by Desktop:

```json
"selector": {
  "data":     [{"dataViewWildcard": {"matchingOption": 0}}],
  "metadata": "_measures.pv311. NAV Period",
  "id":       "field-dfc2ad9f-6335-9242-7b7a-e761a8846cef",
  "order":    1
}
```

**Per-data-point wildcard** — evaluate per row (key for conditional formatting of dataPoint fills, label colours):

```json
"selector": {
  "data": [{"dataViewWildcard": {"matchingOption": 1}}]
}
```

`matchingOption`: 0 = identities+totals, 1 = instances only (per-point), 2 = totals only.

**Scoped by literal value** — e.g. "colour the bar for Currency=GBP differently" (R0101 donutChart):

```json
"selector": {
  "data": [{"scopeId": {"Comparison": {
    "ComparisonKind": 0,
    "Left":  {"Column": {"Expression": {"SourceRef": {"Entity": "d_Currency Exposure"}}, "Property": "Currency ID"}},
    "Right": {"Literal": {"Value": "'GBP'"}}
  }}}]
}
```

#### 8.2 When to use which

| Goal | Selector |
|---|---|
| Set a container default | omitted OR `{"id": "default"}` |
| Per-measure formatting in multi-measure visuals (cardVisual, pivotTable values) | `{"metadata": "Table.Measure"}` |
| Different formatting for the 1st vs 2nd projection of the same measure (duplicated columns in pivotTable Values) | `{"metadata": "...", "id": "field-<guid>", "order": <n>}` |
| Conditional formatting per data row | `{"data": [{"dataViewWildcard": {"matchingOption": 1}}]}` |
| Colour one specific category | `{"data": [{"scopeId": {"Comparison": {...literal match...}}}]}` |

#### 8.3 `pivotTable.columnWidth` selector (real R0101 `pages/52aa922f5e2a520a8bc1/visuals/a2c51b73c818a6e67d35`)

```json
"columnWidth": [{
  "properties": {
    "value": { "expr": { "Literal": { "Value": "150.3488814658086D" } } }
  },
  "selector": { "metadata": "_measures.n010. NAV" }
}]
```

One entry per column; `metadata` keys by `Entity.Property`.

---

### 9. Group visuals

A group is a `visual.json` with `visualGroup` instead of `visual`. Example from R0101 (`pages/fee7097e74e40a801acf/visuals/034dcd20554c18c832c8/visual.json`):

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
  "name": "034dcd20554c18c832c8",
  "position": { "x": 527.14285714285711, "y": 0, "z": 11000, "height": 41.23, "width": 152.96, "tabOrder": 8000 },
  "visualGroup": {
    "displayName": "Filters",
    "groupMode": "ScaleMode",
    "objects": {
      "background": [{ "properties": { "show": { "expr": { "Literal": { "Value": "false" } } } } }]
    }
  },
  "parentGroupName": "abc55704ca672ae6bf6c"
}
```

#### 9.1 Group keys

- `visualGroup.displayName` — user-visible name.
- `visualGroup.groupMode` — `"ScaleMode"` (children scale with group size) or absent (children fixed to group coords).
- `visualGroup.objects.background.show` — groups can have chrome too (rarely used).
- `parentGroupName` — groups can nest arbitrarily. Children reference the group via their own `parentGroupName: <group.name>`.

#### 9.2 Coordinate system

Group `position` is page-absolute. Children's `position` **on disk** is also page-absolute (not group-local) in the R0101 corpus — Desktop handles the translation. But **pbir CLI `add visual`** positions children page-absolute too, and when you set `parentGroupName` afterwards, Desktop may re-interpret. Reliable recipe: write children page-absolute, set `parentGroupName`, let Desktop reconcile on reopen.

---

### 10. Known gotchas (PBIR file format)

#### 10.1 UTF-8 encoding for non-ASCII measure names

Measure names containing `Δ ∆ × € ± –` must be written as **UTF-8**. Writing JSON through a Windows shell subprocess without explicit encoding will round-trip Latin-1 then UTF-8 re-encode, producing mojibake like `\xc3\x8e\xe2\x80\x9d` for `∆`. Fix: write via Python `json.dumps(..., ensure_ascii=True)` (escapes to `\u0394`) OR explicit `open(path, 'w', encoding='utf-8')` with `ensure_ascii=False`. Never rely on locale defaults on Windows.

#### 10.2 Folder names on disk vs pbir CLI DSL

- **Disk:** `Report.Report/definition/pages/<page_hex_id>/visuals/<visual_hex_id>/visual.json`
- **pbir CLI path DSL:** `Report.Report/<page_hex_id>.Page/<visual_hex_id>.Visual`

The `.Page` and `.Visual` suffixes are TYPE markers, NOT part of the on-disk folder name. The CLI needs them to disambiguate in glob patterns (`**/*.Visual`).

#### 10.3 `filterConfig` present-but-empty

Omit the key when no visual-level filters exist. Writing `"filterConfig": {}` or `"filterConfig": {"filters": []}` is legal JSON but creates diff noise and can confuse tooling that treats an empty object as "cleared all filters". pbir CLI omits the key.

#### 10.4 `pbir cp` page-copy mismatch bug (v0.9.9)

After `pbir cp` duplicates a page, the new `page.json` contains stale references that crash Power BI Desktop on page load (`VisualRelationshipService.getRelationship TypeError`). The workflow and the fix script are in the pbir CLI gotchas, [§10.3](#103-pbir-cp-page-copy-mismatch-bug) of the CLI part; prefer `11_PBIP_Authoring clone_page`.

#### 10.5 Never bare-replace visual IDs

Covered once, with the scoping steps, in the pbir CLI gotchas: [§10.5](#105-never-string-replace-visual-ids-unscoped) of the CLI part.

#### 10.6 Delete visuals via `pbir rm`, never `shutil.rmtree`

Covered once in the pbir CLI gotchas: [§10.4](#104-visual-deletion--use-pbir-rm-not-rm--rf) of the CLI part (`pbir rm` cleans `visualInteractions`, `parentGroupName` refs and `syncGroup` entries; a raw folder delete leaves orphans that crash Desktop).

#### 10.7 Strip dangling `visualInteractions` after bulk deletes

Covered once in the pbir CLI gotchas: [§10.6](#106-visualinteractions-cleanup-after-bulk-deletes) of the CLI part (`page.json.visualInteractions` entries whose `source` or `target` no longer exists as a sibling visual id must be dropped).

#### 10.8 `visual.objects` edits require a full Desktop reload

Covered once in the pbir CLI gotchas: [§10.10](#1010-visualobjects-edits-need-a-pbi-desktop-reload-close--reopen) of the CLI part (`objects` are not re-read on a page switch; `visualContainerObjects` usually are).

#### 10.9 Power BI Desktop writes floats like `x: 527.14285714285711`

Don't "tidy" those values — reproduction of the exact bit pattern matters for sync-slicers and bookmark captures. Use `json.dumps` with default float handling and leave alone.

#### 10.10 Audit slicers before deleting content groups

Covered once in the pbir CLI gotchas: [§10.11](#1011-slicers-inside-content-groups) of the CLI part.

#### 10.11 Contamination check after every pbir operation

```bash
git status --short | grep Report | grep -v <new_page_id> | grep -v pages.json | wc -l
```

Should equal your pre-existing unstaged count. Higher == something was touched outside your intended scope; restore and investigate.

---

### Appendix A — Schema URLs (as of R0101, PBIR 2.0.0)

| File | `$schema` |
|---|---|
| `definition.pbir` | `definitionProperties/2.0.0/schema.json` |
| `version.json` | `definition/versionMetadata/1.0.0/schema.json` |
| `report.json` | `definition/report/3.2.0/schema.json` |
| `pages/pages.json` | `definition/pagesMetadata/1.0.0/schema.json` |
| `pages/<id>/page.json` | `definition/page/2.1.0/schema.json` |
| `visuals/<id>/visual.json` | `definition/visualContainer/2.7.0/schema.json` |

Always paste these schemas at the top of hand-edited files — `pbir schema upgrade` relies on them.

### Appendix B — Discovery cheat sheet

```bash
pbir schema types                           # list 49 visual types + 5 entity types
pbir schema roles <visualType>              # data roles (Category, Y, Values, ...)
pbir schema containers <visualType>         # all objects+vCO containers
pbir schema describe <visualType>.<container>   # properties, types, ranges
pbir schema describe <entityType>.<container>   # page.background, report.settings, filter_config.shell
pbir get "<Visual>.<container>.<property>"  # read current value
pbir set "<Visual>.<container>.<property>" --value "..."
pbir visuals cf "<Visual>" --gradient --field "Table.Measure" --on dataPoint.fill
```

Always run `pbir schema roles <type>` on the first-occurrence existing same-type visual in the report before binding a new one — some reports carry legacy role names.

## pbir CLI reference (0.9.9, revalidated against 0.9.25)

Single source of truth for every `pbir` command, flag, visual type, and property path the CLI exposes. Generated by exhaustively running `--help` on every subcommand and `pbir schema` for every registered visual/entity type against a live install of `pbir 0.9.9` (by Kurt Buhler & Maxim Anatsko).

> Binary: `pbir` (Python, Typer-based). Always run with `PYTHONIOENCODING=utf-8` on Windows (output contains box-drawing + fancy arrows; default cp1252 chokes).

### Changes in 0.9.25

This reference was generated from `pbir 0.9.9` (2026-04-17). On 2026-08-21 the installed CLI (`pbir 0.9.25`, "Made by Kurt Buhler & Maxim Anatsko") was re-checked against its `--help` output (`pbir --version`, `--help`, `validate --help`, `desktop --help`, `desktop reload --help`, `report convert --help`); nothing below was inferred. Statements that 0.9.25 corrected are fixed in place and marked "(0.9.25)": the `pbir validate` flags ([§4.4](#pbir-validate-path)), the suffix rule ([§2.1](#21-filesystem-style-path), [§10.2](#102-suffix-requirements-report-page-visual-workspace-semanticmodel-theme-filter)) and the `--version` flag. The remaining deltas:

- **`pbir validate` no longer rewrites schemas by default.** The old rule "`pbir validate` rewrites every visual.json on a schema bump, never run it on a full report as a check" is obsolete. The rewrite is opt-in behind `--update-schemas`; plain `pbir validate "Report.Report"` performs read-only checks (JSON syntax, Microsoft schema compliance, required fields, plus optional `--fields` / `--qa` / `--semantic` / `--all` / `--strict` and `-o/--output` to write a report file) and is safe on a full report. `--update-schemas` stays destructive (it still mass-rewrites and produces the diff noise). AGENTS.md §8 carries the updated rule.
- **`pbir desktop` (new, preview, Windows)** drives the Desktop bridge itself and needs the same Desktop preview feature as the Microsoft CLI:

  | Subcommand | Purpose |
  |---|---|
  | `list` / `status` | running instances + open file + unsaved state + pages |
  | `refresh` / `reload` | reload the on-disk report into the canvas |
  | `screenshot` | one page, or `--all` |

  `pbir desktop reload` options: `-m/--model` ("Also re-apply the model definition (TMDL changes)"), `--pid`, `--wait-seconds`. The Microsoft `powerbi-desktop` CLI 0.1.1 did NOT expose a model flag (its `reload` had only `--pid` and `--wait-seconds`), so "a bridge reload is report-only" was true of the 0.1.1-based `17_Desktop_Bridge` but is not a platform limitation. `17_Desktop_Bridge` has since gained `include_model=true` ([Part III §III.24](#iii24-17_desktop_bridge--debug)), through the bridge CLI library (>= 1.0.0) rather than through `pbir desktop reload -m`.
- **Path DSL: suffixes are required throughout, and globs are first-class.** `Report.Report/Page.Page/Visual.Visual`; help text: "Type suffixes (.Report, .Page, .Visual) are required". Globs are supported and documented (`**/*.Visual`, `**/card*.Visual`); property access uses dot notation after the path (`Visual.Visual.title.fontSize`). The `.Page`-suffix gotcha is therefore the documented norm and extends to `.Visual`. The MCP op auto-adds `.Page`; check that it also handles visual-level paths if one is ever passed.
- **New command surface since 0.9.9** (not in the command tree below):
  - `schema` / `capabilities`: property discovery (`schema types`, `schema describe card`, `schema describe card title`: objects, properties, values, ranges), a local equivalent of the `powerbi-report-author catalog/formatting` lookups.
  - `bpa`: Best Practice Analyzer over reports + rule management.
  - `usage`: Power BI usage metrics (views, viewers, pages, load times).
  - `color` / `fonts`: find/audit/replace across a whole report *and its theme*.
  - `report convert`: legacy to PBIR upgrade and pbir/pbip/pbix container conversion (EXPERIMENTAL). Its help text states "Legacy (monolithic report.json) is deprecated as of June 2026; PBIR is the only format Microsoft supports now", which confirms the PBIR-GA transition.
  - `report merge` / `split-pages` / `split-from-thick`, `mv`, `backup` / `restore`, `download` / `publish`, `batch` (declarative multi-step), `dax` (extension measures + visual calcs), `annotations`, `profile`, `model`.
  - Top-level flags: `--show-legacy` (surface legacy-format reports that need conversion), `--rawdog` (skip every validation check), `--quiet`, `--debug`, `-i/--interactive`.
- **Still to re-verify** (needs a scratch report, not just `--help`): the `pbir cp` page-copy mismatch bug ([§10.3](#103-pbir-cp-page-copy-mismatch-bug); keep the workaround until it is proven fixed), and whether `--show-legacy` is the cheapest legacy-format detector for the PBIR-GA guardrail (versus a `report.json`-presence check).

Sources: the installed CLI on 2026-08-21 as listed above; upstream <https://github.com/maxanatsko/pbir.tools>.

---

### 1. Global flags

```
pbir [OPTIONS] COMMAND [ARGS]...
```

| Flag | Short | Description |
| --- | --- | --- |
| `--interactive` | `-i` | Launch interactive browser mode (arrow keys). Do NOT use from agents. |
| `--show-legacy` | | Include legacy format reports that need conversion to PBIR. |
| `--install-completion` | | Install shell completion (human users only). |
| `--show-completion` | | Print shell completion script. |
| `--version` | `-V` | Print the version and exit (`pbir 0.9.9` when this reference was generated; `pbir 0.9.25` is installed). |
| `--quiet` | `-q` | Suppress animations, tips, spinners. Strongly recommended for scripting. |
| `--debug` | | Verbose logging to stderr: tracebacks, timing, resolution. |
| `--help` | | Per-command help. |

Global tip: `pbir -q <subcommand>` gives clean pipeable output. Decorative chrome goes to stderr on `reference-line add` / `error-bars add`, so stdout is stable for shell chaining.

---

### 2. Path syntax and selectors (mini-language)

#### 2.1 Filesystem-style path

Every pbir object is addressed with a path that mirrors the PBIR on-disk layout:

```
<ReportName>.Report / <PageName>.Page / <VisualName>.Visual
```

The `.Report` / `.Page` / `.Visual` suffixes are REQUIRED (0.9.25: the help text now states this for every command, and globs are first-class, see [Changes in 0.9.25](#changes-in-0925)). Also exist:

- `.Workspace` — Fabric workspace (remote ops, used by `connect`, `download`, `publish`, `report rebind`)
- `.SemanticModel` — semantic-model folder
- `.Theme` — serialized theme folder (produced by `theme serialize`, consumed by `theme build`)
- `.Filter` — single filter, appended as `Report.Report/<FieldName>.Filter`

Pseudo-paths used by specific commands:

| Pseudo-path | Used by |
| --- | --- |
| `Report.Report/theme` | `pbir cp theme`, `pbir cat theme`, `pbir rm --theme` |
| `Report.Report/annotations` | `pbir cat annotations`, `pbir annotations` |
| `Report.Report/bookmark:<Name>` | `pbir rm` to delete a bookmark |
| `Report.Report/filter:<Name>` | `pbir rm` to delete a filter |
| `Report.Report/Page.Page/config` | `pbir cat` a page's config object |

#### 2.2 Glob patterns (ripgrep-style)

- `*` any chars, single level
- `**` any chars, recursive
- `?` single char
- `[abc]` char class
- `**/*.Visual` all visuals
- `**/card*.Visual` fuzzy visual match
- `Report.Report/*.Page` all pages in one report

Where a command accepts globs it also usually requires `--force` (e.g., `pbir set ... -f`) or provides `--dry-run` to preview.

#### 2.3 Property tail

After the path you can append a dot-notation property path. Used by `get`, `set`, `cat`:

```
Visual.Visual.title.fontSize
Visual.Visual.background.color
Visual.Visual.dataPoint.fill
Report.Report.settings.exportDataMode
Page.Page.width
```

#### 2.4 State selectors

Interaction states map to selector entries on the container:

| Selector | Meaning |
| --- | --- |
| `.default` | default state (can usually be omitted) |
| `.hover` | hover state |
| `.press` | pressed state |
| `.selected` | selected state |

Example: `pbir set "Visual.Visual.background.hover.color" --value "#F5F5F5"`

#### 2.5 Per-field / per-series / per-id selectors

Added to a container tail before the leaf property:

| Selector | Meaning | Shape in JSON |
| --- | --- | --- |
| `.field(Table.Column)` | Per-field metadata (dataPoint fill, error styling, etc.) | `selector.metadata` entry |
| `.series(Table.Column=Value)` | Per-series-value (legend slicing) | `selector` with `data` entry |
| `.id(N)` | Addressable entry in a multi-entry container (reference-lines, CF rule cases) | numeric id added by pbir when entry is created |

Examples:

```bash
pbir set "...Visual.dataPoint.field(Sales.Revenue).fill"  --value "#118DFF"
pbir set "...Visual.dataPoint.series(Cities.City=Antwerp).fill" --value "#E66C37"
pbir set "...Visual.y1AxisReferenceLine.id(2).lineColor" --value "#FF0000"
pbir set "...Visual.labels.color.cf.rules.case[0].color" --value "#FF0000"
pbir set "...Visual.dataPoint.field(Sales.Revenue).fill" --remove  # drops the selector entry
```

Hex strings are auto-coerced to `{solid: {color: "..."}}` for color-typed properties. Use `--json` to push raw objects.

#### 2.6 Container aliases on `get` / `set`

| Alias | Expands to |
| --- | --- |
| `bg` | `background` |
| `border` | `border` (passthrough) |
| `title` | `title` (passthrough, but normalises text vs fontColor) |
| `legend` | `legend` |
| `axis.x` | `categoryAxis` |
| `axis.y` | `valueAxis` |
| `colors` | theme colors group |
| `labels` | `labels` |

---

### 3. Property setter path DSL

`pbir set <PATH> --value <V>` writes a single property. `pbir get <PATH>` reads it. The **path is everything after the object suffix**:

```
Report.Report.settings.exportDataMode
Report.Report/Page.Page.width
Report.Report/Page.Page/Visual.Visual.title.text
```

Supported write modes on `pbir set`:

| Flag | Behaviour |
| --- | --- |
| `--value X` | Plain scalar (string, number, bool). Coerced by property type (hex → solid.color). |
| `--property <prop>`, `-p` | Alternative way to specify the trailing property (append to path). |
| `--json '<raw>'` | Raw JSON value (needed for objects like `{solid: {color:"#..."}}` or gradients). |
| `--remove` / `--clear`, `-r` | Drop the override. For `.field(...)`/`.series(...)`/`.id(N)` removes that selector entry. For `.cf` wipes the whole CF entry. |
| `--no-validate` | Skip field-reference validation in `field(...)` selectors. |
| `--force`, `-f` | REQUIRED for glob patterns (bulk). |
| `--where "<k=v>"`, `-w` | Filter targets by predicate (`visual_type=card`, `width__gt=500`, etc.). |
| `--dry-run` | Preview bulk changes without writing (glob paths only). |

#### Theme-level set-formatting path

`pbir theme set-formatting` uses a 4-part path against theme `visualStyles`:

```
{visualType}.{state}.{property}.{subproperty}
   card.*.title.fontSize
   *.*.background.color
   card.*.title.show
```

- `visualType`: `*` wildcard or a concrete type (card, kpi, tableEx...)
- `state`: `*` for default or `hover`/`press`/`selected`
- `property.subproperty`: container tree (title.fontSize, background.color)

---

### 4. Command tree (exhaustive)

Every subcommand below was extracted from `pbir <cmd> --help`. Flags listed with defaults and types.

#### 4.1 Getting Started

##### `pbir setup`
Placeholder. `pbir setup` prints install instructions for Claude / agent plugins (setup was replaced by plugin marketplaces).

##### `pbir config`
```
pbir config show                 # current config
pbir config init                 # create default ~/.config/pbir/config.json
pbir config set <key> <value>    # set a value (value='null' to clear)
pbir config paths                # resolved paths for config/templates/schemas
```
Keys seen in `config show`: `debug`, `quiet`, `output` (auto|json), `spinner`, `active_profile`, `active_report`, `profiles`.

##### `pbir connect [TARGET]...`
Set the active connection for the session.
- 1 arg = local report: `pbir connect "Report.Report"`
- 2 args = remote: `pbir connect MyWorkspace MyReport`
- `--clear` — Clear active connection (prompts to delete local files).
- `--profile <name>`, `-p` — Activate a saved profile.
- `--output <dir>`, `-o` — Output directory for remote downloads.
- `--backup`, `-b` — Create a backup after connecting.

##### `pbir profile`
```
pbir profile list
pbir profile show <name>
pbir profile save <name> [--description "..."]
pbir profile remove <name>
```

#### 4.2 Browse & Query

##### `pbir ls [PATH]`
- `--verbose`, `-v` — show fields used in visuals.
- `--tree`, `-T` — tree view.
- `--all <kind>`, `-A` — flat listing: `pages|visuals|fields|filters|measures|viscalcs|bookmarks|annotations`.
- `--alphabetical`, `-a` — alpha sort instead of tab/natural order.
- `--json` — machine-readable.
- `--images` — list image resources in report.

##### `pbir find <PATTERN>`
Glob search.
- `--type {report|page|visual}`, `-t`
- `--json`
- `--count`, `-c`

##### `pbir model [PATH]`
Read or query the bound semantic model.
- `--definition`, `-d` — Fetch tables/columns/measures (TMDL).
- `--query <DAX>`, `-q` — Execute DAX.
- `--verbose`, `-v` — Full TMDL / full JSON.
- `--table <t>`, `-t` — Filter to table (with `-d`).
- `--output <path>`, `-o` — Save definition to file.
- `--format {table|json}`, `-F` — Output format for `-q`.
- `--cache`, `-c` — Cache definitions when listing all reports.
- `--use-cache` — Use cached definition if available (default: always fetch fresh).
- `--clear-cache` — Drop cached definitions (after model schema change).
- `--json` — machine-readable.

##### `pbir get <PATH>`
Read a property.
- `--json` — Output raw JSON.
- `--structured` — Structured objects (measures, theme colors).

##### `pbir set <PATH>`
See section 3.

##### `pbir cat <PATH>`
Dump raw JSON for a page, visual, theme, annotations, filters. Supports glob; multi-match returns keyed JSON object. Pipe to `jq`. Alias: `pbir json`.

#### 4.3 Create & Edit

##### `pbir add`
Subcommands:

| Sub | Purpose |
| --- | --- |
| `add page` | Add a page |
| `add title` | Add a 24pt title textbox at (20,20) |
| `add subtitle` | Add a 14pt grey subtitle textbox at (20,85) |
| `add visual` | Add a visual to a page |
| `add annotation` | Report/page/visual annotation |
| `add image` | Register an image in `resourcePackages` |
| `add filter` | Report/page/visual filter |
| `add bookmark` | Add a bookmark (capture current state) |

###### `pbir add page [PATH]`
- `--display-name`, `-n` — REQUIRED.
- `--width`, `-w` (default 1280)
- `--height`, `-h` (default 720)
- `--display-option {FitToPage|FitToWidth|ActualSize}` (default FitToPage)
- `--from-template`, `-t` — Name under `~/.pbir/templates/pages/`.
- `--list-templates`, `-l`

###### `pbir add title PATH TITLE`
- `--width`, `-w` (default 1000)

###### `pbir add subtitle PATH SUBTITLE`
- `--width`, `-w` (default 1000)

###### `pbir add visual [VISUAL_TYPE] [PATH]`
- `--name`, `-n` — internal ID (folder name suffix before `.Visual`).
- `--title`, `-t` — displayed title.
- `--x` (default 20), `--y` (default 20).
- `--width`, `-w`, `--height`, `-h` — defaults per type.
- `--data <role>:<field>`, `-d` — Bind one field (repeatable in JSON mode). Ex: `-d values:Sales.Revenue`.
- `--list`, `-l` — List 49+ visual types (grouped).
- `--from-json <file|->`, `-j` — Bulk create from JSON array. Each entry: `{visual_type, x, y, width, height, title, fields: {Role: "Table.Field"}}`.
- `--image <src>` — For image visuals. Accepts file path, URL, or `Table.Measure` reference.

###### `pbir add annotation PATH`
- `--name`, `-n`, `--value`, `-v` — Report/page-level annotations (key=value).
- `--text`, `-t`, `--author`, `-a`, `--category`, `-c` — Visual-level annotations.

###### `pbir add image PATH SOURCE`
- `--name`, `-n` — Display name.
SOURCE accepts local path or `http(s)://` URL; copied into report and registered in `resourcePackages`.

###### `pbir add filter TABLE FIELD`
Scope flags (pick one):
- `--report`, `-r` — report-level filter
- `--page`, `-p` — page-level filter
- `--visual`, `-v` — visual-level filter

Filter-shape flags:
- `--values <v>` (repeatable) — Default filter values.
- `--type {Categorical|Advanced|RelativeDate|RelativeTime|TopN}` (default Categorical)
- `--no-validate` — Skip field validation against model.
- `--n <int>` (TopN)
- `--by-table <t>` (TopN ranking table)
- `--by-field <f>` (TopN ranking field)
- `--direction {Top|Bottom}` (default Top, TopN only)
- `--operator {In|NotIn|GreaterThan|LessThan|Is|IsNot|...}` (Advanced)
- `--period {Last7Days|Last30Days|Last3Months|LastYear|ThisYear|...}` (RelativeDate)

###### `pbir add bookmark REPORT_PATH DISPLAY_NAME`
- `--no-data` — Don't capture filter/slicer state.
- `--no-display` — Don't capture visibility.
- `--no-current-page` — Don't capture active page.

##### `pbir mv FROM_PATH TO_PATH`
Move or rename a page or visual between reports/pages. `--force`, `-f` to overwrite.

For renaming a report folder, use `pbir report rename` (not `pbir mv`).

##### `pbir cp FROM_PATH TO_PATH`
Copy reports, themes, pages, visuals, filters, bookmarks, measures, viscalcs.
- `--format {pbir|pbip|pbix}`, `-F` — Target format when copying a report.
- `--force`, `-f` — Overwrite.
- `--measures`, `-m` — Copy extension measures between reports.
- `--table <t>`, `-t` — Filter measures by table name (with `--measures`).
- `--viscalcs` — Copy visual calculations between visuals.
- `--format-only` — Copy formatting only (title, background, border, …) without data bindings.

##### `pbir rm <PATH>`
Destructive. `--force`, `-f` to skip confirmation.

| Flag | Effect |
| --- | --- |
| *(path to page/visual)* | Delete that node |
| `filter:<Name>` pseudo | Delete a filter (`Report.Report/filter:MonthFilter`) |
| `bookmark:<Name>` pseudo | Delete a bookmark |
| `--measure <name>` | Delete one extension measure |
| `--measures` | Delete ALL extension measures |
| `--viscalc <name>` | Delete one visual calc |
| `--annotations` | Remove annotations |
| `--theme` | Remove custom theme and restore default |
| `--fields` | Clear all field bindings from visuals |
| `--image <name>` | Remove an image from RegisteredResources |
| `--all`, `-a` | Remove all filters, bookmarks, annotations (requires `--force`) |
| `--dry-run` | Preview with globs |

##### `pbir new report [PATH]`
- `--format {pbip|pbir|pbix}`, `-F` (default pbip)
- `--open` — Launch in PBI Desktop after creation.
- `--thick` — Create thick report (NOT recommended).
- `--connection <Workspace/Model.SemanticModel>`, `-c` — Falls back to active connection.
- `--from-template <name>`
- `--list-templates`, `-l`
- `--no-title` — Skip the default title textbox (frees y:0 for visuals).

#### 4.4 Workflow

##### `pbir download <WORKSPACE_REPORT>`
`Workspace.Workspace/Report.Report` format. Workspace-only lists reports.
- `--output <dir>`, `-o`
- `--format {pbir|pbip}`, `-F` (default pbir)
- `--auto-deploy` — Enable auto-deploy.
- `--open` — Open in PBI Desktop after download.

##### `pbir batch`
Declarative multi-step automation. Two spec versions supported; default v2.

| Sub | Notes |
| --- | --- |
| `schema [--version 2]` | Print JSON Schema for the batch spec |
| `validate <spec.json>` | Validate structure (`--json`) |
| `plan <spec.json> [--root <path>] [--verbose-json]` | Resolve targets without changing files |
| `run <spec.json> [--root <p>] [--plan-digest <hash>] [--dry-run] [--json]` | Execute |
| `examples` | List bundled examples (audit-report, brand-format, field-measures, report-polish) |
| `example <name>` | Print one bundled example JSON |

See section 9 for spec shape.

##### `pbir validate [PATH]`
- `--verbose`, `-v` — Schema resolution provenance (exact, implicit, degraded).
- `--output <file>`, `-o` — Write report to file.
- `--update-schemas` — Update `$schema` refs to latest while validating. **(0.9.25)** This is OPT-IN: plain `pbir validate` is read-only (JSON syntax, schema compliance, required fields) and safe to run on a full report; the schema rewrite that older builds did by default now happens only with this flag, so treat `--update-schemas` as the destructive one.
- `--structure` / `--no-structure` — Validate PBIP/PBIR folder structure (default on).
- `--fields`, `-f` — Validate fields exist in connected model.
- `--qa`, `-q` — Quality checks (overlaps, hidden visuals, visual filters, field counts).
- `--all`, `-a` — schema + fields + qa.
- `--strict`, `-s` — Warnings → errors.
- `--json` — JSON summary.
- `--tree`, `-t` — Tree view.
- `--allow-download-schemas` — Fetch missing exact schemas on demand.

##### `pbir publish [SOURCE] [DESTINATION]`
Destination: `Workspace.Workspace/Report.Report`. Uses active connection if omitted.
- `--force`, `-f` — Overwrite remote.
- `--open`, `-o` — Launch published report in browser.

##### `pbir open <PATH>`
Open in Power BI Desktop. If the path ends in `Page.Page`, sets that page active first.

##### `pbir backup [PATH]`
Writes a full copy to `~/.config/pbir/backups/` and annotates `report.json` with `BackupLocation` + `BackupDateTime`.
- `--message`, `-m` — Free-text description.
- `--list`, `-l` — List backups (globally or for one report).
- `--prune` — Remove old backups.
- `--keep <N>`, `-k` (default 3; with `--prune`).
- `--force`, `-f` — Skip confirmation with `--prune`.

##### `pbir restore <PATH>`
Restore most-recent backup by default.
- `--backup-id <TS>`, `-b` — Pick a specific timestamp (e.g., `20260312T143000Z`).
- `--force`, `-f` — Skip confirmation.

##### `pbir schema`

| Sub | Purpose |
| --- | --- |
| `types` | List every registered visual + entity type (`pbir schema types`) |
| `containers <type> [--json]` | List containers for a visual/entity |
| `describe <type>.<container> [--json]` | Properties + types + constraints for a container |
| `roles <type> [--report <r>] [--json]` | Data roles (field wells) for a visual |
| `status` | Compare local vs remote schema versions |
| `fetch [--yes] [--regenerate/--no-regenerate]` | Download raw schemas from GitHub |
| `regenerate` | Regenerate flattened schemas from raw |
| `check <report>` | Scan a report and compare `$schema` versions against latest |
| `upgrade <report> [--yes]` | Rewrite `$schema` in all JSON to latest |

#### 4.5 Object commands

##### `pbir report`

| Sub | Purpose |
| --- | --- |
| `rename <path> <new_name> [--no-rename-folder]` | Rename report + update `.platform` displayName |
| `rebind <path> [target] [--local <p>] [--model-id <guid>]` | Rebind to different semantic model |
| `convert <path> -F {pbir|pbip|pbix} [--name N] [-f] [--validate] [--open]` | Convert between formats |
| `merge <paths...> -o <out>.Report [-f]` | Merge reports (first wins conflicts) |
| `merge-to-thick <report> <model.SemanticModel> [-o out]` | Merge thin + model → thick PBIP |
| `split-pages <path> [-o dir] [-F {pbir|pbip}]` | Split into single-page reports |
| `split-from-thick <path> [--target W.Workspace/M.SemanticModel] [-F {pbir|pbip}] [-o out]` | Split thick → thin (publishes model, rebinds) |
| `clear-diagram <path>` | Delete `semanticModelDiagramLayout.json` |

##### `pbir pages`

| Sub | Purpose |
| --- | --- |
| `rename [path] [-p pattern] [--to N] [-f] [-s sep]` | Rename pages by template; tokens `{displayName}`, `{name}`, `{index}` |
| `active-page <report> <page_name>` | Set landing page |
| `display <path> -o {FitToPage|FitToWidth|ActualSize}` | Page display option |
| `background <path> [--image file] [--scaling Fit/Fill] [--transparency N] [--clear] [--show]` | Canvas bg |
| `wallpaper <path> [--image URL] [--color #HEX] [--transparency N] [--scaling Normal/Fit/Fill]` | Outspace wallpaper |
| `type <path> -t {16:9|4:3|letter|tooltip|custom} [-w W] [-h H] [-l]` | Page type preset |
| `resize <path> -w W -h H` | Resize |
| `move <path> --to N` | Reorder (1-indexed) |
| `json <path>` | Raw page.json |
| `drillthrough <path> [-t Table -f Field] [--cross-report] [--clear] [--show]` | Drillthrough config |
| `set-tooltip <path> [-w 320] [-h 240] [--clear]` | Mark page as tooltip page |
| `hide <path> [--show]` | Hide/show page |
| `interactions <path> [-s src -t tgt --type {Filter|Highlight|NoFilter}] [--disable-from V] [--disable-to V] [--clear]` | Cross-filter control |

##### `pbir visuals`

Core editing:

| Sub | Purpose |
| --- | --- |
| `rename <path> [-p pattern] [--to N] [-f] [--where P] [-s sep]` | Rename visual folders. Tokens `{type}`, `{title}`, `{group}`, `{page}`, `{name}`, `{index}` |
| `resize <path> [-w W -h H] [--where P]` | Resize |
| `position <path> [--x --y --z --width --height --tab-order] [-w PRED]` | Position/size/z/tab |
| `align <page_path> <op> <names...>` | Align op in `{left,right,top,bottom,center-h,center-v,distribute-h,distribute-v}` |
| `bind <visual_path> [--add role:field] [--remove role:field] [--clear role] [--type Column/Measure] [--list-roles] [--show] [--no-validate]` | Field binding |
| `format <visual_path> [-p prop] [-v] [--json]` | Show merged formatting (theme + visual) |
| `properties [path] [-t type] [-s search] [--threshold 0.6] [-v] [--registry] [--json]` | Discover property names |
| `json <path>` | Dump raw visual.json |
| `hide <path> [--show]` | Hide/show visual |
| `show-all <path> -r <role> [--off]` | Toggle show-all on a data role (empty subcategory rows) |
| `field-hidden <path> -r <role> -f <Table.Field> [--show]` | Hide a bound field in the field well |
| `z-order <path> [-f \| -b \| -u N \| -d N \| -v N]` | Layer stacking |
| `snap <path> [-g pixels] [-d]` | Snap position (and optionally dimensions) to grid |
| `mobile <path> [--x --y -w -h --z --tab-order] [-r] [-s]` | Mobile layout (percentages 0–100) |
| `group <path> [-c name] [-m mode] [-l] [-u]` | Manage visual groups |
| `preset [path] [-n {minimal|bold|clean|emphasis|presentation}] [-l]` | Apply style preset |
| `clear-formatting <path> [--keep-cf] [--only-containers] [--only-objects] [--dry-run] [-f]` | Reset visual-level overrides |
| `action <path> [-t {PageNavigation|Bookmark|Drillthrough|WebUrl|Back|Qna|ApplyAllSlicers|ClearAllSlicers}] [--target T] [--tooltip X] [--clear] [--show]` | Click action |

Formatting (single-container convenience wrappers — these correspond to containers on the visual):

- `background` — `--show/--no-show --color --transparency --where`
- `border` — `--show/--no-show --color --width --radius --where`
- `shadow` — `--show/--no-show --position {Outer|Inner} --angle --distance --blur --transparency --color --where`
- `padding` — `--top --bottom --left --right --where`
- `title` — `--show/--no-show --text --fontSize --fontFamily --fontColor --alignment {left|center|right} --titleWrap --bold --italic --underline --where`
- `subtitle` — `--show/--no-show --text --fontSize --fontFamily --fontColor --alignment --bold --italic --underline --where`
- `divider` — `--show/--no-show --color --style {solid|dashed|dotted} --width --where`
- `spacing` — `--customizeSpacing --spaceBelowTitle --spaceBelowSubtitle --where`
- `header` — `--show/--no-show --transparency --foregroundColor --where`
- `tooltip` — `--show/--no-show --type {Default|Canvas|ReportPage} --fontSize --fontFamily --bold --italic --underline --transparency --where`
- `general` — `--altText --keepLayerOrder --responsive --where`
- `legend` — `--show/--no-show --position {Top|Bottom|Left|Right|TopCenter|…} --showTitle/--no-showTitle --titleText --fontFamily --fontSize --fontColor --bold --italic --underline --where`
- `axis <path> <axis_type>` (axis_type ∈ `category|value|y2`) — `--show/--no-show --title --showTitle/--no-showTitle --min --max --gridlines --fontFamily --fontSize --fontColor --bold --italic --where`
- `labels` — `--show/--no-show --fontFamily --fontSize --fontColor --bold --italic --underline --backgroundColor --labelPosition {Inside|Outside|Auto} --labelOrientation {Horizontal|Vertical} --labelDisplayUnits {Auto|None|Thousands|Millions|…} --labelPrecision --where`
- `sort` — `[-f field] [-d {Ascending|Descending}] [-r] [-w PRED]`

Custom visuals / scripts:

- `deneb <path> [-f spec.json] [-s '<inline>'] [-p {vega|vegaLite}] [-c config.json]`
- `python <path> [-f script.py] [-s '<inline>']`
- `r <path> [-f script.r] [-s '<inline>']`

Query / performance:

- `query <path> [-x] [-o out.dax] [--json]` — Extract/execute DAX.
- `test <path> [--json]` — Time query execution, slowest first.

Structural authoring (multi-entry containers):

- `reference-line` subcommands:
  - `add <visual> -v <value_or_field> [-a {y|x}]` → prints new id.
  - `list <visual>`
  - `remove <visual> [--id N] [--all]`
- `error-bars` subcommands:
  - `add <visual> -s Table.Series -u Table.Upper [-l Table.Lower] [--relative]` → prints resolved queryRef (upsert by series).
  - `list <visual>`
  - `remove <visual> [-s Series] [--all]`

Conditional formatting: `pbir visuals cf` — see section 8.

##### `pbir fields`

- `list [path] [--all-reports]`
- `find <term> [report] [-t table] [--type {column|measure|extension|hierarchy|visual_calc}] [--threshold 0.6]`
- `replace <report> --from Table.Field --to Table.Field [--dry-run] [--skip-validation]` — Replaces in projections, sorts, CF, filters.
- `rename <report> [<query_ref> <display_name>] [--clear] [-f field] [-v visual] [-l] [--dry-run]`
- `add <visual_path> -f Table.Field -b <bucket> [-t {Column|Measure}] [-n displayName] [--skip-validation] [--dry-run]`

To clear all bindings: `pbir rm "Report.Report" --fields -f`.

##### `pbir filters`

- `list [path] [--all-reports] [--json]`
- `set <filter_path> [-v value ...] [-t type] [-n name] [-o ordinal]` where `type ∈ {Categorical, Advanced, RelativeDate, RelativeTime, TopN}`.
- `clear <path> [-a]` (`-a` clears all filters at the given report/page path).
- `hide <path> [-a] [--show]`
- `lock <path> [-a] [--unlock]`
- `unlock <path> [-a]`
- `rename <path> <filter_name> <new_name>`
- `json <path>` — raw filter JSON.
- `pane-hide <report>`
- `pane-collapse <report>`
- `pane-get <path> [--json]`
- `pane-set <path>` — theme/page-level styling. Flags: `--width --bg-color --transparency --border --border-color --fg-color --font-family --header-size --title-size --search-size --input-color --checkbox-color`.
- `pane-card <path> [-s {Applied|Available|<id>}] [--bg-color --transparency --border --border-color --fg-color --text-size --input-color --font-family]`

##### `pbir bookmarks`

- `list [path] [--all-reports]`
- `rename <report> <bookmark> <new_name>`
- `data <report> <bookmark> [--on/--off]` (data = filters/slicers)
- `display <report> <bookmark> [--on/--off]` (display = visibility)
- `current-page <report> <bookmark> [--on/--off]`
- `visuals <report> <bookmark> [visual_ids...] [--all]`
- `page <report> <bookmark> [page_name]`
- `json <report>` — raw bookmarks.json

Delete a bookmark: `pbir rm "Report.Report/bookmark:Name" -f`.

##### `pbir annotations`

- `list [path] [--all-reports]`
- `update <path> <name> <value>`
- `rename <path> <old_name> <new_name>`

Read raw: `pbir cat "Report.Report/annotations"`.

##### `pbir dax`

Extension measures (thin-report measures in `reportExtensions.json`):
```
pbir dax measures list   [path] [--all-reports] [-v] [-n substring]
pbir dax measures add    <report> -t Table -n Name -e "DAX" [-d Data-type] [-F Format] [--description ...] [--no-validate]
   Data types: Text, Integer, Double, String (default Double)
pbir dax measures update <report> <name> [-e DAX] [-d type] [-F format] [--description ...] [--data-category ImageUrl] [--display-folder Folder]
pbir dax measures rename <report> <old> <new>
pbir dax measures json   <report>
```
Delete: `pbir rm "Report.Report" --measure "Name" -f`.

Visual calculations (embedded in a visual):
```
pbir dax viscalcs list   [path]
pbir dax viscalcs add    <visual> -n Name -e "RUNNINGSUM([X])" [-q queryRef]
pbir dax viscalcs update <visual> <name> -e "NEW DAX"
pbir dax viscalcs rename <visual> <old> <new>
pbir dax viscalcs json   <visual>
```
Delete: `pbir rm "R.Report/P.Page/V.Visual" --viscalc "Name" -f`.

##### `pbir theme`

| Sub | Purpose |
| --- | --- |
| `serialize <path> [-o out.Theme]` | Split theme JSON into files |
| `build <theme.Theme> [-o report] [-f] [--clean]` | Merge files back into theme.json and optionally apply to a report |
| `validate <path>` | Structural validation |
| `colors <path> [--visuals] [--type {literal|theme|measure|gradient|conditional|named}] [--scope {theme|visual|container}] [--hex HEX] [--no-theme] [--replace --from HEX --to {#HEX|theme:N|named:good/bad/neutral}] [--in <property>] [--include-gradients] [--include-conditional] [--dry-run] [--normalize] [--max-distance 15.0] [--apply] [--json] [--used-only]` | Inspect / replace / normalize colors |
| `set-formatting <report> <path> [--value V] [--json V]` | Theme-level formatting write (`card.*.title.fontSize`, `*.*.background.color`) |
| `push-visual <visual> [-c comps] [--dry-run] [--include-selectors]` | Push visual-level formatting up to theme visualType defaults |
| `rename <report> [new_name] [--theme-file FILE]` | Rename theme file |
| `set-colors <report>` | Named color setters (40+ flags; see below) |
| `set-text-classes <report> <class> [-s size] [-f family] [-c color] [--bold] [--italic] [--underline]` | Set one text class |
| `text-classes <report> [class.property value] [-a] [-s size] [-f face] [-c color]` | Inspect or set (supports `--all` for bulk) |
| `fonts <report>` / `fonts --list-supported` | List/validate/replace fonts. Flags: `-l`, `-v`, `-r OLD -w NEW`, `--set-default FAM`, `--fix` |
| `background <report> [-i file] [--clear] [--show]` | Base64-encoded background image |
| `icons <report> [--set NAME --url DATA] [--add NAME --url DATA] [--remove NAME] [-d description]` | Custom theme icons |
| `create-template [<source>] [--new-template FILE -n NAME -d DESC -a AUTHOR -r {check|warning|none}] [--update-template NAME]` | Library templates in `~/.pbir/templates/themes/` |
| `list-templates [--refresh]` | Available templates |
| `apply-template <report> <template_name> [-f]` | Apply template |

`theme set-colors` (all values are hex strings unless noted):

```
--data-colors JSON   --data-colors-limit N
--background --background-light --background-dark --background-neutral
--foreground --foreground-light --foreground-dark
--foreground-neutral-light --foreground-neutral-dark
--foreground-neutral-secondary --foreground-neutral-secondary-alt
--foreground-neutral-secondary-alt2
--foreground-neutral-tertiary --foreground-neutral-tertiary-alt
--foreground-selected --foreground-button
--table-accent --accent --shape-stroke
--good --neutral --bad
--maximum --center --minimum --null
--first-level-elements --second-level-elements
--third-level-elements --fourth-level-elements
--hyperlink --visited-hyperlink
--map-pushpin --disabled-text
```

Text classes supported by `set-text-classes`/`text-classes`:
```
title, label, callout, header,
largeTitle, dataTitle,
boldLabel, largeLabel, largeLightLabel, lightLabel, semiboldLabel,
smallLabel, smallLightLabel, smallDataLabel
```

---

### 5. Visual type catalog (every type + roles)

`pbir add visual --list` exposes 49 visual types across 7 categories. Roles below come from `pbir schema roles <type>`. **Always verify against an existing same-type visual in your report** — some reports use non-standard role bindings (e.g., donuts sometimes use `Y` instead of `Values`).

Legend: *Required* roles are bold. *Multiple* = accepts >1 field.

#### CHART

| Type | Roles (required in bold) | Accepts |
| --- | --- | --- |
| <a id="cli-linechart"></a>`lineChart` | **Category**, **Y** (mult), Legend, SmallMultiples | Category/Legend/SM=Column, Y=Measure |
| <a id="cli-barchart"></a>`barChart` | **Category**, **Y** (mult), Legend, SmallMultiples | same |
| <a id="cli-columnchart"></a>`columnChart` | **Category**, **Y** (mult), Legend, SmallMultiples | same |
| <a id="cli-clusteredbarchart"></a>`clusteredBarChart` | **Category**, **Y** (mult), Legend, SmallMultiples | same |
| <a id="cli-clusteredcolumnchart"></a>`clusteredColumnChart` | **Category**, **Y** (mult), Legend, SmallMultiples | same |
| <a id="cli-stackedbarchart"></a>`stackedBarChart` | **Category**, **Y**, Legend | Category/Legend=Column, Y=Measure |
| <a id="cli-stackedcolumnchart"></a>`stackedColumnChart` | **Category**, **Y**, Legend | same |
| <a id="cli-stackedareachart"></a>`stackedAreaChart` | **Category**, **Y** (mult), Legend, SmallMultiples | same |
| <a id="cli-areachart"></a>`areaChart` | **Category**, **Y** (mult), Legend, SmallMultiples | same |
| <a id="cli-hundredpercentstackedbarchart"></a>`hundredPercentStackedBarChart` | **Category**, **Y** (mult), Legend | same |
| <a id="cli-hundredpercentstackedcolumnchart"></a>`hundredPercentStackedColumnChart` | **Category**, **Y** (mult), Legend | same |
| <a id="cli-hundredpercentstackedareachart"></a>`hundredPercentStackedAreaChart` | **Category**, **Y** (mult), Legend, SmallMultiples | same |
| <a id="cli-ribbonchart"></a>`ribbonChart` | **Category**, **Y** (mult), Legend | same |
| <a id="cli-lineclusteredcolumncombochart"></a>`lineClusteredColumnComboChart` | **Category**, Y (mult), Y2 (mult), Legend | Category/Legend=Column, Y/Y2=Measure |
| <a id="cli-linestackedcolumncombochart"></a>`lineStackedColumnComboChart` | **Category**, Y (mult), Y2 (mult), Legend | same |
| <a id="cli-waterfallchart"></a>`waterfallChart` | **Category**, **Y**, Breakdown | Category/Breakdown=Column, Y=Measure |
| <a id="cli-funnel"></a>`funnel` | **Category**, **Values** | Category=Column, Values=Measure |
| <a id="cli-scatterchart"></a>`scatterChart` | **X**, **Y**, Category, Size, Legend, SmallMultiples, Tooltips (mult) | X/Y/Size=Measure, Category/Legend/SM=Column, Tooltips=Column/Measure |
| <a id="cli-donutchart"></a>`donutChart` | **Category**, **Values**, Legend | Category/Legend=Column, Values=Measure |
| <a id="cli-piechart"></a>`pieChart` | **Category**, **Values**, Legend | same |
| <a id="cli-treemap"></a>`treemap` | **Category** (mult), **Values**, Details | Category/Details=Column, Values=Measure |
| <a id="cli-gauge"></a>`gauge` | **Value**, TargetValue, MinValue, MaxValue | all Measure |
| <a id="cli-decompositiontreevisual"></a>`decompositionTreeVisual` | **Category** (mult), **Values** | Category=Column, Values=Measure |
| <a id="cli-keydriversvisual"></a>`keyDriversVisual` | Analyze, Explain, Expand | (Key Influencers — special) |

#### CARD

| Type | Roles |
| --- | --- |
| <a id="cli-card"></a>`card` | **Values** (mult, Column or Measure) |
| <a id="cli-cardvisual"></a>`cardVisual` | **Data** (mult, Column or Measure) |
| <a id="cli-kpi"></a>`kpi` | **Indicator** (Measure), TrendLine (Column), Goal (Measure), Goals (mult Measure) |
| <a id="cli-multirowcard"></a>`multiRowCard` | **Values** (mult, Column or Measure) |
| <a id="cli-scorecard"></a>`scorecard` | **Values** (mult, Measure), TargetValue (Measure), Date (Column) |

#### TABLE

| Type | Roles |
| --- | --- |
| <a id="cli-pivottable"></a>`pivotTable` (Matrix) | Rows (mult, Column), Columns (mult, Column), **Values** (mult, Measure) |
| <a id="cli-tableex"></a>`tableEx` | **Values** (mult, Column or Measure) |

#### SLICER

| Type | Roles |
| --- | --- |
| <a id="cli-slicer"></a>`slicer` | **Values** (mult, Column) |
| <a id="cli-advancedslicervisual"></a>`advancedSlicerVisual` | **Values** (mult, Column) |
| <a id="cli-listslicer"></a>`listSlicer` | **Values** (mult, Column), Tooltips (mult Column/Measure) |
| <a id="cli-textslicer"></a>`textSlicer` | **Values** (mult, Column) |

#### MAP

| Type | Roles |
| --- | --- |
| <a id="cli-map"></a>`map` | **Location** (mult, Column), Size (Measure), Legend (Column) |
| <a id="cli-filledmap"></a>`filledMap` | **Location** (mult, Column), Values (Measure), Legend (Column) |
| <a id="cli-azuremap"></a>`azureMap` | Location, Size, Color |
| <a id="cli-shapemap"></a>`shapeMap` | Location, ColorSaturation |

#### CONTAINER

| Type | Roles |
| --- | --- |
| <a id="cli-bookmarknavigator"></a>`bookmarkNavigator` | none (navigator) |
| <a id="cli-actionbutton"></a>`actionButton` | none |
| <a id="cli-pagenavigator"></a>`pageNavigator` | none (schema doesn't list as a first-class visual type) |
| <a id="cli-shape"></a>`shape` | none |

#### MEDIA

| Type | Roles |
| --- | --- |
| <a id="cli-image"></a>`image` | none (set via `--image file|url|Table.Measure` on `add visual`) |
| <a id="cli-textbox"></a>`textbox` | none |

#### CUSTOM

| Type | Roles |
| --- | --- |
| <a id="cli-ainarratives"></a>`aiNarratives` | none |
| <a id="cli-chicletslicer"></a>`chicletSlicer` | Category, Values (custom visual) |
| <a id="cli-hierarchyslicer"></a>`hierarchySlicer` | Fields (custom visual) |
| <a id="cli-rdlvisual"></a>`rdlVisual` | (paginated report embed) |
| <a id="cli-pythonvisual"></a>`pythonVisual` | Values |
| <a id="cli-qnavisual"></a>`qnaVisual` | (Q&A) |
| <a id="cli-scriptvisual"></a>`scriptVisual` (R) | Values |
| <a id="cli-timeline"></a>`timeline` | Time (custom visual) |

#### Catalog-only types

Types the [visual catalog](visual-catalog/INDEX.md) documents that `pbir add visual --list` does not offer (existing reports still contain them; roles from the catalog pages). `matrix` and `table` are the legacy types that `pivotTable` and `tableEx` replace: edit them in place, build new ones as the modern type.

| Type | Roles | Notes |
| --- | --- | --- |
| <a id="cli-accessibletable"></a>`accessibleTable` | Rows (mult, Column/Hierarchy), Columns (mult, Column/Hierarchy), **Values** (mult, Measure) | screen-reader table |
| <a id="cli-animatednumber"></a>`animatedNumber` | none | single animated number; only the `general` object |
| <a id="cli-basicshape"></a>`basicShape` | none | legacy decorative shape; use `shape` |
| <a id="cli-dataqueryvisual"></a>`dataQueryVisual` | **Values** (mult, Column or Measure) | data-inspection visual with `overrideDataReduction` |
| <a id="cli-filterslicer"></a>`filterSlicer` | **Values** (mult, Column/Hierarchy) | unified slicer; `data.mode` is one of `VerticalList`, `HorizontalList`, `Dropdown`, `Basic`, `Single`, `Between`, `Before`, `After`, `Relative`, `RelativeTime`, `RelativeDatePicker` |
| <a id="cli-heatmap"></a>`heatMap` | **Category**, Series, X, Y (mult Measure), Size, Tooltips | geographic density |
| <a id="cli-matrix"></a>`matrix` | Rows, Columns (mult, Column/Hierarchy), **Values** (mult, Measure) | legacy; use `pivotTable` |
| <a id="cli-realtimelinechart"></a>`realTimeLineChart` | **Category**, Series, **Y** (mult, Measure) | streaming / push dataset line chart |
| <a id="cli-table"></a>`table` | **Values** (mult, Column or Measure) | legacy; use `tableEx` |

---

### 6. Container catalog per visual type

Containers come from `pbir schema containers <type>`. Drill into any container with `pbir schema describe <type>.<container>` for property names, JSON types, and constraints.

**Chrome containers** shared by almost every visual (these roughly correspond to `visualContainerObjects` in visual.json):
`background`, `border`, `divider`, `dropShadow`, `general`, `lockAspect`, `padding`, `spacing`, `stylePreset`, `subTitle`, `title`, `visualHeader`, `visualHeaderTooltip`, `visualLink`, `visualTooltip`.

**Internal containers** (the actual `visual.objects` — which is what drives visible rendering):

#### card (18 containers)
`background, border, categoryLabels, divider, dropShadow, general, labels, lockAspect, padding, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip, wordWrap`

#### cardVisual (43 containers) — a.k.a. "new card"
`accentBar, background, border, cardCalloutArea, cardImage, divider, dropShadow, fillCustom, general, glowCustom, grid, image, label, layout, lockAspect, outline, overFlow, padding, referenceLabel, referenceLabelDetail, referenceLabelLayout, referenceLabelTitle, referenceLabelValue, rotation, shadowCustom, shapeCustomRectangle, smallMultiplesAccentBar, smallMultiplesBorder, smallMultiplesCellBackGround, smallMultiplesGrid, smallMultiplesHeader, smallMultiplesLayout, smallMultiplesOuterShape, smallMultiplesOverFlow, spacing, stylePreset, subTitle, title, value, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

Key: **the huge number on a cardVisual is `value.fontSize`**, not `title.fontSize`.

#### kpi (20 containers)
`background, border, divider, dropShadow, general, goals, indicator, lastDate, lockAspect, padding, spacing, status, stylePreset, subTitle, title, trendline, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### multiRowCard (19 containers)
`background, border, card, cardTitle, categoryLabels, dataLabels, divider, dropShadow, general, lockAspect, padding, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### scorecard (21 containers)
`background, border, columnHeaders, detailsPane, divider, dropShadow, general, goals, header, lockAspect, padding, scorecard, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip, visualization`

#### lineChart (39 containers)
`annotationTemplate, anomalyDetection, background, border, categoryAxis, dataPoint, divider, dropShadow, error, filters, forecast, general, labels, layout, legend, lineStyles, lockAspect, markers, padding, plotArea, referenceLine, scalarKey, seriesLabels, smallMultiplesLayout, spacing, stylePreset, subTitle, subheader, title, trend, valueAxis, visualHeader, visualHeaderTooltip, visualLink, visualTooltip, xAxisReferenceLine, y1AxisReferenceLine, y2Axis, zoom`

#### barChart / columnChart (33 containers each — identical)
`annotationTemplate, background, border, categoryAxis, dataPoint, divider, dropShadow, error, filters, general, labels, layout, legend, lockAspect, padding, plotArea, ribbonBands, smallMultiplesLayout, spacing, stylePreset, subTitle, subheader, title, totals, trend, valueAxis, visualHeader, visualHeaderTooltip, visualLink, visualTooltip, xAxisReferenceLine, y1AxisReferenceLine, zoom`

#### clusteredBarChart / clusteredColumnChart (32 containers each)
Same as bar/columnChart, minus `ribbonBands`, minus `totals`; plus `referenceLine`.

#### areaChart (36) / stackedAreaChart (36)
Similar to lineChart plus `referenceLine` / `totals`; stacked adds `totals`.

#### lineClusteredColumnComboChart (35) / lineStackedColumnComboChart (34)
Superset of barChart + lineChart containers. `valueAxis` has 57 properties (y1 and y2).

#### donutChart / pieChart (20 each)
`annotationTemplate, background, border, dataPoint, divider, dropShadow, general, labels, legend, lockAspect, padding, slices, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### treemap (20)
`background, border, categoryLabels, dataPoint, divider, dropShadow, general, labels, layout, legend, lockAspect, padding, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### funnel (19)
`background, border, categoryAxis, dataPoint, divider, dropShadow, general, labels, lockAspect, padding, percentBarLabel, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### waterfallChart (23)
`background, border, breakdown, categoryAxis, divider, dropShadow, general, labels, legend, lockAspect, padding, plotArea, sentimentColors, spacing, stylePreset, subTitle, title, valueAxis, visualHeader, visualHeaderTooltip, visualLink, visualTooltip, y1AxisReferenceLine`

#### ribbonChart (33)
Same set as barChart.

#### scatterChart (35)
`background, border, bubbles, categoryAxis, categoryLabels, clustering, colorBorder, colorByCategory, currentFrameIndex, dataPoint, divider, dropShadow, fillPoint, general, legend, lockAspect, markers, padding, plotArea, plotAreaShading, ratioLine, referenceLine, spacing, stylePreset, subTitle, title, trend, valueAxis, visualHeader, visualHeaderTooltip, visualLink, visualTooltip, xAxisReferenceLine, y1AxisReferenceLine, zoom`

#### pivotTable (29)
`accessibility, annotationTemplate, background, blankRows, border, columnFormatting, columnHeaders, columnTotal, columnWidth, divider, dropShadow, general, grid, lockAspect, padding, rowHeaders, rowTotal, spacing, sparklines, stylePreset, subTitle, subTotals, title, total, values, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### tableEx (24)
`accessibility, background, border, clustering, columnFormatting, columnHeaders, columnWidth, divider, dropShadow, general, grid, lockAspect, padding, spacing, sparklines, stylePreset, subTitle, title, total, values, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### slicer (25)
`background, border, data, date, dateRange, divider, dropShadow, general, header, items, lockAspect, numericInputStyle, padding, pendingChangesIcon, searchBox, selection, slider, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### advancedSlicerVisual (33) / listSlicer (33)
`accentBar, actionState, background, behavior (advanced only) / expansionIcon (list only), border, data, divider, dropShadow, fillCustom, general, glowCustom, icon, image, label, layout, lockAspect, outline, overFlow, padding, rotation, selection, selectionIcon, shadowCustom, shapeCustomRectangle, spacing, stylePreset, subTitle, title, value, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### textSlicer (21)
`applyButton, background, border, divider, dropShadow, dropdown, filterOperator, general, inputText, inputTextBox, lockAspect, padding, slicerSettings, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### map (22)
`background, border, bubbles, categoryLabels, dataPoint, divider, dropShadow, general, heatMap, legend, lockAspect, mapControls, mapStyles, padding, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### filledMap (22)
`background, border, categoryLabels, dataPoint, divider, dropShadow, general, labels, legend, lockAspect, mapControls, mapStyles, padding, spacing, stroke, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### azureMap (29)
`background, barChart, border, bubbleLayer, categoryLabels, commonDataOptions, dataPoint, divider, dropShadow, filledMap, general, heatMapLayer, labels, legend, lockAspect, mapControls, padding, pathLayer, referenceLayer, spacing, stylePreset, subTitle, tileLayer, title, traffic, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### shapeMap (20)
`background, border, dataPoint, defaultColors, divider, dropShadow, general, legend, lockAspect, padding, shape, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip, zoom`

#### gauge (20)
`axis, background, border, calloutValue, dataPoint, divider, dropShadow, general, labels, lockAspect, padding, spacing, stylePreset, subTitle, target, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### decompositionTreeVisual (22)
`analysis, background, border, categoryLabels, dataBars, dataLabels, divider, dropShadow, general, insights, levelHeader, lockAspect, padding, spacing, stylePreset, subTitle, title, tree, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### keyDriversVisual (18)
`background, border, divider, dropShadow, general, keyDrivers, keyDriversDrillVisual, keyInfluencersVisual, lockAspect, padding, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### bookmarkNavigator (25)
`accentBar, background, bookmarks, border, divider, dropShadow, fill, general, glow, layout, lockAspect, outline, padding, rotation, shadow, shape, spacing, stylePreset, subTitle, text, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### actionButton (23)
`background, border, divider, dropShadow, fill, general, glow, icon, lockAspect, outline, padding, rotation, shadow, shape, spacing, stylePreset, subTitle, text, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### shape (22)
`background, border, divider, dropShadow, fill, general, glow, lockAspect, outline, padding, rotation, shadow, shape, spacing, stylePreset, subTitle, text, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### image (17)
`background, border, divider, dropShadow, general, image, imageScaling, lockAspect, padding, spacing, stylePreset, subTitle, title, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### textbox (17)
`background, border, divider, dropShadow, general, lockAspect, padding, spacing, stylePreset, subTitle, text, title, values, visualHeader, visualHeaderTooltip, visualLink, visualTooltip`

#### Sampled property detail — cardVisual.value (15 props)
```
bold, customFormatString, fontColor (object), fontFamily, fontSize [6-45],
horizontalAlignment {left|center|right}, italic, labelDisplayUnits {0,1,1000,...},
labelPrecision, show, showBlankAs, textWrap, transparency, underline, $id
```
CF supported on: `fontColor`, `fontSize`, `transparency`.

#### Sampled property detail — lineChart.dataPoint (4 props)
```
defaultColor (object), fill (object), showAllDataPoints (bool), transparency (number)
```
CF supported on: `defaultColor`, `fill`, `transparency`.

#### Sampled property detail — lineChart.categoryAxis (35 props)
Axis-core: `show`, `axisStyle` {showTitleOnly|showUnitOnly|showBoth}, `axisType` {Scalar|Categorical}, `start`, `end`, `labelDisplayUnits`, `labelPrecision`, `concatenateLabels`, `invertAxis`, `logAxisScale`, `maxMarginFactor`, `preferredCategoryWidth`, `roundRange`, `showAxisTitle`.
Tick-label font: `fontFamily`, `fontSize [6-45]`, `bold`, `italic`, `underline`, `labelColor`.
Title font: `titleText`, `titleBold`, `titleColor`, `titleFontFamily`, `titleFontSize [6-45]`, `titleItalic`, `titleUnderline`.
Gridlines: `gridlineShow`, `gridlineColor`, `gridlineStyle` {solid|dashed|dotted|custom}, `gridlineDashArray`, `gridlineDashCap` {none|round|square}, `gridlineThickness`, `gridlineTransparency`, `gridlineAutoScale`.

#### Sampled property detail — pivotTable.values / tableEx.values (20 / 18 props)
```
backColor (object), backColorPrimary, backColorSecondary, bandedRowHeaders (matrix only),
fontColor, fontColorPrimary, fontColorSecondary, bold, italic, underline,
fontFamily, fontSize [6-45], icon (CF-enabled),
outlineColor, outlineStyle, outlineWeight,
urlIcon, valuesOnRow (matrix only), webURL, wordWrap
```
CF supported on: `fontColor`, `fontSize`, `icon`.

#### Sampled property detail — slicer.header (11 props) and slicer.items (12 props)
- `header`: background, bold, fontColor (CF), fontFamily, italic, outlineStyle, show, showRestatement, text (CF), textSize [6-45], underline
- `items`: accessibilityContrastProperties, background, bold, expandCollapseToggleType {0,1,2}, fontColor (CF), fontFamily, italic, outlineStyle, padding, steppedLayoutIndentation, textSize [6-45], underline

Use `pbir schema describe <type>.<container>` for any other container.

---

### 7. Entity types and containers

#### 7.1 report (3 containers)
- `outspacePane` (2 props)
- `section` (1 prop)
- `settings` (23 props) — includes `allowAutomatedInsights`, `allowChangeFilterTypes`, `allowInlineExploration`, `customMemoryLimit`, `customTimeoutLimit`, `defaultDrillFilterOtherVisuals`, `defaultFilterActionIsDataFilter`, `disableFilterPaneSearch`, `enableDeveloperMode`, `exportDataMode` {AllowSummarized|AllowSummarizedAndUnderlying|None}, `fieldParameterReportSettings`, `filterPaneHiddenInEditMode`, `hideVisualContainerHeader`, `isPersistentUserStateDisabled`, `isReportAnnotationsDisabled`, `pagesPosition` {PagesPane|Bottom}, `pauseQueries`, `queryLimitOption` {None|Shared|Premium|...}, `useCrossReportDrillthrough`, `useDefaultAggregates`, …

#### 7.2 page (8 containers)
- `background` (4 props: color, image, transparency, verticalAlignment)
- `filterCard` (8 props: backgroundColor, border, borderColor, fontFamily, foregroundColor, inputBoxColor, textSize, transparency)
- `outspace` (3 props)
- `outspacePane` (12 props: backgroundColor, border, borderColor, checkboxAndApplyColor, fontFamily, foregroundColor, headerSize, inputBoxColor, searchTextSize, titleSize, transparency, width)
- `pageInformation` (4 props)
- `pageRefresh` (3 props)
- `pageSize` (3 props)
- `personalizeVisual` (2 props)

#### 7.3 theme (2 containers)
- `colors` (37 props): `accent, background, backgroundDark, backgroundLight, backgroundNeutral, bad, center, dataColors (array), disabledText, firstLevelElements, foreground, foregroundButton, foregroundDark, foregroundLight, foregroundNeutralDark, foregroundNeutralLight, foregroundNeutralSecondary, foregroundNeutralSecondary-alt/alt2, foregroundNeutralTertiary, foregroundNeutralTertiary-alt, foregroundSelected, fourthLevelElements, good, hyperlink, icons (any), mapPushpin, maximum, minimum, neutral, null, secondLevelElements, secondaryBackground, shapeStroke, tableAccent, thirdLevelElements, visitedHyperlink`
- `textClasses` (14 props): `boldLabel, callout, dataTitle, header, label, largeLabel, largeLightLabel, largeTitle, lightLabel, semiboldLabel, smallDataLabel, smallLabel, smallLightLabel, title`

#### 7.4 filter_config (1 container)
- `shell` (8 props): `displayName, howCreated {Auto|User|Drill|…}, isHiddenInViewMode, isLockedInViewMode, name, ordinal, restatement, type {Categorical|Range|Advanced|…}`

#### 7.5 bookmark_config (1 container)
- `options` (5 props)

#### 7.6 group (3 containers)
- `background` (3), `general` (5), `lockAspect` (1) — visual group shell.

#### 7.7 filter (visual-container filter pane) (1 container)
- `general` (2 props)

---

### 8. Conditional formatting (CF) command surface

CF entries live on leaf properties (e.g., `dataPoint.fill`, `labels.color`, `values.fontColor`, `accentBar.color`). There are TWO surfaces:

#### 8.1 Structural authoring via `pbir visuals cf`

```
pbir visuals cf <visual_path> [options]
```

- `--list`, `-l` — **deprecated**: use `pbir get "<path>.**.cf"`.
- `--has <container>` — Check whether a container has CF.
- `--info <container.prop>` — Show CF type info (`accentBar.color`).
- `--remove <container.prop_or_container>`, `-r` — Remove one CF entry.
- `--remove-all` — Strip all CF on the visual.

Create / convert:

- `--gradient --field Table.Field --min-color <...> [--mid-color <...>] --max-color <...> [--on <container.prop>]`
- `--rules --field Table.Field --rule "op value color_or_icon" [--rule ...] [--on ...]` (ops: `gt`, `lt`, `gte`, `lte`, `eq`)
- `--icons --field Table.Field --rule "..." [--on ...]`
- `--data-bars --field Table.Field [--positive-color ...] [--negative-color ...] [--on ...]`
- `--measure "<container.prop> MeasureRef"`, `-m` — Apply measure-driven CF.
- `--set-color "<container.prop> key=color [key=color ...]"` — Update colors on an existing CF. Keys: `min`, `max`, `mid`, `null`, `case0`, `case1`, ...
- `--to-measure <container.prop>` — Convert hex/rule CF to a measure-driven CF (auto-creates an extension measure).
- `--theme-colors <container.prop> [min=bad max=good]` — Replace hex colors with theme sentiment tokens (`good`, `bad`, `neutral`).
- `--copy-from <other_visual>` — Copy the full CF configuration from another visual (overwrites target).
- `--on <container.prop>` — Default `dataPoint.fill`.

#### 8.2 Leaf edits via `pbir set` (the modern surface)

For existing CF entries, use plain `pbir set`:

```
pbir set "...Visual.dataPoint.fill.cf.gradient.min.color" --value "bad"
pbir set "...Visual.dataPoint.fill.cf.gradient.max.value" --value 100
pbir set "...Visual.labels.color.cf.rules.case[0].color" --value "#FF0000"
pbir set "...Visual.dataPoint.fill.cf.measure" --value "_Fmt.NewStatusColor"
pbir set "...Visual.dataPoint.fill.cf" --remove   # drop the entire CF entry
```

CF anatomy in PBIR (reference):

- `gradient` → `min.color`, `min.value`, `mid.color`, `mid.value`, `max.color`, `max.value`
- `rules` → `rules.case[N].color`, `rules.case[N].ops[…]`
- `icons` → `icons.rules.case[N].iconSet`, `…`
- `dataBars` → `positiveColor`, `negativeColor`, `axisColor`, `showBar`
- `measure` → `"<Table>.<Measure>"` reference for measure-driven CF

Ask per container which properties accept CF via `pbir schema describe <type>.<container>` — the bottom of each describe output lists "Conditional formatting supported: ...".

---

### 9. Batch spec v2 schema

`pbir batch schema --version 2` prints the full JSON Schema. Summary:

```jsonc
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id":     "https://data-goblin.github.io/pbir-cli/schemas/batch/v2/schema.json",
  "title":   "PBIR Batch Spec v2",
  "required": ["version", "name", "steps"],
  "properties": {
    "version": { "const": 2 },
    "name":    { "type": "string", "minLength": 1 },
    "root":    "string | null",
    "stop_on_error": "boolean",
    "defaults": {
      "select":            "string | string[]",
      "where":             "selectorTree",
      "max_targets":       "integer",
      "require_plan_hash": "boolean"
    },
    "steps": "step[] (minItems 1)"
  }
}
```

#### 9.1 Selector language

`selectorLeaf`:
```jsonc
{
  "field": "path_type | report_name | page_name | visual_name | visual_type
           | x | y | width | height | z
           | has_bindings | has_conditional_formatting",
  "op":    "eq | ne | contains | starts_with | ends_with
           | in | not_in
           | gt | gte | lt | lte | between
           | is_true | is_false",
  "value": "depends on op — scalar for eq/ne/etc; array for in/not_in; [a,b] for between; omitted for is_true/is_false"
}
```

`selectorTree` combinators:
```jsonc
{ "all": [selectorTree, ...] }
{ "any": [selectorTree, ...] }
{ "not": selectorTree }
```

#### 9.2 Assertion language

Assertions use `assertLeaf` which supports the same `eq, ne, …` operators plus `left` / `right` expression fields.

#### 9.3 Example (`pbir batch example brand-format`)

```json
{
  "version": 2,
  "name": "brand-format",
  "root": "Report.Report",
  "stop_on_error": true,
  "defaults": { "select": "**/*.Visual" },
  "steps": [
    {
      "id": "titles",
      "op": "set",
      "where": {
        "not": { "field": "visual_type", "op": "in",
                 "value": ["slicer", "pythonVisual", "rVisual"] }
      },
      "set": {
        "title.show": true, "title.fontSize": 12,
        "background.show": true, "background.color": "#FFFFFF",
        "background.transparency": 0
      }
    },
    {
      "id": "audit",
      "op": "audit",
      "select": ".",
      "summary": ["pages", "visuals", "visual_types"]
    }
  ]
}
```

Bundled examples: `audit-report`, `brand-format`, `field-measures`, `report-polish` (run `pbir batch example <name>`).

#### 9.4 Run flow

```
pbir batch validate spec.json            # schema-validate
pbir batch plan     spec.json --root R   # resolve target paths (produces plan-digest)
pbir batch run      spec.json --root R --plan-digest <hash>  # execute
pbir batch run      spec.json --dry-run  # simulate
```

---

### 10. Known gotchas (pbir CLI)

#### 10.1 UTF-8 / console encoding
pbir output uses fancy Unicode (`→`, `✓`, box-drawing). On Windows with cp1252 you'll see `UnicodeEncodeError` in Python invocations or mangled output in cmd.exe. Always prepend `PYTHONIOENCODING=utf-8` (and prefer PowerShell / wt / bash).

#### 10.2 Suffix requirements (`.Report`, `.Page`, `.Visual`, `.Workspace`, `.SemanticModel`, `.Theme`, `.Filter`)
Type suffixes are mandatory. `pbir ls Report` fails — `pbir ls Report.Report` works. Glob globbing (`**/*.Visual`) depends on the suffix. (0.9.25) Still correct, and now the documented norm in every command's help rather than a quirk; the suffix is a type marker, not part of the on-disk folder name (see the JSON part, [§10.2](#102-folder-names-on-disk-vs-pbir-cli-dsl)).

#### 10.3 `pbir cp` page copy mismatch bug
After `pbir cp "Source.Page" "Target.Page"`, PBI Desktop can crash with `VisualRelationshipService.getRelationship TypeError` because visual IDs in `visualInteractions`/`parentGroupName`/`syncGroup` don't get re-linked. Workflow:

```
pbir cp "Report.Report/A.Page" "Report.Report/B.Page"
python3 ~/bin/pbir_fix_copied_page.py "Report.Report/definition/pages/<new_id>"
```

#### 10.4 Visual deletion — use `pbir rm`, not `rm -rf`
`pbir rm "...Visual" -f` cleans `visualInteractions`, `parentGroupName`, and `syncGroup` references automatically. Raw `rmdir` / `shutil.rmtree` leaves dangling refs that break the report.

#### 10.5 Never string-replace visual IDs unscoped
Hex IDs appear as substrings in base64 blobs (theme icons, images). A bare find/replace contaminates thousands of unrelated files (4000+ seen in practice). Always:
1. Scope to the target page folder.
2. JSON-quote the match (`"<old_id>"`) to require boundaries.

#### 10.6 `visualInteractions` cleanup after bulk deletes
After deleting multiple visuals, filter the page's `visualInteractions` array to keep only entries whose `source` and `target` IDs still exist.

#### 10.7 Container containers: `visualContainerObjects` vs `visual.objects`
Two layers in `visual.json`:

- `visual.visualContainerObjects` — chrome: `title`, `background`, `border`, `visualHeader`, `divider`, `padding`, `spacing`. Everything in the "chrome containers" list above.
- `visual.objects` — internal rendering: `dataPoint`, `legend`, `categoryAxis`, `valueAxis`, `labels`, `items` (slicer), `value` (cardVisual), etc.

Setting `visualContainerObjects.title.fontSize = 28` does NOT resize a cardVisual's big number — that's `value.fontSize`. `pbir schema describe <type>.<container>` tells you which leaves live where.

#### 10.8 Role names vary per report
Even though `pbir schema roles donutChart` lists `Category, Values, Legend`, real reports sometimes use `Y` or `Axis` on historical clones. Always check an existing same-type visual BEFORE binding new ones:

```python
python3 -c "
import json, pathlib
vtype='donutChart'
for d in pathlib.Path('Report.Report/definition/pages').rglob('visual.json'):
    data=json.loads(d.read_text(encoding='utf8'))
    if data.get('visual',{}).get('visualType')==vtype:
        print(d.parent.parent.name, list(data['visual']['query']['queryState'].keys()))
        break
"
```

Wrong role = silently blank visual.

#### 10.9 Container coordinates (parented visuals)
`pbir add visual` positions are page-absolute. When you later set `parentGroupName` on the visual, Power BI interprets x/y as group-local. Subtract the container's page-absolute x/y to get group-local coords.

#### 10.10 `visual.objects` edits need a PBI Desktop reload (close + reopen)
Just switching pages doesn't re-hydrate `visual.objects` from disk. After writing via `pbir set` / `pbir visuals cf`, you need close+reopen. Chrome-level (`visualContainerObjects`) edits are picked up on page switch.

#### 10.11 Slicers inside content groups
Audit a page's `parentGroupName` ancestors before `pbir rm` on a container group. Scenario/simulation slicers often live inside content groups — deleting the group silently deletes them. To preserve one, copy the slicer `visual.json` (with a new `name`) out to the page root first, then delete the group.

#### 10.12 Reference-line / error-bar add are non-idempotent
`pbir visuals reference-line add` appends a new entry each call and prints its new id to stdout. Rerunning produces duplicates. `error-bars add` upserts by series, but it's the only one that does; design your automation accordingly.

#### 10.13 `pbir visuals cf --list`, `format-field`, `format-state` are deprecated
- Use `pbir get "<path>.**.cf"` instead of `cf --list`.
- Use `pbir set '<...>.field(Table.Col).<prop>'` instead of `format-field`.
- Use `pbir set '<...>.hover.<prop>'` (or `.press`/`.selected`) instead of `format-state`.

All three deprecated commands are **scheduled for removal in pbir 1.0.0**.

#### 10.14 `pbir filters pane-set` only works at theme or page level
Pane styling is a theme property — setting it at report level is ignored silently. Pass a page path or configure the theme directly via `pbir theme set-formatting`.

#### 10.15 Unrecognised visual types in `pbir schema containers`
A few types listed by `pbir add visual --list` (e.g. `stackedBarChart`, `stackedColumnChart`, `pageNavigator`) are NOT separately registered in the flattened schema — they're aliases that render under their base type (`barChart` / `columnChart` / `bookmarkNavigator`). If `pbir schema containers X` says "Unknown type", use the alias base or inspect a real visual.json.

#### 10.16 Bulk ops require `--force` or `--dry-run`
`pbir set "**/*.Visual.title.show" --value false` without `-f` errors out (glob detected). Always preview with `--dry-run` first.

#### 10.17 Theme rename cascades
`pbir theme rename` updates the theme filename AND the `report.json` theme reference. If you manually renamed one, re-run `pbir theme rename` to repair dangling refs.

#### 10.18 `pbir connect --clear` deletes local files
The disconnect flow prompts to delete the locally-downloaded report. Choose carefully — there's no "keep local" shortcut if you answer yes.

#### 10.19 Model query cache
`pbir model -d` always fetches fresh by default. Use `--use-cache` to reuse the last fetch, and `--clear-cache` after a model schema change to bust stale entries.

#### 10.20 Fabric auth
`pbir` shows `Fabric CLI: ? Status unavailable - Run fab auth status` when Fabric CLI isn't authenticated. Remote operations (`download`, `publish`, `connect <workspace>`, `report rebind <Workspace/Model>`) require `fab auth login` first.

#### 10.21 Plugin/skill sync
The pbir-cli plugin under `~/.claude/plugins/marketplaces/power-bi-agentic-development/` must be kept in sync with the installed `pbir` binary. After `pip install -U pbir`, `git pull` the marketplace and re-copy to cache + update `installed_plugins.json` so the plugin's command catalog matches the binary.

---

#### Regeneration reminder (P1-182)

This reference is pinned at **pbir 0.9.9** (revalidated against **0.9.25** on 2026-08-21, see [Changes in 0.9.25](#changes-in-0925)). §10.13 calls out three commands
(`pbir visuals cf --list`, `format-field`, `format-state`) scheduled for
removal in **pbir 1.0.0**. When the local `pbir --version` no longer matches
0.9.9 — or 1.0.0 ships — re-run the discovery sequence described at the top
of this CLI part and bump both the header and this footer.

*Last generated: 2026-04-17 from pbir 0.9.9 running on Windows 11.*

<a id="part-vi"></a>
# Part VI — Agentic stack index

**Purpose:** single entry point for agents (Claude, automations) working with PBIP reports via the MCP server + pbir CLI. Every deep reference is linked here with a one-line summary and the exact question it answers.

**Read order for first-time agents:**
1. [§1 The Golden Path](#1-the-golden-path) — edit on disk → `17_Desktop_Bridge reload_and_screenshot` → review (Desktop stays open)
2. [§2 Lifecycle Rules](#2-autonomous-lifecycle-rules) — disk edits vs a running Desktop, encoding, pbir paths
3. [§3 Cheat-Sheet Index](#3-cheat-sheet-index) — "I need to do X" → link to the section that answers it
4. Follow links into [Part V](#part-v) (the PBIR JSON format and the pbir CLI) and [Part I](#part-i) when details are needed

---

## 1. The Golden Path

The reliable way to modify a PBIR page: edit on disk, reload the RUNNING Desktop in place, look, repeat. Desktop is never closed.

```
+----------------------------------------------------------------------+
| 0. (Only if Desktop holds unsaved live-model edits)                  |
|    17_Desktop_Bridge { operation: "save", pbip_path } BEFORE any     |
|    disk edit - a save after a disk edit overwrites it                |
|                                                                      |
| 1. EDIT on disk (Desktop stays open)                                 |
|    Prefer MCP ops over raw pbir shell (see 2.2 for why)              |
|    - Add visuals:   11_PBIP_Authoring:pbir_add_visuals               |
|    - Format props:  pbir set "<path>.Visual.<container>.<prop>"      |
|    - Delete visual: 11_PBIP_Authoring:delete_visual                  |
|    - Container prep: 11_PBIP_Authoring:pbir_resolve_container        |
|                                                                      |
| 2. RELOAD + SCREENSHOT                                               |
|    17_Desktop_Bridge { operation: "reload_and_screenshot",           |
|                        pbip_path, page_name, output }                |
|    (reloads the running Desktop in place, settles, captures page)    |
|                                                                      |
| 3. REVIEW the screenshot -> if more edits are needed, GOTO 1         |
+----------------------------------------------------------------------+
```

The full playbook (layers, dirty-state gate, failure modes) is the `pbi-live-iterate` skill; model edits (`02_Model_Operations`) are live instantly and are verified with `04_Run_DAX`, never with a reload.

**Anti-patterns that will bite you:**
- Saving Desktop after a disk edit - the save writes Desktop's memory over your edit. `17_Desktop_Bridge save` refuses while disk edits are pending; reload instead.
- Closing Desktop to make disk edits "safe" - never needed, loses the user's session.
- Using literal `Δ` / `∆` characters in measure Property refs via shell pbir - encoding mangles to `Î"` (bytes `c3 8e e2 80 9d`). Use MCP `pbir_add_visuals` which serializes via `json.dumps(ensure_ascii=True)` -> `\u0394`.
- Screenshotting a page right after a page switch without letting it settle - use `reload_and_screenshot` (it settles) or retry once.

---

## 2. Autonomous Lifecycle Rules

### 2.1 Disk edits and a running Desktop

Desktop stays open. The ordering rule (save BEFORE the disk edits when Desktop holds unsaved in-memory/TOM work, edit, reload once, never save in between; ANY Desktop save, including the user's Ctrl+S, serializes Desktop's memory over pending disk edits), the dirty-state gate and what a reload applies are in [Part I §7](#7-live-report-iteration-and-the-desktop-bridge); the tool-level contract is [Part III §III.24](#iii24-17_desktop_bridge--debug).

Correct loop: `edit PBIR files -> 17_Desktop_Bridge reload_and_screenshot -> review -> edit -> ...`. The bridge refuses a reload that would discard unsaved work. Never close Desktop to "make edits safe"; a model reopen after non-metadata TMDL edits is the user's call (`17_Desktop_Bridge open`).

### 2.2 Prefer MCP op over raw pbir shell — encoding matters

Measure names with non-ASCII (`Δ`, `∆`, `×`, `€`) break when pbir is invoked via Bash on Windows because:
- Bash reads your JSON as Latin-1
- Re-emits as UTF-8
- Result: `Δ` (U+0394, `ce 94`) → `Î"` (`c3 8e e2 80 9d`)

PBI then shows "Something's wrong with one or more fields" because the measure ref no longer resolves.

**Fix**: use the MCP op `11_PBIP_Authoring:pbir_add_visuals` — it serializes via `json.dumps(ensure_ascii=True)` which escapes non-ASCII to `\u0394` in the temp JSON file, surviving pbir's re-encoding.

**Recovery**: if already corrupted, replace bytes in place:
```python
CORRUPT = b'\xc3\x8e\xe2\x80\x9d'
CORRECT = b'\xce\x94'
for f in pathlib.Path(pbip_report_folder).rglob('*.json'):
    raw = f.read_bytes()
    if CORRUPT in raw:
        f.write_bytes(raw.replace(CORRUPT, CORRECT))
```

### 2.3 pbir CLI DSL suffixes

Paths to `pbir set` / `pbir visuals` commands need type suffixes that are NOT in folder names:
- Folder: `definition/pages/abc123/` → pbir arg: `Report.Report/abc123.Page`
- Folder: `.../visuals/my-card/` → pbir arg: `…/my-card.Visual`

The MCP op `pbir_add_visuals` adds `.Page` automatically (fixed 2026-04-17 after timeout bug).

### 2.4 Waiting after a reload

`17_Desktop_Bridge reload_and_screenshot` settles the canvas itself (retry the screenshot once on `page_not_found` / an empty page list right after a reload). No fixed sleeps and no process polling are needed; Desktop is not restarted.

### 2.5 cardVisual has TWO font multipliers that compound

Sizing is controlled by BOTH `value.fontSize` AND `layout.calloutSize`, and they multiply. The sizing table and the verify-by-screenshot rule are in [Part I §5.14.7](#5147-cardvisual-font-compounding).

### 2.6 Delta/Simulation measures return BLANK without variance slicer

Measures like `Net Asset Value Δ (Simulation)` depend on `s Ownership Variance` slicer = "Simul 1". In the autonomous flow the slicer may default to empty → cards show "Fields need to be fixed" even though the measure name resolves correctly.

**Verify with run_dax** before authoring:
```dax
EVALUATE ROW("delta", [Net Asset Value Δ (Simulation)])
// Returns BLANK = measure needs slicer context
```
Use `Net Asset Value Δ Simul 1` directly (no variance slicer dep) OR set an explicit filter in the visual's filterConfig.

---

## 3. Cheat-Sheet Index

| I need to… | Go to |
|------------|-------|
| **Add a visual** (card/matrix/line/bar) with role-validated fields + container parenting | MCP op `11_PBIP_Authoring:pbir_add_visuals` → [CLI part §4 `pbir add`](#pbir-add) |
| **Know what roles a visual type accepts** | [CLI part §5 Visual catalog](#5-visual-type-catalog-every-type--roles) — all 49 types |
| **Find a formatting property path** | `pbir schema describe <type>.<container>` → [CLI part §6](#6-container-catalog-per-visual-type) |
| **Understand visual.objects vs visualContainerObjects** | [JSON part §3](#3-visualobjects-vs-visualcontainerobjects--the-chrome-vs-internal-distinction) — chrome (title/bg/border) vs internal (axes/legend/values) |
| **Write a Literal value in visual.json** | [JSON part §6 Literal encoding](#6-literal-value-encoding) — `"24D"` for numbers, `"'quoted'"` for strings |
| **Build a new page from an existing page template** | Use `11_PBIP_Authoring:clone_page` (NEVER `pbir cp` — has mismatch bug), then `delete_visual` old content, then `pbir_add_visuals` new content |
| **Position visuals inside a container group** | Use `11_PBIP_Authoring:pbir_resolve_container` → subtract container's page-absolute `(x, y)` from your page-absolute coords → set `parentGroupName` on each visual |
| **Find all content slot coordinates for a page** | Call `11_PBIP_Authoring:pbir_resolve_container` on the page to read each content group's (x, y, width, height) at runtime. |
| **See example of visual X in the wild** | `07_Visual_Operations {operation: "list"}` on a known page, filter by `visualType`, then `07_Visual_Operations {operation: "get_raw_property"}` to inspect bindings. |
| **Set conditional formatting** | [CLI part §8 CF](#8-conditional-formatting-cf-command-surface) — `pbir visuals cf` structural + `pbir set ...cf.*` leaf edits |
| **Bulk-edit lots of visuals** | [CLI part §9 Batch spec v2](#9-batch-spec-v2-schema) |
| **Bind a second measure to an existing visual** | `pbir visuals bind "<visual>" -a "<role>:<field>" -t <Column|Measure>` |
| **Set a theme color or text class** | [CLI part §7 entity types — theme](#7-entity-types-and-containers) — 37 color tokens + 14 text classes |
| **Verify a measure resolves before using it** | `04_Run_DAX` → `EVALUATE ROW("test", [MeasureName])` — if BLANK, check filter dependencies |
| **Debug why a visual shows "Fields need to be fixed"** | [§2.6 variance slicer](#26-deltasimulation-measures-return-blank-without-variance-slicer), then check encoding: `hexdump -C visual.json | grep "Property"` |
| **Check contamination after edits** | `git status --short | grep Report | grep -v <new_page> | wc -l` — must equal pre-existing count |
| **Inspect a DAX query plan + get a verdict** | `13_Query_Plan {operation: "analyze", query: "EVALUATE ..."}` → returns verdict (ok/warn/hot) + findings list with rule ids and hints. Use `capture` to grab raw plan, `compare` to diff before/after a refactor. |
| **Find time-intel measures that should be a calc group** | `02_Model_Operations {object_type: "calculation_group", operation: "detect"}` -> ranked candidates -> `scaffold` to get the calc-group definition (`list_patterns` lists the detectable patterns) -> create it with the same tool's calc-group ops. |
| **Refactor DAX across many measures (inline / extract / pattern / move / swap-table)** | `15_DAX_Refactor {operation: "preview", transform: "<name>", args: {...}}` → review edits → `apply` with the returned `plan_hash`. Apply is gated (`confirm_destructive` for the CATASTROPHIC ones; `move_measure_batch` is preview-only). |
| **Ask the model "what depends on measure X" or "is X safe to delete"** | `16_Dependency_Graph {operation: "build"}` → `get_upstream` (= the node's consumers) / `get_downstream` (= its dependencies) / `get_impact` / `find_unused` / `find_cycles`. Custom queries via `query` op (JSONPath). |

---

## 4. Reference Doc Index

| Where | What it answers | Notes |
|-------|-----------------|-------|
| [Part I — User guide](#part-i) | End-user guide to all MCP tool families, operations, and workflows | Canonical user-facing text |
| [Part III — Tool reference](#part-iii) | Every operation and argument of every tool | Updated with the code; also served as resources |
| [Part V — PBIR JSON reference](#pbir-json-reference) | PBIR file format: visual.json keys, chrome vs internal objects, field binding, literal encoding, selectors, groups | |
| [Part V — pbir CLI reference](#pbir-cli-reference-099-revalidated-against-0925) | Exhaustive pbir CLI: every command, flag, visual type, container, CF structure, batch spec v2, 21 gotchas | Revalidated against 0.9.25 |
| [visual-catalog/INDEX.md](visual-catalog/INDEX.md) | Per-type reference for every in-scope visual (roles, containers, properties, templates, gotchas). Agent-first; read per-type first when authoring. | ~70 lines; links to 43 per-type files |
| [design-principles/INDEX.md](design-principles/INDEX.md) | Agent reasoning layer for Power BI design decisions (page structure, chart selection, density, cards, color, interactivity, typography). The *why* behind the rules the `reports:pbi-report-design` and `reports:modifying-theme-json` skills enforce. Refreshed May 2026 with 2026 platform notes and WCAG/IBCS cross-references. | ~200 lines |

### 4.1 Visual Catalog

Agent-first reference for every in-scope Microsoft Power BI visual type. Stable per-type anchors: `## Overview`, `## Roles`, `## Containers`, `## Properties`, `## Conditional formatting`, `## Templates`, `## Gotchas`, `## Related`.

- **Entry point:** [visual-catalog/INDEX.md](visual-catalog/INDEX.md)
- **Per-type files:** `docs/visual-catalog/<visualType>.md` — 43 types
- **Shared containers/conventions:** `docs/visual-catalog/_shared/` — 14 files
- **Raw R0101 extracts:** `docs/visual-catalog/_extracts/` — one `visual.json` copy per R0101 visual, plus picker-selected `.png` screenshots. Generated locally by the build and git-ignored, so it is not part of a checkout

Read the per-type file first when authoring a visual; it cross-links to [Part V](#part-v) for deeper detail (anchors `cli-<type>` and `json-<type>`).

Regenerate: `python -c "from tools.visual_catalog.build import build_all; build_all(report_root=..., out_dir=Path('docs/visual-catalog'))"` — see `tools/visual_catalog/README.md` for the full workflow (harvest → render → optional screenshot pass through `17_Desktop_Bridge screenshot`).

### 4.2 Design Principles

Agent reasoning layer — the *why* behind Power BI design rules. Complement to the mechanical Visual Catalog (4.1). Stable per-topic anchors: `## Principle`, `## Why it works`, `## How to apply`, `## Worked examples`, `## Anti-patterns`, `## Self-audit checklist`, `## See also`.

- **Entry point:** [design-principles/INDEX.md](design-principles/INDEX.md) — includes a "when to load what" decision matrix and cross-topic tensions section.
- **Topic files (priority order):** `page-structure.md` → `chart-selection.md` → `density.md` → `cards-and-kpis.md` → `color.md` → `interactivity.md` → `typography-spacing.md`.

Load pattern: read the INDEX at task start, then load the 1-2 topic files relevant to the decision at hand (~200 lines each, ~2k-4k tokens). At end-of-build, re-read each topic's `## Self-audit checklist` section and run through it against the produced report.

The autonomous design agent (sub-project E) cites these files as reasoning; the rules-layer skills (`reports:pbi-report-design`, `reports:modifying-theme-json`) will link into them during sub-project D's skill rewrites.

### 4.3 Topics owned by skills, not this library

This library is the **A/B layer** — visual mechanics (visual-catalog) and design reasoning (design-principles). DAX authoring, semantic modeling, theming, PBIP project ops, Fabric/workspace ops, Tabular Editor, and the end-to-end autonomous build workflow are **skill-owned** and live outside this repo.

**Do not duplicate skill knowledge here.** When an agent needs guidance on these topics, invoke the skill via the `Skill` tool. Skill files live at `~/.claude/skills/<skill-name>/` and are loaded on demand.

#### PBIR report authoring skills (D/E layer)

| Skill | Invoke when |
|-------|-------------|
| `autonomous-pbi-build` | "Build me a page for X" / autonomous end-to-end from a brief |
| `pbir-page-builder` | Rebuild / copy / redesign a whole page; slicer audit; group deletion |
| `pbi-visual-author` | Add / bind / reformat a single visual |
| `pbi-report-audit` | Read-only audit against the 7 design-principles self-audit checklists |

Decision rule: `pbi-visual-author` (single visual) → `pbir-page-builder` (whole page) → `autonomous-pbi-build` (from brief). Escalate as scope grows; never invoke `pbir-page-builder` for a single-visual edit.

Layer dependencies:

```text
E: autonomous-pbi-build
     │
     ├── D: pbir-page-builder  ──┐
     ├── D: pbi-visual-author  ──┤──► A visual-catalog (this library)
     └── D: pbi-report-audit   ──┘    B design-principles (this library)
                                      C MCP ops (this server)
```

#### All user skills (invoke via Skill tool, never read files directly)

| Skill | Domain |
|-------|--------|
| `dax` | DAX authoring, measures, time intelligence, anti-pattern review |
| `autonomous-dax-debug` | Closed-loop DAX debug → fix → re-verify |
| `powerbi-data-modeling` | Star schema, relationships, storage modes, Direct Lake, calc groups, RLS/OLS |
| `pbip` | PBIP project structure, rename/fork, PBIX→PBIP migration |
| `tabular-editor` | BPA rules, C# TOM scripting, TE2 CLI deployment |
| `pbi-theme` | Theme JSON authoring, sentiment colours, text classes, `modifying-theme-json` |
| `pbi-custom-visuals` | Deneb/Vega-Lite, SVG DAX, Python/R visuals |
| `pbi-report-audit` | Read-only design audit against the 7 checklist sections |
| `pbi-model-audit` | Model quality / BPA / AI-readiness audit |
| `pbi-visual-author` | Single-visual CRUD (D layer) |
| `pbir-page-builder` | Page-level build/clone/redesign (D layer) |
| `autonomous-pbi-build` | Top-level autonomous page build from a brief (E layer) |
| `fabric` | Fabric workspace ops, `fab` CLI, deployment pipelines |

Routing guidance for common intents is generated from `~/.claude/hooks/pbi-skill-routing.json` (the `pbi-skill-router` hook names the skills per prompt) and indexed in `~/.claude/CLAUDE.md` §"Working on PBI/Fabric" — not in this file.

---

## 5. MCP Op Quick-Reference

### Live iteration (17_Desktop_Bridge) - Desktop is never closed
The loop op is `reload_and_screenshot` {pbip_path, page_name, output}; `status` reports `dirty_sources`, `reload` / `save` refuse while unsaved work or pending disk edits would be lost, `open` is the only op that starts Desktop and there is no close op. Every operation, argument and refusal code: [Part I §5.27](#527-17_desktop_bridge--reload-and-screenshot-a-running-desktop) and [Part III §III.24](#iii24-17_desktop_bridge--debug); the live-iteration playbook is the `pbi-live-iterate` skill.

### Authoring (11_PBIP_Authoring)
- `clone_page` {source_page, new_display_name} — full page copy with ID regeneration (safe)
- `delete_visual` {page_id, visual_id} — remove one visual + refs
- `pbir_add_visuals` {page_id, visuals[], container_group_id?, container_x/y?} — bulk add with role validation + container parenting
- `pbir_resolve_container` {page_id, container_display_name} — look up group id + page-abs coords

### Visual Formatting (07_Visual_Operations)
- `list` {page_name, visual_type} — enumerate visuals
- `set_raw_property` {visual_name, json_path, value} — surgical JSON edit (when pbir path doesn't work)
- `add_field` / `remove_field` {table, field, bucket} — role binding changes
- `update_position` {page_name, visual_name (or display_title), x, y, width, height}

### Debug (09_Debug_Operations)
- `audit` {page_name} — run every visual query, report errors
- `visual` {visual_name, trace: true} — SE/FE timing
- `run_dax` {query} — raw DAX execution

### Connection (01_Connection)
- `detect` — list running PBI instances
- `connect` {model_index: 0} — wire the shared connection state

---

## 6. Coordinate System — always resolve containers before hardcoding

**⚠️ P2-256: the numbers below are the R0101 reference only.**
They assume a 1920×1080 canvas and the specific root-group layout R0101 uses.
Any other canvas size or template invalidates every coordinate on this page.

**Authoritative path:** call `11_PBIP_Authoring:pbir_resolve_container(pbip_path, page_name, container_name)`
first — it returns the container's page-absolute position + size, and the
local-coord offset you need when inserting visuals inside a `parentGroupName`.
Hardcoding from the table below without resolving works ONLY for the exact
R0101 canvas; anywhere else you will silently place visuals off-canvas.

### R0101 reference (for context only — always resolve first)

Every R0101 content page has 3 root groups at identical page-absolute coords:

```
Top Navigator group      (x=0,  y=0,   w=1920, h=80)
Filters pane group       (x=1500, y=90, w=420, h=1000)   # right-side rail
Visuals and graphs group (x=20, y=109, w=1480, h=920)    # THE content area
```

When adding visuals to R0101's "Visuals and graphs" at page-absolute `(px, py)`:
```
local_x = px - 20
local_y = py - 109
```

**R0101 content slots** (page-absolute, subtract (20, 109) for local):
| Slot | Page-abs position | Size | Typical use |
|------|-------------------|------|-------------|
| KPI band | (24, 165) | 930×160 | 4 cards across |
| Trendgraph | (974, 166) | 930×248 | Time-series line chart |
| Matrix | (20, 345) | 934×405 | Entity breakdown table |
| Waterfall | (974, 433) | 933×319 | Period delta chart (columnChart + WF measures) |
| Donut strip | (24, 771) | 1880×290 | Bottom-row donuts |

---

## 7. Visual Type Quick Lookup

Most-used in R0101 (from page catalog):
- **cardVisual** — single KPI number. Role: `Data`. [JSON part §5](#5-roles-per-visual-type)
- **lineChart** — time series. Roles: `Category`, `Y` (multi), `Legend`
- **clusteredBarChart / barChart / columnChart** — Roles: `Category`, `Y`, `Legend`
- **pivotTable** — matrix. Roles: `Rows`, `Columns`, `Values`
- **tableEx** — flat table. Role: `Values`
- **donutChart / pieChart** — Roles: `Category`, `Values`, `Legend`. Note: some reports (notably R0101) use `Y` as the value role instead of `Values`; both are accepted by pbir bind. When authoring always check an existing same-type visual on the target report first — role names are stable within a report but can differ across report templates.
- **slicer / advancedSlicerVisual** — filter UI. Role: `Values`
- **textbox** — static text. No roles.
- **shape / image** — decoration. No roles.
- **bookmarkNavigator** — tab-like nav. Custom.

Full 49-type catalog: [CLI part §5](#5-visual-type-catalog-every-type--roles).

---

## Maintenance

- **When adding a new MCP op** → update §5 Op Quick-Reference
- **When finding a new gotcha** → add to §2 + the relevant doc's gotcha section + `~/.claude/CLAUDE.md` PBI section
- **When pbir version changes** → re-run the CLI reference agent's discovery sequence
- **When a new skill is added** → add it to the §4.3 skills table
- **When design-principles files are updated** → update the "Refreshed" note in §4 Reference Doc Index row for `design-principles/INDEX.md`

---

*MCP-PowerBi-Finvision User Guide — version 7.21.0 — © Finvision*
