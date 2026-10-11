"""Best Practice Analyzer for Semantic Models.

Analyzes a TMSL model (``JsonSerializer.SerializeObject(Model)``) against
Tabular-Editor-style BPA rules.

2026-09-22 review (I-01 … I-05, I-12, I-13, I-20): the regex-cascade evaluator
was replaced by a real parser/evaluator (:mod:`core.analysis.bpa_expression`)
over a TOM-like object graph (:mod:`core.analysis.bpa_model`). A run is now a
pure function of *(rules, model, cfg)*: :meth:`BPAAnalyzer.run` returns a
:class:`BPARunResult` and shares no mutable state between concurrent calls; the
legacy ``analyze_model*`` / ``get_run_notes`` / ``get_coverage`` entry points
wrap it and remember the last result for old callers. Rules that cannot be
evaluated (unknown member, dead regex, parse error, unsupported scope) are
reported as *unsupported* and never contribute violations.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple, Union

from core.analysis.bpa_expression import (
    BpaExpressionError,
    BpaParseError,
    BpaRuntimeError,
    BpaUnsupported,
    CompiledExpression,
    Evaluator,
    compile_expression,
)
from core.analysis.bpa_model import TOM_KIND_OF, ModelView, normalize_scope, resolve_tmsl_model
from core.utilities.json_utils import load_json

logger = logging.getLogger(__name__)

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Shipped rule sets (``ruleset`` argument of 06_Analysis_Operations:full).
RULESET_FILES = {
    "default": os.path.join(_ROOT_DIR, "config", "bpa_rules_comprehensive.json"),
    "microsoft": os.path.join(_ROOT_DIR, "core", "bpa.json"),
}


# Local rule -> Microsoft-ruleset twins it replaces when both sets run (``ruleset=all``): the same
# check under a different id was reported twice (2026-10-03 review D-23). The merge in
# ``06 full`` calls :func:`superseded_rule_ids` with the ids of the rules it already has.
RULE_SUPERSEDES: Dict[str, Tuple[str, ...]] = {
    "USE_DIVIDE_FUNCTION": ("USE_THE_DIVIDE_FUNCTION_FOR_DIVISION",),
    "AVOID_IFERROR_ISERROR": ("AVOID_USING_THE_IFERROR_FUNCTION",),
    "MARK_DATE_TABLE": ("DATE/CALENDAR_TABLES_SHOULD_BE_MARKED_AS_A_DATE_TABLE",),
    "NO_CALENDAR_AUTO": ("REMOVE_AUTO-DATE_TABLE",),
    "PROVIDE_FORMAT_STRING": ("PROVIDE_FORMAT_STRING_FOR_MEASURES",),
    "ADD_MEASURE_DESCRIPTIONS": ("OBJECTS_WITH_NO_DESCRIPTION",),
    "FACT_TABLE_COLUMNS_HIDDEN": ("HIDE_FACT_TABLE_COLUMNS",),
    "AVOID_DUPLICATE_MEASURE_NAMES": ("AVOID_DUPLICATE_MEASURES",),
    "AVOID_BIDIRECTIONAL_RELATIONSHIPS": (
        "AVOID_EXCESSIVE_BI-DIRECTIONAL_OR_MANY-TO-MANY_RELATIONSHIPS",
    ),
    "REDUCE_CALCULATED_COLUMNS": ("REDUCE_NUMBER_OF_CALCULATED_COLUMNS",),
    "REMOVE_UNUSED_COLUMNS": ("UNNECESSARY_COLUMNS",),
    "REMOVE_UNUSED_MEASURES": ("UNNECESSARY_MEASURES",),
    "AVOID_FORMAT_FUNCTION": (),
}


def superseded_rule_ids(present_ids) -> set:
    """Microsoft-ruleset ids that a local rule in *present_ids* already covers."""
    present = {str(i) for i in present_ids}
    out: set = set()
    for local, twins in RULE_SUPERSEDES.items():
        if local in present:
            out.update(twins)
    return out


class BPASeverity(IntEnum):
    """BPA Rule Severity Levels"""

    INFO = 1
    WARNING = 2
    ERROR = 3


@dataclass
class BPAViolation:
    """Represents a Best Practice Analyzer rule violation"""

    rule_id: str
    rule_name: str
    category: str
    severity: BPASeverity
    description: str
    object_type: str
    object_name: str
    table_name: Optional[str] = None
    fix_expression: Optional[str] = None
    details: Optional[str] = None
    # ``(tom_kind, table, name)`` of the violating object when it can be patched (not serialised):
    # the ``03 object_type=patch from_bpa=`` path re-resolves the object from it.
    ref: Optional[Tuple[str, Optional[str], str]] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-friendly dict; ``severity`` as its name (``"WARNING"``), not ``2``.

        ``fix_available: true`` appears only when the FixExpression is machine-applicable (the
        TE subset :mod:`core.analysis.bpa_fix` translates into a patch); prose advice and
        computed expressions leave it out (their text stays in ``fix_expression``).
        """
        out = self._base_dict()
        if self.fix_expression and self.ref is not None:
            from core.analysis.bpa_fix import fix_is_machine_applicable

            if fix_is_machine_applicable(self.fix_expression, self.ref[0]):
                out["fix_available"] = True
        return out

    def _base_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "category": self.category,
            "severity": BPASeverity(int(self.severity)).name,
            "severity_level": int(self.severity),
            "description": self.description,
            "object_type": self.object_type,
            "object_name": self.object_name,
            "table_name": self.table_name,
            "fix_expression": self.fix_expression,
            "details": self.details,
        }


