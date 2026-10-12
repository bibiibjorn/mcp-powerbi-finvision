"""Executable audit taxonomy (F-05): ``06_Analysis_Operations full ruleset=audit``.

``config/audit_taxonomy.json`` is the table that maps every check id of the user's audit taxonomy
(``pbi-model-audit/references/audit-categories.md``: CR, MS, DR, DAX, QP, DOC, DES, DL, AI) to the
rules and topology findings that implement it. This module

* builds the audit-only rules (:mod:`core.analysis.audit_rules`) as BPA rules,
* tags every finding with ``audit_id`` / ``audit_ids`` / ``audit_citation`` (the citation format the
  skill's ``check_findings_table.py`` demands: ``audit-categories.md:<ID>``),
* turns a run into a per-check verdict table (``fail`` / ``pass`` / ``not_evaluated`` /
  ``requires_live`` / ``manual`` / ``engine_enforced``) so "no finding" is never confused with
  "not checked",
* runs the opt-in runtime referential-integrity check (CR10) against a connected engine.

A check marked ``requires_live`` (trace evidence, service settings) is REPORTED as such, with how to
get the evidence - it is never reported as passing.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TAXONOMY_PATH = os.path.join(_ROOT_DIR, "config", "audit_taxonomy.json")

#: ids of the findings the topology validator emits (the ``topology`` entries of the taxonomy).
TOPOLOGY_IDS = (
    "FACT_TO_FACT",
    "SNOWFLAKE_DIMENSION",
    "DISCONNECTED_TABLE",
    "MISSING_DATE_TABLE",
    "AMBIGUOUS_RELATIONSHIP_PATH",
)

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {}


def load_taxonomy(path: Optional[str] = None) -> Dict[str, Any]:
    """The parsed taxonomy (cached per path)."""
    key = path or TAXONOMY_PATH
    with _LOCK:
        if key not in _CACHE:
            with open(key, "r", encoding="utf-8") as fh:
                _CACHE[key] = json.load(fh)
        return _CACHE[key]


def audit_bpa_rules() -> List[Any]:
    """The audit-only rules as ready-to-run :class:`BPARule` objects (``python_check`` set)."""
    from core.analysis.audit_rules import AUDIT_CHECKS, audit_rule_dicts
    from core.analysis.bpa_analyzer import rule_from_dict

    rules: List[Any] = []
    for entry in audit_rule_dicts():
        rule, err = rule_from_dict(entry, source="audit")
        if rule is None:
            logger.warning("audit rule %s skipped: %s", entry.get("ID"), err)
            continue
        rule.python_check = AUDIT_CHECKS[rule.id]
        rule.unsupported_reason = None  # no expression to compile
        rules.append(rule)
    return rules


_INDEX: Dict[int, Tuple[Dict[str, Any], Dict[str, List[str]]]] = {}


def source_index(taxonomy: Dict[str, Any]) -> Dict[str, List[str]]:
    """rule id / topology finding id -> the check ids it implements (taxonomy order)."""
    cached = _INDEX.get(id(taxonomy))
    if cached is not None and cached[0] is taxonomy:
        return cached[1]
    out: Dict[str, List[str]] = {}
    for check in taxonomy.get("checks", []):
        for src in list(check.get("rules", [])) + list(check.get("topology", [])):
            out.setdefault(src, []).append(check["id"])
    _INDEX[id(taxonomy)] = (taxonomy, out)
    return out


def tag(finding: Dict[str, Any], rule_id: Optional[str], taxonomy: Dict[str, Any]) -> bool:
    """Add ``audit_id`` / ``audit_ids`` / ``audit_citation`` to a finding dict; True when mapped."""
    ids = source_index(taxonomy).get(str(rule_id or ""))
    if not ids:
        return False
    finding["audit_id"] = ids[0]
    if len(ids) > 1:
        finding["audit_ids"] = list(ids)
    finding["audit_citation"] = f"audit-categories.md:{ids[0]}"
    ext = {c["id"] for c in taxonomy.get("checks", []) if c.get("extension")}
    if ids[0] in ext:
        finding["audit_extension"] = True
    return True


def build_report(
    taxonomy: Dict[str, Any],
    *,
    violations: Iterable[Dict[str, Any]],
    topology_findings: Iterable[Dict[str, Any]],
    evaluated_rule_ids: Iterable[str],
    topology_ok: bool,
    extra_findings: Iterable[Tuple[str, Dict[str, Any]]] = (),
    runtime_ran: bool = False,
    vertipaq_ran: bool = False,
) -> Dict[str, Any]:
    """Per-check verdicts for one run.

    ``extra_findings`` are ``(check_id, finding)`` pairs from runtime checks (CR10); their check
    is then no longer reported as ``requires_live``.
    """
    evaluated = set(evaluated_rule_ids)
    counts: Dict[str, int] = {}
    samples: Dict[str, List[str]] = {}

    def hit(check_id: str, label: str) -> None:
        counts[check_id] = counts.get(check_id, 0) + 1
        bucket = samples.setdefault(check_id, [])
        if len(bucket) < 10:
            bucket.append(label)

    index = source_index(taxonomy)
    for v in violations:
        for cid in index.get(str(v.get("rule_id")), []):
            name = v.get("object_name") or ""
            tbl = v.get("table_name")
            hit(cid, f"{tbl}.{name}" if tbl else str(name))
    for f in topology_findings:
        for cid in index.get(str(f.get("finding_id") or f.get("rule_id")), []):
            hit(cid, str(f.get("object_name") or ""))
    for cid, f in extra_findings:
        hit(cid, str(f.get("object_name") or ""))

    runtime_ids = {cid for cid, _ in extra_findings}
    checks: List[Dict[str, Any]] = []
    summary = {
        "checks_total": 0,
        "fail": 0,
        "pass": 0,
        "not_evaluated": 0,
        "requires_live": 0,
        "manual": 0,
        "engine_enforced": 0,
    }
    for c in taxonomy.get("checks", []):
        cid = c["id"]
        status = c.get("status", "static")
        count = counts.get(cid, 0)
        sources = list(c.get("rules", [])) + list(c.get("topology", []))
        unevaluated: List[str] = []
        for r in c.get("rules", []):
            if r not in evaluated:
                unevaluated.append(r)
        if c.get("topology") and not topology_ok:
            unevaluated.extend(c["topology"])
        if count:
            verdict = "fail"
        elif status == "engine_enforced":
            verdict = "engine_enforced"
        elif status == "manual":
            verdict = "manual"
        elif c.get("requires_live") and not (runtime_ran and cid in ("CR10",)):
            verdict = "requires_live"
        elif sources and unevaluated:
            verdict = "not_evaluated"
        elif c.get("requires") == "include_vertipaq" and not vertipaq_ran:
            verdict = "not_evaluated"
        else:
            verdict = "pass"
        row: Dict[str, Any] = {
            "id": cid,
            "family": c.get("family"),
            "title": c.get("title"),
            "verdict": verdict,
            "count": count,
        }
        if verdict == "fail":
            row["examples"] = samples.get(cid, [])
        if verdict == "not_evaluated" and unevaluated:
            row["not_evaluated_rules"] = sorted(set(unevaluated))
        if verdict in ("requires_live", "manual", "not_evaluated"):
            for key in ("requires", "hand_off", "applies_when"):
                if c.get(key):
                    row[key] = c[key]
        if c.get("extension"):
            row["extension"] = True
        checks.append(row)
        summary["checks_total"] += 1
        summary[verdict] = summary.get(verdict, 0) + 1
        if cid in runtime_ids and verdict == "fail":
            row["source"] = "runtime"
    summary["findings"] = sum(counts.values())
    return {
        "taxonomy_version": taxonomy.get("version"),
        "citation": taxonomy.get("citation"),
        "summary": summary,
        "checks": checks,
        "note": (
            "A check that is manual / requires_live / not_evaluated has NOT passed: pass means the "
            "mapped rules ran and found nothing."
        ),
    }


# ───────────────────────────── runtime RI (CR10) ───────────────────────

_RI_MAX_RELATIONSHIPS = 40


def runtime_ri_findings(
    model: Dict[str, Any], executor: Any, *, max_relationships: int = _RI_MAX_RELATIONSHIPS
) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
    """Orphan fact keys per active many-to-one relationship (audit CR10), via the connected engine.

    One small DAX query per relationship (``COUNTROWS(FILTER(Fact, NOT ISBLANK(fk) &&
    ISBLANK(RELATED(dim[pk]))))``); a query that fails is skipped and reported in the notes -
    never counted as clean. Returns ``([(check_id, finding)], notes)``.
    """
    from core.analysis.star_schema_validator import _is_card_many
    from core.dax.dax_names import column_ref, quote_table

    notes: List[str] = []
    findings: List[Tuple[str, Dict[str, Any]]] = []
    rels = [
        r
        for r in model.get("relationships") or []
        if isinstance(r, dict)
        and r.get("isActive", True) not in (False, "false")
        and _is_card_many(r.get("fromCardinality", "Many"))
        and not _is_card_many(r.get("toCardinality", "One"))
        and r.get("fromTable")
        and r.get("toTable")
    ]
    if len(rels) > max_relationships:
        notes.append(
            f"RI check limited to the first {max_relationships} of {len(rels)} relationships"
        )
        rels = rels[:max_relationships]
    for r in rels:
        fk = column_ref(str(r["fromTable"]), str(r["fromColumn"]))
        pk = column_ref(str(r["toTable"]), str(r["toColumn"]))
        query = (
            f'EVALUATE ROW("orphans", COUNTROWS(FILTER({quote_table(str(r["fromTable"]))}, '
            f"NOT ISBLANK({fk}) && ISBLANK(RELATED({pk})))))"
        )
        label = f"{r['fromTable']}[{r['fromColumn']}] -> {r['toTable']}[{r['toColumn']}]"
        try:
            res = executor.validate_and_execute_dax(query, top_n=1)
        except Exception as exc:  # noqa: BLE001 - never count a failed probe as clean
            notes.append(f"RI query failed for {label}: {exc}")
            continue
        if not isinstance(res, dict) or not res.get("success"):
            notes.append(f"RI query failed for {label}: {(res or {}).get('error')}")
            continue
        rows = res.get("rows") or []
        value = next(iter((rows[0] or {}).values()), None) if rows else None
        try:
            orphans = int(value or 0)
        except (TypeError, ValueError):
            notes.append(f"RI query for {label} returned {value!r}")
            continue
        if orphans > 0:
            findings.append(
                (
                    "CR10",
                    {
                        "finding_id": "RI_VIOLATION",
                        "object_type": "Relationship",
                        "object_name": label,
                        "description": f"{orphans} fact row(s) carry a key with no match in "
                        f"'{r['toTable']}' (they roll up into the blank member).",
                        "orphans": orphans,
                    },
                )
            )
    return findings, notes
