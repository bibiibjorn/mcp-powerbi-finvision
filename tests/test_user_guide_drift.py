"""Drift guard for the guide (docs/GUIDE.md, Part I is the end-user guide) and the hand-written
prose of ``10_Show_User_Guide``.

``10_Show_User_Guide`` generates its tool/op inventory from the live registry
(see test_user_guide_generated.py), so that part cannot drift. Two things CAN:

* ``docs/GUIDE.md`` - written by hand. By 7.15.0 it still documented tools
  that were removed in 7.6 / 7.8, an argument that had been renamed, and carried
  ``version: 7.1.0`` in its frontmatter.
* ``_PREAMBLE`` / ``_WORKFLOWS`` in ``server/handlers/user_guide_handler.py`` -
  prose that names tools.

Contract enforced here (every failure message says what to edit):

(a) GUIDE.md frontmatter ``version`` (and the closing footer) equal
    ``src/__version__.py``.
(b) Every registered tool has its own ``### N.N `tool` ...`` section (Part I of GUIDE.md).
(c) Every operation of every tool appears, quoted or in backticks, INSIDE that
    tool's section. "Operation" = the enum values of the tool's selector
    argument(s): ``operation``, ``batch_operation``, ``mode`` (when the tool has
    no ``operation``), ``object_type`` (when it is the primary selector) and
    ``topic``; for tools with ``operations_by_type`` (02_Model_Operations) every
    ``(object_type, operation)`` pair must be listed in that object type's row of
    the "Operations by object_type" table. Genuinely internal ops may be listed in
    ``tests/fixtures/user_guide_op_allowlist.txt`` (kept tiny; stale entries fail here too).
(d) ``_PREAMBLE`` / ``_WORKFLOWS`` and (e) GUIDE.md name no tool that is not
    registered (catches removed / renamed tools such as ``12_Autonomous_Workflow``).
(f) Every top-level argument name used in a ``Tool { key: value, ... }`` example of
    GUIDE.md is a DECLARED argument of that tool. The server passes undeclared
    args through silently (``unknown_args``), so a stale example such as
    ``operation_kind`` would otherwise teach a call that does nothing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "GUIDE.md"
ALLOWLIST = ROOT / "tests" / "fixtures" / "user_guide_op_allowlist.txt"
GUIDE_REL = "docs/GUIDE.md"

# `NN_Name` / `SVG_Name` shaped tokens. `07_*` style wildcards do not match
# (a letter must follow the underscore).
_TOOL_TOKEN = re.compile(r"(?<![A-Za-z0-9_])((?:\d{2}|SVG)_[A-Za-z][A-Za-z0-9_]*)")
_SECTION_HEAD = re.compile(r"^### (\d+\.\d+) `([^`]+)`", re.MULTILINE)
_ANY_H1_H3 = re.compile(r"^#{1,3} ", re.MULTILINE)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def registry():
    from server.handlers import register_all_handlers
    from server.registry import get_registry

    reg = get_registry()
    if reg.total_tools() == 0:
        register_all_handlers(reg)
    return reg


@pytest.fixture(scope="module")
def guide_text() -> str:
    assert GUIDE.exists(), f"{GUIDE_REL} is missing"
    return GUIDE.read_text(encoding="utf-8")


def _server_version() -> str:
    sys.path.insert(0, str(ROOT))
    from src.__version__ import __version__

    return __version__


def _frontmatter(text: str) -> Dict[str, str]:
    m = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    assert m, f"{GUIDE_REL} has no YAML frontmatter block at the top"
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _mask_fences(text: str) -> str:
    """Same-length copy of ``text`` with every fenced-code line blanked to spaces."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(" " * len(line))
        else:
            out.append(" " * len(line) if in_fence else line)
    return "\n".join(out)


def _sections(text: str) -> Dict[str, str]:
    """tool name -> text of its ``### N.N `tool` ...`` section (through its #### children)."""
    masked = _mask_fences(text)  # a `# comment` line inside a code block is not a heading
    heads = list(_SECTION_HEAD.finditer(masked))
    stops = [m.start() for m in _ANY_H1_H3.finditer(masked)]
    out: Dict[str, str] = {}
    for h in heads:
        end = next((s for s in stops if s > h.start()), len(text))
        out[h.group(2)] = text[h.start() : end]
    return out


