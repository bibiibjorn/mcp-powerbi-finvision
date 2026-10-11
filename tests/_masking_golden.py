"""Recorded behaviour of the masking stack under the ``finvision`` profile (phase 4a golden).

``snapshot()`` exercises every Finvision-specific decision point through the public defaults only
(no profile argument), so the very same call recorded the fixture at the pre-profile base commit
and checks it afterwards: with ``FINVISION_MASKING_PROFILE=finvision`` the output must be identical
(same prefixes, same pseudonyms for the same values). Synthetic names only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "masking" / "finvision_golden.json"
CLIENTS = Path(__file__).parent / "fixtures" / "masking" / "synthetic_clients.txt"


def _nrn(base: int) -> str:
    return f"{base:09d}{97 - base % 97:02d}"


def _kbo(base: int) -> str:
    return f"{base:08d}{97 - base % 97:02d}"


def _iban_be(account: int) -> str:
    digits = f"{account:012d}"
    check = 98 - int(digits + "111400") % 97  # BE = 11 14, check digits 00
    return f"BE{check:02d}{digits}"


_PAGE_RE = re.compile(
    r"DISTINCT\(SELECTCOLUMNS\('((?:[^']|'')+)', \"v\", '(?:[^']|'')+'\[((?:[^\]]|\]\])+)\]\)\)"
    r"\nORDER BY \[v\] ASC(?:\nSTART AT (.+))?$"
)


class _Model:
    """A tiny INFO.VIEW + paged DISTINCT fake (same protocol the detector reads)."""

    def __init__(self) -> None:
        self.columns: list[tuple[str, str, str]] = []
        self.values: dict[tuple[str, str], list[Any]] = {}
        self.param_tables: set[str] = set()

    def col(self, table: str, column: str, values: list[Any], dtype: str = "Text") -> None:
        self.columns.append((table, column, dtype))
        self.values[(table, column)] = list(values)

    def __call__(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        if "INFO.VIEW.COLUMNS" in query:
            keys = ["T", "C", "DT", "DC", "D"]
            return [dict(zip(keys, (*c, "", ""))) for c in self.columns]
        if "INFO.VIEW.TABLES" in query:
            tables = sorted({c[0] for c in self.columns})
            return [{"T": t, "X": "NAMEOF" if t in self.param_tables else None} for t in tables]
        if "INFO.VIEW.MEASURES" in query or "INFO.VIEW.RELATIONSHIPS" in query:
            return []
        if "INFO.TABLEPERMISSIONS" in query:
            return []
        m = _PAGE_RE.search(query)
        assert m, query
        key = (m.group(1).replace("''", "'"), m.group(2).replace("]]", "]"))
        distinct = sorted({v for v in self.values[key]}, key=lambda v: str(v).casefold())
        if m.group(3) is not None:
            text = m.group(3)
            cursor = text[1:-1].replace('""', '"') if text.startswith('"') else int(text)
            distinct = [v for v in distinct if str(v).casefold() >= str(cursor).casefold()]
        return [{"[v]": v} for v in distinct[:max_rows]]


def _names(prefix: str) -> list[str]:
    out = []
    for line in CLIENTS.read_text(encoding="utf-8").splitlines():
        head, _, rest = line.partition(":")
        if rest and head.strip() == prefix:
            out.append(rest.strip())
    return out


def _model() -> _Model:
    m = _Model()
    m.col("d_Customer", "Customer Name", _names("CUS"))
    m.col("d_Customer", "Address", ["12 Imaginary Lane Zorblaxville", "7 Fake Street Nonexistia"])
    m.col("d_Vendor", "Vendor", _names("VEN"))
    m.col("d_Person", "Full Name", _names("PER"))
    m.col("d_Person", "E-mail", ["quentin@zorblax.example", "marlowe@fakenstein.example"])
    m.col("d_Person", "Mobile", ["+32 470 12 34 56", "+31 6 12345678"])
    m.col("d_Family", "Family Name", ["Zorblax Family", "Quillfeather Family"])
    m.col("d_Entity", "Entity Name", _names("ENT"))
    m.col("d_Bank", "IBAN", [_iban_be(539007547034), _iban_be(123456789123)])
    m.col("d_Contact", "National Number", [int(_nrn(123456)), int(_nrn(654321))], "Int64")
    m.col("d_Counterparty", "Name", ["Wobbly Counterparty Co", "Plinky Counterparty Co"])
    m.col("d_Account", "Account Name", ["Testbank current account", "Dummy savings account"])
    m.col("d_Profit Center", "Profit Center Name", ["Testpilot Family Office", "Mock Pilot Hub"])
    m.col("d_Product", "Product Name", ["Flarbnox Premium Widget", "Glimmerstone Basic Widget"])
    m.col("f_Sales", "Memo", ["Quarterly gizmo invoice", "Annual widget invoice"])
    m.col("s_Period", "Month Label", ["Spring window", "Autumn window"])
    m.col("c_Helper", "Helper Text", ["Some helper words here"])
    m.col("d_Date", "Weekday", ["Mondayish", "Tuesdayish"])
    m.col("Report Lines", "Line Label", ["Gross margin line"])
    m.col("fp_Metric", "Metric", ["Revenue metric", "Cost metric"])
    m.param_tables.add("fp_Metric")
    return m


def _prefix_cases() -> dict[str, Any]:
    from core.client_guard.detector import dimension_prefix
    from core.client_guard.lexicon import prefix_for_names, tokenise

    pairs = [
        ("d_Customer", "Name"),
        ("d_Family", "Family Name"),
        ("d_Entity", "Entity Label"),
        ("d_Contact", "Contact Name"),
        ("d_Counterparty", "Name"),
        ("d_Vendor", "Supplier Code"),
        ("d_Company", "Firma"),
        ("d_Employee", "Voornaam"),
        ("d_User", "UserPrincipalName"),
        ("d_Bank", "Bankrekening IBAN"),
        ("d_Account", "Account Name"),
        ("d_Profit Center", "Profit Center Name"),
        ("d_CostCenter", "Cost Center"),
        ("s_Enterprise_Period", "Month Year"),
        ("d_Enterprise", "Name"),
        ("d_Ledger", "GL Account"),
        ("d_Product", "Product Name"),
        ("d_Debiteur", "Debiteur Nummer"),
        ("d_Klant", "Klantnaam"),
        ("d_Party", "Label"),
    ]
    return {
        "names": [[t, c, prefix_for_names(tokenise(t), tokenise(c))] for t, c in pairs],
        "dimension": [[t, c, dimension_prefix(t, c)] for t, c in pairs],
    }


def _shape_cases() -> list[list[Any]]:
    from core.client_guard.engine import normalise
    from core.client_guard.shapes import find_shapes

    n = _nrn(123456)
    k = _kbo(123456)
    texts = [
        "mail quentin@zorblax.example now",
        f"iban {_iban_be(539007547034)} paid",
        f"nrn {n} and {n[:2]}.{n[2:4]}.{n[4:6]}-{n[6:9]}.{n[9:]}",
        f"kbo be {k[:4]}.{k[4:7]}.{k[7:]} registered",
        "call +32 470 12 34 56 or +31 6 12345678",
        "not shapes: 12345678901 and 0123.456.789 and a@b",
    ]
    return [[t, [list(s) for s in find_shapes(normalise(t))]] for t in texts]


def _config_cases() -> dict[str, Any]:
    from client_gateway.detectors import LIST_PREFIXES, parse_client_list
    from core.client_guard import detector, ner
    from core.client_guard.engine import GENERIC_PREFIXES, PART_PREFIXES

    safe_names = [
        "d_Period",
        "Fiscal Calendar",
        "s_Anything",
        "c_Helper",
        "Number Format",
        "Report Lines",
        "d_Customer",
        "d_Vendor Rates",
        "Stock Exchange",
        "d_Product",
        "Candidate",
        "Time Entries",
    ]
    facts = detector._Facts([], frozenset(), frozenset(), frozenset())
    patterns = detector._compile_safe(None)
    parsed = parse_client_list(
        ["VEN: A Vendor", "PCC: A Center", "ENT: An Entity", "FAM: Ignored", "Plain Name", "X: y"]
    )
    return {
        "list_prefixes": list(LIST_PREFIXES),
        "parsed_list": {k: v for k, v in sorted(parsed.items())},
        "safe_patterns": list(detector.DEFAULT_SAFE_TABLES),
        "safe": [[n, detector.table_is_safe(n, facts, patterns)] for n in safe_names],
        "allow": sorted(ner.DEFAULT_ALLOW),
        "generic_prefixes_subset_ok": {"DIM", "GLA"} <= set(GENERIC_PREFIXES),
        "part_prefixes_subset_ok": {"CUS", "VEN", "PER", "ORG", "ENT", "FAM", "CLI"}
        <= set(PART_PREFIXES),
    }


def _engine_cases() -> dict[str, Any]:
    from core.client_guard.engine import SessionMap
    from core.client_guard.tokens import token_fn_from_key

    smap = SessionMap(token_fn=token_fn_from_key(bytes(range(32))))
    batches = {
        "FAM": ["Zorblax Family"],
        "CLI": ["Wobbly Counterparty Co"],
        "CUS": _names("CUS"),
        "VEN": _names("VEN"),
        "PER": _names("PER"),
        "ORG": _names("ORG"),
        "ENT": _names("ENT"),
        "PCC": ["Testpilot Family Office"],
        "DIM": ["Flarbnox Premium Widget", "Insurance"],
        "GLA": ["Testbank current account"],
    }
    for prefix, values in batches.items():
        smap.add_values(prefix, values, "golden")
    text = (
        "Zorblax Holdings NV paid Plinkerton Supplies BV; ask Quentin Testermans (Testermans), "
        "see Syntheticorp Europe NV, Testpilot Family Office, the Zorblax Family, Wobbly "
        "Counterparty Co, a Flarbnox Premium Widget, Insurance cost, Testbank current account. "
        f"Mail quentin@zorblax.example, {_iban_be(539007547034)}, +32 470 12 34 56, "
        f"{_nrn(123456)}, BE {_kbo(123456)}."
    )
    masked, count = smap.mask_text(text)
    restored, _n = smap.unmask_text(masked)
    return {"masked": masked, "count": count, "restored_ok": restored == text}


def _detect_cases() -> dict[str, Any]:
    from core.client_guard.detector import detect

    out: dict[str, Any] = {}
    for policy in ("identity", "dimensions"):
        result = detect(_model(), policy=policy)
        out[policy] = {
            "decisions": [
                [d.table, d.column, d.mask, list(d.reasons), d.prefix, d.distinct]
                for d in result.decisions
            ],
            "batches": [[b.prefix, b.strong, sorted(b.values)] for b in result.batches],
            "counts": result.counts(),
        }
    return out


def snapshot() -> dict[str, Any]:
    """Everything above as plain JSON data (deterministic, no timestamps or random keys)."""
    return {
        "prefixes": _prefix_cases(),
        "shapes": _shape_cases(),
        "config": _config_cases(),
        "engine": _engine_cases(),
        "detect": _detect_cases(),
    }
