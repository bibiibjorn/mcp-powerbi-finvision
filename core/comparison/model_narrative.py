"""
Model narrative generator

Builds a short executive summary describing what the model appears to do,
based on lightweight summary info and relationships.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _pick_top(items: List[Tuple[str, int]], n: int = 10) -> List[str]:
    return [name for name, _ in sorted(items, key=lambda kv: kv[1], reverse=True)[:n]]


def _classify_tables(summary: Dict[str, Any]) -> Dict[str, List[str]]:
    names = [t.get("name") or "" for t in (summary.get("tables") or {}).get("list", [])]
    by_cols: Dict[str, int] = (summary.get("columns") or {}).get("by_table", {}) or {}
    by_meas: Dict[str, int] = (summary.get("measures") or {}).get("by_table", {}) or {}

    facts: List[str] = []
    dims: List[str] = []
    measure_hubs: List[str] = []

    for nm in names:
        lname = nm.lower()
        if lname.startswith("f_") or " fact" in lname or lname.startswith("fact"):
            facts.append(nm)
        elif lname.startswith("d_") or lname.startswith("dim") or " dimension" in lname:
            dims.append(nm)
        elif lname.startswith("m_") or "measure" in lname:
            measure_hubs.append(nm)

    # Heuristic: large column count could indicate a fact-like table
    if by_cols:
        col_items = list(by_cols.items())
        # Any table in top quartile of column counts and not already classified as dimension could be fact-like
        counts = sorted([c for _, c in col_items])
        if counts:
            q3_idx = min(int(0.75 * len(counts)), len(counts) - 1)
            q3 = counts[q3_idx]
            for nm, c in col_items:
                if c >= q3 and nm not in dims and nm not in facts:
                    facts.append(nm)

    # If nothing detected, fall back to top by measures/columns
    if not facts:
        facts = _pick_top(
            [
                (k, by_cols.get(k, 0) + by_meas.get(k, 0))
                for k in set(list(by_cols.keys()) + list(by_meas.keys()))
            ],
            3,
        )
    if not dims:
        dims = _pick_top([(k, by_cols.get(k, 0)) for k in by_cols.keys()], 5)

    return {
        "facts": sorted(list(dict.fromkeys(facts))),
        "dimensions": sorted(list(dict.fromkeys(dims))),
        "measure_hubs": sorted(list(dict.fromkeys(measure_hubs))),
    }


def _extract_domains(table_names: List[str]) -> List[str]:
    keywords = {
        "date": "Calendar/Time",
        "period": "Period/Time",
        "customer": "Customer",
        "vendor": "Vendor/Supplier",
        "company": "Company/Org",
        "currency": "Currency/FX",
        "account": "GL Accounts",
        "gl ": "GL Accounts",
        "profit center": "Profit Center",
        "cost center": "Cost Center",
        "scenario": "Scenario/Version",
        "aging": "Aging/AR/AP",
        "sales": "Sales",
        "invoice": "Billing/Invoice",
        "rls": "Row-Level Security",
        "report": "Reporting",
        "waterfall": "Waterfall",
    }
    found: Dict[str, int] = {}
    for nm in table_names:
        ln = nm.lower()
        for k, label in keywords.items():
            if k in ln:
                found[label] = found.get(label, 0) + 1
    # Sort by frequency
    return [label for label, _ in sorted(found.items(), key=lambda kv: kv[1], reverse=True)]


def generate_narrative(
    summary: Dict[str, Any], relationships: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    counts = summary.get("counts") or {}
    table_list = [(t.get("name") or "") for t in (summary.get("tables") or {}).get("list", [])]
    table_list = [t for t in table_list if t]
    classified = _classify_tables(summary)
    domains = _extract_domains(table_list)
    rel = summary.get("relationships") or (relationships or {})
    rel_count = rel.get("count", 0)
    rel_active = rel.get("active", 0)
    rel_inactive = rel.get("inactive", 0)

    # Simple star-like hint
    star_hint = None
    if (
        classified["facts"]
        and classified["dimensions"]
        and rel_count >= max(5, len(classified["dimensions"]))
    ):
        star_hint = (
            "Model appears star-schema oriented (fact table(s) with multiple dimension links)."
        )

    # Compose brief text
    text_lines: List[str] = []
    text_lines.append(
        f"This model contains {counts.get('tables', 0)} tables, {counts.get('columns', 0)} columns, "
        f"and {counts.get('measures', 0)} measures, connected by {rel_count} relationships ("
        f"{rel_active} active, {rel_inactive} inactive)."
    )
    if domains:
        text_lines.append(f"The data domain looks to include: {', '.join(domains[:5])}.")
    if classified["facts"]:
        text_lines.append("Likely fact tables: " + ", ".join(classified["facts"][:3]))
    if classified["dimensions"]:
        text_lines.append("Key dimensions: " + ", ".join(classified["dimensions"][:6]))
    if classified["measure_hubs"]:
        text_lines.append("Measures are organized in: " + ", ".join(classified["measure_hubs"][:3]))
    if star_hint:
        text_lines.append(star_hint)

    highlights: List[str] = []
    if rel_inactive:
        highlights.append(f"{rel_inactive} inactive relationship(s) detected")
    if "Calendar/Time" in domains or any("date" in t.lower() for t in table_list):
        highlights.append("Time intelligence likely in use (presence of Date/Period tables)")
    if any("currency" in t.lower() for t in table_list):
        highlights.append("Currency conversion present (Currency tables detected)")
    if any("rls" in t.lower() for t in table_list):
        highlights.append("Row-Level Security artifacts present")

    return {
        "success": True,
        "text": " ".join(text_lines),
        "highlights": highlights,
        "guesses": classified,
        "domains": domains,
        "ctas": [
            {
                "label": "Pick Fast vs Normal Analysis",
                "tool": "propose_analysis",
                "arguments": {"goal": "analyze the model"},
            }
        ],
    }
