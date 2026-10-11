"""Masking profiles: everything that is specific to one organisation is data here.

Two profiles ship:

* ``generic`` (the default): neutral token prefixes, no naming-convention rules, no company
  names in the allow list, only the shapes that exist everywhere (e-mail, international IBAN,
  phone) and name recognition (GLiNER) OFF until it is switched on.
* ``finvision``: exactly the behaviour the gateway had before profiles existed: the ``FAM`` /
  ``GLA`` / ``PCC`` / ``ENT`` / ``DIM`` / ``CLI`` prefixes and their word lists, the S01 / S02
  safe-table naming conventions, the Finvision allow list, the Belgian shape pack and NER on.

The active profile is, in order: environment ``FINVISION_MASKING_PROFILE``, config
``client_guard.profile``, else ``generic``. An unknown name falls back to ``generic`` and ``resolve``
reports it in ``warnings`` (the gateway shows it in ``status``). Country shape packs
(``client_guard.shape_packs``, e.g. ``["be"]``) add to the profile's own packs.

Prefix classes are NOT per profile: ``GENERIC_PREFIXES`` / ``PART_PREFIXES`` are the union over
all profiles, because the engine only needs to know how a prefix behaves, and a remembered value
must keep its rules when the profile is switched. Profiles differ in which prefixes get PRODUCED.

Standard library only (this package must stay importable without the server).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ENV_PROFILE = "FINVISION_MASKING_PROFILE"
PROFILE_KEY = "client_guard.profile"
PACKS_KEY = "client_guard.shape_packs"
DEFAULT_PROFILE = "generic"

# Union over all profiles (see the module docstring). ``VAL`` is generic vocabulary when a whole
# dimension column is masked; a value of a party column / a phone-like shape under ``VAL`` is
# stashed as ``strong`` (identity rules), exactly like a dimension value of a party table.
GENERIC_PREFIXES = frozenset({"DIM", "GLA", "VAL"})
# identity prefixes whose values are split into distinctive parts (see SessionMap._add_parts)
PART_PREFIXES = frozenset({"CUS", "VEN", "PER", "ORG", "ENT", "FAM", "CLI", "VAL"})

KNOWN_SHAPE_PACKS = frozenset({"be"})

# ---------------------------------------------------------------------------------- word lists
# party word (a stem of a table / column name token) -> prefix, first match wins
_COMMON_STEMS: tuple[tuple[str, str], ...] = (
    ("particip", "PER"),
    ("deelnemer", "PER"),
    ("teilnehmer", "PER"),
    ("person", "PER"),
    ("people", "PER"),
    ("owner", "PER"),
    ("eigenaar", "PER"),
    ("eigentumer", "PER"),
    ("proprietaire", "PER"),
    ("holder", "PER"),
    ("inhaber", "PER"),
    ("accounthold", "PER"),
    ("beneficiar", "PER"),
    ("employee", "PER"),
    ("medewerker", "PER"),
    ("werknemer", "PER"),
    ("contact", "PER"),
    ("voornaam", "PER"),
    ("achternaam", "PER"),
    ("prenom", "PER"),
    ("vorname", "PER"),
    ("nachname", "PER"),
    ("surname", "PER"),
    ("firstname", "PER"),
    ("lastname", "PER"),
    ("customer", "CUS"),
    ("klant", "CUS"),
    ("client", "CUS"),
    ("kunde", "CUS"),
    ("debtor", "CUS"),
    ("debiteur", "CUS"),
    ("vendor", "VEN"),
    ("supplier", "VEN"),
    ("leverancier", "VEN"),
    ("fournisseur", "VEN"),
    ("lieferant", "VEN"),
    ("creditor", "VEN"),
    ("crediteur", "VEN"),
    ("company", "ORG"),
    ("companies", "ORG"),
    ("firma", "ORG"),
    ("bedrijf", "ORG"),
    ("societe", "ORG"),
    ("vennootschap", "ORG"),
    ("orgunit", "ORG"),
    ("enterprise", "ORG"),
)

# Finvision: families and entities have their own prefix (the very first stems)
FINVISION_STEMS: tuple[tuple[str, str], ...] = (
    ("famil", "FAM"),
    ("entit", "FAM"),
    *_COMMON_STEMS,
)
# generic: a family is a group of persons, an entity an organisation
GENERIC_STEMS: tuple[tuple[str, str], ...] = (
    ("famil", "PER"),
    ("entit", "ORG"),
    *_COMMON_STEMS,
)

# --- Finvision dimension classes (policy 'dimensions'): PCC / GLA / ENT, else DIM
_PCC_WORDS = frozenset("pc profit profits winst winstcentrum kostenplaats kostenplaatsen".split())
_COST_WORDS = frozenset("cost costs".split())
_CENTER_WORDS = frozenset("center centers centre centres centrum centra".split())
_GLA_WORDS = frozenset(
    "gl ledger ledgers grootboek grootboekrekening grootboekrekeningen coa account accounts "
    "rekening rekeningen compte comptes konto".split()
)
_ENT_WORDS = frozenset(
    "entity entities entiteit entiteiten company companies firm organisation organization "
    "organisations organizations org subsidiary subsidiaries enterprise enterprises "
    "vennootschap vennootschappen societe societes legal".split()
)
_TIME_WORDS = frozenset(
    "period periods periode date dates datum calendar kalender time year years month months "
    "quarter quarters week weeks day days fiscal".split()
)


def _is_pcc(tokens: list[str]) -> bool:
    if any(t in _PCC_WORDS or t.startswith("profit") for t in tokens):
        return True
    if any(t in ("costcenter", "costcentre", "costcentres", "costcenters") for t in tokens):
        return True
    return any(a in _COST_WORDS and b in _CENTER_WORDS for a, b in zip(tokens, tokens[1:]))


def finvision_dimension_class(table_tokens: list[str], column_tokens: list[str]) -> str | None:
    """PCC (profit / cost centers), GLA (ledger accounts), ENT (entities / companies) or None
    (-> the profile's default). Column words win over table words; the words of a period table
    do not count (``s_Enterprise_Period`` holds periods, not enterprises)."""
    if any(t in _TIME_WORDS for t in table_tokens):
        table_tokens = []
    for tokens in (column_tokens, table_tokens):
        if _is_pcc(tokens):
            return "PCC"
        if any(t in _GLA_WORDS for t in tokens):
            return "GLA"
        if any(t in _ENT_WORDS for t in tokens):
            return "ENT"
    return None


# --- Finvision safe-table conventions (policy 'dimensions'): values of these tables are never
# masked. Each regex is searched (case-insensitive) in the raw table name AND in its tokenised
# form (``d_Period`` -> ``d period``), so a word only matches as a word (``Candidate`` is not a
# date table).
#
# The period / currency / scenario words count only when the WHOLE name is made of such words
# (``d_Period``, ``FX Rates``, ``Fiscal Calendar``): ``Stock Exchange``, ``d_Vendor Rates`` or
# ``Time Entries`` hold names and are masked. A table whose name holds a party word (customer,
# vendor, contact ...) is never safe by name; only structure (field parameter / measure table)
# or a configured pattern can make it safe.
_SAFE_WORDS = (
    "period|periods|periode|date|dates|datum|calendar|kalender|time|year|years|month|months"
    "|quarter|quarters|week|weeks|day|days|fiscal|currency|currencies|valuta|fx|rate|rates"
    "|exchange|scenario|scenarios|version|versions|view|views|selector|selection|table|tables"
)
_FORMAT_WORDS = (
    "format|formats|formatting|scale|scales|decimal|decimals|number|numbers|display|unit|units"
)
_LAYOUT_WORDS = (
    "report|line|lines|tag|map|mapping|dimension|kpi|kpis|setup|settings|config|configuration"
    "|layout"
)
_NAME_PREFIX = r"(?:(?:d|dim|dm|f|fact|h|t) )?"


def _whole_name(words: str) -> str:
    return rf"^{_NAME_PREFIX}(?:(?:{words})(?: |$))+$"


FINVISION_SAFE_TABLES: tuple[str, ...] = (
    # period / date / calendar / time, currency / fx / rate, scenario / version
    _whole_name(_SAFE_WORDS),
    # field parameter, slicer and helper tables (prefix convention)
    r"^(s_|s |sf_|sf |sfp_|c_|m_|fp_)",
    # number-format / scale helper tables
    _whole_name(_FORMAT_WORDS),
    # report-line / layout tables
    _whole_name(_LAYOUT_WORDS),
)

# --- allow lists (never masked by the name recognizer)
_SOFTWARE_ALLOW: tuple[str, ...] = (
    "Anthropic",
    "Claude",
    "Claude Code",
    "Claude Desktop",
    "Microsoft",
    "Microsoft Fabric",
    "Fabric",
    "Power BI",
    "Power BI Desktop",
    "Power Query",
    "DAX",
    "DAX Studio",
    "VertiPaq",
    "Tabular Editor",
    "Excel",
    "Outlook",
    "Teams",
    "SharePoint",
    "OneDrive",
    "Azure",
    "AWS",
    "Google",
    "GitHub",
    "Azure DevOps",
    "Python",
    "Deneb",
    "Vega",
    "Performance Analyzer",
    "Analysis Services",
    "SQL Server",
    "Windows",
    "VS Code",
    "Copilot",
    "OpenAI",
    "ChatGPT",
    "Presidio",
    "GLiNER",
    "Hugging Face",
    "SAP",
    "GDPR",
)
# products, tools, platforms and our own companies: organisations, never clients
FINVISION_ALLOW: frozenset[str] = frozenset(
    w.casefold()
    for w in (
        *_SOFTWARE_ALLOW,
        "Finticx",
        "Finvision",
        "Silverfin",
        "Exact Online",
        "Odoo",
        "Isabel",
        "KBO",
        "ITAA",
        "FOD Financiën",
        "EDPB",
        "AVG",
    )
)
GENERIC_ALLOW: frozenset[str] = frozenset(w.casefold() for w in _SOFTWARE_ALLOW)


# ---------------------------------------------------------------------------------- profiles
@dataclass(frozen=True)
class Profile:
    """One masking profile (data only; see the module docstring)."""

    name: str
    # prefixes a ``PREFIX: name`` line of the client list may use (no prefix: ORG)
    list_prefixes: tuple[str, ...]
    # party word -> token prefix (first match in the column's, then the table's name tokens)
    prefix_stems: tuple[tuple[str, str], ...]
    # prefix of a party column of an unknown kind
    party_default_prefix: str
    # prefix of a phone / national number / enterprise number shape
    other_shape_prefix: str
    # prefix of a masked dimension column / table cell that no dimension class names
    dimension_default_prefix: str
    # (table tokens, column tokens) -> prefix, or None for the default
    dimension_classifier: Callable[[list[str], list[str]], str | None] | None
    # regexes of tables never masked under policy 'dimensions' (plus ``client_guard.safe_tables``)
    safe_table_patterns: tuple[str, ...]
    # skip date tables (DataCategory Time) and calculation groups as non-client structure
    structural_skips: bool
    # lower-case terms the name recognizer never masks
    allow_terms: frozenset[str]
    # country shape packs on by default
    shape_packs: frozenset[str]
    # name recognition (GLiNER) on when ``client_guard.ner.enabled`` is not set
    ner_default: bool


FINVISION = Profile(
    name="finvision",
    list_prefixes=("CUS", "VEN", "PER", "ORG", "ENT", "PCC"),
    prefix_stems=FINVISION_STEMS,
    party_default_prefix="CLI",
    other_shape_prefix="CLI",
    dimension_default_prefix="DIM",
    dimension_classifier=finvision_dimension_class,
    safe_table_patterns=FINVISION_SAFE_TABLES,
    structural_skips=False,
    allow_terms=FINVISION_ALLOW,
    shape_packs=frozenset({"be"}),
    ner_default=True,
)

GENERIC = Profile(
    name="generic",
    list_prefixes=("CUS", "VEN", "PER", "ORG"),
    prefix_stems=GENERIC_STEMS,
    party_default_prefix="VAL",
    other_shape_prefix="VAL",
    dimension_default_prefix="VAL",
    dimension_classifier=None,
    safe_table_patterns=(),
    structural_skips=True,
    allow_terms=GENERIC_ALLOW,
    shape_packs=frozenset(),
    ner_default=False,
)

PROFILES: dict[str, Profile] = {p.name: p for p in (GENERIC, FINVISION)}


# ---------------------------------------------------------------------------------- selection
@dataclass(frozen=True)
class Resolved:
    """The profile in force, its effective shape packs and fixed-label warnings."""

    profile: Profile
    shape_packs: frozenset[str]
    warnings: tuple[str, ...] = ()


def _config_get(key: str, default: Any) -> Any:
    try:
        from core.config.config_manager import config

        return config.get(key, default)
    except Exception:  # noqa: BLE001 - never block masking on the config
        return default


def _pack_names(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        items: list[Any] = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        return ["?"]  # an unreadable setting is reported as an unknown pack
    return [str(i).strip().lower() for i in items if str(i).strip()]


def resolve(get: Callable[[str, Any], Any] | None = None) -> Resolved:
    """Profile + shape packs from the environment and config; never raises."""
    read = get or _config_get
    warnings: list[str] = []
    raw = os.environ.get(ENV_PROFILE, "").strip()
    if not raw:
        value = read(PROFILE_KEY, DEFAULT_PROFILE)
        raw = str(value).strip() if value is not None else ""
    name = (raw or DEFAULT_PROFILE).lower()
    profile = PROFILES.get(name)
    if profile is None:
        warnings.append("profile_unknown")
        profile = PROFILES[DEFAULT_PROFILE]
    packs = set(profile.shape_packs)
    for pack in _pack_names(read(PACKS_KEY, None)):
        if pack in KNOWN_SHAPE_PACKS:
            packs.add(pack)
        elif "shape_pack_unknown" not in warnings:
            warnings.append("shape_pack_unknown")
    return Resolved(profile, frozenset(packs), tuple(warnings))


def active_profile() -> Profile:
    """The profile in force (see ``resolve``)."""
    return resolve().profile
