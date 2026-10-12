"""The per-app replay self-test (``tools/masking_lab/selftest.py``, phase 3c, rule R9).

It replays every recorded wire fixture of every named app through the current code. These tests pin
that it passes on the shipped code, that it fails (and names the fixture, never a value) when an
adapter stops masking or a reply stops being rehydrated, and that ``lab.py selftest`` runs it.
Offline; synthetic names only.
"""

from __future__ import annotations

import os

import pytest

from client_gateway.adapters import openai_responses
from client_gateway.adapters.openai_chat import OpenAIChatAdapter
from tools.masking_lab import lab, selftest

pytestmark = pytest.mark.selftest


def run() -> tuple[int, list[str]]:
    lines: list[str] = []
    return selftest.run_selftest(lines.append), lines


def test_every_named_app_passes_on_the_shipped_code():
    code, lines = run()
    assert code == 0, lines
    apps = [ln.split()[0] for ln in lines if not ln.startswith(" ")]
    assert apps == ["claude-code", "copilot", "chatgpt"]
    assert all(" PASS " in ln for ln in lines)
    assert not any("n/a" in ln for ln in lines)  # every stage ran for every app


def test_an_adapter_that_stops_masking_fails_its_app_line(monkeypatch):
    monkeypatch.setattr(OpenAIChatAdapter, "mask_request", lambda self, body, ctx, route=None: body)
    code, lines = run()
    assert code == 1
    (copilot,) = [ln for ln in lines if ln.startswith("copilot")]
    assert " FAIL " in copilot
    assert any("copilot_title_stream" in ln for ln in lines)
    assert all(" PASS " in ln for ln in lines if ln.startswith(("claude-code", "chatgpt")))


def test_a_reply_that_is_not_rehydrated_fails_its_app_line(monkeypatch):
    monkeypatch.setattr(
        openai_responses._ResponsesStream, "_delta", lambda self, raw, *args, **kw: raw
    )
    code, lines = run()
    assert code == 1
    assert any(ln.startswith("copilot") and " FAIL " in ln for ln in lines)
    assert any("copilot_responses" in ln and "not rehydrated" in ln for ln in lines)


def test_the_report_never_holds_a_client_value(monkeypatch):
    monkeypatch.setattr(OpenAIChatAdapter, "mask_request", lambda self, body, ctx, route=None: body)
    _, lines = run()
    from tests._wire_support import all_names

    assert not any(name in ln for ln in lines for name in all_names())


def test_the_run_leaves_the_environment_as_it_found_it():
    before = dict(os.environ)
    run()
    assert dict(os.environ) == before


def test_no_fixtures_is_exit_2_not_a_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(selftest, "WIRE", tmp_path)
    lines: list[str] = []
    assert selftest.run_selftest(lines.append) == 2


def test_lab_selftest_command_runs_it_offline(capsys):
    assert lab.main(["selftest"]) == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == 3 and "FAIL" not in out