def _selector_values(tool) -> List[str]:
    """Operation-like enum values of a tool (see module docstring, rule c)."""
    props = (tool.input_schema or {}).get("properties") or {}

    def enum(key: str) -> List[str]:
        e = (props.get(key) or {}).get("enum")
        return [str(v) for v in e] if e else []

    vals = enum("operation") + enum("batch_operation") + enum("topic")
    if "operation" not in props:
        vals += enum("mode")
    if "operation" not in props or tool.operations_by_type or "batch_operation" in props:
        vals += enum("object_type")
    return list(dict.fromkeys(vals))


def _quoted(token: str, text: str) -> bool:
    """token appears as `token`, "token" or 'token'."""
    return re.search(rf"([`\"']){re.escape(token)}\1", text) is not None


def _load_allowlist() -> Set[Tuple[str, str]]:
    """``tool op  # why`` per line -> {(tool, op)}. ``op`` may be ``object_type/op``."""
    entries: Set[Tuple[str, str]] = set()
    if not ALLOWLIST.exists():
        return entries
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        assert len(parts) == 2, (
            f"{ALLOWLIST.name}: bad line {raw!r} - expected '<tool> <op>  # reason' "
            "(use <object_type>/<op> for 02_Model_Operations)"
        )
        assert "#" in raw, f"{ALLOWLIST.name}: {raw!r} needs a '# reason' justifying the entry"
        entries.add((parts[0], parts[1]))
    return entries


def _all_expected(registry) -> Dict[str, Set[str]]:
    """tool -> every op token the guide must document (object_type/op for by-type tools)."""
    out: Dict[str, Set[str]] = {}
    for t in registry.get_all_tools():
        ops = set(_selector_values(t))
        for kind, kind_ops in (t.operations_by_type or {}).items():
            ops.update(f"{kind}/{o}" for o in kind_ops)
        out[t.name] = ops
    return out


def _is_documented(tool, op: str, section: str) -> bool:
    if "/" in op:  # object_type/op -> the object type's row of the by-type table
        kind, name = op.split("/", 1)
        row = next(
            (ln for ln in section.splitlines() if re.match(rf"\|\s*`{re.escape(kind)}`\s*\|", ln)),
            "",
        )
        return _quoted(name, row)
    return _quoted(op, section)


# ---------------------------------------------------------------------------
# (a) version
# ---------------------------------------------------------------------------
def test_user_guide_frontmatter_version_matches_server_version(guide_text):
    fm = _frontmatter(guide_text)
    want = _server_version()
    assert fm.get("version") == want, (
        f"{GUIDE_REL} frontmatter says version {fm.get('version')!r} but src/__version__.py is "
        f"{want!r}. Bump `version:` in the frontmatter (and the footer line) when you refresh the "
        "guide for a release."
    )


def test_user_guide_footer_version_matches_server_version(guide_text):
    want = _server_version()
    m = re.search(r"User Guide — version ([0-9][0-9.]*[0-9])", guide_text)
    assert m, f"{GUIDE_REL} has no closing '... User Guide — version X.Y.Z ...' footer line"
    assert m.group(1) == want, (
        f"{GUIDE_REL} footer says version {m.group(1)} but src/__version__.py is {want}. "
        "Update the last line of the guide."
    )


def test_user_guide_states_the_registered_tool_count(guide_text, registry):
    n = registry.total_tools()
    assert re.search(
        rf"\b{n} tools\b", guide_text
    ), f"{GUIDE_REL} never says '{n} tools' - the server registers {n}. Fix the count in §1/§4."


# ---------------------------------------------------------------------------
# (b) every tool has a section
# ---------------------------------------------------------------------------
def test_every_registered_tool_has_a_section(guide_text, registry):
    secs = _sections(guide_text)
    missing = sorted(t.name for t in registry.get_all_tools() if t.name not in secs)
    assert not missing, (
        f"tool(s) {missing} are registered but have no '### 5.N `<tool>` — ...' section in "
        f"{GUIDE_REL}. Add a section under '## 5. The tools' (Part I) (the heading format matters: "
        "the drift guard finds a tool's section by it)."
    )


def test_every_registered_tool_name_appears_in_the_guide(guide_text, registry):
    missing = sorted(t.name for t in registry.get_all_tools() if t.name not in guide_text)
    assert not missing, f"tool(s) {missing} are registered but never named in {GUIDE_REL}"


