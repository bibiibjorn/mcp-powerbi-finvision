"""PBIR vs legacy report-format detection.

Power BI is retiring the legacy monolithic ``report.json`` format:

- Jan 2026  — all NEW reports default to PBIR (Service + Desktop).
- June 2026 — legacy is deprecated; PBIR is the only format Microsoft supports.
- ~Q3 2026  — PBIR GA: remaining legacy reports **auto-convert on next Desktop
  save**, which rewrites the whole report folder in one go.

That auto-conversion is the hazard this module exists for. A caller editing a
legacy report is editing a shape that is about to be rewritten wholesale — the
resulting git diff swamps their change, and any assumption they made about the
on-disk layout stops holding. Every PBIP-facing op should be able to say
"this report is still legacy" cheaply, so the warning reaches the caller BEFORE
the edit rather than after the diff.

Pure filesystem inspection: no CLI, no Desktop, no parsing of report bodies.
Never raises — an undeterminable path returns ``UNKNOWN`` rather than blowing up
a caller who only wanted an advisory.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Format verdicts.
FORMAT_PBIR = "PBIR"
FORMAT_LEGACY = "LEGACY"
FORMAT_UNKNOWN = "UNKNOWN"

#: Deprecation milestones, surfaced in the advisory so the caller can judge urgency.
LEGACY_DEPRECATED_SINCE = "2026-06"
PBIR_DEFAULT_SINCE = "2026-01"

_LEGACY_WARNING = (
    "This report is still in the LEGACY (monolithic report.json) format, "
    f"deprecated since {LEGACY_DEPRECATED_SINCE}. Power BI Desktop will "
    "AUTO-CONVERT it to PBIR on the next save, rewriting the whole report "
    "folder — that mass rewrite will bury any change you make now. Convert "
    'deliberately first (commit, then `pbir report convert "<Report>.Report" '
    "-F pbir`), verify, and edit the PBIR output."
)


def _report_dir_from_pbip(pbip: Path) -> Optional[Path]:
    """Resolve the report folder a ``.pbip`` points at.

    Reads ``artifacts[].report.path`` (relative to the .pbip's own folder). Falls
    back to the file stem (``Foo.pbip`` -> ``Foo.Report``), which is the Desktop
    default layout. Returns None when neither resolves to a real directory.
    """
    try:
        import json

        base = pbip.parent
        if pbip.suffix.lower() == ".pbip":
            try:
                doc = json.loads(pbip.read_text(encoding="utf-8"))
                for art in doc.get("artifacts") or []:
                    rel = ((art or {}).get("report") or {}).get("path")
                    if rel:
                        cand = (base / rel).resolve()
                        if cand.is_dir():
                            return cand
            except (OSError, ValueError, AttributeError) as e:
                logger.debug("report_format: unreadable .pbip %s (%s)", pbip, e)
            stem_guess = base / f"{pbip.stem}.Report"
            if stem_guess.is_dir():
                return stem_guess
        return None
    except (OSError, PermissionError):  # pragma: no cover - fs edge
        return None


def _report_dir(path: Path) -> Optional[Path]:
    """Resolve *path* to the report folder that would hold ``definition/``.

    Accepts the ``.Report`` folder itself, a ``.pbip`` file beside it, or a
    project root containing exactly one ``*.Report`` folder.
    """
    try:
        if path.is_file():
            # A .pbip names its own report in artifacts[].report.path — read it
            # rather than guessing. A repo folder commonly holds SEVERAL
            # *.Report siblings (R0200/R0201/R0202/R0203 in G02), so the
            # "exactly one child" heuristic below cannot resolve a .pbip there.
            resolved = _report_dir_from_pbip(path)
            if resolved is not None:
                return resolved
            path = path.parent
        if path.name.lower().endswith(".report"):
            return path
        # Project root: exactly one *.Report child is unambiguous.
        candidates = [
            p for p in path.iterdir() if p.is_dir() and p.name.lower().endswith(".report")
        ]
        if len(candidates) == 1:
            return candidates[0]
        return None
    except (OSError, PermissionError) as e:  # pragma: no cover - fs edge
        logger.debug("report_format: cannot resolve report dir for %s (%s)", path, e)
        return None


def detect_report_format(report_path: str) -> Dict[str, Any]:
    """Classify a report as PBIR, LEGACY, or UNKNOWN.

    PBIR reports carry a ``definition/`` folder (with ``pages/``); legacy
    reports carry a single monolithic ``report.json`` and no ``definition/``.
    A report with BOTH is mid-conversion — reported as PBIR with
    ``legacy_remnant=True`` so a stale ``report.json`` doesn't read as legacy.

    Returns ``{format, report_dir?, reason, is_legacy, ...}``. Never raises.
    """
    result: Dict[str, Any] = {"format": FORMAT_UNKNOWN, "is_legacy": False}
    try:
        p = Path(report_path)
        if not p.exists():
            result["reason"] = f"path does not exist: {report_path}"
            return result

        rdir = _report_dir(p)
        if rdir is None:
            result["reason"] = (
                "could not resolve a single *.Report folder from the given path "
                "(pass the .Report folder, its .pbip, or a root containing exactly one)"
            )
            return result

        result["report_dir"] = str(rdir)
        has_definition = (rdir / "definition").is_dir()
        has_report_json = (rdir / "report.json").is_file()

        if has_definition:
            result["format"] = FORMAT_PBIR
            result["reason"] = "definition/ folder present"
            if has_report_json:
                # Conversion leftover: PBIR wins, but flag it — a stale
                # report.json is a real source of "which file is live?" bugs.
                result["legacy_remnant"] = True
                result["reason"] += " (stale report.json also present)"
            return result

        if has_report_json:
            result["format"] = FORMAT_LEGACY
            result["is_legacy"] = True
            result["reason"] = "monolithic report.json with no definition/ folder"
            result["warning"] = _LEGACY_WARNING
            result["deprecated_since"] = LEGACY_DEPRECATED_SINCE
            result["convert_with"] = f'pbir report convert "{rdir.name}" -F pbir'
            return result

        result["reason"] = "neither definition/ nor report.json found"
        return result
    except Exception as e:  # noqa: BLE001 - advisory helper must never raise
        logger.debug("detect_report_format failed for %s: %s", report_path, e)
        result["reason"] = f"detection failed: {e}"
        return result


def attach_format_warning(result: Dict[str, Any], report_path: Optional[str]) -> Dict[str, Any]:
    """Attach a legacy-format advisory to *result* when the report is legacy.

    Additive and non-destructive: only fires on a confirmed LEGACY verdict, only
    sets keys that are absent, and leaves PBIR/UNKNOWN results untouched so the
    common path pays nothing. Safe to call on error envelopes.
    """
    if not isinstance(result, dict) or not report_path:
        return result
    try:
        verdict = detect_report_format(report_path)
        if verdict.get("is_legacy"):
            result.setdefault("report_format", FORMAT_LEGACY)
            result.setdefault("format_warning", verdict.get("warning"))
            result.setdefault("convert_with", verdict.get("convert_with"))
        elif verdict.get("legacy_remnant"):
            result.setdefault("report_format", FORMAT_PBIR)
            result.setdefault(
                "format_warning",
                "A stale report.json sits beside definition/ — PBIR is live; "
                "the leftover legacy file should be removed to avoid confusion.",
            )
    except Exception as e:  # noqa: BLE001 - never break a handler over an advisory
        logger.debug("attach_format_warning skipped: %s", e)
    return result


__all__ = [
    "FORMAT_PBIR",
    "FORMAT_LEGACY",
    "FORMAT_UNKNOWN",
    "LEGACY_DEPRECATED_SINCE",
    "PBIR_DEFAULT_SINCE",
    "detect_report_format",
    "attach_format_warning",
]
