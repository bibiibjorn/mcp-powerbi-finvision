"""Reconstruction self-check: diff our visual query against Desktop's own (F-39(1)(2)).

Performance Analyzer's export carries the exact DAX query Desktop sent for a
visual. Comparing its SHAPE with the query ``VisualQueryBuilder`` reconstructed
from the PBIR files shows exactly where the reconstruction diverges: a missing
group-by column, a filter on a column we never emitted, a different value list
in a TREATAS, a NOT the reconstruction lacks.

Lexer-driven (strings, comments and quoted names are handled); no model access.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set

from core.dax.dax_names import column_ref, split_qualified


def _tokens(dax: str):
    from core.dax.dax_reference_parser import get_lexer

    lexer = get_lexer()
    return lexer, lexer.tokenize_code(dax or "")


def _qref(tok) -> Optional[str]:
    table, col = split_qualified(tok.value)
    return column_ref(table, col) if table and col else None


def query_shape(dax: str) -> Dict[str, Any]:
    """Group-by columns, filter columns, TREATAS value lists and fields of a query."""
    from core.dax.tokenizer.tokens import TokenType

    lexer, tokens = _tokens(dax)
    group_by: List[str] = []
    fields: Set[str] = set()
    treatas: Dict[str, Set[str]] = {}
    filter_columns: Set[str] = set()
    negated_columns: Set[str] = set()

    for tok in tokens:
        if tok.type == TokenType.QUALIFIED_REF:
            ref = _qref(tok)
            if ref:
                fields.add(ref)

    for i, tok in enumerate(tokens):
        if tok.type != TokenType.FUNCTION:
            continue
        name = tok.value.upper()
        if name == "SUMMARIZECOLUMNS":
            previous: list = []
            for arg in lexer.extract_function_args(tokens, i):
                after_name = len(previous) == 1 and previous[0].type == TokenType.STRING
                previous = arg
                if after_name:
                    continue  # the expression of a "name", <expr> pair (a measure)
                if len(arg) == 1 and arg[0].type == TokenType.QUALIFIED_REF:
                    ref = _qref(arg[0])
                    if ref and ref not in group_by:
                        group_by.append(ref)
                elif arg and arg[0].value.upper() in ("ROLLUPADDISSUBTOTAL", "ROLLUPGROUP"):
                    for t in arg:
                        if t.type == TokenType.QUALIFIED_REF:
                            ref = _qref(t)
                            if ref and ref not in group_by:
                                group_by.append(ref)
        elif name == "TREATAS":
            args = lexer.extract_function_args(tokens, i)
            if len(args) < 2:
                continue
            values = {t.value for t in args[0] if t.type in (TokenType.STRING, TokenType.NUMBER)}
            values |= {
                t.value.upper()
                for t in args[0]
                if t.type in (TokenType.KEYWORD, TokenType.FUNCTION)
                and t.value.upper() in ("TRUE", "FALSE", "BLANK")
            }
            for col_arg in args[1:]:
                if col_arg and col_arg[0].type == TokenType.QUALIFIED_REF:
                    ref = _qref(col_arg[0])
                    if ref:
                        treatas.setdefault(ref, set()).update(values)
                        filter_columns.add(ref)
        elif name in ("FILTER", "KEEPFILTERS", "VALUES"):
            args = lexer.extract_function_args(tokens, i)
            for arg in args:
                for t in arg:
                    if t.type == TokenType.QUALIFIED_REF:
                        ref = _qref(t)
                        if ref:
                            filter_columns.add(ref)
        elif name == "NOT":
            args = lexer.extract_function_args(tokens, i)
            for arg in args:
                for t in arg:
                    if t.type == TokenType.QUALIFIED_REF:
                        ref = _qref(t)
                        if ref:
                            negated_columns.add(ref)
    return {
        "group_by": group_by,
        "filter_columns": sorted(filter_columns),
        "treatas_values": {k: sorted(v) for k, v in sorted(treatas.items())},
        "negated_columns": sorted(negated_columns),
        "fields": sorted(fields),
    }


def diff_queries(reconstructed: str, reference: str) -> Dict[str, Any]:
    """Where the reconstruction diverges from the reference (Desktop) query."""
    ours = query_shape(reconstructed)
    theirs = query_shape(reference)
    out: Dict[str, Any] = {
        "group_by": _set_diff(ours["group_by"], theirs["group_by"]),
        "filter_columns": _set_diff(ours["filter_columns"], theirs["filter_columns"]),
        "negated_columns": _set_diff(ours["negated_columns"], theirs["negated_columns"]),
        "fields": _set_diff(ours["fields"], theirs["fields"]),
    }
    value_diffs: Dict[str, Any] = {}
    for col in sorted(set(ours["treatas_values"]) | set(theirs["treatas_values"])):
        a = set(ours["treatas_values"].get(col, []))
        b = set(theirs["treatas_values"].get(col, []))
        if a != b:
            value_diffs[col] = {
                "only_in_reconstruction": sorted(a - b),
                "only_in_reference": sorted(b - a),
            }
    out["treatas_values"] = value_diffs
    out["matches"] = not value_diffs and all(
        not d["only_in_reconstruction"] and not d["only_in_reference"]
        for k, d in out.items()
        if isinstance(d, dict) and "only_in_reference" in d
    )
    return out


def _set_diff(ours: List[str], theirs: List[str]) -> Dict[str, List[str]]:
    a, b = set(ours), set(theirs)
    return {"only_in_reconstruction": sorted(a - b), "only_in_reference": sorted(b - a)}


def reference_query_from_export(path: str, visual_id: str) -> Optional[str]:
    """The DAX Desktop sent for *visual_id*, read from a Performance Analyzer export."""
    from core.debug.debug_operations import normalise_performance_export
    from pbi_perf_core.perf_analyzer import parse_performance_export

    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    for timing in parse_performance_export(normalise_performance_export(data)):
        if str(timing.visual_id) == str(visual_id) and timing.query_text:
            return timing.query_text
    return None