# ---------------------------------------------------------------------------
# (c) every operation is documented inside its tool's section
# ---------------------------------------------------------------------------
def test_every_operation_is_documented_in_its_tool_section(guide_text, registry):
    secs = _sections(guide_text)
    allow = _load_allowlist()
    expected = _all_expected(registry)
    failures: List[str] = []
    for t in registry.get_all_tools():
        section = secs.get(t.name)
        if section is None:
            continue  # reported by test_every_registered_tool_has_a_section
        num = _section_no(guide_text, t.name)
        missing_flat: List[str] = []
        missing_by_kind: Dict[str, List[str]] = {}
        for op in sorted(expected[t.name]):
            if (t.name, op) in allow or _is_documented(t, op, section):
                continue
            if "/" in op:
                kind, name = op.split("/", 1)
                missing_by_kind.setdefault(kind, []).append(name)
            else:
                missing_flat.append(op)
        if missing_flat:
            failures.append(
                f"{t.name}: op(s) {', '.join(missing_flat)} missing from {GUIDE_REL} section {num} - "
                'mention each as `op` (backticks) or "op" inside that section'
            )
        for kind, names in missing_by_kind.items():
            failures.append(
                f"{t.name}: op(s) {', '.join(names)} of object_type '{kind}' missing from "
                f"{GUIDE_REL} section {num} - list them in the `{kind}` row of its "
                "'Operations by object_type' table"
            )
    nl = "\n"
    assert not failures, (
        nl
        + nl.join(failures)
        + f"{nl}(document the op; only a genuinely internal one may go into {ALLOWLIST.name} "
        "with a reason)"
    )


def _section_no(text: str, tool: str) -> str:
    for m in _SECTION_HEAD.finditer(text):
        if m.group(2) == tool:
            return m.group(1)
    return "?"


def test_allowlist_has_no_stale_entries(guide_text, registry):
    secs = _sections(guide_text)
    expected = _all_expected(registry)
    stale = []
    for tool, op in sorted(_load_allowlist()):
        if tool not in expected or op not in expected[tool]:
            stale.append(f"{tool} {op}: no such tool/op in the registry - delete the line")
        elif tool in secs and _is_documented(registry.get_tool_def(tool), op, secs[tool]):
            stale.append(f"{tool} {op}: already documented in the guide - delete the line")
    assert not stale, f"{ALLOWLIST.name} has stale entries:\n" + "\n".join(stale)
    assert (
        len(_load_allowlist()) <= 5
    ), f"{ALLOWLIST.name} must stay tiny (<= 5 entries) - document the ops instead"


# ---------------------------------------------------------------------------
# (d) handler prose and (e) the guide name only registered tools
# ---------------------------------------------------------------------------
def _phantoms(text: str, registered: Set[str]) -> List[str]:
    return sorted({m.group(1) for m in _TOOL_TOKEN.finditer(text)} - registered)


def test_handler_prose_names_only_registered_tools(registry):
    from server.handlers import user_guide_handler as h

    registered = {t.name for t in registry.get_all_tools()}
    for label, prose in (("_PREAMBLE", h._PREAMBLE), ("_WORKFLOWS", h._WORKFLOWS)):
        bad = _phantoms(prose, registered)
        assert not bad, (
            f"server/handlers/user_guide_handler.py {label} mentions unregistered tool(s) {bad}. "
            "Fix the prose (removed/renamed tool?)."
        )


def _part_one(text: str) -> str:
    """Part I (the end-user guide) of GUIDE.md, up to the Part II anchor. The later Parts are
    developer reference: they name removed tools as history, use ``02_TMDL``-style shorthand and
    carry lower-case anchor slugs, none of which is a stale instruction."""
    start = text.index('<a id="part-i"></a>')
    return text[start : text.index('<a id="part-ii"></a>', start)]


def test_user_guide_names_only_registered_tools(guide_text, registry):
    registered = {t.name for t in registry.get_all_tools()}
    bad = _phantoms(_part_one(guide_text), registered)
    assert not bad, (
        f"{GUIDE_REL} (Part I) mentions unregistered tool(s) {bad}. They were removed or renamed - "
        "rewrite those passages for the current tools (see AGENTS.md §4 for the tool map)."
    )


# ---------------------------------------------------------------------------
# (f) example calls use declared argument names
# ---------------------------------------------------------------------------
_CALL_START = re.compile(r"(?m)^[ \t]*((?:\d{2}|SVG)_[A-Za-z][A-Za-z0-9_]*)[ \t]*\{")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STR_LITERAL = re.compile(r'"([^"\\\n]*)"')
_FENCE = re.compile(r"(?ms)^```[a-z]*\n(.*?)^```")


