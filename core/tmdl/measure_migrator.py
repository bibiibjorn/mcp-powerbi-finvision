"""
TMDL Measure Migrator - Extract and copy measures between TMDL table files.

Review G-14 (2026-09-22) rewrite on the indentation-scoped tree
(:mod:`core.tmdl.tmdl_tree`):

* a measure is exactly its node span (``///`` description lines through the
  last indented line), so the last measure no longer swallows the following
  ``column`` / ``hierarchy`` blocks and multi-line descriptions survive;
* appending inserts the measures after the target table's last measure (or
  before its first child object) and never touches its partitions, columns
  or annotations — the target's real partition is kept;
* ``replace_target=True`` replaces the target's MEASURES only (it used to
  replace the whole file with a default ``m`` partition);
* new tables get a fresh ``lineageTag`` (uuid4); measures copied into the SAME
  model get fresh lineageTags too;
* the write is validated (new TMDL errors abort it) and atomic, line endings
  and BOM of the target are preserved.
"""

import logging
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .tmdl_tree import TmdlDocument, TmdlNode, parse_text, tmdl_name

logger = logging.getLogger(__name__)

_CHILD_OBJECTS = (
    "measure",
    "column",
    "hierarchy",
    "partition",
    "calculationGroup",
    "calculationItem",
    "calendar",
    "annotation",
    "extendedProperty",
    "changedProperty",
    "variation",
    "refreshPolicy",
)


@dataclass
class MeasureInfo:
    """Information about a measure"""

    name: str
    display_folder: str
    content: str
    line_number: int


@dataclass
class MigrationResult:
    """Result of a measure migration operation"""

    success: bool
    measures_found: int
    measures_migrated: int
    duplicates_skipped: int
    backup_path: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    measures: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "success": self.success,
            "measures_found": self.measures_found,
            "measures_migrated": self.measures_migrated,
            "duplicates_skipped": self.duplicates_skipped,
            "backup_path": self.backup_path,
            "errors": self.errors,
            "warnings": self.warnings,
            "measures": self.measures[:50],  # Limit to first 50 for output
        }


def _read(path: Path) -> tuple:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    bom = text.startswith("﻿")
    return raw, (text[1:] if bom else text), bom


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _table_root(doc: TmdlDocument) -> Optional[TmdlNode]:
    for r in doc.roots:
        if r.keyword == "table":
            return r
    return None


def _definition_of(table_file: Path) -> Optional[Path]:
    if table_file.parent.name == "tables" and (table_file.parent.parent / "model.tmdl").exists():
        return table_file.parent.parent
    return None


