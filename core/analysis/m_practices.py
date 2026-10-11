"""Power Query (M) best-practice checks M001–M012.

2026-09-22 review:

- I-11: table M lives in **partition sources**, not only in shared expressions.
  ``scan_m_practices`` now scans every M partition plus ``model.expressions``
  from the TMSL the BPA run already has (one serialisation for both), falling
  back to ``query_executor.get_tmsl_definition()`` and finally to the TOM
  expression enumeration. The old primary path (``EVALUATE … $SYSTEM.TMSCHEMA_
  EXPRESSIONS`` — not valid DAX) is gone. The result carries ``total_issues``
  (the key ``comprehensive_analysis`` sums) as well as ``count``.
- I-22: rules match against *code* (comments and string contents blanked by a
  small M lexer), paths/secrets against the *string literals*: an https URL is
  not an absolute path, ``// Table.Buffer(`` is not a call, a column named
  ``"password"`` is not a credential, and M008 only looks inside the
  ``Table.AddColumn(...)`` argument span.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Volatile / non-foldable M functions — their presence prevents query folding.
_VOLATILE_FUNCS = (
    "datetime.localnow(",
    "datetime.now(",
    "datetimezone.localnow(",
    "datetimezone.utcnow(",
    "number.random(",
    "number.randombetween(",
)

# Tokens that indicate a row-set merge/expand step.
_MERGE_TOKENS = ("table.nestedjoin(", "table.join(", "table.combine(", "table.expandtablecolumn(")

# Credential-looking keys: a record field ``Password = "..."`` in code, or a
# ``pwd=`` / ``password=`` pair inside a connection-string literal.
_SECRET_KEYS = (
    "password",
    "pwd",
    "apikey",
    "api_key",
    "accountkey",
    "secret",
    "clientsecret",
    "sharedaccesskey",
)
_SECRET_FIELD_RE = re.compile(
    r"(?<![\w.#])(" + "|".join(_SECRET_KEYS) + r')\s*=\s*"', re.IGNORECASE
)
_SECRET_IN_STRING_RE = re.compile(
    r"(?:^|[;\s&?])(" + "|".join(_SECRET_KEYS) + r")\s*=\s*[^;\s&]+", re.IGNORECASE
)

# Absolute local / UNC path inside a string literal.
_ABS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\)")


# ─────────────────────────────── M lexer ───────────────────────────────


def _scan_m(text: str) -> Iterator[Tuple[str, int, int]]:
    """Yield ``(kind, start, end)`` spans: ``comment`` and ``string`` (content only)."""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            yield "comment", i, j
            i = j
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            yield "comment", i, j
            i = j
            continue
        if ch == '"' or (ch == "#" and i + 1 < n and text[i + 1] == '"'):
            start = i + (2 if ch == "#" else 1)
            j = start
            while j < n:
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            kind = "identifier" if ch == "#" else "string"
            yield kind, start, j
            i = j + 1
            continue
        i += 1


def _blank(text: str, spans: List[Tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for k in range(start, min(end, len(chars))):
            if chars[k] not in "\r\n":
                chars[k] = " "
    return "".join(chars)


def strip_m_comments(text: str) -> str:
    """Blank ``//`` / ``/* */`` comments outside string literals (length preserved)."""
    if not text:
        return text or ""
    return _blank(text, [(s, e) for k, s, e in _scan_m(text) if k == "comment"])


def m_code_and_strings(text: str) -> Tuple[str, List[str]]:
    """``(code, strings)``: code with comments + literal contents blanked, and the
    decoded string literals (``""`` -> ``"``)."""
    if not text:
        return "", []
    spans = list(_scan_m(text))
    code = _blank(text, [(s, e) for k, s, e in spans if k in ("comment", "string", "identifier")])
    strings = [text[s:e].replace('""', '"') for k, s, e in spans if k == "string"]
    return code, strings


