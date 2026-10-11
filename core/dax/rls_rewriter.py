"""RLS filter rewriter — APPROXIMATE "view as user X" on PBI Desktop.

PBI Desktop's local AS instance does not honor the EffectiveUserName
connection-string property, so USERPRINCIPALNAME() always returns the
live Windows account. This module works around that limitation by
rewriting the role's filter expression at query time:

    USERPRINCIPALNAME()  ->  "<as_user>"
    USERNAME()           ->  "<as_user>"
    USEROBJECTID()       ->  "<object_id>" (falls back to "<as_user>", reported)
    CUSTOMDATA()         ->  "<custom_data>" (falls back to "", reported)

and wrapping EACH top-level EVALUATE body of the caller's query in
CALCULATETABLE with one FILTER per filtered table (ORDER BY / START AT stay
outside the wrapper).

Review E-16 — this is an APPROXIMATION, not EffectiveUserName impersonation
(the response carries ``approximation.known_gaps``):

- a CALCULATETABLE filter can be REMOVED by ALL / REMOVEFILTERS / ALLSELECTED /
  ALLEXCEPT inside measures; real RLS cannot, so "% of total" style measures
  see all rows under simulation but only permitted rows under real RLS;
- the filters propagate over every relationship direction the model has, not
  only where "apply security filter in both directions" is set;
- USEROBJECTID() / CUSTOMDATA() are only right when their values are passed.

The query is rewritten on the lexer's token stream (string literals such as a
DEFINE'd ``"EVALUATE"`` never split the query, comments never swallow the
separator comma).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

KNOWN_GAPS = [
    "ALL/REMOVEFILTERS/ALLSELECTED/ALLEXCEPT inside measures remove the simulated filter; "
    "real RLS cannot be removed (totals / % of total can differ).",
    "Simulated filters propagate over relationships regardless of the relationship's "
    "'apply security filter in both directions' setting.",
    "Dynamic RLS that depends on USEROBJECTID()/CUSTOMDATA() is exact only when "
    "object_id / custom_data are supplied.",
]

_USER_FUNCS = ("USERPRINCIPALNAME", "USERNAME", "USEROBJECTID", "CUSTOMDATA")


def _lexer():
    from core.dax.dax_reference_parser import get_lexer

    return get_lexer()


def _dax_string(s: str) -> str:
    return '"' + str(s).replace('"', '""') + '"'


def rewrite_filter_expression(
    filter_expr: str,
    as_user: str,
    *,
    object_id: Optional[str] = None,
    custom_data: Optional[str] = None,
    notes: Optional[List[str]] = None,
) -> str:
    """Replace USERPRINCIPALNAME() / USERNAME() / USEROBJECTID() / CUSTOMDATA()
    calls with literals, leaving string literals and comments untouched.

    Example:
        rewrite_filter_expression(
            'IF(USERPRINCIPALNAME() = [User], TRUE, FALSE)',
            'vic@x.com',
        )
        -> 'IF("vic@x.com" = [User], TRUE, FALSE)'

    ``notes`` (optional list) receives a line per approximation made.
    """
    from core.dax.tokenizer import TokenType
    from core.dax.tokenizer.lexer import TRIVIA_TYPES

    if not filter_expr or not as_user:
        return filter_expr or ""
    toks = _lexer().tokenize(filter_expr)
    edits: List[Tuple[int, int, str]] = []
    for i, t in enumerate(toks):
        if t.type not in (TokenType.FUNCTION, TokenType.IDENTIFIER, TokenType.KEYWORD):
            continue
        name = t.value.upper()
        if name not in _USER_FUNCS:
            continue
        # NAME ( ) with only whitespace in between
        j = i + 1
        while j < len(toks) and toks[j].type in TRIVIA_TYPES:
            j += 1
        if j >= len(toks) or toks[j].type != TokenType.PAREN_OPEN:
            continue
        k = j + 1
        while k < len(toks) and toks[k].type in TRIVIA_TYPES:
            k += 1
        if k >= len(toks) or toks[k].type != TokenType.PAREN_CLOSE:
            continue
        if name == "USEROBJECTID":
            if object_id:
                lit = _dax_string(object_id)
            else:
                lit = _dax_string(as_user)
                if notes is not None:
                    notes.append("USEROBJECTID() was replaced by the UPN (pass object_id)")
        elif name == "CUSTOMDATA":
            lit = _dax_string(custom_data or "")
            if not custom_data and notes is not None:
                notes.append("CUSTOMDATA() was replaced by an empty string (pass custom_data)")
        else:
            lit = _dax_string(as_user)
        edits.append((t.start, toks[k].end, lit))
    if not edits:
        return filter_expr
    out, cursor = [], 0
    for a, b, text in edits:
        out.append(filter_expr[cursor:a])
        out.append(text)
        cursor = b
    out.append(filter_expr[cursor:])
    return "".join(out)


def _split_query(user_dax: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Split a DAX query into ``(define_prefix, [(evaluate_body, trailer)])``.

    Only top-level (depth-0) ``EVALUATE`` / ``ORDER BY`` / ``START AT`` keyword
    tokens count, so a string literal or comment containing EVALUATE never
    splits the query. ``trailer`` is the ``ORDER BY ... [START AT ...]`` text.
    """
    from core.dax.tokenizer import TokenType

    toks = _lexer().tokenize_code(user_dax)
    depth = 0
    evals: List[int] = []
    order_at: Dict[int, int] = {}
    for i, t in enumerate(toks):
        if t.type in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
            depth += 1
        elif t.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            depth -= 1
        elif depth == 0 and t.type == TokenType.KEYWORD:
            up = t.value.upper()
            if up == "EVALUATE":
                evals.append(i)
            elif (
                up == "ORDER" and evals and i + 1 < len(toks) and toks[i + 1].value.upper() == "BY"
            ):
                order_at.setdefault(evals[-1], i)
            elif (
                up == "START" and evals and i + 1 < len(toks) and toks[i + 1].value.upper() == "AT"
            ):
                order_at.setdefault(evals[-1], i)
    if not evals:
        return user_dax, []
    prefix = user_dax[: toks[evals[0]].start]
    parts: List[Tuple[str, str]] = []
    for n, ei in enumerate(evals):
        body_start = toks[ei].end
        stmt_end = toks[evals[n + 1]].start if n + 1 < len(evals) else len(user_dax)
        cut = order_at.get(ei)
        if cut is not None:
            body = user_dax[body_start : toks[cut].start]
            trailer = user_dax[toks[cut].start : stmt_end]
        else:
            body = user_dax[body_start:stmt_end]
            trailer = ""
        parts.append((body.strip(), trailer.strip()))
    return prefix, parts


