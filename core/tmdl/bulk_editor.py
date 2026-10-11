"""
TMDL Bulk Editor - scoped find/replace and bulk rename on TMDL folders.

Review G-01 / G-02 / G-03 / G-04 / G-13 (2026-09-22) rewrite. Every operation
now works on the indentation-scoped tree (:mod:`core.tmdl.tmdl_tree`) and is a
TRANSACTION:

1. all TMDL files of the definition folder are read (bytes, BOM, line endings
   kept) into an in-memory workspace;
2. the edits are applied in memory — object headers by their exact name span,
   DAX references through the lexer-driven :mod:`core.dax.dax_rename` (strings
   and comments are never touched), structural references (relationships,
   ``sortByColumn``, ``groupByColumn``, hierarchy levels, ``ref table``, role /
   perspective / translation entries) by their TMDL object-ref syntax;
3. the result is validated on a temporary copy; any error that was not already
   present before the edit aborts the whole operation — nothing is written;
4. changed files are written atomically (unique temp + ``os.replace`` through
   ``pbip_utils.save_json_minimal``) and a failed write restores the files
   already written; table files are renamed last (case-only renames through a
   temporary name, renames onto another existing file are refused).

``success`` is False whenever a requested item was skipped or failed.
"""

import logging
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .tmdl_tree import (
    TmdlDocument,
    TmdlNode,
    format_object_ref,
    parse_text,
    split_object_ref,
    tmdl_name,
)
from .validator import TmdlValidator, ValidationResult

logger = logging.getLogger(__name__)

__all__ = [
    "Match",
    "ReplaceResult",
    "RenameResult",
    "TmdlBulkEditor",
    "resolve_definition_dir",
    "FIND_REPLACE_TARGETS",
    "FIND_REPLACE_SCOPES",
]

FIND_REPLACE_TARGETS = (
    "measures",
    "columns",
    "calculation_items",
    "functions",
    "roles",
    "partitions",
    "all",
)
FIND_REPLACE_SCOPES = ("expression", "object", "file")

_WIN_BAD_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Match:
    """Represents a find result"""

    file: str
    line: int
    column: int
    matched_text: str
    context: str  # Surrounding text for preview


@dataclass
class ReplaceResult:
    """Result of a find/replace operation"""

    success: bool
    matches_found: int
    replacements_made: int
    files_modified: int
    backup_path: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    preview: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    scope: str = "expression"
    target: str = "all"
    dry_run: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "success": self.success,
            "matches_found": self.matches_found,
            "replacements_made": self.replacements_made,
            "files_modified": self.files_modified,
            "backup_path": self.backup_path,
            "errors": self.errors,
            "warnings": self.warnings,
            "preview": self.preview,
            "scope": self.scope,
            "target": self.target,
            "dry_run": self.dry_run,
        }


@dataclass
class RenameResult:
    """Result of a bulk rename operation"""

    success: bool
    objects_renamed: int
    references_updated: int
    files_modified: int
    backup_path: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    details: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True
    cascade: Optional[Dict[str, Any]] = None
    preview: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        d: Dict[str, Any] = {
            "success": self.success,
            "objects_renamed": self.objects_renamed,
            "references_updated": self.references_updated,
            "files_modified": self.files_modified,
            "backup_path": self.backup_path,
            "errors": self.errors,
            "warnings": self.warnings,
            "skipped": self.skipped,
            "details": self.details,
            "dry_run": self.dry_run,
        }
        if self.preview:
            d["preview"] = self.preview
        if self.cascade is not None:
            d["cascade"] = self.cascade
        return d


def resolve_definition_dir(tmdl_path: str) -> Path:
    """TMDL definition folder for a ``definition``/``.SemanticModel``/``.pbip`` path."""
    p = Path(tmdl_path)
    if p.is_file() and p.suffix.lower() == ".pbip":
        from core.pbip.pbir_model import resolve_pbip

        res = resolve_pbip(p)
        if len(res.get("models") or []) != 1:
            raise FileNotFoundError(f"{tmdl_path}: expected exactly one semantic model")
        p = Path(res["models"][0]["model_dir"])
    if (p / "definition").is_dir():
        return p / "definition"
    if (p / "tables").is_dir() or (p / "model.tmdl").is_file():
        return p
    raise FileNotFoundError(f"No TMDL definition folder at {tmdl_path}")


def _k(name: Optional[str]) -> str:
    return " ".join((name or "").lower().split())


# ═════════════════════════════════════════════════════════════════════
# Workspace (in-memory transaction)
# ═════════════════════════════════════════════════════════════════════


