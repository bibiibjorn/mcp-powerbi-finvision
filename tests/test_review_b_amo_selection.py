"""2026-09-22 review, slice B — AMO/TOM/ADOMD build selection (B-07, K-09, B-10).

The old picker preferred DAX Studio's 19.84 build over the bundled 19.101 on a
false premise ("bundled = .NET Core"), accepted any build without a version
check, and trace_session AddReference'd a DIFFERENT folder than query_executor
(mixed builds in one process). Synthetic DLL byte blobs stand in for real
assemblies: only the version resource + TargetFramework markers matter.
"""

from __future__ import annotations

import os

import pytest

from core.infrastructure import dll_paths

_NAMES = (
    "Microsoft.AnalysisServices.Core.dll",
    "Microsoft.AnalysisServices.dll",
    "Microsoft.AnalysisServices.Tabular.dll",
    "Microsoft.AnalysisServices.AdomdClient.dll",
)


def _blob(version: str, framework: str) -> bytes:
    key = "Assembly Version".encode("utf-16-le") + b"\x00\x00"
    fkey = "FileVersion".encode("utf-16-le") + b"\x00\x00"
    return (
        b"MZ\x90\x00junk"
        + f".{framework},Version=v4.7.2".encode()
        + b"\x00" * 7
        + fkey
        + b"\x00\x00"
        + "17.0.1.2".encode("utf-16-le")
        + b"\x00\x00"
        + key
        + b"\x00\x00"
        + version.encode("utf-16-le")
        + b"\x00\x00tail"
    )


def _make_folder(root, name, version, framework="NETFramework", missing=()):
    folder = os.path.join(root, name)
    os.makedirs(folder, exist_ok=True)
    for dll in _NAMES:
        if dll in missing:
            continue
        with open(os.path.join(folder, dll), "wb") as fh:
            fh.write(_blob(version, framework))
    return folder


@pytest.fixture
def candidates(tmp_path, monkeypatch):
    bundled = _make_folder(str(tmp_path), "bundled", "19.101.1.0")
    dax_studio = _make_folder(str(tmp_path), "daxstudio", "19.84.1.0")
    te3 = _make_folder(str(tmp_path), "te3", "19.94.1.1", framework="NETCoreApp")
    partial = _make_folder(
        str(tmp_path), "partial", "19.200.0.0", missing=("Microsoft.AnalysisServices.dll",)
    )
    monkeypatch.setattr(dll_paths, "DLL_FOLDER", bundled)
    monkeypatch.setattr(
        dll_paths,
        "KNOWN_AMO_DIRS",
        (("dax_studio", dax_studio), ("tabular_editor_3", te3), ("partial", partial)),
    )
    monkeypatch.delenv(dll_paths.AMO_DIR_ENV, raising=False)
    monkeypatch.setattr(dll_paths, "_selection", None)
    yield {"bundled": bundled, "dax_studio": dax_studio, "te3": te3}
    monkeypatch.setattr(dll_paths, "_selection", None)


def test_inspect_assembly_reads_version_and_framework_without_loading(tmp_path):
    folder = _make_folder(str(tmp_path), "x", "19.101.1.0")
    info = dll_paths.inspect_assembly(os.path.join(folder, _NAMES[2]))
    assert info["assembly_version"] == "19.101.1.0"
    assert info["framework"] == "NETFramework"


def test_newest_suitable_netfx_build_wins_over_dax_studio(candidates):
    sel = dll_paths.select_amo_folder(refresh=True)
    assert sel["folder"] == candidates["bundled"]
    assert sel["version"] == "19.101.1.0"
    by_label = {c["label"]: c for c in sel["candidates"]}
    assert by_label["dax_studio"]["suitable"] is True
    assert by_label["tabular_editor_3"]["suitable"] is False
    assert "NETCoreApp" in by_label["tabular_editor_3"]["reason"]
    assert by_label["partial"]["suitable"] is False  # incomplete set never wins


def test_env_override_wins(candidates, monkeypatch):
    monkeypatch.setenv(dll_paths.AMO_DIR_ENV, candidates["dax_studio"])
    sel = dll_paths.select_amo_folder(refresh=True)
    assert sel["folder"] == candidates["dax_studio"]
    assert "override" in sel["reason"]


def test_every_loader_uses_the_same_folder(candidates):
    dll_paths.select_amo_folder(refresh=True)
    paths = dll_paths.get_dll_paths()
    assert paths.dll_folder == candidates["bundled"]
    assert dll_paths.get_trace_capable_dll_folder() == candidates["bundled"]


def test_trace_session_no_longer_prefers_a_private_folder():
    """B-07: trace_session must not AddReference a second AMO copy."""
    from core.infrastructure import trace_session

    assert not hasattr(trace_session, "_TRACE_ASSEMBLY_DIRS")


def test_amo_info_reports_selection(candidates):
    dll_paths.select_amo_folder(refresh=True)
    info = dll_paths.get_amo_info(include_candidates=True)
    assert info["selected_version"] == "19.101.1.0"
    assert info["selected_folder"] == candidates["bundled"]
    assert isinstance(info["loaded"], list)
    assert any(c["label"] == "tabular_editor_3" for c in info["candidates"])


def test_no_events_trace_is_reported_as_trace_unavailable(monkeypatch):
    """B-10: a trace that delivered nothing is an error naming the AMO build."""
    from core.infrastructure import trace_session
    from core.infrastructure.query_trace import NativeTraceRunner

    result = trace_session.TraceResult()
    result.result_sets = [{"columns": ["[a]"], "row_count": 1}]
    result.events_received = 0
    result.trace_started = False
    monkeypatch.setattr(
        NativeTraceRunner, "_amo_info", staticmethod(lambda: {"selected_version": "19.101.1.0"})
    )
    assert NativeTraceRunner._no_events(result) is True
    err = NativeTraceRunner._no_events_error(result)
    assert err["_error_type"] == "trace_unavailable"
    assert err["reason"] == "trace_no_events"
    assert "19.101.1.0" in err["_error"]
