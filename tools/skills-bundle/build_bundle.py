#!/usr/bin/env python3
"""Build a sanitized, shareable bundle of the user's PBI + Fabric Claude Code skills.

Usage:
    python build_bundle.py --scan          # dry-run: print the sanitization hit-list, write nothing
    python build_bundle.py --build         # sanitize-copy into staging, validate, zip to ~/.claude/downloads
    python build_bundle.py --build --out <dir>   # override output dir

The builder copies a curated set of skills/agents/rules out of ~/.claude, strips
machine- and client-specific content (paths, doc-library pointers, G01/Wealth-Reporting
specifics, Teams webhooks, the finticx DevOps org, personal emails), and produces a
README + installers so a colleague can drop the skills into their own ~/.claude.

Pure sanitization logic lives in `sanitizer.sanitize_text` so it is unit-testable
without touching the filesystem.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

# Windows consoles default to cp1252; our output uses arrows/em-dashes.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# Allow `import sanitizer` / `from sanitizer import ...` when run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sanitizer import (  # noqa: E402
    RESIDUAL_PATTERNS,
    scan_text,
    sanitize_text,
)

# --------------------------------------------------------------------------- #
# Curated selection (see design spec 2026-06-30).
# --------------------------------------------------------------------------- #
SKILLS = [
    "autonomous-dax-debug",
    "autonomous-pbi-build",
    "dax",
    "fabric",
    "pbi-bookmarks",
    "pbi-custom-visuals",
    "pbi-lineage",
    "pbi-live-iterate",
    "pbi-model-audit",
    "pbi-query-plan",
    "pbi-report-audit",
    "pbi-theme",
    "pbi-visual-author",
    "pbip",
    "pbir-page-builder",
    "powerbi-data-modeling",
    "tabular-editor",
]
# Explicitly excluded skill: g01-autonomous-task (client-specific).

AGENTS = [
    "FabricAdmin.md",
    "FabricAppDev.md",
    "FabricDataEngineer.md",
    "FabricIQ.md",
    "FabricMigrationEngineer.md",
    "bpa-expression-helper.md",
    "pbip-validator.md",
    "query-listener.md",
    "semantic-model-auditor.md",
    "visual-script-reviewer.md",
]

RULES = ["fabric-cli.md"]

# Text file extensions we sanitize. Everything else copies verbatim.
TEXT_EXTS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg"}

BUNDLE_NAME = "pbi-fabric-skills-bundle"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def home_dir() -> Path:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if not home:
        sys.exit("ERROR: cannot resolve home directory (USERPROFILE/HOME unset).")
    return Path(home)


def claude_home() -> Path:
    return home_dir() / ".claude"


def default_out_dir() -> Path:
    """The user's real Downloads folder if it exists, else ~/.claude/downloads."""
    dl = home_dir() / "Downloads"
    return dl if dl.is_dir() else (claude_home() / "downloads")


def iter_source_files() -> list[tuple[str, Path]]:
    """Yield (relative-dest-path, absolute-source-path) for every file to ship."""
    root = claude_home()
    out: list[tuple[str, Path]] = []
    for skill in SKILLS:
        base = root / "skills" / skill
        if not base.is_dir():
            print(f"  WARN: skill folder missing, skipping: {base}", file=sys.stderr)
            continue
        for f in sorted(base.rglob("*")):
            if f.is_file():
                rel = Path("skills") / skill / f.relative_to(base)
                out.append((rel.as_posix(), f))
    for agent in AGENTS:
        src = root / "agents" / agent
        if src.is_file():
            out.append((f"agents/{agent}", src))
        else:
            print(f"  WARN: agent missing, skipping: {src}", file=sys.stderr)
    for rule in RULES:
        src = root / "rules" / rule
        if src.is_file():
            out.append((f"rules/{rule}", src))
        else:
            print(f"  WARN: rule missing, skipping: {src}", file=sys.stderr)
    return out


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTS


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_scan() -> int:
    files = iter_source_files()
    total_hits = 0
    touched = 0
    print(f"Dry-run sanitization scan over {len(files)} files\n" + "=" * 70)
    for rel, src in files:
        if not is_text(src):
            continue
        try:
            text = src.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"  (skip non-utf8) {rel}", file=sys.stderr)
            continue
        hits = scan_text(text)
        if hits:
            touched += 1
            total_hits += len(hits)
            print(f"\n### {rel}  ({len(hits)} change(s))")
            for h in hits:
                print(f"  L{h.line_no}: {h.kind}")
                print(f"      - {h.original.strip()[:160]}")
                if h.replacement is None:
                    print("      → [STRIP LINE]")
                else:
                    print(f"      → {h.replacement.strip()[:160]}")
    print("\n" + "=" * 70)
    print(
        f"Summary: {total_hits} change(s) across {touched} file(s) "
        f"(of {len(files)} shipped files)."
    )
    print("Review above. Run with --build to produce the sanitized bundle.")
    return 0


