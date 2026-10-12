# AGENTS.md — MCP-PowerBi-Finvision

> Single source for every coding agent in this repo (Claude Code imports it from `CLAUDE.md` via
> `@AGENTS.md`; Codex reads it directly). Edit this file, not a copy.

Compact project index, loaded into every session. The exhaustive detail lives in one companion doc, **`docs/GUIDE.md`** — **read the matching Part on demand, and update it (not this file) in the same commit when the corresponding code changes**:

- **GUIDE.md Part III, Tool reference** — every tool, every operation, every arg (the former §5). Read before calling/changing any MCP op you don't know cold. It sits between the `<!-- tool-reference:start -->` / `<!-- tool-reference:end -->` markers; `server/resources.py` (resources `powerbi://docs/tool-reference`, `powerbi://docs/tools/{tool}`, `10_Show_User_Guide tool=`) and the drift tests read only that region, so keep the markers and the `## III.N` + backticked tool name heading format.
- **GUIDE.md Part IV, Architecture** — module-level deep dive of `server/` + `core/` (the former §6). Read before structural work.
- The other Parts: I user guide (frontmatter version + footer are checked by `tests/test_user_guide_drift.py`), II client masking, V PBIR reference (file format + `pbir` CLI), VI agentic stack index.

This file keeps only: commands, layout, the tool map, operational policies, coding rules, and gotchas that prevent real damage.

---

## 1. Project at a glance

- **Name:** MCP-PowerBi-Finvision — Python MCP server exposing **28 tools** that drive Power BI Desktop via .NET interop (pythonnet/CLR) over stdio.
- **Version:** 7.21.1 (`src/__version__.py` — must match `manifest.json` and `python/pyproject.toml`; git `vNN` tags are internal milestones, distinct from semver).
- **Platform:** Windows-only. .NET Framework 4.7.2+, Power BI Desktop, Python 3.10+.
- **Capabilities:** DAX execution + debugging, live TOM CRUD, TMDL automation, offline PBIP analysis, PBIR report authoring, BPA + model comparison, query plans, DAX codemods, dependency graph, live Desktop bridge (in-place reload + screenshot).
- **Entry point:** `src/run_server.py` → `src/pbixray_server_enhanced.py`.

### 1.1 Agentic stack context

This server is the **C (execution)** layer of a 5-layer stack: A mechanics = `docs/visual-catalog/`, B reasoning = `docs/design-principles/`, C = this repo, D atomic skills = `~/.claude/skills/{pbir-page-builder, pbi-visual-author, pbi-report-audit}`, E orchestrator = `~/.claude/skills/autonomous-pbi-build`. Cross-layer index: GUIDE.md Part VI. When evolving op names/signatures, check whether D/E skills reference them and update both sides.

---

## 2. Commands cheatsheet

```bash
python src/run_server.py                 # run the server (stdio MCP)

pip install -r requirements.txt          # production deps (pinned)
pip install -e ".[dev]"                  # dev (pytest, black, mypy, flake8)

# Lint, all from the repo root (what each one enforces: section 7)
black --check --line-length 100 core server src client_gateway hooks tools tests pbi-perf-core
flake8 core server src client_gateway hooks tools tests pbi-perf-core   # reads .flake8
mypy core server src client_gateway                                      # reads mypy.ini

python run_tests.py                      # canonical test entrypoint (preferred)
python run_tests.py --fast               # skip @pytest.mark.slow
python run_tests.py --verbose --cov
python run_tests.py --file test_foo      # basename match
pytest tests/test_foo.py::TestClass::test_method   # also works

"MCP Server Setup.bat"                   # repo-root dev-env setup (clone/venv/deps)
# NOTE: no build\package.bat / .mcpb packaging script exists yet.
```

---

## 3. Repository layout

