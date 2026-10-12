"""
PBIP Git Analyzer - Semantic diff and change analysis for PBIP projects.

Analyzes git changes to TMDL files and produces business-readable
summaries of what changed (measures added/modified/removed, relationships
changed, etc.) rather than raw text diffs.

Review G-15 (2026-09-22):
* a DAX-body edit is a "modified" object — every ``--unified=0`` hunk is mapped
  to the object that OWNS those lines by parsing the old and new file text with
  the TMDL tree (the header-only regex saw only header edits); quoted AND bare
  names; measures, columns, calc items, partitions, functions, hierarchies,
  expressions, relationships, roles;
* refs are validated (``rev-parse --verify --end-of-options``) and refused when
  they look like options (``from_ref="--output=C:/x"``);
* git output is decoded as UTF-8 (``core.quotePath=false``, ``-z`` name-status,
  renames ``R100 old new`` parsed);
* the diff is scoped to the project (pathspec), and the working-tree mode
  covers staged + unstaged + untracked files.
"""

import logging
import re as _re_pga
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Header patterns (fallback when the file text is not available).
_NAME = r"(?:'((?:[^']|'')+)'|([^\s=']+(?:\s+[^\s=']+)*?))"
_TMDL_OBJECT_PATTERNS = {
    "measure": _re_pga.compile(rf"^[+\-]\s*measure\s+{_NAME}\s*(?:=|$)", _re_pga.MULTILINE),
    "column": _re_pga.compile(rf"^[+\-]\s*column\s+{_NAME}\s*(?:=|$)", _re_pga.MULTILINE),
    "relationship": _re_pga.compile(r"^[+\-]\s*relationship\s+([\w\-]+)()", _re_pga.MULTILINE),
    "role": _re_pga.compile(rf"^[+\-]\s*role\s+{_NAME}\s*$", _re_pga.MULTILINE),
}

_TMDL_BUCKET_FOR = {
    "measure": "measures",
    "column": "columns",
    "relationship": "relationships",
    "role": "roles",
    "calculationItem": "calculation_items",
    "partition": "partitions",
    "function": "functions",
    "hierarchy": "hierarchies",
    "expression": "expressions",
    "tablePermission": "roles",
}
_BUCKETS = (
    "measures",
    "columns",
    "relationships",
    "roles",
    "calculation_items",
    "partitions",
    "functions",
    "hierarchies",
    "expressions",
    "tables",
)

_HUNK_RE = _re_pga.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", _re_pga.MULTILINE)


