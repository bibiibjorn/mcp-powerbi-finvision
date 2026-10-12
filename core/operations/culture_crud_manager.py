"""Culture CRUD Manager — manage cultures (translations) via TOM."""

import json
import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from core.autonomous.clr_errors import format_clr_error
from core.operations.amo_mixins import AmoSessionMixin

logger = logging.getLogger(__name__)

from core.operations.write_lane import commit as _lane_commit  # noqa: E402


class CultureCrudManager(AmoSessionMixin):
    """CRUD operations for model cultures and translations."""

    def __init__(self):
        self._connection_state = None

    @property
    def connection_state(self):
        if self._connection_state is None:
            from core.infrastructure.connection_state import connection_state

            self._connection_state = connection_state
        return self._connection_state

    @contextmanager
    def _amo_session(self):
        """Yield (server, model) for one AMO transaction; always disconnect."""
        server, _db, model, err = self._get_server_db_model()
        try:
            yield server, model, err
        finally:
            if server is not None:
                try:
                    server.Disconnect()
                except Exception:
                    pass

    def list_cultures(self) -> Dict[str, Any]:
        """List all cultures in the model."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            cultures = []
            for culture in model.Cultures:
                cultures.append(
                    {"name": culture.Name, "translation_count": culture.ObjectTranslations.Count}
                )
            return {"success": True, "cultures": cultures, "count": len(cultures)}

    def describe_culture(self, culture_name: str) -> Dict[str, Any]:
        """Get detailed info about a culture including translations."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            culture = model.Cultures.Find(culture_name)
            if not culture:
                return {"success": False, "error": f"Culture '{culture_name}' not found"}
            translations = []
            for t in culture.ObjectTranslations:
                translations.append(
                    {
                        "property": str(t.Property),
                        "object_type": type(t.Object).__name__,
                        "object_name": (
                            t.Object.Name if hasattr(t.Object, "Name") else str(t.Object)
                        ),
                        "value": t.Value,
                    }
                )
            return {
                "success": True,
                "culture": {
                    "name": culture.Name,
                    "translations": translations,
                    "translation_count": len(translations),
                },
            }

    def create_culture(self, culture_name: str) -> Dict[str, Any]:
        """Create a new culture (e.g., 'fr-FR', 'de-DE')."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            try:
                import Microsoft.AnalysisServices.Tabular as TOM

                culture = TOM.Culture()
                culture.Name = culture_name
                model.Cultures.Add(culture)
                _lane_commit(model, label="culture")
                return {"success": True, "message": f"Culture '{culture_name}' created"}
            except Exception as e:
                return format_clr_error(e, error_type="culture_create_error", culture=culture_name)

    def delete_culture(self, culture_name: str) -> Dict[str, Any]:
        """Delete a culture."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            culture = model.Cultures.Find(culture_name)
            if not culture:
                return {"success": False, "error": f"Culture '{culture_name}' not found"}
            try:
                model.Cultures.Remove(culture)
                _lane_commit(model, label="culture")
                return {"success": True, "message": f"Culture '{culture_name}' deleted"}
            except Exception as e:
                return format_clr_error(e, error_type="culture_delete_error", culture=culture_name)

    def set_translation(
        self,
        culture_name: str,
        object_type: str,
        object_name: str,
        property_name: str,
        value: str,
        table_name: str = None,
    ) -> Dict[str, Any]:
        """Set a translation for an object property."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            culture = model.Cultures.Find(culture_name)
            if not culture:
                return {"success": False, "error": f"Culture '{culture_name}' not found"}
            try:
                import Microsoft.AnalysisServices.Tabular as TOM

                obj = None
                if object_type == "table":
                    obj = model.Tables.Find(object_name)
                elif object_type == "column" and table_name:
                    table = model.Tables.Find(table_name)
                    if table:
                        obj = table.Columns.Find(object_name)
                elif object_type == "measure" and table_name:
                    table = model.Tables.Find(table_name)
                    if table:
                        obj = table.Measures.Find(object_name)
                elif object_type == "hierarchy" and table_name:
                    table = model.Tables.Find(table_name)
                    if table:
                        obj = table.Hierarchies.Find(object_name)
                if not obj:
                    return {
                        "success": False,
                        "error": f"{object_type} '{object_name}' not found",
                    }
                prop_map = {
                    "caption": TOM.TranslatedProperty.Caption,
                    "description": TOM.TranslatedProperty.Description,
                    "display_folder": TOM.TranslatedProperty.DisplayFolder,
                }
                prop = prop_map.get(property_name)
                if not prop:
                    return {
                        "success": False,
                        "error": (
                            f"Invalid property: {property_name}. "
                            f"Use: caption, description, display_folder"
                        ),
                    }
                culture.ObjectTranslations.SetTranslation(obj, prop, value)
                _lane_commit(model, label="culture")
                return {
                    "success": True,
                    "message": (
                        f"Translation set for {object_type} '{object_name}'."
                        f"{property_name} = '{value}'"
                    ),
                }
            except Exception as e:
                return format_clr_error(
                    e,
                    error_type="translation_set_error",
                    culture=culture_name,
                    object_type=object_type,
                    object_name=object_name,
                )

    def bulk_set_translations(
        self,
        culture_name: str,
        translations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Apply a batch of translations under a single SaveChanges.

        Each item is `{object_type, name, property, value, table_name?}`.
        The 'name' key is forwarded as the target object name (table / column /
        measure / hierarchy). 'property' must be one of caption, description,
        display_folder. 'table_name' is required for column / measure / hierarchy.

        Per-item failures are collected and returned in `errors`; the loop never
        raises. `model.SaveChanges()` is called once at the end if any item
        succeeded.
        """
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            culture = model.Cultures.Find(culture_name)
            if not culture:
                return {"success": False, "error": f"Culture '{culture_name}' not found"}

            try:
                import Microsoft.AnalysisServices.Tabular as TOM
            except Exception as exc:
                return {"success": False, "error": f"TOM unavailable: {exc}"}

            prop_map = {
                "caption": TOM.TranslatedProperty.Caption,
                "description": TOM.TranslatedProperty.Description,
                "display_folder": TOM.TranslatedProperty.DisplayFolder,
            }
            valid_object_types = {"table", "column", "measure", "hierarchy"}

            applied = 0
            failed = 0
            errors: List[Dict[str, Any]] = []

            def _fail(item: Dict[str, Any], msg: str) -> None:
                nonlocal failed
                failed += 1
                errors.append({"item": item, "error": msg})

            for item in translations or []:
                try:
                    object_type = item.get("object_type")
                    name = item.get("name")
                    property_name = item.get("property")
                    value = item.get("value")
                    table_name = item.get("table_name")

                    if not object_type or not name or not property_name or value is None:
                        _fail(
                            item,
                            "missing required field (object_type, name, property, value)",
                        )
                        continue
                    if object_type not in valid_object_types:
                        _fail(item, f"invalid object_type '{object_type}'")
                        continue
                    prop = prop_map.get(property_name)
                    if prop is None:
                        _fail(item, f"invalid property '{property_name}'")
                        continue

                    obj = None
                    if object_type == "table":
                        obj = model.Tables.Find(name)
                    else:
                        if not table_name:
                            _fail(item, f"table_name required for object_type '{object_type}'")
                            continue
                        table = model.Tables.Find(table_name)
                        if not table:
                            _fail(item, f"table '{table_name}' not found")
                            continue
                        if object_type == "column":
                            obj = table.Columns.Find(name)
                        elif object_type == "measure":
                            obj = table.Measures.Find(name)
                        elif object_type == "hierarchy":
                            obj = table.Hierarchies.Find(name)
                    if obj is None:
                        _fail(item, f"{object_type} '{name}' not found")
                        continue

                    culture.ObjectTranslations.SetTranslation(obj, prop, value)
                    applied += 1
                except Exception as exc:
                    _fail(item, str(exc))

            if applied > 0:
                try:
                    _lane_commit(model, label="culture")
                except Exception as exc:
                    # SaveChanges failed → nothing persisted (applied=0), but
                    # report the real staged count so the caller isn't misled
                    # into thinking no work was attempted.
                    return format_clr_error(
                        exc,
                        error_type="bulk_translations_save_error",
                        applied=0,
                        staged=applied,
                        failed=applied + failed,
                        errors=errors,
                        culture=culture_name,
                    )

            return {
                "success": failed == 0,
                "applied": applied,
                "failed": failed,
                "errors": errors,
                "culture": culture_name,
            }

    # ---- F-20d: translation exchange + coverage ---------------------------------

    @staticmethod
    def _guard_path(
        value: Any, arg: str, *, must_exist: bool = False, create_parents: bool = False
    ):
        from core.validation.path_guard import guard_or_error

        return guard_or_error(value, arg, must_exist=must_exist, create_parents=create_parents)

    @staticmethod
    def _culture_filter(culture_name: Any) -> Any:
        if not culture_name:
            return None
        if isinstance(culture_name, str):
            return [culture_name]
        return list(culture_name)

    @staticmethod
    def _unknown_cultures(model: Any, wanted: Any) -> Optional[Dict[str, Any]]:
        known = {c.Name for c in model.Cultures}
        unknown = [n for n in (wanted or []) if n not in known]
        if not unknown:
            return None
        return {
            "success": False,
            "error": f"Culture(s) not found: {unknown}",
            "error_type": "culture_not_found",
            "available_cultures": sorted(known),
        }

    def export_translations(
        self,
        culture_name: Any = None,
        file_format: str = "json",
        output_path: Any = None,
        include_untranslated: bool = True,
        limit: int = 500,
    ) -> Dict[str, Any]:
        """Export every translatable caption / description / display folder with its translations.

        ``culture_name`` None = every culture. With ``output_path`` the full document is written
        (path-guarded) and only a preview comes back; without it the rows (JSON) or the text (CSV)
        are returned inline, capped at ``limit`` rows.
        """
        from core.operations import culture_translations as ct

        fmt = str(file_format or "json").strip().lower()
        if fmt not in ("json", "csv"):
            return {
                "success": False,
                "error": f"file_format must be 'json' or 'csv', got {file_format!r}",
                "error_type": "invalid_parameters",
            }
        safe = None
        if output_path:
            safe, perr = self._guard_path(output_path, "output_path", create_parents=True)
            if perr:
                return perr
        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            wanted = self._culture_filter(culture_name)
            bad = self._unknown_cultures(model, wanted)
            if bad:
                return bad
            cultures, rows = ct.build_rows(model, wanted, bool(include_untranslated))
        out: Dict[str, Any] = {
            "success": True,
            "cultures": cultures,
            "row_count": len(rows),
            "file_format": fmt,
        }
        if safe is not None:
            text = (
                ct.rows_to_csv(cultures, rows)
                if fmt == "csv"
                else json.dumps({"cultures": cultures, "rows": rows}, ensure_ascii=False, indent=1)
            )
            try:
                with open(safe, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
            except OSError as exc:
                return {
                    "success": False,
                    "error": f"could not write {safe}: {exc}",
                    "error_type": "write_failed",
                }
            out["output_path"] = str(safe)
            out["preview"] = rows[:5]
            return out
        cap = max(1, int(limit)) if limit is not None else 500
        if fmt == "csv":
            out["csv"] = ct.rows_to_csv(cultures, rows[:cap])
        else:
            out["rows"] = rows[:cap]
        if len(rows) > cap:
            out["truncated"] = True
            out["note"] = (
                f"Only {cap} of {len(rows)} rows returned inline; pass output_path for the full file."
            )
        return out

    def import_translations(
        self,
        culture_name: str,
        translations: Any = None,
        input_path: Any = None,
        file_format: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Import translations for ONE culture from exchange rows (``translations``) or a CSV/JSON file.

        Empty values never blank an existing translation. ``dry_run`` reports what would be set.
        The write is ``bulk_set_translations`` (one save, journaled).
        """
        from core.operations import culture_translations as ct

        if not culture_name or not isinstance(culture_name, str):
            return {
                "success": False,
                "error": "culture_name is required",
                "error_type": "invalid_parameters",
            }
        if (translations is None) == (input_path is None):
            return {
                "success": False,
                "error": "Pass exactly one of translations (list of rows) or input_path (CSV/JSON file).",
                "error_type": "invalid_parameters",
            }
        rows: Any
        if input_path is not None:
            safe, perr = self._guard_path(input_path, "input_path", must_exist=True)
            if perr:
                return perr
            fmt = str(
                file_format or ("csv" if str(safe).lower().endswith(".csv") else "json")
            ).lower()
            try:
                with open(safe, "r", encoding="utf-8") as fh:
                    text = fh.read()
                rows = ct.parse_csv(text) if fmt == "csv" else ct.parse_json(text)
            except (OSError, ValueError) as exc:
                return {
                    "success": False,
                    "error": f"could not read {safe}: {exc}",
                    "error_type": "invalid_input",
                }
        else:
            rows = translations
            if not isinstance(rows, list):
                return {
                    "success": False,
                    "error": "translations must be a list of rows",
                    "error_type": "invalid_parameters",
                }
        items, counts, rejected = ct.rows_to_items(rows, culture_name)
        base = {
            "culture": culture_name,
            "to_apply": len(items),
            **counts,
            "rejected_rows": rejected[:25],
        }
        if dry_run:
            return {"success": True, "dry_run": True, "preview": items[:25], **base}
        if not items:
            return {
                "success": True,
                "applied": 0,
                "message": "Nothing to import (no non-empty values).",
                **base,
            }
        result = self.bulk_set_translations(culture_name, items)
        if isinstance(result, dict):
            result.update({k: v for k, v in base.items() if k not in result})
        return result

    def translation_coverage(self, culture_name: Any = None, limit: int = 50) -> Dict[str, Any]:
        """Per-culture coverage: translated / translatable properties, by property, plus the first
        ``limit`` missing items."""
        from core.operations import culture_translations as ct

        with self._amo_session() as (_, model, err):
            if not model:
                return {"success": False, "error": err or "Not connected"}
            if not list(model.Cultures):
                return {
                    "success": True,
                    "cultures": {},
                    "note": "The model has no cultures; create one first (culture create).",
                }
            wanted = self._culture_filter(culture_name)
            bad = self._unknown_cultures(model, wanted)
            if bad:
                return bad
            return {"success": True, **ct.coverage(model, wanted, limit=int(limit or 50))}
