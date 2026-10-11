"""Review C-10 / K-06 / A-29: every key in ``config/default_config.json`` has a reader, and every
dotted ``config.get("section.key")`` literal in the server code is declared.

Reader detection is AST based (string constants in ``core/ server/ src/ client_gateway/``):

* the dotted path, or a dotted prefix of it, is a string literal (``config.get("query.max_page_size")``
  or ``config.get("tool_timeouts")`` which reads the whole sub-tree), or
* the section is read through an f-string template (``f"model_write.{key}"``, ``f"server.{x}_workers"``,
  ``f"features.{feature}"``) and the leaf name (or the template's suffix match) appears as a literal.

A key nobody reads is a trap (a user edits it and nothing changes) -- delete it, or wire it.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config" / "default_config.json"
SOURCE_DIRS = ("core", "server", "src", "client_gateway")

# Keys read by an external consumer (not by the MCP server process), listed on purpose.
EXTERNAL_READERS: set[str] = set()

# Sections whose keys are read by the client gateway (``client_gateway/``) with their own call-site
# defaults and documented in the gateway docs; they may stay out of the shipped file.
GATEWAY_PREFIXES = ("client_gateway.", "client_guard.")
# Dotted literals that merely look like a config path.
NOT_CONFIG_PATHS = {
    "bpa.json",
    # SSE event types of the Copilot /responses stream (client_gateway/adapters/openai_responses.py)
    "response.created",
    "response.in_progress",
    "response.output_text.delta",
    "response.refusal.delta",
    "response.reasoning_summary_text.delta",
    "response.reasoning_text.delta",
    "response.function_call_arguments.delta",
    "response.custom_tool_call_input.delta",
}
# Read with a call-site default and deliberately not part of the shipped file.
OPTIONAL_UNDECLARED: set[str] = {
    # limits_manager's informational default; A-29 pruned it from the file on purpose
    # (tests/test_review_k_protocol_ops.py::test_dead_security_and_server_config_keys_pruned).
    "server.timeout_seconds",
}


def _leaves(d: dict, pre: str = ""):
    for k, v in d.items():
        p = pre + k
        if isinstance(v, dict) and v:
            yield from _leaves(v, p + ".")
        else:
            yield p


def _scan_sources():
    consts: set[str] = set()
    templates: set[str] = set()
    for sub in SOURCE_DIRS:
        for path in (REPO / sub).rglob("*.py"):
            if "__pycache__" in path.parts or "bin" in path.parts or "obj" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    consts.add(node.value)
                elif isinstance(node, ast.JoinedStr):
                    templates.add(
                        "".join(
                            v.value if isinstance(v, ast.Constant) else "{}" for v in node.values
                        )
                    )
    return consts, templates


def _is_read(leaf: str, consts: set[str], templates: set[str]) -> bool:
    parts = leaf.split(".")
    for i in range(len(parts), 1, -1):  # dotted prefixes of length >= 2
        if ".".join(parts[:i]) in consts:
            return True
    if "tool_timeouts" in consts and parts[0] == "tool_timeouts":
        return True
    section, name = parts[0], parts[-1]
    for tpl in templates:
        if not tpl.startswith(section + ".") or "{}" not in tpl:
            continue
        suffix = tpl[len(section) + 1 :]
        # f"server.{x}_workers": the template itself matches the leaf.
        rx = "^" + ".*".join(re.escape(p) for p in suffix.split("{}")) + "$"
        if suffix != "{}" and re.match(rx, ".".join(parts[1:])):
            return True
        # f"model_write.{key}" / f"features.{feature}": the leaf name is passed as a literal.
        if suffix == "{}" and name in consts and len(parts) == 2:
            return True
    return False


def test_every_config_key_has_a_reader():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    consts, templates = _scan_sources()
    dead = [
        leaf
        for leaf in _leaves(cfg)
        if leaf not in EXTERNAL_READERS and not _is_read(leaf, consts, templates)
    ]
    assert not dead, (
        "config/default_config.json keys that nothing reads (delete them, wire them, or list "
        f"them in EXTERNAL_READERS): {dead}"
    )


def test_every_literal_config_read_is_declared():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    declared = set(_leaves(cfg))
    sections = set(cfg)
    consts, _ = _scan_sources()
    missing = []
    for lit in sorted(consts):
        m = re.fullmatch(r"([a-z_]+)\.([a-z_0-9]+(?:\.[a-z_0-9]+)*)", lit)
        if not m or m.group(1) not in sections or lit in NOT_CONFIG_PATHS:
            continue
        if lit.startswith(GATEWAY_PREFIXES):
            continue
        if lit in declared or any(d.startswith(lit + ".") for d in declared):
            continue
        if lit in OPTIONAL_UNDECLARED:
            continue
        missing.append(lit)
    assert (
        not missing
    ), f"dotted config paths read in code but absent from default_config.json: {missing}"


def test_gateway_port_is_declared():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert cfg["client_gateway"]["port"] == 8890
    assert "client_gateway.port" in set(_leaves(cfg))


def test_fallback_config_only_has_keys_that_are_read():
    from core.config.config_manager import ConfigManager

    consts, templates = _scan_sources()
    fallback = ConfigManager._get_fallback_config(None)  # type: ignore[arg-type]
    dead = [leaf for leaf in _leaves(fallback) if not _is_read(leaf, consts, templates)]
    assert not dead, f"fallback config keys nobody reads: {dead}"
