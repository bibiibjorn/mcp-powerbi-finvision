"""The per-user Internet Settings backend: where Windows apps learn about the PAC file (spec §8.2, R7, R8).

``InternetSettingsBackend`` is the interface (``read`` / ``apply_pac`` / ``restore`` / ``notify``).
``MemoryInternetSettings`` is the fake every test and dry run uses. ``WindowsInternetSettings`` is the
real one: ``winreg`` on ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings`` only,
plus WinINet's ``INTERNET_OPTION_SETTINGS_CHANGED`` / ``INTERNET_OPTION_REFRESH`` for ``notify``.
Building it calls ``guard.require_real_system`` FIRST (see ``client_gateway.routing.guard``).

Only five values are ever read, written or deleted: ``ProxyEnable``, ``ProxyServer``,
``ProxyOverride``, ``AutoConfigURL``, ``AutoDetect``. ``read`` also reports whether a policy locks the
settings (``locked``: the policy key ``HKCU`` / ``HKLM`` ``Software\\Policies\\Microsoft\\Windows\\
CurrentVersion\\Internet Settings`` incl. ``ProxySettingsPerUser`` = 0 = machine-wide settings, the
Internet Explorer "disable changing proxy settings" policies): routing is refused then.

``InternetSettings`` is the only caller of a backend. It takes a backup of the five values ONCE, before
the first change (``<home>/routing/proxy_backup.json``), and ``restore`` puts back exactly the saved
values: a value that did not exist is deleted again, a value that existed gets its old type and data.
The backup file is removed only after the restore read back identical.

Failures raise ``ProxyError(code)`` with a fixed code (``locked``, ``backup_exists_unrelated``,
``restore_mismatch``, ``backend_failed``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from client_gateway.routing.ca import ROUTING_DIR
from client_gateway.routing.guard import require_real_system

BACKUP_FILE = "proxy_backup.json"
SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
POLICY_KEY = r"Software\Policies\Microsoft\Windows\CurrentVersion\Internet Settings"
IE_POLICY_KEY = r"Software\Policies\Microsoft\Internet Explorer\Control Panel"
REG_SZ = 1
REG_DWORD = 4
VALUE_NAMES = ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL", "AutoDetect")
_TYPES = {
    "ProxyEnable": REG_DWORD,
    "ProxyServer": REG_SZ,
    "ProxyOverride": REG_SZ,
    "AutoConfigURL": REG_SZ,
    "AutoDetect": REG_DWORD,
}
# policy values (any of them present in the policy key = that part is set by an administrator)
_POLICY_VALUES = (*VALUE_NAMES, "ProxySettingsPerUser")
_IE_POLICY_VALUES = ("Proxy", "AutoConfig", "Autoconfig", "AutoDetect")


class ProxyError(Exception):
    """An Internet Settings step failed. The message is a fixed code."""


@dataclass(frozen=True)
class RegValue:
    """One registry value: its type (``REG_SZ`` / ``REG_DWORD``) and data."""

    type: int
    data: str | int


@dataclass(frozen=True)
class ProxySettings:
    """The five values (``None`` = the value does not exist) and the policy locks that were found."""

    values: Mapping[str, RegValue | None] = field(
        default_factory=lambda: dict.fromkeys(VALUE_NAMES)
    )
    locked: tuple[str, ...] = ()  # fixed labels of the policies that lock the settings

    def _get(self, name: str) -> str | int | None:
        value = self.values.get(name)
        return value.data if value is not None else None

    @property
    def proxy_enable(self) -> int | None:
        data = self._get("ProxyEnable")
        return int(data) if isinstance(data, int) else None

    @property
    def proxy_server(self) -> str | None:
        data = self._get("ProxyServer")
        return str(data) if isinstance(data, str) and data else None

    @property
    def proxy_override(self) -> str | None:
        data = self._get("ProxyOverride")
        return str(data) if isinstance(data, str) and data else None

    @property
    def auto_config_url(self) -> str | None:
        data = self._get("AutoConfigURL")
        return str(data) if isinstance(data, str) and data else None

    @property
    def auto_detect(self) -> int | None:
        data = self._get("AutoDetect")
        return int(data) if isinstance(data, int) else None

    def same_values(self, other: "ProxySettings") -> bool:
        return all(self.values.get(n) == other.values.get(n) for n in VALUE_NAMES)

    # -------------------------------------------------------------- json (the backup file)
    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "values": {
                n: ({"type": v.type, "data": v.data} if v is not None else None)
                for n, v in ((n, self.values.get(n)) for n in VALUE_NAMES)
            },
        }

    @classmethod
    def from_json(cls, data: Any) -> "ProxySettings":
        raw = data.get("values") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            raise ProxyError("backup_unreadable")
        values: dict[str, RegValue | None] = {}
        for name in VALUE_NAMES:
            item = raw.get(name)
            if item is None:
                values[name] = None
                continue
            if not isinstance(item, dict):
                raise ProxyError("backup_unreadable")
            kind, payload = item.get("type"), item.get("data")
            if kind not in (REG_SZ, REG_DWORD) or kind != _TYPES[name]:
                raise ProxyError("backup_unreadable")
            if not isinstance(payload, str if kind == REG_SZ else int) or isinstance(payload, bool):
                raise ProxyError("backup_unreadable")
            values[name] = RegValue(kind, payload)
        return cls(values=values)


class InternetSettingsBackend(Protocol):
    def read(self) -> ProxySettings:
        """The five values and the policy locks."""

    def apply_pac(self, url: str, disable_autodetect: bool = False) -> None:
        """Set ``AutoConfigURL`` to ``url`` (and ``AutoDetect`` to 0 when asked). Nothing else."""

    def restore(self, saved: ProxySettings) -> None:
        """Put back exactly the five saved values (delete the ones that did not exist)."""

    def notify(self) -> None:
        """Tell running apps the settings changed (WinINet settings-changed + refresh)."""


# ------------------------------------------------------------------------------------ the fake
class MemoryInternetSettings:
    """A dict standing in for the registry: nothing outside this object changes."""

    def __init__(
        self,
        values: Mapping[str, RegValue | None] | None = None,
        locked: tuple[str, ...] = (),
    ) -> None:
        self.values: dict[str, RegValue | None] = dict.fromkeys(VALUE_NAMES)
        self.values.update(values or {})
        self.locked = locked
        self.notified = 0
        self.writes: list[tuple[str, str]] = []  # (operation, detail) for the tests
        self.fail_restore = False

    @classmethod
    def with_values(
        cls,
        *,
        enable: int | None = None,
        server: str | None = None,
        override: str | None = None,
        pac: str | None = None,
        detect: int | None = None,
        locked: tuple[str, ...] = (),
    ) -> "MemoryInternetSettings":
        values: dict[str, RegValue | None] = {
            "ProxyEnable": None if enable is None else RegValue(REG_DWORD, enable),
            "ProxyServer": None if server is None else RegValue(REG_SZ, server),
            "ProxyOverride": None if override is None else RegValue(REG_SZ, override),
            "AutoConfigURL": None if pac is None else RegValue(REG_SZ, pac),
            "AutoDetect": None if detect is None else RegValue(REG_DWORD, detect),
        }
        return cls(values, locked)

    def read(self) -> ProxySettings:
        return ProxySettings(values=dict(self.values), locked=self.locked)

    def apply_pac(self, url: str, disable_autodetect: bool = False) -> None:
        self.writes.append(("apply_pac", url))
        self.values["AutoConfigURL"] = RegValue(REG_SZ, url)
        if disable_autodetect:
            self.values["AutoDetect"] = RegValue(REG_DWORD, 0)

    def restore(self, saved: ProxySettings) -> None:
        self.writes.append(("restore", ""))
        if self.fail_restore:
            raise ProxyError("backend_failed")
        for name in VALUE_NAMES:
            self.values[name] = saved.values.get(name)

    def notify(self) -> None:
        self.notified += 1


# ------------------------------------------------------------------------------------ the real one
class WindowsInternetSettings:
    """``HKCU`` Internet Settings through ``winreg``. REAL: refused unless ``guard`` allows it."""

    INTERNET_OPTION_REFRESH = 37
    INTERNET_OPTION_SETTINGS_CHANGED = 39

    def __init__(self) -> None:
        require_real_system("Internet Settings (HKCU)")  # before winreg / ctypes are imported
        if os.name != "nt":
            raise ProxyError("backend_failed")
        import winreg  # noqa: PLC0415

        self._winreg = winreg

    # -------------------------------------------------------------- helpers
    def _read_values(self, hive: Any, path: str, names: tuple[str, ...]) -> dict[str, Any]:
        w = self._winreg
        found: dict[str, Any] = {}
        try:
            with w.OpenKey(hive, path, 0, w.KEY_READ) as key:
                for name in names:
                    try:
                        found[name] = w.QueryValueEx(key, name)
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            pass
        except OSError:
            found["__unreadable__"] = (1, 0)  # a key that cannot be read: treated as locked
        return found

    def _locks(self) -> tuple[str, ...]:
        w = self._winreg
        out: list[str] = []
        for hive_name, hive in (("HKCU", w.HKEY_CURRENT_USER), ("HKLM", w.HKEY_LOCAL_MACHINE)):
            policy = self._read_values(hive, POLICY_KEY, _POLICY_VALUES)
            if "__unreadable__" in policy:
                out.append(f"{hive_name}_policy_unreadable")
            if "ProxySettingsPerUser" in policy and policy["ProxySettingsPerUser"][1] == 0:
                out.append("proxy_settings_per_machine")
            if any(n in policy for n in VALUE_NAMES):
                out.append(f"{hive_name}_policy_sets_proxy")
        ie = self._read_values(w.HKEY_CURRENT_USER, IE_POLICY_KEY, _IE_POLICY_VALUES)
        if any(v[1] for n, v in ie.items() if n != "__unreadable__"):
            out.append("ie_policy_locks_proxy")
        return tuple(dict.fromkeys(out))

    # -------------------------------------------------------------- interface
    def read(self) -> ProxySettings:
        w = self._winreg
        raw = self._read_values(w.HKEY_CURRENT_USER, SETTINGS_KEY, VALUE_NAMES)
        values: dict[str, RegValue | None] = {}
        for name in VALUE_NAMES:
            if name in raw:
                data, kind = raw[name]
                values[name] = RegValue(int(kind), data)
            else:
                values[name] = None
        return ProxySettings(values=values, locked=self._locks())

    def _open_for_write(self) -> Any:
        w = self._winreg
        return w.OpenKey(w.HKEY_CURRENT_USER, SETTINGS_KEY, 0, w.KEY_SET_VALUE | w.KEY_QUERY_VALUE)

    def apply_pac(self, url: str, disable_autodetect: bool = False) -> None:
        w = self._winreg
        try:
            with self._open_for_write() as key:
                w.SetValueEx(key, "AutoConfigURL", 0, w.REG_SZ, url)
                if disable_autodetect:
                    w.SetValueEx(key, "AutoDetect", 0, w.REG_DWORD, 0)
        except OSError:
            raise ProxyError("backend_failed") from None

    def restore(self, saved: ProxySettings) -> None:
        w = self._winreg
        try:
            with self._open_for_write() as key:
                for name in VALUE_NAMES:
                    value = saved.values.get(name)
                    if value is None:
                        try:
                            w.DeleteValue(key, name)
                        except FileNotFoundError:
                            pass
                    else:
                        w.SetValueEx(key, name, 0, value.type, value.data)
        except OSError:
            raise ProxyError("backend_failed") from None

    def notify(self) -> None:
        import ctypes  # noqa: PLC0415

        wininet = ctypes.WinDLL("wininet", use_last_error=True)
        wininet.InternetSetOptionW.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
        ]  # fmt: skip
        wininet.InternetSetOptionW.restype = ctypes.c_int
        for option in (self.INTERNET_OPTION_SETTINGS_CHANGED, self.INTERNET_OPTION_REFRESH):
            wininet.InternetSetOptionW(None, option, None, 0)


# ------------------------------------------------------------------------------------ the manager
class InternetSettings:
    """Enables and removes the PAC rule through a backend; owns the one-time backup."""

    def __init__(self, home: Path, backend: InternetSettingsBackend) -> None:
        self.path = Path(home) / ROUTING_DIR / BACKUP_FILE
        self.backend = backend

    def backup_exists(self) -> bool:
        return self.path.is_file()

    def backup(self) -> ProxySettings | None:
        """The saved settings, or None when there is no backup. ``ProxyError("backup_unreadable")``
        when the file is damaged."""
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ProxyError("backup_unreadable") from None
        return ProxySettings.from_json(data)

    def _write_backup(self, settings: ProxySettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(settings.to_json(), indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def current(self) -> ProxySettings:
        return self.backend.read()

    def enable(self, pac_url: str, disable_autodetect: bool = False) -> ProxySettings:
        """Point ``AutoConfigURL`` at ``pac_url``. Refused (``ProxyError("locked")``) when a policy
        locks the settings. The backup is written once, BEFORE the first change, and is kept while it
        exists (a second ``enable`` after a crash does not back up our own rule). Returns the settings
        that were in force BEFORE routing (the backup), for the PAC's "previous decision"."""
        current = self.backend.read()
        if current.locked:
            raise ProxyError("locked")
        saved = self.backup()
        if saved is None:
            if current.auto_config_url == pac_url:
                raise ProxyError("backup_exists_unrelated")  # our rule, no backup: previous unknown
            self._write_backup(current)
            saved = current
        self.backend.apply_pac(pac_url, disable_autodetect)
        self.backend.notify()
        return saved

    def disable(self) -> dict[str, Any]:
        """Put the backed-up settings back and notify; the backup is deleted only after the
        settings read back identical. No backup = nothing of ours to undo."""
        saved = self.backup()
        if saved is None:
            return {"restored": False, "reason": "no_backup"}
        self.backend.restore(saved)
        self.backend.notify()
        if not self.backend.read().same_values(saved):
            raise ProxyError("restore_mismatch")
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        return {"restored": True}

    def rule_is_ours(self, pac_url_prefix: str) -> bool:
        """Does ``AutoConfigURL`` point at a PAC of ours (``http://127.0.0.1:<port>/proxy.pac``)?"""
        url = self.backend.read().auto_config_url or ""
        return url.startswith(pac_url_prefix)