def _top_level_pairs(text: str, start: int) -> List[Tuple[str, Optional[str]]]:
    """``(key, string-literal value or None)`` of the object literal whose ``{`` is at
    ``text[start]`` (depth 1 only).

    Understands nested ``{[(``, single/double-quoted strings with backslash escapes
    and stops at the matching ``}``. Placeholders such as ``...`` are ignored.
    """
    keys: List[Tuple[str, Optional[str]]] = []
    depth = 0
    i = start
    quote = ""
    expect_key = False
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "{[(":
            depth += 1
            expect_key = depth == 1 and ch == "{"
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                break
        elif depth == 1 and ch == ",":
            expect_key = True
        elif depth == 1 and expect_key and (ch.isalpha() or ch == "_"):
            m = _KEY.match(text, i)
            j = m.end()
            while j < len(text) and text[j] in " \t":
                j += 1
            if j < len(text) and text[j] == ":":
                v = j + 1
                while v < len(text) and text[v] in " \t":
                    v += 1
                lit = _STR_LITERAL.match(text, v)
                keys.append((m.group(0), lit.group(1) if lit else None))
            expect_key = False
            i = j - 1
        elif depth == 1 and expect_key and not ch.isspace():
            expect_key = False
        i += 1
    return keys


def _example_calls(text: str) -> List[Tuple[str, List[Tuple[str, Optional[str]]], int]]:
    """(tool, top-level (arg, literal) pairs, 1-based line) of every example call in a fence."""
    calls = []
    for fence in _FENCE.finditer(text):
        body, base = fence.group(1), fence.start(1)
        for m in _CALL_START.finditer(body):
            keys = _top_level_pairs(body, m.end() - 1)
            line = text.count("\n", 0, base + m.start()) + 1
            calls.append((m.group(1), keys, line))
    return calls


def test_example_arguments_are_declared_by_their_tool(guide_text, registry):
    registered = {t.name for t in registry.get_all_tools()}
    calls = _example_calls(guide_text)
    assert len(calls) > 100, "example parser found suspiciously few calls - parser broken?"
    failures = []
    for tool, keys, line in calls:
        if tool not in registered:
            continue  # reported by test_user_guide_names_only_registered_tools
        declared = registry.declared_properties(tool)
        bad = [k for k, _ in keys if k not in declared]
        if bad:
            failures.append(
                f"{GUIDE_REL}:{line} {tool} example uses undeclared argument(s) {bad} - use the "
                f"canonical name (10_Show_User_Guide tool={tool} lists them)"
            )
    nl = "\n"
    assert not failures, nl + nl.join(failures)


def test_example_parser_understands_nesting_and_strings():
    sample = (
        '```\n01_Connection { operation: "connect", opts: { a: 1, b: [1, {c: 2}] },\n'
        '                 note: "x, y: {z}", model_index: 0 }\n```\n'
    )
    tool, pairs, _ = _example_calls(sample)[0]
    assert tool == "01_Connection"
    assert pairs == [
        ("operation", "connect"),
        ("opts", None),
        ("note", "x, y: {z}"),
        ("model_index", None),
    ]


_SELECTORS = ("operation", "object_type", "batch_operation", "mode", "sub_operation", "topic")


def test_example_selector_values_are_valid(guide_text, registry):
    """`operation: "x"` / `object_type: "x"` / `mode: "x"` ... in an example must be a real value:
    an enum member, or (02_Model_Operations) a real operation of the given object type."""
    failures = []
    for tool, pairs, line in _example_calls(guide_text):
        if not registry.has_tool(tool):
            continue
        td = registry.get_tool_def(tool)
        props = (td.input_schema or {}).get("properties") or {}
        lits = {k: v for k, v in pairs if v is not None}
        for key in _SELECTORS:
            val = lits.get(key)
            enum = (props.get(key) or {}).get("enum")
            if val is not None and enum and val not in enum:
                failures.append(
                    f"{GUIDE_REL}:{line} {tool} example has {key}: {val!r} - valid values: {enum}"
                )
        if td.operations_by_type and "object_type" in lits and "operation" in lits:
            kind, op = lits["object_type"], lits["operation"]
            valid = td.operations_by_type.get(kind)
            if valid is not None and op not in valid and op != "upsert":
                failures.append(
                    f"{GUIDE_REL}:{line} {tool} example: {op!r} is not an operation of "
                    f"object_type {kind!r} - valid: {valid}"
                )
    nl = "\n"
    assert not failures, nl + nl.join(failures)
