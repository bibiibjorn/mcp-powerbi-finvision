"""The WPAD probe (spec §8.2): ``found`` / ``none`` / ``unknown``, read-only, fake in every test.

The real probe is never built here (rule R8: the guard refuses it under pytest); its logic is driven
through an instance made without ``__init__`` and a fake WinHTTP, so no DLL is loaded and no network is
asked.
"""

from __future__ import annotations

import ctypes
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from client_gateway.routing import wpad
from client_gateway.routing.winproxy import MemoryInternetSettings
from client_gateway.routing.wpad import (
    ERROR_WINHTTP_AUTODETECTION_FAILED,
    FOUND,
    NONE,
    UNKNOWN,
    MemoryWpadProbe,
    WindowsWpadProbe,
    outcome_of,
    wpad_result,
)


def settings(detect: int | None):
    return MemoryInternetSettings.with_values(detect=detect).read()


# ---------------------------------------------------------------------------------- wpad_result
@pytest.mark.parametrize("answer", [FOUND, NONE, UNKNOWN])
def test_the_probe_answer_is_passed_through_when_auto_detect_is_on(answer):
    probe = MemoryWpadProbe(answer)
    assert wpad_result(settings(1), probe) == answer
    assert probe.calls == 1


@pytest.mark.parametrize("detect", [0, None])
def test_nothing_is_probed_when_auto_detect_is_not_on(detect):
    probe = MemoryWpadProbe(FOUND)
    assert wpad_result(settings(detect), probe) == ""
    assert probe.calls == 0


def test_no_probe_means_not_asked():
    assert wpad_result(settings(1), None) == ""


def test_a_probe_that_raises_or_answers_nonsense_is_unknown():
    class Boom:
        def probe(self) -> str:
            raise OSError("no network stack")

    class Odd:
        def probe(self) -> Any:
            return "maybe"

    assert wpad_result(settings(1), Boom()) == UNKNOWN
    assert wpad_result(settings(1), Odd()) == UNKNOWN


# ---------------------------------------------------------------------------------- outcome_of
def test_only_the_documented_winhttp_error_means_none():
    assert outcome_of(True, 0) == FOUND
    assert outcome_of(True, ERROR_WINHTTP_AUTODETECTION_FAILED) == FOUND
    assert ERROR_WINHTTP_AUTODETECTION_FAILED == 12180
    assert outcome_of(False, ERROR_WINHTTP_AUTODETECTION_FAILED) == NONE
    for error in (
        0,
        5,
        12002,
        12007,
        12166,
        12167,
        12181,
    ):  # timeout, name not resolved, PAC errors ...
        assert outcome_of(False, error) == UNKNOWN


# ---------------------------------------------------------------------------------- the real probe, faked WinHTTP
def make_probe(timeout_s: float = 5.0) -> tuple[WindowsWpadProbe, MagicMock, MagicMock]:
    probe = object.__new__(WindowsWpadProbe)  # the guard-protected __init__ is never run
    probe._timeout_s = timeout_s
    probe._winhttp = MagicMock()
    probe._kernel32 = MagicMock()
    return probe, probe._winhttp, probe._kernel32


def test_the_real_probe_asks_auto_detect_dhcp_and_dns_for_chatgpt_with_no_proxy_session():
    probe, winhttp, _k = make_probe()
    seen: dict[str, Any] = {}

    def get_proxy(session, url, options, info):
        seen["url"] = url
        seen["flags"] = options._obj.dwFlags
        seen["detect"] = options._obj.dwAutoDetectFlags
        ctypes.set_last_error(ERROR_WINHTTP_AUTODETECTION_FAILED)
        return 0

    winhttp.WinHttpOpen.return_value = 1234
    winhttp.WinHttpGetProxyForUrl.side_effect = get_proxy
    assert probe.probe() == NONE
    assert seen == {"url": "https://chatgpt.com/", "flags": 0x1, "detect": 0x3}
    assert winhttp.WinHttpOpen.call_args.args[1] == 1  # WINHTTP_ACCESS_TYPE_NO_PROXY
    winhttp.WinHttpSetTimeouts.assert_called_once()
    winhttp.WinHttpCloseHandle.assert_called_once_with(1234)


def test_a_discovered_pac_is_found_and_the_strings_winhttp_allocated_are_freed():
    probe, winhttp, kernel = make_probe()
    winhttp.WinHttpOpen.return_value = 7

    def get_proxy(session, url, options, info):
        info._obj.lpszProxy = 0x1000
        info._obj.lpszProxyBypass = 0x2000
        return 1

    winhttp.WinHttpGetProxyForUrl.side_effect = get_proxy
    assert probe.probe() == FOUND
    assert [c.args[0] for c in kernel.GlobalFree.call_args_list] == [0x1000, 0x2000]
    winhttp.WinHttpCloseHandle.assert_called_once_with(7)


def test_any_other_winhttp_error_or_a_failed_session_is_unknown():
    probe, winhttp, _k = make_probe()
    winhttp.WinHttpOpen.return_value = 7

    def timed_out(session, url, options, info):
        ctypes.set_last_error(12002)  # ERROR_WINHTTP_TIMEOUT
        return 0

    winhttp.WinHttpGetProxyForUrl.side_effect = timed_out
    assert probe.probe() == UNKNOWN
    winhttp.WinHttpOpen.return_value = 0  # no session at all
    winhttp.WinHttpCloseHandle.reset_mock()
    assert probe.probe() == UNKNOWN
    winhttp.WinHttpCloseHandle.assert_not_called()


def test_an_exception_inside_the_probe_is_unknown_not_raised(monkeypatch):
    probe, winhttp, _k = make_probe()
    winhttp.WinHttpOpen.side_effect = OSError("dll gone")
    assert probe.probe() == UNKNOWN


def test_a_probe_that_hangs_is_unknown_after_the_timeout():
    probe, _w, _k = make_probe(timeout_s=0.2)
    release = []

    def hang() -> str:
        while not release:
            time.sleep(0.01)
        return NONE

    probe._once = hang  # type: ignore[method-assign]
    started = time.monotonic()
    try:
        assert probe.probe() == UNKNOWN
        assert time.monotonic() - started < 2.0
    finally:
        release.append(1)  # let the daemon worker end


def test_the_timeout_constants_respect_the_five_second_limit():
    assert wpad.PROBE_TIMEOUT_S <= 5.0
    assert wpad.WINHTTP_TIMEOUT_MS / 1000 < wpad.PROBE_TIMEOUT_S


def test_the_real_probe_cannot_be_built_under_pytest():
    from client_gateway.routing.guard import RealSystemForbidden

    with pytest.raises(RealSystemForbidden):
        WindowsWpadProbe()
