"""Generate ``core/client_guard/data/confusables_latin.txt`` from the Unicode TR39 confusables file.

    python tools/masking_lab/gen_confusables.py [path-to-confusables.txt] [--out PATH]

Without a path the file is downloaded from
https://www.unicode.org/Public/security/latest/confusables.txt. The masking engine folds these
look-alikes ("а" U+0430 CYRILLIC, "ο" U+03BF GREEK ...) onto the Latin letter or digit they imitate
(``core.client_guard.engine.normalise``), because NFKC does not: "Zоrvex" with a Cyrillic o must
still match "Zorvex".

A line of confusables.txt is ``SOURCE ; TARGET [TARGET ...] ; TYPE # comment``. The engine applies
the table AFTER ``NFKC`` + ``casefold`` + mark stripping, so the generator keeps only the mappings
that can still fire there:

* the source is ONE non-ASCII code point whose own ``NFKC(src).casefold()`` is one non-ASCII code
  point (the KEY: what the engine's translate really sees); sources NFKC already turns into ASCII
  are not needed;
* the key is stable under ``NFKC`` + ``casefold``, is not a combining mark (category M*, or
  ``unicodedata.combining``), not whitespace and not in category Cf / Zs / Zl / Zp / Cc / Cn;
* the target, after ``NFKC`` + ``casefold``, is exactly ONE ASCII lower-case letter or digit.

A file line is ``<hex of key><TAB><target>``. When several sources fold to one key, the source that
IS the key wins (TR39 maps capital Cyrillic I to "l", its lower-case form to "i"; the engine sees
the lower-case form, so the lower-case entry decides); ties between different sources with
different targets are dropped as ambiguous. Entries for the upper-case source itself would be dead
(the engine has already case-folded it) and are not written.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

URL = "https://www.unicode.org/Public/security/latest/confusables.txt"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "core" / "client_guard" / "data" / "confusables_latin.txt"
GENERATOR = "tools/masking_lab/gen_confusables.py"

_ENGINE_WHITESPACE = "ㅤᅟᅠﾠ​⠀᠎"
_BAD_CATEGORIES = {"Cf", "Zs", "Zl", "Zp", "Cc", "Cn", "Cs", "Co"}
_TARGET = re.compile(r"[a-z0-9]")


def fold(text: str) -> str:
    """What the engine's ``_normalise_unit`` has made of ``text`` just before the translate."""
    return unicodedata.normalize("NFKC", text).casefold()


def read_source(arg: str | None) -> str:
    if arg:
        return Path(arg).read_text(encoding="utf-8-sig")
    with urllib.request.urlopen(URL, timeout=60) as response:  # noqa: S310 - fixed https URL
        return response.read().decode("utf-8-sig")


def header_lines(text: str) -> tuple[str, str]:
    version = date = ""
    for line in text.splitlines():
        if line.startswith("# Version:") and not version:
            version = line.strip()
        elif line.startswith("# Date:") and not date:
            date = line.strip()
        if version and date:
            break
    return version, date


def usable_key(source: str) -> str | None:
    """The engine-side key of a source code point, or None when no entry can ever fire."""
    if len(source) != 1 or source.isascii():
        return None
    key = fold(source)
    if len(key) != 1 or key.isascii():
        return None
    if fold(key) != key or unicodedata.combining(key):
        return None
    if unicodedata.category(key) in _BAD_CATEGORIES or unicodedata.category(key)[0] == "M":
        return None
    if key.isspace() or key in _ENGINE_WHITESPACE:
        return None
    return key


def usable_target(codepoints: list[str]) -> str | None:
    if len(codepoints) != 1:
        return None
    target = fold(chr(int(codepoints[0], 16)))
    return target if len(target) == 1 and _TARGET.fullmatch(target) else None


def build_table(text: str) -> dict[str, str]:
    direct: dict[str, str] = {}
    indirect: dict[str, set[str]] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = [f.strip() for f in line.split(";")]
        if len(fields) < 2:
            continue
        source_hex, target_hex = fields[0], fields[1].split()
        try:
            source = chr(int(source_hex, 16))
        except ValueError:
            continue
        key, target = usable_key(source), usable_target(target_hex)
        if key is None or target is None:
            continue
        if key == source:
            direct.setdefault(key, target)
        else:
            indirect.setdefault(key, set()).add(target)
    table = dict(direct)
    for key, targets in indirect.items():
        if key not in table and len(targets) == 1:
            table[key] = next(iter(targets))
    return table


def render(table: dict[str, str], version: str, date: str) -> str:
    lines = [
        "# Cross-script look-alikes folded onto Latin letters / digits (Unicode TR39 confusables).",
        f"# Source: {URL}",
        "# Unicode Security Mechanisms for UTS #39 (confusables.txt, copied header lines):",
        version,
        date,
        f"# Generated by {GENERATOR} - do not edit by hand.",
        "# Licence: Unicode License v3 (https://www.unicode.org/license.txt), " "(c) Unicode, Inc.",
        "# Format: <hex code point of the case-folded look-alike><TAB><Latin target>; sorted.",
    ]
    lines += [f"{ord(key):04X}\t{table[key]}" for key in sorted(table, key=ord)]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", nargs="?", help="local confusables.txt (default: download)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output data file")
    args = parser.parse_args(argv)
    text = read_source(args.source)
    version, date = header_lines(text)
    table = build_table(text)
    if not version or len(table) < 500:
        print("confusables: unexpected input (no version header or too few mappings)")
        return 2
    with open(args.out, "w", encoding="utf-8", newline="") as handle:
        handle.write(render(table, version, date))
    print(f"confusables: {len(table)} mappings, {version[2:].strip()}, -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
