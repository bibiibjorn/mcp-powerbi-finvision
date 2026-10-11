"""
Bulk visual creation via DIRECT DISK-WRITE with container parenting.

Builds each visual.json in memory (VisualBuilder) and writes it straight to
disk — no pbir CLI subprocess. Every visual is fully assembled in memory
(container parenting, coordinate translation, ``objects`` /
``visualContainerObjects`` overrides) and written ONCE through
``pbir_executor.write_visual`` -> ``safe_io`` (dry-run aware, locked, writes
confined to the report — H-01 / A-06 / J-06).

Atomic: every spec is validated + built in memory FIRST; if any spec is
invalid, nothing is written. A genuine disk I/O error mid-write rolls back
the folders created in this call.

Role pre-flight: validates each `fields` key against VISUAL_ROLES before
building — binding to a bogus role name silently produces a blank visual.
An uncatalogued `visual_type` is a WARNING (`role_warnings`), not an error.
``card`` is authored as ``cardVisual`` whose value role is ``Data``; the
legacy ``Values`` role is remapped (H-18c).

Field refs (H-18): ``Table.Field`` splits at the FIRST dot (field names such
as ``Growth 2.0`` keep their dots); when the report's bound model
(``definition.pbir`` -> ``byPath``) is readable, the split whose table exists
wins and Measure vs Column comes from its TMDL. ``'Table'[Field]`` /
``Table[Field]`` are accepted too. Explicit ``field_types: {role: "Measure"}``
still overrides.

Containers (H-19): container coordinates are PAGE-ABSOLUTE (the whole
``parentGroupName`` chain is summed); ``add_visuals`` computes them itself
when ``container_x`` / ``container_y`` are omitted.

(The pbir CLI subprocess was removed in v7.8 — it was the only pbir caller
in the server. `subprocess` is still imported so tests can assert it is
never invoked.)
"""

from __future__ import annotations

import json  # noqa: F401 — used by helpers / kept for parity
import logging
import subprocess  # noqa: F401 — retained so tests can assert it's never called
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.pbip.authoring.id_generator import generate_visual_id
from core.pbip.authoring.pbir_executor import write_visual
from core.pbip.authoring.visual_roles import (  # noqa: F401 — re-exported for callers/tests
    InvalidRole,
    UnknownVisualType,
    validate_bindings_ex,
    validate_role,
)

logger = logging.getLogger(__name__)

PBIR_TIMEOUT_DEFAULT = 120  # retained: accepted + ignored for back-compat


class PbirAddVisualsError(Exception):
    """Raised when pbir subprocess fails or pre-flight validation rejects input."""


def _split_field(field_ref: str, known_tables: Optional[Any] = None) -> Tuple[str, str]:
    """Split a field ref into ``(table, field)``.

    Accepted forms: ``'Table'[Field]``, ``Table[Field]`` and ``Table.Field``.
    The dotted form splits at the FIRST dot (H-18b: ``m Measures.Growth 2.0``
    -> ``("m Measures", "Growth 2.0")``); when *known_tables* (the bound
    model's table names) is given, the first split whose table exists wins,
    so a table name containing a dot still resolves.
    """
    if not isinstance(field_ref, str):
        raise ValueError(f"field must be 'Table.Field', got {field_ref!r}")
    ref = field_ref.strip()
    if ref.endswith("]") and "[" in ref:
        table, _, rest = ref.partition("[")
        field = rest[:-1]
        table = table.strip()
        if len(table) >= 2 and table.startswith("'") and table.endswith("'"):
            table = table[1:-1].replace("''", "'")
        if not table or not field:
            raise ValueError(f"field must be 'Table.Field' or 'Table'[Field], got {field_ref!r}")
        return table, field.replace("]]", "]")
    if "." not in ref:
        raise ValueError(f"field must be 'Table.Field', got {field_ref!r}")
    if known_tables:
        for i, ch in enumerate(ref):
            if ch == "." and ref[:i] in known_tables and ref[i + 1 :]:
                return ref[:i], ref[i + 1 :]
    table, _, field = ref.partition(".")
    if not table or not field:
        raise ValueError(f"field must be 'Table.Field', got {field_ref!r}")
    return table, field


