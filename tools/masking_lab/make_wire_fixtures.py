"""Build the sanitized wire fixtures of the masking adapters (phase 2a) from raw capture samples.

    python tools/masking_lab/make_wire_fixtures.py --samples <dir> --private-terms <file> \\
        --out tests/fixtures/masking/wire

The samples are the raw files of a lab capture session (one JSON file per request: method, host,
path, request body, response body or chunks). They hold the capturing account's identifiers and
must never enter the repository. This tool reads the few samples the adapters need and writes
minimized, sanitized copies:

* every UUID becomes a deterministic fake UUID (``00000000-0000-4000-8000-<n>``), every ChatGPT
  file id a fake file id, every provider message id a fake id, every JWT-like token ``<token>``;
* ChatGPT ``user-...`` ids, e-mail addresses, Windows user paths, machine names and every term of the
  private-terms file (case-insensitive) become neutral placeholders; the file with the terms is
  read from where it is and is never copied or printed;
* long system prompts, tool lists and VS Code reminder blocks are trimmed but stay structurally valid;
  opaque reasoning blobs are replaced by a short fake blob;
* streams are stored DECOMPRESSED, one text per recorded chunk (the original chunk boundaries),
  together with the ``content-encoding`` the response used (the tests compress again). A Responses
  stream (Copilot ``/responses``) was recorded with chunks that cut through events: it is stored one
  complete event per chunk, and the echo of the request in ``response.*`` events (tools, instructions,
  ids, the account's ``safety_identifier``) is cut down or replaced;
* the pseudonyms in a sample were made by the lab gateway's key, which a test cannot reproduce: they stay
  in the fixture as they are (``LAB_TOKENS`` in ``tests/_wire_support.py`` maps them to the synthetic
  names and back).

Nothing printed or logged here ever holds the text of a sample or a private term: only fixture names
and counts. After writing, the output is scanned with ``find_leaks``; any hit aborts with exit
code 2 and nothing is left on disk.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import zlib
from pathlib import Path
from typing import Any

FAKE_MARKER = "fvpbi-0123456789abcdef"
FAKE_OPAQUE = "ZmFrZS1vcGFxdWUtcmVhc29uaW5nLWJsb2I="
FAKE_HOST = "files-test.oaiusercontent.com"
TRIM_SUFFIX = " [trimmed]"

# What a fixture must never hold. Shared with tests/test_wire_fixtures_sanitized.py.
LEAK_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
    "jwt": re.compile(r"eyJ"),
    "chatgpt_user_id": re.compile(r"user-[A-Za-z0-9]{10,}"),
    "windows_user_path": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]", re.I),
    "machine_name": re.compile(r"\blt-[0-9a-z]{6,}\b", re.I),
}
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
FAKE_UUID_RE = re.compile(r"00000000-0000-4000-8000-\d{12}")
FILE_ID_RE = re.compile(r"file_[0-9a-f]{32}")
FAKE_FILE_ID_RE = re.compile(r"file_0{24}\d{8}")
MARKER_RE = re.compile(r"fvpbi-[0-9a-f]{16}")

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*")
_USER_ID = re.compile(r"user-[A-Za-z0-9]{10,}")
_USER_PATH = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+(?:[\\/]+[^\s\"']*)?", re.I)
_MACHINE = re.compile(r"\blt-[0-9a-z]{6,}\b", re.I)
_EMAIL = LEAK_PATTERNS["email"]
_MSG_ID = re.compile(r"\bmsg_[A-Za-z0-9]{16,}")
_TOOL_ID = re.compile(r"\btoolu_[A-Za-z0-9]{16,}")
_CMPL_ID = re.compile(r"\bchatcmpl-[A-Za-z0-9]{16,}")
_REMINDER = re.compile(r"(?s)(<(?P<tag>reminderInstructions|importantReminders)>).*?(</(?P=tag)>)")
_LONG_ID_KEYS = frozenset({"id", "item_id"})
_DROP_KEYS = frozenset({"safety_identifier", "prompt_cache_key", "user"})
_TOKEN_KEYS = frozenset({"conduit_token", "token", "resume_conversation_token"})


class FixtureError(Exception):
    """A sample is missing or is not what the selection table expects (message holds no data)."""


def find_leaks(text: str) -> list[str]:
    """Names of the leak classes found in ``text`` (never the matches themselves)."""
    found = [name for name, pattern in LEAK_PATTERNS.items() if pattern.search(text)]
    if any(not FAKE_UUID_RE.fullmatch(u) for u in UUID_RE.findall(text)):
        found.append("uuid")
    if any(not FAKE_FILE_ID_RE.fullmatch(f) for f in FILE_ID_RE.findall(text)):
        found.append("file_id")
    if any(m != FAKE_MARKER for m in MARKER_RE.findall(text)):
        found.append("pbi_marker")
    return found


class Sanitizer:
    """Deterministic text / JSON scrubber; the id maps live as long as the instance (one run)."""

    def __init__(self, private_terms: list[str]) -> None:
        terms = sorted({t for t in private_terms if t}, key=len, reverse=True)
        self._terms = re.compile("|".join(re.escape(t) for t in terms), re.I) if terms else None
        self._uuids: dict[str, str] = {}
        self._files: dict[str, str] = {}
        self._ids: dict[str, str] = {}
        self.replacements = 0

    # ------------------------------------------------------------------ strings
    def _fake_uuid(self, match: re.Match[str]) -> str:
        key = match.group(0).lower()
        if FAKE_UUID_RE.fullmatch(key):
            return key
        return self._uuids.setdefault(key, f"00000000-0000-4000-8000-{len(self._uuids) + 1:012d}")

    def _fake_file(self, match: re.Match[str]) -> str:
        key = match.group(0)
        if FAKE_FILE_ID_RE.fullmatch(key):
            return key
        return self._files.setdefault(key, f"file_{len(self._files) + 1:032d}")

    def _fake_id(self, prefix: str) -> Any:
        def sub(match: re.Match[str]) -> str:
            key = match.group(0)
            return self._ids.setdefault(key, f"{prefix}fake{len(self._ids) + 1:06d}")

        return sub

    def text(self, s: str) -> str:
        """``s`` without identifiers (see the module text)."""
        if not s:
            return s
        out = s
        if self._terms is not None:
            out = self._terms.sub("<redacted>", out)
        out = MARKER_RE.sub(FAKE_MARKER, out)
        out = _JWT.sub("<token>", out)
        out = _USER_PATH.sub("<user-home>", out)
        out = _EMAIL.sub("<email>", out)
        out = _USER_ID.sub("user-<id>", out)
        out = _MACHINE.sub("<machine>", out)
        out = UUID_RE.sub(self._fake_uuid, out)
        out = FILE_ID_RE.sub(self._fake_file, out)
        out = _MSG_ID.sub(self._fake_id("msg_"), out)
        out = _TOOL_ID.sub(self._fake_id("toolu_"), out)
        out = _CMPL_ID.sub(self._fake_id("chatcmpl-"), out)
        if out != s:
            self.replacements += 1
        return out

    def host(self, host: str) -> str:
        return FAKE_HOST if host.endswith(".oaiusercontent.com") else self.text(host)

    # ------------------------------------------------------------------ JSON
    def walk(self, obj: Any, key: str | None = None) -> Any:
        if isinstance(obj, dict):
            return {k: self.walk(v, k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.walk(v, key) for v in obj]
        if isinstance(obj, str):
            if key in ("reasoning_opaque", "encrypted_content"):
                return FAKE_OPAQUE
            if key in _DROP_KEYS:
                return "<id>"
            if key in _LONG_ID_KEYS and len(obj) > 40:  # an opaque provider item id
                return self._ids.setdefault(obj, f"item_fake{len(self._ids) + 1:06d}")
            if key in _TOKEN_KEYS:
                return "<token>"
            return self.text(obj)
        return obj

    def event(self, obj: Any) -> Any:
        """One stream event: account-level metadata events are cut down, the rest is walked."""
        if isinstance(obj, dict):
            kind = obj.get("type")
            if isinstance(obj.get("response"), dict):  # a Responses event echoes the request
                obj = dict(obj, response=_trim_response_object(obj["response"]))
            if kind == "server_ste_metadata":
                obj = dict(obj, metadata={"model_slug": "gpt-5-6", "turn_use_case": "text"})
            elif kind == "conversation_detail_metadata":
                obj = {
                    k: v
                    for k, v in obj.items()
                    if k in ("type", "conversation_id", "default_model_slug")
                }
        return self.walk(obj)

    def stream_chunk(self, chunk: str) -> str:
        """One recorded stream chunk (event aligned), chunk boundaries kept."""
        lines = []
        for line in chunk.split("\n"):
            if line.startswith("data:"):
                body = line[5:]
                core = body.strip()
                lead = body[: len(body) - len(body.lstrip())]
                tail = body[len(body.rstrip()) :]  # noqa: E203
                try:
                    obj = json.loads(core)
                except ValueError:
                    line = "data:" + lead + self.text(core) + tail
                else:
                    line = "data:" + lead + dumps(self.event(obj)) + tail
            else:
                line = self.text(line)
            lines.append(line)
        return "\n".join(lines)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------- Copilot trimming


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + TRIM_SUFFIX


def _trim_reminders(text: str) -> str:
    return _REMINDER.sub(lambda m: f"{m.group(1)}\n[trimmed]\n{m.group(3)}", text)


def _trim_response_object(resp: dict[str, Any]) -> dict[str, Any]:
    """The ``response`` object of a Responses event without the echo of the request (the tools
    list is tens of KB), keeping everything a rehydrator reads (``output``)."""
    out = dict(resp)
    if "tools" in out:
        out["tools"] = []
    if isinstance(out.get("instructions"), str):
        out["instructions"] = _trim(out["instructions"], 200)
    if "metadata" in out:
        out["metadata"] = {}
    return out


def trim_copilot_request(body: dict[str, Any]) -> dict[str, Any]:
    """Cut a captured Copilot chat request down: system prompt, tool list, reminder blocks."""
    out = json.loads(json.dumps(body))
    for message in out.get("messages", []):
        content = message.get("content")
        if message.get("role") == "system" and isinstance(content, str):
            message["content"] = _trim(content, 200)
        elif isinstance(content, str):
            message["content"] = _trim_reminders(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = _trim_reminders(part["text"])
    tools = out.get("tools")
    if isinstance(tools, list) and tools:
        keep = [t for t in tools if "customer_revenue" in json.dumps(t)]
        keep += [t for t in tools if t not in keep][:1]
        for tool in keep:
            fn = tool.get("function", {})
            if isinstance(fn.get("description"), str):
                fn["description"] = _trim(fn["description"], 120)
            if len(json.dumps(tool)) > 2500:
                fn["parameters"] = {"type": "object", "properties": {}}
        out["tools"] = keep
    return out


def trim_responses_request(body: dict[str, Any]) -> dict[str, Any]:
    """Cut a captured Copilot ``/responses`` request down: system item, tool list, reminder blocks."""
    out = json.loads(json.dumps(body))
    if isinstance(out.get("instructions"), str):
        out["instructions"] = _trim(out["instructions"], 200)
    items = out.get("input")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str):
            item["content"] = _trim_reminders(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text = part["text"]
                    part["text"] = (
                        _trim(text, 200) if item.get("role") == "system" else _trim_reminders(text)
                    )
    tools = out.get("tools")
    if isinstance(tools, list) and tools:
        keep = [t for t in tools if "customer_revenue" in json.dumps(t)]
        keep += [t for t in tools if t not in keep][:1]
        for tool in keep:
            if isinstance(tool.get("description"), str):
                tool["description"] = _trim(tool["description"], 120)
            if len(json.dumps(tool)) > 2500:
                tool["parameters"] = {"type": "object", "properties": {}}
        out["tools"] = keep
    return out


def reframe(chunks: list[str]) -> list[str]:
    """One complete SSE event per chunk (the recorded chunks of a Responses stream cut through
    events). The text is the same, only the boundaries move."""
    joined = "".join(chunks).replace("\r\n", "\n")
    return [frame + "\n\n" for frame in joined.split("\n\n") if frame.strip()]


# ---------------------------------------------------------------------- selection table


class Entry:
    def __init__(self, name: str, seq: int, method: str, path: str, kind: str, note: str) -> None:
        self.name = name
        self.seq = seq
        self.method = method
        self.path = re.compile(path)
        self.kind = kind
        self.note = note


SELECTION: tuple[Entry, ...] = (
    Entry(
        "copilot_title_stream", 19, "POST", r"^/chat/completions$", "copilot",
        "title request (system + user text); reply streamed with the name split over deltas",
    ),
    Entry(
        "copilot_tool_call_stream", 30, "POST", r"^/chat/completions$", "copilot",
        "reasoning_text deltas, a reasoning_opaque blob, and a tool call whose JSON arguments "
        "stream in small pieces",
    ),
    Entry(
        "copilot_tool_result_image", 32, "POST", r"^/chat/completions$", "copilot",
        "history with VS Code wrappers, a pasted image (attachment URLs), a tool call and a "
        "role=tool result carrying the MCP marker",
    ),
    Entry(
        "copilot_post_tool_reply_stream", 778891, "POST", r"^/chat/completions$", "copilot",
        "the post-tool reply of a claude model: three pseudonyms of the lab session split over "
        "delta.content events, role on every delta, usage on the finish event (display replay)",
    ),
    Entry(
        "copilot_responses_first", 778896, "POST", r"^/responses$", "copilot_responses",
        "first turn of a /responses chat: a client name in the typed text, a reasoning item, text "
        "and a function call whose JSON arguments stream in small pieces",
    ),
    Entry(
        "copilot_responses_tool_result", 778899, "POST", r"^/responses$", "copilot_responses",
        "history with a reasoning item, assistant text, a function_call and a function_call_output "
        "that carries the MCP marker; the reply streams text with three pseudonyms split over deltas",
    ),
    Entry(
        "copilot_responses_tool_call", 778900, "POST", r"^/responses$", "copilot_responses",
        "multi-turn history; the reply streams reasoning-summary deltas and function-call argument "
        "deltas that hold a pseudonym",
    ),
    Entry(
        "chatgpt_conversation_turn1", 713031, "POST", r"^/backend-api/f/conversation$", "chatgpt",
        "first turn: no conversation_id; gzip v1 stream with the name split over events",
    ),
    Entry(
        "chatgpt_conversation_image_turn", 713046, "POST", r"^/backend-api/f/conversation$",
        "chatgpt", "second turn with an uploaded image (multimodal_text, attachments metadata)",
    ),
    Entry(
        "chatgpt_prepare_first", 713030, "POST", r"^/backend-api/f/conversation/prepare$",
        "chatgpt", "half-typed first message (partial_query is a message object)",
    ),
    Entry(
        "chatgpt_prepare_followup", 713045, "POST", r"^/backend-api/f/conversation/prepare$",
        "chatgpt", "half-typed follow-up with conversation_id and attachment_mime_types",
    ),
    Entry(
        "chatgpt_files_metadata", 713041, "POST", r"^/backend-api/files$", "chatgpt",
        "upload metadata (file_name)",
    ),
    Entry(
        "chatgpt_upload_put", 713043, "PUT", r"^/files/[0-9a-f-]+/raw$", "chatgpt",
        "raw image PUT to the files host (metadata only: the PNG bytes are not stored)",
    ),
    Entry(
        "chatgpt_process_upload_stream", 713044, "POST",
        r"^/backend-api/files/process_upload_stream$", "chatgpt",
        "upload processing request (file_name, origination_thread_id) and its NDJSON reply",
    ),
    Entry(
        "chatgpt_conversation_get", 713015, "GET", r"^/backend-api/conversations/[0-9a-f-]+$",
        "chatgpt", "stored conversation reloaded after the stream (br JSON)",
    ),
)  # fmt: skip


def _load_sample(samples: Path, entry: Entry) -> dict[str, Any]:
    # one sequence number can belong to several hosts (two apps captured in parallel)
    for path in sorted(samples.glob(f"{entry.seq:04d}-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            data.get("seq") == entry.seq
            and data.get("method") == entry.method
            and entry.path.match(data.get("path", "").split("?")[0])
        ):
            return data  # type: ignore[no-any-return]
    raise FixtureError(f"sample {entry.seq} is not there as the expected request ({entry.name})")


def _stream_chunks(sample: dict[str, Any]) -> list[str]:
    """Decoded text of each recorded response chunk (empty chunks dropped)."""
    headers = sample.get("resp_headers") or {}
    encoding = (headers.get("content-encoding") or "").lower()
    if sample.get("resp_chunks_b64") and encoding:
        if encoding not in ("gzip", "x-gzip"):
            raise FixtureError("unsupported recorded encoding")
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS)  # a stream: no end marker
        chunks = [
            dec.decompress(base64.b64decode(c)).decode("utf-8") for c in sample["resp_chunks_b64"]
        ]
    else:
        chunks = list(sample.get("resp_chunks") or [])
    return [c for c in chunks if c]


def build_fixture(entry: Entry, sample: dict[str, Any], san: Sanitizer) -> dict[str, Any]:
    req_headers = sample.get("req_headers") or {}
    body_text = sample.get("req_body") or ""
    request: dict[str, Any] = {
        "method": sample["method"],
        "host": san.host(sample["host"]),
        "path": san.text(sample["path"].split("?")[0]),
        "content_type": req_headers.get("content-type", ""),
    }
    if sample["method"] == "PUT":
        request["content_length"] = int(req_headers.get("content-length", "0"))
        request["body"] = None  # the capture keeps only a preview of a binary body
    elif body_text:
        parsed = json.loads(body_text)
        if entry.kind == "copilot":
            parsed = trim_copilot_request(parsed)
        elif entry.kind == "copilot_responses":
            parsed = trim_responses_request(parsed)
        request["body"] = san.walk(parsed)
    else:
        request["body"] = None
    fixture: dict[str, Any] = {"name": entry.name, "note": entry.note, "request": request}

    resp_headers = sample.get("resp_headers") or {}
    response: dict[str, Any] = {
        "status": sample.get("status"),
        "content_type": resp_headers.get("content-type", ""),
        "content_encoding": resp_headers.get("content-encoding", ""),
    }
    if sample.get("resp_chunks") or sample.get("resp_chunks_b64"):
        recorded = _stream_chunks(sample)
        if entry.kind == "copilot_responses":
            recorded = reframe(recorded)
        response["chunks"] = [san.stream_chunk(c) for c in recorded]
    elif sample.get("resp_body") and entry.name == "chatgpt_conversation_get":
        response["body"] = san.walk(json.loads(sample["resp_body"]))
    else:
        response = {}
    if response:
        fixture["response"] = response
    return fixture


def read_terms(path: Path) -> list[str]:
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def build_all(samples: Path, terms: list[str]) -> dict[str, str]:
    """``{file name: text}`` of every fixture; raises ``FixtureError`` on a missing sample or leak."""
    san = Sanitizer(terms)
    files: dict[str, str] = {}
    for entry in SELECTION:
        fixture = build_fixture(entry, _load_sample(samples, entry), san)
        files[entry.name + ".json"] = json.dumps(fixture, ensure_ascii=False, indent=1) + "\n"
    for name, text in files.items():
        hits = find_leaks(text) + (
            ["private_term"] if san._terms and san._terms.search(text) else []
        )
        if hits:
            raise FixtureError(f"{name}: leak classes left after sanitizing: {', '.join(hits)}")
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--samples", required=True, type=Path)
    ap.add_argument("--private-terms", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        files = build_all(args.samples, read_terms(args.private_terms))
    except FixtureError as exc:
        print(f"make_wire_fixtures: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        with open(args.out / name, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"wrote {name} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
