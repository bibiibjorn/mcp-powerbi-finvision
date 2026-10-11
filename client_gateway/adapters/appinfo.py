"""Which app (and which major version) sent a front-end request: labels only (rule R9).

An AI app that updates itself can change its wire format. The front end records, per chat space, the
``name/version`` labels of the requests it sees (``GatewayState.frontend_note_app``); a label that is
not in ``TESTED_APPS`` (the apps the wire fixtures were recorded with) shows in ``status`` as
``untested_app_version``. Masking and the leak scan keep running either way: the label only says "this
app version was not verified against the fixtures".

Only the app name and its major version leave a header (``vscode/1``, ``copilot-chat/0.32``,
``chrome/141``): a major version of ``0`` keeps its minor, because a 0.x extension changes its wire
format between minors. No other part of a header is read, stored or logged.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# the apps the wire fixtures were recorded with, per chat space (``Adapter.chat_space``). Fill it from
# the labels a lab session reports (``status`` -> ``frontend_apps``) when the fixtures are re-captured;
# ``tests/test_frontend_app_labels.py`` pins every label a fixture records to this table.
TESTED_APPS: dict[str, frozenset[str]] = {
    "copilot": frozenset(),
    "chatgpt_web": frozenset(),
}

_PRODUCT = re.compile(r"^\s*([A-Za-z][A-Za-z0-9._-]{0,31})/(\d+)(?:\.(\d+))?")
_BROWSERS = (
    "Edg",
    "OPR",
    "Firefox",
    "Chrome",
    "Safari",
)  # most specific first (Chrome UAs say Safari)
_BROWSER = {name: re.compile(rf"\b{name}/(\d+)") for name in _BROWSERS}


def label_of(product: str) -> str | None:
    """``"vscode/1.105.0"`` -> ``"vscode/1"``; ``"copilot-chat/0.32.1"`` -> ``"copilot-chat/0.32"``;
    None for anything that is not ``name/version``."""
    match = _PRODUCT.match(product or "")
    if match is None:
        return None
    name, major, minor = match.group(1).lower(), match.group(2), match.group(3)
    version = f"0.{minor or '0'}" if int(major) == 0 else major
    return f"{name}/{version}"


def browser_label(user_agent: str) -> str | None:
    """The browser of a ``User-Agent`` (``chrome/141``), or None."""
    for name in _BROWSERS:
        match = _BROWSER[name].search(user_agent or "")
        if match is not None:
            return f"{'edge' if name == 'Edg' else 'opera' if name == 'OPR' else name.lower()}/{match.group(1)}"
    return None


def app_labels(space: str, headers: Mapping[str, str]) -> tuple[str, ...]:
    """The app labels of one request. Copilot (VS Code): ``editor-plugin-version`` and
    ``editor-version`` (else the ``user-agent`` product); browsers: the ``user-agent``."""
    out: list[str] = []
    if space == "copilot":
        for name in ("editor-plugin-version", "editor-version"):
            label = label_of(headers.get(name, ""))
            if label:
                out.append(label)
        if not out:
            label = label_of(headers.get("user-agent", ""))
            if label:
                out.append(label)
    else:
        label = browser_label(headers.get("user-agent", ""))
        if label:
            out.append(label)
    return tuple(dict.fromkeys(out))


def is_tested(space: str, label: str) -> bool:
    return label in TESTED_APPS.get(space, frozenset())