def _call_span(code_low: str, fn: str, start: int = 0) -> Optional[Tuple[int, int]]:
    """``(open, close)`` of the argument list of the first ``fn(`` at/after *start*."""
    pos = code_low.find(fn, start)
    if pos == -1:
        return None
    open_idx = pos + len(fn) - 1
    depth = 0
    for j in range(open_idx, len(code_low)):
        c = code_low[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return open_idx, j
    return open_idx, len(code_low)


# ─────────────────────────────── rules ─────────────────────────────────


def detect_m_issues(name: str, expr: str) -> List[Dict[str, Any]]:
    """Return best-practice issues for a single M expression.

    Pure: no I/O, so each rule is unit-testable. Caller passes the expression
    name (for reporting) and its raw M text.
    """
    issues: List[Dict[str, Any]] = []
    code, strings = m_code_and_strings(expr or "")
    low = code.lower()
    compact = re.sub(r"\s+", "", low)

    def add(rule: str, severity: str, description: str) -> None:
        issues.append(
            {"rule": rule, "severity": severity, "name": name, "description": description}
        )

    abs_paths = [s for s in strings if _ABS_PATH_RE.match(s.strip())]

    # ── M001-M004 ────────────────────────────────────────────────────────────
    if "table.buffer(" in compact:
        add("M001", "warning", "Table.Buffer used; can cause high memory usage if misapplied.")
    if "web.contents(" in compact and "relativepath" not in compact and "query=" not in compact:
        add(
            "M002",
            "info",
            "Web.Contents without RelativePath/Query options may be less cache-friendly.",
        )
    if "file.contents(" in compact and abs_paths:
        add(
            "M003",
            "warning",
            "File.Contents with absolute path detected; consider parameterizing path.",
        )

    # ── M005 volatile / non-foldable functions ──────────────────────────────
    if any(fn in compact for fn in _VOLATILE_FUNCS):
        add(
            "M005",
            "warning",
            "Volatile function (DateTime.LocalNow/Now/Number.Random) breaks query "
            "folding; move to a parameter or compute server-side where possible.",
        )

    # ── M006 a filter applied AFTER a merge/expand (should filter first) ────
    merges = [compact.find(tok) for tok in _MERGE_TOKENS if tok in compact]
    if merges:
        first_merge = min(merges)
        if compact.find("table.selectrows(", first_merge) != -1:
            add(
                "M006",
                "warning",
                "Table.SelectRows runs after a merge/expand; filter before merging "
                "to reduce rows joined and preserve folding.",
            )

    # ── M007 hardcoded secrets ──────────────────────────────────────────────
    if _SECRET_FIELD_RE.search(code) or any(_SECRET_IN_STRING_RE.search(s) for s in strings):
        add(
            "M007",
            "warning",
            "Possible hardcoded credential/secret in the query; use a data-source "
            "credential or parameter instead.",
        )

    # ── M008 per-row lookup INSIDE Table.AddColumn's argument span ──────────
    start = 0
    while True:
        span = _call_span(compact, "table.addcolumn(", start)
        if span is None:
            break
        if "table.selectrows(" in compact[span[0] : span[1]]:
            add(
                "M008",
                "warning",
                "Per-row Table.SelectRows inside Table.AddColumn is an O(n*m) lookup; "
                "use Table.NestedJoin/merge instead.",
            )
            break
        start = span[0] + 1

    # ── M009 Table.Sort breaks folding ──────────────────────────────────────
    if "table.sort(" in compact:
        add(
            "M009",
            "info",
            "Table.Sort can interrupt query folding on some sources (on SQL sources it often "
            "folds to ORDER BY): verify with View Native Query, and drop it when the model "
            "does not need a sorted load.",
        )

    # ── M010 Csv/Excel from an absolute local/UNC path ──────────────────────
    if ("csv.document(" in compact or "excel.workbook(" in compact) and abs_paths:
        add(
            "M010",
            "warning",
            "Csv.Document/Excel.Workbook bound to an absolute path; parameterize the "
            "path so the query is portable.",
        )

    # ── M011 Value.NativeQuery without an explicit folding hint ──────────────
    if "value.nativequery(" in compact and "enablefolding=true" not in compact:
        add(
            "M011",
            "info",
            "Value.NativeQuery without EnableFolding=true blocks downstream folding; "
            "pass [EnableFolding=true] when the source supports it.",
        )

    # ── M012 Table.Combine (appended-query sprawl) ──────────────────────────
    if compact.count("table.combine(") >= 2:
        add(
            "M012",
            "info",
            "Table.Combine appends queries in-engine; confirm the sources can't be "
            "unioned at the data source for better folding.",
        )

    return issues


# ─────────────────────────────── sources ───────────────────────────────


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value)


