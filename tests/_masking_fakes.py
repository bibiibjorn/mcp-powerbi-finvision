"""A fake masking gateway for the MCP masking-layer tests (not a test module).

Patches the ``client_gateway.control_client`` functions the layer calls (the reason-returning
``*_ex`` variants and the plain ones), so no test can reach a real gateway: values in ``PAIRS``
become their pseudonym on the way out and are restored on the way in.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

PAIRS = {
    "Zorblax Holdings NV": "CUS_48213",
    "Quentin Testermans": "PER_90417",
}


class FakeGateway:
    def __init__(self) -> None:
        self.reachable = True
        self.mask_calls: List[List[str]] = []
        self.unmask_calls: List[List[str]] = []
        self.image_calls: List[bytes] = []
        self.image_action: Optional[str] = "redacted"  # None = the gateway refuses the image
        self.raise_on_mask = False
        self.reason = "unreachable"  # the code of a failed call (``reachable`` False)
        self.fail_first = 0  # the first N mask / unmask calls fail with ``reason``, then work
        self.degraded: Optional[Dict[str, Any]] = None  # the ``degraded`` note of a 200 reply
        self.running = False  # health answers although the calls fail (``reachable`` False)

    def health_ok(self) -> bool:
        return self.reachable or self.running

    @property
    def calls(self) -> int:
        return len(self.mask_calls) + len(self.unmask_calls) + len(self.image_calls)

    def _failing(self, calls: int) -> bool:
        return not self.reachable or calls <= self.fail_first

    def mask_texts_ex(
        self, texts, whole: bool = True, timeout: float = 0
    ) -> Tuple[Optional[List[str]], str, Dict[str, Any]]:
        self.mask_calls.append(list(texts))
        if self.raise_on_mask:
            raise RuntimeError("boom Zorblax Holdings NV")
        if self._failing(len(self.mask_calls)):
            return None, self.reason, {}
        out = []
        for text in texts:
            for real, token in PAIRS.items():
                text = text.replace(real, token)
            out.append(text)
        return out, "", dict(self.degraded or {})

    def unmask_texts_ex(
        self, texts, timeout: float = 0
    ) -> Tuple[Optional[List[str]], str, Dict[str, Any]]:
        self.unmask_calls.append(list(texts))
        if self._failing(len(self.unmask_calls)):
            return None, self.reason, {}
        out = []
        for text in texts:
            for real, token in PAIRS.items():
                text = text.replace(token, real)
            out.append(text)
        return out, "", {}

    def mask_image_ex(
        self, png: bytes, timeout: float = 0
    ) -> Tuple[Optional[Tuple[bytes, str]], str, Dict[str, Any]]:
        self.image_calls.append(bytes(png))
        if not self.reachable or self.image_action is None:
            return None, "mask_failed", {}
        extra = dict(self.degraded or {})
        if self.image_action == "clean":
            return (bytes(png), "clean"), "", extra
        return (b"REDACTED:" + bytes(png), "redacted"), "", extra

    def mask_texts(self, texts, whole: bool = True, timeout: float = 0) -> Optional[List[str]]:
        return self.mask_texts_ex(texts, whole, timeout)[0]

    def unmask_texts(self, texts, timeout: float = 0) -> Optional[List[str]]:
        return self.unmask_texts_ex(texts, timeout)[0]

    def mask_image(self, png: bytes, timeout: float = 0) -> Optional[Tuple[bytes, str]]:
        return self.mask_image_ex(png, timeout)[0]


def install(monkeypatch: Any, fail_mode: str = "closed") -> FakeGateway:
    """Enable the layer (env flag) and route it to a fresh fake gateway. ``fail_mode`` pins
    ``client_gateway.fail_mode``: the tests of the strict behaviour use the default ``closed``, the
    degrade tests pass ``"degrade"`` (the shipped default). ``health`` answers while the fake is
    reachable or ``running`` (the layer asks it after a failed call)."""
    from client_gateway import control_client

    gw = FakeGateway()
    monkeypatch.setenv("FINVISION_MCP_MASKING", "1")
    for name in ("mask_texts", "unmask_texts", "mask_image"):
        monkeypatch.setattr(control_client, name, getattr(gw, name))
        monkeypatch.setattr(control_client, name + "_ex", getattr(gw, name + "_ex"))
    monkeypatch.setattr(control_client, "fail_mode", lambda: fail_mode)
    monkeypatch.setattr(
        control_client, "health", lambda timeout=1.0: {"ok": True} if gw.health_ok() else None
    )
    from server import masking_layer

    monkeypatch.setattr(masking_layer, "_chat_masked", lambda: True)  # this chat: masking on
    return gw


def pin_fail_mode(monkeypatch: Any, mode: str) -> None:
    """Pin config ``client_gateway.fail_mode`` for every reader (gateway, front end, MCP layer):
    the tests of the strict behaviour pin ``closed``, the shipped default is ``degrade``."""
    from core.config.config_manager import config

    real_get = config.get
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: (
            mode if key == "client_gateway.fail_mode" else real_get(key, default)
        ),
    )


def forbid_gateway(monkeypatch: Any) -> Dict[str, int]:
    """Every control_client call counts (and fails the test through the returned counter)."""
    from client_gateway import control_client

    counter = {"calls": 0}

    def _boom(*_a: Any, **_k: Any) -> None:
        counter["calls"] += 1
        raise AssertionError("the gateway must not be called while masking is off")

    for name in (
        "mask_texts",
        "unmask_texts",
        "mask_image",
        "mask_texts_ex",
        "unmask_texts_ex",
        "mask_image_ex",
        "request",
    ):
        monkeypatch.setattr(control_client, name, _boom)
    return counter
