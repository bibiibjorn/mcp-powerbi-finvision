# MCP-PowerBi-Finvision

[![Version](https://img.shields.io/badge/version-7.21.1-blue.svg)](https://github.com/bibiibjorn/MCP-PowerBi-Finvision)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](https://www.microsoft.com/windows)
[![Tools](https://img.shields.io/badge/tools-28-orange.svg)](#tool-catalog)

A production-ready Model Context Protocol (MCP) server for Power BI Desktop. Drives the live model and PBIP files via .NET interop — DAX execution and debugging, TOM CRUD, TMDL automation, PBIR report authoring, BPA, query plans, codemods, dependency graphs, and a live Power BI Desktop bridge (in-place reload + screenshot).

**28 tools across 11 categories**, all running locally over stdio. No exposed ports, no cloud calls.

---

## Highlights

- **Live model CRUD** via TOM — tables, columns, measures, relationships, calc groups, RLS, perspectives, cultures, OLS, field parameters, annotations
- **DAX**: execution with auto-trace, query-plan capture/parse/analyze (logical, physical, xmSQL), benchmarks, codemods (`inline_measure`, `extract_measure`, `replace_pattern`, `move_measure_batch`, `swap_table_reference`), dependency graph, impact analysis
- **TMDL automation**: export, find/replace, bulk rename, migration, daxformatter.com integration
- **PBIP/PBIR authoring**: page CRUD, visual CRUD with field binding, themes, conditional formatting, bookmarks, slicer config, Q&A linguistic schema
- **Debugging**: visual debugger fusing PBIP layout + live model data, SE/FE trace, cross-visual validation, filter permutation, decomposition, root-cause, page profiling
- **Live Desktop loop**: `17_Desktop_Bridge` reloads a running Desktop in place (report, or metadata-only TMDL with `include_model: true` behind a change classifier), saves via UI automation with on-disk verification, screenshots pages / individual visuals; fail-closed guard against discarding unsaved work (Microsoft Desktop Bridge CLI ≥ 1.0.0 supported)
- **Analysis & reporting**: BPA (curated ~72-rule set + Microsoft/Tabular Editor rulesets), model comparison, structured dependency graph, Word documentation, interactive HTML reports

---

## Requirements

| | |
|---|---|
| **OS** | Windows 10/11 (64-bit) |
| **Power BI Desktop** | Installed and running |
| **.NET Framework** | 4.7.2 or higher |
| **Python** | 3.10+ |

---

## Quick start

### 1. Install

**From package (recommended):**
1. Download the latest `.mcpb` from releases
2. Open Claude Desktop → **Settings → MCP Servers → Install from file**
3. Select the `.mcpb` file

**From source with the setup script (recommended):**
Run `MCP Server Setup.bat` from a clone of this repo, or on its own: it then clones this repo for you
(`set MCP_CLONE_DIR=C:\repos` skips the folder dialog; `MCP_REPO_URL` points it at a fork). It installs
Python 3.12+ and uv if missing, builds `venv\` (a re-run reuses it),
installs `requirements.txt` (including the vendored `pbi-perf-core\`) and the optional masking engines
(`requirements-optional.txt`; skip with `set MCP_SETUP_SKIP_OPTIONAL=1`), verifies the imports, registers
the server in Claude Desktop, in Claude Code (when the `claude` CLI is present) and in VS Code for GitHub
Copilot agent mode (when the `code` CLI is present, via `code --add-mcp`), and sets up client masking on
demand. Restart the client afterwards. Clone to a short path (the deepest file is ~135 characters) or
enable `git config --global core.longpaths true`.

**From source by hand:**
```bash
git clone https://github.com/bibiibjorn/mcp-powerbi-finvision.git MCP-PowerBi-Finvision
cd MCP-PowerBi-Finvision
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Add to your MCP client config:
```json
{
  "mcpServers": {
    "mcp-powerbi-finvision": {
      "command": "python",
      "args": ["path/to/MCP-PowerBi-Finvision/src/run_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

### 2. Connect to a running PBI Desktop instance

```
01_Connection { operation: "detect" }
01_Connection { operation: "connect", model_index: 0 }
```

### 3. Inspect

```
02_Model_Operations { object_type: "table",   operation: "list" }
02_Model_Operations { object_type: "measure", operation: "list" }
04_Run_DAX         { query: "EVALUATE TOPN(10, 'Sales')" }
```

### 4. Common workflows

**Analyze a measure:**
```
05_DAX_Intelligence { expression: "[Total Sales]", analysis_mode: "all" }
```

**Debug a slow visual** (PBIP-aware — set path once, reuse):
```
09_Debug_Operations { operation: "set_path", pbip_path: "C:/path/to/Report.pbip" }
09_Debug_Operations { operation: "visual", page_name: "Overview", visual_name: "Sales by Region", trace: true }
```

**Capture a query plan:**
```
13_Query_Plan { operation: "capture", dax_query: "EVALUATE …" }
13_Query_Plan { operation: "analyze", capture_id: "<id-from-capture>" }
```

**Bulk-add visuals to a page (UTF-8 safe via pbir CLI):**
```
11_PBIP_Authoring { operation: "pbir_add_visuals",
                    pbip_path: "...",
                    page: "Overview",
                    visuals: [{ visual_type: "card", x: 16, y: 16,
                                width: 220, height: 100,
                                fields: { Data: "Measures[Total Sales]" } }] }
```

**Reload the running Desktop after file edits and look at the result:**
```
17_Desktop_Bridge { operation: "reload_and_screenshot", pbip_path: "...", page_name: "Overview" }
17_Desktop_Bridge { operation: "reload", pbip_path: "...", include_model: true }   # metadata-only TMDL edits
```

For a comprehensive walkthrough of every tool with worked examples, see **[docs/GUIDE.md](docs/GUIDE.md)** (Part I, the user guide; `python build/md_to_pdf.py` exports the whole guide to a PDF).

---

## Tool catalog

28 tools across 11 categories. Names follow `NN_Tool_Name` where `NN` reflects category order.

| Category | Tool | Purpose |
|----------|------|---------|
| **CORE** | `00_Client_Mode` | Client-masking gateway for Claude Code: client values become pseudonyms before a request leaves the machine, real values come back in the answer; per-chat mode the user switches by typing `masking off/on` (status / on / refresh / gateway_* / route_folder / route_all) |
| | `01_Connection` | Detect / connect / reconnect to PBI Desktop instances |
| | `10_Show_User_Guide` | Display embedded user guide |
| **MODEL** | `02_Model_Operations` | Live TOM CRUD: tables, columns, measures, relationships, calc groups, partitions, hierarchies, perspectives, cultures, named expressions, OLS, RLS roles, annotations, field parameters |
| | `02_TMDL_Operations` | TMDL export, find/replace, bulk rename, migration, DAX formatting |
| | `15_DAX_Refactor` | Bulk DAX refactor transforms with `plan_hash` staleness check (`inline_measure`, `extract_measure`, `replace_pattern`, `move_measure_batch`, `swap_table_reference`) |
| **BATCH** | `03_Batch_Operations` | Transactional bulk CRUD (3–5x faster than serial calls) |
| **QUERY** | `04_Run_DAX` | DAX execution with auto-limits and trace |
| | `04_Query_Operations` | Data sources, M expressions, search, RLS testing, DMV probes |
| **DAX** | `05_DAX_Intelligence` | Static analysis: context transitions, anti-patterns, optimization suggestions, dependencies, impact |
| | `05_Column_Usage_Mapping` | Column ↔ measure usage analysis (live + offline PBIP) |
| **ANALYSIS** | `06_Analysis_Operations` | Simple/full BPA + model comparison |
| | `16_Dependency_Graph` | Structured queryable dependency graph (upstream, downstream, cycles, impact, JSONPath) |
| **PBIP** | `02_QA_Linguistic` | Q&A synonyms + IncludeInQnA flags (offline) |
| | `07_Report_Operations` | Report info, measure usage, rebind, backup/restore, extension measures |
| | `07_PBIP_Operations` | Offline analysis: validate, compare, document, dependency HTML, aggregation analysis, semantic git diff |
| | `07_Page_Operations` | Page CRUD + filters + drillthrough + interactions |
| | `07_Visual_Operations` | Visual CRUD, formatting, field binding, sort/action/code injection, slicer config, raw JSON property access |
| | `07_Bookmark_Operations` | Bookmark CRUD + interactive HTML analysis |
| | `07_Theme_Operations` | Theme colors, fonts, formatting defaults, conditional formatting |
| | `11_PBIP_Authoring` | Page/visual create/clone/delete + bulk pbir CLI with container parenting |
| **DOCS** | `08_Documentation_Word` | Generate/update `.docx` model documentation |
| **DEBUG** | `09_Debug_Operations` | The visual debugger — fuses PBIP layout with live model data, SE/FE trace, debug loop |
| | `09_Validate` | Cross-visual consistency, expected values, filter permutation tests |
| | `09_Profile` | Page perf profiling, decomposition, contribution, root-cause |
| | `09_Document` | PBIP + live-model documentation, measure/filter lineage |
| | `13_Query_Plan` | Capture, parse, analyze, compare DAX query plans |
| | `17_Desktop_Bridge` | Reload / save / screenshot a running Desktop in place (Microsoft Desktop Bridge); `include_model` for metadata-only TMDL; fail-closed unsaved-work guard |

Full per-operation reference (every parameter, every gotcha): **[Part III of docs/GUIDE.md](docs/GUIDE.md#part-iii)**; in-server: `10_Show_User_Guide tool=<name>`.

---

## Safety: gate policy

Destructive operations are gated by `MCP_AUTONOMOUS_GATE_POLICY`:

| Value | Behavior |
|-------|----------|
| `OFF` | No gating |
| `LIGHT` (default) | Blocks only **catastrophic** ops (mass delete >10, table drop, cascade rename, report restore, full refresh, snapshot restore, `inline_measure delete_after=true`, `move_measure_batch >10`). Ordinary destructive ops pass with `gate_notice`. |
| `WARN` | All destructive pass; emit `gate_warning` |
| `STRICT` | All destructive blocked unless `confirm_destructive=true`. **Recommended for unsupervised agent use.** |

Break-glass: `confirm_destructive=true` per call.

---

## Reload hints

PBIP-disk-write ops (visual/page/authoring/theme/TMDL/bookmark) return:
```json
{ "...": "...", "reload_required": true,
  "reload_op_chain": ["17_Desktop_Bridge:reload_and_screenshot"] }
```

Live-TOM ops (`02_Model_Operations`) propagate immediately — no reload needed.

---

## Architecture

```
┌──── MCP stdio (Claude / any MCP client) ────┐
│                                              │
│  src/run_server.py → pbixray_server_enhanced │
│           │                                  │
│           ▼                                  │
│   server/ (MCP protocol layer)               │
│      ├── registry.py     (HandlerRegistry)   │
│      ├── dispatch.py     (ToolDispatcher)    │
│      ├── middleware.py   (truncation, json)  │
│      ├── pbip_cache.py   (LRU PBIP cache)    │
│      └── handlers/       (28 tool handlers)  │
│           │                                  │
│           ▼                                  │
│   core/  (domain logic, no MCP awareness)    │
│      ├── infrastructure/ (TOM, ADOMD, DLL)   │
│      ├── operations/     (CRUD managers)     │
│      ├── dax/            (analyzer, codemods)│
│      ├── tmdl/           (parser, models)    │
│      ├── pbip/           (PBIR engines)      │
│      ├── analysis/       (BPA)               │
│      ├── comparison/     (model diff)        │
│      └── autonomous/     (bridge, gating)    │
│                                              │
│   lib/dotnet/  (AMO + ADOMD DLLs)            │
└──────────────────────────────────────────────┘
```

Bootstrap asserts exactly **28 tools** registered, fails fast on mismatch.

For full architecture, file paths, class names, and gotchas: **[CLAUDE.md](CLAUDE.md)**.

---

## Development

### Run tests

```bash
python run_tests.py              # canonical entrypoint (preferred)
python run_tests.py --fast       # skip @pytest.mark.slow
python run_tests.py --verbose --cov
python run_tests.py --file test_foo
pytest                           # also works
```

### Lint and type check

```bash
black --check --line-length 100 core server src client_gateway hooks tools tests pbi-perf-core
flake8 core server src client_gateway hooks tools tests pbi-perf-core   # .flake8
mypy core server src client_gateway                                      # mypy.ini: strict modules + legacy baseline
```

### Package

```bash
mcpb pack                        # @anthropic-ai/mcpb: builds a .mcpb from manifest.json
```

### Project structure

```
MCP-PowerBi-Finvision/
├── src/                  # entrypoint (run_server.py)
├── server/               # MCP protocol layer
│   ├── registry.py       # HandlerRegistry, ToolDefinition, ToolCategory
│   ├── dispatch.py       # ToolDispatcher (cached security flags)
│   ├── middleware.py     # token estimation + key compaction
│   ├── pbip_cache.py     # PbipModelCache LRU
│   └── handlers/         # 28 tool handlers + sub-handlers
├── core/                 # domain logic, no MCP awareness
│   ├── infrastructure/   # connection, query exec, DLL loading
│   ├── operations/       # CRUD managers (TOM)
│   ├── dax/              # analyzer, plans, codemods, RLS
│   ├── tmdl/             # parser, models, generators
│   ├── pbip/             # scanner, deps, authoring engines
│   ├── analysis/         # BPA analyzer
│   ├── comparison/       # model diff/compare
│   ├── config/           # config_manager + default_config.json
│   ├── utilities/        # json_utils, mermaid, html
│   └── autonomous/       # desktop bridge, gating, evidence, reload hints
├── config/               # default + local overrides
├── lib/dotnet/           # AMO + ADOMD DLLs
├── tests/                # pytest suite
├── docs/                 # GUIDE.md (the one guide), visual-catalog, design-principles
├── build/                # packaging scripts
├── manifest.json         # MCP manifest
└── CLAUDE.md             # exhaustive project reference
```

---

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — exhaustive project reference (every tool, every operation, every parameter, every gotcha, every internal helper)
- **[docs/GUIDE.md](docs/GUIDE.md)** — the one guide: [user guide](docs/GUIDE.md#part-i) with worked examples and workflows, [client masking](docs/GUIDE.md#part-ii), [tool reference](docs/GUIDE.md#part-iii) (every operation), [architecture](docs/GUIDE.md#part-iv), [PBIR reference](docs/GUIDE.md#part-v) (file format + pbir CLI) and the [agentic stack index](docs/GUIDE.md#part-vi)
- **[docs/visual-catalog/](docs/visual-catalog/)** — per-visual-type reference (43 files)
- **[docs/design-principles/](docs/design-principles/)** — reasoning + self-audit checklists (7 topics)

---

## What's new in 7.21

7.21.0 adds the universal masking gateway (client gateway 1.7.0); details in [Part II of docs/GUIDE.md](docs/GUIDE.md#part-ii).

- **Masking on demand** (2026-10-08) — in Claude Code the user types `masking on`: the gateway starts and that
  chat's folder is routed through it; `masking off` sends it direct again and the gateway stops by itself. Nothing
  runs until then.
- **Desktop apps (opt-in, `setup --desktop-apps`)** — VS Code Copilot and ChatGPT in the browser are masked and
  restored through a local TLS front end with one adapter per wire format; switched off only by the user typing
  `masking off`.
- **Routing and lifecycle** — a name-constrained local CA, a per-user PAC that sends only the AI hosts to the
  gateway, a gateway that lives and dies with the Power BI MCP (no service, no autostart), exact backup and restore
  of the proxy settings.
- **`python -m client_gateway setup`** — one human-run command: a plan by default, `--yes` to apply, `--remove`
  to undo, `--status`, `--register` for Claude Desktop / VS Code / Claude Code, managed-PC check.
- **`00_Client_Mode` `setup` and `selftest`**, generic and `finvision` profiles, a format-agnostic leak scan and a
  per-app replay self-test (update-proofing).

## What's new in 7.20

7.20.0 is the release that follows 7.16.0 (git labels v7.17-v7.19 were internal milestones, so the
number stays monotonic). Highlights of the 2026-10 deep-review work, newest first:

- **Safety** — preview-with-diff on every live write, a data/metadata permission split with policy
  rules, `request_id` on every result, `compare_models` on `compare_targets`, graph direction aliases.
- **Trace and perf** — trace-to-visual mapping, perf record/regress history, progress + cancel on long
  operations, a soft inline-result budget, a `04_Run_DAX` batch mode.
- **BPA** — select/patch engine that applies BPA `FixExpression`s; `06_Analysis_Operations`
  `ruleset=audit` with the audit taxonomy.
- **Testing, API, report and model features** — F-01..F-34 subset (persisted test suites, offline PBIP
  model lane, XMLA read-only gate, journal undo/replay hardening, fail-closed reload classifier).
- **DAX** — canon-conformant analyzer and BPA, safe codemods, `find_clones` / `extract_udf`, `explain`,
  a local formatter.
- **Client gateway** — recognition, state, control-surface and engine-matching fixes (G-01..G-28).
- **Performance and cleanup** — PBIP watcher-validated caches, memoised graph/lineage/TMSL, faster CLR
  conversion, one DAX validation per query, orjson; about 4k dead lines removed, then a final pass
  (unused code, quoting helpers, AMO mixins, module splits).
- **Lint truth** — root `mypy.ini` (strict for the clean new modules, legacy modules listed with
  `ignore_errors`), `.flake8`, a black pass at `--line-length 100`; see *Lint and type check* below.

## Version history

### v7.x (current)

- **v14 expansion (24 → 28 tools)** — added `02_QA_Linguistic` (Q&A linguistic schema CRUD), `13_Query_Plan` (DAX query-plan capture/analyze), `15_DAX_Refactor` (bulk DAX refactor transforms with plan_hash check, was 15_DAX_Codemods), `16_Dependency_Graph` (structured queryable graph). Calc-group migration was merged into `02_Model_Operations object_type='calculation_group'` (`detect`/`scaffold`/`list_patterns`). v7.6 trim removed `SVG_Visual_Operations` (unused) → 27 tools.
- **`02_Model_Operations` extensions** — `measure/set_format_string_expression` (CL 1606+ dynamic format string), `culture/bulk_set_translations`, perspective membership ops, field-parameter mutations (`add_field`/`remove_field`/`reorder_fields`), `calculation_group/set_precedence`.
- **`04_Query_Operations` extensions** — `dmv_probe` + `list_dmv_probes` (8 probes incl. vertipaq_columns, calc_dependency, sessions, refresh_policies). `simulate_as_user` (Desktop RLS workaround). `impersonate_run` for Service/SSAS impersonation.
- **Cascade rename** — `02_Model_Operations` table/column/measure rename now accepts `pbip_path` and rewrites PBIR JSON references via `CascadeRenameEngine.apply_renames_typed` (parent-kind aware — column rename of `Foo` doesn't touch `field.Measure.Property == "Foo"`).
- **Semantic git diff** — `07_PBIP_Operations:git_diff` populates per-file `details` with `{measures, columns, relationships, roles}` added/modified/removed.
- **Version labels** — the server version is `7.21.1` (`src/__version__.py`, `manifest.json`, `python/pyproject.toml`). Git commit messages that say `v7.17`-`v7.19` were internal milestones (trace fixes and a since-removed masking design), not releases; 7.20.0 is the next release after 7.16.0.
- **Visual catalog & design principles** — A-layer + B-layer references under `docs/`.

### v13 consolidation

- 38 tools → 24 across 11 categories
- `01_Connection` (detect+connect merged)
- `02_Model_Operations` (5→1 for CRUD)
- `03_Batch_Operations` (transactions internalized)
- `07_Visual_Operations` dispatches to 3 sub-handlers (`visual_layout_handler`, `visual_config_handler`, `visual_creation_handler`)

### v5.x

- Hybrid analysis features
- Token-optimized response format
- Pattern-based fuzzy search

---

## Contributing

PRs welcome. Before adding a new tool or operation, check **[CLAUDE.md §5](CLAUDE.md#5-tool-reference-every-operation)** for the registration pattern and gating helper. The bootstrap assertion in `server/handlers/__init__.py` enforces a known tool count — bump it when adding a tool.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Author

**Finvision** — [@bibiibjorn](https://github.com/bibiibjorn/MCP-PowerBi-Finvision)

Issues and feature requests: [GitHub issues](https://github.com/bibiibjorn/MCP-PowerBi-Finvision/issues).

---

## Acknowledgments

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [pythonnet](https://github.com/pythonnet/pythonnet) — .NET interop
- [PBIXRay](https://github.com/Power-BI-Ray/pbixray) — model analysis primitives
- [pbir CLI](https://github.com/) — PBIR authoring

---

**Note**: This server runs entirely locally. No data leaves your machine — all communication is stdio between the MCP client and the server.