def build_as_user_query(
    user_dax: str,
    role_filters: List[Dict[str, str]],
    as_user: str,
    *,
    object_id: Optional[str] = None,
    custom_data: Optional[str] = None,
    notes: Optional[List[str]] = None,
) -> str:
    """Wrap every EVALUATE body of the user's query so each role-filtered table
    gets its rewritten filter applied.

    Args:
        user_dax: caller's DAX (``EVALUATE ...`` or ``DEFINE ... EVALUATE ...``,
            any number of EVALUATE statements, optional ORDER BY / START AT).
        role_filters: list of {table, filter_expression} from the role's
            table_permissions (as returned by the RLS manager).
        as_user: the UPN to substitute for USERPRINCIPALNAME() etc.

    Returns the rewritten query string ready to execute.
    """
    if not user_dax or not user_dax.strip():
        raise ValueError("user_dax is required")
    if not role_filters:
        return user_dax

    prefix, statements = _split_query(user_dax)
    if not statements:
        if prefix.strip().upper().startswith("DEFINE"):
            raise ValueError("DEFINE block found but no EVALUATE clause")
        # A bare table expression without EVALUATE: treat it as one body.
        prefix, statements = "", [(user_dax.strip(), "")]
    if any(not body for body, _t in statements):
        raise ValueError("no query body after EVALUATE")

    filter_clauses = []
    for f in role_filters:
        table = f.get("table") or f.get("table_name")
        expr = f.get("filter_expression") or f.get("expression") or ""
        if not table or not expr:
            continue
        rewritten = rewrite_filter_expression(
            expr, as_user, object_id=object_id, custom_data=custom_data, notes=notes
        )
        if table.startswith("'") and table.endswith("'"):
            table_ref = table
        else:
            table_ref = "'" + table.replace("'", "''") + "'"
        filter_clauses.append(f"FILTER({table_ref},\n{rewritten}\n)")

    if not filter_clauses:
        return user_dax

    wrapped_statements = []
    for body, trailer in statements:
        # The body ends on its own line so a trailing // comment in it can never
        # comment out the separator comma.
        wrapped = (
            "CALCULATETABLE(\n  "
            + body.rstrip()
            + "\n  ,\n  "
            + ",\n  ".join(filter_clauses)
            + "\n)"
        )
        wrapped_statements.append(f"EVALUATE\n{wrapped}" + (f"\n{trailer}" if trailer else ""))
    return prefix + "\n".join(wrapped_statements)


