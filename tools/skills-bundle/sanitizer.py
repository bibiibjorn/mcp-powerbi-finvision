"""Pure text-sanitization logic for the skills export bundle.

No filesystem access here — `sanitize_text` / `scan_text` take a string and return
the cleaned string + a list of changes. This keeps the rules unit-testable.

Strategy: line-oriented. Each line is tested against:
  1. STRIP rules — if the line is fundamentally a private pointer (doc-library link,
     OneDrive path, G01 reference, Teams webhook), the whole line is dropped.
  2. REPLACE rules — inline tokens (user paths, emails, the FabricIQ repoint phrase)
     are rewritten in place.
STRIP wins over REPLACE (a doc-library line is removed wholesale, not patched).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
_ONEDRIVE = r"M01 - MD Files for Claude"
_USER = r"bjorn\.braet"

# Lines that are private pointers → drop the whole line.
STRIP_PATTERNS: dict[str, re.Pattern] = {
    "doc-library-pointer": re.compile(
        r"\[PBI DOC LIBRARY\]|" + _ONEDRIVE + r"|OneDrive - Finvision",
        re.IGNORECASE,
    ),
    "ms-canonical-pointer": re.compile(
        r"Canonical reference \(Microsoft\)",
        re.IGNORECASE,
    ),
    "g01-pointer": re.compile(
        r"\bG01\b|Wealth Reporting|FamillyOffices|FamilyOffices",
    ),
    "teams-webhook": re.compile(
        r"https?://[^\s]*webhook\.office\.com[^\s]*|" r"https?://[^\s]*\.logic\.azure\.com[^\s]*",
        re.IGNORECASE,
    ),
    "devops-org": re.compile(
        r"dev\.azure\.com/finticx|dev\.azure\.com/Finticx",
    ),
}

# Inline replacements applied to lines that survive STRIP.
REPLACE_RULES: list[tuple[str, re.Pattern, str]] = [
    # Agent persona opener: ", adapted from Microsoft's official `skills-for-fabric`
    # agent into Bjorn's environment." → keep the identity, drop the provenance.
    (
        "agent-provenance",
        re.compile(
            r",?\s*adapted from Microsoft'?s?(?:\s+official)?\s+"
            r"`?skills-for-fabric`?\s+agent\s+into\s+\w+'?s?\s+environment\.?",
            re.IGNORECASE,
        ),
        ".",
    ),
    (
        "ms-fabric-provenance",
        re.compile(r"\badapted from[^.\n]*skills-for-fabric[^.\n]*", re.IGNORECASE),
        "a Fabric specialist",
    ),
    # Windows user path → neutral token.
    ("user-path-win", re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+" + _USER, re.IGNORECASE), "~"),
    # WSL user path.
    ("user-path-wsl", re.compile(r"/mnt/c/Users/" + _USER, re.IGNORECASE), "~"),
    # Personal / company emails.
    (
        "email",
        re.compile(r"\b[\w.+-]*(?:bjorn\.braet|bibiibjorn)[\w.+-]*@[\w.-]+\.\w+", re.IGNORECASE),
        "<your-email>",
    ),
    (
        "email-finvision",
        re.compile(r"\b[\w.+-]+@finvision\.[\w.-]+", re.IGNORECASE),
        "<your-email>",
    ),
    # FabricIQ repoint phrasing.
    (
        "fabriciq-repoint",
        re.compile(r";?\s*repointed to powerbi-finvision[^.\n]*", re.IGNORECASE),
        "",
    ),
    # R01/R0102/R0105 project codes referenced inline → generic.
    ("project-code", re.compile(r"\bR010[0-9]\b|\bR01\b|\bR0102\b|\bR0105\b"), "<project>"),
    # Generic remaining bjorn.braet token (e.g. inside a URL or comment).
    ("user-token", re.compile(_USER, re.IGNORECASE), "<user>"),
]


@dataclass
class Change:
    line_no: int
    kind: str
    original: str
    replacement: str | None  # None == line stripped


def _strip_kind(line: str) -> str | None:
    for kind, pat in STRIP_PATTERNS.items():
        if pat.search(line):
            return kind
    return None


def _apply_replacements(line: str) -> tuple[str, list[str]]:
    kinds: list[str] = []
    out = line
    for kind, pat, repl in REPLACE_RULES:
        if pat.search(out):
            out = pat.sub(repl, out)
            kinds.append(kind)
    return out, kinds


# A Markdown blockquote that is a Microsoft/doc-library reference pointer. When any
# line in a contiguous "> ..." block matches this, the WHOLE block is dropped — the
# per-line STRIP rules can't see that the surrounding "> - foo" bullets belong to it.
_BLOCK_TRIGGER = re.compile(
    r"skills-for-fabric|"
    r"Canonical reference \(Microsoft|"
    r"Microsoft reference|"
    r"Microsoft \(official\)|" + _ONEDRIVE,
    re.IGNORECASE,
)


def _is_quote(line: str) -> bool:
    return line.lstrip().startswith(">")


def _doc_reference_block_lines(lines: list[str]) -> set[int]:
    """0-based indices of lines belonging to a doc-library reference blockquote."""
    drop: set[int] = set()
    n = len(lines)
    i = 0
    while i < n:
        if _is_quote(lines[i]) and _BLOCK_TRIGGER.search(lines[i]):
            # Expand to the full contiguous blockquote (back and forward).
            start = i
            while start - 1 >= 0 and _is_quote(lines[start - 1]):
                start -= 1
            end = i
            while end + 1 < n and _is_quote(lines[end + 1]):
                end += 1
            drop.update(range(start, end + 1))
            i = end + 1
        else:
            i += 1
    return drop


def scan_text(text: str) -> list[Change]:
    """Return the list of changes sanitize_text would make, without applying them."""
    lines = text.splitlines()
    block_drop = _doc_reference_block_lines(lines)
    changes: list[Change] = []
    for idx, line in enumerate(lines):
        i = idx + 1
        if idx in block_drop:
            changes.append(Change(i, "doc-reference-block", line, None))
            continue
        sk = _strip_kind(line)
        if sk:
            changes.append(Change(i, sk, line, None))
            continue
        new, kinds = _apply_replacements(line)
        if new != line:
            changes.append(Change(i, "+".join(kinds), line, new))
    return changes


def sanitize_text(text: str) -> tuple[str, list[Change]]:
    """Return (clean_text, changes). Stripped lines are removed entirely."""
    lines = text.splitlines()
    block_drop = _doc_reference_block_lines(lines)
    out_lines: list[str] = []
    changes: list[Change] = []
    for idx, line in enumerate(lines):
        i = idx + 1
        if idx in block_drop:
            changes.append(Change(i, "doc-reference-block", line, None))
            continue  # drop the whole reference blockquote
        sk = _strip_kind(line)
        if sk:
            changes.append(Change(i, sk, line, None))
            continue  # drop the line
        new, kinds = _apply_replacements(line)
        if new != line:
            changes.append(Change(i, "+".join(kinds), line, new))
        out_lines.append(new)
    clean = "\n".join(out_lines)
    if text.endswith("\n"):
        clean += "\n"
    return clean, changes


# --------------------------------------------------------------------------- #
# Validation gate — residual detection on the *built* tree.
# Kept deliberately strict; the build fails if any of these survive.
# --------------------------------------------------------------------------- #
RESIDUAL_PATTERNS: dict[str, re.Pattern] = {
    "user-path": re.compile(_USER, re.IGNORECASE),
    "onedrive": re.compile(_ONEDRIVE, re.IGNORECASE),
    "doc-library": re.compile(r"\[PBI DOC LIBRARY\]"),
    "g01": re.compile(r"\bG01\b|FamillyOffices"),
    "teams-webhook": re.compile(r"webhook\.office\.com", re.IGNORECASE),
    "devops-org": re.compile(r"dev\.azure\.com/finticx", re.IGNORECASE),
    "skills-for-fabric": re.compile(r"skills-for-fabric", re.IGNORECASE),
    "email": re.compile(r"(?:bjorn\.braet|bibiibjorn)[\w.+-]*@", re.IGNORECASE),
}