class PbipGitAnalyzer:
    """Analyzes git changes to PBIP projects with semantic understanding."""

    def __init__(self, repo_path: str, pathspecs: Optional[List[str]] = None):
        """
        Args:
            repo_path: Path inside a git repository (PBIP project folder).
            pathspecs: Optional paths (absolute or repo-relative) that scope
                every diff; default = *repo_path* itself.
        """
        self.repo_path = Path(repo_path).resolve()
        self.git_root = self._find_git_root()
        self.pathspecs = self._relative_pathspecs(pathspecs or [str(self.repo_path)])

    def _find_git_root(self) -> Path:
        """Find the git repository root."""
        cwd = self.repo_path if self.repo_path.is_dir() else self.repo_path.parent
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(cwd),
                timeout=10,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, NotADirectoryError):
            pass
        raise ValueError(f"Not a git repository: {self.repo_path}")

    def _relative_pathspecs(self, paths: List[str]) -> List[str]:
        out: List[str] = []
        root = Path(self.git_root).resolve()
        for p in paths:
            pp = Path(p)
            try:
                rel = pp.resolve().relative_to(root) if pp.is_absolute() else pp
            except ValueError:
                continue
            spec = rel.as_posix() or "."
            if spec not in out:
                out.append(spec)
        return out or ["."]

    def _run_git(self, args: List[str], **kwargs) -> str:
        """Run a git command (UTF-8, unquoted paths) and return stdout."""
        if "input" not in kwargs:
            kwargs.setdefault("stdin", subprocess.DEVNULL)  # not the MCP server's stdio pipe
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false"] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.git_root),
            timeout=30,
            **kwargs,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
        return result.stdout

    # ── ref safety ────────────────────────────────────────────────────

    def _verify_ref(self, ref: str) -> str:
        """Refuse option-looking / malformed refs and verify they resolve to a commit."""
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("git ref must be a non-empty string")
        ref = ref.strip()
        if (
            ref.startswith("-")
            or ".." in ref
            or ":" in ref
            or any(ch.isspace() or ord(ch) < 32 for ch in ref)
        ):
            raise ValueError(f"invalid git ref: {ref!r}")
        try:
            self._run_git(
                ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"]
            )
        except RuntimeError as e:
            raise ValueError(f"unknown git ref {ref!r}: {e}")
        return ref

    # ── modes ─────────────────────────────────────────────────────────

    def analyze_working_changes(self) -> Dict[str, Any]:
        """TMDL/report changes since HEAD: staged + unstaged + untracked."""
        spec = ["--"] + self.pathspecs
        staged = self._run_git(["diff", "--cached", "--name-status", "-z"] + spec)
        unstaged = self._run_git(["diff", "--name-status", "-z"] + spec)
        untracked = self._run_git(["ls-files", "--others", "--exclude-standard", "-z"] + spec)

        changes = self._parse_name_status(staged)
        for st, fp in self._parse_name_status(unstaged):
            if (st, fp) not in changes and fp not in {c[1] for c in changes}:
                changes.append((st, fp))
        for item in _split_z_or_lines(untracked):
            if (
                item
                and item.endswith((".tmdl", ".json", ".pbir"))
                and item not in {c[1] for c in changes}
            ):
                changes.append(("A", item))

        result = self._categorize_changes(changes)
        result["pathspecs"] = self.pathspecs
        result["details"] = self._collect_tmdl_details_working(changes)
        return result

    def analyze_commit_diff(self, from_ref: str = "HEAD~1", to_ref: str = "HEAD") -> Dict[str, Any]:
        """TMDL/report changes between two refs."""
        from_ref = self._verify_ref(from_ref)
        to_ref = self._verify_ref(to_ref)
        output = self._run_git(
            ["diff", "--name-status", "-z", from_ref, to_ref, "--"] + self.pathspecs
        )
        changes = self._parse_name_status(output)
        result = self._categorize_changes(changes)
        result["from_ref"] = from_ref
        result["to_ref"] = to_ref
        result["pathspecs"] = self.pathspecs
        result["details"] = self._collect_tmdl_details(changes, from_ref, to_ref)
        return result

    def summarize_pr_changes(self, base_branch: str = "main") -> Dict[str, Any]:
        """Changes of HEAD against the merge base with *base_branch*."""
        base_branch = self._verify_ref(base_branch)
        try:
            merge_base = self._run_git(
                ["merge-base", "--end-of-options", base_branch, "HEAD"]
            ).strip()
        except RuntimeError:
            merge_base = base_branch
        merge_base = merge_base or base_branch

        output = self._run_git(
            ["diff", "--name-status", "-z", merge_base, "HEAD", "--"] + self.pathspecs
        )
        changes = self._parse_name_status(output)
        result = self._categorize_changes(changes)
        try:
            log = self._run_git(
                ["log", "--oneline", f"{merge_base}..HEAD", "--"] + self.pathspecs
            ).strip()
            result["commits"] = log.split("\n") if log else []
        except RuntimeError:
            result["commits"] = []
        result["base_branch"] = base_branch
        result["merge_base"] = merge_base
        result["pathspecs"] = self.pathspecs
        result["details"] = self._collect_tmdl_details(changes, merge_base, "HEAD")
        result["summary"] = self._generate_summary_text(result)
        return result

    # ── name-status ───────────────────────────────────────────────────

    def _parse_name_status(self, output: str) -> List[tuple]:
        """``git diff --name-status`` (``-z`` or tab lines) -> ``[(status, path)]``.

        Renames/copies (``R100 old new``) yield the NEW path with status ``R``.
        """
        changes: List[tuple] = []
        if "\0" in output:
            parts = [p for p in output.split("\0")]
            i = 0
            while i < len(parts):
                st = parts[i].strip()
                if not st:
                    i += 1
                    continue
                code = st[0]
                if code in ("R", "C"):
                    if i + 2 < len(parts):
                        _keep(changes, code, parts[i + 2])
                    i += 3
                else:
                    if i + 1 < len(parts):
                        _keep(changes, code, parts[i + 1])
                    i += 2
            return changes
        for line in output.strip().split("\n"):
            line = line.strip("\r")
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            code = fields[0].strip()[:1]
            path = fields[2] if code in ("R", "C") and len(fields) >= 3 else fields[1]
            _keep(changes, code, path)
        return changes

    def _categorize_changes(self, changes: List[tuple]) -> Dict[str, Any]:
        """Categorize file changes into semantic categories."""
        result: Dict[str, Any] = {
            "tables": {"added": [], "modified": [], "deleted": []},
            "relationships": {"modified": False},
            "expressions": {"modified": False},
            "functions": {"modified": False},
            "roles": {"added": [], "modified": [], "deleted": []},
            "perspectives": {"added": [], "modified": [], "deleted": []},
            "cultures": {"added": [], "modified": [], "deleted": []},
            "report": {"modified": False, "pages_changed": []},
            "model_config": {"modified": False},
            "total_tmdl_changes": 0,
            "total_report_changes": 0,
            "files": [],
        }
        for status, filepath in changes:
            fp = Path(filepath)
            parts = fp.parts
            name = fp.stem
            action = {"A": "added", "M": "modified", "D": "deleted", "R": "added"}.get(
                status, "modified"
            )
            result["files"].append({"status": status, "path": filepath})
            if filepath.endswith(".tmdl"):
                result["total_tmdl_changes"] += 1
                if "tables" in parts:
                    result["tables"][action].append(name)
                elif fp.name == "relationships.tmdl":
                    result["relationships"]["modified"] = True
                elif fp.name == "expressions.tmdl":
                    result["expressions"]["modified"] = True
                elif fp.name == "functions.tmdl" or "functions" in parts:
                    result["functions"]["modified"] = True
                elif fp.name in ("model.tmdl", "database.tmdl"):
                    result["model_config"]["modified"] = True
                elif "roles" in parts:
                    result["roles"][action].append(name)
                elif "perspectives" in parts:
                    result["perspectives"][action].append(name)
                elif "cultures" in parts:
                    result["cultures"][action].append(name)
            elif filepath.endswith((".json", ".pbir")):
                result["total_report_changes"] += 1
                result["report"]["modified"] = True
                if "pages" in parts:
                    try:
                        idx = list(parts).index("pages")
                        if idx + 1 < len(parts) and not parts[idx + 1].endswith(".json"):
                            page = parts[idx + 1]
                            if page not in result["report"]["pages_changed"]:
                                result["report"]["pages_changed"].append(page)
                    except (ValueError, IndexError):
                        pass
        return result

    # ── object-level TMDL diff ────────────────────────────────────────

    def _parse_tmdl_diff(
        self,
        diff_text: str,
        filepath: str,
        old_text: Optional[str] = None,
        new_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Object-level changes of one TMDL file.

        Each ``--unified=0`` hunk is mapped to the objects that own the changed
        lines in the OLD file (``-a,b``) and in the NEW file (``+c,d``); an object
        present on both sides is ``modified``, only new = ``added``, only old =
        ``removed``. Header lines on ``+``/``-`` lines are used as well (and are
        the only signal when the file texts are unavailable).
        """
        out: Dict[str, Any] = {"file": filepath}
        for b in _BUCKETS:
            out[b] = {"added": [], "modified": [], "removed": []}
        touched_old: Dict[str, set] = {b: set() for b in _BUCKETS}
        touched_new: Dict[str, set] = {b: set() for b in _BUCKETS}
        old_all: Dict[str, set] = {b: set() for b in _BUCKETS}
        new_all: Dict[str, set] = {b: set() for b in _BUCKETS}

        old_owner = _owners(old_text) if old_text is not None else None
        new_owner = _owners(new_text) if new_text is not None else None
        if old_owner is not None:
            for bucket, name in old_owner[1]:
                old_all[bucket].add(name)
        if new_owner is not None:
            for bucket, name in new_owner[1]:
                new_all[bucket].add(name)

        for m in _HUNK_RE.finditer(diff_text or ""):
            a, b = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            c, d = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
            if old_owner is not None:
                for ln in range(a, a + b):
                    o = old_owner[0].get(ln - 1)
                    if o:
                        touched_old[o[0]].add(o[1])
            if new_owner is not None:
                for ln in range(c, c + d):
                    o = new_owner[0].get(ln - 1)
                    if o:
                        touched_new[o[0]].add(o[1])

        # header-line fallback (+/- header lines in the patch)
        head_added: Dict[str, set] = {b: set() for b in _BUCKETS}
        head_removed: Dict[str, set] = {b: set() for b in _BUCKETS}
        for kind, pattern in _TMDL_OBJECT_PATTERNS.items():
            bucket = _TMDL_BUCKET_FOR[kind]
            for mm in pattern.finditer(diff_text or ""):
                name = (
                    (mm.group(1) or "").replace("''", "'") if mm.group(1) else (mm.group(2) or "")
                )
                name = name.strip()
                if not name:
                    continue
                (head_added if mm.group(0).lstrip().startswith("+") else head_removed)[bucket].add(
                    name
                )

        have_texts = old_owner is not None and new_owner is not None
        for bucket in _BUCKETS:
            changed = touched_old[bucket] | touched_new[bucket]
            added, removed, modified = set(), set(), set()
            if have_texts:
                for name in changed:
                    if name in old_all[bucket] and name in new_all[bucket]:
                        modified.add(name)
                    elif name in new_all[bucket]:
                        added.add(name)
                    elif name in old_all[bucket]:
                        removed.add(name)
            ha, hr = head_added[bucket], head_removed[bucket]
            known = old_all[bucket] | new_all[bucket]
            if have_texts:
                added |= {n for n in ha if n in new_all[bucket] and n not in old_all[bucket]}
                removed |= {n for n in hr if n in old_all[bucket] and n not in new_all[bucket]}
                modified |= {n for n in (ha | hr) if n in old_all[bucket] and n in new_all[bucket]}
            # names the file texts do not know (texts unavailable): +/- header rule
            ua, ur = ha - known, hr - known
            modified |= ua & ur
            added |= ua - ur
            removed |= ur - ua
            out[bucket] = {
                "added": sorted(added),
                "modified": sorted(modified),
                "removed": sorted(removed),
            }
        return out

    def _show(self, ref: Optional[str], filepath: str) -> Optional[str]:
        """File text at *ref* (``None`` = working tree); None if absent."""
        if ref is None:
            path = Path(self.git_root) / filepath
            try:
                return path.read_text(encoding="utf-8-sig")
            except OSError:
                return None
        try:
            return self._run_git(["show", f"{ref}:{filepath}"])
        except Exception:  # noqa: BLE001 - absent on that side
            return None

    def _diff_tmdl_file(
        self, filepath: str, from_ref: str, to_ref: Optional[str]
    ) -> Dict[str, Any]:
        """Object-level diff of *filepath* between *from_ref* and *to_ref*
        (``to_ref=None`` = the working tree)."""
        try:
            args = (
                ["diff", "--unified=0", from_ref] + ([to_ref] if to_ref else []) + ["--", filepath]
            )
            diff_text = self._run_git(args)
        except Exception as e:  # noqa: BLE001
            return {"file": filepath, "error": str(e)}
        old_text = self._show(from_ref, filepath)
        new_text = self._show(to_ref, filepath)
        return self._parse_tmdl_diff(
            diff_text,
            filepath,
            old_text if old_text is not None else "",
            new_text if new_text is not None else "",
        )

    def _collect_tmdl_details(
        self, changes: List[tuple], from_ref: str, to_ref: str
    ) -> List[Dict[str, Any]]:
        details: List[Dict[str, Any]] = []
        for status, fp in changes:
            if status in ("M", "A", "R", "D") and fp.endswith(".tmdl"):
                details.append(self._diff_tmdl_file(fp, from_ref, to_ref))
        return details

    def _collect_tmdl_details_working(self, changes: List[tuple]) -> List[Dict[str, Any]]:
        """Working tree vs HEAD (staged + unstaged) per changed .tmdl file."""
        details: List[Dict[str, Any]] = []
        seen: set = set()
        for status, fp in changes:
            if not fp.endswith(".tmdl") or fp in seen:
                continue
            seen.add(fp)
            if status == "A":
                old = ""
            else:
                old = self._show("HEAD", fp) or ""
            try:
                diff_text = (
                    self._run_git(["diff", "HEAD", "--unified=0", "--", fp])
                    if status != "A"
                    else ""
                )
            except Exception as e:  # noqa: BLE001
                details.append({"file": fp, "error": str(e)})
                continue
            new = self._show(None, fp) or ""
            if status == "A":
                n_lines = new.count("\n") + 1
                diff_text = f"@@ -0,0 +1,{n_lines} @@\n"
            details.append(self._parse_tmdl_diff(diff_text, fp, old, new))
        return details

    def _generate_summary_text(self, categorized: Dict[str, Any]) -> str:
        """Business-readable summary of changes."""
        lines = []
        tables = categorized.get("tables", {})
        if tables.get("added"):
            lines.append(f"Added tables: {', '.join(tables['added'])}")
        if tables.get("modified"):
            lines.append(f"Modified tables: {', '.join(tables['modified'])}")
        if tables.get("deleted"):
            lines.append(f"Deleted tables: {', '.join(tables['deleted'])}")
        if categorized.get("relationships", {}).get("modified"):
            lines.append("Relationships modified")
        if categorized.get("expressions", {}).get("modified"):
            lines.append("Shared expressions/parameters modified")
        if categorized.get("functions", {}).get("modified"):
            lines.append("User-defined functions modified")
        roles = categorized.get("roles", {})
        if any(roles.get(k) for k in ("added", "modified", "deleted")):
            lines.append(
                f"Security roles changed: +{len(roles.get('added', []))} "
                f"~{len(roles.get('modified', []))} -{len(roles.get('deleted', []))}"
            )
        if categorized.get("model_config", {}).get("modified"):
            lines.append("Model configuration changed")
        details = categorized.get("details") or []
        n_meas = sum(len((d.get("measures") or {}).get("modified", [])) for d in details)
        if n_meas:
            lines.append(f"{n_meas} measure(s) modified")
        report = categorized.get("report", {})
        if report.get("modified"):
            pages = report.get("pages_changed", [])
            lines.append(
                f"Report pages changed: {', '.join(pages)}"
                if pages
                else "Report definition changed"
            )
        if not lines:
            lines.append("No semantic model or report changes detected")
        return "; ".join(lines)


def _keep(changes: List[tuple], code: str, path: str) -> None:
    path = (path or "").strip()
    if path.endswith((".tmdl", ".json", ".pbir")):
        changes.append((code, path))


def _split_z_or_lines(output: str) -> List[str]:
    if "\0" in output:
        return [p.strip() for p in output.split("\0") if p.strip()]
    return [ln.strip() for ln in output.splitlines() if ln.strip()]


def _owners(text: Optional[str]) -> Tuple[Dict[int, Tuple[str, str]], List[Tuple[str, str]]]:
    """``({line_index: (bucket, qualified name)}, [all (bucket, name)])`` for a TMDL text."""
    from core.tmdl.tmdl_tree import parse_text

    line_owner: Dict[int, Tuple[str, str]] = {}
    objects: List[Tuple[str, str]] = []
    if not text:
        return line_owner, objects
    try:
        doc = parse_text(text)
    except Exception:  # noqa: BLE001
        return line_owner, objects

    def visit(node, table: Optional[str], owner: Optional[Tuple[str, str]]) -> None:
        kw = node.keyword
        bucket = _TMDL_BUCKET_FOR.get(kw) if node.kind == "object" else None
        if kw == "column" and node.kind != "object":
            bucket = None
        cur = owner
        if kw == "table" and node.kind == "object" and node.parent is None:
            table = node.name
            cur = ("tables", node.name or "")
            objects.append(cur)
        elif (
            bucket and node.name is not None and (owner is None or owner[0] in ("tables", "roles"))
        ):
            name = node.name
            if bucket == "roles" and kw == "tablePermission":
                name = f"{owner[1]}/{node.name}" if owner else node.name
                bucket = "roles"
                cur = (bucket, owner[1]) if owner else (bucket, name)
            else:
                cur = (bucket, name)
                objects.append(cur)
        elif kw == "role" and node.kind == "object":
            cur = ("roles", node.name or "")
            objects.append(cur)
        if cur is not None:
            for ln in range(node.span_start, node.end):
                if ln not in line_owner or line_owner[ln][0] == "tables":
                    line_owner[ln] = cur
        for c in node.children:
            visit(c, table, cur)

    for root in doc.roots:
        visit(root, None, None)
    return line_owner, objects
