"""
VAR-level debugging + run_dax handlers.

Extracted from debug_handler.py as part of P2-183 split.

Public entry points (re-exported via debug_handler.py):

- _handle_debug_variable  — dispatcher for operation=debug_variable
- _handle_step_variables — dispatcher for operation=step_variables
- _handle_run_dax         — dispatcher for operation=run_dax
- _extract_variables_full, _classify_var_type, _build_var_chain_query,
  _trace_measures_individually — helpers (kept for test/extension access)

F-13: VARs are parsed with the DAX lexer (strings, comments and nested
``VAR ... RETURN`` blocks are handled), and each step is evaluated as ONE
``VAR ... RETURN <target>`` block INSIDE the filter context
(``CALCULATE`` / ``CALCULATETABLE``) -- a query-scope ``DEFINE VAR`` is evaluated
once in an empty context and cannot be re-filtered, so the old
``CALCULATE(_var, filters)`` silently returned unfiltered totals.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from server.handlers._debug.helpers import cache_provenance
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

_NOT_CONNECTED = "Not connected to Power BI model. Use 01_Connection operation=connect first."


def _parse_var_block(dax: str) -> Dict[str, Any]:
    """Lexer-driven parse of a measure's TOP-LEVEL ``VAR ... RETURN`` chain.

    Returns ``{"variables": [{name, expression, start, end}], "return_expression",
    "nested_blocks"}``. Only VARs at parenthesis depth 0 that belong to the
    outermost chain are variables; a ``VAR ... RETURN`` nested inside a VAR's
    expression (``VAR a = VAR b = 1 RETURN b + 1``) stays part of that
    expression, and VAR/RETURN inside strings or comments are never keywords.
    """
    from core.dax.dax_reference_parser import get_lexer
    from core.dax.tokenizer.tokens import TokenType

    tokens = get_lexer().tokenize_code(dax or "")
    variables: List[Dict[str, Any]] = []
    return_expression: Optional[str] = None
    nested_blocks = 0

    depth = 0
    current: Optional[Dict[str, Any]] = None  # var being read
    pending = 0  # nested VAR blocks opened inside the current expression
    i = 0
    n = len(tokens)

    def _close(end_pos: int) -> None:
        nonlocal current
        if current is not None:
            current["end"] = end_pos
            current["expression"] = dax[current["start"] : end_pos].strip()
            variables.append(current)
            current = None

    while i < n:
        tok = tokens[i]
        if tok.type in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
            depth += 1
        elif tok.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            depth = max(0, depth - 1)
        elif tok.type == TokenType.KEYWORD and depth == 0:
            word = tok.value.upper()
            if word == "VAR":
                if current is not None and pending > 0:
                    # Inside a nested block: a VAR after an operator opens a deeper
                    # block; after a complete operand it is a sibling VAR of that block.
                    if _starts_nested(tokens, i, current):
                        pending += 1
                        nested_blocks += 1
                elif current is not None and pending == 0 and _starts_nested(tokens, i, current):
                    pending += 1
                    nested_blocks += 1
                else:
                    _close(tok.start)
                    # VAR <name> = <expr>
                    if (
                        i + 2 < n
                        and tokens[i + 1].type in (TokenType.IDENTIFIER, TokenType.KEYWORD)
                        and tokens[i + 2].value == "="
                    ):
                        current = {
                            "name": tokens[i + 1].value,
                            "start": tokens[i + 2].end,
                            "end": None,
                            "expression": "",
                        }
                        i += 3
                        continue
            elif word == "RETURN":
                if current is not None and pending > 0:
                    pending -= 1
                else:
                    _close(tok.start)
                    return_expression = dax[tok.end :].strip()
                    break
        i += 1
    _close(len(dax or ""))
    return {
        "variables": variables,
        "return_expression": return_expression,
        "nested_blocks": nested_blocks,
    }


def _starts_nested(tokens, i: int, current: Dict[str, Any]) -> bool:
    """Does the VAR at ``tokens[i]`` open a nested block inside the current expression?

    It does when it follows an operator (``=`` of the current VAR, ``+``, ``&&``
    ...): a VAR there starts an operand. A VAR after a complete operand
    (name, number, ``)``...) starts the next top-level variable.
    """
    from core.dax.tokenizer.tokens import TokenType

    prev = tokens[i - 1] if i > 0 else None
    return prev is not None and prev.type == TokenType.OPERATOR


def _extract_variables_full(dax: str) -> List[Dict[str, str]]:
    """Extract top-level VAR definitions with full (untruncated) expressions.

    Returns an ordered list of ``{"name", "expression"}`` dicts, preserving
    declaration order for dependency chaining (lexer-driven, see
    :func:`_parse_var_block`).
    """
    return [
        {"name": v["name"], "expression": v["expression"]}
        for v in _parse_var_block(dax)["variables"]
    ]


# DAX functions that typically return tables (not scalars)
_TABLE_RETURNING_FUNCTIONS = {
    "FILTER",
    "ALL",
    "ALLSELECTED",
    "ALLEXCEPT",
    "ALLNOBLANKROW",
    "VALUES",
    "SUMMARIZE",
    "ADDCOLUMNS",
    "SELECTCOLUMNS",
    "TOPN",
    "DISTINCT",
    "UNION",
    "INTERSECT",
    "EXCEPT",
    "DATATABLE",
    "GENERATESERIES",
    "GENERATE",
    "GENERATEALL",
    "CROSSJOIN",
    "NATURALINNERJOIN",
    "NATURALLEFTOUTERJOIN",
    "CALCULATETABLE",
    "TREATAS",
    "SUMMARIZECOLUMNS",
    "GROUPBY",
    "ROW",
    "DETAILROWS",
    "SAMPLE",
    "SUBSTITUTEWITHINDEX",
    "ROLLUP",
    "ROLLUPADDISSUBTOTAL",
    "ROLLUPISSUBTOTAL",
    "ROLLUPGROUP",
    "KEEPFILTERS",
    "DATESBETWEEN",
    "DATESINPERIOD",
    "DATESYTD",
    "DATESMTD",
    "DATESQTD",
    "WINDOW",
    "OFFSET",
    "INDEX",
}


def _classify_var_type(expression: str) -> str:
    """Determine if a variable expression returns a table or scalar.

    Checks whether the expression starts with a known table-returning DAX
    function, a table constructor, or a bare table reference.

    Returns:
        'table' or 'scalar'
    """
    from core.dax.dax_reference_parser import get_lexer
    from core.dax.tokenizer.tokens import TokenType

    tokens = get_lexer().tokenize_code(expression or "")
    if not tokens:
        return "scalar"
    first = tokens[0]
    if first.type == TokenType.BRACE_OPEN:
        return "table"
    if first.type == TokenType.TABLE_REF and len(tokens) == 1:
        return "table"
    if first.type in (TokenType.FUNCTION, TokenType.IDENTIFIER, TokenType.KEYWORD):
        if first.value.upper() in _TABLE_RETURNING_FUNCTIONS:
            return "table"
    return "scalar"


def _var_block(variables: List[Dict[str, str]], target_idx: int, probe: str) -> str:
    lines = [f"VAR {v['name']} = {v['expression']}" for v in variables[: target_idx + 1]]
    # A newline before RETURN keeps a trailing // comment in the last VAR harmless.
    return "\n".join(lines) + f"\nRETURN {probe}"


def _build_var_chain_query(
    variables: List[Dict[str, str]],
    target_idx: int,
    var_type: str,
    max_rows: int = 100,
    filter_args: Optional[List[str]] = None,
) -> str:
    """Build a DAX query that evaluates one variable in the measure's filter context.

    The VAR chain (index 0..target_idx) is evaluated as a single expression
    INSIDE ``CALCULATE`` (scalar) / ``CALCULATETABLE`` (table) with
    ``filter_args``, so every VAR sees the visual's filters (F-13).
    """
    target = variables[target_idx]["name"]
    block = _var_block(variables, target_idx, target)
    filters = [f for f in (filter_args or []) if f]
    if var_type == "table":
        inner = (
            f"CALCULATETABLE(\n{block}\n,\n{', '.join(filters)}\n)" if filters else f"(\n{block}\n)"
        )
        return f"EVALUATE\nTOPN({int(max_rows)}, {inner})"
    inner = f"CALCULATE(\n{block}\n,\n{', '.join(filters)}\n)" if filters else f"(\n{block}\n)"
    return f'EVALUATE\nROW("value", {inner})'


def _var_filter_context(
    args: Dict[str, Any], qe: Any
) -> Tuple[List[str], Optional[Dict[str, Any]]]:
    """CALCULATE filter args for debug_variable / step_variables.

    From the visual (``page_name`` + ``visual_id``/``visual_name``) when given,
    plus manual ``filters`` (e.g. ``"'Date'[Year] = 2025"`` to pin one row of a
    grouped visual). Returns ``(args, info)``.
    """
    filter_args: List[str] = []
    info: Optional[Dict[str, Any]] = None
    page_name = args.get("page_name")
    if page_name:
        from server.handlers._debug.analyze import _get_visual_filter_context_for_measure

        info, parts = _get_visual_filter_context_for_measure(
            page_name=page_name,
            visual_id=args.get("visual_id"),
            visual_name=args.get("visual_name"),
            include_slicers=args.get("include_slicers", True),
            qe=qe,
        )
        filter_args.extend(parts)
    manual = [f for f in (args.get("filters") or []) if isinstance(f, str) and f.strip()]
    filter_args.extend(manual)
    if manual:
        info = dict(info or {})
        info["manual_filters"] = manual
    return filter_args, info


def _handle_debug_variable(
    args: Dict[str, Any],
) -> Dict[str, Any]:
    """Debug a single variable inside a measure's DAX expression.

    Evaluates the variable by building a DEFINE query that
    includes the full VAR chain up to the target variable.
    """
    try:
        measure_name = args.get("measure_name")
        table_name = args.get("table_name")
        variable_name = args.get("variable_name")
        max_rows = args.get("max_rows", 100)

        if not measure_name:
            return error_envelope(
                "missing_argument",
                "measure_name is required",
            )
        if not variable_name:
            return error_envelope(
                "missing_argument",
                "variable_name is required",
            )

        if not connection_state.is_connected():
            return error_envelope("operation_failed", _NOT_CONNECTED)

        qe = connection_state.query_executor
        if not qe:
            return ErrorHandler.handle_manager_unavailable("query_executor")

        # Resolve measure expression — reach through debug_handler for
        # test-patch compatibility (unittest.mock.patch targets the
        # re-exported name on the parent module).
        from server.handlers import debug_handler as _dh

        resolved = _dh._resolve_measure_expression(measure_name, table_name, qe)
        if not resolved["success"]:
            return resolved

        expression = resolved["expression"]

        # Parse variables
        variables = _extract_variables_full(expression)
        if not variables:
            return error_envelope(
                "not_found",
                f"No VAR definitions found in measure " f"'{measure_name}'",
                expression_preview=expression[:200],
            )

        # Find the target variable
        target_idx = None
        for i, v in enumerate(variables):
            if v["name"].lower() == variable_name.lower():
                target_idx = i
                break

        if target_idx is None:
            return error_envelope(
                "not_found",
                f"Variable '{variable_name}' not found " f"in measure '{measure_name}'",
                available_variables=[v["name"] for v in variables],
            )

        target = variables[target_idx]
        var_type = _classify_var_type(target["expression"])
        filter_args, filter_info = _var_filter_context(args, qe)

        # Build and execute query
        query = _build_var_chain_query(variables, target_idx, var_type, max_rows, filter_args)

        start = time.monotonic()
        exec_result = qe.validate_and_execute_dax(query, top_n=max_rows, bypass_cache=True)
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)

        if not exec_result.get("success"):
            return error_envelope(
                "operation_failed",
                exec_result.get("error"),
                variable=variable_name,
                type=var_type,
                query=query,
            )

        rows = exec_result.get("rows", [])

        result: Dict[str, Any] = {
            "success": True,
            "variable": variable_name,
            "type": var_type,
            "expression": (
                target["expression"][:300] + "..."
                if len(target["expression"]) > 300
                else target["expression"]
            ),
            "execution_time_ms": elapsed_ms,
            "query": query,
            "cache": cache_provenance(exec_result),
        }
        if filter_info:
            result["filter_context"] = filter_info

        if var_type == "scalar":
            value = None
            if rows:
                row = rows[0]
                value = row.get("value", row.get("[value]"))
            result["value"] = value
        else:
            result["rows"] = rows
            result["row_count"] = len(rows)

        return result

    except Exception as e:
        logger.error(f"Error in debug_variable: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("debug_variable", e)


def _handle_step_variables(
    args: Dict[str, Any],
) -> Dict[str, Any]:
    """Step through all variables in a measure, evaluating each.

    Every step evaluates the VAR chain up to that variable inside the
    visual's filter context (plus manual ``filters``), so the reported value
    is what the measure's VAR holds in that context -- at the visual's grand
    total, or at the row pinned by ``filters``.
    """
    try:
        from server.request_context import raise_if_cancelled

        measure_name = args.get("measure_name")
        table_name = args.get("table_name")
        max_rows = args.get("max_rows", 10)

        if not measure_name:
            return error_envelope(
                "missing_argument",
                "measure_name is required",
            )

        if not connection_state.is_connected():
            return error_envelope("operation_failed", _NOT_CONNECTED)

        qe = connection_state.query_executor
        if not qe:
            return ErrorHandler.handle_manager_unavailable("query_executor")

        # Resolve measure expression — reach through debug_handler for
        # test-patch compatibility (unittest.mock.patch targets the
        # re-exported name on the parent module).
        from server.handlers import debug_handler as _dh

        resolved = _dh._resolve_measure_expression(measure_name, table_name, qe)
        if not resolved["success"]:
            return resolved

        expression = resolved["expression"]

        parsed = _parse_var_block(expression)
        variables = [
            {"name": v["name"], "expression": v["expression"]} for v in parsed["variables"]
        ]
        if not variables:
            return error_envelope(
                "not_found",
                f"No VAR definitions found in measure " f"'{measure_name}'",
                expression_preview=expression[:200],
            )

        filter_args, filter_info = _var_filter_context(args, qe)

        # Step through each variable
        steps: List[Dict[str, Any]] = []
        total_start = time.monotonic()
        previous_ms = 0.0

        for idx, var in enumerate(variables):
            raise_if_cancelled()
            var_type = _classify_var_type(var["expression"])
            rows_limit = max_rows if var_type == "table" else 1

            query = _build_var_chain_query(variables, idx, var_type, rows_limit, filter_args)

            step_start = time.monotonic()
            exec_result = qe.validate_and_execute_dax(query, top_n=rows_limit, bypass_cache=True)
            step_ms = round((time.monotonic() - step_start) * 1000, 1)

            step: Dict[str, Any] = {
                "var_name": var["name"],
                "type": var_type,
                "expression": (
                    var["expression"][:200] + "..."
                    if len(var["expression"]) > 200
                    else var["expression"]
                ),
                # The chain up to this VAR is evaluated each time: execution_time_ms
                # is CUMULATIVE; delta_ms approximates this VAR's own cost.
                "execution_time_ms": step_ms,
                "delta_ms": round(max(0.0, step_ms - previous_ms), 1),
            }
            previous_ms = step_ms

            if exec_result.get("success"):
                rows = exec_result.get("rows", [])
                if var_type == "scalar":
                    value = None
                    if rows:
                        row = rows[0]
                        value = row.get("value", row.get("[value]"))
                    step["value"] = value
                else:
                    step["row_count"] = len(rows)
                    step["rows"] = rows[:5]
                    if len(rows) > 5 or exec_result.get("truncated"):
                        step["truncated"] = True
            else:
                step["error"] = exec_result.get("error")

            steps.append(step)

        total_ms = round((time.monotonic() - total_start) * 1000, 1)

        # Find the most expensive step by its OWN cost (delta), not the cumulative time
        slowest = max(steps, key=lambda s: s.get("delta_ms", 0)) if steps else None

        result: Dict[str, Any] = {
            "success": True,
            "measure": measure_name,
            "variable_count": len(variables),
            "total_execution_time_ms": total_ms,
            "steps": steps,
            "evaluation_context": (
                "each VAR evaluated inside the visual's filter context (grand total; "
                "pin a row with `filters`)"
                if filter_args
                else "each VAR evaluated with no filters (model total)"
            ),
            "timing_note": (
                "execution_time_ms is cumulative (the chain up to the VAR runs each "
                "step); delta_ms is the step's increment. Result cache bypassed."
            ),
        }
        if parsed["return_expression"] is not None:
            result["return_expression"] = parsed["return_expression"][:300]
        if parsed["nested_blocks"]:
            result["nested_var_blocks"] = parsed["nested_blocks"]

        if slowest:
            result["slowest_variable"] = {
                "name": slowest["var_name"],
                "ms": slowest["delta_ms"],
                "cumulative_ms": slowest["execution_time_ms"],
            }

        if filter_args:
            result["filter_context"] = {
                "source": args.get("page_name") or "manual",
                "filters_applied": len(filter_args),
                **({"detail": filter_info} if filter_info else {}),
            }

        return result

    except Exception as e:
        logger.error(f"Error in step_variables: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("step_variables", e)


#: 04_Run_DAX trace-result keys that are ROWS (they go to ``result``, the rest
#: of the trace to ``se_fe_trace`` - the legacy 09 run_dax shape).
_ROW_KEYS = ("rows", "columns", "row_count", "truncated", "export", "sample")
_ENVELOPE_KEYS = ("success", "query", "cache", "connection")


def _handle_run_dax(args: Dict[str, Any]) -> Dict[str, Any]:
    """09_Debug_Operations run_dax: a thin alias of ``04_Run_DAX`` (L-12).

    Both go through ``query_handler.execute_dax`` - one executor, one cache
    policy, one trace store. The legacy response shape is kept: rows under
    ``result``, timings under ``se_fe_trace`` (with the ``trace_id`` that
    ``optimize`` consumes). Defaults differ from 04 on purpose: ``top_n``
    1000 and ``bypass_cache`` true (the debugger wants the CURRENT answer, F-07).
    """
    from server.handlers.query_handler import execute_dax

    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    query = str(args.get("query") or args.get("dax_query") or "").strip()
    if not query:
        return {
            "success": False,
            "error": "query parameter is required",
            "error_type": "missing_argument",
        }

    source = "09_Debug_Operations:run_dax"
    base: Dict[str, Any] = {
        "query": query,
        "top_n": args.get("top_n", 1000),
        "bypass_cache": bool(args.get("bypass_cache", True)),
        "clear_cache": args.get("clear_cache", True),
    }
    impersonation = {
        k: args.get(k) for k in ("roles", "effective_user_name", "custom_data") if args.get(k)
    }
    response: Dict[str, Any] = {"success": True, "query": query, "executed_via": "04_Run_DAX"}

    if args.get("trace", False):
        traced = execute_dax({**base, "mode": "trace"}, source=source)
        if not isinstance(traced, dict) or traced.get("success") is False:
            traced = traced if isinstance(traced, dict) else {}
            response["se_fe_trace"] = {
                "error": traced.get("error"),
                "error_type": traced.get("error_type"),
            }
        else:
            response["se_fe_trace"] = {
                k: v for k, v in traced.items() if k not in _ROW_KEYS + _ENVELOPE_KEYS
            }
            if not impersonation and "rows" in traced:
                # Rows of the SAME traced run - no second execution.
                response["result"] = {
                    "success": True,
                    **{k: traced[k] for k in _ROW_KEYS if k in traced},
                    "cache": traced.get("cache"),
                }
        if impersonation:
            response["trace_identity_note"] = (
                "Timings were traced WITHOUT RLS impersonation (the trace lane cannot "
                "impersonate); the rows in `result` are the impersonated answer."
            )

    if "result" not in response:
        exec_result = execute_dax({**base, "mode": "auto", **impersonation}, source=source)
        if not isinstance(exec_result, dict):
            exec_result = error_envelope("executor_error", "executor returned no result")
        response["result"] = exec_result
        if exec_result.get("success") is False:
            response["success"] = False
            response.setdefault("error", exec_result.get("error"))
            response.setdefault("error_type", exec_result.get("error_type"))
        if exec_result.get("impersonated"):
            response["impersonated"] = exec_result["impersonated"]

    return response


# =============================================================================
# OPTIMIZE OPERATION — SE/FE trace + DAX analysis + suggestions
# =============================================================================


def _measure_refs_in(expression: str, known_measures: Optional[set] = None) -> List[str]:
    """Measure names referenced by *expression* (lexer-driven, F-17).

    Unqualified ``[X]`` refs are measures by DAX convention; a qualified
    ``'T'[X]`` is a column unless the model says X is a measure. VAR names and
    strings/comments never count.
    """
    from core.dax.dax_reference_parser import scan_references

    out: List[str] = []
    known = {m.lower() for m in (known_measures or set())}
    for ref in scan_references(expression or ""):
        if ref.kind == "bracket":
            name = ref.name
        elif ref.kind == "qualified" and known and ref.name.lower() in known:
            name = ref.name
        else:
            continue
        if known and name.lower() not in known:
            continue
        if name not in out:
            out.append(name)
    return out


def _trace_measures_individually(
    measures: List[str], clear_cache: bool = True
) -> Dict[str, Dict[str, Any]]:
    """Run individual traces per measure to isolate timing.

    CAVEAT: Individual traces lack the visual's combined filter context,
    so timings may differ from the actual visual query.

    Args:
        measures: List of measure names to trace individually
        clear_cache: Whether to clear VertiPaq cache before each trace

    Returns:
        Dict mapping measure_name -> {total_ms, se_ms, fe_ms, se_queries}
    """
    from core.dax.dax_names import bracket

    results: Dict[str, Dict[str, Any]] = {}
    try:
        from core.infrastructure.query_trace import NativeTraceRunner
        from server.request_context import is_cancelled

        conn_str = (
            connection_state.connection_manager.connection_string
            if connection_state.connection_manager
            else None
        )
        if not conn_str or not NativeTraceRunner.is_available():
            return results

        runner = NativeTraceRunner(conn_str)
        for measure_name in measures:
            if is_cancelled():
                break
            try:
                query = f'EVALUATE ROW("Result", {bracket(measure_name)})'
                trace_result = runner.execute_with_trace(query, clear_cache, capture_plan=False)
                if "_error" not in trace_result:
                    # F-17: NativeTraceRunner reports the timings at the TOP level
                    # (only _build_trace_response nests them under "performance").
                    perf_data = trace_result.get("performance") or trace_result
                    results[measure_name] = {
                        "total_ms": perf_data.get("total_ms", 0),
                        "se_ms": perf_data.get("se_ms", 0),
                        "fe_ms": perf_data.get("fe_ms", 0),
                        "se_queries": perf_data.get("se_queries", 0),
                        "se_cache_hits": perf_data.get("se_cache_hits", 0),
                    }
                else:
                    results[measure_name] = {"error": trace_result["_error"]}
            except Exception as me:
                results[measure_name] = {"error": str(me)}
    except Exception as e:
        logger.warning(f"Per-measure tracing failed: {e}")

    return results
