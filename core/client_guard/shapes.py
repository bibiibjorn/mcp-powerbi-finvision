"""Shape detectors: identifiers that are recognised by their FORM, independent of any dictionary.

All functions work on already-normalised text (NFKC, casefolded, whitespace collapsed; see
``engine.normalise``) and return spans in that text. Each detector validates a checksum where one
exists, so ordinary numbers are not masked by accident.

Prefix per kind (spec section 3): e-mail -> USR, IBAN -> ACC, everything else -> the profile's
"other" prefix (``CLI`` under ``finvision``, ``VAL`` under ``generic``).

Core shapes (e-mail, international IBAN, phone) are always on. Country packs add shapes that only
exist in one country: pack ``be`` = Belgian national number and enterprise (KBO) number. Which packs
are on comes from the profile / ``client_guard.shape_packs`` (``core.client_guard.profiles``).
"""

from __future__ import annotations

import re
from collections.abc import Collection

PREFIX_EMAIL = "USR"
PREFIX_IBAN = "ACC"
PREFIX_OTHER = "CLI"

_IBAN_LENGTHS = {
    code[:2]: int(code[2:])
    for code in (
        "AD24 AE23 AL28 AT20 AZ28 BA20 BE16 BG22 BH22 BR29 BY28 CH21 CR22 CY28 CZ24 DE22 DK18 "
        "DO28 EE20 EG29 ES24 FI18 FO18 FR27 GB22 GE22 GI23 GL18 GR27 GT28 HR21 HU28 IE22 IL23 "
        "IQ23 IS26 IT27 JO30 KW30 KZ20 LB28 LC32 LI21 LT20 LU20 LV21 LY25 MC27 MD24 ME22 MK19 "
        "MR27 MT31 MU30 NL18 NO15 PK24 PL28 PS29 PT25 QA29 RO24 RS22 SA24 SC31 SE24 SI19 SK24 "
        "SM27 ST25 SV28 TL23 TN24 TR26 UA29 VA22 VG24 XK20"
    ).split()
}

_IBAN_START = re.compile(r"(?<![a-z0-9])([a-z]{2})\d{2}")
_NN_PLAIN = re.compile(r"(?<![\d.,])\d{11}(?!\d)(?![.,]\d)")
_NN_FORMATTED = re.compile(
    r"(?<![\d.,])\d{2}[. \-]\d{2}[. \-]\d{2}[. \-]\d{3}[. \-]\d{2}(?!\d)(?![.,]\d)"
)
_KBO = re.compile(r"(?<![a-z0-9])be ?([01]\d{3})[. ]?(\d{3})[. ]?(\d{3})(?!\d)")
_PHONE = re.compile(r"(?<![\w+\])])\+\d(?:[ .\-]?\d){7,14}(?!\d)")
_DIGIT = re.compile(r"\d")
_NON_DIGIT = re.compile(r"\D")

_EMAIL_LOCAL_MAX = 64
_EMAIL_DOMAIN_MAX = 255
_EMAIL_LOCAL_PUNCT = frozenset("._%+-")
_EMAIL_DOMAIN_PUNCT = frozenset(".-")

Span = tuple[int, int, str]


def iban_is_valid(candidate: str) -> bool:
    """True for a structurally valid IBAN: known country, exact length, MOD-97 == 1."""
    s = candidate.replace(" ", "").upper()
    if not (s.isascii() and s.isalnum()):
        return False
    length = _IBAN_LENGTHS.get(s[:2])
    if length is None or len(s) != length or not s[2:4].isdigit():
        return False
    rearranged = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def be_national_number_is_valid(digits: str) -> bool:
    """Belgian national number: 11 digits, check = 97 - (first 9 mod 97); also the 2000+ form."""
    if len(digits) != 11 or not digits.isdigit():
        return False
    base, check = int(digits[:9]), int(digits[9:])
    return 97 - base % 97 == check or 97 - (2_000_000_000 + base) % 97 == check


def be_kbo_is_valid(digits: str) -> bool:
    """Belgian enterprise number (KBO/BCE, VAT without the BE): 10 digits, mod-97 check."""
    if len(digits) != 10 or not digits.isdigit() or digits[0] not in "01":
        return False
    return 97 - int(digits[:8]) % 97 == int(digits[8:])


def _emails(norm: str) -> list[Span]:
    out: list[Span] = []
    n = len(norm)
    at = norm.find("@")
    while at != -1:
        j = at - 1
        while j >= 0 and at - j <= _EMAIL_LOCAL_MAX:
            c = norm[j]
            if c.isalnum() or c in _EMAIL_LOCAL_PUNCT:
                j -= 1
            else:
                break
        start = j + 1
        while start < at and norm[start] == ".":
            start += 1
        k = at + 1
        while k < n and k - at <= _EMAIL_DOMAIN_MAX:
            c = norm[k]
            if c.isalnum() or c in _EMAIL_DOMAIN_PUNCT:
                k += 1
            else:
                break
        end = k
        while end > at + 1 and norm[end - 1] in _EMAIL_DOMAIN_PUNCT:
            end -= 1
        first = at + 1
        domain = norm[first:end]
        labels = domain.split(".")
        if start < at and len(labels) >= 2 and all(labels):
            tld = labels[-1]
            if len(tld) >= 2 and tld.isalpha():
                out.append((start, end, PREFIX_EMAIL))
        at = norm.find("@", max(end, at + 1))
    return out


def _ibans(norm: str) -> list[Span]:
    out: list[Span] = []
    n = len(norm)
    for m in _IBAN_START.finditer(norm):
        length = _IBAN_LENGTHS.get(m.group(1).upper())
        if length is None:
            continue
        i = m.start()
        j = i
        count = 0
        while j < n and count < length:
            c = norm[j]
            if c.isascii() and c.isalnum():
                count += 1
                j += 1
            elif c == " " and j + 1 < n and norm[j + 1].isascii() and norm[j + 1].isalnum():
                j += 1
            else:
                break
        if count != length or (j < n and norm[j].isascii() and norm[j].isalnum()):
            continue
        if iban_is_valid(norm[i:j]):
            out.append((i, j, PREFIX_IBAN))
    return out


def find_shapes(
    norm: str,
    packs: Collection[str] | None = None,
    other_prefix: str = PREFIX_OTHER,
) -> list[Span]:
    """Every shape-detector hit in ``norm`` as ``(start, end, token_prefix)``.

    ``packs``: the country packs to run (``None`` = all known: leak scans want every shape);
    ``other_prefix``: the prefix of a shape that is neither e-mail nor IBAN."""
    out: list[Span] = []
    be = packs is None or "be" in packs
    if "@" in norm:
        out.extend(_emails(norm))
    if _DIGIT.search(norm) is None:
        return out
    out.extend(_ibans(norm))
    if be:
        for m in _NN_PLAIN.finditer(norm):
            if be_national_number_is_valid(m.group(0)):
                out.append((m.start(), m.end(), other_prefix))
        for m in _NN_FORMATTED.finditer(norm):
            if be_national_number_is_valid(_NON_DIGIT.sub("", m.group(0))):
                out.append((m.start(), m.end(), other_prefix))
        if "be" in norm:
            for m in _KBO.finditer(norm):
                if be_kbo_is_valid("".join(m.groups())):
                    out.append((m.start(), m.end(), other_prefix))
    if "+" in norm:
        for m in _PHONE.finditer(norm):
            out.append((m.start(), m.end(), other_prefix))
    return out
