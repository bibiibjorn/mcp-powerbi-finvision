"""Target resolution + change classification for ``06_Analysis_Operations compare old=.. new=..`` (F-17).

A compare target is one of::

    {"port": 51234}                              a running Desktop instance
    {"xmla": {"workspace": "...", "model": "..."}}   a published model (READ-ONLY; not live-verified)
    {"pbip_path": "C:/repo/Report.pbip"}         a PBIP project on disk (.pbip / .SemanticModel / definition)
    {"git_ref": "HEAD", "pbip_path": "C:/repo/Report.pbip"}   that project as committed at <ref>

Every target becomes the same thing - the full ``JsonSerializer`` TMSL of the model - and goes through
the ONE engine (``normalize_tmsl`` + ``ModelDiffer``). Nothing is connected globally or written: the
PBIP / git targets load an in-memory TOM database from TMDL text (git versions are extracted with
``git show`` into a temp folder, never checked out). A bare integer is a port.

:func:`classify_changes` splits the differ's result into the changes that can alter results (DAX,
relationships, roles, sources, added / removed / renamed objects) and the cosmetic ones (formatting-only
DAX, descriptions, display folders, format strings, hidden flags, annotations ...).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

__all__ = ["TargetError", "parse_target", "load_target", "classify_changes", "COSMETIC_FIELDS"]

#: Modified-object fields that never change a number: presentation / documentation metadata.
COSMETIC_FIELDS = frozenset(
    {
        "description",
        "display_folder",
        "format_string",
        "format_string_expression",
        "is_hidden",
        "hidden",
        "lineage_tag",
        "source_lineage_tag",
        "annotations",
        "data_category",
        "synonyms",
        "translations",
        "alignment",
        "display_ordinal",
        "table_detail_position",
        "is_default_label",
        "is_default_image",
    }
)


class TargetError(ValueError):
    """The target spec is not usable (the message says what to pass)."""

    def __init__(self, message: str, error_type: str = "invalid_input", **extra: Any):
        super().__init__(message)
        self.error_type = error_type
        self.extra = extra


def parse_target(value: Any, side: str) -> Tuple[str, Dict[str, Any]]:
    """``(kind, spec)`` for ``old`` / ``new``; kind is port | xmla | pbip | git."""
    if isinstance(value, bool):
        raise TargetError(f"{side} must be an object, got {value!r}")
    if isinstance(value, (int, str)) and str(value).strip().isdigit():
        return "port", {"port": int(value)}
    if not isinstance(value, dict):
        raise TargetError(
            f"{side} must be {{port}} | {{xmla:{{workspace, model}}}} | {{pbip_path}} | "
            f"{{git_ref, pbip_path}}, got {value!r}"
        )
    given = [k for k in ("port", "xmla", "pbip_path", "git_ref") if value.get(k) not in (None, "")]
    if value.get("git_ref") not in (None, ""):
        if str(value["git_ref"]).startswith("-"):
            raise TargetError(f"{side}.git_ref {value['git_ref']!r} is not a git ref")
        if not value.get("pbip_path"):
            raise TargetError(f"{side}: git_ref needs pbip_path (the project inside the repo)")
        return "git", {"git_ref": str(value["git_ref"]), "pbip_path": str(value["pbip_path"])}
    if len(given) != 1:
        raise TargetError(
            f"{side}: pass exactly one of port, xmla, pbip_path (+ optional git_ref); got {given}"
        )
    if "port" in given:
        try:
            port = int(value["port"])
        except (TypeError, ValueError):
            raise TargetError(f"{side}.port must be an integer") from None
        if not 1 <= port <= 65535:
            raise TargetError(f"{side}.port must be between 1 and 65535")
        return "port", {"port": port}
    if "xmla" in given:
        x = value["xmla"]
        if not isinstance(x, dict) or not x.get("workspace") or not x.get("model"):
            raise TargetError(f"{side}.xmla needs workspace and model")
        return "xmla", {"workspace": str(x["workspace"]), "model": str(x["model"])}
    return "pbip", {"pbip_path": str(value["pbip_path"])}


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def _serialize_options() -> Any:
    """The client's serialize options (``SerializeOptions``; ``JsonSerializeOptions`` in older
    builds), or None when neither type exists."""
    import Microsoft.AnalysisServices.Tabular as tabular  # type: ignore

    for name in ("SerializeOptions", "JsonSerializeOptions"):
        try:
            options = getattr(tabular, name)()
        except Exception:  # noqa: BLE001 - the type does not exist in every client build
            continue
        for attr, value in (
            ("IgnoreInferredObjects", False),
            ("IgnoreInferredProperties", False),
            ("IgnoreTimestamps", True),
        ):
            try:
                setattr(options, attr, value)
            except Exception:  # noqa: BLE001
                pass
        return options
    return None


def _serialize_model(db: Any) -> Dict[str, Any]:
    """Full TMSL of the model, the snapshot BPA / compare share.

    A TMDL-loaded database is serialised through ``SerializeDatabase`` (the model is the ``model``
    key): ``SerializeObject(db.Model)`` raises ``CompatibilityViolationException`` ("database
    compatibility level of -2 is below ...") on a model that uses a newer property such as
    ``relatedColumnDetails``, because a detached ``Model`` carries no database level. The model
    form stays as the fallback (a live database from Desktop serialises either way).
    """
    from Microsoft.AnalysisServices.Tabular import JsonSerializer  # type: ignore

    options = _serialize_options()
    errors: List[str] = []
    attempts = []
    if hasattr(JsonSerializer, "SerializeDatabase"):
        attempts.append(
            lambda: json.loads(
                JsonSerializer.SerializeDatabase(db, options)
                if options is not None
                else JsonSerializer.SerializeDatabase(db)
            ).get("model")
        )
    attempts.append(
        lambda: json.loads(
            JsonSerializer.SerializeObject(db.Model, options)
            if options is not None
            else JsonSerializer.SerializeObject(db.Model)
        )
    )
    for attempt in attempts:
        try:
            model = attempt()
            if isinstance(model, dict):
                return model
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc).splitlines()[0][:200])
    raise TargetError(
        "could not serialise the model to TMSL: " + "; ".join(errors or ["no serializer"]),
        "tmsl_serialize_failed",
    )


def _definition_label(definition: Path) -> str:
    folder = definition.parent if definition.name.lower() == "definition" else definition
    return folder.name.rsplit(".SemanticModel", 1)[0] or folder.name


def _load_folder(definition: Path, label: str, source: Dict[str, Any]) -> Dict[str, Any]:
    from core.tmdl.tom_check import load_database_from_folder

    loaded = load_database_from_folder(str(definition))
    db = (loaded or {}).get("database")
    if db is None:
        raise TargetError(
            (loaded or {}).get("error")
            or (loaded or {}).get("reason")
            or "TMDL deserialisation failed",
            "tmdl_invalid",
            definition_folder=str(definition),
        )
    out = {
        "model": _serialize_model(db),
        "compatibility_level": int(getattr(db, "CompatibilityLevel", 0) or 0) or None,
        "label": label,
        "source": source,
    }
    if loaded.get("udf_gate"):
        out["warning"] = (
            "the TOM client dropped the DAX user-defined functions on load (UDF compatibility gate); "
            "function changes are not compared for this side"
        )
    return out


def _git(args: List[str], cwd: Path) -> bytes:
    proc = subprocess.run(  # noqa: S603 - fixed git binary, list args, no shell
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        check=False,
        timeout=60,
        stdin=subprocess.DEVNULL,  # never inherit the MCP server's stdio pipe
    )
    if proc.returncode != 0:
        raise TargetError(
            f"git {' '.join(args[:2])} failed: {proc.stderr.decode('utf-8', 'replace').strip()[:300]}",
            "git_error",
        )
    return proc.stdout


def _extract_git_definition(git_ref: str, pbip_path: str, dest: Path) -> Tuple[Path, str]:
    """``git show`` every file of the project's TMDL definition folder at ``git_ref`` into ``dest``.

    Returns ``(definition folder in dest, label)``. Read-only: the working tree and index are untouched.
    """
    from core.infrastructure import offline_model

    p = Path(pbip_path).expanduser()
    try:
        definition_now = offline_model.resolve_definition_folder(str(p))
    except offline_model.OfflineModelError:
        definition_now = p  # the project may only exist at <ref>: treat the path as the folder
    anchor = definition_now if definition_now.exists() else p
    cwd = anchor if anchor.is_dir() else anchor.parent
    if not cwd.exists():
        raise TargetError(
            f"{pbip_path} does not exist and has no existing parent folder", "not_found"
        )
    root = Path(_git(["rev-parse", "--show-toplevel"], cwd).decode().strip()).resolve()
    try:
        rel = definition_now.resolve().relative_to(root).as_posix()
    except ValueError:
        raise TargetError(
            f"{pbip_path} is not inside the git repository {root}", "git_error"
        ) from None
    names = (
        _git(["ls-tree", "-r", "--name-only", "-z", git_ref, "--", rel], root)
        .decode("utf-8")
        .split("\0")
    )
    names = [n for n in names if n]
    if not names:
        raise TargetError(f"{rel} does not exist at {git_ref}", "not_found", git_ref=git_ref)
    for name in names:
        data = _git(["show", f"{git_ref}:{name}"], root)
        target = dest / Path(name).relative_to(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return dest, _definition_label(definition_now)


def _load_xmla(workspace: str, model: str) -> Dict[str, Any]:
    """Published model through the XMLA endpoint, read-only, WITHOUT making it the active target."""
    from core.infrastructure import xmla_target
    from core.infrastructure.dll_paths import load_amo_assemblies

    if not load_amo_assemblies():
        raise TargetError("AMO assemblies not available", "amo_unavailable")
    from Microsoft.AnalysisServices.Tabular import Server  # type: ignore

    server = Server()
    try:
        token, expires = xmla_target.get_token()
        server.AccessToken = xmla_target._amo_token(token, expires)  # noqa: SLF001
        server.Connect(xmla_target.build_connection_string(workspace, model))
        db = next((d for d in server.Databases if str(d.Name) == model), None)
        if db is None and server.Databases.Count == 1:
            db = server.Databases[0]
        if db is None:
            raise TargetError(f"model {model!r} not found in workspace {workspace!r}", "not_found")
        return {
            "model": _serialize_model(db),
            "compatibility_level": int(getattr(db, "CompatibilityLevel", 0) or 0) or None,
            "label": f"{workspace}/{model}",
            "source": {"xmla": {"workspace": workspace, "model": model}},
        }
    finally:
        try:
            server.Disconnect()
        except Exception:  # noqa: BLE001
            pass


def load_target(kind: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """``{model: TMSL dict, compatibility_level, label, source[, warning]}`` for a non-port target."""
    from core.infrastructure import offline_model

    if kind == "pbip":
        try:
            definition = offline_model.resolve_definition_folder(spec["pbip_path"])
        except offline_model.OfflineModelError as exc:
            raise TargetError(str(exc), "not_found") from exc
        return _load_folder(
            definition, _definition_label(definition), {"pbip_path": spec["pbip_path"]}
        )
    if kind == "git":
        tmp = Path(tempfile.mkdtemp(prefix="mcp_compare_git_"))
        try:
            definition, label = _extract_git_definition(
                spec["git_ref"], spec["pbip_path"], tmp / "definition"
            )
            return _load_folder(definition, f"{label}@{spec['git_ref']}", dict(spec))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if kind == "xmla":
        return _load_xmla(spec["workspace"], spec["model"])
    raise TargetError(f"unsupported target kind {kind!r}")


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------
_LIST_KINDS = ("added", "removed", "renamed", "moved", "modified")


def _is_field_diff(node: Any) -> bool:
    return isinstance(node, dict) and "from" in node and "to" in node


def _field_diffs_below(node: Any, path: str = "") -> List[Tuple[str, str, Dict[str, Any]]]:
    """Every ``{from, to}`` leaf under *node* as ``(path, field, diff)`` (any depth)."""
    out: List[Tuple[str, str, Dict[str, Any]]] = []
    if not isinstance(node, dict):
        return out
    for key, value in node.items():
        if _is_field_diff(value):
            out.append((path, str(key), value))
        elif isinstance(value, dict):
            out.extend(_field_diffs_below(value, f"{path}.{key}" if path else str(key)))
    return out


def classify_changes(diff: Dict[str, Any], limit: int = 200) -> Dict[str, Any]:
    """Split a ``ModelDiffer`` result into ``affects_results`` and ``cosmetic`` change lists.

    A change whose shape is not recognised (e.g. a role's ``filter_expressions`` detail) is NEVER
    dropped: it counts as ``affects_results``. Only a ``{from, to}`` field in ``COSMETIC_FIELDS``
    (or a formatting-only DAX change) is cosmetic.
    """
    affects: List[Dict[str, Any]] = []
    cosmetic: List[Dict[str, Any]] = []
    seen = set()

    def emit(target: List[Dict[str, Any]], row: Dict[str, Any]) -> None:
        key = json.dumps(row, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            target.append(row)

    def emit_fields(category: str, where: str, fields: List[str]) -> None:
        result_fields = sorted(k for k in fields if k not in COSMETIC_FIELDS)
        cosmetic_fields = sorted(k for k in fields if k in COSMETIC_FIELDS)
        if result_fields:
            emit(
                affects,
                {
                    "category": category,
                    "object": where,
                    "change": "modified",
                    "fields": result_fields,
                },
            )
        if cosmetic_fields:
            emit(
                cosmetic,
                {
                    "category": category,
                    "object": where,
                    "change": "modified",
                    "fields": cosmetic_fields,
                },
            )

    def walk(node: Any, category: str, path: str) -> None:
        if not isinstance(node, dict):
            return
        for kind in _LIST_KINDS:
            items = node.get(kind)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("to") or item.get("from") or "?")
                where = f"{path}.{name}" if path else name
                changes = item.get("changes")
                if kind != "modified" or not isinstance(changes, dict):
                    emit(affects, {"category": category, "object": where, "change": kind})
                    continue
                fields = [k for k, v in changes.items() if _is_field_diff(v)]
                emit_fields(category, where, fields)
                for sub_key, sub in changes.items():
                    if _is_field_diff(sub) or not isinstance(sub, (dict, list)):
                        continue  # a field diff is handled above; has_changes / own_changes flags
                    if isinstance(sub, dict) and any(k in sub for k in _LIST_KINDS):
                        walk(sub, sub_key, where)  # columns / measures / calculation_items ...
                    elif (
                        isinstance(sub, dict)
                        and sub
                        and all(_is_field_diff(v) for v in sub.values())
                    ):
                        emit_fields(category, where, [str(k) for k in sub])  # a group of fields
                    elif sub:
                        # opaque nested change (role filter_expressions, table_permissions ...):
                        # never silently dropped; it can change what a user sees
                        emit_fields(category, where, [str(sub_key)])

    for category in (
        "tables",
        "relationships",
        "roles",
        "perspectives",
        "expressions",
        "functions",
    ):
        walk(diff.get(category), category, "")
    # Measure moves only exist in the cross-table view; the rest of "measures" duplicates tables.*.
    moved = (diff.get("measures") or {}).get("moved")
    if isinstance(moved, list):
        walk({"moved": moved}, "measures", "")
    props = diff.get("model_properties")
    if isinstance(props, dict):
        for path, field, _change in _field_diffs_below(props):
            target = cosmetic if field in COSMETIC_FIELDS else affects
            emit(
                target,
                {
                    "category": "model_properties",
                    "object": f"{path}.{field}" if path else field,
                    "change": "modified",
                },
            )
    for c in diff.get("cosmetic_changes") or []:
        emit(
            cosmetic,
            {
                "category": str(c.get("object_type") or "").lower(),
                "object": c.get("name"),
                "change": "modified",
                "fields": [c.get("field")],
                "detail": "formatting only (the expression is equivalent)",
            },
        )
    return {
        "affects_results": affects[:limit],
        "affects_results_count": len(affects),
        "cosmetic": cosmetic[:limit],
        "cosmetic_count": len(cosmetic),
        "truncated": len(affects) > limit or len(cosmetic) > limit,
    }
