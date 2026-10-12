"""Learn the cells of data tables that arrive in a masked request (models the gateway never read).

The dictionary holds the values of the Power BI models open in Desktop. Data can also come from
somewhere else: a published model over XMLA, a Fabric warehouse or lakehouse query, a closed
PBIP folder, a table the user pastes from Excel. Its text cells (profit centres, GL account names,
cost centres, codes) are client data the gateway has not seen, and the name recognizer does not
catch them: they are not names of people or companies.

So, per masked request, before anything is masked, the NEW content (the last message) is scanned
for data tables:

* tool results of data tools: JSON row sets (a list of objects), in the result text of a tool
  whose name is a query / read-data tool (``DATA_TOOLS``). From a DAX result only the cells of
  ``Table[Column]`` keys are taken (a ``[Measure]`` key holds formatted numbers or SVG); from a
  SQL or grid result every key; a matrix read (``header`` / ``cells``) gives its row headers;
* a pasted table in the user's own text: tab-separated lines (an Excel or Power BI copy) or a
  Markdown table (header row left out).

Every text cell becomes a generic dictionary value (``DIM``), exactly as if the gateway had read
that column from the model: the same matching rules apply (report vocabulary such as "Other
income" stays readable as a phrase, codes and names are masked everywhere), and the walk that
follows masks the table and every later mention. Numbers, dates, periods, booleans, very short
or long cells and code-shaped cells (DAX, paths, object names) are left out. A query over model
metadata (``INFO.``, ``$SYSTEM``, ``TMSCHEMA``) is skipped: measure and table names are not
client data. Only counts leave this module.
"""

from __future__ import annotations

import json
import re
from typing import Any

from client_gateway.reminders import recognition_texts

DATA_TOOLS = re.compile(
    r"(?:run_dax|04_run_dax|09_debug_operations|run_query|query_semantic_model|read_table|"
    r"read_matrix|dax_query|dax_batch|show_as_table|data_validation|run_kql|livy_session|"
    r"sample_data|graphql)",
    re.IGNORECASE,
)
METADATA_QUERY = re.compile(r"\bINFO\.|\$SYSTEM|TMSCHEMA|DISCOVER_|\.show\s", re.IGNORECASE)
_TABLE_COLUMN = re.compile(r"^'?[^\[\]']+'?\[[^\[\]]+\]$")
_NUMBERISH = re.compile(r"^[\s€$£%+\-−–(),.'’0-9eEkKmMbBnN/:x×]*$")
_DATE = re.compile(
    r"^(?:\d{4}-\d{2}(?:-\d{2})?(?:[T ][\d:.]+Z?)?|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})$"
)
_MONTHS = (
    "jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july "
    "august september october november december januari februari maart mei juni juli augustus "
    "oktober okt mrt janvier février fevrier mars avril mai juin juillet août aout septembre "
    "octobre novembre décembre decembre"
).split()
_PERIOD = re.compile(
    r"^(?:(?:" + "|".join(sorted(set(_MONTHS), key=len, reverse=True)) + r")\.?[\s\-/']*"
    r"(?:\d{2}|\d{4})?|(?:q[1-4]|h[12]|s[12]|p\d{1,2}|w\d{1,2}|fy|ytd|qtd|mtd)[\s\-/']*\d{0,4}"
    r"|\d{4}[\s\-/]*(?:q[1-4]|h[12]|m?\d{1,2})?|(?:fy|cy)\s?\d{2,4})$",
    re.IGNORECASE,
)
_CODEISH = re.compile(r"[\[\]{}<>=|\\@#;]|^(?:https?:|www\.|[A-Za-z]:\\)|\(\s*'|'\[|/\S+/")
_BOOL = frozenset({"true", "false", "yes", "no", "ja", "nee", "null", "none", "blank", "n/a"})
MIN_CELL = 3
MAX_CELL = 80
MAX_CELLS = 20_000  # per request: a bigger table is still learned, up to this many cells
MAX_RESULT_CHARS = 2_000_000