```text
src/                  bootstrap (run_server.py, pbixray_server_enhanced.py, __version__.py)
server/               MCP protocol layer: registry.py, dispatch.py, middleware.py, pbip_cache.py
core/client_guard/    Client masking shared by the gateway: engine (pseudonyms), detector (what to mask), word lists, home
client_gateway/       local masking gateway: Claude Code route (ANTHROPIC_BASE_URL; hooks/client_masking_hook.py) + TLS front end, adapters, routing, lifecycle, `setup` for the desktop apps (GUIDE.md Part II)
server/handlers/      one file per tool; __init__.py register_all_handlers() asserts 28 tools
server/handlers/_internal/  shared sub-handlers (visual layout/config/creation split)
core/infrastructure/  connection, query executor, DLL loading (lib/dotnet/ AMO+ADOMD)
core/operations/      TOM CRUD managers + batch/changeset engines
core/dax/             analyzer, injector, formatter, rules, tokenizer, optimizer
core/codemods/        15_DAX_Refactor transforms (NOT core/dax/code_rewriter.py)
core/tmdl/            unified_parser.py (canonical), generators, migrator, validator
core/pbip/            scanner, dependency engine, authoring/ (pbir_executor, reference_cleanup)
core/analysis/        bpa_analyzer, m_practices (M001–M012), star_schema_validator
core/autonomous/      desktop_bridge, gating, reload_hints, clr_errors, evidence, loops/debug_loop
core/{graph,query_plan,debug,comparison,config,utilities,validation,...}  see GUIDE.md Part IV
config/               default_config.json (+ gitignored local_config.json override)
tests/                one file per feature; conftest.py fixes sys.path + resets gate cache
docs/                 GUIDE.md (Parts I-VI), visual-catalog/, design-principles/ (the last two are served as MCP resources)
```

---

## 4. Tool map (28 tools — full op tables in GUIDE.md Part III)

