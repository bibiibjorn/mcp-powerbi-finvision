"""Review C-07 / C-08 / K-10 / K-19: one dependency story.

* ``requirements.txt`` (+ ``requirements-optional.txt``) mirror ``python/pyproject.toml``;
* every third-party module imported by the server is declared;
* no declared package is dead weight (the nine pins that nothing imported are gone for good);
* the version string and the console-script entry point agree across files.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib" if sys.version_info >= (3, 11) else "tomli")

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((REPO / "python" / "pyproject.toml").read_text(encoding="utf-8"))
PROJECT = PYPROJECT["project"]

# Pinned packages nothing in the code base imports (C-08 #1). Re-adding one needs a real import.
DEAD_PINS = {
    "wmi",
    "pbixray",
    "openpyxl",
    "reportlab",
    "matplotlib",
    "networkx",
    "polars",
    "mss",
    "beautifulsoup4",
}

# import name -> distribution name
IMPORT_TO_DIST = {
    "PIL": "pillow",
    "docx": "python-docx",
    "jsonpath_ng": "jsonpath-ng",
    "ahocorasick": "pyahocorasick",
    "clr": "pythonnet",
    "pbi_perf_core": "pbi-perf-core",
    "rapidocr_onnxruntime": "rapidocr-onnxruntime",
    "win32api": "pywin32",
    "win32con": "pywin32",
    "win32crypt": "pywin32",
    "win32event": "pywin32",
    "win32file": "pywin32",
    "win32gui": "pywin32",
    "win32process": "pywin32",
    "win32com": "pywin32",
    "pywintypes": "pywin32",
}
# Imported on purpose without a pin of their own (reason in the value).
UNPINNED_IMPORTS = {
    "torch": "optional NER: CPU wheel installed from the PyTorch index (see requirements-optional.txt)",
    "onnxruntime": "pulled in by rapidocr-onnxruntime / gliner (optional extras)",
    "Microsoft": ".NET namespace reached through pythonnet",
    "System": ".NET namespace reached through pythonnet",
}
SOURCE_DIRS = ("core", "server", "src", "client_gateway", "pbi-perf-core/pbi_perf_core")


def _norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _req_key(line: str) -> str:
    """Canonical text of one requirement: name+specifier+marker with quoting/spacing normalised."""
    line = line.strip().replace("'", '"')
    name = re.match(r"[A-Za-z0-9_.\-]+", line).group(0)
    rest = line[len(name) :]
    return _norm_name(name) + re.sub(r"\s+", "", rest)


def _req_name(line: str) -> str:
    return _norm_name(re.match(r"[A-Za-z0-9_.\-]+", line.strip()).group(0))


def _requirement_lines(path: Path) -> list[str]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith(("-e", "-r", "--")):
            out.append(line)
    return out


def _declared(*groups: str) -> set[str]:
    out: set[str] = set()
    for g in groups:
        reqs = (
            PROJECT["dependencies"] if g == "dependencies" else PROJECT["optional-dependencies"][g]
        )
        out |= {_req_key(r) for r in reqs if not r.lstrip().lower().startswith("pbi-perf-core")}
    return out


def test_requirements_txt_mirrors_pyproject_core_plus_gateway_extra():
    assert {_req_key(r) for r in _requirement_lines(REPO / "requirements.txt")} == _declared(
        "dependencies", "gateway"
    )


def test_optional_requirements_mirror_the_optional_extras():
    assert {
        _req_key(r) for r in _requirement_lines(REPO / "requirements-optional.txt")
    } == _declared("gateway-ocr", "gateway-ner")


def test_python_requirements_is_only_an_include():
    assert _requirement_lines(REPO / "python" / "requirements.txt") == []
    assert "-r ../requirements.txt" in (REPO / "python" / "requirements.txt").read_text(
        encoding="utf-8"
    )


def test_pbi_perf_core_is_declared_in_both_places():
    assert re.search(
        r"^-e \./pbi-perf-core\s*$", (REPO / "requirements.txt").read_text(encoding="utf-8"), re.M
    )
    assert any(_req_name(r) == "pbi-perf-core" for r in PROJECT["dependencies"])


def test_dead_pins_stay_gone():
    names = {_req_name(r) for r in PROJECT["dependencies"]}
    for group in PROJECT["optional-dependencies"].values():
        names |= {_req_name(r) for r in group}
    names |= {_req_name(r) for r in _requirement_lines(REPO / "requirements.txt")}
    assert not (names & {_norm_name(p) for p in DEAD_PINS})


def test_security_floors_and_psutil_pin():
    reqs = {_req_name(r): r for r in PROJECT["dependencies"]}
    for name, floor in (("urllib3", (2, 7)), ("requests", (2, 33)), ("pillow", (12, 3))):
        m = re.search(r">=\s*(\d+)\.(\d+)", reqs[name])
        assert m and (int(m.group(1)), int(m.group(2))) >= floor, reqs[name]
    # psutil 7.x is what the dev environment runs; the old "<7" pin kept pip off-spec.
    assert "<8" in reqs["psutil"].replace(" ", "")


def _third_party_imports() -> dict[str, str]:
    local = {"core", "server", "src", "client_gateway", "tools", "hooks", "tests", "pbi_perf_core"}
    local |= {p.stem for p in (REPO / "src").glob("*.py")}
    found: dict[str, str] = {}
    for sub in SOURCE_DIRS:
        for path in (REPO / sub).rglob("*.py"):
            if {"__pycache__", "bin", "obj"} & set(path.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module]
                for m in mods:
                    top = m.split(".")[0]
                    if top in sys.stdlib_module_names or top in local or top == "__future__":
                        continue
                    found.setdefault(top, str(path.relative_to(REPO)))
    return found


def test_every_third_party_import_is_declared():
    declared = {_req_name(r) for r in PROJECT["dependencies"]}
    for group in PROJECT["optional-dependencies"].values():
        declared |= {_req_name(r) for r in group}
    missing = {}
    for top, where in _third_party_imports().items():
        if top in UNPINNED_IMPORTS:
            continue
        dist = _norm_name(IMPORT_TO_DIST.get(top, top))
        # winrt namespace packages are declared as winrt-Windows.* / winrt-runtime
        if dist == "winrt":
            if any(d.startswith("winrt-") for d in declared):
                continue
        if dist not in declared:
            missing[top] = where
    assert not missing, f"imported but not declared in python/pyproject.toml: {missing}"


def test_version_is_the_same_everywhere():
    ns: dict = {}
    exec((REPO / "src" / "__version__.py").read_text(encoding="utf-8"), ns)
    manifest = json.loads((REPO / "manifest.json").read_text(encoding="utf-8"))
    assert ns["__version__"] == manifest["version"] == PROJECT["version"]


def test_console_script_points_at_the_sync_wrapper():
    target = PROJECT["scripts"]["pbixray-server"]
    module, func = target.split(":")
    tree = ast.parse((REPO / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8"))
    sync = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    asyn = {n.name for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}
    assert func in sync and func not in asyn, f"{target}: entry point must be a plain function"