class _TFile:
    def __init__(self, path: Path, rel: str) -> None:
        self.path = path
        self.rel = rel
        self.original = path.read_bytes()
        text = self.original.decode("utf-8")
        self.bom = text.startswith("﻿")
        self.text = text[1:] if self.bom else text
        self.orig_text = self.text
        self._doc: Optional[TmdlDocument] = None
        self.new_rel: Optional[str] = None

    @property
    def doc(self) -> TmdlDocument:
        if self._doc is None:
            self._doc = parse_text(self.text, path=str(self.path))
        return self._doc

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.text = text
            self._doc = None

    @property
    def changed(self) -> bool:
        return self.text != self.orig_text or self.new_rel is not None

    def payload(self) -> bytes:
        return (("﻿" if self.bom else "") + self.text).encode("utf-8")


class _Workspace:
    def __init__(self, definition: Path) -> None:
        self.definition = definition
        self.files: Dict[str, _TFile] = {}
        for p in sorted(definition.rglob("*.tmdl")):
            rel = p.relative_to(definition).as_posix()
            self.files[rel] = _TFile(p, rel)

    def tables(self) -> List[Tuple[_TFile, TmdlNode]]:
        out = []
        for f in self.files.values():
            if not f.rel.startswith("tables/"):
                continue
            for r in f.doc.roots:
                if r.keyword == "table":
                    out.append((f, r))
        return out

    def find_table(self, name: str) -> Optional[Tuple[_TFile, TmdlNode]]:
        for f, t in self.tables():
            if _k(t.name) == _k(name):
                return f, t
        return None

    def changed(self) -> List[_TFile]:
        return [f for f in self.files.values() if f.changed]

    # -- validation on a scratch copy -----------------------------------
    def validate(
        self, validator_factory: Callable[[], TmdlValidator]
    ) -> Tuple[ValidationResult, ValidationResult]:
        before = validator_factory().validate_syntax(str(self.definition))
        tmp = Path(tempfile.mkdtemp(prefix="tmdl_edit_"))
        try:
            copy = tmp / "definition"
            shutil.copytree(self.definition, copy)
            for f in self.changed():
                target = copy / (f.new_rel or f.rel)
                if f.new_rel:
                    (copy / f.rel).unlink()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(f.payload())
            after = validator_factory().validate_syntax(str(copy))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return before, after

    # -- commit ---------------------------------------------------------
    def commit(self) -> List[str]:
        """Write changed files atomically; restore on failure. Returns written rels."""
        from core.utilities.pbip_utils import notify_path_written, save_json_minimal

        written: List[_TFile] = []
        renamed: List[Tuple[Path, Path]] = []
        try:
            for f in self.changed():
                if f.text != f.orig_text:
                    save_json_minimal(f.path, f.payload(), f.original, raise_on_error=True)
                    written.append(f)
            for f in self.changed():
                if f.new_rel:
                    dst = self.definition / f.new_rel
                    _rename_file(f.path, dst)
                    renamed.append((f.path, dst))
                    notify_path_written(f.path)
                    notify_path_written(dst)
        except Exception:
            for src, dst in reversed(renamed):
                try:
                    _rename_file(dst, src)
                except OSError:
                    logger.error("rollback: could not rename %s back to %s", dst, src)
            for f in written:
                try:
                    save_json_minimal(f.path, f.original, f.payload(), raise_on_error=True)
                except OSError:
                    logger.error("rollback: could not restore %s", f.path)
            raise
        return [f.new_rel or f.rel for f in self.changed()]


def _rename_file(src: Path, dst: Path) -> None:
    """Rename *src* to *dst*; case-only renames go through a temporary name.

    Refuses to overwrite a DIFFERENT existing file (G-01).
    """
    if dst.exists():
        try:
            same = os.path.samefile(src, dst)
        except OSError:
            same = False
        if not same:
            raise FileExistsError(f"refusing to overwrite existing file {dst}")
        tmp = src.with_name(f".{src.name}.{uuid.uuid4().hex[:8]}.renaming")
        os.replace(src, tmp)
        os.replace(tmp, dst)
        return
    os.replace(src, dst)


# ═════════════════════════════════════════════════════════════════════
# Text edit helpers
# ═════════════════════════════════════════════════════════════════════


def _line_offsets(text: str) -> List[int]:
    offs = [0]
    for ln in text.splitlines(keepends=True):
        offs.append(offs[-1] + len(ln))
    return offs


def _expr_region(doc: TmdlDocument, node: TmdlNode, offs: List[int]) -> Optional[Tuple[int, int]]:
    """Absolute ``(start, end)`` character span of *node*'s ``=`` body."""
    if not node.has_expression or node.expr_col < 0:
        return None
    if node.backtick:
        start = offs[node.start + 1] if node.start + 1 < len(offs) else offs[-1]
        close = node.expr_end - 1
        end = offs[close] if close < len(offs) else offs[-1]
        return (start, max(start, end))
    start = offs[node.start] + node.expr_col
    last = max(node.expr_end - 1, node.start)
    end = offs[last] + len(doc.lines[last])
    return (start, end)


