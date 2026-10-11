"""Modern-model BPA rules (2026-10-03 review D-28).

The TE-style rule set (``config/bpa_rules_comprehensive.json`` / ``core/bpa.json``) knows nothing
about the 2025-26 model objects. These rules read the TMSL model dict directly - they need members
the expression evaluator's node layer does not expose (UDF signatures, calendar column groups,
field-parameter ``NAMEOF`` targets, linguistic metadata) - and are attached to every
:class:`~core.analysis.bpa_analyzer.BPAAnalyzer` as built-in rules with a ``python_check``.

  UDF_MISSING_DESCRIPTION             user-defined function without a description
  UDF_EXPR_PARAMETER_REUSED           ``expr`` parameter used more than once (re-evaluated per use)
  UDF_NAME_LEAKS_SECURED_OBJECT       function name / description names an RLS-secured table
  CALENDAR_NO_TIME_UNIT_MAPPING       calendar object without a time-unit column mapping
  WEEK_TI_WITHOUT_CALENDAR            week time-intelligence function but no calendar object
  CALCGROUP_DUPLICATE_PRECEDENCE      two calculation groups share a precedence
  CALCGROUP_NO_SELECTION_EXPRESSIONS  no (multiple-or-empty-)selection expression defined
  CALCITEM_NO_FORMAT_STRING           calculation item without a format string expression
  FIELD_PARAMETER_MISSING_FIELD       field parameter ``NAMEOF`` target does not exist
  FIELD_PARAMETER_HIDDEN_FIELD        field parameter ``NAMEOF`` target is hidden
  AI_TABLE_NO_DESCRIPTION             visible table without a description (Copilot / Q&A readiness)
  AI_COLUMN_NO_DESCRIPTION            visible column without a description
  AI_NO_SYNONYMS                      no linguistic metadata / synonyms on a model with visible objects
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# (object_type, object_name, table_name or None, details or None)
Hit = Tuple[str, str, Optional[str], Optional[str]]

_MAX_HITS_PER_RULE = 500

_WEEK_TI = (
    "TOTALWTD",
    "STARTOFWEEK",
    "ENDOFWEEK",
    "NEXTWEEK",
    "PREVIOUSWEEK",
    "OPENINGBALANCEWEEK",
    "CLOSINGBALANCEWEEK",
)
_WEEK_TI_RE = re.compile(r"\b(" + "|".join(_WEEK_TI) + r")\s*\(", re.IGNORECASE)
_NAMEOF_RE = re.compile(
    r"NAMEOF\s*\(\s*(?:'((?:[^']|'')+)'|([A-Za-z_][\w ]*))?\s*\[([^\]]+)\]\s*\)", re.I
)


@dataclass(frozen=True)
class ModernRule:
    id: str
    name: str
    category: str
    severity: int  # 1 info / 2 warning / 3 error
    description: str
    scope: str
    fix: str
    check: Callable[[Dict[str, Any]], List[Hit]]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    if isinstance(value, dict):
        return _text(value.get("expression"))
    return str(value)


def _tables(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [t for t in model.get("tables") or [] if isinstance(t, dict)]


def _visible_tables(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [t for t in _tables(model) if not t.get("isHidden") and not t.get("isPrivate")]


def _norm(name: Any) -> str:
    return " ".join(str(name or "").lower().split())


# ───────────────────────────── UDF hygiene ─────────────────────────────


def _udfs(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [f for f in model.get("functions") or [] if isinstance(f, dict)]


def _udf_missing_description(model: Dict[str, Any]) -> List[Hit]:
    return [
        ("Function", str(f.get("name", "")), None, None)
        for f in _udfs(model)
        if not _text(f.get("description")).strip()
    ]


def _udf_expr_parameter_reused(model: Dict[str, Any]) -> List[Hit]:
    from core.dax.analyzer.rules.udf_rules import udf_signature
    from core.dax.dax_reference_parser import get_lexer
    from core.dax.tokenizer import TokenType

    out: List[Hit] = []
    for f in _udfs(model):
        name = str(f.get("name", ""))
        body = _text(f.get("expression"))
        text = body if body.lstrip().upper().startswith("FUNCTION") else f"FUNCTION {name} = {body}"
        sig = udf_signature(text)
        if not sig:
            continue
        code = get_lexer().tokenize_code(text)
        # parameter names, positionally, from the header
        from core.dax.analyzer.rules.udf_rules import (
            _find_signature,
            _param_name,
            _split_parameters,
        )

        header = _find_signature(code)
        if header is None:
            continue
        open_idx, close_idx, _ = header
        params = [
            (_param_name(run), mode)
            for run, mode in zip(_split_parameters(code, open_idx, close_idx), sig[1])
        ]
        has_var = any(
            t.type == TokenType.KEYWORD and t.value.upper() == "VAR" for t in code[close_idx:]
        )
        reused = []
        for pname, mode in params:
            if mode != "expr" or not pname:
                continue
            uses = sum(
                1
                for t in code[close_idx + 1 :]
                if t.type == TokenType.IDENTIFIER and t.value.upper() == pname.upper()
            )
            if uses > 1:
                reused.append(f"{pname} ({uses}x)")
        if reused:
            out.append(
                (
                    "Function",
                    name,
                    None,
                    "expr parameter(s) used more than once: "
                    + ", ".join(reused)
                    + (" (a VAR exists - confirm it caches them)" if has_var else ""),
                )
            )
    return out


def _udf_name_leaks_secured_object(model: Dict[str, Any]) -> List[Hit]:
    secured: Set[str] = set()
    for role in model.get("roles") or []:
        for tp in (role.get("tablePermissions") or []) if isinstance(role, dict) else []:
            if _text(tp.get("filterExpression")).strip():
                secured.add(_norm(tp.get("name")))
    secured.discard("")
    if not secured:
        return []
    out: List[Hit] = []
    for f in _udfs(model):
        hay = _norm(f"{f.get('name', '')} {_text(f.get('description'))}")
        hit = next((s for s in sorted(secured) if s in hay), None)
        if hit:
            out.append(
                (
                    "Function",
                    str(f.get("name", "")),
                    None,
                    f"mentions the RLS-secured table '{hit}'",
                )
            )
    return out


# ───────────────────────────── calendars ───────────────────────────────


def _calendar_no_time_unit_mapping(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for t in _tables(model):
        for cal in t.get("calendars") or []:
            if not isinstance(cal, dict):
                continue
            groups = cal.get("calendarColumnGroups") or cal.get("columnGroups") or []
            mapped = [
                g
                for g in groups
                if isinstance(g, dict) and (g.get("timeUnit") or g.get("timeunit"))
            ]
            if not mapped:
                out.append(("Calendar", str(cal.get("name", "")), str(t.get("name", "")), None))
    return out


def _week_ti_without_calendar(model: Dict[str, Any]) -> List[Hit]:
    if any(t.get("calendars") for t in _tables(model)):
        return []
    out: List[Hit] = []
    for t in _tables(model):
        for m in t.get("measures") or []:
            if isinstance(m, dict) and _WEEK_TI_RE.search(_text(m.get("expression"))):
                out.append(("Measure", str(m.get("name", "")), str(t.get("name", "")), None))
        cg = t.get("calculationGroup")
        for item in (cg.get("calculationItems") or []) if isinstance(cg, dict) else []:
            if _WEEK_TI_RE.search(_text(item.get("expression"))):
                out.append(
                    ("CalculationItem", str(item.get("name", "")), str(t.get("name", "")), None)
                )
    return out


# ───────────────────────────── calculation groups ──────────────────────


def _calc_groups(model: Dict[str, Any]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    return [
        (t, t["calculationGroup"])
        for t in _tables(model)
        if isinstance(t.get("calculationGroup"), dict)
    ]


def _calcgroup_duplicate_precedence(model: Dict[str, Any]) -> List[Hit]:
    by_prec: Dict[Any, List[str]] = {}
    for t, cg in _calc_groups(model):
        if cg.get("precedence") is not None:
            by_prec.setdefault(cg.get("precedence"), []).append(str(t.get("name", "")))
    out: List[Hit] = []
    for prec, names in by_prec.items():
        if len(names) > 1:
            for n in names:
                out.append(
                    (
                        "CalculationGroup",
                        n,
                        n,
                        f"precedence {prec} is shared with: {', '.join(x for x in names if x != n)}",
                    )
                )
    return out


def _calcgroup_no_selection_expressions(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for t, cg in _calc_groups(model):
        if len(cg.get("calculationItems") or []) < 2:
            continue
        missing = [
            k
            for k in ("noSelectionExpression", "multipleOrEmptySelectionExpression")
            if not _text(cg.get(k)).strip()
        ]
        if missing:
            out.append(
                (
                    "CalculationGroup",
                    str(t.get("name", "")),
                    str(t.get("name", "")),
                    ", ".join(missing),
                )
            )
    return out


def _calcitem_no_format_string(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for t, cg in _calc_groups(model):
        for item in cg.get("calculationItems") or []:
            if isinstance(item, dict) and not _text(item.get("formatStringDefinition")).strip():
                out.append(
                    ("CalculationItem", str(item.get("name", "")), str(t.get("name", "")), None)
                )
    return out


# ───────────────────────────── field parameters ────────────────────────


def _field_parameter_targets(model: Dict[str, Any]):
    """``(table, table_name_ref, column_or_measure)`` of every NAMEOF in a calculated table."""
    for t in _tables(model):
        for p in t.get("partitions") or []:
            src = p.get("source") if isinstance(p, dict) else None
            if not isinstance(src, dict) or str(src.get("type", "")).lower() != "calculated":
                continue
            expr = _text(src.get("expression"))
            if "NAMEOF" not in expr.upper():
                continue
            for m in _NAMEOF_RE.finditer(expr):
                tname = (m.group(1) or m.group(2) or "").replace("''", "'").strip()
                yield t, tname, m.group(3).replace("]]", "]").strip()


def _resolve_field(model: Dict[str, Any], table: str, name: str):
    """The measure / column dict a NAMEOF reference points at, or None."""
    want = _norm(name)
    for t in _tables(model):
        if table and _norm(t.get("name")) != _norm(table):
            continue
        for m in t.get("measures") or []:
            if isinstance(m, dict) and _norm(m.get("name")) == want:
                return m, t
        for c in t.get("columns") or []:
            if isinstance(c, dict) and _norm(c.get("name")) == want:
                return c, t
    return None


def _field_parameter_missing_field(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for t, tname, name in _field_parameter_targets(model):
        if _resolve_field(model, tname, name) is None:
            ref = f"'{tname}'[{name}]" if tname else f"[{name}]"
            out.append(
                ("CalculatedTable", str(t.get("name", "")), None, f"NAMEOF target {ref} not found")
            )
    return out


def _field_parameter_hidden_field(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for t, tname, name in _field_parameter_targets(model):
        res = _resolve_field(model, tname, name)
        if res is not None and (res[0].get("isHidden") or res[1].get("isHidden")):
            ref = f"'{tname}'[{name}]" if tname else f"[{name}]"
            out.append(
                ("CalculatedTable", str(t.get("name", "")), None, f"NAMEOF target {ref} is hidden")
            )
    return out


# ───────────────────────────── AI / Copilot readiness ──────────────────


def _ai_table_no_description(model: Dict[str, Any]) -> List[Hit]:
    return [
        ("Table", str(t.get("name", "")), None, None)
        for t in _visible_tables(model)
        if not _text(t.get("description")).strip()
        and not isinstance(t.get("calculationGroup"), dict)
    ][:_MAX_HITS_PER_RULE]


def _ai_column_no_description(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for t in _visible_tables(model):
        for c in t.get("columns") or []:
            if (
                not isinstance(c, dict)
                or c.get("isHidden")
                or str(c.get("type", "")).lower() == "rownumber"
            ):
                continue
            if not _text(c.get("description")).strip():
                out.append(("Column", str(c.get("name", "")), str(t.get("name", "")), None))
                if len(out) >= _MAX_HITS_PER_RULE:
                    return out
    return out


def _ai_no_synonyms(model: Dict[str, Any]) -> List[Hit]:
    if not _visible_tables(model):
        return []
    for culture in model.get("cultures") or []:
        meta = culture.get("linguisticMetadata") if isinstance(culture, dict) else None
        if not meta:
            continue
        blob = json.dumps(meta, default=str) if not isinstance(meta, str) else meta
        if re.search(r"synonym|\"Terms\"|\"terms\"", blob, re.IGNORECASE):
            return []
    return [("Model", str(model.get("name") or "Model"), None, "no culture carries synonyms")]


MODERN_RULES: Tuple[ModernRule, ...] = (
    ModernRule(
        "UDF_MISSING_DESCRIPTION",
        "User-defined function without a description",
        "Maintenance",
        1,
        "A DAX user-defined function without a description is invisible to other authors and to Copilot.",
        "Function",
        "Add a /// doc comment / description to the function.",
        _udf_missing_description,
    ),
    ModernRule(
        "UDF_EXPR_PARAMETER_REUSED",
        "UDF expr parameter used more than once",
        "DAX Expressions",
        2,
        "An `expr` (lazy) parameter is re-evaluated at every use inside the function body: using it twice evaluates the caller's expression twice (canon AP-48 / UDF guidance).",
        "Function",
        "Capture the parameter once: VAR _v = param RETURN ... _v ..., or declare it `val`.",
        _udf_expr_parameter_reused,
    ),
    ModernRule(
        "UDF_NAME_LEAKS_SECURED_OBJECT",
        "UDF name or description names an RLS-secured table",
        "Security",
        1,
        "Function metadata is visible to every user who can see the model; naming a table that row-level security protects discloses it.",
        "Function",
        "Rename the function / reword its description.",
        _udf_name_leaks_secured_object,
    ),
    ModernRule(
        "CALENDAR_NO_TIME_UNIT_MAPPING",
        "Calendar without time-unit column mapping",
        "Time Intelligence",
        2,
        "A calendar object only drives calendar-based time intelligence when its columns are mapped to time units (year, quarter, month, week, day).",
        "Calendar",
        "Map the date table's columns to time units in the calendar definition.",
        _calendar_no_time_unit_mapping,
    ),
    ModernRule(
        "WEEK_TI_WITHOUT_CALENDAR",
        "Week time intelligence without a calendar object",
        "Time Intelligence",
        2,
        "TOTALWTD / STARTOFWEEK / ... need a Calendar object (Sep 2025); a classic marked date table cannot drive them.",
        "Measure, CalculationItem",
        "Add a Calendar object (ISO / 4-4-5 weeks) or use the Day-Number pattern.",
        _week_ti_without_calendar,
    ),
    ModernRule(
        "CALCGROUP_DUPLICATE_PRECEDENCE",
        "Calculation groups share a precedence",
        "Calculation Groups",
        2,
        "Two calculation groups with the same precedence have an undefined application order (canon AP-44 precedence design).",
        "CalculationGroup",
        "Give every calculation group a unique precedence.",
        _calcgroup_duplicate_precedence,
    ),
    ModernRule(
        "CALCGROUP_NO_SELECTION_EXPRESSIONS",
        "Calculation group without selection expressions",
        "Calculation Groups",
        1,
        "Without a no-selection / multiple-or-empty-selection expression the group applies no defined behaviour when no (or several) items are selected.",
        "CalculationGroup",
        "Define noSelectionExpression and multipleOrEmptySelectionExpression.",
        _calcgroup_no_selection_expressions,
    ),
    ModernRule(
        "CALCITEM_NO_FORMAT_STRING",
        "Calculation item without a format string expression",
        "Calculation Groups",
        1,
        "A calculation item that changes the meaning of a measure (percent, index, YoY) should carry its own format string expression.",
        "CalculationItem",
        "Add a format string expression (SELECTEDMEASUREFORMATSTRING() to keep the measure's).",
        _calcitem_no_format_string,
    ),
    ModernRule(
        "FIELD_PARAMETER_MISSING_FIELD",
        "Field parameter points at a missing field",
        "Error Prevention",
        3,
        "A NAMEOF() target that does not exist makes the field-parameter row, and every visual bound to it, fail.",
        "CalculatedTable",
        "Fix or remove the NAMEOF reference.",
        _field_parameter_missing_field,
    ),
    ModernRule(
        "FIELD_PARAMETER_HIDDEN_FIELD",
        "Field parameter points at a hidden field",
        "Maintenance",
        1,
        "A hidden field in a field parameter still works but is easy to break by 'cleaning up' hidden objects.",
        "CalculatedTable",
        "Unhide the field or document the dependency.",
        _field_parameter_hidden_field,
    ),
    ModernRule(
        "AI_TABLE_NO_DESCRIPTION",
        "Visible table without a description (Copilot readiness)",
        "AI Readiness",
        1,
        "Copilot and Q&A read table descriptions to choose the right table.",
        "Table",
        "Describe what one row of the table is and when to use it.",
        _ai_table_no_description,
    ),
    ModernRule(
        "AI_COLUMN_NO_DESCRIPTION",
        "Visible column without a description (Copilot readiness)",
        "AI Readiness",
        1,
        "Copilot and Q&A read column descriptions to resolve ambiguous fields.",
        "DataColumn, CalculatedColumn",
        "Describe the column (unit, grain, allowed values).",
        _ai_column_no_description,
    ),
    ModernRule(
        "AI_NO_SYNONYMS",
        "No synonyms on a model with visible objects (Copilot readiness)",
        "AI Readiness",
        1,
        "Linguistic metadata with synonyms improves natural-language matching (Q&A retires Dec 2026; Copilot keeps using synonyms).",
        "Model",
        "Add synonyms for the business names users actually say (02_QA_Linguistic).",
        _ai_no_synonyms,
    ),
)


def modern_rule_dicts() -> List[Dict[str, Any]]:
    """The rules as BPA rule dicts (``Expression`` is empty: they run via ``python_check``)."""
    return [
        {
            "ID": r.id,
            "Name": r.name,
            "Category": r.category,
            "Severity": r.severity,
            "Description": r.description,
            "Scope": r.scope,
            "Expression": "",
            "FixExpression": r.fix,
        }
        for r in MODERN_RULES
    ]


CHECKS: Dict[str, Callable[[Dict[str, Any]], List[Hit]]] = {r.id: r.check for r in MODERN_RULES}
