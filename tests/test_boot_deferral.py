"""WP-P wave 2 -- boot costs (P-05 schema check deferral, P-16 tail scan of the AMO DLLs)."""

from __future__ import annotations

from core.infrastructure import dll_paths
from server.registry import HandlerRegistry, ToolDefinition


def _reg(*names, bad=()):
    reg = HandlerRegistry()
    for n in names:
        schema = (
            {"type": "object", "properties": {"x": {"type": "no-such-type"}}}
            if n in bad
            else {"type": "object", "properties": {"x": {"type": "string"}}}
        )
        reg.register(
            ToolDefinition(
                name=n, description="d", handler=lambda a: {"success": True}, input_schema=schema
            )
        )
    return reg


def _count_check_schema(monkeypatch):
    import jsonschema

    calls = []
    real = jsonschema.Draft202012Validator.check_schema.__func__

    def spy(cls, schema, *a, **k):
        calls.append(cls)
        return real(cls, schema, *a, **k)

    for cls in (jsonschema.Draft202012Validator, jsonschema.Draft7Validator):
        monkeypatch.setattr(cls, "check_schema", classmethod(spy))
    return calls


def test_prebuild_does_not_run_check_schema(monkeypatch):
    calls = _count_check_schema(monkeypatch)
    reg = _reg("01_A", "02_B", "03_C")
    reg.prebuild()
    assert calls == []
    assert len(reg.get_all_tools_as_mcp()) == 3  # tools/list is still built up front


def test_validator_is_built_on_first_use_and_cached(monkeypatch):
    calls = _count_check_schema(monkeypatch)
    reg = _reg("01_A", "02_B")
    reg.prebuild()
    v = reg.get_input_validator("01_A")
    assert v is not None and len(calls) == 1
    assert reg.get_input_validator("01_A") is v and len(calls) == 1
    assert list(v.iter_errors({"x": 5}))  # still validates input
    assert reg.get_input_validator("nope") is None


def test_eager_prebuild_is_still_available(monkeypatch):
    calls = _count_check_schema(monkeypatch)
    reg = _reg("01_A", "02_B")
    reg.prebuild(validators=True)
    assert len(calls) == 2


def test_broken_schema_is_checked_and_logged_once(monkeypatch, caplog):
    calls = _count_check_schema(monkeypatch)
    reg = _reg("99_Bad", bad=("99_Bad",))
    reg.prebuild()
    with caplog.at_level("ERROR"):
        assert reg.get_input_validator("99_Bad") is None
        assert reg.get_input_validator("99_Bad") is None
        assert reg.get_input_validator("99_Bad") is None
    assert len(calls) == 1
    assert sum("Invalid inputSchema" in r.message for r in caplog.records) == 1


def test_register_resets_validator_state(monkeypatch):
    reg = _reg("99_Bad", bad=("99_Bad",))
    assert reg.get_input_validator("99_Bad") is None
    reg.register(
        ToolDefinition(
            name="99_Bad",
            description="fixed",
            handler=lambda a: {"success": True},
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
    )
    assert reg.get_input_validator("99_Bad") is not None


# ----------------------------------------------------------------------- P-16


def _dll(version: str, tfm: str, pad_before: int, pad_after: int, file_version: str = "") -> bytes:
    asm = "Assembly Version".encode("utf-16-le") + b"\x00\x00" + version.encode("utf-16-le")
    fv = (
        "FileVersion".encode("utf-16-le")
        + b"\x00\x00"
        + (file_version or version).encode("utf-16-le")
    )
    return (
        b"\x00" * pad_before
        + asm
        + b"\x00" * 8
        + fv
        + b"\x00" * 8
        + tfm.encode()
        + b"\x00" * pad_after
    )


TFM = ".NETFramework,Version=v4.7.2"


def test_tail_scan_equals_full_scan(tmp_path):
    big = tmp_path / "big.dll"
    big.write_bytes(_dll("19.114.8.0", TFM, pad_before=2_000_000, pad_after=20_000))
    small = tmp_path / "small.dll"
    small.write_bytes(_dll("19.84.1.0", TFM, pad_before=100, pad_after=100))
    for path, version in ((big, "19.114.8.0"), (small, "19.84.1.0")):
        info = dll_paths.inspect_assembly(str(path))
        assert info["assembly_version"] == version and info["file_version"] == version
        assert info["target_framework"] == TFM and info["framework"] == "NETFramework"


def test_values_outside_the_tail_fall_back_to_the_full_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dll_paths, "_TAIL_BYTES", 4096)
    far = tmp_path / "far.dll"
    far.write_bytes(_dll("19.50.0.0", TFM, pad_before=10, pad_after=100_000))  # metadata up front
    info = dll_paths.inspect_assembly(str(far))
    assert info["assembly_version"] == "19.50.0.0"
    assert info["target_framework"] == TFM


def test_missing_and_unreadable_files_report_as_before(tmp_path):
    info = dll_paths.inspect_assembly(str(tmp_path / "nope.dll"))
    assert info == {"path": str(tmp_path / "nope.dll"), "exists": False}
    plain = tmp_path / "plain.dll"
    plain.write_bytes(b"MZ" + b"\x00" * 100)
    info = dll_paths.inspect_assembly(str(plain))
    assert info["assembly_version"] is None and "target_framework" not in info
