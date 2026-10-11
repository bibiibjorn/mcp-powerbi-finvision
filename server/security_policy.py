"""Data-vs-metadata permission split + per-model / per-op policy rules (F-22 / POL-01).

Evaluated by the dispatcher BEFORE the destructive-op gate (``ToolDispatcher.dispatch``), on the
canonical (alias-folded) arguments:

``security.allow_data_read`` (default ``true``)
    ``false`` lets every METADATA operation work and refuses the operations that return model row
    values (``server.op_classification.returns_data``: DAX result rows, ``sample_data``,
    ``distribution``, ``drill``, result exports, rendered visuals ...) with
    ``error_type: data_read_disabled``. Aggregate-only operations (``column statistics`` counts,
    ``rls_audit`` row counts, VertiPaq sizes) stay available.

``security.rules`` (default ``[]``)
    A list of rules, each ``{id?, database?, tool?, operation?, object_type?, object?, action,
    message?, allow_preview?}``. Every present matcher must match (absent = any); string matchers
    are case-insensitive globs (``*`` / ``?``; brackets are literal) and may be a list (any of). ``action`` is ``deny``
    (``policy_denied``) or ``require_confirm`` (``policy_confirmation_required`` unless the call
    carries ``confirm_destructive=true``); an unknown / missing action is read as ``deny`` (fail
    closed). The first matching rule decides.

    * ``database`` matches the connected model's name, the PBIP project name / path or the offline
      folder (any of them); when none of those can be resolved a rule that names a database
      matches (fail closed: not provably another model).
    * ``operation`` matches ``<op>``, ``<object_type>.<op>`` and ``<object_type>/<op>``, so
      ``table.delete`` also catches ``safe_delete``, the 03 batch (``tables`` -> ``table``) and a
      changeset that deletes a table.
    * ``object`` matches any table / column / measure / role ... name named in the call (also the
      ``Table[Column]`` spelling and every item of a batch / changeset).
    * a ``preview=true`` / ``dry_run=true`` call matches like the real one unless the rule sets
      ``allow_preview: true``.

Fully permissive by default: with no ``rules`` and ``allow_data_read`` true nothing is evaluated.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

__all__ = [
    "SecurityPolicy",
    "load_policy",
    "operation_spellings",
    "named_objects",
]

_PLURAL_TO_SINGULAR = {
    "tables": "table",
    "columns": "column",
    "measures": "measure",
    "relationships": "relationship",
    "hierarchies": "hierarchy",
    "partitions": "partition",
    "perspectives": "perspective",
    "cultures": "culture",
    "roles": "role",
    "annotations": "annotation",
    "functions": "function",
}
#: ``safe_*`` composites are the raw op with an extra proof: a rule on ``delete`` covers them.
_OP_FOLD = {"safe_delete": "delete", "safe_rename": "rename", "upsert": "create"}
_NAME_KEYS = (
    "name",
    "new_name",
    "table",
    "table_name",
    "column",
    "column_name",
    "measure",
    "measure_name",
    "role_name",
    "perspective_name",
    "culture_name",
    "expression_name",
    "function_name",
    "hierarchy_name",
    "partition_name",
    "calendar_name",
    "group_name",
    "query_group",
    "ref",
)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None and str(v) != ""]
    return [str(value)]


def _glob_re(pattern: str) -> "re.Pattern[str]":
    """``*`` / ``?`` globs only: ``[`` / ``]`` are literal (``Table[Column]`` is a name, not a class)."""
    body = "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern)
    return re.compile(body + r"\Z", re.IGNORECASE | re.DOTALL)


def _glob_any(patterns: Iterable[str], candidates: Iterable[str]) -> bool:
    cands = [c for c in candidates if isinstance(c, str) and c]
    for pat in patterns:
        rx = _glob_re(pat)
        if any(rx.match(c) for c in cands):
            return True
    return False


def operation_spellings(tool: str, args: Mapping[str, Any]) -> Set[str]:
    """Every spelling of the call's operation a rule's ``operation`` may name (lower-case)."""
    out: Set[str] = set()

    def add(obj: Optional[str], op: Optional[str]) -> None:
        if not op:
            return
        op = str(op).lower()
        ops = {op, _OP_FOLD.get(op, op)}
        objs = {str(obj).lower()} if obj else set()
        if obj and str(obj).lower() in _PLURAL_TO_SINGULAR:
            objs.add(_PLURAL_TO_SINGULAR[str(obj).lower()])
        for o in ops:
            out.add(o)
            for t in objs:
                out.add(f"{t}.{o}")
                out.add(f"{t}/{o}")

    operation = args.get("operation")
    if tool == "03_Batch_Operations":
        obj = args.get("object_type") or (operation if operation else None)
        batch_op = args.get("batch_operation")
        if batch_op:
            add(obj, batch_op)
        changeset = args.get("changeset")
        if isinstance(changeset, Mapping):
            for typ, ops in changeset.items():
                if isinstance(ops, Mapping):
                    for op, items in ops.items():
                        if items:
                            add(typ, op)
        if not batch_op and not isinstance(changeset, Mapping) and obj:
            out.add(str(obj).lower())
        return out
    if tool == "02_Model_Operations":
        add(args.get("object_type"), operation or "list")
        return out
    add(None, operation)
    return out


def named_objects(args: Mapping[str, Any]) -> List[str]:
    """Object names a call names (top-level, ``items`` of a batch, a changeset's items)."""
    names: List[str] = []

    def take(d: Mapping[str, Any]) -> None:
        for key in _NAME_KEYS:
            value = d.get(key)
            if isinstance(value, str) and value:
                names.append(value)
        table = d.get("table_name") or d.get("table")
        for key in ("column_name", "column", "measure_name", "measure", "name"):
            value = d.get(key)
            if isinstance(table, str) and table and isinstance(value, str) and value:
                names.append(f"{table}[{value}]")

    take(args)
    for item in args.get("items") or []:
        if isinstance(item, Mapping):
            take(item)
    changeset = args.get("changeset")
    if isinstance(changeset, Mapping):
        for ops in changeset.values():
            if not isinstance(ops, Mapping):
                continue
            for items in ops.values():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, Mapping):
                            take(item)
    return names