def _resolve_field_type(
    table: str,
    field: str,
    *,
    hint: Optional[str],
    measure_names: set,
    definition_path: Optional[Path] = None,
) -> str:
    """Decide Column vs Measure. Order: explicit hint -> caller measure-name
    set -> the bound model's TMDL (H-18a) -> Column (the safe majority for
    slicers/axes)."""
    if hint in ("Measure", "Column"):
        return hint
    if field in measure_names:
        return "Measure"
    if definition_path is not None:
        from core.pbip.authoring.model_lookup import field_kind

        kind = field_kind(definition_path, table, field)
        if kind in ("Measure", "Column"):
            return kind
    return "Column"


def _load_measure_names() -> set:
    """Deprecated (H-18a): field kinds now come from the bound model's TMDL
    (``model_lookup.field_kind``). Kept for callers; always empty."""
    return set()


VISUAL_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/visualContainer/2.9.0/schema.json"
)


def _build_projection(table: str, field: str, ftype: str) -> Dict[str, Any]:
    """Build one queryState projection (Column or Measure) — generic path."""
    from core.pbip.authoring.data_binding_builder import (
        build_column_binding,
        build_measure_binding,
    )

    if ftype == "Measure":
        return build_measure_binding(table, field)
    return build_column_binding(table, field)


def _build_generic_visual(
    spec: Dict[str, Any], queryState: Dict[str, Any]
) -> Tuple[str, Dict[str, Any]]:
    """Build a minimal valid visual.json for ANY visual type, without relying on
    VisualBuilder's template registry (which only knows a subset of types).

    Mirrors the structure VisualBuilder produces: $schema, name, position,
    visual.{visualType, query.queryState}, optional title.
    """
    vid = generate_visual_id()
    vdict: Dict[str, Any] = {
        "$schema": VISUAL_SCHEMA,
        "name": vid,
        "position": {
            "x": float(spec.get("x", 0)),
            "y": float(spec.get("y", 0)),
            "z": int(spec.get("z", 0)),
            "height": float(spec.get("height", 200)),
            "width": float(spec.get("width", 300)),
            "tabOrder": int(spec.get("z", 0)),
        },
        "visual": {
            "visualType": spec["visual_type"],
            "query": {"queryState": queryState},
            "drillFilterOtherVisuals": True,
        },
    }
    if spec.get("title"):
        from core.pbip.authoring.visual_templates import _string_literal

        vdict["visual"]["visualContainerObjects"] = {
            "title": [{"properties": {"text": _string_literal(spec["title"])}}]
        }
    return vid, vdict


def _card_roles(vt: str, fields: Dict[str, Any]) -> Tuple[str, Dict[str, Any], List[str]]:
    """H-18c: ``card`` is authored as ``cardVisual`` (role ``Data``); remap ``Values``.

    An explicit ``cardVisual`` keeps strict validation (``Values`` is an error).
    Returns ``(visual_type_for_validation, fields, notices)``.
    """
    if vt != "card":
        return vt, fields, []
    notices: List[str] = []
    out: Dict[str, Any] = {}
    for role, ref in fields.items():
        if role == "Values":
            notices.append(
                "role 'Values' remapped to 'Data' (card is authored as cardVisual, "
                "whose value role is Data)"
            )
            role = "Data"
        out[role] = ref
    return "cardVisual", out, notices


