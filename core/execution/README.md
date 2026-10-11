# Execution Module

Small helpers used by the query execution layer.

- **dmv_helper.py** — `get_field_value(row, keys)`: read a DMV / INFO.* result field whether it
  comes back as `Name` or bracketed `[Name]`.

Query execution itself (command gate, result cache, locale-neutral value conversion, INFO.*
metadata, DMV queries, exports) lives in `core/infrastructure/query_executor.py`; the shared
ADOMD plumbing (command gate, `clr_to_py`, command-timeout budget) in
`core/infrastructure/adomd_utils.py`.

The modules this README used to list (`query_cache.py`, `search_helper.py`, `table_mapper.py`,
`tom_fallback.py`) never existed in this tree (B-31, 2026-09-22 review).
