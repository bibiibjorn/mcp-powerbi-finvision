"""
Performance Optimizer for PBIXRay MCP Server
Checks relationship key uniqueness against the configured cardinality.

2026-09-22 review: B-16 — INFO.RELATIONSHIPS has ``FromCardinality`` /
``ToCardinality`` (1 = One, 2 = Many), not ``Cardinality``; reading the latter
made every relationship "Unknown" and the duplicate-key check never ran.
B-31 — the unreferenced ``analyze_column_cardinality`` /
``analyze_encoding_efficiency`` were removed (VertiPaq analysis lives in
``04_Query_Operations vertipaq_analyzer``).
"""

import logging
from typing import Any, Dict, Optional

from core.execution.dmv_helper import get_field_value
from core.utilities.type_conversions import safe_int

logger = logging.getLogger(__name__)

#: TOM RelationshipEndCardinality: None=0, One=1, Many=2.
_CARDINALITY_NAMES = {0: "None", 1: "One", 2: "Many"}


def _cardinality_name(value: Any) -> Optional[str]:
    """``1``/``'1'``/``'One'`` -> ``'One'``; ``2``/``'Many'`` -> ``'Many'``; else None."""
    if value is None or value == "":
        return None
    try:
        return _CARDINALITY_NAMES.get(int(str(value).strip()))
    except ValueError:
        text = str(value).strip().lower()
        if text in ("one", "many", "none"):
            return text.capitalize()
    return None


def _qt(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _qc(c: str) -> str:
    return "[" + str(c).replace("]", "]]") + "]"


class PerformanceOptimizer:
    """Analyze model performance and provide optimization recommendations."""

    def __init__(self, query_executor):
        """Initialize with query executor."""
        self.executor = query_executor

    def _build_maps(self):
        tables_map: Dict[int, str] = {}
        cols_map: Dict[int, str] = {}
        t_res = self.executor.execute_info_query("TABLES", top_n=5000)
        if t_res.get("success"):
            for tr in t_res.get("rows", []):
                tid = get_field_value(tr, ["ID", "TableID"])
                name = get_field_value(tr, ["Name", "Table"])
                try:
                    if tid is not None and name:
                        tables_map[int(str(tid))] = str(name)
                except Exception:
                    pass
        c_res = self.executor.execute_info_query("COLUMNS", top_n=5000)
        if c_res.get("success"):
            for cr in c_res.get("rows", []):
                cid = get_field_value(cr, ["ID", "ColumnID"])
                # INFO.COLUMNS carries ExplicitName/InferredName, not Name.
                cname = get_field_value(cr, ["Name", "ExplicitName", "InferredName"])
                try:
                    if cid is not None and cname:
                        cols_map[int(str(cid))] = str(cname)
                except Exception:
                    pass
        return tables_map, cols_map

    def _check_unique(self, table: str, column: str) -> Optional[Dict[str, int]]:
        check_query = (
            'EVALUATE\nROW("TotalRows", COUNTROWS(' + _qt(table) + "), "
            '"UniqueValues", DISTINCTCOUNT(' + _qt(table) + _qc(column) + "))"
        )
        result = self.executor.validate_and_execute_dax(check_query, 0, bypass_cache=True)
        if not (result.get("success") and result.get("rows")):
            return None
        row = result["rows"][0]
        total = safe_int(get_field_value(row, ["TotalRows"]) or 0)
        unique = safe_int(get_field_value(row, ["UniqueValues"]) or 0)
        return {"total": total, "unique": unique}

    def analyze_relationship_cardinality(self) -> Dict[str, Any]:
        """Check the One side(s) of every relationship for duplicate keys."""
        try:
            tables_by_id, columns_by_id = self._build_maps()

            rels_result = self.executor.execute_info_query("RELATIONSHIPS", top_n=5000)
            if not rels_result.get("success"):
                return {"success": False, "error": "Failed to get relationships"}

            relationships = rels_result["rows"]
            issues = []
            checked = 0
            unknown_cardinality = 0

            def _resolve(rel, name_key, id_key, mapping):
                value = get_field_value(rel, [name_key])
                if value:
                    return value
                raw = get_field_value(rel, [id_key])
                try:
                    return mapping.get(int(str(raw))) if raw is not None else None
                except Exception:
                    return None

            for rel in relationships:
                from_table = _resolve(rel, "FromTable", "FromTableID", tables_by_id)
                to_table = _resolve(rel, "ToTable", "ToTableID", tables_by_id)
                from_col = _resolve(rel, "FromColumn", "FromColumnID", columns_by_id)
                to_col = _resolve(rel, "ToColumn", "ToColumnID", columns_by_id)
                if not to_table or not to_col or not from_table or not from_col:
                    continue

                from_card = _cardinality_name(get_field_value(rel, ["FromCardinality"]))
                to_card = _cardinality_name(get_field_value(rel, ["ToCardinality"]))
                if from_card is None and to_card is None:
                    # Legacy single-field shape (older builds / TOM fallbacks).
                    legacy = str(get_field_value(rel, ["Cardinality"]) or "")
                    if "One" in legacy:
                        to_card = "One"
                if from_card is None and to_card is None:
                    unknown_cardinality += 1
                    continue
                configured = f"{from_card or '?'}-to-{to_card or '?'}"

                sides = []
                if to_card == "One":
                    sides.append((to_table, to_col))
                if from_card == "One":
                    sides.append((from_table, from_col))
                for table, column in sides:
                    stats = self._check_unique(table, column)
                    checked += 1
                    if not stats:
                        continue
                    total, unique = stats["total"], stats["unique"]
                    if total > 0 and total != unique:
                        issues.append(
                            {
                                "relationship": f"{from_table}[{from_col}] -> {to_table}[{to_col}]",
                                "configured_cardinality": configured,
                                "issue": "Duplicate keys in one-side",
                                "details": f"{table}[{column}] has {total - unique} duplicate values",
                                "severity": "high",
                                "recommendation": (
                                    f"Remove duplicates from {table}[{column}] or change the "
                                    "relationship to Many-to-Many"
                                ),
                                "total_rows": total,
                                "unique_values": unique,
                            }
                        )

            return {
                "success": True,
                "relationships_analyzed": len(relationships),
                "one_sides_checked": checked,
                "unknown_cardinality": unknown_cardinality,
                "issues_found": len(issues),
                "issues": issues,
                "summary": {
                    "healthy_relationships": len(relationships) - len(issues),
                    "relationships_with_issues": len(issues),
                },
            }

        except Exception as e:
            logger.error(f"Error analyzing cardinality: {e}")
            return {"success": False, "error": str(e)}
