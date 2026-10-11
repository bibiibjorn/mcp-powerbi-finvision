"""The guard in front of every REAL system backend (rule R8).

A real backend changes the owner's Windows certificate store, the per-user Internet Settings or calls
WinINet. Building one is refused unless the environment variable ``FINVISION_MASKING_REAL_SYSTEM`` is
exactly ``1``; the variable is set only by ``setup`` / the go-live step in a terminal, never by the
tests, the lab or the build. ``tests/conftest.py`` deletes it for every test, and under pytest
(``PYTEST_CURRENT_TEST`` is set while a test runs) a real backend is refused even when the variable
is somehow present.

Every constructor of a real backend calls ``require_real_system`` FIRST, before it touches ctypes,
``winreg`` or a file; a refusal is a ``RealSystemForbidden`` with a fixed message.
"""

from __future__ import annotations

import os

ENV = "FINVISION_MASKING_REAL_SYSTEM"
UNDER_TEST_ENV = "PYTEST_CURRENT_TEST"


class RealSystemForbidden(RuntimeError):
    """A real system backend was requested where it must not be built."""

    def __init__(self, what: str) -> None:
        super().__init__(
            f"{what}: refused (the real system is only touched by setup, with "
            f"{ENV}=1, never under test)"
        )


def real_system_ok() -> bool:
    """True only outside a test and with ``FINVISION_MASKING_REAL_SYSTEM=1``."""
    return os.environ.get(ENV, "").strip() == "1" and not os.environ.get(UNDER_TEST_ENV)


def require_real_system(what: str) -> None:
    """Raise ``RealSystemForbidden`` unless a real backend may be built."""
    if not real_system_ok():
        raise RealSystemForbidden(what)