class TmdlMeasureMigrator:
    """Copy measures between TMDL table files (display-folder filter, duplicates)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.backup_enabled = self.config.get("backup_before_edit", True)
        self.backup_dir = Path(self.config.get("backup_directory", "./backups/tmdl"))

    # ─── extract ─────────────────────────────────────────────────────

    def extract_measures(
        self,
        source_path: str,
        display_folder_filter: Optional[str] = None,
    ) -> List[MeasureInfo]:
        """Measures of a TMDL table file, each with its exact text block."""
        path = Path(source_path)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        _raw, text, _bom = _read(path)
        doc = parse_text(text, path=str(path))
        table = _table_root(doc)
        if table is None:
            return []
        measures: List[MeasureInfo] = []
        for node in table.children:
            if node.keyword != "measure":
                continue
            folder = node.prop("displayFolder") or ""
            if display_folder_filter and not folder.startswith(display_folder_filter):
                continue
            block = "\n".join(doc.lines[node.span_start : node.end])
            measures.append(
                MeasureInfo(
                    name=node.name or "",
                    display_folder=folder,
                    content=block,
                    line_number=node.start,
                )
            )
        logger.info(f"Extracted {len(measures)} measures from {source_path}")
        return measures

    # ─── migrate ─────────────────────────────────────────────────────

    def migrate_measures(
        self,
        source_path: str,
        target_path: str,
        display_folder_filter: Optional[str] = None,
        replace_target: bool = False,
        skip_duplicates: bool = True,
        dry_run: bool = True,
    ) -> MigrationResult:
        """Copy measures from *source_path* into the table file *target_path*."""
        result = MigrationResult(
            success=False, measures_found=0, measures_migrated=0, duplicates_skipped=0
        )
        try:
            source_measures = self.extract_measures(source_path, display_folder_filter)
            result.measures_found = len(source_measures)
            if not source_measures:
                result.warnings.append(
                    f"No measures found matching filter: {display_folder_filter}"
                )
                result.success = True
                return result

            target_file = Path(target_path)
            src_def = _definition_of(Path(source_path))
            tgt_def = _definition_of(target_file)
            same_model = bool(src_def and tgt_def and src_def.resolve() == tgt_def.resolve())

            if target_file.exists():
                original, text, bom = _read(target_file)
                doc = parse_text(text, path=str(target_file))
                table = _table_root(doc)
                if table is None:
                    result.errors.append(f"{target_path} has no 'table' object")
                    return result
                existing = {(m.name or "").lower() for m in table.children_of("measure")}
            else:
                original, text, bom, doc, table = b"", "", False, None, None
                existing = set()

            to_copy: List[MeasureInfo] = []
            seen: set = set()
            for m in source_measures:
                key = m.name.lower()
                if key in seen or (key in existing and not replace_target):
                    if skip_duplicates or key in seen:
                        result.duplicates_skipped += 1
                        result.warnings.append(f"Skipped duplicate: {m.name}")
                        continue
                    result.errors.append(
                        f"measure {m.name!r} already exists in the target (skip_duplicates=false "
                        "would create a duplicate name)"
                    )
                    return result
                seen.add(key)
                to_copy.append(m)
                result.measures.append({"name": m.name, "display_folder": m.display_folder})
            result.measures_migrated = len(to_copy)

            if dry_run:
                result.success = True
                result.warnings.append("DRY RUN - No changes applied")
                return result

            nl = _newline(text) if text else "\n"
            blocks = [
                self._block(m.content, same_model or not target_file.exists()) for m in to_copy
            ]
            if table is None:
                new_text = self._new_table_text(target_file.stem, blocks, nl)
            else:
                new_text = self._insert(doc, table, blocks, replace_target, nl)

            if self.backup_enabled and target_file.exists():
                result.backup_path = str(self._create_backup(target_path))

            payload = (("﻿" if bom else "") + new_text).encode("utf-8")
            if tgt_def is not None:
                err = _validate_candidate(tgt_def, target_file, payload)
                if err:
                    result.errors.extend(err)
                    result.errors.append(
                        "the migration would introduce TMDL errors — nothing was written"
                    )
                    return result
            from core.utilities.pbip_utils import save_json_minimal

            target_file.parent.mkdir(parents=True, exist_ok=True)
            save_json_minimal(target_file, payload, original, raise_on_error=True)
            if table is None and tgt_def is not None:
                result.warnings.append(
                    f"created table file {target_file.name}; add 'ref table {tmdl_name(target_file.stem)}' "
                    "to model.tmdl if you rely on the table order"
                )
            result.success = True
            logger.info(f"Migrated {result.measures_migrated} measures to {target_path}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error during measure migration: {e}", exc_info=True)
            result.errors.append(str(e))
        return result

    # ─── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _block(content: str, fresh_lineage: bool) -> List[str]:
        lines = content.split("\n")
        if fresh_lineage:
            lines = [
                (
                    re.sub(r"^(\s*lineageTag:\s*).*$", lambda m: m.group(1) + str(uuid.uuid4()), ln)
                    if ln.lstrip().startswith("lineageTag:") and _indent(ln) == 2
                    else ln
                )
                for ln in lines
            ]
        return lines

    @staticmethod
    def _insert(
        doc: TmdlDocument, table: TmdlNode, blocks: List[List[str]], replace_target: bool, nl: str
    ) -> str:
        lines = list(doc.lines)
        measures = table.children_of("measure")
        remove: List[range] = []
        if replace_target and measures:
            insert_at = measures[0].span_start
            for m in measures:
                remove.append(range(m.span_start, m.end))
        elif measures:
            insert_at = measures[-1].end
        else:
            first_child = next((c for c in table.children if c.keyword in _CHILD_OBJECTS), None)
            insert_at = first_child.span_start if first_child is not None else table.end
        drop = set(i for r in remove for i in r)
        new_block: List[str] = []
        for b in blocks:
            new_block.append("")
            new_block.extend(b)
        new_block.append("")
        out: List[str] = []
        for i, ln in enumerate(lines):
            if i == insert_at:
                out.extend(new_block)
            if i in drop:
                continue
            out.append(ln)
        if insert_at >= len(lines):
            out.extend(new_block)
        # collapse runs of >1 blank line created by the splice
        collapsed: List[str] = []
        for ln in out:
            if not ln.strip() and collapsed and not collapsed[-1].strip():
                continue
            collapsed.append(ln)
        text = nl.join(collapsed)
        if doc.raw_lines and doc.raw_lines[-1].endswith(("\n", "\r")) and not text.endswith(nl):
            text += nl
        return text

    def _new_table_text(
        self, stem: str, blocks: List[List[str]], nl: str, lineage_tag: Optional[str] = None
    ) -> str:
        parts = [f"table {tmdl_name(stem)}", f"\tlineageTag: {lineage_tag or uuid.uuid4()}", ""]
        for b in blocks:
            parts.extend(b)
            parts.append("")
        parts.extend(self._default_partition(stem).split("\n"))
        return nl.join(parts).rstrip("\r\n") + nl

    def _create_backup(self, file_path: str) -> Path:
        source = Path(file_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"{source.stem}_{timestamp}{source.suffix}"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return backup_path

    def _default_partition(self, name: str = "m Measure") -> str:
        """Default (empty, single hidden column) partition of a measure table."""
        return (
            f"\tpartition {tmdl_name(name)} = m\n"
            "\t\tmode: import\n"
            "\t\tsource =\n"
            "\t\t\t\tlet\n"
            '\t\t\t\t    Source = Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText("i44FAA==", '
            "BinaryEncoding.Base64), Compression.Deflate)), let _t = ((type nullable text) meta "
            "[Serialized.Text = true]) in type table [Column1 = _t]),\n"
            '\t\t\t\t    #"Changed Type" = Table.TransformColumnTypes(Source,{{"Column1", type text}}),\n'
            '\t\t\t\t    #"Removed Columns" = Table.RemoveColumns(#"Changed Type",{"Column1"})\n'
            "\t\t\t\tin\n"
            '\t\t\t\t    #"Removed Columns"\n'
            "\n"
            "\tannotation PBI_ResultType = Table\n"
        )


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def _validate_candidate(definition: Path, target_file: Path, payload: bytes) -> List[str]:
    """Validate *definition* with *target_file* replaced by *payload* (scratch copy)."""
    import tempfile

    from .validator import TmdlValidator

    before = TmdlValidator().validate_syntax(str(definition))
    tmp = Path(tempfile.mkdtemp(prefix="tmdl_migrate_"))
    try:
        copy = tmp / "definition"
        shutil.copytree(definition, copy)
        dst = copy / target_file.relative_to(definition)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(payload)
        after = TmdlValidator().validate_syntax(str(copy))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    def sig(e: Any) -> tuple:
        return (e.code, Path(e.file).name, re.sub(r"line \d+", "", e.message))

    base = {sig(e) for e in before.errors}
    return [
        f"validation: {e.code} {Path(e.file).name}:{e.line} {e.message}"
        for e in after.errors
        if sig(e) not in base
    ][:20]
