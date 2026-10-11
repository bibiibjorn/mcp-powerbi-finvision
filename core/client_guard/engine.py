"""Client Guard masking engine: reversible pseudonymisation of client identifiers.

One machine-wide ``SessionMap`` (owned by the gateway process, shared by every chat). Real values
are replaced by stable tokens (``FAM_0007``) on the way out and restored on the way back in. The
map lives in memory only: this module never writes to disk, never logs and never puts a real value
in an exception message.

Matching (spec section 3):

* NFKC + ``casefold()`` + the Unicode TR39 confusables fold (Cyrillic a / Greek o
  become Latin a / o; NFKC does not do that) + whitespace collapse (NBSP, Hangul filler
  included) on a normalised copy of the text; Aho-Corasick over that copy; an offset map takes
  each hit back to the original.
* Boundary tiers by normalised value length: 6+ characters match anywhere (also inside words),
  4-5 characters match whole words only, shorter values and common words match only when they
  are the WHOLE string.
* Shape detectors (e-mail, IBAN, E.164 phone, plus the country packs the profile turns on, for
  example the Belgian national / enterprise number) run on every mask and add unknown hits to the
  map (fail closed).
"""

from __future__ import annotations

import collections
import contextlib
import functools
import json
import random
import re
import threading
import unicodedata
from bisect import bisect_right
from pathlib import Path
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, Optional
from urllib.parse import quote as url_quote
from urllib.parse import quote_plus

import ahocorasick  # type: ignore[import-not-found]

from core.client_guard.common_words import COMMON_WORDS, EVERYDAY_WORDS
from core.client_guard.profiles import GENERIC_PREFIXES, PART_PREFIXES, Profile, resolve
from core.client_guard.shapes import find_shapes, iban_is_valid

try:  # typo matching needs rapidfuzz; without it the map still masks exact values
    from core.client_guard.fuzzy import FuzzyIndex
except ImportError:  # pragma: no cover - dependency missing
    FuzzyIndex = None  # type: ignore[assignment,misc]

MASK_FAILED = "Client Guard: masking failed"
UNMASK_FAILED = "Client Guard: unmasking failed"

Context = Literal["plain", "json"]

SKIP_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking", "image", "document"})
SKIP_KEYS = frozenset({"signature", "data"})

# A value of an opaque-payload key is only left alone when it really looks like one (base64 /
# base64url, no spaces): a structured tool input that happens to use the key "data" must still
# be masked.
_OPAQUE_PAYLOAD = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")

_UNICODE_WS = "\\s\u3164\u115f\u1160\uffa0\u200b\u2800\u180e"
_WS_RUN = re.compile(f"[{_UNICODE_WS}]+")
# invisible format characters (Unicode category Cf: soft hyphen, ZWNJ / ZWJ, word joiner, BOM, bidi
# marks ...) are dropped, so "Acme<soft hyphen>Holding" from a PDF or a Word copy still matches
# (U+200B is whitespace above and keeps counting as a separator)
_FORMAT_CHARS = re.compile(
    "[\u00ad\u0600-\u0605\u061c\u06dd\u070f\u08e2\u200c-\u200f\u202a-\u202e"
    "\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb\U000110bd\U0001bca0-\U0001bca3"
    "\U0001d173-\U0001d17a\U000e0001\U000e0020-\U000e007f]"
)


def _load_confusables() -> dict[int, str]:
    """The TR39 look-alike table (``data/confusables_latin.txt``, made by
    ``tools/masking_lab/gen_confusables.py``) as a ``str.translate`` table; empty when the file is
    missing or unreadable (matching then simply keeps exact-script spellings only)."""
    path = Path(__file__).with_name("data") / "confusables_latin.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    table: dict[int, str] = {}
    for line in lines:
        if not line or line.startswith("#"):
            continue
        code, _, target = line.partition("\t")
        try:
            point = int(code, 16)
        except ValueError:
            continue
        # never touch ASCII, and never a character the whitespace / format passes own
        if point < 0x80 or len(target) != 1 or not target.isascii():
            continue
        char = chr(point)
        if _WS_RUN.fullmatch(char) or _FORMAT_CHARS.fullmatch(char):
            continue
        table[point] = target
    return table


_CONFUSABLES = str.maketrans(_load_confusables())
_ASCII_WS_TABLE = str.maketrans({c: " " for c in "\t\n\v\f\r\x1c\x1d\x1e\x1f"})
_SPACE_RUN = re.compile(" {2,}")
_ASCII_UNITS = re.compile(r"\s{2,}")
_UNITS = re.compile(f"([{_UNICODE_WS}]{{2,}})|([\\x21-\\x7e]?[^\\x00-\\x7f{_UNICODE_WS}]+)")

# a pseudonym as the model may write it back: lower-cased (a file name), or with a Markdown escape
# (``CUS\_48213``, ``\_`` inside a JSON string)
_LOOSE_TOKEN = re.compile(
    r"(?<![0-9A-Za-z\\])([A-Za-z]{2,4})(?:\\{1,2})?_(\d{4,})([ULul]?)(?![0-9A-Za-z])"
)
_GENERIC_TOKEN = re.compile(r"(?<![0-9A-Za-z_])[A-Z]{2,4}_\d{4,}[UL]?(?![0-9A-Za-z])")
_DIGIT_RUN = re.compile(r"\d+")
_ESCAPE_OR_QUOTE = re.compile(r'(?s)\\.|"')
# a pseudonym as it appears in NORMALISED (lower-case) text: PREFIX_digits (the optional u / l case
# suffix is checked separately)
_TOKEN_SHAPE = re.compile(r"(?<![0-9a-z])[a-z]{2,4}_\d{4,}")
_LETTER_RUN = re.compile(r"[^\W\d_]+")
# GENERIC_PREFIXES (generic dimension values and GL account names: report vocabulary rather than
# names, so a phrase of ordinary words only is matched as a whole cell, see SessionMap._tier) and
# PART_PREFIXES (identity prefixes whose values are split into distinctive parts, see
# SessionMap._add_parts) are the union over all profiles: ``core.client_guard.profiles``.
PART_MAX_SHARED = 2
_PART_WORD = re.compile(r"[^\W\d_][^\W\d_'’\-]{3,}")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;\n\r\t•*#>|]|(?:^|\s)[-–])[\s\"'“‘(\[]*$")
_LINE_WORD = re.compile(r"[^\W\d_]{4,}")


_IDENTIFIER_SEPARATORS = frozenset("_-./\\")

# Name recognition runs in one worker shared by every chat. Best-effort work (the earlier
# conversation of a chat masked for the first time) marks itself with ``background_priority`` and
# yields to the NEW text of any chat: after a gateway restart the history scans of several long
# chats kept the worker busy and a new message waited behind them (2026-10-07).
_PRIORITY = threading.local()


@contextlib.contextmanager
def background_priority() -> Iterator[None]:
    previous = getattr(_PRIORITY, "background", False)
    _PRIORITY.background = True
    try:
        yield
    finally:
        _PRIORITY.background = previous


def is_background() -> bool:
    return bool(getattr(_PRIORITY, "background", False))


def _identifier_context(norm: str, start: int, end: int) -> bool:
    """Is the span glued to a path / file-name / identifier separator (underscore, hyphen, dot,
    slash, backslash)? There a
    lower-case surname or acronym ("c:/clients/peeters/q3.xlsx", "vanhoutte_q3") is the name
    whatever its capitalisation."""
    return (start > 0 and norm[start - 1] in _IDENTIFIER_SEPARATORS) or (
        end < len(norm) and norm[end] in _IDENTIFIER_SEPARATORS
    )


def _inside_sentence(s: str, start: int) -> bool:
    """Is ``s[start:]`` a word inside running text: not the first word of a sentence, a line,
    a bullet or a cell, and not in a title-case line (a heading: most words capitalised)? An
    everyday word that is part of a client name ("Lambert") is a name only there."""
    if _SENTENCE_START.search(s, max(0, start - 12), start) is not None:
        return False
    a = s.rfind("\n", 0, start) + 1
    b = s.find("\n", start)
    words = _LINE_WORD.findall(s[a : b if b >= 0 else len(s)])
    capital = sum(w[:1].isupper() for w in words)
    return not (len(words) >= 3 and capital >= 0.7 * len(words))


