"""Pseudonym key rotation (ENISA linkability; owner go 2026-10-08): a new key gives every client a
new code, and the codes of an earlier key generation can never be read as another client."""

from __future__ import annotations

import os
import time

import pytest

from core.client_guard.engine import SessionMap
from core.client_guard.tokens import (
    GENERATIONS,
    KEY_FILE,
    key_age_days,
    load_key,
    load_or_create_key,
    rotate_if_due,
    rotate_key,
    stable_token_fn,
    token_range,
)

NAME = "Zorvex Holding"


def _code(token_fn) -> str:
    m = SessionMap(token_fn=token_fn)
    m.add_values("CUS", [NAME], "t")
    return m.mask_text(f"see {NAME}")[0].split()[-1]


def test_an_existing_key_is_generation_zero_and_keeps_its_codes(tmp_path) -> None:
    key = load_or_create_key(tmp_path)
    assert load_key(tmp_path) == (key, 0)
    code = _code(stable_token_fn(tmp_path))
    assert code == _code(stable_token_fn(tmp_path)) and len(code) == len("CUS_12345")


def test_rotation_gives_new_codes_in_a_range_no_earlier_generation_uses(tmp_path) -> None:
    before = _code(stable_token_fn(tmp_path))
    assert rotate_key(tmp_path) == 1
    after = _code(stable_token_fn(tmp_path))
    assert after != before
    low, high = token_range(1)
    assert low <= int(after.split("_")[1]) <= high and len(after) == len("CUS_123456")
    assert int(before.split("_")[1]) < low  # generation 0 lives below every later range


def test_ranges_of_all_generations_are_disjoint_and_wrap_after_the_last() -> None:
    ranges = [token_range(g) for g in range(GENERATIONS + 1)]
    for i, (lo, hi) in enumerate(ranges):
        for lo2, hi2 in ranges[i + 1 :]:
            assert hi < lo2 or hi2 < lo


def test_generations_cycle_1_to_9(tmp_path) -> None:
    load_or_create_key(tmp_path)
    seen = [rotate_key(tmp_path) for _ in range(GENERATIONS + 1)]
    assert seen == [*range(1, GENERATIONS + 1), 1]


def test_an_old_generation_code_is_left_alone_not_restored_to_another_client(tmp_path) -> None:
    old_code = _code(stable_token_fn(tmp_path))
    rotate_key(tmp_path)
    m = SessionMap(token_fn=stable_token_fn(tmp_path))
    m.add_values("CUS", [NAME, "Other Client NV"], "t")
    text, n = m.unmask_text(f"compare {old_code}")
    assert text == f"compare {old_code}" and n == 0


def test_rotation_when_due_only(tmp_path) -> None:
    load_or_create_key(tmp_path)
    now = time.time()
    assert rotate_if_due(tmp_path, 90, now=now) is None  # brand new key
    assert rotate_if_due(tmp_path, 0, now=now + 400 * 86_400) is None  # 0 = never
    old = now - 91 * 86_400
    os.utime(tmp_path / KEY_FILE, (old, old))
    assert key_age_days(tmp_path, now) == pytest.approx(91, abs=0.01)
    assert rotate_if_due(tmp_path, 90, now=now) == 1
    assert key_age_days(tmp_path) < 1  # the new key starts its own period


def test_the_gateway_state_rotates_an_old_key_at_start(tmp_path, monkeypatch) -> None:
    from client_gateway import state as state_mod

    load_or_create_key(tmp_path)
    old = time.time() - 200 * 86_400
    os.utime(tmp_path / KEY_FILE, (old, old))
    monkeypatch.setattr(state_mod, "cfg", lambda key, default: default)
    fn, ephemeral = state_mod._token_source(tmp_path)
    assert ephemeral is False and load_key(tmp_path)[1] == 1
    assert fn.token_range == token_range(1)  # type: ignore[attr-defined]


def test_cli_rotate_key_refuses_while_a_gateway_runs(tmp_path, monkeypatch, capsys) -> None:
    from client_gateway import cli

    monkeypatch.setattr(cli, "masking_home", lambda: tmp_path)
    load_or_create_key(tmp_path)
    monkeypatch.setattr(cli, "health", lambda port, timeout=1.0: {"ok": True})
    assert cli.main(["rotate-key"]) == 1 and load_key(tmp_path)[1] == 0
    monkeypatch.setattr(cli, "health", lambda port, timeout=1.0: None)
    assert cli.main(["rotate-key"]) == 0 and load_key(tmp_path)[1] == 1
    assert "generation 1" in capsys.readouterr().out
