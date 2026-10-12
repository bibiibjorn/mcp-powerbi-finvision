"""Does automatic proxy detection (WPAD) find anything on this network? READ-ONLY (spec §8.2).

Windows ships with "Automatically detect settings" ON, so ``AutoDetect=1`` is the normal case. It only
matters when the detection FINDS a PAC (the company proxy owns the routing: setup refuses). When it finds
nothing, auto-detect is effectively direct, so routing can go ahead: the gateway's own PAC (served at
``AutoConfigURL``) is used and ``AutoDetect`` is turned off while the gateway runs (the original value is
in the proxy backup, so ``setup --remove`` restores it exactly).

``WpadProbe.probe()`` answers one of three fixed codes:

* ``found``   a PAC was discovered (DHCP option 252 / DNS ``wpad``): keep refusing;
* ``none``    the detection ran and found nothing (``ERROR_WINHTTP_AUTODETECTION_FAILED``): direct;
* ``unknown`` anything else (any other error, a timeout, no WinHTTP): refuse, fail safe.

``MemoryWpadProbe`` is the fake every test uses. ``WindowsWpadProbe`` is the real one: WinHTTP
``WinHttpGetProxyForUrl`` with ``WINHTTP_AUTOPROXY_AUTO_DETECT`` (DHCP + DNS) for ``https://chatgpt.com/``
through ctypes, in a worker thread with a hard 5 s limit. It changes nothing on the PC (no registry write,
no setting); it only asks the network. Building it calls ``guard.require_real_system`` FIRST (rule R8).
"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from typing import Protocol

from client_gateway.routing.guard import require_real_system

FOUND = "found"
NONE = "none"
UNKNOWN = "unknown"
RESULTS = (FOUND, NONE, UNKNOWN)

PROBE_URL = "https://chatgpt.com/"
PROBE_TIMEOUT_S = 5.0
WINHTTP_TIMEOUT_MS = 4000  # each WinHTTP phase; the worker thread's 5 s join is the hard limit
WINHTTP_ACCESS_TYPE_NO_PROXY = 1
WINHTTP_AUTOPROXY_AUTO_DETECT = 0x00000001
WINHTTP_AUTO_DETECT_TYPE_DHCP = 0x00000001
WINHTTP_AUTO_DETECT_TYPE_DNS_A = 0x00000002
ERROR_WINHTTP_AUTODETECTION_FAILED = 12180


class WpadProbe(Protocol):
    def probe(self) -> str:
        """``found``, ``none`` or ``unknown`` (never raises, never changes anything)."""


class MemoryWpadProbe:
    """A fixed answer: nothing outside this object is read."""

    def __init__(self, result: str = NONE) -> None:
        if result not in RESULTS:
            raise ValueError("bad_wpad_result")
        self.result = result
        self.calls = 0

    def probe(self) -> str:
        self.calls += 1
        return self.result


def wpad_result(settings: object, probe: WpadProbe | None) -> str:
    """The probe answer that matters for ``settings`` (a ``ProxySettings``): ``""`` when
    auto-detect is not on (nothing to probe) or no probe is available, else ``found`` / ``none`` /
    ``unknown`` (a probe that raises or answers anything else is ``unknown``)."""
    if getattr(settings, "auto_detect", None) != 1 or probe is None:
        return ""
    try:
        answer = str(probe.probe())
    except Exception:  # noqa: BLE001 - a probe that fails is not a "none": fail safe
        return UNKNOWN
    return answer if answer in RESULTS else UNKNOWN


def outcome_of(succeeded: bool, error: int) -> str:
    """The probe code for a ``WinHttpGetProxyForUrl`` result."""
    if succeeded:
        return FOUND
    return NONE if error == ERROR_WINHTTP_AUTODETECTION_FAILED else UNKNOWN


class WindowsWpadProbe:
    """WinHTTP auto-detection, asked read-only. REAL: refused unless ``guard`` allows it."""

    def __init__(self) -> None:
        require_real_system("WPAD probe")  # before ctypes loads a DLL
        if os.name != "nt":
            raise RuntimeError("WPAD probe: Windows only")
        self._timeout_s = PROBE_TIMEOUT_S
        self._winhttp = ctypes.WinDLL("winhttp", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _once(self) -> str:
        w = self._winhttp
        w.WinHttpOpen.restype = ctypes.c_void_p
        w.WinHttpOpen.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ]  # fmt: skip
        w.WinHttpSetTimeouts.restype = wintypes.BOOL
        w.WinHttpSetTimeouts.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]  # fmt: skip
        w.WinHttpGetProxyForUrl.restype = wintypes.BOOL
        w.WinHttpGetProxyForUrl.argtypes = [
            ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
        ]  # fmt: skip
        w.WinHttpCloseHandle.restype = wintypes.BOOL
        w.WinHttpCloseHandle.argtypes = [ctypes.c_void_p]
        k = self._kernel32
        k.GlobalFree.restype = ctypes.c_void_p
        k.GlobalFree.argtypes = [ctypes.c_void_p]

        class AutoProxyOptions(ctypes.Structure):
            _fields_ = [
                ("dwFlags", wintypes.DWORD),
                ("dwAutoDetectFlags", wintypes.DWORD),
                ("lpszAutoConfigUrl", ctypes.c_void_p),
                ("lpvReserved", ctypes.c_void_p),
                ("dwReserved", wintypes.DWORD),
                ("fAutoLogonIfChallenged", wintypes.BOOL),
            ]

        class ProxyInfo(ctypes.Structure):
            _fields_ = [
                ("dwAccessType", wintypes.DWORD),
                ("lpszProxy", ctypes.c_void_p),
                ("lpszProxyBypass", ctypes.c_void_p),
            ]

        session = w.WinHttpOpen(
            "Finvision masking WPAD probe", WINHTTP_ACCESS_TYPE_NO_PROXY, None, None, 0
        )
        if not session:
            return UNKNOWN
        try:
            t = WINHTTP_TIMEOUT_MS
            w.WinHttpSetTimeouts(session, t, t, t, t)
            options = AutoProxyOptions()
            options.dwFlags = WINHTTP_AUTOPROXY_AUTO_DETECT
            options.dwAutoDetectFlags = (
                WINHTTP_AUTO_DETECT_TYPE_DHCP | WINHTTP_AUTO_DETECT_TYPE_DNS_A
            )
            info = ProxyInfo()
            ctypes.set_last_error(0)
            ok = bool(
                w.WinHttpGetProxyForUrl(
                    session, PROBE_URL, ctypes.byref(options), ctypes.byref(info)
                )
            )
            error = ctypes.get_last_error()
            if ok:  # the strings WinHTTP allocated are ours to free
                for pointer in (info.lpszProxy, info.lpszProxyBypass):
                    if pointer:
                        k.GlobalFree(pointer)
            return outcome_of(ok, error)
        finally:
            w.WinHttpCloseHandle(session)

    def probe(self) -> str:
        box: list[str] = []

        def work() -> None:
            try:
                box.append(self._once())
            except Exception:  # noqa: BLE001 - any failure is "unknown", never an exception
                box.append(UNKNOWN)

        thread = threading.Thread(target=work, name="wpad-probe", daemon=True)
        thread.start()
        thread.join(self._timeout_s)
        if thread.is_alive() or not box:
            return UNKNOWN
        return box[0] if box[0] in RESULTS else UNKNOWN
