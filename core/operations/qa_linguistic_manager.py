"""Q&A linguistic schema CRUD for PBIP semantic models (offline, on disk).

Review C-17 / G-23: TMDL projects do NOT store a ``linguisticMetadata.json``
file — the schema lives inline in ``definition/cultures/<culture>.tmdl``::

    cultureInfo en-GB

    <TAB>linguisticMetadata =
    <TAB><TAB><TAB>{
    <TAB><TAB><TAB>  "Version": "4.1.0",
    ...
    <TAB><TAB><TAB>}
    <TAB><TAB>contentType: json

so every op used to fail with "not found" on real projects. This module reads
and writes that block (preserving its indentation, the file's line endings and
BOM, via an atomic write), falls back to ``model.bim`` cultures and to the
legacy ``cultures/<culture>/linguisticMetadata.json`` layout, resolves the
semantic model through the shared PBIP resolver (no arbitrary
``sorted(glob)[0]``) and defaults the culture to the model's ``culture:``.

Provides read, audit, and surgical mutation of the linguistic schema:
  * synonym add/remove
  * IncludeInQnA toggle per entity
  * audit (entities without extra synonyms / with IncludeInQnA=false / unbound)

PBI Desktop must be closed for writes to land (it rewrites the TMDL on save).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_FALLBACK_CULTURE = "en-US"


# ------------------------------------------------------------------ resolving


def _resolve_model_dir(pbip_path: str) -> Tuple[Optional[Path], Optional[str]]:
    """``(<X>.SemanticModel dir, error)`` for a .pbip / folder / model path."""
    p = Path(pbip_path).resolve()
    if p.is_dir() and p.name.endswith(".SemanticModel"):
        return p, None
    try:
        from core.pbip.pbir_model import resolve_pbip

        res = resolve_pbip(str(p))
        models = res.get("models") or []
    except Exception as e:  # noqa: BLE001 - fall back to a local scan
        logger.debug("resolve_pbip failed for %s: %s", p, e)
        models = []
    dirs = [Path(m["model_dir"]) for m in models if m.get("model_dir")]
    if not dirs:
        base = p.parent if p.is_file() else p
        dirs = sorted(base.glob("*.SemanticModel"))
    if not dirs:
        return None, f"No .SemanticModel folder found for '{pbip_path}'"
    if len(dirs) > 1:
        preferred = [d for d in dirs if d.name == "Model.SemanticModel"]
        if len(preferred) == 1:
            return preferred[0], None
        return None, (
            "Ambiguous PBIP path — several semantic models: "
            + ", ".join(sorted(d.name for d in dirs))
            + ". Pass the .SemanticModel folder (or its .pbip) directly."
        )
    return dirs[0], None


def _model_culture(model_dir: Path) -> Optional[str]:
    tmdl = model_dir / "definition" / "model.tmdl"
    if tmdl.is_file():
        m = re.search(r"^\s*culture:\s*(\S+)", tmdl.read_text(encoding="utf-8-sig"), re.M)
        if m:
            return m.group(1).strip().strip("'\"")
    bim = model_dir / "model.bim"
    if bim.is_file():
        try:
            doc = json.loads(bim.read_text(encoding="utf-8-sig"))
            return (doc.get("model") or {}).get("culture")
        except ValueError:
            return None
    return None


class _Store:
    """One linguistic schema location + how to write it back."""

    def __init__(self, path: Path, kind: str, culture: str):
        self.path = path
        self.kind = kind  # "tmdl" | "bim" | "json"
        self.culture = culture

    # -- reading ------------------------------------------------------------
    def read(self) -> Dict[str, Any]:
        raw = self.path.read_bytes()
        text = raw.decode("utf-8-sig")
        if self.kind == "json":
            return json.loads(text)
        if self.kind == "bim":
            culture = self._bim_culture(json.loads(text))
            content = (culture.get("linguisticMetadata") or {}).get("content")
            if isinstance(content, str):
                return json.loads(content)
            if isinstance(content, dict):
                return content
            raise ValueError("culture has no linguisticMetadata content")
        _pre, block, _post, _indent = _split_tmdl(text)
        return json.loads(block)

    def _bim_culture(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        for c in (doc.get("model") or {}).get("cultures") or []:
            if str(c.get("name", "")).lower() == self.culture.lower():
                return c
        raise ValueError(f"culture '{self.culture}' not in model.bim")

    # -- writing ------------------------------------------------------------
    def write(self, schema: Dict[str, Any]) -> None:
        from core.utilities.pbip_utils import _atomic_write_bytes, file_lock, notify_path_written

        with file_lock(self.path):
            raw = self.path.read_bytes()
            bom = raw.startswith(b"\xef\xbb\xbf")
            text = raw.decode("utf-8-sig")
            eol = "\r\n" if "\r\n" in text else "\n"
            body = json.dumps(schema, indent=2, ensure_ascii=False)
            if self.kind == "json":
                new_text = body.replace("\n", eol) + (eol if text.endswith(("\n", "\r\n")) else "")
            elif self.kind == "bim":
                doc = json.loads(text)
                culture = self._bim_culture(doc)
                lm = culture.setdefault("linguisticMetadata", {})
                lm["content"] = schema if isinstance(lm.get("content"), dict) else body
                new_text = json.dumps(doc, indent=2, ensure_ascii=False).replace("\n", eol)
            else:
                pre, _block, post, indent = _split_tmdl(text)
                lines = [indent + ln if ln else ln for ln in body.split("\n")]
                new_text = pre + eol.join(lines) + eol + post
            payload = new_text.encode("utf-8")
            if bom:
                payload = b"\xef\xbb\xbf" + payload
            _atomic_write_bytes(self.path, payload)
        notify_path_written(self.path)


def _split_tmdl(text: str) -> Tuple[str, str, str, str]:
    """``(prefix, json_block, suffix, json_indent)`` of a culture TMDL file.

    The prefix ends right after the ``linguisticMetadata =`` line; the JSON
    block is every following line indented deeper than that property (JSON
    lines are dedented by their common indentation); the suffix starts at the
    first line back at property level (e.g. ``contentType: json``).
    """
    lines = text.splitlines(keepends=True)
    start = None
    prop_indent = ""
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)linguisticMetadata\s*=\s*$", ln.rstrip("\r\n"))
        if m:
            start = i
            prop_indent = m.group(1)
            break
    if start is None:
        raise ValueError("no linguisticMetadata block in the culture file")
    body_lines: List[str] = []
    j = start + 1
    while j < len(lines):
        raw = lines[j].rstrip("\r\n")
        if raw.strip() == "":
            body_lines.append(lines[j])
            j += 1
            continue
        indent = raw[: len(raw) - len(raw.lstrip())]
        if len(indent) <= len(prop_indent) + 1 and not raw.lstrip().startswith(
            ("{", "}", '"', "[", "]")
        ):
            break
        body_lines.append(lines[j])
        j += 1
    # trailing blank lines belong to the suffix
    while body_lines and body_lines[-1].strip() == "":
        j -= 1
        body_lines.pop()
    content = [ln.rstrip("\r\n") for ln in body_lines]
    nonblank = [ln for ln in content if ln.strip()]
    common = min((len(ln) - len(ln.lstrip()) for ln in nonblank), default=0)
    indent = nonblank[0][:common] if nonblank else prop_indent + "\t\t"
    block = "\n".join(ln[common:] if len(ln) >= common else ln.lstrip() for ln in content)
    return "".join(lines[: start + 1]), block, "".join(lines[j:]), indent


def _resolve_store(
    pbip_path: str, culture: Optional[str]
) -> Tuple[Optional[_Store], Optional[str]]:
    model_dir, err = _resolve_model_dir(pbip_path)
    if model_dir is None:
        return None, err
    culture = culture or _model_culture(model_dir) or _FALLBACK_CULTURE
    cultures = model_dir / "definition" / "cultures"
    candidates = [
        (cultures / f"{culture}.tmdl", "tmdl"),
        (cultures / culture / "linguisticMetadata.json", "json"),
    ]
    for path, kind in candidates:
        if path.is_file():
            return _Store(path, kind, culture), None
    # case-insensitive culture file match (en-gb.tmdl)
    if cultures.is_dir():
        for f in cultures.iterdir():
            if f.suffix.lower() == ".tmdl" and f.stem.lower() == culture.lower():
                return _Store(f, "tmdl", culture), None
    bim = model_dir / "model.bim"
    if bim.is_file():
        return _Store(bim, "bim", culture), None
    available = sorted(f.stem for f in cultures.glob("*.tmdl")) if cultures.is_dir() else []
    return None, (
        f"No linguistic metadata for culture '{culture}' in {model_dir.name}"
        + (f" (cultures present: {available})" if available else "")
    )


def _find_entity(entities: Dict[str, Any], name: str) -> Optional[str]:
    if name in entities:
        return name
    lowered = str(name).lower()
    return next((k for k in entities if str(k).lower() == lowered), None)


# ------------------------------------------------------------------- manager


class QaLinguisticManager:
    """Read / mutate the Q&A linguistic schema for a PBIP project."""

    def _open(self, pbip_path: str, culture: Optional[str]):
        store, err = _resolve_store(pbip_path, culture)
        if store is None:
            return None, None, {"success": False, "error": err, "error_type": "not_found"}
        try:
            return store, store.read(), None
        except Exception as e:  # noqa: BLE001
            return (
                store,
                None,
                {
                    "success": False,
                    "error": f"Failed to read schema from {store.path.name}: {e}",
                    "error_type": "read_error",
                },
            )

    def load(self, pbip_path: str, culture: Optional[str] = None) -> Dict[str, Any]:
        store, schema, err = self._open(pbip_path, culture)
        if err:
            return err
        return {
            "success": True,
            "path": str(store.path),
            "storage": store.kind,
            "culture": store.culture,
            "schema": schema,
        }

    def audit(self, pbip_path: str, culture: Optional[str] = None) -> Dict[str, Any]:
        store, schema, err = self._open(pbip_path, culture)
        if err:
            return err
        entities = schema.get("Entities", {}) or {}
        no_extra: List[str] = []
        excluded: List[str] = []
        unbound: List[str] = []
        for name, entity in entities.items():
            terms = entity.get("Terms") or []
            term_count = sum(len(t) if isinstance(t, dict) else 1 for t in terms)
            if term_count <= 1:
                no_extra.append(name)
            if entity.get("IncludeInQnA") is False:
                excluded.append(name)
            binding = (entity.get("Definition") or {}).get("Binding") or {}
            if not binding.get("ConceptualEntity") and not binding.get("ConceptualProperty"):
                unbound.append(name)
        return {
            "success": True,
            "path": str(store.path),
            "culture": store.culture,
            "entity_count": len(entities),
            "entities_without_extra_synonyms": sorted(no_extra),
            "entities_excluded_from_qa": sorted(excluded),
            "entities_without_binding": sorted(unbound),
        }

    def _mutate(self, pbip_path, culture, entity, fn) -> Dict[str, Any]:
        store, schema, err = self._open(pbip_path, culture)
        if err:
            return err
        entities = schema.setdefault("Entities", {})
        key = _find_entity(entities, entity)
        if key is None:
            return {"success": False, "error": f"Entity '{entity}' not in schema"}
        out = fn(entities[key], key)
        if out.get("success") and out.get("action") != "noop":
            try:
                store.write(schema)
            except Exception as e:  # noqa: BLE001
                return {"success": False, "error": f"Failed to write schema: {e}"}
            out["path"] = str(store.path)
        return out

    def set_include_in_qa(
        self,
        pbip_path: str,
        culture: Optional[str],
        entity: str,
        include: bool,
    ) -> Dict[str, Any]:
        def fn(ent, key):
            ent["IncludeInQnA"] = bool(include)
            return {"success": True, "entity": key, "include": bool(include)}

        return self._mutate(pbip_path, culture, entity, fn)

    def add_synonym(
        self,
        pbip_path: str,
        culture: Optional[str],
        entity: str,
        synonym: str,
        state: str = "Authored",
    ) -> Dict[str, Any]:
        if not synonym:
            return {"success": False, "error": "synonym is required"}

        def fn(ent, key):
            terms = ent.setdefault("Terms", [])
            for t in terms:
                if isinstance(t, dict) and synonym in t:
                    return {"success": True, "action": "noop", "note": "synonym already present"}
            terms.append({synonym: {"State": state}})
            return {"success": True, "action": "added", "entity": key, "synonym": synonym}

        return self._mutate(pbip_path, culture, entity, fn)

    def remove_synonym(
        self,
        pbip_path: str,
        culture: Optional[str],
        entity: str,
        synonym: str,
    ) -> Dict[str, Any]:
        def fn(ent, key):
            terms = ent.get("Terms") or []
            new_terms = [t for t in terms if not (isinstance(t, dict) and synonym in t)]
            if len(new_terms) == len(terms):
                return {"success": False, "error": f"synonym '{synonym}' not present on '{key}'"}
            ent["Terms"] = new_terms
            return {"success": True, "action": "removed", "entity": key, "synonym": synonym}

        return self._mutate(pbip_path, culture, entity, fn)
