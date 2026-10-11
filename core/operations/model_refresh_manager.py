"""Model Refresh Manager — refresh model, tables, or partitions via TOM.

Field parameter tables are calculated tables; use refresh_type='calculate' to
recompute them after member edits, or 'full' to reprocess everything.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from core.autonomous.clr_errors import format_clr_error
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

try:
    from core.infrastructure.dll_paths import load_amo_assemblies, load_adomd_assembly

    if load_amo_assemblies():
        from Microsoft.AnalysisServices.Tabular import Server as AMOServer
    else:
        AMOServer = None
    if load_adomd_assembly():
        from Microsoft.AnalysisServices.AdomdClient import AdomdCommand
    else:
        AdomdCommand = None
except Exception as e:
    logger.debug(f"AMO/ADOMD assemblies not loaded: {e}")
    AMOServer = None
    AdomdCommand = None
from core.operations.amo_mixins import AmoSessionMixin  # noqa: E402

_REFRESH_TYPES = {
    "full": 0,
    "automatic": 2,
    "dataOnly": 3,
    "calculate": 4,
    "clearValues": 5,
    "defragment": 6,
    # Builds the explicit string / full-text indexes (Sep 2026, needs CL 1707).
    "indexes": 9,
}

#: Compatibility level that introduced ``stringIndexingBehavior`` (and the ``indexes`` refresh).
INDEXES_MIN_CL = 1707
#: Compatibility level that introduced ``fullTextIndexingBehavior``.
FULL_TEXT_MIN_CL = 1708


def indexes_refresh_refusal(db) -> Optional[Dict[str, Any]]:
    """Error dict when ``refresh_type=indexes`` cannot run on this database, else None.

    Explicit string / full-text indexes exist from compatibility level 1707. A CL upgrade is
    irreversible, so this never upgrades - it tells the caller how to do it deliberately.
    """
    try:
        level = int(getattr(db, "CompatibilityLevel", None))
    except (TypeError, ValueError):
        level = None
    if level is not None and level >= INDEXES_MIN_CL:
        return None
    return {
        "success": False,
        "error": (
            f"refresh_type='indexes' builds explicit string / full-text indexes, which need "
            f"compatibility level {INDEXES_MIN_CL}+ (this model is at "
            f"{level if level is not None else 'an unknown level'}). Nothing was changed."
        ),
        "error_type": "compatibility_level_too_low",
        "compatibility_level": level,
        "required_compatibility_level": INDEXES_MIN_CL,
        "suggestion": (
            "Raise the level deliberately (irreversible): 02_Model_Operations object_type=model "
            "operation=set_properties compatibility_level=1707 allow_compatibility_upgrade=true "
            "confirm_destructive=true. Or use refresh_type=full / automatic."
        ),
    }


def _resolve_refresh_type(refresh_type: str):
    """Resolve a refresh type string to the TOM RefreshType enum value."""
    key = (refresh_type or "automatic").strip()
    for name in _REFRESH_TYPES:
        if name.lower() == key.lower():
            key = name
            break
    if key not in _REFRESH_TYPES:
        return None, f"Invalid refresh_type '{refresh_type}'. Valid: {list(_REFRESH_TYPES.keys())}"
    try:
        from Microsoft.AnalysisServices.Tabular import RefreshType

        return getattr(RefreshType, key[0].upper() + key[1:]), None
    except Exception as e:
        return None, f"Failed to resolve RefreshType: {e}"


class ModelRefreshManager(AmoSessionMixin):
    """Refresh the whole model, a set of tables, or report status."""

    def __init__(self):
        self._connection_state = None

    @property
    def connection_state(self):
        if self._connection_state is None:
            from core.infrastructure.connection_state import connection_state

            self._connection_state = connection_state
        return self._connection_state

    def refresh(
        self,
        refresh_type: str = "automatic",
        tables: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Refresh the model or a specific set of tables.

        Args:
            refresh_type: full|automatic|dataOnly|calculate|clearValues|defragment
            tables: Optional list of table names. If omitted, refreshes the whole model.

        Returns:
            Dict with success, refresh_type, refreshed targets, and any errors.
        """
        # C-35: a bare string used to be iterated per character.
        if isinstance(tables, str):
            tables = [tables] if tables.strip() else None
        if tables is not None:
            tables = [t for t in tables if t]
            if not tables:
                return error_envelope(
                    "invalid_parameters",
                    "tables was given but is empty — omit it to refresh the whole model",
                )

        server, db, model, err = self._get_server_db_model()
        if err:
            return {"success": False, "error": err}

        rtype, err = _resolve_refresh_type(refresh_type)
        if err:
            try:
                server.Disconnect()
            except Exception:
                pass
            return {"success": False, "error": err}
        if str(refresh_type or "").strip().lower() == "indexes":
            refusal = indexes_refresh_refusal(db)
            if refusal:
                try:
                    server.Disconnect()
                except Exception:
                    pass
                return refusal

        refreshed: List[str] = []
        missing: List[str] = []

        try:
            if tables:
                for name in tables:
                    tbl = model.Tables.Find(name)
                    if not tbl:
                        missing.append(name)
                        continue
                    tbl.RequestRefresh(rtype)
                    refreshed.append(name)
                scope = "tables"
                if not refreshed:
                    # C-35: every listed table was missing — nothing was requested,
                    # so do not SaveChanges and do not report success.
                    return {
                        "success": False,
                        "error": f"None of the requested tables exist: {missing}",
                        "error_type": "table_not_found",
                        "missing_tables": missing,
                    }
            else:
                model.RequestRefresh(rtype)
                refreshed = [t.Name for t in model.Tables]
                scope = "model"

            from core.operations.write_lane import commit

            commit(model, label=f"model/refresh:{scope}")

            result = {
                "success": True,
                "scope": scope,
                "refresh_type": refresh_type,
                "refreshed_count": len(refreshed),
                "refreshed": refreshed,
                "note": "Refresh is processed synchronously on Power BI Desktop; table data reflects the new state on return.",
            }
            if missing:
                result["missing_tables"] = missing
                result["warning"] = f"{len(missing)} table(s) not found and skipped"
            return result

        except Exception as e:
            logger.error(f"Refresh failed: {e}")
            # T0-5: unwind the full CLR chain so the agent sees the root cause
            # (e.g. broken query, RLS conflict) instead of an opaque
            # "One or more errors occurred" top-level message.
            return format_clr_error(
                e,
                error_type="refresh_error",
                refresh_type=refresh_type,
                refreshed_before_error=refreshed,
            )
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    # ---- refresh status / history (RF-01) --------------------------------

    def refresh_status(self, table_name: Optional[str] = None) -> Dict[str, Any]:
        """Per-partition refresh state of the OPEN model, read from TOM (read-only).

        Desktop exposes each partition's ``RefreshedTime``, ``ModifiedTime`` and ``State`` and
        nothing more: there is no refresh log, duration, failure message or in-flight
        progress for a Desktop model (that needs a Service/XMLA refresh history).
        """
        from core.infrastructure import offline_model

        if offline_model.is_active():
            # N-31: a deserialised TMDL folder has no engine, so every partition reads "not
            # ready" with no refresh time; that is not a status, it is "no engine".
            from core.validation.error_handler import ErrorHandler

            return ErrorHandler.handle_manager_unavailable("query_executor")
        server, db, model, err = self._get_server_db_model()
        if err:
            return error_envelope("connection_error", err)
        try:
            tables = []
            partitions: List[Dict[str, Any]] = []
            newest = None
            for t in model.Tables:
                if table_name and str(t.Name) != table_name:
                    continue
                rows = []
                for p in t.Partitions:
                    refreshed = _iso(getattr(p, "RefreshedTime", None))
                    row = {
                        "table": str(t.Name),
                        "partition": str(p.Name),
                        "state": _enum_text(getattr(p, "State", None)),
                        "mode": _enum_text(getattr(p, "Mode", None)),
                        "refreshed_time": refreshed,
                        "modified_time": _iso(getattr(p, "ModifiedTime", None)),
                    }
                    rows.append(row)
                    partitions.append(row)
                    if refreshed and (newest is None or refreshed > newest):
                        newest = refreshed
                times = [r["refreshed_time"] for r in rows if r["refreshed_time"]]
                tables.append(
                    {
                        "table": str(t.Name),
                        "partitions": len(rows),
                        "states": sorted({r["state"] for r in rows if r["state"]}),
                        "last_refreshed": max(times) if times else None,
                        "never_refreshed": not times,
                    }
                )
            if table_name and not tables:
                return error_envelope("table_not_found", f"Table '{table_name}' not found")
            not_ready = [
                f"{p['table']}/{p['partition']}"
                for p in partitions
                if p["state"] and p["state"].lower() != "ready"
            ]
            return {
                "success": True,
                "model_last_refreshed": newest,
                "table_count": len(tables),
                "partition_count": len(partitions),
                "not_ready_count": len(not_ready),
                "not_ready_partitions": not_ready[:50],
                "tables": tables,
                "partitions": partitions,
                "limits": _DESKTOP_REFRESH_LIMITS,
            }
        except Exception as e:  # noqa: BLE001
            logger.error("refresh_status failed: %s", e)
            return error_envelope("tom_error", str(e))
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def refresh_history(
        self, since: Optional[str] = None, limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """Refreshes THIS SERVER requested on the connected model, from the write journal.

        Only refreshes made through ``02_Model_Operations`` (model / table / partition
        refresh) are listed - newest first, with the scope and ``refresh_type`` requested.
        Refreshes started in Desktop's UI, by a schedule or by another tool are not
        visible: Desktop keeps no refresh history.
        """
        from core.operations import write_journal

        try:
            cutoff = write_journal.parse_since(since)
        except ValueError as exc:
            return error_envelope("invalid_parameters", str(exc))
        try:
            cap = max(1, int(limit)) if limit is not None else 20
        except (TypeError, ValueError):
            cap = 20
        try:
            key, _ident = write_journal._identity(None)  # noqa: SLF001
            view = write_journal.read_journal(key)
        except Exception as e:  # noqa: BLE001
            return error_envelope("journal_error", f"journal unreadable: {e}")
        rows = []
        for e in reversed(view.get("entries") or []):
            if not _is_refresh_entry(e):
                continue
            if cutoff is not None and write_journal._ts_epoch(e.get("ts")) < cutoff:  # noqa: SLF001
                continue
            args = e.get("args") or {}
            rows.append(
                {
                    "entry_id": e.get("id"),
                    "ts": e.get("ts"),
                    "operation": e.get("operation"),
                    "refresh_type": args.get("refresh_type"),
                    "tables": args.get("tables") or args.get("table_name"),
                    "partition": args.get("partition_name"),
                    "session": e.get("session"),
                }
            )
        out: Dict[str, Any] = {
            "success": True,
            "journal": view.get("key"),
            "refreshes": rows[:cap],
            "matching": len(rows),
            "limits": _DESKTOP_REFRESH_LIMITS,
        }
        if len(rows) > cap:
            out["truncated"] = True
        if not write_journal.is_enabled():
            out["warning"] = "The write journal is disabled (model_write.journal_enabled=false)."
        return out


_DESKTOP_REFRESH_LIMITS = (
    "Power BI Desktop keeps no refresh log: there is no start/end time, duration, row count or "
    "error text per refresh, and no history of refreshes started from its UI. Status is the "
    "partitions' RefreshedTime/State from the model; history is only what this server requested "
    "(write journal). For a published model use the Service refresh history (REST/XMLA lane)."
)


def _iso(value: Any) -> Optional[str]:
    """ISO text of a CLR DateTime; None for unset (0001-01-01) values."""
    if value is None:
        return None
    try:
        if getattr(value, "Year", 9999) <= 1:
            return None
        return value.ToString("yyyy-MM-ddTHH:mm:ss") if hasattr(value, "ToString") else str(value)
    except Exception:  # noqa: BLE001
        text = str(value)
        return None if text.startswith("0001") else text


def _enum_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value).split(".")[-1].strip() or None


_REFRESH_OP = re.compile(r"(^|/)refresh(:|$)")


def _is_refresh_entry(entry: Dict[str, Any]) -> bool:
    """A journal entry written by a model/table/partition refresh (not a refresh POLICY edit)."""
    for field in ("operation", "label"):
        if _REFRESH_OP.search(str(entry.get(field) or "")):
            return True
    return False
