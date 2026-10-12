"""
Model Diff Engine

Compares two Power BI models and identifies all differences including schema
changes, measure modifications, relationship and security updates.

Input shape: :func:`core.comparison.tmsl_normalizer.normalize_tmsl` (canonical
snake_case keys). The older ``export_tmdl_structure`` / TMDL-file shapes are
still accepted (relationships with combined ``'T'[c]`` references).

2026-09-22 review:

- I-07: keys aligned with the normaliser — cardinality, cross-filter, security
  filter, RLS filter expressions, calculated-column DAX, partitions/M,
  calculation items, named expressions, UDFs and model properties are diffed.
- I-08: role / perspective ``added``/``removed`` are ``{"name": …}`` dicts.
- I-18: DAX is compared token-wise (whitespace, comments, keyword case and
  CRLF/LF are *cosmetic*, reported separately and not counted); objects are
  matched by ``lineage_tag`` first, so renames and cross-table moves are
  reported as such instead of remove + add; renamed tables are counted;
  added/removed tables carry their measures; ``total_changes`` counts changed
  OBJECTS (a modified measure is no longer also a "modified table").
"""

import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def dax_equivalent(a: Optional[str], b: Optional[str]) -> Tuple[bool, bool]:
    """``(equivalent, cosmetic)`` — cosmetic = texts differ only in formatting."""
    a = a or ""
    b = b or ""
    if a == b:
        return True, False
    if a.replace("\r\n", "\n").strip() == b.replace("\r\n", "\n").strip():
        return True, True
    try:
        from core.dax.dax_formatter_client import token_equivalent

        same, _reason = token_equivalent(a, b)
        return (True, True) if same else (False, False)
    except Exception:  # noqa: BLE001 — fall back to whitespace-insensitive compare
        same = " ".join(a.split()) == " ".join(b.split())
        return (True, True) if same else (False, False)


def _text_equivalent(a: Optional[str], b: Optional[str]) -> Tuple[bool, bool]:
    """Line-ending / trailing-whitespace insensitive compare for M and other text."""
    a = a or ""
    b = b or ""
    if a == b:
        return True, False
    na = "\n".join(line.rstrip() for line in a.replace("\r\n", "\n").strip().split("\n"))
    nb = "\n".join(line.rstrip() for line in b.replace("\r\n", "\n").strip().split("\n"))
    return (True, True) if na == nb else (False, False)


