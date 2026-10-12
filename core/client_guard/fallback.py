"""In-process best-effort masking, for the MCP layer when the masking gateway cannot be used.

Fail mode ``degrade`` (``client_gateway.fail_mode``, the default) never lets a masking problem make
a Power BI MCP call fail. When the gateway cannot be reached or does not answer in time, the layer
masks the result HERE with what can run without it: the value SHAPES of the active profile (e-mail
address, IBAN, phone number, and the country packs: Belgian national / enterprise number), with the
same keyed pseudonyms as the gateway (``<home>/order.key``). The model dictionary, the remembered
values, the client list and name recognition live in the gateway process and are NOT available
here, so this is a floor, not the real thing: the layer reports the result as degraded.

Nothing here keeps a value: the map holds only the shape values it met in this process (memory
only), and a failure is a fixed code, never a message that could carry text.
"""

from __future__ import annotations

import threading

from core.client_guard.engine import SessionMap
from core.client_guard.home import home_dir

_lock = threading.Lock()
_map: SessionMap | None = None


def _build() -> SessionMap:
    """A map without dictionary values and without recognizers (no model is ever loaded in the
    MCP process): the shape detectors only. The pseudonym numbers are keyed like the gateway's."""
    from core.client_guard.tokens import ephemeral_token_fn, stable_token_fn

    try:
        token_fn = stable_token_fn(home_dir(), 0)
    except Exception:  # noqa: BLE001 - never block on the key file
        token_fn = ephemeral_token_fn()
    return SessionMap(token_fn=token_fn, recognizers=[], fuzzy=False)


def _get() -> SessionMap:
    global _map
    with _lock:
        if _map is None:
            _map = _build()
        return _map


def reset() -> None:
    """Drop the process map (tests)."""
    global _map
    with _lock:
        _map = None


def mask_texts(texts: list[str], whole: bool = True) -> tuple[list[str], bool]:
    """``(texts, ok)``: every text with its value shapes pseudonymised. ``ok`` is False when the
    in-process engine failed; the texts are then returned as they were (the caller reports it)."""
    try:
        smap = _get()
        return [smap.mask_text(t, whole)[0] if t else t for t in texts], True
    except Exception:  # noqa: BLE001 - a fixed outcome, never the message
        return list(texts), False