def _apply_edits(text: str, edits: List[Tuple[int, int, str]]) -> str:
    if not edits:
        return text
    edits = sorted(edits, key=lambda e: e[0])
    out: List[str] = []
    cursor = 0
    for s, e, rep in edits:
        if s < cursor:
            continue
        out.append(text[cursor:s])
        out.append(rep)
        cursor = e
    out.append(text[cursor:])
    return "".join(out)


def _prop_value_span(doc: TmdlDocument, node: TmdlNode, offs: List[int]) -> Tuple[int, int]:
    line = doc.lines[node.start]
    colon = line.index(":")
    k = colon + 1
    while k < len(line) and line[k] in " \t":
        k += 1
    end = len(line.rstrip())
    return (offs[node.start] + k, offs[node.start] + end)


def _name_span(node: TmdlNode, offs: List[int]) -> Tuple[int, int]:
    return (offs[node.start] + node.name_col[0], offs[node.start] + node.name_col[1])


def _owning(node: TmdlNode, keyword: str) -> Optional[TmdlNode]:
    for a in node.ancestors():
        if a.keyword == keyword:
            return a
    return None


def _is_dax_body(node: TmdlNode) -> bool:
    from .validator import _is_dax_carrier

    return node.has_expression and _is_dax_carrier(node)


def _rls_table(node: TmdlNode) -> Optional[str]:
    return node.name if node.keyword == "tablePermission" else None


def _dax_mask(text: str) -> List[Tuple[int, int]]:
    """Spans of DAX string literals and comments in *text* (never rewritten)."""
    from core.dax.dax_reference_parser import get_lexer
    from core.dax.tokenizer.tokens import TokenType

    masked = (TokenType.STRING, TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK, TokenType.DATETIME)
    spans: List[Tuple[int, int]] = []
    try:
        tokens = get_lexer().tokenize(text)
    except Exception:  # noqa: BLE001 - fall back to "no masking"
        return spans
    for t in tokens:
        if t.type in masked:
            spans.append((t.start, t.end))
    return spans


# ═════════════════════════════════════════════════════════════════════
# Editor
# ═════════════════════════════════════════════════════════════════════


