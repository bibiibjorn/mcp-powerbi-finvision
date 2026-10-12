"""Regenerate ``core/client_guard/data/common_en_nl.txt`` from the FrequencyWords lists.

    python tools/masking_lab/gen_common_words.py [en_50k.txt nl_50k.txt] [--out PATH]

Without paths the two lists are downloaded from
https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/{en,nl}/{en,nl}_50k.txt
(Hermit Dave, MIT licence). Each line of a source file is ``word count``, most frequent first.

The rule (it reproduces the committed file byte for byte; checked 2026-10-08):

* per language, walk the list in file order and keep a word when it has 3 or more characters, is
  alphabetic (``str.isalpha``) and every character is a LATIN letter (``unicodedata.name`` starts
  with ``LATIN``: accents such as the e-acute of "een" stay, the Greek omicron the English list
  holds inside a look-alike "you" does not);
* lower-case it, and stop at the first 12,000 kept words of that language (the cut-off counts KEPT
  words, not file lines);
* the union of both languages, de-duplicated, sorted by code point (plain ``sorted``), one word
  per line, LF line endings, a final newline, after the three header lines below.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "core" / "client_guard" / "data" / "common_en_nl.txt"
PER_LANGUAGE = 12_000
MIN_LENGTH = 3
HEADER = (
    "# Common English and Dutch words (top 12,000 each, alphabetic, 3+ letters).\n"
    "# Source: FrequencyWords by Hermit Dave (MIT licence), "
    "https://github.com/hermitdave/FrequencyWords\n"
    "# content/2018/en/en_50k.txt and content/2018/nl/nl_50k.txt. One word per line.\n"
)


def is_latin_word(word: str) -> bool:
    return (
        len(word) >= MIN_LENGTH
        and word.isalpha()
        and all(unicodedata.name(c, "").startswith("LATIN") for c in word)
    )


def top_words(lines: list[str]) -> list[str]:
    kept: list[str] = []
    for line in lines:
        parts = line.split()
        if parts and is_latin_word(parts[0]):
            kept.append(parts[0].lower())
            if len(kept) == PER_LANGUAGE:
                break
    return kept


def render(en_lines: list[str], nl_lines: list[str]) -> str:
    words = sorted(set(top_words(en_lines)) | set(top_words(nl_lines)))
    return HEADER + "".join(f"{w}\n" for w in words)


def read_lines(path: str | None, lang: str) -> list[str]:
    if path:
        return Path(path).read_text(encoding="utf-8-sig").splitlines()
    url = f"{BASE}/{lang}/{lang}_50k.txt"
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https URL
        return response.read().decode("utf-8-sig").splitlines()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "sources", nargs="*", help="local en_50k.txt nl_50k.txt (default: download)"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output data file")
    args = parser.parse_args(argv)
    if len(args.sources) not in (0, 2):
        parser.error("give both the English and the Dutch file, or neither")
    en, nl = (args.sources or [None, None])[:2]
    text = render(read_lines(en, "en"), read_lines(nl, "nl"))
    with open(args.out, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    print(f"common words: {text.count(chr(10)) - 3} words -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
