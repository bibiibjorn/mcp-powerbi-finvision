"""Name lexicon of the Client Guard detector: tokenise table/column names, spot party words.

Table and column names are split into tokens first (space, ``_``, ``-``, ``.`` and camelCase /
PascalCase boundaries, accents folded, lower case), so ``d_Customer`` gives ``d, customer`` and
``UserPrincipalName`` gives ``user, principal, name``. The lexicon covers nl / fr / en / de.

Two word classes are kept apart on purpose:

* **party words** name a party (family, customer, vendor, participant, user, e-mail, IBAN ...).
  A column whose own name holds one is a candidate, and so is an identifying column
  (``name``, ``code``, ``id`` ...) of a table whose name holds one.
* **identifier words** (``name``, ``naam``, ``nom``, ``code``, ``id`` ...) say a column is an
  identifier or a label. Alone they decide nothing: ``d_Product[Name]`` is not a client column.

"member" is deliberately NOT a party word (``Member`` is an everyday model word).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from core.client_guard.profiles import Profile, active_profile

_UP = "A-ZÀ-Þ"
_LOW = "a-zß-ÿ"
_SPLIT = re.compile("[^0-9A-Za-zÀ-ɏ]+")
_CAMEL = re.compile(rf"[{_UP}]+(?=[{_UP}][{_LOW}])|[{_UP}]?[{_LOW}]+|[{_UP}]+|\d+")

# Whole-token matches (plurals spelled out where a stem would be too greedy).
_PARTY_EXACT = frozenset("""
    person persons persoon personen personne personnes people
    owner owners holder holders beneficiary beneficiaries
    customer customers debtor debtors debiteur debiteuren debiteurs
    creditor creditors crediteur crediteuren
    vendor vendors supplier suppliers
    counterparty counterparties party parties
    employee employees medewerker medewerkers werknemer werknemers
    contact contacts contactpersoon
    relatie relaties
    company companies firma firmen bedrijf bedrijven societe societes vennootschap
    enterprise orgunit
    user users username usernames gebruiker gebruikers utilisateur utilisateurs benutzer upn login
    email emails mail mails courriel
    iban ibans bankrekening bankrekeningen
    deelnemer deelnemers teilnehmer inhaber kunde kunden lieferant lieferanten
    eigenaar eigenaars eigentumer proprietaire proprietaires
    voornaam achternaam familienaam prenom vorname nachname surname firstname lastname
    """.split())

# Prefix matches: concatenated Dutch / French / German words (``klantnaam``, ``familienaam``).
_PARTY_STEMS = (
    "famil",
    "entit",
    "particip",
    "client",
    "klant",
    "leverancier",
    "fournisseur",
    "lieferant",
    "deelnemer",
    "eigenaar",
    "beneficiar",
    "bedrijf",
    "contactpers",
    "accounthold",
    "bankaccount",
)

# Two-word phrases whose parts alone are not party words.
_PARTY_PHRASES = frozenset(
    {
        ("bank", "account"),
        ("bank", "rekening"),
        ("org", "unit"),
        ("first", "name"),
        ("last", "name"),
        ("given", "name"),
        ("sur", "name"),
        ("e", "mail"),
    }
)

_IDENT = frozenset("""
    name names naam namen nom noms label labels id ids code codes group groups
    number nummer numero
    """.split())

_PHONE = frozenset("phone phones tel telephone telefoon telefon gsm mobile mobiel fax".split())

# Names that hint at a national / enterprise number held in a numeric column.
_NUMBER_HINT = frozenset("""
    nrn rrn niss insz ssn national nationaal rijksregister registre
    kbo bce vat btw tva enterprise ondernemingsnummer
    """.split())

_PARTY_DATA_CATEGORIES = frozenset({"address"})

_USER_WORDS = frozenset("""
    user users username usernames gebruiker gebruikers utilisateur utilisateurs benutzer upn
    login principal email emails mail mails courriel
    """.split())
_IBAN_WORDS = frozenset("iban ibans bankrekening bankrekeningen".split())


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def tokenise(name: str) -> list[str]:
    """Lower-case, accent-folded tokens of a table / column name (see the module docstring)."""
    tokens: list[str] = []
    for chunk in _SPLIT.split(_fold(name)):
        if chunk:
            tokens.extend(t.lower() for t in _CAMEL.findall(chunk))
    return tokens


def _party_token(token: str) -> bool:
    return token in _PARTY_EXACT or any(token.startswith(s) for s in _PARTY_STEMS)


def has_party_word(tokens: list[str]) -> bool:
    """True when a token (or an adjacent pair) names a party."""
    if any(_party_token(t) for t in tokens):
        return True
    return any((a, b) in _PARTY_PHRASES for a, b in zip(tokens, tokens[1:]))


def has_ident_word(tokens: list[str]) -> bool:
    return any(t in _IDENT for t in tokens)


def has_phone_word(tokens: list[str]) -> bool:
    return any(t in _PHONE for t in tokens)


def has_number_hint(tokens: list[str]) -> bool:
    return any(t in _NUMBER_HINT for t in tokens)


def is_user_name(tokens: list[str]) -> bool:
    return any(t in _USER_WORDS for t in tokens) or ("e", "mail") in set(zip(tokens, tokens[1:]))


def is_iban_name(tokens: list[str]) -> bool:
    if any(t in _IBAN_WORDS or t.startswith("bankaccount") for t in tokens):
        return True
    pairs = set(zip(tokens, tokens[1:]))
    return ("bank", "account") in pairs or ("bank", "rekening") in pairs


def prefix_for_names(
    table_tokens: list[str], column_tokens: list[str], profile: Profile | None = None
) -> str:
    """Token prefix for a masked column: column words first, then table words, else the profile's
    default (``CLI`` under ``finvision``, ``VAL`` under ``generic``). The word -> prefix map is
    the profile's (``profiles.py``)."""
    if is_iban_name(column_tokens):
        return "ACC"
    if is_user_name(column_tokens):
        return "USR"
    prof = profile or active_profile()
    for tokens in (column_tokens, table_tokens):
        for token in tokens:
            for stem, prefix in prof.prefix_stems:
                if token.startswith(stem):
                    return prefix
    return prof.party_default_prefix


@dataclass(frozen=True)
class NameHints:
    """What the names (and optional description / data category) say about a column."""

    party: bool
    party_via: str
    ident: bool
    phone: bool
    number_hint: bool
    table_tokens: tuple[str, ...]
    column_tokens: tuple[str, ...]


def name_hints(
    table: str,
    column: str,
    description: str = "",
    data_category: str = "",
) -> NameHints:
    """Name-based evidence for ``table[column]``.

    ``party_via`` says which rule fired: ``column-name``, ``table-name`` (a party table plus an
    identifying column), ``data-category`` or ``description`` (a party word in the description of
    an identifying column); empty when none did.
    """
    t_tok, c_tok = tokenise(table), tokenise(column)
    ident = has_ident_word(c_tok)
    via = ""
    if has_party_word(c_tok):
        via = "column-name"
    elif ident and has_party_word(t_tok):
        via = "table-name"
    elif _fold(data_category).strip().lower() in _PARTY_DATA_CATEGORIES:
        via = "data-category"
    elif ident and description and has_party_word(tokenise(description)):
        via = "description"
    return NameHints(
        party=bool(via),
        party_via=via,
        ident=ident,
        phone=has_phone_word(c_tok),
        number_hint=has_number_hint(c_tok) or has_party_word(c_tok) or has_ident_word(c_tok),
        table_tokens=tuple(t_tok),
        column_tokens=tuple(c_tok),
    )
