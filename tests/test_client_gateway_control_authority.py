"""Control client side of the masking authority: ``mask_texts`` / ``unmask_texts`` / ``mask_image``.

The HTTP layer is replaced by a fake ``request`` (the real one is only exercised for the token
header, with ``urlopen`` replaced): nothing here opens a socket.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.request
from typing import Any

import pytest

from client_gateway import control_client as cc

PNG = b"\x89PNG\r\n\x1a\nfake image bytes"


class FakeRequest:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method, path, body=None, headers=None, timeout=3.0, base=None):
        self.calls.append(
            {"method": method, "path": path, "body": body, "timeout": timeout, "headers": headers}
        )
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


@pytest.fixture()
def fake(monkeypatch):
    def install(reply: Any) -> FakeRequest:
        fr = FakeRequest(reply)
        monkeypatch.setattr(cc, "request", fr)
        return fr

    return install


# ---------------------------------------------------------------------------------- mask_texts
def test_mask_texts_posts_texts_and_returns_the_masked_list(fake):
    fr = fake((200, {"ok": True, "texts": ["CUS_10001 x", "y"], "masked": 1}))
    assert cc.mask_texts(["Zorvex x", "y"]) == ["CUS_10001 x", "y"]
    (call,) = fr.calls
    assert call["method"] == "POST"
    assert call["path"] == "/__gateway/mask"
    assert call["body"] == {"texts": ["Zorvex x", "y"], "whole": True}


def test_mask_texts_passes_whole_false_and_the_timeout(fake):
    fr = fake((200, {"ok": True, "texts": ["a"], "masked": 0}))
    cc.mask_texts(["a"], whole=False, timeout=12.5)
    assert fr.calls[0]["body"]["whole"] is False
    assert fr.calls[0]["timeout"] == 12.5


def test_mask_texts_has_a_generous_default_timeout(fake):
    fr = fake((200, {"ok": True, "texts": ["a"], "masked": 0}))
    cc.mask_texts(["a"])
    assert fr.calls[0]["timeout"] >= 30.0  # the gateway waits up to 30 s for its dictionary


def test_mask_texts_of_nothing_needs_no_call(fake):
    fr = fake(None)
    assert cc.mask_texts([]) == []
    assert fr.calls == []


@pytest.mark.parametrize(
    "reply",
    [
        None,  # gateway not reachable
        (503, {"ok": False, "error": "not_ready"}),
        (503, {"ok": False, "error": "mask_failed"}),
        (403, {"ok": False, "error": "token_required"}),
        (500, {}),
        (200, {}),
        (200, {"ok": False, "texts": ["a"]}),
        (200, {"ok": True}),
        (200, {"ok": True, "texts": "not a list"}),
        (200, {"ok": True, "texts": ["only one"]}),  # two sent: never a partial list
        (200, {"ok": True, "texts": ["a", 5]}),
        (200, {"ok": True, "texts": ["a", None]}),
    ],
)
def test_mask_texts_is_none_unless_a_complete_200(fake, reply):
    fake(reply)
    assert cc.mask_texts(["a", "b"]) is None


def test_mask_texts_never_raises(fake):
    fake(RuntimeError("socket exploded"))
    assert cc.mask_texts(["a"]) is None


def test_mask_texts_never_raises_on_unserialisable_input(fake):
    fake((200, {"ok": True, "texts": [], "masked": 0}))
    assert cc.mask_texts([object()]) is None  # type: ignore[list-item]


# ---------------------------------------------------------------------------------- unmask_texts
def test_unmask_texts_round_trip(fake):
    fr = fake((200, {"ok": True, "texts": ["Zorvex x"], "unmasked": 1}))
    assert cc.unmask_texts(["CUS_10001 x"]) == ["Zorvex x"]
    (call,) = fr.calls
    assert call["method"] == "POST"
    assert call["path"] == "/__gateway/unmask"
    assert call["body"] == {"texts": ["CUS_10001 x"]}
    assert call["timeout"] > 0


def test_unmask_texts_of_nothing_needs_no_call(fake):
    fr = fake(None)
    assert cc.unmask_texts([]) == []
    assert fr.calls == []


@pytest.mark.parametrize(
    "reply",
    [
        None,
        (503, {"ok": False, "error": "unmask_failed"}),
        (403, {"ok": False, "error": "token_required"}),
        (200, {"ok": True, "texts": ["a", "b"]}),
        (200, {"ok": False, "texts": ["a"]}),
        (200, {"ok": True, "texts": [1]}),
    ],
)
def test_unmask_texts_is_none_unless_a_complete_200(fake, reply):
    fake(reply)
    assert cc.unmask_texts(["a"]) is None


def test_unmask_texts_never_raises(fake):
    fake(OSError("gone"))
    assert cc.unmask_texts(["a"]) is None


# ---------------------------------------------------------------------------------- mask_image
def test_mask_image_posts_base64_and_returns_bytes_and_action(fake):
    redacted = b"\x89PNG redacted"
    fr = fake(
        (200, {"ok": True, "png_b64": base64.b64encode(redacted).decode(), "action": "redacted"})
    )
    assert cc.mask_image(PNG) == (redacted, "redacted")
    (call,) = fr.calls
    assert call["method"] == "POST"
    assert call["path"] == "/__gateway/mask_image"
    assert base64.b64decode(call["body"]["png_b64"]) == PNG
    assert call["timeout"] >= 30.0


def test_mask_image_clean(fake):
    fake((200, {"ok": True, "png_b64": base64.b64encode(PNG).decode(), "action": "clean"}))
    assert cc.mask_image(PNG, timeout=5.0) == (PNG, "clean")


@pytest.mark.parametrize(
    "reply",
    [
        None,
        (503, {"ok": False, "error": "mask_failed"}),
        (503, {"ok": False, "error": "mask_failed", "reason": "no OCR engine available"}),
        (400, {"ok": False, "error": "bad_request"}),
        (200, {}),
        (200, {"ok": False, "png_b64": "AAAA", "action": "clean"}),
        (200, {"ok": True, "png_b64": "AAAA"}),  # no action
        (200, {"ok": True, "png_b64": "AAAA", "action": "weird"}),
        (200, {"ok": True, "png_b64": 5, "action": "clean"}),
        (200, {"ok": True, "png_b64": "%%% not base64", "action": "clean"}),
        (200, {"ok": True, "png_b64": "", "action": "clean"}),
    ],
)
def test_mask_image_is_none_unless_a_valid_200(fake, reply):
    fake(reply)
    assert cc.mask_image(PNG) is None


def test_mask_image_never_raises(fake):
    fake(RuntimeError("boom"))
    assert cc.mask_image(PNG) is None
    assert cc.mask_image("not bytes") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------- the token
class _Resp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._raw = json.dumps(payload).encode()
        self.status = status

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


@pytest.mark.parametrize("route", ["mask", "unmask", "mask_image"])
def test_the_real_request_sends_the_control_token_on_authority_paths(monkeypatch, tmp_path, route):
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path))
    (tmp_path / "gateway.token").write_text("tok" * 15, encoding="ascii")
    seen: list[urllib.request.Request] = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return _Resp({"ok": True, "texts": ["a"], "png_b64": "AAAA", "action": "clean"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    reply = cc.request("POST", f"/__gateway/{route}", {"x": 1})
    assert reply is not None
    assert seen[0].get_header("X-gateway-token") == "tok" * 15
    assert seen[0].get_header("Content-type") == "application/json"
    assert io.BytesIO(seen[0].data).read() == b'{"x": 1}'


def test_mask_texts_end_to_end_with_a_fake_urlopen_sends_token_and_texts(monkeypatch, tmp_path):
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path))
    (tmp_path / "gateway.token").write_text("s" * 40, encoding="ascii")
    seen: list[urllib.request.Request] = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        payload = json.loads(req.data)
        return _Resp({"ok": True, "texts": [t.upper() for t in payload["texts"]], "masked": 0})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert cc.mask_texts(["abc", "dé"]) == ["ABC", "DÉ"]
    assert seen[0].get_header("X-gateway-token") == "s" * 40
    assert seen[0].full_url.endswith("/__gateway/mask")


def test_without_a_token_file_the_gateway_answers_403_and_the_result_is_none(monkeypatch, tmp_path):
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path))  # no gateway.token there
    seen: list[urllib.request.Request] = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        import urllib.error

        raise urllib.error.HTTPError(
            req.full_url, 403, "forbidden", {}, io.BytesIO(b'{"ok":false,"error":"token_required"}')  # type: ignore[arg-type]
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert cc.mask_texts(["a"]) is None
    assert seen[0].get_header("X-gateway-token") is None


def test_unreachable_gateway_is_none_through_the_real_request(monkeypatch, tmp_path):
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path))

    def refuse(req, timeout=None):
        raise ConnectionRefusedError

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert cc.mask_texts(["a"]) is None
    assert cc.unmask_texts(["a"]) is None
    assert cc.mask_image(PNG) is None
