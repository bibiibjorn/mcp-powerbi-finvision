"""A fake masking gateway for the MCP masking-layer tests (not a test module).

Patches the three ``client_gateway.control_client`` functions the layer calls, so no test can reach
a real gateway: values in ``PAIRS`` become their pseudonym on the way out and are restored on the
way in.
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

    @property
    def calls(self) -> int:
        return len(self.mask_calls) + len(self.unmask_calls) + len(self.image_calls)

    def mask_texts(self, texts, whole: bool = True, timeout: float = 0) -> Optional[List[str]]:
        self.mask_calls.append(list(texts))
        if self.raise_on_mask:
            raise RuntimeError("boom Zorblax Holdings NV")
        if not self.reachable:
            return None
        out = []
        for text in texts:
            for real, token in PAIRS.items():
                text = text.replace(real, token)
            out.append(text)
        return out

    def unmask_texts(self, texts, timeout: float = 0) -> Optional[List[str]]:
        self.unmask_calls.append(list(texts))
        if not self.reachable:
            return None
        out = []
        for text in texts:
            for real, token in PAIRS.items():
                text = text.replace(token, real)
            out.append(text)
        return out

    def mask_image(self, png: bytes, timeout: float = 0) -> Optional[Tuple[bytes, str]]:
        self.image_calls.append(bytes(png))
        if not self.reachable or self.image_action is None:
            return None
        if self.image_action == "clean":
            return bytes(png), "clean"
        return b"REDACTED:" + bytes(png), "redacted"


def install(monkeypatch: Any) -> FakeGateway:
    """Enable the layer (env flag) and route it to a fresh fake gateway."""
    from client_gateway import control_client

    gw = FakeGateway()
    monkeypatch.setenv("FINVISION_MCP_MASKING", "1")
    monkeypatch.setattr(control_client, "mask_texts", gw.mask_texts)
    monkeypatch.setattr(control_client, "unmask_texts", gw.unmask_texts)
    monkeypatch.setattr(control_client, "mask_image", gw.mask_image)
    from server import masking_layer

    monkeypatch.setattr(masking_layer, "_chat_masked", lambda: True)  # this chat: masking on
    return gw


def forbid_gateway(monkeypatch: Any) -> Dict[str, int]:
    """Every control_client call counts (and fails the test through the returned counter)."""
    from client_gateway import control_client

    counter = {"calls": 0}

    def _boom(*_a: Any, **_k: Any) -> None:
        counter["calls"] += 1
        raise AssertionError("the gateway must not be called while masking is off")

    for name in ("mask_texts", "unmask_texts", "mask_image", "request"):
        monkeypatch.setattr(control_client, name, _boom)
    return counter