def _current_database_names() -> List[str]:
    """Names the connected model is known by (database, project, offline folder, XMLA model)."""
    names: List[str] = []
    try:
        from core.infrastructure import offline_model, xmla_target

        info = offline_model.info()
        if info and info.get("definition_folder"):
            folder = str(info["definition_folder"])
            names.append(folder)
            norm = folder.replace("\\", "/").rstrip("/").split("/")
            names.extend(norm[-3:])
        active = xmla_target.active()
        if active:
            names.extend(str(active.get(k)) for k in ("model", "workspace") if active.get(k))
    except Exception as e:  # noqa: BLE001 - policy lookup must never crash a call
        logger.debug("policy: lane names unavailable: %s", e)
    try:
        from core.infrastructure.connection_state import connection_state

        qe = getattr(connection_state, "query_executor", None)
        getter = getattr(qe, "_get_database_name", None)
        if callable(getter):
            db = getter()
            if db:
                names.append(str(db))
        mgr = getattr(connection_state, "connection_manager", None)
        inst = mgr.get_instance_info() if mgr is not None else None
        if isinstance(inst, dict):
            for key in ("display_name", "window_title", "file_full_path", "pbip_folder_path"):
                if inst.get(key):
                    names.append(str(inst[key]))
    except Exception as e:  # noqa: BLE001
        logger.debug("policy: connection names unavailable: %s", e)
    seen: Set[str] = set()
    out: List[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


class _Rule:
    __slots__ = (
        "id",
        "database",
        "tool",
        "operation",
        "object_type",
        "object",
        "action",
        "message",
        "allow_preview",
        "index",
    )

    def __init__(self, raw: Mapping[str, Any], index: int):
        self.index = index
        self.id = str(raw.get("id") or f"rule-{index + 1}")
        self.database = _as_list(raw.get("database"))
        self.tool = _as_list(raw.get("tool"))
        self.operation = _as_list(raw.get("operation"))
        self.object_type = _as_list(raw.get("object_type"))
        self.object = _as_list(raw.get("object"))
        action = str(raw.get("action") or "deny").strip().lower()
        self.action = action if action in ("deny", "require_confirm") else "deny"
        self.message = str(raw.get("message") or "")
        self.allow_preview = _truthy(raw.get("allow_preview"))

    def matches(
        self,
        tool: str,
        args: Mapping[str, Any],
        db_names,
        *,
        preview: bool,
    ) -> Optional[bool]:
        """True / False; the database lookup is lazy (``db_names()``) because it can be a round trip."""
        if preview and self.allow_preview:
            return False
        if self.tool and not _glob_any(self.tool, [tool]):
            return False
        if self.operation and not _glob_any(self.operation, operation_spellings(tool, args)):
            return False
        if self.object_type and not _glob_any(
            self.object_type, [str(args.get("object_type") or "")]
        ):
            return False
        if self.object and not _glob_any(self.object, named_objects(args)):
            return False
        if self.database:
            names = db_names()
            if names and not _glob_any(self.database, names):
                return False
            # no resolvable name: cannot prove it is another model -> the rule applies
        return True


class SecurityPolicy:
    """The parsed ``security.allow_data_read`` + ``security.rules``."""

    def __init__(self, allow_data_read: bool = True, rules: Optional[List[Any]] = None):
        self.allow_data_read = bool(allow_data_read)
        self.rules: List[_Rule] = []
        self.invalid_rules = 0
        for i, raw in enumerate(rules or []):
            if isinstance(raw, Mapping):
                self.rules.append(_Rule(raw, i))
            else:
                # not a rule object: counted + logged (a typo must be visible), nothing to match
                self.invalid_rules += 1
                logger.error("security.rules[%d] is not an object; ignored", i)

    @property
    def active(self) -> bool:
        return (not self.allow_data_read) or bool(self.rules)

    def evaluate(
        self,
        tool: str,
        args: Mapping[str, Any],
        label: str,
        *,
        returns_data: bool,
        confirmed: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """An ``error_envelope`` that refuses the call, or None."""
        if not self.active:
            return None
        if returns_data and not self.allow_data_read:
            return error_envelope(
                "data_read_disabled",
                f"{label} returns model data and security.allow_data_read is false. Metadata "
                "operations (names, definitions, measure expressions, relationships, counts) keep "
                "working; aggregate-only operations such as `02_Model_Operations column "
                "statistics` stay available.",
                operation=label,
                returns_data=True,
            )
        if not self.rules:
            return None
        preview = _truthy(args.get("preview")) or _truthy(args.get("dry_run"))
        cache: Dict[str, List[str]] = {}

        def db_names() -> List[str]:
            if "n" not in cache:
                cache["n"] = _current_database_names()
            return cache["n"]

        for rule in self.rules:
            try:
                hit = rule.matches(tool, args, db_names, preview=preview)
            except Exception as e:  # noqa: BLE001 - a rule that cannot be evaluated fails closed
                logger.error("security rule %s failed (%s); treating as a match", rule.id, e)
                hit = True
            if not hit:
                continue
            if rule.action == "require_confirm":
                if confirmed:
                    return None  # the break-glass flag satisfies a require_confirm rule
                return error_envelope(
                    "policy_confirmation_required",
                    f"{label} matches security rule '{rule.id}' (require_confirm)"
                    + (f": {rule.message}" if rule.message else "")
                    + ". Re-call with confirm_destructive=true to proceed.",
                    operation=label,
                    rule=rule.id,
                )
            return error_envelope(
                "policy_denied",
                f"{label} is denied by security rule '{rule.id}'"
                + (f": {rule.message}" if rule.message else "")
                + ".",
                operation=label,
                rule=rule.id,
            )
        return None


def load_policy(cfg: Any) -> SecurityPolicy:
    """Build the policy from the config manager (``cfg.get('security.*')``)."""
    try:
        allow = cfg.get("security.allow_data_read", True)
        rules = cfg.get("security.rules", []) or []
    except Exception:  # noqa: BLE001 - config stub in tests
        return SecurityPolicy()
    if isinstance(allow, str):
        allow = allow.strip().lower() not in {"0", "false", "no", "off"}
    if not isinstance(rules, list):
        logger.error("security.rules must be a list; ignored")
        rules = []
    return SecurityPolicy(allow_data_read=True if allow is None else bool(allow), rules=rules)
