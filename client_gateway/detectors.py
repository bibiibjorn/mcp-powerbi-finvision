"""The gateway's detection layers, assembled in one place (the Presidio recognizer pattern).

1. Dictionary   values of the open Power BI models (provider)            exact, all spellings
2. Remembered   every value read before, DPAPI-encrypted (``store``)     closed models
3. Client list  ``<home>/client_names.txt``, one name per line, optional ``PREFIX: name``
4. Typos        misspellings of identity values (``core.client_guard.fuzzy``)
5. NER          names in no model at all: GLiNER, local (``core.client_guard.ner``)
6. Shapes       e-mail, IBAN, phone (always on) + the profile's country packs (``be``: Belgian
                national / enterprise number)
   Allow list   never masked: the profile's software / company terms, config
                ``client_guard.allow_terms`` and ``<home>/allow_terms.txt`` (keeps report text and
                tool names readable)

What is organisation-specific (token prefixes, naming conventions, allow terms, country packs, the
NER default) is the PROFILE: ``FINVISION_MASKING_PROFILE`` / ``client_guard.profile``, ``generic``
by default (``core.client_guard.profiles``).

Configuration (``config/*.json``): ``client_guard.ner.enabled`` (default follows the profile: on
for ``finvision``, off for ``generic``; an explicit value wins; the model runs in
its own worker process, ONNX Runtime, 2 s budget per request: ``client_guard.ner.budget_s``,
``.threads``, ``.onnx``, ``.wait_s`` for the first request). The worker starts when a chat
switches masking on, or lazily when the safety net of an unmasked chat first sees prose (never at
gateway start unless masking is on by default), and is unloaded after ``.idle_s`` (default 20
minutes) without use while no chat is masked. Names it recognizes are remembered (DPAPI store), ``client_guard.ner.model``, ``client_guard.ner.threshold``,
``client_guard.ner.wait_s``, ``client_guard.ner.threads``, ``client_guard.fuzzy`` (default true),
``client_guard.remember`` (default true). Environment ``FINVISION_GATEWAY_NER=0`` switches NER off
(tests do).

Cost while masking is OFF (the shipped default, with the unmasked guard on): every request body is
JSON-parsed and its newest message scanned for known values (~3 ms for a 2 MB body); the NER worker
(its own process, ~1 GB) starts lazily the first time the guard sees prose and is unloaded after
``client_guard.ner.idle_s``; a screenshot in the newest message is read once by Windows OCR (about
2 s at 1080p, then cached). ``client_gateway.guard_unmasked`` / ``guard_ner`` switch the guard off
or limit it to known values.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from client_gateway.state import cfg
from client_gateway.store import ValueStore
from core.client_guard.engine import GENERIC_PREFIXES, SessionMap, ordinary_word
from core.client_guard.profiles import FINVISION, resolve

CLIENT_LIST = "client_names.txt"
ALLOW_LIST = "allow_terms.txt"
NER_ENV = "FINVISION_GATEWAY_NER"
# the finvision profile's list prefixes; ``parse_client_list`` uses the active profile's
LIST_PREFIXES = FINVISION.list_prefixes


def _read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    return [
        ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]


def parse_client_list(
    lines: list[str], prefixes: tuple[str, ...] | None = None
) -> dict[str, list[str]]:
    """``"VEN: Janssens NV"`` -> VEN; a line without a known prefix is an organisation (ORG).
    The known prefixes are the active profile's unless ``prefixes`` is given."""
    known = prefixes if prefixes is not None else resolve().profile.list_prefixes
    out: dict[str, list[str]] = {}
    for line in lines:
        head, sep, rest = line.partition(":")
        prefix = head.strip().upper()
        if sep and prefix in known and rest.strip():
            out.setdefault(prefix, []).append(rest.strip())
        else:
            out.setdefault("ORG", []).append(line)
    return out


def _flag(key: str, default: bool) -> bool:
    value = cfg(key, default)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "off", "no")
    return bool(value)


