"""The system backends of the routing as one bundle, and the ONE place the real ones are built:
``real_backends()`` (certificate store, Internet Settings, the read-only managed-device and WPAD probes). Every
real constructor asks ``client_gateway.routing.guard`` first, so ``real_backends`` raises ``RealSystemForbidden`` in every
test, lab run and build, and returns the backends only for a gateway that the owner approved
(``client_gateway.routing`` on; the process that starts it sets ``FINVISION_MASKING_REAL_SYSTEM=1`` for
that child only, see ``cli.spawn``). Tests build a ``Backends`` from the fakes instead."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from client_gateway.routing.certstore import CertStoreBackend, WindowsCertStore
from client_gateway.routing.managed import ManagedProbe, WindowsManagedProbe
from client_gateway.routing.wpad import WindowsWpadProbe, WpadProbe
from client_gateway.routing.winproxy import InternetSettingsBackend, WindowsInternetSettings


@dataclass(frozen=True)
class Backends:
    certs: CertStoreBackend
    settings: InternetSettingsBackend
    crypto: Any = None  # ``(protect, unprotect)`` for the CA key; None = DPAPI of the current user
    managed: ManagedProbe | None = None  # the managed-device check (read-only); None = not asked
    wpad: WpadProbe | None = None  # does WPAD find a PAC (read-only); None = not asked = unknown


def real_backends() -> Backends:
    return Backends(
        WindowsCertStore(),
        WindowsInternetSettings(),
        managed=WindowsManagedProbe(),
        wpad=WindowsWpadProbe(),
    )
