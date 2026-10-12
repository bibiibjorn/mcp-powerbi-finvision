"""
Cascade rename engine: propagate a model rename into PBIR report JSON.

Deep review 2026-09-22 (H-12 / H-32 / H-36 / H-39) — the engine now walks
PBIR STRUCTURALLY instead of string-matching:

* A field reference is ``{Column|Measure|Hierarchy: {Expression: {SourceRef:
  {Entity | Source}}, Property | Hierarchy}}``. ``Source`` aliases are
  resolved through the enclosing query's ``From[{Name, Entity}]``, so a
  filter condition written against alias ``m`` is matched to its table.
* **Table rename**: every ``SourceRef.Entity`` / ``From[].Entity`` equal to
  the old table is renamed (was: nothing, while reporting success).
* **Column / measure rename** is scoped to the owning TABLE when it is known
  (was: every table's ``Property == old`` -- a rename bled across tables).
* ``queryRef`` / ``selector.metadata`` / ``queryName`` strings
  (``Table.Field``, ``Sum(Table.Field)``) and ``nativeQueryRef`` are rebuilt,
  so per-measure formatting and conditional formatting stay bound.
* DAX text only lives in ``Expression`` / ``expression`` STRING values
  (visual calculations, report extension measures): those are rewritten with
  the lexer-driven ``core.dax.dax_rename`` helpers. Arbitrary display text
  (titles, textbox runs, page / bookmark names) is never touched.
* Files are rewritten through ``safe_io.update_json`` (locked RMW, original
  EOL / BOM / trailing newline / UTF-8-vs-escape traits preserved).

``retarget_field_refs`` exposes the same walk for one visual
(``07_Visual_Operations:replace_measure`` / ``remove_field``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_FIELD_KEYS = ("Column", "Measure", "Hierarchy")
_QREF_KEYS = ("queryRef", "metadata", "queryName")
_DAX_KEYS = ("Expression", "expression")


@dataclass
class RenameSpec:
    """One rename. ``kind`` = table | column | measure | hierarchy.

    ``table`` = owning table of a column / measure / hierarchy (None =
    any table, the legacy untyped behaviour). ``new_table`` retargets the
    field to another table as well (replace_measure).
    """

    kind: str
    old: str
    new: str
    table: Optional[str] = None
    new_table: Optional[str] = None

    @property
    def field_key(self) -> Optional[str]:
        return {"column": "Column", "measure": "Measure", "hierarchy": "Hierarchy"}.get(self.kind)


# ---------------------------------------------------------------------------
# Structural walk
# ---------------------------------------------------------------------------


def _source_entity(field_node: Dict[str, Any], aliases: Dict[str, str]) -> Optional[str]:
    expr = field_node.get("Expression")
    sref = expr.get("SourceRef") if isinstance(expr, dict) else None
    if not isinstance(sref, dict):
        return None
    if isinstance(sref.get("Entity"), str):
        return sref["Entity"]
    src = sref.get("Source")
    return aliases.get(src) if isinstance(src, str) else None


def _qref_patterns(spec: RenameSpec) -> List[Tuple["re.Pattern[str]", str]]:
    """(pattern, replacement) pairs for queryRef-like strings."""
    pats: List[Tuple[re.Pattern, str]] = []
    if spec.kind == "table":
        pats.append(
            (
                re.compile(r"(^|\()" + re.escape(spec.old) + r"\."),
                r"\g<1>" + spec.new.replace("\\", "\\\\") + ".",
            )
        )
        return pats
    new_table = spec.new_table
    tail = r"(?=$|\))"
    if spec.table is not None:
        repl_table = new_table if new_table is not None else spec.table
        pats.append(
            (
                re.compile(r"(^|\()" + re.escape(spec.table) + r"\." + re.escape(spec.old) + tail),
                r"\g<1>" + repl_table.replace("\\", "\\\\") + "." + spec.new.replace("\\", "\\\\"),
            )
        )
    else:
        pats.append(
            (
                re.compile(r"(^|\()([^().]+)\." + re.escape(spec.old) + tail),
                r"\g<1>\g<2>." + spec.new.replace("\\", "\\\\"),
            )
        )
    return pats


def _rename_dax_text(text: str, spec: RenameSpec) -> Tuple[str, int]:
    from core.dax import dax_rename

    if spec.kind == "table":
        return dax_rename.rename_table_refs(text, spec.old, spec.new)
    if spec.kind == "column" and spec.table:
        out, n = dax_rename.rename_column_refs(text, spec.table, spec.old, spec.new)
        if spec.new_table and spec.new_table != spec.table:
            out2, n2 = dax_rename.rename_table_refs(out, spec.table, spec.new_table)
            return out2, n + n2
        return out, n
    if spec.kind == "measure":
        return dax_rename.rename_measure_refs(text, spec.old, spec.new, home_table=spec.table)
    if spec.kind == "column":
        # Unknown table: bracket form of the column only.
        pattern = re.compile(r"\[" + re.escape(spec.old) + r"\]")
        return pattern.subn(lambda _m, n=spec.new: f"[{n}]", text)
    return text, 0


class _Walker:
    def __init__(self, spec: RenameSpec) -> None:
        self.spec = spec
        self.count = 0
        self.qref_patterns = _qref_patterns(spec)
        self.deneb_hits = 0

    # -- field node ---------------------------------------------------------
    def _field(self, key: str, node: Dict[str, Any], aliases: Dict[str, str]) -> bool:
        spec = self.spec
        expr = node.get("Expression")
        sref = expr.get("SourceRef") if isinstance(expr, dict) else None
        if spec.kind == "table":
            if isinstance(sref, dict) and sref.get("Entity") == spec.old:
                sref["Entity"] = spec.new
                self.count += 1
                return True
            return False
        if key != spec.field_key:
            return False
        name_key = "Hierarchy" if key == "Hierarchy" else "Property"
        if node.get(name_key) != spec.old:
            return False
        entity = _source_entity(node, aliases)
        if spec.table is not None and entity != spec.table:
            return False
        node[name_key] = spec.new
        if (
            spec.new_table is not None
            and isinstance(sref, dict)
            and isinstance(sref.get("Entity"), str)
            and sref["Entity"] != spec.new_table
        ):
            sref["Entity"] = spec.new_table
        self.count += 1
        return True

    def _qref(self, value: str) -> str:
        out = value
        for pat, repl in self.qref_patterns:
            out, n = pat.subn(repl, out)
            self.count += n
        return out

    # -- recursion ----------------------------------------------------------
    def walk(self, node: Any, aliases: Dict[str, str]) -> None:
        if isinstance(node, list):
            for item in node:
                self.walk(item, aliases)
            return
        if not isinstance(node, dict):
            return
        local = aliases
        frm = node.get("From")
        if isinstance(frm, list):
            local = dict(aliases)
            for entry in frm:
                if not isinstance(entry, dict):
                    continue
                if self.spec.kind == "table" and entry.get("Entity") == self.spec.old:
                    entry["Entity"] = self.spec.new
                    self.count += 1
                if isinstance(entry.get("Name"), str) and isinstance(entry.get("Entity"), str):
                    local[entry["Name"]] = entry["Entity"]
        for key in _FIELD_KEYS:
            child = node.get(key)
            if isinstance(child, dict):
                self._field(key, child, local)
        # Projection: {field, queryRef, nativeQueryRef}
        for key, value in list(node.items()):
            if key in _QREF_KEYS and isinstance(value, str):
                node[key] = self._qref(value)
            elif key == "nativeQueryRef" and isinstance(value, str):
                if self.spec.kind in ("column", "measure") and value == self.spec.old:
                    field = node.get("field")
                    if not isinstance(field, dict) or self._projection_matches(field, local):
                        node[key] = self.spec.new
                        self.count += 1
            elif key in _DAX_KEYS and isinstance(value, str):
                new_text, n = _rename_dax_text(value, self.spec)
                if n:
                    node[key] = new_text
                    self.count += n
            elif key == "jsonSpec" and isinstance(value, dict):
                lit = ((value.get("expr") or {}).get("Literal") or {}).get("Value")
                if isinstance(lit, str) and self.spec.old in lit:
                    self.deneb_hits += 1
            else:
                self.walk(value, local)

    def _projection_matches(self, field: Dict[str, Any], aliases: Dict[str, str]) -> bool:
        key = self.spec.field_key
        node = field.get(key) if key else None
        if not isinstance(node, dict):
            # Aggregation(Column) etc.: accept when the new name is now bound.
            text = json.dumps(field)
            return json.dumps(self.spec.new) in text
        name_key = "Hierarchy" if key == "Hierarchy" else "Property"
        if node.get(name_key) not in (self.spec.new, self.spec.old):
            return False
        if self.spec.table is None:
            return True
        entity = _source_entity(node, aliases)
        return entity in (self.spec.table, self.spec.new_table)


def retarget_field_refs(obj: Any, spec: RenameSpec) -> int:
    """Apply *spec* to one JSON object in place; returns the replacement count."""
    walker = _Walker(spec)
    walker.walk(obj, {})
    return walker.count


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class CascadeRenameEngine:
    """Walk one ``.Report`` folder and rewrite field references."""

    _VALID_KINDS = ("measure", "column", "hierarchy", "table")

    def __init__(self, report_path: str):
        """
        Args:
            report_path: Path to the `.Report` folder (contains `definition/`)
        """
        self._report_path = Path(report_path).resolve()
        self._definition = self._report_path / "definition"

    # -- public API -----------------------------------------------------------
    def apply_renames(self, renames: Dict[str, str], dry_run: bool = False) -> dict:
        """Legacy untyped entry point (TMDL bulk_rename): MEASURE renames, any table.

        ``renames`` = ``{old_measure: new_measure}``. Only measure
        references (typed ``Measure.Property``, queryRef/metadata strings,
        DAX expression strings) change -- never arbitrary strings equal to
        the old name (page / bookmark display names used to be renamed too).
        """
        specs = [RenameSpec("measure", old, new) for old, new in (renames or {}).items()]
        return self.apply_specs(specs, dry_run=dry_run)

    def apply_renames_typed(
        self,
        renames_by_kind: Dict[str, Dict[str, str]],
        dry_run: bool = False,
        table_name: Optional[str] = None,
    ) -> dict:
        """Typed rename: ``{"measure": {"Old": "New"}, "column": {...}, "table": {...}}``.

        ``table_name`` scopes column / measure / hierarchy renames to their
        owning table (strongly recommended -- without it a column rename
        touches same-named columns of every table).
        """
        specs: List[RenameSpec] = []
        for kind, mapping in (renames_by_kind or {}).items():
            if kind not in self._VALID_KINDS:
                continue
            for old, new in (mapping or {}).items():
                specs.append(
                    RenameSpec(kind, old, new, table=None if kind == "table" else table_name)
                )
        return self.apply_specs(specs, dry_run=dry_run)

    def apply_specs(self, specs: List[RenameSpec], dry_run: bool = False) -> dict:
        from core.pbip.authoring import safe_io

        result: dict = {
            "success": False,
            "files_modified": 0,
            "replacements": 0,
            "changed_files": [],
            "errors": [],
            "dry_run": dry_run,
        }
        if not self._definition.exists():
            result["errors"].append(f"Definition directory not found: {self._definition}")
            return result
        specs = [s for s in specs if s.old and s.new and s.old != s.new]
        if not specs:
            result["success"] = True
            result["error_count"] = 0
            result["files_attempted"] = 0
            return result

        target_files = self._collect_target_files()
        total_errors = 0
        deneb_files: List[str] = []

        for file_path in target_files:
            try:
                raw = file_path.read_bytes()
                try:
                    obj = json.loads(raw.decode("utf-8-sig"))
                except (ValueError, UnicodeDecodeError):
                    count = self._raw_fallback(file_path, raw, specs, dry_run)
                    if count:
                        result["replacements"] += count
                        result["files_modified"] += 1
                        result["changed_files"].append(
                            {"path": str(file_path), "replacements": count, "raw_fallback": True}
                        )
                    continue
                count, deneb = self._apply_to_obj(obj, specs)
                if deneb:
                    deneb_files.append(str(file_path))
                if count <= 0:
                    continue
                if not dry_run:

                    def _mutate(data: Any, _specs: List[RenameSpec] = specs) -> None:
                        self._apply_to_obj(data, _specs)

                    safe_io.update_json(file_path, _mutate)
                result["replacements"] += count
                result["files_modified"] += 1
                result["changed_files"].append({"path": str(file_path), "replacements": count})
            except Exception as exc:
                msg = f"Error processing {file_path}: {exc}"
                logger.error(msg, exc_info=True)
                result["errors"].append(msg)
                total_errors += 1

        attempted = len(target_files)
        result["error_count"] = total_errors
        result["files_attempted"] = attempted
        if deneb_files:
            result["deneb_specs_mentioning_old_name"] = deneb_files[:50]
            result["deneb_warning"] = (
                "Deneb/Vega specs reference data fields by name inside their JSON "
                "spec text; review these specs for the old name (not auto-renamed)."
            )
        if total_errors == 0:
            result["success"] = True
        else:
            result["success"] = False
            result["partial_success"] = total_errors < attempted
        return result

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _apply_to_obj(obj: Any, specs: List[RenameSpec]) -> Tuple[int, int]:
        total = 0
        deneb = 0
        for spec in specs:
            walker = _Walker(spec)
            walker.walk(obj, {})
            total += walker.count
            deneb += walker.deneb_hits
        return total, deneb

    @staticmethod
    def _raw_fallback(file_path: Path, raw: bytes, specs: List[RenameSpec], dry_run: bool) -> int:
        """Unparseable JSON: bracket-form measure/column names only (best effort)."""
        from core.pbip.authoring import safe_io

        text = raw.decode("utf-8", errors="replace")
        total = 0
        for spec in specs:
            if spec.kind not in ("measure", "column"):
                continue
            pattern = re.compile(r"\[" + re.escape(spec.old) + r"\]")
            text, n = pattern.subn(lambda _m, n=spec.new: f"[{n}]", text)
            total += n
        if total and not dry_run:
            safe_io.write_bytes_minimal(file_path, text.encode("utf-8"), raw)
        return total

    def _collect_target_files(self) -> List[Path]:
        """Every PBIR JSON file that can hold a field reference."""
        files: List[Path] = []
        pages_dir = self._definition / "pages"
        if pages_dir.exists():
            files.extend(sorted(pages_dir.glob("*/visuals/*/visual.json")))
            files.extend(sorted(pages_dir.glob("*/visuals/*/mobile.json")))
            files.extend(sorted(pages_dir.glob("*/page.json")))
        bookmarks_dir = self._definition / "bookmarks"
        if bookmarks_dir.exists():
            files.extend(sorted(bookmarks_dir.glob("*.json")))
        for name in ("filters.json", "report.json", "reportExtensions.json"):
            p = self._definition / name
            if p.exists():
                files.append(p)
        return files
