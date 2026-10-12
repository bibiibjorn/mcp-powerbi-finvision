"""client_gateway.keep: the keep files of a Power BI MCP that masks without a Claude Code chat."""

from __future__ import annotations

import json
import os

from client_gateway import keep


def test_register_and_alive(tmp_path):
    assert keep.any_alive(tmp_path) is False
    assert keep.register(tmp_path) is True
    assert keep.registered(tmp_path)
    assert keep.any_alive(tmp_path) is True
    keep.unregister(tmp_path)
    assert not keep.registered(tmp_path)
    assert keep.any_alive(tmp_path) is False


def test_stale_file_of_a_dead_process_is_removed(tmp_path, monkeypatch):
    folder = tmp_path / keep.KEEP_DIR
    folder.mkdir()
    stale = folder / "999999.json"
    stale.write_text(json.dumps({"pid": 999999, "created": 1.0}), encoding="utf-8")
    monkeypatch.setattr(keep, "_create_time", lambda pid: None)
    assert keep.any_alive(tmp_path) is False
    assert not stale.exists()


def test_reused_pid_is_another_process(tmp_path, monkeypatch):
    folder = tmp_path / keep.KEEP_DIR
    folder.mkdir()
    path = folder / f"{os.getpid()}.json"
    path.write_text(json.dumps({"pid": os.getpid(), "created": 5.0}), encoding="utf-8")
    monkeypatch.setattr(keep, "_create_time", lambda pid: 500.0)
    assert keep.any_alive(tmp_path) is False
    assert not path.exists()


def test_unreadable_file_is_left_alone(tmp_path):
    folder = tmp_path / keep.KEEP_DIR
    folder.mkdir()
    junk = folder / "x.json"
    junk.write_text("not json", encoding="utf-8")
    assert keep.any_alive(tmp_path) is False
    assert junk.exists()