class TmdlBulkEditor:
    """TMDL bulk editor: scoped find/replace + reference-aware bulk rename."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.backup_enabled = self.config.get("backup_before_edit", True)
        self.backup_dir = Path(self.config.get("backup_directory", "./backups/tmdl"))
        self.validator = TmdlValidator(config)

    def _new_validator(self) -> TmdlValidator:
        return TmdlValidator({**self.config, "tom_check": self.config.get("tom_check", "off")})

    # ─── find ────────────────────────────────────────────────────────

    # ─── find / replace ──────────────────────────────────────────────

    def replace_in_measures(
        self,
        tmdl_path: str,
        find: str,
        replace: str,
        regex: bool = False,
        case_sensitive: bool = True,
        dry_run: bool = True,
        target: str = "measures",
        backup: bool = True,
        scope: str = "expression",
        include_strings_and_comments: bool = False,
    ) -> ReplaceResult:
        """Find and replace inside the targeted objects only (G-13).

        target: measures | columns | calculation_items | functions | roles |
        partitions | all. scope: ``expression`` (the ``=`` bodies — DAX strings
        and comments are skipped unless ``include_strings_and_comments``),
        ``object`` (every line of each targeted object block) or ``file``
        (whole files; only with target ``all`` — the pre-2026-09 behaviour).
        """
        result = ReplaceResult(
            success=False,
            matches_found=0,
            replacements_made=0,
            files_modified=0,
            scope=scope,
            target=target,
            dry_run=dry_run,
        )
        if target not in FIND_REPLACE_TARGETS:
            result.errors.append(f"target must be one of {', '.join(FIND_REPLACE_TARGETS)}")
            return result
        if scope not in FIND_REPLACE_SCOPES:
            result.errors.append(f"scope must be one of {', '.join(FIND_REPLACE_SCOPES)}")
            return result
        if scope == "file" and target != "all":
            result.errors.append("scope='file' is only valid with target='all'")
            return result
        try:
            definition = resolve_definition_dir(tmdl_path)
            rx = _compile(find, regex, case_sensitive)
        except FileNotFoundError as e:
            result.errors.append(f"TMDL path does not exist: {e}")
            return result
        except ValueError as e:
            result.errors.append(str(e))
            return result

        try:
            ws = _Workspace(definition)
            for f in ws.files.values():
                offs = _line_offsets(f.text)
                edits: List[Tuple[int, int, str]] = []
                for start, end, is_dax in _regions(f, target, scope, offs):
                    chunk = f.text[start:end]
                    mask = [] if (include_strings_and_comments or not is_dax) else _dax_mask(chunk)
                    for m in rx.finditer(chunk):
                        if _masked(m.start(), m.end(), mask):
                            continue
                        rep = m.expand(replace) if regex else replace
                        result.matches_found += 1
                        if rep != m.group(0):
                            edits.append((start + m.start(), start + m.end(), rep))
                if not edits:
                    continue
                new_text = _apply_edits(f.text, edits)
                result.replacements_made += len(edits)
                old_lines = f.text.splitlines()
                new_lines = new_text.splitlines()
                touched = sorted({_line_of(offs, s) for s, _e, _r in edits})
                for ln in touched[:200]:
                    if ln < len(old_lines):
                        result.preview.append(
                            {
                                "file": str(f.path),
                                "line": ln + 1,
                                "old": old_lines[ln].strip(),
                                "new": new_lines[ln].strip() if ln < len(new_lines) else "",
                            }
                        )
                f.set_text(new_text)

            changed = ws.changed()
            result.files_modified = len(changed)
            if not dry_run and changed:
                if backup and self.backup_enabled:
                    result.backup_path = str(self._create_backup(definition))
                ok = self._validate_and_commit(ws, result.errors, result.warnings)
                if not ok:
                    result.files_modified = 0
                    return result
            result.success = not result.errors
            if scope == "expression":
                result.warnings.append(
                    "scope defaults to 'expression' (DAX bodies only, strings/comments skipped) "
                    "since 2026-09-22; pass scope='object' for whole object blocks or "
                    "scope='file' with target='all' for the old whole-file behaviour"
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error during replace operation: {e}", exc_info=True)
            result.errors.append(f"Operation failed: {e}")
            result.success = False
        return result

    # ─── rename ──────────────────────────────────────────────────────

    def rename_measure(
        self,
        tmdl_path: str,
        table_name: str,
        old_name: str,
        new_name: str,
        update_references: bool = True,
        dry_run: bool = True,
    ) -> RenameResult:
        """Rename a single measure with optional reference updates."""
        renames = [
            {
                "object_type": "measure",
                "table_name": table_name,
                "old_name": old_name,
                "new_name": new_name,
            }
        ]
        return self.bulk_rename(tmdl_path, renames, update_references, dry_run)

    def bulk_rename(
        self,
        tmdl_path: str,
        renames: List[Dict[str, str]],
        update_references: bool = True,
        dry_run: bool = True,
        backup: bool = True,
        cascade_to_reports: Any = None,
    ) -> RenameResult:
        """Rename measures / columns / tables and every reference to them.

        ``update_references`` controls DAX references; structural references
        (relationships, sortBy, groupBy, hierarchy levels, ``ref table``,
        role / perspective / translation entries) are ALWAYS updated — the
        model would not open otherwise.

        ``cascade_to_reports``: ``None``/``False`` = TMDL only; ``True`` = every
        ``.Report`` bound to this model (``definition.pbir``); a path (``.Report``
        folder, ``.pbip`` or folder) = the reports it resolves to that are bound
        to this model. Only renames that succeeded are cascaded, and only after
        the TMDL commit succeeded.
        """
        result = RenameResult(
            success=False,
            objects_renamed=0,
            references_updated=0,
            files_modified=0,
            dry_run=dry_run,
        )
        try:
            definition = resolve_definition_dir(tmdl_path)
        except FileNotFoundError as e:
            result.errors.append(f"TMDL path does not exist: {e}")
            return result

        try:
            ws = _Workspace(definition)
            report_dirs: List[str] = []
            if cascade_to_reports:
                report_dirs, err = _resolve_cascade_reports(definition, cascade_to_reports)
                if err:
                    result.errors.append(err)
                    return result
                bad = [r for r in renames if r.get("object_type") == "table"]
                if bad:
                    result.errors.append(
                        "cascade_to_reports does not support table renames (the PBIR cascade "
                        "engine rewrites Property names, not Entity); rename the table without "
                        "cascade_to_reports or via 02_Model_Operations safe_rename"
                    )
                    return result

            for i, rename in enumerate(renames):
                self._apply_rename(ws, i, rename, update_references, result)

            if cascade_to_reports and not result.errors:
                col_clash = _column_cascade_clashes(ws, result.details)
                if col_clash:
                    result.errors.extend(col_clash)

            changed = ws.changed()
            result.files_modified = len(changed)
            if dry_run:
                for f in changed:
                    result.preview.append(
                        {
                            "file": f.rel,
                            "renamed_to": f.new_rel,
                            "changed_lines": _changed_line_count(f),
                        }
                    )
            if result.errors:
                result.success = False
                if not dry_run:
                    result.files_modified = 0
                    result.warnings.append("nothing was written because of the errors above")
                return result

            if not dry_run and changed:
                if backup and self.backup_enabled:
                    result.backup_path = str(self._create_backup(definition))
                if not self._validate_and_commit(ws, result.errors, result.warnings):
                    result.files_modified = 0
                    return result

            if report_dirs:
                result.cascade = _run_cascade(report_dirs, result.details, dry_run)
                if not result.cascade.get("success"):
                    result.errors.append(
                        "TMDL rename committed, but the PBIR cascade reported errors — see 'cascade'"
                        if not dry_run
                        else "PBIR cascade preview reported errors — see 'cascade'"
                    )
            result.success = not result.errors and not result.skipped
            logger.info(
                f"Bulk rename: {result.objects_renamed} objects, "
                f"{result.references_updated} references updated (dry_run={dry_run})"
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error during bulk rename: {e}", exc_info=True)
            result.errors.append(f"Operation failed: {e}")
            result.success = False
        return result

    # -- single rename (in memory) --------------------------------------
    def _apply_rename(
        self,
        ws: _Workspace,
        i: int,
        rename: Dict[str, str],
        update_references: bool,
        result: RenameResult,
    ) -> None:
        obj_type = rename.get("object_type", "measure")
        old = rename.get("old_name")
        new = rename.get("new_name")
        table_name = rename.get("table_name")
        label = f"rename {i + 1} ({obj_type} {old!r} -> {new!r})"
        if not isinstance(old, str) or not isinstance(new, str) or not old or not new.strip():
            result.errors.append(f"{label}: old_name and new_name must be non-empty strings")
            return
        if new != new.strip() or "\n" in new or "\r" in new:
            result.errors.append(f"{label}: new_name has leading/trailing whitespace or a newline")
            return
        if old == new:
            result.skipped.append({"rename": i + 1, "reason": "old_name == new_name"})
            return
        if obj_type == "table":
            self._rename_table(ws, label, old, new, update_references, result)
        elif obj_type in ("measure", "column"):
            self._rename_field(ws, label, obj_type, table_name, old, new, update_references, result)
        else:
            result.errors.append(f"{label}: unknown object type {obj_type!r}")

    def _rename_field(
        self,
        ws: _Workspace,
        label: str,
        kind: str,
        table_name: Optional[str],
        old: str,
        new: str,
        update_references: bool,
        result: RenameResult,
    ) -> None:
        # locate the object
        hits: List[Tuple[_TFile, TmdlNode, TmdlNode]] = []
        for f, t in ws.tables():
            if table_name and _k(t.name) != _k(table_name):
                continue
            for c in t.children:
                if c.keyword == kind and _k(c.name) == _k(old):
                    hits.append((f, t, c))
        if not hits:
            where = f" in table {table_name!r}" if table_name else ""
            result.errors.append(f"{label}: {kind} {old!r} not found{where}")
            return
        if len(hits) > 1:
            result.errors.append(
                f"{label}: {kind} {old!r} exists in several tables "
                f"({', '.join(sorted(str(h[1].name) for h in hits))}); pass table_name"
            )
            return
        f, table, node = hits[0]
        tname = table.name or ""
        # collisions (G-01 for fields): case-only renames are allowed
        for c in table.children:
            if c is node or c.keyword not in ("column", "measure"):
                continue
            if _k(c.name) == _k(new):
                result.errors.append(
                    f"{label}: table {tname!r} already has a {c.keyword} named {c.name!r}"
                )
                return
        if kind == "measure":
            for f2, t2 in ws.tables():
                for c in t2.children:
                    if c is not node and c.keyword == "measure" and _k(c.name) == _k(new):
                        result.errors.append(
                            f"{label}: measure {new!r} already exists in table {t2.name!r}"
                        )
                        return

        refs = 0
        warnings: List[str] = []
        # 1) header
        offs = _line_offsets(f.text)
        f.set_text(_apply_edits(f.text, [(*_name_span(node, offs), tmdl_name(new))]))
        # 2) structural + DAX references in every file
        from core.dax.dax_rename import rename_column_refs, rename_measure_refs

        lone_left = 0
        for tf in ws.files.values():
            doc = tf.doc
            offs = _line_offsets(tf.text)
            edits: List[Tuple[int, int, str]] = []
            dax_edits = 0
            for n in doc.iter():
                kw = n.keyword
                if kind == "column":
                    if (
                        kw in ("fromColumn", "toColumn")
                        and n.parent is not None
                        and n.parent.keyword == "relationship"
                    ):
                        raw = doc.lines[n.start].split(":", 1)[1].strip()
                        t, c = split_object_ref(raw)
                        if _k(t) == _k(tname) and _k(c) == _k(old):
                            edits.append(
                                (*_prop_value_span(doc, n, offs), format_object_ref(t, new))
                            )
                    elif kw in ("sortByColumn", "groupByColumn") and n.kind == "property":
                        owner = _owning(n, "table")
                        if (
                            owner is not None
                            and _k(owner.name) == _k(tname)
                            and _k(n.value) == _k(old)
                        ):
                            edits.append((*_prop_value_span(doc, n, offs), tmdl_name(new)))
                    elif (
                        kw == "column"
                        and n.kind == "property"
                        and n.parent is not None
                        and n.parent.keyword == "level"
                    ):
                        owner = _owning(n, "table")
                        if (
                            owner is not None
                            and _k(owner.name) == _k(tname)
                            and _k(n.value) == _k(old)
                        ):
                            edits.append((*_prop_value_span(doc, n, offs), tmdl_name(new)))
                    elif kw in ("baseColumn",) and n.kind == "property":
                        t, c = split_object_ref(n.value)
                        if _k(t) == _k(tname) and _k(c) == _k(old):
                            edits.append(
                                (*_prop_value_span(doc, n, offs), format_object_ref(t, new))
                            )
                    elif (
                        kw in ("columnPermission", "perspectiveColumn")
                        and n.parent is not None
                        and _k(n.parent.name) == _k(tname)
                        and _k(n.name) == _k(old)
                    ):
                        edits.append((*_name_span(n, offs), tmdl_name(new)))
                    elif (
                        kw == "column"
                        and n.kind == "object"
                        and n.parent is not None
                        and n.parent.keyword == "table"
                        and _owning(n, "translations") is not None
                        and _k(n.parent.name) == _k(tname)
                        and _k(n.name) == _k(old)
                    ):
                        edits.append((*_name_span(n, offs), tmdl_name(new)))
                    elif (
                        kw == "translationColumn"
                        and n.parent is not None
                        and _k(n.parent.name) == _k(tname)
                        and _k(n.name) == _k(old)
                    ):
                        edits.append((*_name_span(n, offs), tmdl_name(new)))
                else:  # measure
                    if (
                        kw == "perspectiveMeasure"
                        and n.parent is not None
                        and _k(n.parent.name) == _k(tname)
                        and _k(n.name) == _k(old)
                    ):
                        edits.append((*_name_span(n, offs), tmdl_name(new)))
                    elif (
                        kw in ("measure", "translationMeasure")
                        and n.kind == "object"
                        and _owning(n, "cultureInfo") is not None
                        and n.parent is not None
                        and _k(n.parent.name) == _k(tname)
                        and _k(n.name) == _k(old)
                    ):
                        edits.append((*_name_span(n, offs), tmdl_name(new)))
                if update_references and _is_dax_body(n):
                    region = _expr_region(doc, n, offs)
                    if region is None:
                        continue
                    chunk = tf.text[region[0] : region[1]]
                    if kind == "measure":
                        new_chunk, cnt = rename_measure_refs(chunk, old, new, home_table=tname)
                    else:
                        row_ctx = False
                        owner = _owning(n, "table")
                        if (
                            n.keyword == "column"
                            and owner is not None
                            and _k(owner.name) == _k(tname)
                        ):
                            row_ctx = True
                        if _rls_table(n) is not None and _k(n.name) == _k(tname):
                            row_ctx = True
                        new_chunk, cnt = rename_column_refs(
                            chunk, tname, old, new, include_unqualified=row_ctx
                        )
                        if not row_ctx:
                            lone_left += _count_lone(chunk, old)
                    if cnt:
                        edits.append((region[0], region[1], new_chunk))
                        dax_edits += 1
                        refs += cnt
            if edits:
                refs += len(edits) - dax_edits
                tf.set_text(_apply_edits(tf.text, edits))
        if lone_left and kind == "column":
            warnings.append(
                f"{lone_left} unqualified [{old}] reference(s) outside {tname!r}'s row context "
                "were left unchanged (they could be measures or iterator row context) — review them"
            )
        result.objects_renamed += 1
        result.references_updated += refs
        result.warnings.extend(warnings)
        result.details.append(
            {
                "object": f"{tname}[{old}]",
                "new_name": new,
                "type": kind,
                "table": tname,
                "references_updated": refs,
            }
        )

    def _rename_table(
        self,
        ws: _Workspace,
        label: str,
        old: str,
        new: str,
        update_references: bool,
        result: RenameResult,
    ) -> None:
        hit = ws.find_table(old)
        if hit is None:
            result.errors.append(f"{label}: table {old!r} not found")
            return
        f, node = hit
        for f2, t2 in ws.tables():
            if t2 is not node and _k(t2.name) == _k(new):
                result.errors.append(f"{label}: table {t2.name!r} already exists (file {f2.rel})")
                return
        old_name = node.name or old
        new_rel = f"tables/{_WIN_BAD_FILENAME.sub('_', new)}.tmdl"
        target = ws.definition / new_rel
        if target.exists() and not _same_file(target, f.path):
            result.errors.append(f"{label}: refusing to overwrite existing file {new_rel}")
            return
        for other in ws.files.values():
            if other is not f and _k(other.new_rel or "") == _k(new_rel):
                result.errors.append(f"{label}: another rename already targets {new_rel}")
                return

        from core.dax.dax_rename import rename_table_refs

        refs = 0
        offs = _line_offsets(f.text)
        f.set_text(_apply_edits(f.text, [(*_name_span(node, offs), tmdl_name(new))]))
        for tf in ws.files.values():
            doc = tf.doc
            offs = _line_offsets(tf.text)
            edits: List[Tuple[int, int, str]] = []
            for n in doc.iter():
                kw = n.keyword
                if (
                    kw in ("fromColumn", "toColumn")
                    and n.parent is not None
                    and n.parent.keyword == "relationship"
                ):
                    raw = doc.lines[n.start].split(":", 1)[1].strip()
                    t, c = split_object_ref(raw)
                    if _k(t) == _k(old_name):
                        edits.append((*_prop_value_span(doc, n, offs), format_object_ref(new, c)))
                        refs += 1
                elif kw == "ref" and n.ref_kind == "table" and _k(n.name) == _k(old_name):
                    edits.append((*_name_span(n, offs), tmdl_name(new)))
                    refs += 1
                elif kw in ("tablePermission", "perspectiveTable", "translationTable") and _k(
                    n.name
                ) == _k(old_name):
                    edits.append((*_name_span(n, offs), tmdl_name(new)))
                    refs += 1
                elif (
                    kw == "table"
                    and n.parent is not None
                    and n.parent.keyword == "model"
                    and _owning(n, "translations") is not None
                    and _k(n.name) == _k(old_name)
                ):
                    edits.append((*_name_span(n, offs), tmdl_name(new)))
                    refs += 1
                elif kw in ("baseColumn", "defaultHierarchy") and n.kind == "property":
                    t, c = split_object_ref(n.value)
                    if _k(t) == _k(old_name):
                        edits.append((*_prop_value_span(doc, n, offs), format_object_ref(new, c)))
                        refs += 1
                elif kw == "baseTable" and n.kind == "property" and _k(n.value) == _k(old_name):
                    edits.append((*_prop_value_span(doc, n, offs), tmdl_name(new)))
                    refs += 1
                if update_references and _is_dax_body(n):
                    region = _expr_region(doc, n, offs)
                    if region is None:
                        continue
                    chunk = tf.text[region[0] : region[1]]
                    new_chunk, cnt = rename_table_refs(chunk, old_name, new)
                    if cnt:
                        edits.append((region[0], region[1], new_chunk))
                        refs += cnt
            if edits:
                tf.set_text(_apply_edits(tf.text, edits))
        if _k(new_rel) != _k(f.rel) or new_rel != f.rel:
            f.new_rel = new_rel if new_rel != f.rel else None
        result.objects_renamed += 1
        result.references_updated += refs
        result.details.append(
            {
                "object": old_name,
                "new_name": new,
                "type": "table",
                "file": f.rel,
                "new_file": new_rel,
                "references_updated": refs,
            }
        )

    # -- commit -----------------------------------------------------------
    def _validate_and_commit(self, ws: _Workspace, errors: List[str], warnings: List[str]) -> bool:
        before, after = ws.validate(self._new_validator)

        def sig(e: Any) -> Tuple[str, str, str]:
            rel = Path(e.file).name
            return (e.code, rel, re.sub(r"line \d+", "", e.message))

        baseline = {sig(e) for e in before.errors}
        new_errors = [e for e in after.errors if sig(e) not in baseline]
        if new_errors:
            for e in new_errors[:20]:
                errors.append(f"validation: {e.code} {Path(e.file).name}:{e.line} {e.message}")
            errors.append("the edit would introduce TMDL errors — nothing was written")
            return False
        base_w = {sig(w) for w in before.warnings}
        for w in after.warnings:
            if sig(w) not in base_w:
                warnings.append(f"validation: {w.code} {Path(w.file).name}:{w.line} {w.message}")
        try:
            ws.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(f"write failed, all files restored: {e}")
            return False
        return True

    def _create_backup(self, path: Path) -> Path:
        """Create timestamped backup of the TMDL folder"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"{path.parent.name or path.name}_backup_{timestamp}"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(path, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return backup_path