class ModelDiffer:
    """
    Compares two Power BI models and identifies differences.

    Performs deep comparison of tables, columns, measures, relationships,
    roles, perspectives and model properties.
    """

    def __init__(self, model1: Dict[str, Any], model2: Dict[str, Any]):
        self.model1 = model1
        self.model2 = model2
        self.diff_result: Optional[Dict[str, Any]] = None
        self.cosmetic: List[Dict[str, Any]] = []

    # ── entry point ──────────────────────────────────────────────────────

    def compare(self) -> Dict[str, Any]:
        """Perform complete model comparison."""
        logger.info("Starting model comparison")
        self.cosmetic = []
        tables_diff = self._compare_tables()
        measures = self._extract_all_measures(tables_diff)
        self._detect_measure_moves(measures)

        self.diff_result = {
            "summary": {
                "model1_name": self._get_model_name(self.model1),
                "model2_name": self._get_model_name(self.model2),
                "total_changes": 0,
                "changes_by_category": {},
            },
            "tables": tables_diff,
            "measures": measures,
            "relationships": self._compare_relationships(),
            "roles": self._compare_roles(),
            "perspectives": self._compare_perspectives(),
            "expressions": self._compare_named(
                self.model1.get("expressions", []), self.model2.get("expressions", []), "M"
            ),
            "functions": self._compare_named(
                self.model1.get("functions", []), self.model2.get("functions", []), "DAX"
            ),
            "model_properties": self._compare_model_properties(),
            "cosmetic_changes": self.cosmetic,
        }
        self._calculate_summary()
        logger.info(
            f"Comparison complete: {self.diff_result['summary']['total_changes']} total changes"
        )
        return self.diff_result

    # ── helpers ──────────────────────────────────────────────────────────

    def _get_model_name(self, model: Dict[str, Any]) -> str:
        for key in ("model", "database"):
            name = (model.get(key) or {}).get("name")
            if name and not self._is_uuid(name):
                return name
        for key in ("model", "database"):
            name = (model.get(key) or {}).get("name")
            if name:
                return name
        return "Unknown Model"

    def _is_uuid(self, value: str) -> bool:
        return bool(_UUID_RE.match(str(value or "")))

    def _cosmetic(self, kind: str, name: str, field: str) -> None:
        self.cosmetic.append({"object_type": kind, "name": name, "field": field})

    def _dax_change(
        self, kind: str, name: str, field: str, a: Any, b: Any, **extra
    ) -> Optional[Dict[str, Any]]:
        same, cosmetic = dax_equivalent(a, b)
        if same:
            if cosmetic:
                self._cosmetic(kind, name, field)
            return None
        out = {"from": a or "", "to": b or "", "diff": self._generate_text_diff(a or "", b or "")}
        out.update(extra)
        return out

    @staticmethod
    def _pair_renames(
        removed: Dict[str, Dict[str, Any]],
        added: Dict[str, Dict[str, Any]],
        *,
        signature=None,
    ) -> List[Tuple[str, str]]:
        """Pair removed/added objects by ``lineage_tag``, then (1:1) by *signature*."""
        pairs: List[Tuple[str, str]] = []
        added_by_tag = {}
        for n, o in added.items():
            tag = o.get("lineage_tag")
            if tag:
                added_by_tag.setdefault(tag, []).append(n)
        for old, o in list(removed.items()):
            tag = o.get("lineage_tag")
            cands = [c for c in added_by_tag.get(tag, []) if c in added] if tag else []
            if len(cands) == 1:
                pairs.append((old, cands[0]))
        taken_old = {p[0] for p in pairs}
        taken_new = {p[1] for p in pairs}
        if signature is not None:
            sig_old: Dict[Any, List[str]] = {}
            sig_new: Dict[Any, List[str]] = {}
            for n, o in removed.items():
                if n not in taken_old and not o.get("lineage_tag"):
                    s = signature(o)
                    if s:
                        sig_old.setdefault(s, []).append(n)
            for n, o in added.items():
                if n not in taken_new and not o.get("lineage_tag"):
                    s = signature(o)
                    if s:
                        sig_new.setdefault(s, []).append(n)
            for s, olds in sig_old.items():
                news = sig_new.get(s, [])
                if len(olds) == 1 and len(news) == 1:
                    pairs.append((olds[0], news[0]))
        return pairs

    @staticmethod
    def _dax_signature(obj: Dict[str, Any]) -> Optional[str]:
        expr = obj.get("expression") or ""
        if not expr.strip():
            return None
        return " ".join(expr.split()).lower()

    # ── tables ───────────────────────────────────────────────────────────

    def _compare_tables(self) -> Dict[str, Any]:
        tables1 = {t["name"]: t for t in self.model1.get("tables", [])}
        tables2 = {t["name"]: t for t in self.model2.get("tables", [])}

        result: Dict[str, Any] = {
            "added": [],
            "removed": [],
            "modified": [],
            "renamed": [],
            "unchanged": [],
        }
        removed = {n: tables1[n] for n in set(tables1) - set(tables2)}
        added = {n: tables2[n] for n in set(tables2) - set(tables1)}

        def structure(t: Dict[str, Any]) -> Optional[Tuple]:
            cols = tuple(sorted(c.get("name") for c in t.get("columns", [])))
            meas = tuple(sorted(m.get("name") for m in t.get("measures", [])))
            return (cols, meas) if (cols or meas) else None

        pairs = self._pair_renames(removed, added, signature=structure)
        for old, new in pairs:
            detail = self._compare_table_details(tables1[old], tables2[new])
            entry = {
                "from": old,
                "to": new,
                "columns_count": len(tables1[old].get("columns", [])),
                "measures_count": len(tables1[old].get("measures", [])),
                "matched_by": "lineage_tag" if tables1[old].get("lineage_tag") else "structure",
            }
            if detail["has_changes"]:
                entry["changes"] = detail
            result["renamed"].append(entry)
            removed.pop(old, None)
            added.pop(new, None)

        for name in sorted(added):
            t = added[name]
            result["added"].append(
                {
                    "name": name,
                    "columns_count": len(t.get("columns", [])),
                    "measures_count": len(t.get("measures", [])),
                    "is_calculation_group": t.get("is_calculation_group", False),
                    "measures": [self._measure_brief(m) for m in t.get("measures", [])],
                }
            )
        for name in sorted(removed):
            t = removed[name]
            result["removed"].append(
                {
                    "name": name,
                    "columns_count": len(t.get("columns", [])),
                    "measures_count": len(t.get("measures", [])),
                    "measures": [self._measure_brief(m) for m in t.get("measures", [])],
                }
            )
        for name in sorted(set(tables1) & set(tables2)):
            detail = self._compare_table_details(tables1[name], tables2[name])
            if detail["has_changes"]:
                result["modified"].append({"name": name, "changes": detail})
            else:
                result["unchanged"].append(name)
        return result

    @staticmethod
    def _measure_brief(m: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": m.get("name"),
            "expression": m.get("expression"),
            "format_string": m.get("format_string"),
            "display_folder": m.get("display_folder"),
        }

    def _compare_table_details(
        self, table1: Dict[str, Any], table2: Dict[str, Any]
    ) -> Dict[str, Any]:
        tname = table2.get("name") or table1.get("name") or ""
        result: Dict[str, Any] = {
            "has_changes": False,
            "own_changes": False,
            "columns": self._compare_columns(
                table1.get("columns", []), table2.get("columns", []), tname
            ),
            "measures": self._compare_measures(
                table1.get("measures", []), table2.get("measures", []), tname
            ),
            "hierarchies": self._compare_hierarchies(
                table1.get("hierarchies", []), table2.get("hierarchies", [])
            ),
            "partitions": self._compare_partitions(
                table1.get("partitions", []), table2.get("partitions", []), tname
            ),
        }

        if table1.get("is_calculation_group") != table2.get("is_calculation_group"):
            result["calculation_group_changed"] = {
                "from": table1.get("is_calculation_group", False),
                "to": table2.get("is_calculation_group", False),
            }
            result["own_changes"] = True

        for field in ("description", "is_hidden", "data_category", "calculation_group_precedence"):
            if table1.get(field) != table2.get(field):
                result[f"{field}_changed"] = {"from": table1.get(field), "to": table2.get(field)}
                result["own_changes"] = True

        annot = self._compare_annotations(
            table1.get("annotations", []), table2.get("annotations", [])
        )
        if annot:
            result["annotations"] = annot
            result["own_changes"] = True

        if table1.get("is_calculation_group") or table2.get("is_calculation_group"):
            ci = self._compare_calculation_items(
                table1.get("calculation_items", []), table2.get("calculation_items", []), tname
            )
            if ci["added"] or ci["removed"] or ci["modified"]:
                result["calculation_items"] = ci

        subs = ("columns", "measures", "hierarchies", "partitions", "calculation_items")
        if result["own_changes"] or any(
            isinstance(result.get(k), dict)
            and any(result[k].get(x) for x in ("added", "removed", "modified", "renamed"))
            for k in subs
        ):
            result["has_changes"] = True
        return result

    def _compare_columns(self, columns1, columns2, table_name: str = "") -> Dict[str, Any]:
        cols1 = {c["name"]: c for c in columns1}
        cols2 = {c["name"]: c for c in columns2}
        result: Dict[str, Any] = {"added": [], "removed": [], "modified": [], "renamed": []}

        removed = {n: cols1[n] for n in set(cols1) - set(cols2)}
        added = {n: cols2[n] for n in set(cols2) - set(cols1)}
        for old, new in self._pair_renames(removed, added):
            changes = self._column_changes(cols1[old], cols2[new], f"{table_name}[{new}]")
            result["renamed"].append({"from": old, "to": new, "changes": changes})
            removed.pop(old, None)
            added.pop(new, None)

        for n in sorted(added):
            c = added[n]
            result["added"].append(
                {
                    "name": n,
                    "data_type": c.get("data_type"),
                    "is_calculated": c.get("is_calculated", False),
                    "expression": c.get("expression"),
                }
            )
        for n in sorted(removed):
            c = removed[n]
            result["removed"].append(
                {
                    "name": n,
                    "data_type": c.get("data_type"),
                    "is_calculated": c.get("is_calculated", False),
                }
            )
        for n in sorted(set(cols1) & set(cols2)):
            changes = self._column_changes(cols1[n], cols2[n], f"{table_name}[{n}]")
            if changes:
                result["modified"].append({"name": n, "changes": changes})
        return result

    def _column_changes(
        self, col1: Dict[str, Any], col2: Dict[str, Any], label: str
    ) -> Dict[str, Any]:
        changes: Dict[str, Any] = {}
        dt1, dt2 = col1.get("data_type"), col2.get("data_type")
        # TMDL omits the data type of calculated columns and calculated-table columns (the engine
        # infers it); a live / TMSL side always states it. "Unknown" on one side is therefore not a
        # change (R0201 live test: 226 false data_type changes, disk vs Desktop).
        inferred = {None, "", "Unknown"}
        if dt1 != dt2 and not (dt1 in inferred or dt2 in inferred):
            changes["data_type"] = {"from": dt1, "to": dt2}
        if bool(col1.get("is_calculated")) != bool(col2.get("is_calculated")):
            changes["is_calculated"] = {
                "from": col1.get("is_calculated", False),
                "to": col2.get("is_calculated", False),
            }
        if col1.get("expression") or col2.get("expression"):
            ch = self._dax_change(
                "Column", label, "expression", col1.get("expression"), col2.get("expression")
            )
            if ch:
                changes["expression"] = ch
        for field in (
            "description",
            "display_folder",
            "format_string",
            "data_category",
            "summarize_by",
            "sort_by_column",
            "is_key",
            "is_hidden",
            "source_column",
        ):
            if col1.get(field) != col2.get(field):
                changes[field] = {"from": col1.get(field), "to": col2.get(field)}
        annot = self._compare_annotations(col1.get("annotations", []), col2.get("annotations", []))
        if annot:
            changes["annotations"] = annot
        props = self._compare_properties(col1.get("properties", {}), col2.get("properties", {}))
        if props:
            changes["properties"] = props
        return changes

    def _compare_measures(self, measures1, measures2, table_name: str = "") -> Dict[str, Any]:
        meas1 = {m["name"]: m for m in measures1}
        meas2 = {m["name"]: m for m in measures2}
        result: Dict[str, Any] = {"added": [], "removed": [], "modified": [], "renamed": []}

        removed = {n: meas1[n] for n in set(meas1) - set(meas2)}
        added = {n: meas2[n] for n in set(meas2) - set(meas1)}
        for old, new in self._pair_renames(removed, added, signature=self._dax_signature):
            changes = self._measure_changes(meas1[old], meas2[new], f"{table_name}[{new}]")
            result["renamed"].append(
                {
                    "from": old,
                    "to": new,
                    "matched_by": "lineage_tag" if meas1[old].get("lineage_tag") else "expression",
                    "changes": changes,
                }
            )
            removed.pop(old, None)
            added.pop(new, None)

        for n in sorted(added):
            m = added[n]
            result["added"].append(
                {
                    "name": n,
                    "expression": m.get("expression"),
                    "format_string": m.get("format_string"),
                    "display_folder": m.get("display_folder"),
                    "lineage_tag": m.get("lineage_tag"),
                }
            )
        for n in sorted(removed):
            m = removed[n]
            result["removed"].append(
                {
                    "name": n,
                    "expression": m.get("expression"),
                    "format_string": m.get("format_string"),
                    "display_folder": m.get("display_folder"),
                    "lineage_tag": m.get("lineage_tag"),
                }
            )
        for n in sorted(set(meas1) & set(meas2)):
            changes = self._measure_changes(meas1[n], meas2[n], f"{table_name}[{n}]")
            if changes:
                result["modified"].append(
                    {
                        "name": n,
                        "display_folder": meas2[n].get("display_folder"),
                        "changes": changes,
                    }
                )
        return result

    def _measure_changes(
        self, m1: Dict[str, Any], m2: Dict[str, Any], label: str
    ) -> Dict[str, Any]:
        changes: Dict[str, Any] = {}
        ch = self._dax_change(
            "Measure",
            label,
            "expression",
            m1.get("expression"),
            m2.get("expression"),
            impact="high",
        )
        if ch:
            changes["expression"] = ch
        for field in ("format_string_definition", "kpi"):
            a, b = m1.get(field), m2.get(field)
            if isinstance(a, str) or isinstance(b, str):
                ch = self._dax_change("Measure", label, field, a, b)
                if ch:
                    changes[field] = ch
            elif a != b:
                changes[field] = {"from": a, "to": b}
        for field in (
            "format_string",
            "display_folder",
            "description",
            "is_hidden",
            "data_category",
        ):
            if m1.get(field) != m2.get(field):
                changes[field] = {"from": m1.get(field), "to": m2.get(field)}
        annot = self._compare_annotations(m1.get("annotations", []), m2.get("annotations", []))
        if annot:
            changes["annotations"] = annot
        props = self._compare_properties(m1.get("properties", {}), m2.get("properties", {}))
        if props:
            changes["properties"] = props
        return changes

    def _compare_hierarchies(self, hierarchies1, hierarchies2) -> Dict[str, Any]:
        hier1 = {h["name"]: h for h in hierarchies1}
        hier2 = {h["name"]: h for h in hierarchies2}
        result: Dict[str, Any] = {
            "added": sorted(set(hier2) - set(hier1)),
            "removed": sorted(set(hier1) - set(hier2)),
            "modified": [],
        }
        for name in sorted(set(hier1) & set(hier2)):
            levels1 = hier1[name].get("levels", [])
            levels2 = hier2[name].get("levels", [])
            if levels1 != levels2:
                result["modified"].append(
                    {
                        "name": name,
                        "levels_from": [lv.get("name") for lv in levels1],
                        "levels_to": [lv.get("name") for lv in levels2],
                    }
                )
        return result

    def _compare_partitions(self, partitions1, partitions2, table_name: str = "") -> Dict[str, Any]:
        part1 = {p["name"]: p for p in partitions1}
        part2 = {p["name"]: p for p in partitions2}
        result: Dict[str, Any] = {
            "added": sorted(set(part2) - set(part1)),
            "removed": sorted(set(part1) - set(part2)),
            "modified": [],
        }
        for name in sorted(set(part1) & set(part2)):
            p1, p2 = part1[name], part2[name]
            changes: Dict[str, Any] = {}
            for field in ("mode", "type", "data_source"):
                if p1.get(field) != p2.get(field):
                    changes[field] = {"from": p1.get(field), "to": p2.get(field)}
            s1, s2 = p1.get("source") or "", p2.get("source") or ""
            if str(p1.get("type") or "").lower() in ("calculated", "calculationgroup"):
                same, cosmetic = dax_equivalent(s1, s2)
            else:
                same, cosmetic = _text_equivalent(s1, s2)
            if not same:
                changes["source"] = {"from": s1, "to": s2, "diff": self._generate_text_diff(s1, s2)}
            elif cosmetic:
                self._cosmetic("Partition", f"{table_name}/{name}", "source")
            if changes:
                result["modified"].append({"name": name, "changes": changes})
        return result

    def _compare_calculation_items(self, items1, items2, table_name: str = "") -> Dict[str, Any]:
        calc1 = {c["name"]: c for c in items1}
        calc2 = {c["name"]: c for c in items2}
        result: Dict[str, Any] = {
            "added": sorted(set(calc2) - set(calc1)),
            "removed": sorted(set(calc1) - set(calc2)),
            "modified": [],
        }
        for name in sorted(set(calc1) & set(calc2)):
            c1, c2 = calc1[name], calc2[name]
            label = f"{table_name}[{name}]"
            changes: Dict[str, Any] = {}
            ch = self._dax_change(
                "CalculationItem", label, "expression", c1.get("expression"), c2.get("expression")
            )
            if ch:
                changes["expression"] = ch
            ch = self._dax_change(
                "CalculationItem",
                label,
                "format_string_definition",
                c1.get("format_string_definition"),
                c2.get("format_string_definition"),
            )
            if ch:
                changes["format_string_definition"] = {"from": ch["from"], "to": ch["to"]}
            for field in ("ordinal", "description"):
                if c1.get(field) != c2.get(field):
                    changes[field] = {"from": c1.get(field), "to": c2.get(field)}
            annot = self._compare_annotations(c1.get("annotations", []), c2.get("annotations", []))
            if annot:
                changes["annotations"] = annot
            if changes:
                result["modified"].append({"name": name, "changes": changes})
        return result

    # ── measures (top-level view) ─────────────────────────────────────────

    def _extract_all_measures(self, tables_diff: Dict[str, Any]) -> Dict[str, Any]:
        """All measure changes across tables: modified, removed, added, renamed, moved."""
        out: Dict[str, List[Dict[str, Any]]] = {
            "modified": [],
            "removed": [],
            "added": [],
            "renamed": [],
            "moved": [],
        }

        def collect(changes: Dict[str, Any], table_name: str) -> None:
            meas = changes.get("measures", {})
            for key in ("modified", "removed", "added", "renamed"):
                for m in meas.get(key, []):
                    item = dict(m)
                    item["table"] = table_name
                    out[key].append(item)

        for table in tables_diff.get("modified", []):
            collect(table.get("changes", {}), table["name"])
        for table in tables_diff.get("renamed", []):
            if table.get("changes"):
                collect(table["changes"], table["to"])
        for table in tables_diff.get("added", []):
            for m in table.get("measures", []):
                item = dict(m)
                item["table"] = table["name"]
                out["added"].append(item)
        for table in tables_diff.get("removed", []):
            for m in table.get("measures", []):
                item = dict(m)
                item["table"] = table["name"]
                out["removed"].append(item)
        return out

    def _detect_measure_moves(self, measures: Dict[str, Any]) -> None:
        """Pair a removed measure in table A with an added one in table B that
        carries the same lineage tag (or, without tags, the same name + DAX)."""
        lineage1 = self._measure_lineage(self.model1)
        lineage2 = self._measure_lineage(self.model2)
        removed = measures["removed"]
        added = measures["added"]
        used_added = set()
        keep_removed = []
        for r in removed:
            tag = r.get("lineage_tag") or lineage1.get((r.get("table"), r.get("name")))
            match = None
            for idx, a in enumerate(added):
                if idx in used_added or a.get("table") == r.get("table"):
                    continue
                atag = a.get("lineage_tag") or lineage2.get((a.get("table"), a.get("name")))
                if tag and atag and tag == atag:
                    match = idx
                    break
                if not tag and not atag and a.get("name") == r.get("name"):
                    same, _ = dax_equivalent(a.get("expression"), r.get("expression"))
                    if same:
                        match = idx
                        break
            if match is None:
                keep_removed.append(r)
                continue
            used_added.add(match)
            a = added[match]
            entry = {
                "name": a.get("name"),
                "from_table": r.get("table"),
                "to_table": a.get("table"),
                "renamed_from": r.get("name") if r.get("name") != a.get("name") else None,
            }
            ch = self._dax_change(
                "Measure",
                f"{a.get('table')}[{a.get('name')}]",
                "expression",
                r.get("expression"),
                a.get("expression"),
            )
            if ch:
                entry["changes"] = {"expression": ch}
            measures["moved"].append(entry)
        measures["removed"] = keep_removed
        measures["added"] = [a for idx, a in enumerate(added) if idx not in used_added]

    @staticmethod
    def _measure_lineage(model: Dict[str, Any]) -> Dict[Tuple[str, str], str]:
        out: Dict[Tuple[str, str], str] = {}
        for t in model.get("tables", []) or []:
            for m in t.get("measures", []) or []:
                if m.get("lineage_tag"):
                    out[(t.get("name"), m.get("name"))] = m["lineage_tag"]
        return out

    # ── relationships ─────────────────────────────────────────────────────

    _REL_FIELDS = (
        "from_cardinality",
        "to_cardinality",
        "is_active",
        "cross_filtering_behavior",
        "security_filtering_behavior",
        "rely_on_referential_integrity",
    )
    _REL_DEFAULTS = {
        "from_cardinality": "Many",
        "to_cardinality": "One",
        "is_active": True,
        "cross_filtering_behavior": "OneDirection",
        "security_filtering_behavior": "OneDirection",
        "rely_on_referential_integrity": False,
    }

    def _rel_value(self, rel: Dict[str, Any], field: str) -> Any:
        value = rel.get(field)
        if value is None and field == "cross_filtering_behavior":
            value = rel.get("cross_filter_direction")
        if (
            value is None
            and field in ("from_cardinality", "to_cardinality")
            and rel.get("cardinality")
        ):
            parts = str(rel["cardinality"]).split(":")
            if len(parts) == 2:
                value = parts[0] if field == "from_cardinality" else parts[1]
        if value is None:
            value = self._REL_DEFAULTS.get(field)
        if isinstance(value, str):
            return value[:1].upper() + value[1:]
        return value

    def _compare_relationships(self) -> Dict[str, Any]:
        rels1 = {self._relationship_key(r): r for r in self.model1.get("relationships", [])}
        rels2 = {self._relationship_key(r): r for r in self.model2.get("relationships", [])}
        result: Dict[str, Any] = {"added": [], "removed": [], "modified": []}

        def describe(rel: Dict[str, Any]) -> Dict[str, Any]:
            norm = self._normalize_relationship(rel)
            out = dict(norm)
            for f in self._REL_FIELDS:
                out[f] = self._rel_value(rel, f)
            return out

        for key in sorted(set(rels2) - set(rels1)):
            result["added"].append(describe(rels2[key]))
        for key in sorted(set(rels1) - set(rels2)):
            result["removed"].append(describe(rels1[key]))
        for key in sorted(set(rels1) & set(rels2)):
            r1, r2 = rels1[key], rels2[key]
            changes: Dict[str, Any] = {}
            for f in self._REL_FIELDS:
                v1, v2 = self._rel_value(r1, f), self._rel_value(r2, f)
                if v1 != v2:
                    changes[f] = {"from": v1, "to": v2}
            annot = self._compare_annotations(r1.get("annotations", []), r2.get("annotations", []))
            if annot:
                changes["annotations"] = annot
            if changes:
                entry = self._normalize_relationship(r1)
                entry["changes"] = changes
                result["modified"].append(entry)
        return result

    def _normalize_relationship(self, rel: Dict[str, Any]) -> Dict[str, str]:
        """Separate table/column fields from either the canonical or the TMDL shape."""
        if "from_table" in rel and "to_table" in rel:
            return {
                "from_table": rel.get("from_table", "Unknown"),
                "from_column": rel.get("from_column", "Unknown"),
                "to_table": rel.get("to_table", "Unknown"),
                "to_column": rel.get("to_column", "Unknown"),
            }
        from_parts = self._parse_column_ref(rel.get("from_column", ""))
        to_parts = self._parse_column_ref(rel.get("to_column", ""))
        return {
            "from_table": from_parts["table"],
            "from_column": from_parts["column"],
            "to_table": to_parts["table"],
            "to_column": to_parts["column"],
        }

    def _parse_column_ref(self, col_ref: str) -> Dict[str, str]:
        """``'T'[c]`` / ``T[c]`` / ``T.c`` -> ``{table, column}``."""
        if not col_ref:
            return {"table": "Unknown", "column": "Unknown"}
        try:
            from core.dax.dax_names import split_qualified

            table, column = split_qualified(col_ref)
            if table and column:
                return {"table": table, "column": column}
        except Exception:  # noqa: BLE001
            pass
        match = re.match(r"^'?([^'\[]+)'?\[([^\]]+)\]$", col_ref)
        if match:
            return {"table": match.group(1), "column": match.group(2)}
        if "." in col_ref and "[" not in col_ref:
            parts = col_ref.split(".", 1)
            return {"table": parts[0], "column": parts[1]}
        return {"table": "Unknown", "column": col_ref}

    def _relationship_key(self, rel: Dict[str, Any]) -> str:
        norm = self._normalize_relationship(rel)
        return (
            f"{norm['from_table']}[{norm['from_column']}]|{norm['to_table']}[{norm['to_column']}]"
        )

    # ── security / perspectives / named objects ───────────────────────────

    def _compare_roles(self) -> Dict[str, Any]:
        roles1 = {r["name"]: r for r in self.model1.get("roles", [])}
        roles2 = {r["name"]: r for r in self.model2.get("roles", [])}
        modified = []
        for name in sorted(set(roles1) & set(roles2)):
            r1, r2 = roles1[name], roles2[name]
            changes: Dict[str, Any] = {}

            def perms(role):
                out = {}
                for p in role.get("table_permissions", []):
                    expr = p.get("expression")
                    if expr is None:
                        expr = p.get("filter_expression")
                    out[p.get("table", "")] = {
                        "expression": expr or "",
                        "metadata_permission": p.get("metadata_permission"),
                        "column_permissions": p.get("column_permissions"),
                    }
                return out

            f1, f2 = perms(r1), perms(r2)
            added_tables = sorted(set(f2) - set(f1))
            removed_tables = sorted(set(f1) - set(f2))
            changed_tables = []
            filter_diffs = {}
            for t in sorted(set(f1) & set(f2)):
                ch = self._dax_change(
                    "TablePermission",
                    f"{name}/{t}",
                    "filter_expression",
                    f1[t]["expression"],
                    f2[t]["expression"],
                )
                other = {
                    k: {"from": f1[t][k], "to": f2[t][k]}
                    for k in ("metadata_permission", "column_permissions")
                    if f1[t][k] != f2[t][k]
                }
                if ch or other:
                    changed_tables.append(t)
                    filter_diffs[t] = {**({"filter_expression": ch} if ch else {}), **other}
            if added_tables or removed_tables or changed_tables:
                changes["filter_expressions"] = {
                    "added_tables": added_tables,
                    "removed_tables": removed_tables,
                    "changed_tables": changed_tables,
                    "details": filter_diffs,
                }
            m1, m2 = set(r1.get("members", [])), set(r2.get("members", []))
            if m1 != m2:
                changes["members"] = {"added": sorted(m2 - m1), "removed": sorted(m1 - m2)}
            for field in ("model_permission", "description"):
                if r1.get(field) != r2.get(field):
                    changes[field] = {"from": r1.get(field), "to": r2.get(field)}
            if changes:
                modified.append({"name": name, "changes": changes})
        return {
            "added": [{"name": n} for n in sorted(set(roles2) - set(roles1))],
            "removed": [{"name": n} for n in sorted(set(roles1) - set(roles2))],
            "modified": modified,
        }

    def _compare_perspectives(self) -> Dict[str, Any]:
        persp1 = {p["name"]: p for p in self.model1.get("perspectives", [])}
        persp2 = {p["name"]: p for p in self.model2.get("perspectives", [])}

        def objects(p):
            out = set()
            for t in p.get("tables", []):
                tname = t.get("name", "")
                out.add(f"table:{tname}")
                for kind in ("columns", "measures", "hierarchies"):
                    for o in t.get(kind, []):
                        out.add(f"{kind[:-1]}:{tname}.{o}")
            return out

        modified = []
        for name in sorted(set(persp1) & set(persp2)):
            o1, o2 = objects(persp1[name]), objects(persp2[name])
            if o1 != o2:
                modified.append(
                    {
                        "name": name,
                        "added_objects": sorted(o2 - o1),
                        "removed_objects": sorted(o1 - o2),
                    }
                )
        return {
            "added": [{"name": n} for n in sorted(set(persp2) - set(persp1))],
            "removed": [{"name": n} for n in sorted(set(persp1) - set(persp2))],
            "modified": modified,
        }

    def _compare_named(self, items1, items2, language: str) -> Dict[str, Any]:
        """Named expressions (M) / UDFs (DAX): added / removed / modified."""
        a = {i["name"]: i for i in items1 or [] if isinstance(i, dict) and i.get("name")}
        b = {i["name"]: i for i in items2 or [] if isinstance(i, dict) and i.get("name")}
        modified = []
        for name in sorted(set(a) & set(b)):
            e1, e2 = a[name].get("expression"), b[name].get("expression")
            if language == "DAX":
                same, cosmetic = dax_equivalent(e1, e2)
            else:
                same, cosmetic = _text_equivalent(e1, e2)
            changes: Dict[str, Any] = {}
            if not same:
                changes["expression"] = {
                    "from": e1 or "",
                    "to": e2 or "",
                    "diff": self._generate_text_diff(e1 or "", e2 or ""),
                }
            elif cosmetic:
                self._cosmetic(
                    "Function" if language == "DAX" else "NamedExpression", name, "expression"
                )
            for field in ("kind", "description", "is_hidden"):
                if a[name].get(field) != b[name].get(field):
                    changes[field] = {"from": a[name].get(field), "to": b[name].get(field)}
            if changes:
                modified.append({"name": name, "changes": changes})
        return {
            "added": [{"name": n} for n in sorted(set(b) - set(a))],
            "removed": [{"name": n} for n in sorted(set(a) - set(b))],
            "modified": modified,
        }

    def _compare_model_properties(self) -> Dict[str, Any]:
        changes: Dict[str, Any] = {}
        db = self._compare_properties(
            (self.model1.get("database") or {}).get("properties", {}),
            (self.model2.get("database") or {}).get("properties", {}),
        )
        if db:
            changes["database"] = db
        m1 = self.model1.get("model") or {}
        m2 = self.model2.get("model") or {}
        props = self._compare_properties(
            m1.get("properties", {}) or {}, m2.get("properties", {}) or {}
        )
        if (
            m1.get("compatibility_level")
            and m2.get("compatibility_level")
            and m1.get("compatibility_level") != m2.get("compatibility_level")
        ):
            props["compatibility_level"] = {
                "from": m1.get("compatibility_level"),
                "to": m2.get("compatibility_level"),
            }
        c1 = sorted(self.model1.get("cultures", []) or [])
        c2 = sorted(self.model2.get("cultures", []) or [])
        if c1 != c2:
            props["cultures"] = {"from": c1, "to": c2}
        if props:
            changes["model"] = props
        return changes

    @staticmethod
    def _compare_properties(props1: Dict[str, Any], props2: Dict[str, Any]) -> Dict[str, Any]:
        changes = {}
        for key in set(props1) | set(props2):
            if props1.get(key) != props2.get(key):
                changes[key] = {"from": props1.get(key), "to": props2.get(key)}
        return changes

    @staticmethod
    def _compare_annotations(annots1, annots2) -> Dict[str, Any]:
        a1 = {a["name"]: a.get("value") for a in annots1 or []}
        a2 = {a["name"]: a.get("value") for a in annots2 or []}
        return {
            k: {"from": a1.get(k), "to": a2.get(k)}
            for k in set(a1) | set(a2)
            if a1.get(k) != a2.get(k)
        }

    def _normalize_dax(self, dax: str) -> str:
        """Kept for callers of the old API: comment-free, whitespace-collapsed DAX."""
        if not dax:
            return ""
        try:
            from core.dax.dax_utilities import normalize_dax

            return " ".join(normalize_dax(dax).split())
        except Exception:  # noqa: BLE001
            return " ".join(dax.split()).strip()

    @staticmethod
    def _generate_text_diff(text1: str, text2: str) -> List[str]:
        if not text1 and not text2:
            return []
        return list(
            difflib.unified_diff(
                (text1 or "").replace("\r\n", "\n").splitlines(),
                (text2 or "").replace("\r\n", "\n").splitlines(),
                fromfile="original",
                tofile="modified",
                lineterm="",
            )
        )

    # ── summary ──────────────────────────────────────────────────────────

    def _calculate_summary(self) -> None:
        d = self.diff_result
        assert d is not None
        tables = d["tables"]
        cats: Dict[str, int] = {
            "tables_added": len(tables["added"]),
            "tables_removed": len(tables["removed"]),
            "tables_renamed": len(tables["renamed"]),
            "tables_modified": len(tables["modified"]),
            "relationships_added": len(d["relationships"]["added"]),
            "relationships_removed": len(d["relationships"]["removed"]),
            "relationships_modified": len(d["relationships"]["modified"]),
            "roles_added": len(d["roles"]["added"]),
            "roles_removed": len(d["roles"]["removed"]),
            "roles_modified": len(d["roles"]["modified"]),
            "perspectives_added": len(d["perspectives"]["added"]),
            "perspectives_removed": len(d["perspectives"]["removed"]),
            "perspectives_modified": len(d["perspectives"]["modified"]),
            "expressions_changed": sum(
                len(d["expressions"][k]) for k in ("added", "removed", "modified")
            ),
            "functions_changed": sum(
                len(d["functions"][k]) for k in ("added", "removed", "modified")
            ),
            "model_properties_changed": sum(len(v) for v in d["model_properties"].values()),
        }
        col = {"added": 0, "removed": 0, "modified": 0, "renamed": 0}
        other = {"hierarchies": 0, "partitions": 0, "calculation_items": 0}
        table_properties_modified = 0

        def count(changes: Dict[str, Any]) -> None:
            nonlocal table_properties_modified
            if changes.get("own_changes"):
                table_properties_modified += 1
            for k in col:
                col[k] += len(changes.get("columns", {}).get(k, []))
            for k in other:
                sec = changes.get(k) or {}
                other[k] += sum(len(sec.get(x, [])) for x in ("added", "removed", "modified"))

        for t in tables["modified"]:
            count(t["changes"])
        for t in tables["renamed"]:
            if t.get("changes"):
                count(t["changes"])
        meas = d["measures"]
        cats.update(
            {
                "table_properties_modified": table_properties_modified,
                "columns_added": col["added"],
                "columns_removed": col["removed"],
                "columns_modified": col["modified"],
                "columns_renamed": col["renamed"],
                "measures_added": len(meas["added"]),
                "measures_removed": len(meas["removed"]),
                "measures_modified": len(meas["modified"]),
                "measures_renamed": len(meas["renamed"]),
                "measures_moved": len(meas["moved"]),
                "hierarchies_changed": other["hierarchies"],
                "partitions_changed": other["partitions"],
                "calculation_items_changed": other["calculation_items"],
            }
        )
        d["summary"]["changes_by_category"] = cats
        # Count changed OBJECTS: "tables_modified" is a container count (a table
        # listed there because one of its measures changed) and is excluded.
        d["summary"]["total_changes"] = sum(v for k, v in cats.items() if k != "tables_modified")
        d["summary"]["cosmetic_changes"] = len(self.cosmetic)


def compare_models(model1: Dict[str, Any], model2: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience function to compare two models."""
    return ModelDiffer(model1, model2).compare()
