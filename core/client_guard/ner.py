"""Named-entity recognizer for client names that no open model holds (GLiNER, local, CPU).

The dictionary only knows the values of the Power BI models it read; a name in a pasted e-mail, a
note or a document is caught here. A recognizer proposes ``(surface, prefix)`` pairs; the engine
turns each into a dictionary value (``SessionMap(recognizers=[...])``), so a name found once is
masked everywhere afterwards and gets one stable pseudonym.

Pipeline per string (the Presidio pattern: model, then allow list and filters):

1. ``prose_segments`` keeps natural-language lines only. JSON, DAX, xmSQL and code are skipped:
   their client values come from the dictionary, and a NER model reads identifiers as names.
2. GLiNER (``labels`` person / organization / company, ``threshold``) on chunks of at most
   ``MAX_CHARS`` characters, batched.
3. ``name_part`` cuts a lower-case lead off a span ("familie De Smet" -> "De Smet", "le cabinet
   Moreau Avocats" -> "Moreau Avocats"); then ``keep`` drops what is not a client name:
   allow-listed terms (products, tools, our own companies, configured terms), anything shaped like
   a model object (``_ [ ] | ( )``), phrases that are not written as a proper noun, and phrases of
   ordinary words only - unless the model is confident (``confident``, default 0.9) about a name
   of two or more words ("Silver Lake Farms") or a surname with its particle ("De Wit"; one bare
   ordinary word such as "Noord" or "Baker" stays dropped), or it is a person written as two or
   more capitalised words ("Tom Smit"). Measured on a synthetic NL/FR/EN set (2026-10-08): the negatives that rule removes
   ("Client", "Ik") scored at most 0.76, the lost names mostly 0.9 and more.

The model loads in a background thread; ``find`` waits for it up to ``wait_s`` seconds.

Two ways to call it:

* ``complete=True`` (a masked chat): every new prose segment is recognized, however many there
  are, within ``max_s`` seconds. When the model is not there, the time runs out or the worker
  fails, ``NerIncomplete`` is raised and the caller fails closed (nothing is sent). Nothing is
  ever skipped silently.
* the default (the safety net of an unmasked chat): at most ``MAX_NEW_PER_CALL`` new segments
  within ``budget_s``; what does not fit is skipped and counted (``over_budget``, ``timeouts``,
  ``skipped``), because that chat must not wait for a model.

Results are cached per segment.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from typing import Any

from core.client_guard.engine import _LEGAL_FORMS, is_background
from core.client_guard.profiles import FINVISION_ALLOW, resolve
from core.config.ner_defaults import DEFAULT_MODEL

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.5
DEFAULT_CONFIDENT = 0.9  # an all-ordinary-words span is kept from this score on (module text)
LABELS = ("person", "organization", "company")
PREFIX = {"person": "PER", "organization": "ORG", "company": "ORG"}
MAX_CHARS = 1200
# Bound actual model inputs, including chunks expanded from one long prose block.
# What is over budget is skipped and counted (status: over_budget).
MAX_NEW_PER_CALL = 12
BATCH_CHUNKS = 8
CACHE_SIZE = 50_000
MAX_S = 60.0  # complete mode: the most model time one request may take before it fails closed
RETRY_S = 30.0  # a model that failed to load is tried again after this (doubling, up to 10 minutes)
MAX_RETRY_S = 600.0


class NerIncomplete(RuntimeError):
    """Complete mode: not every new segment could be recognized (the caller fails closed)."""


# products, tools, platforms and our own companies: organisations, never clients. The list is the
# profile's (``profiles.py``); this name is the ``finvision`` one, which an explicit
# ``default_allow`` / the active profile replaces for a recognizer.
DEFAULT_ALLOW = FINVISION_ALLOW
PARTICLES = frozenset(
    "van de der den het ter ten te von zu le la les du des d' di da del dos y & en and et of".split()
)
_OBJECT_SHAPE = re.compile(r"[_\[\]|(){}<>=/\\@#]")
_CODE_CHARS = set("{}[]();=<>|\\`$")
_WORDS = re.compile(r"[^\W\d_][\w'’\-]*")
_LINE_NO = re.compile(r"^\s*\d+[\t→│|]\s?")


SHORT_TEXT_CHARS = 200
_CELL_SPLIT = re.compile(r"[|\t;]")
_NAME_CELL = re.compile(r"^[^\W\d_][\w'’.\-]*(?: (?:[^\W\d_][\w'’.\-]*|&))+$")


def _name_cells(line: str) -> list[str]:
    """Name-like cells of a table row or ``key=value;`` line: two or more capitalised words and
    nothing else ("Marnix Verbeke", "Acme Holding NV"). A Markdown / TSV / CSV-ish row never
    passes ``_is_prose`` (short cells, ``|`` and ``=``), so a name in a table cell would never
    reach the model."""
    if not (_CELL_SPLIT.search(line) or "=" in line):
        # a line that is only a name (an e-mail signature, "Grace Liu"): too short for prose
        words = _WORDS.findall(line)
        if (
            2 <= len(words) <= 5
            and _NAME_CELL.match(line)
            and all(w[:1].isupper() or w.casefold() in PARTICLES for w in words)
        ):
            return [line]
        return []
    cells: list[str] = []
    for part in _CELL_SPLIT.split(line):
        part = part.rsplit("=", 1)[-1].strip().strip("\"'")
        words = _WORDS.findall(part)
        if (
            len(words) >= 2
            and _NAME_CELL.match(part)
            and all(w[:1].isupper() or w.casefold() in PARTICLES for w in words)
        ):
            cells.append(part)
    return cells


def prose_segments(text: str, min_words: int = 3) -> list[str]:
    """Natural-language lines of ``text`` (merged into paragraphs); code and data are left out.
    A short message (a bare name, a two-word reply) counts with two words; name-like cells of
    table rows are segments of their own."""
    out: list[str] = []
    buf: list[str] = []
    lines = text.splitlines()
    if len(text) <= SHORT_TEXT_CHARS and len([ln for ln in lines if ln.strip()]) <= 3:
        min_words = min(min_words, 2)
    for raw in lines:
        line = _LINE_NO.sub("", raw).strip()
        if line and _is_prose(line, min_words):
            buf.append(line)
            continue
        if buf:
            out.append(" ".join(buf))
            buf = []
        if line:
            out.extend(_name_cells(line))
    if buf:
        out.append(" ".join(buf))
    return out


_ADDRESSY = re.compile(r"<?[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+>?|https?://\S+|www\.\S+")


def _is_prose(line: str, min_words: int) -> bool:
    line = _ADDRESSY.sub(" ", line)  # an e-mail header line ("Van: name <address>") is prose
    words = _WORDS.findall(line)
    if len(words) < min_words:
        return False
    visible = [c for c in line if not c.isspace()]
    if not visible:
        return False
    letters = sum(c.isalpha() for c in visible)
    code = sum(c in _CODE_CHARS for c in visible)
    if letters / len(visible) < 0.6 or code / len(visible) > 0.04:
        return False
    low = line.lower()
    return not low.startswith(
        (
            "select ",
            "evaluate ",
            "define ",
            "def ",
            "import ",
            "from ",
            "return ",
            "var ",
            "set dc_kind",
        )
    )


def _chunks(segment: str) -> list[str]:
    if len(segment) <= MAX_CHARS:
        return [segment]
    parts = re.split(r"(?<=[.!?;])\s+", segment)
    out, cur = [], ""
    for p in parts:
        while len(p) > MAX_CHARS:  # one very long sentence: hard cut on a space
            cut = p.rfind(" ", 0, MAX_CHARS) or MAX_CHARS
            out.append(p[:cut])
            p = p[cut:].lstrip()
        if len(cur) + len(p) + 1 > MAX_CHARS and cur:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return out


def _pack_segments(segments: list[str]) -> list[str]:
    """Pack short prose runs into model inputs without cutting a name across inputs."""
    out: list[str] = []
    current: list[str] = []
    size = 0
    for segment in segments:
        if size and size + len(segment) + 1 > MAX_CHARS:
            out.append("\n".join(current))
            current, size = [], 0
        if len(segment) > MAX_CHARS:
            out.append(segment)  # _chunks splits this sentence at a space later
            continue
        current.append(segment)
        size += len(segment) + (1 if size else 0)
    if current:
        out.append("\n".join(current))
    return out


def name_part(surface: str) -> str:
    """The span from its last capitalised stretch on: a lower-case lead that is not a name
    particle ("familie", "mevrouw", "le cabinet") is cut, particles before a capitalised word stay
    ("van der Berg"). A span with no capitalised word comes back unchanged (``keep`` drops it)."""
    words = surface.split()
    cut = 0
    for i, word in enumerate(words):
        letters = word.split("'")[-1] if "'" in word[:3] else word  # d'Ursel, l'Oréal
        first = next((c for c in letters if c.isalpha()), "")
        if first and first.islower() and word.casefold() not in PARTICLES:
            cut = i + 1
    rest = words[cut:]
    if cut == 0 or not any(next((c for c in w if c.isalpha()), "").isupper() for w in rest):
        return surface
    return " ".join(rest)


def keep(
    surface: str,
    label: str,
    ordinary: Callable[[str], bool],
    allow: frozenset[str],
    score: float | None = None,
    confident: float = DEFAULT_CONFIDENT,
) -> bool:
    """Is this span a client name (True) or report text / a product / prose (False)?
    ``score``: the model's confidence; a span of ordinary words only is kept from ``confident``
    on (without a score the old rule stands)."""
    s = surface.strip(" .,:;!?\"'“”‘’()")
    if len(s) < 3 or s.casefold() in allow or _OBJECT_SHAPE.search(s):
        return False
    words = _WORDS.findall(s)
    if not words:
        return False
    # An allow-listed word at the start or end ("Azure Data Factory", "Microsoft Teams Rooms") makes
    # a PRODUCT, but only for an organization without a legal form: a person called Isabel or
    # Claude, and a company such as "Excel Logistics NV" or "Windows & Doors Verhaeghe BV", are
    # clients.
    if label != "person" and not any(w.casefold() in _LEGAL_FORMS for w in words):
        low = s.casefold()
        if any(low.startswith(a + " ") or low.endswith(" " + a) for a in allow):
            return False
    content = [w for w in words if w.casefold() not in PARTICLES]
    if not content or not all(w[:1].isupper() for w in content):
        return False  # not written as a proper noun ("de bakker op de hoek")
    if all(ordinary(w.casefold()) for w in content):
        if label == "person" and len(content) >= 2:
            return True  # "Tom Smit"
        # a confident name of two or more words ("Silver Lake Farms", "Zwart Vastgoed") or a
        # surname with its particle ("De Wit"); ONE bare ordinary word never, however sure the
        # model is: "Noord" in "while Noord stayed flat" scored 0.99 as a person
        # (tools/ner_bench.py), and a recognized name is remembered
        if score is None or score < confident:
            return False
        return len(content) >= 2 or (label == "person" and len(words) > len(content))
    return True


class NerRecognizer:
    """GLiNER-based recognizer (``find(text) -> [(surface, prefix)]``)."""

    def __init__(
        self,
        ordinary: Callable[[str], bool],
        model_name: str = DEFAULT_MODEL,
        threshold: float = DEFAULT_THRESHOLD,
        allow: Iterable[str] = (),
        confident: float = DEFAULT_CONFIDENT,
        wait_s: float = 120.0,
        threads: int = 4,
        loader: Callable[[str], Any] | None = None,
        strict: bool = False,
        budget_s: float = 2.0,
        max_s: float = MAX_S,
        idle_s: float = 0.0,
        retry_s: float = RETRY_S,
        default_allow: frozenset[str] | None = None,
    ) -> None:
        """``default_allow``: the built-in allow list (lower-case); default is the active
        profile's (``core.client_guard.profiles``). ``allow`` adds configured terms to it."""
        self.model_name = model_name
        self.strict = strict
        self.budget_s = budget_s  # safety-net model time per request; the rest is skipped
        self.max_s = max_s  # complete mode: hard limit, then NerIncomplete
        self.threshold = threshold
        self.confident = confident
        base = default_allow if default_allow is not None else resolve().profile.allow_terms
        self.allow = base | frozenset(a.casefold() for a in allow)
        self.ordinary = ordinary
        self.wait_s = wait_s
        self.threads = threads
        self._loader = loader
        self._model: Any = None
        self._error: str | None = None
        self._ready = threading.Event()
        self._started = False
        self.idle_s = idle_s  # unload the model after this many idle seconds (0 = never)
        self.keep_alive: Callable[[], bool] | None = None  # True while a masked chat needs it
        self._last_used = time.monotonic()
        self._idle_timer: threading.Timer | None = None
        self._retry_base = retry_s
        self._retry_s = retry_s
        self._retry_at = 0.0
        self._start_lock = threading.Lock()
        self._predict_lock = threading.Lock()
        self._fg_cond = threading.Condition()
        self._fg_waiting = 0  # new-text calls waiting for the worker: background work yields
        self._cache: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self.stats = {
            "segments": 0,
            "cache_hits": 0,
            "entities": 0,
            "dropped": 0,
            "skipped": 0,
            "over_budget": 0,
            "timeouts": 0,
            "worker_errors": 0,
            "incomplete": 0,
        }

    # ------------------------------------------------------------------ loading
    def start(self) -> None:
        """Load the model in a background thread (idempotent). A load that FAILED is tried again
        once its backoff has passed: one HF-hub timeout or one out-of-memory must not leave every
        masked request refused until the gateway restarts."""
        with self._start_lock:
            if self._started:
                failed = self._ready.is_set() and self._model is None
                if not (failed and time.monotonic() >= self._retry_at):
                    return
                self._ready.clear()
                self._error = None
            self._started = True
        threading.Thread(target=self._load, name="ner-load", daemon=True).start()

    def _load(self) -> None:
        try:
            if self._loader is not None:
                self._model = self._loader(self.model_name)
            else:
                import torch  # noqa: PLC0415 - heavy, only when the recognizer is used
                from gliner import GLiNER  # noqa: PLC0415

                torch.set_num_threads(max(1, self.threads))
                self._model = GLiNER.from_pretrained(self.model_name)
            self._retry_s = self._retry_base
        except Exception as exc:  # noqa: BLE001 - reported through status, never the text
            self._error = type(exc).__name__
            self._retry_at = time.monotonic() + self._retry_s
            self._retry_s = min(self._retry_s * 2, MAX_RETRY_S)
            logger.warning("NER model did not load (%s)", self._error)
        finally:
            self._ready.set()

    @property
    def state(self) -> str:
        if not self._started:
            return "not_started"
        if not self._ready.is_set():
            return "loading"
        return "ready" if self._model is not None else f"failed ({self._error})"

    def ready(self) -> bool:
        return self._model is not None

    # ------------------------------------------------------------------ idle unload
    def _touch(self) -> None:
        """The model was used: (re)arm the idle timer."""
        self._last_used = time.monotonic()
        if self.idle_s <= 0 or self._model is None or self._idle_timer is not None:
            return
        timer = threading.Timer(self.idle_s, self._idle_check)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _idle_check(self) -> None:
        self._idle_timer = None
        idle = time.monotonic() - self._last_used
        if idle < self.idle_s * 0.99 or (self.keep_alive is not None and self.keep_alive()):
            timer = threading.Timer(max(1.0, self.idle_s - idle), self._idle_check)
            timer.daemon = True
            self._idle_timer = timer
            timer.start()
            return
        self.unload()

    def unload(self) -> None:
        """Stop the model (its worker process ends); the next ``start`` loads it again."""
        with self._start_lock:
            model, self._model = self._model, None
            self._started = False
            self._ready.clear()
            self._error = None
        close = getattr(model, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - best effort
                pass

    # ------------------------------------------------------------------ finding
    def find(self, text: str) -> list[tuple[str, str]]:
        return self.find_many([text])

    def find_many(
        self,
        texts: list[str],
        budget_s: float | None = None,
        wait: bool = True,
        complete: bool = False,
    ) -> list[tuple[str, str]]:
        """Recognize all request text blocks with shared model batches and segment cache.
        ``complete=True``: everything new or ``NerIncomplete`` (see the module text).
        Otherwise ``budget_s`` overrides the model time budget and ``wait=False`` never waits for
        a model that is still loading (the unmasked-chat safety net uses both)."""
        segments = [
            chunk
            for segment in _pack_segments([seg for text in texts for seg in prose_segments(text)])
            for chunk in _chunks(segment)
        ]
        if not segments:
            return []
        if self._model is None:
            self.start()  # also retries a load that failed earlier, once its backoff has passed
            if not self._ready.wait(self.wait_s if wait else 0) or self._model is None:
                if complete:
                    self.stats["incomplete"] += 1
                    raise NerIncomplete(
                        "model_loading" if not self._ready.is_set() else "model_unavailable"
                    )
                if self.strict:
                    raise RuntimeError("NER model not available")
                self.stats["skipped"] += 1  # degraded: dictionary, typos and shapes still mask
                return []
        out: list[tuple[str, str]] = []
        todo: list[tuple[str, str]] = []
        seen: set[str] = set()
        for seg in segments:
            key = hashlib.blake2b(seg.encode("utf-8"), digest_size=16).hexdigest()
            with self._cache_lock:
                hit = self._cache.get(key)
                if hit is not None:
                    self._cache.move_to_end(key)
            if hit is not None:
                self.stats["cache_hits"] += 1
                out.extend(hit)
            elif key not in seen:
                todo.append((key, seg))
                seen.add(key)
        if len(todo) > MAX_NEW_PER_CALL and not complete:
            self.stats["over_budget"] += len(todo) - MAX_NEW_PER_CALL
            todo = todo[-MAX_NEW_PER_CALL:]  # the newest text (the end of the message) first
        limit = self.max_s if complete else (self.budget_s if budget_s is None else budget_s)
        done = self._predict_many([seg for _, seg in todo], time.monotonic() + limit)
        self._touch()
        for (key, _), found in zip(todo, done):  # only what finished in time (and is cached)
            with self._cache_lock:
                self._cache[key] = found
                if len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
            out.extend(found)
        if complete and len(done) < len(todo):
            self.stats["incomplete"] += 1
            raise NerIncomplete("timeout_or_worker")  # a retry only runs the unfinished part
        return out

    def _call_model(self, texts: list[str], deadline: float) -> list[Any]:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("NER budget used up")
        model = self._model
        if getattr(model, "accepts_timeout", False) or type(model).__name__ == "WorkerModel":
            return model.batch_predict_entities(
                texts, list(LABELS), threshold=self.threshold, timeout=left
            )
        if hasattr(model, "batch_predict_entities"):
            return model.batch_predict_entities(texts, list(LABELS), threshold=self.threshold)
        return [model.predict_entities(c, list(LABELS), threshold=self.threshold) for c in texts]

    def _acquire_worker(self, deadline: float) -> bool:
        """The worker, within ``deadline``. A best-effort (background) call waits while any new-text
        call wants the worker, so the new message of a chat is never queued behind history scans."""

        def _left() -> float:
            return -1 if deadline == float("inf") else max(0.0, deadline - time.monotonic())

        if not is_background():
            with self._fg_cond:
                self._fg_waiting += 1
            try:
                return self._predict_lock.acquire(timeout=_left())
            finally:
                with self._fg_cond:
                    self._fg_waiting -= 1
                    self._fg_cond.notify_all()
        with self._fg_cond:
            while self._fg_waiting > 0:
                left = _left()
                if left == 0:
                    return False
                self._fg_cond.wait(0.5 if left < 0 else min(left, 0.5))
        return self._predict_lock.acquire(timeout=_left())

    def _predict_many(
        self, segments: list[str], deadline: float = float("inf")
    ) -> list[list[tuple[str, str]]]:
        """Results for the leading segments that finished within ``deadline`` (a shorter list
        when the budget ran out or the worker failed; non-strict only)."""
        self.stats["segments"] += len(segments)
        indexed = [(i, chunk) for i, seg in enumerate(segments) for chunk in _chunks(seg)]
        out: list[list[tuple[str, str]]] = [[] for _ in segments]
        complete = len(segments)
        for start in range(0, len(indexed), BATCH_CHUNKS):
            batch = indexed[start : start + BATCH_CHUNKS]
            texts = [chunk for _, chunk in batch]
            try:
                if not self._acquire_worker(deadline):
                    raise TimeoutError("worker_busy")
                try:
                    results = self._call_model(texts, deadline)
                finally:
                    self._predict_lock.release()
            except TimeoutError:
                if self.strict:
                    raise
                self.stats["timeouts"] += 1
                complete = batch[0][0]
                break
            except Exception:  # noqa: BLE001 - worker down / crashed
                if self.strict:
                    raise
                self.stats["worker_errors"] += 1
                complete = batch[0][0]
                break
            if len(results) != len(batch):
                raise RuntimeError("NER returned an incomplete batch")
            for (index, _), ents in zip(batch, results):
                for ent in ents:
                    surface = name_part(str(ent.get("text", "")))
                    label = str(ent.get("label", ""))
                    raw_score = ent.get("score")
                    score = float(raw_score) if isinstance(raw_score, (int, float)) else None
                    if label in PREFIX and keep(
                        surface, label, self.ordinary, self.allow, score, self.confident
                    ):
                        out[index].append((surface.strip(" .,:;!?\"'“”‘’()"), PREFIX[label]))
                        self.stats["entities"] += 1
                    else:
                        self.stats["dropped"] += 1
        return out[:complete]