def simulate_as_user(
    user_dax: str,
    role_name: str,
    as_user: str,
    *,
    object_id: Optional[str] = None,
    custom_data: Optional[str] = None,
) -> Dict[str, Any]:
    """High-level entry: look up the role, rewrite the query, execute, return
    the result PLUS the rewritten query (for transparency / debugging).

    Returns a dict with:
        {
          success, rows, columns, row_count, ms,
          simulated: { as_user, role_name, filters_rewritten },
          approximation: { exact: False, known_gaps: [...], notes: [...] },
          rewritten_query   # the DAX actually executed
        }
    On lookup or rewrite failure, returns {success: False, error, error_type}.
    """
    from core.infrastructure.connection_state import connection_state

    if not connection_state.is_connected():
        return {
            "success": False,
            "error": "Not connected to Power BI Desktop",
            "error_type": "not_connected",
        }
    if not role_name:
        return {
            "success": False,
            "error": "role_name is required",
            "error_type": "invalid_args",
        }
    if not as_user:
        return {
            "success": False,
            "error": "as_user is required (UPN to simulate)",
            "error_type": "invalid_args",
        }

    rls_mgr = getattr(connection_state, "rls_manager", None)
    if rls_mgr is None:
        return {
            "success": False,
            "error": "rls_manager not available",
            "error_type": "not_connected",
        }

    roles_res = rls_mgr.list_roles()
    if not roles_res.get("success", True):
        return roles_res
    target_role = None
    for r in roles_res.get("roles", []):
        if r.get("name") == role_name:
            target_role = r
            break
    if target_role is None:
        return {
            "success": False,
            "error": f"role {role_name!r} not found",
            "error_type": "invalid_args",
            "available_roles": [r.get("name") for r in roles_res.get("roles", [])],
        }

    table_perms = target_role.get("table_permissions", []) or []
    notes: List[str] = []
    try:
        rewritten = build_as_user_query(
            user_dax,
            table_perms,
            as_user,
            object_id=object_id,
            custom_data=custom_data,
            notes=notes,
        )
    except ValueError as e:
        return {"success": False, "error": str(e), "error_type": "invalid_args"}

    qe = getattr(connection_state, "query_executor", None)
    if qe is None:
        return {
            "success": False,
            "error": "query_executor not available",
            "error_type": "not_connected",
        }

    exec_res = qe.validate_and_execute_dax(rewritten, top_n=int(0), bypass_cache=True)
    if not isinstance(exec_res, dict):
        return {
            "success": False,
            "error": "executor returned non-dict",
            "error_type": "trace_unavailable",
            "rewritten_query": rewritten,
        }

    out = {
        "success": bool(exec_res.get("success", True)),
        "rows": exec_res.get("rows", []),
        "columns": exec_res.get("columns", []),
        "row_count": exec_res.get("row_count", 0),
        "ms": exec_res.get("execution_time_ms", exec_res.get("ms")),
        "simulated": {
            "as_user": as_user,
            "role_name": role_name,
            "filters_rewritten": len(table_perms),
        },
        "approximation": {
            "exact": False,
            "known_gaps": list(KNOWN_GAPS),
            "notes": sorted(set(notes)),
        },
        "rewritten_query": rewritten,
    }
    if not out["success"]:
        out["error"] = exec_res.get("error")
        out["error_type"] = exec_res.get("error_type", "query_execution_error")
    return out
