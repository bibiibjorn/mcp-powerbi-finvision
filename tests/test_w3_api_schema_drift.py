"""A-15: schema <-> handler drift, as a test (the review's p05b probe, ported).

For every registered tool, walk the handler's reachable functions (a name-based
call graph over ``server/handlers``) and collect every argument key it reads:
``args.get("k")`` / ``args["k"]`` / ``"k" in args`` / ``args.pop("k")`` and the
coercion helpers. Each key must be declared in the tool's FULL schema
(emitted + deferred), be a legacy alias the dispatcher folds in
(``server/arg_aliases.py``), or be one of the protocol-level args.

Before: 25 tools read args no schema declared (``candidate``, ``confirm_bulk_sync``,
``from_ref``...), so an LLM could not discover them. The wave-3 re-run found the
last ones (``no_cache``, ``dry_run`` on 02_QA, ``delete_after``...); this test
keeps the count at zero.

It also checks that every ``operation`` enum value has a literal in the
handler's closure (no advertised op without a dispatch branch).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

from server.arg_aliases import IGNORED_LEGACY_ARGS, aliases_for
from server.handlers import register_all_handlers
from server.registry import HandlerRegistry

ROOT = Path(__file__).resolve().parent.parent

#: Args every tool understands (protocol layer) or that are envelope keys.
GLOBAL_OK = {
    "confirm_destructive",
    "response_format",
    "page_size",
    "next_token",
    "_meta",
    "confirmed",
    "evidence",
    "inline_image",
    # dispatch._ALWAYS_ALLOWED_ARGS: request_id (idempotent writes) and full (opt out of the
    # soft inline budget / compact default output), accepted by every tool.
    "request_id",
    "full",
}

#: Reads the name-based call graph attributes to a tool although they belong to
#: ANOTHER tool's handler that merely shares a helper name (verified by reading
#: the code: the key is never read on this tool's path). Keep this list short
#: and justified.
FALSE_POSITIVES: Dict[str, Set[str]] = {
    # 09_Debug's `_handle_analyze` shares its name with pbip_operations_handler's
    # 07_PBIP `analyze` branch (output_format / output_path / auto_open).
    "09_Debug_Operations": {"auto_open", "output_format", "output_path"},
    # `_severity(transform, args)` receives the transform's INNER args object.
    "15_DAX_Refactor": {"delete_after"},
}

#: Functions whose argument reads belong to ONE tool only: other tools call them
#: with a request dict they BUILD (L-12 forwarders), not with their caller's args.
BOUNDARIES: Dict[str, Set[str]] = {"execute_dax": {"04_Run_DAX"}}

_ARGV = {"args", "arguments"}
_HELPERS = {"get_optional_int", "get_optional_bool", "coerce_num", "_arg", "_get_arg"}

_ast_cache: Dict[Path, ast.Module] = {}


def _parse(path: Path) -> ast.Module:
    if path not in _ast_cache:
        _ast_cache[path] = ast.parse(path.read_text(encoding="utf-8"))
    return _ast_cache[path]


def _mod_path(mod: str):
    p = ROOT / (mod.replace(".", "/") + ".py")
    if p.exists():
        return p
    p = ROOT / mod.replace(".", "/") / "__init__.py"
    return p if p.exists() else None


def _imports(path: Path, modname: str) -> Set[str]:
    out: Set[str] = set()
    pkg = modname if path.name == "__init__.py" else modname.rsplit(".", 1)[0]
    for n in ast.walk(_parse(path)):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                parts = pkg.split(".")
                parts = parts[: len(parts) - (n.level - 1)] if n.level > 1 else parts
                base = ".".join(parts + ([base] if base else []))
            out.add(base)
            out.update(f"{base}.{a.name}" for a in n.names)
    return {m for m in out if m.split(".")[0] == "server"}


def _closure(start: str) -> Dict[str, Path]:
    seen: Dict[str, Path] = {}
    stack = [start]
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        p = _mod_path(m)
        if p is None:
            continue
        seen[m] = p
        stack.extend(i for i in _imports(p, m) if i.startswith("server.handlers"))
    return seen


def _defs(paths: Dict[str, Path]) -> Dict[str, List[Tuple[Path, ast.AST]]]:
    d: Dict[str, List[Tuple[Path, ast.AST]]] = {}
    for p in paths.values():
        for n in ast.walk(_parse(p)):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                d.setdefault(n.name, []).append((p, n))
    return d


def _globals(paths: Dict[str, Path]) -> Dict[str, List[ast.AST]]:
    g: Dict[str, List[ast.AST]] = {}
    for p in paths.values():
        for n in _parse(p).body:
            if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        g.setdefault(t.id, []).append(n.value)
    return g


def _refs(node: ast.AST, globs, depth: int = 0) -> Set[str]:
    s: Set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            s.add(n.id)
            if n.id in globs and depth < 3:
                for v in globs[n.id]:
                    s |= _refs(v, globs, depth + 1)
        elif isinstance(n, ast.Attribute):
            s.add(n.attr)
    return s


def _reads(fn: ast.AST) -> Dict[str, int]:
    keys: Dict[str, int] = {}
    for n in ast.walk(fn):
        k = None
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("get", "pop", "setdefault")
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id in _ARGV
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str)
        ):
            k = n.args[0].value
        elif (
            isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Name)
            and n.value.id in _ARGV
            and isinstance(n.slice, ast.Constant)
            and isinstance(n.slice.value, str)
        ):
            k = n.slice.value
        elif (
            isinstance(n, ast.Compare)
            and len(n.ops) == 1
            and isinstance(n.ops[0], (ast.In, ast.NotIn))
            and isinstance(n.comparators[0], ast.Name)
            and n.comparators[0].id in _ARGV
            and isinstance(n.left, ast.Constant)
            and isinstance(n.left.value, str)
        ):
            k = n.left.value
        elif (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in _HELPERS
            and len(n.args) >= 2
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id in _ARGV
            and isinstance(n.args[1], ast.Constant)
            and isinstance(n.args[1].value, str)
        ):
            k = n.args[1].value
        if k is not None:
            keys.setdefault(k, n.lineno)
    return keys


def _literals(nodes) -> Set[str]:
    out: Set[str] = set()
    for node in nodes:
        out |= {
            n.value
            for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
    return out


def drift_report() -> Dict[str, Dict[str, object]]:
    reg = HandlerRegistry()
    register_all_handlers(reg)
    report: Dict[str, Dict[str, object]] = {}
    for td in sorted(reg.get_all_tools(), key=lambda t: t.name):
        mod, name = td.handler.__module__, td.handler.__name__
        paths = _closure(mod)
        defs = _defs(paths)
        globs = _globals(paths)
        start = [x for x in defs.get(name, []) if x[0] == paths.get(mod)] or defs.get(name, [])
        seen, stack, reach = set(), list(start), []
        while stack:
            p, fn = stack.pop()
            key = (str(p), fn.lineno)
            if key in seen:
                continue
            seen.add(key)
            reach.append((p, fn))
            for r in _refs(fn, globs):
                if r in BOUNDARIES and td.name not in BOUNDARIES[r]:
                    continue
                stack.extend(defs.get(r, []))
        reads: Dict[str, str] = {}
        for p, fn in reach:
            for k, ln in _reads(fn).items():
                reads.setdefault(k, f"{p.relative_to(ROOT)}:{ln}")
        declared = set(reg.declared_properties(td.name))
        allowed = (
            declared
            | GLOBAL_OK
            | set(aliases_for(td.name))
            | set(IGNORED_LEGACY_ARGS.get(td.name, {}))
            | FALSE_POSITIVES.get(td.name, set())
        )
        undeclared = sorted((k, loc) for k, loc in reads.items() if k not in allowed)
        module_nodes = [
            n
            for p in paths.values()
            for n in _parse(p).body
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        lits = _literals([fn for _, fn in reach]) | _literals(module_nodes)
        enum = (reg.get_full_schema(td.name)["properties"].get("operation") or {}).get("enum") or []
        report[td.name] = {
            "undeclared": undeclared,
            "enum_missing": [e for e in enum if e not in lits],
        }
    return report


@pytest.fixture(scope="module")
def report():
    return drift_report()


def test_no_handler_reads_an_undeclared_argument(report):
    rows = {t: r["undeclared"] for t, r in report.items() if r["undeclared"]}
    assert not rows, rows


def test_every_operation_enum_value_has_a_dispatch_literal(report):
    rows = {t: r["enum_missing"] for t, r in report.items() if r["enum_missing"]}
    assert not rows, rows


def test_false_positive_allowances_are_still_needed(report):
    """An allowance that no longer suppresses anything must be removed."""
    import copy

    saved = copy.deepcopy(FALSE_POSITIVES)
    try:
        FALSE_POSITIVES.clear()
        raw = drift_report()
    finally:
        FALSE_POSITIVES.update(saved)
    for tool, names in saved.items():
        hits = {k for k, _ in raw[tool]["undeclared"]}
        assert names <= hits, (tool, sorted(names - hits))


# ---------------------------------------------------------------------------
# A real defect the drift probe surfaced
# ---------------------------------------------------------------------------


def test_qa_linguistic_dry_run_no_longer_writes_ungated(monkeypatch, tmp_path):
    """02_QA_Linguistic read no `dry_run`, but gate_and_wrap skips the gate for
    dry_run=true - so a "dry run" under STRICT wrote the culture file ungated.
    Now dry_run previews and never reaches the manager."""
    import server.handlers.qa_linguistic_handler as qa
    from core.autonomous.gating import invalidate_policy_cache

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    invalidate_policy_cache()
    calls = []

    class _Mgr:
        def __getattr__(self, name):
            return lambda **kw: calls.append((name, kw)) or {"success": True}

    monkeypatch.setattr(qa, "QaLinguisticManager", _Mgr)
    out = qa.handle_qa_linguistic(
        {
            "operation": "add_synonym",
            "pbip_path": str(tmp_path),
            "entity": "Sales",
            "synonym": "revenue",
            "dry_run": True,
        }
    )
    assert calls == []
    assert out["success"] is True and out["dry_run"] is True
    assert out["would_change"] == {"entity": "Sales", "synonym": "revenue"}
