"""Per-app replay self-test of the masking gateway (rule R9). Offline: no gateway, no network.

``python tools/masking_lab/lab.py selftest`` replays every recorded fixture of every named app
(``tests/fixtures/masking/wire/``, synthetic names only) through the CURRENT code and prints one line
per app:

* **masked out**: the recorded request, run through the app's adapter, leaves no synthetic client
  name in any string;
* **rehydrated back**: the recorded reply (every stream, with each name replaced by its pseudonym
  and spread over the recorded event boundaries; the stored conversation of ChatGPT) comes back as
  the recorded text, and no pseudonym stays on screen;
* **leak scan**: the residual scan of the front end (``leakscan.residual_hits``) finds no known
  identity value in the masked body.

Apps: ``claude-code`` (Anthropic Messages through the ANTHROPIC_BASE_URL path; no capture exists
yet, so a small synthetic request and stream stand in), ``copilot`` (Chat Completions and
Responses), ``chatgpt`` (web). A new fixture is picked up by its route; an app update that changes a
wire format shows up here as a failed line. Exit 0 = every app passes, 1 = a check failed (the
fixture and the check are named, never a value), 2 = no fixtures found.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
WIRE = REPO_ROOT / "tests" / "fixtures" / "masking" / "wire"
APPS = ("claude-code", "copilot", "chatgpt")
_APP_OF_ADAPTER = {
    "openai_chat": "copilot",
    "openai_responses": "copilot",
    "chatgpt_web": "chatgpt",
}


@dataclass
class Stage:
    ok: int = 0
    total: int = 0

    def add(self, passed: bool) -> bool:
        self.total += 1
        self.ok += int(passed)
        return passed

    def text(self) -> str:
        return "n/a" if not self.total else f"{self.ok}/{self.total}"


@dataclass
class AppResult:
    name: str
    masked: Stage = field(default_factory=Stage)
    rehydrated: Stage = field(default_factory=Stage)
    leak: Stage = field(default_factory=Stage)
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        stages = (self.masked, self.rehydrated, self.leak)
        return not self.problems and any(s.total for s in stages)

    def line(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        return (
            f"{self.name:<12} {verdict}  masked out {self.masked.text()}  "
            f"rehydrated back {self.rehydrated.text()}  leak scan {self.leak.text()}"
        )


def _swap(text: str, mapping: dict[str, str]) -> str:
    for old, new in mapping.items():
        text = text.replace(old, new)
    return text


class _Replay:
    """The state and helpers of one self-test run (imports are lazy: ``lab.py`` stays light)."""

    def __init__(self, tmp: Path) -> None:
        from client_gateway.adapters.base import MaskContext, Route
        from client_gateway.adapters.registry import adapter_for
        from client_gateway.leakscan import residual_hits
        from tests import _wire_support as w

        self.w, self.MaskContext, self.Route = w, MaskContext, Route
        self.adapter_for, self.residual_hits = adapter_for, residual_hits
        self.state = w.make_state(tmp)
        self.names = w.all_names()
        self.slots: dict[str, Callable[[list[Any]], list[Any]]] = {
            "openai_chat": w.copilot_slots,
            "openai_responses": w.responses_slots,
            "chatgpt_web": w.chatgpt_slots,
        }
        # every pseudonym a fixture may carry (lab session or a name left in clear) -> this state's
        ours = w.lab_tokens_of(self.state)
        self.mapping = {
            **{n: w.token_of(self.state, n) for n in (w.NAME, w.NAME2, w.NAME3)},
            **ours,
        }
        self.tokens = set(self.mapping.values())

    # -- one fixture
    def fixture(self, fx: dict[str, Any], res: AppResult, adapter: Any) -> None:
        req = fx["request"]
        route = self.Route(req["method"], req["host"], req["path"], req.get("content_type", ""))
        name = fx["name"]
        if req["method"] == "POST" and req.get("body") is not None:
            self._request(fx, res, adapter, route)
        response = fx.get("response") or {}
        if "chunks" in response:
            self._stream(fx, res, adapter, route)
        elif response.get("body") is not None:
            self._buffered(fx, res, adapter, route)
        elif req["method"] == "POST" and req.get("body") is None:
            res.problems.append(f"{name}: a POST fixture without a body")

    def _request(self, fx: dict[str, Any], res: AppResult, adapter: Any, route: Any) -> None:
        w = self.w
        body = w.relabel_request(w.body_bytes(fx), self.state)
        ctx = self.MaskContext(self.state, marker=w.MARKER)
        try:
            out = adapter.mask_request(body, ctx, route)
        except Exception as exc:  # noqa: BLE001 - reported by type only, never a value
            res.masked.add(False)
            res.problems.append(f"{fx['name']}: masking failed ({type(exc).__name__})")
            return
        strings = w.strings_of(json.loads(out))
        if not res.masked.add(not any(n in s for s in strings for n in self.names)):
            res.problems.append(f"{fx['name']}: a client name is left in the masked request")
        if not res.leak.add(self.residual_hits(self.state.smap, out) == 0):
            res.problems.append(f"{fx['name']}: the leak scan found a client value")

    def _stream(self, fx: dict[str, Any], res: AppResult, adapter: Any, route: Any) -> None:
        w, response = self.w, fx["response"]
        slots = self.slots[adapter.name]
        chunks = response["chunks"]
        masked, expected = w.mask_stream(chunks, slots, self.mapping)
        masked = [_swap(c, self.mapping) for c in masked]  # whole-object events carry names too
        expected = {fid: _swap(t, w.LAB_TOKENS) for fid, t in expected.items()}
        ctx = self.MaskContext(self.state, marker=None)
        rehydrator = adapter.rehydrator(
            response.get("content_type") or "text/event-stream", None, ctx, route, True
        )
        out = [rehydrator.feed(c.encode()) for c in masked] + [rehydrator.finish()]
        text = b"".join(out).decode("utf-8")
        if expected:
            good = w.stream_text([text], slots) == expected
            good = good and not any(t in text for t in self.tokens)
        else:  # nothing to rehydrate (NDJSON, events without text): every byte passes unchanged
            good = text == "".join(masked)
        good = good and rehydrator.stats["errors"] == 0
        if not res.rehydrated.add(good):
            res.problems.append(f"{fx['name']}: the reply is not rehydrated to the recorded text")

    def _buffered(self, fx: dict[str, Any], res: AppResult, adapter: Any, route: Any) -> None:
        original = fx["response"]["body"]
        masked = _swap(json.dumps(original), {n: self.mapping[n] for n in (self.w.NAME,)})
        ctx = self.MaskContext(self.state, marker=None)
        out = adapter.unmask_response(
            masked.encode(),
            fx["response"].get("content_type") or "application/json",
            None,
            ctx,
            route,
        )
        if not res.rehydrated.add(json.loads(out) == original):
            res.problems.append(f"{fx['name']}: the stored reply is not unmasked to the recording")


def _claude_code(replay: _Replay, res: AppResult) -> None:
    """Anthropic Messages (Claude Code): a synthetic request and stream (no capture exists yet)."""
    from client_gateway.masking import mask_request
    from client_gateway.memo import RemaskMemo
    from client_gateway.sse import SSERehydrator

    w, state = replay.w, replay.state
    token = w.token_of(state, w.NAME)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": f"Summarise {w.NAME} please"}]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [{"type": "text", "text": f"rows: {w.NAME2}"}],
                }
            ],
        },
    ]
    body = json.dumps({"model": "m", "max_tokens": 5, "messages": messages}).encode()
    memo = RemaskMemo()
    try:
        out = mask_request(body, "application/json", state, memo)
    except Exception as exc:  # noqa: BLE001
        res.masked.add(False)
        res.problems.append(f"synthetic request: masking failed ({type(exc).__name__})")
        return
    if not res.masked.add(
        not any(n in s for s in w.strings_of(json.loads(out)) for n in replay.names)
    ):
        res.problems.append("synthetic request: a client name is left in the masked request")
    if not res.leak.add(replay.residual_hits(state.smap, out) == 0):
        res.problems.append("synthetic request: the leak scan found a client value")

    def frame(name: str, data: dict[str, Any]) -> bytes:
        return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()

    def piece(text: str) -> bytes:
        delta = {"type": "text_delta", "text": text}
        return frame(
            "content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}
        )

    msg = {"id": "m1", "type": "message", "role": "assistant", "content": []}
    wire = [
        frame("message_start", {"type": "message_start", "message": msg}),
        frame(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        piece("Customer "),
        piece(token[:3]),
        piece(token[3:] + " is fine."),
        frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
        frame("message_stop", {"type": "message_stop"}),
    ]
    rehydrator = SSERehydrator(state.smap, memo)
    text = b"".join(rehydrator.feed(c) for c in wire) + rehydrator.finish()
    shown = "".join(
        json.loads(line[5:])["delta"].get("text", "")
        for line in text.decode().splitlines()
        if line.startswith("data:") and '"content_block_delta"' in line
    )
    good = shown == f"Customer {w.NAME} is fine." and token not in shown
    if not res.rehydrated.add(good):
        res.problems.append("synthetic stream: the reply is not rehydrated")


def run_selftest(out: Callable[[str], None] = print) -> int:
    """Run the self-test; one line per named app. Returns the exit code (0 pass, 1 fail, 2 none)."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    files = sorted(WIRE.glob("*.json"))
    if not files:
        out(f"selftest: no wire fixtures in {WIRE}")
        return 2
    with tempfile.TemporaryDirectory(prefix="masking-selftest-") as tmp:
        # this run never reads or writes the real masking home, never loads the NER model and is
        # never routed (the environment is restored when it ends)
        env = {
            "FINVISION_MASKING_HOME": str(Path(tmp) / "home"),
            "FINVISION_GATEWAY_NER": "0",
            "CLAUDE_CONFIG_DIR": str(Path(tmp) / "claude"),
        }
        patched = mock.patch.dict(os.environ, env)
        patched.start()
        os.environ.pop("ANTHROPIC_BASE_URL", None)
        try:
            return _run(Path(tmp), out)
        finally:
            patched.stop()


def _run(tmp: Path, out: Callable[[str], None]) -> int:
    files = sorted(WIRE.glob("*.json"))
    results = {name: AppResult(name) for name in APPS}
    unrouted: list[str] = []
    replay = _Replay(tmp)
    _claude_code(replay, results["claude-code"])
    for path in files:
        fx = json.loads(path.read_text(encoding="utf-8"))
        req = fx["request"]
        adapter = replay.adapter_for(
            req["method"], req["host"], req["path"], req.get("content_type", "")
        )
        app = _APP_OF_ADAPTER.get(adapter.name) if adapter is not None else None
        if app is None:
            unrouted.append(fx["name"])
            continue
        replay.fixture(fx, results[app], adapter)
    for res in results.values():
        out(res.line())
        for problem in res.problems:
            out(f"  - {problem}")
    for name in unrouted:
        out(f"unrouted fixture: {name} (no named app serves its route)")
    return 0 if all(r.passed for r in results.values()) and not unrouted else 1


if __name__ == "__main__":
    raise SystemExit(run_selftest())
