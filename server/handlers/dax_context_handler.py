"""
DAX Context Handler
Handles DAX context analysis and debugging operations with integrated validation
"""

from typing import Dict, Any, List, Tuple, Optional
import logging
from server.registry import ToolDefinition
from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from core.validation.error_response import error_envelope
from core.utilities.progress_ticker import Ticker

logger = logging.getLogger(__name__)

# Advisory attached to any 05_DAX_Intelligence response that carries a
# rewrite/optimization payload. The analyzer detects structure, not result
# equivalence, so a rewrite can change values — the caller must verify.
_SEMANTIC_WARNING = (
    "Static analysis detects structure, not result equivalence. The "
    "optimized_measure / rewriter_draft may change results. Run 04_Run_DAX on "
    "the original and the rewrite over representative filter context and compare "
    "row-level values before applying."
)


def _result_has_rewrite_payload(result: Dict[str, Any]) -> bool:
    """True if *result* carries a rewrite/optimization the caller might apply."""
    if not isinstance(result, dict) or not result.get("success"):
        return False
    om = result.get("optimized_measure")
    if isinstance(om, dict) and (om.get("rewriter_draft") or om.get("optimized_dax")):
        return True
    if om and not isinstance(om, dict):  # any non-empty scalar/str optimized form
        return True
    imp = result.get("improvements")
    if isinstance(imp, dict) and (imp.get("rewriter_draft") or imp.get("has_improvements")):
        return True
    if result.get("rewriter_draft"):
        return True
    return False


def _attach_semantic_warning(result: Dict[str, Any]) -> Dict[str, Any]:
    """Inject the semantic-equivalence advisory when a rewrite is present.

    No-op on error responses, responses without a rewrite payload, or when a
    ``semantic_warning`` is already set (don't clobber a caller-specific note).
    """
    if not isinstance(result, dict):
        return result
    if "semantic_warning" in result:
        return result
    if _result_has_rewrite_payload(result):
        result["semantic_warning"] = _SEMANTIC_WARNING
    return result


_ROOT_TABLE_FUNCTIONS = {
    "SELECTCOLUMNS",
    "ADDCOLUMNS",
    "SUMMARIZE",
    "SUMMARIZECOLUMNS",
    "TOPN",
    "SAMPLE",
    "ROW",
    "DATATABLE",
    "CROSSJOIN",
    "UNION",
    "INTERSECT",
    "EXCEPT",
    "GENERATE",
    "GENERATEALL",
    "GENERATESERIES",
    "TREATAS",
    "NATURALINNERJOIN",
    "NATURALLEFTOUTERJOIN",
}
_AMBIGUOUS_TABLE_FUNCTIONS = {
    "FILTER",
    "VALUES",
    "ALL",
    "ALLSELECTED",
    "DISTINCT",
    "CALCULATETABLE",
}
_AGGREGATION_FUNCTIONS = {
    "CALCULATE",
    "SUM",
    "SUMX",
    "AVERAGE",
    "AVERAGEX",
    "COUNT",
    "COUNTX",
    "COUNTROWS",
    "MIN",
    "MINX",
    "MAX",
    "MAXX",
    "DIVIDE",
}


def _code_tokens(expression: str):
    from core.dax.dax_reference_parser import get_lexer

    return get_lexer().tokenize_code(expression or "")


def _build_validation_query(expression: str) -> str:
    """Wrap *expression* in a CHEAP, comment-safe validation query (review D-14).

    - ``DEFINE … EVALUATE`` / ``EVALUATE …`` queries pass through unchanged
      (the first CODE token decides, so a leading comment does not matter).
    - A table expression becomes ``EVALUATE TOPN(0, <expr>\\n)`` and a scalar
      ``EVALUATE ROW("Result", IF(FALSE(), <expr>\\n))`` — the engine binds and
      type-checks the expression without computing the grand total of a heavy
      measure. The closing parens sit on their own line so a trailing
      ``// comment`` in the expression cannot comment them out.
    """
    from core.dax.tokenizer import TokenType

    code = _code_tokens(expression)
    if not code:
        return expression
    first = code[0]
    if first.type == TokenType.KEYWORD and first.value.upper() in ("EVALUATE", "DEFINE"):
        return expression

    is_table = False
    if first.type == TokenType.BRACE_OPEN or (first.type == TokenType.TABLE_REF and len(code) == 1):
        is_table = True
    elif (
        first.type in (TokenType.FUNCTION, TokenType.IDENTIFIER, TokenType.KEYWORD)
        and len(code) > 1
        and code[1].type == TokenType.PAREN_OPEN
    ):
        name = first.value.upper()
        if name in _ROOT_TABLE_FUNCTIONS:
            is_table = True
        elif name in _AMBIGUOUS_TABLE_FUNCTIONS:
            has_aggregation = any(
                t.type == TokenType.FUNCTION and t.value.upper() in _AGGREGATION_FUNCTIONS
                for t in code
            )
            is_table = not has_aggregation

    if is_table:
        return f"EVALUATE TOPN(0, {expression}\n)"
    return f'EVALUATE ROW("Result", IF(FALSE(), {expression}\n))'


def _validate_dax_syntax(expression: str) -> Tuple[bool, str]:
    """
    Validate DAX syntax before analysis by asking the engine to bind the
    expression in a cheap wrapper (see ``_build_validation_query``).

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not connection_state.is_connected():
        return (
            False,
            "Not connected to Power BI instance. Please connect using tool '01_Connection' first.",
        )

    qe = connection_state.query_executor
    if not qe:
        return False, "Query executor not available"

    try:
        test_query = _build_validation_query(expression.strip())
        result = qe.validate_and_execute_dax(test_query, top_n=0)

        if result.get("success"):
            return True, ""
        error_msg = result.get("error", "Unknown validation error")
        # Strip our wrapper from the engine message (the user didn't write it)
        for artefact in ('ROW("Result", IF(FALSE(), ', 'ROW("Result",', "TOPN(0, "):
            error_msg = error_msg.replace(artefact, "")
        return False, error_msg
    except Exception as e:
        return False, str(e)


def _looks_like_dax(text: str) -> bool:
    """True when *text* has DAX structure (a call, a bracket/quoted name, a
    literal or an operator) — i.e. it must be analysed as DAX, never swapped
    for a fuzzily matched measure (review D-04)."""
    from core.dax.tokenizer import TokenType

    structural = {
        TokenType.PAREN_OPEN,
        TokenType.COLUMN_REF,
        TokenType.QUALIFIED_REF,
        TokenType.TABLE_REF,
        TokenType.STRING,
        TokenType.NUMBER,
        TokenType.DATETIME,
        TokenType.KEYWORD,
        TokenType.OPERATOR,
        TokenType.BRACE_OPEN,
        TokenType.COMMA,
    }
    return any(t.type in structural for t in _code_tokens(text))


def _measure_suggestions(search: str, rows: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    from difflib import SequenceMatcher

    term = (search or "").lower().strip()
    scored = []
    for r in rows:
        name = str(r.get("Name", "") or "")
        if not name:
            continue
        sim = SequenceMatcher(None, term, name.lower()).ratio()
        if sim > 0.3 or (term and term in name.lower()):
            scored.append((sim, name, str(r.get("Table", "") or "")))
    scored.sort(key=lambda x: x[0], reverse=True)
    from core.dax.dax_names import column_ref

    return [column_ref(t, n) for _, n, t in scored[:limit]]


def _resolve_measure_input(args: Dict[str, Any]) -> Dict[str, Any]:
    """Decide what 05 analyses (review D-04).

    Returns ``{"expression", "measure", "table", "error"?}``:

    - ``measure_name`` given -> exact (case-insensitive) lookup of that measure;
      unknown -> error with suggestions. A fuzzy match is NEVER used silently.
    - ``expression`` that exactly names a measure (optionally ``[Name]``) ->
      that measure's expression.
    - ``expression`` with DAX structure -> analysed as DAX, verbatim.
    - a bare word/phrase that is not an exact measure name -> error with
      suggestions (it cannot be valid DAX either).
    Measures are enumerated through the query executor (no ad-hoc AMO connect).
    """
    explicit = args.get("measure_name")
    expression = args.get("expression")
    candidate = explicit if explicit else expression
    if not candidate:
        return {"expression": None, "measure": None, "table": None}

    text = str(candidate).strip()
    from core.dax.tokenizer import TokenType

    code = _code_tokens(text)
    lookup_name = text
    if len(code) == 1 and code[0].type == TokenType.COLUMN_REF:
        from core.dax.dax_names import unbracket

        lookup_name = unbracket(code[0].value)

    must_be_name = bool(explicit)
    # An exact name match always wins (names may contain parens/operators:
    # "NAV TWR (%) Axis (cumul)"), so every single-line input is looked up.
    maybe_name = must_be_name or ("\n" not in text and len(text) < 200)
    if not maybe_name:
        return {"expression": expression, "measure": None, "table": None}

    qe = getattr(connection_state, "query_executor", None)
    rows: List[Dict[str, Any]] = []
    enum_error = None
    if qe is not None:
        try:
            enum = qe.enumerate_measures_tom()
            if isinstance(enum, dict) and enum.get("success"):
                rows = enum.get("rows", []) or []
            else:
                enum_error = (enum or {}).get("error") if isinstance(enum, dict) else None
        except Exception as e:  # noqa: BLE001
            enum_error = str(e)

    wanted = " ".join(lookup_name.lower().split())
    for r in rows:
        if " ".join(str(r.get("Name", "") or "").lower().split()) == wanted:
            return {
                "expression": r.get("Expression", "") or "",
                "measure": r.get("Name"),
                "table": r.get("Table"),
            }

    if must_be_name or not _looks_like_dax(text):
        suggestions = _measure_suggestions(lookup_name, rows)
        msg = f"No measure named {text!r} was found in the model."
        if enum_error:
            msg += f" (measure enumeration failed: {enum_error})"
        if suggestions:
            msg += " Did you mean: " + ", ".join(suggestions) + "?"
        msg += " Pass the exact name as measure_name, or pass DAX code as expression."
        return {
            "expression": None,
            "measure": None,
            "table": None,
            "error": {
                "success": False,
                "error": msg,
                "error_type": "measure_not_found",
                "suggestions": suggestions or None,
            },
        }
    # "[X]" that is not a measure: analyse the DAX as written.
    return {"expression": expression, "measure": None, "table": None}


def _handle_list_udfs(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all DAX user-defined functions in the model."""
    executor = connection_state.query_executor
    if not executor:
        return error_envelope("not_connected", "Not connected")

    result = executor.validate_and_execute_dax("EVALUATE INFO.USERDEFINEDFUNCTIONS()")

    if result.get("success"):
        return {
            "success": True,
            "udfs": result.get("rows", []),
            "count": result.get("row_count", 0),
        }

    # Distinguish "model/DAX version has no UDF support" from a real infra failure.
    err = (result.get("error") or "").lower()
    if "has not been recognized" in err or "cannot find" in err or "unknown" in err:
        return {
            "success": True,
            "udfs": [],
            "count": 0,
            "note": "UDFs not supported in this model or DAX version",
        }

    return {
        "success": False,
        "error": result.get("error", "Failed to list UDFs"),
        "error_type": result.get("error_type", "query_error"),
    }