def keep_cell(value: Any) -> str | None:
    """The cell text when it may be client data, else None."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not (MIN_CELL <= len(s) <= MAX_CELL) or "\n" in s:
        return None
    low = s.casefold()
    numeric = _NUMBERISH.match(s) is not None and any(c.isdigit() for c in s)
    if (
        low in _BOOL
        or numeric
        or _DATE.match(s)
        or _PERIOD.match(s)
        or _CODEISH.search(s)
        or not any(c.isalpha() for c in s)
    ):
        return None
    return s


def _row_sets(obj: Any, depth: int = 0) -> list[list[dict[str, Any]]]:
    """Every list of objects in a parsed JSON result (a row set), however deep it is nested."""
    if depth > 8:
        return []
    found: list[list[dict[str, Any]]] = []
    if isinstance(obj, list):
        if obj and all(isinstance(r, dict) for r in obj):
            found.append(obj)
        for item in obj:
            found.extend(_row_sets(item, depth + 1))
    elif isinstance(obj, dict):
        for value in obj.values():
            found.extend(_row_sets(value, depth + 1))
    return found


def cells_from_result(text: str) -> list[str]:
    """Text cells of the data tables in one tool result text (JSON); [] when it is not JSON."""
    if len(text) > MAX_RESULT_CHARS:
        return []
    start = text.find("{")
    first = text.find("[")
    if first != -1 and (start == -1 or first < start):
        start = first
    if start == -1:
        return []
    try:
        tree = json.loads(text[start:])
    except ValueError:
        return []
    out: list[str] = []
    for rows in _row_sets(tree):
        keys = {k for r in rows for k in r if isinstance(k, str)}
        dax = any(_TABLE_COLUMN.match(k) or k.startswith("[") for k in keys)
        for row in rows:
            for key, value in row.items():
                if not isinstance(key, str):
                    continue
                if dax and not _TABLE_COLUMN.match(key):
                    continue  # a [Measure] column, or a field the DAX engine did not name
                if key in ("cells", "columns"):
                    continue
                if isinstance(value, list):  # a grid row as a list of cell strings
                    out.extend(c for c in (keep_cell(v) for v in value) if c)
                    continue
                cell = keep_cell(value)
                if cell:
                    out.append(cell)
    return out


_MD_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")


def cells_from_paste(text: str) -> list[str]:
    """Text cells of tab-separated blocks and Markdown tables in a user's message."""
    out: list[str] = []
    lines = text.splitlines()
    tsv = [ln for ln in lines if ln.count("\t") >= 1]
    if len(tsv) >= 2:
        rows = [ln.split("\t") for ln in tsv]
        body = rows
        if all(keep_cell(c) or not c.strip() for c in rows[0]) and any(
            _NUMBERISH.match(c.strip()) and c.strip() for r in rows[1:] for c in r
        ):
            body = rows[1:]  # a header row (labels over numbers): column titles, not data
        out.extend(c for r in body for c in (keep_cell(v) for v in r) if c)
    for i, line in enumerate(lines):
        if _MD_SEP.match(line) and i > 0 and "|" in lines[i - 1]:
            j = i + 1
            while j < len(lines) and "|" in lines[j]:
                out.extend(
                    c for c in (keep_cell(v) for v in lines[j].strip().strip("|").split("|")) if c
                )
                j += 1
    return out


def _texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
    return []


def new_table_cells(tree: Any) -> list[str]:
    """The text cells of every data table in the NEW content of a request (the last message):
    data-tool results (their tool name and query are read from the message before it) and
    tables the user pasted."""
    messages = tree.get("messages") if isinstance(tree, dict) else None
    if not isinstance(messages, list) or not messages:
        return []
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        return []
    tools: dict[str, tuple[str, str]] = {}
    if len(messages) >= 2 and isinstance(messages[-2], dict):
        prev = messages[-2].get("content")
        for block in prev if isinstance(prev, list) else []:
            if isinstance(block, dict) and block.get("type") in (
                "tool_use",
                "server_tool_use",
                "mcp_tool_use",
            ):
                name = str(block.get("name") or "")
                try:
                    query = json.dumps(block.get("input") or {}, ensure_ascii=False)
                except (TypeError, ValueError):
                    query = ""
                tools[str(block.get("id") or "")] = (name, query)
    out: list[str] = []
    content = last.get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            name, query = tools.get(str(block.get("tool_use_id") or ""), ("", ""))
            if not DATA_TOOLS.search(name) or METADATA_QUERY.search(query):
                continue
            for text in _texts(block.get("content")):
                out.extend(cells_from_result(text))
        elif block.get("type") == "text" and isinstance(block.get("text"), str):
            for piece in recognition_texts(block["text"]):  # an attached file's table counts
                out.extend(cells_from_paste(piece))
        if len(out) >= MAX_CELLS:
            break
    return list(dict.fromkeys(out))[:MAX_CELLS]
