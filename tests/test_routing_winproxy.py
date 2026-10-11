"""The Internet Settings backend and its manager (phase 3b.4): the one-time backup and the exact restore,
policy locks, notifications. Only the in-memory fake is used: the real ``HKCU`` backend cannot be built
under test (``test_routing_guard.py``)."""

from __future__ import annotations

import json

import pytest

from client_gateway.routing.winproxy import (
    REG_DWORD,
    REG_SZ,
    VALUE_NAMES,
    InternetSettings,
    MemoryInternetSettings,
    ProxyError,
    ProxySettings,
    RegValue,
)

PAC = "http://127.0.0.1:8890/proxy.pac"


def make(**kw):
    backend = MemoryInternetSettings.with_values(**kw)
    return backend, InternetSettings_for(backend)


def InternetSettings_for(backend, home=None):
    import tempfile
    from pathlib import Path

    return InternetSettings(Path(home or tempfile.mkdtemp()), backend)


CASES = {
    "nothing_set": {},
    "static_proxy": {
        "enable": 1,
        "server": "proxy.corp:8080",
        "override": "<local>;*.corp.local",
        "detect": 0,
    },
    "per_protocol_proxy": {"enable": 1, "server": "http=h1:80;https=h2:443", "detect": 0},
    "disabled_static_proxy": {"enable": 0, "server": "proxy.corp:8080", "detect": 1},
    "pac_url": {"enable": 0, "pac": "http://wpad.corp/proxy.pac", "detect": 0},
    "autodetect_only": {"detect": 1},
    "everything": {
        "enable": 1,
        "server": "p:1",
        "override": "10.*",
        "pac": "http://x/y.pac",
        "detect": 1,
    },
}


# ---------------------------------------------------------------------------------- backup and restore
@pytest.mark.parametrize("case", sorted(CASES))
def test_restore_puts_back_exactly_what_was_there(case, tmp_path):
    backend = MemoryInternetSettings.with_values(**CASES[case])
    before = dict(backend.values)
    manager = InternetSettings(tmp_path, backend)
    previous = manager.enable(PAC, disable_autodetect=True)
    assert previous.values == before  # what the PAC builder gets to read
    assert backend.values["AutoConfigURL"] == RegValue(REG_SZ, PAC)
    assert backend.values["AutoDetect"] == RegValue(REG_DWORD, 0)
    assert manager.disable() == {"restored": True}
    assert backend.values == before  # absent values are absent again, the rest byte for byte
    assert not manager.backup_exists()
    assert backend.notified == 2  # one after enabling, one after restoring


def test_only_the_five_values_are_touched_and_nothing_else_changes(tmp_path):
    backend = MemoryInternetSettings.with_values(enable=1, server="p:1", detect=1)
    manager = InternetSettings(tmp_path, backend)
    manager.enable(PAC)  # without disable_autodetect: AutoDetect stays as it was
    assert backend.values["ProxyEnable"] == RegValue(REG_DWORD, 1)
    assert backend.values["ProxyServer"] == RegValue(REG_SZ, "p:1")
    assert backend.values["AutoDetect"] == RegValue(REG_DWORD, 1)
    assert set(backend.values) == set(VALUE_NAMES)
    assert backend.writes == [("apply_pac", PAC)]


def test_the_backup_is_written_once_before_the_first_change(tmp_path):
    backend = MemoryInternetSettings.with_values(enable=1, server="proxy.corp:8080")
    manager = InternetSettings(tmp_path, backend)
    seen = []
    original = backend.apply_pac

    def spy(url, disable_autodetect=False):
        seen.append(manager.backup_exists())  # the backup must exist before the change is made
        original(url, disable_autodetect)

    backend.apply_pac = spy
    manager.enable(PAC)
    assert seen == [True]
    saved = json.loads((tmp_path / "routing" / "proxy_backup.json").read_text(encoding="utf-8"))
    assert saved["values"]["ProxyServer"] == {"type": REG_SZ, "data": "proxy.corp:8080"}
    assert saved["values"]["AutoConfigURL"] is None
    # a second enable (a restart after a crash) keeps the ORIGINAL backup, not our own rule
    again = manager.enable(PAC)
    assert again.auto_config_url is None and again.proxy_server == "proxy.corp:8080"
    assert manager.backup().auto_config_url is None


def test_our_own_rule_without_a_backup_is_refused_because_the_previous_state_is_unknown(tmp_path):
    backend = MemoryInternetSettings.with_values(pac=PAC)
    with pytest.raises(ProxyError, match="backup_exists_unrelated"):
        InternetSettings(tmp_path, backend).enable(PAC)
    assert backend.writes == []