# Rule-id → 15_DAX_Refactor transform that can auto-fix it. Used by lint_model
# to flag which findings have a one-call mechanical fix. Every key must be a
# rule the unified analyzer can emit (pinned by a test — review D-07).
_AUTO_FIX_TRANSFORMS = {
    "MAINT_UNUSED_VAR": "remove_unused_variables",
    "CORR_DIVISION_WITHOUT_DIVIDE": "use_divide",
}

_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _fix_would_edit(rule_id: str, expression: str) -> bool:
    """True when the auto-fix transform of *rule_id* would actually change *expression*.

    ``use_divide`` only rewrites a measure whose whole body is one top-level division, so for a
    division inside an iterator the "auto fix" was a silent no-op and ``auto_fixable_count`` was
    overstated (2026-10-03 review D-04). The check uses the transforms' own rewrite functions.
    """
    try:
        if rule_id == "CORR_DIVISION_WITHOUT_DIVIDE":
            from core.codemods.transforms.use_divide import UseDivide

            return UseDivide._rewrite(expression) is not None
        if rule_id == "MAINT_UNUSED_VAR":
            from core.codemods.transforms.remove_unused_variables import RemoveUnusedVariables

            return RemoveUnusedVariables._rewrite(expression) is not None
    except Exception:  # noqa: BLE001 - a failing probe must not hide the finding
        return False
    return True


def _starts_with_function_header(text: str) -> bool:
    """True when the first CODE token is the FUNCTION keyword (comments ignored)."""
    code = _code_tokens(text)
    return bool(code) and code[0].value.upper() == "FUNCTION"


def _collect_udf_definitions(executor) -> List[tuple]:
    """Return [(udf_name, definition_text)] for every DAX UDF in the model.

    Best-effort: a model/DAX version without UDF support (or a DMV failure)
    yields an empty list so lint_model still reports on measures. The text fed to
    the analyzer is reconstructed as ``FUNCTION <name> = <expression>`` when the
    stored expression is only the ``(params) => body`` lambda, so the UDF rules
    (which key off the ``FUNCTION <name> = (`` header) can see the signature.
    """
    try:
        res = executor.validate_and_execute_dax("EVALUATE INFO.USERDEFINEDFUNCTIONS()")
    except Exception:  # pragma: no cover - executor-level failure
        return []
    if not isinstance(res, dict) or not res.get("success"):
        return []

    out: List[tuple] = []
    for row in res.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        name = ""
        expr = ""
        for key, value in row.items():
            k = str(key).strip("[]").lower()
            if k == "name" and not name:
                name = str(value or "")
            elif k == "expression" and not expr:
                expr = str(value or "")
        if not expr.strip():
            continue
        text = expr if _starts_with_function_header(expr) else f"FUNCTION {name or 'Udf'} = {expr}"
        out.append((name or "Udf", text))
    return out