_LEGAL_FORMS = frozenset(
    "bvba bv nv sa srl sprl cvba cv vof commv comm gcv ltd llc inc gmbh ag plc sarl sas spa "
    "holding group groep groupe invest management services consulting consult partners".split()
)


@functools.lru_cache(maxsize=1)
def _everyday_keys() -> frozenset[str]:
    """EVERYDAY_WORDS normalised once per process (21k words, shared by every SessionMap)."""
    return frozenset(normalise_key(w) for w in EVERYDAY_WORDS)


@functools.lru_cache(maxsize=1)
def _common_keys() -> frozenset[str]:
    return frozenset(normalise_key(w) for w in COMMON_WORDS)


def ordinary_word(word: str) -> bool:
    """An everyday EN/NL word or a curated common word (for recognizers that need the test)."""
    key = normalise_key(word)
    return key in _everyday_keys() or key in _common_keys()


def _same_class(a: str, b: str) -> bool:
    return (a.isalpha() and b.isalpha()) or (a.isdigit() and b.isdigit())


def _on_word_boundaries(text: str, start: int, end: int, n: int) -> bool:
    """A hit must not continue a word: no letter next to a letter, no digit next to a digit, on
    either side ("Insurance2024" still matches; "includes" does not match "include")."""
    if start > 0 and _same_class(text[start - 1], text[start]):
        return False
    return not (end < n and _same_class(text[end - 1], text[end]))


_SLUG_SEPARATORS = ("_", "-", ".", "%20", "+", "")
_MIN_VARIANT_LEN = 6  # shorter slug / encoded variants are not added (they would match too much)
MAX_ALIASES = 200_000  # per-surface alias tokens; beyond it the canonical token is used

_BATCH_RNG = random.SystemRandom()

# stable pseudonyms (token_fn): FAM_48213, five digits, 10000..99999
TOKEN_DIGITS = 5
TOKEN_MIN = 10_000
TOKEN_MAX = 99_999
TOKEN_SPAN = TOKEN_MAX - TOKEN_MIN + 1
# a range that is full continues here, past every key generation's range (core.client_guard.tokens)
TOKEN_OVERFLOW = 1_000_000


class MaskingError(Exception):
    """Masking (or unmasking) failed. The message is FIXED: it never carries input text."""

    def __init__(self, message: str = MASK_FAILED) -> None:
        super().__init__(message)


# --------------------------------------------------------------------------------------------
# Normalisation and offset map
# --------------------------------------------------------------------------------------------


def normalise(text: str) -> str:
    """NFKC, ``casefold()``, TR39 look-alikes folded to Latin, every whitespace run collapsed to
    one space (not stripped)."""
    if text.isascii():
        out = text.lower().translate(_ASCII_WS_TABLE)
        if "  " in out:
            out = _SPACE_RUN.sub(" ", out)
        return out
    return _normalise_unit(text)


def _strip_marks(text: str) -> str:
    """``text`` without combining marks (diacritics): "Zorvex" and the accented spelling of it
    are one name for matching. Hangul and other scripts that decompose into letters are put back
    together."""
    decomposed = unicodedata.normalize("NFD", text)
    if not any(unicodedata.combining(c) for c in decomposed):
        return text
    return unicodedata.normalize(
        "NFC", "".join(c for c in decomposed if not unicodedata.combining(c))
    )


def _normalise_unit(text: str) -> str:
    """The non-ASCII branch of ``normalise``: NFKC, casefold, diacritics dropped, cross-script
    look-alikes folded to Latin (TR39), invisible format characters dropped, whitespace runs
    collapsed (also used unit by unit by the offset map)."""
    folded = _strip_marks(unicodedata.normalize("NFKC", text).casefold()).translate(_CONFUSABLES)
    return _WS_RUN.sub(" ", _FORMAT_CHARS.sub("", folded))


def normalise_key(value: str) -> str:
    """Dictionary key of a value: ``normalise`` plus surrounding spaces stripped."""
    return normalise(value).strip(" ")


class _OffsetMap:
    """Maps spans of the normalised text back to the original.

    Only the non-linear stretches (whitespace runs, clusters that changed length under NFKC /
    casefold) are stored; everything between them is a 1:1 copy. A hit that touches a non-linear
    stretch expands to the whole stretch, so a masked span never cuts a character in half.
    """

    __slots__ = ("n_starts", "n_ends", "o_starts", "o_ends")

    def __init__(
        self, n_starts: list[int], n_ends: list[int], o_starts: list[int], o_ends: list[int]
    ) -> None:
        self.n_starts = n_starts
        self.n_ends = n_ends
        self.o_starts = o_starts
        self.o_ends = o_ends

    def start(self, pos: int) -> int:
        j = bisect_right(self.n_starts, pos) - 1
        if j < 0:
            return pos
        if pos < self.n_ends[j]:
            return self.o_starts[j]
        return pos + self.o_ends[j] - self.n_ends[j]

    def end(self, pos: int) -> int:
        """Original index just past the normalised character at ``pos - 1``."""
        last = pos - 1
        j = bisect_right(self.n_starts, last) - 1
        if j < 0:
            return last + 1
        if last < self.n_ends[j]:
            return self.o_ends[j]
        return last + 1 + self.o_ends[j] - self.n_ends[j]


def _build_offset_map(text: str, norm: str) -> _OffsetMap:
    """Rebuild the normalisation unit by unit; any inconsistency maps hits to the whole string."""
    ascii_only = text.isascii()
    pattern = _ASCII_UNITS if ascii_only else _UNITS
    n_starts: list[int] = []
    n_ends: list[int] = []
    o_starts: list[int] = []
    o_ends: list[int] = []
    parts: list[str] = []
    last = 0
    npos = 0

    def plain(gap: str) -> str:
        if ascii_only:
            return gap.lower().translate(_ASCII_WS_TABLE)
        return _WS_RUN.sub(" ", _FORMAT_CHARS.sub("", _strip_marks(gap.lower())))

    for m in pattern.finditer(text):
        a, b = m.span()
        if a > last:
            piece = plain(text[last:a])
            parts.append(piece)
            npos += len(piece)
        if ascii_only or m.group(1) is not None:
            piece = " "
        else:
            piece = _normalise_unit(m.group(0))
        parts.append(piece)
        if len(piece) != b - a:
            n_starts.append(npos)
            n_ends.append(npos + len(piece))
            o_starts.append(a)
            o_ends.append(b)
        npos += len(piece)
        last = b
    if last < len(text):
        parts.append(plain(text[last:]))
    if "".join(parts) != norm:
        return _OffsetMap([0], [len(norm)], [0], [len(text)])
    return _OffsetMap(n_starts, n_ends, o_starts, o_ends)


# --------------------------------------------------------------------------------------------
# Context helpers for unmasking (DAX literals)
# --------------------------------------------------------------------------------------------


def _single_quote_open(ctx: str) -> bool:
    """True when the position just after ``ctx`` is inside a single-quoted name.

    Apostrophes inside a word (``Don't``, ``D'Hondt``) are not quotes: an apostrophe is ignored
    when it sits between two alphanumerics (the character after the last one is the first
    letter of the token being inserted).
    """
    count = 0
    n = len(ctx)
    k = ctx.find("'")
    while k != -1:
        prev = ctx[k - 1] if k > 0 else ""
        nxt = ctx[k + 1] if k + 1 < n else "X"
        if not (prev.isalnum() and nxt.isalnum()):
            count += 1
        k = ctx.find("'", k + 1)
    return count % 2 == 1


