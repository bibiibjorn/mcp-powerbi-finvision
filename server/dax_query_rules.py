"""Text of the ``powerbi://docs/dax-query-rules`` resource (PRM-01).

Our own condensed checklist for writing DAX QUERIES (not measures) that the engine accepts the
first time, for clients that have none of our skills. Written from public DAX behaviour; it is
deliberately not derived from any vendor resource text. Keep it around 40 lines: it is read into
an LLM context on demand.
"""

DAX_QUERY_RULES = """# DAX query rules (checklist for EVALUATE queries)

Validate a draft with `04_Run_DAX` mode=validate before running it (parse/bind only, no execution).

## Shape
1. A query that returns several rows needs an ORDER BY, otherwise the row order is undefined.
2. Use ONE DEFINE block; put every MEASURE, VAR, COLUMN, TABLE and FUNCTION definition in it,
   then the EVALUATE statement(s).
3. Define a measure with its table (`DEFINE MEASURE 'Sales'[Margin] = ...`), but refer to any
   measure without a table (`[Margin]`). Columns are always written with their table.
4. Renaming with SELECTCOLUMNS / ADDCOLUMNS changes the column names: ORDER BY must then use the
   NEW names.

## Choosing the grouping function
5. Default to SUMMARIZECOLUMNS for "measure by dimension" output. It takes no boolean filter
   arguments (use TREATAS or a filter table), and it needs at least one column or measure
   expression that is not just a filter.
6. Never compute new values inside SUMMARIZE (SUMMARIZE with name/expression pairs is
   unreliable); group with SUMMARIZE, then add values with ADDCOLUMNS.
7. GROUPBY works over a table value or variable, and CURRENTGROUP() is valid only inside its
   aggregation arguments.

## Filters
8. A boolean filter argument of CALCULATE / CALCULATETABLE may reference one table only and may
   not contain a measure. Compute the measure into a VAR first and compare against the VAR, or
   use FILTER over the table instead.
9. IN needs a table on its right side; give it a table variable or a `{ ... }` constructor.
   Never store a boolean expression in a VAR and pass it as a filter.

## Variables
10. You cannot write `_MyTable[Column]` for a table variable. Read a column from it through
    a table function: `SELECTCOLUMNS(_MyTable, "x", [Column])`, `SUMMARIZE(_MyTable, [Column])`
    or `VALUES` / `MAXX` over the variable.

## Time intelligence
11. DATESINPERIOD's offset must match the window it describes: a 12-month window ending at
    the anchor is `-12`, MONTH, not `-11`.
12. A bare ROW(...) has no filter context, so time-intelligence functions have no date to work
    from. Give it an explicit anchor: wrap it in CALCULATETABLE with
    `TREATAS({ MAX('Fact'[Date]) }, 'Calendar'[Date])`. Take "the last date" from the fact table,
    not from the calendar table (which extends into the future).

## Before you run it
13. Only names that exist: list tables/columns/measures first (`02_Model_Operations` list).
14. Add TOPN or a filter to exploratory queries; large results are truncated by `top_n`.
"""