def cmd_build(out_dir: Path) -> int:
    files = iter_source_files()
    staging = out_dir / BUNDLE_NAME
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    report_lines: list[str] = [
        f"Sanitization report for {BUNDLE_NAME}",
        "=" * 60,
        "",
    ]
    total_changes = 0

    for rel, src in files:
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if is_text(src):
            try:
                text = src.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(src, dest)
                continue
            clean, hits = sanitize_text(text)
            dest.write_text(clean, encoding="utf-8", newline="\n")
            if hits:
                total_changes += len(hits)
                report_lines.append(f"{rel}  ({len(hits)} change(s)):")
                for h in hits:
                    # Show the RESULT, not the raw original — the report ships in
                    # the bundle and must not re-leak the scrubbed values.
                    if h.replacement is None:
                        report_lines.append(
                            f"  L{h.line_no} [STRIP]   {h.kind}: " f"<line removed>"
                        )
                    else:
                        report_lines.append(
                            f"  L{h.line_no} [REPLACE] {h.kind}: "
                            f"now → {h.replacement.strip()[:140]}"
                        )
                report_lines.append("")
        else:
            shutil.copy2(src, dest)

    # Static assets: README + installers.
    _write_static_assets(staging)

    # Sanitization report.
    report_lines.insert(2, f"Total changes: {total_changes}")
    (staging / "SANITIZATION-REPORT.txt").write_text(
        "\n".join(report_lines) + "\n", encoding="utf-8", newline="\n"
    )

    # Validation gate — fail the build on any residual sensitive token.
    residuals = _validate(staging)
    if residuals:
        print("\nBUILD FAILED — residual sensitive tokens found:", file=sys.stderr)
        for r in residuals:
            print(f"  {r}", file=sys.stderr)
        return 2

    # Zip it.
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{BUNDLE_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(staging.rglob("*")):
            if f.is_file():
                zf.write(f, Path(BUNDLE_NAME) / f.relative_to(staging))

    n_skills = len(SKILLS)
    n_agents = len(AGENTS)
    print(f"\nBuilt {BUNDLE_NAME}")
    print(f"  {n_skills} skills, {n_agents} agents, {len(RULES)} rule(s)")
    print(f"  {total_changes} sanitization change(s) — see SANITIZATION-REPORT.txt")
    print(f"  staging: {staging}")
    print(f"  zip:     {zip_path}")
    return 0


def _validate(staging: Path) -> list[str]:
    hits: list[str] = []
    for f in sorted(staging.rglob("*")):
        if not f.is_file() or not is_text(f):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for name, pat in RESIDUAL_PATTERNS.items():
                if pat.search(line):
                    rel = f.relative_to(staging).as_posix()
                    hits.append(f"{rel}:{i} [{name}] {line.strip()[:120]}")
    return hits


def _write_static_assets(staging: Path) -> None:
    here = Path(__file__).resolve().parent / "assets"
    for name in ("README.md", "install.ps1", "install.sh", "Install (double-click me).bat"):
        src = here / name
        if not src.is_file():
            sys.exit(f"ERROR: missing static asset {src}")
        shutil.copy2(src, staging / name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true", help="dry-run hit-list, no writes")
    g.add_argument("--build", action="store_true", help="produce sanitized bundle + zip")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output dir (default: ~/Downloads, else ~/.claude/downloads)",
    )
    args = ap.parse_args()

    if args.scan:
        return cmd_scan()
    out_dir = args.out or default_out_dir()
    return cmd_build(out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
