"""``python -m client_gateway setup --check``: does masking work on this machine, right now?

Read-only. It changes nothing and STARTS nothing: a gateway that is not running is reported as such
(it starts on demand, ``masking on``). When one runs it makes one ``/__gateway/mask`` round trip and
one ``/__gateway/unmask`` round trip with a fixed harmless test text (the same calls the Power BI
MCP makes, with the control token this CLI reads itself) and says:

* ``PASS``      both worked and the dictionary is ready;
* ``DEGRADED``  masking works but with what is known so far (fail mode ``degrade``: the dictionary is
                not complete); the reason is a fixed code and the fix is concrete;
* ``FAIL``      a call did not work (unreachable, token, refused, ...), again with reason and fix.

Exit code 0 for ``PASS`` / ``DEGRADED`` / not running, 1 for ``FAIL``. The last line is
``VERDICT: <PASS|DEGRADED|FAIL|NOT RUNNING> ...`` (what the setup script shows). The output holds
counts and fixed labels only, never a value.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from client_gateway import cli
from client_gateway.state import TOKEN_HEADER, fail_mode, masking_home, read_token

EXIT_OK = 0
EXIT_FAIL = 1
TEST_TEXT = "Finvision masking check: nothing to mask in this sentence."
CHECK_TIMEOUT_S = 40.0

Call = Callable[..., tuple[int, dict[str, Any]] | None]

# what to do about each fixed code (reason of a failed call, ``not_ready`` reason, class labels)
FIXES = {
    "unreachable": "the gateway did not answer: run python -m client_gateway start, then check again",
    "timeout": "the gateway answered too slowly (it may still be reading a big model): wait a "
    "minute and check again",
    "token_required": "the control token is missing or stale: rerun python -m client_gateway "
    "setup --yes",
    "not_ready": "the dictionary is not ready and client_gateway.fail_mode is closed: set it to "
    "degrade (the default) or fix the dictionary (see the classes above)",
    "mask_failed": "the gateway could not mask the text: run python -m client_gateway status and "
    "look at the warnings; restart it with python -m client_gateway start --replace",
    "bad_reply": "the gateway answered something this version does not understand: restart it "
    "with python -m client_gateway start --replace",
    "dictionary_empty": "no Power BI model has been read: open the client model in Power BI "
    "Desktop and check again in a minute",
    "connect_failed": "Power BI Desktop could not be connected: close and reopen the model, then "
    "check again",
    "query_failed": "a query against the open model failed: update Power BI Desktop to the latest "
    "version, or close other heavy models and check again",
    "column_unreadable": "a column could not be read completely: check again in a minute (a "
    "column that keeps failing is given up after two reads)",
    "timeout_class": "reading the model takes long: wait a few minutes and check again, or raise "
    "client_gateway.detect_timeout_s",
    "new_instance": "a model was just opened: wait a minute and check again",
    "instance_listing_failed": "the open Power BI Desktop models could not be listed: check that "
    "Power BI Desktop runs, then check again",
}


def _fix_for(code: str, classes: list[str]) -> str:
    for label in classes:
        key = "timeout_class" if label == "timeout" else label
        if key in FIXES:
            return FIXES[key]
    return FIXES.get(code, "run python -m client_gateway status for the details")


def _labels(value: Any) -> list[str]:
    return [str(c) for c in value if isinstance(c, str)][:16] if isinstance(value, list) else []


def _status_line(info: dict[str, Any] | None) -> tuple[str, str, list[str]]:
    """``(line, reason, classes)`` for the dictionary out of a status reply."""
    if info is None:
        return "dictionary:    unknown (status not readable with this token)", "", []
    dictionary = info.get("dictionary")
    values = dictionary.get("values") if isinstance(dictionary, dict) else None
    count = f"{values} values" if isinstance(values, int) else "values unknown"
    why = info.get("not_ready")
    if info.get("ready") is True or not why:
        return f"dictionary:    ready, {count}", "", []
    reason = str(why.get("reason") or "dictionary_not_ready") if isinstance(why, dict) else ""
    classes = _labels(why.get("classes")) if isinstance(why, dict) else []
    extra = f"; classes: {', '.join(classes)}" if classes else ""
    return f"dictionary:    NOT READY ({reason}{extra}), {count}", reason, classes


def run_check(
    port: int | None = None,
    out: Callable[[str], None] | None = None,
    call: Call | None = None,
    token: str | None = None,
) -> int:
    """Run the check against the gateway on ``port`` (default: the CLI's own resolution). ``call``
    and ``token`` are injectable for tests."""
    emit: Callable[[str], None] = out if out is not None else _stdout
    send: Call = call if call is not None else cli.call
    port = port if port is not None else cli.resolve_port(None)
    secret = token if token is not None else read_token(masking_home())
    headers = {TOKEN_HEADER: secret} if secret else {}
    emit("Finvision masking check (read-only, starts nothing)")
    emit(f"  fail mode:     {fail_mode()} (config client_gateway.fail_mode)")
    health = send(port, "GET", "/__gateway/health", timeout=2.0)
    if health is None or health[0] != 200:
        emit(f"  gateway:       not running on port {port}; nothing was started")
        emit(
            "VERDICT: NOT RUNNING - the gateway starts on demand (type: masking on); masking is "
            "off while no gateway runs. Nothing to check."
        )
        return EXIT_OK
    emit(f"  gateway:       running on port {port} (version {health[1].get('version', '?')})")
    reply = send(port, "GET", "/__gateway/status", headers=headers, timeout=5.0)
    info = reply[1] if reply is not None and reply[0] == 200 else None
    line, why, classes = _status_line(info)
    emit("  " + line)
    code, degraded, classes_hit = _round_trip(send, port, headers, emit)
    if code:
        emit(f"VERDICT: FAIL ({code}) - fix: {_fix_for(code, classes)}")
        return EXIT_FAIL
    if degraded or why:
        reason = degraded or why
        merged = classes_hit or classes
        emit(
            f"VERDICT: DEGRADED ({reason}) - masking works with what is known so far; "
            f"fix: {_fix_for(reason, merged)}"
        )
        return EXIT_OK
    emit("VERDICT: PASS - masking round trip works and the dictionary is ready")
    return EXIT_OK


def _failure(reply: tuple[int, dict[str, Any]] | None) -> str:
    """The fixed code of a reply that is not a usable 200."""
    if reply is None:
        return "unreachable"
    if reply[0] == 403:
        return "token_required"
    if reply[0] == 503:
        return "not_ready" if reply[1].get("error") == "not_ready" else "mask_failed"
    return "bad_reply"


def _round_trip(
    send: Call, port: int, headers: dict[str, str], emit: Callable[[str], None]
) -> tuple[str, str, list[str]]:
    """``(failure code, degraded reason, degraded classes)``: the code is non-empty when a call
    failed. Prints one line per call."""
    masked = send(
        port,
        "POST",
        "/__gateway/mask",
        {"texts": [TEST_TEXT], "whole": True},
        headers,
        CHECK_TIMEOUT_S,
    )
    texts = masked[1].get("texts") if masked is not None else None
    if masked is None or masked[0] != 200 or not isinstance(texts, list) or len(texts) != 1:
        code = _failure(masked) if masked is None or masked[0] != 200 else "bad_reply"
        emit(f"  mask:          FAILED ({code})")
        return code, "", []
    body = masked[1]
    degraded = (
        str(body.get("degraded_reason") or "dictionary_not_ready") if body.get("degraded") else ""
    )
    classes = _labels(body.get("degraded_classes"))
    emit("  mask:          ok" + (f" (degraded: {degraded})" if degraded else ""))
    back = send(
        port, "POST", "/__gateway/unmask", {"texts": [str(texts[0])]}, headers, CHECK_TIMEOUT_S
    )
    got = back[1].get("texts") if back is not None else None
    if back is None or back[0] != 200 or not isinstance(got, list) or got != [TEST_TEXT]:
        code = _failure(back) if back is None or back[0] != 200 else "bad_reply"
        emit(f"  unmask:        FAILED ({code})")
        return code, degraded, classes
    emit("  unmask:        ok")
    return "", degraded, classes


def _stdout(text: str) -> None:
    sys.stdout.write(text + "\n")


def main() -> int:
    return run_check()