@dataclass
class BPARule:
    """Represents a Best Practice Analyzer rule"""

    id: str
    name: str
    category: str
    description: str
    severity: BPASeverity
    scope: List[str]
    expression: str
    fix_expression: Optional[str] = None
    compatibility_level: int = 1200
    # Parsed expression (lazily filled) and why the rule cannot run, if it cannot.
    compiled: Optional[CompiledExpression] = field(default=None, repr=False, compare=False)
    unsupported_reason: Optional[str] = field(default=None, compare=False)
    source: str = field(default="builtin", compare=False)
    # Modern-model rules (core.analysis.bpa_modern_rules) run a Python check over the TMSL model
    # dict instead of an expression: ``check(model) -> [(object_type, name, table, details)]``.
    python_check: Optional[Any] = field(default=None, repr=False, compare=False)


@dataclass
class BPARunResult:
    """Outcome of one BPA run (pure — nothing is stored on the analyzer)."""

    violations: List[BPAViolation]
    notes: List[str]
    coverage: Dict[str, Any]
    elapsed_seconds: float = 0.0
    cache_stats: Dict[str, Any] = field(default_factory=dict)
    model_found: bool = True

    def summary(self) -> Dict[str, Any]:
        return summarize_violations(self.violations)


def summarize_violations(violations: List[BPAViolation]) -> Dict[str, Any]:
    """Counts by severity name and by category."""
    summary: Dict[str, Any] = {
        "total": len(violations),
        "by_severity": {"INFO": 0, "WARNING": 0, "ERROR": 0},
        "by_category": {},
    }
    for v in violations:
        sev = BPASeverity(int(v.severity)).name
        summary["by_severity"][sev] = summary["by_severity"].get(sev, 0) + 1
        summary["by_category"][v.category] = summary["by_category"].get(v.category, 0) + 1
    return summary


def _split_scope(scope_raw: Any) -> List[str]:
    if isinstance(scope_raw, str):
        return [s.strip() for s in scope_raw.split(",") if s.strip()]
    if isinstance(scope_raw, list):
        return [str(s).strip() for s in scope_raw if str(s).strip()]
    return []


def _expression_text(raw: Any) -> str:
    if isinstance(raw, list):
        return "\n".join(str(x) for x in raw)
    return str(raw or "")


def rule_from_dict(
    data: Dict[str, Any], source: str = "file"
) -> Tuple[Optional[BPARule], Optional[str]]:
    """Build a :class:`BPARule` from a rules-file entry.

    Returns ``(rule, None)`` or ``(None, error)`` — a malformed entry (missing
    ID, bad Severity) never raises mid-load (I-20).
    """
    if not isinstance(data, dict):
        return None, "rule entry is not an object"
    rid = str(data.get("ID") or "").strip()
    if not rid:
        return None, "missing ID"
    try:
        severity = BPASeverity(int(data.get("Severity", 1)))
    except (ValueError, TypeError):
        return None, f"invalid Severity {data.get('Severity')!r} (expected 1, 2 or 3)"
    rule = BPARule(
        id=rid,
        name=str(data.get("Name") or rid),
        category=str(data.get("Category") or ""),
        description=str(data.get("Description") or ""),
        severity=severity,
        scope=_split_scope(data.get("Scope", "")),
        expression=_expression_text(data.get("Expression", "")),
        fix_expression=data.get("FixExpression"),
        compatibility_level=int(data.get("CompatibilityLevel", 1200) or 1200),
        source=source,
    )
    prepare_rule(rule)
    return rule, None


