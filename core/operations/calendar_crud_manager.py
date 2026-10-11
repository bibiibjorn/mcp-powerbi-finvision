"""Calendar CRUD Manager — native calendars / time-intelligence column groups via TOM (CAL-01).

A calendar lives on a (date) table and maps its columns to time units so the
engine's calendar-based time intelligence works. Rules enforced here BEFORE the
write (each one is an engine error otherwise, surfaced only at SaveChanges):

* calendar names are unique across the whole model;
* every column belongs to the host table and is used once per calendar;
* ``time_unit`` values are the case-sensitive TOM enum names (``Year``,
  ``Quarter``, ``Month``, ``Date``, ``MonthOfYear`` … — never plurals or ``Day``);
* a time unit appears at most once per calendar;
* at most ONE time-related group per calendar (all relative / flag columns go into it);
* a column maps to the same time unit in every calendar that uses it.

Calendars need a recent compatibility level; the engine rejects them otherwise and
this manager never upgrades the CL (use ``02_Model_Operations model set_properties``
deliberately).
"""

import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.operations.write_lane import commit as _lane_commit  # noqa: E402
from core.operations.write_lane import safe_undo  # noqa: E402

TIME_UNITS = (
    "Year",
    "Semester",
    "SemesterOfYear",
    "Quarter",
    "QuarterOfYear",
    "QuarterOfSemester",
    "Month",
    "MonthOfYear",
    "MonthOfSemester",
    "MonthOfQuarter",
    "Week",
    "WeekOfYear",
    "WeekOfSemester",
    "WeekOfQuarter",
    "WeekOfMonth",
    "Date",
    "DayOfYear",
    "DayOfSemester",
    "DayOfQuarter",
    "DayOfMonth",
    "DayOfWeek",
)