# ═════════════════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════════════════


def _changed_line_count(f: _TFile) -> int:
    a = f.orig_text.splitlines()
    b = f.text.splitlines()
    return sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))


def _count_lone(chunk: str, name: str) -> int:
    from core.dax.dax_reference_parser import scan_references

    try:
        return sum(
            1 for r in scan_references(chunk) if r.kind == "bracket" and _k(r.name) == _k(name)
        )
    except Exception:  # noqa: BLE001
        return 0


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _compile(pattern: str, regex: bool, case_sensitive: bool) -> "re.Pattern[str]":
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("pattern must be a non-empty string")
    if regex and len(pattern) > 4096:
        raise ValueError("Regex pattern too long (max 4096 chars)")
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern: {e}")


def _masked(s: int, e: int, spans: List[Tuple[int, int]]) -> bool:
    for a, b in spans:
        if s < b and e > a:
            return True
    return False


def _line_of(offs: List[int], pos: int) -> int:
    import bisect

    return max(0, bisect.bisect_right(offs, pos) - 1)


_TARGET_OBJECTS = {
    "measures": {"measure"},
    "columns": {"column"},
    "calculation_items": {"calculationItem"},
    "functions": {"function"},
    "roles": {"tablePermission"},
    "partitions": {"partition"},
}


