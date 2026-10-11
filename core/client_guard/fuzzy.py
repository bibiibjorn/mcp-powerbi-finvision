"""Approximate matching of client names: typos and spelling variants of dictionary values.

A Presidio-style fuzzy deny list (exact matching misses "Jansens Holding" for "Janssens
Holding"): candidates are runs of 1-5 words (particles count: "Van den Broeck & Leclercq") in the NORMALISED text whose ORIGINAL form starts with
a capital (names do, prose rarely), compared with the identity values of the same word count and
the same first letter by Damerau-Levenshtein distance with a length-scaled tolerance:

  value length  < 5   no typo allowed (short names are matched exactly or not at all)
  5 .. 8              1 edit
  9 +                 2 edits

Never fuzzed: generic values (dimension members, GL names), single ordinary words, values shorter
than 5 characters, candidates made of ordinary words only (they are prose, not a misspelt name).
The search only reads; the engine decides what to do with a hit.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import Any

from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein

MIN_LEN = 5
MAX_WORDS = 5
# a name word in normalised text: letters (with diacritics), inner apostrophes / hyphens
_WORD = re.compile(r"[^\W\d_](?:[\w'’\-]*[^\W_])?")
_JOINERS = frozenset({" ", " & ", " - ", "-"})


@functools.lru_cache(maxsize=200_000)
def fold(text: str) -> str:
    """Diacritics off, for comparison only (``é`` and ``e`` count as the same letter)."""
    if text.isascii():
        return text
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def allowed_edits(length: int) -> int:
    if length < MIN_LEN:
        return 0
    return 1 if length <= 8 else 2


class FuzzyIndex:
    """Identity values grouped by (word count, first folded letter), with length bounds."""

    def __init__(self, entries: Iterable[tuple[str, Any]], ordinary: Callable[[str], bool]):
        self._blocks: dict[tuple[int, str], tuple[list[str], list[Any]]] = {}
        self._bounds: dict[tuple[int, str], tuple[int, int]] = {}
        self._ordinary = functools.lru_cache(maxsize=100_000)(ordinary)
        for key, entry in entries:
            folded = fold(key)
            words = folded.split(" ")
            if len(folded) < MIN_LEN or len(words) > MAX_WORDS or not folded[:1].isalpha():
                continue
            if all(self._ordinary(w) for w in words):
                continue  # an ordinary word or phrase: a near miss of it is prose
            block = (len(words), folded[0])
            keys, targets = self._blocks.setdefault(block, ([], []))
            keys.append(folded)
            targets.append(entry)
            lo, hi = self._bounds.get(block, (len(folded), len(folded)))
            self._bounds[block] = (min(lo, len(folded)), max(hi, len(folded)))
        self._letters = frozenset(letter for _, letter in self._blocks)

    def __bool__(self) -> bool:
        return bool(self._blocks)

    def search(
        self,
        norm: str,
        is_capital: Callable[[int], bool],
        taken: list[tuple[int, int]],
        known: Callable[[str], bool],
    ) -> list[tuple[int, int, Any]]:
        """Fuzzy hits in ``norm``: ``(start, end, entry)``. ``is_capital(pos)`` says whether the
        ORIGINAL character at normalised position ``pos`` is upper case; ``taken`` spans (exact
        hits) are skipped; ``known(candidate)`` says whether the candidate is a dictionary key.

        Cost: only words that start with a capital and whose first folded letter has a block are
        expanded; every word is folded once (cached across calls); candidates grow word by word;
        a block is only searched when the candidate length is within its length bounds."""
        if not self._blocks:
            return []
        words = [(m.start(), m.end()) for m in _WORD.finditer(norm)]
        if not words:
            return []
        covered = _covered(words, taken)
        hits: list[tuple[int, int, Any]] = []
        used_until = -1
        letters = self._letters
        ordinary = self._ordinary
        for i, (start, first_end) in enumerate(words):
            if start < used_until or covered[i]:
                continue
            first = fold(norm[start])[:1]
            if first not in letters or not is_capital(start):
                continue  # names start with a capital; prose and code words do not
            best: tuple[int, int, int, Any] | None = None  # (distance, -length, end, entry)
            cand = ""
            n_tokens = 0
            all_ordinary = True
            for n in range(1, MAX_WORDS + 1):
                j = i + n - 1
                if j >= len(words) or covered[j]:
                    break
                ws, we = words[j]
                if n > 1:
                    joiner = norm[words[j - 1][1] : ws]
                    if joiner not in _JOINERS:
                        break
                    cand += joiner
                    n_tokens += joiner.count(" ")
                word = fold(norm[ws:we])
                cand += word
                n_tokens += 1 if n == 1 else 0
                if all_ordinary and not ordinary(word):
                    all_ordinary = False
                if len(cand) < MIN_LEN or all_ordinary:
                    continue
                if known(norm[start:we]):
                    continue
                block = (n_tokens if n > 1 else 1, first)
                bounds = self._bounds.get(block)
                limit = allowed_edits(len(cand))
                if bounds is None or not limit:
                    continue
                if len(cand) + limit < bounds[0] or len(cand) - limit > bounds[1]:
                    continue
                keys, targets = self._blocks[block]
                found = process.extractOne(
                    cand, keys, scorer=DamerauLevenshtein.distance, score_cutoff=limit
                )
                if found is None:
                    continue
                # distance 0 is a candidate that differs from a value only by diacritics (the
                # comparison is folded; an exact key was skipped by ``known`` above): a hit
                key, distance, index = found
                if allowed_edits(len(key)) < distance:
                    continue  # the VALUE is too short for that many edits
                option = (distance, -(we - start), we, targets[index])
                if best is None or option[:2] < best[:2]:
                    best = option
            if best is not None:
                hits.append((start, best[2], best[3]))
                used_until = best[2]
        return hits


def _covered(words: list[tuple[int, int]], taken: list[tuple[int, int]]) -> list[bool]:
    """Per word: does it overlap an exact hit? One sweep over both sorted lists."""
    out = [False] * len(words)
    if not taken:
        return out
    spans = sorted(taken)
    j = 0
    for i, (a, b) in enumerate(words):
        while j < len(spans) and spans[j][1] <= a:
            j += 1
        k = j
        while k < len(spans) and spans[k][0] < b:
            if spans[k][1] > a:
                out[i] = True
                break
            k += 1
    return out