def _build_visual_dict(
    spec: Dict[str, Any],
    measure_names: Optional[set] = None,
    *,
    definition_path: Optional[Path] = None,
    warnings: Optional[List[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Map one bulk field-spec onto a visual.json dict; return (visual_id, dict).

    Tries VisualBuilder (type-aware template) first; for visual types not in its
    registry (e.g. advancedSlicerVisual), falls back to a generic skeleton so
    direct-write supports every type the old pbir CLI accepted.

    ``definition_path`` enables bound-model lookups (table split + Measure vs
    Column); fields the model does not know are reported in *warnings*.

    Raises ValueError on a bad field / missing visual_type — the caller treats
    that as a validation error and writes nothing.
    """
    from core.pbip.authoring.visual_builder import VisualBuilder
    from core.pbip.authoring.visual_templates import get_template  # for registry check

    vt = spec.get("visual_type")
    if not vt:
        raise ValueError("spec missing 'visual_type'")
    measure_names = measure_names if measure_names is not None else set()

    field_types = spec.get("field_types") or {}
    if not isinstance(field_types, dict):
        raise ValueError("field_types must be an object {role: 'Measure'|'Column'}")
    fields = spec.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object {role: 'Table.Field'}")
    _vt_roles, mapped_fields, _notices = _card_roles(vt, fields)

    index: Dict[str, Any] = {}
    if definition_path is not None:
        from core.pbip.authoring.model_lookup import model_index

        index = model_index(definition_path)

    # Resolve field types up front (a bad field ref raises ValueError here).
    resolved: List[Tuple[str, str, str, str]] = []  # (role, table, field, ftype)
    for (orig_role, field_ref), role in zip(fields.items(), mapped_fields.keys()):
        table, field = _split_field(field_ref, index or None)
        hint = field_types.get(orig_role, field_types.get(role))
        ftype = _resolve_field_type(
            table,
            field,
            hint=hint,
            measure_names=measure_names,
            definition_path=definition_path if index else None,
        )
        if index and warnings is not None and hint not in ("Measure", "Column"):
            info = index.get(table)
            if info is None:
                warnings.append(f"{field_ref!r}: table {table!r} not found in the bound model")
            elif field not in info["measures"] and field not in info["columns"]:
                warnings.append(
                    f"{field_ref!r}: {field!r} is neither a measure nor a column of "
                    f"{table!r} in the bound model (bound as Column)"
                )
        resolved.append((role, table, field, ftype))

    # Path A: VisualBuilder knows this type → use its template.
    try:
        get_template(vt, "probe")  # KeyError if unknown
        known = True
    except KeyError:
        known = False

    if known:
        builder = VisualBuilder(vt)
        builder.position(
            x=float(spec.get("x", 0)),
            y=float(spec.get("y", 0)),
            width=float(spec.get("width", 300)),
            height=float(spec.get("height", 200)),
            z=int(spec.get("z", 0)),
        )
        if spec.get("title"):
            builder.set_title(spec["title"])
        for role, table, field, ftype in resolved:
            if ftype == "Measure":
                builder.add_measure(table=table, measure=field, bucket=role)
            else:
                builder.add_column(table=table, column=field, bucket=role)
        visual_dict = builder.build()
        return visual_dict["name"], visual_dict

    # Path B: generic skeleton for unknown types (e.g. advancedSlicerVisual).
    query_state: Dict[str, Any] = {}
    for role, table, field, ftype in resolved:
        query_state.setdefault(role, {"projections": []})["projections"].append(
            _build_projection(table, field, ftype)
        )
    return _build_generic_visual(spec, query_state)


def _resolve_page_dir(definition_path: Path, page: str) -> Optional[Path]:
    """Resolve a page folder by ID or display name (DUP-5 consolidated).

    Tolerates a trailing ``.Page`` suffix on the input (the pbir CLI page
    arg uses that form; folders on disk are bare hex IDs).
    """
    from core.utilities.pbip_utils import find_page_folder

    return find_page_folder(definition_path, page)


def _preflight_roles_ex(visuals: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """Validate each visual's `fields` roles against VISUAL_ROLES.

    Returns ``(errors, warnings)``. An uncatalogued ``visual_type`` is a
    WARNING, not an error — the registry is a guard rail and Power BI ships
    new visual types faster than it is refreshed. Blocking a valid create on
    a missing registry entry is worse than letting pbir/Desktop validate the
    role at bind time. ``card`` validates against cardVisual's roles after
    the ``Values`` -> ``Data`` remap (H-18c).
    """
    errors: List[str] = []
    warnings: List[str] = []
    for idx, v in enumerate(visuals):
        if not isinstance(v, dict):
            errors.append(f"visuals[{idx}]: must be an object")
            continue
        vt = v.get("visual_type")
        if not vt:
            errors.append(f"visuals[{idx}]: missing 'visual_type'")
            continue
        fields = v.get("fields") or {}
        if not isinstance(fields, dict):
            errors.append(f"visuals[{idx}] ({vt}): fields must be an object {{role: field}}")
            continue
        check_vt, mapped, notices = _card_roles(vt, fields)
        ok, role_errors, role_warnings = validate_bindings_ex(check_vt, list(mapped.keys()))
        errors.extend(f"visuals[{idx}] ({vt}): {msg}" for msg in role_errors)
        warnings.extend(f"visuals[{idx}] ({vt}): {msg}" for msg in role_warnings)
        warnings.extend(f"visuals[{idx}] ({vt}): {msg}" for msg in notices)
    return errors, warnings


def _preflight_roles(visuals: List[Dict[str, Any]]) -> List[str]:
    """Back-compat wrapper: errors only (see `_preflight_roles_ex`)."""
    errors, _warnings = _preflight_roles_ex(visuals)
    return errors


def _find_container_groups(visuals_dir: Path, display_name: str) -> List[str]:
    """Every visual group on the page whose displayName equals *display_name*."""
    from core.pbip.authoring.safe_io import read_json

    out: List[str] = []
    try:
        folders = sorted(f for f in Path(visuals_dir).iterdir() if f.is_dir())
    except OSError:
        return out
    for folder in folders:
        data = read_json(folder / "visual.json")
        if not isinstance(data, dict):
            continue
        vg = data.get("visualGroup") or {}
        if isinstance(vg, dict) and vg.get("displayName") == display_name:
            out.append(folder.name)
    return out


def _find_container_group(visuals_dir: Path, display_name: str) -> Optional[str]:
    """Find a visual group by displayName; returns folder name (== visual id)."""
    hits = _find_container_groups(visuals_dir, display_name)
    return hits[0] if hits else None


def _container_absolute(visuals_dir: Path, container_id: str) -> Optional[Dict[str, Any]]:
    """Page-absolute geometry of a group (H-19): own position + parent chain.

    Returns None when *container_id* is not a visual group on this page.
    """
    from core.pbip.authoring.safe_io import is_safe_segment, read_json
    from core.pbip.authoring.targets import absolute_offset

    if not is_safe_segment(container_id):
        return None
    data = read_json(Path(visuals_dir) / container_id / "visual.json")
    if not isinstance(data, dict) or "visualGroup" not in data:
        return None
    pos = data.get("position") or {}
    off = absolute_offset(Path(visuals_dir).parent, data)
    return {
        "x": off["x"] + float(pos.get("x", 0) or 0),
        "y": off["y"] + float(pos.get("y", 0) or 0),
        "width": pos.get("width"),
        "height": pos.get("height"),
        "local": {"x": pos.get("x"), "y": pos.get("y")},
        "parent_group": data.get("parentGroupName"),
    }


def _parent_in_memory(
    data: Dict[str, Any], container_id: str, container_x: float, container_y: float
) -> None:
    """Set parentGroupName and translate page-absolute (x,y) to container-local."""
    data["parentGroupName"] = container_id
    pos = data.get("position") or {}
    if "x" in pos:
        pos["x"] = float(pos["x"]) - float(container_x)
    if "y" in pos:
        pos["y"] = float(pos["y"]) - float(container_y)
    data["position"] = pos


def _merge_formatting_in_memory(
    data: Dict[str, Any],
    objects: Optional[Dict[str, Any]],
    vco: Optional[Dict[str, Any]],
) -> None:
    """Shallow-merge caller ``objects`` / ``visualContainerObjects`` into ``visual``."""
    if objects is not None and not isinstance(objects, dict):
        raise ValueError("objects must be an object {card: [entries]}")
    if vco is not None and not isinstance(vco, dict):
        raise ValueError("visualContainerObjects must be an object {card: [entries]}")
    visual = data.setdefault("visual", {})
    if objects:
        visual.setdefault("objects", {}).update(objects)
    if vco:
        visual.setdefault("visualContainerObjects", {}).update(vco)


def _patch_into_container(
    visual_folder: Path,
    container_id: str,
    container_x: float,
    container_y: float,
) -> None:
    """Set parentGroupName on a visual and translate its position to local coords.

    Page-absolute (x,y) → subtract container's page-absolute (x,y) → local (x,y).
    Locked read-modify-write through ``safe_io`` (H-01).
    """
    from core.pbip.authoring import safe_io

    vj_path = Path(visual_folder) / "visual.json"
    if not isinstance(safe_io.read_json(vj_path), dict):
        raise PbirAddVisualsError(f"visual.json unreadable: {vj_path}")

    def _mutate(data: Any) -> None:
        _parent_in_memory(data, container_id, container_x, container_y)

    safe_io.update_json(vj_path, _mutate)


def _apply_visual_formatting(
    visual_folder: Path,
    objects: Optional[Dict[str, Any]],
    vco: Optional[Dict[str, Any]],
) -> None:
    """T1-B: merge per-visual `objects` and `visualContainerObjects` into visual.json.

    Both are pass-through; the caller owns the schema. Merge semantics:

    - ``objects`` overrides ``visual.objects`` keys verbatim (shallow dict
      merge so existing authored objects survive).
    - ``vco`` overrides ``visual.visualContainerObjects`` the same way.

    Locked read-modify-write through ``safe_io`` (H-01).
    """
    if not objects and not vco:
        return
    from core.pbip.authoring import safe_io

    vj_path = Path(visual_folder) / "visual.json"
    if not isinstance(safe_io.read_json(vj_path), dict):
        raise PbirAddVisualsError(f"visual.json unreadable: {vj_path}")

    def _mutate(data: Any) -> None:
        _merge_formatting_in_memory(data, objects, vco)

    safe_io.update_json(vj_path, _mutate)


def add_visuals(
    definition_path: Path,
    page: str,
    visuals: List[Dict[str, Any]],
    container_group_id: Optional[str] = None,
    container_display_name: Optional[str] = None,
    container_x: Optional[float] = None,
    container_y: Optional[float] = None,
    timeout: int = PBIR_TIMEOUT_DEFAULT,
) -> Dict[str, Any]:
    """Create multiple visuals on a page, optionally parented in a container.

    Args:
        definition_path: path to `Report.Report/definition` folder
        page: page folder id or displayName (exact / unique prefix)
        visuals: list of visual specs {visual_type, x, y, width, height, title,
            fields, field_types?, objects?, visualContainerObjects?}; x/y are
            PAGE-ABSOLUTE
        container_group_id: group folder id to parent into (validated)
        container_display_name: look up container by displayName (must be unique)
        container_x, container_y: container's page-absolute top-left. When
            omitted they are COMPUTED from the container's group chain (H-19).
        timeout: accepted for back-compat; ignored (no subprocess)

    Returns:
        {success, visual_ids: [...], created_count, container_id?,
         container_offset?, container_applied, formatting_applied, ...}
    """
    from core.pbip.authoring.targets import resolve_page_dir

    definition_path = Path(definition_path)
    if not definition_path.exists():
        return {"success": False, "error": f"definition path not found: {definition_path}"}

    page_dir, page_err = resolve_page_dir(definition_path, page)
    if page_dir is None:
        err = dict(page_err or {})
        err.setdefault("error", f"page not found: {page}")
        err.setdefault("error_type", "page_not_found")
        err["success"] = False
        return err

    # Pre-flight role validation. Unknown visual types produce warnings, not
    # errors — see `_preflight_roles_ex`.
    role_errors, role_warnings = _preflight_roles_ex(visuals)
    if role_errors:
        return {
            "success": False,
            "error": "role validation failed",
            "error_type": "invalid_role",
            "role_errors": role_errors,
            "role_warnings": role_warnings,
        }

    # Resolve container (by id or unique display name) + its page-absolute offset.
    visuals_dir = page_dir / "visuals"
    container_id = container_group_id
    if container_id is None and container_display_name:
        hits = _find_container_groups(visuals_dir, container_display_name)
        if not hits:
            return {
                "success": False,
                "error": f"container group not found by displayName: {container_display_name}",
                "error_type": "container_not_found",
            }
        if len(hits) > 1:
            return {
                "success": False,
                "error": (
                    f"{len(hits)} groups on this page are named {container_display_name!r}; "
                    "pass container_group_id"
                ),
                "error_type": "ambiguous_container",
                "candidates": hits[:25],
            }
        container_id = hits[0]
    container_offset: Optional[Dict[str, Any]] = None
    if container_id is not None:
        geo = _container_absolute(visuals_dir, str(container_id))
        if geo is None:
            return {
                "success": False,
                "error": f"container group {container_id!r} is not a visual group on this page",
                "error_type": "container_not_found",
            }
        cx = float(container_x) if container_x is not None else geo["x"]
        cy = float(container_y) if container_y is not None else geo["y"]
        caller = container_x is not None or container_y is not None
        container_offset = {
            "x": cx,
            "y": cy,
            "source": "caller" if caller else "computed",
            "computed": {"x": geo["x"], "y": geo["y"]},
        }

    # ── VALIDATE-ALL pass — build every visual in memory; NO disk writes. ──
    # A bad spec here returns before anything is written (atomicity).
    field_warnings: List[str] = []
    built: List[Tuple[str, Dict[str, Any], int]] = []  # (visual_id, visual_dict, spec_idx)
    for idx, spec in enumerate(visuals):
        try:
            per: List[str] = []
            vid, vdict = _build_visual_dict(
                spec, set(), definition_path=definition_path, warnings=per
            )
            field_warnings.extend(f"visuals[{idx}]: {w}" for w in per)
            if container_offset is not None:
                _parent_in_memory(
                    vdict, str(container_id), container_offset["x"], container_offset["y"]
                )
            _merge_formatting_in_memory(
                vdict, spec.get("objects"), spec.get("visualContainerObjects")
            )
        except ValueError as e:
            return {
                "success": False,
                "error_type": "field_unresolved",
                "error": f"visuals[{idx}]: {e}",
                "spec_index": idx,
            }
        built.append((vid, vdict, idx))

    # ── WRITE pass — each visual is written once, fully assembled. ──
    # A genuine disk I/O error rolls back every folder created in THIS call.
    created_ids: List[str] = []
    container_applied: List[str] = []
    formatting_applied: List[str] = []
    try:
        for vid, vdict, idx in built:
            write_visual(visuals_dir, vdict)
            created_ids.append(vid)
            if container_id:
                container_applied.append(vid)
            spec = visuals[idx]
            if spec.get("objects") or spec.get("visualContainerObjects"):
                formatting_applied.append(vid)
    except Exception as e:  # noqa: BLE001 — disk I/O during write
        from core.pbip.authoring.pbir_executor import delete_visual_files

        logger.exception("add_visuals write failed; rolling back %d folder(s)", len(created_ids))
        rolled_back: List[str] = []
        for vid in created_ids:
            try:
                delete_visual_files(visuals_dir / vid)
                rolled_back.append(vid)
            except Exception:  # noqa: BLE001
                pass
        return {
            "success": False,
            "error_type": "write_failed",
            "error": str(e),
            "rolled_back": rolled_back,
        }

    result: Dict[str, Any] = {
        "success": True,
        "page_id": page_dir.name,
        "visual_ids": created_ids,
        "created_count": len(created_ids),
        "container_id": container_id,
        "container_applied": container_applied,
        "formatting_applied": formatting_applied,
        "formatting_skipped": [],
    }
    if container_offset is not None:
        result["container_offset"] = container_offset
    if role_warnings:
        result["role_warnings"] = role_warnings
    if field_warnings:
        result["field_warnings"] = field_warnings
    return result


def resolve_container(
    definition_path: Path, page: str, container_display_name: str
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Standalone container lookup helper for callers that want coords too.

    Returns ``(container_id, {x, y, width, height, local, parent_group})``
    where ``x``/``y`` are PAGE-ABSOLUTE (the group chain is summed — H-19;
    ``local`` is the raw, possibly group-local, position), or ``(None, {})``.
    """
    from core.pbip.authoring.targets import resolve_page_dir

    definition_path = Path(definition_path)
    page_dir, _err = resolve_page_dir(definition_path, page)
    if not page_dir:
        return None, {}

    visuals_dir = page_dir / "visuals"
    cid = _find_container_group(visuals_dir, container_display_name)
    if not cid:
        return None, {}
    geo = _container_absolute(visuals_dir, cid)
    return cid, (geo or {})