def _target_object(node: TmdlNode, target: str) -> bool:
    """Is *node* (or the object that owns it) one of the targeted objects?"""
    kinds = set().union(*_TARGET_OBJECTS.values()) if target == "all" else _TARGET_OBJECTS[target]
    probe: Optional[TmdlNode] = node
    while probe is not None:
        if probe.keyword in kinds and probe.kind == "object":
            if (
                probe.keyword == "column"
                and probe.parent is not None
                and probe.parent.keyword != "table"
            ):
                return False
            return True
        if probe.keyword in ("table", "role", "cultureInfo", "perspective"):
            return False
        probe = probe.parent
    return False


def _regions(f: _TFile, target: str, scope: str, offs: List[int]) -> List[Tuple[int, int, bool]]:
    """``(start, end, is_dax)`` spans of *f* that a find/replace may touch."""
    doc = f.doc
    if scope == "file":
        return [(0, len(f.text), False)]
    out: List[Tuple[int, int, bool]] = []
    if scope == "object":
        for n in doc.iter():
            if n.kind != "object" or not _target_object(n, target):
                continue
            if n.parent is not None and _target_object(n.parent, target):
                continue  # nested inside an already-covered object
            start = offs[n.span_start]
            end = offs[min(n.end, len(offs) - 1)]
            out.append((start, end, False))
        return out
    for n in doc.iter():
        if not n.has_expression:
            continue
        if not _target_object(n, target):
            continue
        is_dax = _is_dax_body(n)
        is_m = n.keyword == "source" and not is_dax
        if not is_dax and not (is_m and target in ("partitions", "all")):
            continue
        region = _expr_region(doc, n, offs)
        if region is not None and region[1] > region[0]:
            out.append((region[0], region[1], is_dax))
    return out