def test_disable_without_a_backup_changes_nothing(tmp_path):
    backend = MemoryInternetSettings.with_values(enable=1, server="p:1")
    before = dict(backend.values)
    assert InternetSettings(tmp_path, backend).disable() == {
        "restored": False,
        "reason": "no_backup",
    }
    assert backend.values == before and backend.writes == [] and backend.notified == 0


def test_a_restore_that_does_not_read_back_identical_keeps_the_backup(tmp_path):
    backend = MemoryInternetSettings.with_values(enable=1, server="p:1")
    manager = InternetSettings(tmp_path, backend)
    manager.enable(PAC)
    backend.restore = lambda saved: None  # the registry write silently did nothing
    with pytest.raises(ProxyError, match="restore_mismatch"):
        manager.disable()
    assert manager.backup_exists()  # nothing is lost: it can be tried again


def test_a_backend_failure_while_restoring_keeps_the_backup(tmp_path):
    backend = MemoryInternetSettings.with_values(pac="http://corp/p.pac")
    manager = InternetSettings(tmp_path, backend)
    manager.enable(PAC)
    backend.fail_restore = True
    with pytest.raises(ProxyError, match="backend_failed"):
        manager.disable()
    assert manager.backup_exists()
    backend.fail_restore = False
    assert manager.disable() == {"restored": True}
    assert backend.values["AutoConfigURL"] == RegValue(REG_SZ, "http://corp/p.pac")


# ---------------------------------------------------------------------------------- locks
@pytest.mark.parametrize(
    "lock", ["proxy_settings_per_machine", "HKLM_policy_sets_proxy", "ie_policy_locks_proxy"]
)
def test_a_policy_lock_refuses_routing_and_writes_nothing(lock, tmp_path):
    backend = MemoryInternetSettings.with_values(enable=1, server="p:1", locked=(lock,))
    manager = InternetSettings(tmp_path, backend)
    with pytest.raises(ProxyError, match="locked"):
        manager.enable(PAC)
    assert backend.writes == [] and backend.notified == 0 and not manager.backup_exists()


def test_read_reports_the_locks_with_the_values(tmp_path):
    settings = MemoryInternetSettings.with_values(enable=1, locked=("x",)).read()
    assert settings.locked == ("x",) and settings.proxy_enable == 1


# ---------------------------------------------------------------------------------- the data model
def test_the_accessors_read_absent_empty_and_typed_values():
    empty = MemoryInternetSettings.with_values().read()
    assert (empty.proxy_enable, empty.proxy_server, empty.proxy_override) == (None, None, None)
    assert (empty.auto_config_url, empty.auto_detect) == (None, None)
    blank = MemoryInternetSettings.with_values(server="", pac="", override="").read()
    assert blank.proxy_server is None and blank.auto_config_url is None
    assert blank.proxy_override is None
    full = MemoryInternetSettings.with_values(
        enable=1, server="a:1", override="o", pac="u", detect=0
    )
    settings = full.read()
    assert settings.proxy_enable == 1 and settings.proxy_server == "a:1"
    assert settings.proxy_override == "o" and settings.auto_config_url == "u"
    assert settings.auto_detect == 0


def test_the_json_form_round_trips_and_rejects_damage():
    settings = MemoryInternetSettings.with_values(enable=1, server="a:1", detect=0).read()
    assert ProxySettings.from_json(settings.to_json()).same_values(settings)
    good = settings.to_json()
    for bad in (
        None,
        [],
        {"values": 3},
        {"values": {"ProxyServer": 5}},
        {"values": {"ProxyServer": {"type": REG_DWORD, "data": 1}}},  # the wrong type for the name
        {"values": {"ProxyEnable": {"type": REG_DWORD, "data": "x"}}},
        {"values": {**good["values"], "ProxyServer": {"type": REG_SZ, "data": 3}}},
    ):
        with pytest.raises(ProxyError, match="backup_unreadable"):
            ProxySettings.from_json(bad)


def test_a_damaged_backup_file_is_a_fixed_code(tmp_path):
    backend = MemoryInternetSettings.with_values(enable=1)
    manager = InternetSettings(tmp_path, backend)
    manager.enable(PAC)
    manager.path.write_text("not json", encoding="utf-8")
    with pytest.raises(ProxyError, match="backup_unreadable"):
        manager.disable()


def test_rule_is_ours_looks_at_the_pac_url(tmp_path):
    backend = MemoryInternetSettings.with_values(pac="http://corp/p.pac")
    manager = InternetSettings(tmp_path, backend)
    assert not manager.rule_is_ours("http://127.0.0.1:")
    manager.enable(PAC)
    assert manager.rule_is_ours("http://127.0.0.1:") and not manager.rule_is_ours("http://other")