def prepare_rule(rule: BPARule) -> BPARule:
    """Parse the rule expression once; record why it cannot run, if it cannot."""
    if rule.compiled is not None or rule.unsupported_reason:
        return rule
    try:
        rule.compiled = compile_expression(rule.expression)
    except BpaParseError as exc:
        rule.unsupported_reason = f"parse error: {exc}"
    except BpaUnsupported as exc:
        rule.unsupported_reason = str(exc)
    return rule


def _rules_from_payload(data: Any) -> List[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("rules", []) or []
    return []


def load_rules_file(
    path: str, existing_ids: Optional[set] = None, source: str = "external"
) -> Tuple[List[BPARule], Dict[str, Any]]:
    """Validate and load an external rules file without touching any analyzer.

    Accepts the Tabular Editor shape (top-level array) and ``{"rules": [...]}``.
    Returns ``(rules, report)`` where report lists ``added``,
    ``skipped_duplicate_ids``, ``invalid`` and ``unsupported`` (rules that
    load but cannot be evaluated).
    """
    data = load_json(path)
    existing = set(existing_ids or ())
    rules: List[BPARule] = []
    report: Dict[str, Any] = {
        "path": path,
        "added": 0,
        "skipped_duplicate_ids": [],
        "invalid": [],
        "unsupported": [],
    }
    for entry in _rules_from_payload(data):
        rule, err = rule_from_dict(entry, source=source)
        if rule is None:
            rid = entry.get("ID") if isinstance(entry, dict) else None
            report["invalid"].append({"id": rid, "error": err})
            continue
        if rule.id in existing:
            report["skipped_duplicate_ids"].append(rule.id)
            continue
        existing.add(rule.id)
        rules.append(rule)
        if rule.unsupported_reason:
            report["unsupported"].append({"id": rule.id, "reason": rule.unsupported_reason})
    report["added"] = len(rules)
    return rules, report


_ANNOTATION_RE = re.compile(r'GetAnnotation\(\s*"([^"]+)"\s*\)')


class BPAAnalyzer:
    """
    Best Practice Analyzer for Semantic Models
    Analyzes TMSL models against best practice rules
    """

    def __init__(self, rules_file_path: Optional[str] = None):
        """
        Initialize the BPA Analyzer

        Args:
            rules_file_path: Path to the BPA rules JSON file
        """
        self.rules: List[BPARule] = []
        self.load_errors: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._last: Optional[BPARunResult] = None

        if rules_file_path:
            self.load_rules(rules_file_path)

        # Load built-in enhanced rules and custom rules
        self._init_builtin_rules()
        self._load_custom_rules()

    # ── rule loading ────────────────────────────────────────────────────

    def _add_rule_dicts(self, entries: List[Any], source: str) -> int:
        existing_ids = {r.id for r in self.rules}
        added = 0
        for entry in entries:
            rule, err = rule_from_dict(entry, source=source)
            if rule is None:
                self.load_errors.append(
                    {
                        "source": source,
                        "id": entry.get("ID") if isinstance(entry, dict) else None,
                        "error": err,
                    }
                )
                continue
            if rule.id in existing_ids:
                continue
            self.rules.append(rule)
            existing_ids.add(rule.id)
            added += 1
        return added

    def _init_builtin_rules(self) -> None:
        """Add built-in enhanced BPA rules to the rule set."""
        added = self._add_rule_dicts(self._get_builtin_rules(), "builtin")
        if added:
            logger.info(f"Added {added} built-in enhanced BPA rules")
        self._init_modern_rules()

    def _init_modern_rules(self) -> None:
        """UDF / calendar / calculation-group / field-parameter / AI-readiness rules
        (2026-10-03 review D-28); they carry a ``python_check`` instead of an expression."""
        from core.analysis.bpa_modern_rules import CHECKS, modern_rule_dicts

        existing = {r.id for r in self.rules}
        for entry in modern_rule_dicts():
            if entry["ID"] in existing:
                continue
            rule, err = rule_from_dict(entry, source="builtin")
            if rule is None:
                self.load_errors.append({"source": "builtin", "id": entry["ID"], "error": err})
                continue
            rule.python_check = CHECKS[rule.id]
            rule.unsupported_reason = None  # no expression to compile
            self.rules.append(rule)

    def _load_custom_rules(self) -> None:
        """Load custom BPA rules from config/bpa_rules/*.json.

        Each JSON file should have a top-level ``rules`` array (or be an
        array). Rules whose ID already exists are skipped to avoid duplicates.
        """
        custom_dir = os.path.join(_ROOT_DIR, "config", "bpa_rules")
        if not os.path.isdir(custom_dir):
            return
        for fpath in sorted(glob.glob(os.path.join(custom_dir, "*.json"))):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                added = self._add_rule_dicts(
                    _rules_from_payload(data), f"custom:{os.path.basename(fpath)}"
                )
                if added:
                    logger.info(f"Loaded {added} custom BPA rules from {os.path.basename(fpath)}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load custom BPA rules from {fpath}: {e}")

    @staticmethod
    def _get_builtin_rules() -> List[Dict[str, Any]]:
        """Return built-in enhanced BPA rule definitions.

        2026-09-22 (I-12): DAX_CALCULATE_COUNTROWS (flagged the textbook
        pattern), DAX_NESTED_IF, NAMING_MEASURE_LEADING_TRAILING_SPACE,
        FORMAT_MEASURE_NO_FORMAT_STRING and REL_BIDIRECTIONAL_WARNING
        (duplicates of rules-file rules — every hit was reported twice) were
        removed; CALCGROUP_ORDINAL_GAPS now really checks the ordinals and
        REL_TABLE_NO_RELATIONSHIPS uses ``UsedInRelationships``.
        """
        return [
            {
                "ID": "DAX_ISBLANK_ZERO",
                "Name": "Simplify IF(ISBLANK(...), 0, ...)",
                "Category": "DAX Expressions",
                "Severity": 1,
                "Description": (
                    "IF(ISBLANK(x), 0, x) can be simplified to x + 0 or COALESCE(x, 0) "
                    "for cleaner DAX."
                ),
                "Scope": "Measure",
                "Expression": 'RegEx.IsMatch(Expression, "IF\\s*\\(\\s*ISBLANK\\s*\\(.+?\\)\\s*,\\s*0\\b", "(?i)")',
                "FixExpression": "Use COALESCE(expression, 0) or expression + 0 instead.",
            },
            {
                "ID": "NAMING_TABLE_CONVENTION",
                "Name": "Table name should follow consistent naming convention",
                "Category": "Naming",
                "Severity": 1,
                "Description": (
                    "Table names with leading/trailing spaces or a leading underscore/"
                    "hyphen are discouraged."
                ),
                "Scope": "Table, CalculatedTable",
                "Expression": 'RegEx.IsMatch(Name, "^[\\s_\\-]|\\s$")',
                "FixExpression": "Rename the table to follow your project's naming convention.",
            },
            {
                "ID": "NAMING_COLUMN_SPECIAL_CHAR_START",
                "Name": "Column name starts with special character",
                "Category": "Naming",
                "Severity": 2,
                "Description": (
                    "Column names should not start with special characters like @, #, $. "
                    "Letters of any script (Ü, é, CJK) are fine."
                ),
                "Scope": "DataColumn, CalculatedColumn, CalculatedTableColumn",
                "Expression": 'RegEx.IsMatch(Name, "^[^\\w]")',
                "FixExpression": "Rename the column to start with a letter, number, or underscore.",
            },
            {
                "ID": "FORMAT_PERCENTAGE_CONSISTENCY",
                "Name": "Percentage measure should use % format",
                "Category": "Formatting",
                "Severity": 1,
                "Description": (
                    "Measures whose names suggest a percentage (containing '%', 'pct', or "
                    "'percent') should use a percentage format string."
                ),
                "Scope": "Measure",
                "Expression": (
                    'RegEx.IsMatch(Name, "(%|\\bpct\\b|percent)", "(?i)") '
                    'and not FormatString.Contains("%") '
                    "and string.IsNullOrWhiteSpace(FormatStringExpression)"
                ),
                "FixExpression": "Set the format string to '0.0%' or '0.00%'.",
            },
            {
                "ID": "REL_TABLE_NO_RELATIONSHIPS",
                "Name": "Table has no relationships",
                "Category": "Relationships",
                "Severity": 1,
                "Description": (
                    "Tables without any relationships may be disconnected from the model. "
                    "Measure tables, calculated (parameter / field-parameter) tables and "
                    "calculation groups are not flagged."
                ),
                "Scope": "Table",
                "Expression": "not UsedInRelationships.Any() and Measures.Count() == 0",
                "FixExpression": (
                    "Create a relationship to connect this table to the model, or document why "
                    "it is intentionally disconnected."
                ),
            },
            {
                "ID": "CALCGROUP_ORDINAL_GAPS",
                "Name": "Calculation group has ordinal gaps",
                "Category": "Calculation Groups",
                "Severity": 1,
                "Description": (
                    "Calculation items with explicit ordinals should be numbered "
                    "0..n-1 without gaps or duplicates for predictable ordering."
                ),
                "Scope": "CalculationGroup",
                "Expression": (
                    "CalculationItems.Where(Ordinal >= 0).Count() > 1 and ("
                    "CalculationItems.Where(Ordinal >= 0).Select(Ordinal).Distinct().Count() "
                    "<> CalculationItems.Where(Ordinal >= 0).Count() or "
                    "CalculationItems.Where(Ordinal >= 0).Max(Ordinal) "
                    "- CalculationItems.Where(Ordinal >= 0).Min(Ordinal) + 1 "
                    "<> CalculationItems.Where(Ordinal >= 0).Count())"
                ),
                "FixExpression": "Re-number calculation item ordinals sequentially (0, 1, 2...).",
            },
            {
                "ID": "PERF_TABLE_TOO_MANY_COLUMNS",
                "Name": "Table has too many columns",
                "Category": "Performance",
                "Severity": 1,
                "Description": (
                    "Tables with more than 30 columns may indicate a need to split into "
                    "multiple tables or remove unused columns."
                ),
                "Scope": "Table",
                "Expression": "Columns.Count > 30 and Columns.Count <= 50",
                "FixExpression": "Remove unused columns or split the table into smaller tables.",
            },
            {
                "ID": "PERF_WIDE_TABLE_WARNING",
                "Name": "Table has excessive columns (>50)",
                "Category": "Performance",
                "Severity": 2,
                "Description": (
                    "Tables with more than 50 columns often carry columns nobody uses; the "
                    "column count itself has no measured impact (severity is WARNING, not ERROR: "
                    "ERROR is reserved for a measured defect or a hard runtime error). "
                    "Consider removing unnecessary columns."
                ),
                "Scope": "Table",
                "Expression": "Columns.Count > 50",
                "FixExpression": (
                    "Aggressively prune unused columns. Consider splitting into fact and "
                    "dimension tables."
                ),
            },
        ]

    def load_rules(self, rules_file_path: str) -> None:
        """Load BPA rules from a JSON file (replaces the current rule set)."""
        try:
            data = load_json(rules_file_path)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Error loading BPA rules: {str(e)}")
            raise
        self.rules = []
        self._add_rule_dicts(_rules_from_payload(data), f"file:{os.path.basename(rules_file_path)}")
        logger.info(f"Loaded {len(self.rules)} BPA rules")

    def merge_rules_file(self, rules_file_path: str) -> int:
        """Append rules from an external file to THIS analyzer's rule set.

        Kept for backward compatibility; ``06_Analysis_Operations`` no longer
        uses it (external rules are per call — see :func:`load_rules_file` and
        the ``extra_rules`` argument of :meth:`run`). Returns the number of
        rules added.
        """
        with self._lock:
            rules, _report = load_rules_file(
                rules_file_path, {r.id for r in self.rules}, source="external"
            )
            self.rules.extend(rules)
        logger.info(f"Merged {len(rules)} external BPA rules from {rules_file_path}")
        return len(rules)

    # ── running ─────────────────────────────────────────────────────────

    @staticmethod
    def _select_rules(rules: List[BPARule], cfg: Dict[str, Any]) -> List[BPARule]:
        sev = str(cfg.get("severity_at_least", "") or "").upper()
        level = {"INFO": 1, "WARNING": 2, "ERROR": 3}.get(sev)
        if level:
            rules = [r for r in rules if int(r.severity) >= level]
        ids = cfg.get("rule_ids") or []
        if isinstance(ids, (list, tuple, set)) and ids:
            wanted_ids = {str(i) for i in ids}
            rules = [r for r in rules if r.id in wanted_ids]
        cats = cfg.get("include_categories") or []
        if isinstance(cats, list) and cats:
            wanted = {str(c).lower() for c in cats}
            rules = [r for r in rules if str(r.category).lower() in wanted]
        try:
            mr_val = cfg.get("max_rules")
            mr = int(mr_val) if mr_val is not None and str(mr_val).strip() != "" else None
        except (TypeError, ValueError):
            mr = None
        if mr and mr > 0 and len(rules) > mr:
            rules = rules[:mr]
        return rules

    @staticmethod
    def _int_cfg(cfg: Dict[str, Any], *keys: str) -> Optional[int]:
        for key in keys:
            val = cfg.get(key)
            if val is None or str(val).strip() == "":
                continue
            try:
                n = int(val)
            except (TypeError, ValueError):
                continue
            if n > 0:
                return n
        return None

    def run(
        self,
        tmsl_json: Union[str, bytes, Dict[str, Any]],
        cfg: Optional[Dict[str, Any]] = None,
        *,
        extra_rules: Optional[List[BPARule]] = None,
    ) -> BPARunResult:
        """Evaluate the rule set against *tmsl_json* and return the result.

        cfg keys (all optional): ``max_seconds`` (global budget, default 60),
        ``per_rule_max_ms`` (a rule running longer is truncated and reported),
        ``severity_at_least``, ``include_categories``, ``max_rules``,
        ``max_tables``, ``max_items_per_rule`` (+ per-kind
        ``max_measures_per_rule`` / ``max_columns_per_rule`` /
        ``max_relationships_per_rule``).

        Pure with respect to the analyzer: concurrent calls do not interfere.
        """
        started = time.perf_counter()
        cfg = dict(cfg or {})
        with self._lock:
            rules = list(self.rules)
        for r in extra_rules or []:
            if all(r.id != x.id for x in rules):
                rules.append(r)
        rules = self._select_rules(rules, cfg)

        model = resolve_tmsl_model(tmsl_json)
        notes: List[str] = []
        if not model:
            result = BPARunResult(
                violations=[],
                notes=["No model found in the TMSL input; BPA did not run."],
                coverage=self._coverage(rules, {}, set(), {}, [], [], {}, 0),
                model_found=False,
            )
            self._remember(result)
            return result

        mt = self._int_cfg(cfg, "max_tables")
        if mt and isinstance(model.get("tables"), list) and len(model["tables"]) > mt:
            model = dict(model)
            model["tables"] = model["tables"][:mt]
            notes.append(f"BPA sampled the first {mt} tables (max_tables).")

        try:
            max_seconds = float(cfg.get("max_seconds", 60) or 60)
        except (TypeError, ValueError):
            max_seconds = 60.0
        try:
            per_rule_ms = float(cfg.get("per_rule_max_ms") or 0)
        except (TypeError, ValueError):
            per_rule_ms = 0.0
        generic_cap = self._int_cfg(cfg, "max_items_per_rule")
        caps = {
            "Measure": self._int_cfg(cfg, "max_measures_per_rule") or generic_cap,
            "Column": self._int_cfg(cfg, "max_columns_per_rule") or generic_cap,
            "Relationship": self._int_cfg(cfg, "max_relationships_per_rule") or generic_cap,
        }

        view = ModelView(model)
        evaluator = Evaluator(view.model_node)
        violations: List[BPAViolation] = []
        unsupported: Dict[str, str] = {}
        evaluated_ids: set = set()
        errors: Dict[str, Dict[str, Any]] = {}
        truncated: List[str] = []
        not_run: List[str] = []
        partial_scopes: Dict[str, List[str]] = {}
        objects_evaluated = 0

        for idx, rule in enumerate(rules):
            if (time.perf_counter() - started) > max_seconds:
                not_run.extend(r.id for r in rules[idx:])
                notes.append(
                    f"BPA time budget of {int(max_seconds)}s reached after {idx} of "
                    f"{len(rules)} rules; {len(rules) - idx} rule(s) were not run."
                )
                break
            if rule.python_check is not None:
                try:
                    hits = rule.python_check(model)
                except Exception as exc:  # noqa: BLE001 - one rule must not stop the run
                    unsupported[rule.id] = f"{type(exc).__name__}: {exc}"
                    continue
                for otype, oname, tname, details in hits:
                    violations.append(
                        BPAViolation(
                            rule_id=rule.id,
                            rule_name=rule.name,
                            category=rule.category,
                            severity=rule.severity,
                            description=rule.description,
                            object_type=otype,
                            object_name=oname,
                            table_name=tname,
                            fix_expression=rule.fix_expression,
                            details=details,
                        )
                    )
                evaluated_ids.add(rule.id)
                objects_evaluated += len(hits)
                continue
            prepare_rule(rule)
            if rule.unsupported_reason or rule.compiled is None:
                unsupported[rule.id] = rule.unsupported_reason or "not compiled"
                continue
            scopes: List[str] = []
            bad_scopes: List[str] = []
            for s in rule.scope:
                canon = normalize_scope(s)
                if s.strip().lower() == "datasource":
                    scopes.extend(["ProviderDataSource", "StructuredDataSource"])
                elif canon is None:
                    bad_scopes.append(s)
                elif canon not in scopes:
                    scopes.append(canon)
            if not scopes:
                unsupported[rule.id] = f"unsupported scope(s): {', '.join(bad_scopes) or '(none)'}"
                continue
            if bad_scopes:
                partial_scopes[rule.id] = bad_scopes

            rule_violations: List[BPAViolation] = []
            rule_start = time.perf_counter()
            n_eval = 0
            n_err = 0
            first_err: Optional[str] = None
            per_kind: Dict[str, int] = {}
            was_truncated = False
            try:
                for scope in scopes:
                    for obj in view.iter_scope(scope):
                        cap = caps.get(obj.kind)
                        if cap and per_kind.get(obj.kind, 0) >= cap:
                            was_truncated = True
                            break
                        per_kind[obj.kind] = per_kind.get(obj.kind, 0) + 1
                        try:
                            verdict = evaluator.evaluate(rule.compiled, obj)
                        except BpaUnsupported:
                            raise
                        except (BpaRuntimeError, BpaExpressionError) as exc:
                            n_err += 1
                            first_err = first_err or str(exc)
                            continue
                        except (RecursionError, TypeError, ValueError, AttributeError) as exc:
                            n_err += 1
                            first_err = first_err or f"{type(exc).__name__}: {exc}"
                            continue
                        if not isinstance(verdict, bool):
                            raise BpaUnsupported(
                                f"expression evaluates to {type(verdict).__name__}, not a boolean"
                            )
                        n_eval += 1
                        if verdict:
                            rule_violations.append(self._violation(rule, obj))
                        if (
                            per_rule_ms
                            and (time.perf_counter() - rule_start) * 1000.0 > per_rule_ms
                        ):
                            was_truncated = True
                            break
                    if was_truncated:
                        break
            except BpaUnsupported as exc:
                unsupported[rule.id] = str(exc)
                continue
            objects_evaluated += n_eval
            if n_err:
                errors[rule.id] = {"errors": n_err, "evaluated": n_eval, "first_error": first_err}
            if n_eval == 0 and n_err > 0:
                unsupported[rule.id] = f"evaluation failed on every object: {first_err}"
                continue
            evaluated_ids.add(rule.id)
            if was_truncated:
                truncated.append(rule.id)
                notes.append(
                    f"Rule {rule.id} truncated by a per-rule budget; results may be partial."
                )
            violations.extend(rule_violations)

        coverage = self._coverage(
            rules,
            unsupported,
            evaluated_ids,
            errors,
            truncated,
            not_run,
            partial_scopes,
            objects_evaluated,
        )
        needs = self._annotation_gaps(rules, view, evaluated_ids)
        if needs:
            coverage["rules_needing_vertipaq_annotations"] = needs
            notes.append(
                f"{len(needs)} evaluated rule(s) read VertiPaq annotations "
                "(Vertipaq_Cardinality, Vertipaq_RowCount, ...) that this model does not "
                "carry; their clean verdict is vacuous. Run 06 full with "
                "include_vertipaq=true (or annotate the model) to evaluate them."
            )
        if coverage["rules_unsupported"] or truncated or not_run:
            notes.append(
                f"BPA coverage: {coverage['rules_evaluated']}/{coverage['rules_total']} rules "
                f"evaluated; {coverage['rules_unsupported']} unsupported, "
                f"{len(truncated)} truncated, {len(not_run)} not run. "
                "No violation from an unsupported/not-run rule does NOT mean compliant."
            )
        result = BPARunResult(
            violations=violations,
            notes=notes,
            coverage=coverage,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            cache_stats={
                "cache_size": evaluator.cache_size(),
                "cache_hits": evaluator.index_hits,
                "cache_misses": evaluator.index_misses,
            },
        )
        self._remember(result)
        return result

    @staticmethod
    def _violation(rule: BPARule, obj: Any) -> BPAViolation:
        table = getattr(obj, "table", None)
        kind = TOM_KIND_OF.get(getattr(obj, "kind", ""))
        ref = None
        if kind:
            tname = table.bpa_name if table is not None and table is not obj else None
            name = obj.bpa_name
            if obj.kind == "Relationship":
                name = str(obj.data.get("name") or obj.bpa_name)
            ref = (kind, tname, name)
        return BPAViolation(
            rule_id=rule.id,
            rule_name=rule.name,
            category=rule.category,
            severity=rule.severity,
            description=rule.description,
            object_type=obj.scope,
            object_name=obj.display_name,
            table_name=table.bpa_name if table is not None and table is not obj else None,
            fix_expression=rule.fix_expression,
            ref=ref,
        )

    @staticmethod
    def _coverage(
        rules: List[BPARule],
        unsupported: Dict[str, str],
        evaluated_ids: set,
        errors: Dict[str, Dict[str, Any]],
        truncated: List[str],
        not_run: List[str],
        partial_scopes: Dict[str, List[str]],
        objects_evaluated: int,
    ) -> Dict[str, Any]:
        return {
            "rules_total": len(rules),
            "rules_evaluated": len(evaluated_ids),
            "evaluated_rule_ids": sorted(evaluated_ids),
            "rules_unsupported": len(unsupported),
            "unsupported_rule_ids": sorted(unsupported),
            "unsupported_reasons": dict(sorted(unsupported.items())),
            "rules_truncated": sorted(truncated),
            "rules_not_run": list(not_run),
            "rules_with_errors": errors,
            "rules_with_unsupported_scopes": partial_scopes,
            "objects_evaluated": objects_evaluated,
        }

    @staticmethod
    def _annotation_gaps(rules: List[BPARule], view: ModelView, evaluated: set) -> List[str]:
        present: set = set()
        for coll in (view.tables, view.all_columns, view.all_measures, view.relationships):
            for node in coll:
                for a in node.data.get("annotations") or []:
                    if isinstance(a, dict) and a.get("name"):
                        present.add(str(a["name"]))
        out = []
        for r in rules:
            if r.id not in evaluated:
                continue
            wanted = {
                n
                for n in _ANNOTATION_RE.findall(r.expression)
                if n.lower().startswith(("vertipaq", "longlength", "datetimewith"))
            }
            if wanted and not (wanted & present):
                out.append(r.id)
        return out

    def _remember(self, result: BPARunResult) -> None:
        with self._lock:
            self._last = result

    # ── legacy entry points (wrap run(); remember the last result) ─────────

    def analyze_model(
        self, tmsl_json: Union[str, Dict], max_seconds: Optional[float] = None
    ) -> List[BPAViolation]:
        """Full analysis (default 60 s budget, no per-rule truncation)."""
        cfg: Dict[str, Any] = {"max_seconds": max_seconds if max_seconds is not None else 60}
        return self.run(tmsl_json, cfg).violations

    @property
    def violations(self) -> List[BPAViolation]:
        with self._lock:
            return list(self._last.violations) if self._last else []

    def get_run_notes(self) -> List[str]:
        """Notes from the most recent run (budgets, coverage)."""
        with self._lock:
            return list(self._last.notes) if self._last else []

    def get_coverage(self) -> Dict[str, Any]:
        """Rule-evaluation coverage of the most recent run."""
        with self._lock:
            if self._last is None:
                return self._coverage(self.rules, {}, set(), {}, [], [], {}, 0)
            return dict(self._last.coverage)