def _handle_lint_model(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run the unified DAX analyzer over EVERY measure (and UDF) in one call.

    Returns findings (one row per issue) with an ``auto_fixable`` flag mapping
    to a 15_DAX_Refactor transform where one exists, a ``per_measure`` summary
    (health score + finding count), and ``by_rule`` counts.

    Filters (review D-23): ``min_severity`` (critical|high|medium|low|info),
    ``rule_ids`` (list), ``tables`` (list), ``max_findings`` (default 200; the
    response says how many were omitted). ``suppress_rules`` (list) drops rule
    ids model-wide; ``// lint-disable RULE`` comments in a measure drop them for
    that measure. The model schema is built once and passed to every measure so
    schema-aware rules agree with ``05 analyze``.
    """
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    executor = connection_state.query_executor
    if not executor:
        return ErrorHandler.handle_manager_unavailable("query_executor")

    enum = executor.enumerate_measures_tom()
    if not enum.get("success"):
        return {
            "success": False,
            "error": enum.get("error", "Failed to enumerate measures"),
            "error_type": enum.get("error_type", "enumerate_failed"),
        }

    rows = enum.get("rows", []) or []
    wanted_tables = {str(t).lower() for t in (args.get("tables") or [])}
    measures = []
    table_of: Dict[str, str] = {}
    for r in rows:
        if not r.get("Expression"):
            continue
        if wanted_tables and str(r.get("Table", "")).lower() not in wanted_tables:
            continue
        name = r.get("Name", "")
        measures.append((name, r.get("Expression", "") or ""))
        table_of.setdefault(name, r.get("Table", ""))

    from core.dax.analyzer.models import AnalysisContext
    from core.dax.analyzer.unified_analyzer import get_shared_analyzer

    ctx = AnalysisContext()
    try:
        from core.dax.analyzer.schema_builder import build_model_schema

        ctx.schema = build_model_schema(executor)
    except Exception as e:  # noqa: BLE001 - schema is enrichment only
        logger.debug(f"lint_model schema enrichment skipped: {e}")

    # The TMSL snapshot BPA reads: richer schema (storage mode, relationship table names, RLS
    # tables, calc-group / field-parameter tables) and every non-measure DAX expression
    # (review D-16 / D-05 / D-09). Best-effort: no AMO just leaves lint_model on measures + UDFs.
    other_objects: List[Any] = []
    tmsl_available = False
    # Measure annotation ``lint-disable`` = "RULE_A, RULE_B": accepted findings of that measure
    # (09-22 ledger D-23). enumerate_measures_tom() carries no annotations; the TMSL snapshot does.
    annotation_suppress: Dict[str, set] = {}
    try:
        from core.dax.analyzer.tmsl_objects import (
            extract_dax_objects,
            resolve_model,
            schema_from_tmsl,
        )

        tr = executor.get_tmsl_definition() if hasattr(executor, "get_tmsl_definition") else None
        tmsl_model = (
            resolve_model(tr.get("tmsl")) if isinstance(tr, dict) and tr.get("success") else None
        )
        if tmsl_model is not None:
            tmsl_available = True
            ctx.schema = schema_from_tmsl(tmsl_model)
            other_objects = extract_dax_objects(tmsl_model)
            import re as _re

            for _t in tmsl_model.get("tables", []) or []:
                for _m in _t.get("measures", []) or []:
                    for _a in _m.get("annotations", []) or []:
                        if str(_a.get("name", "")).strip().lower() in (
                            "lint-disable",
                            "lint_disable",
                            "lintdisable",
                        ):
                            ids_ = {
                                x.upper()
                                for x in _re.split(r"[\s,;]+", str(_a.get("value", "")))
                                if x
                            }
                            if ids_:
                                annotation_suppress.setdefault(
                                    str(_m.get("name", "")), set()
                                ).update(ids_)
            if wanted_tables:
                other_objects = [o for o in other_objects if o.table.lower() in wanted_tables]
    except Exception as e:  # noqa: BLE001 - enrichment only
        logger.debug(f"lint_model TMSL enrichment skipped: {e}")
    suppress = {str(r).upper() for r in (args.get("suppress_rules") or [])}
    if suppress:
        ctx.suppressed_rules = suppress

    analyzer = get_shared_analyzer()
    udfs = [] if wanted_tables else _collect_udf_definitions(executor)
    if udfs and ctx.schema is not None:
        try:
            from core.dax.analyzer.rules.udf_rules import udf_signature

            for _n, text in udfs:
                sig = udf_signature(text)
                if sig:
                    ctx.schema.udf_signatures[sig[0].upper()] = sig[1]
        except Exception as e:  # noqa: BLE001 - enrichment only
            logger.debug(f"lint_model UDF signatures skipped: {e}")
    import dataclasses

    # F-26: per-measure progress + cooperative cancel. A cancelled sweep returns what it analysed
    # (``cancelled: true``), never an all-clear for the measures it did not reach.
    ticker = Ticker(len(measures) + len(other_objects) + len(udfs), "object")
    cancelled = False
    results = []
    for _name, _dax in measures:
        if ticker.cancelled():
            cancelled = True
            break
        accepted = set(ctx.suppressed_rules or ()) | annotation_suppress.get(_name, set())
        results.append(
            analyzer.analyze(
                _dax,
                dataclasses.replace(ctx, measure_name=_name, suppressed_rules=accepted or None),
            )
        )
        ticker.tick(_name)

    findings: List[Dict[str, Any]] = []
    per_measure: List[Dict[str, Any]] = []
    suppressed_total = 0
    # Identical (object, rule, title) findings collapse into one row with ``occurrences``: a UDF
    # that trips the same rule six times showed six indistinguishable rows (R0201 live test).
    finding_index: Dict[Any, Dict[str, Any]] = {}

    def _collect(
        name: str, table: str, res, kind: Optional[str] = None, expression: str = ""
    ) -> None:
        nonlocal suppressed_total
        suppressed_total += len(res.suppressed)
        entry = {
            "measure": name,
            "table": table,
            "health_score": res.health_score,
            "findings": len(res.issues),
        }
        if kind:
            entry["object_kind"] = kind
        if res.issues or res.suppressed:
            if res.suppressed:
                entry["suppressed"] = res.suppressed
            per_measure.append(entry)
        for issue in res.issues:
            # WP6 / E-22: 15_DAX_Refactor transforms only rewrite MEASURES, and
            # are scoped to the one measure the finding is about.
            transform = None if kind else _AUTO_FIX_TRANSFORMS.get(issue.rule_id)
            if transform and (
                issue.severity == "info" or not _fix_would_edit(issue.rule_id, expression)
            ):
                transform = None  # the transform would not change this measure
            row = {
                "measure": name,
                "table": table,
                "rule_id": issue.rule_id,
                "severity": issue.severity,
                "category": issue.category,
                "title": issue.title,
                "description": issue.description,
                "auto_fixable": transform is not None,
                "fix_transform": transform,
                "fix_args": {"measures": [f"{table}[{name}]"]} if transform else None,
            }
            if kind:
                row["object_kind"] = kind
            # description is part of the key: one rule/title can carry different specifics
            # (which column, which function) that collapsing would silently lose.
            dedupe_key = (name, table, kind, issue.rule_id, issue.title, issue.description)
            seen = finding_index.get(dedupe_key)
            if seen is not None:
                seen["occurrences"] += 1
                continue
            row["occurrences"] = 1
            finding_index[dedupe_key] = row
            findings.append(row)

    for (name, _dax), res in zip(measures, results):
        _collect(name, table_of.get(name, ""), res, expression=_dax)

    # Every other DAX expression of the model: format strings, calculation items, calculated
    # columns / tables, RLS filters, KPI expressions (review D-16).
    objects_by_kind: Dict[str, int] = {}
    for obj in other_objects:
        if cancelled or ticker.cancelled():
            cancelled = True
            break
        octx = dataclasses.replace(ctx, measure_name=obj.name, object_kind=obj.kind)
        try:
            ores = analyzer.analyze(obj.expression, octx)
        except Exception as e:  # noqa: BLE001 - one bad expression must not stop the sweep
            logger.debug(f"lint_model: {obj.kind} {obj.table}[{obj.name}] failed: {e}")
            ticker.tick(obj.name)
            continue
        objects_by_kind[obj.kind] = objects_by_kind.get(obj.kind, 0) + 1
        _collect(obj.name, obj.table, ores, kind=obj.kind)
        ticker.tick(obj.name)

    # DAX UDFs (GA, CL 1702) are linted alongside measures so the UDF-specific
    # rules (PY_UDF_*) are actually exercised. Best-effort: no UDF support in the
    # model just yields nothing.
    udfs_analyzed = 0
    if udfs and not cancelled and not ticker.cancelled():
        for (udf_name, _text), res in zip(udfs, analyzer.analyze_batch(udfs, ctx)):
            _collect(udf_name, "", res, kind="function")
            udfs_analyzed += 1
            ticker.tick(udf_name)
    elif udfs:
        cancelled = True

    # Filters
    rule_filter = {str(r).upper() for r in (args.get("rule_ids") or [])}
    if rule_filter:
        findings = [f for f in findings if f["rule_id"].upper() in rule_filter]
    by_rule_all_severities: Dict[str, int] = {}
    for f in findings:
        by_rule_all_severities[f["rule_id"]] = by_rule_all_severities.get(f["rule_id"], 0) + 1
    min_sev = str(args.get("min_severity") or "").lower()
    if min_sev in _SEV_RANK:
        findings = [f for f in findings if _SEV_RANK.get(f["severity"], 9) <= _SEV_RANK[min_sev]]
    by_rule: Dict[str, int] = {}
    for f in findings:
        by_rule[f["rule_id"]] = by_rule.get(f["rule_id"], 0) + 1

    # Sort worst-first: severity, then by how common the rule is (frequent first),
    # then by measure name for a stable order.
    findings.sort(
        key=lambda f: (
            _SEV_RANK.get(f["severity"], 9),
            -by_rule_all_severities.get(f["rule_id"], 0),
            f["measure"],
        )
    )
    per_measure.sort(key=lambda m: (m["health_score"], m["measure"]))

    total = len(findings)
    total_occurrences = sum(int(f.get("occurrences") or 1) for f in findings)
    max_findings = args.get("max_findings")
    limit = int(max_findings) if max_findings is not None else 200
    omitted = max(0, total - limit) if limit >= 0 else 0
    if limit >= 0:
        findings = findings[:limit]

    out = {
        "success": True,
        "measures_analyzed": len(results),
        "functions_analyzed": udfs_analyzed,
        "other_objects_analyzed": objects_by_kind,
        "tmsl_available": tmsl_available,
        "total_findings": total,  # distinct rows (identical findings collapsed)
        "total_occurrences": total_occurrences,  # rows x their `occurrences`
        "returned_findings": len(findings),
        "by_rule": by_rule,
        # only when min_severity actually filtered: otherwise it repeats by_rule
        **(
            {"by_rule_all_severities": by_rule_all_severities}
            if by_rule_all_severities != by_rule
            else {}
        ),
        "auto_fixable_count": sum(1 for f in findings if f["auto_fixable"]),
        "per_measure": per_measure,
        "findings": findings,
    }
    if cancelled:
        out["cancelled"] = True
        out["partial"] = True
        out["measures_not_analyzed"] = len(measures) - len(results)
    if omitted:
        out["omitted_findings"] = omitted
        out["note"] = (
            f"{omitted} lower-ranked finding(s) omitted; narrow with min_severity / rule_ids / "
            "tables or raise max_findings."
        )
    if suppressed_total:
        out["suppressed_findings"] = suppressed_total
    return out


def _handle_expand_measure(args: Dict[str, Any]) -> Dict[str, Any]:
    """Emit a self-contained ``DEFINE … EVALUATE`` query for a measure (DAX Studio
    "Define Dependent Measures" parity).

    Dependencies are found with the lexer (strings / comments / format strings
    never count; ``'T'[M]`` qualified measure refs and escaped names such as
    ``[Sales ]]x]`` do) and every name is escaped with ``core.dax.dax_names``
    (review D-19). UDFs called by the measure or its dependencies are emitted as
    ``DEFINE FUNCTION`` (review D-27). ``inline=true`` instead returns a single
    expression with every dependency inlined as ``CALCULATE(<body>)`` (the
    implicit CALCULATE of a measure reference keeps the semantics).
    """
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    target = args.get("measure_name") or args.get("expression") or args.get("measure")
    if not target:
        return error_envelope("missing_argument", "expression (measure name) is required")

    executor = connection_state.query_executor
    if not executor:
        return ErrorHandler.handle_manager_unavailable("query_executor")

    enum = executor.enumerate_measures_tom()
    if not enum.get("success"):
        return {
            "success": False,
            "error": enum.get("error", "Failed to enumerate measures"),
            "error_type": enum.get("error_type", "enumerate_failed"),
        }

    from core.dax.dax_names import column_ref, dax_string, unbracket
    from core.dax.dax_reference_parser import get_lexer, scan_references
    from core.dax.tokenizer import TokenType

    rows = enum.get("rows", []) or []
    # name -> (table, expression); lookups are case-insensitive (DAX names are).
    by_name = {r.get("Name", ""): (r.get("Table", ""), r.get("Expression", "") or "") for r in rows}

    def _norm(n: str) -> str:
        return " ".join((n or "").lower().split())

    by_norm = {_norm(k): k for k in by_name}
    home_of = {_norm(k): _norm(v[0]) for k, v in by_name.items()}

    wanted = str(target).strip()
    code = get_lexer().tokenize_code(wanted)
    if len(code) == 1 and code[0].type == TokenType.COLUMN_REF:
        wanted = unbracket(code[0].value)
    resolved = by_norm.get(_norm(wanted))
    if resolved is None:
        return {
            "success": False,
            "error": f"Measure not found: {target}",
            "error_type": "measure_not_found",
            "suggestions": _measure_suggestions(wanted, rows) or None,
        }

    def _measure_deps(expr: str) -> List[str]:
        deps: List[str] = []
        for ref in scan_references(expr):
            key = _norm(ref.name)
            if not key or key not in by_norm:
                continue
            if ref.kind == "bracket" or (
                ref.kind == "qualified" and _norm(ref.table) == home_of.get(key)
            ):
                canon = by_norm[key]
                if canon not in deps:
                    deps.append(canon)
        return deps

    udf_defs = {}
    try:
        udf_defs = {_norm(n): (n, text) for n, text in _collect_udf_definitions(executor)}
    except Exception:  # noqa: BLE001 - UDFs are optional
        udf_defs = {}

    def _udf_calls(expr: str) -> List[str]:
        if not udf_defs:
            return []
        toks = get_lexer().tokenize_code(expr)
        out: List[str] = []
        for i, t in enumerate(toks[:-1]):
            if (
                t.type in (TokenType.IDENTIFIER, TokenType.FUNCTION)
                and toks[i + 1].type == TokenType.PAREN_OPEN
            ):
                key = _norm(t.value)
                if key in udf_defs and key not in out:
                    out.append(key)
        return out

    # DFS post-order: dependencies before dependants.
    order: List[str] = []
    state: Dict[str, int] = {}
    cycle = False

    def visit(name: str) -> None:
        nonlocal cycle
        st = state.get(name)
        if st == 2:
            return
        if st == 1:
            cycle = True
            return
        state[name] = 1
        for dep in _measure_deps(by_name[name][1]):
            if dep != name:
                visit(dep)
        state[name] = 2
        order.append(name)

    visit(resolved)

    udf_order: List[str] = []
    pending = [resolved] + [m for m in order if m != resolved]
    seen_udf = set()
    for m in pending:
        for key in _udf_calls(by_name[m][1]):
            stack = [key]
            while stack:
                k = stack.pop()
                if k in seen_udf:
                    continue
                seen_udf.add(k)
                udf_order.append(k)
                stack.extend(x for x in _udf_calls(udf_defs[k][1]) if x not in seen_udf)

    target_table = by_name[resolved][0]
    deps = [m for m in order if m != resolved]

    if bool(args.get("inline", False)):
        if cycle:
            return {
                "success": False,
                "error": "Circular measure dependency — cannot inline.",
                "error_type": "dependency_cycle",
            }

        def inline(name: str, depth: int = 0) -> str:
            expr = by_name[name][1]
            if depth > 32:
                return expr
            edits = []
            for ref in scan_references(expr):
                key = _norm(ref.name)
                if key not in by_norm:
                    continue
                if ref.kind == "bracket" or (
                    ref.kind == "qualified" and _norm(ref.table) == home_of.get(key)
                ):
                    edits.append((ref.start, ref.end, by_norm[key]))
            out = []
            cursor = 0
            for start, end, dep in sorted(edits):
                if start < cursor:
                    continue
                out.append(expr[cursor:start])
                out.append(f"CALCULATE(\n{inline(dep, depth + 1)}\n)")
                cursor = end
            out.append(expr[cursor:])
            return "".join(out)

        body = inline(resolved)
        define = ""
        if udf_order:
            define = (
                "DEFINE\n"
                + "\n".join(
                    f"\tFUNCTION {udf_defs[k][0]} = {_strip_function_header(udf_defs[k][1])}"
                    for k in reversed(udf_order)
                )
                + "\n"
            )
        query = f"{define}EVALUATE\n\tROW({dax_string(resolved)}, {body}\n)"
        return {
            "success": True,
            "measure": resolved,
            "table": target_table,
            "mode": "inline",
            "dependency_count": len(deps),
            "dependencies": deps,
            "functions": [udf_defs[k][0] for k in udf_order],
            "inlined_expression": body,
            "query": query,
        }

    define_lines = [
        f"\tFUNCTION {udf_defs[k][0]} = {_strip_function_header(udf_defs[k][1])}"
        for k in reversed(udf_order)
    ]
    for mname in order:
        table, expr = by_name[mname]
        # Newline before the next definition keeps a trailing // comment harmless.
        define_lines.append(f"\tMEASURE {column_ref(table, mname)} = {expr}\n")

    query = (
        "DEFINE\n"
        + "\n".join(define_lines)
        + f"\nEVALUATE\n\tROW({dax_string(resolved)}, {column_ref(target_table, resolved)})"
    )

    out = {
        "success": True,
        "measure": resolved,
        "table": target_table,
        "dependency_count": len(deps),
        "dependencies": deps,
        "functions": [udf_defs[k][0] for k in udf_order],
        "query": query,
    }
    if cycle:
        out["warning"] = "Circular measure dependency detected — the query will not run as-is."
    return out


def _model_facts(executor) -> Tuple[Any, List[Dict[str, Any]], List[Dict[str, Any]], Any]:
    """``(schema, roles, calc_groups, tmsl_model)`` - best effort, never raises.

    The TMSL snapshot (the one lint_model reads) gives the richest schema, the role -> table map
    and the calculation groups; without it the DMV schema builder is used.
    """
    schema: Any = None
    roles: List[Dict[str, Any]] = []
    calc_groups: List[Dict[str, Any]] = []
    tmsl_model = None
    try:
        from core.dax.analyzer.tmsl_objects import resolve_model, schema_from_tmsl

        tr = executor.get_tmsl_definition() if hasattr(executor, "get_tmsl_definition") else None
        tmsl_model = (
            resolve_model(tr.get("tmsl")) if isinstance(tr, dict) and tr.get("success") else None
        )
        if tmsl_model is not None:
            schema = schema_from_tmsl(tmsl_model)
            for role in tmsl_model.get("roles", []) or []:
                tabs = [
                    str(tp.get("name", ""))
                    for tp in role.get("tablePermissions", []) or []
                    if str(tp.get("filterExpression", "") or "").strip()
                    or isinstance(tp.get("filterExpression"), (list, dict))
                ]
                roles.append({"name": str(role.get("name", "")), "tables": tabs})
            for tbl in tmsl_model.get("tables", []) or []:
                cg = tbl.get("calculationGroup")
                if isinstance(cg, dict):
                    calc_groups.append(
                        {
                            "table": str(tbl.get("name", "")),
                            "precedence": cg.get("precedence"),
                            "items": [
                                str(i.get("name", "")) for i in cg.get("calculationItems", []) or []
                            ],
                        }
                    )
    except Exception as e:  # noqa: BLE001 - enrichment only
        logger.debug(f"model facts via TMSL skipped: {e}")
    if schema is None:
        try:
            from core.dax.analyzer.schema_builder import build_model_schema

            schema = build_model_schema(executor)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"model facts via DMV skipped: {e}")
    return schema, roles, calc_groups, tmsl_model


def _handle_find_clones(args: Dict[str, Any]) -> Dict[str, Any]:
    """F-15: copy-pasted DAX by normalised token structure (see ``core.dax.clone_detector``).

    Scans every measure and, when the TMSL snapshot is available, calculation items and
    calculated columns. Read-only; the UDF proposals are for ``15_DAX_Refactor extract_udf``
    (whole measure bodies) or by hand (fragments, calc items).
    """
    executor = connection_state.query_executor
    if not executor:
        return ErrorHandler.handle_manager_unavailable("query_executor")
    enum = executor.enumerate_measures_tom()
    if not enum.get("success"):
        return {
            "success": False,
            "error": enum.get("error", "Failed to enumerate measures"),
            "error_type": enum.get("error_type", "enumerate_failed"),
        }
    wanted_tables = {str(t).lower() for t in (args.get("tables") or [])}
    objects: List[Dict[str, Any]] = []
    measure_names: set = set()
    for r in enum.get("rows", []) or []:
        name = r.get("Name", "")
        if name:
            measure_names.add(str(name).lower())
        if not r.get("Expression"):
            continue
        if wanted_tables and str(r.get("Table", "")).lower() not in wanted_tables:
            continue
        objects.append(
            {
                "kind": "measure",
                "table": r.get("Table", ""),
                "name": name,
                "expression": r.get("Expression", "") or "",
            }
        )
    other_kinds = 0
    _schema, _roles, _groups, tmsl_model = _model_facts(executor)
    if tmsl_model is not None:
        try:
            from core.dax.analyzer.tmsl_objects import extract_dax_objects

            for o in extract_dax_objects(tmsl_model):
                if o.kind not in ("calc_item", "calc_column"):
                    continue
                if wanted_tables and o.table.lower() not in wanted_tables:
                    continue
                objects.append(
                    {"kind": o.kind, "table": o.table, "name": o.name, "expression": o.expression}
                )
                other_kinds += 1
        except Exception as e:  # noqa: BLE001
            logger.debug(f"find_clones: calc items / columns skipped: {e}")

    from core.dax.clone_detector import MIN_UDF_COMPATIBILITY_LEVEL, find_clones
    from core.dax.udf_support import read_compatibility_level, read_function_names

    def _num(key: str, default, cast):
        v = args.get(key)
        return cast(v) if v is not None else default

    try:
        res = find_clones(
            objects,
            min_tokens=_num("min_tokens", 12, int),
            min_similarity=_num("min_similarity", 0.85, float),
            min_group_size=_num("min_group_size", 2, int),
            include_fragments=bool(args.get("include_fragments", True)),
            include_near=bool(args.get("include_near", True)),
            measure_names=measure_names,
            max_groups=_num("max_groups", 50, int),
        )
    except (TypeError, ValueError) as e:
        return error_envelope("invalid_argument", f"invalid find_clones argument: {e}")

    cl = read_compatibility_level(executor)
    fns = read_function_names(executor)
    out: Dict[str, Any] = {
        "success": True,
        "measures_scanned": len(measure_names) if not wanted_tables else None,
        "other_objects_scanned": other_kinds,
        **res,
        "udf": {
            "compatibility_level": cl,
            "required_compatibility_level": MIN_UDF_COMPATIBILITY_LEVEL,
            "supported": (cl >= MIN_UDF_COMPATIBILITY_LEVEL) if cl is not None else None,
            "existing_functions": len(fns) if fns is not None else None,
        },
        "next_step": (
            "A group with extractable_by_transform=true can be applied with 15_DAX_Refactor "
            "preview/apply transform=extract_udf args={measures:[...], function_name:...}; the "
            "apply is value-verified at the grand total - sweep the real grains as well."
        ),
    }
    if cl is not None and cl < MIN_UDF_COMPATIBILITY_LEVEL:
        out["udf"]["note"] = (
            "UDF proposals need a compatibility-level upgrade (irreversible): propose it as a "
            "separate, explicitly approved change, or use the calc-group candidates."
        )
    if not res.get("groups"):
        out["note"] = "no clone groups at these thresholds (lower min_tokens / min_similarity)"
    return out


def _handle_explain(args: Dict[str, Any]) -> Dict[str, Any]:
    """F-33: structured explanation of a measure (``core.dax.explain``) - structure, no prose.

    ``include_values=true`` additionally evaluates the measure's VARs in a filter context through
    the existing ``09_Debug_Operations step_variables`` machinery (``filters`` / ``page_name`` +
    ``visual_id`` / ``visual_name``); it runs queries, so it is opt-in.
    """
    if not (args.get("measure_name") or args.get("expression")):
        return error_envelope(
            "missing_argument", "explain needs measure_name (or an expression) to explain"
        )
    resolution = _resolve_measure_input(args)
    if resolution.get("error"):
        return resolution["error"]
    measure_name = resolution.get("measure")
    measure_table = resolution.get("table")
    expression = resolution.get("expression") if measure_name else args.get("expression")
    if not expression or not str(expression).strip():
        return error_envelope("missing_argument", "the measure has no expression to explain")

    executor = connection_state.query_executor
    schema, roles, calc_groups, _tmsl = _model_facts(executor) if executor else (None, [], [], None)
    try:  # the measure names let a bare [Name] resolve as a measure (the TMSL schema may lack them)
        from core.dax.analyzer.models import ModelSchema

        enum = executor.enumerate_measures_tom() if executor else None
        names = {
            str(r.get("Name", "")).strip().lower()
            for r in ((enum or {}).get("rows") or [])
            if isinstance(r, dict) and r.get("Name")
        }
        if names:
            schema = schema if schema is not None else ModelSchema()
            schema.measure_names = set(getattr(schema, "measure_names", None) or ()) | names
    except Exception as e:  # noqa: BLE001 - enrichment only
        logger.debug(f"explain: measure names skipped: {e}")

    from core.dax.explain import explain_expression

    try:
        out = explain_expression(
            str(expression),
            schema=schema,
            calc_groups=calc_groups,
            roles=roles,
            measure=measure_name,
            table=measure_table,
        )
    except Exception as e:  # noqa: BLE001 - never a stack trace for an odd expression
        logger.warning(f"explain failed: {e}", exc_info=True)
        return error_envelope("operation_failed", f"could not explain the expression: {e}")
    out["success"] = True
    out["expression_chars"] = len(str(expression))
    out["source"] = "model measure" if measure_name else "expression"

    if args.get("include_values"):
        if not measure_name:
            out["variable_values_error"] = (
                "include_values needs a model measure (measure_name): step_variables evaluates "
                "the measure's VARs in the model"
            )
        elif not out["variables"]:
            out["variable_values_error"] = "the measure declares no VAR"
        else:
            from server.handlers._debug.variables import _handle_step_variables

            sv_args: Dict[str, Any] = {"measure_name": measure_name, "table_name": measure_table}
            for key in ("filters", "page_name", "visual_id", "visual_name", "max_rows"):
                if args.get(key) is not None:
                    sv_args[key] = args[key]
            sv = _handle_step_variables(sv_args)
            if sv.get("success"):
                by_name = {st.get("var_name", "").lower(): st for st in sv.get("steps", [])}
                for v in out["variables"]:
                    st = by_name.get(str(v["name"]).lower())
                    if st is not None:
                        v["value"] = {
                            k: st[k]
                            for k in ("value", "row_count", "rows", "truncated", "error")
                            if k in st
                        }
                out["variable_values"] = {
                    "evaluation_context": sv.get("evaluation_context"),
                    "filter_context": sv.get("filter_context"),
                    "slowest_variable": sv.get("slowest_variable"),
                }
            else:
                out["variable_values_error"] = sv.get("error") or "step_variables failed"
    return out


def _strip_function_header(text: str) -> str:
    """``FUNCTION Name = (x) => …`` -> ``(x) => …`` (the DEFINE line adds the header)."""
    from core.dax.tokenizer import TokenType

    toks = _code_tokens(text)
    if toks and toks[0].value.upper() == "FUNCTION":
        for t in toks:
            if t.type == TokenType.OPERATOR and t.value == "=":
                return text[t.end :].strip()
    return text


def _format_debug_steps_friendly(expression: str, steps) -> str:
    """Format debug steps in a user-friendly way"""
    lines = []

    lines.append("=" * 80)
    lines.append("🔍 DAX CONTEXT DEBUGGER - STEP-BY-STEP EXECUTION ANALYSIS")
    lines.append("=" * 80)
    lines.append("")

    lines.append("📝 Your DAX Expression:")
    lines.append("-" * 80)
    lines.append(expression)
    lines.append("")

    lines.append("=" * 80)
    lines.append(f"🎯 Found {len(steps)} Context Transitions")
    lines.append("=" * 80)
    lines.append("")

    lines.append("💡 What are Context Transitions?")
    lines.append(
        "   Context transitions occur when DAX switches between filter context and row context."
    )
    lines.append(
        "   Understanding these is crucial for writing efficient DAX and avoiding common pitfalls."
    )
    lines.append("")

    for step in steps:
        lines.append("-" * 80)
        lines.append(f"Step {step.step_number} of {len(steps)}")
        lines.append("-" * 80)
        lines.append("")

        # Show code fragment with pointer
        lines.append("📍 Execution Point:")
        lines.append(f"   {step.code_fragment}")
        lines.append("   ⬆️  The ▶ arrow shows exactly where DAX is evaluating")
        lines.append("")

        # Context information
        lines.append("🔄 Context Information:")

        if step.row_context:
            lines.append(f"   • Row Context: {step.row_context.get('type', 'Active')}")
            if "function" in step.row_context:
                lines.append(f"     Function: {step.row_context['function']}")
            lines.append("     ℹ️  Row context = iterating over rows of a table")
        else:
            lines.append("   • Row Context: None")
            lines.append("     ℹ️  No row iteration at this point")

        if step.filter_context:
            lines.append(f"   • Filter Context: {len(step.filter_context)} active filters")
            for table, filters in step.filter_context.items():
                lines.append(f"     - {table}: {filters}")
        else:
            lines.append("   • Filter Context: Inherited from visual/slicer")
            lines.append("     ℹ️  Using the filter context from your report")

        lines.append("")

        # Explanation
        lines.append("📖 What's Happening:")
        # Wrap explanation text
        explanation_lines = _wrap_text(step.explanation, width=76, indent=3)
        lines.extend(explanation_lines)
        lines.append("")

        # Performance hint
        if step.execution_time_ms:
            lines.append(f"⏱️  Execution Time: {step.execution_time_ms:.2f}ms")
            lines.append("")

        # Visual separation between steps
        lines.append("")

    lines.append("=" * 80)
    lines.append("✅ ANALYSIS COMPLETE")
    lines.append("=" * 80)
    lines.append("")
    lines.append("💡 Key Takeaways:")
    lines.append(
        "   • Context transitions can impact performance - minimize unnecessary transitions"
    )
    lines.append("   • Measure references inside iterators cause row-by-row context transitions")
    lines.append(
        "   • CALCULATE modifies filter context and transitions from row to filter context"
    )
    lines.append("   • Use variables (VAR) to cache values and reduce repeated transitions")
    lines.append("")
    lines.append("📚 Need more details? Use format='report' for optimization suggestions!")
    lines.append("")

    return "\n".join(lines)


def _wrap_text(text: str, width: int = 80, indent: int = 0) -> list:
    """Wrap text to specified width with indentation"""
    import textwrap

    wrapper = textwrap.TextWrapper(
        width=width,
        initial_indent=" " * indent,
        subsequent_indent=" " * indent,
        break_long_words=False,
        break_on_hyphens=False,
    )

    return wrapper.wrap(text)


def handle_dax_intelligence(args: Dict[str, Any]) -> Dict[str, Any]:
    """Public entry — runs the analyzer then attaches the semantic-equivalence
    advisory to any response carrying a rewrite payload (audit H5)."""
    return _attach_semantic_warning(_handle_dax_intelligence_impl(args))


def _handle_dax_intelligence_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Unified DAX Intelligence Tool

    Combines validation, analysis, debugging, and dependency operations into a single tool.

    Modes (analysis_mode):
    - 'all' (DEFAULT): Runs ALL analysis modes - analyze + debug + report
    - 'analyze': Context transition analysis with anti-patterns
    - 'debug': Step-by-step debugging with friendly/steps output
    - 'report': Comprehensive report with optimization + profiling

    Dependency operations (operation):
    - 'dependencies': Measure dependency tree with Mermaid diagram
    - 'impact': Impact analysis — what depends on a measure
    - 'export': Export all measures to CSV

    Smart measure detection: Automatically fetches measure expressions if a measure name is provided.
    Auto-skips validation for auto-fetched measures (already in model, must be valid).
    One advice engine: every anti-pattern / recommendation block is a view of the unified rule
    analyzer's findings (the regex "research article" engine was removed, review D-01).
    """
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    # Dependency operations: delegate early before expression is required
    operation = args.get("operation")
    if operation == "analyze":
        operation = None  # documented default op (review D-25)
    if operation in ("dependencies", "impact", "export"):
        from server.handlers._internal.dependencies_helpers import handle_dax_operations

        return handle_dax_operations(args)

    if operation == "list_udfs":
        return _handle_list_udfs(args)

    if operation == "lint_model":
        return _handle_lint_model(args)

    if operation == "expand_measure":
        return _handle_expand_measure(args)

    if operation == "find_clones":
        return _handle_find_clones(args)

    if operation == "explain":
        return _handle_explain(args)

    expression = args.get("expression")
    analysis_mode = args.get(
        "analysis_mode", "all"
    )  # Default to 'all' mode (analyze + debug + report)
    skip_validation = args.get("skip_validation", False)

    if not expression and not args.get("measure_name"):
        return error_envelope(
            "missing_argument",
            "expression (DAX) or measure_name is required. For dependency ops use operation=dependencies|impact|export.",
        )

    # Measure resolution (review D-04): an explicit measure_name, or an
    # expression that EXACTLY names a measure, analyses that measure; DAX is
    # analysed verbatim; a fuzzy match is never substituted silently.
    warnings = []
    resolution = _resolve_measure_input(args)
    if resolution.get("error"):
        return resolution["error"]
    measure_name = resolution.get("measure")
    measure_table = resolution.get("table")
    if measure_name:
        expression = resolution.get("expression") or ""

    # Step 1: Validate DAX syntax (unless explicitly skipped)
    # IMPORTANT: Auto-skip validation for auto-fetched measures (they're already in the model and must be valid)
    if measure_name and not skip_validation:
        logger.info(
            f"Auto-fetched measure '{measure_name}' - skipping validation (already in model)"
        )
        skip_validation = True

    validation_result = {"valid": True, "message": "Validation skipped"}
    if not skip_validation:
        is_valid, error_msg = _validate_dax_syntax(expression)
        validation_result = {
            "valid": is_valid,
            "message": "DAX syntax is valid" if is_valid else f"DAX syntax error: {error_msg}",
        }

        if not is_valid:
            # Review D-14: an invalid expression is a failure, not success+None.
            return {
                "success": False,
                "error": f"DAX syntax error: {error_msg}",
                "error_type": "invalid_dax",
                "validation": validation_result,
                "analysis": None,
                "note": f"{analysis_mode.title()} skipped due to syntax errors. Fix the syntax errors first.",
            }

    # Step 2: Route to appropriate analysis mode
    try:
        if analysis_mode == "all":
            # ── Single unified analyzer replaces 3 overlapping analyzers ──
            from core.dax import DaxContextAnalyzer, DaxContextDebugger
            from core.dax.analysis_pipeline import (
                run_context_analysis,
                run_vertipaq_analysis,
                run_call_tree,
            )

            analyzer = DaxContextAnalyzer()
            debugger = DaxContextDebugger()

            # 1. Context transitions (for annotated code + transition list)
            result_analyze, _ = run_context_analysis(expression)
            if result_analyze is None:
                result_analyze = analyzer.analyze_context_transitions(expression)
            annotated_dax = analyzer.format_dax_with_annotations(
                expression, result_analyze.transitions
            )

            # 2. VertiPaq column analysis
            vertipaq_analysis = run_vertipaq_analysis(expression, connection_state)

            # 3. Unified analyzer — single pass over the JSON + Python rule set
            unified_result = None
            try:
                from core.dax.analyzer.unified_analyzer import get_shared_analyzer
                from core.dax.analyzer.models import AnalysisContext
                from core.dax.analyzer.schema_builder import build_model_schema

                ctx = AnalysisContext(vertipaq_data=vertipaq_analysis)
                # Schema-aware enrichment: lets rules resolve measure-vs-column
                # ambiguity and reason about relationship topology / RLS.
                # build_model_schema never raises; degrades to static-only.
                try:
                    ctx.schema = build_model_schema(
                        getattr(connection_state, "query_executor", None)
                    )
                except Exception as _se:
                    logger.debug(f"Schema enrichment skipped: {_se}")
                unified_result = get_shared_analyzer().analyze(expression, ctx)
            except Exception as e:
                logger.warning(f"Unified analyzer failed: {e}")
                warnings.append(f"Unified analyzer failed: {e}")

            # 4. Code rewriter + improvements. One advice engine (2026-10-03 review D-01): the
            # anti_patterns block is a view of the unified analyzer's own findings.
            anti_patterns = (
                unified_result.to_anti_patterns_format()
                if unified_result is not None
                else {
                    "success": True,
                    "patterns_detected": 0,
                    "pattern_matches": {},
                    "recommendations": [],
                    "articles": [],
                }
            )
            improvements = debugger.generate_improved_dax(
                dax_expression=expression,
                context_analysis=result_analyze,
                vertipaq_analysis=vertipaq_analysis,
                connection_state=connection_state,
            )

            # 5. Call tree
            call_tree_data = run_call_tree(expression, connection_state)

            # ── Build response from unified result ──
            if unified_result:
                issues_list = []
                for issue in unified_result.issues:
                    d = {
                        "rule_id": issue.rule_id,
                        "severity": issue.severity,
                        "title": issue.title,
                        "desc": issue.description,
                        "fix": issue.fix_suggestion,
                    }
                    if issue.flagged_function:
                        d["function"] = issue.flagged_function
                        if issue.se_classification:
                            d["se"] = issue.se_classification
                        if issue.callback_risk and issue.callback_risk != "none":
                            d["callback_risk"] = issue.callback_risk
                    if issue.estimated_improvement:
                        d["improvement"] = issue.estimated_improvement
                    issues_list.append(d)

                issues_data = {
                    "total": unified_result.total_issues,
                    "health_score": unified_result.health_score,
                    "items": issues_list,
                }
                if unified_result.suppressed:
                    issues_data["suppressed"] = unified_result.suppressed
            else:
                issues_data = {"total": 0, "health_score": None, "items": []}

            response = {
                "success": True,
                "validation": validation_result,
                "mode": "all",
                "annotated_dax_code": {
                    "code": annotated_dax,
                    "legend": {
                        "🔄": "Iterator (row context)",
                        "📊": "Measure ref (implicit CALCULATE)",
                        "⚡": "Explicit CALCULATE/CALCULATETABLE",
                        "🔴": "HIGH impact",
                        "🟡": "MEDIUM impact",
                        "🟢": "LOW impact",
                    },
                },
                "analysis_summary": {
                    "complexity": result_analyze.complexity_score,
                    "nesting": result_analyze.max_nesting_level,
                    "transitions": len(result_analyze.transitions),
                    "anti_patterns": anti_patterns.get("patterns_detected", 0),
                    "improvements": improvements.get("improvements_count", 0),
                },
                "context_transitions": [
                    {
                        "fn": t.function,
                        "line": t.line,
                        "type": t.type.value,
                        "impact": t.performance_impact.value,
                        "why": t.explanation,
                    }
                    for t in result_analyze.transitions
                ],
                "issues": issues_data,
                "anti_patterns": {
                    "patterns_detected": anti_patterns.get("patterns_detected", 0),
                    "pattern_matches": anti_patterns.get("pattern_matches", {}),
                    "recommendations": anti_patterns.get("recommendations", []),
                },
                "improvements": {
                    "has_improvements": improvements.get("has_improvements", False),
                    "count": improvements.get("improvements_count", 0),
                    "details": improvements.get("improvements", []),
                    "rewriter_draft": improvements.get("rewriter_draft"),
                },
                "call_tree": call_tree_data.get("visualization", "") if call_tree_data else None,
            }

            # Optional sections
            if vertipaq_analysis and vertipaq_analysis.get("columns_analyzed", 0) > 0:
                response["vertipaq"] = {
                    "columns": vertipaq_analysis.get("column_analysis", {}),
                    "total_size_mb": vertipaq_analysis.get("total_size_mb", 0),
                    "high_cardinality": vertipaq_analysis.get("high_cardinality_columns", []),
                }
            if measure_name:
                response["measure"] = measure_name
                response["resolved_measure"] = measure_name
                response["table"] = measure_table
            if warnings:
                response["warnings"] = warnings

            # Articles: collect from unified result issues only
            if unified_result:
                seen_urls = set()
                articles = []
                for issue in unified_result.issues:
                    for ref in issue.references or []:
                        url = ref.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            articles.append({"title": ref.get("title", ""), "url": url})
                if articles:
                    response["articles"] = articles

            response = {k: v for k, v in response.items() if v is not None}
            return response

        elif analysis_mode == "analyze":
            # Context transition analysis with anti-pattern detection
            from core.dax import DaxContextAnalyzer, DaxContextDebugger

            analyzer = DaxContextAnalyzer()
            debugger = DaxContextDebugger()

            result = analyzer.analyze_context_transitions(expression)

            # Generate annotated DAX code for visual display
            annotated_dax = analyzer.format_dax_with_annotations(expression, result.transitions)

            # Get VertiPaq analysis for comprehensive optimization
            vertipaq_analysis = None
            try:
                from core.dax.vertipaq_analyzer import VertiPaqAnalyzer

                vertipaq = VertiPaqAnalyzer(connection_state)
                vertipaq_analysis = vertipaq.analyze_dax_columns(expression)
                if not vertipaq_analysis.get("success"):
                    logger.warning(
                        f"VertiPaq analysis failed: {vertipaq_analysis.get('error', 'Unknown error')}"
                    )
            except Exception as e:
                logger.warning(f"VertiPaq analysis not available: {e}")

            # ONE unified-analyzer run (review D-11): best_practices_analysis and
            # static_analysis used to be the same engine's issues twice.
            best_practices_result = None
            rules_analysis = None
            anti_patterns = {
                "success": False,
                "patterns_detected": 0,
                "pattern_matches": {},
                "recommendations": [],
                "articles": [],
                "error": "unified analyzer unavailable",
                "error_type": "analyzer_unavailable",
            }
            try:
                from core.dax.analyzer.models import AnalysisContext
                from core.dax.analyzer.schema_builder import build_model_schema
                from core.dax.analyzer.unified_analyzer import get_shared_analyzer

                ctx = AnalysisContext(vertipaq_data=vertipaq_analysis)
                try:
                    ctx.schema = build_model_schema(
                        getattr(connection_state, "query_executor", None)
                    )
                except Exception as _se:
                    logger.debug(f"Schema enrichment skipped: {_se}")
                unified = get_shared_analyzer().analyze(expression, ctx)
                anti_patterns = unified.to_anti_patterns_format()
                best_practices_result = unified.to_best_practices_format()
                rules_analysis = {
                    "health_score": unified.health_score,
                    "issue_count": unified.total_issues,
                    "note": "Same engine as best_practices_analysis — issues are listed there once.",
                }
            except Exception as e:
                logger.warning(f"Unified analysis not available: {e}")
                warnings.append(f"Unified analyzer failed: {e}")

            # Generate specific improvements and new DAX code
            improvements = debugger.generate_improved_dax(
                dax_expression=expression,
                context_analysis=result,
                vertipaq_analysis=vertipaq_analysis,
                connection_state=connection_state,  # Pass for output validation
            )

            # Combine all articles
            all_articles = []
            if anti_patterns.get("articles"):
                all_articles.extend(anti_patterns["articles"])
            if best_practices_result and best_practices_result.get("articles_referenced"):
                all_articles.extend(best_practices_result["articles_referenced"])

            # Deduplicate
            seen_urls = set()
            unique_articles = []
            for article in all_articles:
                url = article.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_articles.append(article)

            response = {
                "success": True,
                "validation": validation_result,
                "mode": "analyze",
                "annotated_dax_code": {
                    "code": annotated_dax,
                    "legend": {
                        "🔄": "Iterator (row context)",
                        "📊": "Measure ref (implicit CALCULATE)",
                        "⚡": "Explicit CALCULATE/CALCULATETABLE",
                        "🔴": "HIGH impact",
                        "🟡": "MEDIUM impact",
                        "🟢": "LOW impact",
                    },
                },
                "analysis": result.to_dict() if hasattr(result, "to_dict") else result,
            }

            # Include measure info if auto-fetched
            if measure_name:
                response["measure_info"] = {
                    "name": measure_name,
                    "table": measure_table,
                    "note": f"Analysed the model expression of measure {measure_name!r} (table {measure_table!r})",
                }

            # Include best practices analysis
            response["best_practices_analysis"] = (
                best_practices_result
                if best_practices_result
                else {"note": "Best practices analysis not available"}
            )

            # ALWAYS include anti-pattern detection results (even if failed or no patterns found)
            response["anti_patterns"] = {
                "success": anti_patterns.get("success", False),
                "patterns_detected": anti_patterns.get("patterns_detected", 0),
                "pattern_matches": anti_patterns.get("pattern_matches", {}),
                "recommendations": anti_patterns.get("recommendations", []),
                "articles": anti_patterns.get("articles", []),
                "error": anti_patterns.get("error") if not anti_patterns.get("success") else None,
            }

            # Include static analysis (rules engine)
            response["static_analysis"] = (
                rules_analysis if rules_analysis else {"note": "Static analysis not available"}
            )

            # Include VertiPaq analysis
            response["vertipaq_analysis"] = (
                vertipaq_analysis
                if vertipaq_analysis and vertipaq_analysis.get("success")
                else {
                    "note": "VertiPaq analysis not available",
                    "reason": (
                        vertipaq_analysis.get("error")
                        if vertipaq_analysis
                        else "Analysis failed or not connected to model"
                    ),
                }
            )

            # Include specific improvements with rewriter draft
            if improvements.get("has_improvements"):
                response["improvements"] = {
                    "summary": improvements.get("summary"),
                    "count": improvements.get("improvements_count"),
                    "details": improvements.get("improvements"),
                    "original_code": improvements.get("original_code"),
                    "rewriter_draft": improvements.get("rewriter_draft"),
                }

            response["optimized_measure"] = {
                "rewriter_draft": improvements.get("rewriter_draft"),
                "has_optimization_opportunities": improvements.get("has_improvements", False),
                "opportunities_count": improvements.get("improvements_count", 0),
            }

            # Only include articles when patterns were actually detected
            matched_articles = [a for a in unique_articles if a.get("matched_patterns")]
            if matched_articles:
                response["articles_referenced"] = {
                    "total": len(matched_articles),
                    "articles": matched_articles,
                }

            if warnings:
                response["warnings"] = warnings

            return response

        elif analysis_mode == "debug":
            # Step-by-step debugging
            from core.dax import DaxContextDebugger

            debugger = DaxContextDebugger()

            output_format = args.get("output_format", "friendly")
            breakpoints = args.get("breakpoints")

            steps = debugger.step_through(dax_expression=expression, breakpoints=breakpoints)

            if not steps:
                result = {
                    "success": True,
                    "validation": validation_result,
                    "message": "✅ No context transitions detected in this DAX expression.",
                    "explanation": "This is a simple expression without CALCULATE, iterators, or measure references that would cause context transitions.",
                    "total_steps": 0,
                    "mode": "debug",
                }
                if measure_name:
                    result["measure_info"] = {
                        "name": measure_name,
                        "table": measure_table,
                        "note": f"Analysed the model expression of measure {measure_name!r} (table {measure_table!r})",
                    }
                if warnings:
                    result["warnings"] = warnings
                return result

            # Format output based on requested format
            if output_format == "friendly":
                formatted_output = _format_debug_steps_friendly(expression, steps)
                # Add validation header
                if validation_result["valid"]:
                    validation_header = "✅ DAX SYNTAX VALIDATION: PASSED\n\n"
                    formatted_output = validation_header + formatted_output

                # Add measure info header if auto-fetched
                if measure_name:
                    measure_header = f"📊 Analyzing measure: '{measure_table}'[{measure_name}]\n\n"
                    formatted_output = measure_header + formatted_output

                result = {
                    "success": True,
                    "validation": validation_result,
                    "formatted_output": formatted_output,
                    "total_steps": len(steps),
                    "mode": "debug",
                }
                if measure_name:
                    result["measure_info"] = {
                        "name": measure_name,
                        "table": measure_table,
                        "note": f"Analysed the model expression of measure {measure_name!r} (table {measure_table!r})",
                    }
                if warnings:
                    result["warnings"] = warnings
                return result
            else:
                # 'steps' format - raw data
                steps_dict = [
                    {
                        "step_number": step.step_number,
                        "code_fragment": step.code_fragment,
                        "filter_context": step.filter_context,
                        "row_context": step.row_context,
                        "intermediate_result": step.intermediate_result,
                        "explanation": step.explanation,
                        "execution_time_ms": step.execution_time_ms,
                    }
                    for step in steps
                ]

                result = {
                    "success": True,
                    "validation": validation_result,
                    "debug_steps": steps_dict,
                    "total_steps": len(steps_dict),
                    "mode": "debug",
                }
                if measure_name:
                    result["measure_info"] = {
                        "name": measure_name,
                        "table": measure_table,
                        "note": f"Analysed the model expression of measure {measure_name!r} (table {measure_table!r})",
                    }
                if warnings:
                    result["warnings"] = warnings
                return result

        elif analysis_mode == "report":
            # Comprehensive debug report with all enhancements
            from core.dax import DaxContextDebugger

            debugger = DaxContextDebugger()

            include_profiling = args.get("include_profiling", True)
            include_optimization = args.get("include_optimization", True)

            result = debugger.generate_debug_report(
                expression,
                include_profiling=include_profiling,
                include_optimization=include_optimization,
                connection_state=connection_state,  # Pass connection state for enhanced analysis
            )

            response = {
                "success": True,
                "validation": validation_result,
                "report": result,
                "mode": "report",
            }

            # Extract optimized code from report if available
            # The report already includes the optimized measure in the text
            # Let's also provide it in structured format
            try:
                # Try to get the improvements from the report generation
                from core.dax import DaxContextAnalyzer

                analyzer = DaxContextAnalyzer()
                result_analyze = analyzer.analyze_context_transitions(expression)

                # Run static analysis rules engine
                try:
                    from core.dax.dax_rules_engine import DaxRulesEngine

                    rules_engine = DaxRulesEngine()
                    response["static_analysis"] = rules_engine.analyze(expression)
                except Exception as rules_err:
                    logger.warning(f"Rules engine not available in report mode: {rules_err}")

                from core.dax import DaxContextDebugger

                debugger_temp = DaxContextDebugger()

                # Get VertiPaq analysis for comprehensive optimization
                vertipaq_analysis = None
                try:
                    from core.dax.vertipaq_analyzer import VertiPaqAnalyzer

                    vertipaq = VertiPaqAnalyzer(connection_state)
                    vertipaq_analysis = vertipaq.analyze_dax_columns(expression)
                except Exception:
                    pass

                improvements = debugger_temp.generate_improved_dax(
                    dax_expression=expression,
                    context_analysis=result_analyze,
                    vertipaq_analysis=vertipaq_analysis,
                )

                response["optimized_measure"] = {
                    "rewriter_draft": improvements.get("rewriter_draft"),
                    "has_optimization_opportunities": improvements.get("has_improvements", False),
                    "opportunities_count": improvements.get("improvements_count", 0),
                }
            except Exception as e:
                logger.warning(f"Could not extract optimized measure: {e}")

            if measure_name:
                response["measure_info"] = {
                    "name": measure_name,
                    "table": measure_table,
                    "note": f"Analysed the model expression of measure {measure_name!r} (table {measure_table!r})",
                }
            if warnings:
                response["warnings"] = warnings
            return response
        else:
            return error_envelope(
                "invalid_argument",
                f"Invalid analysis_mode: {analysis_mode}. Use 'all' (default), 'analyze', 'debug', or 'report'.",
            )

    except ImportError as ie:
        logger.error(f"Import error: {ie}", exc_info=True)
        return {
            "success": False,
            "validation": validation_result,
            "error": "DAX Intelligence components not available. This is an internal error.",
            "error_type": "import_error",
        }
    except Exception as e:
        logger.error(f"Error in DAX Intelligence ({analysis_mode} mode): {e}", exc_info=True)
        return error_envelope(
            "operation_failed",
            f"Error in DAX Intelligence ({analysis_mode} mode): {str(e)}",
            validation=validation_result,
        )


