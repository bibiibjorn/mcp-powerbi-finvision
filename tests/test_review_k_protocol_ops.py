"""K-07 (log rotation / buffered flush), K-12 (manifest launcher), K-14 (gate
policy pinned for the suite), A-27 (server.log_level honoured), A-28 (BOM),
A-29 (config reload reaches the dispatcher's cached flags).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
from logging.handlers import MemoryHandler, RotatingFileHandler

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def isolated_root_logger():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield root
    for h in list(root.handlers):
        if h not in saved_handlers:
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    root.setLevel(saved_level)


def test_logging_rotates_and_flushes_buffered_info(tmp_path, isolated_root_logger):
    from server.logging_setup import MAX_LOG_BYTES, configure_logging

    path = configure_logging(str(tmp_path), "ERROR", flush_interval=0.1)
    assert path == str(tmp_path / "pbixray.log")
    mem = [
        h
        for h in isolated_root_logger.handlers
        if isinstance(h, MemoryHandler) and getattr(h, "_mcp_file_path", None) == path
    ]
    assert len(mem) == 1
    assert isinstance(mem[0].target, RotatingFileHandler)
    assert mem[0].target.maxBytes == MAX_LOG_BYTES and mem[0].target.backupCount >= 1
    # idempotent
    assert configure_logging(str(tmp_path), "ERROR") == path
    assert (
        len(
            [h for h in isolated_root_logger.handlers if getattr(h, "_mcp_file_path", None) == path]
        )
        == 1
    )
    # A-27: stderr honours the configured level
    console = [h for h in isolated_root_logger.handlers if getattr(h, "_mcp_console", False)]
    assert console and console[0].level == logging.ERROR
    # K-07: an INFO record reaches disk without a WARNING or process exit
    logging.getLogger("wp2.k07").info("buffered info line")
    deadline = time.monotonic() + 3
    content = ""
    while time.monotonic() < deadline:
        mem[0].target.flush()
        content = pathlib.Path(path).read_text(encoding="utf-8") if os.path.exists(path) else ""
        if "buffered info line" in content:
            break
        time.sleep(0.05)
    assert "buffered info line" in content


def test_manifest_launches_the_venv_python():
    """K-12: `py -3` picks the launcher default (3.14 here) while the venv's
    native wheels are cp313."""
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    cfg = manifest["server"]["mcp_config"]
    assert cfg["command"] == "${__dirname}/venv/Scripts/python.exe"
    assert cfg["args"] == ["${__dirname}/src/run_server.py"]
    names = {t["name"] for t in manifest["tools"]}
    from server.handlers import register_all_handlers
    from server.registry import HandlerRegistry

    reg = HandlerRegistry()
    register_all_handlers(reg)
    assert names == {t.name for t in reg.get_all_tools()}


def test_run_server_reexec_guard_reads_pyvenv_version(tmp_path):
    # Execute only the helper section (the rest of the module boots the server),
    # rooted at a temp dir with a fake venv.
    src = (ROOT / "src" / "run_server.py").read_text(encoding="utf-8")
    helpers = src.split('if __name__ == "__main__":')[0]
    ns: dict = {"__file__": str(tmp_path / "src" / "run_server.py")}
    exec(compile(helpers, "run_server_helpers", "exec"), ns)
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "pyvenv.cfg").write_text("home = x\nversion = 3.13.5\n", encoding="utf-8")
    assert ns["_venv_version"]() == (3, 13)


def test_suite_pins_the_gate_policy():
    """K-14: conftest pins LIGHT so an exported STRICT/OFF can't flip tests."""
    assert os.environ.get("MCP_AUTONOMOUS_GATE_POLICY") == "LIGHT"
    from core.autonomous.gating import current_policy

    assert current_policy() == "LIGHT"


def test_load_json_accepts_utf8_bom(tmp_path):
    from core.utilities.json_utils import load_json

    p = tmp_path / "local_config.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"security": {"read_only_mode": True}}).encode())
    assert load_json(p) == {"security": {"read_only_mode": True}}


def test_config_reload_refreshes_dispatcher_security_flags(tmp_path, monkeypatch):
    # this test writes its OWN local_config.json in a temp dir: let the manager read it (conftest
    # switches local configs off so the developer's real one never changes what tests see)
    monkeypatch.delenv("MCP_PBI_NO_LOCAL_CONFIG", raising=False)
    from core.config.config_manager import ConfigManager
    import server.dispatch as dispatch_mod

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "default_config.json").write_text(
        json.dumps({"security": {"read_only_mode": False}}), encoding="utf-8"
    )
    cm = ConfigManager(str(cfg_dir))
    original = dispatch_mod.config
    dispatch_mod.config = cm
    try:
        d = dispatch_mod.ToolDispatcher()
        assert d._read_only is False
        (cfg_dir / "local_config.json").write_text(
            json.dumps({"security": {"read_only_mode": True}}), encoding="utf-8"
        )
        cm.reload()
        assert d._read_only is True
    finally:
        dispatch_mod.config = original


def test_dead_security_and_server_config_keys_pruned():
    """A-29: keys that looked like working security settings but were never read."""
    cfg = json.loads((ROOT / "config" / "default_config.json").read_text(encoding="utf-8"))
    for key in (
        "enable_input_validation",
        "strict_m_validation",
        "allowed_export_extensions",
        "max_export_path_length",
    ):
        assert key not in cfg["security"], key
    for key in ("timeout_seconds", "tool_names_mode", "show_admin_tools"):
        assert key not in cfg["server"], key