def _literal_state(ctx: str, context: Context) -> tuple[bool, bool]:
    """(inside a double-quoted DAX literal, inside a single-quoted name) after ``ctx``.

    ``plain``: ``ctx`` is decoded text; a doubled quote toggles twice so it never disturbs the
    parity. ``json``: ``ctx`` is raw JSON string content, so DAX quotes appear as ``\\"`` and an
    unescaped quote starts a new JSON string (state resets).
    """
    if context == "plain":
        return ctx.count('"') % 2 == 1, _single_quote_open(ctx)
    start = 0
    dq = 0
    for m in _ESCAPE_OR_QUOTE.finditer(ctx):
        if m.group(0) == '"':
            start = m.end()
            dq = 0
        elif m.group(0) == '\\"':
            dq += 1
    return dq % 2 == 1, _single_quote_open(ctx[start:])


# --------------------------------------------------------------------------------------------
# Hit selection
# --------------------------------------------------------------------------------------------


def _fold_diacritics(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _slug_variants(surface: str) -> list[str]:
    """File-name, URL and identifier forms of a value: ``Janssens Holding`` also appears as
    ``Janssens_Holding.xlsx``, ``janssens-holding``, ``Janssens%20Holding`` or ``JanssensHolding``,
    and an accented name often travels without its accents. The text matcher folds case and
    whitespace only, so each form is a dictionary entry of its own (it stores that very spelling,
    so the round trip is exact). Variants shorter than ``_MIN_VARIANT_LEN`` are skipped."""
    base = " ".join(surface.split())
    plain = _fold_diacritics(base)
    forms = [base] if plain == base else [base, plain]
    out: list[str] = [] if plain == base else [plain]
    for form in forms:
        if " " in form:
            out.extend(form.replace(" ", sep) for sep in _SLUG_SEPARATORS)
        if not form.isascii() or " " in form:
            out.append(url_quote(form, safe=""))
            out.append(quote_plus(form))
    return [
        v for v in dict.fromkeys(out) if v != surface and len(normalise_key(v)) >= _MIN_VARIANT_LEN
    ]


def _person_forms(surface: str) -> list[str]:
    """Other ways to write a person: "Peeters, Jan", "Peeters Jan", "J. Peeters", "J.Peeters",
    "Jan P." for "Jan Peeters" (first token = first name, the rest = surname). A value that starts
    with an initial is already in that shape and gets none."""
    tokens = surface.split()
    if not 2 <= len(tokens) <= 4:
        return []
    first, rest = tokens[0], " ".join(tokens[1:])
    if len(first.rstrip(".")) < 2:
        return []
    forms = [
        f"{rest}, {first}",
        f"{rest} {first}",
        f"{first[0]}. {rest}",
        f"{first[0]}.{rest}",
        f"{first} {rest[0]}.",
    ]
    return [
        f
        for f in dict.fromkeys(forms)
        if f != surface and len(normalise_key(f)) >= _MIN_VARIANT_LEN
    ]


def _cosmetic(text: str) -> str:
    """``text`` with Unicode composition and whitespace runs folded, CASE kept."""
    return _WS_RUN.sub(" ", unicodedata.normalize("NFKC", text))


def _select(hits: list[tuple[int, int, Any]]) -> list[tuple[int, int, Any]]:
    """Leftmost-longest, non-overlapping."""
    hits.sort(key=lambda h: (h[0], h[0] - h[1]))
    out: list[tuple[int, int, Any]] = []
    cursor = 0
    for hit in hits:
        if hit[0] >= cursor:
            out.append(hit)
            cursor = hit[1]
    return out


@dataclass(eq=False)
class _Entry:
    token: str
    canon: str
    key: str
    prefix: str
    source: str
    tier: int  # 0 whole string only, 1 whole word, 2 substring
    alias_of: Optional["_Entry"] = None  # DAX/M doubled-quote form -> the plain value's entry
    alias_quote: str = ""  # the quote character that is doubled in that form (" or ')
    generic: bool = False  # report vocabulary rules apply (word boundaries, everyday words)
    cased: bool = False  # an ambiguous name: in running text only in its own capitalisation
    proper: bool = False  # a part that is an everyday word: also only inside a sentence


class _LiveAutomaton:
    """Aho-Corasick over a growing word set that never makes a reader wait for a full rebuild.

    ``add`` puts a word in a small PENDING list; ``automatons()`` returns the big automaton (built
    once, then only replaced) plus a small one over the pending words, so a word added a moment ago
    matches at once and costs milliseconds, not the rebuild of 450 000 words under a lock (2026-10
    review: one NER hit stalled every chat's streaming for up to a second). When the pending list
    grows past ``threshold`` the big automaton is rebuilt in a background thread from a snapshot
    and swapped in; words added meanwhile stay pending. Readers only ever iterate finished
    automatons."""

    def __init__(self, threshold: int = 4000, background: bool = True) -> None:
        self._lock = threading.Lock()
        self._threshold = threshold
        self._background = background
        self._main: Any = None
        self._main_words: list[str] = []
        self._pending: list[str] = []
        self._fresh: Any = None
        self._fresh_n = 0
        self._compacting = False

    def add(self, word: str) -> None:
        with self._lock:
            self._pending.append(word)

    @staticmethod
    def _build(words: Iterable[str]) -> Any:
        auto = ahocorasick.Automaton()
        n = 0
        for w in words:
            auto.add_word(w, w)
            n += 1
        if not n:
            return None
        auto.make_automaton()
        return auto

    def automatons(self) -> tuple[Any, Any]:
        start = False
        with self._lock:
            if self._main is None and self._pending:  # the first build is synchronous
                self._main_words = list(dict.fromkeys(self._pending))
                self._pending = []
                self._main = self._build(self._main_words)
                self._fresh, self._fresh_n = None, 0
            if len(self._pending) != self._fresh_n:
                self._fresh = self._build(self._pending)
                self._fresh_n = len(self._pending)
            if len(self._pending) > self._threshold and not self._compacting:
                self._compacting = start = True
            pair = (self._main, self._fresh)
        if start:
            if self._background:
                threading.Thread(target=self.compact, name="automaton", daemon=True).start()
            else:
                self.compact()
                with self._lock:
                    pair = (self._main, self._fresh)
        return pair

    def compact(self) -> None:
        """Fold the pending words into a new big automaton (off the readers' path)."""
        try:
            with self._lock:
                taken = len(self._pending)
                words = list(dict.fromkeys(self._main_words + self._pending[:taken]))
            built = self._build(words)  # the long part: no lock held
            with self._lock:
                self._main, self._main_words = built, words
                del self._pending[:taken]
                self._fresh, self._fresh_n = None, -1
        finally:
            with self._lock:
                self._compacting = False


class SessionMap:
    """Token map of one Claude session (token <-> value), in memory only."""

    def __init__(
        self,
        common_words: Iterable[str] | None = None,
        token_fn: Callable[[str, str], int] | None = None,
        recognizers: Iterable[Any] | None = None,
        fuzzy: bool = True,
        on_recognized: Callable[[list[tuple[str, str]]], None] | None = None,
        profile: Profile | None = None,
        shape_packs: Iterable[str] | None = None,
        never: Iterable[str] = (),
    ) -> None:
        """``never``: terms that are never a dictionary value, on top of the profile's allow terms
        (the configured ``client_guard.allow_terms`` and ``allow_terms.txt``).

        ``token_fn(prefix, normalised_key) -> 10000..99999`` makes the pseudonyms STABLE: a
        value gets the same number whatever else is in the model (a collision takes the next
        free number, a deterministic linear probe). Without it numbers count up per prefix.

        ``recognizers``: objects with ``find(text) -> [(surface, prefix)]`` (for example a NER
        model) that propose client values the dictionary does not know; each proposal becomes a
        dictionary value before the text is masked, so it is masked everywhere from then on.
        ``fuzzy``: also mask misspellings of identity values (``core.client_guard.fuzzy``).
        ``on_recognized(new)``: called (outside the lock; a failure is swallowed) with the
        ``(surface, prefix)`` pairs a recognizer added that were new, so the owner can remember
        them across a restart.

        ``profile`` / ``shape_packs``: which shapes run and under which prefix (default: the active
        profile and its effective packs, ``core.client_guard.profiles.resolve``)."""
        self._lock = threading.RLock()
        if profile is None:
            resolved = resolve()
            profile = resolved.profile
            packs = resolved.shape_packs if shape_packs is None else frozenset(shape_packs)
        else:
            packs = profile.shape_packs if shape_packs is None else frozenset(shape_packs)
        self._shape_packs: frozenset[str] = packs
        self._shape_other = profile.other_shape_prefix
        # the prefix of a generic dimension value (``DIM`` / ``VAL``), e.g. a pasted table's cells
        self.dimension_prefix = profile.dimension_default_prefix
        # product and vendor names (Anthropic, Claude, Claude Code, Power BI ...) are never
        # dictionary values: a vendor table holding "Anthropic" rewrote Claude Code's own system
        # prompt, and Anthropic refused every masked request with HTTP 429 (2026-10-08)
        self._never: frozenset[str] = frozenset(
            normalise_key(t) for t in (*profile.allow_terms, *never)
        )
        self._on_recognized = on_recognized
        self._recognizers = list(recognizers or ())
        self._fuzzy_on = bool(fuzzy) and FuzzyIndex is not None
        self._fuzzy_index: Any = None
        self._fuzzy_dirty = True
        self._token_fn = token_fn
        self._used: dict[str, set[int]] = {}
        self._overflow: dict[str, int] = {}
        words = set(COMMON_WORDS)
        if common_words is not None:
            words.update(common_words)
        self._common = frozenset(normalise_key(w) for w in words)
        self._everyday = _everyday_keys() | self._common
        self._by_key: dict[str, _Entry] = {}
        self._rev: dict[str, str] = {}
        self._aliases: dict[tuple[str, str], str] = {}  # (entry key, exact surface) -> token
        self._uncounted = 0
        self._derived: set[str] = set()  # tokens of slug / URL forms and per-spelling aliases
        self._escaped: set[str] = set()  # tokens of JSON-escaped forms: never quote-doubled
        self._counters: dict[str, int] = {}
        self._counts: dict[str, dict[str, int]] = {}
        self._key_auto = _LiveAutomaton()  # dictionary keys that match in running text
        self._tok_auto = _LiveAutomaton()  # every pseudonym (restored in answers)
        self._revision = 0  # changes whenever matching can change (new value, stronger rules)
        # (revision, key) of every value added or strengthened, in revision order: a result
        # cached at an older revision stays valid unless one of the later keys occurs in its text
        self._part_df: collections.Counter[str] = collections.Counter()
        self._key_log_revs: list[int] = []
        self._key_log_keys: list[str] = []
        self._delta_auto: dict[int, tuple[int, Any]] = {}
        self.stats: dict[str, int] = {
            "values": 0,
            "masked_hits": 0,
            "unmasked_hits": 0,
            "unknown_tokens": 0,
            "case_other": 0,
            "shape_hits": 0,
            "fuzzy_hits": 0,
            "recognized": 0,
        }

    # ------------------------------------------------------------------ dictionary
    @property
    def has_values(self) -> bool:
        return bool(self._by_key)

    def _log_key(self, key: str) -> None:
        self._key_log_revs.append(self._revision)
        self._key_log_keys.append(key)

    def changed_since(
        self, text: str, revision: int, normalised: bool = False, loose: bool = False
    ) -> bool:
        """Could masking ``text`` give a different result now than at ``revision``? Only when a
        value added (or strengthened) after that revision occurs in it: a plain substring test on
        the normalised text, so the answer errs on "changed". Typo matches of new values are not
        considered (a cached result is the text as it was already sent)."""
        with self._lock:
            if revision >= self._revision:
                return False
            first = bisect_right(self._key_log_revs, revision)
            keys = self._key_log_keys[first:]
            if not keys:
                return False
            norm = text if normalised else normalise(text)
            if loose:
                # OCR text: values are matched approximately in images, so any word of 4+
                # letters of a new value that occurs in the read text counts as a change
                words = {w for k in keys for w in _LETTER_RUN.findall(k) if len(w) >= 4}
                keys = sorted(words) or keys
            if len(keys) <= 16:
                return any(k in norm for k in keys)
            if loose:
                return any(k in norm for k in keys)
            built = self._delta_auto.get(revision)
            if built is None or built[0] != self._revision:
                auto = ahocorasick.Automaton()
                for k in keys:
                    auto.add_word(k, k)
                auto.make_automaton()
                if len(self._delta_auto) > 32:
                    self._delta_auto.clear()
                self._delta_auto[revision] = (self._revision, auto)
                built = self._delta_auto[revision]
            return next(built[1].iter(norm), None) is not None

    @property
    def revision(self) -> int:
        """Changes whenever masking output can change: cache results under it, not the count."""
        return self._revision

    def counts(self) -> dict[str, dict[str, int]]:
        """``{source: {prefix: n}}`` (counts only)."""
        with self._lock:
            return {src: dict(by_prefix) for src, by_prefix in self._counts.items()}

    def add_values(
        self,
        prefix: str,
        values: Iterable[str],
        source: str,
        order: Callable[[str], str] | None = None,
        strong: bool = False,
    ) -> int:
        """Add values under ``prefix``; returns the number of NEW base values (variants excluded).

        Add-only: existing tokens are never renumbered or removed, the same casefolded value
        always keeps its token. Numbering inside a batch is randomised, so a token never tells
        where a value sorts. ``order`` (a keyed hash of the value key) replaces the random
        shuffle with an order that looks random but is the same in every session.

        ``strong`` keeps identity matching for values under a generic prefix (a dimension value
        of a party table: an address, a document number). A value that is already known as a
        generic value and now comes as an identity value (an enterprise name that is also a
        dimension value) keeps its token but gets the identity rules: the stronger reading wins
        whatever the order the batches arrive in.
        """
        generic = prefix in GENERIC_PREFIXES and not strong
        with self._lock:
            fresh: dict[str, str] = {}
            for value in values:
                surface = value.strip()
                key = normalise_key(surface)
                if not key or key in self._never:
                    continue
                known = self._by_key.get(key)
                if known is not None:
                    if known.generic and not generic:
                        self._strengthen(known)
                elif key not in fresh:
                    fresh[key] = surface
            keys = list(fresh)
            if self._token_fn is not None:
                keys.sort()  # numbers come from the value: a fixed order keeps collisions stable
            elif order is not None:
                keys.sort(key=order)
            else:
                _BATCH_RNG.shuffle(keys)
            created = [self._new_entry(prefix, fresh[k], k, source, generic=generic) for k in keys]
            self._add_variants([(e, fresh[e.key]) for e in created])
            if not generic and prefix in PART_PREFIXES:
                self._add_parts(prefix, [fresh[k] for k in keys])
            return len(created)

    def _add_parts(self, prefix: str, surfaces: list[str]) -> None:
        """Distinctive words of identity values become values of their own ("Vanhoutte" for
        "Vanhoutte Bouwwerken NV"): people name a client by part of its name. A word qualifies
        when it has 5+ letters, is no ordinary word or legal form, and occurs in at most
        ``PART_MAX_SHARED`` identity values (a stem shared by many names says nothing). In
        running text a part is masked only in its own capitalisation (``cased``). A part that
        is an everyday word ("Lambert", "Horizon") qualifies too, but only where a capital can
        only mean a name: inside a sentence, not as its first word or in a heading
        (``proper``). The curated finance words ("Global", "Partners") never do."""
        for surface in surfaces:
            words = {w for w in _PART_WORD.findall(surface)}
            if len(surface.split()) < 2:
                continue  # a one-word value is already a value
            for w in words:
                self._part_df[w.casefold()] += 1
        for surface in surfaces:
            words = _PART_WORD.findall(surface)
            if prefix == "PER":
                # a person: the surname words only (a first name alone does not identify anyone).
                # The first TOKEN is the first name, whether or not it is long enough to be a part
                # word ("Jan Peeters", "J. Peeters": the surname is the first part word)
                first = surface.split(None, 1)[0] if surface.split() else ""
                first_start = surface.find(first) if first else -1
                candidates = {
                    m.group(0) for m in _PART_WORD.finditer(surface) if m.start() != first_start
                }
                tokens = surface.split()
                if len(tokens) == 2 and len("".join(_LETTER_RUN.findall(tokens[1]))) < 4:
                    # "Peeters Jan" (last name first, ERP style): the short second token is the
                    # first name, so the first token is the surname
                    candidates.update(_PART_WORD.findall(tokens[0]))
            else:
                candidates = set(words)
            for w in candidates:
                key = normalise_key(w)
                if (
                    len(key) < 5
                    or key in self._never
                    or key in self._by_key
                    or key in self._common
                    or key in _LEGAL_FORMS
                    or self._part_df[w.casefold()] > PART_MAX_SHARED
                    or len(surface.split()) < 2
                    or not w[:1].isupper()
                ):
                    continue
                entry = self._new_entry(prefix, w, key, "part", counted=False, generic=False)
                entry.cased = True
                entry.proper = key in self._everyday
                entry.tier = 2 if len(key) >= 6 else 1
                self._derived.add(entry.token)  # a part, not a value of its own
                self._key_auto.add(key)
                self.stats["parts"] = self.stats.get("parts", 0) + 1

    def _strengthen(self, entry: _Entry) -> None:
        """Identity rules for a known generic value and its spelling variants (token kept)."""
        targets = [entry]
        for variant, _quote, _kind in self._variant_forms(entry.canon):
            other = self._by_key.get(normalise_key(variant))
            if other is not None and other.generic:
                targets.append(other)
        for target in targets:
            target.generic = False
            target.tier, target.cased = self._rules(target.key, target.canon, False)
        self._fuzzy_dirty = True
        self._revision += 1
        for target in targets:
            self._key_auto.add(target.key)
            self._log_key(target.key)

    def _rules(self, key: str, surface: str, generic: bool) -> tuple[int, bool]:
        """``(tier, cased)`` of a value.

        An identity value that is ONE ordinary word ("Horizon", "Bakker") is ambiguous: as a whole
        cell it is always the name; in running text it is matched on word boundaries and only in
        its own capitalisation (or in capitals), so "the horizon" and "de bakker" stay readable
        while "Horizon" and "BAKKER" are masked. A value of three characters is matched in running
        text only when it is an acronym in capitals ("VDK", "KBC"): "Jan" or "Bol" in title case
        would also hit months and words, so they stay whole-cell only. The curated common words
        ("services", "total") stay whole-cell only, and so does a value stored without any
        capital (its own spelling is the ordinary word)."""
        tier = self._tier(key, generic)
        if generic or " " in key or key in self._common or not any(c.isalpha() for c in key):
            return tier, False
        if len(key) < 4:
            if len(key) == 3 and surface.isupper() and surface.isalpha():
                return 1, True
            return tier, False
        if key in self._everyday and any(c.isupper() for c in surface):
            return max(tier, 1), True
        return tier, False

    def _tier(self, key: str, generic: bool = False) -> int:
        """0 = only as the WHOLE string (an exact cell), 1 = short value on word boundaries,
        2 = any longer value on word boundaries.

        Whole-string only: short values and curated ordinary words; for GENERIC values (dimension
        values, GL account names) also values without a letter (amounts, band labels) and phrases
        of everyday words only ("Other income", "Software"): report text, not something that names
        a client. Identity values (customers, vendors, persons ...) keep phrase matching however
        ordinary their words are ("Global Investment Partners", "Smith")."""
        if len(key) < 4 or key in self._common:
            return 0
        if not generic and not any(c.isalpha() for c in key):
            return 1  # a numeric ID (customer / account number): whole words, never inside a number
        if generic:
            if not any(c.isalpha() for c in key):
                return 0  # amounts, band labels, numeric codes
            words = _LETTER_RUN.findall(key)
            if words and all(len(w) < 3 or w in self._everyday for w in words):
                return 0
        return 1 if len(key) < 6 else 2

    def _new_entry(
        self,
        prefix: str,
        surface: str,
        key: str,
        source: str,
        alias_of: _Entry | None = None,
        alias_quote: str = "",
        counted: bool = True,
        generic: bool | None = None,
    ) -> _Entry:
        if generic is None:
            generic = prefix in GENERIC_PREFIXES
        token = self._next_token(prefix, key)
        tier, cased = self._rules(key, surface, generic)
        entry = _Entry(
            token, surface, key, prefix, source, tier, alias_of, alias_quote, generic, cased
        )
        self._by_key[key] = entry
        self._revision += 1
        self._log_key(key)
        self._fuzzy_dirty = True
        self._rev[token] = surface
        if counted:
            by_prefix = self._counts.setdefault(source, {})
            by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
        else:
            self._uncounted += 1  # slug / URL forms of a value: matched, not counted as values
        self.stats["values"] = len(self._by_key) - self._uncounted
        if entry.tier:
            self._key_auto.add(key)
        self._tok_auto.add(token)
        return entry

    def _next_token(self, prefix: str, key: str) -> str:
        if self._token_fn is None:
            n = self._counters.get(prefix, 0) + 1
            self._counters[prefix] = n
            return f"{prefix}_{n:04d}"
        low, high = getattr(self._token_fn, "token_range", (TOKEN_MIN, TOKEN_MAX))
        used = self._used.setdefault(prefix, set())
        if len(used) >= high - low + 1:  # the range is full: continue past every range
            n = TOKEN_OVERFLOW + self._overflow.get(prefix, 0)
            self._overflow[prefix] = n - TOKEN_OVERFLOW + 1
            return f"{prefix}_{n}"
        n = int(self._token_fn(prefix, key))
        if not low <= n <= high:
            n = low + n % (high - low + 1)
        while n in used:
            n = n + 1 if n < high else low
        used.add(n)
        return f"{prefix}_{n:0{len(str(high))}d}"

    @staticmethod
    def _variant_forms(surface: str) -> list[tuple[str, str, str]]:
        """``(variant, doubled quote, kind)`` spellings of a value: IBAN groupings, JSON escapes,
        DAX/M doubled quotes, HTML ampersand, slugs. kind is "escape", "slug" or ""."""
        compact = surface.replace(" ", "")
        variants: list[tuple[str, str]] = []
        escapes: set[str] = set()
        if iban_is_valid(compact):
            variants.append((compact, ""))
            groups = re.findall(r".{1,4}", compact)
            variants.append((" ".join(groups), ""))
        escaped = json.dumps(surface)[1:-1]
        if escaped != surface:  # non-ASCII, quote, backslash or control characters
            variants.append((escaped, ""))
            escapes.add(escaped)
            twice = json.dumps(escaped)[1:-1]  # a JSON document stored inside a JSON string
            if twice != escaped:
                variants.append((twice, ""))
                escapes.add(twice)
        if '"' in surface:
            variants.append((surface.replace('"', '""'), '"'))
        if "'" in surface:
            variants.append((surface.replace("'", "''"), "'"))
        if "&" in surface:
            variants.append((surface.replace("&", "&amp;"), ""))
        slugs = {v: True for v in _slug_variants(surface)}
        return [
            (v, q, "slug" if v in slugs else "escape" if v in escapes else "")
            for v, q in [*variants, *((s, "") for s in slugs)]
        ]

    def _add_variants(self, entries: list[tuple[_Entry, str]]) -> None:
        pending: list[tuple[_Entry, str, str, str, str]] = []
        pending_keys: set[str] = set()
        for entry, surface in entries:
            forms = self._variant_forms(surface)
            if entry.prefix == "PER" and not entry.generic and entry.source != "part":
                forms = forms + [(f, "", "person") for f in _person_forms(surface)]
            for variant, quote, kind in forms:
                key = normalise_key(variant)
                if (
                    key
                    and key not in self._by_key
                    and key not in pending_keys
                    and key not in self._never
                ):
                    pending_keys.add(key)
                    pending.append((entry, variant, key, quote, kind))
        if self._token_fn is not None:
            pending.sort(key=lambda p: p[2])
        else:
            _BATCH_RNG.shuffle(pending)
        for entry, variant, key, quote, kind in pending:
            if key not in self._by_key:
                made = self._new_entry(
                    entry.prefix,
                    variant,
                    key,
                    "variant",
                    entry if quote else None,
                    quote,
                    counted=kind not in ("slug", "person"),
                    generic=entry.generic,
                )
                if kind == "escape":
                    self._escaped.add(made.token)
                elif kind in ("slug", "person"):
                    self._derived.add(made.token)
                if kind == "person":
                    made.tier = min(made.tier, 1)  # an initial form matches whole words only

    # ------------------------------------------------------------------ matching
    def _dictionary_hits(self, norm: str, whole: bool = True) -> list[tuple[int, int, Any]]:
        hits: list[tuple[int, int, Any]] = []
        autos = [a for a in self._key_auto.automatons() if a is not None]
        if autos:
            n = len(norm)
            for end, key in (m for a in autos for m in a.iter(norm)):
                entry = self._by_key[key]
                start = end + 1 - len(key)
                if entry.tier == 1 and (
                    (start > 0 and norm[start - 1].isalnum())
                    or (end + 1 < n and norm[end + 1].isalnum())
                ):
                    continue  # a short value: whole words only ("Bols" inside "Bolster")
                if (
                    entry.tier == 2
                    and entry.generic
                    and not _on_word_boundaries(norm, start, end + 1, n)
                ):
                    # a generic value inside a longer word ("include" in "includes") is report or
                    # code vocabulary. Identity values (names) still match inside words: a client
                    # name glued into a file name or identifier ("JanssensQ3.xlsx") is a leak.
                    continue
                hits.append((start, end + 1, entry))
        core = norm.strip(" ") if whole else ""
        if core:
            exact = self._by_key.get(core)
            if exact is not None and exact.tier == 0:
                start = len(norm) - len(norm.lstrip(" "))
                hits.append((start, start + len(core), exact))
        return hits

    def _token_spans(self, norm: str) -> list[tuple[int, int]]:
        """Spans of known pseudonyms in NORMALISED text (a pseudonym is not client data)."""
        if not self._rev or "_" not in norm:
            return []
        spans: list[tuple[int, int]] = []
        for m in _TOKEN_SHAPE.finditer(norm):
            end = m.end()
            token = m.group(0).upper()
            if token not in self._rev:
                continue
            if end < len(norm) and norm[end] in "ul" and token + norm[end].upper() in self._rev:
                end += 1
            spans.append((m.start(), end))
        return spans

    def _without_token_hits(
        self, norm: str, hits: list[tuple[int, int, Any]], inbound: bool = False
    ) -> list[tuple[int, int, Any]]:
        """Drop dictionary hits that lie INSIDE a pseudonym: a 5-digit dictionary value such as an
        account number is a whole word inside ``CUS_48213`` and must neither be masked there nor
        count as a leak. ``inbound`` (text on its way OUT to the model, which holds no pseudonym of
        ours): a hit that IS the whole pseudonym-shaped string is a real code that happens to look
        like one (``CUS_10001``) and stays a hit."""
        if not hits:
            return hits
        spans = self._token_spans(norm)
        if not spans:
            return hits
        return [
            h
            for h in hits
            if not any(
                a <= h[0] and h[1] <= b and not (inbound and (h[0], h[1]) == (a, b))
                for a, b in spans
            )
        ]

    def _register_token_collisions(self, s: str, norm: str) -> None:
        """Text on its way out holds a string shaped like one of OUR pseudonyms that is not a
        dictionary value (a real data code such as ``CUS_10001``). Left alone, the model's echo of
        it would be restored as another entity's real value. It becomes a value of its own and
        gets a pseudonym of its own (restored to the same text). Caller holds the lock."""
        spans = self._token_spans(norm)
        if not spans:
            return
        plain = s.isascii() and len(s) == len(norm)
        omap = None if plain else _build_offset_map(s, norm)
        for a, b in spans:
            key = norm[a:b]
            if key in self._by_key:
                continue
            surface = s[a:b] if omap is None else s[omap.start(a) : omap.end(b)]
            prefix = surface.split("_", 1)[0].upper()
            entry = self._new_entry(prefix, surface, key, "collision", generic=False)
            self._add_variants([(entry, surface)])
            self.stats["token_collisions"] = self.stats.get("token_collisions", 0) + 1

    def _shape_hits(self, norm: str) -> list[tuple[int, int, Any]]:
        out: list[tuple[int, int, Any]] = []
        for start, end, prefix in find_shapes(norm, self._shape_packs, self._shape_other):
            key = norm[start:end]
            out.append((start, end, self._by_key.get(key) or (prefix, key)))
        return out

    def _ensure_fuzzy(self) -> Any:
        if not self._fuzzy_on:
            return None
        if self._fuzzy_dirty:
            ordinary = self._everyday.__contains__
            self._fuzzy_index = FuzzyIndex(
                (
                    (key, e)
                    for key, e in self._by_key.items()
                    if not e.generic
                    and not e.cased
                    and e.alias_of is None
                    and e.source not in ("variant", "shape")
                ),
                ordinary,
            )
            self._fuzzy_dirty = False
        return self._fuzzy_index

    @staticmethod
    def _case_ok(surface: str, entry: _Entry) -> bool:
        """An ambiguous name counts in running text only in its own spelling or in capitals."""
        canon = entry.canon
        if surface in (canon, canon.upper()) or _cosmetic(surface) == _cosmetic(canon):
            return True
        plain, plain_canon = _strip_marks(_cosmetic(surface)), _strip_marks(_cosmetic(canon))
        return plain in (plain_canon, plain_canon.upper())  # same name, other diacritics

    def _hits(
        self, s: str, norm: str, whole: bool, fuzzy: bool, inbound: bool = False
    ) -> tuple[list[tuple[int, int, Any]], Any]:
        """Every span to mask (dictionary, ambiguous names in their own case, typos, shapes),
        overlaps resolved; plus the offset map when one was needed. ``mask_text`` and
        ``count_leaks`` use this one function, so the leak check applies exactly the rules the
        masking applied."""
        omap: Any = None
        plain = s.isascii() and len(s) == len(norm)

        def original(a: int, b: int) -> str:
            nonlocal omap
            if plain:
                return s[a:b]
            if omap is None:
                omap = _build_offset_map(s, norm)
            return s[omap.start(a) : omap.end(b)]

        found = self._without_token_hits(norm, self._dictionary_hits(norm, whole), inbound)
        if any(isinstance(h[2], _Entry) and h[2].cased for h in found):
            core = norm.strip(" ")
            found = [
                h
                for h in found
                if not (isinstance(h[2], _Entry) and h[2].cased)
                or (whole and norm[h[0] : h[1]] == core)  # the whole cell is the name
                or (not h[2].proper and _identifier_context(norm, h[0], h[1]))
                or (
                    self._case_ok(original(h[0], h[1]), h[2])
                    # original() above built omap when the text needed one
                    and (
                        not h[2].proper or _inside_sentence(s, h[0] if plain else omap.start(h[0]))
                    )
                )
            ]
        if fuzzy:
            index = self._ensure_fuzzy()
            if index:
                if plain:

                    def is_capital(pos: int) -> bool:
                        return s[pos].isupper()

                else:

                    def is_capital(pos: int) -> bool:
                        return original(pos, pos + 1)[:1].isupper()

                # a typo may extend over a word that only a "part" hit covers ("Zorvx Industries
                # NV": "Industries" is a part of the value); exact whole-value hits are skipped
                taken = [
                    (a, b)
                    for a, b, e in found
                    if not (isinstance(e, _Entry) and e.source == "part")
                ]
                extra = index.search(norm, is_capital, taken, self._by_key.__contains__)
                if extra:
                    self.stats["fuzzy_hits"] += len(extra)
                    found = found + extra
        if not plain and omap is None and found:
            omap = _build_offset_map(s, norm)
        return _select(found + self._shape_hits(norm)), omap

    def count_leaks(self, text: str, whole: bool = True) -> int:
        """Dictionary-value (and shape-detector) occurrences in ``text``; never the values.
        ``whole=False`` leaves out whole-string-only values (the same rule as ``mask_text``).
        Misspellings are not counted: they are a masking aid, not a check (the residual check
        after masking looks for the values themselves)."""
        if not text:
            return 0
        with self._lock:
            hits, _ = self._hits(text, normalise(text), whole, fuzzy=False)
            return len(hits)

    def exempt_leaks(self, text: str) -> int:
        """Identity values and shapes in text that is copied verbatim into a request (check-only,
        whole-string-only values and generic vocabulary left out, like developer-written text)."""
        if not text:
            return 0
        with self._lock:
            hits, _ = self._hits(text, normalise(text), False, fuzzy=False)
        return sum(1 for _a, _b, t in hits if not isinstance(t, _Entry) or not t.generic)

    def identity_hits(self, text: str, whole: bool = True) -> dict[str, int]:
        """``{prefix: n}`` of IDENTITY values (customers, vendors, persons, companies, parts of
        their names; not generic dimension values or GL names, not shapes) in ``text``. Read-only:
        for the unmasked-chat safety net. Never returns a value. ``whole=False`` is the
        developer-text rule of ``mask_text`` (a whole-string-only value, such as a lone ordinary
        word, is not a hit)."""
        if not text:
            return {}
        out: dict[str, int] = {}
        with self._lock:
            hits, _ = self._hits(text, normalise(text), whole, fuzzy=False)
        for _a, _b, target in hits:
            if isinstance(target, _Entry) and not target.generic:
                out[target.prefix] = out.get(target.prefix, 0) + 1
        return out

    # ------------------------------------------------------------------ masking
    def mask_text(
        self, s: str, whole: bool = True, *, recognize: bool = True, premasked: bool = False
    ) -> tuple[str, int]:
        """Mask one string. Any internal failure raises ``MaskingError`` (fixed message).

        ``whole=False``: whole-string-only values (ordinary words, short values: a cell that holds
        just that word) are not matched, and the recognizers do not run. For developer-written
        text such as tool definitions, where a lone "status" or "include" is a keyword, never a
        client cell.

        ``premasked=True``: ``s`` is a result the masking authority already pseudonymised (an MCP
        result with the PBI marker). A string shaped like one of OUR pseudonyms in it IS one of
        our pseudonyms and stays as it is; by default (text that was never masked) such a string
        is a real data code that merely looks like one and becomes a value of its own, so that
        masking an already masked result would hand the model a SECOND pseudonym that unmasks to
        the first one instead of to the client name."""
        if not s:
            return s, 0
        result: tuple[str, int] = (s, 0)
        failed = False
        try:
            proposals: list[tuple[str, str]] = []
            if whole and recognize:
                for recognizer in self._recognizers:  # outside the lock: may take milliseconds
                    if hasattr(recognizer, "find_many"):  # masking: all of it, or it raises
                        proposals.extend(recognizer.find_many([s], complete=True))
                    else:
                        proposals.extend(recognizer.find(s))
            with self._lock:
                fresh = self._add_recognized(proposals) if proposals else []
                result = self._mask_text(s, whole, premasked)
            self._notify_recognized(fresh)
        except (
            Exception
        ):  # noqa: BLE001 - fail closed; the original exception is dropped on purpose
            failed = True
        if failed:
            raise MaskingError(MASK_FAILED)
        return result

    def recognize(self, text: str) -> int:
        """Run the recognizers on ``text`` and add what they propose, without masking it (for
        text that is not sent as such: the OCR text of an image). Returns the number of new
        values. Like a request text, it is recognized COMPLETELY or the recognizer raises (the
        caller fails closed): a name recognizer that is down must not make a screenshot look
        clean."""
        return self.recognize_many([text])

    def recognize_many(self, texts: list[str]) -> int:
        """Recognize request strings together before masking each leaf. A recognizer with
        ``find_many`` must recognize ALL of it (``complete=True``) or raise: the caller fails
        closed instead of sending text that was only partly checked."""
        if not texts:
            return 0
        proposals: list[tuple[str, str]] = []
        for recognizer in self._recognizers:
            if hasattr(recognizer, "find_many"):
                proposals.extend(recognizer.find_many(texts, complete=True))
            else:
                for text in texts:
                    proposals.extend(recognizer.find(text))
        if not proposals:
            return 0
        with self._lock:
            fresh = self._add_recognized(proposals)
        self._notify_recognized(fresh)
        return len(fresh)

    def recognize_best_effort(self, texts: list[str], budget_s: float) -> int:
        """Recognize what fits in ``budget_s`` and add it; never raises, never blocks longer.

        For text that is NOT new in a request: the earlier conversation of a chat that is masked
        for the first time. That history already left this machine in its earlier turns (or was
        masked then, and the remembered dictionary still holds what was recognized), so it must not
        stop the new message; what is found here is masked from now on. Returns the number of new
        values."""
        if not texts:
            return 0
        proposals: list[tuple[str, str]] = []
        try:
            for recognizer in self._recognizers:
                if hasattr(recognizer, "find_many"):
                    with background_priority():  # the new text of any chat goes first
                        proposals.extend(recognizer.find_many(texts, budget_s=budget_s, wait=False))
                else:
                    for text in texts:
                        proposals.extend(recognizer.find(text))
        except (
            Exception
        ):  # noqa: BLE001 - best effort: the dictionary still masks every known value
            pass
        if not proposals:
            return 0
        with self._lock:
            fresh = self._add_recognized(proposals)
        self._notify_recognized(fresh)
        return len(fresh)

    def _add_recognized(self, proposals: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Proposals of a recognizer become identity values (masked everywhere from now on).
        Returns the new ``(surface, prefix)`` pairs."""
        fresh: list[tuple[str, str]] = []
        for surface, prefix in proposals:
            surface = surface.strip()
            key = normalise_key(surface)
            if len(key) < 3 or key in self._by_key or key in self._never:
                continue
            entry = self._new_entry(prefix, surface, key, "recognized", generic=False)
            self._add_variants([(entry, surface)])
            if prefix in PART_PREFIXES:
                self._add_parts(prefix, [surface])
            self.stats["recognized"] += 1
            fresh.append((surface, prefix))
        return fresh

    def _notify_recognized(self, fresh: list[tuple[str, str]]) -> None:
        if fresh and self._on_recognized is not None:
            try:
                self._on_recognized(fresh)
            except Exception:  # noqa: BLE001 - remembering is best effort, masking goes on
                pass

    def _mask_text(self, s: str, whole: bool = True, premasked: bool = False) -> tuple[str, int]:
        norm = normalise(s)
        if self._rev and "_" in norm and not premasked:
            self._register_token_collisions(s, norm)
        hits, omap = self._hits(s, norm, whole, fuzzy=whole, inbound=not premasked)
        if not hits:
            return s, 0
        out: list[str] = []
        cursor = 0
        count = 0
        for start, end, target in hits:
            o_start = start if omap is None else omap.start(start)
            o_end = end if omap is None else omap.end(end)
            if o_end <= cursor:
                continue
            o_start = max(o_start, cursor)
            surface = s[o_start:o_end]
            if isinstance(target, _Entry):
                entry = target
            else:
                prefix, key = target
                entry = self._add_shape(prefix, surface, key)
                self.stats["shape_hits"] += 1
            out.append(s[cursor:o_start])
            out.append(self._token_for(entry, surface, s, o_start, o_end))
            cursor = o_end
            count += 1
        out.append(s[cursor:])
        self.stats["masked_hits"] += count
        return "".join(out), count

    def _add_shape(self, prefix: str, surface: str, key: str) -> _Entry:
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        entry = self._new_entry(prefix, surface, key, "shape", generic=False)
        self._add_variants([(entry, surface)])
        return entry

    @staticmethod
    def _inside_quotes(before: str, quote: str) -> bool:
        if quote == '"':
            return before.count('"') % 2 == 1
        return _single_quote_open(before)

    def _token_for(self, entry: _Entry, surface: str, text: str, o_start: int, o_end: int) -> str:
        if entry.alias_of is not None and self._inside_quotes(text[:o_start], entry.alias_quote):
            return entry.alias_of.token  # unmask re-doubles the quote inside a DAX literal/name
        if surface == entry.canon or _cosmetic(surface) == _cosmetic(entry.canon):
            return entry.token  # same text up to whitespace runs / Unicode composition
        following = text[o_end] if o_end < len(text) else ""
        if not following.isalnum():
            suffix = "U" if surface.isupper() else "L" if surface.islower() else ""
            if suffix:
                token = entry.token + suffix
                known = self._rev.get(token)
                if known is None:
                    self._rev[token] = surface
                    self._derived.add(token)
                    self._tok_auto.add(token)
                    return token
                if known == surface:
                    return token
        return self._alias_token(entry, surface)

    def _alias_token(self, entry: _Entry, surface: str) -> str:
        """A pseudonym that stands for exactly THIS surface (``insuranceCost`` inside an
        identifier must come back as ``insurance``, not as the canonical ``Insurance``). One
        alias per distinct surface, stable for the life of the map."""
        known = self._aliases.get((entry.key, surface))
        if known is not None:
            return known
        self.stats["case_other"] += 1
        if len(self._aliases) >= MAX_ALIASES:
            return entry.token  # bounded memory: the canonical token (case may change)
        token = self._next_token(entry.prefix, entry.key + "\x00" + surface)
        self._aliases[(entry.key, surface)] = token
        self._derived.add(token)
        self._rev[token] = surface
        self._tok_auto.add(token)
        return token

    # ------------------------------------------------------------------ unmasking
    def unmask_text(self, s: str, context: Context = "plain", prefix: str = "") -> tuple[str, int]:
        """Replace this session's tokens by the real values.

        ``context="json"`` JSON-escapes the real value (``s`` is a raw JSON string fragment such
        as a ``partial_json`` delta). ``prefix`` is the already-seen text of the SAME string, used
        only to tell whether a token sits inside a DAX ``"..."`` literal or a ``'...'`` name.
        Unknown token-shaped strings are left alone and counted in ``stats["unknown_tokens"]``.
        """
        if not s:
            return s, 0
        result: tuple[str, int] = (s, 0)
        failed = False
        try:
            # no ``self._lock``: masking a big paste holds it for a second or more, and every
            # streamed delta of every chat unmasks here. Everything this reads is either
            # immutable once built (the automatons) or a single dict lookup.
            result = self._unmask_text(s, context, prefix)
        except Exception:  # noqa: BLE001 - never surface input text
            failed = True
        if failed:
            raise MaskingError(UNMASK_FAILED)
        return result

    def _unmask_text(self, s: str, context: Context, prefix: str) -> tuple[str, int]:
        spans: list[tuple[int, int, Any]] = []
        if "_" in s:
            hits = [
                (e + 1 - len(t), e + 1, t)
                for auto in self._tok_auto.automatons()
                if auto is not None
                for e, t in auto.iter(s)
            ]
            spans = [sp for sp in _select(hits) if not self._digits_continue_token(s, sp)]
        lowered: set[int] = set()
        if "_" in s:
            spans, lowered = self._loose_spans(s, spans)
        out: list[str] = []
        cursor = 0
        for start, end, token in spans:
            real = self._rev[token]
            if start in lowered:
                real = real.lower()  # the model wrote the token in lower case: so does the answer
            if ('"' in real or "'" in real) and token not in self._escaped:
                in_dq, in_sq = _literal_state(prefix + s[:start], context)
                if in_dq:
                    real = real.replace('"', '""')
                elif in_sq:
                    real = real.replace("'", "''")
            if context == "json":
                real = json.dumps(real, ensure_ascii=False)[1:-1]
            out.append(s[cursor:start])
            out.append(real)
            cursor = end
        if "_" in s:
            known = {span[0] for span in spans}
            for m in _GENERIC_TOKEN.finditer(s):
                if m.start() not in known:
                    self.stats["unknown_tokens"] += 1
        if not spans:
            return s, 0
        out.append(s[cursor:])
        self.stats["unmasked_hits"] += len(spans)
        return "".join(out), len(spans)

    def _loose_spans(
        self, s: str, spans: list[tuple[int, int, Any]]
    ) -> tuple[list[tuple[int, int, Any]], set[int]]:
        """Add the pseudonyms of ``s`` that are not written in their exact form: lower-cased or
        Markdown-escaped. Only a token that exists counts; exact hits keep their place."""
        extra: list[tuple[int, int, Any]] = []
        lowered: set[int] = set()
        for m in _LOOSE_TOKEN.finditer(s):
            text = m.group(0)
            if any(a < m.end() and m.start() < b for a, b, _t in spans):
                continue
            token = f"{m.group(1)}_{m.group(2)}".upper()
            suffix = m.group(3).upper()
            if suffix and token + suffix in self._rev:
                token += suffix
            elif suffix:
                continue  # a letter glued on a token is a different word
            if token not in self._rev:
                continue
            if text == token:
                continue  # exact form: handled above (or refused on purpose)
            extra.append((m.start(), m.end(), token))
            if text.replace("\\", "").islower():
                lowered.add(m.start())
        if not extra:
            return spans, lowered
        return sorted(spans + extra), lowered

    def _digits_continue_token(self, s: str, span: tuple[int, int, Any]) -> bool:
        """True when ``s`` carries MORE digits after a numeric token than a token can have.

        ``FAM_10002`` in a session whose highest ``FAM`` number is 10001 is an unknown token, not
        ``FAM_1000`` followed by a ``2``. A longer run (``FAM_00012024``) can only be a short
        token with digits glued to it (``Janssens2024``) and stays a hit.
        """
        _, end, token = span
        if end >= len(s) or not s[end].isdigit() or not token[-1].isdigit():
            return False
        prefix, digits = token.split("_", 1)
        if self._token_fn is not None:
            return False  # every token has exactly TOKEN_DIGITS digits: more digits are glued on
        extra = _DIGIT_RUN.match(s, end)
        run = len(digits) + (len(extra.group(0)) if extra else 0)
        return run <= max(4, len(str(self._counters.get(prefix, 0))))

    # ------------------------------------------------------------------ trees
    def unmask_tree(self, obj: Any, on_string: Callable[[str, str], None] | None = None) -> Any:
        """Unmask every string (and dict key); ``on_string(masked, unmasked)`` sees changes."""

        def leaf(value: str) -> str:
            out = self.unmask_text(value)[0]
            if on_string is not None and out != value:
                on_string(value, out)
            return out

        return self._walk(
            obj, leaf, lambda key: self.unmask_text(key)[0], MaskingError(UNMASK_FAILED)
        )

    def _walk(
        self,
        obj: Any,
        leaf: Callable[[str], str],
        key_fn: Callable[[str], str],
        error: MaskingError,
    ) -> Any:
        failed = False
        result: Any = obj
        try:
            result = self._walk_inner(obj, leaf, key_fn)
        except MaskingError:
            raise
        except Exception:  # noqa: BLE001 - e.g. recursion limit on a hostile tree
            failed = True
        if failed:
            raise error
        return result

    def _walk_inner(
        self, obj: Any, leaf: Callable[[str], str], key_fn: Callable[[str], str]
    ) -> Any:
        if isinstance(obj, str):
            return leaf(obj)
        if isinstance(obj, dict):
            block_type = obj.get("type")
            if isinstance(block_type, str) and block_type in SKIP_BLOCK_TYPES:
                return obj
            out: dict[Any, Any] = {}
            for key, value in obj.items():
                new_key = key_fn(key) if isinstance(key, str) else key
                if (
                    key in SKIP_KEYS
                    and isinstance(value, str)
                    and _OPAQUE_PAYLOAD.fullmatch(value) is not None
                ):
                    out[new_key] = value
                else:
                    out[new_key] = self._walk_inner(value, leaf, key_fn)
            return out
        if isinstance(obj, (list, tuple, set, frozenset)):
            return [self._walk_inner(v, leaf, key_fn) for v in obj]
        return obj
