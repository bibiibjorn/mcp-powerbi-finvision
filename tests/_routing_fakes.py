"""Fakes for the routing tests (not a test module): a DPAPI stand-in, an in-memory certificate store, an
in-memory Internet Settings backend. Nothing here touches the real system (rule R8)."""

from __future__ import annotations

import base64
from typing import Any


def fake_crypto() -> tuple[Any, Any]:
    """``(protect, unprotect)`` with the call shapes of ``win32crypt.CryptProtectData`` /
    ``CryptUnprotectData``: reversible, and the output is visibly not the input."""

    def protect(
        plain: bytes, description: str, entropy: Any, reserved: Any, prompt: Any, flags: int
    ) -> bytes:
        return b"FAKE-DPAPI:" + base64.b64encode(plain[::-1])

    def unprotect(
        blob: bytes, entropy: Any, reserved: Any, prompt: Any, flags: int
    ) -> tuple[str, bytes]:
        if not blob.startswith(b"FAKE-DPAPI:"):
            raise ValueError("not a fake DPAPI blob")
        return "desc", base64.b64decode(blob[len(b"FAKE-DPAPI:") :])[::-1]

    return protect, unprotect
