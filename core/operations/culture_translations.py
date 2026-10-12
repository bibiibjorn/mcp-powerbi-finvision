"""Translation export / import / coverage helpers for ``culture`` (F-20d).

Pure functions over the TOM object graph (duck-typed: ``model.Tables`` ->
``Columns`` / ``Measures`` / ``Hierarchies``, ``model.Cultures`` ->
``ObjectTranslations``), so they are testable with plain fakes. The write side is
``CultureCrudManager.bulk_set_translations`` (one SaveChanges through
``write_lane.commit``); nothing in this module saves.

Exchange row (JSON, one per translatable object property)::

    {"object_type": "measure", "table_name": "Sales", "name": "Total",
     "property": "caption", "source": "Total", "translations": {"nl-BE": "Totaal"}}

CSV carries the same columns with one extra column per culture
(``object_type,table_name,name,property,source,nl-BE,fr-BE``).
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

#: TOM TranslatedProperty member -> the property name the culture ops use.
PROPERTY_BY_TOM = {
    "Caption": "caption",
    "Description": "description",
    "DisplayFolder": "display_folder",
}
VALID_PROPERTIES = tuple(PROPERTY_BY_TOM.values())
FIXED_COLUMNS = ["object_type", "table_name", "name", "property", "source"]

_Key = Tuple[str, str, str, str]  # (object_type, table_name, name, property)


def _kind_of(obj: Any) -> Optional[str]:
    """table / column / measure / hierarchy from a TOM object's CLR type name (DataColumn -> column)."""
    name = type(obj).__name__.lower()
    for kind in ("hierarchy", "measure", "column", "table"):
        if kind in name:
            return kind
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def translatable_items(model: Any) -> Iterator[Tuple[_Key, str]]:
    """Yield ``((object_type, table_name, name, property), source_value)`` for every translatable
    property that has a source value (caption = the object's name, description, display folder)."""
    for table in model.Tables:
        t_name = _text(table.Name)
        yield ("table", "", t_name, "caption"), t_name
        if _text(getattr(table, "Description", None)):
            yield ("table", "", t_name, "description"), _text(table.Description)
        for kind, coll in (
            ("column", table.Columns),
            ("measure", table.Measures),
            ("hierarchy", table.Hierarchies),
        ):
            for obj in coll:
                if kind == "column" and _text(getattr(obj, "Type", None)) == "RowNumber":
                    continue  # the engine's hidden row-number column is not translatable
                name = _text(obj.Name)
                yield (kind, t_name, name, "caption"), name
                for prop, attr in (
                    ("description", "Description"),
                    ("display_folder", "DisplayFolder"),
                ):
                    value = _text(getattr(obj, attr, None))
                    if value:
                        yield (kind, t_name, name, prop), value


def culture_translations(culture: Any) -> Dict[_Key, str]:
    """Existing translations of one culture, keyed like :func:`translatable_items`."""
    out: Dict[_Key, str] = {}
    for tr in culture.ObjectTranslations:
        obj = tr.Object
        kind = _kind_of(obj)
        prop = PROPERTY_BY_TOM.get(_text(tr.Property))
        if kind is None or prop is None:
            continue
        table_name = (
            "" if kind == "table" else _text(getattr(getattr(obj, "Table", None), "Name", ""))
        )
        out[(kind, table_name, _text(obj.Name), prop)] = _text(tr.Value)
    return out