| Tool | What it does / critical usage rule |
|------|-----------------------------------|
| `00_Client_Mode` | Control of the **client-masking gateway** (`client_gateway/`): `status`/`gateway_status` (READ; counts, `default_mode`, `warnings`, app labels), `on`, `off` (always refused: the user types `masking off`), `refresh`, `gateway_start`/`gateway_stop`, `route_folder`/`unroute_folder`, `route_all`/`unroute_all`, plus `setup` (READ: returns the command a HUMAN runs, never installs a certificate itself) and `selftest` (READ: offline replay of the recorded wire fixtures, one line per app). **Masking on demand** (2026-10-08): the gateway runs ONLY while a chat has masking on: the user's typed `masking on` (hook, any chat; `on` here does the same) starts it and routes the chat's project folder (`client_gateway/switch.py`); `masking off` unroutes the folder (the direct URL is WRITTEN, never deleted: a running session keeps a removed key) and the gateway exits by itself (`client_gateway/reaper.py`). The MCP never starts it (only the opt-in desktop routing does) and its masking layer masks only while its own chat has masking on. Claude Code's `system` field: its leading short identity lines are never rewritten (a rewritten identity sentence got 429 on every request); the instructions block after them is masked (live-verified 2026-10-08). The gateway masks (front end + adapters for the desktop apps, `setup` for the system pieces, R1-R10). Unroute and stop are refused in a routed chat. Detail: GUIDE.md Part II (user-facing), Part III §III.0a. |
| `01_Connection` | detect / connect / reconnect / `ensure` (idempotent). Multi-instance: correct `model_index`, verify via `database` field. **v7.16** `connect_xmla` (published model, READ-ONLY — `dispatch._security_gate` is an ALLOW-list: every write-classified call is refused with `service_read_only` except tools that only write local files, and `write_lane.commit` raises `ServiceReadOnly` as a backstop; ClearCache is never sent to a service target; token `MCP_PBI_XMLA_TOKEN` or the MSAL cache via `core/infrastructure/xmla_target.py`) and `connect_folder` (PBIP model offline via `core/infrastructure/offline_model.py`; `write_lane.commit` persists each write to TMDL staged as `*.mcp_new` + `os.replace`, with a TOM re-read, restore-on-failure, only changed files rewritten in their own line endings, and `offline_folder_changed` when the folder was edited externally; rollback = re-read the folder via `write_lane.safe_undo`, never a bare `UndoLocalChanges()`). `status` reports `target` + `tool_profile`. |
| `02_Model_Operations` | Live TOM CRUD dispatcher (18 object_types: table/column/measure/relationship/calculation_group/partition/hierarchy/perspective/culture/named_expression/ols_rule/model/role/annotation/field_parameter/function/query_group/calendar). Writes propagate instantly — no reload. `safe_delete`/`safe_rename` = fail-closed evidence-checked composites (run the gate too; STRICT blocks). Rename = lexer cascade over every DAX object + verify/rollback; `preview=true` dry-run + `preview_hash`; `pbip_path` adds the PBIR cascade. `create` never overwrites (`already_exists`; measure names are model-wide) unless `if_exists='update'`. All saves go through `core/operations/write_lane.commit`, which also journals each write (`core/operations/write_journal.py`, `output/journal/<project>.jsonl`, pre/post TMSL images); `object_type='model'` ops `history`/`diff`/`undo`/`replay`/`checkpoint_create`/`checkpoint_restore` read and apply it (config `model_write.journal_enabled`). **M4 parity pack:** `model` `set_properties` (`compatibility_level` = irreversible, needs `allow_compatibility_upgrade` + `confirm_destructive`, CATASTROPHIC, downgrades refused), `refresh_status`/`refresh_history` (Desktop has no refresh log), `refresh_type=indexes` (CL 1707+); `table` `mark_as_date_table` (data-validated); `hierarchy` `add_level`/`remove_level`/`rename_level`; `role` `get_effective_permissions`; `query_group` CRUD; `relationship`/`calculation_group` `rename`, `calculation_group` `update`; `column update` `string_indexing_behavior`/`full_text_indexing_behavior` (CL 1707/1708 — refused below, NEVER auto-upgrades the level). Never add a `SaveChanges()` that bypasses `commit`. **v7.16** `object_type=calendar` (list/get/create/update/delete; `core/operations/calendar_crud_manager.py`, TimeUnit validation before the write). |
| `02_TMDL_Operations` | TMDL export / find_replace / bulk_rename / migrate_measures / format_dax (daxformatter.com). |
| `02_QA_Linguistic` | Q&A linguistic schema CRUD on PBIP (offline; Desktop closed for writes). |
| `03_Batch_Operations` | Transactional bulk CRUD (`batch_operation`) + declarative `apply_changeset` (preview→`plan_hash`→apply). Every batch is ONE atomic SaveChanges (all-or-nothing). CATASTROPHIC: any table delete, delete >10 items, rename batches, table/column updates with `new_name`, table-refresh of >= `model_write.catastrophic_refresh_tables` tables; classic deletes >20 hard-require `confirm_destructive`. |
| `04_Run_DAX` | Execute DAX (must start with EVALUATE); modes auto/analyze/simple/trace/evaluate_and_log — THE DAX executor (`query_handler.execute_dax`; 09 `run_dax`, 04_Query `impersonate_run`, 13 `evaluate_and_log` forward to it); `export_path` writes full result to CSV/JSON. Result cache is keyed on connection + model generation (writes, swaps and Desktop-side edits invalidate it); `bypass_cache=true` forces a round-trip — no more renaming vars. `mode=analyze` = real N-run benchmark (`runs`, `cold`); `mode=validate` = engine parse/bind WITHOUT executing (`{valid, errors[], elapsed_ms}`; unused-branch VAR rewrite in `core/dax/dax_validate.py`, ORDER BY not checked, 10 s backstop); `roles`/`effective_user_name`/`custom_data` honoured. **v7.15.0** `mode=trace` adds `result_sets` (every EVALUATE, not just the first), `execution_metrics` (peak memory, FE/SE CPU, queue wait — fires on Desktop) and `runner_lane`. |
| `04_Query_Operations` | Metadata, `search_objects`, `search_string`, RLS testing (test_rls/impersonate_run/simulate_as_user), 8 DMV probes, `info_view` (INFO.VIEW.* with DMV fallback), `warm_cache`. |
| `05_DAX_Intelligence` | Static DAX analysis, dependencies, impact, `lint_model` (all measures, worst-first), `expand_measure` (DEFINE MEASURE inline). |
| `05_Column_Usage_Mapping` | Column→measure usage. **Unused columns: always `get_unused_columns_pbip` (offline)** — live method has false positives. |
| `06_Analysis_Operations` | `simple` overview (2–5s); `full` = BPA (curated ~75 rules incl. the Sep-2026 TEXTCONTAINS / APPROXIMATEDISTINCTCOUNT / string-index rules, `ruleset=microsoft\|all` for Tabular Editor BPARules; `rules_path` adds TE rules for that call) + performance/integrity + topology + M practices from one TMSL snapshot; `compare` two open models (auto-picks with exactly two open). |
| `07_Report_Operations` | Report structure, measure_usage, rename/rebind, backup/restore (restore = CATASTROPHIC), discover_schema, extension measures. |
| `07_PBIP_Operations` | Offline PBIP analysis. **HTML model report = `analyze` + `output_format='html'` + `auto_open`** — NOT `dependency_html`, NOT `generate_documentation`. Also validate_model, compare_models, query_*, scan_broken_refs, git_diff, aggregation_analysis. |
| `07_Page_Operations` | Page CRUD, page filters, drillthrough, tooltips, interactions. Disk writes — Desktop closed or use bridge reload after. |
| `07_Visual_Operations` | Visual CRUD/layout/formatting/fields/sort/actions/slicers/raw JSON properties. Thin shell → `visual_{layout,config,creation}_handler`. |
| `07_Bookmark_Operations` | Bookmark CRUD + groups + HTML `analyze`. |
| `07_Theme_Operations` | Theme audit/colors/fonts/text classes, conditional formatting (list/add/remove/copy_cf), apply/export/import theme. |
| `08_Documentation_Word` | Word .docx docs (generate / update). |
| `09_Debug_Operations` | **The visual debugger — ALWAYS use this (op `visual` / `run_dax`) for debugging, never `07_Report_Operations`.** PBIP path persists via `set_path`. Also compare/drill/analyze/step_variables/optimize/audit/`debug_loop`. **v7.15.0** `run_dax` returns `result_sets` + `execution_metrics` like `04_Run_DAX`. |
| `09_Validate` | cross_visual, expected_value, filter_permutation, `capture_baseline`/`compare_baseline` (regression guard around model edits). **v7.16** persisted JSON test suites: `test_put`/`test_remove`/`test_list`/`test_run`/`test_export` (JUnit) — `core/validation/test_suites.py`, handler `_debug/tools.py:_handle_suite`. `test_put`/`test_remove` are WRITE (suite JSON only); suite/output paths are path-guarded; zero tests run = `no_tests_ran` + exit 1; last run goes to `<suite>.last-run.json`. |
| `09_Profile` | Page/visual perf profiling (`page trace=true` → Performance-Analyzer table + ESTIMATED "Other" wait; `perf_analyzer_import` → measured pane export), decompose, contribution, trend, root_cause. |
| `09_Document` | Page/report/measure_lineage/filter_lineage docs from PBIP + live model. |
| `10_Show_User_Guide` | Comprehensive user guide (generated from live registry). |
| `11_PBIP_Authoring` | clone_page/clone_report/create_page/create_visual/delete_* + `pbir_add_visuals` (bulk, UTF-8 safe via `ensure_ascii=True`). |
| `13_Query_Plan` | capture/parse/analyze/compare DAX plans, plan_rows, export_daxx, evaluate_and_log, benchmark. **v7.15.0:** real plans via the in-process AMO trace; `capture` returns a SUMMARY (headline, operator census top-20, top-10 `#Records`, cache consumers vs SE queries, findings) and writes `exports/perf/<capture_id>/{ServerTimings.json,QueryPlan.json,capture.daxx}` — plans hit 10k+ rows, so page them with `plan_rows` (`plan`, `offset`, `limit`, `min_records`), never inline. `compare` is a structural plan diff; `benchmark` takes `cold_runs`/`warm_runs` for per-run server timings (avg/stddev/min/max/median). **v7.16** live trace `trace_start`/`trace_fetch`/`trace_stop` (`core/infrastructure/live_trace.py`; auto-stop, one at a time). |
| `15_DAX_Refactor` | preview/apply codemods with `plan_hash` staleness check + `rollback` (journal replay). **`move_measure_batch` is PREVIEW-ONLY** — apply refuses (`transform_not_persistable`); real moves via `02 measure/move`. `inline_measure delete_after=true` = CATASTROPHIC. |
| `16_Dependency_Graph` | build (cached by scope+path+model etag+report signature) / upstream / downstream / find_unused (no consumers; refuses without the report side) / find_cycles / get_impact / `batch_impact` (evidence source) / quick_lineage / export / JSONPath query. Model side = every DAX object (calc columns/tables, calc items, RLS, format strings, UDFs, field params, relationships, sort-by, hierarchies); report side = PBIR bindings/filters/bookmarks. Unknown node = never safe; impact results carry `unknown_nodes`, `report_side_available`, `model_etag`, `built_at`, `graph_scope`. |
| `17_Desktop_Bridge` | **The standard live-iteration path:** edit PBIR → `reload_and_screenshot` (reload running Desktop in place + settle + screenshot) → review → iterate. **Rule: save BEFORE disk edits; after disk edits reload, never save first.** `reload` REFUSES (`unsaved_changes`/`dirty_state_unknown`) when Desktop may hold unsaved in-memory/TOM edits (override `discard_unsaved`); `save` REFUSES (`conflict_pending_disk_and_memory`) while disk edits are pending (override `overwrite_disk_edits`). Also status/save (UI-automation Ctrl+S)/screenshot(_all)/resolve_page/**`manifest`** (capability probe). `screenshot*` take **`scale` (1–3)**; `screenshot_all` takes `settle_ms`. A plain `reload` re-reads the **report** definition only (the MS CLI pins `reloadModelDefinition:false`, 0.1.1 and 1.0.0). **`reload` / `reload_and_screenshot` `include_model=true`** (rare-op arg; needs CLI >= 1.0.0, runs `tools/bridge_lib.mjs`) also re-applies the on-disk TMDL, but only after the same unsaved gate AND a change classifier (`core/autonomous/model_change_classifier.py`) proved it metadata-only (measures, calc columns/tables, relationships, formats, descriptions, roles...): a partition / M / named-expression / data-source change or a new/removed import table/column is refused (`model_reload_unsafe_change`) → close Desktop WITHOUT saving + `open` (a live spike showed such a reload empties the table and can wedge the host). Result carries `model_reload` {applied, mismatch}; verify by DAX, the Data pane can stay stale. `screenshot per_visual` uses `report.snapshot.capture/v2` regions when the live manifest lists them (flighted per instance), else a v1 crop that first locates the page inside the viewport PNG. `open` returns only a pid whose `currentFilePath` matches (the CLI's own pid is untrusted); a save verified on disk that leaves `hasUnsavedChanges` true marks the pid `flag_stuck` (reported, gate not bypassed). The CLI script is resolved from the installed package's `bin` (`dist/cli.js` from CLI 0.2.0); `status`/`manifest` report `bridge_cli_version`. See GUIDE.md Part V, "Changes in 0.9.25". |

Categories (`server/registry.py:ToolCategory`, 11): CORE, MODEL, BATCH, QUERY, DAX, ANALYSIS, PBIP, DOCS, DEBUG, AUTHORING, AUTONOMOUS (empty since v7.8). `12_Autonomous_Workflow` and `SVG_Visual_Operations` were removed (v7.8 / v7.6). Calc-group migration = `02_Model_Operations object_type='calculation_group'` ops detect/scaffold/list_patterns — there is no `14_*` tool.

---

## 5. Architecture essentials (deep dive: GUIDE.md Part IV)

- **Protocol surface (M4):** every tool has a top-level `title` in `tools/list` (`registry.TOOL_TITLES`); 11 prompts (`server/prompts.py`, incl. `create-dax-query`, `run-dax-with-metrics`, `analyze-dax-query`, `connect-desktop-by-name`); resource `powerbi://docs/dax-query-rules` (`server/dax_query_rules.py`, our own wording — never paste vendor resource text); the session `instructions` state the lane split (model/DAX/authoring here, canvas + interaction tests in `pbi-webview2`).
- **Call lifecycle:** `server/mcp_app.py` `call_tool()` → cached-validator input check → rate limit → `ToolDispatcher.dispatch_and_render_async()` (bounded live/offline pools, handler in `contextvars.copy_context()`, per-op read-only/confirm gates from `server/op_classification.py`, writes serialised + `bump_model_generation`) → `server/response_pipeline.py` (serialize once; >450K chars spill to `output/`; no key compaction) → `CallToolResult`. Timed-out WRITE = `timeout_outcome_unknown` (verify, never blind-retry).
- **Opt-in pagination:** pass `page_size` (+ `next_token`) on any tool — dispatcher paginates the first present list key (`measures`/`columns`/`visuals`/`rows`/...), else the largest list (`paginated_key`).
- **New op? classify it** in `server/op_classification.py` (the suite fails on an unclassified op) and give the tool all four annotation hints.
- **New arg? declare it** in the tool's schema (`tests/test_w3_api_schema_drift.py` fails on a read-but-undeclared arg). Rare-op args go in `server/tool_surface.py` `DEFERRED_ARGS` (kept out of tools/list, shown by `10_Show_User_Guide tool=`); long prose goes in `guide_notes` / the `x-doc` keyword, never the emitted description (the tools/list budget test only goes down). Legacy arg names live in `server/arg_aliases.py` (canonical: `query`, `pbip_path`, `page_name`, `visual_name`/`visual_id`, `confirm_destructive`); every failure is `error_envelope(<type>, ...)`.
- **`connection_state`** singleton (RLock, lazy-loads ~15 managers). PBIP LRU cache keyed `path|max_mtime`, mtime re-checked post-parse.
- **Two gating patterns coexist** (verify which a handler uses before "following the pattern"): PBIR/disk-write handlers → `gate_and_wrap()` (`_authoring_utils.py`); live-TOM/DAX handlers (`02_Model_Operations`, `02_TMDL_Operations`, `03_Batch_Operations`, `15_DAX_Refactor`) → inline `require_autonomous_mode()` + `merge_gate_info()`.
- **CRUD manager naming — THREE styles; grep before referencing:** `CRUD` (Measure/Column/Table/Relationship, helper `_get_server_db_model()` → `(server, db, model)`); `Crud` (Culture/Partition/RlsRole/Ols/NamedExpression/Hierarchy/Perspective/Function); no suffix (CalculationGroup/Partition/RLS/Annotation/FieldParameter/ModelRefresh/QaLinguistic — `CalculationGroupManager` uses `_connect_amo_server_db()` → `(server, db, Tabular)`).
- **Config:** `config/default_config.json` + gitignored `local_config.json`; singleton `core.config.config_manager.config` (RLock; dispatcher caches security flags at init). Live-TOM write lane: `model_write.*` (`loop_pause_s`, `health_check_in_loops`, `health_timeout_s`, `verify_renames`, `catastrophic_refresh_tables`). Per-tool timeouts resolve via `core/config/tool_timeouts.py` `_REGISTERED_TOOL_TIMEOUTS` (registered names, v7.14.1).
- **Env vars:** `MCP_AUTONOMOUS_GATE_POLICY` (OFF|LIGHT default|WARN|STRICT), `PYTHONIOENCODING=utf-8`.

### Test discipline

- One test file per feature/module, shipped in the same change. Confirm baseline-green BEFORE changing tests.
- Run the suite with `venv/Scripts/python.exe run_tests.py`. The system Python lacks the venv deps (e.g. `pbi_perf_core`) and reports ~81 false failures + 5 collection errors.
- Test the dispatch shell, not just the manager — mock at the manager boundary (`monkeypatch.setitem(_handlers, ...)` + `MagicMock`).
- `tests/conftest.py` has an autouse **live-seam tripwire**: constructing a real ADOMD connection, spawning the powerbi-desktop bridge CLI (a fake CLI under the temp dir is fine) or a non-loopback `socket.connect` fails the test unless it carries `@pytest.mark.live_seam`. `python run_tests.py --fast` skips the `slow` tests (gateway CLI/hook/images/server/NER, headless CLI, golden sweep; list in conftest `_SLOW_FILES`/`_SLOW_TESTS`).
- Never call live AMO/CLR/network. CLR exception tests: `FakeClrException` with `GetType()`/`Message`/`InnerException` attrs.
- TOM lazy imports: `monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", fake_tom)` AND chain the `Microsoft` + `Microsoft.AnalysisServices` parent modules.
- Coverage-gap workflow ("review the test suite"): run suite green → list modules without `test_*.py` → cross-ref `git log --since="3 months ago" -- core/ server/` → propose 3–5 files, act on go-ahead.

---

## 6. Operational policies

### 6.1 Destructive-op gate (`MCP_AUTONOMOUS_GATE_POLICY`)

Autonomous *mode* was removed in v7.8 — gate is policy + `confirm_destructive=true` only (break-glass, single call).

| Policy | Behavior |
|--------|----------|
| `OFF` | No gating. |
| `LIGHT` | **Default.** Blocks only CATASTROPHIC ops unless confirmed: mass deletes >10, table / calc-group / culture / named-expression drops, table / calc-group renames, any rename with `pbip_path`, a `compatibility_level` upgrade (`model set_properties`), a measure/column rename whose dry-run rewrites >= 1 expression, `daxlib_install`, report restore, a model refresh without `tables` or covering >= `model_write.catastrophic_refresh_tables` (default 10) tables (`refresh_type=calculate|indexes` is only DESTRUCTIVE), `inline_measure delete_after`, `move_measure_batch >10`. Ordinary destructive ops pass with advisory `gate_notice`. Read-only ops (list/get/describe/detect/scaffold, rename `preview`) are never gated. |
| `WARN` | All pass; emits `gate_warning`. |
| `STRICT` | All destructive ops block unless confirmed. **Recommended for unsupervised use.** |

**Evidence-aware gate (LIGHT only):** a CATASTROPHIC **delete** (02 table/calc-group delete, 03 mass/table deletes, changeset table+column deletes) auto-downgrades to advisory (`evidence_cleared: true`) only when the handler's OWN in-process evidence (`core/autonomous/evidence.py` → `core.graph.service.get_or_build_graph(scope='both', all_bound_reports=True)` → `get_impact`) proves zero blast radius. A caller-supplied `evidence` arg is IGNORED (`deprecated_args`). Proof requires, for every target: `safe`, `unknown_nodes == []`, `report_side_available`, `graph_scope == 'both'`, `model_etag` == the live model's etag now, not `stale`; and for the graph: `model_side_complete`, no unscanned `other_bound_reports`. Anything missing = not proven → the normal block, with `blast_radius`/`unsafe_nodes`/`evidence_unproven_reasons` on it. **Fails CLOSED** (no connection, no PBIP path, empty graph, no report side, unknown/ambiguous target, relationship deletes). STRICT is never relaxed. Renames and `model/refresh` never auto-clear. `confirm_destructive` must be a real boolean `true` (alias `confirmed`).

### 6.2 Reload + live-edit rules

- Disk-writing PBIP ops return `reload_required: true` + `reload_op_chain: ["17_Desktop_Bridge:reload_and_screenshot"]`. Live-TOM ops need NO reload (instant propagation).
- Standard loop: edit `visual.json`/`page.json` → bridge `reload_and_screenshot` → review → iterate. Desktop stays open.
- If Desktop has unsaved in-memory changes (fresh TOM edits), `save` BEFORE the disk edit — reload re-reads disk and silently discards unsaved edits, and a save AFTER a disk edit overwrites it. The bridge enforces both (reload/save refuse; see GUIDE.md Part III §III.24). Disk TMDL edits: metadata-only → `reload include_model=true` (classified); anything touching partitions / M / data sources / import tables → close without saving + `open`.
- Model changes verify via `04_Run_DAX` / `09_Validate`, never via reload. Never close Desktop unprompted.

---

## 7. Platform & coding rules

- Windows-only runtime (pythonnet, WMI, pywin32). **UTF-8 always:** `open(..., encoding='utf-8')` (charmap errors otherwise, incl. `ast.parse` on source).
- Thread safety: shared state uses `threading.RLock`; double-checked locking for lazy init.
- **Lint, and what is actually enforced** (deep review C-09; measured 2026-10-03, all from the repo root):
  - `black --line-length 100` over `core server src client_gateway hooks tools tests pbi-perf-core` (there is
    no root black config; `python/pyproject.toml [tool.black]` is only read from `python/`). Before the style
    pass 389 of 945 files differed; after it the check is clean and must stay clean.
  - `flake8` reads `.flake8`: max line length 100, ignores only `E203` / `W503` (black style) and `E501`
    (black never reflows strings/comments/docstrings: 1,030 lines stay above 100 columns, tracked with
    `flake8 --extend-ignore= --select=E501 --count`, not gated). Everything else is gated and the count is 0:
    F-codes (the F401/F811/F841/F541 findings were fixed; kept lazy-TOM / re-export imports carry an explicit
    `# noqa`), E402/E731/E741/W605. CI still only gates `--select=E9,F63,F7,F82`.
  - `mypy` reads the root `mypy.ini` (the old `python/pyproject.toml [tool.mypy]` was never read from the
    root and was removed). It is **not** `strict` overall: 11 modules are strict (every `--strict` flag:
    `client_gateway.reminders`, `core.analysis.bpa_fix`, `core.dax.udf_support`, `core.debug.dax_query_builder`,
    `core.debug.live_swap`, `core.graph.cleanup`, `core.operations.culture_translations`,
    `core.operations.write_preview`, `core.utilities.progress_ticker`, `core.validation.test_generator`,
    `core.validation.tieout`); 164 legacy modules are listed with `ignore_errors = True` (baseline 1,133 errors,
    mostly `arg-type` / `attr-defined` / `assignment` / `union-attr`); every other module is checked at the
    default level, so a new module cannot add errors. Current result: 0 errors in 457 source files. When you
    fix a legacy module, delete its section.
- **Every `subprocess` call in server code passes `stdin=subprocess.DEVNULL`** (git also gets
  `GIT_TERMINAL_PROMPT=0` + a `cat` pager): the server's stdin is the MCP protocol pipe, and a child
  that inherits it hung `validate_report level=commit` past its 300 s timeout (R0201 live test,
  2026-10-03) while the same call ran in 6 s outside the server.
- **`x or DEFAULT` silently drops `0`** — use `x if x is not None else DEFAULT` when 0 is valid.
- For JSONL/compact output use stdlib `json.dumps(..., separators=(",", ":"))` (or `dumps_json(indent=0)`). `MCPJsonEncoder` writes NaN/Infinity as `null`.
- Handler category strings must exactly match `ToolCategory` enum values (pinned by `tests/test_review_a_registry.py`).
- When extracting f-string methods to modules, only re-indent the `return` keyword; preserve f-string content exactly.
- Broad `except Exception` in `core/infrastructure/` is intentional (CLR interop stability).

---

## 8. Known gotchas

### PBIR / authoring

- **Two formatting layers in `visual.json`:** `visualContainerObjects` = CHROME (title/background/border); `visual.objects` = INTERNAL rendering (axes, card value font, matrix headers). Setting vCO `title.fontSize` does NOT touch a card's number. Rule: copy `visual.objects` verbatim from a working same-type visual.
- **`cardVisual` font compounding:** `value.fontSize × layout.calloutSize` multiply at render. Conservative: 14D + 8D for 4 cards × 440 px.
- **Role names per visual type vary by report** — check an existing same-type visual before binding; wrong role = silently blank. Canonical roles: `docs/visual-catalog/<type>.md`.
- Unicode in measure names: use `pbir_add_visuals` (ensure_ascii). Raw shell pbir mangles `Δ` → `Î"`.
- pbir CLI page arg needs `.Page` suffix (MCP op auto-adds; 0.9.25 requires `.Report`/`.Page`/`.Visual` suffixes throughout). After `pbir cp` the page/visual GUIDs still need regenerating (the old `~/bin/pbir_fix_copied_page.py` helper is gone) — prefer `11_PBIP_Authoring clone_page`. **`pbir validate` no longer rewrites by default** (installed 0.9.25): the mass `$schema` rewrite moved behind opt-in `--update-schemas`, so plain `pbir validate` is now SAFE report-wide as a read-only check. Treat `--update-schemas` as the destructive flag. Full delta: GUIDE.md Part V, "Changes in 0.9.25".
- Delete visuals via `11_PBIP_Authoring delete_visual` (scrubs `visualInteractions` + bookmark targets, filter-layer guarded); `pbir -q rm` is the fallback — never `shutil.rmtree`. Strip dangling `visualInteractions` after bulk deletes.
- Visual IDs appear inside base64 blobs — scope string-replaces to the target page folder and match JSON-quoted `"old_id"`.
- Container coords: `pbir add visual` positions are page-absolute; subtract container x/y for group-local after `parentGroupName`.
- Audit slicers before deleting content groups (scenario slicers live inside them). `visual.objects` edits need close+reopen, not page switch.

### Debug handler

- DAX result keys use underscored names (`[Total_Performance_PV1]`); PBIP display names differ — match raw name, display_name for headers only.
- Waterfalls stored as `columnChart` (detect via WF1-WF5 measure pattern). `cardVisual` with field params may have 0 measures ("No measure specified").
- Debug handler returns `"success": True` (MCP wrapper adds `ok`). Slicer state from PBIP may be stale (`pbip_warning`). Two TREATAS filters for one slicer table = two separate slicer visuals.

### Misc

- Error-shaped results skip the `reload_required` hint (`skip_reload_hint_on_error`). Tool-count assertion fails fast at bootstrap.
- WSL `/mnt/c/...` paths normalized before PBIP cache keys. Tests use `invalidate_policy_cache()` (autouse fixture in conftest).
- Bulk ops: resolve culture/perspective ONCE up top, `model.SaveChanges()` ONCE at the end — never per item.

---

## 9. Workflow rules (project-scoped)

- Plan mode for any non-trivial task (3+ steps or architectural decisions); if something goes sideways, STOP and re-plan.
- Never mark complete without proving it works (tests/logs/demo). Simplicity first, minimal blast radius, root causes over temp fixes.
- Offload research/exploration to subagents to keep main context clean (one task per subagent).
- Task tracking: plan in `tasks/todo.md`, verify plan, mark complete as you go, results in review section, update `tasks/lessons.md` after corrections.

---

## 10. Pointers

- `docs/GUIDE.md` — **the one guide**: Part I user guide, II client masking, III tool reference (full per-tool op tables + args, former CLAUDE.md §5), IV architecture (module-level deep dive, former §6), V PBIR reference (file format + `pbir` CLI with the 0.9.25 revalidation), VI agentic stack index (A–E)
- `docs/visual-catalog/INDEX.md` (43 per-type files), `docs/design-principles/INDEX.md`; the per-type pages link into Part V by anchor (`#cli-<type>`, `#json-<type>`)

User's global rules + memory live in `~/.claude/CLAUDE.md` and `~/.claude/projects/.../memory/`. General PBI rules belong there; **this file is project-specific truth, kept under ~300 lines — put new exhaustive detail in GUIDE.md Part III / IV, not here.**
