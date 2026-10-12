"""Static audit-taxonomy rules (F-05; the checks ``06_Analysis_Operations full ruleset=audit`` adds).

The user's audit taxonomy (``pbi-model-audit/references/audit-categories.md``: CR, MS, DR, DAX, QP,
DOC, DES, DL, AI) is made executable by :mod:`core.analysis.audit_taxonomy`
(``config/audit_taxonomy.json`` maps every check id to the rules that implement it). Most checks
were already covered by the curated / Microsoft rulesets, the modern-model rules
(:mod:`core.analysis.bpa_modern_rules`) and the topology validator; THIS module adds only what no
existing rule checks. Same shape as the modern rules: a Python check over the TMSL model dict,
``check(model) -> [(object_type, name, table, details)]``, attached as a ``python_check`` rule.

  AUDIT_BIDI_ON_RLS_PATH            CR1   bidirectional relationship inside the scope RLS filters
  AUDIT_COLUMN_DATATYPE_UNKNOWN     CR3   column whose data type is Unknown
  AUDIT_RLS_ON_FACT_TABLE           CR5   RLS filter expression on a fact table
  AUDIT_RLS_INACTIVE_RELATIONSHIP   CR6   inactive relationship that is the only link to a secured table
  AUDIT_USERELATIONSHIP_IN_RLS_FILTER CR8 USERELATIONSHIP inside an RLS filter expression
  AUDIT_COMPOSITE_KEY_RELATIONSHIP  CR9   multi-column (composite) relationship keys
  AUDIT_CALCITEM_NO_SELECTEDMEASURE DES12 calculation item that never reads SELECTEDMEASURE()
  AUDIT_UDF_UNUSED                  DES13 user-defined function nothing references
  AUDIT_UDF_DUPLICATE_BODY          DES13 two user-defined functions with the same body
  AUDIT_AI_IMPLICIT_MEASURES        AI1   implicit measures allowed on visible numeric columns
  AUDIT_AI_DUPLICATE_FIELD_NAMES    AI5   same field name on visible objects of different tables
  AUDIT_AI_CRYPTIC_NAMES            AI6   visible objects with technical (snake / Camel / UPPER) names

Relationship ambiguity (DES11) is a graph check in :mod:`core.analysis.star_schema_validator`
(``AMBIGUOUS_RELATIONSHIP_PATH``); it is mapped in the taxonomy, not duplicated here.

Every rule is conservative (over-firing is the failure mode of an advisor): each one has a
true-positive and a true-negative fixture in ``tests/test_audit_rules.py``.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.analysis.bpa_modern_rules import Hit, ModernRule
from core.analysis.bpa_model import sanitize_dax, text_of
from core.analysis.star_schema_validator import _is_card_many, _truthy

__all__ = ["AUDIT_RULES", "AUDIT_CHECKS", "audit_rule_dicts"]

_MAX_HITS = 300


# ─────────────────────────────── helpers ───────────────────────────────


def _norm(name: Any) -> str:
    return " ".join(str(name or "").lower().split())


def _tables(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [t for t in model.get("tables") or [] if isinstance(t, dict)]


def _rels(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in model.get("relationships") or [] if isinstance(r, dict)]


def _rel_label(r: Dict[str, Any]) -> str:
    return (
        f"{r.get('fromTable', '?')}[{r.get('fromColumn', '?')}] -> "
        f"{r.get('toTable', '?')}[{r.get('toColumn', '?')}]"
    )


def _is_bidi(r: Dict[str, Any]) -> bool:
    return str(r.get("crossFilteringBehavior", "")).strip().lower() in {"bothdirections", "both"}


def _secured(model: Dict[str, Any]) -> Dict[str, List[str]]:
    """normalised table name -> roles that put a (non-empty) filter expression on it."""
    out: Dict[str, List[str]] = {}
    for role in model.get("roles") or []:
        if not isinstance(role, dict):
            continue
        for tp in role.get("tablePermissions") or []:
            if isinstance(tp, dict) and text_of(tp.get("filterExpression")).strip():
                out.setdefault(_norm(tp.get("name")), []).append(str(role.get("name", "")))
    return out


def _display_name(model: Dict[str, Any], norm: str) -> str:
    for t in _tables(model):
        if _norm(t.get("name")) == norm:
            return str(t.get("name"))
    return norm


def _fact_like(model: Dict[str, Any]) -> Set[str]:
    """Normalised names of fact-like tables: MANY side of an active relationship, never ONE side."""
    many: Set[str] = set()
    one: Set[str] = set()
    for r in _rels(model):
        if not _truthy(r.get("isActive", True)):
            continue
        if r.get("fromTable") and _is_card_many(r.get("fromCardinality", "Many")):
            many.add(_norm(r["fromTable"]))
        if r.get("toTable") and not _is_card_many(r.get("toCardinality", "One")):
            one.add(_norm(r["toTable"]))
    return many - one


def _visible(table: Dict[str, Any]) -> bool:
    return not table.get("isHidden") and not table.get("isPrivate")


def _visible_columns(model: Dict[str, Any]):
    for t in _tables(model):
        if not _visible(t) or isinstance(t.get("calculationGroup"), dict):
            continue
        for c in t.get("columns") or []:
            if (
                isinstance(c, dict)
                and not c.get("isHidden")
                and str(c.get("type", "")).lower() != "rownumber"
            ):
                yield t, c


def _visible_measures(model: Dict[str, Any]):
    for t in _tables(model):
        if not _visible(t):
            continue
        for m in t.get("measures") or []:
            if isinstance(m, dict) and not m.get("isHidden"):
                yield t, m


# ───────────────────────── CR: relationships and RLS ───────────────────


def _bidi_on_rls_path(model: Dict[str, Any]) -> List[Hit]:
    secured = _secured(model)
    if not secured:
        return []
    # tables RLS reaches: the secured ones plus everything their filter propagates to along
    # single-direction relationships (a filter flows from the ONE side to the MANY side).
    scope: Set[str] = set(secured)
    changed = True
    while changed:
        changed = False
        for r in _rels(model):
            if not _truthy(r.get("isActive", True)):
                continue
            src, dst = _norm(r.get("toTable")), _norm(r.get("fromTable"))
            if src in scope and dst not in scope:
                scope.add(dst)
                changed = True
    out: List[Hit] = []
    for r in _rels(model):
        if not _truthy(r.get("isActive", True)):
            continue
        sec_bidi = str(r.get("securityFilteringBehavior", "")).strip().lower() == "bothdirections"
        if not (_is_bidi(r) or sec_bidi):
            continue
        ends = {_norm(r.get("fromTable")), _norm(r.get("toTable"))}
        if ends & scope:
            hit_tables = sorted(_display_name(model, n) for n in ends & scope)
            out.append(
                (
                    "Relationship",
                    _rel_label(r),
                    None,
                    "bidirectional"
                    + (" security" if sec_bidi else "")
                    + f" filtering on a path RLS reaches ({', '.join(hit_tables)})",
                )
            )
    return out


def _column_datatype_unknown(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for t in _tables(model):
        for c in t.get("columns") or []:
            if (
                isinstance(c, dict)
                and str(c.get("type", "")).lower() != "rownumber"
                and str(c.get("dataType", "")).strip().lower() == "unknown"
            ):
                out.append(("Column", str(c.get("name", "")), str(t.get("name", "")), None))
    return out[:_MAX_HITS]


def _rls_on_fact_table(model: Dict[str, Any]) -> List[Hit]:
    secured = _secured(model)
    facts = _fact_like(model)
    return [
        (
            "Table",
            _display_name(model, n),
            None,
            f"filtered by role(s): {', '.join(sorted(set(roles)))}",
        )
        for n, roles in sorted(secured.items())
        if n in facts
    ]


def _rls_inactive_relationship(model: Dict[str, Any]) -> List[Hit]:
    secured = _secured(model)
    if not secured:
        return []
    active_pairs = {
        frozenset((_norm(r.get("fromTable")), _norm(r.get("toTable"))))
        for r in _rels(model)
        if _truthy(r.get("isActive", True))
    }
    out: List[Hit] = []
    for r in _rels(model):
        if _truthy(r.get("isActive", True)):
            continue
        ends = {_norm(r.get("fromTable")), _norm(r.get("toTable"))}
        if not ends & set(secured) or frozenset(ends) in active_pairs:
            continue
        out.append(
            (
                "Relationship",
                _rel_label(r),
                None,
                "inactive; row-level security does not propagate through it (secured: "
                + ", ".join(sorted(_display_name(model, n) for n in ends & set(secured)))
                + ")",
            )
        )
    return out


_USEREL_RE = re.compile(r"\bUSERELATIONSHIP\s*\(", re.IGNORECASE)


def _userelationship_in_rls_filter(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for role in model.get("roles") or []:
        if not isinstance(role, dict):
            continue
        for tp in role.get("tablePermissions") or []:
            if not isinstance(tp, dict):
                continue
            if _USEREL_RE.search(sanitize_dax(text_of(tp.get("filterExpression")))):
                out.append(
                    (
                        "TablePermission",
                        str(tp.get("name", "")),
                        str(tp.get("name", "")),
                        f"role '{role.get('name')}' calls USERELATIONSHIP in its filter",
                    )
                )
    return out


def _composite_key_relationship(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    groups: Dict[frozenset, List[Dict[str, Any]]] = {}
    for r in _rels(model):
        rtype = str(r.get("type", "")).strip().lower()
        if rtype and rtype != "singlecolumn":
            out.append(
                ("Relationship", _rel_label(r), None, f"relationship type '{r.get('type')}'")
            )
        groups.setdefault(
            frozenset((_norm(r.get("fromTable")), _norm(r.get("toTable")))), []
        ).append(r)
    for pair, members in groups.items():
        if len(members) < 2 or len(pair) < 2:
            continue
        froms = {(_norm(r.get("fromTable")), _norm(r.get("fromColumn"))) for r in members}
        tos = {(_norm(r.get("toTable")), _norm(r.get("toColumn"))) for r in members}
        # role-playing dimensions reuse ONE key on the dimension side; a composite key is
        # several DIFFERENT key columns on both sides between the same two tables.
        if len(froms) == len(members) and len(tos) == len(members):
            cols = "; ".join(_rel_label(r) for r in members)
            out.append(
                (
                    "Relationship",
                    _rel_label(members[0]),
                    None,
                    f"{len(members)} relationships between the same two tables on different "
                    f"column pairs look like a composite key ({cols}); use one concatenated "
                    "surrogate key",
                )
            )
    return out


# ───────────────────────── DES: calculation groups, UDFs ───────────────


_SELECTEDMEASURE_RE = re.compile(r"\bSELECTEDMEASURE\s*\(", re.IGNORECASE)


def _calcitem_no_selectedmeasure(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for t in _tables(model):
        cg = t.get("calculationGroup")
        if not isinstance(cg, dict):
            continue
        for item in cg.get("calculationItems") or []:
            if not isinstance(item, dict):
                continue
            expr = text_of(item.get("expression")).strip()
            if expr and not _SELECTEDMEASURE_RE.search(sanitize_dax(expr)):
                out.append(
                    (
                        "CalculationItem",
                        str(item.get("name", "")),
                        str(t.get("name", "")),
                        "the expression never calls SELECTEDMEASURE(), so it ignores the "
                        "measure under evaluation",
                    )
                )
    return out[:_MAX_HITS]


def _dax_texts(model: Dict[str, Any], skip_function: Optional[str] = None) -> List[str]:
    """Every DAX body in the model (measures, columns, calc items, RLS, UDFs, calculated tables)."""
    texts: List[str] = []
    for t in _tables(model):
        for part in t.get("partitions") or []:
            src = part.get("source") if isinstance(part, dict) else None
            if isinstance(src, dict) and str(src.get("type", "")).lower() == "calculated":
                texts.append(text_of(src.get("expression")))
        for c in t.get("columns") or []:
            if isinstance(c, dict):
                texts.append(text_of(c.get("expression")))
        for m in t.get("measures") or []:
            if isinstance(m, dict):
                texts.append(text_of(m.get("expression")))
                for key in ("formatStringDefinition", "detailRowsDefinition"):
                    texts.append(text_of((m.get(key) or {}).get("expression")))
        cg = t.get("calculationGroup")
        for item in (cg.get("calculationItems") or []) if isinstance(cg, dict) else []:
            if isinstance(item, dict):
                texts.append(text_of(item.get("expression")))
                texts.append(text_of((item.get("formatStringDefinition") or {}).get("expression")))
    for role in model.get("roles") or []:
        for tp in (role.get("tablePermissions") or []) if isinstance(role, dict) else []:
            if isinstance(tp, dict):
                texts.append(text_of(tp.get("filterExpression")))
    for f in model.get("functions") or []:
        if isinstance(f, dict) and f.get("name") != skip_function:
            texts.append(text_of(f.get("expression")))
    return [sanitize_dax(x) for x in texts if x]


def _udf_unused(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for f in model.get("functions") or []:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        name = str(f["name"])
        rx = re.compile(r"(?<![\w.])" + re.escape(name) + r"\s*\(", re.IGNORECASE)
        if not any(rx.search(text) for text in _dax_texts(model, skip_function=name)):
            out.append(
                ("Function", name, None, "no measure, column, item, role or function calls it")
            )
    return out


def _udf_body(f: Dict[str, Any]) -> str:
    body = text_of(f.get("expression"))
    body = re.sub(r"^\s*FUNCTION\s+[\w.]+\s*=", "", body, flags=re.IGNORECASE)
    return " ".join(body.lower().split())


def _udf_duplicate_body(model: Dict[str, Any]) -> List[Hit]:
    groups: Dict[str, List[str]] = {}
    for f in model.get("functions") or []:
        if isinstance(f, dict) and f.get("name") and _udf_body(f):
            groups.setdefault(_udf_body(f), []).append(str(f["name"]))
    out: List[Hit] = []
    for names in groups.values():
        if len(names) > 1:
            for n in names:
                out.append(
                    (
                        "Function",
                        n,
                        None,
                        "same body as " + ", ".join(x for x in names if x != n),
                    )
                )
    return out


# ───────────────────────── AI / Copilot readiness ──────────────────────

_NUMERIC = {"int64", "double", "decimal", "currency", "number"}


def _ai_implicit_measures(model: Dict[str, Any]) -> List[Hit]:
    if _truthy(model.get("discourageImplicitMeasures")):
        return []
    out: List[Hit] = []
    for t, c in _visible_columns(model):
        if str(c.get("dataType", "")).lower() not in _NUMERIC:
            continue
        if str(c.get("summarizeBy", "default")).lower() in {"none"}:
            continue
        if c.get("isKey") or re.search(r"(key|id|nr|number|code)$", _norm(c.get("name")), re.I):
            continue
        out.append(
            (
                "Column",
                str(c.get("name", "")),
                str(t.get("name", "")),
                f"summarizeBy={c.get('summarizeBy', 'default')} and the model allows implicit "
                "measures (discourageImplicitMeasures is off)",
            )
        )
        if len(out) >= _MAX_HITS:
            break
    return out


def _ai_duplicate_field_names(model: Dict[str, Any]) -> List[Hit]:
    seen: Dict[str, List[Tuple[str, str]]] = {}
    for t, c in _visible_columns(model):
        seen.setdefault(_norm(c.get("name")), []).append((str(t.get("name")), str(c.get("name"))))
    for t, m in _visible_measures(model):
        seen.setdefault(_norm(m.get("name")), []).append((str(t.get("name")), str(m.get("name"))))
    out: List[Hit] = []
    for entries in seen.values():
        tables = {t for t, _ in entries}
        if len(tables) < 2:
            continue
        where = ", ".join(sorted(tables))
        for t, name in entries:
            out.append(("Column", name, t, f"the same field name is also visible on: {where}"))
        if len(out) >= _MAX_HITS:
            break
    return out[:_MAX_HITS]


def _cryptic(name: str) -> Optional[str]:
    n = name.strip()
    if not n or " " in n or len(n) <= 3:
        return None
    if "_" in n and re.search(r"[A-Za-z]_[A-Za-z0-9]", n):
        return "snake_case / underscore"
    if re.search(r"[a-z][A-Z]", n):
        return "CamelCase"
    if n.isupper() and sum(ch.isalpha() for ch in n) >= 4:
        return "UPPER CASE abbreviation"
    return None


def _ai_cryptic_names(model: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for t in _tables(model):
        if not _visible(t) or isinstance(t.get("calculationGroup"), dict):
            continue
        why = _cryptic(str(t.get("name", "")))
        if why:
            out.append(("Table", str(t.get("name", "")), None, why))
    for t, c in _visible_columns(model):
        why = _cryptic(str(c.get("name", "")))
        if why:
            out.append(("Column", str(c.get("name", "")), str(t.get("name", "")), why))
    for t, m in _visible_measures(model):
        why = _cryptic(str(m.get("name", "")))
        if why:
            out.append(("Measure", str(m.get("name", "")), str(t.get("name", "")), why))
    return out[:_MAX_HITS]


# ─────────────────────────────── registry ──────────────────────────────

AUDIT_RULES: Tuple[ModernRule, ...] = (
    ModernRule(
        "AUDIT_BIDI_ON_RLS_PATH",
        "Bidirectional relationship on a path row-level security reaches",
        "Security",
        3,
        "Bidirectional cross-filtering (or bidirectional security filtering) on a relationship "
        "that touches a secured table, or a table the RLS filter propagates to, lets a filter flow "
        "back up the path and can expose or distort rows (audit CR1).",
        "Relationship",
        "Make the relationship single-direction; use CROSSFILTER() inside the one measure that "
        "needs the other direction.",
        _bidi_on_rls_path,
    ),
    ModernRule(
        "AUDIT_COLUMN_DATATYPE_UNKNOWN",
        "Column with an Unknown data type",
        "Error Prevention",
        3,
        "A column whose data type is Unknown cannot be aggregated, related or sorted reliably "
        "(audit CR3).",
        "DataColumn, CalculatedColumn, CalculatedTableColumn",
        "Set the column's data type (refresh the schema / fix the source query).",
        _column_datatype_unknown,
    ),
    ModernRule(
        "AUDIT_RLS_ON_FACT_TABLE",
        "Row-level security filter on a fact table",
        "Security",
        3,
        "An RLS filter belongs on the dimension: a filter on a fact table is evaluated per fact "
        "row, bypasses the star-schema propagation and is the slowest form of RLS (audit CR5).",
        "Table",
        "Filter the dimension table instead and let the relationship propagate it.",
        _rls_on_fact_table,
    ),
    ModernRule(
        "AUDIT_RLS_INACTIVE_RELATIONSHIP",
        "Inactive relationship is the only link to a secured table",
        "Security",
        3,
        "Row-level security never propagates through an inactive relationship: rows of the "
        "related table stay visible to the role (audit CR6).",
        "Relationship",
        "Make the relationship active, or secure the related table directly.",
        _rls_inactive_relationship,
    ),
    ModernRule(
        "AUDIT_USERELATIONSHIP_IN_RLS_FILTER",
        "USERELATIONSHIP inside an RLS filter expression",
        "Security",
        3,
        "USERELATIONSHIP combined with row-level security errors at query time; an RLS filter "
        "expression must not call it (audit CR8).",
        "TablePermission",
        "Filter on a column of the secured table directly, or restructure the relationship.",
        _userelationship_in_rls_filter,
    ),
    ModernRule(
        "AUDIT_COMPOSITE_KEY_RELATIONSHIP",
        "Composite (multi-column) relationship key",
        "Relationships",
        2,
        "Power BI relationships join on ONE column. Several relationships between the same two "
        "tables on different key columns are a composite join modelled by hand and filter "
        "inconsistently (audit CR9).",
        "Relationship",
        "Build one concatenated surrogate key on both tables and relate on it.",
        _composite_key_relationship,
    ),
    ModernRule(
        "AUDIT_CALCITEM_NO_SELECTEDMEASURE",
        "Calculation item that never calls SELECTEDMEASURE()",
        "Calculation Groups",
        2,
        "A calculation item that does not evaluate SELECTEDMEASURE() returns the same value for "
        "every measure in the visual (audit DES12).",
        "CalculationItem",
        "Wrap the measure under evaluation: SELECTEDMEASURE(), or CALCULATE(SELECTEDMEASURE(), ...).",
        _calcitem_no_selectedmeasure,
    ),
    ModernRule(
        "AUDIT_UDF_UNUSED",
        "User-defined function nothing calls",
        "Maintenance",
        1,
        "A user-defined function that no measure, column, calculation item, role or other "
        "function references is dead code (audit DES13).",
        "Function",
        "Delete the function or document why it is kept.",
        _udf_unused,
    ),
    ModernRule(
        "AUDIT_UDF_DUPLICATE_BODY",
        "User-defined functions with the same body",
        "Maintenance",
        1,
        "Two functions with an identical body are a copy-paste: a fix to one silently misses the "
        "other (audit DES13).",
        "Function",
        "Keep one function and point the callers at it.",
        _udf_duplicate_body,
    ),
    ModernRule(
        "AUDIT_AI_IMPLICIT_MEASURES",
        "Implicit measures allowed on visible numeric columns",
        "AI Readiness",
        2,
        "Implicit measures (a dragged column aggregation) are invisible to data agents; "
        "discourageImplicitMeasures plus explicit measures is the Copilot-ready setup (audit AI1).",
        "DataColumn, CalculatedColumn",
        "Set DiscourageImplicitMeasures, set SummarizeBy = None on the columns, add explicit measures.",
        _ai_implicit_measures,
    ),
    ModernRule(
        "AUDIT_AI_DUPLICATE_FIELD_NAMES",
        "Same field name on visible objects of different tables",
        "AI Readiness",
        2,
        "Copilot resolves a question by field name; one name on several tables is ambiguous "
        "(audit AI5).",
        "DataColumn, CalculatedColumn",
        "Give the fields distinguishing business names (or hide the duplicates).",
        _ai_duplicate_field_names,
    ),
    ModernRule(
        "AUDIT_AI_CRYPTIC_NAMES",
        "Visible objects with technical names",
        "AI Readiness",
        2,
        "snake_case, CamelCase and UPPER_CASE abbreviations (TR_AMT, CustName) cannot be read by "
        "Copilot or by business users (audit AI6).",
        "Table, DataColumn, CalculatedColumn, Measure",
        "Rename to the business term ('Customer Name'); the rename cascades through DAX and PBIR.",
        _ai_cryptic_names,
    ),
)

AUDIT_CHECKS: Dict[str, Callable[[Dict[str, Any]], List[Hit]]] = {
    r.id: r.check for r in AUDIT_RULES
}


def audit_rule_dicts() -> List[Dict[str, Any]]:
    """The rules as BPA rule dicts (``Expression`` empty: they run via ``python_check``)."""
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
        for r in AUDIT_RULES
    ]
