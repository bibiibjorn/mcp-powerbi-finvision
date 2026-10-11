"""Synthetic, labelled corpus for the masking evaluation (seeded, deterministic).

A ``Sample`` carries the text, the ``gold`` surfaces that must NOT survive masking (client values
as they appear in that text, typos included) and the ``keep`` surfaces that MUST survive (report
vocabulary, model object names, product names, ordinary words). ``dictionary()`` is the value set
a provider would have read from an open model; ``unknown`` names are deliberately NOT in it.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

SEED = 20261002

FIRST = (
    "Jan Pieter Lotte Sofie Wouter Elke Bart Inge Joris Annelies Koen Lien Thomas Marie Lucas Emma "
    "Arnaud Camille Nathalie Olivier Céline Julien Mathilde Yannick Chloé Hanne Ruben Femke Stijn "
    "Ilse Dries Margot Jeroen Evelien Maxime Amélie"
).split()
SURNAMES = (
    "Peeters Janssens Maes Jacobs Mertens Willems Claes Goossens Wouters Dubois Lambert Dupont "
    "Vermeulen Michiels Hermans Pauwels Lemaire Declercq Vandenberghe Coppens Renard Leclercq "
    "Martens Verstraeten Cools Segers Vanhoutte Dewulf Lenaerts Verbeke Wuyts Thys Moens Jansen "
    "Bogaerts Smets Lauwers Van Damme De Smet Van den Broeck"
).split(" ")
# multi-word surnames survive the split above as separate tokens; rebuild the known ones
SURNAMES = [s for s in SURNAMES if s not in ("Van", "Damme", "De", "Smet", "den", "Broeck")] + [
    "Van Damme",
    "De Smet",
    "Van den Broeck",
]
WORDLIKE_SURNAMES = ["Bakker", "Visser", "Smit", "Koster", "De Koning", "Vos"]
STEMS = (
    "Bouwwerken Logistiek Consult Invest Immo Techniek Distributie Transport Engineering "
    "Interieur Metaal Verpakking Elektro Coating"
).split()
FORMS = ["NV", "BV", "BVBA", "SRL", "SA", "CommV", "VOF", "Ltd"]
SHORT_COMPANIES = ["VDK", "ZNB", "QBX", "Bols", "Ixor"]
WORDLIKE_COMPANIES = ["Atlas", "Horizon", "Kompas"]

GENERIC_GLA = ["Other income", "Bank charges", "Marketing costs", "Services", "Rent"]
GENERIC_DIM = ["Noord", "Zuid", "Projecten 2025", "Atelier Zuid", "Centrum"]
PROFIT_CENTERS = ["Antwerpen Haven", "Gent Centrum", "Liège Nord"]

KEEP_TERMS = [
    "Power BI",
    "DAX Studio",
    "Claude Code",
    "Anthropic",
    "Microsoft Fabric",
    "Finticx",
    "Finvision",
    "Excel",
    "Performance Analyzer",
    "VertiPaq",
    "SUMMARIZECOLUMNS",
    "CALCULATE",
]


@dataclass
class Sample:
    category: str
    text: str
    gold: list[str] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)


@dataclass
class Corpus:
    dictionary: dict[str, list[str]]
    strong_dim: list[str]
    samples: list[Sample]
    unknown_names: list[str]


def _company(rng: random.Random) -> str:
    kind = rng.random()
    s1, s2 = rng.sample(SURNAMES, 2)
    if kind < 0.45:
        return f"{s1} {rng.choice(STEMS)} {rng.choice(FORMS)}"
    if kind < 0.7:
        return f"{s1} & {s2}"
    if kind < 0.85:
        return f"{rng.choice(STEMS)} {s1}"
    return f"{s1} {rng.choice(FORMS)}"


def _person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(SURNAMES)}"


_KEYS = "qwertyuiopasdfghjklzxcvbnm"


def typo(word: str, rng: random.Random, edits: int = 1) -> str:
    """One or two keyboard-style edits inside the longest token (never the first letter)."""
    parts = word.split(" ")
    i = max(range(len(parts)), key=lambda k: len(parts[k]))
    w = parts[i]
    for _ in range(edits):
        if len(w) < 4:
            break
        pos = rng.randrange(1, len(w) - 1)
        op = rng.choice("sdti")
        if op == "s":
            w = w[:pos] + rng.choice(_KEYS) + w[pos + 1 :]
        elif op == "d":
            w = w[:pos] + w[pos + 1 :]
        elif op == "t" and pos + 1 < len(w):
            w = w[:pos] + w[pos + 1] + w[pos] + w[pos + 2 :]
        else:
            w = w[:pos] + rng.choice(_KEYS) + w[pos:]
    parts[i] = w
    return " ".join(parts)


def _amount(rng: random.Random) -> str:
    return f"{rng.randint(1_000, 950_000):,}".replace(",", ".") + f",{rng.randint(0, 99):02d}"


def build(seed: int = SEED) -> Corpus:
    rng = random.Random(seed)
    customers = sorted({_company(rng) for _ in range(60)})
    vendors = sorted({_company(rng) for _ in range(60)} - set(customers))
    persons = sorted({_person(rng) for _ in range(60)})
    unknown_companies = sorted({_company(rng) for _ in range(80)} - set(customers) - set(vendors))
    unknown_persons = sorted({_person(rng) for _ in range(80)} - set(persons))
    wordlike_persons = [f"{rng.choice(FIRST)} {s}" for s in WORDLIKE_SURNAMES]
    dictionary = {
        "CUS": customers + SHORT_COMPANIES[:3] + WORDLIKE_COMPANIES,
        "VEN": vendors + SHORT_COMPANIES[3:],
        "PER": persons + wordlike_persons + WORDLIKE_SURNAMES,
        "PCC": PROFIT_CENTERS,
        "GLA": GENERIC_GLA,
        "DIM": GENERIC_DIM,
    }
    strong_dim = [f"Kerkstraat {n}, 9000 Gent" for n in (12, 48, 103)]
    samples: list[Sample] = []
    add = samples.append

    def pick(pool: list[str]) -> str:
        return rng.choice(pool)

    # 1. dictionary values, exact, in prose (nl / en / fr)
    for _ in range(40):
        c, p = pick(customers + vendors), pick(persons)
        t = rng.choice(
            [
                f"Kan je nagaan waarom {c} in oktober een openstaand saldo van € {_amount(rng)} heeft? "
                f"{p} vroeg ernaar.",
                f"Why does {c} show {_amount(rng)} overdue in the aging report? {p} flagged it.",
                f"Pouvez-vous vérifier le solde de {c} pour septembre ? Contact : {p}.",
            ]
        )
        add(Sample("dict_exact_prose", t, [c, p]))
    # 2. dictionary values inside a DAX result (JSON text)
    for _ in range(25):
        rows = [
            {"d_Vendor[VendorName]": pick(vendors), "[AP___Total__RC_]": rng.random() * 1e5}
            for _ in range(4)
        ]
        add(
            Sample(
                "dict_exact_json",
                json.dumps({"rows": rows}),
                [r["d_Vendor[VendorName]"] for r in rows],
                ["d_Vendor[VendorName]"],
            )
        )
    # 3. other casing
    for _ in range(20):
        c = pick(customers)
        form = rng.choice([c.upper(), c.lower()])
        add(Sample("dict_case", f"Status update: {form} paid the invoice late again.", [form]))
    # 4. typos of dictionary names (1 edit; 2 edits for long tokens)
    for _ in range(50):
        base = pick(customers + vendors + persons)
        longest = max(len(t) for t in base.split(" "))
        variant = typo(base, rng, 2 if longest >= 9 and rng.random() < 0.3 else 1)
        if variant == base:
            continue
        t = rng.choice(
            [
                f"Kun je {variant} even nakijken? Het saldo lijkt fout.",
                f"Please check {variant} for the September close.",
                f"Le client {variant} conteste la facture.",
            ]
        )
        add(Sample("typo", t, [variant]))
    # 5. short identity values (fewer than 4 characters, or 4 in caps-like names)
    for name in SHORT_COMPANIES:
        for t in (
            f"We paid {name} twice in Q3.",
            f"Het contract met {name} loopt af.",
            f"Factuur {name} ontbreekt.",
        ):
            add(Sample("short_identity", t, [name]))
    # 6. identity values that are ordinary words: as a name (must mask) and as a word (keep)
    for name in WORDLIKE_COMPANIES + [p for p in wordlike_persons]:
        add(Sample("wordlike_identity", f"Klant {name} vraagt een kredietnota.", [name]))
    for word, sentence in (
        ("atlas", "The atlas of the region is printed in colour."),
        ("bakker", "De bakker op de hoek levert elke ochtend brood."),
        ("visser", "Een visser vertrekt vroeg naar zee."),
        ("horizon", "Over a five-year horizon the forecast stays flat."),
        ("kompas", "Gebruik het kompas om de richting te bepalen."),
        ("vos", "De vos sluipt door het bos."),
    ):
        add(Sample("wordlike_ordinary", sentence, [], [word]))
    # 7. names that are NOT in any open model (emails, notes, pasted text)
    for _ in range(50):
        c, p = pick(unknown_companies), pick(unknown_persons)
        first = p.split(" ")[0].lower()
        t = rng.choice(
            [
                f"Mail van {p}: de factuur voor {c} klopt niet, kan je dit bekijken?",
                f"Meeting notes – {p} (CFO at {c}) asks for a split per profit center.",
                f"Bonjour, je suis {p} de {c}. Le montant de septembre est incorrect.",
                f"Van: {p} <{first}@example.be>\nOnderwerp: aanmaning {c}\nGraag een update.",
            ]
        )
        add(Sample("unknown_names", t, [c, p]))
    # 8. generic report vocabulary: must stay readable in prose
    for _ in range(15):
        g, d = pick(GENERIC_GLA), pick(GENERIC_DIM[:2] + ["Centrum"])
        add(
            Sample(
                "generic_prose",
                f"In Q3 2025 {g.lower()} rose while {d} stayed flat; Total Revenue fell 4%.",
                [],
                [g.lower(), "Total Revenue", "Q3 2025"],
            )
        )
    # 9. strong dimension values (addresses of a party table)
    for a in strong_dim:
        add(Sample("strong_dim", f"Leveringsadres: {a}. Graag bevestigen.", [a]))
    # 9b. a client named by a distinctive part of its name only ("Vanhoutte" for the company)
    import collections

    identity = customers + vendors + persons + wordlike_persons + WORDLIKE_SURNAMES
    df = collections.Counter(w for v in identity for w in set(v.split(" ")))
    parts = sorted(
        {
            w
            for v in customers + vendors
            for w in v.split(" ")
            if len(w) >= 5 and df[w] <= 2 and w not in STEMS and w not in FORMS
        }
    )
    for w in rng.sample(parts, min(20, len(parts))):
        add(
            Sample(
                "partial_name",
                rng.choice(
                    [
                        f"Heb je nog nieuws van {w} over de factuur van september?",
                        f"Any update from {w} on the overdue invoices?",
                    ]
                ),
                [w],
            )
        )
    # 10. developer / tool text that must stay readable
    negatives = [
        "EVALUATE SUMMARIZECOLUMNS('d_Vendor'[VendorName], \"Total\", [AP | Total (RC)])",
        "SET DC_KIND=\"AUTO\"; SELECT 'd_Currency_Rates'.[Exchange rate type] FROM 'd_Currency_Rates';",
        "Run 09_Debug_Operations in Power BI Desktop, then compare with DAX Studio and VertiPaq.",
        "Claude Code talks to Anthropic; the report runs on Microsoft Fabric for Finticx and Finvision.",
        "def total(rows):\n    return sum(r['Amount'] for r in rows)  # Excel export",
        "Use CALCULATE with KEEPFILTERS; Performance Analyzer shows the visual at 340 ms.",
        "Periods: OCT 25, Q3 2025, FY24; currencies EUR, USD; scenario Budget vs Actual.",
        "The Services line and Other Income are report lines, not customers.",
    ]
    for t in negatives:
        keep = [k for k in KEEP_TERMS if k in t]
        add(Sample("negative_dev", t, [], keep))
    return Corpus(dictionary, strong_dim, samples, unknown_companies + unknown_persons)
