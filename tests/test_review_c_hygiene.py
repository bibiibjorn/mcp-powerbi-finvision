"""Review C-21 / C-12: the debug module survives a missing sibling package; the C# trace runner's
build output is git-ignored while the runtime set stays tracked."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_debug_operations_imports_without_pbi_perf_core():
    code = (
        "import sys; sys.modules['pbi_perf_core'] = None\n"
        "import core.debug.debug_operations as d\n"
        "print(d.DEFAULT_QUERY_SLOTS)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    from pbi_perf_core.perf_analyzer import DEFAULT_QUERY_SLOTS

    assert proc.stdout.strip() == str(DEFAULT_QUERY_SLOTS)


def test_dax_executor_build_output_is_ignored_but_runtime_exe_is_not():
    def ignored(rel: str) -> bool:
        r = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", rel], cwd=REPO, capture_output=True
        )
        return r.returncode == 0

    base = "core/infrastructure/dax_executor"
    rel = "bin/Release/net8.0-windows/win-x64"
    assert ignored(f"{base}/obj/project.assets.json")
    assert ignored(f"{base}/{rel}/DaxExecutor.pdb")
    assert ignored(f"{base}/{rel}/de/System.CommandLine.resources.dll")
    # dll_paths.get_trace_runner_path() launches this one.
    assert not ignored(f"{base}/{rel}/DaxExecutor.exe")
    assert not ignored(f"{base}/{rel}/DaxExecutor.dll")


def test_mcpbignore_keeps_the_bridge_lib_helper():
    text = (REPO / ".mcpbignore").read_text(encoding="utf-8")
    assert "!tools/bridge_lib.mjs" in text
    assert "\ntools/\n" not in text.replace("\r\n", "\n")
