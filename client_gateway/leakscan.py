"""Format-agnostic residual leak scan of an outgoing body (rule R9).

An adapter masks what it understands. An app update that adds a field, a part type or an endpoint
shape the adapter does not know could carry a client name out in text nobody walked. So after the
adapter ran, the front end scans the WHOLE outgoing body once more with the engine's identity
matcher (``SessionMap.identity_hits``: customers, vendors, persons, companies and parts of their
names; never generic dimension values, never shapes) in every form the value can take on the wire:

* a JSON body: its keys, strings and numbers joined (an escaped ``\\u005a...`` name is plain here, an
  account number that is a JSON number is text here), and its raw text with the ``\\n`` / ``\\r`` /
  ``\\t`` escapes read as whitespace (in raw JSON the ``n`` of ``\\n`` would glue to a pseudonym that
  follows it and hide it from the matcher, and a known pseudonym would then look like a value);
* any other body: the raw decoded text (``Zorblax Holdings NV`` as it stands);
* the URL-decoded form of each (a form body or a query string, ``Zorblax%20Holdings``).

Verbatim developer text (the top-level ``tools`` / ``functions`` / ``instructions`` fields of a JSON
body: tool and function descriptions, parameter schemas, the app's own instructions) is scanned with
the developer-text rule of the masker (``identity_hits(whole=False)``): a value that is only a
whole-string cell, such as a lone ordinary word, is not a hit there, exactly as the adapter did not
mask it. Content fields keep the strict scan.

``residual_hits`` returns a COUNT; no value, no position and no text ever leaves this module. A
count above zero makes the front end block the request with ``residual_leak`` (nothing is sent).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote_plus

from core.client_guard.engine import SessionMap

MAX_DEPTH = 60
# top-level JSON fields that hold the developer's own text, not the conversation
DEVELOPER_KEYS = frozenset({"tools", "functions", "instructions"})
_ESCAPES = re.compile(r"\\[nrtbf]")


def json_strings(obj: Any, _depth: int = 0, numbers: bool = False) -> list[str]:
    """Every string leaf AND dict key of a JSON tree (a deeper tree than ``MAX_DEPTH`` is cut);
    with ``numbers`` the numbers too, as text."""
    if _depth > MAX_DEPTH:
        return []
    if isinstance(obj, str):
        return [obj]
    if numbers and isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return [str(obj)]
    if isinstance(obj, list):
        return [s for v in obj for s in json_strings(v, _depth + 1, numbers)]
    if isinstance(obj, dict):
        return [
            s
            for k, v in obj.items()
            for s in json_strings(k, _depth + 1, numbers) + json_strings(v, _depth + 1, numbers)
        ]
    return []


def _hits(smap: SessionMap, text: str, whole: bool = True) -> int:
    if not text:
        return 0
    return sum(smap.identity_hits(text, whole=whole).values())


def _forms(text: str) -> list[str]:
    """The text, and its URL-decoded form when that differs."""
    out = [text]
    if "%" in text or "+" in text:
        decoded = unquote_plus(text)
        if decoded != text:
            out.append(decoded)
    return out


def residual_hits(smap: SessionMap, body: bytes) -> int:
    """Known identity values left in ``body`` (0 = clean). Stops at the first form that has a hit.
    A body that is not UTF-8 is read with replacement characters (the values are ASCII or valid
    UTF-8 where they matter)."""
    text = body.decode("utf-8", "replace")
    tree: Any = None
    if text.lstrip()[:1] in ("{", "["):
        try:
            tree = json.loads(text)
        except (ValueError, RecursionError):
            tree = None
    developer: list[str] = []
    if isinstance(tree, dict) and DEVELOPER_KEYS.intersection(tree):
        developer = json_strings({k: tree[k] for k in tree if k in DEVELOPER_KEYS})
        tree = {k: v for k, v in tree.items() if k not in DEVELOPER_KEYS}
        text = json.dumps(tree, ensure_ascii=False, separators=(",", ":"))
    strings: list[str] | None = None
    if tree is not None:
        try:
            strings = json_strings(tree, numbers=True)
        except RecursionError:
            strings = None
    if strings is None:
        candidates = _forms(text)
    else:
        candidates = _forms("\n".join(strings)) + _forms(_ESCAPES.sub(" ", text))
    for candidate in candidates:
        hits = _hits(smap, candidate)
        if hits:
            return hits
    for candidate in _forms("\n".join(developer)) if developer else []:
        hits = _hits(smap, candidate, whole=False)
        if hits:
            return hits
    return 0