def iter_m_sources(model: Dict[str, Any]) -> Iterator[Dict[str, str]]:
    """Every M text in a TMSL model: shared expressions and M partition sources."""
    for e in model.get("expressions") or []:
        if not isinstance(e, dict):
            continue
        if str(e.get("kind") or "m").lower() != "m":
            continue
        yield {
            "name": _text(e.get("name")) or "<expression>",
            "kind": "expression",
            "expression": _text(e.get("expression")),
        }
    for t in model.get("tables") or []:
        if not isinstance(t, dict):
            continue
        for p in t.get("partitions") or []:
            if not isinstance(p, dict):
                continue
            src = p.get("source")
            if not isinstance(src, dict) or str(src.get("type") or "").lower() != "m":
                continue
            yield {
                "name": f"{_text(t.get('name'))} / {_text(p.get('name'))}",
                "kind": "partition",
                "table": _text(t.get("name")),
                "expression": _text(src.get("expression")),
            }


def _resolve_model(tmsl: Any) -> Optional[Dict[str, Any]]:
    from core.analysis.bpa_model import resolve_tmsl_model

    return resolve_tmsl_model(tmsl)


def scan_m_sources(
    sources: List[Dict[str, str]], issues_max: Optional[int] = None
) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    for src in sources:
        for issue in detect_m_issues(src["name"], src.get("expression") or ""):
            issue["source_kind"] = src.get("kind", "expression")
            if src.get("table"):
                issue["table"] = src["table"]
            issues.append(issue)
    total = len(issues)
    truncated = isinstance(issues_max, int) and issues_max > 0 and total > issues_max
    if truncated:
        issues = issues[:issues_max]
    return {
        "success": True,
        "count": total,
        "total_issues": total,
        "issues": issues,
        "sources_scanned": len(sources),
        "partitions_scanned": sum(1 for s in sources if s.get("kind") == "partition"),
        "expressions_scanned": sum(1 for s in sources if s.get("kind") == "expression"),
        **({"truncated": True} if truncated else {}),
    }


def scan_m_practices(
    query_executor: Any,
    dmv_cap: int = 1000,
    issues_max: Optional[int] = None,
    *,
    tmsl: Any = None,
) -> Dict[str, Any]:
    """Scan every M query of the model (partitions + shared expressions).

    Pass the already-fetched TMSL as *tmsl* to avoid a second serialisation;
    otherwise ``query_executor.get_tmsl_definition()`` is used, and as a last
    resort the TOM enumeration of shared expressions (partitions are then NOT
    covered and the result says so).
    """
    try:
        model = _resolve_model(tmsl) if tmsl is not None else None
        source = "tmsl"
        if (
            model is None
            and query_executor is not None
            and hasattr(query_executor, "get_tmsl_definition")
        ):
            res = query_executor.get_tmsl_definition()
            if isinstance(res, dict) and res.get("success"):
                model = _resolve_model(res.get("tmsl"))
        if model is not None:
            result = scan_m_sources(list(iter_m_sources(model))[: max(dmv_cap, 1) * 10], issues_max)
            result["source"] = source
            return result

        if query_executor is None or not hasattr(query_executor, "enumerate_m_expressions_tom"):
            return {
                "success": False,
                "error": "No TMSL and no TOM enumeration available for M scan",
            }
        data = query_executor.enumerate_m_expressions_tom(dmv_cap)
        if not isinstance(data, dict) or not data.get("success"):
            return (
                data
                if isinstance(data, dict)
                else {"success": False, "error": "M enumeration failed"}
            )
        sources = [
            {
                "name": row.get("Name") or "<unknown>",
                "kind": "expression",
                "expression": _text(row.get("Expression")),
            }
            for row in data.get("rows", [])
            if str(row.get("Kind", "M")).upper() == "M"
        ]
        result = scan_m_sources(sources, issues_max)
        result["source"] = "tom_expressions"
        result["note"] = "TMSL unavailable: only shared expressions were scanned, not partition M."
        return result
    except Exception as e:  # noqa: BLE001 — analysis must degrade, not crash
        return {"success": False, "error": str(e)}
