"""Is this PC managed by a company? (spec §8.3, phase 4b.) READ-ONLY.

A new root certificate on a device that is enrolled in Intune, or that runs Defender for Endpoint, can
raise an alert or be removed again by policy, so ``setup`` tells the user to clear the certificate with
IT first and continues only with ``--managed-ok``.

``ManagedProbe`` is the interface (``reasons``: fixed codes, empty = not managed as far as can be seen).
``MemoryManagedProbe`` is the fake every test uses. ``WindowsManagedProbe`` is the real one: it READS
``HKLM\\SOFTWARE\\Microsoft\\Enrollments`` (an enrollment whose ``ProviderID`` is ``MS DM Server``) and
asks the service manager whether the Defender for Endpoint service (``Sense``) runs. It never writes.
Building it calls ``guard.require_real_system`` FIRST, like the other two real backends (rule R8).
"""

from __future__ import annotations

import os
from typing import Protocol

from client_gateway.routing.guard import require_real_system

INTUNE = "intune_enrollment"
DEFENDER = "defender_for_endpoint"
ENROLLMENTS_KEY = r"SOFTWARE\Microsoft\Enrollments"
INTUNE_PROVIDER = "MS DM Server"
DEFENDER_SERVICE = "Sense"


class ManagedProbe(Protocol):
    def reasons(self) -> list[str]:
        """Fixed codes (``intune_enrollment``, ``defender_for_endpoint``) of what says this PC is
        managed; an empty list when nothing does."""


class MemoryManagedProbe:
    """A fixed answer: nothing outside this object is read."""

    def __init__(self, reasons: tuple[str, ...] = ()) -> None:
        self._reasons = list(reasons)
        self.calls = 0

    def reasons(self) -> list[str]:
        self.calls += 1
        return list(self._reasons)


class WindowsManagedProbe:
    """Registry READ and service query. REAL: refused unless ``guard`` allows it."""

    def __init__(self) -> None:
        require_real_system("managed-device check")  # before winreg / psutil are imported
        if os.name != "nt":
            raise RuntimeError("managed-device check: Windows only")
        import winreg  # noqa: PLC0415

        self._winreg = winreg

    def _intune(self) -> bool:
        w = self._winreg
        try:
            with w.OpenKey(
                w.HKEY_LOCAL_MACHINE, ENROLLMENTS_KEY, 0, w.KEY_READ | w.KEY_WOW64_64KEY
            ) as root:
                index = 0
                while True:
                    try:
                        name = w.EnumKey(root, index)
                    except OSError:
                        return False
                    index += 1
                    try:
                        with w.OpenKey(root, name, 0, w.KEY_READ | w.KEY_WOW64_64KEY) as sub:
                            provider, _kind = w.QueryValueEx(sub, "ProviderID")
                    except OSError:
                        continue
                    if str(provider) == INTUNE_PROVIDER:
                        return True
        except OSError:
            return False

    @staticmethod
    def _defender() -> bool:
        try:
            import psutil  # noqa: PLC0415

            return str(psutil.win_service_get(DEFENDER_SERVICE).status()) == "running"
        except Exception:  # noqa: BLE001 - no such service, or no permission: not seen running
            return False

    def reasons(self) -> list[str]:
        out: list[str] = []
        if self._intune():
            out.append(INTUNE)
        if self._defender():
            out.append(DEFENDER)
        return out