def build_rows(
    model: Any, culture_names: Optional[Iterable[str]] = None, include_untranslated: bool = True
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Return ``(cultures, rows)`` in the exchange shape. ``culture_names`` None = every culture."""
    cultures = {_text(c.Name): c for c in model.Cultures}
    wanted = list(culture_names) if culture_names else list(cultures)
    existing = {name: culture_translations(cultures[name]) for name in wanted if name in cultures}
    rows: List[Dict[str, Any]] = []
    for key, source in translatable_items(model):
        translations = {n: existing[n][key] for n in existing if key in existing[n]}
        if not include_untranslated and not translations:
            continue
        rows.append(
            {
                "object_type": key[0],
                "table_name": key[1],
                "name": key[2],
                "property": key[3],
                "source": source,
                "translations": {n: translations.get(n, "") for n in existing},
            }
        )
    return list(existing), rows


def rows_to_csv(cultures: List[str], rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(FIXED_COLUMNS + cultures)
    for r in rows:
        writer.writerow(
            [r["object_type"], r["table_name"], r["name"], r["property"], r["source"]]
            + [r["translations"].get(c, "") for c in cultures]
        )
    return buf.getvalue()


def parse_csv(text: str) -> List[Dict[str, Any]]:
    """CSV (export layout) -> exchange rows; every non-fixed column is a culture."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = reader.fieldnames or []
    cultures = [f for f in fields if f not in FIXED_COLUMNS]
    out = []
    for rec in reader:
        out.append(
            {
                **{k: rec.get(k, "") for k in FIXED_COLUMNS},
                "translations": {c: rec.get(c, "") for c in cultures},
            }
        )
    return out


def parse_json(text: str) -> List[Dict[str, Any]]:
    data = json.loads(text.lstrip("﻿"))
    if isinstance(data, dict):
        data = data.get("rows") or data.get("translations") or []
    if not isinstance(data, list):
        raise ValueError('expected a JSON list of rows (or {"rows": [...]})')
    return data


def rows_to_items(
    rows: Iterable[Dict[str, Any]], culture_name: str
) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[Dict[str, Any]]]:
    """Exchange / bulk rows -> ``bulk_set_translations`` items for ``culture_name``.

    A row's value is ``translations[culture_name]`` (export layout) or ``value`` (bulk layout);
    empty values are skipped (an import never blanks a translation). Returns
    ``(items, counts, rejected)``.
    """
    items: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    counts = {"rows": 0, "skipped_empty": 0, "rejected": 0}
    for row in rows:
        counts["rows"] += 1
        if not isinstance(row, dict):
            counts["rejected"] += 1
            rejected.append({"row": row, "error": "row is not an object"})
            continue
        trans = row.get("translations")
        if isinstance(trans, dict) and culture_name in trans:
            value = trans[culture_name]
        else:
            value = row.get("value")
        if value is None or str(value) == "":
            counts["skipped_empty"] += 1
            continue
        kind = str(row.get("object_type") or "").lower()
        prop = str(row.get("property") or "").lower()
        name = row.get("name")
        if (
            kind not in ("table", "column", "measure", "hierarchy")
            or prop not in VALID_PROPERTIES
            or not name
        ):
            counts["rejected"] += 1
            rejected.append({"row": row, "error": "needs object_type, name and a valid property"})
            continue
        item = {"object_type": kind, "name": str(name), "property": prop, "value": str(value)}
        if kind != "table":
            if not row.get("table_name"):
                counts["rejected"] += 1
                rejected.append({"row": row, "error": f"table_name required for {kind}"})
                continue
            item["table_name"] = str(row["table_name"])
        items.append(item)
    return items, counts, rejected


def coverage(
    model: Any, culture_names: Optional[Iterable[str]] = None, limit: int = 50
) -> Dict[str, Any]:
    """Per-culture translation coverage over every translatable (object, property) with a source."""
    cultures, rows = build_rows(model, culture_names, include_untranslated=True)
    total = len(rows)
    report: Dict[str, Any] = {}
    for c in cultures:
        done = sum(1 for r in rows if r["translations"].get(c))
        by_prop: Dict[str, Dict[str, int]] = {}
        missing = []
        for r in rows:
            slot = by_prop.setdefault(r["property"], {"total": 0, "translated": 0})
            slot["total"] += 1
            if r["translations"].get(c):
                slot["translated"] += 1
            else:
                missing.append({k: r[k] for k in ("object_type", "table_name", "name", "property")})
        report[c] = {
            "translated": done,
            "total": total,
            "coverage_pct": round(done / total * 100, 1) if total else 100.0,
            "by_property": by_prop,
            "missing": missing[: max(0, int(limit))],
            "missing_count": len(missing),
        }
    return {"cultures": report, "translatable_properties": total}