def register_dax_handlers(registry):
    """Register unified DAX Intelligence handler (Tool 03)"""
    from server.tool_schemas import TOOL_SCHEMAS

    tools = [
        ToolDefinition(
            name="05_DAX_Intelligence",
            description=(
                "Static DAX analysis (no execution): context transitions, anti-patterns, call "
                "tree, optimized rewrite (analyze), dependencies, impact, export (CSV), "
                "list_udfs, lint_model (every measure, worst first), expand_measure (inline "
                "deps as DEFINE MEASURE), find_clones (copy-pasted DAX), explain (structured "
                "measure explanation). Pass expression or measure_name (exact names only; "
                "near-misses are suggested). Runtime timings: 09_Debug_Operations; model-wide "
                "BPA: 06_Analysis_Operations; queryable graph: 16_Dependency_Graph."
            ),
            guide_notes="DAX expression-level analysis: context transitions, anti-patterns, VertiPaq, call tree, optimized code, dependency trees, impact analysis, export, lint_model, expand_measure. find_clones = copy-pasted DAX across measures / calc items / calc columns by token structure, with UDF signature or calc-group-item proposals (apply with 15_DAX_Refactor extract_udf); explain = structured explanation of a measure (call tree with argument roles, filter modifiers in order, context transitions, iterators, VARs with uses, calc-group / RLS notes; include_values adds VAR values via step_variables). Pass DAX as expression, or measure_name to analyse a model measure (an expression that exactly names a measure also resolves; fuzzy matches are only suggested). Model-read-only; side effects: export writes a CSV, dependencies with html=true writes (and with auto_open=true opens) an HTML file. For live trace+SE/FE timing on a measure use 09_Debug_Operations:visual; for model-wide structural BPA rules use 06_Analysis_Operations; for structured queryable model+report dependency graph (find_unused/find_cycles/get_impact + JSONPath) use 16_Dependency_Graph.",
            handler=handle_dax_intelligence,
            input_schema=TOOL_SCHEMAS.get("dax_intelligence", {}),
            category="dax",
            sort_order=50,  # 05 = DAX Intelligence
            annotations={
                # Never writes the model. export / dependencies(html=true) only write
                # local output files, the same convention as 04_Run_DAX export_path,
                # so the tool stays read-only for op_classification / read_only_mode.
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        ),
    ]

    for tool in tools:
        registry.register(tool)

    logger.info(f"Registered {len(tools)} DAX Intelligence handler (Tool 05)")
