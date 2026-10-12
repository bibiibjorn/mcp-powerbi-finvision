"""Synthetic TMSL models for the BPA tests (review I-01 … I-05, I-12, I-28).

Everything here is invented (Contoso-style names); the SHAPE mirrors what
``JsonSerializer.SerializeObject(Model)`` emits for a Power BI Desktop model:
lowerCamel keys, lowerCamel enums, default-valued properties omitted,
``"type": "calculated"`` for calculated columns, a ``rowNumber`` column,
multi-line expressions as JSON arrays, M partitions.

``build_clean_model`` is a well-built star schema (hidden keys with
``isAvailableInMdx: false``, decimal amounts, marked date table, sorted month
names, described + formatted measures in display folders, single-direction
many-to-one relationships, fully qualified column references). A correct BPA
engine should report (almost) nothing on it.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List

_DIMS = [
    ("Product", ["Product Name", "Brand", "Category", "Colour"]),
    ("Customer", ["Customer Name", "Segment", "City", "Country"]),
    ("Store", ["Store Name", "Channel", "Region"]),
    ("Promotion", ["Promotion Name", "Promotion Type"]),
    ("Currency", ["Currency Code", "Currency Name"]),
]
_GEO = {"City": "City", "Country": "Country", "Region": "StateOrProvince"}
_FACTS = [
    ("Sales", ["Product", "Customer", "Store", "Promotion", "Currency"]),
    ("Returns", ["Product", "Customer", "Store"]),
    ("Budget", ["Product", "Store"]),
]


def _m_partition(name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "mode": "import",
        "source": {
            "type": "m",
            "expression": [
                "let",
                '    Source = Sql.Database(#"Server Name", #"Database Name"),',
                f'    Data = Source{{[Schema="dbo", Item="{name}"]}}[Data]',
                "in",
                "    Data",
            ],
        },
    }


def _key(name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "dataType": "int64",
        "sourceColumn": name,
        "isHidden": True,
        "isAvailableInMdx": False,
        "summarizeBy": "none",
        "description": f"Surrogate key {name}.",
    }


def _row_number() -> Dict[str, Any]:
    return {
        "type": "rowNumber",
        "name": "RowNumber-2662979B-1795-4F74-8F37-6A1BA8059B61",
        "dataType": "int64",
        "isHidden": True,
        "isUnique": True,
        "isKey": True,
        "isNullable": False,
        "attributeHierarchy": {},
    }


def _date_table() -> Dict[str, Any]:
    return {
        "name": "Date",
        "dataCategory": "Time",
        "description": "Calendar dimension, one row per day.",
        "columns": [
            {
                "name": "Date",
                "dataType": "dateTime",
                "sourceColumn": "Date",
                "isKey": True,
                "formatString": "yyyy-mm-dd",
                "summarizeBy": "none",
                "description": "Calendar date.",
            },
            {
                "name": "Year",
                "dataType": "int64",
                "sourceColumn": "Year",
                "summarizeBy": "none",
                "description": "Calendar year.",
            },
            {
                "name": "Month Name",
                "dataType": "string",
                "sourceColumn": "Month Name",
                "sortByColumn": "Month Number",
                "summarizeBy": "none",
                "description": "Month name.",
            },
            {
                "name": "Month Number",
                "dataType": "int64",
                "sourceColumn": "Month Number",
                "isHidden": True,
                "summarizeBy": "none",
                "description": "Month number (sort key).",
            },
        ],
        "hierarchies": [
            {
                "name": "Calendar",
                "levels": [
                    {"name": "Year", "ordinal": 0, "column": "Year"},
                    {"name": "Month", "ordinal": 1, "column": "Month Name"},
                ],
            }
        ],
        "partitions": [_m_partition("Date")],
    }


def _measure(i: int, facts: List[str]) -> Dict[str, Any]:
    fact = facts[i % len(facts)]
    base = f"'{fact}'[Amount]"
    kind = i % 6
    if kind == 0:
        expr = f"SUM ( {base} )"
    elif kind == 1:
        expr = f"DIVIDE ( [Measure {i - 1:04d}], [Measure 0000] )"
    elif kind == 2:
        expr = [
            "VAR _cur = [Measure 0000]",
            "VAR _prev = CALCULATE ( [Measure 0000], SAMEPERIODLASTYEAR ( 'Date'[Date] ) )",
            "RETURN",
            "    DIVIDE ( _cur - _prev, _prev )",
        ]
    elif kind == 3:
        expr = f"CALCULATE ( SUM ( {base} ), 'Date'[Year] = 2024 )"
    elif kind == 4:
        expr = f"TOTALYTD ( SUM ( {base} ), 'Date'[Date] )"
    else:
        expr = f"SUMX ( VALUES ( 'Product'[Product Key] ), CALCULATE ( SUM ( {base} ) ) )"
    return {
        "name": f"Measure {i:04d}",
        "expression": expr,
        "formatString": "0.0%" if kind in (1, 2) else "#,0.00",
        "displayFolder": f"Folder {i // 50:02d}",
        "description": f"Synthetic measure {i}.",
        "lineageTag": f"00000000-0000-0000-0000-{i:012d}",
    }


def build_clean_model(n_measures: int = 1500) -> Dict[str, Any]:
    """A clean star schema with *n_measures* measures in one measure table."""
    tables: List[Dict[str, Any]] = [_date_table()]
    rels: List[Dict[str, Any]] = []
    for dim, attrs in _DIMS:
        cols = [_key(f"{dim} Key")]
        cols += [
            {
                "name": a,
                "dataType": "string",
                "sourceColumn": a,
                "summarizeBy": "none",
                "description": f"{a}.",
            }
            | ({"dataCategory": _GEO[a]} if a in _GEO else {})
            for a in attrs
        ]
        tables.append(
            {
                "name": dim,
                "description": f"{dim} dimension.",
                "columns": cols,
                "partitions": [_m_partition(dim)],
            }
        )
    for fact, dims in _FACTS:
        cols = [_key(f"{d} Key") for d in dims]
        cols.append(_key("Date Key") | {"dataType": "dateTime", "name": "Order Date"})
        cols.append(
            {
                "name": "Amount",
                "dataType": "decimal",
                "sourceColumn": "Amount",
                "isHidden": True,
                "isAvailableInMdx": False,
                "formatString": "#,0.00",
                "description": "Line amount.",
            }
        )
        cols.append(
            {
                "name": "Quantity",
                "dataType": "int64",
                "sourceColumn": "Quantity",
                "isHidden": True,
                "isAvailableInMdx": False,
                "description": "Units.",
            }
        )
        cols.append(_row_number())
        tables.append(
            {
                "name": fact,
                "description": f"{fact} fact table.",
                "columns": cols,
                "partitions": [_m_partition(fact)],
            }
        )
        for d in dims:
            rels.append(
                {
                    "name": f"{fact}_{d}",
                    "fromTable": fact,
                    "fromColumn": f"{d} Key",
                    "toTable": d,
                    "toColumn": f"{d} Key",
                }
            )
        rels.append(
            {
                "name": f"{fact}_Date",
                "fromTable": fact,
                "fromColumn": "Order Date",
                "toTable": "Date",
                "toColumn": "Date",
            }
        )
    facts = [f for f, _ in _FACTS]
    tables.append(
        {
            "name": "Measures",
            "description": "Measure table.",
            "columns": [
                {
                    "name": "Column1",
                    "dataType": "string",
                    "sourceColumn": "Column1",
                    "isHidden": True,
                    "isAvailableInMdx": False,
                    "summarizeBy": "none",
                    "description": "Placeholder.",
                }
            ],
            "measures": [_measure(i, facts) for i in range(n_measures)]
            + [
                {
                    "name": f"{f} Quantity",
                    "expression": f"SUM ( '{f}'[Quantity] )",
                    "formatString": "#,0",
                    "displayFolder": "Quantities",
                    "description": f"Units in {f}.",
                }
                for f in facts
            ],
            "partitions": [_m_partition("Measures")],
        }
    )
    return {
        "name": "Model",
        "culture": "en-US",
        "defaultPowerBIDataSourceVersion": "powerBI_V3",
        "discourageImplicitMeasures": True,
        "tables": tables,
        "relationships": rels,
        # synonyms present: a clean model satisfies AI_NO_SYNONYMS (2026-10-03 D-28)
        "cultures": [
            {
                "name": "en-US",
                "linguisticMetadata": {
                    "content": {"Entities": {"customer": {"Terms": [{"client": {}}]}}}
                },
            }
        ],
        "expressions": [
            {
                "name": "Server Name",
                "kind": "m",
                "expression": '"sql.contoso.example" meta [IsParameterQuery=true, Type="Text"]',
            },
            {
                "name": "Database Name",
                "kind": "m",
                "expression": '"ContosoDW" meta [IsParameterQuery=true, Type="Text"]',
            },
        ],
    }


def with_changes(model: Dict[str, Any], fn) -> Dict[str, Any]:
    """Deep copy of *model* mutated by ``fn(model_copy)``."""
    m = copy.deepcopy(model)
    fn(m)
    return m


def table(model: Dict[str, Any], name: str) -> Dict[str, Any]:
    return next(t for t in model["tables"] if t["name"] == name)