class Detectors:
    """Builds the session map with its recognizers and feeds it the static value sources."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.resolved = resolve()
        self.profile = self.resolved.profile
        self.fuzzy = _flag("client_guard.fuzzy", True)
        self.store = ValueStore(self.home) if _flag("client_guard.remember", True) else None
        self.ner: Any = None
        self.ner_error: str | None = None
        self.ner_unavailable = False  # NER wanted (profile / config) but the package is missing
        allow = [str(a) for a in (cfg("client_guard.allow_terms", []) or [])]
        allow += _read_lines(self.home / ALLOW_LIST)
        self.allow = allow
        if self._ner_requested() and importlib.util.find_spec("gliner") is None:
            self.ner_unavailable = True
        elif self._ner_requested():
            try:
                from core.client_guard.ner import (  # noqa: PLC0415
                    DEFAULT_CONFIDENT,
                    DEFAULT_MODEL,
                    DEFAULT_THRESHOLD,
                    NerRecognizer,
                )

                threads = int(cfg("client_guard.ner.threads", 0)) or max(
                    2, min(12, (os.cpu_count() or 4) // 2)
                )
                int8 = _flag("client_guard.ner.int8", False)
                loader = None
                if _flag("client_guard.ner.worker", True):
                    from client_gateway.ner_worker import WorkerModel  # noqa: PLC0415

                    onnx = "" if not _flag("client_guard.ner.onnx", True) else None

                    def _worker(name: str) -> WorkerModel:  # the model runs in its own process
                        return WorkerModel(name, threads=threads, int8=int8, onnx_dir=onnx)

                    loader = _worker

                self.ner = NerRecognizer(
                    ordinary_word,
                    model_name=str(cfg("client_guard.ner.model", DEFAULT_MODEL)),
                    threshold=float(cfg("client_guard.ner.threshold", DEFAULT_THRESHOLD)),
                    confident=float(cfg("client_guard.ner.confident", DEFAULT_CONFIDENT)),
                    allow=allow,
                    default_allow=self.profile.allow_terms,
                    # only the first masked request after "masking on" waits for the worker
                    wait_s=float(cfg("client_guard.ner.wait_s", 5)),
                    # the most model time the new text of one request gets (then it goes on)
                    max_s=float(cfg("client_guard.ner.max_s", 8)),
                    threads=threads,
                    loader=loader,
                    budget_s=float(cfg("client_guard.ner.budget_s", 2.0)),
                    idle_s=float(cfg("client_guard.ner.idle_s", 1200)),
                )
            except Exception as exc:  # noqa: BLE001 - reported in status
                self.ner_error = type(exc).__name__
        self.static_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def _ner_requested(self) -> bool:
        """Name recognition is switched on: not ``FINVISION_GATEWAY_NER=0`` and the config value
        (default: the profile's) is true. Whether the package is installed is checked apart."""
        if os.environ.get(NER_ENV, "").strip().lower() in ("0", "false", "off"):
            return False
        return _flag("client_guard.ner.enabled", self.profile.ner_default)

    def warnings(self) -> list[str]:
        """Fixed codes for weak detector states (the gateway adds them to its own warnings):
        an unknown profile, an unknown shape pack, name recognition wanted but not installed or
        failed to load."""
        out = list(self.resolved.warnings)
        if self.ner_unavailable:
            out.append("ner_unavailable")
        elif self.ner_error or (self.ner is not None and str(self.ner.state).startswith("failed")):
            out.append("ner_load_failed")
        return out

    def session_map(self, token_fn: Callable[[str, str], int] | None) -> SessionMap:
        return SessionMap(
            token_fn=token_fn,
            recognizers=[self.ner] if self.ner is not None else [],
            fuzzy=self.fuzzy,
            on_recognized=self.remember_recognized,
            profile=self.profile,
            shape_packs=self.resolved.shape_packs,
            never=self.allow,
        )

    def remember_recognized(self, fresh: list[tuple[str, str]]) -> None:
        """Names the recognizer found that no model holds: kept in the DPAPI store, so they stay
        masked after a gateway restart (a resumed chat resends its whole history)."""
        by_prefix: dict[str, set[str]] = {}
        for surface, prefix in fresh:
            by_prefix.setdefault(prefix, set()).add(surface)
        self.remember([(prefix, False, values) for prefix, values in by_prefix.items()])

    def start(self, smap: SessionMap) -> None:
        """Gateway start: load remembered values and the client list into the map. The NER worker
        is NOT started here: it starts when a chat switches masking on (``masking_on``), so a
        gateway that only passes traffic through never loads a model."""
        self.load_static(smap)

    def masking_on(self) -> None:
        """A chat switched masking on: start the NER worker (idempotent, in the background)."""
        if self.ner is not None:
            self.ner.start()

    def load_static(self, smap: SessionMap) -> None:
        counts: dict[str, int] = {}
        if self.store is not None:
            remembered = self.store.load()
            for (prefix, strong), values in sorted(
                remembered.items(),
                key=lambda kv: (
                    0 if kv[0][0] not in GENERIC_PREFIXES else 1 if kv[0][1] else 2,
                    kv[0][0],
                ),
            ):
                smap.add_values(prefix, values, "remembered", strong=strong)
                counts["remembered"] = counts.get("remembered", 0) + len(values)
        for prefix, values in parse_client_list(_read_lines(self.home / CLIENT_LIST)).items():
            smap.add_values(prefix, values, "client_list")
            counts["client_list"] = counts.get("client_list", 0) + len(values)
        with self._lock:
            self.static_counts = counts

    def remember(self, batches: list[tuple[str, bool, set[str]]]) -> int:
        if self.store is None:
            return 0
        try:
            return self.store.remember(batches)
        except Exception as exc:  # noqa: BLE001 - never breaks a detection cycle
            self.store.error = type(exc).__name__
            return 0

    def status(self) -> dict[str, Any]:
        with self._lock:
            static = dict(self.static_counts)
        ner: dict[str, Any] = {"enabled": self.ner is not None}
        if self.ner is not None:
            ner.update(
                state=self.ner.state,
                model=self.ner.model_name,
                threshold=self.ner.threshold,
                **self.ner.stats,
            )
        elif self.ner_unavailable:
            ner["error"] = "unavailable"
        elif self.ner_error:
            ner["error"] = self.ner_error
        return {
            "profile": self.profile.name,
            "dictionary": "on",
            "remembered": {
                "enabled": self.store is not None,
                "values": self.store.count() if self.store is not None else 0,
                "error": self.store.error if self.store is not None else None,
            },
            "client_list": static.get("client_list", 0),
            "fuzzy": self.fuzzy,
            "ner": ner,
            "allow_terms": len(self.allow),
            "shapes": "on",
            "shape_packs": sorted(self.resolved.shape_packs),
        }
