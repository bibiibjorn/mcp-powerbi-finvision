"""Select-and-patch engine: ``03_Batch_Operations object_type=patch`` (F-03, I-30 #1).

A *selection language* and a *property-patch language* over the live model, built on the
Dynamic-LINQ evaluator BPA rules already use (:mod:`core.analysis.bpa_expression`) and the TMSL
object view of :mod:`core.analysis.bpa_model`:

    select  Measures.Where(Expression.Contains("SUMX") and not IsHidden)
    patch   {"DisplayFolder": "{Table.Name}\\\\Iterators", "Description": "{Name} (iterator)"}

Safety model (a patch language is a foot-gun, so it is narrow):

* ``select`` must start with a model collection (``Measures``, ``Columns``, ``Tables``,
  ``Hierarchies``, ``Partitions``, ``Relationships``, ``Roles``, ``Perspectives``, ``Expressions``,
  ``DataSources``, ``CalculationItems``) and evaluates to objects; at most ``PATCH_MAX_OBJECTS``.
* ``patch`` properties are the ALLOW-LIST shared with ``02_Model_Operations object_type=any``
  (:data:`core.operations.tom_properties.ALLOWED`) - names, expressions, data types and keys are
  refused. String values are templates: ``{Name}``, ``{Table.Name}``, any scalar member of the
  object; ``{{`` / ``}}`` are literal braces. ``{"Delete()": true}`` deletes the selection.
* a plan is computed from the TMSL snapshot of the SAME open model: every object's ``before`` and
  ``after`` value is in the preview and in the ``plan_hash``; apply recomputes the plan against the
  current model and refuses (``stale_plan``) when the hash differs.
* apply = every ``setattr`` / ``Remove`` then ONE ``write_lane.commit`` (journaled, paced,
  undoable); any failure while staging rolls the whole batch back.

``from_bpa`` swaps the explicit ``select`` / ``patch`` for BPA rule ids: the rules are run, each
violation's ``FixExpression`` is translated by :mod:`core.analysis.bpa_fix` (the supported
subset; anything else comes back as ``fix_unsupported`` with the expression text) and the result is
the same plan.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.analysis.bpa_expression import (
    BpaExpressionError,
    EnumValue,
    Evaluator,
    compile_expression,
)
from core.analysis.bpa_fix import DELETE_KEY, translate_fix_expression
from core.analysis.bpa_model import TOM_KIND_OF, ModelView, Node, resolve_tmsl_model
from core.operations import tom_properties as tp
from core.operations.write_lane import commit as _lane_commit
from core.operations.write_lane import safe_undo

logger = logging.getLogger(__name__)

__all__ = [
    "PatchEngine",
    "PatchError",
    "PATCH_MAX_OBJECTS",
    "PATCH_CATASTROPHIC_OBJECTS",
    "DELETE_KINDS",
    "COLLECTIONS",
    "build_plan",
    "build_bpa_plan",
    "plan_hash",
    "render_template",
    "select_nodes",
    "serialize_model",
]

#: hard cap on the objects one patch may touch.
PATCH_MAX_OBJECTS = 500
#: above this many objects (or on any delete of more than 10, or a HIGH_IMPACT property) the
#: handler classifies the apply CATASTROPHIC (config ``model_write.catastrophic_patch_objects``).
PATCH_CATASTROPHIC_OBJECTS = 50
#: preview rows returned inline (the plan_hash still covers every object).
PREVIEW_ROWS = 200

#: ``select`` head -> Model member the evaluator resolves.
COLLECTIONS: Dict[str, str] = {
    "measures": "AllMeasures",
    "columns": "AllColumns",
    "tables": "Tables",
    "hierarchies": "AllHierarchies",
    "levels": "AllLevels",
    "partitions": "AllPartitions",
    "relationships": "Relationships",
    "calculationitems": "AllCalculationItems",
    "roles": "Roles",
    "perspectives": "Perspectives",
    "cultures": "Cultures",
    "expressions": "Expressions",
    "datasources": "DataSources",
    "functions": "Functions",
}

_KIND_OF = TOM_KIND_OF

#: kinds ``Delete()`` may remove (each is a plain member of a parent collection).
DELETE_KINDS = frozenset(
    {
        "measure",
        "column",
        "hierarchy",
        "relationship",
        "role",
        "perspective",
        "named_expression",
        "data_source",
    }
)

_SELECT_RE = re.compile(r"^\s*(?:Model\s*\.\s*)?(?:All)?([A-Za-z]+)\b(.*)$", re.DOTALL)
_TPL_RE = re.compile(r"\{\{|\}\}|\{([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\}")


class PatchError(Exception):
    """A user-facing patch failure (carries the error envelope fields)."""

    def __init__(self, error_type: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.envelope: Dict[str, Any] = {
            "success": False,
            "error_type": error_type,
            "error": message,
            **extra,
        }


# ───────────────────────────── selection ───────────────────────────────


def parse_select(select: Any):
    """Compile a ``select`` expression; returns ``CompiledExpression``. Raises :class:`PatchError`."""
    text = str(select or "").strip()
    if not text:
        raise PatchError("invalid_parameters", "select is required (for example Measures)")
    m = _SELECT_RE.match(text)
    head = m.group(1).lower() if m else ""
    member = COLLECTIONS.get(head)
    if member is None:
        raise PatchError(
            "invalid_select",
            f"select must start with a model collection, got {text[:40]!r}",
            collections=sorted(c.capitalize() for c in COLLECTIONS),
            example='Measures.Where(Expression.Contains("SUMX") and not IsHidden)',
        )
    rewritten = f"Model.{member}{m.group(2)}"
    try:
        return compile_expression(rewritten)
    except BpaExpressionError as exc:
        raise PatchError("invalid_select", f"select does not parse: {exc}", select=text) from exc


def select_nodes(view: ModelView, select: Any) -> List[Node]:
    """The objects ``select`` evaluates to (de-duplicated, model order)."""
    compiled = parse_select(select)
    try:
        value = Evaluator(view.model_node).evaluate(compiled, view.model_node)
    except BpaExpressionError as exc:
        raise PatchError("invalid_select", f"select failed to evaluate: {exc}", select=str(select))
    except (RecursionError, TypeError, ValueError, AttributeError) as exc:
        raise PatchError(
            "invalid_select", f"select failed to evaluate: {type(exc).__name__}: {exc}"
        )
    if not isinstance(value, list) or any(not isinstance(n, Node) for n in value):
        raise PatchError(
            "invalid_select",
            "select must evaluate to a collection of model objects (end with .Where(...) or a "
            "collection name, not .Count() / .Select(...))",
            select=str(select),
        )
    seen: set = set()
    out: List[Node] = []
    for n in value:
        if id(n) not in seen:
            seen.add(id(n))
            out.append(n)
    return out


# ───────────────────────────── templates ───────────────────────────────


def _scalar_text(value: Any) -> str:
    if isinstance(value, Node):
        return value.bpa_name
    if isinstance(value, EnumValue):
        return value.name
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        raise ValueError("not a scalar")
    return str(value)


def render_template(text: str, node: Node) -> str:
    """Expand ``{Name}`` / ``{Table.Name}`` placeholders against ``node``; ``{{`` ``}}`` escape."""

    def sub(m: "re.Match[str]") -> str:
        token = m.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        cur: Any = node
        for part in m.group(1).split("."):
            getter = getattr(cur, "bpa_get", None)
            if getter is None:
                raise PatchError(
                    "invalid_template", f"placeholder {{{m.group(1)}}} is not a member"
                )
            try:
                cur = getter(part)
            except BpaExpressionError as exc:
                raise PatchError(
                    "invalid_template",
                    f"placeholder {{{m.group(1)}}} does not resolve on {node.scope} "
                    f"{node.bpa_name!r}: {exc}",
                ) from exc
        try:
            return _scalar_text(cur)
        except ValueError as exc:
            raise PatchError(
                "invalid_template", f"placeholder {{{m.group(1)}}} is not a scalar value"
            ) from exc

    return _TPL_RE.sub(sub, text)


# ─────────────────────────────── planning ──────────────────────────────


def _plain(value: Any) -> Any:
    if isinstance(value, EnumValue):
        return value.name
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)


def _same(before: Any, after: Any, *, enum: bool = False) -> bool:
    """Is ``after`` already the current value (enums compare case-insensitively: TMSL says
    ``bothDirections``, TOM ``BothDirections``)."""
    if isinstance(before, bool):
        flag = tp._as_bool(after)  # noqa: SLF001
        return flag is not None and flag == before
    if before is None or after is None:
        return before is None and after is None
    if enum and isinstance(before, str) and isinstance(after, str):
        return tp._norm(before) == tp._norm(after)  # noqa: SLF001
    if isinstance(before, (int, float)) and not isinstance(after, bool):
        try:
            return float(after) == float(before)
        except (TypeError, ValueError):
            return False
    return before == after


def _ref_of(node: Node) -> Tuple[str, Optional[str], str]:
    """``(tom_kind, table, name)`` of a patchable node (``("?", ...)`` for the others)."""
    kind = _KIND_OF.get(node.kind, "?")
    table = None
    if node.table is not None and node.table is not node:
        table = node.table.bpa_name
    name = node.bpa_name
    if node.kind == "Relationship":
        name = str(node.data.get("name") or node.bpa_name)
    return kind, table, name


def _label(node: Node, kind: str, table: Optional[str], name: str) -> str:
    return f"{node.scope} {table + '.' if table else ''}{node.display_name}"


def _validate_patch(patch: Any) -> Tuple[Dict[str, Any], bool]:
    if not isinstance(patch, dict) or not patch:
        raise PatchError(
            "invalid_parameters",
            "patch must be a non-empty object {Property: value-template}",
            example={"DisplayFolder": "{Table.Name}", "IsHidden": True},
        )
    delete = False
    props: Dict[str, Any] = {}
    for key, value in patch.items():
        if str(key).strip().lower() in (DELETE_KEY.lower(), "delete"):
            if value not in (True, "true", "True"):
                raise PatchError("invalid_parameters", f"{DELETE_KEY} must be true")
            delete = True
            continue
        if isinstance(value, (list, dict)):
            raise PatchError(
                "invalid_parameters",
                f"patch value for {key!r} must be a string, number, boolean or null",
            )
        props[str(key)] = value
    if delete and props:
        raise PatchError(
            "invalid_parameters", f"{DELETE_KEY} cannot be combined with property assignments"
        )
    return props, delete


def _new_plan(mode: str, select: Any, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    plan: Dict[str, Any] = {
        "mode": mode,
        "select": select,
        "entries": [],
        "errors": [],
        "unsupported": [],
    }
    plan.update(extra or {})
    return plan


def _entry(node: Node, kind: str, table: Optional[str], name: str, delete: bool) -> Dict[str, Any]:
    return {
        "kind": kind,
        "scope": node.scope,
        "table": table,
        "name": name,
        "label": _label(node, kind, table, name),
        "delete": delete,
        "changes": [],
    }


def _add_changes(
    entry: Dict[str, Any],
    node: Node,
    kind: str,
    props: Dict[str, Any],
    errors: List[Dict[str, Any]],
) -> None:
    for prop, raw in props.items():
        canon = tp._canonical(kind, prop)  # noqa: SLF001 - the shared allow-list
        if canon is None:
            errors.append(
                {
                    "object": entry["label"],
                    "property": prop,
                    "error_type": "denied_property",
                    "error": f"{prop!r} is not patchable on a {kind} "
                    + tp._DENY_REASONS.get(
                        tp._norm(prop), "(not on the allow-list)"
                    ),  # noqa: SLF001
                    "settable_properties": list(tp.ALLOWED.get(kind, ())),
                }
            )
            continue
        is_enum = False
        try:
            raw_before = node.bpa_get(canon)
            is_enum = isinstance(raw_before, EnumValue)
            before = _plain(raw_before)
        except BpaExpressionError:
            before = None
        try:
            after = render_template(raw, node) if isinstance(raw, str) else raw
        except PatchError as exc:
            errors.append(
                {
                    "object": entry["label"],
                    "property": canon,
                    "error_type": exc.envelope["error_type"],
                    "error": exc.envelope["error"],
                }
            )
            continue
        entry["changes"].append(
            {
                "property": canon,
                "before": before,
                "after": after,
                "action": "noop" if _same(before, after, enum=is_enum) else "set",
                "high_impact": (kind, canon) in tp.HIGH_IMPACT or None,
            }
        )


def _finish(plan: Dict[str, Any]) -> Dict[str, Any]:
    entries = plan["entries"]
    plan["object_count"] = len(entries)
    plan["delete_count"] = sum(1 for e in entries if e["delete"])
    plan["set_count"] = sum(1 for e in entries for c in e["changes"] if c["action"] == "set")
    plan["noop_count"] = sum(1 for e in entries for c in e["changes"] if c["action"] == "noop")
    plan["changed_object_count"] = sum(
        1 for e in entries if e["delete"] or any(c["action"] == "set" for c in e["changes"])
    )
    plan["high_impact"] = any(c.get("high_impact") for e in entries for c in e["changes"])
    kinds = sorted({e["kind"] for e in entries if e["delete"]})
    plan["delete_kinds"] = kinds
    plan["plan_hash"] = plan_hash(plan)
    return plan


def plan_hash(plan: Dict[str, Any]) -> str:
    """SHA-256 over the plan content (mode, select, every object's before/after)."""
    body = {
        "mode": plan.get("mode"),
        "select": plan.get("select"),
        "patch": plan.get("patch"),
        "rules": plan.get("rules"),
        "entries": [
            [
                e["kind"],
                e["table"],
                e["name"],
                e["delete"],
                [[c["property"], c["before"], c["after"]] for c in e["changes"]],
            ]
            for e in plan["entries"]
        ],
    }
    blob = json.dumps(body, sort_keys=True, default=str, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_plan(
    model_dict: Dict[str, Any], select: Any, patch: Any, *, max_objects: int = PATCH_MAX_OBJECTS
) -> Dict[str, Any]:
    """Pure plan for ``select`` + ``patch`` over a TMSL model dict (no TOM). Raises PatchError."""
    props, delete = _validate_patch(patch)
    view = ModelView(model_dict)
    nodes = select_nodes(view, select)
    if len(nodes) > max_objects:
        raise PatchError(
            "too_many_objects",
            f"select matched {len(nodes)} objects; a patch is capped at {max_objects}. "
            "Narrow the Where(...) or split the work.",
            matched=len(nodes),
            cap=max_objects,
        )
    plan = _new_plan("select", str(select).strip(), {"patch": {k: props[k] for k in props}})
    if delete:
        plan["patch"] = {DELETE_KEY: True}
    for node in nodes:
        kind, table, name = _ref_of(node)
        if kind == "?":
            plan["errors"].append(
                {
                    "object": node.scope + " " + node.display_name,
                    "error_type": "object_not_patchable",
                    "error": f"{node.scope} objects cannot be patched",
                }
            )
            continue
        entry = _entry(node, kind, table, name, delete)
        if delete:
            if kind not in DELETE_KINDS:
                plan["errors"].append(
                    {
                        "object": entry["label"],
                        "error_type": "object_not_deletable",
                        "error": f"Delete() is not supported for {kind} objects "
                        f"(supported: {sorted(DELETE_KINDS)})",
                    }
                )
                continue
        else:
            _add_changes(entry, node, kind, props, plan["errors"])
        plan["entries"].append(entry)
    return _finish(plan)


def build_bpa_plan(
    model_dict: Dict[str, Any],
    rule_ids: List[str],
    analyzer: Any,
    extra_rules: Optional[List[Any]] = None,
    *,
    max_objects: int = PATCH_MAX_OBJECTS,
) -> Dict[str, Any]:
    """Pure plan from BPA findings: run ``rule_ids``, translate each rule's FixExpression."""
    ids = [str(r).strip() for r in rule_ids if str(r).strip()]
    if not ids:
        raise PatchError("invalid_parameters", "from_bpa needs at least one BPA rule id")
    pool = {r.id: r for r in list(getattr(analyzer, "rules", []) or []) + list(extra_rules or [])}
    unknown = [i for i in ids if i not in pool]
    if unknown:
        raise PatchError(
            "unknown_rule", f"unknown BPA rule id(s): {unknown}", unknown_rule_ids=unknown
        )
    run = analyzer.run(
        model_dict, {"rule_ids": ids, "max_seconds": 120}, extra_rules=list(extra_rules or [])
    )
    view = ModelView(model_dict)
    index: Dict[Tuple[str, Optional[str], str], Node] = {}
    for scope in (
        "Table",
        "CalculatedTable",
        "CalculationGroup",
        "DataColumn",
        "CalculatedColumn",
        "CalculatedTableColumn",
        "Measure",
        "Hierarchy",
        "Partition",
        "Relationship",
        "ModelRole",
        "NamedExpression",
        "Perspective",
        "ProviderDataSource",
        "StructuredDataSource",
    ):
        for node in view.iter_scope(scope):
            index.setdefault(_ref_of(node), node)
    plan = _new_plan("bpa", None, {"rules": sorted(ids)})
    by_obj: Dict[Tuple[str, Optional[str], str], Dict[str, Any]] = {}
    unsupported: Dict[str, Dict[str, Any]] = {}
    for v in run.violations:
        rule = pool.get(v.rule_id)
        ref = getattr(v, "ref", None)
        if rule is None or ref is None:
            unsupported.setdefault(
                v.rule_id,
                {
                    "rule_id": v.rule_id,
                    "fix_expression": getattr(rule, "fix_expression", None),
                    "reason": "the finding carries no object reference",
                },
            )
            continue
        kind = ref[0]
        fix = translate_fix_expression(str(rule.fix_expression or ""), kind)
        if not fix.supported:
            unsupported.setdefault(
                v.rule_id,
                {
                    "rule_id": v.rule_id,
                    "fix_expression": rule.fix_expression,
                    "reason": fix.unsupported or "no FixExpression",
                },
            )
            continue
        node = index.get(ref)
        if node is None:
            continue
        entry = by_obj.get(ref)
        if entry is None:
            entry = _entry(node, kind, ref[1], ref[2], False)
            by_obj[ref] = entry
        if fix.delete:
            if kind not in DELETE_KINDS:
                plan["errors"].append(
                    {
                        "object": entry["label"],
                        "rule_id": v.rule_id,
                        "error_type": "object_not_deletable",
                        "error": f"Delete() is not supported for {kind} objects",
                    }
                )
                continue
            entry["delete"] = True
        else:
            _add_changes(entry, node, kind, fix.patch, plan["errors"])
    for entry in by_obj.values():
        if entry["delete"] and entry["changes"]:
            plan["errors"].append(
                {
                    "object": entry["label"],
                    "error_type": "conflicting_fixes",
                    "error": "one rule deletes the object while another patches it; run them separately",
                }
            )
            continue
        merged: Dict[str, Dict[str, Any]] = {}
        for c in entry["changes"]:
            prev = merged.get(c["property"])
            if prev is not None and prev["after"] != c["after"]:
                plan["errors"].append(
                    {
                        "object": entry["label"],
                        "property": c["property"],
                        "error_type": "conflicting_fixes",
                        "error": f"two rules set {c['property']} to different values",
                    }
                )
                continue
            merged[c["property"]] = c
        entry["changes"] = list(merged.values())
        if entry["changes"] or entry["delete"]:
            plan["entries"].append(entry)
    if len(plan["entries"]) > max_objects:
        raise PatchError(
            "too_many_objects",
            f"the BPA findings touch {len(plan['entries'])} objects; capped at {max_objects}. "
            "Run fewer rules per call.",
            matched=len(plan["entries"]),
            cap=max_objects,
        )
    plan["unsupported"] = sorted(unsupported.values(), key=lambda u: u["rule_id"])
    return _finish(plan)


# ───────────────────────────── live model ──────────────────────────────


def serialize_model(model: Any) -> Optional[Dict[str, Any]]:
    """TMSL dict of the OPEN TOM model (the same options ``06 full`` serialises with)."""
    from Microsoft.AnalysisServices.Tabular import JsonSerializer  # type: ignore

    text = None
    try:
        from Microsoft.AnalysisServices.Tabular import JsonSerializeOptions  # type: ignore

        options = JsonSerializeOptions()
        for name, value in (
            ("IgnoreInferredObjects", False),
            ("IgnoreInferredProperties", False),
            ("IgnoreTimestamps", True),
        ):
            try:
                setattr(options, name, value)
            except Exception:  # noqa: BLE001 - older TOM versions
                pass
        text = JsonSerializer.SerializeObject(model, options)
    except Exception:  # noqa: BLE001
        text = JsonSerializer.SerializeObject(model)
    return resolve_tmsl_model(text)


_PARENT = {
    "relationship": "Relationships",
    "role": "Roles",
    "perspective": "Perspectives",
    "named_expression": "Expressions",
    "data_source": "DataSources",
    "table": "Tables",
}
_TABLE_CHILD = {
    "column": "Columns",
    "measure": "Measures",
    "hierarchy": "Hierarchies",
    "partition": "Partitions",
}


def _parent_collection(model: Any, kind: str, table: Optional[str]) -> Any:
    if kind in _PARENT:
        return getattr(model, _PARENT[kind], None)
    tbl = tp.TomPropertyManager._find(model.Tables, table or "")  # noqa: SLF001
    return getattr(tbl, _TABLE_CHILD[kind], None) if tbl is not None else None


def resolve_tom(model: Any, entry: Dict[str, Any]) -> Any:
    coll = _parent_collection(model, entry["kind"], entry["table"])
    return (
        tp.TomPropertyManager._find(coll, entry["name"]) if coll is not None else None
    )  # noqa: SLF001


class PatchEngine:
    """Open the live model, plan, validate, and (on apply) write ONE batch."""

    def __init__(self) -> None:
        self._connection_state = None

    @property
    def connection_state(self):
        if self._connection_state is None:
            from core.infrastructure.connection_state import connection_state

            self._connection_state = connection_state
        return self._connection_state

    def _open(self):
        from core.operations.amo_helpers import get_server_db_model

        return get_server_db_model(self.connection_state)

    @staticmethod
    def _close(server: Any) -> None:
        try:
            if server is not None:
                server.Disconnect()
        except Exception:  # noqa: BLE001
            pass

    # ---- staging: coerce every value against the real TOM object, no writes ----------
    @staticmethod
    def _stage(
        model: Any, db: Any, plan: Dict[str, Any]
    ) -> Tuple[List[Tuple], List[Dict[str, Any]]]:
        ops: List[Tuple] = []
        errors: List[Dict[str, Any]] = []
        level = getattr(db, "CompatibilityLevel", None)
        for entry in plan["entries"]:
            obj = resolve_tom(model, entry)
            if obj is None:
                errors.append(
                    {
                        "object": entry["label"],
                        "error_type": "not_found",
                        "error": "the object is not in the open model",
                    }
                )
                continue
            if entry["delete"]:
                ops.append(("delete", entry, obj, None, None))
                continue
            props = {p.name: p for p in tp.reflect(obj)}
            for ch in entry["changes"]:
                if ch["action"] == "noop":
                    continue
                canon = ch["property"]
                meta = props.get(canon)
                if meta is None or not meta.can_write:
                    errors.append(
                        {
                            "object": entry["label"],
                            "property": canon,
                            "error_type": "feature_unsupported_by_client",
                            "error": f"the loaded TOM does not expose a writable {entry['kind']}.{canon}",
                        }
                    )
                    continue
                minimum = tp.MIN_CL.get((entry["kind"], canon))
                if minimum and level is not None and int(level) < minimum:
                    errors.append(
                        {
                            "object": entry["label"],
                            "property": canon,
                            "error_type": "compatibility_level_too_low",
                            "error": f"{entry['kind']}.{canon} needs compatibility level {minimum}+",
                            "required_compatibility_level": minimum,
                        }
                    )
                    continue
                if canon in tp._REF_PROPS:  # noqa: SLF001
                    value, cerr = tp.TomPropertyManager._resolve_ref_value(  # noqa: SLF001
                        model, obj, canon, ch["after"]
                    )
                else:
                    value, cerr = tp.TomPropertyManager._coerce(meta, ch["after"])  # noqa: SLF001
                if cerr:
                    errors.append(
                        {
                            "object": entry["label"],
                            "property": canon,
                            "error_type": cerr.get("error_type", "invalid_parameters"),
                            "error": cerr.get("error", "invalid value"),
                        }
                    )
                    continue
                ops.append(("set", entry, obj, canon, value))
        return ops, errors

    @staticmethod
    def _write(model: Any, ops: List[Tuple], plan: Dict[str, Any], label: str) -> Dict[str, Any]:
        try:
            for action, entry, obj, canon, value in ops:
                if action == "set":
                    setattr(obj, canon, value)
                else:
                    coll = _parent_collection(model, entry["kind"], entry["table"])
                    coll.Remove(obj)
            info = _lane_commit(
                model,
                label=label,
                journal_meta={"patch_plan_hash": plan["plan_hash"], "mode": plan["mode"]},
            )
        except Exception as exc:  # noqa: BLE001 - CLR interop
            try:
                safe_undo(model)
            except Exception:  # noqa: BLE001
                pass
            env = tp._tom_error(exc)  # noqa: SLF001
            env["rolled_back"] = True
            return env
        return {"success": True, **(info or {})}

    # ---- public ---------------------------------------------------------------
    def run(
        self,
        *,
        select: Any = None,
        patch: Any = None,
        from_bpa: Optional[List[str]] = None,
        preview: bool = True,
        plan_hash_arg: Optional[str] = None,
        gate: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
        bpa_analyzer: Any = None,
        bpa_extra_rules: Optional[List[Any]] = None,
        max_objects: int = PATCH_MAX_OBJECTS,
    ) -> Dict[str, Any]:
        server, db, model, err = self._open()
        if model is None:
            return {
                "success": False,
                "error_type": "connection_error",
                "error": err or "Not connected",
            }
        try:
            model_dict = serialize_model(model)
            if not model_dict:
                return {
                    "success": False,
                    "error_type": "tmsl_unavailable",
                    "error": "could not read the open model as TMSL",
                }
            try:
                if from_bpa:
                    plan = build_bpa_plan(
                        model_dict, from_bpa, bpa_analyzer, bpa_extra_rules, max_objects=max_objects
                    )
                else:
                    plan = build_plan(model_dict, select, patch, max_objects=max_objects)
            except PatchError as exc:
                return exc.envelope
            if plan["errors"]:
                return {
                    "success": False,
                    "error_type": "patch_invalid",
                    "error": f"{len(plan['errors'])} problem(s); nothing was written "
                    f"(first: {plan['errors'][0].get('error')})",
                    "errors": plan["errors"][:20],
                    "error_count": len(plan["errors"]),
                }
            ops, stage_errors = self._stage(model, db, plan)
            if stage_errors:
                return {
                    "success": False,
                    "error_type": "patch_invalid",
                    "error": f"{len(stage_errors)} problem(s) against the open model; nothing was "
                    f"written (first: {stage_errors[0].get('error')})",
                    "errors": stage_errors[:20],
                    "error_count": len(stage_errors),
                }
            if preview:
                return self._preview(plan)
            if not plan_hash_arg:
                return {
                    "success": False,
                    "error_type": "plan_hash_required",
                    "error": "plan_hash is required on apply. Call with preview=true first and pass "
                    "back its plan_hash.",
                }
            if plan_hash_arg != plan["plan_hash"]:
                return {
                    "success": False,
                    "error_type": "stale_plan",
                    "error": "The plan changed since preview (the model or the selection moved). "
                    "Re-run preview and retry.",
                    "expected": plan_hash_arg,
                    "current": plan["plan_hash"],
                }
            if not ops:
                return {
                    "success": True,
                    "applied": 0,
                    "message": "nothing to change (every selected property already has its value)",
                    "plan_hash": plan["plan_hash"],
                    "object_count": plan["object_count"],
                    "noop_count": plan["noop_count"],
                }
            if gate is not None:
                blocked = gate(plan)
                if blocked is not None:
                    return blocked
            res = self._write(model, ops, plan, label="patch/apply")
            if not res.get("success"):
                return res
            res.update(
                {
                    "applied": plan["changed_object_count"],
                    "properties_set": plan["set_count"],
                    "objects_deleted": plan["delete_count"],
                    "plan_hash": plan["plan_hash"],
                    "mode": plan["mode"],
                }
            )
            if plan["unsupported"]:
                res["fix_unsupported"] = plan["unsupported"]
            return res
        finally:
            self._close(server)

    @staticmethod
    def _preview(plan: Dict[str, Any]) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for e in plan["entries"]:
            if e["delete"]:
                rows.append({"object": e["label"], "kind": e["kind"], "action": "delete"})
                continue
            for c in e["changes"]:
                rows.append(
                    {
                        "object": e["label"],
                        "kind": e["kind"],
                        "property": c["property"],
                        "before": c["before"],
                        "after": c["after"],
                        "action": c["action"],
                    }
                )
        out: Dict[str, Any] = {
            "success": True,
            "preview": True,
            "mode": plan["mode"],
            "plan_hash": plan["plan_hash"],
            "object_count": plan["object_count"],
            "changed_object_count": plan["changed_object_count"],
            "operations": {
                "set": plan["set_count"],
                "noop": plan["noop_count"],
                "delete": plan["delete_count"],
            },
            "changes": rows[:PREVIEW_ROWS],
        }
        if plan["select"]:
            out["select"] = plan["select"]
        if plan.get("rules"):
            out["rules"] = plan["rules"]
        if len(rows) > PREVIEW_ROWS:
            out["changes_truncated"] = True
            out["changes_total"] = len(rows)
        if plan["unsupported"]:
            out["fix_unsupported"] = plan["unsupported"]
        if plan["high_impact"]:
            out["high_impact"] = True
        return out
