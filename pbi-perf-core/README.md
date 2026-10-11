# pbi-perf-core

DAX Studio / Performance Analyzer parity, as one pure-Python package shared by
both Power BI MCP servers — the local Desktop server (`MCP-PowerBi-Finvision`)
and the online Fabric server (`MCP-Fabric-Python Based`).

One implementation of the maths means a local model and an online model report
the **same numbers for the same events**, and both can be checked against the
tool everyone already trusts.

## Why this exists

Power BI's performance numbers are not sums. The three that matter are all
derived, and each is easy to get subtly wrong:

| Number | How it is actually produced |
|---|---|
| **Formula engine time** | A sweep line over the query window: the time during which *no* storage-engine scan was running. Not `total − Σ scans`. |
| **Storage engine time** | A reconciliation against the formula engine, then netted for overlap — concurrent scans share wall clock and are counted once. |
| **"Other" (Performance Analyzer)** | Never serialised anywhere. It is the residual `lifecycle − DAX − DirectQuery − render − parameters`, and it is mostly *queue wait*. |

On the reference capture, naively summing the event durations yields 81 ms and
64 ms CPU where DAX Studio reports **100 ms and 32 ms**. Three engine rules
account for the gap, and all three are encoded here:

1. `VertiPaqScanInternal` rows restate the CPU of the scan they accompany —
   counting them adds phantom time and doubles CPU.
2. `BatchVertiPaqScan` rows span all their member scans on the clock, while
   their `Duration` reports only the batch's own overhead.
3. Overlapping scans contribute only the wall clock not already covered.

## Modules

| Module | Responsibility |
|---|---|
| `server_timings` | `ServerTimesModel.ProcessResults` parity: FE/SE split, batch and internal handling, cache matches, callbacks, timeline |
| `query_plan` | Physical/logical plan parsing, cardinality inheritance, operator census, findings, structural diff |
| `xmsql` | Storage-engine query text cleanup: internal ids → real names, alias/lineage stripping |
| `execution_metrics` | Event 136: peak memory, FE/SE CPU split, queue wait (`executionDelayMs`) |
| `vpa` | VertiPaq Analyzer: sizes, cardinality, encoding, relationships, findings |
| `perf_analyzer` | Performance Analyzer semantics incl. the "Other" residual, plus a K-slot queue simulator |
| `daxstudio_export` | `ServerTimings.json` (v6), `QueryPlan.json` (v3), `.daxx` writers |

## Usage

```python
from pbi_perf_core import process_results, parse_plan, analyse_plan
from pbi_perf_core.xmsql import XmSqlCleaner

cleaner = XmSqlCleaner(column_map, table_map)      # built once per session
timings = process_results(events, xmsql_cleaner=cleaner)

print(timings.headline())
# {'total_ms': 1428, 'fe_ms': 1328, 'se_ms': 100, 'se_queries': 189, ...}

physical = parse_plan(plan_text, "physical")
print(analyse_plan(physical, result_rows=8, se_query_count=189)["findings"])
```

The input contract is a list of dicts (or `TraceEvent`s) with keys
`event_class, event_subclass, text_data, start_time, end_time, current_time,
duration_ms, cpu_time_ms, session_id, activity_id, request_id, object_name,
application_name`. Both `snake_case` and the raw AMO `PascalCase` names are
accepted, so exported captures replay without translation.

## Tests

```bash
python -m pytest tests -q
```

The suite is built on a **real DAX Studio export** (`tests/fixtures/`), taken
2026-09-02 from the R0201 P&L Summary matrix against the S02 model: 370 trace
events and 19,963 plan rows. Because DAX Studio already computed the correct
answer from those exact events, the tests reproduce its headline numbers from
its own inputs — Total 1428, FE 1328 (93%), SE 100, SE CPU 32, 189 SE queries,
110 cache hits, 27 batch children, 70 internal rows excluded.

That is a genuine oracle rather than a self-consistent restatement: the numbers
are unreachable unless the batch, internal-scan and overlap rules are all
correct.

## Installation

```bash
<venv>/Scripts/python -m pip install -e path/to/pbi-perf-core
```

Install into both MCP server virtual environments so they share one
implementation. No runtime dependencies.