def validate_groups(
    groups: List[Dict[str, Any]],
    time_related_columns: Optional[List[str]],
    table_columns: List[str],
    other_calendar_units: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Pure validation of a calendar definition. Returns error messages (empty = valid)."""
    errors: List[str] = []
    cols = set(table_columns)
    seen_units: set = set()
    used: set = set()
    other = other_calendar_units or {}
    for i, g in enumerate(groups or []):
        unit = g.get("time_unit")
        if unit not in TIME_UNITS:
            hint = ""
            if isinstance(unit, str):
                low = {u.lower(): u for u in TIME_UNITS}
                guess = low.get(unit.lower().rstrip("s")) or (
                    "Date" if unit.lower() in ("day", "days") else None
                )
                hint = f" (did you mean '{guess}'?)" if guess else ""
            errors.append(f"groups[{i}].time_unit {unit!r} is not a TOM TimeUnit{hint}")
            continue
        if unit in seen_units:
            errors.append(f"time unit {unit} appears more than once")
        seen_units.add(unit)
        primary = g.get("primary_column")
        if not primary:
            errors.append(f"groups[{i}] ({unit}) needs primary_column")
            continue
        for c in [primary] + list(g.get("associated_columns") or []):
            if c not in cols:
                errors.append(f"column '{c}' is not in the host table")
            elif c in used:
                errors.append(f"column '{c}' is used more than once in this calendar")
            used.add(c)
            prev = other.get(c)
            if prev and prev != unit:
                errors.append(f"column '{c}' is mapped to {prev} in another calendar, not {unit}")
    for c in time_related_columns or []:
        if c not in cols:
            errors.append(f"time-related column '{c}' is not in the host table")
        elif c in used:
            errors.append(f"column '{c}' is used more than once in this calendar")
        used.add(c)
    return errors


def _rollback(model: Any) -> None:
    """Undo the unsaved edit; a failed rollback must not mask the save error being reported."""
    try:
        safe_undo(model)
    except Exception as e:  # noqa: BLE001
        logger.warning("calendar rollback failed: %s", e)


def _kind(group: Any) -> str:
    """Runtime .NET type name (pythonnet may hand back base-typed wrappers)."""
    try:
        return str(group.GetType().Name)
    except Exception:  # plain Python fakes in tests
        return type(group).__name__


class CalendarCrudManager:
    """CRUD for calendars on a table (TOM ``Table.Calendars``)."""

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
        from core.operations.amo_helpers import get_server_db_model

        server, _db, model, err = get_server_db_model(self.connection_state)
        try:
            yield server, model, err
        finally:
            if server is not None:
                try:
                    server.Disconnect()
                except Exception:
                    pass

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _describe(cal) -> Dict[str, Any]:
        groups, related = [], []
        for g in cal.CalendarColumnGroups:
            if _kind(g) == "TimeRelatedColumnGroup":
                related.extend(c.Name for c in g.Columns)
            else:
                groups.append(
                    {
                        "time_unit": str(g.TimeUnit),
                        "primary_column": (
                            g.PrimaryColumn.Name if g.PrimaryColumn is not None else None
                        ),
                        "associated_columns": [c.Name for c in g.AssociatedColumns],
                    }
                )
        return {
            "name": cal.Name,
            "table": cal.Table.Name if cal.Table is not None else None,
            "description": cal.Description or "",
            "groups": groups,
            "time_related_columns": related,
        }

    @staticmethod
    def _all_calendars(model):
        for t in model.Tables:
            for cal in t.Calendars:
                yield t, cal

    def _units_elsewhere(
        self, model, skip: Optional[str], table_name: Optional[str] = None
    ) -> Dict[str, str]:
        """{column name: time unit} of the OTHER calendars on ``table_name`` (N-28): a calendar can
        only use columns of its host table, so same-named columns of other tables are unrelated."""
        out: Dict[str, str] = {}
        for t, cal in self._all_calendars(model):
            if cal.Name == skip:
                continue
            if table_name is not None and t.Name != table_name:
                continue
            for g in cal.CalendarColumnGroups:
                if _kind(g) == "TimeUnitColumnAssociation":
                    unit = str(g.TimeUnit)
                    if g.PrimaryColumn is not None:
                        out[g.PrimaryColumn.Name] = unit
                    for c in g.AssociatedColumns:
                        out[c.Name] = unit
        return out

    @staticmethod
    def _build(TOM, cal, table, groups, time_related_columns) -> None:
        import System  # type: ignore

        for g in groups or []:
            # The TOM ctor takes the TimeUnit (there is no parameterless one).
            assoc = TOM.TimeUnitColumnAssociation(System.Enum.Parse(TOM.TimeUnit, g["time_unit"]))
            assoc.PrimaryColumn = table.Columns[g["primary_column"]]
            for c in g.get("associated_columns") or []:
                assoc.AssociatedColumns.Add(table.Columns[c])
            cal.CalendarColumnGroups.Add(assoc)
        if time_related_columns:
            rel = TOM.TimeRelatedColumnGroup()
            for c in time_related_columns:
                rel.Columns.Add(table.Columns[c])
            cal.CalendarColumnGroups.Add(rel)

    # ------------------------------------------------------------------ CRUD
    def list_calendars(self, table_name: Optional[str] = None) -> Dict[str, Any]:
        with self._amo_session() as (_, model, err):
            if not model:
                return {
                    "success": False,
                    "error": err or "Not connected",
                    "error_type": "not_connected",
                }
            cals = [
                self._describe(c)
                for t, c in self._all_calendars(model)
                if not table_name or t.Name == table_name
            ]
            return {"success": True, "calendars": cals, "count": len(cals)}

    def get_calendar(self, calendar_name: str) -> Dict[str, Any]:
        with self._amo_session() as (_, model, err):
            if not model:
                return {
                    "success": False,
                    "error": err or "Not connected",
                    "error_type": "not_connected",
                }
            for _t, cal in self._all_calendars(model):
                if cal.Name == calendar_name:
                    return {"success": True, "calendar": self._describe(cal)}
            return {
                "success": False,
                "error_type": "not_found",
                "error": f"Calendar '{calendar_name}' not found",
            }

    def create_calendar(
        self,
        calendar_name: str,
        table_name: str,
        groups: List[Dict[str, Any]],
        time_related_columns: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not calendar_name or not table_name:
            return {
                "success": False,
                "error_type": "missing_argument",
                "error": "calendar_name and table_name are required",
            }
        if not groups and not time_related_columns:
            return {
                "success": False,
                "error_type": "missing_argument",
                "error": "groups (or time_related_columns) required",
            }
        with self._amo_session() as (_, model, err):
            if not model:
                return {
                    "success": False,
                    "error": err or "Not connected",
                    "error_type": "not_connected",
                }
            table = model.Tables.Find(table_name)
            if table is None:
                return {
                    "success": False,
                    "error_type": "not_found",
                    "error": f"Table '{table_name}' not found",
                }
            if any(c.Name == calendar_name for _t, c in self._all_calendars(model)):
                return {
                    "success": False,
                    "error_type": "already_exists",
                    "error": f"Calendar '{calendar_name}' already exists (names are model-wide)",
                }
            errors = validate_groups(
                groups,
                time_related_columns,
                [c.Name for c in table.Columns],
                self._units_elsewhere(model, None, table_name),
            )
            if errors:
                return {
                    "success": False,
                    "error_type": "invalid_calendar",
                    "error": "; ".join(errors),
                    "errors": errors,
                }
            import Microsoft.AnalysisServices.Tabular as TOM

            cal = TOM.Calendar()
            cal.Name = calendar_name
            if description:
                cal.Description = description
            self._build(TOM, cal, table, groups, time_related_columns)
            table.Calendars.Add(cal)
            try:
                info = _lane_commit(model, label=f"calendar create {calendar_name}")
            except Exception as e:
                _rollback(model)
                return {
                    "success": False,
                    "error_type": "save_failed",
                    "error": str(e),
                    "suggestion": "Calendars need a recent compatibility level; check model get_properties.",
                }
            return {
                "success": True,
                "action": "created",
                "calendar": calendar_name,
                "table": table_name,
                **info,
            }

    def update_calendar(
        self,
        calendar_name: str,
        groups: Optional[List[Dict[str, Any]]] = None,
        time_related_columns: Optional[List[str]] = None,
        description: Optional[str] = None,
        new_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Replace the calendar's column groups (when given) / rename / re-describe."""
        with self._amo_session() as (_, model, err):
            if not model:
                return {
                    "success": False,
                    "error": err or "Not connected",
                    "error_type": "not_connected",
                }
            found = next(
                ((t, c) for t, c in self._all_calendars(model) if c.Name == calendar_name), None
            )
            if found is None:
                return {
                    "success": False,
                    "error_type": "not_found",
                    "error": f"Calendar '{calendar_name}' not found",
                }
            table, cal = found
            if new_name and any(c.Name == new_name for _t, c in self._all_calendars(model)):
                return {
                    "success": False,
                    "error_type": "already_exists",
                    "error": f"Calendar '{new_name}' already exists",
                }
            if groups is not None or time_related_columns is not None:
                current = self._describe(cal)
                g = groups if groups is not None else current["groups"]
                r = (
                    time_related_columns
                    if time_related_columns is not None
                    else current["time_related_columns"]
                )
                errors = validate_groups(
                    g,
                    r,
                    [c.Name for c in table.Columns],
                    self._units_elsewhere(model, calendar_name, table.Name),
                )
                if errors:
                    return {
                        "success": False,
                        "error_type": "invalid_calendar",
                        "error": "; ".join(errors),
                        "errors": errors,
                    }
                import Microsoft.AnalysisServices.Tabular as TOM

                cal.CalendarColumnGroups.Clear()
                self._build(TOM, cal, table, g, r)
            if description is not None:
                cal.Description = description
            if new_name:
                cal.Name = new_name
            try:
                info = _lane_commit(model, label=f"calendar update {calendar_name}")
            except Exception as e:
                _rollback(model)
                return {"success": False, "error_type": "save_failed", "error": str(e)}
            return {
                "success": True,
                "action": "updated",
                "calendar": new_name or calendar_name,
                **info,
            }

    def delete_calendar(self, calendar_name: str) -> Dict[str, Any]:
        with self._amo_session() as (_, model, err):
            if not model:
                return {
                    "success": False,
                    "error": err or "Not connected",
                    "error_type": "not_connected",
                }
            found = next(
                ((t, c) for t, c in self._all_calendars(model) if c.Name == calendar_name), None
            )
            if found is None:
                return {
                    "success": False,
                    "error_type": "not_found",
                    "error": f"Calendar '{calendar_name}' not found",
                }
            table, cal = found
            table.Calendars.Remove(cal)
            try:
                info = _lane_commit(model, label=f"calendar delete {calendar_name}")
            except Exception as e:
                _rollback(model)
                return {"success": False, "error_type": "save_failed", "error": str(e)}
            return {"success": True, "action": "deleted", "calendar": calendar_name, **info}
