"""Unit tests for the skills-bundle sanitizer (pure text logic, no filesystem)."""

import sys
from pathlib import Path

import pytest

# The builder lives outside the package tree; add it to sys.path.
_TOOLS = Path(__file__).resolve().parent.parent / "tools" / "skills-bundle"
sys.path.insert(0, str(_TOOLS))

from sanitizer import (  # noqa: E402
    RESIDUAL_PATTERNS,
    sanitize_text,
    scan_text,
)


# --------------------------------------------------------------------------- #
# STRIP rules — whole line dropped
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "line",
    [
        "See [PBI DOC LIBRARY] map for the canon docs.",
        "Read C:/Users/x/OneDrive - Finvision/.../M01 - MD Files for Claude/foo.md",
        "Canonical reference (Microsoft): skills/semantic-model-authoring/...",
        "This applies to the G01 Wealth Reporting repo.",
        "Posts to https://finvision.webhook.office.com/abc on commit.",
        "origin https://dev.azure.com/finticx/Finticx_SAASPlatform/_git/X",
    ],
)
def test_strip_lines_are_removed(line):
    body = f"keep me\n{line}\nkeep me too\n"
    clean, changes = sanitize_text(body)
    assert line not in clean
    assert "keep me" in clean and "keep me too" in clean
    assert any(c.replacement is None for c in changes)


def test_strip_only_drops_the_offending_line():
    body = "alpha\nbeta uses M01 - MD Files for Claude here\ngamma\n"
    clean, _ = sanitize_text(body)
    assert clean.splitlines() == ["alpha", "gamma"]


# --------------------------------------------------------------------------- #
# REPLACE rules — inline rewrite
# --------------------------------------------------------------------------- #
def test_windows_user_path_replaced():
    clean, changes = sanitize_text(r"run C:\Users\bjorn.braet\bin\tool.py now")
    assert "bjorn.braet" not in clean
    assert "~" in clean
    assert changes and changes[0].replacement is not None


def test_wsl_user_path_replaced():
    clean, _ = sanitize_text("path /mnt/c/Users/bjorn.braet/repo")
    assert "bjorn.braet" not in clean
    assert "~/repo" in clean


def test_emails_replaced():
    for addr in ("bjorn.braet@finvision.com", "bibiibjorn@gmail.com", "someone@finvision.be"):
        clean, _ = sanitize_text(f"contact {addr} for access")
        assert addr not in clean
        assert "<your-email>" in clean


def test_fabriciq_repoint_phrase_removed():
    line = "FabricIQ; repointed to powerbi-finvision for live local model. Use it."
    clean, _ = sanitize_text(line)
    assert "repointed to powerbi-finvision" not in clean
    assert "FabricIQ" in clean  # the agent itself stays


def test_agent_persona_opener_keeps_identity():
    line = (
        "You are **FabricAdmin**, adapted from Microsoft's official "
        "`skills-for-fabric` agent into Bjorn's environment. Preserve the persona."
    )
    clean, _ = sanitize_text(line)
    assert "You are **FabricAdmin**" in clean
    assert "skills-for-fabric" not in clean
    assert "Bjorn" not in clean


def test_project_codes_genericized():
    clean, _ = sanitize_text("Active target is R0105; R0102 is legacy.")
    assert "R0105" not in clean and "R0102" not in clean
    assert "<project>" in clean


def test_doc_reference_blockquote_dropped_whole():
    body = (
        "# DAX\n"
        "\n"
        "> **Canonical reference (Microsoft official):** for DAX rules,\n"
        "> Microsoft's `skills-for-fabric` is canonical. Read on demand:\n"
        "> - DAX rules → `skills/semantic-model-authoring/references/x.md`\n"
        "> - Perf guide → `…/dax-perf-decision-guide.md`\n"
        "\n"
        "Write correct, readable DAX.\n"
    )
    clean, changes = sanitize_text(body)
    assert "skills-for-fabric" not in clean
    assert "semantic-model-authoring" not in clean
    assert "Canonical reference" not in clean
    # Surrounding real content survives.
    assert "# DAX" in clean
    assert "Write correct, readable DAX." in clean
    assert any(c.kind == "doc-reference-block" for c in changes)


def test_microsoft_reference_block_dropped():
    body = (
        "intro\n"
        "> **Microsoft reference (knowledge, NOT toolchain):** MS `skills-for-fabric`\n"
        "> carries the deepest PBIR internals. See `skills/powerbi-report-authoring/`.\n"
        "outro\n"
    )
    clean, _ = sanitize_text(body)
    assert "skills-for-fabric" not in clean
    assert clean.splitlines() == ["intro", "outro"]


def test_normal_blockquote_not_dropped():
    body = "> Note: prefer SUMX here for clarity.\nbody text\n"
    clean, changes = sanitize_text(body)
    assert "prefer SUMX" in clean
    assert not any(c.kind == "doc-reference-block" for c in changes)


def test_benign_text_untouched():
    body = "Use SUMX over a filtered table; avoid CALCULATE in row context.\n"
    clean, changes = sanitize_text(body)
    assert clean == body
    assert changes == []


# --------------------------------------------------------------------------- #
# scan_text mirrors sanitize_text decisions without applying them
# --------------------------------------------------------------------------- #
def test_scan_matches_sanitize_change_count():
    body = (
        "C:\\Users\\bjorn.braet\\x\n"
        "[PBI DOC LIBRARY] here\n"
        "harmless line\n"
        "email bjorn.braet@finvision.com\n"
    )
    scan = scan_text(body)
    _, applied = sanitize_text(body)
    assert len(scan) == len(applied) == 3


def test_trailing_newline_preserved():
    assert sanitize_text("a\nb\n")[0].endswith("\n")
    assert not sanitize_text("a\nb")[0].endswith("\n")


# --------------------------------------------------------------------------- #
# Validation gate — residual patterns catch leftovers
# --------------------------------------------------------------------------- #
def test_sanitized_output_passes_residual_gate():
    body = (
        "C:\\Users\\bjorn.braet\\tool — see [PBI DOC LIBRARY]\n"
        "G01 work, ping bjorn.braet@finvision.com\n"
        "uses M01 - MD Files for Claude\n"
        "https://x.webhook.office.com/y\n"
        "dev.azure.com/finticx/repo\n"
    )
    clean, _ = sanitize_text(body)
    for name, pat in RESIDUAL_PATTERNS.items():
        assert not pat.search(clean), f"residual {name} survived: {clean!r}"


def test_residual_gate_fires_on_planted_token():
    # A token the sanitizer would normally strip, planted on a line it can't see
    # as a pointer, still gets caught by the residual gate.
    assert RESIDUAL_PATTERNS["user-path"].search("inline bjorn.braet token")