def _resolve_cascade_reports(definition: Path, cascade: Any) -> Tuple[List[str], Optional[str]]:
    from core.pbip.pbir_model import find_reports_bound_to_model, resolve_pbip

    model_dir = definition.parent
    if cascade is True:
        reports = find_reports_bound_to_model(str(model_dir))
        if not reports:
            return [], "cascade_to_reports=true but no .Report folder is bound to this model"
        return [str(r) for r in reports], None
    if not isinstance(cascade, str):
        return [], "cascade_to_reports must be true or a path (.Report folder, .pbip or folder)"
    res = resolve_pbip(cascade)
    bound: List[str] = []
    other: List[str] = []
    for r in res.get("reports") or []:
        md = r.get("model_dir")
        if md and _same_file(Path(md), model_dir):
            bound.append(r["report_dir"])
        else:
            other.append(r.get("name") or r.get("report_dir"))
    if not bound:
        extra = f" (found {', '.join(map(str, other))} bound to other models)" if other else ""
        return (
            [],
            f"cascade_to_reports={cascade!r} resolves to no report bound to this model{extra}",
        )
    return bound, None


def _column_cascade_clashes(ws: _Workspace, details: List[Dict[str, Any]]) -> List[str]:
    """The PBIR cascade renames Column properties by NAME: refuse when another
    table has a column with the same old name (its bindings would be renamed too)."""
    out: List[str] = []
    for d in details:
        if d.get("type") != "column":
            continue
        old = d["object"].split("[", 1)[1][:-1]
        for _f, t in ws.tables():
            if _k(t.name) == _k(d.get("table")):
                continue
            if any(c.keyword == "column" and _k(c.name) == _k(old) for c in t.children):
                out.append(
                    f"cannot cascade column rename {d['object']} to reports: table {t.name!r} also has "
                    f"a column {old!r} and the PBIR cascade matches column names without the table"
                )
                break
    return out


def _run_cascade(
    report_dirs: List[str], details: List[Dict[str, Any]], dry_run: bool
) -> Dict[str, Any]:
    from core.pbip.cascade_rename import CascadeRenameEngine

    by_kind: Dict[str, Dict[str, str]] = {}
    for d in details:
        if d.get("type") in ("measure", "column"):
            old = d["object"].split("[", 1)[1][:-1]
            by_kind.setdefault(d["type"], {})[old] = d["new_name"]
    out: Dict[str, Any] = {"success": True, "dry_run": dry_run, "reports": []}
    if not by_kind:
        return out
    for rd in report_dirs:
        try:
            res = CascadeRenameEngine(rd).apply_renames_typed(by_kind, dry_run=dry_run)
        except Exception as e:  # noqa: BLE001
            res = {"success": False, "errors": [str(e)]}
        res["report"] = rd
        out["reports"].append(res)
        if not res.get("success"):
            out["success"] = False
    return out
